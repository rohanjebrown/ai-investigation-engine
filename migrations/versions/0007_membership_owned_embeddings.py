"""Add membership-owned embeddings alongside the legacy shared-vector table.

Revision ID: 0007_membership_owned_embeddings
Revises: 0006_readiness_guard
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision = "0007_membership_owned_embeddings"
down_revision = "0006_readiness_guard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The legacy chunk_embeddings table remains unchanged while existing
    # application readers and writers continue using shared content/range keys.
    op.create_table(
        "membership_chunk_embeddings",
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunking_configuration_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("embedding_configuration_id", sa.Integer(), nullable=False),
        sa.Column("embedding", VECTOR(768), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_version_id", "chunking_configuration_id", "ordinal"],
            [
                "chunk_memberships.document_version_id",
                "chunk_memberships.chunking_configuration_id",
                "chunk_memberships.ordinal",
            ],
            name="fk_membership_chunk_embeddings_membership",
        ),
        sa.ForeignKeyConstraint(
            ["embedding_configuration_id"],
            ["embedding_configurations.id"],
            name="fk_membership_chunk_embeddings_configuration",
        ),
        sa.PrimaryKeyConstraint(
            "document_version_id",
            "chunking_configuration_id",
            "ordinal",
            "embedding_configuration_id",
            name="pk_membership_chunk_embeddings",
        ),
        sa.CheckConstraint("vector_norm(embedding) > 0", name="ck_membership_chunk_embeddings_nonzero"),
    )
    op.create_index(
        "ix_membership_chunk_embeddings_configuration_membership",
        "membership_chunk_embeddings",
        ["embedding_configuration_id", "document_version_id", "chunking_configuration_id", "ordinal"],
    )


def downgrade() -> None:
    # This only removes the new table. The legacy shared-vector table is never
    # changed by this revision.
    op.drop_index(
        "ix_membership_chunk_embeddings_configuration_membership",
        table_name="membership_chunk_embeddings",
    )
    op.drop_table("membership_chunk_embeddings")
