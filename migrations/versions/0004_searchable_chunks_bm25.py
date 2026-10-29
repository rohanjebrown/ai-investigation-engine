"""Create BM25 index when pg_textsearch is installed; otherwise leave fallback usable."""

from alembic import op

revision = "0004_searchable_chunks_bm25"
down_revision = "0003_canonical_search_chunks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extname = 'pg_textsearch') THEN
            EXECUTE 'CREATE INDEX searchable_chunks_bm25_idx ON searchable_chunks USING bm25 (text) WITH (text_config = ''english'')';
          END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS searchable_chunks_bm25_idx")
