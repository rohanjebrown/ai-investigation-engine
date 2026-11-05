from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid7

import pytest
from sqlalchemy import func, insert, inspect, select, text
from sqlalchemy.exc import IntegrityError

from investigation_engine.infrastructure.persistence.models import (
    ChunkEmbeddingRow,
    ChunkingConfigurationRow,
    ChunkMembershipRow,
    ContentRow,
    DocumentRow,
    DocumentVersionRow,
    EmbeddingConfigurationRow,
    MembershipChunkEmbeddingRow,
    VersionChunkPlanRow,
)


def _vector():
    return [0.1] + [0.0] * 767


def _membership(connection, rows, *, ordinal=0, start=0, end=5):
    connection.execute(
        insert(ChunkMembershipRow).values(
            document_version_id=rows["version_id"],
            chunking_configuration_id=rows["chunking_id"],
            ordinal=ordinal,
            start=start,
            end=end,
        )
    )


def _membership_embedding(rows, embedding_id):
    return dict(
        document_version_id=rows["version_id"],
        chunking_configuration_id=rows["chunking_id"],
        ordinal=0,
        embedding_configuration_id=embedding_id,
        embedding=_vector(),
        created_at=rows["now"],
    )


def test_membership_has_one_to_many_embedding_relationship(derived_rows):
    relation = inspect(ChunkMembershipRow).relationships["embeddings"]
    assert relation.uselist
    assert relation.mapper.class_ is MembershipChunkEmbeddingRow
    assert {column.name for column in relation.local_columns} == {
        "document_version_id",
        "chunking_configuration_id",
        "ordinal",
    }


def test_membership_accepts_multiple_embedding_configurations(db_engine, derived_rows, embedding_values):
    rows = derived_rows
    second_profile = {**embedding_values, "model_id": "gemini-embedding-alternate"}
    with db_engine.begin() as connection:
        _membership(connection, rows)
        second_id = connection.scalar(
            insert(EmbeddingConfigurationRow).values(**second_profile).returning(EmbeddingConfigurationRow.id)
        )
        connection.execute(
            insert(MembershipChunkEmbeddingRow),
            [
                _membership_embedding(rows, rows["embedding_id"]),
                _membership_embedding(rows, second_id),
            ],
        )
        embeddings = connection.scalars(
            select(MembershipChunkEmbeddingRow.embedding_configuration_id).where(
                MembershipChunkEmbeddingRow.document_version_id == rows["version_id"]
            )
        ).all()

    assert set(embeddings) == {rows["embedding_id"], second_id}


def test_duplicate_membership_configuration_pair_is_rejected(db_engine, derived_rows):
    rows = derived_rows
    with db_engine.begin() as connection:
        _membership(connection, rows)
        connection.execute(
            insert(MembershipChunkEmbeddingRow).values(**_membership_embedding(rows, rows["embedding_id"]))
        )

    with pytest.raises(IntegrityError):
        with db_engine.begin() as connection:
            connection.execute(
                insert(MembershipChunkEmbeddingRow).values(**_membership_embedding(rows, rows["embedding_id"]))
            )


def test_embedding_requires_an_existing_membership(db_engine, derived_rows):
    rows = derived_rows
    invalid = _membership_embedding(rows, rows["embedding_id"])
    invalid["ordinal"] = 9
    with pytest.raises(IntegrityError):
        with db_engine.begin() as connection:
            connection.execute(insert(MembershipChunkEmbeddingRow).values(**invalid))


def test_equal_content_versions_keep_independent_membership_identity(db_engine, derived_rows):
    rows = derived_rows
    second_document_id, second_version_id = uuid7(), uuid7()
    with db_engine.begin() as connection:
        _membership(connection, rows)
        connection.execute(insert(DocumentRow).values(id=second_document_id, owner_id=None, created_at=rows["now"]))
        connection.execute(
            insert(DocumentVersionRow).values(
                id=second_version_id,
                document_id=second_document_id,
                version_number=1,
                content_sha256=rows["digest"],
                created_at=rows["now"],
                kind="dataset",
                source_id="synthetic-second",
                record_locator={"position": 1},
            )
        )
        connection.execute(
            insert(VersionChunkPlanRow).values(
                document_version_id=second_version_id,
                chunking_configuration_id=rows["chunking_id"],
                expected_chunk_count=1,
                created_at=rows["now"],
            )
        )
        connection.execute(
            insert(ChunkMembershipRow).values(
                document_version_id=second_version_id,
                chunking_configuration_id=rows["chunking_id"],
                ordinal=0,
                start=0,
                end=5,
            )
        )
        memberships = connection.execute(
            select(ChunkMembershipRow.document_version_id, ChunkMembershipRow.ordinal).where(
                ChunkMembershipRow.chunking_configuration_id == rows["chunking_id"]
            )
        ).all()

    assert set(memberships) == {(rows["version_id"], 0), (second_version_id, 0)}


def test_whitespace_only_membership_does_not_require_an_embedding(db_engine, derived_rows):
    rows = derived_rows
    document_id, version_id = uuid7(), uuid7()
    whitespace = " \t\n"
    digest = sha256(whitespace.encode()).digest()
    now = datetime.now(UTC)
    with db_engine.begin() as connection:
        connection.execute(insert(ContentRow).values(sha256=digest, text=whitespace))
        connection.execute(insert(DocumentRow).values(id=document_id, owner_id=None, created_at=now))
        connection.execute(
            insert(DocumentVersionRow).values(
                id=version_id,
                document_id=document_id,
                version_number=1,
                content_sha256=digest,
                created_at=now,
                kind="dataset",
                source_id="whitespace-only",
                record_locator={"position": 0},
            )
        )
        connection.execute(
            insert(VersionChunkPlanRow).values(
                document_version_id=version_id,
                chunking_configuration_id=rows["chunking_id"],
                expected_chunk_count=1,
                created_at=now,
            )
        )
        connection.execute(
            insert(ChunkMembershipRow).values(
                document_version_id=version_id,
                chunking_configuration_id=rows["chunking_id"],
                ordinal=0,
                start=0,
                end=len(whitespace),
            )
        )
        assert (
            connection.scalar(
                select(ChunkMembershipRow.ordinal).where(ChunkMembershipRow.document_version_id == version_id)
            )
            == 0
        )
        assert (
            connection.scalar(
                select(MembershipChunkEmbeddingRow.embedding_configuration_id).where(
                    MembershipChunkEmbeddingRow.document_version_id == version_id
                )
            )
            is None
        )


def test_additive_migration_preserves_legacy_documents_and_embeddings(migration_database, migrate):
    engine = migration_database
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION vector"))
    migrate(engine, "0006_readiness_guard")

    now = datetime.now(UTC)
    document_id, version_id = uuid7(), uuid7()
    content = "legacy"
    digest = sha256(content.encode()).digest()
    with engine.begin() as connection:
        connection.execute(insert(ContentRow).values(sha256=digest, text=content))
        connection.execute(insert(DocumentRow).values(id=document_id, owner_id=None, created_at=now))
        connection.execute(
            insert(DocumentVersionRow).values(
                id=version_id,
                document_id=document_id,
                version_number=1,
                content_sha256=digest,
                created_at=now,
                kind="dataset",
                source_id="legacy",
                record_locator={"position": 0},
            )
        )
        chunking_id = connection.scalar(
            insert(ChunkingConfigurationRow)
            .values(
                strategy="whole_passage",
                algorithm_version="1",
                tokenizer_name="synthetic",
                tokenizer_revision="1",
                tokenizer_artifact_sha256=b"t" * 32,
                tokenizer_runtime_version="test",
                max_tokens=10,
                overlap=0,
            )
            .returning(ChunkingConfigurationRow.id)
        )
        embedding_id = connection.scalar(
            insert(EmbeddingConfigurationRow)
            .values(
                provider="gemini_developer_api",
                model_id="gemini-embedding-2",
                model_revision=None,
                dimensions=768,
                document_format="gemini_no_title_v1",
                normalization="none",
                created_at=now,
            )
            .returning(EmbeddingConfigurationRow.id)
        )
        connection.execute(
            insert(VersionChunkPlanRow).values(
                document_version_id=version_id,
                chunking_configuration_id=chunking_id,
                expected_chunk_count=1,
                created_at=now,
            )
        )
        connection.execute(
            insert(ChunkMembershipRow).values(
                document_version_id=version_id,
                chunking_configuration_id=chunking_id,
                ordinal=0,
                start=0,
                end=len(content),
            )
        )
        connection.execute(
            insert(ChunkEmbeddingRow).values(
                embedding_configuration_id=embedding_id,
                content_sha256=digest,
                start=0,
                end=len(content),
                embedding=_vector(),
                created_at=now,
            )
        )

    migrate(engine)
    with engine.connect() as connection:
        assert connection.scalar(select(DocumentRow.id).where(DocumentRow.id == document_id)) == document_id
        assert connection.scalar(select(DocumentVersionRow.id).where(DocumentVersionRow.id == version_id)) == version_id
        assert connection.scalar(select(ChunkEmbeddingRow.embedding_configuration_id)) == embedding_id
        stored_vector = connection.scalar(select(ChunkEmbeddingRow.embedding))
        assert stored_vector[0] == pytest.approx(0.1)
        assert stored_vector[1:] == [0.0] * 767
        assert connection.scalar(select(func.count()).select_from(ChunkMembershipRow)) == 1
        assert connection.scalar(select(MembershipChunkEmbeddingRow.embedding_configuration_id)) is None
        assert connection.scalar(select(func.count()).select_from(ChunkEmbeddingRow)) == 1
        assert connection.scalar(select(func.count()).select_from(MembershipChunkEmbeddingRow)) == 0
