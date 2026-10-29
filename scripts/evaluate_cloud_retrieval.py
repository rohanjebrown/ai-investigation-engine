"""Read-only, full-corpus retrieval evaluation for the existing 2Wiki dev cases.

Place in scripts/evaluate_cloud_retrieval.py in the AI Investigation Engine repo.
Only SELECTs are performed; default_transaction_read_only is enforced at connection level.
No document ingestion, embedding writes or migrations.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from statistics import mean, median
from time import perf_counter

import pyarrow.parquet as pq
from dotenv import dotenv_values
from sqlalchemy import create_engine, literal, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from index_evaluation_corpus import PROFILE, MeasuredGemini  # noqa: E402

from investigation_engine.application.embeddings import EmbeddingConfig  # noqa: E402
from investigation_engine.application.use_cases.search_evidence import SearchEvidence  # noqa: E402
from investigation_engine.evaluation.cached_queries import CachedQueryEmbeddings  # noqa: E402
from investigation_engine.evaluation.retrieval import covered, fingerprint, missing_gold, validate_suite  # noqa: E402
from investigation_engine.infrastructure.ingestion.twowiki_adapter import DATASET_ID, extract_2wiki  # noqa: E402
from investigation_engine.infrastructure.persistence.models import ChunkingConfigurationRow  # noqa: E402
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import _configuration  # noqa: E402
from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import (  # noqa: E402
    PostgreSQLEvidenceRepository,
    _candidate_statement,
    _eligible_memberships,
    _evidence,
    _hydrate,
)
from investigation_engine.ingestion.corpus_processing import FILE_SHA256, REVISION, verify_input  # noqa: E402


def read_env(path: Path):
    file_values = dotenv_values(path) if path.exists() else {}

    def get(name, default=None):
        return os.environ.get(name) or file_values.get(name) or default

    needed = ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")
    if any(not get(name) for name in needed):
        raise RuntimeError("Missing DB_HOST, DB_USER, DB_PASSWORD or DB_NAME")
    host = get("DB_HOST")
    sslmode = get("DB_SSLMODE", "disable")
    if sslmode == "disable" and host not in ("127.0.0.1", "localhost", "::1") and not host.startswith("/cloudsql/"):
        raise ValueError("A remote database connection must use TLS or a Cloud SQL socket")
    url = URL.create(
        "postgresql+psycopg",
        username=get("DB_USER"),
        password=get("DB_PASSWORD"),
        host=host,
        port=int(get("DB_PORT", "5432")),
        database=get("DB_NAME"),
        query={"sslmode": sslmode, "connect_timeout": "5"},
    )
    return url, get("GEMINI_API_KEY")


READ_ONLY = {
    "options": "-c default_transaction_read_only=on -c statement_timeout=15000",
}


def verify_dataset_gold(dataset: Path, suite: dict, corpus: dict, cases: list[dict]) -> dict:
    """Verify the pinned input and each gold sentence using the real extractor."""
    digest = verify_input(dataset, FILE_SHA256)
    validate_suite(suite, corpus)
    wanted = {}
    for case in cases:
        if missing_gold(case, corpus):
            raise ValueError("An evaluation gold item is absent from the evaluation manifest")
        for gold in case["required_evidence"]:
            source = gold["source"]
            if source["kind"] != "dataset" or source["source_id"] != DATASET_ID or source["revision"] != REVISION:
                raise ValueError("Gold source is not the pinned 2Wiki dataset")
            locator = source["record_locator"]
            if set(locator) != {"split", "example_id", "context_position"} or locator["split"] != "dev":
                raise ValueError("Gold source locator is not a 2Wiki dev occurrence")
            wanted.setdefault(locator["example_id"], []).append(gold)
    checked = 0
    seen = set()
    for batch in pq.ParquetFile(dataset).iter_batches(batch_size=128, columns=["_id", "context"]):
        for row in batch.to_pylist():
            example_id = row["_id"]
            if example_id not in wanted:
                continue
            if example_id in seen:
                raise ValueError("Duplicate gold example ID in dataset")
            seen.add(example_id)
            documents = extract_2wiki(
                {"_id": example_id, "context": json.loads(row["context"])}, split="dev", revision=REVISION
            )
            for gold in wanted[example_id]:
                position = gold["source"]["record_locator"]["context_position"]
                if type(position) is not int or not 0 <= position < len(documents):
                    raise ValueError("Gold context position is invalid")
                document = documents[position]
                spans = document.source_spans
                index = gold["sentence_index"]
                if (
                    sha256(document.text.encode("utf8")).hexdigest() != gold["content_sha256"]
                    or type(index) is not int
                    or not 0 <= index < len(spans)
                    or (spans[index].start, spans[index].end) != (gold["start"], gold["end"])
                ):
                    raise ValueError("Gold content, sentence index or offsets differ from the pinned dataset")
                checked += 1
    if seen != set(wanted):
        raise ValueError("A gold source example is absent from the pinned dataset")
    return {"dataset_sha256": digest, "dataset_revision": REVISION, "gold_sentences_verified": checked}


def postgres_preflight(url, expected_database, chunking_id, cases):
    """Check corpus counts and gold against the actual serving eligibility query."""
    engine = create_engine(url, hide_parameters=True, connect_args=READ_ONLY)
    try:
        with engine.connect() as connection:
            conn = connection.execution_options(isolation_level="REPEATABLE READ")
            snapshot = conn.scalar(text("SELECT pg_current_snapshot()::text"))
            if conn.scalar(text("SELECT current_database()")) != expected_database:
                raise RuntimeError("Connected database is not the expected evaluation database")
            if conn.scalar(text("SHOW transaction_read_only")) != "on":
                raise RuntimeError("Evaluation transaction is not read-only")
            counts = dict(
                conn.execute(
                    text("""
                SELECT count(*) AS active_occurrences, count(DISTINCT dv.content_sha256) AS unique_contents
                FROM document_versions dv JOIN documents d ON d.current_version_id = dv.id
                WHERE d.owner_id IS NULL AND d.deleted_at IS NULL AND dv.document_id = d.id
                  AND dv.kind = 'dataset' AND dv.source_id = :dataset AND dv.revision = :revision
                  AND dv.record_locator->>'split' = 'dev'
            """),
                    {"dataset": DATASET_ID, "revision": REVISION},
                )
                .mappings()
                .one()
            )
            if counts["active_occurrences"] < 125760 or counts["unique_contents"] < 50000:
                raise RuntimeError("Pinned public 2Wiki corpus does not meet the expected scale")
            with Session(bind=conn) as session:
                row = session.get(ChunkingConfigurationRow, chunking_id)
                if row is None:
                    raise RuntimeError("Required serving chunking profile does not exist")
                chunking = _configuration(row)
                profile = EmbeddingConfig(provider=PROFILE["provider"], model_id=PROFILE["model_id"])
                members = _eligible_memberships(chunking, profile, None)
                digests = {
                    bytes.fromhex(gold["content_sha256"]) for case in cases for gold in case["required_evidence"]
                }
                candidates = _candidate_statement(members, literal(1.0), literal(0.0), "cosine_similarity")
                candidates = candidates.where(members.c.content_sha256.in_(digests))
                rows = session.execute(_hydrate(candidates)).mappings().all()
                hits = tuple(_evidence(row, index) for index, row in enumerate(rows, 1))
            unavailable = []
            representative_mismatches = []
            for case in cases:
                for index, gold in enumerate(case["required_evidence"]):
                    canonical = any(
                        hit.content_sha256 == gold["content_sha256"]
                        and hit.start <= gold["start"] < gold["end"] <= hit.end
                        for hit in hits
                    )
                    if not canonical:
                        unavailable.append({"case_id": case["id"], "gold_index": index})
                    elif not any(covered(hit, gold) for hit in hits):
                        representative_mismatches.append({"case_id": case["id"], "gold_index": index})
            return {
                "readonly": True,
                "snapshot_fingerprint": sha256(snapshot.encode("ascii")).hexdigest(),
                "database": expected_database,
                "dataset_counts": counts,
                "chunking_configuration_id": chunking_id,
                "chunking": asdict(chunking),
                "embedding_profile": asdict(profile),
                "gold_sentences": sum(len(case["required_evidence"]) for case in cases),
                "canonical_gold_unavailable": unavailable,
                "strict_representative_mismatches": representative_mismatches,
                "scope": "public, current, nondeleted, complete selected profile; exact canonical representative",
                "corpus_attestation_limit": "Counts and selected gold do not prove every corpus occurrence is correct.",
            }
    finally:
        engine.dispose()


def measure(hits, gold, *, strict):
    if not strict:
        unique = {}
        for item in gold:
            unique.setdefault((item["content_sha256"], item["start"], item["end"]), item)
        gold = list(unique.values())
    unique_hits = []
    seen_hits = set()
    for hit in hits:
        if hit.identity not in seen_hits:
            unique_hits.append(hit)
            seen_hits.add(hit.identity)
    matches = []
    for hit in unique_hits:
        matched = set()
        for idx, item in enumerate(gold):
            if strict:
                found = covered(hit, item)
            else:
                found = (
                    hit.content_sha256 == item["content_sha256"] and hit.start <= item["start"] < item["end"] <= hit.end
                )
            if found:
                matched.add(idx)
        matches.append(matched)
    output = {}
    for k in (5, 10, 20):
        found = set().union(*matches[:k]) if matches[:k] else set()
        output[str(k)] = {
            "recall": len(found) / len(gold) if gold else None,
            "all_required": len(found) == len(gold) if gold else None,
        }
    first_rank = next((i for i, matched in enumerate(matches, 1) if matched), None)
    return {"mrr": (1.0 / first_rank if first_rank else 0.0) if gold else None, "at_k": output}


def pct(values, q):
    if not values:
        return None
    sorted_values = sorted(values)
    if q == 0.5:
        return round(median(values), 2)
    import math

    return round(sorted_values[max(0, math.ceil(len(sorted_values) * q) - 1)], 2)


def summarize(rows, modes=("semantic", "hybrid", "keyword")):
    by_mode = {}
    for mode in modes:
        subset = [x for x in rows if x["mode"] == mode]
        ok = [x for x in subset if x["status"] == "ok"]
        with_gold = [x for x in ok if x["gold_count"]]
        timing = [x["duration_ms"] for x in ok]
        by_mode[mode] = {
            "attempted": len(subset),
            "successful": len(ok),
            "failed": len(subset) - len(ok),
            "p50_latency_ms": pct(timing, 0.5),
            "p95_latency_ms": pct(timing, 0.95),
            "strict": {
                "mrr": mean(x["strict"]["mrr"] for x in with_gold) if with_gold else None,
                "recall_at_5": mean(x["strict"]["at_k"]["5"]["recall"] for x in with_gold) if with_gold else None,
                "recall_at_10": mean(x["strict"]["at_k"]["10"]["recall"] for x in with_gold) if with_gold else None,
                "recall_at_20": mean(x["strict"]["at_k"]["20"]["recall"] for x in with_gold) if with_gold else None,
            },
            "canonical": {
                "mrr": mean(x["canonical"]["mrr"] for x in with_gold) if with_gold else None,
                "recall_at_5": mean(x["canonical"]["at_k"]["5"]["recall"] for x in with_gold) if with_gold else None,
                "recall_at_10": mean(x["canonical"]["at_k"]["10"]["recall"] for x in with_gold) if with_gold else None,
                "recall_at_20": mean(x["canonical"]["at_k"]["20"]["recall"] for x in with_gold) if with_gold else None,
                "all_required_at_20": mean(x["canonical"]["at_k"]["20"]["all_required"] for x in with_gold)
                if with_gold
                else None,
            },
        }
        by_mode[mode]["by_category"] = {
            category: {
                "cases": len(group),
                "canonical_recall_at_20": mean(x["canonical"]["at_k"]["20"]["recall"] for x in group),
            }
            for category in sorted({x["category"] for x in with_gold})
            if (group := [x for x in with_gold if x["category"] == category])
        }
    return by_mode


def summarize_paired(rows, modes=("semantic", "hybrid", "keyword")):
    successful = {mode: {row["id"] for row in rows if row["mode"] == mode and row["status"] == "ok"} for mode in modes}
    common = set.intersection(*(successful[mode] for mode in modes))
    paired = [row for row in rows if row["id"] in common and row["mode"] in modes]
    return {"paired_cases": len(common), "case_ids": sorted(common), "modes": summarize(paired, modes)}


def code_fingerprint():
    """Bind checkpoints to scoring, provider, retrieval and dependency bytes."""
    paths = (
        Path(__file__),
        ROOT / "scripts/index_evaluation_corpus.py",
        ROOT / "src/investigation_engine/application/use_cases/search_evidence.py",
        ROOT / "src/investigation_engine/application/rrf.py",
        ROOT / "src/investigation_engine/evaluation/retrieval.py",
        ROOT / "src/investigation_engine/evaluation/cached_queries.py",
        ROOT / "src/investigation_engine/infrastructure/persistence/postgresql_evidence_repository.py",
        ROOT / "src/investigation_engine/infrastructure/persistence/postgresql_chunk_plan_repository.py",
        ROOT / "src/investigation_engine/infrastructure/persistence/_embedding_mapping.py",
        ROOT / "src/investigation_engine/infrastructure/embeddings/gemini_provider.py",
        ROOT / "src/investigation_engine/infrastructure/ingestion/twowiki_adapter.py",
        ROOT / "uv.lock",
    )
    return fingerprint({str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest() for path in paths})


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf8")
    tmp.replace(path)


async def run(args):
    suite = json.loads(args.suite.read_text(encoding="utf8"))
    corpus = json.loads(args.corpus.read_text(encoding="utf8"))
    cases = [x for x in suite["cases"] if x["split"] == "dev"]
    if not cases or any(x["expected_behavior"] != "answer" for x in cases):
        raise RuntimeError("Expected 2Wiki dev answer cases are missing")
    cases = cases[: args.max_cases]
    dataset_audit = verify_dataset_gold(args.dataset, suite, corpus, cases)
    if args.output.exists() and any(args.output.iterdir()):
        raise RuntimeError("Output directory is not empty; choose a fresh directory")
    url, api_key = read_env(args.db_env_file)
    audit = postgres_preflight(url, args.expected_database, args.chunking_id, cases)
    run_fingerprint = fingerprint(
        {
            "version": 2,
            "suite": fingerprint(suite),
            "corpus": fingerprint(corpus),
            "dataset": dataset_audit["dataset_sha256"],
            "cases": [case["id"] for case in cases],
            "target": fingerprint(
                {
                    "host": url.host,
                    "port": url.port,
                    "database": url.database,
                    "user": url.username,
                    "sslmode": url.query.get("sslmode"),
                }
            ),
            "snapshot": audit["snapshot_fingerprint"],
            "chunking": audit["chunking"],
            "embedding": audit["embedding_profile"],
            "code": code_fingerprint(),
            "modes": ["semantic", "hybrid", "keyword"],
            "limit": 20,
            "rrf_k": 60,
        }
    )
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
            ).strip()
        )
    except OSError, subprocess.CalledProcessError:
        commit, dirty = None, None
    metadata = {
        "local_git_commit": commit,
        "local_worktree_has_changes": dirty,
        "environment": "local Python application running against the real Cloud SQL database; NOT a deployed Cloud Run revision test",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "evaluation_kind": "real_full_corpus_retrieval_2wiki_dev_only",
        "suite_fingerprint": fingerprint(suite),
        "suite_version": suite["version"],
        "case_count": len(cases),
        "strict_identity_metric": "Gold source occurrence, sentence bounds and SHA must all match",
        "canonical_metric": "Gold content SHA and sentence bounds must be included; source occurrence may differ",
        "controlled_cases_excluded": 12,
        "gold_label_warning": "Only annotated supporting facts are treated as gold. Other relevant passages may exist.",
        "db_audit": audit,
        "dataset_audit": dataset_audit,
        "run_fingerprint": run_fingerprint,
        "timing_scope": "Local retrieval with cached query vectors, excluding live embedding; not server-only or Cloud Run latency.",
        "snapshot_limit": "Read-only preflight uses one snapshot; retrieval requests do not share a snapshot.",
    }
    preflight_path = args.output / "preflight.json"
    save_json(preflight_path, metadata)
    print(json.dumps({"preflight": audit, "mode": "read_only"}, ensure_ascii=True, indent=2))
    if args.preflight_only:
        return
    if audit["canonical_gold_unavailable"]:
        raise RuntimeError("Gold evidence preflight failed; see preflight.json before scoring")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing; no generation is performed by this script")
    async_engine = create_async_engine(url, hide_parameters=True, connect_args=READ_ONLY, pool_size=2, max_overflow=0)
    try:
        sessions = async_sessionmaker(async_engine, expire_on_commit=False)
        async with sessions() as session:
            chunking = _configuration(await session.get(ChunkingConfigurationRow, args.chunking_id))
        async with MeasuredGemini(api_key=api_key, batch_size=25, batch_token_limit=32768) as provider:
            if asdict(provider.configuration) != audit["embedding_profile"]:
                raise RuntimeError("Query embedding model does not match verified corpus profile")
            repo = PostgreSQLEvidenceRepository(sessions)
            rows = []
            for case in cases:
                checkpoint = args.output / "cases" / f"{sha256(case['id'].encode('utf8')).hexdigest()}.json"
                question = case["question"]
                try:
                    if sum(provider.request_counts.values()) + 2 > args.max_provider_requests:
                        raise RuntimeError("Provider request budget exhausted")
                    start_embedding = perf_counter()
                    vector = list(await provider.embed_query(question))
                    embedding_ms = round((perf_counter() - start_embedding) * 1000, 2)
                except Exception as error:
                    raise RuntimeError(
                        f"Query embedding failed ({type(error).__name__}); partial results preserved"
                    ) from None
                cache = CachedQueryEmbeddings(provider.configuration, {question: vector})
                case_rows = []
                modes = ("semantic", "hybrid", "keyword")
                offset = cases.index(case) % len(modes)
                for mode in modes[offset:] + modes[:offset]:
                    service = SearchEvidence(
                        repo,
                        cache,
                        chunking,
                        provider.configuration,
                        mode="semantic" if mode == "keyword" else mode,
                        hybrid_repository=repo,
                        semantic_candidates=20,
                        keyword_candidates=20,
                        rrf_k=60,
                    )
                    start = perf_counter()
                    try:
                        search = service.execute_keywords if mode == "keyword" else service.execute
                        hits = await search(question, 20, owner_id=None)
                        latency = (perf_counter() - start) * 1000
                        case_rows.append(
                            {
                                "id": case["id"],
                                "category": case["category"],
                                "mode": mode,
                                "gold_count": len(case["required_evidence"]),
                                "status": "ok",
                                "duration_ms": round(latency, 2),
                                "query_embedding_duration_ms": embedding_ms,
                                "strict": measure(hits, case["required_evidence"], strict=True),
                                "canonical": measure(hits, case["required_evidence"], strict=False),
                                "hits": [
                                    {
                                        "rank": rank,
                                        "title": hit.title,
                                        "sha": hit.content_sha256,
                                        "start": hit.start,
                                        "end": hit.end,
                                        "score": float(hit.score),
                                        "score_kind": str(hit.score_kind),
                                        "source_id": hit.source.source_id,
                                        "record_locator": hit.source.record_locator,
                                        "document_version_id": str(hit.document_version_id),
                                        "source_spans": [asdict(span) for span in hit.source_spans],
                                    }
                                    for rank, hit in enumerate(hits, 1)
                                ],
                            }
                        )
                    except Exception as error:
                        case_rows.append(
                            {
                                "id": case["id"],
                                "category": case["category"],
                                "mode": mode,
                                "status": "failed",
                                "error_type": type(error).__name__,
                            }
                        )
                if all(row["status"] == "ok" for row in case_rows):
                    save_json(checkpoint, {"id": case["id"], "run_fingerprint": run_fingerprint, "runs": case_rows})
                rows.extend(case_rows)
                print(f"Completed {len(rows) // 3}/{len(cases)} cases", flush=True)
                if args.min_query_interval:
                    await asyncio.sleep(args.min_query_interval)
            report = {
                **metadata,
                "modes": ("semantic", "hybrid", "keyword"),
                "summary": summarize(rows),
                "paired_summary": summarize_paired(rows),
                "questions": rows,
                "query_embedding_requests_this_process": provider.request_counts,
            }
            save_json(args.output / "retrieval.json", report)
            print(json.dumps(report["summary"], indent=2, ensure_ascii=True))
    finally:
        await async_engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=ROOT / "data/samples/evaluation/suite.json")
    parser.add_argument("--corpus", type=Path, default=ROOT / "data/samples/evaluation/corpus.json")
    parser.add_argument("--dataset", type=Path, default=ROOT / "data/raw/2wiki/dev.parquet")
    parser.add_argument("--db-env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--expected-database", default="investigation_engine")
    parser.add_argument("--chunking-id", type=int, default=1)
    parser.add_argument("--max-cases", type=int, default=28)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/evaluation/cloud-retrieval-v2")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--max-provider-requests", type=int, default=56)
    parser.add_argument("--min-query-interval", type=float, default=1.3)
    args = parser.parse_args()
    if not 1 <= args.max_cases <= 28:
        parser.error("--max-cases must be between 1 and 28")
    if args.max_provider_requests < 0 or not 0 <= args.min_query_interval <= 3600:
        parser.error("Invalid provider request budget or pacing interval")
    try:
        asyncio.run(run(args), loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None)
    except Exception as error:
        # Avoid printing raw provider and DB exceptions (which may contain credentials).
        print(f"Evaluation stopped: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
