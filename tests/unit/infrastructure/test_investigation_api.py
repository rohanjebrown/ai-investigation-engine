from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from investigation_engine.application.investigations import (
    InvestigateQuestion, InvestigationDecision, InvestigationStatus, SearchEvidenceCall,
)
from investigation_engine.application.ports.generation_errors import GenerationProviderError
from investigation_engine.application.retrieval import Evidence
from investigation_engine.domain.content import Content
from investigation_engine.domain.document import Document
from investigation_engine.domain.document_version import DocumentVersion
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.infrastructure.api.app import create_app


def make_evidence(text="A fact from the shared source."):
    document = Document(owner_id=None)
    version = DocumentVersion(document_id=document.id, version_number=1, content=Content(text),
        source=SourceReference(kind=SourceKind.DATASET, source_id="source", record_locator={"row": 1}))
    return Evidence(document_id=document.id, document_version_id=version.id,
        content_sha256=version.content.sha256, start=0, end=len(text), text=text,
        source=version.source, source_spans=(), rank=1, score=0.8)


class Model:
    def __init__(self, actions):
        self.actions = iter(actions)
    async def next_action(self, turn):
        return next(self.actions)


async def test_investigation_endpoint_returns_search_steps_and_resolved_sources():
    hit = make_evidence()
    class Search:
        def __init__(self): self.scopes = []
        async def execute(self, query, limit, *, owner_id):
            self.scopes.append(owner_id)
            return (hit,)
    search = Search()
    usecase = InvestigateQuestion(search, Model([
        SearchEvidenceCall(query="a fact", call_id="tool-1"),
        InvestigationDecision(status=InvestigationStatus.ANSWERED,
            answer="A fact is established. [E1]", citations=("E1",)),
    ]))
    services = SimpleNamespace(settings=SimpleNamespace(investigation_enabled=True,
        investigation_timeout_seconds=10), investigations=usecase)
    @asynccontextmanager
    async def factory(): yield services
    app = create_app(service_factory=factory)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/investigations", json={"question": "What is established?"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "answered" and body["citations"] == ["E1"]
    assert body["evidence"][0]["evidence"]["source"]["source_id"] == "source"
    assert body["searches"][0]["query"] == "a fact" and body["searches"][0]["evidence_ids"] == ["E1"]
    assert search.scopes == [None]


async def test_investigation_endpoint_uses_only_trusted_admin_scope():
    settings = SimpleNamespace(investigation_enabled=True, investigation_timeout_seconds=10,
        admin_api_token="x" * 40, admin_principal_id="trusted-admin", admin_require_https=False)
    class Capture:
        async def execute(self, question, *, owner_id):
            assert owner_id == "trusted-admin"
            from investigation_engine.application.investigations import InvestigationResult, TokenUsage
            return InvestigationResult(status=InvestigationStatus.INSUFFICIENT_EVIDENCE,
                answer=None, citations=(), conflicts=(), evidence=(), searches=(),
                generation_requests=1, embedding_requests=0, message="No evidence", usage=TokenUsage())
    services = SimpleNamespace(settings=settings, investigations=Capture())
    @asynccontextmanager
    async def factory(): yield services
    app = create_app(service_factory=factory)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/investigations", json={"question": "Question"},
                headers={"Authorization": f"Bearer {'x' * 40}", "X-Owner-ID": "attacker"})
    assert response.status_code == 200 and response.json()["status"] == "insufficient_evidence"


async def test_investigation_disabled_and_provider_failure_are_not_abstentions():
    services = SimpleNamespace(settings=SimpleNamespace(investigation_enabled=False), investigations=None)
    @asynccontextmanager
    async def factory(): yield services
    app = create_app(service_factory=factory)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            disabled = await client.post("/investigations", json={"question": "Question"})
    assert disabled.status_code == 503 and disabled.json()["detail"]["code"] == "investigation_disabled"

    class Broken:
        async def execute(self, *args, **kwargs): raise GenerationProviderError("secret")
    services = SimpleNamespace(settings=SimpleNamespace(investigation_enabled=True), investigations=Broken())
    @asynccontextmanager
    async def factory2(): yield services
    app = create_app(service_factory=factory2)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/investigations", json={"question": "Question"})
    assert response.status_code == 503 and response.json()["error"]["code"] == "generation_unavailable"
    assert "secret" not in response.text
