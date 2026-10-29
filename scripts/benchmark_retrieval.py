"""Disposable synthetic exact-retrieval benchmark; no settings or live AI.

Run from the repository root with the existing dev environment and cached Docker
image. Optionally pass a trusted frozen pre-change repository module to compare
both implementations on identical rows. Output is local/ignored by default.
"""

import argparse
import asyncio
import importlib.util
import json
import logging
import random
from dataclasses import asdict
from hashlib import sha256
from math import ceil
from pathlib import Path
from statistics import median
from time import perf_counter
from uuid import uuid7

import docker
from alembic import command
from alembic.config import Config
from sqlalchemy import bindparam, create_engine, event, insert, select, text, update
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer

from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy, chunk_document
from investigation_engine.application.embeddings import EmbeddingConfig
from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
from investigation_engine.application.rrf import reciprocal_rank_fusion
from investigation_engine.application.timing import stage_timing
from investigation_engine.application.use_cases.index_document_version import IndexDocumentVersion
from investigation_engine.domain.content import Content
from investigation_engine.domain.document import Document
from investigation_engine.domain.document_version import DocumentVersion
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.infrastructure.persistence.models import (
    ChunkEmbeddingRow,
    ChunkingConfigurationRow,
    ChunkMembershipRow,
    ContentRow,
    DocumentRow,
    DocumentVersionRow,
    EmbeddingConfigurationRow,
    VersionChunkPlanRow,
)
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import (
    PostgreSQLChunkPlanRepository,
)
from investigation_engine.infrastructure.persistence.postgresql_document_activation_repository import (
    PostgreSQLDocumentActivationRepository,
)
from investigation_engine.infrastructure.persistence.postgresql_document_repository import PostgreSQLDocumentRepository
from investigation_engine.infrastructure.persistence.postgresql_embedding_repository import (
    PostgreSQLEmbeddingRepository,
)
from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import PostgreSQLEvidenceRepository

IMAGE = "pgvector/pgvector@sha256:9d2e61c7352b9e9f4798df5fd9a498f043f4cda1cdacc707de3d198650f4321e"
randomizer = random.Random(42)
DENSE = [randomizer.uniform(-1, 1) for _ in range(768)]


def portable_signature(result):
    """Return evidence identity/content/scoring without generated DB UUIDs."""
    return [
        dict(
            occurrence=(
                item.source.kind.value,
                item.source.source_id,
                item.source.revision,
                json.dumps(item.source.record_locator, sort_keys=True, separators=(",", ":"), default=str),
            ),
            content_sha256=item.content_sha256,
            start=item.start,
            end=item.end,
            text=item.text,
            source=asdict(item.source),
            source_spans=[asdict(span) for span in item.source_spans],
            score=item.score,
            score_kind=item.score_kind,
            semantic_score=item.semantic_score,
            keyword_score=item.keyword_score,
            rank=item.rank,
            semantic_rank=item.semantic_rank,
            keyword_rank=item.keyword_rank,
        )
        for item in result
    ]


class Tokenizer:
    identity = TokenizerIdentity(name="characters", revision="1", artifact_sha256="0" * 64, runtime_version="benchmark")

    def offsets(self, value):
        return tuple((i, i + 1) for i in range(len(value)))


class Provider:
    configuration = EmbeddingConfig(provider="synthetic", model_id="dense-benchmark", model_revision="1")

    async def embed_documents(self, texts):
        return tuple(tuple(DENSE) for _ in texts)


class StageMeasurements(logging.Handler):
    """Collect only numeric stage samples emitted during a benchmark batch."""

    def __init__(self):
        super().__init__()
        self.stages = {}

    def emit(self, record):
        self.stages.setdefault(record.stage, []).append(record.duration_ms)

    def summary(self):
        return {
            name: {
                "samples": len(values),
                "p50_ms": median(values),
                "p95_ms": sorted(values)[ceil(0.95 * len(values)) - 1],
            }
            for name, values in self.stages.items()
        }


async def benchmark(engine, url, args, container):
    async_engine = create_async_engine(url, poolclass=NullPool, hide_parameters=True)
    sessions = async_sessionmaker(async_engine, expire_on_commit=False)
    try:
        doc = Document(owner_id=None, title="Benchmark")
        version = DocumentVersion(
            document_id=doc.id,
            version_number=1,
            content=Content("seed source"),
            source=SourceReference(kind=SourceKind.DATASET, source_id="seed"),
        )
        await PostgreSQLDocumentRepository(sessions).register(doc, version)
        config = ChunkingConfig(strategy=ChunkingStrategy.WHOLE_PASSAGE, tokenizer=Tokenizer.identity, max_tokens=1024)
        plans = PostgreSQLChunkPlanRepository(sessions)
        await plans.save(chunk_document(version, config=config, tokenizer=Tokenizer()))
        profile = Provider.configuration
        await IndexDocumentVersion(
            plans,
            PostgreSQLEmbeddingRepository(sessions),
            PostgreSQLDocumentActivationRepository(sessions),
            Provider(),
            max_attempts=1,
        ).execute(doc.id, version.id, config, profile)
        with engine.connect() as connection:
            templates = [
                dict(connection.execute(select(model)).mappings().one())
                for model in (DocumentRow, DocumentVersionRow, VersionChunkPlanRow, ChunkEmbeddingRow)
            ]
        repositories = {"optimized": PostgreSQLEvidenceRepository(sessions)}
        if args.baseline:
            spec = importlib.util.spec_from_file_location("retrieval_benchmark_baseline", args.baseline)
            baseline = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(baseline)
            repositories = {"baseline": baseline.PostgreSQLEvidenceRepository(sessions), **repositories}
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
        with engine.connect() as connection:
            metadata = dict(
                server_version=connection.scalar(text("SHOW server_version")),
                pgvector_version=connection.scalar(text("SELECT extversion FROM pg_extension WHERE extname='vector'")),
                work_mem=connection.scalar(text("SHOW work_mem")),
                image=IMAGE,
                embedding_storage=args.embedding_storage,
                samples=args.samples,
                p95_method="nearest rank ceil(0.95*N)",
                warmup_calls=1,
                baseline_sha256=sha256(args.baseline.read_bytes()).hexdigest() if args.baseline else None,
                optimized_sha256=sha256(
                    Path(
                        "src/investigation_engine/infrastructure/persistence/postgresql_evidence_repository.py"
                    ).read_bytes()
                ).hexdigest(),
            )
        resources = container.get_wrapped_container().attrs["HostConfig"]
        metadata.update(container_memory_limit=resources.get("Memory"), container_nanocpus=resources.get("NanoCpus"))
        (output / "run.json").write_text(json.dumps(metadata, indent=2), encoding="utf8")
        measurements = []
        previous = 1
        all_lengths = []
        for size in args.sizes:
            started = perf_counter()
            with engine.begin() as connection:
                for batch in range(previous, size, 1000):
                    rows = [[] for _ in range(6)]
                    pointers = []
                    for i in range(batch, min(batch + 1000, size)):
                        body = (
                            f"Source record {i}. "
                            + ("zephyr archive " if i % 100 == 0 else "orchard ledger ")
                            + (
                                "The expedition collected regional histories and documented careful observations of local communities, "
                                "weather patterns, transport routes, scientific instruments and historic buildings. "
                                "Researchers catalogued maps, letters, photographs and field notes for future study. "
                                "This passage describes the collection and its scholarly context without giving evaluation labels."
                            )
                        )
                        all_lengths.append(len(body))
                        digest = sha256(body.encode()).digest()
                        did, vid = uuid7(), uuid7()
                        rows[0].append(dict(sha256=digest, text=body))
                        rows[1].append({**templates[0], "id": did, "current_version_id": None})
                        rows[2].append(
                            {
                                **templates[1],
                                "id": vid,
                                "document_id": did,
                                "content_sha256": digest,
                                "source_id": f"bench:{i}",
                            }
                        )
                        rows[3].append({**templates[2], "document_version_id": vid})
                        rows[4].append(
                            dict(
                                document_version_id=vid,
                                chunking_configuration_id=templates[2]["chunking_configuration_id"],
                                ordinal=0,
                                start=0,
                                end=len(body),
                            )
                        )
                        rows[5].append({**templates[3], "content_sha256": digest, "end": len(body), "embedding": DENSE})
                        pointers.append(dict(doc_key=did, version_key=vid))
                    for model, values in zip(
                        (
                            ContentRow,
                            DocumentRow,
                            DocumentVersionRow,
                            VersionChunkPlanRow,
                            ChunkMembershipRow,
                            ChunkEmbeddingRow,
                        ),
                        rows,
                    ):
                        connection.execute(insert(model.__table__), values)
                    connection.execute(
                        update(DocumentRow.__table__)
                        .where(DocumentRow.id == bindparam("doc_key"))
                        .values(current_version_id=bindparam("version_key")),
                        pointers,
                    )
                for model in (
                    ContentRow,
                    DocumentRow,
                    DocumentVersionRow,
                    VersionChunkPlanRow,
                    ChunkMembershipRow,
                    ChunkEmbeddingRow,
                    ChunkingConfigurationRow,
                    EmbeddingConfigurationRow,
                ):
                    connection.execute(text(f"ANALYZE {model.__tablename__}"))
            print(f"Seeded {size} records in {perf_counter() - started:.2f}s", flush=True)
            with engine.connect() as connection:
                average_lexical_bytes = connection.scalar(
                    text("SELECT avg(pg_column_size(to_tsvector('pg_catalog.english', text)))::float8 FROM contents")
                )
            previous = size
            for kind in ("keyword", "semantic", "hybrid"):
                expected = None
                for variant, repository in repositories.items():
                    samples = []
                    captured = []

                    async def search():
                        if kind == "keyword":
                            return await repository.search_keywords(
                                "zephyr archive", config, profile, owner_id=None, limit=20
                            )
                        if kind == "semantic":
                            return await repository.search(tuple(DENSE), config, profile, owner_id=None, limit=20)
                        sem, kw = await repository.search_channels(
                            tuple(DENSE),
                            "zephyr archive",
                            config,
                            profile,
                            owner_id=None,
                            semantic_limit=20,
                            keyword_limit=20,
                        )
                        with stage_timing("hybrid_fusion"):
                            return reciprocal_rank_fusion(sem, kw, limit=10)

                    first_started = perf_counter()
                    result = await search()
                    first_call_ms = (perf_counter() - first_started) * 1000
                    # First application call, NOT a flushed OS/PostgreSQL cache.
                    # Independent database runs generate different UUIDs. Keep the
                    # source occurrence and all evidence/scoring fields in the
                    # comparison while excluding those generated document IDs.
                    signature = [
                        (
                            r.identity,
                            r.content_sha256,
                            r.text,
                            r.source,
                            r.source_spans,
                            r.score,
                            r.score_kind,
                            r.semantic_score,
                            r.keyword_score,
                            r.semantic_rank,
                            r.keyword_rank,
                        )
                        for r in result
                    ]
                    if expected is None:
                        expected = signature
                    else:
                        assert signature == expected, "Optimization changed ranked evidence or provenance"
                    cpu_before = container.get_wrapped_container().stats(stream=False, one_shot=True)["cpu_stats"][
                        "cpu_usage"
                    ]["total_usage"]

                    def capture(connection, cursor, statement, parameters, context, executemany):
                        captured.append((statement, parameters))

                    event.listen(async_engine.sync_engine, "before_cursor_execute", capture)
                    stage_logger = logging.getLogger("investigation_engine.application.timing")
                    stage_samples = StageMeasurements()
                    previous_level = stage_logger.level
                    previously_disabled = stage_logger.disabled
                    # Alembic fileConfig disables pre-existing application loggers.
                    # Enable this collector explicitly, then restore caller state.
                    stage_logger.disabled = False
                    stage_logger.addHandler(stage_samples)
                    stage_logger.setLevel(logging.DEBUG)
                    try:
                        for _ in range(args.samples):
                            begin = perf_counter()
                            result = await search()
                            samples.append((perf_counter() - begin) * 1000)
                            assert 0 < len(result) <= 20
                    finally:
                        stage_logger.removeHandler(stage_samples)
                        stage_logger.setLevel(previous_level)
                        stage_logger.disabled = previously_disabled
                        event.remove(async_engine.sync_engine, "before_cursor_execute", capture)
                    cpu_after = container.get_wrapped_container().stats(stream=False, one_shot=True)["cpu_stats"][
                        "cpu_usage"
                    ]["total_usage"]
                    assert len(captured) == args.samples
                    sql, params = captured[-1]
                    with engine.connect() as connection:
                        plan = connection.exec_driver_sql(
                            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, params
                        ).scalar_one()[0]
                        storage = (
                            connection.execute(
                                text("""
                            SELECT a.attstorage,
                                   pg_relation_size('chunk_embeddings') AS main_bytes,
                                   pg_relation_size(c.reltoastrelid) AS toast_bytes,
                                   pg_indexes_size('chunk_embeddings') AS index_bytes,
                                   pg_total_relation_size('chunk_embeddings') AS total_table_bytes,
                                   pg_database_size(current_database()) AS database_bytes,
                                   avg(pg_column_size(embedding))::bigint AS avg_vector_datum_bytes
                            FROM pg_attribute a
                            JOIN pg_class c ON c.oid = a.attrelid
                            CROSS JOIN chunk_embeddings
                            WHERE a.attrelid = 'chunk_embeddings'::regclass
                              AND a.attname = 'embedding'
                            GROUP BY a.attstorage, c.reltoastrelid
                        """)
                            )
                            .mappings()
                            .one()
                        )
                        db_bytes = storage["database_bytes"]
                    entry = dict(
                        records=size,
                        kind=kind,
                        variant=variant,
                        first_call_ms=first_call_ms,
                        stage_timings=stage_samples.summary(),
                        median_ms=median(samples),
                        p95_ms=sorted(samples)[ceil(0.95 * len(samples)) - 1],
                        samples_ms=samples,
                        execution_ms=plan["Execution Time"],
                        planning_ms=plan["Planning Time"],
                        database_bytes=db_bytes,
                        storage_policy=storage["attstorage"],
                        main_table_bytes=storage["main_bytes"],
                        toast_bytes=storage["toast_bytes"],
                        index_bytes=storage["index_bytes"],
                        total_table_bytes=storage["total_table_bytes"],
                        avg_vector_datum_bytes=storage["avg_vector_datum_bytes"],
                        result_signature_sha256=sha256(
                            json.dumps(portable_signature(result), sort_keys=True, default=str).encode()
                        ).hexdigest(),
                        result_count=len(result),
                        median_passage_chars=median(all_lengths),
                        chunk_memberships=size,
                        distinct_embeddings=size,
                        cpu_seconds_batch=(cpu_after - cpu_before) / 1e9,
                        average_lexical_bytes=average_lexical_bytes,
                        shared_hit_blocks=plan["Plan"].get("Shared Hit Blocks", 0),
                        shared_read_blocks=plan["Plan"].get("Shared Read Blocks", 0),
                        temp_read_blocks=plan["Plan"].get("Temp Read Blocks", 0),
                        temp_written_blocks=plan["Plan"].get("Temp Written Blocks", 0),
                    )
                    (output / f"plan-{size}-{kind}-{variant}.json").write_text(
                        json.dumps(plan, indent=2), encoding="utf8"
                    )
                    measurements.append(entry)
                    (output / "measurements.json").write_text(json.dumps(measurements, indent=2), encoding="utf8")
                    print(json.dumps(entry), flush=True)
    finally:
        await async_engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline", type=Path, help="Frozen pre-change repository module; compared on the SAME database"
    )
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1000, 10000, 50000])
    parser.add_argument("--output", default=".cache/retrieval-optimization")
    parser.add_argument(
        "--embedding-storage",
        choices=("external", "plain"),
        default="external",
        help="Disposable-only embedding storage policy, set before seeding",
    )
    args = parser.parse_args()
    if not 3 <= args.samples <= 100:
        parser.error("Use 3-100 samples; small-sample P95 is descriptive only")
    if args.sizes != sorted(set(args.sizes)) or any(not 1000 <= size <= 50000 for size in args.sizes):
        parser.error("Sizes must be unique increasing values from 1000 to 50000")
    client = docker.from_env()
    client.images.get(IMAGE)
    client.images.get("testcontainers/ryuk:0.8.1")
    client.close()
    container = PostgresContainer(
        IMAGE, username="benchmark", password="disposable-only", dbname="benchmark", driver="psycopg"
    )
    container.ports["5432"] = ("127.0.0.1", None)
    container.start()
    engine = None
    try:
        port = int(container.get_exposed_port(5432))
        assert port not in (5432, 15432)
        url = URL.create(
            "postgresql+psycopg",
            username=container.username,
            password=container.password,
            host="127.0.0.1",
            port=port,
            database=container.dbname,
        )
        engine = create_engine(url, hide_parameters=True)
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION vector"))
            cfg = Config("alembic.ini")
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")
            connection.execute(
                text(
                    "ALTER TABLE chunk_embeddings ALTER COLUMN embedding SET STORAGE "
                    + ("PLAIN" if args.embedding_storage == "plain" else "EXTERNAL")
                )
            )
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(benchmark(engine, url, args, container))
    finally:
        if engine is not None:
            engine.dispose()
        container.stop()


if __name__ == "__main__":
    main()
