"""Immutable transcript and result records for investigations."""

import json
from dataclasses import dataclass, field
from math import isfinite

from investigation_engine.application.investigation_decisions import ConflictClaim
from investigation_engine.application.investigation_values import (
    SEARCH_EVIDENCE_TOOL_NAME,
    InvestigationStatus,
    SearchDisposition,
    TokenUsage,
)
from investigation_engine.application.retrieval import Evidence


@dataclass(frozen=True, slots=True, kw_only=True)
class IdentifiedEvidence:
    evidence_id: str
    evidence: Evidence


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchExchange:
    query: str = field(repr=False)
    call_id: str
    evidence: tuple[IdentifiedEvidence, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class InvestigationTurn:
    question: str = field(repr=False)
    exchanges: tuple[SearchExchange, ...]
    final_only: bool = False

    def search_responses(self) -> list[dict]:
        """Prefix-stable wire presentation; keep full evidence on the records.

        Canonical ranges are introduced once, in conversation order. Rendering
        a longer history must not compact an earlier function response anew.
        """
        first_seen = {}
        responses = []
        for sequence, exchange in enumerate(self.exchanges, 1):
            results, new_ids, previous_ids = [], [], []
            for reference in exchange.evidence:
                hit = reference.evidence
                canonical = (hit.content_sha256, hit.start, hit.end)
                if canonical in first_seen:
                    original, first_search = first_seen[canonical]
                    evidence_id = original.evidence_id
                    payload = evidence_payload(original)
                    payload.pop("excerpt")
                    payload.update(evidence_id=evidence_id, previously_available=True, first_seen_search=first_search)
                    previous_ids.append(evidence_id)
                else:
                    payload = evidence_payload(reference)
                    first_seen[canonical] = (reference, sequence)
                    new_ids.append(reference.evidence_id)
                results.append(payload)
            responses.append(
                {
                    "query": exchange.query,
                    "results": results,
                    "new_evidence_ids": new_ids,
                    "previously_available_evidence_ids": previous_ids,
                    "no_new_evidence": not new_ids,
                }
            )
        return responses

    def context_json(self) -> str:
        """Bounded, provider-neutral representation of the complete tool transcript."""
        return json.dumps(
            {
                "question": self.question,
                "final_only": self.final_only,
                "searches": self.search_responses(),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchRecord:
    sequence: int
    tool_name: str
    query: str = field(repr=False)
    evidence_ids: tuple[str, ...]
    result_count: int
    duration_ms: float
    succeeded: bool
    disposition: SearchDisposition

    def __post_init__(self) -> None:
        if self.sequence < 1 or self.tool_name != SEARCH_EVIDENCE_TOOL_NAME or self.result_count < 0:
            raise ValueError("Invalid investigation search record")
        if not isfinite(self.duration_ms) or self.duration_ms < 0:
            raise ValueError("Invalid search duration")


@dataclass(frozen=True, slots=True, kw_only=True)
class InvestigationResult:
    status: InvestigationStatus
    answer: str | None = field(repr=False)
    citations: tuple[str, ...]
    conflicts: tuple[ConflictClaim, ...]
    evidence: tuple[IdentifiedEvidence, ...]
    searches: tuple[SearchRecord, ...]
    generation_requests: int
    embedding_requests: int
    message: str | None = None
    model_version: str | None = None
    usage: TokenUsage = TokenUsage()


def evidence_payload(reference: IdentifiedEvidence) -> dict:
    hit = reference.evidence
    # The model reasons over whole excerpts and human-readable source context.
    # IDs, hashes, offsets and locators remain on Evidence for citation resolution.
    source = {
        "kind": hit.source.kind.value,
        "revision": hit.source.revision,
        "display_name": hit.source.display_name,
    }
    # Free-form metadata may supply source qualifiers outside the dataset case.
    # Keep it when present rather than assuming every corpus uses it as boilerplate.
    if hit.source.metadata:
        source["metadata"] = hit.source.metadata
    return {
        "evidence_id": reference.evidence_id,
        "title": hit.title,
        "excerpt": hit.text,
        "source": source,
    }
