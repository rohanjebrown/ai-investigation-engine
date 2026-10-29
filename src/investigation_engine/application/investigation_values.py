"""Provider-independent values and validation for evidence-led investigations."""

from dataclasses import dataclass, field
from enum import StrEnum

from investigation_engine.application.ports.generation_errors import GenerationResponseError

MAX_SEARCH_QUERY_CHARS = 500
MAX_TOOL_CALL_ID_CHARS = 128
SEARCH_EVIDENCE_TOOL_NAME = "search_evidence"


class InvestigationStatus(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    INCOMPLETE = "incomplete"


class SearchDisposition(StrEnum):
    SUCCEEDED = "succeeded"
    EMPTY = "empty"
    FAILED = "failed"
    REPEATED_QUERY = "repeated_query"
    SEARCH_LIMIT = "search_limit"
    EMBEDDING_LIMIT = "embedding_limit"
    EVIDENCE_LIMIT = "evidence_limit"
    CONTEXT_LIMIT = "context_limit"


@dataclass(frozen=True, slots=True, kw_only=True)
class TokenUsage:
    prompt_tokens: int = 0
    candidate_tokens: int = 0
    thinking_tokens: int = 0

    def __post_init__(self) -> None:
        if any(
            type(token_count) is not int  # noqa: WPS516 - reject bool and integer subclasses
            or token_count < 0
            for token_count in (
                self.prompt_tokens,
                self.candidate_tokens,
                self.thinking_tokens,
            )
        ):
            raise GenerationResponseError("Invalid provider token usage")


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchEvidenceCall:
    query: str = field(repr=False)
    call_id: str
    usage: TokenUsage = TokenUsage()
    model_version: str | None = None

    def __post_init__(self) -> None:
        validate_search_query(self.query)
        if not isinstance(self.call_id, str):
            raise GenerationResponseError("Tool call has an invalid call identifier")
        if not self.call_id or len(self.call_id) > MAX_TOOL_CALL_ID_CHARS:
            raise GenerationResponseError("Tool call has an invalid call identifier")


def validate_search_query(query: str) -> None:
    # Keep type, blank-text, and size checks in their original short-circuit order.
    if not isinstance(query, str):
        raise GenerationResponseError("Search query must contain 1-500 characters")
    if not query.strip():
        raise GenerationResponseError("Search query must contain 1-500 characters")
    if len(query) > MAX_SEARCH_QUERY_CHARS:
        raise GenerationResponseError("Search query must contain 1-500 characters")
    try:
        query.encode("utf-8")
    except UnicodeEncodeError:
        raise GenerationResponseError("Search query must be valid UTF-8 text") from None
