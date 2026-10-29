"""Offline answer/investigation quality metrics; never imported by production API code."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from statistics import mean
from time import perf_counter
from typing import Literal
from uuid import UUID

from investigation_engine.application.ports.provider_diagnostics import safe_diagnostic
from investigation_engine.application.retrieval import Evidence
from investigation_engine.evaluation.answer_aliases import SCORER_VERSION, matched_alias, normalization_record
from investigation_engine.evaluation.answer_quality_report import (
    console_summary as console_summary,
)
from investigation_engine.evaluation.answer_quality_report import (
    export_answer_quality as export_answer_quality,
)
from investigation_engine.evaluation.grounding import independent_assessment
from investigation_engine.evaluation.retrieval import (
    covered,
    fingerprint,
    missing_gold,
    occurrence,
    validate_suite,
)

AnswerMode = Literal["single_step", "multi_step"]
_NEGATIONS = {"not", "no", "never", "neither", "without"}
_LIMIT_DISPOSITIONS = {
    "search_limit",
    "embedding_limit",
    "evidence_limit",
    "context_limit",
}
_CONFLICT_MARKERS = ("conflict", "contradict", "disagree", "inconsistent", "conflicting")


@dataclass(frozen=True, slots=True)
class AnsweringRun:
    """A caller-composed existing use case plus the configuration actually used."""

    name: str
    mode: AnswerMode
    service: object = field(repr=False, compare=False)
    retrieval_mode: str
    chunking: dict
    embedding: dict
    generation_model: str
    answering_budgets: dict | None = None
    investigation_budgets: dict | None = None

    def __post_init__(self) -> None:
        if not self.name.strip() or self.mode not in ("single_step", "multi_step"):
            raise ValueError("Answering run requires a name and supported mode")
        if self.retrieval_mode not in ("semantic", "hybrid"):
            raise ValueError("Answering run requires a known retrieval mode")
        if (
            not self.generation_model.strip()
            or not isinstance(self.chunking, dict)
            or not isinstance(self.embedding, dict)
        ):
            raise ValueError("Answering run requires explicit model and retrieval configurations")
        if self.mode == "multi_step" and not isinstance(self.investigation_budgets, dict):
            raise ValueError("Multi-step run must record its investigation budgets")
        if self.mode == "single_step" and not isinstance(self.answering_budgets, dict):
            raise ValueError("Single-step run must record its answer/context budgets")

    def configuration(self) -> dict:
        return {
            "name": self.name,
            "mode": self.mode,
            "retrieval_mode": self.retrieval_mode,
            "chunking": self.chunking,
            "embedding": self.embedding,
            "generation_model": self.generation_model,
            "answering_budgets": self.answering_budgets,
            "investigation_budgets": self.investigation_budgets,
        }


@dataclass(frozen=True, slots=True)
class _AnswerResponse:
    status: str
    answer: str | None
    evidence: list[tuple[str, Evidence]]
    citations: list[str]
    conflicts: list[dict]
    searches: list[dict]
    generation_requests: int
    embedding_requests: int
    usage: dict
    model_version: str | None


def validate_answer_suite(suite: dict, corpus: dict) -> None:
    """Validate task-13 labels without changing retrieval-v1 semantics."""
    validate_suite(suite, corpus)
    if suite.get("answer_quality_version") != "answer-quality-v1":
        raise ValueError("Unsupported or missing answer-quality annotation version")
    expected_counts = {
        "simple_factual": (1, "answer"),
        "two_hop": (2, "answer"),
        "three_plus_hop": (4, "answer"),
        "unanswerable": (0, "abstain"),
        "contradictory": (2, "report_conflict"),
    }
    documents = {occurrence(doc["source"]): doc for doc in corpus["documents"]}
    for case in suite["cases"]:
        _validate_answer_case(case, expected_counts, corpus, documents)
    counts = Counter(case["category"] for case in suite["cases"])
    if counts != Counter(
        {
            "simple_factual": 8,
            "two_hop": 12,
            "three_plus_hop": 8,
            "unanswerable": 6,
            "contradictory": 6,
        }
    ):
        raise ValueError("The fixed answer-quality suite must retain its reviewed 40-case category mix")


def _validate_answer_case(case: dict, expected_counts: dict, corpus: dict, documents: dict) -> None:
    category = case["category"]
    if category not in expected_counts:
        raise ValueError("Evaluation case has an unsupported category")
    expected_evidence_count, expected_behavior = expected_counts[category]
    facts = case.get("required_evidence")
    case_id = case["id"]
    if not isinstance(facts, list):
        raise ValueError(f"{case_id} supporting facts must be an array")
    if facts and len(facts) != expected_evidence_count:
        raise ValueError(f"{case_id} has an unexpected supporting-fact count")
    if case.get("expected_behavior") != expected_behavior:
        raise ValueError(f"{case_id} has an inconsistent expected behavior")
    answer = case.get("expected_answer")
    if expected_behavior == "answer" and (not isinstance(answer, str) or not answer.strip()):
        raise ValueError(f"{case_id} requires an expected answer")
    alternatives = case.get("acceptable_answers", [])
    if not isinstance(alternatives, list) or any(
        not isinstance(candidate, str) or not candidate.strip() for candidate in alternatives
    ):
        raise ValueError(f"{case_id} has malformed acceptable-answer annotations")
    if expected_behavior != "answer" and answer is not None:
        raise ValueError(f"{case_id} must not have an answer label")
    basis = case.get("category_basis", "").casefold()
    if category == "three_plus_hop" and not (
        "two subject-to-person-to-attribute chains" in basis and "not a claim of a linear four-hop path" in basis
    ):
        raise ValueError("Bridge-comparison annotations must state their branching interpretation")
    if case.get("split") == "dev":
        _validate_dev_answer_case(case, corpus, facts)
    if category == "contradictory":
        _validate_conflict_claims(case, facts, documents)


def _validate_dev_answer_case(case: dict, corpus: dict, facts: list[dict]) -> None:
    case_id = case["id"]
    if case.get("dataset_revision") != corpus["dataset_revision"]:
        raise ValueError(f"{case_id} has a dataset revision mismatch")
    for fact in facts:
        locator = fact["source"]["record_locator"]
        if locator.get("split") != "dev" or locator.get("example_id") != case.get("original_question_id"):
            raise ValueError(f"{case_id} gold occurrence does not match its source record")


def _validate_conflict_claims(case: dict, facts: list[dict], documents: dict) -> None:
    claims = case.get("expected_conflict_claims")
    if not facts:
        return  # Keep the case visible; the evaluator marks missing labels unavailable.
    if not isinstance(claims, list) or len(claims) != 2:
        return  # Missing evaluation-only conflict labels cannot be scored.
    case_id = case["id"]
    indexes = set()
    for claim in claims:
        if (
            not isinstance(claim, dict)
            or set(claim) != {"value", "required_evidence_index"}
            or not isinstance(claim["value"], str)
            or not claim["value"].strip()
            or type(claim["required_evidence_index"]) is not int
            or claim["required_evidence_index"] not in (0, 1)
        ):
            raise ValueError(f"{case_id} has a malformed conflict claim label")
        indexes.add(claim["required_evidence_index"])
    if indexes != {0, 1}:
        raise ValueError(f"{case_id} conflict claim labels must map to both source facts")
    for claim in claims:
        fact = facts[claim["required_evidence_index"]]
        document = documents.get(occurrence(fact["source"]))
        if document is None or not _positive_phrase(
            document["text"][fact["start"] : fact["end"]],
            claim["value"],
        ):
            raise ValueError(f"{case_id} conflict claim value is not present in its labeled source sentence")


def _words(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFC", value).casefold()
    return tuple(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


def _positive_phrase(answer: str, expected: str) -> bool:
    answer_words, expected_words = _words(answer), _words(expected)
    if not expected_words:
        return False
    width = len(expected_words)
    found_positive = False
    found_negated = False
    for start in range(len(answer_words) - width + 1):
        if answer_words[start : start + width] != expected_words:
            continue
        prefix = answer_words[max(0, start - 3) : start]
        suffix = answer_words[start + width : start + width + 2]
        negated = any(token in _NEGATIONS for token in prefix) or (bool(suffix) and suffix[0] in {"false", "incorrect"})
        found_negated |= negated
        found_positive |= not negated
    return found_positive and not found_negated


def _direct_value(answer: str) -> bool:
    value = re.sub(r"\[E[1-9][0-9]*\]", "", answer).strip().strip(" \t\r\n\"'“”‘’.,;:!?()[]{}")
    return len(value) <= 120 and len(_words(value)) <= 8 and not any(mark in value for mark in ".;\n")


def answer_correctness(case: dict, status: str, answer: str | None) -> str:
    """Exact value matching is conservative; non-literal paraphrases go to review."""
    if case["expected_behavior"] != "answer":
        return "not_applicable"
    if status != "answered" or not isinstance(answer, str):
        return "no_answer"
    accepted = case.get("acceptable_answers") or [case["expected_answer"]]
    matches = [_positive_phrase(answer, item) for item in accepted]
    if any(matches):
        return "correct"
    if matched_alias(case, answer) is not None:
        return "correct"
    if _direct_value(answer):
        return "incorrect"
    return "human_review"


def _partial_coverage(hit: Evidence, gold: dict) -> bool:
    if (
        occurrence(asdict(hit.source)) != occurrence(gold["source"])
        or hit.content_sha256 != gold["content_sha256"]
        or not (hit.start < gold["end"] and gold["start"] < hit.end)
    ):
        return False
    return any(
        span.start == gold["start"]
        and span.end == gold["end"]
        and any(
            locator.kind.value == "sentence" and locator.index == gold["sentence_index"] for locator in span.locators
        )
        for span in hit.source_spans
    )


def _unique_evidence(items: list[tuple[str, Evidence]]) -> list[tuple[str, Evidence]]:
    unique = []
    seen = set()
    for evidence_id, item in items:
        if item.identity not in seen:
            unique.append((evidence_id, item))
            seen.add(item.identity)
    return unique


def _coverage(items: list[tuple[str, Evidence]], gold: list[dict]) -> dict:
    unique = _unique_evidence(items)
    strict = {fact_index for fact_index, fact in enumerate(gold) if any(covered(hit, fact) for _, hit in unique)}
    partial = {
        fact_index for fact_index, fact in enumerate(gold) if any(_partial_coverage(hit, fact) for _, hit in unique)
    }
    strict_relevant = sum(any(covered(hit, fact) for fact in gold) for _, hit in unique)
    partial_relevant = sum(any(_partial_coverage(hit, fact) for fact in gold) for _, hit in unique)
    denominator = len(gold)
    return {
        "gold_fact_count": denominator,
        "unique_evidence_count": len(unique),
        "strict_fact_count": len(strict),
        "strict_recall": len(strict) / denominator if denominator else None,
        "all_required_strict": len(strict) == denominator if denominator else None,
        "strict_precision": strict_relevant / len(unique) if unique and denominator else None,
        "partial_fact_count": len(partial),
        "partial_recall": len(partial) / denominator if denominator else None,
        "partial_precision": partial_relevant / len(unique) if unique and denominator else None,
        "policy": {
            "strict": "Same source occurrence and Content hash; cited range fully contains the complete annotated sentence and preserves its exact sentence locator/bounds.",
            "partial": "Same source occurrence/hash and exact sentence locator/bounds; cited range overlaps at least one character of that sentence. Partial overlap is reported separately and does not count as strict support.",
            "duplicates": "Evidence references with identical document-version/range identity count once.",
        },
    }


def _unobserved_coverage(gold: list[dict]) -> dict:
    """Keep execution failures from being misreported as zero retrieval/citation."""
    result = _coverage([], gold)
    for key in (
        "unique_evidence_count",
        "strict_fact_count",
        "strict_recall",
        "all_required_strict",
        "strict_precision",
        "partial_fact_count",
        "partial_recall",
        "partial_precision",
    ):
        result[key] = None
    result["observation"] = "No completed application result exposed evidence for scoring."
    return result


def _conflict_supported(case: dict, conflicts: list[dict], id_map: dict[str, Evidence]) -> bool | None:
    labels = case.get("expected_conflict_claims")
    if not labels:
        return None
    for label in labels:
        fact = case["required_evidence"][label["required_evidence_index"]]
        matched = False
        for claim in conflicts:
            value_matches = _positive_phrase(claim["claim"], label["value"])
            evidence_matches = any(
                evidence_id in id_map and covered(id_map[evidence_id], fact) for evidence_id in claim["citations"]
            )
            if value_matches and evidence_matches:
                matched = True
                break
        if not matched:
            return False
    return True


def _single_step_conflict_supported(
    case: dict, answer: str | None, citations: list[str], id_map: dict[str, Evidence]
) -> bool | None:
    """Require both labeled values, an explicit conflict cue and their gold sources."""
    labels = case.get("expected_conflict_claims")
    if not labels:
        return None
    if not isinstance(answer, str) or not any(marker in answer.casefold() for marker in _CONFLICT_MARKERS):
        return False
    for label in labels:
        value_pattern = r"\s+".join(re.escape(part) for part in label["value"].split())
        match = re.search(rf"(?<!\w){value_pattern}(?!\w)", answer, flags=re.IGNORECASE)
        if match is None or not _positive_phrase(answer, label["value"]):
            return False
        inline = re.match(r"[\s.,;:!?()]*\[(E[1-9][0-9]*)\]", answer[match.end() :])
        if inline is None or inline.group(1) not in citations:
            return False
        fact = case["required_evidence"][label["required_evidence_index"]]
        if inline.group(1) not in id_map or not covered(id_map[inline.group(1)], fact):
            return False
    return True


def _observed_outcome(
    case: dict, status: str, answer: str | None, conflicts: list[dict], conflict_supported: bool | None
) -> str:
    if status == "incomplete":
        return "incomplete"
    if status == "provider_error":
        return "provider_failure"
    behavior = case["expected_behavior"]
    if behavior == "answer":
        if status == "insufficient_evidence":
            return "incorrect_abstention"
        if status == "conflicting_evidence":
            return "false_conflict"
        correctness = answer_correctness(case, status, answer)
        return {
            "correct": "correct_answer",
            "incorrect": "incorrect_answer",
            "human_review": "answer_requires_human_review",
            "no_answer": "no_answer",
        }.get(correctness, "unavailable")
    if behavior == "abstain":
        if status == "insufficient_evidence":
            return "correct_abstention"
        if status == "answered":
            return "unsupported_answer"
        if status == "conflicting_evidence":
            return "false_conflict"
    if behavior == "report_conflict":
        if status in ("conflicting_evidence", "answered") and conflict_supported is True:
            return "correct_conflict"
        if status == "conflicting_evidence":
            return "conflict_needs_review_or_missed_fact"
        return "missed_contradiction"
    return "provider_failure"


def _evidence_dict(evidence_id: str, item: Evidence) -> dict:
    return {
        "evidence_id": evidence_id,
        "document_id": str(item.document_id),
        "document_version_id": str(item.document_version_id),
        "content_sha256": item.content_sha256,
        "start": item.start,
        "end": item.end,
        "text": item.text,
        "title": item.title,
        "source": _json_value(asdict(item.source)),
        "source_spans": _json_value(item.source_spans),
        "rank": item.rank,
        "score": item.score,
        "score_kind": item.score_kind,
    }


def _json_value(value):
    if isinstance(value, UUID):
        return str(value)
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value


def _response(run: AnsweringRun, result) -> _AnswerResponse:
    """Normalize single-step and investigation result objects to one shape."""
    if run.mode == "single_step":
        status = getattr(result.status, "value", result.status)
        retrieved = list(result.retrieved_evidence)
        cited = list(result.citations)
        evidence = [(item.evidence_id, item.evidence) for item in retrieved]
        citations = [item.evidence_id for item in cited]
        searches = [
            {
                "sequence": 1,
                "query": None,
                "evidence_ids": [key for key, _ in evidence],
                "result_count": len(evidence),
                "succeeded": True,
            }
        ]
        return _AnswerResponse(
            status=status,
            answer=result.answer,
            evidence=evidence,
            citations=citations,
            conflicts=[],
            searches=searches,
            generation_requests=int(getattr(result, "generation_requests", 1)),
            embedding_requests=int(getattr(result, "embedding_requests", 1)),
            usage=_json_value(getattr(result, "usage", {})),
            model_version=getattr(result, "model_version", None),
        )

    status = getattr(result.status, "value", result.status)
    evidence = [(item.evidence_id, item.evidence) for item in result.evidence]
    conflicts = [{"claim": item.claim, "citations": list(item.citations)} for item in result.conflicts]
    searches = [
        {
            "sequence": item.sequence,
            "query": item.query,
            "evidence_ids": list(item.evidence_ids),
            "result_count": item.result_count,
            "duration_ms": item.duration_ms,
            "succeeded": item.succeeded,
            "disposition": getattr(item.disposition, "value", item.disposition),
        }
        for item in result.searches
    ]
    return _AnswerResponse(
        status=status,
        answer=result.answer,
        evidence=evidence,
        citations=list(result.citations),
        conflicts=conflicts,
        searches=searches,
        generation_requests=int(result.generation_requests),
        embedding_requests=int(result.embedding_requests),
        usage=_json_value(result.usage),
        model_version=result.model_version,
    )


async def evaluate_answers(
    suite: dict,
    corpus: dict,
    runs: list[AnsweringRun],
    *,
    indexed_corpus_fingerprint: str,
    annotation_corpus: dict | None = None,
    case_ids: tuple[str, ...] | None = None,
    owner_scope: Literal["shared", "administrator"] = "shared",
    owner_id: str | None = None,
    stop_requested: Callable[[], bool] | None = None,
    on_result: Callable[[str, dict], None] | None = None,
) -> dict:
    """Run existing application use cases; evaluation labels never enter their inputs."""
    validate_answer_suite(suite, annotation_corpus or corpus)
    if indexed_corpus_fingerprint != fingerprint(corpus):
        raise ValueError("Indexed corpus attestation does not match the manifest")
    if owner_scope == "shared" and owner_id is not None:
        raise ValueError("Shared evaluation scope cannot contain a principal ID")
    if owner_scope not in ("shared", "administrator"):
        raise ValueError("Unsupported evaluation owner scope")
    if owner_scope == "administrator" and (not isinstance(owner_id, str) or not owner_id.strip()):
        raise ValueError("Administrator evaluation scope requires an explicit trusted principal")
    if not runs or len({run.name for run in runs}) != len(runs):
        raise ValueError("Distinct named answering runs are required")
    if len({run.mode for run in runs}) != len(runs):
        raise ValueError("At most one run per answering mode is supported")
    baseline = runs[0]
    for run in runs[1:]:
        if (
            run.retrieval_mode != baseline.retrieval_mode
            or run.chunking != baseline.chunking
            or run.embedding != baseline.embedding
            or run.generation_model != baseline.generation_model
        ):
            raise ValueError(
                "Paired answering runs require matching retrieval, chunking, embedding and generation configurations"
            )
    cases = suite["cases"]
    if case_ids is not None:
        if not case_ids or len(set(case_ids)) != len(case_ids):
            raise ValueError("Selected case IDs must be nonempty and unique")
        by_id = {case["id"]: case for case in cases}
        if any(item not in by_id for item in case_ids):
            raise ValueError("Selected case ID is not in the evaluation suite")
        cases = [by_id[item] for item in case_ids]

    started_all = perf_counter()
    reports = []
    for run in runs:
        rows = []
        for case_index, case in enumerate(cases):
            if stop_requested is not None and stop_requested():
                rows.extend(_not_run(case_row, "stopped_after_provider_failure") for case_row in cases[case_index:])
                break
            rows.append(await _evaluate_case(run, case, corpus, owner_id, on_result))
            if stop_requested is not None and stop_requested():
                rows.extend(
                    _not_run(case_row, "stopped_after_provider_failure") for case_row in cases[case_index + 1 :]
                )
                break
        reports.append(
            {
                "configuration": run.configuration(),
                "aggregate": _aggregate(rows),
                "by_category": _by_category(rows),
                "questions": rows,
            }
        )

    comparisons = []
    if len(reports) == 2:
        left, right = reports
        right_by_id = {row["id"]: row for row in right["questions"]}
        paired = [
            (row, right_by_id[row["id"]])
            for row in left["questions"]
            if row["id"] in right_by_id
            and row.get("status") not in ("failed", "provider_error", "unavailable", "not_run")
            and right_by_id[row["id"]].get("status") not in ("failed", "provider_error", "unavailable", "not_run")
        ]
        comparisons.append(
            {
                "baseline": left["configuration"]["name"],
                "other": right["configuration"]["name"],
                "paired_cases": len(paired),
                "paired_question_ids": [baseline_row["id"] for baseline_row, _ in paired],
                "outcome_changes": [
                    {
                        "id": baseline_row["id"],
                        "baseline": baseline_row["outcome"],
                        "other": comparison_row["outcome"],
                    }
                    for baseline_row, comparison_row in paired
                    if baseline_row["outcome"] != comparison_row["outcome"]
                ],
                "answer_correctness_changes": [
                    {
                        "id": baseline_row["id"],
                        "baseline": baseline_row["answer_correctness"],
                        "other": comparison_row["answer_correctness"],
                    }
                    for baseline_row, comparison_row in paired
                    if baseline_row["answer_correctness"] != comparison_row["answer_correctness"]
                ],
                "strict_evidence_recall_delta": _paired_delta(paired, "retrieval_coverage", "strict_recall"),
                "strict_citation_recall_delta": _paired_delta(paired, "citation_coverage", "strict_recall"),
                "latency_delta_ms": _paired_numeric_delta(paired, "latency_ms"),
                "generation_request_delta": _paired_numeric_delta(paired, "generation_requests"),
                "interpretation": "Paired descriptive differences only; no statistical significance or superiority is inferred.",
            }
        )
    return {
        "evaluation_version": suite.get("answer_quality_version", "answer-quality-v1"),
        "answer_scorer_version": SCORER_VERSION,
        "suite_version": suite["version"],
        "suite_fingerprint": fingerprint(suite),
        "dataset_revision": corpus["dataset_revision"],
        "corpus_id": corpus["corpus_id"],
        "corpus_fingerprint": fingerprint(corpus),
        "annotation_corpus_fingerprint": fingerprint(annotation_corpus or corpus),
        "owner_scope": owner_scope,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "question_ids": [case["id"] for case in cases],
        "evaluated_case_count": len(cases),
        "stopped_early": bool(stop_requested is not None and stop_requested()),
        "runs": reports,
        "paired_comparisons": comparisons,
        "notes": [
            "Expected answers and gold annotations are recorded in the report but were not passed to production use cases or provider prompts.",
            "Gold source-occurrence matching measures annotated evidence agreement, not semantic support of each generated claim.",
            "Paraphrases that do not contain an explicitly accepted value require human review; no LLM judge is used.",
            "Reviewed evaluation-only direct nationality aliases are recorded separately from unchanged benchmark gold values.",
            "Answer correctness, citation validity, annotated fact matching, semantic support, and entity identity are independent assessment axes.",
            "Execution failures without a completed application result have unobserved evidence metrics, not zero-recall scores.",
        ],
        "total_elapsed_ms": (perf_counter() - started_all) * 1000,
    }


async def _evaluate_case(
    run: AnsweringRun,
    case: dict,
    corpus: dict,
    owner_id: str | None,
    on_result: Callable[[str, dict], None] | None,
) -> dict:
    unavailable = _annotation_unavailable_reason(case)
    if unavailable:
        return {
            "id": case["id"],
            "category": case["category"],
            "status": "unavailable",
            "outcome": "unavailable",
            "reason": unavailable,
            "answer_correctness": "unavailable",
        }

    missing = missing_gold(case, corpus)
    if missing:
        return {
            "id": case["id"],
            "category": case["category"],
            "status": "unavailable",
            "outcome": "unavailable",
            "reason": "gold_missing_from_corpus",
            "missing_gold": missing,
            "answer_correctness": "unavailable",
        }

    row = {
        "id": case["id"],
        "question": case["question"],
        "category": case["category"],
        "expected_behavior": case["expected_behavior"],
        "expected_answer": case["expected_answer"],
        "acceptable_answers": case.get("acceptable_answers")
        or ([case["expected_answer"]] if case["expected_answer"] else []),
        "required_evidence": case["required_evidence"],
    }
    begin = perf_counter()
    try:
        response = await run.service.execute(case["question"], owner_id=owner_id)
        latency_ms = (perf_counter() - begin) * 1000
        normalized = _response(run, response)
        status = normalized.status
        answer = normalized.answer
        evidence = normalized.evidence
        conflicts = normalized.conflicts
        searches = normalized.searches
        evidence_map = {key: hit for key, hit in evidence}
        cited_ids = list(normalized.citations)
        for conflict in conflicts:
            cited_ids.extend(conflict["citations"])
        # Unknown IDs should be impossible after application validation; preserve the failure if violated.
        if any(key not in evidence_map for key in cited_ids):
            raise ValueError("Application result contains an unresolved evidence identifier")
        cited_evidence = [(key, evidence_map[key]) for key in cited_ids]
        retrieval = _coverage(evidence, case["required_evidence"])
        citation_metrics = _coverage(cited_evidence, case["required_evidence"])
        unique_cited_ids = list(dict.fromkeys(cited_ids))
        conflict_supported = _conflict_supported(case, conflicts, evidence_map)
        if run.mode == "single_step" and case["expected_behavior"] == "report_conflict":
            conflict_supported = _single_step_conflict_supported(
                case,
                answer,
                unique_cited_ids,
                evidence_map,
            )
        outcome = _observed_outcome(case, status, answer, conflicts, conflict_supported)
        limit_hit = status == "incomplete" or any(item.get("disposition") in _LIMIT_DISPOSITIONS for item in searches)
        failure_analysis = _failure_analysis(
            outcome,
            status,
            retrieval,
            citation_metrics,
            limit_hit,
        )
        result_row = {
            **row,
            "status": status,
            "outcome": outcome,
            "answer": answer,
            "citations": unique_cited_ids,
            "conflicts": conflicts,
            "searches": searches,
            "retrieved_evidence": [_evidence_dict(key, hit) for key, hit in evidence],
            "citation_evidence": [_evidence_dict(key, evidence_map[key]) for key in unique_cited_ids],
            "answer_correctness": answer_correctness(case, status, answer),
            "answer_normalization": normalization_record(case, answer),
            "retrieval_coverage": retrieval,
            "citation_coverage": citation_metrics,
            "conflict_supports_both_labeled_claims": conflict_supported,
            "failure_analysis": failure_analysis,
            "semantic_claim_support": {
                "status": "human_review_required" if status == "answered" else "not_applicable",
                "supported_claims": None,
                "unsupported_claims": None,
                "reason": "Exact gold occurrence matching does not prove that generated claims logically follow from cited text.",
            },
            "investigation_behavior": {
                "search_count": len(searches),
                "generation_requests": normalized.generation_requests,
                "embedding_query_requests": normalized.embedding_requests,
                "latency_ms": latency_ms,
                "required_facts_retrieved": retrieval["strict_fact_count"],
                "required_facts_cited": citation_metrics["strict_fact_count"],
                "budget_exhausted": limit_hit,
                "evidence_gap_at_stop": status in ("answered", "insufficient_evidence")
                and (retrieval["strict_recall"] is not None and retrieval["strict_recall"] < 1),
                "follow_up_searches_with_new_required_evidence": _useful_followups(
                    searches, evidence_map, case["required_evidence"]
                ),
            },
            "usage": normalized.usage,
            "model_version": normalized.model_version or run.generation_model,
            "error_type": None,
        }
        result_row["independent_assessment"] = independent_assessment(result_row)
        if on_result is not None:
            on_result(run.name, result_row)
        return result_row
    except Exception as error:
        latency_ms = (perf_counter() - begin) * 1000
        kind = type(error).__name__
        failure = (
            "provider_failure"
            if kind.startswith(("Generation", "Embedding"))
            else ("retrieval_failure" if kind.endswith(("RepositoryError", "DatabaseError")) else "execution_failure")
        )
        unobserved = _unobserved_coverage(case["required_evidence"])
        result_row = {
            **row,
            "status": "provider_error" if failure == "provider_failure" else "failed",
            "outcome": failure,
            "answer": None,
            "citations": [],
            "conflicts": [],
            "searches": None,
            "retrieved_evidence": None,
            "citation_evidence": None,
            "answer_correctness": "unavailable",
            "retrieval_coverage": unobserved,
            "citation_coverage": unobserved.copy(),
            "conflict_supports_both_labeled_claims": None,
            "failure_analysis": _failure_analysis(
                failure, "provider_error", unobserved, unobserved, False, error_type=kind
            ),
            "semantic_claim_support": {
                "status": "not_assessed",
                "supported_claims": None,
                "unsupported_claims": None,
            },
            "investigation_behavior": {
                "search_count": None,
                "generation_requests": None,
                "embedding_query_requests": None,
                "latency_ms": latency_ms,
                "required_facts_retrieved": None,
                "required_facts_cited": None,
                "budget_exhausted": None,
                "evidence_gap_at_stop": None,
                "follow_up_searches_with_new_required_evidence": None,
            },
            "usage": None,
            "model_version": run.generation_model,
            "error_type": kind,
            "provider_diagnostic": safe_diagnostic(error),
        }
        result_row["independent_assessment"] = independent_assessment(result_row)
        if on_result is not None:
            on_result(run.name, result_row)
        return result_row


def _not_run(case: dict, reason: str) -> dict:
    return {
        "id": case["id"],
        "category": case["category"],
        "status": "not_run",
        "outcome": "not_run",
        "answer_correctness": "unavailable",
        "reason": reason,
    }


def _useful_followups(searches: list[dict], evidence_map: dict[str, Evidence], gold: list[dict]) -> int:
    known = set()
    useful = 0
    for search in searches:
        hits = [evidence_map[key] for key in search["evidence_ids"] if key in evidence_map]
        facts = {index for index, item in enumerate(gold) if any(covered(hit, item) for hit in hits)}
        if search["sequence"] > 1 and facts - known:
            useful += 1
        known |= facts
    return useful


def _annotation_unavailable_reason(case: dict) -> str | None:
    if case["expected_behavior"] == "answer" and not case["required_evidence"]:
        return "missing_gold_annotations"
    if case["expected_behavior"] == "report_conflict":
        if len(case["required_evidence"]) != 2:
            return "missing_gold_annotations"
        if len(case.get("expected_conflict_claims", ())) != 2:
            return "missing_conflict_claim_annotations"
    return None


def _failure_analysis(
    outcome: str,
    status: str,
    retrieval: dict,
    citations: dict,
    budget_exhausted: bool,
    *,
    error_type: str | None = None,
) -> dict | None:
    if outcome in ("correct_answer", "correct_abstention", "correct_conflict"):
        return None
    if error_type is not None:
        return {
            "classification": outcome,
            "observation": f"Execution raised {error_type}; no completed result exposed its evidence or citations.",
            "hypothesis": "The sanitized exception type does not identify the failing workflow stage or underlying provider/database cause.",
        }
    if status == "incomplete" or budget_exhausted:
        return {
            "classification": "budget_exhaustion",
            "observation": "The workflow returned incomplete or recorded a configured resource limit.",
            "hypothesis": "The current per-run budgets ended the investigation before a final decision.",
        }
    if retrieval["strict_recall"] is not None and retrieval["strict_recall"] < 1:
        if status == "answered":
            classification = "retrieval_failure"
        else:
            classification = "search_planning_or_retrieval_failure"
        return {
            "classification": classification,
            "observation": f"Strictly retrieved {retrieval['strict_fact_count']} of {retrieval['gold_fact_count']} required facts.",
            "hypothesis": "Search logs identify query order and returned evidence, but do not establish whether planning or ranking caused the gap.",
        }
    if outcome in ("missed_contradiction", "false_conflict", "unsupported_answer", "incorrect_answer"):
        return {
            "classification": "reasoning_or_synthesis_failure",
            "observation": f"Observed outcome: {outcome}.",
            "hypothesis": "Gold matching and output status expose the failure class but do not prove its semantic cause.",
        }
    if citations["strict_recall"] is not None and citations["strict_recall"] < 1:
        return {
            "classification": "citation_failure",
            "observation": f"Citations strictly cover {citations['strict_fact_count']} of {citations['gold_fact_count']} required facts.",
            "hypothesis": "Required evidence was available in the model-facing retrieved set but not fully cited.",
        }
    if outcome in ("answer_requires_human_review", "conflict_needs_review_or_missed_fact"):
        return {
            "classification": "human_review_required",
            "observation": f"Observed outcome: {outcome}.",
            "hypothesis": "The available deterministic annotations cannot settle this response.",
        }
    return {
        "classification": outcome,
        "observation": f"Observed outcome: {outcome}.",
        "hypothesis": "No causal explanation is available from the recorded observable behavior.",
    }


def _aggregate(rows: list[dict]) -> dict:
    outcomes = Counter(row["outcome"] for row in rows)
    answer_counts = Counter(row["answer_correctness"] for row in rows)
    successful = [
        row
        for row in rows
        if row["status"] in ("answered", "insufficient_evidence", "conflicting_evidence", "incomplete")
    ]
    answer_scores = [
        1.0 if row["answer_correctness"] == "correct" else 0.0
        for row in rows
        if row["answer_correctness"] in ("correct", "incorrect")
    ]

    def avg(path):
        vals = [row[path[0]][path[1]] for row in successful if row[path[0]][path[1]] is not None]
        return mean(vals) if vals else None

    return {
        "outcome_counts": dict(sorted(outcomes.items())),
        "answer_correctness_counts": dict(sorted(answer_counts.items())),
        "determinately_scored_answer_count": len(answer_scores),
        "deterministic_answer_accuracy": mean(answer_scores) if answer_scores else None,
        "correct_abstentions": outcomes["correct_abstention"],
        "incorrect_abstentions": outcomes["incorrect_abstention"],
        "unsupported_answers": outcomes["unsupported_answer"],
        "correct_conflicts": outcomes["correct_conflict"],
        "missed_contradictions": outcomes["missed_contradiction"],
        "false_conflicts": outcomes["false_conflict"],
        "incomplete_or_budget_limited": sum(row["outcome"] == "incomplete" for row in rows),
        "provider_failures": outcomes["provider_failure"],
        "strict_evidence_recall": avg(("retrieval_coverage", "strict_recall")),
        "partial_evidence_recall": avg(("retrieval_coverage", "partial_recall")),
        "strict_citation_recall": avg(("citation_coverage", "strict_recall")),
        "partial_citation_recall": avg(("citation_coverage", "partial_recall")),
        "strict_citation_precision": avg(("citation_coverage", "strict_precision")),
        "partial_citation_precision": avg(("citation_coverage", "partial_precision")),
        "all_gold_facts_strictly_retrieved": sum(
            row["retrieval_coverage"]["all_required_strict"] is True for row in successful
        ),
        "all_gold_facts_strictly_cited": sum(
            row["citation_coverage"]["all_required_strict"] is True for row in successful
        ),
        "mean_latency_ms": mean(row["investigation_behavior"]["latency_ms"] for row in successful)
        if successful
        else None,
        "mean_search_calls": mean(row["investigation_behavior"]["search_count"] for row in successful)
        if successful
        else None,
        "mean_generation_requests": mean(
            row["investigation_behavior"]["generation_requests"]
            for row in successful
            if row["investigation_behavior"]["generation_requests"] is not None
        )
        if successful
        else None,
        "mean_follow_up_searches_with_new_required_evidence": mean(
            row["investigation_behavior"]["follow_up_searches_with_new_required_evidence"] for row in successful
        )
        if successful
        else None,
        "semantic_claim_support": "human_review_required; no automatic semantic claim judge",
        "semantic_evidence_support_counts": dict(
            sorted(Counter(independent_assessment(row)["semantic_evidence_support"] for row in rows).items())
        ),
        "entity_identity_counts": dict(
            sorted(Counter(independent_assessment(row)["cross_document_entity_identity"] for row in rows).items())
        ),
        "evaluated_cases": len(rows),
    }


def _by_category(rows: list[dict]) -> dict:
    result = {}
    for category in sorted({row["category"] for row in rows}):
        subset = [row for row in rows if row["category"] == category]
        result[category] = _aggregate(subset)
    return result


def _paired_delta(pairs: list[tuple[dict, dict]], group: str, key: str) -> float | None:
    values = [
        (comparison_row[group][key] - baseline_row[group][key])
        for baseline_row, comparison_row in pairs
        if baseline_row[group][key] is not None and comparison_row[group][key] is not None
    ]
    return mean(values) if values else None


def _paired_numeric_delta(pairs: list[tuple[dict, dict]], key: str) -> float | None:
    values = [
        (comparison_row["investigation_behavior"][key] - baseline_row["investigation_behavior"][key])
        for baseline_row, comparison_row in pairs
        if baseline_row["investigation_behavior"][key] is not None
        and comparison_row["investigation_behavior"][key] is not None
    ]
    return mean(values) if values else None
