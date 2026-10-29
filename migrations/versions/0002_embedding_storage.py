"""Create the approved chunk-plan and embedding storage foundation.

Revision ID: 0002_embedding_storage
Revises: 0001_document_registration
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision = "0002_embedding_storage"
down_revision = "0001_document_registration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Also emitted in offline SQL. Installing/enabling extensions is outside
    # this migration's authority; fail before creating any derived tables.
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extname = 'vector')
               OR pg_catalog.to_regtype('vector') IS NULL
               OR pg_catalog.to_regprocedure('vector_norm(vector)') IS NULL
               OR pg_catalog.to_regprocedure('vector_dims(vector)') IS NULL THEN
                RAISE EXCEPTION 'pgvector must already be installed and visible, with vector_norm and vector_dims available';
            END IF;
        END $$;
    """)
    op.create_table(
        "chunking_configurations",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column("strategy", sa.Text(collation="C"), nullable=False),
        sa.Column("algorithm_version", sa.Text(collation="C"), nullable=False),
        sa.Column("tokenizer_name", sa.Text(collation="C"), nullable=False),
        sa.Column("tokenizer_revision", sa.Text(collation="C"), nullable=False),
        sa.Column("tokenizer_artifact_sha256", postgresql.BYTEA(), nullable=False),
        sa.Column("tokenizer_runtime_version", sa.Text(collation="C"), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("overlap", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_chunking_configurations"),
        sa.CheckConstraint(
            "strategy IN ('whole_passage', 'fixed_tokens', 'sentence_packing')",
            name="ck_chunking_configurations_strategy",
        ),
        sa.CheckConstraint(
            "btrim(algorithm_version) <> '' AND btrim(tokenizer_name) <> '' "
            "AND btrim(tokenizer_revision) <> '' AND btrim(tokenizer_runtime_version) <> ''",
            name="ck_chunking_configurations_identifiers",
        ),
        sa.CheckConstraint(
            "octet_length(tokenizer_artifact_sha256) = 32",
            name="ck_chunking_configurations_tokenizer_digest",
        ),
        sa.CheckConstraint("max_tokens > 0", name="ck_chunking_configurations_budget"),
        sa.CheckConstraint(
            "overlap >= 0 AND overlap < max_tokens AND (strategy = 'fixed_tokens' OR overlap = 0)",
            name="ck_chunking_configurations_overlap",
        ),
    )
    op.create_table(
        "embedding_configurations",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column("provider", sa.Text(collation="C"), nullable=False),
        sa.Column("model_id", sa.Text(collation="C"), nullable=False),
        sa.Column("model_revision", sa.Text(collation="C"), nullable=True),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("document_format", sa.Text(collation="C"), nullable=False),
        sa.Column("normalization", sa.Text(collation="C"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_embedding_configurations"),
        sa.CheckConstraint(
            "btrim(provider) <> '' AND btrim(model_id) <> '' "
            "AND (model_revision IS NULL OR btrim(model_revision) <> '')",
            name="ck_embedding_configurations_identifiers",
        ),
        sa.CheckConstraint("dimensions = 768", name="ck_embedding_configurations_dimensions"),
        sa.CheckConstraint(
            "document_format = 'gemini_no_title_v1'",
            name="ck_embedding_configurations_document_format",
        ),
        sa.CheckConstraint("normalization = 'none'", name="ck_embedding_configurations_normalization"),
    )
    op.create_table(
        "version_chunk_plans",
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunking_configuration_id", sa.Integer(), nullable=False),
        sa.Column("expected_chunk_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "document_version_id",
            "chunking_configuration_id",
            name="pk_version_chunk_plans",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name="fk_version_chunk_plans_version",
        ),
        sa.ForeignKeyConstraint(
            ["chunking_configuration_id"],
            ["chunking_configurations.id"],
            name="fk_version_chunk_plans_configuration",
        ),
        sa.CheckConstraint("expected_chunk_count >= 0", name="ck_version_chunk_plans_count"),
    )
    op.create_table(
        "chunk_memberships",
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunking_configuration_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("end", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "document_version_id",
            "chunking_configuration_id",
            "ordinal",
            name="pk_chunk_memberships",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id", "chunking_configuration_id"],
            ["version_chunk_plans.document_version_id", "version_chunk_plans.chunking_configuration_id"],
            name="fk_chunk_memberships_plan",
        ),
        sa.UniqueConstraint(
            "document_version_id",
            "chunking_configuration_id",
            "start",
            "end",
            name="uq_chunk_memberships_range",
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_chunk_memberships_ordinal"),
        sa.CheckConstraint('start >= 0 AND "end" > start', name="ck_chunk_memberships_range"),
    )
    op.create_table(
        "chunk_embeddings",
        sa.Column("embedding_configuration_id", sa.Integer(), nullable=False),
        sa.Column("content_sha256", postgresql.BYTEA(), nullable=False),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("end", sa.Integer(), nullable=False),
        sa.Column("embedding", VECTOR(768), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "embedding_configuration_id",
            "content_sha256",
            "start",
            "end",
            name="pk_chunk_embeddings",
        ),
        sa.ForeignKeyConstraint(
            ["embedding_configuration_id"],
            ["embedding_configurations.id"],
            name="fk_chunk_embeddings_configuration",
        ),
        sa.ForeignKeyConstraint(
            ["content_sha256"],
            ["contents.sha256"],
            name="fk_chunk_embeddings_content",
        ),
        sa.CheckConstraint('start >= 0 AND "end" > start', name="ck_chunk_embeddings_range"),
        sa.CheckConstraint("vector_norm(embedding) > 0", name="ck_chunk_embeddings_nonzero"),
    )


def downgrade() -> None:
    # Retain all authoritative data and the pre-existing vector extension.
    op.drop_table("chunk_embeddings")
    op.drop_table("chunk_memberships")
    op.drop_table("version_chunk_plans")
    op.drop_table("embedding_configurations")
    op.drop_table("chunking_configurations")
