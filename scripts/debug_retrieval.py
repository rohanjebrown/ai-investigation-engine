"""Read-only semantic, lexical and RRF diagnostic against the configured DB."""

import argparse
import asyncio
import json
import sys
import traceback
from pathlib import Path
from time import perf_counter

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from investigation_engine.application.rrf import reciprocal_rank_fusion  # noqa: E402
from investigation_engine.application.use_cases.search_evidence import SearchEvidence  # noqa: E402
from investigation_engine.evaluation.cached_queries import CachedQueryEmbeddings  # noqa: E402
from investigation_engine.infrastructure.config import settings  # noqa: E402
from investigation_engine.infrastructure.embeddings.gemini_provider import GeminiEmbeddingProvider  # noqa: E402
from investigation_engine.infrastructure.persistence.models import ChunkingConfigurationRow  # noqa: E402
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import _configuration  # noqa: E402
from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import PostgreSQLEvidenceRepository  # noqa: E402
from investigation_engine.ingestion.corpus_processing import tokenizer_from_file  # noqa: E402


def candidate(hit):
    return {
        "rank": hit.rank,
        "title": hit.title,
        "content_sha256": hit.content_sha256,
        "range": {"start": hit.start, "end": hit.end},
        "score": hit.score,
        "text_preview": hit.text[:180].replace("\n", " "),
    }


def diagnose_channels(semantic, lexical, fused, limit, k):
    semantic_ranks = {hit.identity: index for index, hit in enumerate(semantic, 1)}
    lexical_ranks = {hit.identity: index for index, hit in enumerate(lexical, 1)}
    fused_rows = []
    for index, hit in enumerate(fused, 1):
        sr, lr = semantic_ranks.get(hit.identity), lexical_ranks.get(hit.identity)
        contributions = {
            name: (1 / (k + rank) if rank is not None else 0.0) for name, rank in (("semantic", sr), ("lexical", lr))
        }
        fused_rows.append(
            {
                "rank": index,
                "title": hit.title,
                "semantic_rank": sr,
                "lexical_rank": lr,
                "rrf_contributions": contributions,
                "rrf_score": hit.score,
                "content_sha256": hit.content_sha256,
                "range": {"start": hit.start, "end": hit.end},
            }
        )
    sids, lids = set(semantic_ranks), set(lexical_ranks)
    return {
        "semantic_only_count": len(sids - lids),
        "lexical_only_count": len(lids - sids),
        "both_channels_count": len(sids & lids),
        "final_result_count": len(fused),
        "rrf": fused_rows,
    }


async def diagnose(question, limit, engine, embedding_provider, chunking, embedding_config, rrf_k):
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    repository = PostgreSQLEvidenceRepository(sessions, lexical_backend=settings.lexical_search_backend)
    query_start = perf_counter()
    vector = await embedding_provider.embed_query(question)
    query_embedding_ms = (perf_counter() - query_start) * 1000
    cache = CachedQueryEmbeddings(embedding_config, {question: list(vector)})
    start = perf_counter()
    semantic = await repository.search(vector, chunking, embedding_config, owner_id=None, limit=limit)
    semantic_ms = (perf_counter() - start) * 1000
    start = perf_counter()
    lexical = await repository.search_keywords(question, chunking, embedding_config, owner_id=None, limit=limit)
    lexical_ms = (perf_counter() - start) * 1000
    fused = reciprocal_rank_fusion(semantic, lexical, limit=limit, k=rrf_k)
    hybrid_service = SearchEvidence(
        repository,
        cache,
        chunking,
        embedding_config,
        mode="hybrid",
        hybrid_repository=repository,
        semantic_candidates=limit,
        keyword_candidates=limit,
        rrf_k=rrf_k,
    )
    start = perf_counter()
    actual_hybrid = await hybrid_service.execute(question, limit, owner_id=None)
    hybrid_ms = (perf_counter() - start) * 1000
    same = len(fused) == len(actual_hybrid) and all(
        left.identity == right.identity and left.rank == right.rank and abs(left.score - right.score) < 1e-12
        for left, right in zip(fused, actual_hybrid)
    )
    result = {
        "question": question,
        "query_embedding_duration_ms": round(query_embedding_ms, 2),
        "semantic": {
            "candidate_count": len(semantic),
            "duration_ms": round(semantic_ms, 2),
            "candidates": [candidate(hit) for hit in semantic],
        },
        "lexical": {
            "backend": settings.lexical_search_backend,
            "bm25_score_order": "ascending" if settings.lexical_search_backend == "bm25" else None,
            "candidate_count": len(lexical),
            "duration_ms": round(lexical_ms, 2),
            "zero_candidates": not lexical,
            "candidates": [candidate(hit) for hit in lexical],
        },
        "fusion": diagnose_channels(semantic, lexical, fused, limit, rrf_k),
        "correctness": {
            "matches_actual_hybrid": same,
            "actual_hybrid_duration_ms": round(hybrid_ms, 2),
            "identity_order_score_match": same,
        },
    }
    if not same:
        raise RuntimeError("Manual channel fusion does not match production hybrid retrieval")
    return result


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", required=True, action="append")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100")
    if not settings.gemini_api_key or settings.serving_chunking_configuration_id is None:
        raise RuntimeError("GEMINI_API_KEY and SERVING_CHUNKING_CONFIGURATION_ID are required")
    if settings.chunking_tokenizer_path is None:
        raise RuntimeError("CHUNKING_TOKENIZER_PATH is required")
    tokenizer = tokenizer_from_file(settings.chunking_tokenizer_path)
    engine = create_async_engine(
        settings.database_url(),
        pool_size=1,
        max_overflow=0,
        connect_args={
            "options": "-c default_transaction_read_only=on -c statement_timeout=15000",
        },
    )
    provider = GeminiEmbeddingProvider(api_key=settings.gemini_api_key, batch_size=1, batch_token_limit=8192)
    try:
        async with engine.connect() as connection:
            if await connection.scalar(text("SHOW transaction_read_only")) != "on":
                raise RuntimeError("Database connection is not read-only")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            row = await session.get(ChunkingConfigurationRow, settings.serving_chunking_configuration_id)
            if row is None:
                raise RuntimeError("Configured chunking profile was not found")
            chunking = _configuration(row)
            if chunking.tokenizer != tokenizer.identity:
                raise RuntimeError("Configured tokenizer does not match the selected serving profile")
        outputs = []
        async with provider:
            for question in args.question:
                outputs.append(
                    await diagnose(
                        question, args.limit, engine, provider, chunking, provider.configuration, settings.rrf_k
                    )
                )
        rendered = json.dumps(outputs, ensure_ascii=False, indent=2)
        print(rendered)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf8")
    finally:
        await provider.aclose()
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(
            main(),
            loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None,
        )
    except Exception as error:
        print(traceback.format_exc())
        print(f"Retrieval diagnostic stopped: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
