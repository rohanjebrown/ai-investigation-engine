"""One small opt-in Voyage request; no database, embeddings, or Gemini."""

import os
from uuid import UUID

import pytest

from investigation_engine.application.debug_trace import request_trace
from investigation_engine.application.retrieval import Evidence
from investigation_engine.domain.content import Content
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.infrastructure.reranking.voyage import VoyageReranker


async def test_one_controlled_voyage_reranking_request():
    # Collection is gated by --run-external; environment alone never enables this test.
    if os.environ.get("RERANKER_BACKEND") != "voyage" or not os.environ.get("VOYAGE_API_KEY"):
        pytest.skip("Explicitly set RERANKER_BACKEND=voyage and VOYAGE_API_KEY after checking free allowance")
    passages = ("The Danube flows through Budapest.", "Mars is a planet in the Solar System.")
    candidates = tuple(
        Evidence(
            document_id=UUID(int=index),
            document_version_id=UUID(int=index + 10),
            content_sha256=Content(text).sha256,
            start=0,
            end=len(text),
            text=text,
            source=SourceReference(kind=SourceKind.DATASET, source_id="synthetic-smoke"),
            source_spans=(),
            score=0.5,
            rank=index,
        )
        for index, text in enumerate(passages, 1)
    )
    provider = VoyageReranker(api_key=os.environ["VOYAGE_API_KEY"], max_requests=1, timeout_seconds=10)
    try:
        with request_trace("voyage-smoke", "reranking"):
            result = await provider.rerank("Which river flows through Budapest?", candidates)
    finally:
        await provider.aclose()
    assert len(result) == len(candidates)
    assert {id(row.evidence) for row in result} == {id(hit) for hit in candidates}
    assert [row.relevance_score for row in result] == sorted((row.relevance_score for row in result), reverse=True)
