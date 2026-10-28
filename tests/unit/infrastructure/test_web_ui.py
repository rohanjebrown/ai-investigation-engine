from contextlib import asynccontextmanager
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from investigation_engine.application.answers import AnswerStatus, GenerationResult
from investigation_engine.application.ports.generation_errors import GenerationProviderError
from investigation_engine.application.retrieval import Evidence
from investigation_engine.application.use_cases.answer_question import AnswerQuestion
from investigation_engine.infrastructure.api.app import create_app


@pytest.fixture
def web(registration):
    hits = []
    for index in range(2):
        document, version = registration(body=f"Exact source {index}: 😀 <script>untrusted</script>", source_id=f"source-{index}")
        hits.append(Evidence(document_id=document.id, document_version_id=version.id,
            content_sha256=version.content.sha256, start=0, end=len(version.content.text), text=version.content.text,
            source=version.source, source_spans=(), rank=index + 1, score=1.0))
    search = SimpleNamespace(execute=AsyncMock(return_value=tuple(hits)))
    generator = SimpleNamespace(generate=AsyncMock(return_value=GenerationResult(
        status=AnswerStatus.ANSWERED, answer="An answer. [E1] [E2]", citations=("E1", "E2"))))

    @asynccontextmanager
    async def factory():
        yield AnswerQuestion(search, generator)
    return SimpleNamespace(app=create_app(service_factory=factory), search=search, generator=generator, hits=hits)


class Page(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.elements = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


async def test_page_and_local_assets_load_without_retrieval_or_generation(web):
    async with web.app.router.lifespan_context(web.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url="http://test") as client:
            response = await client.get("/")
            assert response.status_code == 200 and "text/html" in response.headers["content-type"]
            assert "AI Investigation Engine" in response.text
            assert "script-src 'self'" in response.headers["content-security-policy"]
            assert response.headers["x-content-type-options"] == "nosniff"
            page = Page(response.text)
            assert ("label", {"for": "question"}) in page.elements
            assert any(tag == "form" and attrs.get("id") == "question-form" for tag, attrs in page.elements)
            assert any(attrs.get("role") == "status" for tag, attrs in page.elements)
            assets = [attrs.get("src") or attrs.get("href") for tag, attrs in page.elements if tag in ("script", "link")]
            assert set(assets) == {"/static/app.js", "/static/admin.js", "/static/styles.css"}
            for asset in assets:
                loaded = await client.get(asset)
                assert loaded.status_code == 200
                assert "javascript" in loaded.headers["content-type"] if asset.endswith(".js") else "text/css" in loaded.headers["content-type"]
            assert (await client.get("/health")).json() == {"status": "ok"}
    web.search.execute.assert_not_called()
    web.generator.generate.assert_not_called()


async def test_static_root_does_not_interpolate_queries_and_cannot_serve_server_files(web):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url="http://test") as client:
        response = await client.get("/", params={"question": "<script>never-interpolate</script>"})
        assert "never-interpolate" not in response.text
        for url in ("/static/.env", "/static/%2e%2e/config.py", "/static/missing.js"):
            assert (await client.get(url)).status_code == 404


async def test_same_app_keeps_question_contract_multiple_sources_and_shared_scope(web):
    async with web.app.router.lifespan_context(web.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url="http://test") as client:
            assert (await client.get("/")).status_code == 200
            response = await client.post("/questions", json={"question": "q"})
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "answered"
            assert [item["evidence_id"] for item in body["citations"]] == ["E1", "E2"]
            for item, hit in zip(body["citations"], web.hits, strict=True):
                assert item["evidence"]["text"] == hit.text
                assert item["evidence"]["document_version_id"] == str(hit.document_version_id)
            assert (await client.post("/questions", json={"question": "q", "owner_id": "private"})).status_code == 422
            assert (await client.post("/questions", json={"question": " "})).status_code == 422
    web.search.execute.assert_awaited_once_with("q", 5, owner_id=None)
    assert sorted(web.app.openapi()["paths"]) == [
        "/documents", "/documents/{document_id}", "/documents/{document_id}/index", "/health", "/investigations", "/questions",
    ]


async def test_insufficient_evidence_and_backend_error_remain_distinct(web):
    async with web.app.router.lifespan_context(web.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url="http://test") as client:
            web.search.execute.return_value = ()
            response = await client.post("/questions", json={"question": "q"})
            assert response.status_code == 200 and response.json()["status"] == "insufficient_evidence"
            web.generator.generate.assert_not_called()
            web.search.execute.return_value = tuple(web.hits)
            web.generator.generate.side_effect = GenerationProviderError("secret provider details")
            response = await client.post("/questions", json={"question": "q"})
            assert response.status_code == 503 and response.json()["error"]["code"] == "generation_unavailable"
            assert "secret provider details" not in response.text


async def test_asset_paths_do_not_depend_on_process_working_directory(web, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url="http://test") as client:
        assert (await client.get("/")).status_code == 200
        assert (await client.get("/static/app.js")).status_code == 200
