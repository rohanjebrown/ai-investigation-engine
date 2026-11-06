import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from investigation_engine.application.ports.evidence_reranker import RerankingError
from investigation_engine.application.retrieval import Evidence
from investigation_engine.infrastructure.reranking.voyage import ENDPOINT, VoyageReranker


@pytest.fixture
def candidates(registration):
    hits = []
    for i in range(3):
        document, version = registration(body=f"Passage {i} 😀", source_id=f"private-source-{i}")
        hits.append(
            Evidence(
                document_id=document.id,
                document_version_id=version.id,
                content_sha256=version.content.sha256,
                start=0,
                end=len(version.content.text),
                text=version.content.text,
                source=version.source,
                source_spans=(),
                score=0.2 + i / 10,
                rank=i + 1,
                title=f"Title {i}",
            )
        )
    return tuple(hits)


def body(indexes=(2, 0, 1)):
    return {
        "object": "list",
        "model": "rerank-3",
        "usage": {"total_tokens": 42},
        "data": [{"index": i, "relevance_score": score} for i, score in zip(indexes, (0.9, 0.5, 0.2))],
    }


async def test_request_is_text_only_and_result_reuses_objects_without_score_changes(candidates):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = VoyageReranker(api_key="synthetic-secret", client=client)
        result = await provider.rerank("Original query 😀", candidates)
    payload = json.loads(requests[0].content)
    assert str(requests[0].url) == ENDPOINT and requests[0].method == "POST"
    assert payload == {
        "query": "Original query 😀",
        "model": "rerank-3",
        "truncation": False,
        "documents": [f"{hit.title}\n\n{hit.text}" for hit in candidates],
    }
    assert requests[0].headers["Authorization"] == "Bearer synthetic-secret"
    for value in ("private-source", "document_id", "metadata", "vector", "synthetic-secret"):
        assert value not in requests[0].content.decode()
    assert [row.relevance_score for row in result] == [0.9, 0.5, 0.2]
    assert all(row.evidence is candidates[i] for row, i in zip(result, (2, 0, 1)))
    assert [row.evidence.rank for row in result] == [3, 1, 2]


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        [],
        {},
        {"data": []},
        {**body(), "object": "other"},
        {**body(), "model": "wrong"},
        {**body(), "data": []},
        {**body(), "data": {}},
        {**body(), "data": body()["data"][:2]},
        {**body(), "data": body()["data"] + [body()["data"][0]]},
        {**body(), "data": [None] * 3},
        body((0, 0, 1)),
        body((0, 1, 3)),
        body((0, 1, -1)),
        body((0, 1, True)),
        body((0, 1, "2")),
        body((0, 1, 2.0)),
        {**body(), "usage": None},
        {**body(), "usage": {}},
        {**body(), "usage": {"total_tokens": True}},
        {**body(), "usage": {"total_tokens": -1}},
    ],
)
async def test_invalid_response_shapes_and_indexes_are_rejected(candidates, invalid):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=invalid))
    ) as client:
        with pytest.raises(RerankingError):
            await VoyageReranker(api_key="synthetic", client=client).rerank("query", candidates)


@pytest.mark.parametrize("score", [None, True, "0.5", -0.1, 1.1, float("nan"), float("inf"), 10**400])
async def test_invalid_scores_fall_back_via_safe_error(candidates, score):
    value = body()
    value["data"][0]["relevance_score"] = score
    # Raw JSON permits exercising malicious NaN/Infinity values, which httpx's encoder rejects.
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=json.dumps(value)))
    ) as client:
        with pytest.raises(RerankingError, match="invalid_score"):
            await VoyageReranker(api_key="synthetic", client=client).rerank("query", candidates)


@pytest.mark.parametrize(
    "failure,reason",
    [
        (httpx.ReadTimeout("sensitive raw error"), "timeout"),
        (httpx.ConnectError("sensitive raw error"), "transport_error"),
        (httpx.Response(429, text="sensitive raw error"), "http_429"),
        (httpx.Response(503, text="sensitive raw error"), "http_503"),
        (httpx.Response(401, text="sensitive raw error"), "http_401"),
        (httpx.Response(302, headers={"Location": "https://other.example"}), "http_302"),
        (httpx.Response(200, text="sensitive raw error"), "invalid_json"),
    ],
)
async def test_failures_are_not_retried_or_logged_with_sensitive_bodies(candidates, failure, reason, caplog):
    requests = []

    def handle(request):
        requests.append(request)
        if isinstance(failure, Exception):
            raise failure
        return failure

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = VoyageReranker(api_key="synthetic", client=client)
        for _ in range(2):
            with pytest.raises(RerankingError, match=reason):
                await provider.rerank("sensitive query", candidates)
    assert len(requests) == 1
    assert "sensitive" not in caplog.text
    assert any(getattr(record, "reason", None) == reason for record in caplog.records)


async def test_total_timeout_is_bounded_and_async_work_can_progress(candidates):
    progressed = asyncio.Event()

    async def handle(request):
        progressed.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = VoyageReranker(api_key="synthetic", client=client, timeout_seconds=0.01)
        with pytest.raises(RerankingError, match="timeout"):
            await provider.rerank("query", candidates)
    assert progressed.is_set()


async def test_budget_reserves_failed_attempts_and_cache_deduplicates_concurrent_requests(candidates):
    requests = []

    async def handle(request):
        requests.append(request)
        await asyncio.sleep(0)
        return httpx.Response(200, json=body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = VoyageReranker(api_key="synthetic", client=client, max_requests=1)
        first, second = await asyncio.gather(provider.rerank("query", candidates), provider.rerank("query", candidates))
        assert first == second
        # Identical text, different source references: map cached indexes onto current objects.
        other = tuple(replace(h, document_id=candidates[0].document_id) for h in candidates)
        cached = await provider.rerank("query", other)
        assert cached[0].evidence is other[2]
        with pytest.raises(RerankingError, match="request_budget_exhausted"):
            await provider.rerank("new query", candidates)
    assert len(requests) == 1


async def test_failure_consumes_budget_for_different_queries(candidates):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
        provider = VoyageReranker(api_key="synthetic", client=client, max_requests=1)
        with pytest.raises(RerankingError, match="http_503"):
            await provider.rerank("query", candidates)
        with pytest.raises(RerankingError, match="request_budget_exhausted"):
            await provider.rerank("different", candidates)


async def test_outer_cancellation_propagates_and_prevents_duplicate_attempts(candidates):
    started = asyncio.Event()
    requests = []

    async def handle(request):
        requests.append(request)
        started.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = VoyageReranker(api_key="synthetic", client=client)
        task = asyncio.create_task(provider.rerank("query", candidates))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(RerankingError, match="cancelled_request"):
            await provider.rerank("query", candidates)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "key,budget,reason", [(None, 1, "missing_credentials"), ("synthetic", 0, "request_budget_exhausted")]
)
async def test_no_requests_without_credentials_or_budget(candidates, key, budget, reason):
    def forbidden(request):
        pytest.fail("No HTTP expected")

    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:
        provider = VoyageReranker(api_key=key, max_requests=budget, client=client)
        assert await provider.rerank("query", ()) == ()
        with pytest.raises(RerankingError, match=reason):
            await provider.rerank("query", candidates)


async def test_equal_scores_keep_input_order_and_missing_titles_send_passage_only(candidates):
    value = body()
    for row in value["data"]:
        row["relevance_score"] = 0.5
    candidates = tuple(replace(hit, title=None) for hit in candidates)

    def handle(request):
        assert json.loads(request.content)["documents"] == [hit.text for hit in candidates]
        return httpx.Response(200, json=value)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await VoyageReranker(api_key="synthetic", client=client).rerank("query", candidates)
    assert tuple(row.evidence for row in result) == candidates
