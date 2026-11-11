from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from google.genai import errors

from investigation_engine.application.investigations import InvestigateQuestion
from investigation_engine.infrastructure.api.app import create_app
from investigation_engine.infrastructure.generation import gemini_investigation_model as transport
from investigation_engine.infrastructure.generation.gemini_diagnostics import quota_details
from investigation_engine.infrastructure.generation.gemma4_investigation_model import (
    GEMMA4_MODEL,
    Gemma4InvestigationModel,
)


def quota_error(quota_id=None, retry_after=None):
    entries = []
    if quota_id:
        entries.append(
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [{"quotaId": quota_id, "description": "private-provider-body"}],
            }
        )
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return errors.APIError(
        429,
        {"error": {"status": "RESOURCE_EXHAUSTED", "details": entries}},
        response=httpx.Response(429, headers=headers),
    )


@pytest.mark.parametrize(
    "quota_id,scope,metric",
    [
        ("GenerateContentInputTokensPerModelPerMinute", "minute", "tokens"),
        ("GenerateRequestsPerMinutePerProjectPerModel", "minute", "requests"),
        ("GenerateRequestsPerDayPerProjectPerModel", "daily", "requests"),
        (None, "unknown", None),
    ],
)
def test_quota_classification_uses_only_structured_details(quota_id, scope, metric):
    value = quota_details(quota_error(quota_id))
    assert value == {"quota_scope": scope, "quota_metric": metric, "retry_after_seconds": None}


def test_generic_quota_exceeded_does_not_claim_a_daily_reset():
    error = errors.APIError(
        429,
        {
            "error": {
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "QUOTA_EXCEEDED"},
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "12.5s"},
                ]
            }
        },
    )
    assert quota_details(error) == {"quota_scope": "unknown", "quota_metric": None, "retry_after_seconds": 12.5}


@pytest.mark.parametrize(
    "daily_error",
    [
        quota_error("GenerateRequestsPerDayPerProjectPerModel", "1"),
        errors.APIError(
            429,
            {
                "error": {
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                            "violations": [{"quotaMetric": "requests", "quotaUnit": "1/day/{project}"}],
                        }
                    ]
                }
            },
            response=httpx.Response(429, headers={"Retry-After": "1"}),
        ),
    ],
)
async def test_daily_quota_with_retry_hint_does_not_retry(monkeypatch, daily_error):
    api = AsyncMock(side_effect=daily_error)
    fake = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=api), aclose=AsyncMock()), close=Mock()
    )
    monkeypatch.setattr(transport.genai, "Client", Mock(return_value=fake))
    model = Gemma4InvestigationModel(api_key="key", model=GEMMA4_MODEL)
    from investigation_engine.application.investigations import InvestigationTurn
    from investigation_engine.application.ports.generation_errors import GenerationRateLimitError

    try:
        with pytest.raises(GenerationRateLimitError) as caught:
            await model.next_action(InvestigationTurn(question="Q", exchanges=()))
        assert caught.value.quota_scope == "daily" and api.await_count == 1
    finally:
        await model.aclose()


@pytest.mark.parametrize(
    "quota_id,delay,code,scope",
    [
        ("GenerateContentInputTokensPerModelPerMinute", "12", "generation_rate_limit", "minute"),
        ("GenerateRequestsPerMinutePerProjectPerModel", None, "generation_rate_limit", "minute"),
        ("GenerateRequestsPerDayPerProjectPerModel", None, "generation_daily_quota", "daily"),
        (None, None, "generation_quota_exceeded", "unknown"),
    ],
)
async def test_provider_limit_is_preserved_through_investigation_endpoint(
    monkeypatch, caplog, quota_id, delay, code, scope
):
    api = AsyncMock(side_effect=quota_error(quota_id, delay))
    fake = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=api), aclose=AsyncMock()), close=Mock()
    )
    monkeypatch.setattr(transport.genai, "Client", Mock(return_value=fake))
    model = Gemma4InvestigationModel(api_key="private-key", model=GEMMA4_MODEL, retry_max_attempts=1)
    service = InvestigateQuestion(SimpleNamespace(execute=AsyncMock()), model)
    services = SimpleNamespace(settings=SimpleNamespace(investigation_enabled=True), investigations=service)

    @asynccontextmanager
    async def factory():
        yield services

    app = create_app(service_factory=factory)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                result = await client.post("/investigations", json={"question": "private question"})
        assert result.status_code == 429 and result.json()["error"]["code"] == code
        assert result.json()["error"]["quota_scope"] == scope
        event = next(r for r in caplog.records if getattr(r, "event", None) == "generation.provider_failed")
        assert event.http_status == 429 and event.quota_scope == scope
        assert "private" not in result.text
        if delay:
            assert 0 <= result.json()["error"]["retry_after_seconds"] <= int(delay)
            assert result.headers["Retry-After"] == str(result.json()["error"]["retry_after_seconds"])
        else:
            assert "retry_after_seconds" not in result.json()["error"] and "Retry-After" not in result.headers
        assert api.await_count == 1
    finally:
        await model.aclose()
