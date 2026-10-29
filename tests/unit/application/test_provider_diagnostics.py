import json

import pytest

from investigation_engine.application.ports.provider_diagnostics import (
    attach_diagnostic,
    safe_diagnostic,
    status_category,
)


@pytest.mark.parametrize(
    "status,category",
    [
        (401, "authentication"),
        (403, "permission"),
        (429, "rate_or_quota_limit"),
        (503, "availability"),
        (504, "timeout"),
        (400, "request_rejected"),
    ],
)
def test_status_diagnostics_preserve_only_allowlisted_fields(status, category):
    error = attach_diagnostic(
        RuntimeError("secret document text"),
        operation="generate_content",
        model_id="gemini-3.6-flash",
        http_status=status,
        category=status_category(status),
        retryable=status in (429, 503, 504),
        request_count=2,
    )
    output = safe_diagnostic(error)
    assert output["category"] == category and output["http_status"] == status
    assert output["request_count"] == 2
    assert "secret" not in json.dumps(output)


def test_diagnostics_reject_payload_like_model_strings_and_ignore_unknown_objects():
    error = attach_diagnostic(
        RuntimeError("secret"),
        operation="embed_content",
        model_id="https://example.invalid/?key=secret",
        category="transport",
        request_count=1,
    )
    assert safe_diagnostic(error)["model_id"] == "unrecorded"
    assert safe_diagnostic(RuntimeError("secret")) is None
