"""Embedding failures that do not expose provider SDK types or raw payloads."""


class EmbeddingInputError(ValueError):
    """An input is invalid or exceeds the provider's complete-input limit."""


class EmbeddingRateLimitError(RuntimeError):
    """The provider rejected a request because of a rate or quota limit."""


class EmbeddingProviderError(RuntimeError):
    """Transport, authentication, availability or another provider failure."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class EmbeddingResponseError(RuntimeError):
    """The provider returned an unusable token count or embedding response."""
