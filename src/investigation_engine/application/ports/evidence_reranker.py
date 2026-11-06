"""Optional ordering step; retrieval evidence and scores remain authoritative."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from investigation_engine.application.retrieval import Evidence


class RerankingError(Exception):
    """Safe diagnostic category, never a provider response body or credential."""


@dataclass(frozen=True, slots=True)
class RerankedEvidence:
    evidence: Evidence
    relevance_score: float


class EvidenceReranker(Protocol):
    async def rerank(self, query: str, candidates: Sequence[Evidence]) -> tuple[RerankedEvidence, ...]:
        """Return every original object once, in descending relevance order.

        Keep provider scores separate from original scores/ranks/provenance.
        Raise RerankingError when ordering is unavailable.
        """
        ...
