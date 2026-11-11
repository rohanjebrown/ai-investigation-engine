"""Allowlisted operational logging for failed Google generation requests."""

import logging
import math
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic, perf_counter

from google.genai import errors

from investigation_engine.application.ports.provider_diagnostics import (
    _safe_error_reason,
    _safe_provider_code,
    safe_diagnostic,
)
from investigation_engine.application.timing import log_event
from investigation_engine.infrastructure.generation.gemini_diagnostics import error_details

_pending_failure = ContextVar("pending_generation_failure", default=None)


@contextmanager
def capture_generation_failure():
    """Let a retry controller attach its decision before the single failure event."""
    captured = []
    token = _pending_failure.set(captured)
    try:
        yield captured
    finally:
        _pending_failure.reset(token)


def log_generation_failure(
    *,
    source_error,
    failure,
    model,
    started,
    turn=None,
    attempt=1,
    max_attempts=1,
    will_retry=False,
    retry_delay_seconds=None,
    retryable_override=None,
):
    """Log safe metadata without serializing the exception, headers, or request."""
    pending = _pending_failure.get()
    if pending is not None:
        pending.append(dict(source_error=source_error, failure=failure, model=model, started=started, turn=turn))
        return
    try:
        _log_generation_failure(
            source_error,
            failure,
            model,
            started,
            turn,
            attempt,
            max_attempts,
            will_retry,
            retry_delay_seconds,
            retryable_override,
        )
    except Exception:
        # Observability must not replace the original provider error.
        pass


def _log_generation_failure(
    source_error,
    failure,
    model,
    started,
    turn,
    attempt,
    max_attempts,
    will_retry,
    retry_delay_seconds,
    retryable_override,
):
    diagnostic = safe_diagnostic(failure) or {}
    details = error_details(source_error) if isinstance(source_error, errors.APIError) else {}
    request_id = None
    try:
        response = getattr(source_error, "response", None)
        headers = getattr(response, "headers", None)
        if headers is not None:
            for name in ("x-request-id", "x-goog-request-id"):
                value = headers.get(name)
                secrets = [v for k, v in os.environ.items() if re.search(r"key|secret|token|password", k, re.I) and v]
                if (
                    isinstance(value, str)
                    and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value)
                    and not value.startswith("AIza")
                    and not any(secret in value for secret in secrets)
                ):
                    request_id = value
                    break
    except Exception:
        pass
    safe_model = model if re.fullmatch(r"[A-Za-z0-9_.\-/]{1,160}", model) else "unrecorded"
    retry_deadline = getattr(failure, "retry_after_deadline", None)
    log_event(
        "generation.provider_failed",
        level=logging.WARNING,
        succeeded=False,
        provider="gemma4" if model.startswith("gemma-") else "gemini",
        model_id=diagnostic.get("model_id", safe_model),
        http_status=diagnostic.get("http_status"),
        provider_code=diagnostic.get("provider_code") or _safe_provider_code(details.get("provider_code")),
        error_reason=diagnostic.get("error_reason") or _safe_error_reason(details.get("error_reason")),
        error_category=diagnostic.get("category"),
        exception_type=type(source_error).__name__,
        duration_ms=(perf_counter() - started) * 1000,
        turn_number=turn,
        retryable=diagnostic.get("retryable") if retryable_override is None else retryable_override,
        provider_request_id=request_id,
        quota_scope=getattr(failure, "quota_scope", None),
        quota_metric=getattr(failure, "quota_metric", None),
        retry_after_seconds=max(0, math.ceil(retry_deadline - monotonic())) if retry_deadline is not None else None,
        attempt=attempt,
        max_attempts=max_attempts,
        will_retry=will_retry,
        retry_delay_seconds=retry_delay_seconds,
    )
