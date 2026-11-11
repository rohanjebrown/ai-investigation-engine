import asyncio
import logging
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from google.genai import errors, types

from investigation_engine.application.investigations import InvestigateQuestion, InvestigationTurn
from investigation_engine.application.ports.generation_errors import (
    GenerationProviderError,
    GenerationRateLimitError,
    GenerationResponseError,
    GenerationTimeoutError,
)
from investigation_engine.application.retrieval import Evidence
from investigation_engine.application.timing import _trace
from investigation_engine.evaluation.live_providers import PacedInvestigationModel
from investigation_engine.evaluation.telemetry import ObservedGenerator, RequestBudget
from investigation_engine.infrastructure.generation import gemini_investigation_model as transport
from investigation_engine.infrastructure.generation import gemma4_investigation_model as gemma


def error(status, *, headers=None, details=None):
    response = httpx.Response(status, headers=headers)
    return errors.APIError(status, details or {"error": {"status": "INTERNAL"}}, response)


def response(text="STATUS: insufficient_evidence"):
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                finish_reason=types.FinishReason.STOP,
                content=types.Content(role="model", parts=[types.Part(text=text)]),
            )
        ]
    )


@pytest.fixture
async def model(monkeypatch):
    api = AsyncMock()
    client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=api), aclose=AsyncMock()), close=Mock()
    )
    factory = Mock(return_value=client)
    monkeypatch.setattr(transport.genai, "Client", factory)
    sleep = AsyncMock()
    monkeypatch.setattr(gemma.asyncio, "sleep", sleep)
    monkeypatch.setattr(gemma.random, "uniform", lambda low, high: high / 2)
    instance = gemma.Gemma4InvestigationModel(api_key="private-key", model=gemma.GEMMA4_MODEL, timeout_seconds=30)
    yield SimpleNamespace(provider=instance, api=api, sleep=sleep, client=client, factory=factory)
    await instance.aclose()


@pytest.mark.parametrize("status,failures", [(500, 0), (500, 1), (500, 4), (504, 1)])
async def test_success_and_transient_recovery(model, caplog, status, failures):
    model.api.side_effect = [*[error(status) for _ in range(failures)], response()]
    token = _trace.set("correlation-123")
    try:
        with caplog.at_level(logging.INFO, logger="investigation_engine.application.timing"):
            result = await model.provider.next_action(InvestigationTurn(question="Question?", exchanges=()))
    finally:
        _trace.reset(token)
    assert result.status.value == "insufficient_evidence"
    assert model.api.await_count == failures + 1 == model.provider.provider_requests
    assert model.factory.call_args.kwargs["http_options"].retry_options.attempts == 1
    assert (
        model.client.aio.models.generate_content.await_args_list[0].kwargs
        == model.client.aio.models.generate_content.await_args_list[-1].kwargs
    )
    assert [call.args[0] for call in model.sleep.await_args_list] == [min(8, 2**i) / 2 for i in range(failures)]
    events = [r for r in caplog.records if getattr(r, "event", None) == "generation.provider_failed"]
    assert len(events) == failures
    assert all(r.request_id == "correlation-123" and r.will_retry and r.max_attempts == 5 for r in events)
    assert [r.attempt for r in events] == list(range(1, failures + 1))
    assert len([r for r in caplog.records if getattr(r, "event", None) == "generation.retry_recovered"]) == bool(
        failures
    )


async def test_rate_limit_retry_after_and_daily_quota(model):
    model.api.side_effect = [error(429, headers={"Retry-After": "2"}), response()]
    await model.provider.next_action(InvestigationTurn(question="Q", exchanges=()))
    model.sleep.assert_awaited_once_with(2.0)
    model.api.reset_mock()
    model.sleep.reset_mock()
    model.api.side_effect = error(
        429,
        headers={"Retry-After": "2"},
        details={
            "error": {"details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "QUOTA_EXCEEDED"}]}
        },
    )
    with pytest.raises(GenerationRateLimitError):
        await model.provider.next_action(InvestigationTurn(question="Q", exchanges=()))
    assert model.api.await_count == 1
    model.sleep.assert_not_awaited()


async def test_rate_limit_retry_after_http_date(model):
    retry_date = format_datetime(datetime.now(UTC) + timedelta(seconds=6), usegmt=True)
    model.api.side_effect = [error(429, headers={"Retry-After": retry_date}), response()]
    await model.provider.next_action(InvestigationTurn(question="Q", exchanges=()))
    assert 3 <= model.sleep.await_args.args[0] <= 6


async def test_retry_after_is_respected_for_service_unavailable(model):
    model.api.side_effect = [error(503, headers={"Retry-After": "2"}), response()]
    await model.provider.next_action(InvestigationTurn(question="Q", exchanges=()))
    model.sleep.assert_awaited_once_with(2.0)


async def test_rate_limit_zero_hint_retries_but_missing_hint_does_not(model):
    model.api.side_effect = [error(429, headers={"Retry-After": "0"}), response()]
    await model.provider.next_action(InvestigationTurn(question="Q", exchanges=()))
    assert model.api.await_count == 2
    model.api.reset_mock()
    model.sleep.reset_mock()
    model.api.side_effect = error(429)
    with pytest.raises(GenerationRateLimitError):
        await model.provider.next_action(InvestigationTurn(question="Q", exchanges=()))
    assert model.api.await_count == 1
    model.sleep.assert_not_awaited()


async def test_retry_after_beyond_deadline_does_not_sleep(model):
    model.api.side_effect = error(429, headers={"Retry-After": "60"})
    with pytest.raises(GenerationRateLimitError):
        await model.provider.next_action(InvestigationTurn(question="Q", exchanges=()))
    assert model.api.await_count == 1
    model.sleep.assert_not_awaited()


@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_client_errors_do_not_retry(model, status):
    model.api.side_effect = error(status)
    with pytest.raises(GenerationProviderError):
        await model.provider.next_action(InvestigationTurn(question="Q", exchanges=()))
    assert model.api.await_count == 1
    model.sleep.assert_not_awaited()


async def test_malformed_model_output_does_not_retry(model):
    model.api.return_value = response("not a valid final")
    with pytest.raises(GenerationResponseError):
        await model.provider.next_action(InvestigationTurn(question="Q", exchanges=()))
    assert model.api.await_count == 1
    model.sleep.assert_not_awaited()


async def test_transport_timeout_and_exhaustion(model, caplog):
    model.api.side_effect = [httpx.ConnectTimeout("private"), response()]
    await model.provider.next_action(InvestigationTurn(question="Q", exchanges=()))
    assert model.api.await_count == 2
    model.api.reset_mock()
    model.api.side_effect = error(500)
    with caplog.at_level(logging.WARNING, logger="investigation_engine.application.timing"):
        with pytest.raises(GenerationProviderError):
            await model.provider.next_action(InvestigationTurn(question="Q", exchanges=()))
    assert model.api.await_count == 5
    assert len([r for r in caplog.records if getattr(r, "event", None) == "generation.retry_exhausted"]) == 1


async def test_deadline_and_cancelled_backoff_stop_attempts(model, monkeypatch):
    model.provider.retry_total_timeout_seconds = 0.01
    model.api.side_effect = error(500)
    with pytest.raises(GenerationProviderError):
        await model.provider.next_action(InvestigationTurn(question="Q", exchanges=()))
    assert model.api.await_count == 1
    model.sleep.assert_not_awaited()
    model.provider.retry_total_timeout_seconds = 30
    model.api.reset_mock()
    model.sleep.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await model.provider.next_action(InvestigationTurn(question="Q", exchanges=()))
    assert model.api.await_count == 1


async def test_inflight_attempt_obeys_total_deadline(model):
    async def never_returns(**kwargs):
        await asyncio.Event().wait()

    model.provider.retry_total_timeout_seconds = 0.01
    model.api.side_effect = never_returns
    with pytest.raises(GenerationTimeoutError):
        await model.provider.next_action(InvestigationTurn(question="Q", exchanges=()))
    assert model.api.await_count == 1


async def test_retry_after_tool_response_does_not_repeat_search(model, registration):
    doc, version = registration(body="The fact is true.", source_id="source")
    hit = Evidence(
        document_id=doc.id,
        document_version_id=version.id,
        content_sha256=version.content.sha256,
        text="The fact is true.",
        start=0,
        end=17,
        source=version.source,
        source_spans=(),
        score=1.0,
        rank=1,
    )
    tool = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                finish_reason=types.FinishReason.STOP,
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                name="search_evidence", args={"query": "lookup"}, id="native-id"
                            ),
                            thought_signature="c2ln",
                        )
                    ],
                ),
            )
        ]
    )
    model.api.side_effect = [tool, error(500), response("STATUS: answered\nANSWER: True. [E1]")]
    search = SimpleNamespace(execute=AsyncMock(return_value=(hit,)))
    result = await InvestigateQuestion(search, model.provider).execute("Q", owner_id=None)
    assert result.generation_requests == 2 and model.provider.provider_requests == 3
    search.execute.assert_awaited_once()
    first, second = model.api.await_args_list[1:]
    assert first.kwargs == second.kwargs
    assert first.kwargs["contents"][-1].parts[0].function_response.id == "native-id"


async def test_investigation_rpc_budget_includes_retries_without_changing_turn_count(model):
    tool = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                finish_reason=types.FinishReason.STOP,
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                name="search_evidence", args={"query": "lookup"}, id="native-id"
                            )
                        )
                    ],
                ),
            )
        ]
    )
    model.api.side_effect = [error(500), tool]
    search = SimpleNamespace(execute=AsyncMock(return_value=()))
    result = await InvestigateQuestion(search, model.provider, max_generation_requests=2).execute("Q", owner_id=None)
    assert result.status.value == "incomplete"
    assert result.generation_requests == 1 and model.provider.provider_requests == 2
    assert model.api.await_count == 2
    search.execute.assert_awaited_once()


async def test_four_failures_then_success_are_one_investigation_turn(model):
    model.api.side_effect = [error(500), error(500), error(500), error(500), response()]
    search = SimpleNamespace(execute=AsyncMock())
    result = await InvestigateQuestion(search, model.provider, max_generation_requests=6).execute("Q", owner_id=None)
    assert result.status.value == "insufficient_evidence"
    assert result.generation_requests == 1 and model.provider.provider_requests == 5
    search.execute.assert_not_awaited()


async def test_retry_reuses_prepared_history_if_native_registry_changes(model):
    tool = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                finish_reason=types.FinishReason.STOP,
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                name="search_evidence", args={"query": "lookup"}, id="native-id"
                            ),
                            thought_signature="c2ln",
                        )
                    ],
                ),
            )
        ]
    )
    calls = 0

    async def scripted(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return tool
        if calls == 2:
            model.provider._native_calls.clear()
            raise error(500)
        return response()

    model.api.side_effect = scripted
    search = SimpleNamespace(execute=AsyncMock(return_value=()))
    result = await InvestigateQuestion(search, model.provider).execute("Q", owner_id=None)
    assert result.status.value == "insufficient_evidence" and calls == 3
    assert model.api.await_args_list[1].kwargs == model.api.await_args_list[2].kwargs
    search.execute.assert_awaited_once()


async def test_evaluator_budget_prevents_unreserved_retries(model):
    model.api.side_effect = error(500)
    budget = RequestBudget(1)
    pacer = SimpleNamespace(wait=AsyncMock())
    paced = PacedInvestigationModel(model.provider, pacer)
    model.provider.retry_allowed = lambda: budget.used < budget.maximum
    observed = ObservedGenerator(paced, budget)
    with pytest.raises(GenerationProviderError):
        await observed.next_action(InvestigationTurn(question="Q", exchanges=()))
    assert budget.used == 1 and model.api.await_count == 1


async def test_evaluator_reserves_and_paces_each_retry(model):
    model.api.side_effect = [error(500), response()]
    budget = RequestBudget(2)
    pacer = SimpleNamespace(wait=AsyncMock())
    paced = PacedInvestigationModel(model.provider, pacer)
    model.provider.retry_allowed = lambda: budget.used < budget.maximum

    async def before_retry(turn):
        budget.require(1)
        await pacer.wait(paced.current_reservation)
        budget.consume()

    model.provider.before_retry = before_retry
    await ObservedGenerator(paced, budget).next_action(InvestigationTurn(question="Q", exchanges=()))
    assert budget.used == 2 and model.api.await_count == 2 and pacer.wait.await_count == 2


def test_retry_settings_validate_before_constructing_client(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(transport.genai, "Client", factory)
    for options in (
        {"retry_max_attempts": 0},
        {"retry_max_attempts": 11},
        {"timeout_seconds": 30, "retry_total_timeout_seconds": 31},
    ):
        with pytest.raises(ValueError):
            gemma.Gemma4InvestigationModel(api_key="key", model=gemma.GEMMA4_MODEL, **options)
    factory.assert_not_called()
