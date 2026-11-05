"""Set-based, resumable readiness backfill. Refuses non-staging databases."""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from uuid import UUID

from sqlalchemy import and_, any_, bindparam, column, exists, func, literal, select, text, values
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from investigation_engine.application.embeddings import EmbeddingConfig  # noqa: E402
from investigation_engine.infrastructure.config import settings  # noqa: E402
from investigation_engine.infrastructure.persistence._embedding_mapping import configuration_filter  # noqa: E402
from investigation_engine.infrastructure.persistence.models import (  # noqa: E402
    ChunkingConfigurationRow,
    ChunkPlanReadinessRow,
    DocumentRow,
    DocumentVersionRow,
    EmbeddingConfigurationRow,
    VersionChunkPlanRow,
)
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import (  # noqa: E402
    _configuration,
    _configuration_values,
)


@dataclass(frozen=True, slots=True)
class Candidate:
    document_id: UUID
    version_id: UUID
    chunking_configuration_id: int | None
    already_ready: bool


@dataclass(slots=True)
class Metrics:
    candidate_ms: float = 0.0
    statement_ms: float = 0.0
    commit_ms: float = 0.0
    rollback_ms: float = 0.0
    transactions: int = 0
    validation_retries: int = 0


def _candidate_query(chunking, canonical_embedding_id, last_document_id, batch_size, candidate_document_ids=None):
    values_by_key = _configuration_values(chunking)
    plan_id = (
        select(VersionChunkPlanRow.chunking_configuration_id)
        .join(ChunkingConfigurationRow)
        .where(
            VersionChunkPlanRow.document_version_id == DocumentVersionRow.id,
            *(getattr(ChunkingConfigurationRow, key) == value for key, value in values_by_key.items()),
        )
        .order_by(VersionChunkPlanRow.chunking_configuration_id)
        .limit(1)
        .correlate(DocumentVersionRow)
        .scalar_subquery()
    )
    has_marker = exists(
        select(1).where(
            ChunkPlanReadinessRow.document_version_id == DocumentVersionRow.id,
            ChunkPlanReadinessRow.chunking_configuration_id == plan_id,
            ChunkPlanReadinessRow.embedding_configuration_id == canonical_embedding_id,
        )
    )
    query = (
        select(
            DocumentRow.id,
            DocumentVersionRow.id,
            plan_id.label("chunking_configuration_id"),
            has_marker.label("already_ready"),
        )
        .select_from(DocumentRow)
        .join(DocumentVersionRow, DocumentVersionRow.id == DocumentRow.current_version_id)
        .where(
            DocumentRow.deleted_at.is_(None),
            DocumentVersionRow.document_id == DocumentRow.id,
        )
        .order_by(DocumentRow.id)
        .limit(batch_size)
    )
    if last_document_id is not None:
        query = query.where(DocumentRow.id > last_document_id)
    if candidate_document_ids is not None:
        query = query.where(
            DocumentRow.id
            == any_(
                bindparam(
                    "candidate_document_ids",
                    list(candidate_document_ids),
                    type_=ARRAY(DocumentRow.__table__.c.id.type),
                )
            )
        )
    return query


def _bulk_insert_statement(candidates, chunking, canonical_embedding_id):
    typed_values = (
        values(
            column("document_id", DocumentRow.__table__.c.id.type),
            column("document_version_id", DocumentVersionRow.__table__.c.id.type),
            column("chunking_configuration_id", VersionChunkPlanRow.__table__.c.chunking_configuration_id.type),
            name="backfill_page",
        )
        .data(
            [
                (candidate.document_id, candidate.version_id, candidate.chunking_configuration_id)
                for candidate in candidates
            ]
        )
        .cte("backfill_page")
    )
    config_values = _configuration_values(chunking)
    locked = (
        select(
            typed_values.c.document_id,
            typed_values.c.document_version_id,
            typed_values.c.chunking_configuration_id,
        )
        .select_from(
            typed_values.join(DocumentRow, DocumentRow.id == typed_values.c.document_id)
            .join(
                DocumentVersionRow,
                and_(
                    DocumentVersionRow.id == typed_values.c.document_version_id,
                    DocumentVersionRow.document_id == DocumentRow.id,
                    DocumentRow.current_version_id == DocumentVersionRow.id,
                ),
            )
            .join(
                VersionChunkPlanRow,
                and_(
                    VersionChunkPlanRow.document_version_id == DocumentVersionRow.id,
                    VersionChunkPlanRow.chunking_configuration_id == typed_values.c.chunking_configuration_id,
                ),
            )
            .join(
                ChunkingConfigurationRow,
                ChunkingConfigurationRow.id == VersionChunkPlanRow.chunking_configuration_id,
            )
        )
        .where(
            DocumentRow.deleted_at.is_(None),
            *(getattr(ChunkingConfigurationRow, key) == value for key, value in config_values.items()),
        )
        .order_by(typed_values.c.document_id)
        .with_for_update(of=(DocumentRow, DocumentVersionRow, VersionChunkPlanRow, ChunkingConfigurationRow))
        .cte("locked_active_plans")
        .prefix_with("MATERIALIZED")
    )
    already_ready = exists(
        select(1).where(
            ChunkPlanReadinessRow.document_version_id == locked.c.document_version_id,
            ChunkPlanReadinessRow.chunking_configuration_id == locked.c.chunking_configuration_id,
            ChunkPlanReadinessRow.embedding_configuration_id == canonical_embedding_id,
        )
    )
    source = (
        select(
            locked.c.document_version_id,
            locked.c.chunking_configuration_id,
            literal(canonical_embedding_id),
        )
        .where(~already_ready)
        .order_by(locked.c.document_id)
    )
    return (
        pg_insert(ChunkPlanReadinessRow)
        .from_select(
            ["document_version_id", "chunking_configuration_id", "embedding_configuration_id"],
            source,
        )
        .on_conflict_do_nothing()
        .returning(ChunkPlanReadinessRow.document_version_id)
    )


async def _apply_batch(sessions, candidates, chunking, canonical_embedding_id, metrics):
    metrics.transactions += 1
    session = sessions()
    try:
        await session.begin()
        # Match activation's configuration lock discipline; the validator also
        # takes this lock and retains all row-level validation locks until commit.
        began = perf_counter()
        await session.execute(text("LOCK TABLE embedding_configurations IN SHARE MODE"))
        metrics.statement_ms += (perf_counter() - began) * 1000
        began = perf_counter()
        inserted = set(
            (await session.scalars(_bulk_insert_statement(candidates, chunking, canonical_embedding_id))).all()
        )
        metrics.statement_ms += (perf_counter() - began) * 1000
        began = perf_counter()
        await session.commit()
        metrics.commit_ms += (perf_counter() - began) * 1000
        return inserted, metrics
    except BaseException:
        began = perf_counter()
        if session.in_transaction():
            await session.rollback()
        metrics.rollback_ms += (perf_counter() - began) * 1000
        raise
    finally:
        await session.close()


def _sqlstate(error):
    original = getattr(error, "orig", None)
    return getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)


async def _apply_with_recovery(sessions, candidates, chunking, canonical_embedding_id, metrics):
    if not candidates:
        return 0, 0, 0
    try:
        inserted, _ = await _apply_batch(sessions, candidates, chunking, canonical_embedding_id, metrics)
        # A concurrent activation/direct validated insert may win the same key.
        # Count its conflict as skipped; all persistent data remains validator-checked.
        return len(inserted), len(candidates) - len(inserted), 0
    except IntegrityError as error:
        if _sqlstate(error) != "23514":
            raise
        metrics.validation_retries += 1
        if len(candidates) == 1:
            return 0, 0, 1
        midpoint = len(candidates) // 2
        left = await _apply_with_recovery(sessions, candidates[:midpoint], chunking, canonical_embedding_id, metrics)
        right = await _apply_with_recovery(sessions, candidates[midpoint:], chunking, canonical_embedding_id, metrics)
        return tuple(a + b for a, b in zip(left, right, strict=True))


async def backfill(
    sessions,
    chunking,
    embedding,
    *,
    batch_size=200,
    dry_run=False,
    report_every=1000,
    max_documents=None,
    candidate_document_ids=None,
    emit=print,
):
    if not 1 <= batch_size <= 2000:
        raise ValueError("batch_size must be between 1 and 2000")
    if report_every < 1:
        raise ValueError("report_every must be positive")
    if max_documents is not None and max_documents < 1:
        raise ValueError("max_documents must be positive when supplied")
    if candidate_document_ids is not None and len(candidate_document_ids) > 5000:
        raise ValueError("candidate_document_ids is limited to 5000 for controlled diagnostics")
    counts = {"processed": 0, "verified": 0, "skipped": 0, "failed": 0, "would_validate": 0}
    metrics = Metrics()
    started = perf_counter()
    async with sessions() as session:
        async with session.begin():
            canonical_id = await session.scalar(
                select(func.min(EmbeddingConfigurationRow.id)).where(*configuration_filter(embedding))
            )
            total_query = (
                select(func.count())
                .select_from(DocumentRow)
                .where(
                    DocumentRow.deleted_at.is_(None),
                    DocumentRow.current_version_id.is_not(None),
                )
            )
            if candidate_document_ids is not None:
                total_query = total_query.where(
                    DocumentRow.id
                    == any_(
                        bindparam(
                            "candidate_document_ids",
                            list(candidate_document_ids),
                            type_=ARRAY(DocumentRow.__table__.c.id.type),
                        )
                    )
                )
            total = await session.scalar(total_query)
    if canonical_id is None:
        raise ValueError("No stored configuration matches the selected embedding profile")
    last_document_id = None
    next_report = report_every
    while True:
        if max_documents is not None:
            remaining = max_documents - counts["processed"]
            if remaining <= 0:
                break
            page_limit = min(batch_size, remaining)
        else:
            page_limit = batch_size
        async with sessions() as session:
            async with session.begin():
                began = perf_counter()
                page = (
                    await session.execute(
                        _candidate_query(
                            chunking,
                            canonical_id,
                            last_document_id,
                            page_limit,
                            candidate_document_ids,
                        )
                    )
                ).all()
                metrics.candidate_ms += (perf_counter() - began) * 1000
        if not page:
            break
        candidates = [Candidate(*row) for row in page]
        last_document_id = candidates[-1].document_id
        counts["processed"] += len(candidates)
        already_ready = sum(candidate.already_ready for candidate in candidates)
        no_plan = sum(candidate.chunking_configuration_id is None for candidate in candidates)
        if dry_run:
            counts["verified"] += already_ready
            counts["skipped"] += no_plan
            counts["would_validate"] += len(candidates) - already_ready - no_plan
        else:
            counts["verified"] += already_ready
            counts["skipped"] += no_plan
            pending = [
                candidate
                for candidate in candidates
                if candidate.chunking_configuration_id is not None and not candidate.already_ready
            ]
            verified, skipped, failed = await _apply_with_recovery(sessions, pending, chunking, canonical_id, metrics)
            counts["verified"] += verified
            counts["skipped"] += skipped
            counts["failed"] += failed
        elapsed = perf_counter() - started
        if counts["processed"] >= next_report:
            rate = counts["processed"] / elapsed if elapsed else 0
            remaining = max(0, total - counts["processed"])
            eta = remaining / rate if rate else None
            emit(
                f"Processed: {counts['processed']:,} | Verified: {counts['verified']:,} | "
                f"Skipped: {counts['skipped']:,} | Failed: {counts['failed']:,} | "
                f"Rate: {rate:,.1f}/s | ETA: {_duration(eta)}"
            )
            while next_report <= counts["processed"]:
                next_report += report_every
    elapsed = perf_counter() - started
    counts.update(
        elapsed_seconds=round(elapsed, 3),
        average_records_per_second=round(counts["processed"] / elapsed, 3) if elapsed else 0,
        total_active_documents=total,
        candidate_query_ms=round(metrics.candidate_ms, 3),
        database_statement_ms=round(metrics.statement_ms, 3),
        commit_ms=round(metrics.commit_ms, 3),
        rollback_ms=round(metrics.rollback_ms, 3),
        transactions=metrics.transactions,
        validation_retry_subdivisions=metrics.validation_retries,
        peak_python_memory_bytes=_peak_memory(),
        dry_run=dry_run,
    )
    return counts


def _duration(seconds):
    if seconds is None:
        return "unknown"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}"


def _peak_memory():
    try:
        import tracemalloc

        return tracemalloc.get_traced_memory()[1] if tracemalloc.is_tracing() else None
    except RuntimeError:
        return None


def _arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunking-configuration-id", type=int, required=True)
    parser.add_argument("--embedding-configuration-id", type=int, required=True)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--report-every", type=int, default=1000)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


async def _run(args):
    connect_args = {}
    if args.dry_run:
        connect_args["options"] = "-c default_transaction_read_only=on"
    engine = create_async_engine(
        settings.database_url(), poolclass=NullPool, hide_parameters=True, connect_args=connect_args
    )
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            async with session.begin():
                database = await session.scalar(text("SELECT current_database()"))
                if "stag" not in database.casefold():
                    raise RuntimeError("This script refuses databases whose name does not identify staging")
                chunk_row = await session.get(ChunkingConfigurationRow, args.chunking_configuration_id)
                embedding_row = await session.get(EmbeddingConfigurationRow, args.embedding_configuration_id)
        if chunk_row is None or embedding_row is None:
            raise ValueError("Both selected configuration IDs must exist in this staging database")
        chunking = _configuration(chunk_row)
        embedding = EmbeddingConfig(
            provider=embedding_row.provider,
            model_id=embedding_row.model_id,
            model_revision=embedding_row.model_revision,
            dimensions=embedding_row.dimensions,
            document_format=embedding_row.document_format,
            normalization=embedding_row.normalization,
        )
        import tracemalloc

        if not tracemalloc.is_tracing():
            tracemalloc.start()
        result = await backfill(
            sessions,
            chunking,
            embedding,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            report_every=args.report_every,
        )
        return result
    finally:
        await engine.dispose()


def main():
    args = _arguments()
    result = asyncio.run(_run(args), loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None)
    print(json.dumps({"database_backfill_complete": not args.dry_run, "summary": result}, indent=2))


if __name__ == "__main__":
    main()
