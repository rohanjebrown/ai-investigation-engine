"""Classify SDK failures without retaining provider messages or response bodies."""

import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http import HTTPStatus


def error_details(error):
    code = getattr(error, "status", None)
    message = str(getattr(error, "message", "")).casefold()
    if "thought_signature" in message or "thought signature" in message:
        reason = (
            "missing_thought_signature"
            if any(word in message for word in ("missing", "required", "include"))
            else "invalid_tool_history"
        )
    elif "function" in message and "response" in message and any(word in message for word in ("order", "match", "id")):
        reason = "function_response_mismatch"
    elif "function" in message and any(word in message for word in ("order", "history", "turn")):
        reason = "invalid_tool_history"
    elif getattr(error, "code", None) == HTTPStatus.TOO_MANY_REQUESTS:
        reason = "quota_or_rate_limit"
    elif "unsupported" in message or "not supported" in message:
        reason = "unsupported_configuration"
    elif code == "INVALID_ARGUMENT":
        reason = "invalid_argument"
    else:
        reason = "provider_rejection"
    return {"provider_code": code, "error_reason": reason}


def quota_details(error):
    """Extract only structured, bounded quota labels and retry hints."""
    details = getattr(error, "details", None)
    envelope = details.get("error", details) if isinstance(details, dict) else {}
    entries = envelope.get("details", []) if isinstance(envelope, dict) else []
    scopes, metrics, hints = set(), set(), []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        if entry.get("@type") == "type.googleapis.com/google.rpc.QuotaFailure":
            violations = entry.get("violations", [])
            for violation in violations if isinstance(violations, list) else []:
                if not isinstance(violation, dict):
                    continue
                parts = [violation.get(key, "") for key in ("quotaId", "quotaMetric", "quotaUnit")]
                label = " ".join(part.casefold() for part in parts if isinstance(part, str))
                if "perday" in label or "/d/" in label or "/day" in label:
                    scopes.add("daily")
                if "perminute" in label or "/min/" in label or "/minute" in label:
                    scopes.add("minute")
                if "token" in label:
                    metrics.add("tokens")
                elif "request" in label:
                    metrics.add("requests")
        if entry.get("@type") == "type.googleapis.com/google.rpc.RetryInfo":
            value = entry.get("retryDelay")
            if isinstance(value, str) and value.endswith("s"):
                try:
                    hints.append(float(value[:-1]))
                except ValueError:
                    pass
    headers = getattr(getattr(error, "response", None), "headers", None)
    raw = headers.get("Retry-After") if headers is not None else None
    if isinstance(raw, str):
        try:
            hints.append(float(raw))
        except ValueError:
            try:
                date = parsedate_to_datetime(raw)
                if date.tzinfo is not None:
                    hints.append(max(0.0, (date - datetime.now(UTC)).total_seconds()))
            except TypeError, ValueError, OverflowError:
                pass
    valid = [value for value in hints if math.isfinite(value) and 0 <= value <= 604800]
    return {
        "quota_scope": "daily" if "daily" in scopes else "minute" if "minute" in scopes else "unknown",
        "quota_metric": next(iter(metrics)) if len(metrics) == 1 else None,
        "retry_after_seconds": max(valid) if valid else None,
    }
