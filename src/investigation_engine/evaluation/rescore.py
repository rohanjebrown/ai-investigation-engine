"""Rescore saved application results without executing services or changing gold."""

from copy import deepcopy

from investigation_engine.evaluation.answer_aliases import SCORER_VERSION, normalization_record
from investigation_engine.evaluation.answer_quality import (
    _aggregate, _by_category, _failure_analysis, _observed_outcome, answer_correctness, validate_answer_suite,
)
from investigation_engine.evaluation.grounding import apply_grounding_review, independent_assessment
from investigation_engine.evaluation.retrieval import fingerprint


def rescore_saved_report(original: dict, suite: dict, corpus: dict, *, reviews: list[dict] | None = None) -> dict:
    validate_answer_suite(suite, corpus)
    if (original.get("suite_fingerprint") != fingerprint(suite)
        or original.get("annotation_corpus_fingerprint", original.get("corpus_fingerprint")) != fingerprint(corpus)):
        raise ValueError("Saved report does not match the unchanged suite and annotation corpus")
    result = deepcopy(original)
    by_id = {case["id"]: case for case in suite["cases"]}
    review_map = {}
    for review in reviews or []:
        key = (review["mode"], review["case_id"])
        if key in review_map:
            raise ValueError("Duplicate grounding review")
        review_map[key] = review
    used = set()
    observed = {"answered", "insufficient_evidence", "conflicting_evidence", "incomplete"}
    for run in result["runs"]:
        for index, row in enumerate(run["questions"]):
            case = by_id[row["id"]]
            if row.get("status") in observed:
                if row.get("question") != case["question"] or row.get("expected_answer") != case["expected_answer"]:
                    raise ValueError("Saved question or gold value differs from the frozen annotation")
                row["previous_answer_scoring"] = {"answer_correctness": row.get("answer_correctness"),
                    "outcome": row.get("outcome"),
                    "scorer_version": original.get("answer_scorer_version", "legacy-literal-v1")}
                row["answer_correctness"] = answer_correctness(case, row["status"], row.get("answer"))
                row["answer_normalization"] = normalization_record(case, row.get("answer"))
                row["outcome"] = _observed_outcome(case, row["status"], row.get("answer"),
                    row.get("conflicts") or [], row.get("conflict_supports_both_labeled_claims"))
                row["failure_analysis"] = _failure_analysis(row["outcome"], row["status"],
                    row["retrieval_coverage"], row["citation_coverage"],
                    bool((row.get("investigation_behavior") or {}).get("budget_exhausted")))
            key = (run["configuration"]["mode"], row["id"])
            if key in review_map:
                review = review_map[key]
                row = apply_grounding_review(row, expected_fingerprint=review["expected_fingerprint"],
                    evidence_support=review["evidence_support"], entity_identity=review["entity_identity"],
                    rationale=review["rationale"], reviewer=review["reviewer"])
                used.add(key)
                run["questions"][index] = row
            row["independent_assessment"] = independent_assessment(row)
        run["aggregate"] = _aggregate(run["questions"])
        run["by_category"] = _by_category(run["questions"])
    if used != set(review_map):
        raise ValueError("Grounding review does not identify a saved run/case")
    # Normalization changes no response, evidence, completion status, or latency.
    # Preserve paired numeric deltas; refresh only their answer/outcome labels.
    runs = {run["configuration"]["name"]: {row["id"]: row for row in run["questions"]}
            for run in result["runs"]}
    for comparison in result.get("paired_comparisons", []):
        pairs = [(runs[comparison["baseline"]][key], runs[comparison["other"]][key])
                 for key in comparison["paired_question_ids"]]
        comparison["outcome_changes"] = [{"id": a["id"], "baseline": a["outcome"], "other": b["outcome"]}
                                          for a, b in pairs if a["outcome"] != b["outcome"]]
        comparison["answer_correctness_changes"] = [{"id": a["id"], "baseline": a["answer_correctness"], "other": b["answer_correctness"]}
            for a, b in pairs if a["answer_correctness"] != b["answer_correctness"]]
    result["answer_scorer_version"] = SCORER_VERSION
    result["scoring_scope"] = "Answer-value agreement is independent of semantic evidence support and entity identity."
    return result
