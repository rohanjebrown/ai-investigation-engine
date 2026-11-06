"""Explicit sentence coverage and descriptive benchmark metrics (no model judge)."""

import math
import re
import string
from collections import Counter
from dataclasses import asdict
from statistics import mean, median

from investigation_engine.evaluation.retrieval import covered

ANSWER_SCORER = "2wiki-original-text-em-f1-v1"
ANSWER_SCORER_SOURCE = "https://github.com/Alab-NII/2wikimultihop/blob/main/2wikimultihop_evaluate.py"


def normalize_answer(value):
    """Original 2Wiki text scorer: lower, ASCII punctuation, articles, whitespace."""
    value = "".join(char for char in value.lower() if char not in string.punctuation)
    return " ".join(re.sub(r"\b(a|an|the)\b", " ", value).split())


def answer_scores(answer, reference):
    if reference is None:
        return {"exact_match": None, "f1": None}
    prediction, gold = normalize_answer(answer or ""), normalize_answer(reference)
    exact = float(prediction == gold) if answer is not None else 0.0
    if (prediction in ("yes", "no", "noanswer") or gold in ("yes", "no", "noanswer")) and prediction != gold:
        return {"exact_match": exact, "f1": 0.0}
    a, b = prediction.split(), gold.split()
    common = sum((Counter(a) & Counter(b)).values())
    f1 = 2 * common / (len(a) + len(b)) if common else 0.0
    return {"exact_match": exact, "f1": f1}


def statistics(values):
    values = sorted(value for value in values if value is not None)
    if not values:
        return dict.fromkeys(("count", "minimum", "median", "mean", "p90", "p95", "maximum"), None) | {"count": 0}
    return {
        "count": len(values),
        "minimum": values[0],
        "median": median(values),
        "mean": mean(values),
        "p90": values[math.ceil(0.90 * len(values)) - 1],
        "p95": values[math.ceil(0.95 * len(values)) - 1],
        "maximum": values[-1],
    }


def fact_covered(hit, gold, *, strict=False):
    if gold.get("mapping_error"):
        return False
    if strict:
        return covered(hit, gold)
    # The dataset extractor verified sentence offsets against exact canonical
    # text. A full contained interval counts even through another occurrence.
    return hit.content_sha256 == gold["content_sha256"] and hit.start <= gold["start"] < gold["end"] <= hit.end


def retrieval_metrics(hits, gold, *, candidates=(), availability=None, final_observed=True):
    """Every original gold fact remains in the denominator, including unresolved ones.

    Rank positions are actual returned positions; duplicates consume their slot
    but cannot award the same fact twice. No title-only credit is possible.
    """
    matches = [[i for i, fact in enumerate(gold) if fact_covered(hit, fact)] for hit in hits]
    candidate_matches = {i for hit in candidates for i, fact in enumerate(gold) if fact_covered(hit, fact)}
    output = {"gold_count": len(gold), "at_k": {}, "supporting_facts": []}
    for k in (5, 20):
        found = {i for match in matches[:k] for i in match}
        strict_found = {i for hit in hits[:k] for i, fact in enumerate(gold) if fact_covered(hit, fact, strict=True)}
        output["at_k"][str(k)] = {
            "recall": len(found) / len(gold) if gold else None,
            "complete": bool(gold) and len(found) == len(gold),
            "strict_occurrence_recall": len(strict_found) / len(gold) if gold else None,
        }
    output["mrr_at_20"] = next((1 / rank for rank, match in enumerate(matches[:20], 1) if match), 0.0) if gold else None
    canonical = {(hit.content_sha256, hit.start, hit.end) for hit in hits}
    output["exact_canonical_duplicate_rate"] = (len(hits) - len(canonical)) / len(hits) if hits else 0.0
    output["distinct_source_documents"] = len({hit.document_id for hit in hits})
    output["distinct_canonical_contents"] = len({hit.content_sha256 for hit in hits})
    output["near_duplicate_rate"] = None
    for i, fact in enumerate(gold):
        ranks = [rank for rank, found in enumerate(matches, 1) if i in found]
        present = (availability or {}).get(str(i), "not_checked")
        if fact.get("mapping_error"):
            state = "unresolvable_gold_mapping"
        elif ranks:
            state = "retrieved_final"
        elif i in candidate_matches:
            state = (
                "candidate_only_excluded_from_final" if final_observed else "candidate_only_final_selection_unavailable"
            )
        elif present in ("absent_from_shared_corpus", "present_but_ineligible"):
            state = present
        elif present == "eligible":
            state = "present_not_retrieved"
        else:
            state = "presence_unknown_not_retrieved"
        output["supporting_facts"].append({"gold_index": i, "state": state, "corpus_state": present, "ranks": ranks})
    return output


def evidence_record(hit, rank=None, *, include_text=False):
    result = {
        "evidence_id": f"{hit.document_version_id}:{hit.start}:{hit.end}",
        "canonical_id": f"{hit.content_sha256}:{hit.start}:{hit.end}",
        "document_id": str(hit.document_id),
        "document_version_id": str(hit.document_version_id),
        "content_sha256": hit.content_sha256,
        "start": hit.start,
        "end": hit.end,
        "rank": hit.rank if rank is None else rank,
        "score": hit.score,
        "score_kind": hit.score_kind,
        "semantic_rank": hit.semantic_rank,
        "keyword_rank": hit.keyword_rank,
        "source": {
            "kind": hit.source.kind.value,
            "source_id": hit.source.source_id,
            "revision": hit.source.revision,
            "record_locator": hit.source.record_locator,
        },
    }
    if include_text:
        result.update(text=hit.text, source_spans=[asdict(span) for span in hit.source_spans])
    return result
