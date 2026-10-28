"""Run paired semantic/hybrid evaluation over the persistent local corpus."""

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from index_evaluation_corpus import (  # noqa: E402
    MeasuredGemini, database_counts, database_url, verify_registered_corpus,
)
from investigation_engine.application.use_cases.search_evidence import SearchEvidence  # noqa: E402
from investigation_engine.evaluation.cached_queries import CachedQueryEmbeddings  # noqa: E402
from investigation_engine.evaluation.retrieval import (  # noqa: E402
    RetrievalRun, evaluate, export, fingerprint,
)
from investigation_engine.ingestion.corpus_processing import configurations, tokenizer_from_file  # noqa: E402
from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import PostgreSQLEvidenceRepository  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402


async def run(args) -> dict:
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    url = database_url()
    verified_occurrences = verify_registered_corpus(url, corpus)
    tokenizer = tokenizer_from_file(args.tokenizer)
    chunking = configurations(tokenizer)[0]
    # Resolve the persisted profiles and require a complete directly measured corpus.
    from sqlalchemy import create_engine, text
    from index_evaluation_corpus import PROFILE
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as c:
            chunking_id = c.scalar(text("SELECT id FROM chunking_configurations WHERE strategy='whole_passage' AND tokenizer_name=:name AND tokenizer_revision=:rev AND tokenizer_artifact_sha256=decode(:digest,'hex') AND tokenizer_runtime_version=:runtime AND max_tokens=32000 AND overlap=0 AND algorithm_version='1'"), {
                "name": chunking.tokenizer.name, "rev": chunking.tokenizer.revision,
                "digest": chunking.tokenizer.artifact_sha256, "runtime": chunking.tokenizer.runtime_version})
            embedding_id = c.scalar(text("SELECT id FROM embedding_configurations WHERE provider=:provider AND model_id=:model AND dimensions=768 AND document_format=:format AND normalization='none' AND model_revision IS NULL"), {
                "provider": PROFILE["provider"], "model": PROFILE["model_id"], "format": PROFILE["document_format"]})
    finally:
        engine.dispose()
    if chunking_id is None or embedding_id is None:
        raise RuntimeError("Persisted evaluation profiles are missing")
    state = database_counts(url, chunking_id, embedding_id)
    required = {"documents": 218, "document_versions": 218, "contents": 197,
                "chunk_memberships": 218, "chunk_embeddings": 197, "active_versions": 218,
                "active_searchable_occurrences": 218, "incomplete_planned_versions": 0}
    if any(state.get(key) != value for key, value in required.items()):
        raise RuntimeError("Persistent evaluation database is not complete and searchable")

    key = __import__("os").environ.get("GEMINI_API_KEY") or __import__("dotenv").dotenv_values(ROOT / ".env").get("GEMINI_API_KEY")
    provider = MeasuredGemini(api_key=key, batch_size=25, batch_token_limit=32768)
    try:
        query_vectors = {}
        for case in suite["cases"]:
            question = case["question"]
            if question not in query_vectors:
                query_vectors[question] = list(await provider.embed_query(question))
        query_counts = provider.request_counts.copy()
        query_tokens = provider.counted_tokens
        cache = CachedQueryEmbeddings(provider.configuration, query_vectors)
        async_engine = create_async_engine(url, hide_parameters=True)
        try:
            sessions = async_sessionmaker(async_engine, expire_on_commit=False)
            repository = PostgreSQLEvidenceRepository(sessions)
            chunking_record = asdict(chunking)
            embedding_record = asdict(provider.configuration)
            runs = []
            for mode in ("semantic", "hybrid"):
                service = SearchEvidence(repository, cache, chunking, provider.configuration,
                    mode=mode, hybrid_repository=repository, semantic_candidates=20,
                    keyword_candidates=20, rrf_k=60)
                runs.append((RetrievalRun(name=mode, mode=mode,
                    chunking=chunking_record, embedding=embedding_record,
                    semantic_candidates=20, keyword_candidates=20, rrf_k=60), service))
            report = await evaluate(suite, corpus, runs, ks=(5, 10, 20), owner_id=None,
                                    indexed_corpus_fingerprint=fingerprint(corpus))
        finally:
            await async_engine.dispose()
    finally:
        await provider.aclose()
    report["database_attestation"] = state
    report["verified_manifest_occurrences"] = verified_occurrences
    report["query_embedding_requests"] = query_counts
    report["query_embedding_counted_tokens"] = query_tokens
    report["generation_requests"] = 0
    export(report, args.output)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=ROOT / "data/samples/evaluation/corpus.json")
    parser.add_argument("--suite", type=Path, default=ROOT / "data/samples/evaluation/suite.json")
    parser.add_argument("--tokenizer", type=Path, default=ROOT / "artifacts/tokenizers/voyage-4/tokenizer.json")
    parser.add_argument("--output", type=Path, default=ROOT / "reports/evaluation/task15-2026-10-28")
    args = parser.parse_args()
    result = asyncio.run(run(args), loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None)
    print(json.dumps({"output": str(args.output), "database": result["database_attestation"],
                      "runs": [{"mode": run["configuration"]["mode"], "aggregate": run["aggregate"],
                                "by_category": run["by_category"]} for run in result["runs"]]}, indent=2))


if __name__ == "__main__":
    main()
