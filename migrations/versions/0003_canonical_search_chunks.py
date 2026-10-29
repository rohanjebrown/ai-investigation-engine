"""Persist exact canonical chunk text and backfill existing memberships."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_canonical_search_chunks"
down_revision = "0002_embedding_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "searchable_chunks",
        sa.Column("content_sha256", postgresql.BYTEA(), nullable=False),
        sa.Column("start", sa.Integer(), nullable=False),
        sa.Column("end", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("content_sha256", "start", "end", name="pk_searchable_chunks"),
        sa.ForeignKeyConstraint(["content_sha256"], ["contents.sha256"], name="fk_searchable_chunks_content"),
        sa.CheckConstraint('start >= 0 AND "end" > start', name="ck_searchable_chunks_range"),
    )
    op.execute("""
        INSERT INTO searchable_chunks (content_sha256, start, "end", text)
        SELECT DISTINCT c.sha256, m.start, m.end,
               pg_catalog.substr(c.text, m.start + 1, m.end - m.start)
        FROM chunk_memberships AS m
        JOIN document_versions AS v ON v.id = m.document_version_id
        JOIN contents AS c ON c.sha256 = v.content_sha256
        ON CONFLICT (content_sha256, start, "end") DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table("searchable_chunks")
