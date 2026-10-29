"""Render the existing paired evaluation JSON into a compact submission report."""

import argparse
import json
from pathlib import Path


def render(report):
    completed = sum(row["status"] != "not_run" for run in report["runs"] for row in run["questions"])
    rows = [
        "# Submission evaluation — 29 October 2026",
        "",
        "This report summarizes the frozen paired run, not a claim of production readiness.",
        "",
        f"Saved outcomes: **{completed}/80** (40 selected questions × two modes). "
        f"Stopped early: {report.get('stopped_early', 'unavailable')}. "
        f"Generation attempts: {report.get('execution', {}).get('generation_requests_observed', 'unavailable')}. "
        "The outcome tables retain provider failures and unrun questions; the paired comparison excludes provider failures. "
        "No failed request was retried. Source audits, when attached, are attributed to Codex and bound to exact "
        "saved responses; they are not independent human review or an assessment of all answers.",
        "",
        "[Machine-readable results and source audits](submission-answer-quality.json).",
        "",
        "## Dataset and method",
        "",
        "The pinned 2Wiki development processing experiment registered 125,760 occurrences and 56,680 exact Contents. "
        "The attested local evaluation corpus is smaller: 218 active source versions and 197 real 768-dimensional Gemini embeddings. "
        "All 40 frozen questions are selected, with identical corpus, gold annotations, generation model and semantic retrieval configuration in both modes. "
        "Investigation is allowed additional searches; it also has a larger context/evidence budget, so this is a workflow comparison, not an isolated causal reasoning experiment.",
        "",
        "The five categories are simple factual, two-hop, three-plus-hop/bridge comparison, unanswerable and contradictory controls. "
        "Previously inspected development questions are not an unseen benchmark. Gold labels never enter model input. "
        "No document embeddings were regenerated; results are checkpointed per question/mode and failed calls are not retried.",
        "",
        "Generation uses gemini-3.5-flash-lite, at least 4.2 seconds between requests, one attempt, and bounded output/context. "
        "The operator reported 15 RPM, 250k TPM and 500 RPD; remaining project usage is unavailable. "
        "The run has a 300-generation-attempt cap and 240-query-embedding cap. Quotas are project-scoped, not independently verified here. "
        "A provider failure stops the run cleanly. No billing change or paid batch API is used.",
        "",
        "## Retrieval baseline",
        "",
        "Historical Task 15: Recall@5 0.875; Recall@10 0.9265; Recall@20 0.9412; MRR@20 0.9853; "
        "complete supporting-evidence coverage@20 0.8235. Semantic and hybrid had identical measured coverage; "
        "only 4/8 bridge comparisons had complete support at K=20. These are historical real-embedding retrieval metrics, "
        "not newly measured answer accuracy or evidence of hybrid superiority. "
        "[Original baseline](../../reports/evaluation/task15-2026-10-28/retrieval.json).",
        "",
        "## Answer and citation results",
        "",
        "Answer correctness uses the existing conservative normalized gold/alias matcher; unscorable prose is not silently correct. "
        "Strict citation precision/recall measure gold source-occurrence and supporting-fact matching. "
        "Structural citation validity only checks identifiers; none of these establishes semantic entailment. "
        "Unreviewed semantic support remains requires_review. Incomplete means operational limits, while insufficient evidence is a model conclusion.",
        "",
        "| Mode | Evaluated | Outcomes | Strict citation P / R | Evidence recall | Mean latency ms |",
        "| --- | ---: | --- | --- | ---: | ---: |",
    ]
    for run in report["runs"]:
        a = run["aggregate"]
        rows.append(
            f"| {run['configuration']['mode']} | {a.get('evaluated_cases')} | {a.get('outcome_counts')} | "
            f"{a.get('strict_citation_precision')} / {a.get('strict_citation_recall')} | "
            f"{a.get('strict_evidence_recall')} | {a.get('mean_latency_ms')} |"
        )
    rows += [
        "",
        "### Usage by mode",
        "",
        "| Mode | Search calls | Generation attempts | Prompt / candidate / thinking tokens | Complete gold evidence cases |",
        "| --- | ---: | ---: | --- | ---: |",
    ]
    for run in report["runs"]:
        measured = [r for r in run["questions"] if r["status"] != "not_run"]
        searches = sum(((r.get("investigation_behavior") or {}).get("search_count") or 0) for r in measured)
        requests = sum(((r.get("investigation_behavior") or {}).get("generation_requests") or 0) for r in measured)
        tokens = [
            sum(((r.get("usage") or {}).get(key) or 0) for r in measured)
            for key in ("prompt_tokens", "candidate_tokens", "thinking_tokens")
        ]
        rows.append(
            f"| {run['configuration']['mode']} | {searches} | {requests} | {tokens} | "
            f"{run['aggregate'].get('all_gold_facts_strictly_retrieved')} |"
        )
    rows += [
        "",
        "Tokens cover returned usage metadata; failed requests may not supply usage. "
        "Provider attempt accounting in execution below is authoritative. No semantic support is imputed to unreviewed answers.",
        "",
        "### Paired descriptive comparison",
        "",
        "```json",
        json.dumps(report.get("paired_comparisons", []), indent=2, ensure_ascii=False),
        "```",
        "",
    ]
    rows += [
        "",
        "### Per-category outcomes",
        "",
        "| Mode | Category | Evaluated | Outcomes |",
        "| --- | --- | ---: | --- |",
    ]
    for run in report["runs"]:
        for category, a in run["by_category"].items():
            rows.append(
                f"| {run['configuration']['mode']} | {category} | {a.get('evaluated_cases')} | {a.get('outcome_counts')} |"
            )
    rows += [
        "",
        "## Usage and configuration",
        "",
        "Exact saved configuration, attempts, tokens and timing:",
        "",
        "```json",
        json.dumps(report.get("execution", {}), indent=2, ensure_ascii=False),
        "```",
        "",
        "## Representative outcomes and failure analysis",
        "",
        "The following representatives are selected by outcome after execution; all cases remain in the machine-readable report. "
        "Failure labels are diagnostic evidence, not proof of an unobserved model reasoning process.",
        "",
    ]
    for run in report["runs"]:
        seen = set()
        for row in run["questions"]:
            outcome = row["outcome"]
            if outcome == "not_run" or outcome in seen:
                continue
            seen.add(outcome)
            rows += [
                f"- **{run['configuration']['mode']} / {outcome} / {row['id']}**: {row['question']}",
                f"  Answer: {row.get('answer')!r}. Status: {row['status']}. "
                f"Analysis: {row.get('failure_analysis')}. "
                f"Independent support assessment: {row.get('independent_assessment')}.",
            ]
            excerpts = [
                {"id": e.get("evidence_id"), "title": e.get("title"), "excerpt": e.get("text", "")[:350]}
                for e in (row.get("citation_evidence") or row.get("retrieved_evidence") or [])[:2]
            ]
            rows += [f"  Search trace: {row.get('searches')}. Evidence sample: {excerpts}"]
    rows += [
        "",
        "## Reliability findings and limits",
        "",
        "Earlier two-hop and bridge abstentions were accompanied by only 1/2 and 2/4 required facts in context: "
        "observed evidence gaps, not proof of a generator defect. A prior contradiction control supplied both gold facts "
        "but Quick Answer missed the disagreement. Earlier investigations exhausted one-search budgets; a provider HTTP 503 "
        "was recorded separately. Task 16D obtained a benchmark-correct nationality while the cited athlete passage did not "
        "establish the intended director identity. Its source review marked support unsupported and identity unresolved. "
        "[Preserved investigation](../../reports/evaluation/task16d-2026-10-28/analysis.md).",
        "",
        "This milestone adds identity/context/conflict guidance, rejects duplicate identical claims masquerading as a conflict, "
        "and adds controlled identity workflow regressions. Fake providers verify evidence transport, citations, ownership and "
        "outcomes; they do not demonstrate improved Gemini reasoning. No expensive second LLM judge was added. "
        "Quick Answer retains its narrower outcome contract: disagreement may appear in text rather than a typed conflict. "
        "No claim of semantic grounding improvement or statistical superiority follows from benchmark answer matches alone.",
        "",
        "## Verification and deployment",
        "",
        "See [completion verification](completion-verification-2026-10-29.md) for exact unit/integration/JavaScript/lint/container "
        "results, earlier failed attempts, skipped opt-in cases, trigger changes and production blockers. Offline deterministic "
        "tests, disposable PostgreSQL tests, real-embedding retrieval and real Gemini generation are separate evidence classes. "
        "No production data was modified and no new application revision was deployed by this milestone.",
        "",
        "## Reproduction and remaining work",
        "",
        "The source artifact is reports/evaluation/completion-2026-10-29/answer-quality.json; per-mode question checkpoints, "
        "full report tables and the frozen selection are beside it. Run the existing persistent answer-quality script with that "
        "selection/output and matching configuration to load completed checkpoints. Do not rerun completed failures to improve scores. "
        "The source database remains pinned to migration 0002 for this historical comparison, distinct from the current "
        "production schema and BM25 experiments. No migration was applied to the evaluation database.",
        "",
        "Further work needs independently reviewed semantic/entity support, a held-out question set, aggregate public quota "
        "protection, secret references/scoped IAM, and a real authorized push verifying Cloud Build deployment. "
        "Missing retrieval facts, search planning, ambiguous identities and provider availability remain substantive limitations.",
        "",
    ]
    return "\n".join(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(render(json.loads(args.source.read_text(encoding="utf-8"))), encoding="utf-8")


if __name__ == "__main__":
    main()
