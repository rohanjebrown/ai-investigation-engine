"""Attributable evidence values and provider/storage-independent search checks."""

from dataclasses import dataclass, field, replace
from math import isfinite
from typing import Literal
from uuid import UUID

from investigation_engine.domain.provenance import SourceReference
from investigation_engine.domain.source_span import SourceSpan

MAX_EVIDENCE_RESULTS = 100


def validate_search_question(question: str) -> None:
    if not isinstance(question, str) or not question.strip():
        raise ValueError("Question must be nonblank text")
    try:
        question.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Question must be valid UTF-8 text") from None


def validate_search_scope(owner_id: str | None, limit: int) -> None:
    # Reject bool, since bool subclasses int.
    if type(limit) is not int or not 1 <= limit <= MAX_EVIDENCE_RESULTS:  # noqa: WPS516
        raise ValueError("Evidence limit must be an integer between 1 and 100")
    # Keep trusted-scope type and blank-value checks short-circuiting together.
    if owner_id is not None and (not isinstance(owner_id, str) or not owner_id.strip()):  # noqa: WPS221
        raise ValueError("Trusted owner scope must be a nonblank principal or None")


def intersecting_source_spans(
    spans: tuple[SourceSpan, ...],
    start: int,
    end: int,
) -> tuple[SourceSpan, ...]:
    """Retain original spans/locators in order, without claiming full retrieval.

    The intersection with the evidence range is [max(start,s.start),min(end,s.end)).
    Keeping original bounds makes partial source-span coverage explicit.
    """
    # This short projection keeps the two half-open range checks adjacent.
    return tuple(span for span in spans if span.start < end and start < span.end)  # noqa: WPS221


@dataclass(frozen=True, slots=True, kw_only=True)
class Evidence:
    document_id: UUID
    document_version_id: UUID
    content_sha256: str
    start: int
    end: int
    text: str = field(repr=False)
    source: SourceReference = field(repr=False)
    source_spans: tuple[SourceSpan, ...]
    score: float
    rank: int
    title: str | None = None
    score_kind: Literal["cosine_similarity", "keyword_rank", "rrf"] = "cosine_similarity"
    semantic_score: float | None = None
    keyword_score: float | None = None
    semantic_rank: int | None = None
    keyword_rank: int | None = None

    def __post_init__(self) -> None:
        self._validate_range()
        self._validate_rank_and_score_kind()
        self._validate_channel_metadata()
        object.__setattr__(self, "source", replace(self.source))

    @property
    def identity(self) -> tuple[UUID, int, int]:
        """One source-version occurrence/range; no semantic/content collapse."""
        return self.document_version_id, self.start, self.end

    def _validate_range(self) -> None:
        # Exact integer checks reject bool; the comparison states the range invariant.
        if type(self.start) is not int or type(self.end) is not int or not 0 <= self.start < self.end:  # noqa: WPS221, WPS516
            raise ValueError("Evidence requires a nonempty canonical character range")
        if len(self.text) != self.end - self.start:
            raise ValueError("Evidence text does not match its canonical range")

    def _validate_rank_and_score_kind(self) -> None:
        # Rank and score validation are one evidence invariant.
        if type(self.rank) is not int or self.rank < 1 or not isfinite(self.score):  # noqa: WPS221, WPS516
            raise ValueError("Evidence requires a positive rank and finite score")
        if self.score_kind not in ("cosine_similarity", "keyword_rank", "rrf"):
            raise ValueError("Unknown evidence score kind")

    def _validate_channel_metadata(self) -> None:
        channel_scores = (self.semantic_score, self.keyword_score)
        # Keep one finite-value predicate for both retrieval channels.
        has_non_finite_channel_score = any(score is not None and not isfinite(score) for score in channel_scores)  # noqa: WPS221
        if has_non_finite_channel_score:
            raise ValueError("Channel scores must be finite")
        # Exact integer checks reject bool, which subclasses int.
        if any(
            channel_rank is not None and (type(channel_rank) is not int or channel_rank < 1)  # noqa: WPS516
            for channel_rank in (self.semantic_rank, self.keyword_rank)
        ):
            raise ValueError("Channel ranks must be positive integers")
