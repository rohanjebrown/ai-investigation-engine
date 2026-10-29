"""Application-owned Reciprocal Rank Fusion over source-version occurrences."""

from collections.abc import Sequence
from dataclasses import replace

from investigation_engine.application.retrieval import Evidence, validate_search_scope

MAX_RRF_K = 10_000


# `k` is the conventional RRF smoothing parameter name.
def validate_rrf_k(k: int) -> None:  # noqa: WPS111
    # Keep the established RRF symbol; exact type rejects bool as a smoothing value.
    if type(k) is not int or not 0 <= k <= MAX_RRF_K:  # noqa: WPS111, WPS516
        raise ValueError(f"RRF k must be an integer between 0 and {MAX_RRF_K}")


class _FusionAccumulator:
    """Collect unique occurrences and their independent channel ranks."""

    def __init__(self, smoothing: int) -> None:
        self._smoothing = smoothing
        self._candidates = {}
        self._scores = {}
        self._channels = {}

    def add_channel(
        self,
        name: str,
        expected_kind: str,
        channel_results: Sequence[Evidence],
    ) -> None:
        seen = set()
        for evidence in channel_results:
            self._add_evidence(name, expected_kind, evidence, seen)

    def ranked(self, limit: int) -> tuple[Evidence, ...]:
        ordered_keys = sorted(
            self._candidates,
            key=lambda evidence_key: (
                -self._scores[evidence_key],
                self._candidates[evidence_key].document_id,
                evidence_key[0],
                evidence_key[1],
                evidence_key[2],
            ),
        )
        return tuple(
            replace(
                self._candidates[evidence_key],
                score=self._scores[evidence_key],
                rank=rank,
                score_kind="rrf",
                **{
                    "semantic_score": None,
                    "keyword_score": None,
                    "semantic_rank": None,
                    "keyword_rank": None,
                    **self._channels[evidence_key],
                },
            )
            for rank, evidence_key in enumerate(ordered_keys[:limit], 1)
        )

    def _add_evidence(self, name: str, expected_kind: str, evidence: Evidence, seen: set) -> None:
        if evidence.score_kind != expected_kind:
            raise ValueError("RRF input has an incompatible channel score kind")
        evidence_key = evidence.identity
        previous = self._candidates.get(evidence_key)
        if previous is not None and not _same_evidence_payload(previous, evidence):
            raise ValueError("Retrieval channels disagree on the same evidence occurrence")
        if evidence_key in seen:
            return
        seen.add(evidence_key)
        rank = len(seen)
        self._candidates.setdefault(evidence_key, evidence)
        previous_score = self._scores.get(evidence_key, 0)
        reciprocal_rank = 1.0 / (self._smoothing + rank)
        self._scores[evidence_key] = previous_score + reciprocal_rank
        # Keep each channel's score and rank together for the final evidence.
        channel_values = {f"{name}_score": evidence.score, f"{name}_rank": rank}
        self._channels.setdefault(evidence_key, {}).update(channel_values)


def reciprocal_rank_fusion(
    semantic: Sequence[Evidence],
    keyword: Sequence[Evidence],
    *,
    limit: int,
    k: int = 60,  # noqa: WPS111
) -> tuple[Evidence, ...]:
    validate_search_scope(None, limit)
    validate_rrf_k(k)
    accumulator = _FusionAccumulator(k)
    accumulator.add_channel("semantic", "cosine_similarity", semantic)
    accumulator.add_channel("keyword", "keyword_rank", keyword)
    return accumulator.ranked(limit)


def _same_evidence_payload(left: Evidence, right: Evidence) -> bool:
    return (
        left.document_id,
        left.content_sha256,
        left.text,
        left.source,
        left.source_spans,
        left.title,
    ) == (
        right.document_id,
        right.content_sha256,
        right.text,
        right.source,
        right.source_spans,
        right.title,
    )
