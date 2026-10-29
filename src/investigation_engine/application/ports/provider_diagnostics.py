"""Small, allowlisted provider diagnostics; never copy SDK messages or bodies."""

import re
from dataclasses import asdict, dataclass
from http import HTTPStatus

MAX_HTTP_STATUS = 599
_KNOWN_STATUS_CATEGORIES = (
    (HTTPStatus.TOO_MANY_REQUESTS, "rate_or_quota_limit"),
    (HTTPStatus.UNAUTHORIZED, "authentication"),
    (HTTPStatus.FORBIDDEN, "permission"),
    (HTTPStatus.REQUEST_TIMEOUT, "timeout"),
    (HTTPStatus.GATEWAY_TIMEOUT, "timeout"),
)
_ALLOWED_OPERATIONS = ("generate_content", "count_tokens", "embed_content")
_ALLOWED_CATEGORIES = (
    "rate_or_quota_limit",
    "authentication",
    "permission",
    "timeout",
    "availability",
    "request_rejected",
    "transport",
    "invalid_response",
)
_ALLOWED_PROVIDER_CODES = (
    "INVALID_ARGUMENT",
    "FAILED_PRECONDITION",
    "OUT_OF_RANGE",
    "UNAUTHENTICATED",
    "PERMISSION_DENIED",
    "NOT_FOUND",
    "RESOURCE_EXHAUSTED",
    "INTERNAL",
    "UNAVAILABLE",
    "DEADLINE_EXCEEDED",
    "CANCELLED",
    "UNKNOWN",
)
_ALLOWED_ERROR_REASONS = (
    "missing_thought_signature",
    "function_response_mismatch",
    "invalid_tool_history",
    "unsupported_configuration",
    "quota_or_rate_limit",
    "invalid_argument",
    "provider_rejection",
    "missing_native_history",
    "history_mismatch",
    "invalid_response",
)


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
    for known_status, category in _KNOWN_STATUS_CATEGORIES:
        if status == known_status:
            return category

    # Exact ints exclude bool and integer-enum values from diagnostic inputs.
    if type(status) is int and HTTPStatus.INTERNAL_SERVER_ERROR <= status <= MAX_HTTP_STATUS:  # noqa: WPS516
        return "availability"
    return "request_rejected"


def _validate_diagnostic_labels(operation, category):
    if operation not in _ALLOWED_OPERATIONS:
        raise ValueError("Unknown provider operation")
    if category not in _ALLOWED_CATEGORIES:
        raise ValueError("Unknown safe provider error category")


def _valid_http_status(http_status):
    return type(http_status) is int and HTTPStatus.CONTINUE <= http_status <= MAX_HTTP_STATUS  # noqa: WPS516


def _safe_model_id(model_id):
    is_safe = isinstance(model_id, str) and re.fullmatch(r"[a-zA-Z0-9_.\-/]{1,160}", model_id)
    return model_id if is_safe else "unrecorded"


def _validate_request_count(request_count):
    if type(request_count) is not int or request_count < 0:  # noqa: WPS516
        raise ValueError("Invalid provider request count")


def _safe_provider_code(provider_code):
    return provider_code if isinstance(provider_code, str) and provider_code in _ALLOWED_PROVIDER_CODES else None


def _safe_error_reason(error_reason):
    return error_reason if isinstance(error_reason, str) and error_reason in _ALLOWED_ERROR_REASONS else None


def _safe_investigation_step(investigation_step):
    is_valid = type(investigation_step) is int and investigation_step > 0  # noqa: WPS516
    return investigation_step if is_valid else None


def attach_diagnostic(  # noqa: WPS211 - keep the established keyword diagnostic fields API
    error,
    *,
    operation,
    model_id,
    http_status=None,
    category,
    retryable=False,
    request_count,
    provider_code=None,
    error_reason=None,
    investigation_step=None,
):
    _validate_diagnostic_labels(operation, category)
    status = http_status if _valid_http_status(http_status) else None
    model = _safe_model_id(model_id)
    _validate_request_count(request_count)
    error.diagnostic = ProviderDiagnostic(
        operation,
        model,
        status,
        category,
        bool(retryable),
        category == "rate_or_quota_limit",
        request_count,
        _safe_provider_code(provider_code),
        _safe_error_reason(error_reason),
        _safe_investigation_step(investigation_step),
    )
    return error


def safe_diagnostic(error):
    diagnostic = getattr(error, "diagnostic", None)
    return asdict(diagnostic) if isinstance(diagnostic, ProviderDiagnostic) else None
