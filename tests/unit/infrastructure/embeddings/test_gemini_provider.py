import asyncio
import json
import traceback
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from google import genai
from google.genai import errors, types

from investigation_engine.application.ports.embedding_errors import (
    EmbeddingInputError, EmbeddingProviderError, EmbeddingRateLimitError,
    EmbeddingResponseError,
)
from investigation_engine.application.ports.embedding_provider import EmbeddingProvider
from investigation_engine.infrastructure.embeddings import gemini_provider as module


def response(first=2.0):
    return types.EmbedContentResponse(
        embeddings=[types.ContentEmbedding(values=[first] + [0.0] * 767)]
    )


@pytest.fixture
def sdk(monkeypatch):
    models = SimpleNamespace(
        count_tokens=AsyncMock(return_value=types.CountTokensResponse(total_tokens=5)),
        embed_content=AsyncMock(return_value=response()),
    )
    client = SimpleNamespace(
        aio=SimpleNamespace(models=models, aclose=AsyncMock()), close=Mock(),
    )
    factory = Mock(return_value=client)
    monkeypatch.setattr(module.genai, "Client", factory)
    return SimpleNamespace(client=client, models=models, factory=factory)


async def test_exact_document_inputs_keep_order_and_request_one_vector_each(sdk):
    sdk.models.embed_content.return_value = types.EmbedContentResponse(embeddings=[
        types.ContentEmbedding(values=[2.0] + [0.0] * 767),
        types.ContentEmbedding(values=[3.0] + [0.0] * 767),
    ])
    texts = [" A😀\r\n", "second | text: literal"]
    async with module.GeminiEmbeddingProvider(api_key="dummy-key") as provider:
        port: EmbeddingProvider = provider
        profile = port.configuration
        vectors = await port.embed_documents(texts)
    assert isinstance(vectors, tuple) and all(isinstance(v, tuple) for v in vectors)
    assert [v[0] for v in vectors] == [2.0, 3.0]  # No client normalization.
    for text, count in zip(texts, sdk.models.count_tokens.await_args_list, strict=True):
        assert count.kwargs["model"] == "gemini-embedding-2"
        content = count.kwargs["contents"]
        assert len(content.parts) == 1
        assert content.parts[0].text == "title: none | text: " + text
    embed = sdk.models.embed_content.await_args
    assert embed.kwargs["model"] == "gemini-embedding-2"
    assert profile.model_id == embed.kwargs["model"]
    assert [item.parts[0].text for item in embed.kwargs["contents"]] == [
        "title: none | text: " + text for text in texts]
    config = embed.kwargs["config"]
    assert config.output_dimensionality == 768
    assert profile.dimensions == config.output_dimensionality
    assert profile.document_format == "gemini_no_title_v1" and profile.normalization == "none"
    assert config.task_type is None and config.title is None
    options = sdk.factory.call_args.kwargs
    assert options["enterprise"] is False
    assert options["http_options"].retry_options.attempts == 1
    assert options["http_options"].timeout == 60_000
    sdk.client.aio.aclose.assert_awaited_once()
    sdk.client.close.assert_called_once()


async def test_query_uses_its_own_exact_versioned_envelope(sdk):
    async with module.GeminiEmbeddingProvider(api_key="dummy-key") as provider:
        result = await provider.embed_query("  Where is 😀?\n")
    assert result == (2.0,) + (0.0,) * 767
    content = sdk.models.count_tokens.await_args.kwargs["contents"]
    assert content.parts[0].text == "task: search result | query:   Where is 😀?\n"
    assert sdk.models.embed_content.await_args.kwargs["contents"] is content


async def test_batch_size_and_aggregate_token_limit_bound_synchronous_requests(sdk):
    sdk.models.count_tokens.return_value = types.CountTokensResponse(total_tokens=2)
    sdk.models.embed_content.side_effect = [
        types.EmbedContentResponse(embeddings=[
            types.ContentEmbedding(values=[1.0] + [0.0] * 767),
            types.ContentEmbedding(values=[2.0] + [0.0] * 767),
        ]), response(3),
    ]
    async with module.GeminiEmbeddingProvider(api_key="dummy-key", batch_size=2,
                                              batch_token_limit=5) as provider:
        vectors = await provider.embed_documents(["first", "second", "third"])
    assert [vector[0] for vector in vectors] == [1, 2, 3]
    # The batch size is two and the aggregate token limit is five.
    assert sdk.models.count_tokens.await_count == 3
    assert sdk.models.embed_content.await_count == 2
    assert provider.request_counts == {"count_tokens": 3, "embed_content": 2}


@pytest.mark.parametrize("texts", [[], "single string", None, ["ok", ""], [" \t"], [1], ["\ud800"]])
async def test_invalid_document_inputs_fail_before_any_request(sdk, texts):
    async with module.GeminiEmbeddingProvider(api_key="dummy-key") as provider:
        with pytest.raises(EmbeddingInputError):
            await provider.embed_documents(texts)
    sdk.models.count_tokens.assert_not_awaited()
    sdk.models.embed_content.assert_not_awaited()


@pytest.mark.parametrize("query", [None, "", " \n"])
async def test_invalid_query_is_rejected_before_formatting(sdk, query):
    async with module.GeminiEmbeddingProvider(api_key="dummy-key") as provider:
        with pytest.raises(EmbeddingInputError):
            await provider.embed_query(query)
    sdk.models.count_tokens.assert_not_awaited()


@pytest.mark.parametrize("total", [None, 0, -1, True, 2.5])
async def test_missing_or_invalid_model_token_counts_cannot_enable_embedding(sdk, total):
    sdk.models.count_tokens.return_value = SimpleNamespace(total_tokens=total)
    async with module.GeminiEmbeddingProvider(api_key="dummy-key") as provider:
        with pytest.raises(EmbeddingResponseError, match="token count"):
            await provider.embed_documents(["body"])
    sdk.models.embed_content.assert_not_awaited()


async def test_complete_formatted_input_is_counted_at_the_model_limit(sdk):
    sdk.models.count_tokens.side_effect = [
        types.CountTokensResponse(total_tokens=8192),
        types.CountTokensResponse(total_tokens=8193),
    ]
    async with module.GeminiEmbeddingProvider(api_key="dummy-key") as provider:
        await provider.embed_documents(["allowed"])
        with pytest.raises(EmbeddingInputError, match="8192-token"):
            await provider.embed_query("too large after formatting")
    assert sdk.models.embed_content.await_count == 1
    assert sdk.models.count_tokens.await_args.kwargs["contents"].parts[0].text == (
        "task: search result | query: too large after formatting"
    )


@pytest.mark.parametrize("values", [
    None, [1.0] * 767, [True] + [0.0] * 767, ["1"] + [0.0] * 767,
    [float("nan")] + [0.0] * 767, [float("inf")] + [0.0] * 767,
    [0.0] * 768, [10 ** 1000] + [0.0] * 767, [1e308] * 768,
])
async def test_invalid_vectors_are_rejected_without_normalization(sdk, values):
    # Bypass SDK coercion to also protect the adapter's response boundary.
    sdk.models.embed_content.return_value = SimpleNamespace(
        embeddings=[SimpleNamespace(values=values)]
    )
    async with module.GeminiEmbeddingProvider(api_key="dummy-key") as provider:
        with pytest.raises(EmbeddingResponseError):
            await provider.embed_query("query")


@pytest.mark.parametrize("embeddings", [None, [], [object(), object()], [object()]])
async def test_missing_or_malformed_embedding_responses_are_rejected(sdk, embeddings):
    sdk.models.embed_content.return_value = SimpleNamespace(embeddings=embeddings)
    async with module.GeminiEmbeddingProvider(api_key="dummy-key") as provider:
        with pytest.raises(EmbeddingResponseError):
            await provider.embed_documents(["body"])


async def test_provider_reported_truncation_is_not_accepted(sdk):
    result = response()
    result.embeddings[0].statistics = types.ContentEmbeddingStatistics(truncated=True)
    sdk.models.embed_content.return_value = result
    async with module.GeminiEmbeddingProvider(api_key="dummy-key") as provider:
        with pytest.raises(EmbeddingResponseError, match="truncated"):
            await provider.embed_query("query")


@pytest.mark.parametrize("code,error_type", [
    (400, EmbeddingInputError), (413, EmbeddingInputError),
    (401, EmbeddingProviderError), (403, EmbeddingProviderError),
    (404, EmbeddingProviderError), (429, EmbeddingRateLimitError),
    (501, EmbeddingProviderError), (503, EmbeddingProviderError),
])
async def test_api_failures_are_mapped_and_raw_sdk_payloads_are_not_retained(sdk, code, error_type):
    private_text = "sensitive-body"
    sdk.models.count_tokens.side_effect = errors.APIError(
        code, {"error": {"message": "sensitive-body dummy-key"}},
    )
    async with module.GeminiEmbeddingProvider(api_key="dummy-key") as provider:
        with pytest.raises(error_type) as caught:
            await provider.embed_documents([private_text])
    diagnostic = "".join(traceback.format_exception(caught.value))
    assert str(code) in str(caught.value.__cause__)
    if isinstance(caught.value, EmbeddingProviderError):
        assert caught.value.retryable is (code in (408, 425, 500, 502, 503, 504))
    assert "sensitive-body" not in diagnostic and "dummy-key" not in diagnostic
    assert caught.value.__context__ is None
    sdk.models.embed_content.assert_not_awaited()
    from investigation_engine.application.ports.provider_diagnostics import safe_diagnostic
    safe = safe_diagnostic(caught.value)
    assert safe["http_status"] == code and safe["operation"] == "count_tokens"
    assert safe["model_id"] == "gemini-embedding-2" and safe["request_count"] == 1
    assert safe["rate_or_quota_limit"] is (code == 429)
    assert "sensitive-body" not in str(safe) and "dummy-key" not in str(safe)


@pytest.mark.parametrize("error,error_type", [
    (httpx.ReadTimeout("sensitive-body dummy-key"), EmbeddingProviderError),
    (ValueError("sensitive-body dummy-key"), EmbeddingResponseError),
])
async def test_embedding_transport_or_decoding_failure_stops_later_inputs(sdk, error, error_type):
    sdk.models.embed_content.side_effect = [response(), error]
    async with module.GeminiEmbeddingProvider(api_key="dummy-key", batch_size=1) as provider:
        with pytest.raises(error_type) as caught:
            await provider.embed_documents(["first", "second", "third"])
    assert sdk.models.embed_content.await_count == 2
    diagnostic = "".join(traceback.format_exception(caught.value))
    assert type(error).__name__ in diagnostic
    if isinstance(caught.value, EmbeddingProviderError):
        assert caught.value.retryable
    assert "sensitive-body" not in diagnostic and "dummy-key" not in diagnostic


@pytest.mark.parametrize("stage", ["count_tokens", "embed_content"])
async def test_cancellation_propagates_and_context_closes_both_clients(sdk, stage):
    entered = asyncio.Event()

    async def pending_request(**kwargs):
        entered.set()
        await asyncio.Future()

    getattr(sdk.models, stage).side_effect = pending_request

    async def run():
        async with module.GeminiEmbeddingProvider(api_key="dummy-key") as provider:
            await provider.embed_documents(["first", "second"])

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
    sdk.client.aio.aclose.assert_awaited_once()
    sdk.client.close.assert_called_once()
    assert sdk.models.count_tokens.await_count == (1 if stage == "count_tokens" else 2)


async def test_close_is_idempotent_and_closed_provider_cannot_be_reused(sdk):
    provider = module.GeminiEmbeddingProvider(api_key="dummy-key")
    await provider.aclose()
    await provider.aclose()
    with pytest.raises(EmbeddingProviderError, match="closed"):
        await provider.embed_query("query")
    sdk.client.aio.aclose.assert_awaited_once()
    sdk.client.close.assert_called_once()


@pytest.mark.parametrize("key", [None, "", "  "])
def test_missing_credentials_do_not_construct_a_client(sdk, key):
    with pytest.raises(EmbeddingProviderError, match="GEMINI_API_KEY"):
        module.GeminiEmbeddingProvider(api_key=key)
    sdk.factory.assert_not_called()


async def test_real_sdk_serializes_single_entry_requests_over_mock_transport(monkeypatch):
    """Use the installed SDK and HTTP serializer, with no socket or live service."""
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append((request.url, body))
        if request.url.path.endswith(":countTokens"):
            return httpx.Response(200, json={"totalTokens": 5})
        if request.url.path.endswith(":batchEmbedContents"):
            n = len(body.get("requests", []))
        else:
            assert request.url.path.endswith(":embedContent")
            n = 1
        return httpx.Response(200, json={"embeddings": [{"values": [2.0 + i] + [0.0] * 767} for i in range(n)]})

    transport = httpx.MockTransport(handle)
    real_client = genai.Client
    sync_http = httpx.Client(transport=transport, trust_env=False)
    async_http = httpx.AsyncClient(transport=transport, trust_env=False)

    def create_client(**kwargs):
        kwargs["http_options"].httpx_client = sync_http
        kwargs["http_options"].httpx_async_client = async_http
        return real_client(**kwargs)

    monkeypatch.setattr(module.genai, "Client", create_client)
    try:
        async with module.GeminiEmbeddingProvider(api_key="dummy-key") as provider:
            vectors = await provider.embed_documents(["one😀", "two"])
            await provider.embed_query("where?")
        assert len(vectors) == 2 and vectors[0][0] == 2.0
        assert len(requests) == 5
        expected = ["title: none | text: one😀", "title: none | text: two",
                    "task: search result | query: where?"]
        for index, text in enumerate(expected[:2]):
            count_url, count_body = requests[index]
            assert count_url.host == "generativelanguage.googleapis.com"
            assert count_url.path == "/v1beta/models/gemini-embedding-2:countTokens"
            assert count_body["contents"][0]["parts"] == [{"text": text}]
        embed_url, embed_body = requests[2]
        assert embed_url.path.endswith(":batchEmbedContents")
        assert [item["content"]["parts"][0]["text"] for item in embed_body["requests"]] == expected[:2]
        for item in embed_body["requests"]:
            assert item["model"] == "models/gemini-embedding-2"
            assert item["outputDimensionality"] == 768
            assert "taskType" not in item and "title" not in item
        assert requests[3][0].path.endswith(":countTokens")
        assert requests[4][0].path.endswith(":batchEmbedContents")
    finally:
        # SDK intentionally leaves caller-supplied HTTP clients to their owner.
        await async_http.aclose()
        sync_http.close()
