import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from google.genai import errors

from investigation_engine.application.investigations import InvestigationTurn
from investigation_engine.application.ports.generation_errors import (
    GenerationProviderError,
    GenerationRateLimitError,
    GenerationTimeoutError,
)
from investigation_engine.application.ports.provider_diagnostics import safe_diagnostic
from investigation_engine.application.timing import _trace
from investigation_engine.infrastructure.generation import gemini_investigation_model as module
from investigation_engine.infrastructure.generation.gemma4_investigation_model import Gemma4InvestigationModel
from investigation_engine.infrastructure.structured_logging import TimingJsonFormatter


@pytest.mark.parametrize(
    "adapter,model,provider",
    [
        (module.GeminiInvestigationModel, "gemini-test", "gemini"),
        (Gemma4InvestigationModel, "gemma-4-26b-a4b-it", "gemma4"),
    ],
)
@pytest.mark.parametrize(
    "status,code,exception",
    [
        (500, "INTERNAL", GenerationProviderError),
        (504, "DEADLINE_EXCEEDED", GenerationProviderError),
        (429, "RESOURCE_EXHAUSTED", GenerationRateLimitError),
        (400, "INVALID_ARGUMENT", GenerationProviderError),
    ],
)
async def test_provider_failure_is_structured_once_without_payload(
    monkeypatch, caplog, adapter, model, provider, status, code, exception
):
    monkeypatch.setenv("DEBUG_TRACE_ENABLED", "false")
    response = httpx.Response(status, headers={"x-request-id": "req-123", "authorization": "secret-header"})
    original = errors.APIError(status, {"error": {"status": code, "message": "private-prompt secret-key"}}, response)
    models = SimpleNamespace(generate_content=AsyncMock(side_effect=original))
    client = SimpleNamespace(aio=SimpleNamespace(models=models, aclose=AsyncMock()), close=Mock())
    monkeypatch.setattr(module.genai, "Client", Mock(return_value=client))
    options = {"retry_max_attempts": 1} if adapter is Gemma4InvestigationModel else {}
    instance = adapter(api_key="secret-key", model=model, **options)
    token = _trace.set("app-request-123")
    try:
        with caplog.at_level(logging.WARNING, logger="investigation_engine.application.timing"):
            with pytest.raises(exception) as caught:
                await instance.next_action(InvestigationTurn(question="private-prompt", exchanges=()))
    finally:
        _trace.reset(token)
        await instance.aclose()
    assert safe_diagnostic(caught.value)["http_status"] == status
    events = [r for r in caplog.records if getattr(r, "event", None) == "generation.provider_failed"]
    assert len(events) == 1 and models.generate_content.await_count == 1
    value = json.loads(TimingJsonFormatter().format(events[0]))
    assert value["provider"] == provider and value["model_id"] == model
    assert value["http_status"] == status and value["provider_code"] == code
    assert value["error_reason"] == (
        "quota_or_rate_limit" if status == 429 else "invalid_argument" if status == 400 else "provider_rejection"
    )
    assert value["request_id"] == "app-request-123" and value["provider_request_id"] == "req-123"
    expected_retryable = status != 400 and not (status == 429 and provider == "gemma4")
    assert value["exception_type"] == "APIError" and value["retryable"] is expected_retryable
    assert value["turn_number"] == 1 and value["duration_ms"] >= 0
    assert all(secret not in json.dumps(value) for secret in ("private-prompt", "secret-key", "secret-header"))


@pytest.mark.parametrize(
    "adapter,model",
    [(module.GeminiInvestigationModel, "gemini-test"), (Gemma4InvestigationModel, "gemma-4-26b-a4b-it")],
)
async def test_unexpected_exception_is_logged_and_propagates_unchanged(monkeypatch, caplog, adapter, model):
    original = RuntimeError("private request and secret")
    models = SimpleNamespace(generate_content=AsyncMock(side_effect=original))
    client = SimpleNamespace(aio=SimpleNamespace(models=models, aclose=AsyncMock()), close=Mock())
    monkeypatch.setattr(module.genai, "Client", Mock(return_value=client))
    options = {"retry_max_attempts": 1} if adapter is Gemma4InvestigationModel else {}
    instance = adapter(api_key="secret", model=model, **options)
    try:
        with caplog.at_level(logging.WARNING, logger="investigation_engine.application.timing"):
            with pytest.raises(RuntimeError) as caught:
                await instance.next_action(InvestigationTurn(question="private request", exchanges=()))
        assert caught.value is original
        events = [r for r in caplog.records if getattr(r, "event", None) == "generation.provider_failed"]
        assert len(events) == 1 and events[0].exception_type == "RuntimeError"
        assert "private request" not in json.dumps(json.loads(TimingJsonFormatter().format(events[0])))
    finally:
        await instance.aclose()


async def test_local_timeout_remains_a_timeout_and_omits_unknown_fields(monkeypatch, caplog):
    models = SimpleNamespace(generate_content=AsyncMock(side_effect=TimeoutError("private")))
    client = SimpleNamespace(aio=SimpleNamespace(models=models, aclose=AsyncMock()), close=Mock())
    monkeypatch.setattr(module.genai, "Client", Mock(return_value=client))
    instance = module.GeminiInvestigationModel(api_key="secret", model="gemini-test")
    try:
        with caplog.at_level(logging.WARNING, logger="investigation_engine.application.timing"):
            with pytest.raises(GenerationTimeoutError) as caught:
                await instance.next_action(InvestigationTurn(question="private", exchanges=()))
        assert safe_diagnostic(caught.value)["category"] == "timeout"
        event = next(r for r in caplog.records if getattr(r, "event", None) == "generation.provider_failed")
        value = json.loads(TimingJsonFormatter().format(event))
        assert value["exception_type"] == "TimeoutError" and value["retryable"] is False
        assert value["http_status"] is None and value["provider_request_id"] is None
    finally:
        await instance.aclose()


async def test_logging_failure_does_not_replace_provider_failure(monkeypatch):
    from investigation_engine.infrastructure.generation import failure_logging

    models = SimpleNamespace(generate_content=AsyncMock(side_effect=errors.APIError(500, {})))
    client = SimpleNamespace(aio=SimpleNamespace(models=models, aclose=AsyncMock()), close=Mock())
    monkeypatch.setattr(module.genai, "Client", Mock(return_value=client))
    monkeypatch.setattr(failure_logging, "log_event", Mock(side_effect=RuntimeError("logger failure")))
    instance = module.GeminiInvestigationModel(api_key="secret", model="gemini-test")
    try:
        with pytest.raises(GenerationProviderError) as caught:
            await instance.next_action(InvestigationTurn(question="Q", exchanges=()))
        assert safe_diagnostic(caught.value)["http_status"] == 500
    finally:
        await instance.aclose()
