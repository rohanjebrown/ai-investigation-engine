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
    if type(limit) is not int or not 1 <= limit <= MAX_EVIDENCE_RESULTS:
        raise ValueError("Evidence limit must be an integer between 1 and 100")
    if owner_id is not None and (not isinstance(owner_id, str) or not owner_id.strip()):
        raise ValueError("Trusted owner scope must be a nonblank principal or None")


def intersecting_source_spans(spans: tuple[SourceSpan, ...], start: int, end: int) -> tuple[SourceSpan, ...]:
    """Retain original spans/locators in order, without claiming full retrieval.

    The intersection with the evidence range is [max(start,s.start),min(end,s.end)).
    Keeping original bounds makes partial source-span coverage explicit.
    """
    return tuple(span for span in spans if span.start < end and start < span.end)


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
        if type(self.start) is not int or type(self.end) is not int or not 0 <= self.start < self.end:
            raise ValueError("Evidence requires a nonempty canonical character range")
        if len(self.text) != self.end - self.start:
            raise ValueError("Evidence text does not match its canonical range")
        if type(self.rank) is not int or self.rank < 1 or not isfinite(self.score):
            raise ValueError("Evidence requires a positive rank and finite score")
        if self.score_kind not in ("cosine_similarity", "keyword_rank", "rrf"):
            raise ValueError("Unknown evidence score kind")
        if any(value is not None and not isfinite(value) for value in (self.semantic_score, self.keyword_score)):
            raise ValueError("Channel scores must be finite")
        if any(value is not None and (type(value) is not int or value < 1) for value in (self.semantic_rank, self.keyword_rank)):
            raise ValueError("Channel ranks must be positive integers")
        object.__setattr__(self, "source", replace(self.source))

    @property
    def identity(self) -> tuple[UUID, int, int]:
        """One source-version occurrence/range; no semantic/content collapse."""
        return self.document_version_id, self.start, self.end
