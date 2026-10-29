import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from investigation_engine.application.answers import AnswerStatus, GenerationResult
from investigation_engine.application.ports.embedding_errors import EmbeddingResponseError
from investigation_engine.application.ports.generation_errors import (
    GenerationProviderError,
    GenerationResponseError,
    GenerationTimeoutError,
)
from investigation_engine.application.ports.repository_errors import RepositoryError
from investigation_engine.application.retrieval import Evidence
from investigation_engine.application.use_cases.answer_question import AnswerQuestion
from investigation_engine.infrastructure.api.app import create_app


@pytest.fixture
def api(registration):
    document, version = registration(body="Paris is in France.")
    hit = Evidence(
        document_id=document.id,
        document_version_id=version.id,
        content_sha256=version.content.sha256,
        start=0,
        end=len(version.content.text),
        text=version.content.text,
        source=version.source,
        source_spans=(),
        rank=1,
        score=1.0,
    )
    search = SimpleNamespace(execute=AsyncMock(return_value=(hit,)))
    generator = SimpleNamespace(
        generate=AsyncMock(
            return_value=GenerationResult(status=AnswerStatus.ANSWERED, answer="Paris is in France.", citations=("E1",))
        )
    )
    service = AnswerQuestion(search, generator)
    events = []

    @asynccontextmanager
    async def factory():
        events.append("startup")
        try:
            yield service
        finally:
            events.append("shutdown")

    return SimpleNamespace(
        app=create_app(service_factory=factory), search=search, generator=generator, hit=hit, events=events
    )


async def post(api, payload, **kwargs):
    async with api.app.router.lifespan_context(api.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as client:
            return await client.post("/questions", json=payload, **kwargs)


async def test_answer_serialization_health_and_safe_lifecycle(api):
    assert api.events == [] and not hasattr(api.app.state, "questions")
    async with api.app.router.lifespan_context(api.app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as client:
            assert (await client.get("/health")).json() == {"status": "ok"}
            api.search.execute.assert_not_called()
            response = await client.post("/questions", json={"question": "Where?"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "answered" and body["answer"] == "Paris is in France."
    citation = body["citations"][0]
    assert citation["evidence_id"] == "E1"
    assert citation["evidence"]["document_version_id"] == str(api.hit.document_version_id)
    assert citation["evidence"]["source"]["record_locator"] == {"record": 1}
    assert citation["evidence"]["text"] == api.hit.text
    assert citation["evidence"]["start"] == 0 and citation["evidence"]["source_spans"] == []
    assert api.events == ["startup", "shutdown"] and not hasattr(api.app.state, "questions")


async def test_http_total_and_nested_answer_stages_share_trace_without_question_text(api, caplog):
    with caplog.at_level(logging.INFO, logger="investigation_engine.application.timing"):
        response = await post(api, {"question": "private-question-sentinel"})
    assert response.status_code == 200
    records = [record for record in caplog.records if hasattr(record, "stage")]
    assert {record.stage for record in records} >= {"http.request", "answer.total", "answer_generation"}
    assert len({record.args[0] for record in records}) == 1
    assert "private-question-sentinel" not in caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"question": ""},
        {"question": " \n"},
        {"question": "x" * 4001},
        {"question": 1},
        {"question": None},
        {"question": "q", "owner_id": "private-owner"},
        {"question": "q", "model": "unrestricted"},
        {"question": "q", "limit": 100},
    ],
)
async def test_invalid_inputs_do_not_retrieve_generate_or_echo_sensitive_fields(api, payload):
    response = await post(api, payload)
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_request"
    assert "private-owner" not in response.text
    api.search.execute.assert_not_called()
    api.generator.generate.assert_not_called()


async def test_headers_cannot_select_private_scope(api):
    response = await post(
        api, {"question": "Where?"}, headers={"X-Owner-ID": "private-owner", "Authorization": "Bearer fake"}
    )
    assert response.status_code == 401
    api.search.execute.assert_not_called()


async def test_empty_evidence_and_explicit_abstention_are_http_success_outcomes(api):
    api.search.execute.return_value = ()
    response = await post(api, {"question": "Where?"})
    assert response.status_code == 200 and response.json()["status"] == "insufficient_evidence"
    assert response.json()["answer"] is None and response.json()["citations"] == []
    api.generator.generate.assert_not_called()
    api.search.execute.return_value = (api.hit,)
    api.generator.generate.return_value = GenerationResult(
        status=AnswerStatus.INSUFFICIENT_EVIDENCE, answer=None, citations=()
    )
    response = await post(api, {"question": "Where?"})
    assert response.status_code == 200 and response.json()["status"] == "insufficient_evidence"
    assert response.json()["answer"] is None


@pytest.mark.parametrize(
    "error,status,code",
    [
        (GenerationProviderError("never-expose-secret"), 503, "generation_unavailable"),
        (GenerationTimeoutError("never-expose-secret"), 504, "request_timeout"),
        (GenerationResponseError("never-expose-secret"), 502, "invalid_provider_response"),
        (EmbeddingResponseError("never-expose-secret"), 502, "invalid_provider_response"),
        (RepositoryError("never-expose-secret"), 503, "retrieval_unavailable"),
        (RuntimeError("never-expose-secret"), 500, "internal_error"),
    ],
)
async def test_controlled_failure_statuses_do_not_fabricate_answers_or_expose_errors(api, error, status, code):
    api.generator.generate.side_effect = error
    response = await post(api, {"question": "Where?"})
    assert response.status_code == status and response.json()["error"]["code"] == code
    assert "never-expose-secret" not in response.text and "answer" not in response.json()


def test_factory_and_openapi_create_no_clients_connections_or_settings_import(api, monkeypatch):
    from investigation_engine.infrastructure.api import bootstrap

    def forbidden(*args, **kwargs):
        raise AssertionError("Resources must only initialize in lifespan")

    monkeypatch.setattr(bootstrap, "create_async_engine", forbidden)
    monkeypatch.setattr(bootstrap, "GeminiEmbeddingProvider", forbidden)
    monkeypatch.setattr(bootstrap, "GeminiAnswerGenerator", forbidden)
    assert sorted(create_app().openapi()["paths"]) == [
        "/documents",
        "/documents/{document_id}",
        "/documents/{document_id}/index",
        "/health",
        "/investigations",
        "/questions",
    ]


def test_documented_uvicorn_loop_supports_psycopg_on_windows():
    import asyncio

    from uvicorn import Config

    config = Config(
        "investigation_engine.infrastructure.api.app:create_app", factory=True, loop="asyncio:SelectorEventLoop"
    )
    assert config.get_loop_factory() is asyncio.SelectorEventLoop


async def test_response_validation_failure_is_safe_and_distinct_from_abstention(api, monkeypatch):
    async def bad_response(question, *, owner_id):
        return {"status": "answered", "answer": "uncited never-expose-secret", "citations": [], "message": None}

    # The service is installed by lifespan; replace its execute method while
    # retaining the real response-model validation path.
    async with api.app.router.lifespan_context(api.app):
        monkeypatch.setattr(api.app.state.questions, "execute", bad_response)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test") as client:
            response = await client.post("/questions", json={"question": "q"})
    assert response.status_code == 500 and response.json()["error"]["code"] == "internal_error"
    assert "never-expose-secret" not in response.text
