"""Embedding failures that do not expose provider SDK types or raw payloads."""


class EmbeddingInputError(ValueError):
    """An input is invalid or exceeds the provider's complete-input limit."""


class EmbeddingRateLimitError(RuntimeError):
    """The provider rejected a request because of a rate or quota limit."""

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class EmbeddingProviderError(RuntimeError):
    """Transport, authentication, availability or another provider failure."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class EmbeddingResponseError(RuntimeError):
    """The provider returned an unusable token count or embedding response."""
