"""Observe actual Quick Answer preparation separately from ranked retrieval."""

import re
from collections import Counter

from investigation_engine.evaluation.metrics import evidence_record, fact_covered, retrieval_metrics


class LexicalSearch:
    """Use the application's lexical route through Quick Answer's search port."""

    def __init__(self, search):
        self.search = search

    async def execute(self, question, limit, *, owner_id):
        return await self.search.execute_keywords(question, limit, owner_id=owner_id)


def covered_facts(hits, gold):
    return {i for i, fact in enumerate(gold) if any(fact_covered(hit, fact) for hit in hits)}


def serving_record(prepared, ranked, gold, *, max_evidence, availability=None):
    admitted = tuple(prepared.selected.values())
    raw = tuple(hit for channel in prepared.channels for hit in channel)
    prefix_matches = len(prepared.channels) == 1 and tuple(hit.identity for hit in raw) == tuple(
        hit.identity for hit in ranked[:max_evidence]
    )
    counts = Counter(reason for _, reason in prepared.decisions)
    count_limit = counts["evidence_count"] + max(0, len(ranked) - max_evidence) if prefix_matches else None
    metrics = retrieval_metrics(admitted, gold, availability=availability)
    return {
        "status": "ok" if admitted else "empty_context" if not prepared.balanced else "context_exhausted",
        "boundary": "AnswerQuestion.prepare_evidence; actual serving-limit retrieval, canonical balancing and serialized context admission",
        "max_evidence": max_evidence,
        "query_count": prepared.embedding_requests,
        "would_generate": bool(admitted),
        "admitted_passages": len(admitted),
        "context_chars": len(prepared.request.context_json()) if admitted else 0,
        "serialized_payload_chars": len(prepared.request.context_json()),
        "admitted_text_chars": sum(len(hit.text) for hit in admitted),
        "recall": metrics["at_k"]["20"]["recall"],
        "complete_support": bool(gold) and len(covered_facts(admitted, gold)) == len(gold),
        "distinct_source_documents": len({hit.document_id for hit in admitted}),
        "supporting_facts_lost_from_ranked": len(covered_facts(ranked, gold) - covered_facts(admitted, gold)),
        "rejected_evidence_count_limit": count_limit,
        "admission_count_limit_rejections": counts["evidence_count"],
        "rejected_context_budget": counts["context_budget"],
        "rejected_canonical_duplicates": len(raw) - len(prepared.balanced),
        "retrieval_matches_ranked_prefix": prefix_matches,
        "count_attribution": "Count limit includes the verified serving retrieval cutoff and admission count cap. Null when serving retrieval differs from the ranked prefix; no approximate attribution.",
        "retrieved": [evidence_record(hit, i + 1) for i, hit in enumerate(raw)],
        "admitted": [
            {**evidence_record(hit, i + 1), "citation_id": key}
            for i, (key, hit) in enumerate(prepared.selected.items())
        ],
        "decisions": [
            {"evidence_id": evidence_record(hit)["evidence_id"], "reason": reason} for hit, reason in prepared.decisions
        ],
        "fact_decision_reasons": {
            str(i): sorted({reason for hit, reason in prepared.decisions if fact_covered(hit, fact)})
            for i, fact in enumerate(gold)
        },
        "metrics": metrics,
    }


def pipeline_facts(ranked, channels, gold, serving, availability, *, retrieval_observed):
    ranked_matches = covered_facts(ranked, gold)
    channel_matches = {name: covered_facts(hits, gold) for name, hits in channels.items()}
    admitted_matches = {
        i for i, fact in enumerate((serving or {}).get("metrics", {}).get("supporting_facts", [])) if fact["ranks"]
    }
    result = []
    for i, fact in enumerate(gold):
        corpus_state = availability.get(str(i), "not_checked")
        in_channels = [name for name, matches in channel_matches.items() if i in matches]
        if fact.get("mapping_error"):
            state = "unresolvable_gold_mapping"
        elif i in admitted_matches:
            state = "included_in_admitted_context"
        elif corpus_state in ("absent_from_shared_corpus", "present_but_ineligible"):
            state = corpus_state
        elif not retrieval_observed:
            state = "retrieval_unavailable"
        elif i in ranked_matches:
            state = (
                "ranked_but_serving_unavailable"
                if not serving or serving.get("error")
                else "ranked_excluded_from_serving"
            )
        elif in_channels:
            state = "channel_evidence_excluded_from_ranked_top20"
        else:
            state = (
                "available_not_retrieved_by_channels"
                if corpus_state == "eligible"
                else "presence_unknown_not_retrieved"
            )
        reason = None
        if state == "ranked_excluded_from_serving":
            reasons = set(serving["fact_decision_reasons"][str(i)])
            reason = (
                "context_budget"
                if "context_budget" in reasons
                else "evidence_count_limit"
                if ("evidence_count" in reasons or serving["retrieval_matches_ranked_prefix"])
                else "serving_retrieval_difference_or_canonical_balancing"
            )
        result.append(
            {
                "gold_index": i,
                "corpus_state": corpus_state,
                "channels": in_channels,
                "channel_ranks": {
                    name: [rank for rank, hit in enumerate(hits, 1) if fact_covered(hit, fact)]
                    for name, hits in channels.items()
                },
                "ranked_ranks": [rank for rank, hit in enumerate(ranked, 1) if fact_covered(hit, fact)],
                "in_ranked_top20": i in ranked_matches,
                "in_admitted_context": i in admitted_matches,
                "state": state,
                "exclusion_reason": reason,
            }
        )
    return result


def similar_passages(hits, threshold=0.85):
    """Approximation only: lowercase Unicode word-token 5-shingle Jaccard."""
    shingles = []
    for hit in hits:
        words = re.findall(r"\w+", hit.text.casefold())
        shingles.append({tuple(words[i : i + 5]) for i in range(max(0, len(words) - 4))})
    pairs = []
    for i, first in enumerate(hits):
        for j in range(i + 1, len(hits)):
            second = hits[j]
            if (first.content_sha256, first.start, first.end) == (second.content_sha256, second.start, second.end):
                continue
            a, b = shingles[i], shingles[j]
            if a and b:
                similarity = len(a & b) / len(a | b)
                if similarity >= threshold:
                    pairs.append(
                        {
                            "ranks": [i + 1, j + 1],
                            "evidence_ids": [
                                evidence_record(first)["evidence_id"],
                                evidence_record(second)["evidence_id"],
                            ],
                            "jaccard": similarity,
                        }
                    )
    return {
        "method": "Approximate lowercase Unicode word-token 5-shingle Jaccard; distinct canonical ranges; no relevance judgment",
        "threshold": threshold,
        "pairs": sorted(pairs, key=lambda item: -item["jaccard"])[:10],
    }
