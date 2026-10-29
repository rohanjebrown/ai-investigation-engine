"""Independent assessment axes and auditable, result-bound source reviews.

This module does not infer semantic entailment or entity identity from names,
gold-fact coverage, or citation IDs. Reviews are explicit offline judgments.
"""

from copy import deepcopy

from investigation_engine.evaluation.retrieval import fingerprint


def review_fingerprint(row: dict) -> str:
    return fingerprint(
        {
            key: row.get(key)
            for key in ("id", "question", "status", "answer", "citations", "citation_evidence", "retrieved_evidence")
        }
    )


def independent_assessment(row: dict) -> dict:
    status = row.get("status")
    answered = status == "answered"
    observed = status in ("answered", "insufficient_evidence", "conflicting_evidence", "incomplete")
    evidence_support = "requires_review" if answered else ("not_applicable" if observed else "unavailable")
    identity = "requires_review" if answered else ("not_applicable" if observed else "unavailable")
    review = row.get("grounding_review")
    if review is not None:
        if (
            answered
            and review.get("result_fingerprint") == review_fingerprint(row)
            and review.get("evidence_support") in ("supported", "unsupported", "unresolved")
            and review.get("entity_identity") in ("established", "different_entities", "unresolved", "not_applicable")
        ):
            evidence_support, identity = review["evidence_support"], review["entity_identity"]
        else:
            raise ValueError("Grounding review does not match this answer and evidence")
    citation_ids = row.get("citations") or []
    known_ids = {item["evidence_id"] for item in row.get("retrieved_evidence") or []}
    if review is None:
        assessment_basis = "semantic_support_not_automatically_scored"
    else:
        assessment_basis = "explicit_source_review"
    identifier_validity = (
        (
            "valid"
            if citation_ids and all(key in known_ids for key in citation_ids)
            else "invalid"
            if citation_ids
            else "not_applicable"
        )
        if observed
        else "unavailable"
    )
    return {
        "answer_correctness": row.get("answer_correctness", "unavailable"),
        "citation_identifier_validity": identifier_validity,
        "gold_supporting_fact_matching": {
            key: (row.get("citation_coverage") or {}).get(key)
            for key in ("strict_fact_count", "strict_precision", "strict_recall")
        },
        "semantic_evidence_support": evidence_support,
        "cross_document_entity_identity": identity,
        "basis": assessment_basis,
    }


def apply_grounding_review(
    row: dict, *, expected_fingerprint: str, evidence_support: str, entity_identity: str, rationale: str, reviewer: str
) -> dict:
    """Attach a review only to the exact saved answer/evidence that was inspected."""
    if row.get("status") != "answered" or review_fingerprint(row) != expected_fingerprint:
        raise ValueError("Grounding review does not match this answer and evidence")
    if evidence_support not in ("supported", "unsupported", "unresolved"):
        raise ValueError("Unknown evidence-support review value")
    if entity_identity not in ("established", "different_entities", "unresolved", "not_applicable"):
        raise ValueError("Unknown entity-identity review value")
    if not isinstance(rationale, str) or not rationale.strip() or not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("A source review requires an attributed rationale")
    updated = deepcopy(row)
    updated["grounding_review"] = {
        "result_fingerprint": expected_fingerprint,
        "evidence_support": evidence_support,
        "entity_identity": entity_identity,
        "rationale": rationale,
        "reviewer": reviewer,
        "scope": "Evidence support only; unsupported does not assert that the answer itself is false.",
    }
    updated["semantic_claim_support"] = {
        "status": "source_reviewed",
        "assessment": evidence_support,
        "supported_claims": None,
        "unsupported_claims": None,
        "reason": rationale,
        "reviewer": reviewer,
    }
    updated["independent_assessment"] = independent_assessment(updated)
    return updated
