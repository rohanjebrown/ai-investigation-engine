"""Add the opt-in cosine HNSW index for shared chunk embeddings.

Revision ID: 0008_chunk_embeddings_hnsw
Revises: 0007_membership_owned_embeddings
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_chunk_embeddings_hnsw"
down_revision = "0007_membership_owned_embeddings"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_chunk_embeddings_embedding_hnsw"
MIGRATION_MARKER = "created by alembic revision 0008_chunk_embeddings_hnsw"


def _matching_cosine_hnsw_index(bind):
    return bind.execute(
        sa.text("""
        SELECT x.relname
        FROM pg_catalog.pg_index i
        JOIN pg_catalog.pg_class t ON t.oid = i.indrelid
        JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_catalog.pg_class x ON x.oid = i.indexrelid
        JOIN pg_catalog.pg_am a ON a.oid = x.relam
        WHERE n.nspname = 'public' AND t.relname = 'chunk_embeddings'
          AND a.amname = 'hnsw' AND i.indisvalid AND i.indisready AND i.indislive
          AND i.indpred IS NULL AND i.indexprs IS NULL AND i.indnatts = 1
          AND i.indkey[0] = (SELECT attnum FROM pg_catalog.pg_attribute
              WHERE attrelid=t.oid AND attname='embedding' AND NOT attisdropped)
          AND pg_catalog.pg_get_indexdef(i.indexrelid)
              ILIKE '%USING hnsw (embedding vector_cosine_ops)%'
          AND x.reloptions @> ARRAY['m=16', 'ef_construction=64']
        ORDER BY x.relname
        LIMIT 1
    """)
    ).scalar_one_or_none()


def upgrade() -> None:
    # CREATE INDEX CONCURRENTLY cannot run inside the normal Alembic transaction.
    # A valid equivalent index is reused; a failed prior concurrent build is
    # removed before retrying. The migration lock in env.py spans this block.
    context = op.get_context()
    with context.autocommit_block():
        bind = op.get_bind()
        if _matching_cosine_hnsw_index(bind):
            return
        state = bind.execute(
            sa.text("""
            SELECT i.indisvalid, i.indisready, obj_description(x.oid, 'pg_class') AS marker
            FROM pg_catalog.pg_index i
            JOIN pg_catalog.pg_class x ON x.oid = i.indexrelid
            JOIN pg_catalog.pg_class t ON t.oid = i.indrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = 'public' AND t.relname = 'chunk_embeddings' AND x.relname = :name
        """),
            {"name": INDEX_NAME},
        ).one_or_none()
        if state is not None:
            if state.indisvalid and state.indisready:
                raise RuntimeError(f"{INDEX_NAME} exists but has an incompatible definition")
            if state.marker != MIGRATION_MARKER:
                raise RuntimeError("Unowned invalid index requires operator review; refusing to delete it")
            op.drop_index(INDEX_NAME, table_name="chunk_embeddings", schema="public", postgresql_concurrently=True)
        op.create_index(
            INDEX_NAME,
            "chunk_embeddings",
            ["embedding"],
            schema="public",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_concurrently=True,
        )
        bind.execute(sa.text(f"COMMENT ON INDEX public.\"{INDEX_NAME}\" IS '{MIGRATION_MARKER}'"))


def downgrade() -> None:
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        created_here = bind.execute(
            sa.text("""
            SELECT pg_catalog.obj_description(pg_catalog.to_regclass(:name), 'pg_class') = :marker
        """),
            {"name": f"public.{INDEX_NAME}", "marker": MIGRATION_MARKER},
        ).scalar_one()
        if created_here:
            op.drop_index(
                INDEX_NAME,
                table_name="chunk_embeddings",
                schema="public",
                postgresql_concurrently=True,
                if_exists=True,
            )
