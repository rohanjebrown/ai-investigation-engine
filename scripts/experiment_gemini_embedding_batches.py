"""Run one bounded real-service experiment at each requested synchronous batch size."""

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from dotenv import dotenv_values

from investigation_engine.application.ports.embedding_errors import (
    EmbeddingInputError,
    EmbeddingProviderError,
    EmbeddingRateLimitError,
    EmbeddingResponseError,
)
from investigation_engine.application.ports.provider_diagnostics import safe_diagnostic
from investigation_engine.infrastructure.embeddings.gemini_provider import GeminiEmbeddingProvider


class MeasuredGemini(GeminiEmbeddingProvider):
    def __init__(self, *, api_key: str, batch_size: int):
        super().__init__(api_key=api_key, batch_size=batch_size)
        self.counted_tokens = 0

    async def _request(self, request, *, operation):
        response = await super()._request(request, operation=operation)
        if operation == "token counting":
            self.counted_tokens += response.total_tokens
        return response


async def run(args) -> dict:
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    texts = tuple(
        dict.fromkeys(
            item["text"] for item in corpus["documents"] if item["source"]["source_id"] == "xanhho/2WikiMultihopQA"
        )
    )
    sizes = (1, 2, 5, 10, 25)
    if len(texts) < max(sizes):
        raise ValueError("The pinned evaluation manifest has too few distinct dataset passages")
    key = os.environ.get("GEMINI_API_KEY") or dotenv_values(args.env_file).get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    provider = MeasuredGemini(api_key=key, batch_size=25)
    report = {
        "kind": "gemini_embedding_2_synchronous_batch_experiment",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "model": "gemini-embedding-2",
        "dimensions": 768,
        "input_source": "distinct exact passages from the pinned evaluation corpus",
        "planned_batch_sizes": list(sizes),
        "completed": [],
        "failed": None,
        "request_counts": provider.request_counts,
        "total_counted_input_tokens": 0,
        "provider_quota_accounting": "Gemini response request/token counters; RPD input accounting is not exposed by the API response",
    }
    try:
        for size in sizes:
            before = provider.request_counts.copy()
            tokens_before = provider.counted_tokens
            started = perf_counter()
            try:
                vectors = await provider.embed_documents(texts[:size])
                elapsed = perf_counter() - started
                report["completed"].append(
                    {
                        "inputs_submitted": size,
                        "embeddings_returned": len(vectors),
                        "dimensions": sorted({len(vector) for vector in vectors}),
                        "elapsed_seconds": round(elapsed, 3),
                        "count_tokens_requests": provider.request_counts["count_tokens"] - before["count_tokens"],
                        "embedding_requests": provider.request_counts["embed_content"] - before["embed_content"],
                        "input_tokens": provider.counted_tokens - tokens_before,
                    }
                )
            except (
                EmbeddingInputError,
                EmbeddingProviderError,
                EmbeddingRateLimitError,
                EmbeddingResponseError,
            ) as error:
                report["failed"] = safe_diagnostic(error) or {"error_type": type(error).__name__}
                report["stopped_after_failed_size"] = size
                break
    finally:
        report["request_counts"] = provider.request_counts
        report["total_counted_input_tokens"] = provider.counted_tokens
        await provider.aclose()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=Path("data/samples/evaluation/corpus.json"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--output", type=Path, default=Path("reports/gemini-embedding-batch-experiment-2026-10-28.json")
    )
    args = parser.parse_args()
    report = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
