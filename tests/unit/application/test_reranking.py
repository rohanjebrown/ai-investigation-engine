"""Shared workflow tests: real fusion and Voyage adapter, synthetic HTTP only."""

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from investigation_engine.application import debug_trace
from investigation_engine.application.answers import AnswerStatus, GenerationResult
from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy
from investigation_engine.application.embeddings import EmbeddingConfig
from investigation_engine.application.investigations import (
    InvestigateQuestion,
    InvestigationDecision,
    InvestigationStatus,
    SearchEvidenceCall,
)
from investigation_engine.application.ports.evidence_reranker import RerankedEvidence, RerankingError
from investigation_engine.application.retrieval import Evidence
from investigation_engine.application.rrf import reciprocal_rank_fusion
from investigation_engine.application.use_cases.answer_question import AnswerQuestion
from investigation_engine.application.use_cases.search_evidence import SearchEvidence
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.infrastructure.reranking.voyage import VoyageReranker

QUESTION = "Who is the maternal grandfather of Princess Alexandra of Hanover (born 1999)?"
CAROLINE = (
    "Caroline, Princess of Hanover (Caroline Louise Marguerite Grimaldi; born 23 January 1957), "
    "is the eldest child of Rainier III, Prince of Monaco, and the American actress Grace Kelly."
)


@pytest.fixture
def pipeline(tokenizer, registration):
    def hit(number, text=None, title=None):
        text = text or f"Synthetic candidate {number}."
        span = SourceSpan(start=0, end=len(text), locators=(SourceLocator(kind=LocatorKind.SENTENCE, index=0),))
        document, version = registration(body=text, source_id=f"source-{number}", source_spans=(span,))
        return Evidence(
            document_id=document.id,
            document_version_id=version.id,
            content_sha256=version.content.sha256,
            start=0,
            end=len(text),
            text=text,
            title=title,
            source=version.source,
            source_spans=version.source_spans,
            score=0.9 - number / 100,
            rank=number,
        )

    semantic = tuple(hit(n) if n != 4 else hit(n, CAROLINE, "Caroline, Princess of Hanover") for n in range(1, 13))
    keyword = tuple(replace(item, score_kind="keyword_rank", score=2.0) for item in semantic if item.rank != 4)[:8]
    expected = reciprocal_rank_fusion(semantic, keyword, limit=None)
    assert expected[8].text == CAROLINE and expected[8].semantic_rank == 4
    repository = SimpleNamespace(search_channels=AsyncMock(return_value=(semantic, keyword)), search=AsyncMock())
    embedding = EmbeddingConfig(provider="synthetic", model_id="test")
    provider = SimpleNamespace(configuration=embedding, embed_query=AsyncMock(return_value=(1.0,) + (0.0,) * 767))
    chunking = ChunkingConfig(strategy=ChunkingStrategy.WHOLE_PASSAGE, tokenizer=tokenizer.identity, max_tokens=300)
    requests = []

    def response(request):
        payload = json.loads(request.content)
        requests.append(payload)
        # This is a controlled mock ranking, not a claim about the real Voyage model.
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "rerank-3",
                "usage": {"total_tokens": 123},
                "data": [
                    {"index": i, "relevance_score": 0.99 if CAROLINE in text else 0.1}
                    for i, text in enumerate(payload["documents"])
                ],
            },
        )

    def service(reranker=None, mode="hybrid"):
        return SearchEvidence(
            repository, provider, chunking, embedding, mode=mode, hybrid_repository=repository, reranker=reranker
        )

    return SimpleNamespace(
        service=service,
        repository=repository,
        requests=requests,
        response=response,
        semantic=semantic,
        keyword=keyword,
        expected=expected,
        hit=hit,
    )


async def test_princess_alexandra_rank_nine_survives_until_reranking_before_top_five(pipeline):
    p = pipeline
    async with httpx.AsyncClient(transport=httpx.MockTransport(p.response)) as client:
        reranker = VoyageReranker(api_key="synthetic", client=client)
        result = await p.service(reranker).execute(QUESTION, 5, owner_id="trusted")
    assert len(p.requests[0]["documents"]) == len(p.expected) == 12
    assert p.requests[0]["query"] == QUESTION
    assert result[0] == p.expected[8] and result[0].rank == 9
    assert result[0].score == pytest.approx(1 / 64)
    assert result[0].source == p.semantic[3].source
    assert result[0].identity == p.semantic[3].identity
    assert result[0].source_spans == p.semantic[3].source_spans
    assert len(result) == 5
    assert result[0].score_kind == "rrf"


async def test_disabled_preserves_rrf_and_never_calls_http(pipeline, monkeypatch):
    post = AsyncMock(side_effect=AssertionError("Disabled provider was called"))
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    result = await pipeline.service().execute(QUESTION, 5, owner_id=None)
    assert result == pipeline.expected[:5]
    post.assert_not_called()


async def test_semantic_enabled_exposes_candidate_pool_and_keeps_original_objects(pipeline):
    p = pipeline
    p.repository.search.return_value = p.semantic
    reranker = SimpleNamespace(
        rerank=AsyncMock(return_value=tuple(RerankedEvidence(item, 0.5) for item in reversed(p.semantic)))
    )
    result = await p.service(reranker, mode="semantic").execute("query", 5, owner_id="trusted")
    assert p.repository.search.await_args.kwargs["limit"] == 20
    assert p.repository.search.await_args.kwargs["owner_id"] == "trusted"
    assert result[0] is p.semantic[-1]
    assert result == tuple(reversed(p.semantic))[:5]


async def test_duplicate_channel_occurrences_are_fused_once_but_distinct_sources_remain(pipeline):
    p = pipeline
    original = p.semantic[0]
    distinct = replace(
        original,
        document_id=p.semantic[1].document_id,
        document_version_id=p.semantic[1].document_version_id,
        source=p.semantic[1].source,
    )
    p.repository.search_channels.return_value = (
        (original, original, distinct),
        (replace(original, score_kind="keyword_rank"),),
    )
    reranker = SimpleNamespace(
        rerank=AsyncMock(side_effect=lambda query, hits: tuple(RerankedEvidence(h, 1.0) for h in hits))
    )
    result = await p.service(reranker).execute("query", 5, owner_id=None)
    assert len(result) == 2 and len(reranker.rerank.await_args.args[1]) == 2
    assert {h.identity for h in result} == {original.identity, distinct.identity}


async def test_empty_pool_does_not_invoke_reranker(pipeline):
    pipeline.repository.search_channels.return_value = ((), ())
    reranker = SimpleNamespace(rerank=AsyncMock(side_effect=AssertionError("Empty pool")))
    assert await pipeline.service(reranker).execute("query", 5, owner_id=None) == ()
    reranker.rerank.assert_not_called()


@pytest.mark.parametrize(
    "error", ["http_429", "timeout", "invalid_indexes", "request_budget_exhausted", "missing_credentials"]
)
async def test_unavailable_reranker_falls_back_to_original_rrf(pipeline, error, caplog):
    reranker = SimpleNamespace(rerank=AsyncMock(side_effect=RerankingError(error)))
    result = await pipeline.service(reranker).execute(QUESTION, 5, owner_id=None)
    assert result == pipeline.expected[:5]
    assert any(getattr(record, "reason", None) == error for record in caplog.records)
    assert QUESTION not in caplog.text


async def test_port_cannot_replace_evidence_or_drop_candidates(pipeline):
    reranker = SimpleNamespace(rerank=AsyncMock(return_value=(RerankedEvidence(pipeline.semantic[0], 1),)))
    assert await pipeline.service(reranker).execute(QUESTION, 5, owner_id=None) == pipeline.expected[:5]


@pytest.mark.parametrize("response", [httpx.Response(503), httpx.Response(200, json={"data": []})])
async def test_real_adapter_provider_failures_fall_back_at_shared_search_boundary(pipeline, response):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
        service = pipeline.service(VoyageReranker(api_key="synthetic", client=client))
        assert await service.execute(QUESTION, 5, owner_id=None) == pipeline.expected[:5]


async def test_quick_answer_citations_follow_promoted_passage(pipeline):
    generator = SimpleNamespace(
        generate=AsyncMock(
            return_value=GenerationResult(
                status=AnswerStatus.ANSWERED,
                answer="Rainier III [E1]",
                citations=("E1",),
            )
        )
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(pipeline.response)) as client:
        search = pipeline.service(VoyageReranker(api_key="synthetic", client=client))
        result = await AnswerQuestion(search, generator).execute(QUESTION, owner_id=None)
    assert generator.generate.await_args.args[0].evidence[0].text == CAROLINE
    assert result.citations[0].evidence_id == "E1"
    assert result.citations[0].evidence == pipeline.expected[8]
    assert len(result.retrieved_evidence) == 5


async def test_investigation_reranks_each_active_query_without_reranking_admitted_evidence(pipeline):
    p = pipeline
    second = p.hit(20, "Rainier III was Prince of Monaco.")
    p.repository.search_channels.side_effect = [(p.semantic, p.keyword), ((second,), ())]
    actions = [
        SearchEvidenceCall(query=QUESTION, call_id="1"),
        SearchEvidenceCall(query="Rainier III biography", call_id="2"),
        InvestigationDecision(
            status=InvestigationStatus.ANSWERED, answer="Rainier III [E1] [E6]", citations=("E1", "E6")
        ),
    ]
    turns = []

    async def action(turn):
        turns.append(turn)
        return actions.pop(0)

    async with httpx.AsyncClient(transport=httpx.MockTransport(p.response)) as client:
        result = await InvestigateQuestion(
            p.service(VoyageReranker(api_key="synthetic", client=client)), SimpleNamespace(next_action=action)
        ).execute(QUESTION, owner_id="trusted")
    assert [request["query"] for request in p.requests] == [QUESTION, "Rainier III biography"]
    assert p.requests[1]["documents"] == [second.text]
    assert result.evidence[0].evidence == p.expected[8]
    assert result.evidence[0].evidence_id == "E1"
    assert turns[2].exchanges[0].evidence[0] == turns[1].exchanges[0].evidence[0]
    assert result.citations == ("E1", "E6")


async def test_full_union_over_one_hundred_candidates_is_not_capped_by_rrf(pipeline):
    semantic = tuple(pipeline.hit(n) for n in range(1, 101))
    keyword = tuple(replace(pipeline.hit(n), score_kind="keyword_rank") for n in range(101, 201))
    pipeline.repository.search_channels.return_value = (semantic, keyword)
    reranker = SimpleNamespace(
        rerank=AsyncMock(side_effect=lambda query, hits: tuple(RerankedEvidence(h, 0.5) for h in hits))
    )
    assert len(await pipeline.service(reranker).execute("query", 5, owner_id=None)) == 5
    assert len(reranker.rerank.await_args.args[1]) == 200


async def test_local_trace_has_rank_mapping_usage_final_selection_and_fallback(pipeline, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DEBUG_TRACE_ENABLED", "true")
    monkeypatch.setenv("VOYAGE_API_KEY", "synthetic-secret")
    for name in ("K_SERVICE", "K_REVISION", "CLOUD_RUN_JOB"):
        monkeypatch.delenv(name, raising=False)
    async with httpx.AsyncClient(transport=httpx.MockTransport(pipeline.response)) as client:
        with debug_trace.request_trace("reranking", "question"):
            await pipeline.service(VoyageReranker(api_key="synthetic-secret", client=client)).execute(
                QUESTION, 5, owner_id=None
            )
            await pipeline.service().execute(QUESTION, 5, owner_id=None)
    text = next(tmp_path.glob(".debug/traces/*.jsonl")).read_text()
    records = [json.loads(line) for line in text.splitlines()]
    by_event = {row["event"]: row["data"] for row in records}
    assert by_event["rerank.candidates"]["query"] == QUESTION
    first_candidates = next(row["data"]["candidates"] for row in records if row["event"] == "rerank.candidates")
    assert first_candidates[8]["semantic_rank"] == 4
    promoted = by_event["rerank.ranking"]["ranking"][0]
    assert promoted["original_position"] == 9 and promoted["reranked_position"] == 1
    assert promoted["relevance_score"] == 0.99
    assert by_event["voyage.response"]["total_tokens"] == 123
    assert by_event["voyage.response"]["model"] == "rerank-3"
    assert by_event["voyage.response"]["duration_ms"] >= 0
    assert by_event["rerank.selection"]["fallback"] is True
    assert by_event["rerank.selection"]["reason"] == "disabled"
    assert "synthetic-secret" not in text and "Authorization" not in text
