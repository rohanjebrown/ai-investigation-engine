"""Application-owned Reciprocal Rank Fusion over source-version occurrences."""

from collections.abc import Sequence
from dataclasses import replace

from investigation_engine.application.retrieval import Evidence, validate_search_scope


def validate_rrf_k(k: int) -> None:
    if type(k) is not int or not 0 <= k <= 10000:
        raise ValueError("RRF k must be an integer between 0 and 10000")


def reciprocal_rank_fusion(
    semantic: Sequence[Evidence], keyword: Sequence[Evidence], *, limit: int, k: int = 60,
) -> tuple[Evidence, ...]:
    validate_search_scope(None, limit)
    validate_rrf_k(k)
    candidates = {}
    scores = {}
    channels = {}
    for name, expected_kind, results in (("semantic", "cosine_similarity", semantic), ("keyword", "keyword_rank", keyword)):
        seen = set()
        for item in results:
            if item.score_kind != expected_kind:
                raise ValueError("RRF input has an incompatible channel score kind")
            key = item.identity
            previous = candidates.get(key)
            if previous is not None and (
                previous.document_id, previous.content_sha256, previous.text, previous.source, previous.source_spans, previous.title
            ) != (item.document_id, item.content_sha256, item.text, item.source, item.source_spans, item.title):
                raise ValueError("Retrieval channels disagree on the same evidence occurrence")
            if key in seen:
                continue
            seen.add(key)
            rank = len(seen)  # First occurrence, compact independent channel ranks.
            candidates.setdefault(key, item)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            channels.setdefault(key, {}).update({f"{name}_score": item.score, f"{name}_rank": rank})
    ordered = sorted(candidates, key=lambda key: (
        -scores[key], candidates[key].document_id, key[0], key[1], key[2],
    ))
    return tuple(
        replace(candidates[key], score=scores[key], rank=rank, score_kind="rrf", **{
            "semantic_score": None, "keyword_score": None, "semantic_rank": None, "keyword_rank": None, **channels[key],
        }) for rank, key in enumerate(ordered[:limit], 1)
    )
