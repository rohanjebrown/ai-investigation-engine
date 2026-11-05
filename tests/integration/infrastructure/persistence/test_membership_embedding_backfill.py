import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, func, insert, select, update

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from scripts.backfill_membership_chunk_embeddings import backfill, preflight, verify

from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy
from investigation_engine.application.document_access import DocumentActor
from investigation_engine.application.ingestion.extracted_document import ExtractedDocument
from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
from investigation_engine.application.use_cases.index_document_version import IndexDocumentVersion
from investigation_engine.application.use_cases.ingest_document import IngestDocument
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.infrastructure.persistence.models import (
    ChunkEmbeddingRow,
    ChunkMembershipRow,
    DocumentVersionRow,
    EmbeddingConfigurationRow,
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
    identity = TokenizerIdentity(name="backfill-test", revision="1", artifact_sha256="3" * 64, runtime_version="test")

    def offsets(self, value):
        return tuple((index, index + 1) for index in range(len(value)))


@pytest.fixture
def ingestion(sessions, synthetic_provider):
    tokenizer = CharacterTokenizer()
    chunking = ChunkingConfig(
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
    )
    return IngestDocument(
        PostgreSQLIndexedDocumentRepository(sessions),
        indexer,
        tokenizer,
        chunking,
        synthetic_provider.configuration,
    )


def extracted(content, source_id, revision=None):
    return ExtractedDocument(
        text=content,
        source=SourceReference(
            kind=SourceKind.UPLOAD,
            source_id=source_id,
            revision=revision,
            record_locator={"upload": source_id},
            display_name="backfill.txt",
        ),
        title="Backfill test",
    )


async def remove_membership_vectors(sessions):
    async with sessions() as session, session.begin():
        await session.execute(delete(MembershipChunkEmbeddingRow))


async def add_profile_and_legacy_vector(sessions, *, model_id, same_profile=False, different_vector=False):
    async with sessions() as session, session.begin():
        original = await session.scalar(select(EmbeddingConfigurationRow).order_by(EmbeddingConfigurationRow.id))
        values = {
            "provider": original.provider,
            "model_id": original.model_id if same_profile else model_id,
            "model_revision": original.model_revision,
            "dimensions": original.dimensions,
            "document_format": original.document_format,
            "normalization": original.normalization,
            "created_at": datetime.now(UTC),
        }
        new_id = await session.scalar(
            insert(EmbeddingConfigurationRow).values(**values).returning(EmbeddingConfigurationRow.id)
        )
        original_vectors = (
            await session.scalars(
                select(ChunkEmbeddingRow)
                .where(ChunkEmbeddingRow.embedding_configuration_id == original.id)
                .order_by(ChunkEmbeddingRow.content_sha256)
            )
        ).all()
        for row in original_vectors:
            vector = list(row.embedding)
            if different_vector:
                vector[1] = 0.25
            session.add(
                ChunkEmbeddingRow(
                    embedding_configuration_id=new_id,
                    content_sha256=row.content_sha256,
                    start=row.start,
                    end=row.end,
                    embedding=vector,
                    created_at=datetime.now(UTC),
                )
            )
        return new_id


async def test_backfill_copies_vectors_and_repeated_run_is_idempotent(ingestion, sessions):
    await ingestion.create(extracted("Historical searchable content.", "backfill-basic"), actor=DocumentActor("alice"))
    await remove_membership_vectors(sessions)

    result = await backfill(sessions, batch_size=1)
    assert result["backfill_complete"] is True
    assert result["inserted_this_run"] == 1
    assert result["verified_relationships"] == 1
    again = await backfill(sessions, batch_size=1)
    assert again["inserted_this_run"] == 0
    assert again["verification_passed"] is True


async def test_duplicate_document_content_maps_each_membership_independently(ingestion, sessions):
    await ingestion.create(extracted("Shared historical content.", "duplicate-source-a"), actor=DocumentActor("alice"))
    await ingestion.create(extracted("Shared historical content.", "duplicate-source-b"), actor=DocumentActor("bob"))
    await remove_membership_vectors(sessions)

    report = await backfill(sessions)
    assert report["inserted_this_run"] == 2
    async with sessions() as session:
        members = await session.scalar(select(func.count()).select_from(MembershipChunkEmbeddingRow))
        assert members == 2


async def test_backfill_preserves_multiple_and_equivalent_configuration_profiles(ingestion, sessions):
    await ingestion.create(extracted("Configuration content.", "profile-backfill"), actor=DocumentActor("alice"))
    await add_profile_and_legacy_vector(sessions, model_id="alternate-embedding-model")
    await add_profile_and_legacy_vector(sessions, model_id="duplicate", same_profile=True)
    await remove_membership_vectors(sessions)

    report = await backfill(sessions)
    assert report["legacy_matches"] == 2
    assert report["duplicate_compatible_matches"] == 1
    assert report["ambiguous_vector_matches"] == 0
    async with sessions() as session:
        rows = (
            await session.scalars(
                select(MembershipChunkEmbeddingRow).order_by(MembershipChunkEmbeddingRow.embedding_configuration_id)
            )
        ).all()
        assert len(rows) == 2
        assert rows[0].embedding_configuration_id < rows[1].embedding_configuration_id


async def test_whitespace_memberships_remain_without_owned_embeddings(sessions, synthetic_provider):
    tokenizer = CharacterTokenizer()
    chunking = ChunkingConfig(
        strategy=ChunkingStrategy.FIXED_TOKENS,
        tokenizer=tokenizer.identity,
        max_tokens=1,
    )
    indexer = IndexDocumentVersion(
        PostgreSQLChunkPlanRepository(sessions),
        PostgreSQLEmbeddingRepository(sessions),
        PostgreSQLDocumentActivationRepository(sessions),
        synthetic_provider,
        max_attempts=1,
    )
    workflow = IngestDocument(
        PostgreSQLIndexedDocumentRepository(sessions),
        indexer,
        tokenizer,
        chunking,
        synthetic_provider.configuration,
    )
    _, version = await workflow.create(extracted("a b", "whitespace-backfill"), actor=DocumentActor("alice"))
    await remove_membership_vectors(sessions)
    report = await backfill(sessions)
    assert report["whitespace_memberships"] == 1
    async with sessions() as session:
        members = (
            await session.scalars(
                select(ChunkMembershipRow).where(ChunkMembershipRow.document_version_id == version.id)
            )
        ).all()
        vectors = (
            await session.scalars(
                select(MembershipChunkEmbeddingRow).where(MembershipChunkEmbeddingRow.document_version_id == version.id)
            )
        ).all()
        assert len(members) == 3 and len(vectors) == 2


async def test_unindexed_historical_membership_is_reported_without_fabricating_vector(ingestion, sessions):
    _, version = await ingestion.create(
        extracted("No legacy vector remains.", "incomplete-backfill"), actor=DocumentActor("alice")
    )
    async with sessions() as session, session.begin():
        await session.execute(
            delete(ChunkEmbeddingRow).where(
                ChunkEmbeddingRow.content_sha256
                == select(DocumentVersionRow.content_sha256)
                .where(DocumentVersionRow.id == version.id)
                .scalar_subquery()
            )
        )
        await session.execute(delete(MembershipChunkEmbeddingRow))
    report = await preflight_from_sessions(sessions)
    assert report["eligible_memberships_without_legacy_vectors"] == 1
    result = await backfill(sessions)
    assert result["inserted_this_run"] == 0
    assert result["verification_passed"] is True


async def test_partial_execution_resumes_and_concurrent_dual_write_is_safe(ingestion, sessions):
    source = extracted("Concurrent occurrence content.", "resume-backfill-a")
    await ingestion.create(source, actor=DocumentActor("alice"))
    await ingestion.create(extracted(source.text, "resume-backfill-b"), actor=DocumentActor("bob"))
    await remove_membership_vectors(sessions)

    partial = await backfill(sessions, batch_size=1, max_batches=1)
    assert partial["backfill_complete"] is False
    resumed, _ = await asyncio.gather(
        backfill(sessions, batch_size=1),
        ingestion.create(extracted(source.text, "resume-backfill-c"), actor=DocumentActor("charlie")),
    )
    assert resumed["backfill_complete"] is True
    assert (await verify_from_sessions(sessions))["verification_passed"] is True


async def verify_from_sessions(sessions):
    async with sessions() as session, session.begin():
        return await verify(session)


async def test_conflicting_equivalent_vectors_and_unexpected_configuration_are_refused(ingestion, sessions):
    await ingestion.create(extracted("Conflicting vectors.", "conflict-backfill"), actor=DocumentActor("alice"))
    await add_profile_and_legacy_vector(
        sessions, model_id="duplicate-conflict", same_profile=True, different_vector=True
    )
    await remove_membership_vectors(sessions)
    report = await preflight_from_sessions(sessions)
    assert report["ambiguous_vector_matches"] == 1
    with pytest.raises(RuntimeError, match="Preflight inconsistencies"):
        await backfill(sessions)


async def test_unexpected_membership_configuration_identity_is_reported(ingestion, sessions):
    _, version = await ingestion.create(
        extracted("Unexpected configuration identity.", "unexpected-backfill"), actor=DocumentActor("alice")
    )
    async with sessions() as session, session.begin():
        configuration = await session.scalar(select(EmbeddingConfigurationRow).order_by(EmbeddingConfigurationRow.id))
        alternate_id = await session.scalar(
            insert(EmbeddingConfigurationRow)
            .values(
                provider=configuration.provider,
                model_id="unmapped-profile",
                model_revision=configuration.model_revision,
                dimensions=configuration.dimensions,
                document_format=configuration.document_format,
                normalization=configuration.normalization,
                created_at=datetime.now(UTC),
            )
            .returning(EmbeddingConfigurationRow.id)
        )
        membership = await session.scalar(
            select(ChunkMembershipRow).where(ChunkMembershipRow.document_version_id == version.id)
        )
        session.add(
            MembershipChunkEmbeddingRow(
                document_version_id=membership.document_version_id,
                chunking_configuration_id=membership.chunking_configuration_id,
                ordinal=membership.ordinal,
                embedding_configuration_id=alternate_id,
                embedding=[0.1] + [0.0] * 767,
                created_at=datetime.now(UTC),
            )
        )
    report = await preflight_from_sessions(sessions)
    assert report["unexpected_membership_embeddings"] == 1
    with pytest.raises(RuntimeError, match="Preflight inconsistencies"):
        await backfill(sessions)


async def test_preflight_refuses_membership_range_outside_content(ingestion, sessions):
    _, version = await ingestion.create(
        extracted("Boundary validation content.", "invalid-boundary-backfill"), actor=DocumentActor("alice")
    )
    async with sessions() as session, session.begin():
        membership = await session.scalar(
            select(ChunkMembershipRow).where(ChunkMembershipRow.document_version_id == version.id)
        )
        await session.execute(
            update(ChunkMembershipRow)
            .where(
                ChunkMembershipRow.document_version_id == membership.document_version_id,
                ChunkMembershipRow.chunking_configuration_id == membership.chunking_configuration_id,
                ChunkMembershipRow.ordinal == membership.ordinal,
            )
            .values(end=membership.end + 1)
        )
    report = await preflight_from_sessions(sessions)
    assert report["invalid_membership_boundaries"] == 1
    with pytest.raises(RuntimeError, match="Preflight inconsistencies"):
        await backfill(sessions)


async def preflight_from_sessions(sessions):
    async with sessions() as session, session.begin():
        return await preflight(session)
