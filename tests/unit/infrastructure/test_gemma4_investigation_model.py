"""Gemma 4 native function calling and strict text-final adaptation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from google.genai import errors, types

from investigation_engine.application.investigations import (
    InvestigateQuestion,
    InvestigationStatus,
    InvestigationTurn,
)
from investigation_engine.application.ports.generation_errors import (
    GenerationProviderError,
    GenerationRateLimitError,
    GenerationResponseError,
    GenerationTimeoutError,
)
from investigation_engine.application.retrieval import Evidence
from investigation_engine.infrastructure.generation.gemma4_investigation_model import (
    GEMMA4_MODEL,
    Gemma4InvestigationModel,
)


def _response(content, *, usage=None):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(finish_reason=types.FinishReason.STOP, content=content)],
        usage_metadata=usage,
        model_version=GEMMA4_MODEL,
    )


def _tool_response(index, query):
    return _response(
        types.Content(
            role="model",
            parts=[
                types.Part(text="hidden thought", thought=True),
                types.Part(
                    function_call=types.FunctionCall(
                        name="search_evidence", args={"query": query}, id=f"native-{index}"
                    ),
                    thought_signature="c2lnbmF0dXJlLTE=" if index == 1 else "c2lnbmF0dXJlLTI=",
                ),
            ],
        )
    )


def _text_response(text):
    return _response(types.Content(role="model", parts=[types.Part(text=text)]))


@pytest.fixture
async def provider(monkeypatch):
    models = SimpleNamespace(generate_content=AsyncMock())
    client = SimpleNamespace(aio=SimpleNamespace(models=models, aclose=AsyncMock()), close=Mock())
    factory = Mock(return_value=client)
    monkeypatch.setattr(
        "investigation_engine.infrastructure.generation.gemini_investigation_model.genai.Client", factory
    )
    instance = Gemma4InvestigationModel(api_key="credential-sentinel", model=GEMMA4_MODEL, retry_max_attempts=1)
    yield SimpleNamespace(provider=instance, models=models, client=client, factory=factory)
    await instance.aclose()


def _evidence(registration, text, source_id):
    document, version = registration(body=text, source_id=source_id)
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


async def test_two_native_searches_preserve_signed_history_and_finalize_with_valid_citations(provider, registration):
    provider.models.generate_content.side_effect = [
        _tool_response(1, "William Jolliffe father"),
        _tool_response(2, "Hylton Jolliffe 3rd Baron Hylton father"),
        _text_response(
            "STATUS: answered\nANSWER: Hedworth Jolliffe, 2nd Baron Hylton, was his paternal grandfather. [E1] [E2]"
        ),
    ]
    first = _evidence(registration, "William Jolliffe was the son of Hylton Jolliffe.", "first-source")
    second = _evidence(registration, "Hylton Jolliffe was the son of Hedworth Jolliffe.", "second-source")

    class Search:
        calls = []

        async def execute(self, query, limit, *, owner_id):
            self.calls.append(query)
            return (first,) if len(self.calls) == 1 else (second,)

    search = Search()
    result = await InvestigateQuestion(search, provider.provider).execute(
        "Who is William Jolliffe's paternal grandfather?", owner_id=None
    )

    assert result.status == InvestigationStatus.ANSWERED
    assert result.answer.endswith("[E1] [E2]") and result.citations == ("E1", "E2")
    assert [item.evidence for item in result.evidence] == [first, second]
    assert [item.evidence.source.source_id for item in result.evidence] == ["first-source", "second-source"]
    assert search.calls == ["William Jolliffe father", "Hylton Jolliffe 3rd Baron Hylton father"]
    assert provider.models.generate_content.await_count == 3

    initial_request, second_request, final_request = [
        call.kwargs for call in provider.models.generate_content.await_args_list
    ]
    for request in (initial_request, second_request, final_request):
        config = request["config"]
        assert config.tools and config.tools[0].function_declarations[0].name == "search_evidence"
        assert config.tool_config is None
        assert config.response_json_schema is None and config.response_mime_type is None
        assert request["model"] == GEMMA4_MODEL
    assert len(initial_request["contents"]) == 1
    assert second_request["contents"][:1] == initial_request["contents"]
    assert final_request["contents"][:3] == second_request["contents"]
    calls = [
        part.function_call
        for content in final_request["contents"]
        for part in content.parts
        if part.function_call is not None
    ]
    responses = [
        part.function_response
        for content in final_request["contents"]
        for part in content.parts
        if part.function_response is not None
    ]
    assert [call.id for call in calls] == ["native-1", "native-2"]
    assert [response.id for response in responses] == ["native-1", "native-2"]
    assert [response.name for response in responses] == ["search_evidence", "search_evidence"]
    assert responses[1].response["results"][0]["evidence_id"] == "E2"


async def test_final_only_limit_removes_search_tool_but_keeps_native_history(provider, registration):
    provider.models.generate_content.side_effect = [
        _tool_response(1, "one bounded lookup"),
        _text_response("STATUS: answered\nANSWER: The supported fact is true. [E1]"),
    ]
    evidence = _evidence(registration, "The supported fact is true.", "source")

    class Search:
        async def execute(self, query, limit, *, owner_id):
            return (evidence,)

    result = await InvestigateQuestion(Search(), provider.provider, max_search_calls=1).execute(
        "Question?", owner_id=None
    )
    assert result.status == InvestigationStatus.ANSWERED and result.citations == ("E1",)
    final_request = provider.models.generate_content.await_args.kwargs
    assert final_request["config"].tools == []
    assert "final_only is enabled" in final_request["config"].system_instruction
    assert len(final_request["contents"]) == 3


async def test_unknown_citations_are_rejected_by_shared_investigation_validation(provider, registration):
    provider.models.generate_content.side_effect = [
        _tool_response(1, "supported fact"),
        _text_response("STATUS: answered\nANSWER: Unsupported citation test. [E9]"),
    ]
    evidence = _evidence(registration, "A supported fact.", "source")

    class Search:
        async def execute(self, query, limit, *, owner_id):
            return (evidence,)

    with pytest.raises(GenerationResponseError, match="outside the retrieved evidence registry"):
        await InvestigateQuestion(Search(), provider.provider).execute("Question?", owner_id=None)


@pytest.mark.parametrize(
    "text",
    [
        "Here is the answer: STATUS: answered\nANSWER: Fact. [E1]",
        "STATUS: answered\nANSWER: Fact with no source.",
        "STATUS: insufficient_evidence\nANSWER: Guessed answer.",
        "STATUS: conflicting_evidence\nCLAIM: Same claim [E1]",
        "STATUS: unknown\nANSWER: Fact. [E1]",
    ],
)
def test_malformed_or_unsupported_final_text_is_rejected(text):
    with pytest.raises(GenerationResponseError):
        Gemma4InvestigationModel._parse_response(_text_response(text))


@pytest.mark.parametrize("args", [{"query": 7}, {"query": "q", "owner_id": "not-allowed"}])
def test_invalid_native_tool_arguments_are_rejected(args):
    response = _response(
        types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(name="search_evidence", args=args, id="native-1"))],
        )
    )
    with pytest.raises(GenerationResponseError):
        Gemma4InvestigationModel._parse_response(response)


@pytest.mark.parametrize(
    "status,exception",
    [(429, GenerationRateLimitError), (503, GenerationProviderError)],
)
async def test_provider_errors_are_bounded_sanitized_and_not_retried(provider, status, exception):
    provider.models.generate_content.side_effect = errors.APIError(
        status, {"error": {"message": "credential-sentinel private-response"}}
    )
    with pytest.raises(exception) as caught:
        await provider.provider.next_action(InvestigationTurn(question="Question?", exchanges=()))
    assert provider.models.generate_content.await_count == 1
    assert "credential-sentinel" not in str(caught.value)
    assert "private-response" not in str(caught.value)


async def test_provider_timeout_is_reported_as_timeout_without_retry(provider):
    provider.models.generate_content.side_effect = TimeoutError
    with pytest.raises(GenerationTimeoutError):
        await provider.provider.next_action(InvestigationTurn(question="Question?", exchanges=()))
    assert provider.models.generate_content.await_count == 1


async def test_malformed_text_insufficient_and_conflicting_decisions_have_distinct_contracts():
    insufficient = Gemma4InvestigationModel._parse_response(_text_response("STATUS: insufficient_evidence"))
    assert insufficient.status == InvestigationStatus.INSUFFICIENT_EVIDENCE
    assert insufficient.answer is None and insufficient.citations == ()

    conflict = Gemma4InvestigationModel._parse_response(
        _text_response(
            "STATUS: conflicting_evidence\nCLAIM: It was born in Oslo. [E1]\nCLAIM: It was born in Bergen. [E2]"
        )
    )
    assert conflict.status == InvestigationStatus.CONFLICTING_EVIDENCE
    assert [(item.claim, item.citations) for item in conflict.conflicts] == [
        ("It was born in Oslo. [E1]", ("E1",)),
        ("It was born in Bergen. [E2]", ("E2",)),
    ]
    assert conflict.answer is None and conflict.citations == ()


def test_grouped_natural_language_citations_are_normalized_without_losing_ids():
    decision = Gemma4InvestigationModel._parse_response(
        _text_response("STATUS: answered\nANSWER: The answer is supported by both sources. [E1, E2]")
    )
    assert decision.answer == "The answer is supported by both sources. [E1] [E2]"
    assert decision.citations == ("E1", "E2")
