"""Provider-independent generation failures; messages must not contain payloads."""

from time import monotonic


class GenerationProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class GenerationRateLimitError(GenerationProviderError):
    """Provider rejected a request because of rate or quota limits."""

    def __init__(self, message, *, retryable=False, quota_scope="unknown", quota_metric=None, retry_after_seconds=None):
        super().__init__(message, retryable=retryable)
        self.quota_scope = quota_scope if quota_scope in ("minute", "daily") else "unknown"
        self.quota_metric = quota_metric if quota_metric in ("tokens", "requests") else None
        self.retry_after_deadline = (
            monotonic() + retry_after_seconds
            if type(retry_after_seconds) in (int, float) and 0 <= retry_after_seconds <= 604800
            else None
        )


class GenerationTimeoutError(GenerationProviderError):
    """Provider request exceeded its configured time limit."""


class GenerationResponseError(RuntimeError):
    """Malformed output or structurally invalid citations; never abstention."""


class EvidenceContextLimitError(RuntimeError):
    """Retrieved evidence exists but no intact item fits the configured budget."""
