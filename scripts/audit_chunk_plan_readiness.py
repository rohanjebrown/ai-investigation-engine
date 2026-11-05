"""Read-only, keyset-paged audit of readiness rows against migration 0006 invariants."""

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from investigation_engine.infrastructure.config import settings  # noqa: E402
from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import (  # noqa: E402
    PYTHON_WHITESPACE,
)

AUDIT_BATCH_SQL = text(
    """
    WITH batch AS MATERIALIZED (
        SELECT r.document_version_id, r.chunking_configuration_id, r.embedding_configuration_id
          FROM chunk_plan_readiness r
         WHERE CAST(:after_version_id AS uuid) IS NULL
            OR ROW(r.document_version_id, r.chunking_configuration_id, r.embedding_configuration_id)
               > ROW(CAST(:after_version_id AS uuid), :after_chunking_id, :after_embedding_id)
         ORDER BY r.document_version_id, r.chunking_configuration_id, r.embedding_configuration_id
         LIMIT :batch_size
    ), base AS MATERIALIZED (
        SELECT b.document_version_id,
               b.chunking_configuration_id,
               b.embedding_configuration_id,
               p.expected_chunk_count,
               c.id IS NOT NULL AS has_chunking_configuration,
               c.strategy,
               c.algorithm_version,
               c.overlap,
               v.id IS NOT NULL AS has_document_version,
               v.content_sha256,
               ct.sha256 IS NOT NULL AS has_content,
               ct.text AS content_text,
               ec.id IS NOT NULL AS has_embedding_configuration,
               ec.provider AS embedding_provider,
               ec.model_id AS embedding_model_id,
               ec.model_revision AS embedding_model_revision,
               ec.dimensions AS embedding_dimensions,
               ec.document_format AS embedding_document_format,
               ec.normalization AS embedding_normalization,
               (SELECT min(ec2.id)
                  FROM embedding_configurations ec2
                 WHERE ec2.provider = ec.provider
                   AND ec2.model_id = ec.model_id
                   AND ec2.model_revision IS NOT DISTINCT FROM ec.model_revision
                   AND ec2.dimensions = ec.dimensions
                   AND ec2.document_format = ec.document_format
                   AND ec2.normalization = ec.normalization) AS canonical_embedding_configuration_id,
               (SELECT count(*)
                  FROM version_chunk_plans p2
                  JOIN chunking_configurations c2 ON c2.id = p2.chunking_configuration_id
                 WHERE p2.document_version_id = b.document_version_id
                   AND c2.strategy = c.strategy
                   AND c2.algorithm_version = c.algorithm_version
                   AND c2.tokenizer_name = c.tokenizer_name
                   AND c2.tokenizer_revision = c.tokenizer_revision
                   AND c2.tokenizer_artifact_sha256 = c.tokenizer_artifact_sha256
                   AND c2.tokenizer_runtime_version = c.tokenizer_runtime_version
                   AND c2.max_tokens = c.max_tokens
                   AND c2.overlap = c.overlap) AS matching_plan_count,
               p.document_version_id IS NOT NULL AS has_plan
          FROM batch b
          LEFT JOIN version_chunk_plans p
            ON p.document_version_id = b.document_version_id
           AND p.chunking_configuration_id = b.chunking_configuration_id
          LEFT JOIN chunking_configurations c ON c.id = p.chunking_configuration_id
          LEFT JOIN document_versions v ON v.id = b.document_version_id
          LEFT JOIN contents ct ON ct.sha256 = v.content_sha256
          LEFT JOIN embedding_configurations ec ON ec.id = b.embedding_configuration_id
    ), ordered_memberships AS (
        SELECT base.*,
               m.ordinal,
               m.start,
               m."end",
               lag(m.start) OVER (
                   PARTITION BY base.document_version_id, base.chunking_configuration_id,
                                base.embedding_configuration_id
                   ORDER BY m.ordinal
               ) AS previous_start,
               max(m."end") OVER (
                   PARTITION BY base.document_version_id, base.chunking_configuration_id,
                                base.embedding_configuration_id
                   ORDER BY m.ordinal ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
               ) AS covered_before,
               CASE WHEN m.ordinal IS NULL THEN NULL ELSE
                   btrim(substring(base.content_text FROM m.start + 1 FOR m."end" - m.start), :whitespace_chars) <> ''
               END AS is_eligible,
               CASE WHEN m.ordinal IS NULL THEN NULL ELSE EXISTS (
                   SELECT 1
                     FROM chunk_embeddings ce
                     JOIN embedding_configurations e ON e.id = ce.embedding_configuration_id
                    WHERE ce.content_sha256 = base.content_sha256
                      AND ce.start = m.start
                      AND ce."end" = m."end"
                      AND e.provider = base.embedding_provider
                      AND e.model_id = base.embedding_model_id
                      AND e.model_revision IS NOT DISTINCT FROM base.embedding_model_revision
                      AND e.dimensions = base.embedding_dimensions
                      AND e.document_format = base.embedding_document_format
                      AND e.normalization = base.embedding_normalization
               ) END AS has_compatible_embedding
          FROM base
          LEFT JOIN chunk_memberships m
            ON m.document_version_id = base.document_version_id
           AND m.chunking_configuration_id = base.chunking_configuration_id
    ), summarized AS (
        SELECT document_version_id,
               chunking_configuration_id,
               embedding_configuration_id,
               max(expected_chunk_count) AS expected_chunk_count,
               bool_and(has_chunking_configuration) AS has_chunking_configuration,
               bool_and(has_document_version) AS has_document_version,
               bool_and(has_content) AS has_content,
               bool_and(has_embedding_configuration) AS has_embedding_configuration,
               max(algorithm_version) AS algorithm_version,
               max(canonical_embedding_configuration_id) AS canonical_embedding_configuration_id,
               max(matching_plan_count) AS matching_plan_count,
               bool_or(has_plan) AS has_plan,
               count(ordinal)::integer AS membership_count,
               min(ordinal) AS first_ordinal,
               max(ordinal) AS last_ordinal,
               max("end") AS final_end,
               bool_and(
                   start >= 0 AND start < "end" AND "end" <= char_length(content_text)
                   AND CASE WHEN ordinal = 0
                       THEN start = 0 AND previous_start IS NULL
                       ELSE previous_start < start
                         AND start <= covered_before
                         AND ((strategy = 'fixed_tokens' AND overlap > 0) OR start >= covered_before)
                   END
               ) FILTER (WHERE ordinal IS NOT NULL) AS valid_geometry,
               count(*) FILTER (WHERE is_eligible)::integer AS eligible_membership_count,
               count(*) FILTER (WHERE is_eligible AND NOT has_compatible_embedding)::integer
                   AS missing_compatible_embedding_count,
               max(char_length(content_text)) AS content_length
          FROM ordered_memberships
         GROUP BY document_version_id, chunking_configuration_id, embedding_configuration_id
    )
    SELECT document_version_id,
           chunking_configuration_id,
           embedding_configuration_id,
           expected_chunk_count,
           has_chunking_configuration,
           has_document_version,
           has_content,
           has_embedding_configuration,
           canonical_embedding_configuration_id = embedding_configuration_id AS canonical_embedding,
           algorithm_version = '1' AS supported_chunking_algorithm,
           matching_plan_count = 1 AS unambiguous_matching_plan,
           has_plan,
           expected_chunk_count >= 1 AND membership_count = expected_chunk_count AS complete_membership_count,
           first_ordinal = 0 AND last_ordinal = expected_chunk_count - 1 AS ordinal_coverage,
           final_end = content_length AS complete_content_coverage,
           valid_geometry IS TRUE AS valid_membership_geometry,
           eligible_membership_count > 0 AS has_eligible_membership,
           missing_compatible_embedding_count = 0 AS all_eligible_memberships_embedded,
           membership_count,
           eligible_membership_count,
           missing_compatible_embedding_count
      FROM summarized
     ORDER BY document_version_id, chunking_configuration_id, embedding_configuration_id
    """
)

CHECKS = (
    "has_plan",
    "has_chunking_configuration",
    "has_document_version",
    "has_content",
    "has_embedding_configuration",
    "canonical_embedding",
    "supported_chunking_algorithm",
    "unambiguous_matching_plan",
    "complete_membership_count",
    "ordinal_coverage",
    "complete_content_coverage",
    "valid_membership_geometry",
    "has_eligible_membership",
    "all_eligible_memberships_embedded",
)


def run(args):
    database_url = settings.database_url()
    engine = create_engine(
        database_url,
        hide_parameters=True,
        poolclass=NullPool,
        connect_args={
            "options": (f"-c default_transaction_read_only=on -c statement_timeout={args.statement_timeout_ms}")
        },
    )
    counts = {name: 0 for name in CHECKS}
    examined = 0
    invalid = 0
    samples = []
    cursor = (args.after_version_id, args.after_chunking_id, args.after_embedding_id)
    try:
        with engine.connect() as connection:
            database = connection.scalar(text("SELECT current_database()"))
            if "stag" not in database.lower():
                raise RuntimeError(f"Refusing readiness audit against non-staging database {database!r}")
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            timeout = connection.scalar(text("SHOW statement_timeout"))
            if connection.scalar(text("SHOW transaction_read_only")) != "on":
                raise RuntimeError("Readiness audit connection is not read-only")
            initial_rows = connection.scalar(text("SELECT count(*) FROM chunk_plan_readiness"))
            connection.rollback()
            batch_count = 0
            while args.max_batches is None or batch_count < args.max_batches:
                transaction = connection.begin()
                try:
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                    result = (
                        connection.execute(
                            AUDIT_BATCH_SQL,
                            {
                                "after_version_id": cursor[0],
                                "after_chunking_id": cursor[1],
                                "after_embedding_id": cursor[2],
                                "batch_size": args.batch_size,
                                "whitespace_chars": PYTHON_WHITESPACE,
                            },
                        )
                        .mappings()
                        .all()
                    )
                finally:
                    transaction.rollback()
                if not result:
                    break
                batch_count += 1
                for row in result:
                    key = (
                        str(row["document_version_id"]),
                        row["chunking_configuration_id"],
                        row["embedding_configuration_id"],
                    )
                    cursor = key
                    examined += 1
                    failures = [name for name in CHECKS if not row[name]]
                    for name in CHECKS:
                        if not row[name]:
                            counts[name] += 1
                    if failures:
                        invalid += 1
                        if len(samples) < args.sample_limit:
                            samples.append(
                                {
                                    "document_version_id": key[0],
                                    "chunking_configuration_id": key[1],
                                    "embedding_configuration_id": key[2],
                                    "failed_checks": failures,
                                    "membership_count": row["membership_count"],
                                    "eligible_membership_count": row["eligible_membership_count"],
                                    "missing_compatible_embedding_count": row["missing_compatible_embedding_count"],
                                }
                            )
                if examined and examined % args.report_every < len(result):
                    print(json.dumps({"processed": examined, "invalid": invalid, "last_cursor": cursor}))
            final_rows = connection.scalar(text("SELECT count(*) FROM chunk_plan_readiness"))
            return {
                "database": database,
                "alembic_revision": revision,
                "read_only": True,
                "effective_statement_timeout": timeout,
                "statement_timeout_is_audit_session_only": True,
                "batch_size": args.batch_size,
                "batches_processed": batch_count,
                "initial_readiness_rows": initial_rows,
                "final_readiness_rows": final_rows,
                "examined": examined,
                "complete": result == []
                and args.max_batches is None
                or (args.max_batches is not None and batch_count < args.max_batches),
                "invalid": invalid,
                "failed_check_counts": counts,
                "invalid_samples": samples,
                "resume_cursor": None
                if result == []
                else {
                    "after_version_id": cursor[0],
                    "after_chunking_id": cursor[1],
                    "after_embedding_id": cursor[2],
                },
                "invariants": list(CHECKS),
            }
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--report-every", type=int, default=10000)
    parser.add_argument("--max-batches", type=int)
    parser.add_argument("--sample-limit", type=int, default=20)
    parser.add_argument("--statement-timeout-ms", type=int, default=120000)
    parser.add_argument("--after-version-id")
    parser.add_argument("--after-chunking-id", type=int)
    parser.add_argument("--after-embedding-id", type=int)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 5000:
        parser.error("--batch-size must be between 1 and 5000")
    if args.report_every < 1 or args.sample_limit < 0:
        parser.error("--report-every must be positive and --sample-limit nonnegative")
    if not 1000 <= args.statement_timeout_ms <= 300000:
        parser.error("--statement-timeout-ms must be between 1000 and 300000")
    cursor_values = (args.after_version_id, args.after_chunking_id, args.after_embedding_id)
    if any(value is not None for value in cursor_values) and not all(value is not None for value in cursor_values):
        parser.error("All three --after-* cursor values must be provided together")
    if args.max_batches is not None and args.max_batches < 1:
        parser.error("--max-batches must be positive")
    print(json.dumps(run(args), indent=2))


if __name__ == "__main__":
    main()
