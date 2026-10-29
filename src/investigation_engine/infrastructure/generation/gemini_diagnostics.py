"""Classify SDK failures without retaining provider messages or response bodies."""

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
