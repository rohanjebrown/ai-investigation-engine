"""After DB recreation, verify the corpus remains searchable with a real query vector."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from dotenv import dotenv_values  # noqa: E402
from index_evaluation_corpus import PROFILE, MeasuredGemini, database_counts, database_url, verify_registered_corpus  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from investigation_engine.application.use_cases.search_evidence import SearchEvidence  # noqa: E402
from investigation_engine.evaluation.cached_queries import CachedQueryEmbeddings  # noqa: E402
from investigation_engine.evaluation.retrieval import fingerprint  # noqa: E402
from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import PostgreSQLEvidenceRepository  # noqa: E402
from investigation_engine.ingestion.corpus_processing import configurations, tokenizer_from_file  # noqa: E402


async def run(args):
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    question = suite["cases"][0]["question"]
    tokenizer = tokenizer_from_file(args.tokenizer)
    chunking = configurations(tokenizer)[0]
    url = database_url()
    verified_occurrences = verify_registered_corpus(url, corpus)
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as c:
            chunk_id = c.scalar(
                text(
                    "SELECT id FROM chunking_configurations WHERE strategy='whole_passage' AND max_tokens=32000 AND overlap=0 AND tokenizer_name=:name AND tokenizer_revision=:revision AND tokenizer_artifact_sha256=decode(:digest,'hex') AND tokenizer_runtime_version=:runtime"
                ),
                {
                    "name": chunking.tokenizer.name,
                    "revision": chunking.tokenizer.revision,
                    "digest": chunking.tokenizer.artifact_sha256,
                    "runtime": chunking.tokenizer.runtime_version,
                },
            )
            embedding_id = c.scalar(
                text(
                    "SELECT id FROM embedding_configurations WHERE provider=:provider AND model_id=:model AND dimensions=768 AND document_format=:format AND normalization='none' AND model_revision IS NULL"
                ),
                {"provider": PROFILE["provider"], "model": PROFILE["model_id"], "format": PROFILE["document_format"]},
            )
    finally:
        engine.dispose()
    state = database_counts(url, chunk_id, embedding_id)
    if state["chunk_embeddings"] != 197 or state["active_searchable_occurrences"] != 218:
        raise RuntimeError("Persistent evaluation corpus failed completeness check after container recreation")
    key = os.environ.get("GEMINI_API_KEY") or dotenv_values(ROOT / ".env").get("GEMINI_API_KEY")
    provider = MeasuredGemini(api_key=key, batch_size=25, batch_token_limit=32768)
    start = perf_counter()
    try:
        vector = await provider.embed_query(question)
        cache = CachedQueryEmbeddings(provider.configuration, {question: list(vector)})
        async_engine = create_async_engine(url, hide_parameters=True)
        try:
            sessions = async_sessionmaker(async_engine, expire_on_commit=False)
            repository = PostgreSQLEvidenceRepository(sessions)
            results = {}
            for mode in ("semantic", "hybrid"):
                service = SearchEvidence(
                    repository,
                    cache,
                    chunking,
                    provider.configuration,
                    mode=mode,
                    hybrid_repository=repository,
                    semantic_candidates=20,
                    keyword_candidates=20,
                    rrf_k=60,
                )
                hits = await service.execute(question, 10, owner_id=None)
                if not hits or any(not hit.text for hit in hits):
                    raise RuntimeError(f"No valid {mode} evidence returned after database restart")
                results[mode] = [
                    {
                        "source_id": hit.source.source_id,
                        "record_locator": hit.source.record_locator,
                        "content_sha256": hit.content_sha256,
                        "start": hit.start,
                        "end": hit.end,
                        "score": hit.score,
                        "score_kind": getattr(hit.score_kind, "value", hit.score_kind),
                    }
                    for hit in hits
                ]
        finally:
            await async_engine.dispose()
    finally:
        await provider.aclose()
    report = {
        "kind": "persistent_corpus_restart_search_verification",
        "database": state,
        "verified_manifest_occurrences": verified_occurrences,
        "corpus_fingerprint": fingerprint(corpus),
        "question_id": suite["cases"][0]["id"],
        "question": question,
        "query_embedding_requests": provider.request_counts,
        "query_embedding_counted_tokens": provider.counted_tokens,
        "search_latency_seconds": round(perf_counter() - start, 3),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=ROOT / "data/samples/evaluation/corpus.json")
    parser.add_argument("--suite", type=Path, default=ROOT / "data/samples/evaluation/suite.json")
    parser.add_argument("--tokenizer", type=Path, default=ROOT / "artifacts/tokenizers/voyage-4/tokenizer.json")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "reports/evaluation/task15-2026-10-28/restart-search.json"
    )
    args = parser.parse_args()
    result = asyncio.run(run(args), loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None)
    print(
        json.dumps(
            {
                "database": result["database"],
                "query_embedding_requests": result["query_embedding_requests"],
                "latency_seconds": result["search_latency_seconds"],
                "result_counts": {mode: len(rows) for mode, rows in result["results"].items()},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
