import asyncio

import pytest
from sqlalchemy import func, select, text

from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy
from investigation_engine.application.document_access import DocumentActor
from investigation_engine.application.ingestion.extracted_document import ExtractedDocument
from investigation_engine.application.ports.embedding_errors import EmbeddingResponseError
from investigation_engine.application.ports.repository_errors import DocumentUpdateConflict, RepositoryError
from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
from investigation_engine.application.use_cases.index_document_version import IndexDocumentVersion
from investigation_engine.application.use_cases.ingest_document import IngestDocument
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.infrastructure.persistence.models import (
    ChunkEmbeddingRow,
    ChunkMembershipRow,
    ChunkPlanReadinessRow,
    ContentRow,
    DocumentRow,
    DocumentVersionRow,
    MembershipChunkEmbeddingRow,
)
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import (
    PostgreSQLChunkPlanRepository,
)
from investigation_engine.infrastructure.persistence.postgresql_document_activation_repository import (
    PostgreSQLDocumentActivationRepository,
)
from investigation_engine.infrastructure.persistence.postgresql_embedding_repository import (
    PostgreSQLEmbeddingRepository,
)
from investigation_engine.infrastructure.persistence.postgresql_indexed_document_repository import (
    PostgreSQLIndexedDocumentRepository,
)


class CharacterTokenizer:
    identity = TokenizerIdentity(name="atomic-test", revision="1", artifact_sha256="2" * 64, runtime_version="test")

    def offsets(self, value):
        return tuple((index, index + 1) for index in range(len(value)))


def make_ingestion(sessions, synthetic_provider, tokenizer=None, chunking=None):
    tokenizer = tokenizer or CharacterTokenizer()
    chunking = chunking or ChunkingConfig(
        strategy=ChunkingStrategy.WHOLE_PASSAGE,
        tokenizer=tokenizer.identity,
        max_tokens=256,
    )
    indexer = IndexDocumentVersion(
        PostgreSQLChunkPlanRepository(sessions),
        PostgreSQLEmbeddingRepository(sessions),
        PostgreSQLDocumentActivationRepository(sessions),
        synthetic_provider,
        max_attempts=1,
        embedding_batch_size=25,
    )
    return IngestDocument(
        PostgreSQLIndexedDocumentRepository(sessions),
        indexer,
        tokenizer,
        chunking,
        synthetic_provider.configuration,
    )


@pytest.fixture
def ingestion(sessions, synthetic_provider):
    return make_ingestion(sessions, synthetic_provider)


def extracted(body, *, source_id, revision=None):
    return ExtractedDocument(
        text=body,
        source=SourceReference(
            kind=SourceKind.UPLOAD,
            source_id=source_id,
            revision=revision,
            record_locator={"upload": source_id},
            display_name="input.txt",
        ),
        title="Input",
    )


async def counts(sessions):
    async with sessions() as session:
        return {
            "contents": await session.scalar(select(func.count()).select_from(ContentRow)),
            "documents": await session.scalar(select(func.count()).select_from(DocumentRow)),
            "versions": await session.scalar(select(func.count()).select_from(DocumentVersionRow)),
            "memberships": await session.scalar(select(func.count()).select_from(ChunkMembershipRow)),
            "shared_embeddings": await session.scalar(select(func.count()).select_from(ChunkEmbeddingRow)),
            "membership_embeddings": await session.scalar(
                select(func.count()).select_from(MembershipChunkEmbeddingRow)
            ),
            "readiness": await session.scalar(select(func.count()).select_from(ChunkPlanReadinessRow)),
        }


async def test_create_commits_fully_indexed_document_and_both_embedding_representations(
    ingestion, sessions, synthetic_provider
):
    document, version = await ingestion.create(
        extracted("Indexed evidence.", source_id="atomic-success"), actor=DocumentActor("alice")
    )

    assert document.current_version_id == version.id
    assert synthetic_provider.calls == ["Indexed evidence."]
    assert await counts(sessions) == {
        "contents": 1,
        "documents": 1,
        "versions": 1,
        "memberships": 1,
        "shared_embeddings": 1,
        "membership_embeddings": 1,
        "readiness": 1,
    }


async def test_embedding_failure_leaves_no_partial_initial_registration(ingestion, sessions, synthetic_provider):
    synthetic_provider.failures = [EmbeddingResponseError("invalid provider response")]
    with pytest.raises(EmbeddingResponseError):
        await ingestion.create(
            extracted("Never persisted.", source_id="atomic-provider-failure"), actor=DocumentActor("alice")
        )
    assert await counts(sessions) == {
        "contents": 0,
        "documents": 0,
        "versions": 0,
        "memberships": 0,
        "shared_embeddings": 0,
        "membership_embeddings": 0,
        "readiness": 0,
    }


async def test_database_failure_rolls_back_content_plan_vectors_readiness_and_activation(
    ingestion, sessions, db_engine
):
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE FUNCTION reject_membership_embedding() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RAISE EXCEPTION 'injected persistence failure'; END $$"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER reject_membership_embedding BEFORE INSERT ON membership_chunk_embeddings "
                "FOR EACH ROW EXECUTE FUNCTION reject_membership_embedding()"
            )
        )
    try:
        with pytest.raises(RepositoryError):
            await ingestion.create(
                extracted("Roll back everything.", source_id="atomic-db-failure"), actor=DocumentActor("alice")
            )
    finally:
        with db_engine.begin() as connection:
            connection.execute(
                text("DROP TRIGGER IF EXISTS reject_membership_embedding ON membership_chunk_embeddings")
            )
            connection.execute(text("DROP FUNCTION IF EXISTS reject_membership_embedding()"))
    assert await counts(sessions) == {
        "contents": 0,
        "documents": 0,
        "versions": 0,
        "memberships": 0,
        "shared_embeddings": 0,
        "membership_embeddings": 0,
        "readiness": 0,
    }


async def test_failed_replacement_keeps_existing_active_version(ingestion, sessions, synthetic_provider):
    document, active = await ingestion.create(
        extracted("Old searchable evidence.", source_id="atomic-update"), actor=DocumentActor("alice")
    )
    synthetic_provider.failures = [EmbeddingResponseError("temporary response failure")]
    with pytest.raises(EmbeddingResponseError):
        await ingestion.replace(
            document.id,
            extracted("Replacement is not ready.", source_id="atomic-update", revision="replacement-2"),
            actor=DocumentActor("alice"),
            expected_current_version_id=active.id,
        )
    async with sessions() as session:
        assert (
            await session.scalar(select(DocumentRow.current_version_id).where(DocumentRow.id == document.id))
            == active.id
        )
        assert await session.scalar(select(func.count()).select_from(DocumentVersionRow)) == 1


async def test_repeated_source_retry_is_idempotent_and_reuses_embedding(ingestion, sessions, synthetic_provider):
    source = extracted("Same exact source text.", source_id="stable-idempotency")
    first = await ingestion.create(source, actor=DocumentActor("alice"))
    provider_calls = tuple(synthetic_provider.calls)
    second = await ingestion.create(source, actor=DocumentActor("alice"))
    assert second[0].id == first[0].id and second[1].id == first[1].id
    assert tuple(synthetic_provider.calls) == provider_calls
    assert (await counts(sessions))["versions"] == 1


async def test_equal_content_deduplicates_content_but_keeps_occurrence_embeddings(
    ingestion, sessions, synthetic_provider
):
    first = await ingestion.create(
        extracted("Shared exact content.", source_id="source-one"), actor=DocumentActor("alice")
    )
    calls = tuple(synthetic_provider.calls)
    second = await ingestion.create(
        extracted("Shared exact content.", source_id="source-two"), actor=DocumentActor("bob")
    )
    assert first[0].id != second[0].id
    assert tuple(synthetic_provider.calls) == calls
    current = await counts(sessions)
    assert current["contents"] == 1 and current["documents"] == current["versions"] == 2
    assert current["shared_embeddings"] == 1
    assert current["membership_embeddings"] == 2


async def test_whitespace_membership_has_no_embedding_but_nonblank_memberships_are_complete(
    sessions, synthetic_provider
):
    tokenizer = CharacterTokenizer()
    chunking = ChunkingConfig(
        strategy=ChunkingStrategy.FIXED_TOKENS,
        tokenizer=tokenizer.identity,
        max_tokens=1,
    )
    workflow = make_ingestion(sessions, synthetic_provider, tokenizer, chunking)
    document, version = await workflow.create(
        extracted("a b", source_id="whitespace-membership"), actor=DocumentActor("alice")
    )
    async with sessions() as session:
        memberships = (
            await session.scalars(
                select(ChunkMembershipRow).where(ChunkMembershipRow.document_version_id == version.id)
            )
        ).all()
        owned = (
            await session.scalars(
                select(MembershipChunkEmbeddingRow).where(MembershipChunkEmbeddingRow.document_version_id == version.id)
            )
        ).all()
        assert len(memberships) == 3
        assert len(owned) == 2
    assert synthetic_provider.calls == ["a", "b"]
    assert document.current_version_id == version.id


async def test_concurrent_replacements_preserve_one_consistent_active_version(ingestion, sessions):
    document, active = await ingestion.create(
        extracted("Initial version.", source_id="atomic-race"), actor=DocumentActor("alice")
    )
    replacements = await asyncio.gather(
        ingestion.replace(
            document.id,
            extracted("First replacement.", source_id="atomic-race", revision="race-a"),
            actor=DocumentActor("alice"),
            expected_current_version_id=active.id,
        ),
        ingestion.replace(
            document.id,
            extracted("Second replacement.", source_id="atomic-race", revision="race-b"),
            actor=DocumentActor("alice"),
            expected_current_version_id=active.id,
        ),
        return_exceptions=True,
    )
    successes = [value for value in replacements if isinstance(value, tuple)]
    failures = [value for value in replacements if isinstance(value, BaseException)]
    assert len(successes) == len(failures) == 1
    assert isinstance(failures[0], DocumentUpdateConflict)
    async with sessions() as session:
        current = await session.scalar(select(DocumentRow.current_version_id).where(DocumentRow.id == document.id))
        assert current == successes[0][1].id
