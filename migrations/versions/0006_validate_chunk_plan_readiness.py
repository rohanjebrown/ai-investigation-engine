"""Validate readiness writes and tighten invalidation to affected data only."""

from alembic import op

revision = "0006_readiness_guard"
down_revision = "0005_chunk_plan_readiness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION validate_chunk_plan_readiness() RETURNS trigger
        LANGUAGE plpgsql AS $body$
        DECLARE
          plan_row record;
          embedding_profile record;
          content_text text;
          whitespace_chars text;
          eligible_count integer;
          missing_embeddings integer;
          plan_matches integer;
          membership_count integer;
          first_ordinal integer;
          last_ordinal integer;
          maximum_end integer;
          valid_geometry boolean;
        BEGIN
          -- Match activation's lock order: version, plan/configuration, content,
          -- embedding configurations, then embedding rows. This also makes a
          -- direct INSERT safe against concurrent source mutations.
          PERFORM 1 FROM document_versions
           WHERE id = NEW.document_version_id
           FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'readiness requires an existing document version' USING ERRCODE = '23514';
          END IF;

          SELECT p.expected_chunk_count, c.strategy, c.algorithm_version, c.overlap,
                 c.tokenizer_name, c.tokenizer_revision, c.tokenizer_artifact_sha256,
                 c.tokenizer_runtime_version, c.max_tokens, v.content_sha256
            INTO plan_row
            FROM version_chunk_plans p
            JOIN chunking_configurations c ON c.id = p.chunking_configuration_id
            JOIN document_versions v ON v.id = p.document_version_id
           WHERE p.document_version_id = NEW.document_version_id
             AND p.chunking_configuration_id = NEW.chunking_configuration_id
           FOR UPDATE OF p, c;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'readiness requires a supported persisted chunk plan' USING ERRCODE = '23514';
          END IF;
          IF plan_row.algorithm_version <> '1' THEN
            RAISE EXCEPTION 'readiness requires a supported persisted chunk plan' USING ERRCODE = '23514';
          END IF;

          SELECT text INTO content_text FROM contents
           WHERE sha256 = plan_row.content_sha256
           FOR SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'readiness requires the version Content' USING ERRCODE = '23514';
          END IF;

          LOCK TABLE embedding_configurations IN SHARE MODE;
          SELECT * INTO embedding_profile FROM embedding_configurations
           WHERE id = NEW.embedding_configuration_id
           FOR SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'readiness requires an existing embedding configuration' USING ERRCODE = '23514';
          END IF;
          PERFORM id FROM embedding_configurations e
           WHERE e.provider = embedding_profile.provider
             AND e.model_id = embedding_profile.model_id
             AND e.model_revision IS NOT DISTINCT FROM embedding_profile.model_revision
             AND e.dimensions = embedding_profile.dimensions
             AND e.document_format = embedding_profile.document_format
             AND e.normalization = embedding_profile.normalization
           ORDER BY e.id
           FOR SHARE;
          IF NEW.embedding_configuration_id <> (
            SELECT min(e.id) FROM embedding_configurations e
             WHERE e.provider = embedding_profile.provider
               AND e.model_id = embedding_profile.model_id
               AND e.model_revision IS NOT DISTINCT FROM embedding_profile.model_revision
               AND e.dimensions = embedding_profile.dimensions
               AND e.document_format = embedding_profile.document_format
               AND e.normalization = embedding_profile.normalization
          ) THEN
            RAISE EXCEPTION 'readiness requires the canonical equivalent embedding configuration'
              USING ERRCODE = '23514';
          END IF;

          SELECT count(*) INTO plan_matches
            FROM version_chunk_plans p
            JOIN chunking_configurations c ON c.id = p.chunking_configuration_id
           WHERE p.document_version_id = NEW.document_version_id
             AND c.strategy = plan_row.strategy
             AND c.algorithm_version = plan_row.algorithm_version
             AND c.tokenizer_name = plan_row.tokenizer_name
             AND c.tokenizer_revision = plan_row.tokenizer_revision
             AND c.tokenizer_artifact_sha256 = plan_row.tokenizer_artifact_sha256
             AND c.tokenizer_runtime_version = plan_row.tokenizer_runtime_version
             AND c.max_tokens = plan_row.max_tokens
             AND c.overlap = plan_row.overlap;
          IF plan_matches <> 1 THEN
            RAISE EXCEPTION 'readiness requires one unambiguous matching chunk plan' USING ERRCODE = '23514';
          END IF;

          whitespace_chars := convert_from(
            decode(
              '090a0b0c0d1c1d1e1f20c285c2a0e19a80e28080e28081e28082e28083e28084e28085e28086e28087e28088e28089e2808ae280a8e280a9e280afe2819fe38080',
              'hex'
            ),
            'UTF8'
          );
          WITH ordered AS (
            SELECT ordinal, start, "end",
                   lag(start) OVER (ORDER BY ordinal) AS previous_start,
                   max("end") OVER (
                     ORDER BY ordinal ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                   ) AS covered_before
              FROM chunk_memberships
             WHERE document_version_id = NEW.document_version_id
               AND chunking_configuration_id = NEW.chunking_configuration_id
          ), summary AS (
            SELECT count(*)::integer AS row_count,
                   min(ordinal) AS first_ord,
                   max(ordinal) AS last_ord,
                   max("end") AS final_end,
                   bool_and(
                     start >= 0 AND start < "end" AND "end" <= char_length(content_text)
                     AND CASE WHEN ordinal = 0
                       THEN start = 0 AND previous_start IS NULL
                       ELSE previous_start < start
                         AND start <= covered_before
                         AND (plan_row.strategy = 'fixed_tokens' AND plan_row.overlap > 0
                              OR start >= covered_before)
                     END
                   ) AS geometry_ok
              FROM ordered
          )
          SELECT row_count, first_ord, last_ord, final_end, geometry_ok
            INTO membership_count, first_ordinal, last_ordinal, maximum_end, valid_geometry
            FROM summary;
          IF plan_row.expected_chunk_count < 1
             OR membership_count <> plan_row.expected_chunk_count
             OR first_ordinal <> 0
             OR last_ordinal <> plan_row.expected_chunk_count - 1
             OR maximum_end <> char_length(content_text)
             OR valid_geometry IS DISTINCT FROM TRUE THEN
            RAISE EXCEPTION 'readiness requires complete, ordered chunk coverage' USING ERRCODE = '23514';
          END IF;

          -- Keep the Unicode whitespace set identical to Python strip().
          -- Membership ranges and vectors are locked before this completeness check.
          PERFORM ce.embedding_configuration_id
            FROM chunk_embeddings ce
            JOIN embedding_configurations e ON e.id = ce.embedding_configuration_id
            JOIN chunk_memberships m
              ON m.document_version_id = NEW.document_version_id
             AND m.chunking_configuration_id = NEW.chunking_configuration_id
             AND m.start = ce.start AND m."end" = ce."end"
           WHERE ce.content_sha256 = plan_row.content_sha256
             AND e.provider = embedding_profile.provider
             AND e.model_id = embedding_profile.model_id
             AND e.model_revision IS NOT DISTINCT FROM embedding_profile.model_revision
             AND e.dimensions = embedding_profile.dimensions
             AND e.document_format = embedding_profile.document_format
             AND e.normalization = embedding_profile.normalization
             AND btrim(substring(content_text FROM m.start + 1 FOR m."end" - m.start), whitespace_chars) <> ''
           ORDER BY ce.embedding_configuration_id, ce.content_sha256, ce.start, ce."end"
           FOR UPDATE OF ce;

          SELECT count(*)::integer,
                 count(*) FILTER (
                   WHERE NOT EXISTS (
                     SELECT 1
                       FROM chunk_embeddings ce
                       JOIN embedding_configurations e ON e.id = ce.embedding_configuration_id
                      WHERE ce.content_sha256 = plan_row.content_sha256
                        AND ce.start = m.start AND ce."end" = m."end"
                        AND e.provider = embedding_profile.provider
                        AND e.model_id = embedding_profile.model_id
                        AND e.model_revision IS NOT DISTINCT FROM embedding_profile.model_revision
                        AND e.dimensions = embedding_profile.dimensions
                        AND e.document_format = embedding_profile.document_format
                        AND e.normalization = embedding_profile.normalization
                   )
                 )
            INTO eligible_count, missing_embeddings
            FROM chunk_memberships m
           WHERE m.document_version_id = NEW.document_version_id
             AND m.chunking_configuration_id = NEW.chunking_configuration_id
             AND btrim(substring(content_text FROM m.start + 1 FOR m."end" - m.start), whitespace_chars) <> '';
          IF eligible_count = 0 OR missing_embeddings <> 0 THEN
            RAISE EXCEPTION 'readiness requires complete eligible embeddings' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $body$;
        CREATE TRIGGER trg_chunk_plan_readiness_validate
          BEFORE INSERT OR UPDATE ON chunk_plan_readiness
          FOR EACH ROW EXECUTE FUNCTION validate_chunk_plan_readiness();
    """)

    # Membership changes are common during ingestion. Keep their invalidation
    # set-based so a multi-row insert probes readiness once per affected plan.
    op.execute("DROP TRIGGER IF EXISTS trg_chunk_memberships_invalidate_readiness ON chunk_memberships")
    op.execute("DROP TRIGGER IF EXISTS trg_chunk_memberships_lock_plan ON chunk_memberships")
    op.execute("DROP FUNCTION IF EXISTS lock_chunk_plan_before_membership_change()")
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
          IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END;
        $body$;
        CREATE TRIGGER trg_chunk_memberships_lock_plan_update
          BEFORE UPDATE ON chunk_memberships
          FOR EACH ROW WHEN (
            OLD.document_version_id IS DISTINCT FROM NEW.document_version_id OR
            OLD.chunking_configuration_id IS DISTINCT FROM NEW.chunking_configuration_id OR
            OLD.ordinal IS DISTINCT FROM NEW.ordinal OR OLD.start IS DISTINCT FROM NEW.start OR
            OLD."end" IS DISTINCT FROM NEW."end"
          ) EXECUTE FUNCTION lock_chunk_plan_before_membership_change();
        CREATE TRIGGER trg_chunk_memberships_lock_plan_delete
          BEFORE DELETE ON chunk_memberships
          FOR EACH ROW EXECUTE FUNCTION lock_chunk_plan_before_membership_change();
    """)
    op.execute("""
        CREATE FUNCTION invalidate_readiness_for_memberships() RETURNS trigger
        LANGUAGE plpgsql AS $body$
        BEGIN
          IF TG_OP = 'INSERT' THEN
            DELETE FROM chunk_plan_readiness r
             USING (SELECT DISTINCT document_version_id, chunking_configuration_id FROM new_memberships) n
             WHERE r.document_version_id = n.document_version_id
               AND r.chunking_configuration_id = n.chunking_configuration_id;
          ELSIF TG_OP = 'DELETE' THEN
            DELETE FROM chunk_plan_readiness r
             USING (SELECT DISTINCT document_version_id, chunking_configuration_id FROM old_memberships) o
             WHERE r.document_version_id = o.document_version_id
               AND r.chunking_configuration_id = o.chunking_configuration_id;
          ELSE
            DELETE FROM chunk_plan_readiness r
             USING (
               SELECT o.document_version_id, o.chunking_configuration_id FROM old_memberships o
                WHERE NOT EXISTS (
                  SELECT 1 FROM new_memberships n
                   WHERE ROW(n.document_version_id, n.chunking_configuration_id, n.ordinal, n.start, n."end")
                         IS NOT DISTINCT FROM
                         ROW(o.document_version_id, o.chunking_configuration_id, o.ordinal, o.start, o."end")
                )
               UNION
               SELECT n.document_version_id, n.chunking_configuration_id FROM new_memberships n
                WHERE NOT EXISTS (
                  SELECT 1 FROM old_memberships o
                   WHERE ROW(o.document_version_id, o.chunking_configuration_id, o.ordinal, o.start, o."end")
                         IS NOT DISTINCT FROM
                         ROW(n.document_version_id, n.chunking_configuration_id, n.ordinal, n.start, n."end")
                )
             ) changed
             WHERE r.document_version_id = changed.document_version_id
               AND r.chunking_configuration_id = changed.chunking_configuration_id;
          END IF;
          RETURN NULL;
        END;
        $body$;
        CREATE TRIGGER trg_chunk_memberships_invalidate_insert
          AFTER INSERT ON chunk_memberships
          REFERENCING NEW TABLE AS new_memberships
          FOR EACH STATEMENT EXECUTE FUNCTION invalidate_readiness_for_memberships();
        CREATE TRIGGER trg_chunk_memberships_invalidate_update
          AFTER UPDATE ON chunk_memberships
          REFERENCING OLD TABLE AS old_memberships NEW TABLE AS new_memberships
          FOR EACH STATEMENT EXECUTE FUNCTION invalidate_readiness_for_memberships();
        CREATE TRIGGER trg_chunk_memberships_invalidate_delete
          AFTER DELETE ON chunk_memberships
          REFERENCING OLD TABLE AS old_memberships
          FOR EACH STATEMENT EXECUTE FUNCTION invalidate_readiness_for_memberships();
    """)

    # Correct shared-vector invalidation to match Content as well as range.
    op.execute("""
        CREATE OR REPLACE FUNCTION invalidate_chunk_plan_readiness() RETURNS trigger
        LANGUAGE plpgsql AS $body$
        DECLARE
          profile record;
        BEGIN
          IF TG_TABLE_NAME = 'chunk_memberships' THEN
            IF TG_OP <> 'INSERT' THEN
              DELETE FROM chunk_plan_readiness
               WHERE document_version_id = OLD.document_version_id
                 AND chunking_configuration_id = OLD.chunking_configuration_id;
            END IF;
            IF TG_OP <> 'DELETE' THEN
              DELETE FROM chunk_plan_readiness
               WHERE document_version_id = NEW.document_version_id
                 AND chunking_configuration_id = NEW.chunking_configuration_id;
            END IF;
          ELSIF TG_TABLE_NAME = 'version_chunk_plans' THEN
            IF TG_OP = 'UPDATE'
               AND OLD.expected_chunk_count IS NOT DISTINCT FROM NEW.expected_chunk_count THEN
              RETURN NULL;
            END IF;
            IF TG_OP <> 'INSERT' THEN
              DELETE FROM chunk_plan_readiness
               WHERE document_version_id = OLD.document_version_id
                 AND chunking_configuration_id = OLD.chunking_configuration_id;
            END IF;
            IF TG_OP <> 'DELETE' THEN
              DELETE FROM chunk_plan_readiness
               WHERE document_version_id = NEW.document_version_id
                 AND chunking_configuration_id = NEW.chunking_configuration_id;
              SELECT * INTO profile FROM chunking_configurations WHERE id = NEW.chunking_configuration_id;
              DELETE FROM chunk_plan_readiness r USING chunking_configurations c
               WHERE r.document_version_id = NEW.document_version_id
                 AND c.id = r.chunking_configuration_id
                 AND c.strategy = profile.strategy
                 AND c.algorithm_version = profile.algorithm_version
                 AND c.tokenizer_name = profile.tokenizer_name
                 AND c.tokenizer_revision = profile.tokenizer_revision
                 AND c.tokenizer_artifact_sha256 = profile.tokenizer_artifact_sha256
                 AND c.tokenizer_runtime_version = profile.tokenizer_runtime_version
                 AND c.max_tokens = profile.max_tokens AND c.overlap = profile.overlap;
            END IF;
          ELSIF TG_TABLE_NAME = 'document_versions' THEN
            IF TG_OP = 'UPDATE' AND OLD.content_sha256 IS NOT DISTINCT FROM NEW.content_sha256 THEN
              RETURN NULL;
            END IF;
            DELETE FROM chunk_plan_readiness WHERE document_version_id = OLD.id;
          ELSIF TG_TABLE_NAME = 'contents' THEN
            IF TG_OP = 'UPDATE' AND OLD.text IS NOT DISTINCT FROM NEW.text THEN
              RETURN NULL;
            END IF;
            DELETE FROM chunk_plan_readiness r USING document_versions v
             WHERE r.document_version_id = v.id AND v.content_sha256 = OLD.sha256;
          ELSIF TG_TABLE_NAME = 'chunk_embeddings' THEN
            IF TG_OP = 'UPDATE' AND ROW(OLD.embedding_configuration_id, OLD.content_sha256, OLD.start, OLD."end")
               IS NOT DISTINCT FROM ROW(NEW.embedding_configuration_id, NEW.content_sha256, NEW.start, NEW."end") THEN
              RETURN NULL;
            END IF;
            SELECT * INTO profile FROM embedding_configurations WHERE id = OLD.embedding_configuration_id;
            DELETE FROM chunk_plan_readiness r
             USING chunk_memberships m, document_versions v, embedding_configurations ec
             WHERE r.document_version_id = m.document_version_id
               AND r.chunking_configuration_id = m.chunking_configuration_id
               AND v.id = m.document_version_id
               AND v.content_sha256 = OLD.content_sha256
               AND m.start = OLD.start AND m."end" = OLD."end"
               AND ec.id = r.embedding_configuration_id
               AND ec.provider = profile.provider AND ec.model_id = profile.model_id
               AND ec.model_revision IS NOT DISTINCT FROM profile.model_revision
               AND ec.dimensions = profile.dimensions
               AND ec.document_format = profile.document_format
               AND ec.normalization = profile.normalization;
          ELSIF TG_TABLE_NAME = 'embedding_configurations' THEN
            IF TG_OP = 'INSERT' THEN
              DELETE FROM chunk_plan_readiness r USING embedding_configurations ec
               WHERE ec.id = r.embedding_configuration_id
                 AND r.embedding_configuration_id > NEW.id
                 AND ec.provider = NEW.provider AND ec.model_id = NEW.model_id
                 AND ec.model_revision IS NOT DISTINCT FROM NEW.model_revision
                 AND ec.dimensions = NEW.dimensions
                 AND ec.document_format = NEW.document_format
                 AND ec.normalization = NEW.normalization;
              RETURN NULL;
            ELSIF TG_OP = 'DELETE' THEN
              SELECT OLD.provider, OLD.model_id, OLD.model_revision, OLD.dimensions,
                     OLD.document_format, OLD.normalization INTO profile;
              DELETE FROM chunk_plan_readiness WHERE embedding_configuration_id = OLD.id;
            ELSE
              IF ROW(OLD.provider, OLD.model_id, OLD.model_revision, OLD.dimensions, OLD.document_format, OLD.normalization)
                 IS NOT DISTINCT FROM ROW(NEW.provider, NEW.model_id, NEW.model_revision, NEW.dimensions, NEW.document_format, NEW.normalization) THEN
                RETURN NULL;
              END IF;
              SELECT OLD.provider, OLD.model_id, OLD.model_revision, OLD.dimensions,
                     OLD.document_format, OLD.normalization INTO profile;
            END IF;
            DELETE FROM chunk_plan_readiness r USING embedding_configurations ec
             WHERE ec.id = r.embedding_configuration_id
               AND ec.provider = profile.provider AND ec.model_id = profile.model_id
               AND ec.model_revision IS NOT DISTINCT FROM profile.model_revision
               AND ec.dimensions = profile.dimensions
               AND ec.document_format = profile.document_format
               AND ec.normalization = profile.normalization;
          ELSIF TG_TABLE_NAME = 'chunking_configurations' THEN
            IF TG_OP = 'UPDATE'
               AND ROW(OLD.strategy, OLD.algorithm_version, OLD.tokenizer_name, OLD.tokenizer_revision,
                       OLD.tokenizer_artifact_sha256, OLD.tokenizer_runtime_version, OLD.max_tokens, OLD.overlap)
                   IS NOT DISTINCT FROM
                   ROW(NEW.strategy, NEW.algorithm_version, NEW.tokenizer_name, NEW.tokenizer_revision,
                       NEW.tokenizer_artifact_sha256, NEW.tokenizer_runtime_version, NEW.max_tokens, NEW.overlap) THEN
              RETURN NULL;
            END IF;
            SELECT OLD.strategy, OLD.algorithm_version, OLD.tokenizer_name, OLD.tokenizer_revision,
                   OLD.tokenizer_artifact_sha256, OLD.tokenizer_runtime_version, OLD.max_tokens, OLD.overlap
              INTO profile;
            DELETE FROM chunk_plan_readiness WHERE chunking_configuration_id = OLD.id;
            DELETE FROM chunk_plan_readiness r USING chunking_configurations c
             WHERE c.id = r.chunking_configuration_id
               AND c.strategy = profile.strategy
               AND c.algorithm_version = profile.algorithm_version
               AND c.tokenizer_name = profile.tokenizer_name
               AND c.tokenizer_revision = profile.tokenizer_revision
               AND c.tokenizer_artifact_sha256 = profile.tokenizer_artifact_sha256
               AND c.tokenizer_runtime_version = profile.tokenizer_runtime_version
               AND c.max_tokens = profile.max_tokens AND c.overlap = profile.overlap;
          END IF;
          RETURN NULL;
        END;
        $body$;
    """)

    # Avoid clearing readiness for metadata-only updates. Only validation inputs
    # and membership/vector identity keys affect the marker.
    for table in (
        "version_chunk_plans",
        "document_versions",
        "contents",
        "chunk_embeddings",
        "embedding_configurations",
        "chunking_configurations",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_invalidate_readiness ON {table}")
    op.execute("""
        CREATE TRIGGER trg_version_chunk_plans_invalidate_readiness
          AFTER INSERT OR DELETE OR UPDATE OF expected_chunk_count ON version_chunk_plans
          FOR EACH ROW EXECUTE FUNCTION invalidate_chunk_plan_readiness();
        CREATE TRIGGER trg_document_versions_invalidate_readiness
          AFTER DELETE OR UPDATE OF content_sha256 ON document_versions
          FOR EACH ROW EXECUTE FUNCTION invalidate_chunk_plan_readiness();
        CREATE TRIGGER trg_contents_invalidate_readiness
          AFTER DELETE OR UPDATE OF text, sha256 ON contents
          FOR EACH ROW EXECUTE FUNCTION invalidate_chunk_plan_readiness();
        CREATE TRIGGER trg_chunk_embeddings_invalidate_readiness
          AFTER DELETE OR UPDATE OF embedding_configuration_id, content_sha256, start, "end" ON chunk_embeddings
          FOR EACH ROW EXECUTE FUNCTION invalidate_chunk_plan_readiness();
        CREATE TRIGGER trg_embedding_configurations_invalidate_readiness
          AFTER INSERT OR DELETE OR UPDATE OF provider, model_id, model_revision, dimensions, document_format, normalization
          ON embedding_configurations
          FOR EACH ROW EXECUTE FUNCTION invalidate_chunk_plan_readiness();
        CREATE TRIGGER trg_chunking_configurations_invalidate_readiness
          AFTER DELETE OR UPDATE OF strategy, algorithm_version, tokenizer_name, tokenizer_revision,
            tokenizer_artifact_sha256, tokenizer_runtime_version, max_tokens, overlap
          ON chunking_configurations
          FOR EACH ROW EXECUTE FUNCTION invalidate_chunk_plan_readiness();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_chunk_plan_readiness_validate ON chunk_plan_readiness")
    op.execute("DROP FUNCTION IF EXISTS validate_chunk_plan_readiness()")
    op.execute("DROP TRIGGER IF EXISTS trg_chunk_memberships_invalidate_insert ON chunk_memberships")
    op.execute("DROP TRIGGER IF EXISTS trg_chunk_memberships_invalidate_update ON chunk_memberships")
    op.execute("DROP TRIGGER IF EXISTS trg_chunk_memberships_invalidate_delete ON chunk_memberships")
    op.execute("DROP FUNCTION IF EXISTS invalidate_readiness_for_memberships()")
    op.execute("DROP TRIGGER IF EXISTS trg_chunk_memberships_lock_plan_update ON chunk_memberships")
    op.execute("DROP TRIGGER IF EXISTS trg_chunk_memberships_lock_plan_delete ON chunk_memberships")
    op.execute("""
        CREATE TRIGGER trg_chunk_memberships_lock_plan
          BEFORE UPDATE OR DELETE ON chunk_memberships
          FOR EACH ROW EXECUTE FUNCTION lock_chunk_plan_before_membership_change();
        CREATE TRIGGER trg_chunk_memberships_invalidate_readiness
          AFTER INSERT OR UPDATE OR DELETE ON chunk_memberships
          FOR EACH ROW EXECUTE FUNCTION invalidate_chunk_plan_readiness();
    """)
    for table in (
        "version_chunk_plans",
        "document_versions",
        "contents",
        "chunk_embeddings",
        "embedding_configurations",
        "chunking_configurations",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_invalidate_readiness ON {table}")
    op.execute("""
        CREATE TRIGGER trg_version_chunk_plans_invalidate_readiness
          AFTER INSERT OR UPDATE OR DELETE ON version_chunk_plans
          FOR EACH ROW EXECUTE FUNCTION invalidate_chunk_plan_readiness();
        CREATE TRIGGER trg_document_versions_invalidate_readiness
          AFTER UPDATE OR DELETE ON document_versions
          FOR EACH ROW EXECUTE FUNCTION invalidate_chunk_plan_readiness();
        CREATE TRIGGER trg_contents_invalidate_readiness
          AFTER UPDATE OR DELETE ON contents
          FOR EACH ROW EXECUTE FUNCTION invalidate_chunk_plan_readiness();
        CREATE TRIGGER trg_chunk_embeddings_invalidate_readiness
          AFTER UPDATE OR DELETE ON chunk_embeddings
          FOR EACH ROW EXECUTE FUNCTION invalidate_chunk_plan_readiness();
        CREATE TRIGGER trg_embedding_configurations_invalidate_readiness
          AFTER UPDATE OR DELETE ON embedding_configurations
          FOR EACH ROW EXECUTE FUNCTION invalidate_chunk_plan_readiness();
        CREATE TRIGGER trg_chunking_configurations_invalidate_readiness
          AFTER UPDATE OR DELETE ON chunking_configurations
          FOR EACH ROW EXECUTE FUNCTION invalidate_chunk_plan_readiness();
    """)
