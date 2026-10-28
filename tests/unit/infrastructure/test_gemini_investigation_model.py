import json
import traceback
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from google.genai import errors, types

from investigation_engine.application.investigations import (
    IdentifiedEvidence, InvestigationTurn, SearchExchange,
)
from investigation_engine.application.ports.generation_errors import (
    GenerationProviderError, GenerationRateLimitError, GenerationResponseError,
)
from investigation_engine.application.ports.provider_diagnostics import safe_diagnostic
from investigation_engine.application.retrieval import Evidence
from investigation_engine.domain.content import Content
from investigation_engine.domain.document import Document
from investigation_engine.domain.document_version import DocumentVersion
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.infrastructure.generation import gemini_investigation_model as module


def _response(part, *, usage=None):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        finish_reason=types.FinishReason.STOP,
        content=types.Content(role="model", parts=[part]),
    )], usage_metadata=usage)


@pytest.fixture
def provider(monkeypatch):
    models = SimpleNamespace(generate_content=AsyncMock())
    client = SimpleNamespace(aio=SimpleNamespace(models=models, aclose=AsyncMock()), close=Mock())
    factory = Mock(return_value=client)
    monkeypatch.setattr(module.genai, "Client", factory)
    instance = module.GeminiInvestigationModel(api_key="never-print-this-key", model="gemini-test")
    yield SimpleNamespace(provider=instance, models=models, client=client, factory=factory)


def test_native_tool_turn_is_one_bounded_request_and_continuation_is_preserved(provider):
    tool_content = types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
        name="search_evidence", args={"query": "new query"}, id="call-1"))])
    provider.models.generate_content.side_effect = [
        _response(tool_content.parts[0], usage=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=21, candidates_token_count=4, thoughts_token_count=7)),
        _response(types.Part(text=json.dumps({"status": "answered", "answer": "Fact. [E1]",
            "citations": ["E1"], "conflicts": []}))),
    ]
    turn = InvestigationTurn(question="Question", exchanges=())
    action = __import__("asyncio").run(provider.provider.next_action(turn))
    assert action.query == "new query" and action.call_id != "call-1"
    assert action.usage.prompt_tokens == 21 and action.usage.thinking_tokens == 7
    assert not hasattr(action, "provider_state")
    document = Document(owner_id=None)
    version = DocumentVersion(document_id=document.id, version_number=1, content=Content("Fact."),
        source=SourceReference(kind=SourceKind.DATASET, source_id="src", record_locator={}))
    evidence = Evidence(document_id=document.id, document_version_id=version.id,
        content_sha256=version.content.sha256, start=0, end=5, text="Fact.", source=version.source,
        source_spans=(), rank=1, score=1.0)
    next_turn = InvestigationTurn(question="Question", exchanges=(SearchExchange(
        query=action.query, call_id=action.call_id,
        evidence=(IdentifiedEvidence(evidence_id="E1", evidence=evidence),)),))
    decision = __import__("asyncio").run(provider.provider.next_action(next_turn))
    assert decision.answer == "Fact. [E1]" and decision.model_version is None
    contents = provider.models.generate_content.await_args.kwargs["contents"]
    assert contents[1].parts[0].function_call.name == "search_evidence"
    assert contents[1].parts[0].function_call.args == {"query": "new query"}
    assert contents[2].parts[0].function_response.id == "call-1"
    function_result = contents[2].parts[0].function_response.response
    assert function_result["results"][0]["evidence_id"] == "E1"
    assert function_result["results"][0]["document_version_id"] == str(version.id)
    config = provider.models.generate_content.await_args.kwargs["config"]
    assert config.response_json_schema == module.FINAL_SCHEMA
    assert config.max_output_tokens == 1024
    assert provider.factory.call_args.kwargs["http_options"].retry_options.attempts == 1
    assert provider.models.generate_content.await_count == 2


@pytest.mark.parametrize("name,args", [("unknown", {"query": "q"}), ("search_evidence", {"query": "q", "owner_id": "x"})])
def test_unknown_tools_and_untrusted_arguments_are_rejected(name, args):
    call = types.FunctionCall(name=name, args=args, id="call")
    with pytest.raises(GenerationResponseError):
        module.GeminiInvestigationModel._parse_response(_response(types.Part(function_call=call)))


@pytest.mark.parametrize("status,exception", [(429, GenerationRateLimitError), (503, GenerationProviderError)])
async def test_provider_errors_are_sanitized_and_never_retried(provider, status, exception):
    provider.models.generate_content.side_effect = errors.APIError(status, {"error": {"message": "never-print-this-key"}})
    with pytest.raises(exception) as caught:
        await provider.provider.next_action(InvestigationTurn(question="Q", exchanges=()))
    assert "never-print-this-key" not in "".join(traceback.format_exception(caught.value))
    assert provider.models.generate_content.await_count == 1
    assert safe_diagnostic(caught.value)["http_status"] == status
    assert safe_diagnostic(caught.value)["request_count"] == 1


async def test_close_is_idempotent(provider):
    await provider.provider.aclose()
    await provider.provider.aclose()
    provider.client.aio.aclose.assert_awaited_once()
