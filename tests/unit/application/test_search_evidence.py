from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid7

import pytest

from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy
from investigation_engine.application.embeddings import EmbeddingConfig
from investigation_engine.application.ports.embedding_errors import EmbeddingInputError, EmbeddingResponseError
from investigation_engine.application.ports.repository_errors import RepositoryError
from investigation_engine.application.retrieval import Evidence, intersecting_source_spans
from investigation_engine.application.use_cases.search_evidence import SearchEvidence
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import PYTHON_WHITESPACE


@pytest.fixture
def search(tokenizer):
    config = ChunkingConfig(strategy=ChunkingStrategy.WHOLE_PASSAGE, tokenizer=tokenizer.identity, max_tokens=100)
    profile = EmbeddingConfig(provider="synthetic", model_id="known-cosine")
    vector = (1.0,) + (0.0,) * 767
    provider = SimpleNamespace(
        configuration=profile,
        embed_query=AsyncMock(return_value=vector),
        embed_documents=AsyncMock(side_effect=AssertionError("Document formatting used for query")),
    )
    repository = SimpleNamespace(search=AsyncMock(return_value=()))
    return SimpleNamespace(
        use_case=SearchEvidence(repository, provider, config, profile),
        provider=provider,
        repository=repository,
        config=config,
        profile=profile,
        vector=vector,
    )


async def test_exact_query_and_trusted_scope_pass_to_selected_profile(search):
    question = "  Where is 😀?\n"
    result = await search.use_case.execute(question, 3, owner_id="principal:alice")
    assert result == ()
    search.provider.embed_query.assert_awaited_once_with(question)
    search.provider.embed_documents.assert_not_called()
    search.repository.search.assert_awaited_once_with(
        search.vector, search.config, search.profile, owner_id="principal:alice", limit=3
    )


@pytest.mark.parametrize("question", ["", " \t\u0085", None, 17, "\ud800"])
async def test_invalid_question_fails_before_provider_or_repository(search, question):
    with pytest.raises(ValueError):
        await search.use_case.execute(question, owner_id=None)
    search.provider.embed_query.assert_not_called()
    search.repository.search.assert_not_called()


@pytest.mark.parametrize("limit", [0, -1, 101, True, 1.5, "2"])
async def test_result_count_is_strict_and_bounded_before_io(search, limit):
    with pytest.raises(ValueError, match="limit"):
        await search.use_case.execute("question", limit, owner_id=None)
    search.provider.embed_query.assert_not_called()
    search.repository.search.assert_not_called()


@pytest.mark.parametrize("owner", ["", "  ", 1, False])
async def test_invalid_trusted_scope_fails_before_io(search, owner):
    with pytest.raises(ValueError, match="scope"):
        await search.use_case.execute("question", owner_id=owner)
    search.provider.embed_query.assert_not_called()
    search.repository.search.assert_not_called()


async def test_mismatched_provider_profile_is_rejected_before_embedding(search):
    search.provider.configuration = replace(search.profile, model_revision="different")
    with pytest.raises(EmbeddingInputError, match="does not match"):
        await search.use_case.execute("question", owner_id=None)
    search.provider.embed_query.assert_not_called()


async def test_profile_cannot_change_during_query(search):
    async def change_profile(question):
        search.provider.configuration = replace(search.profile, model_id="different")
        return search.vector

    search.provider.embed_query.side_effect = change_profile
    with pytest.raises(EmbeddingInputError, match="changed"):
        await search.use_case.execute("question", owner_id=None)
    search.repository.search.assert_not_called()


@pytest.mark.parametrize(
    "vector",
    [
        (1.0,) * 767,
        (1.0,) * 769,
        (0.0,) * 768,
        (float("nan"),) + (0.0,) * 767,
        (float("inf"),) + (0.0,) * 767,
        (1e100,) * 768,
        (1e-100,) * 768,
    ],
)
async def test_bad_query_vectors_fail_before_database_io(search, vector):
    search.provider.embed_query.return_value = vector
    with pytest.raises(EmbeddingResponseError, match="storage"):
        await search.use_case.execute("question", owner_id=None)
    search.repository.search.assert_not_called()


async def test_provider_and_repository_failures_propagate(search):
    search.provider.embed_query.side_effect = EmbeddingResponseError("invalid provider response")
    with pytest.raises(EmbeddingResponseError, match="provider response"):
        await search.use_case.execute("question", owner_id=None)
    search.repository.search.assert_not_called()
    search.provider.embed_query.side_effect = None
    search.repository.search.side_effect = RepositoryError("database unavailable")
    with pytest.raises(RepositoryError, match="unavailable"):
        await search.use_case.execute("question", owner_id=None)


def test_source_intersections_preserve_original_bounds_and_native_locators():
    spans = tuple(
        SourceSpan(start=a, end=b, locators=(SourceLocator(kind=LocatorKind.SENTENCE, index=i),))
        for i, (a, b) in enumerate(((0, 3), (4, 8), (8, 12)))
    )
    assert intersecting_source_spans(spans, 2, 8) == spans[:2]
    assert intersecting_source_spans(spans, 3, 4) == ()
    assert intersecting_source_spans((), 0, 1) == ()


def test_evidence_preserves_source_identity_with_defensive_provenance_copies():
    source = SourceReference(
        kind=SourceKind.DATASET, source_id="source", record_locator={"context": [1]}, metadata={"labels": ["original"]}
    )
    fields = dict(
        document_id=uuid7(),
        document_version_id=uuid7(),
        content_sha256="0" * 64,
        start=2,
        end=4,
        text="😀a",
        source=source,
        source_spans=(),
        score=0.75,
        rank=1,
    )
    first, second = Evidence(**fields), Evidence(**fields)
    first.source.record_locator["context"].append(2)
    source.metadata["labels"].append("changed")
    assert second.source.record_locator == {"context": [1]}
    assert second.source.metadata == {"labels": ["original"]}
    assert first.identity == (fields["document_version_id"], 2, 4)


def test_sql_blankness_characters_match_python_strip_exactly():
    assert set(PYTHON_WHITESPACE) == {chr(i) for i in range(0x110000) if chr(i).isspace()}


async def test_hybrid_embeds_once_and_requests_independent_bounded_channels(search):
    channels = SimpleNamespace(search_channels=AsyncMock(return_value=((), ())))
    service = SearchEvidence(
        search.repository,
        search.provider,
        search.config,
        search.profile,
        mode="hybrid",
        hybrid_repository=channels,
        semantic_candidates=7,
        keyword_candidates=11,
        rrf_k=20,
    )
    assert await service.execute("  Original query 😀 ", 3, owner_id="trusted") == ()
    search.provider.embed_query.assert_awaited_once_with("  Original query 😀 ")
    channels.search_channels.assert_awaited_once_with(
        search.vector,
        "  Original query 😀 ",
        search.config,
        search.profile,
        owner_id="trusted",
        semantic_limit=7,
        keyword_limit=11,
    )
    search.repository.search.assert_not_called()


async def test_hybrid_channel_failure_does_not_silently_fall_back_to_semantic(search):
    channels = SimpleNamespace(search_channels=AsyncMock(side_effect=RepositoryError("keyword failed")))
    service = SearchEvidence(
        search.repository, search.provider, search.config, search.profile, mode="hybrid", hybrid_repository=channels
    )
    with pytest.raises(RepositoryError, match="keyword failed"):
        await service.execute("q", owner_id=None)
    search.repository.search.assert_not_called()


@pytest.mark.parametrize(
    "options",
    [
        dict(mode="keyword"),
        dict(mode="hybrid"),
        dict(semantic_candidates=0),
        dict(keyword_candidates=101),
        dict(rrf_k=-1),
    ],
)
def test_invalid_server_retrieval_configuration_fails_before_io(search, options):
    with pytest.raises(ValueError):
        SearchEvidence(search.repository, search.provider, search.config, search.profile, **options)
