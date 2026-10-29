"""Create the approved document registration schema.

Revision ID: 0001_document_registration
Revises: None
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_document_registration"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contents",
        sa.Column("sha256", postgresql.BYTEA(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.CheckConstraint("octet_length(sha256) = 32", name="ck_contents_sha256_length"),
        sa.CheckConstraint(
            "sha256 = pg_catalog.sha256(pg_catalog.convert_to(text, 'UTF8'))",
            name="ck_contents_sha256_matches_text",
        ),
        sa.PrimaryKeyConstraint("sha256", name="pk_contents"),
    )
    # The circular active-version FK is added after both tables exist.
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", sa.Text(collation="C"), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
    )
    op.create_table(
        "document_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content_sha256", postgresql.BYTEA(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.Text(collation="C"), nullable=False),
        sa.Column("source_id", sa.Text(collation="C"), nullable=False),
        sa.Column("revision", sa.Text(collation="C"), nullable=True),
        sa.Column(
            "record_locator",
            postgresql.JSONB(none_as_null=True),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(none_as_null=True),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "structure",
            postgresql.JSONB(none_as_null=True),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "source_spans",
            postgresql.JSONB(none_as_null=True),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.CheckConstraint("version_number > 0", name="ck_document_versions_positive_number"),
        sa.CheckConstraint("kind IN ('upload', 'dataset')", name="ck_document_versions_kind"),
        sa.CheckConstraint(
            "jsonb_typeof(record_locator) = 'object'",
            name="ck_document_versions_record_locator_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata) = 'object'",
            name="ck_document_versions_metadata_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(structure) = 'array'",
            name="ck_document_versions_structure_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_spans) = 'array'",
            name="ck_document_versions_source_spans_array",
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], name="fk_document_versions_document"),
        sa.ForeignKeyConstraint(["content_sha256"], ["contents.sha256"], name="fk_document_versions_content"),
        sa.PrimaryKeyConstraint("id", name="pk_document_versions"),
        sa.UniqueConstraint("document_id", "version_number", name="uq_document_versions_number"),
        sa.UniqueConstraint("document_id", "id", name="uq_document_versions_owned_id"),
        sa.UniqueConstraint(
            "kind",
            "source_id",
            "revision",
            "record_locator",
            name="uq_document_versions_source_occurrence",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index("ix_document_versions_content_sha256", "document_versions", ["content_sha256"])
    op.create_foreign_key(
        "fk_documents_current_owned_version",
        "documents",
        "document_versions",
        ["id", "current_version_id"],
        ["document_id", "id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_documents_current_owned_version", "documents", type_="foreignkey")
    op.drop_index("ix_document_versions_content_sha256", table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_table("documents")
    op.drop_table("contents")
