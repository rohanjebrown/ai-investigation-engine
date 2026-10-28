"""Provider-independent generation failures; messages must not contain payloads."""


class GenerationProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class GenerationRateLimitError(GenerationProviderError):
    pass


class GenerationTimeoutError(GenerationProviderError):
    pass


class GenerationResponseError(RuntimeError):
    """Malformed output or structurally invalid citations; never abstention."""


class EvidenceContextLimitError(RuntimeError):
    """Retrieved evidence exists but no intact item fits the configured budget."""
