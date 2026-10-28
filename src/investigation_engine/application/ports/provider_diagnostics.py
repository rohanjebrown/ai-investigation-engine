"""Small, allowlisted provider diagnostics; never copy SDK messages or bodies."""

from dataclasses import asdict, dataclass
import re


@dataclass(frozen=True, slots=True)
class ProviderDiagnostic:
    """request_count is cumulative for this operation on the client instance.

    It is an observation for the failure, not a value to sum across reports.
    A 429 distinguishes rate/quota rejection but does not identify which limit.
    """
    operation: str
    model_id: str
    http_status: int | None
    category: str
    retryable: bool
    rate_or_quota_limit: bool
    request_count: int
    provider_code: str | None = None
    error_reason: str | None = None
    investigation_step: int | None = None


def status_category(status):
    if status == 429:
        return "rate_or_quota_limit"
    if status == 401:
        return "authentication"
    if status == 403:
        return "permission"
    if status in (408, 504):
        return "timeout"
    if type(status) is int and 500 <= status <= 599:
        return "availability"
    return "request_rejected"


def attach_diagnostic(error, *, operation, model_id, http_status=None,
                      category, retryable=False, request_count, provider_code=None,
                      error_reason=None, investigation_step=None):
    if operation not in ("generate_content", "count_tokens", "embed_content"):
        raise ValueError("Unknown provider operation")
    if category not in ("rate_or_quota_limit", "authentication", "permission", "timeout",
                        "availability", "request_rejected", "transport", "invalid_response"):
        raise ValueError("Unknown safe provider error category")
    status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
    model = model_id if isinstance(model_id, str) and re.fullmatch(r"[a-zA-Z0-9_.\-/]{1,160}", model_id) else "unrecorded"
    if type(request_count) is not int or request_count < 0:
        raise ValueError("Invalid provider request count")
    codes = {"INVALID_ARGUMENT", "FAILED_PRECONDITION", "OUT_OF_RANGE", "UNAUTHENTICATED",
             "PERMISSION_DENIED", "NOT_FOUND", "RESOURCE_EXHAUSTED", "INTERNAL", "UNAVAILABLE",
             "DEADLINE_EXCEEDED", "CANCELLED", "UNKNOWN"}
    reasons = {"missing_thought_signature", "function_response_mismatch", "invalid_tool_history",
               "unsupported_configuration", "quota_or_rate_limit", "invalid_argument",
               "provider_rejection", "missing_native_history", "history_mismatch", "invalid_response"}
    error.diagnostic = ProviderDiagnostic(operation, model, status, category,
        bool(retryable), category == "rate_or_quota_limit", request_count,
        provider_code if isinstance(provider_code, str) and provider_code in codes else None,
        error_reason if isinstance(error_reason, str) and error_reason in reasons else None,
        investigation_step if type(investigation_step) is int and investigation_step > 0 else None)
    return error


def safe_diagnostic(error):
    value = getattr(error, "diagnostic", None)
    return asdict(value) if isinstance(value, ProviderDiagnostic) else None
