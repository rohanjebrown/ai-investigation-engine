"""Record verified chunk plan and embedding profile combinations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_chunk_plan_readiness"
down_revision = "0004_searchable_chunks_bm25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chunk_plan_readiness",
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunking_configuration_id", sa.Integer(), nullable=False),
        sa.Column("embedding_configuration_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_version_id", "chunking_configuration_id"],
            ["version_chunk_plans.document_version_id", "version_chunk_plans.chunking_configuration_id"],
            name="fk_chunk_plan_readiness_plan",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["embedding_configuration_id"],
            ["embedding_configurations.id"],
            name="fk_chunk_plan_readiness_embedding_configuration",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "document_version_id",
            "chunking_configuration_id",
            "embedding_configuration_id",
            name="pk_chunk_plan_readiness",
        ),
    )

    # Keep the marker trustworthy when writes bypass the repositories. The
    # triggers invalidate only affected readiness rows and never alter source
    # rows or change the application's deletion policy.
    op.execute("""
        CREATE FUNCTION lock_chunk_plan_before_membership_change() RETURNS trigger
        LANGUAGE plpgsql AS $body$
        BEGIN
          PERFORM 1 FROM version_chunk_plans p
           WHERE (p.document_version_id = OLD.document_version_id
                  AND p.chunking_configuration_id = OLD.chunking_configuration_id)
              OR (TG_OP = 'UPDATE'
                  AND p.document_version_id = NEW.document_version_id
                  AND p.chunking_configuration_id = NEW.chunking_configuration_id)
           ORDER BY p.document_version_id, p.chunking_configuration_id
           FOR UPDATE;
          IF TG_OP = 'DELETE' THEN
            RETURN OLD;
          END IF;
          RETURN NEW;
        END;
        $body$;
        CREATE TRIGGER trg_chunk_memberships_lock_plan
          BEFORE UPDATE OR DELETE ON chunk_memberships
          FOR EACH ROW EXECUTE FUNCTION lock_chunk_plan_before_membership_change();
    """)
    op.execute("""
        CREATE FUNCTION invalidate_chunk_plan_readiness() RETURNS trigger
        LANGUAGE plpgsql AS $body$
        DECLARE
          old_version uuid;
          old_chunking integer;
          old_embedding integer;
          old_sha bytea;
          old_start integer;
          old_end integer;
          old_embedding_profile record;
          old_chunking_profile record;
        BEGIN
          IF TG_TABLE_NAME IN ('version_chunk_plans', 'chunk_memberships') THEN
            IF TG_OP <> 'INSERT' THEN
              old_version := OLD.document_version_id;
              old_chunking := OLD.chunking_configuration_id;
              DELETE FROM chunk_plan_readiness
               WHERE document_version_id = old_version AND chunking_configuration_id = old_chunking;
            END IF;
            IF TG_OP <> 'DELETE' THEN
              DELETE FROM chunk_plan_readiness
               WHERE document_version_id = NEW.document_version_id
                 AND chunking_configuration_id = NEW.chunking_configuration_id;
              IF TG_TABLE_NAME = 'version_chunk_plans' THEN
                SELECT * INTO old_chunking_profile FROM chunking_configurations
                 WHERE id = NEW.chunking_configuration_id;
                DELETE FROM chunk_plan_readiness r
                 USING chunking_configurations c
                 WHERE r.document_version_id = NEW.document_version_id
                   AND c.id = r.chunking_configuration_id
                   AND c.strategy = old_chunking_profile.strategy
                   AND c.algorithm_version = old_chunking_profile.algorithm_version
                   AND c.tokenizer_name = old_chunking_profile.tokenizer_name
                   AND c.tokenizer_revision = old_chunking_profile.tokenizer_revision
                   AND c.tokenizer_artifact_sha256 = old_chunking_profile.tokenizer_artifact_sha256
                   AND c.tokenizer_runtime_version = old_chunking_profile.tokenizer_runtime_version
                   AND c.max_tokens = old_chunking_profile.max_tokens
                   AND c.overlap = old_chunking_profile.overlap;
              END IF;
            END IF;
            RETURN NULL;
          ELSIF TG_TABLE_NAME = 'document_versions' THEN
            DELETE FROM chunk_plan_readiness WHERE document_version_id = OLD.id;
            RETURN NULL;
          ELSIF TG_TABLE_NAME = 'contents' THEN
            DELETE FROM chunk_plan_readiness r USING document_versions v
             WHERE r.document_version_id = v.id AND v.content_sha256 = OLD.sha256;
            RETURN NULL;
          ELSIF TG_TABLE_NAME = 'chunk_embeddings' THEN
            SELECT * INTO old_embedding_profile FROM embedding_configurations
             WHERE id = OLD.embedding_configuration_id;
            DELETE FROM chunk_plan_readiness r
             USING chunk_memberships m, version_chunk_plans p, embedding_configurations ec
             WHERE r.document_version_id = m.document_version_id
               AND r.chunking_configuration_id = m.chunking_configuration_id
               AND p.document_version_id = m.document_version_id
               AND p.chunking_configuration_id = m.chunking_configuration_id
               AND m.start = OLD.start AND m."end" = OLD."end"
               AND ec.id = r.embedding_configuration_id
               AND ec.provider = old_embedding_profile.provider
               AND ec.model_id = old_embedding_profile.model_id
               AND ec.model_revision IS NOT DISTINCT FROM old_embedding_profile.model_revision
               AND ec.dimensions = old_embedding_profile.dimensions
               AND ec.document_format = old_embedding_profile.document_format
               AND ec.normalization = old_embedding_profile.normalization;
            RETURN NULL;
          ELSIF TG_TABLE_NAME = 'embedding_configurations' THEN
            SELECT OLD.provider, OLD.model_id, OLD.model_revision, OLD.dimensions,
                   OLD.document_format, OLD.normalization INTO old_embedding_profile;
            DELETE FROM chunk_plan_readiness r USING embedding_configurations ec
             WHERE (r.embedding_configuration_id = OLD.id OR (
                    ec.id = r.embedding_configuration_id
               AND ec.provider = old_embedding_profile.provider
               AND ec.model_id = old_embedding_profile.model_id
               AND ec.model_revision IS NOT DISTINCT FROM old_embedding_profile.model_revision
               AND ec.dimensions = old_embedding_profile.dimensions
               AND ec.document_format = old_embedding_profile.document_format
               AND ec.normalization = old_embedding_profile.normalization));
            RETURN NULL;
          ELSIF TG_TABLE_NAME = 'chunking_configurations' THEN
            SELECT OLD.strategy, OLD.algorithm_version, OLD.tokenizer_name, OLD.tokenizer_revision,
                   OLD.tokenizer_artifact_sha256, OLD.tokenizer_runtime_version, OLD.max_tokens, OLD.overlap
              INTO old_chunking_profile;
            DELETE FROM chunk_plan_readiness r USING chunking_configurations cc
             WHERE (r.chunking_configuration_id = OLD.id OR (
                    cc.id = r.chunking_configuration_id
               AND cc.strategy = old_chunking_profile.strategy
               AND cc.algorithm_version = old_chunking_profile.algorithm_version
               AND cc.tokenizer_name = old_chunking_profile.tokenizer_name
               AND cc.tokenizer_revision = old_chunking_profile.tokenizer_revision
               AND cc.tokenizer_artifact_sha256 = old_chunking_profile.tokenizer_artifact_sha256
               AND cc.tokenizer_runtime_version = old_chunking_profile.tokenizer_runtime_version
               AND cc.max_tokens = old_chunking_profile.max_tokens
               AND cc.overlap = old_chunking_profile.overlap));
            RETURN NULL;
          END IF;
          RETURN NULL;
        END;
        $body$;
    """)
    for table in (
        "version_chunk_plans",
        "chunk_memberships",
        "document_versions",
        "contents",
        "chunk_embeddings",
        "embedding_configurations",
        "chunking_configurations",
    ):
        events = (
            "INSERT OR UPDATE OR DELETE"
            if table in ("version_chunk_plans", "chunk_memberships")
            else "UPDATE OR DELETE"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_invalidate_readiness AFTER {events} ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION invalidate_chunk_plan_readiness()"
        )


def downgrade() -> None:
    for table in (
        "version_chunk_plans",
        "chunk_memberships",
        "document_versions",
        "contents",
        "chunk_embeddings",
        "embedding_configurations",
        "chunking_configurations",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_invalidate_readiness ON {table}")
    op.execute("DROP TRIGGER IF EXISTS trg_chunk_memberships_lock_plan ON chunk_memberships")
    op.execute("DROP FUNCTION IF EXISTS lock_chunk_plan_before_membership_change()")
    op.execute("DROP FUNCTION IF EXISTS invalidate_chunk_plan_readiness()")
    op.drop_table("chunk_plan_readiness")
