"""Read-only shared-corpus attestation and gold availability; never indexing."""

from dataclasses import asdict
from hashlib import sha256
from types import SimpleNamespace

from sqlalchemy import func, literal, select, text

from investigation_engine.evaluation.metrics import fact_covered
from investigation_engine.infrastructure.ingestion.twowiki_adapter import DATASET_ID
from investigation_engine.infrastructure.persistence._embedding_mapping import configuration_filter
from investigation_engine.infrastructure.persistence.models import (
    ChunkingConfigurationRow,
    DocumentRow,
    DocumentVersionRow,
    EmbeddingConfigurationRow,
    SearchableChunkRow,
)
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import _configuration
from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import (
    _candidate_statement,
    _eligible_memberships,
    _evidence,
    _hydrate,
)
from investigation_engine.ingestion.corpus_processing import REVISION


async def preflight(engine, *, expected_database, chunking_id, embedding, cases, minimum_corpus_documents=100000):
    hashes = {
        bytes.fromhex(fact["content_sha256"])
        for case in cases
        for fact in case["gold"]
        if not fact.get("mapping_error")
    }
    async with engine.connect() as connection:
        connection = await connection.execution_options(isolation_level="REPEATABLE READ")
        if await connection.scalar(text("SHOW transaction_read_only")) != "on":
            raise ValueError("Evaluation requires a read-only connection")
        if await connection.scalar(text("SELECT current_database()")) != expected_database:
            raise ValueError("Database identity differs from expected database")
        snapshot = await connection.scalar(text("SELECT pg_current_snapshot()::text"))
        shared = (
            select(DocumentVersionRow.content_sha256)
            .join(DocumentRow, DocumentRow.current_version_id == DocumentVersionRow.id)
            .where(
                DocumentRow.owner_id.is_(None),
                DocumentRow.deleted_at.is_(None),
                DocumentVersionRow.document_id == DocumentRow.id,
            )
            .subquery()
        )
        counts = (
            (
                await connection.execute(
                    select(
                        func.count().label("shared_current_occurrences"),
                        func.count(func.distinct(shared.c.content_sha256)).label("unique_contents"),
                    ).select_from(shared)
                )
            )
            .mappings()
            .one()
        )
        dataset_count = await connection.scalar(
            select(func.count())
            .select_from(DocumentVersionRow)
            .join(DocumentRow, DocumentRow.current_version_id == DocumentVersionRow.id)
            .where(
                DocumentRow.owner_id.is_(None),
                DocumentRow.deleted_at.is_(None),
                DocumentVersionRow.document_id == DocumentRow.id,
                DocumentVersionRow.source_id == DATASET_ID,
                DocumentVersionRow.revision == REVISION,
                DocumentVersionRow.record_locator["split"].astext == "dev",
            )
        )
        if dataset_count < minimum_corpus_documents:
            raise ValueError("Expected the existing full shared 2Wiki corpus, not a question-specific miniature corpus")
        # Core mappings are used here because this is an AsyncConnection, not an ORM Session.
        mapping = (
            (
                await connection.execute(
                    select(ChunkingConfigurationRow.__table__).where(ChunkingConfigurationRow.id == chunking_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if mapping is None:
            raise ValueError("Serving chunking configuration is absent")
        chunking = _configuration(SimpleNamespace(**mapping))
        if not await connection.scalar(
            select(func.count()).select_from(EmbeddingConfigurationRow).where(*configuration_filter(embedding))
        ):
            raise ValueError("Cache embedding profile is absent from the indexed database")
        present = set(
            await connection.scalars(
                select(shared.c.content_sha256).where(shared.c.content_sha256.in_(hashes)).distinct()
            )
        )
        content_keys = (
            select(SearchableChunkRow.content_sha256)
            .where(SearchableChunkRow.content_sha256.in_(hashes))
            .distinct()
            .cte("evaluation_gold_contents")
        )
        members = _eligible_memberships(
            chunking, embedding, None, content_keys, "_evaluation_gold", candidate_ranges_only=False
        )
        rows = (
            (
                await connection.execute(
                    _hydrate(_candidate_statement(members, literal(1.0), literal(0.0), "cosine_similarity"))
                )
            )
            .mappings()
            .all()
        )
        # Core results expose the physical metadata column name; the application
        # converter expects the ORM attribute name used by repository Sessions.
        hits = tuple(
            _evidence({**row, "source_metadata": row.get("source_metadata", row.get("metadata"))}, i + 1)
            for i, row in enumerate(rows)
        )
        extensions = dict(
            (
                await connection.execute(
                    text("SELECT extname, extversion FROM pg_extension WHERE extname IN ('vector', 'pg_textsearch')")
                )
            ).all()
        )
        server = await connection.scalar(text("SHOW server_version"))
    availability = {}
    for case in cases:
        availability[case["id"]] = {
            str(i): "unresolvable_gold_mapping"
            if fact.get("mapping_error")
            else "absent_from_shared_corpus"
            if bytes.fromhex(fact["content_sha256"]) not in present
            else "eligible"
            if any(fact_covered(hit, fact) for hit in hits)
            else "present_but_ineligible"
            for i, fact in enumerate(case["gold"])
        }
    metadata = {
        "database": expected_database,
        "read_only": True,
        **dict(counts),
        "pinned_dataset_occurrences": dataset_count,
        "snapshot_fingerprint": sha256(snapshot.encode()).hexdigest(),
        "snapshot_scope": "Preflight only. Application requests use their existing per-request snapshots; corpus is not frozen across the run.",
        "attestation_limit": "Counts and selected gold availability do not prove the whole corpus content is unchanged.",
        "postgres_version": server,
        "extensions": extensions,
        "chunking_configuration_id": chunking_id,
        "chunking": asdict(chunking),
        "embedding": asdict(embedding),
        "gold_availability": availability,
    }
    return chunking, metadata, availability
