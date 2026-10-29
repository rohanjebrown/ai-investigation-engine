"""Real 2Wiki processing on a disposable local PostgreSQL container only.

No application settings, cloud connections, provider clients or vector seeding.
The worker mode also provides a reusable local bulk-processing CLI.
"""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import (
    PostgreSQLChunkPlanRepository,
)
from investigation_engine.infrastructure.persistence.postgresql_document_repository import PostgreSQLDocumentRepository
from investigation_engine.ingestion.corpus_processing import (
    DATASET_ID,
    REVISION,
    atomic_json,
    configurations,
    fingerprint,
    load_checkpoint,
    process_corpus,
    save_checkpoint,
    source_documents,
    tokenizer_from_file,
)

IMAGE = "pgvector/pgvector@sha256:9d2e61c7352b9e9f4798df5fd9a498f043f4cda1cdacc707de3d198650f4321e"


def local_url(port):
    if port in (5432, 15432) or not 1 <= port <= 65535:
        raise ValueError("Refusing an application or managed-database port")
    return URL.create(
        "postgresql+psycopg",
        host="127.0.0.1",
        port=port,
        username="corpus_validation",
        password=os.environ["CORPUS_DB_PASSWORD"],
        database="corpus_validation",
        query={"connect_timeout": "3"},
    )


def database_identity(engine):
    with engine.connect() as connection:
        return fingerprint(
            {
                "cluster": str(connection.scalar(text("SELECT system_identifier FROM pg_control_system()"))),
                "database_oid": connection.scalar(text("SELECT oid FROM pg_database WHERE datname=current_database()")),
                "alembic_revision": connection.scalar(text("SELECT version_num FROM alembic_version")),
            }
        )


def peak_memory_bytes():
    if os.name != "nt":
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t)
            for name in (
                "PeakWorkingSetSize",
                "WorkingSetSize",
                "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage",
                "PagefileUsage",
                "PeakPagefileUsage",
            )
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    if not ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        return None
    return counters.PeakWorkingSetSize


def counts(engine):
    with engine.connect() as connection:
        result = {
            name: connection.scalar(text(f"SELECT count(*) FROM {name}"))
            for name in (
                "documents",
                "document_versions",
                "contents",
                "version_chunk_plans",
                "chunk_memberships",
                "chunk_embeddings",
            )
        }
        result.update(
            active_versions=connection.scalar(
                text("SELECT count(*) FROM documents WHERE current_version_id IS NOT NULL")
            ),
            searchable_source_occurrences=connection.scalar(
                text(
                    "SELECT count(*) FROM documents d JOIN document_versions v ON d.current_version_id=v.id WHERE d.deleted_at IS NULL AND EXISTS (SELECT 1 FROM chunk_embeddings e WHERE e.content_sha256=v.content_sha256)"
                )
            ),
            database_bytes=connection.scalar(text("SELECT pg_database_size(current_database())")),
            stable_ids=connection.scalar(
                text(
                    "SELECT md5(string_agg(id::text || ':' || document_id::text, ',' ORDER BY id)) FROM document_versions"
                )
            ),
        )
        return result


def integrity(engine, dataset, limit):
    checked = 0
    expected = []
    with engine.connect() as connection:

        def check_batch(batch):
            nonlocal checked
            ids = sorted({item.source.record_locator["example_id"] for item in batch})
            rows = connection.execute(
                text("""
                SELECT v.record_locator, v.kind, v.source_id, v.revision, v.display_name,
                       v.metadata, v.structure, v.source_spans, v.version_number,
                       encode(c.sha256,'hex') AS content_sha256, c.text, d.title, d.owner_id,
                       d.current_version_id, d.deleted_at
                FROM document_versions v JOIN contents c ON c.sha256=v.content_sha256
                JOIN documents d ON d.id=v.document_id
                WHERE v.record_locator->>'example_id'=ANY(:ids)
            """),
                {"ids": ids},
            ).mappings()
            found = {(r["record_locator"]["example_id"], r["record_locator"]["context_position"]): r for r in rows}
            from hashlib import sha256

            for item in batch:
                locator = item.source.record_locator
                row = found[(locator["example_id"], locator["context_position"])]
                assert row["text"] == item.text
                assert row["content_sha256"] == sha256(item.text.encode("utf-8")).hexdigest()
                assert row["kind"] == item.source.kind and row["source_id"] == DATASET_ID
                assert row["revision"] == REVISION and row["record_locator"] == locator
                assert row["metadata"] == item.source.metadata and row["display_name"] == item.title
                assert row["title"] == item.title and row["owner_id"] is None and row["deleted_at"] is None
                assert row["version_number"] == 1 and row["current_version_id"] is None
                assert row["structure"] == [asdict(s) for s in item.structure]
                assert row["source_spans"] == [
                    {**asdict(s), "locators": [asdict(loc) for loc in s.locators]} for s in item.source_spans
                ]
                checked += 1

        for item in source_documents(dataset):
            if checked + len(expected) >= limit:
                break
            expected.append(item)
            if len(expected) == 512:
                check_batch(expected)
                expected = []
        if expected:
            check_batch(expected)
        bad_plans = connection.scalar(
            text("""
            SELECT count(*) FROM version_chunk_plans p JOIN document_versions v ON v.id=p.document_version_id
            JOIN contents c ON c.sha256=v.content_sha256
            WHERE p.expected_chunk_count<>1 OR NOT EXISTS (
                SELECT 1 FROM chunk_memberships m WHERE m.document_version_id=p.document_version_id
                AND m.chunking_configuration_id=p.chunking_configuration_id
                AND m.ordinal=0 AND m.start=0 AND m."end"=char_length(c.text))
        """)
        )
        assert bad_plans == 0
        invalid_hashes = connection.scalar(
            text("SELECT count(*) FROM contents WHERE sha256<>sha256(convert_to(text,'UTF8'))")
        )
        assert invalid_hashes == 0
    return {
        "original_source_occurrences_compared": checked,
        "invalid_plans": bad_plans,
        "invalid_hashes": invalid_hashes,
        "exact_text_spans_titles_provenance_verified": True,
    }


async def worker(args):
    url = local_url(args.port)
    sync = create_engine(url, hide_parameters=True)
    identity = database_identity(sync)
    sync.dispose()
    engine = create_async_engine(url, pool_size=args.concurrency, max_overflow=0, hide_parameters=True)
    tokenizer = tokenizer_from_file(args.tokenizer)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    repository = PostgreSQLDocumentRepository(sessions)
    if args.crash_after:
        delegate = repository

        class CrashRepository:
            def __init__(self):
                self.completed = 0

            async def register(self, document, version):
                result = await delegate.register(document, version)
                self.completed += 1
                if self.completed == args.crash_after:
                    os._exit(75)  # Deliberate test-only process death after an actual commit.
                return result

        repository = CrashRepository()
    last_print = -1

    def progress(cursor):
        nonlocal last_print
        if cursor // 5000 > last_print:
            print(json.dumps({"range_start": args.start_at, "completed_in_range": cursor - args.start_at}), flush=True)
            last_print = cursor // 5000

    try:
        report = await process_corpus(
            args.dataset,
            repository,
            PostgreSQLChunkPlanRepository(sessions),
            tokenizer,
            configurations(tokenizer)[0],
            checkpoint_path=args.checkpoint,
            target_identity=identity,
            batch_size=args.batch_size,
            concurrency=args.concurrency,
            stop_after=args.limit,
            progress=progress,
            start_at=args.start_at,
        )
        report["peak_process_memory_bytes"] = peak_memory_bytes()
        atomic_json(args.result, report)
        return 0 if report["failure"] is None else 2
    finally:
        await engine.dispose()


def run_worker(args, port, *, limit, label, crash_after=0, checkpoint=None):
    command_args = [
        sys.executable,
        __file__,
        "--worker",
        "--port",
        str(port),
        "--dataset",
        str(args.dataset),
        "--tokenizer",
        str(args.tokenizer),
        "--checkpoint",
        str(checkpoint or args.output / "checkpoint.json"),
        "--result",
        str(args.output / (label + ".json")),
        "--limit",
        str(limit),
        "--concurrency",
        str(args.concurrency),
        "--batch-size",
        str(args.batch_size),
    ]
    if crash_after:
        command_args += ["--crash-after", str(crash_after)]
    result = subprocess.run(command_args, check=False)
    if crash_after:
        assert result.returncode == 75
        return {"deliberate_process_exit": 75}
    if result.returncode not in (0, 2):
        raise RuntimeError(f"Corpus worker stopped with exit code {result.returncode}")
    return json.loads((args.output / (label + ".json")).read_text(encoding="utf-8"))


def run_partitioned(args, port, limit):
    """Bounded foreground children, each owning one disjoint source range/file."""
    checkpoint_path = args.output / "checkpoint.json"
    checkpoint = load_checkpoint(checkpoint_path)
    begin = checkpoint["completed_occurrences"]
    started = perf_counter()
    children = []
    for index in range(args.processes):
        start = begin + (limit - begin) * index // args.processes
        end = begin + (limit - begin) * (index + 1) // args.processes
        label = f"range-{limit}-{index}"
        result_path = args.output / (label + ".json")
        command_args = [
            sys.executable,
            __file__,
            "--worker",
            "--port",
            str(port),
            "--dataset",
            str(args.dataset),
            "--tokenizer",
            str(args.tokenizer),
            "--checkpoint",
            str(args.output / (label + "-checkpoint.json")),
            "--result",
            str(result_path),
            "--start-at",
            str(start),
            "--limit",
            str(end),
            "--concurrency",
            str(args.concurrency),
            "--batch-size",
            str(args.batch_size),
        ]
        children.append((subprocess.Popen(command_args), result_path, start, end))
    try:
        for child, _, _, _ in children:
            if child.wait() not in (0, 2):
                raise RuntimeError("A foreground corpus range worker failed; its last checkpoint remains available")
    finally:
        for child, _, _, _ in children:
            if child.poll() is None:
                child.terminate()
                child.wait()
    parts = [json.loads(path.read_text(encoding="utf-8")) for _, path, _, _ in children]
    cursor = begin
    for part, (_, _, start, end) in zip(parts, children, strict=True):
        if cursor == start:
            cursor = part["completed_occurrences"]
    failure = next((part["failure"] for part in parts if part["failure"]), None)
    if failure is None:
        assert cursor == limit
        checkpoint["completed_occurrences"] = limit
        save_checkpoint(checkpoint_path, checkpoint)
    return {
        "range_start": begin,
        "resumed_from": begin,
        "completed_occurrences": cursor,
        "successful_operations_this_run": sum(p["successful_operations_this_run"] for p in parts),
        "attempted_operations_this_run": sum(p["attempted_operations_this_run"] for p in parts),
        "duration_seconds": perf_counter() - started,
        **{
            key: sum(p[key] for p in parts)
            for key in ("registration_worker_seconds", "chunking_worker_seconds", "plan_persistence_worker_seconds")
        },
        "peak_process_memory_bytes": max(p["peak_process_memory_bytes"] or 0 for p in parts),
        "sum_worker_peak_bytes_upper_bound": sum(p["peak_process_memory_bytes"] or 0 for p in parts),
        "foreground_processes": len(parts),
        "connections_per_process": args.concurrency,
        "failure": failure,
        "embeddings_generated": 0,
        "activation_performed": False,
        "partitions": parts,
    }


def main(args):
    args.output.mkdir(parents=True, exist_ok=True)
    if args.worker:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
            return runner.run(worker(args))
    # Refuse to silently reuse a checkpoint from a previously removed database.
    if load_checkpoint(args.output / "checkpoint.json") is not None or (args.output / "validation.json").exists():
        raise ValueError("Use a fresh output directory for a new disposable database")
    subprocess.run(["docker", "image", "inspect", IMAGE], check=True, stdout=subprocess.DEVNULL)
    os.environ["CORPUS_DB_PASSWORD"] = "disposable-corpus-validation-password"
    started = perf_counter()
    report = {
        "source": "real pinned 2Wiki development context passages",
        "image": IMAGE,
        "generation_api_requests": 0,
        "embedding_api_requests": 0,
        "phases": [],
    }
    try:
        local_container = PostgresContainer(
            IMAGE,
            username="corpus_validation",
            password=os.environ["CORPUS_DB_PASSWORD"],
            dbname="corpus_validation",
            driver="psycopg",
        )
        local_container.ports["5432"] = ("127.0.0.1", None)
        with local_container as container:
            port = int(container.get_exposed_port(5432))
            url = local_url(port)
            engine = create_engine(url, hide_parameters=True)
            try:
                with engine.begin() as connection:
                    connection.execute(text("CREATE EXTENSION vector"))
                    report["postgres_version"] = connection.scalar(text("SHOW server_version"))
                    report["pgvector_version"] = connection.scalar(
                        text("SELECT extversion FROM pg_extension WHERE extname='vector'")
                    )
                config = Config("alembic.ini")
                with engine.begin() as connection:
                    config.attributes["connection"] = connection
                    command.upgrade(config, "head")
                for limit in (100, 1000):
                    phase = run_worker(args, port, limit=min(limit, args.limit), label=f"stage-{limit}")
                    phase["counts"] = counts(engine)
                    report["phases"].append(phase)
                    if phase["failure"]:
                        return 2
                    if args.limit <= limit:
                        break
                if args.limit > 1000:
                    checkpoint_before = load_checkpoint(args.output / "checkpoint.json")
                    report["interrupted_worker"] = run_worker(
                        args, port, limit=args.limit, label="interrupted", crash_after=7
                    )
                    assert checkpoint_before == load_checkpoint(args.output / "checkpoint.json")
                    report["interruption_checkpoint_preserved"] = True
                    report["after_interruption_counts"] = counts(engine)
                    for limit in sorted({min(10000, args.limit), args.limit}):
                        phase = (
                            run_partitioned(args, port, limit)
                            if args.processes > 1
                            else run_worker(args, port, limit=limit, label=f"stage-{limit}")
                        )
                        atomic_json(args.output / f"stage-{limit}.json", phase)
                        phase["counts"] = counts(engine)
                        report["phases"].append(phase)
                        atomic_json(args.output / "validation.json", report)
                        if phase["failure"]:
                            break
                final = counts(engine)
                completed = report["phases"][-1]["completed_occurrences"]
                if report["phases"][-1]["failure"] is None:
                    assert all(
                        final[name] == completed
                        for name in ("documents", "document_versions", "version_chunk_plans", "chunk_memberships")
                    )
                    assert (
                        final["chunk_embeddings"]
                        == final["active_versions"]
                        == final["searchable_source_occurrences"]
                        == 0
                    )
                    if completed == 125760:
                        assert final["contents"] == 56680
                    report["registration_and_plan_cardinalities_verified"] = True
                report["fully_indexed_versions"] = 0
                report["integrity"] = integrity(engine, args.dataset, completed)
                report["before_restart"] = final
                engine.dispose()
                container.get_wrapped_container().restart()
                port = int(container.get_exposed_port(5432))
                engine = create_engine(local_url(port), hide_parameters=True)
                for attempt in range(60):
                    try:
                        after = counts(engine)
                        break
                    except Exception:
                        import time

                        time.sleep(0.5)
                else:
                    raise RuntimeError("Disposable database did not return after restart")
                assert {k: v for k, v in final.items() if k != "database_bytes"} == {
                    k: v for k, v in after.items() if k != "database_bytes"
                }
                report["restart_preserved_counts_and_ids"] = True
                # A completed checkpoint is a no-op; a fresh checkpoint forces
                # actual repository replay for the first 1,000 source occurrences.
                report["completed_run_repeat"] = run_worker(args, port, limit=completed, label="repeat")
                report["prefix_repository_replay"] = run_worker(
                    args,
                    port,
                    limit=min(1000, completed),
                    label="prefix-replay",
                    checkpoint=args.output / "replay-checkpoint.json",
                )
                replayed = counts(engine)
                assert {k: v for k, v in after.items() if k != "database_bytes"} == {
                    k: v for k, v in replayed.items() if k != "database_bytes"
                }
                report["rerun_preserved_counts_and_ids"] = True
                report["final_counts"] = replayed
            finally:
                engine.dispose()
    finally:
        report["total_duration_seconds"] = perf_counter() - started
        report["host_free_disk_bytes_after"] = shutil.disk_usage(args.output).free
        report["peak_controller_memory_bytes"] = peak_memory_bytes()
        atomic_json(args.output / "validation.json", report)
    return 2 if report["phases"][-1]["failure"] is not None else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--port", type=int)
    parser.add_argument("--dataset", type=Path, default=Path("data/raw/2wiki/dev.parquet"))
    parser.add_argument("--tokenizer", type=Path, default=Path("artifacts/tokenizers/voyage-4/tokenizer.json"))
    parser.add_argument("--output", type=Path, default=Path(".cache/corpus-scale/processing"))
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--limit", type=int, default=125760)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--crash-after", type=int, default=0)
    parser.add_argument("--start-at", type=int, default=0)
    parser.add_argument("--processes", type=int, choices=(1, 2, 3, 4), default=1)
    parsed = parser.parse_args()
    if not 1 <= parsed.concurrency <= 32 or parsed.processes * parsed.concurrency > 32:
        parser.error("Use at most 32 total foreground database connections")
    raise SystemExit(main(parsed))
