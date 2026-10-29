"""Real GenAI serialization over an offline HTTP transport; no mocked models API."""

import json
import traceback
from dataclasses import fields
from types import SimpleNamespace

import httpx
import pytest
from google import genai
from google.genai import errors, types

from investigation_engine.application.investigations import (
    InvestigateQuestion,
    InvestigationStatus,
    InvestigationTurn,
    SearchEvidenceCall,
    SearchExchange,
)
from investigation_engine.application.ports.generation_errors import GenerationProviderError, GenerationResponseError
from investigation_engine.application.ports.provider_diagnostics import safe_diagnostic
from investigation_engine.application.retrieval import Evidence
from investigation_engine.infrastructure.generation import gemini_investigation_model as module


def tool_content(index, query, *, signed=True):
    call = {"functionCall": {"name": "search_evidence", "args": {"query": query}, "id": f"native-{index}"}}
    if signed:
        # Synthetic offline protocol fixtures, never submitted to Gemini.
        call["thoughtSignature"] = "c2lnbmF0dXJl" + ("LTE=" if index == 1 else "LTI=")
    return {"role": "model", "parts": [{"text": "test thought summary", "thought": True}, call]}


def response(content):
    return {
        "candidates": [{"content": content, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 5, "thoughtsTokenCount": 2},
        "modelVersion": "gemini-3.6-flash",
    }


def final_content():
    return {
        "role": "model",
        "parts": [
            {
                "text": json.dumps(
                    {
                        "status": "answered",
                        "answer": "Aster Vale was Canadian. [E1] [E2]",
                        "citations": ["E1", "E2"],
                        "conflicts": [],
                    }
                )
            }
        ],
    }


@pytest.fixture
def sdk_transport(monkeypatch):
    original_client = genai.Client
    requests, replies, paths = [], [], []

    async def handle(request):
        assert request.url.host == "generativelanguage.googleapis.com"
        assert request.url.path in {
            f"/v1beta/models/{model}:generateContent"
            for model in ("gemini-3.6-flash", "gemma-4-31b-it", "gemma-4-26b-a4b-it")
        }
        paths.append(request.url.path)
        requests.append(json.loads(request.content))
        status, body = replies.pop(0)
        return httpx.Response(status, json=body, request=request)

    def client(**kwargs):
        options = kwargs.pop("http_options")
        options.httpx_async_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        return original_client(**kwargs, http_options=options)

    monkeypatch.setattr(module.genai, "Client", client)
    return SimpleNamespace(
        requests=requests,
        replies=replies,
        paths=paths,
        create=lambda model="gemini-3.6-flash": module.GeminiInvestigationModel(
            api_key="credential-sentinel", model=model
        ),
    )


def hit(registration, source, text):
    document, version = registration(body=text, source_id=source)
    return Evidence(
        document_id=document.id,
        document_version_id=version.id,
        content_sha256=version.content.sha256,
        start=0,
        end=len(text),
        text=text,
        source=version.source,
        source_spans=(),
        rank=1,
        score=0.9,
    )


async def test_sdk_serializes_two_signed_sequential_searches_and_final_decision(sdk_transport, registration):
    first = tool_content(1, "film director")
    second = tool_content(2, "Aster Vale nationality")
    sdk_transport.replies.extend([(200, response(first)), (200, response(second)), (200, response(final_content()))])
    evidence = [
        hit(registration, "film", "The film was directed by Aster Vale."),
        hit(registration, "person", "Aster Vale was Canadian."),
    ]

    class Search:
        calls = []

        async def execute(self, query, limit, *, owner_id):
            self.calls.append(query)
            return (evidence[len(self.calls) - 1],)

    provider, search = sdk_transport.create(), Search()
    try:
        result = await InvestigateQuestion(
            search, provider, max_turns=3, max_search_calls=2, max_generation_requests=3, max_embedding_requests=2
        ).execute("Director nationality?", owner_id=None)
        assert result.status == InvestigationStatus.ANSWERED and result.citations == ("E1", "E2")
        assert search.calls == ["film director", "Aster Vale nationality"]
        assert result.generation_requests == 3 and result.usage.prompt_tokens == 60
        assert provider._native_calls == {}
        requests = sdk_transport.requests
        assert [len(r["contents"]) for r in requests] == [1, 3, 5]
        for index, request in enumerate(requests):
            assert [c["role"] for c in request["contents"]] == ["user", "model", "user", "model", "user"][
                : 2 * index + 1
            ]
            assert request["tools"] == requests[0]["tools"]
            assert request["toolConfig"] == requests[0]["toolConfig"]
            assert request["generationConfig"] == requests[0]["generationConfig"]
        assert requests[1]["contents"][1] == first == requests[2]["contents"][1]
        assert requests[2]["contents"][3] == second
        for index, expected in ((2, "native-1"), (4, "native-2")):
            fr = requests[2]["contents"][index]["parts"][0]["functionResponse"]
            assert fr["id"] == expected and fr["name"] == "search_evidence"
            assert fr["response"]["results"][0]["evidence_id"] == f"E{index // 2}"
        assert all(
            "provider_state" not in {field.name for field in fields(value)}
            for value in (SearchExchange, SearchEvidenceCall)
        )
    finally:
        await provider.aclose()


async def test_missing_signature_is_rejected_before_any_tool_can_run(sdk_transport):
    sdk_transport.replies.append((200, response(tool_content(1, "q", signed=False))))
    provider = sdk_transport.create()
    try:
        with pytest.raises(GenerationResponseError) as caught:
            await provider.next_action(InvestigationTurn(question="Q", exchanges=()))
        assert len(sdk_transport.requests) == 1 and not provider._native_calls
        assert safe_diagnostic(caught.value)["investigation_step"] == 1
    finally:
        await provider.aclose()


async def test_history_order_query_and_question_must_match_native_calls(sdk_transport):
    sdk_transport.replies.extend([(200, response(tool_content(1, "q1"))), (200, response(tool_content(2, "q2")))])
    provider = sdk_transport.create()
    try:
        a = await provider.next_action(InvestigationTurn(question="Q", exchanges=()))
        e1 = SearchExchange(query=a.query, call_id=a.call_id, evidence=())
        b = await provider.next_action(InvestigationTurn(question="Q", exchanges=(e1,)))
        e2 = SearchExchange(query=b.query, call_id=b.call_id, evidence=())
        for turn in (
            InvestigationTurn(question="Q", exchanges=(e2, e1)),
            InvestigationTurn(question="different", exchanges=(e1, e2)),
            InvestigationTurn(question="Q", exchanges=(SearchExchange(query="wrong", call_id=a.call_id, evidence=()),)),
        ):
            with pytest.raises(GenerationResponseError):
                await provider.next_action(turn)
        assert len(sdk_transport.requests) == 2
    finally:
        await provider.aclose()


async def test_evidence_only_checkpoint_cannot_resume_on_new_client(sdk_transport):
    sdk_transport.replies.append((200, response(tool_content(1, "q"))))
    original = sdk_transport.create()
    try:
        action = await original.next_action(InvestigationTurn(question="Q", exchanges=()))
    finally:
        await original.aclose()
    restored = sdk_transport.create()
    try:
        with pytest.raises(GenerationResponseError) as caught:
            await restored.next_action(
                InvestigationTurn(
                    question="Q", exchanges=(SearchExchange(query=action.query, call_id=action.call_id, evidence=()),)
                )
            )
        assert len(sdk_transport.requests) == 1
        diagnostic = safe_diagnostic(caught.value)
        assert diagnostic["error_reason"] == "missing_native_history" and diagnostic["request_count"] == 0
    finally:
        await restored.aclose()


async def test_http400_records_allowlisted_reason_without_message_or_signature(sdk_transport):
    sdk_transport.replies.extend(
        [
            (200, response(tool_content(1, "q1"))),
            (200, response(tool_content(2, "q2"))),
            (
                400,
                {
                    "error": {
                        "code": 400,
                        "status": "INVALID_ARGUMENT",
                        "message": "Function call is missing a thought_signature. credential-sentinel private-document-sentinel signature-sentinel",
                    }
                },
            ),
        ]
    )
    provider = sdk_transport.create()
    try:
        a = await provider.next_action(InvestigationTurn(question="Q", exchanges=()))
        e1 = SearchExchange(query=a.query, call_id=a.call_id, evidence=())
        b = await provider.next_action(InvestigationTurn(question="Q", exchanges=(e1,)))
        e2 = SearchExchange(query=b.query, call_id=b.call_id, evidence=())
        with pytest.raises(GenerationProviderError) as caught:
            await provider.next_action(InvestigationTurn(question="Q", exchanges=(e1, e2)))
        diagnostic = safe_diagnostic(caught.value)
        assert diagnostic["provider_code"] == "INVALID_ARGUMENT"
        assert diagnostic["error_reason"] == "missing_thought_signature"
        assert diagnostic["http_status"] == 400 and diagnostic["investigation_step"] == 3
        assert diagnostic["request_count"] == 3 and not diagnostic["retryable"]
        safe = json.dumps(diagnostic) + "".join(traceback.format_exception(caught.value))
        assert all(
            value not in safe for value in ("credential-sentinel", "private-document-sentinel", "signature-sentinel")
        )
        assert len(sdk_transport.requests) == 3
    finally:
        await provider.aclose()


async def test_parallel_calls_are_rejected_without_partial_tool_execution(sdk_transport):
    content = tool_content(1, "q1")
    content["parts"].append(tool_content(2, "q2")["parts"][-1])
    sdk_transport.replies.append((200, response(content)))
    provider = sdk_transport.create()
    try:
        with pytest.raises(GenerationResponseError):
            await provider.next_action(InvestigationTurn(question="Q", exchanges=()))
        assert not provider._native_calls and len(sdk_transport.requests) == 1
    finally:
        await provider.aclose()


async def test_historical_reconstruction_has_no_sdk_generated_signature(sdk_transport):
    """Reproduce Task 16B's serialized defect; the SDK does not repair unsigned history."""
    sdk_transport.replies.append(
        (
            400,
            {
                "error": {
                    "code": 400,
                    "status": "INVALID_ARGUMENT",
                    "message": "Function call is missing a thought_signature.",
                }
            },
        )
    )
    provider = sdk_transport.create()
    try:
        contents = [
            types.Content(role="user", parts=[types.Part(text="Q")]),
            types.Content(
                role="model",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            name="search_evidence", args={"query": "saved query"}, id="task16b-resume-1"
                        )
                    )
                ],
            ),
            types.Content(
                role="user", parts=[types.Part.from_function_response(name="search_evidence", response={"results": []})]
            ),
        ]
        with pytest.raises(errors.ClientError):
            await provider._client.aio.models.generate_content(model=provider.model, contents=contents)
        wire = sdk_transport.requests[0]["contents"]
        assert "thoughtSignature" not in wire[1]["parts"][0]
        assert "id" not in wire[2]["parts"][0]["functionResponse"]
        assert wire[1]["parts"][0]["functionCall"]["id"] == "task16b-resume-1"
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    "status,code,reason",
    [(429, "RESOURCE_EXHAUSTED", "quota_or_rate_limit"), (503, "UNAVAILABLE", "provider_rejection")],
)
async def test_sdk_error_categories_distinguish_quota_and_availability(sdk_transport, status, code, reason):
    sdk_transport.replies.append((status, {"error": {"code": status, "status": code, "message": "private-sentinel"}}))
    provider = sdk_transport.create()
    try:
        with pytest.raises(GenerationProviderError) as caught:
            await provider.next_action(InvestigationTurn(question="Q", exchanges=()))
        diagnostic = safe_diagnostic(caught.value)
        assert diagnostic["provider_code"] == code and diagnostic["error_reason"] == reason
        assert diagnostic["rate_or_quota_limit"] == (status == 429)
        assert len(sdk_transport.requests) == 1
    finally:
        await provider.aclose()


@pytest.mark.parametrize("model", ["gemma-4-31b-it", "gemma-4-26b-a4b-it"])
async def test_gemma_native_calls_are_preserved_without_inventing_signatures(sdk_transport, model):
    native = tool_content(1, "query", signed=False)
    sdk_transport.replies.extend([(200, response(native)), (200, response(final_content()))])
    provider = sdk_transport.create(model)
    try:
        action = await provider.next_action(InvestigationTurn(question="Q", exchanges=()))
        decision = await provider.next_action(
            InvestigationTurn(
                question="Q", exchanges=(SearchExchange(query=action.query, call_id=action.call_id, evidence=()),)
            )
        )
        assert decision.status == InvestigationStatus.ANSWERED
        assert sdk_transport.requests[1]["contents"][1] == native
        assert all(path == f"/v1beta/models/{model}:generateContent" for path in sdk_transport.paths)
    finally:
        await provider.aclose()
