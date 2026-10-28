import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid7

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.pool import Pool

from investigation_engine.application.answers import AnswerStatus, GenerationResult
from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy, chunk_document
from investigation_engine.application.embeddings import EmbeddingConfig
from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
from investigation_engine.application.use_cases.index_document_version import IndexDocumentVersion
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.infrastructure.api import bootstrap
from investigation_engine.infrastructure.api.app import create_app
from investigation_engine.infrastructure.persistence.models import ChunkingConfigurationRow
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import PostgreSQLChunkPlanRepository
from investigation_engine.infrastructure.persistence.postgresql_document_activation_repository import PostgreSQLDocumentActivationRepository
from investigation_engine.infrastructure.persistence.postgresql_embedding_repository import PostgreSQLEmbeddingRepository


@pytest.mark.parametrize("include_shared", [True, False])
@pytest.mark.parametrize("retrieval_mode", ["semantic", "hybrid"])
async def test_real_registration_through_http_rag_keeps_private_sources_out_and_releases_transactions(
    repository, registration, sessions, db_engine, monkeypatch, include_shared, retrieval_mode,
):
    class Tokenizer:
        identity = TokenizerIdentity(name="characters", revision="1", artifact_sha256="0" * 64, runtime_version="test")

        def offsets(self, text):
            return tuple((i, i + 1) for i in range(len(text)))

    class Embedding:
        configuration = EmbeddingConfig(provider="synthetic", model_id="rag-integration", model_revision="1")
        aclose = AsyncMock()

        async def embed_documents(self, texts):
            return tuple((1.0, 0.0 if "PRIVATE" in text else 1.0) + (0.0,) * 766 for text in texts)

        async def embed_query(self, query):
            return (1.0,) + (0.0,) * 767

    embeddings = Embedding()
    plans = PostgreSQLChunkPlanRepository(sessions)
    store = PostgreSQLEmbeddingRepository(sessions)
    activation = PostgreSQLDocumentActivationRepository(sessions)
    index = IndexDocumentVersion(plans, store, activation, embeddings, max_attempts=1)
    config = ChunkingConfig(strategy=ChunkingStrategy.WHOLE_PASSAGE, tokenizer=Tokenizer.identity, max_tokens=128)
    body = "A😀 is described by the shared source."
    shared = None
    # Include a private occurrence of identical Content and a closer private vector.
    for text, owner in [(body, None if include_shared else "private-owner"),
                        (body, "another-owner"), ("PRIVATE details must never reach the LLM", "private-owner")]:
        span = SourceSpan(start=0, end=len(text), locators=(SourceLocator(kind=LocatorKind.SENTENCE, index=3),))
        document, version = await repository.register(*registration(
            body=text, owner_id=owner, source_id=str(uuid7()), source_spans=(span,)))
        await plans.save(chunk_document(version, config=config, tokenizer=Tokenizer()))
        await index.execute(document.id, version.id, config, embeddings.configuration)
        if owner is None:
            shared = document, version

    with db_engine.connect() as connection:
        config_id = connection.scalar(select(ChunkingConfigurationRow.id))
    active = 0
    requests = []

    def checkout(*args):
        nonlocal active
        active += 1

    def checkin(*args):
        nonlocal active
        active -= 1

    class Generator:
        aclose = AsyncMock()

        async def generate(self, request):
            assert active == 0
            await asyncio.sleep(0)
            assert active == 0
            requests.append(request)
            assert len(request.evidence) == 1 and request.evidence[0].text == body
            return GenerationResult(status=AnswerStatus.ANSWERED, answer="The shared source describes A😀. [E1]", citations=("E1",))

    generator = Generator()
    # Actual production composition/lifespan, but settings and AI constructors
    # are replaced. Every database URL originates in the disposable fixture.
    settings = SimpleNamespace(database_url=lambda: db_engine.url, gemini_api_key="synthetic-only",
        gemini_generation_model="synthetic-only", serving_chunking_configuration_id=config_id,
        generation_timeout_seconds=30, generation_max_attempts=1, generation_max_output_tokens=1024,
        rag_max_evidence=5, rag_max_context_chars=12000, retrieval_mode=retrieval_mode,
        semantic_candidate_limit=20, keyword_candidate_limit=20, rrf_k=60)
    monkeypatch.setitem(sys.modules, "investigation_engine.infrastructure.config", SimpleNamespace(settings=settings))
    monkeypatch.setattr(bootstrap, "GeminiEmbeddingProvider", lambda **kwargs: embeddings)
    monkeypatch.setattr(bootstrap, "GeminiAnswerGenerator", lambda **kwargs: generator)
    app = create_app()
    event.listen(Pool, "checkout", checkout)
    event.listen(Pool, "checkin", checkin)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                assert (await client.get("/health")).status_code == 200
                page = await client.get("/")
                assert page.status_code == 200 and "AI Investigation Engine" in page.text
                assert (await client.get("/static/app.js")).status_code == 200
                response = await client.post("/questions", json={"question": "What describes A😀?"},
                                             headers={"X-Owner-ID": "private-owner"})
        assert active == 0
    finally:
        event.remove(Pool, "checkout", checkout)
        event.remove(Pool, "checkin", checkin)
    assert response.status_code == 200
    result = response.json()
    if include_shared:
        assert result["status"] == "answered" and len(requests) == 1
        hit = result["citations"][0]["evidence"]
        assert hit["document_id"] == str(shared[0].id) and hit["document_version_id"] == str(shared[1].id)
        assert hit["text"] == body and (hit["start"], hit["end"]) == (0, len(body))
        assert hit["source"]["source_id"] == shared[1].source.source_id
        assert hit["source_spans"][0]["locators"] == [{"kind": "sentence", "index": 3}]
        assert hit["score_kind"] == ("rrf" if retrieval_mode=="hybrid" else "cosine_similarity")
        if retrieval_mode=="hybrid":
            assert hit["semantic_score"] is not None and hit["keyword_score"] is not None
    else:
        assert result["status"] == "insufficient_evidence" and result["answer"] is None
        assert result["citations"] == [] and requests == []
    embeddings.aclose.assert_awaited_once()
    generator.aclose.assert_awaited_once()
