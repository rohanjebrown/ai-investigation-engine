"""Preflight, backfill and verify membership-owned embeddings.

Default invocation is read-only. Writing requires --execute and the exact target
database name via --expected-database-name. No embedding provider is used.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.audit_chunk_plan_readiness import AUDIT_BATCH_SQL, CHECKS  # noqa: E402

from investigation_engine.infrastructure.config import settings  # noqa: E402
from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import (  # noqa: E402
    PYTHON_WHITESPACE,
)

BATCH_SIZE_LIMIT = 5000

_PROFILES = """
profiles AS MATERIALIZED (
    SELECT id AS source_configuration_id,
           min(id) OVER (
               PARTITION BY provider, model_id, model_revision, dimensions, document_format, normalization
           ) AS target_configuration_id
      FROM embedding_configurations
)
"""

_SOURCES = f"""
WITH {_PROFILES}, source_vectors AS MATERIALIZED (
    SELECT m.document_version_id,
           m.chunking_configuration_id,
           m.ordinal,
           p.target_configuration_id,
           min(ce.embedding_configuration_id) AS source_configuration_id,
           count(*)::bigint AS compatible_match_count,
           count(DISTINCT ce.embedding::text)::bigint AS distinct_vector_count,
           min(ce.embedding::text) AS selected_vector_text,
           min(pg_column_size(ce.embedding))::bigint AS vector_bytes
      FROM chunk_memberships m
      JOIN document_versions v ON v.id = m.document_version_id
      JOIN contents c ON c.sha256 = v.content_sha256
      JOIN profiles p ON TRUE
      JOIN chunk_embeddings ce
        ON ce.embedding_configuration_id = p.source_configuration_id
       AND ce.content_sha256 = v.content_sha256
       AND ce.start = m.start
       AND ce."end" = m."end"
     WHERE btrim(substring(c.text FROM m.start + 1 FOR m."end" - m.start), :whitespace_chars) <> ''
     GROUP BY m.document_version_id, m.chunking_configuration_id, m.ordinal, p.target_configuration_id
)
"""

_READY_MISSING_SQL = text(
    f"""
    WITH {_PROFILES}
    SELECT count(*)::bigint
      FROM chunk_plan_readiness r
      JOIN version_chunk_plans p
        ON p.document_version_id = r.document_version_id
       AND p.chunking_configuration_id = r.chunking_configuration_id
      JOIN document_versions v ON v.id = r.document_version_id
      JOIN contents c ON c.sha256 = v.content_sha256
      JOIN chunk_memberships m
        ON m.document_version_id = r.document_version_id
       AND m.chunking_configuration_id = r.chunking_configuration_id
      JOIN profiles profile ON profile.target_configuration_id = r.embedding_configuration_id
 LEFT JOIN chunk_embeddings ce
        ON ce.embedding_configuration_id = profile.source_configuration_id
       AND ce.content_sha256 = v.content_sha256
       AND ce.start = m.start
       AND ce."end" = m."end"
     WHERE btrim(substring(c.text FROM m.start + 1 FOR m."end" - m.start), :whitespace_chars) <> ''
       AND ce.embedding_configuration_id IS NULL
    """
)

_STATS_SQL = text(
    f"""
    {_SOURCES}
    SELECT
      (SELECT count(*)::bigint FROM chunk_memberships) AS total_memberships,
      (SELECT count(*)::bigint
         FROM chunk_memberships m
         JOIN document_versions v ON v.id = m.document_version_id
         JOIN contents c ON c.sha256 = v.content_sha256
        WHERE btrim(substring(c.text FROM m.start + 1 FOR m."end" - m.start), :whitespace_chars) <> ''
      ) AS eligible_memberships,
      (SELECT count(*)::bigint FROM chunk_memberships m
         JOIN document_versions v ON v.id = m.document_version_id
         JOIN contents c ON c.sha256 = v.content_sha256
        WHERE btrim(substring(c.text FROM m.start + 1 FOR m."end" - m.start), :whitespace_chars) = ''
      ) AS whitespace_memberships,
      (SELECT count(*)::bigint FROM membership_chunk_embeddings) AS existing_membership_embeddings,
      (SELECT count(*)::bigint FROM source_vectors) AS legacy_matches,
      (SELECT count(*)::bigint
         FROM chunk_memberships m
         JOIN document_versions v ON v.id = m.document_version_id
         JOIN contents c ON c.sha256 = v.content_sha256
        WHERE btrim(substring(c.text FROM m.start + 1 FOR m."end" - m.start), :whitespace_chars) <> ''
          AND NOT EXISTS (
            SELECT 1 FROM chunk_embeddings ce
             WHERE ce.content_sha256 = v.content_sha256
               AND ce.start = m.start AND ce."end" = m."end"
          )
      ) AS eligible_memberships_without_legacy_vectors,
      (SELECT count(*)::bigint FROM source_vectors s
         LEFT JOIN membership_chunk_embeddings me
           ON (me.document_version_id, me.chunking_configuration_id, me.ordinal, me.embedding_configuration_id)
            = (s.document_version_id, s.chunking_configuration_id, s.ordinal, s.target_configuration_id)
        WHERE me.document_version_id IS NULL
      ) AS missing_membership_embeddings,
      (SELECT count(*)::bigint FROM source_vectors s
         JOIN membership_chunk_embeddings me
           ON (me.document_version_id, me.chunking_configuration_id, me.ordinal, me.embedding_configuration_id)
            = (s.document_version_id, s.chunking_configuration_id, s.ordinal, s.target_configuration_id)
        WHERE me.embedding::text IS DISTINCT FROM s.selected_vector_text
      ) AS conflicting_membership_embeddings,
      (SELECT count(*)::bigint FROM source_vectors WHERE compatible_match_count > 1) AS duplicate_compatible_matches,
      (SELECT count(*)::bigint FROM source_vectors WHERE distinct_vector_count > 1) AS ambiguous_vector_matches,
      (SELECT coalesce(sum(s.vector_bytes + 48), 0)::bigint
         FROM source_vectors s
         LEFT JOIN membership_chunk_embeddings me
           ON (me.document_version_id, me.chunking_configuration_id, me.ordinal, me.embedding_configuration_id)
            = (s.document_version_id, s.chunking_configuration_id, s.ordinal, s.target_configuration_id)
        WHERE me.document_version_id IS NULL
      ) AS estimated_additional_bytes,
      (SELECT count(*)::bigint FROM chunk_plan_readiness) AS readiness_rows,
      (SELECT pg_total_relation_size('membership_chunk_embeddings'::regclass))::bigint AS current_membership_table_bytes
    """
)

_BY_CONFIGURATION_SQL = text(
    f"""
    {_SOURCES}
    SELECT s.target_configuration_id AS embedding_configuration_id,
           ec.provider,
           ec.model_id,
           ec.model_revision,
           ec.dimensions,
           ec.document_format,
           ec.normalization,
           count(*)::bigint AS legacy_relationships,
           count(*) FILTER (WHERE me.document_version_id IS NULL)::bigint AS missing_membership_embeddings,
           count(*) FILTER (WHERE me.document_version_id IS NOT NULL
                              AND me.embedding::text IS DISTINCT FROM s.selected_vector_text)::bigint
             AS conflicting_membership_embeddings,
           count(*) FILTER (WHERE s.compatible_match_count > 1)::bigint AS duplicate_compatible_matches,
           count(*) FILTER (WHERE s.distinct_vector_count > 1)::bigint AS ambiguous_vector_matches,
           coalesce(sum(s.vector_bytes + 48) FILTER (WHERE me.document_version_id IS NULL), 0)::bigint
             AS estimated_additional_bytes
      FROM source_vectors s
      JOIN embedding_configurations ec ON ec.id = s.target_configuration_id
 LEFT JOIN membership_chunk_embeddings me
        ON (me.document_version_id, me.chunking_configuration_id, me.ordinal, me.embedding_configuration_id)
         = (s.document_version_id, s.chunking_configuration_id, s.ordinal, s.target_configuration_id)
     GROUP BY s.target_configuration_id, ec.provider, ec.model_id, ec.model_revision,
              ec.dimensions, ec.document_format, ec.normalization
     ORDER BY s.target_configuration_id
    """
)

_UNEXPECTED_SQL = text(
    f"""
    {_SOURCES}
    SELECT count(*)::bigint
      FROM membership_chunk_embeddings me
 LEFT JOIN source_vectors s
        ON (s.document_version_id, s.chunking_configuration_id, s.ordinal, s.target_configuration_id)
         = (me.document_version_id, me.chunking_configuration_id, me.ordinal, me.embedding_configuration_id)
     WHERE s.document_version_id IS NULL
    """
)

_DUPLICATE_IDENTITY_SQL = text(
    """
    SELECT count(*)::bigint
      FROM (
        SELECT document_version_id, chunking_configuration_id, ordinal, embedding_configuration_id
          FROM membership_chunk_embeddings
         GROUP BY document_version_id, chunking_configuration_id, ordinal, embedding_configuration_id
        HAVING count(*) > 1
      ) duplicates
    """
)

_INTEGRITY_SQL = text(
    """
    WITH plan_counts AS (
      SELECT p.document_version_id, p.chunking_configuration_id, p.expected_chunk_count,
             count(m.ordinal)::bigint AS actual_count,
             min(m.ordinal) AS first_ordinal,
             max(m.ordinal) AS last_ordinal
        FROM version_chunk_plans p
        LEFT JOIN chunk_memberships m
          ON m.document_version_id = p.document_version_id
         AND m.chunking_configuration_id = p.chunking_configuration_id
       GROUP BY p.document_version_id, p.chunking_configuration_id, p.expected_chunk_count
    )
    SELECT
      (SELECT count(*)::bigint
         FROM chunk_memberships m
         JOIN document_versions v ON v.id = m.document_version_id
         JOIN contents c ON c.sha256 = v.content_sha256
        WHERE m.start < 0 OR m."end" <= m.start OR m."end" > char_length(c.text)
      ) AS invalid_membership_boundaries,
      (SELECT count(*)::bigint FROM plan_counts
        WHERE actual_count <> expected_chunk_count
           OR (actual_count > 0 AND (first_ordinal <> 0 OR last_ordinal <> expected_chunk_count - 1))
      ) AS incomplete_chunk_plans
    """
)

_BATCH_KEYS_SQL = text(
    f"""
    {_SOURCES}
    SELECT s.document_version_id, s.chunking_configuration_id, s.ordinal, s.target_configuration_id
      FROM source_vectors s
 LEFT JOIN membership_chunk_embeddings me
        ON (me.document_version_id, me.chunking_configuration_id, me.ordinal, me.embedding_configuration_id)
         = (s.document_version_id, s.chunking_configuration_id, s.ordinal, s.target_configuration_id)
     WHERE me.document_version_id IS NULL
       AND (
         CAST(:after_version_id AS uuid) IS NULL
         OR ROW(s.document_version_id, s.chunking_configuration_id, s.ordinal, s.target_configuration_id)
            > ROW(CAST(:after_version_id AS uuid), :after_chunking_id, :after_ordinal, :after_embedding_id)
       )
     ORDER BY s.document_version_id, s.chunking_configuration_id, s.ordinal, s.target_configuration_id
     LIMIT :batch_size
    """
)

_INSERT_BATCH_SQL = text(
    f"""
    WITH page AS MATERIALIZED (
      SELECT (entry->>'document_version_id')::uuid AS document_version_id,
             (entry->>'chunking_configuration_id')::integer AS chunking_configuration_id,
             (entry->>'ordinal')::integer AS ordinal,
             (entry->>'embedding_configuration_id')::integer AS embedding_configuration_id
        FROM jsonb_array_elements(CAST(:page AS jsonb)) entry
    ), {_PROFILES}, source_vectors AS MATERIALIZED (
      SELECT DISTINCT ON (m.document_version_id, m.chunking_configuration_id, m.ordinal,
                          p.target_configuration_id)
             m.document_version_id, m.chunking_configuration_id, m.ordinal,
             p.target_configuration_id, ce.embedding_configuration_id AS source_configuration_id,
             ce.embedding
        FROM page pg
        JOIN chunk_memberships m
          ON (m.document_version_id, m.chunking_configuration_id, m.ordinal)
           = (pg.document_version_id, pg.chunking_configuration_id, pg.ordinal)
        JOIN document_versions v ON v.id = m.document_version_id
        JOIN profiles p ON p.target_configuration_id = pg.embedding_configuration_id
        JOIN chunk_embeddings ce
          ON ce.embedding_configuration_id = p.source_configuration_id
         AND ce.content_sha256 = v.content_sha256
         AND ce.start = m.start
         AND ce."end" = m."end"
        ORDER BY m.document_version_id, m.chunking_configuration_id, m.ordinal,
                 p.target_configuration_id, ce.embedding_configuration_id
    )
    INSERT INTO membership_chunk_embeddings (
      document_version_id, chunking_configuration_id, ordinal, embedding_configuration_id,
      embedding, created_at
    )
    SELECT s.document_version_id, s.chunking_configuration_id, s.ordinal, s.target_configuration_id,
           s.embedding, now()
      FROM source_vectors s
     ORDER BY s.document_version_id, s.chunking_configuration_id, s.ordinal, s.target_configuration_id
    ON CONFLICT (document_version_id, chunking_configuration_id, ordinal, embedding_configuration_id)
      DO NOTHING
    """
)

_VERIFY_BATCH_SQL = text(
    f"""
    WITH page AS MATERIALIZED (
      SELECT (entry->>'document_version_id')::uuid AS document_version_id,
             (entry->>'chunking_configuration_id')::integer AS chunking_configuration_id,
             (entry->>'ordinal')::integer AS ordinal,
             (entry->>'embedding_configuration_id')::integer AS embedding_configuration_id
        FROM jsonb_array_elements(CAST(:page AS jsonb)) entry
    ), {_PROFILES}, source_vectors AS MATERIALIZED (
      SELECT DISTINCT ON (m.document_version_id, m.chunking_configuration_id, m.ordinal,
                          p.target_configuration_id)
             m.document_version_id, m.chunking_configuration_id, m.ordinal,
             p.target_configuration_id, ce.embedding
        FROM page pg
        JOIN chunk_memberships m
          ON (m.document_version_id, m.chunking_configuration_id, m.ordinal)
           = (pg.document_version_id, pg.chunking_configuration_id, pg.ordinal)
        JOIN document_versions v ON v.id = m.document_version_id
        JOIN profiles p ON p.target_configuration_id = pg.embedding_configuration_id
        JOIN chunk_embeddings ce
          ON ce.embedding_configuration_id = p.source_configuration_id
         AND ce.content_sha256 = v.content_sha256
         AND ce.start = m.start
         AND ce."end" = m."end"
        ORDER BY m.document_version_id, m.chunking_configuration_id, m.ordinal,
                 p.target_configuration_id, ce.embedding_configuration_id
    )
    SELECT count(*)::bigint AS checked,
           count(*) FILTER (WHERE me.document_version_id IS NULL)::bigint AS missing,
           count(*) FILTER (WHERE me.embedding::text IS DISTINCT FROM s.embedding::text)::bigint AS mismatched
      FROM source_vectors s
      LEFT JOIN membership_chunk_embeddings me
        ON (me.document_version_id, me.chunking_configuration_id, me.ordinal, me.embedding_configuration_id)
         = (s.document_version_id, s.chunking_configuration_id, s.ordinal, s.target_configuration_id)
    """
)


def _params(batch_size: int | None = None, after=None) -> dict[str, object]:
    params: dict[str, object] = {"whitespace_chars": PYTHON_WHITESPACE}
    if batch_size is not None:
        params["batch_size"] = batch_size
        params.update(
            after_version_id=None if after is None else str(after["document_version_id"]),
            after_chunking_id=None if after is None else after["chunking_configuration_id"],
            after_ordinal=None if after is None else after["ordinal"],
            after_embedding_id=None if after is None else after["target_configuration_id"],
        )
    return params


async def _audit_readiness(session) -> dict[str, object]:
    cursor = (None, None, None)
    audited = 0
    invalid_readiness = 0
    failures = {name: 0 for name in CHECKS}
    while True:
        rows = (
            (
                await session.execute(
                    AUDIT_BATCH_SQL,
                    {
                        "after_version_id": cursor[0],
                        "after_chunking_id": cursor[1],
                        "after_embedding_id": cursor[2],
                        "batch_size": 500,
                        "whitespace_chars": PYTHON_WHITESPACE,
                    },
                )
            )
            .mappings()
            .all()
        )
        if not rows:
            break
        for row in rows:
            cursor = (row["document_version_id"], row["chunking_configuration_id"], row["embedding_configuration_id"])
            audited += 1
            checks_failed = [name for name in CHECKS if not row[name]]
            if checks_failed:
                invalid_readiness += 1
                for name in checks_failed:
                    failures[name] += 1
    return {
        "readiness_rows_audited": audited,
        "invalid_readiness_rows": invalid_readiness,
        "readiness_failed_checks": failures,
    }


async def preflight(session) -> dict[str, object]:
    """Return read-only counts and inconsistencies for all versions/configurations."""
    stats = dict((await session.execute(_STATS_SQL, _params())).mappings().one())
    by_configuration = [dict(row) for row in (await session.execute(_BY_CONFIGURATION_SQL, _params())).mappings().all()]
    unexpected = await session.scalar(_UNEXPECTED_SQL, _params())
    duplicate_identities = await session.scalar(_DUPLICATE_IDENTITY_SQL)
    integrity = dict((await session.execute(_INTEGRITY_SQL)).mappings().one())
    missing_ready = await session.scalar(_READY_MISSING_SQL, _params())
    stats["unexpected_membership_embeddings"] = unexpected
    stats["duplicate_membership_configuration_pairs"] = duplicate_identities
    stats.update(integrity)
    stats["ready_plan_missing_legacy_embeddings"] = missing_ready
    stats["missing_legacy_vectors_required_by_readiness"] = missing_ready
    stats["by_embedding_configuration"] = by_configuration
    stats["estimated_additional_bytes_note"] = (
        "Approximation is pgvector payload plus 48 bytes per row; excludes table and index overhead."
    )
    stats.update(await _audit_readiness(session))
    stats["ambiguous_or_conflicting_matches"] = (
        stats["ambiguous_vector_matches"] > 0
        or stats["conflicting_membership_embeddings"] > 0
        or stats["unexpected_membership_embeddings"] > 0
        or stats["duplicate_membership_configuration_pairs"] > 0
        or stats["invalid_membership_boundaries"] > 0
        or stats["ready_plan_missing_legacy_embeddings"] > 0
        or stats["invalid_readiness_rows"] > 0
    )
    return stats


async def verify(session) -> dict[str, object]:
    """Verify data relationship, exact vectors, and readiness coverage read-only."""
    report = await preflight(session)
    report["verified_relationships"] = report["legacy_matches"] - report["missing_membership_embeddings"]
    report["verification_passed"] = not any(
        (
            report["missing_membership_embeddings"],
            report["conflicting_membership_embeddings"],
            report["ambiguous_vector_matches"],
            report["unexpected_membership_embeddings"],
            report["duplicate_membership_configuration_pairs"],
            report["invalid_membership_boundaries"],
            report["ready_plan_missing_legacy_embeddings"],
            report["invalid_readiness_rows"],
        )
    )
    return report


async def backfill(sessions, *, batch_size: int = 500, max_batches: int | None = None) -> dict[str, object]:
    """Copy compatible legacy vectors in short, restartable transactions."""
    if type(batch_size) is not int or not 1 <= batch_size <= BATCH_SIZE_LIMIT:
        raise ValueError(f"batch_size must be between 1 and {BATCH_SIZE_LIMIT}")
    if max_batches is not None and (type(max_batches) is not int or max_batches < 1):
        raise ValueError("max_batches must be a positive integer when provided")
    async with sessions() as session:
        async with session.begin():
            initial = await preflight(session)
    if any(
        (
            initial["ambiguous_vector_matches"],
            initial["conflicting_membership_embeddings"],
            initial["unexpected_membership_embeddings"],
            initial["duplicate_membership_configuration_pairs"],
            initial["invalid_membership_boundaries"],
            initial["ready_plan_missing_legacy_embeddings"],
            initial["invalid_readiness_rows"],
        )
    ):
        raise RuntimeError(f"Preflight inconsistencies must be resolved before writing: {initial}")
    inserted = 0
    batches = 0
    after = None
    reconciliation_pass = False
    while True:
        if max_batches is not None and batches >= max_batches:
            break
        async with sessions() as session:
            async with session.begin():
                await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
                keys = (await session.execute(_BATCH_KEYS_SQL, _params(batch_size, after))).mappings().all()
                if not keys:
                    if after is not None and not reconciliation_pass:
                        after = None
                        reconciliation_pass = True
                        continue
                    break
                after = keys[-1]
                page = json.dumps(
                    [
                        {
                            "document_version_id": str(row["document_version_id"]),
                            "chunking_configuration_id": row["chunking_configuration_id"],
                            "ordinal": row["ordinal"],
                            "embedding_configuration_id": row["target_configuration_id"],
                        }
                        for row in keys
                    ]
                )
                ambiguous = await session.scalar(
                    text(
                        f"{_SOURCES} SELECT count(*) FROM source_vectors s "
                        "JOIN jsonb_to_recordset(CAST(:page AS jsonb)) AS pg("
                        "document_version_id uuid, chunking_configuration_id integer, ordinal integer, "
                        "embedding_configuration_id integer) "
                        "ON (pg.document_version_id, pg.chunking_configuration_id, pg.ordinal, "
                        "pg.embedding_configuration_id) = (s.document_version_id, s.chunking_configuration_id, "
                        "s.ordinal, s.target_configuration_id) WHERE s.distinct_vector_count > 1"
                    ),
                    {**_params(), "page": page},
                )
                if ambiguous:
                    raise RuntimeError("Conflicting vectors exist among equivalent embedding configurations")
                result = await session.execute(_INSERT_BATCH_SQL, {**_params(), "page": page})
                inserted += result.rowcount or 0
                checked = (await session.execute(_VERIFY_BATCH_SQL, {**_params(), "page": page})).mappings().one()
                if checked["missing"] or checked["mismatched"]:
                    raise RuntimeError("Batch verification found missing or conflicting membership embeddings")
                batches += 1
    async with sessions() as session:
        async with session.begin():
            final = await verify(session)
    partial = max_batches is not None and final["missing_membership_embeddings"] > 0
    if not partial and not final["verification_passed"]:
        raise RuntimeError(f"Backfill verification failed: {final}")
    final["inserted_this_run"] = inserted
    final["batches_committed"] = batches
    final["backfill_complete"] = not partial and final["verification_passed"]
    return final


async def _run(args) -> dict[str, object]:
    connect_args = {} if args.execute else {"options": "-c default_transaction_read_only=on"}
    engine = create_async_engine(
        settings.database_url(), poolclass=NullPool, hide_parameters=True, connect_args=connect_args
    )
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            async with session.begin():
                database = await session.scalar(text("SELECT current_database()"))
                if args.expected_database_name and database != args.expected_database_name:
                    raise RuntimeError(f"Connected to {database!r}, expected {args.expected_database_name!r}")
                initial = await preflight(session)
        if args.execute:
            result = await backfill(sessions, batch_size=args.batch_size, max_batches=args.max_batches)
        elif args.verify:
            async with sessions() as session:
                async with session.begin():
                    result = await verify(session)
        else:
            result = initial
        result["database"] = database
        result["mode"] = "execute" if args.execute else ("verify" if args.verify else "preflight")
        return result
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--execute", action="store_true", help="copy vectors; requires --expected-database-name")
    modes.add_argument("--verify", action="store_true", help="run read-only post-backfill verification")
    parser.add_argument("--expected-database-name", help="refuse writes unless the connected database name matches")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--max-batches", type=int, help="optional execution limit for controlled/resumable runs")
    args = parser.parse_args()
    if args.execute and not args.expected_database_name:
        parser.error("--execute requires --expected-database-name")
    print(
        json.dumps(
            asyncio.run(
                _run(args),
                loop_factory=asyncio.SelectorEventLoop,
            ),
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
