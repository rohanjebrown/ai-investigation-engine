from uuid import uuid7

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import insert, inspect, text, update
from sqlalchemy.exc import DataError, IntegrityError

from investigation_engine.infrastructure.persistence.models import (
    Base,
    ChunkEmbeddingRow,
    ChunkingConfigurationRow,
    ChunkMembershipRow,
    DocumentRow,
    DocumentVersionRow,
    EmbeddingConfigurationRow,
)


def test_migrated_schema_matches_mappings_and_only_essential_indexes(db_engine):
    with db_engine.connect() as connection:
        # Migration 0004 owns this optional extension index outside ORM metadata.
        bm25 = connection.scalar(text("SELECT indexdef FROM pg_indexes WHERE indexname='searchable_chunks_bm25_idx'"))
        assert bm25 and "USING bm25 (text)" in bm25 and "english" in bm25
        assert not compare_metadata(
            MigrationContext.configure(
                connection,
                opts={
                    "compare_type": True,
                    "compare_server_default": True,
                    "include_object": lambda obj, name, kind, reflected, compare_to: (
                        not (kind == "index" and name == "searchable_chunks_bm25_idx" and reflected)
                    ),
                },
            ),
            Base.metadata,
        )
        inspector = inspect(connection)
        for table in Base.metadata.tables.values():
            assert inspector.get_pk_constraint(table.name)["constrained_columns"] == [
                col.name for col in table.primary_key.columns
            ]
            assert {check["name"] for check in inspector.get_check_constraints(table.name)} == {
                c.name for c in table.constraints if c.__class__.__name__ == "CheckConstraint"
            }
        derived = [
            "chunking_configurations",
            "embedding_configurations",
            "version_chunk_plans",
            "chunk_memberships",
            "chunk_embeddings",
        ]
        indexes = connection.scalars(
            text("SELECT indexname FROM pg_indexes WHERE schemaname='public' AND tablename=ANY(:tables)"),
            {"tables": derived},
        ).all()
        assert set(indexes) == {"pk_" + name for name in derived} | {"uq_chunk_memberships_range"}


@pytest.mark.parametrize(
    "changes,constraint",
    [
        ({"max_tokens": 0, "overlap": 0}, "ck_chunking_configurations_budget"),
        ({"overlap": 4}, "ck_chunking_configurations_overlap"),
        ({"strategy": "sentence_packing"}, "ck_chunking_configurations_overlap"),
        ({"strategy": "unknown", "overlap": 0}, "ck_chunking_configurations_strategy"),
        ({"tokenizer_artifact_sha256": b"short"}, "ck_chunking_configurations_tokenizer_digest"),
    ],
)
def test_invalid_chunking_configuration_is_rejected(db_engine, chunking_values, changes, constraint):
    with pytest.raises(IntegrityError) as caught, db_engine.begin() as connection:
        connection.execute(insert(ChunkingConfigurationRow).values(**(chunking_values | changes)))
    assert caught.value.orig.diag.constraint_name == constraint


@pytest.mark.parametrize(
    "changes,constraint",
    [
        ({"dimensions": 767}, "ck_embedding_configurations_dimensions"),
        ({"document_format": "title_aware"}, "ck_embedding_configurations_document_format"),
        ({"normalization": "l2"}, "ck_embedding_configurations_normalization"),
        ({"model_id": " "}, "ck_embedding_configurations_identifiers"),
    ],
)
def test_invalid_embedding_configuration_is_rejected(db_engine, embedding_values, changes, constraint):
    with pytest.raises(IntegrityError) as caught, db_engine.begin() as connection:
        connection.execute(insert(EmbeddingConfigurationRow).values(**(embedding_values | changes)))
    assert caught.value.orig.diag.constraint_name == constraint


@pytest.mark.parametrize(
    "changes,constraint",
    [
        ({"start": -1}, "ck_chunk_memberships_range"),
        ({"end": 0}, "ck_chunk_memberships_range"),
        ({"ordinal": -1}, "ck_chunk_memberships_ordinal"),
        ({"document_version_id": uuid7()}, "fk_chunk_memberships_plan"),
    ],
)
def test_memberships_require_valid_ranges_and_their_actual_plan(db_engine, derived_rows, changes, constraint):
    values = dict(
        document_version_id=derived_rows["version_id"],
        chunking_configuration_id=derived_rows["chunking_id"],
        ordinal=0,
        start=0,
        end=2,
    )
    with pytest.raises(IntegrityError) as caught, db_engine.begin() as connection:
        connection.execute(insert(ChunkMembershipRow).values(**(values | changes)))
    assert caught.value.orig.diag.constraint_name == constraint


@pytest.mark.parametrize(
    "changes,constraint", [({"end": 1}, "pk_chunk_memberships"), ({"ordinal": 1}, "uq_chunk_memberships_range")]
)
def test_duplicate_ordered_membership_or_range_is_rejected(db_engine, derived_rows, changes, constraint):
    values = dict(
        document_version_id=derived_rows["version_id"],
        chunking_configuration_id=derived_rows["chunking_id"],
        ordinal=0,
        start=0,
        end=2,
    )
    with db_engine.begin() as connection:
        connection.execute(insert(ChunkMembershipRow).values(**values))
    with pytest.raises(IntegrityError) as caught, db_engine.begin() as connection:
        connection.execute(insert(ChunkMembershipRow).values(**(values | changes)))
    assert caught.value.orig.diag.constraint_name == constraint


@pytest.mark.parametrize(
    "vector,error_type,constraint",
    [
        ([1.0] * 767, DataError, None),
        ([0.0] * 768, IntegrityError, "ck_chunk_embeddings_nonzero"),
        ([float("nan")] + [0.0] * 767, DataError, None),
        ([float("inf")] + [0.0] * 767, DataError, None),
    ],
)
def test_embedding_vectors_must_be_finite_nonzero_and_768_dimensional(
    db_engine, derived_rows, vector, error_type, constraint
):
    with pytest.raises(error_type) as caught, db_engine.begin() as connection:
        # Bypass the Python vector binder so PostgreSQL, rather than client-side
        # dimension validation, must reject the invalid stored representation.
        connection.execute(
            insert(ChunkEmbeddingRow).values(
                embedding_configuration_id=derived_rows["embedding_id"],
                content_sha256=derived_rows["digest"],
                start=0,
                end=2,
                embedding=text("CAST(:test_vector AS vector)"),
                created_at=derived_rows["now"],
            ),
            {"test_vector": "[" + ",".join(str(value) for value in vector) + "]"},
        )
    if constraint:
        assert caught.value.orig.diag.constraint_name == constraint
    else:
        assert caught.value.orig.sqlstate == "22000"


def test_equivalent_embedding_key_cannot_be_stored_twice(db_engine, derived_rows):
    values = dict(
        embedding_configuration_id=derived_rows["embedding_id"],
        content_sha256=derived_rows["digest"],
        start=0,
        end=2,
        embedding=[1.0] + [0.0] * 767,
        created_at=derived_rows["now"],
    )
    with db_engine.begin() as connection:
        connection.execute(insert(ChunkEmbeddingRow).values(**values))
    with pytest.raises(IntegrityError) as caught, db_engine.begin() as connection:
        connection.execute(insert(ChunkEmbeddingRow).values(**values))
    assert caught.value.orig.diag.constraint_name == "pk_chunk_embeddings"


def test_document_cannot_activate_another_documents_version(db_engine, derived_rows):
    other_id = uuid7()
    with db_engine.begin() as connection:
        connection.execute(insert(DocumentRow).values(id=other_id, owner_id=None, created_at=derived_rows["now"]))
    with pytest.raises(IntegrityError) as caught, db_engine.begin() as connection:
        connection.execute(
            update(DocumentRow).where(DocumentRow.id == other_id).values(current_version_id=derived_rows["version_id"])
        )
    assert caught.value.orig.diag.constraint_name == "fk_documents_current_owned_version"


@pytest.mark.parametrize(
    "field,value,constraint",
    [
        ("record_locator", [], "ck_document_versions_record_locator_object"),
        ("source_metadata", [], "ck_document_versions_metadata_object"),
        ("structure", {}, "ck_document_versions_structure_array"),
        ("source_spans", {}, "ck_document_versions_source_spans_array"),
    ],
)
def test_version_provenance_and_span_json_preserves_top_level_shapes(db_engine, derived_rows, field, value, constraint):
    with pytest.raises(IntegrityError) as caught, db_engine.begin() as connection:
        connection.execute(
            update(DocumentVersionRow)
            .where(DocumentVersionRow.id == derived_rows["version_id"])
            .values(**{field: value})
        )
    assert caught.value.orig.diag.constraint_name == constraint


def test_membership_resolves_its_versions_content_and_unicode_slice(db_engine, derived_rows):
    with db_engine.begin() as connection:
        connection.execute(
            insert(ChunkMembershipRow).values(
                document_version_id=derived_rows["version_id"],
                chunking_configuration_id=derived_rows["chunking_id"],
                ordinal=0,
                start=0,
                end=2,
            )
        )
        row = connection.execute(
            text("""SELECT v.id, v.record_locator, substring(c.text FROM m.start + 1 FOR m."end" - m.start)
            FROM chunk_memberships m JOIN version_chunk_plans p USING (document_version_id, chunking_configuration_id)
            JOIN document_versions v ON v.id=p.document_version_id JOIN contents c ON c.sha256=v.content_sha256""")
        ).one()
    assert row == (derived_rows["version_id"], {"position": 0}, "A😀")
    assert "content_sha256" not in ChunkMembershipRow.__table__.c
