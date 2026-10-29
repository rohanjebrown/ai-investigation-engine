"""Answer-quality report serialization and human-readable summaries."""

import json
from pathlib import Path

from investigation_engine.evaluation.grounding import independent_assessment


def export_answer_quality(report: dict, directory: Path) -> None:
    """Write stable UTF-8 files; console output should use ``console_summary``."""
    directory.mkdir(parents=True, exist_ok=True)
    body = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    (directory / "answer-quality.json").write_text(body, encoding="utf-8", newline="\n")
    lines = [
        "# Answer-quality evaluation",
        "",
        f"Suite: `{report['suite_version']}` / `{report['evaluation_version']}`",
        f"Corpus: `{report['corpus_id']}` ({report['corpus_fingerprint']})",
        f"Cases: {report['evaluated_case_count']}",
        "",
        "| Mode | Cases | Determinate answer accuracy | Strict evidence recall | Strict citation recall | Correct abstentions | Correct conflicts | Incomplete | Mean latency ms | Mean searches | Mean generation requests |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for run in report["runs"]:
        aggregate = run["aggregate"]
        lines.append(
            "| {mode} | {cases} | {accuracy} | {evidence} | {citation} | {abstain} | {conflict} | {incomplete} | {latency} | {searches} | {generation} |".format(
                mode=run["configuration"]["mode"],
                cases=aggregate["evaluated_cases"],
                accuracy=_fmt(aggregate["deterministic_answer_accuracy"]),
                evidence=_fmt(aggregate["strict_evidence_recall"]),
                citation=_fmt(aggregate["strict_citation_recall"]),
                abstain=aggregate["correct_abstentions"],
                conflict=aggregate["correct_conflicts"],
                incomplete=aggregate["incomplete_or_budget_limited"],
                latency=_fmt(aggregate["mean_latency_ms"]),
                searches=_fmt(aggregate["mean_search_calls"]),
                generation=_fmt(aggregate["mean_generation_requests"]),
            )
        )
    execution = report.get("execution")
    if execution:
        lines += [
            "",
            f"Execution: `{execution.get('kind', 'unspecified')}`; generation model `{execution.get('generation_model_requested', 'unrecorded')}`.",
            f"Generation requests: {execution.get('generation_requests_observed', 'unrecorded')}/{execution.get('generation_request_cap', 'unbounded')}; "
            f"embedding text inputs: {execution.get('embedding_inputs_observed', 'unrecorded')}/{execution.get('embedding_text_cap', 'unbounded')}.",
            f"Embedding API operations: `{json.dumps(execution.get('embedding_request_counts', {}), sort_keys=True)}`.",
            f"Database: {execution.get('test_database', 'unrecorded')}; Cloud SQL contacted: {execution.get('cloud_sql_contacted', 'unrecorded')}.",
            "",
        ]
    lines += [
        "",
        "## By category",
        "",
        "See JSON for per-category aggregates and each question's source occurrences, cited/retrieved evidence, searches, usage, error classes, paired deltas and unavailable metrics.",
        "",
        "Gold matching is not semantic claim verification. Answers without a deterministic accepted value require human review.",
        "",
    ]
    lines += [
        "## Independent answer and grounding assessments",
        "",
        "Correctness concerns the benchmark answer value. Citation IDs and gold-fact overlap do not prove semantic support or entity identity.",
        "",
        "| Mode / Case | Answer correctness | Citation IDs | Semantic evidence support | Entity identity |",
        "|---|---|---|---|---|",
    ]
    for run in report["runs"]:
        for row in run["questions"]:
            axes = independent_assessment(row)
            lines.append(
                f"| {run['configuration']['mode']} / `{row['id']}` | {axes['answer_correctness']} | "
                f"{axes['citation_identifier_validity']} | {axes['semantic_evidence_support']} | "
                f"{axes['cross_document_entity_identity']} |"
            )
    lines += [
        "",
        "Explicit source reviews, where present, are attributed and bound to the exact saved answer and evidence fingerprint.",
        "",
        "## Errors and review",
        "",
    ]
    for run in report["runs"]:
        failures = [
            row
            for row in run["questions"]
            if row["outcome"]
            not in ("correct_answer", "correct_abstention", "correct_conflict", "answer_requires_human_review")
            or independent_assessment(row)["semantic_evidence_support"] in ("unsupported", "unresolved")
        ]
        lines.append(f"### {run['configuration']['name']}")
        if not failures:
            lines.append("No benchmark answer/completion failures; grounding assessment remains separate.")
        else:
            for row in failures:
                analysis = row.get("failure_analysis") or {}
                retrieval = row.get("retrieval_coverage") or {}
                citation = row.get("citation_coverage") or {}
                retrieved_count = retrieval.get("strict_fact_count", "unavailable")
                retrieved_total = retrieval.get("gold_fact_count", "unavailable")
                cited_count = citation.get("strict_fact_count", "unavailable")
                cited_total = citation.get("gold_fact_count", "unavailable")
                if retrieved_count is None:
                    retrieved_count = "unobserved"
                if cited_count is None:
                    cited_count = "unobserved"
                diagnostic = row.get("provider_diagnostic")
                source_review = row.get("grounding_review")
                source_review_note = (
                    (
                        f" Source review ({source_review['reviewer']}): "
                        f"{source_review['evidence_support']}; {source_review['rationale']}"
                    )
                    if source_review
                    else ""
                )
                diagnostic_summary = (
                    (
                        f" Provider diagnostic: `{diagnostic['operation']}` / HTTP `{diagnostic['http_status']}` / "
                        f"`{diagnostic['category']}`; model `{diagnostic['model_id']}`; "
                        f"request count {diagnostic['request_count']}; retryable {diagnostic['retryable']}."
                    )
                    if diagnostic
                    else ""
                )
                lines.append(
                    f"- `{row['id']}` ({row['category']}): expected `{row.get('expected_behavior')}`; "
                    f"actual `{row['outcome']}` / `{row['status']}`; "
                    f"strict supporting facts retrieved {retrieved_count}/{retrieved_total}; cited "
                    f"{cited_count}/{cited_total}; "
                    f"classification `{analysis.get('classification', 'unavailable')}`; "
                    f"observation: {analysis.get('observation', 'unavailable')} "
                    f"Hypothesis: {analysis.get('hypothesis', 'not assigned')}{diagnostic_summary}{source_review_note}"
                )
        lines.append("")
    lines += [
        "Semantic claim support requires human review; unsupported-claim counts are not inferred from gold-source matching.",
        "Execution failures without a completed application result have unobserved evidence metrics, not zero-recall scores.",
        "The report contains expected answers and labels for audit, but these values are excluded from application/provider inputs.",
        "",
    ]
    (directory / "answer-quality.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


def console_summary(report: dict) -> str:
    """ASCII-safe summary for legacy Windows terminals; full reports remain UTF-8."""
    summary = {
        "evaluation_version": report["evaluation_version"],
        "corpus_fingerprint": report["corpus_fingerprint"],
        "question_ids": report["question_ids"],
        "runs": [{"name": run["configuration"]["name"], "aggregate": run["aggregate"]} for run in report["runs"]],
        "paired_comparisons": report["paired_comparisons"],
    }
    return json.dumps(summary, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _fmt(value) -> str:
    return "unavailable" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)
