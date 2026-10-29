import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy
from investigation_engine.application.embeddings import EmbeddingConfig
from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
from investigation_engine.infrastructure.api import bootstrap


@pytest.fixture
def resources(monkeypatch):
    events = []
    config = ChunkingConfig(
        strategy=ChunkingStrategy.WHOLE_PASSAGE,
        max_tokens=128,
        tokenizer=TokenizerIdentity(name="test", revision="1", artifact_sha256="0" * 64, runtime_version="test"),
    )
    settings = SimpleNamespace(
        gemini_api_key="never-expose-secret",
        gemini_generation_model="explicit-model",
        serving_chunking_configuration_id=7,
        generation_timeout_seconds=30,
        generation_max_attempts=2,
        generation_max_output_tokens=1024,
        rag_max_evidence=5,
        rag_max_context_chars=12000,
        database_url=Mock(return_value="synthetic-only"),
        retrieval_mode="semantic",
        semantic_candidate_limit=20,
        keyword_candidate_limit=20,
        rrf_k=60,
        chunking_tokenizer_path=Path("tokenizer.json"),
    )
    monkeypatch.setitem(sys.modules, "investigation_engine.infrastructure.config", SimpleNamespace(settings=settings))
    engine = SimpleNamespace(dispose=AsyncMock(side_effect=lambda: events.append("engine")))
    embed = SimpleNamespace(
        configuration=EmbeddingConfig(provider="synthetic", model_id="test"),
        aclose=AsyncMock(side_effect=lambda: events.append("embedding")),
    )
    generate = SimpleNamespace(aclose=AsyncMock(side_effect=lambda: events.append("generation")))
    engine_factory = Mock(return_value=engine)
    monkeypatch.setattr(bootstrap, "create_async_engine", engine_factory)
    monkeypatch.setattr(bootstrap, "GeminiEmbeddingProvider", Mock(return_value=embed))
    monkeypatch.setattr(bootstrap, "GeminiAnswerGenerator", Mock(return_value=generate))
    monkeypatch.setattr(bootstrap, "_configuration", Mock(return_value=config))

    class Session:
        get = AsyncMock(return_value=object())

        async def __aenter__(self):
            events.append("session_open")
            return self

        async def __aexit__(self, *args):
            events.append("session_closed")

    session = Session()
    monkeypatch.setattr(bootstrap, "async_sessionmaker", Mock(return_value=lambda: session))
    return SimpleNamespace(
        settings=settings,
        engine=engine,
        embed=embed,
        generate=generate,
        engine_factory=engine_factory,
        session=session,
        events=events,
    )


async def test_lifespan_closes_profile_lookup_before_serving_and_resources_in_reverse_order(resources):
    r = resources
    async with bootstrap.question_service() as service:
        assert r.events == ["session_open", "session_closed"]
        assert service is not None
    assert r.events == ["session_open", "session_closed", "generation", "embedding", "engine"]
    assert r.session.get.await_args.args[1] == 7


async def test_failed_profile_lookup_closes_every_created_resource_without_raw_errors(resources):
    r = resources
    r.session.get.side_effect = RuntimeError("never-expose-secret")
    with pytest.raises(RuntimeError, match="RAG lifecycle failed") as caught:
        async with bootstrap.question_service():
            pytest.fail("Must not serve on incomplete startup")
    assert "never-expose-secret" not in str(caught.value)
    assert r.events[-3:] == ["generation", "embedding", "engine"]


async def test_missing_serving_configuration_row_fails_startup(resources):
    resources.session.get.return_value = None
    with pytest.raises(RuntimeError, match="RAG lifecycle failed"):
        async with bootstrap.question_service():
            pytest.fail("Must not serve with a missing configuration row")


async def test_incompatible_tokenizer_profile_fails_startup(resources, monkeypatch):
    incompatible = SimpleNamespace(
        identity=TokenizerIdentity(
            name="other-tokenizer", revision="1", artifact_sha256="1" * 64, runtime_version="test"
        )
    )
    monkeypatch.setattr(bootstrap, "HuggingFaceTokenizer", Mock(return_value=incompatible))
    with pytest.raises(RuntimeError, match="Application lifecycle failed"):
        async with bootstrap.application_service():
            pytest.fail("Must not serve with a mismatched tokenizer")
    assert resources.events[-3:] == ["generation", "embedding", "engine"]


async def test_missing_model_fails_before_creating_clients_or_engine(resources):
    resources.settings.gemini_generation_model = None
    with pytest.raises(RuntimeError, match="GEMINI_GENERATION_MODEL"):
        async with bootstrap.question_service():
            pytest.fail("Must not start without explicit model")
    resources.engine_factory.assert_not_called()


async def test_partial_startup_failure_still_disposes_existing_resources(resources, monkeypatch):
    def fail(**kwargs):
        raise ValueError("never-expose-secret")

    monkeypatch.setattr(bootstrap, "GeminiAnswerGenerator", fail)
    with pytest.raises(RuntimeError, match="RAG lifecycle failed"):
        async with bootstrap.question_service():
            pytest.fail("Must not start with a failed provider")
    assert resources.events == ["embedding", "engine"]
