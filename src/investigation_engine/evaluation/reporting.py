"""Saved, comparable run artifacts. Aggregate reports contain no passage text."""

import json
from collections import Counter
from pathlib import Path
from statistics import mean

from investigation_engine.evaluation.answer_focus import assessment_summary
from investigation_engine.evaluation.investigation import investigation_summary
from investigation_engine.evaluation.metrics import statistics
from investigation_engine.ingestion.corpus_processing import atomic_json


def write_json(path, value):
    # Preserve the last valid manifest if a replacement cannot be written.
    serializable = json.loads(json.dumps(value, allow_nan=False, default=str))
    atomic_json(Path(path), serializable)


def append_jsonl(path, value):
    with Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False, default=str) + "\n")


def read_jsonl(path):
    return list(iter_jsonl(path))


def iter_jsonl(path):
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def serving_summary(rows):
    observed = [row["serving"] for row in rows if row.get("serving") and not row["serving"].get("error")]
    total = len(rows)
    result = {
        "observed_attempts": len(observed),
        "attempts": total,
        "observed_questions": len(
            {row["question_id"] for row in rows if row.get("serving") and not row["serving"].get("error")}
        ),
        "statuses": dict(Counter(row["serving"]["status"] if row.get("serving") else "unavailable" for row in rows)),
        "recall": sum(item["recall"] or 0 for item in observed) / total if observed else None,
        "complete_support": sum(item["complete_support"] for item in observed) / total if observed else None,
        "duration_ms": statistics([item["duration_ms"] for item in observed]),
        "admission_ms": statistics([item["timings"]["evidence_selection_ms"] for item in observed]),
        "retrieval_prefix_mismatches": sum(not item["retrieval_matches_ranked_prefix"] for item in observed),
    }
    for key in (
        "admitted_passages",
        "context_chars",
        "admitted_text_chars",
        "distinct_source_documents",
        "supporting_facts_lost_from_ranked",
        "rejected_evidence_count_limit",
        "admission_count_limit_rejections",
        "rejected_context_budget",
        "rejected_canonical_duplicates",
    ):
        result[key] = statistics([item[key] for item in observed])
    result["timings_ms"] = {
        key: statistics([item["timings"].get(key) for item in observed])
        for key in (
            "query_embedding_ms",
            "connection_acquisition_ms",
            "database_setup_ms",
            "sql_observed_ms",
            "repository_ms",
            "rrf_ms",
            "evidence_selection_ms",
            "total_retrieval_ms",
        )
    }
    return result


def quality_summary(rows):
    count = len(rows)
    available = any(row["status"] not in ("failed", "blocked") for row in rows)
    result = {
        "questions": len({row["question_id"] for row in rows}),
        "attempts": count,
        "measurement_available": available,
        "blocked_missing_cache": bool(rows)
        and all((row.get("error") or {}).get("category") == "missing_cached_embedding" for row in rows),
    }
    for k in (5, 20):
        for source, output in (("recall", "recall"), ("complete", "complete")):
            result[f"{output}_at_{k}"] = (
                sum(row["metrics"]["at_k"][str(k)][source] or 0 for row in rows) / count if count else None
            )
    result["mrr_at_20"] = sum(row["metrics"]["mrr_at_20"] or 0 for row in rows) / count if count else None
    result["serving"] = serving_summary(rows)
    return result


def summarize(rows):
    result = {}
    measured = [row for row in rows if not row["warmup"]]
    for mode in sorted({row["mode"] for row in measured}):
        subset = [row for row in measured if row["mode"] == mode]
        count = len(subset)

        def average(get):
            # Missing annotations/errors cannot improve an operational mean.
            return sum(get(row) or 0 for row in subset) / count

        timing_keys = (
            "query_embedding_ms",
            "connection_acquisition_ms",
            "database_setup_ms",
            "sql_observed_ms",
            "repository_ms",
            "rrf_ms",
            "evidence_selection_ms",
            "total_retrieval_ms",
        )
        result[mode] = {
            "questions": len({row["question_id"] for row in subset}),
            "attempts": count,
            "statuses": dict(Counter(row["status"] for row in subset)),
            "error_categories": dict(Counter(row["error"]["category"] for row in subset if row["error"])),
            "answered": sum(row["status"] == "answered" for row in subset),
            "abstained": sum(row["status"] == "insufficient_evidence" for row in subset),
            "failed_or_incomplete": sum(row["status"] in ("failed", "incomplete") for row in subset),
            "gold_annotated_attempts": sum(bool(row["metrics"]["gold_count"]) for row in subset),
            "unresolvable_gold_facts": sum(
                sum(f["state"] == "unresolvable_gold_mapping" for f in row["metrics"]["supporting_facts"])
                for row in subset
            ),
            "gold_fact_states": dict(
                Counter(fact["state"] for row in subset for fact in row["metrics"]["supporting_facts"])
            ),
            "recall_at_5": average(lambda row: row["metrics"]["at_k"]["5"]["recall"]),
            "recall_at_20": average(lambda row: row["metrics"]["at_k"]["20"]["recall"]),
            "complete_at_5": average(lambda row: row["metrics"]["at_k"]["5"]["complete"]),
            "complete_at_20": average(lambda row: row["metrics"]["at_k"]["20"]["complete"]),
            "mrr_at_20": average(lambda row: row["metrics"]["mrr_at_20"]),
            "strict_occurrence_recall_at_20": average(
                lambda row: row["metrics"]["at_k"]["20"]["strict_occurrence_recall"]
            ),
            "exact_canonical_duplicate_rate": average(lambda row: row["metrics"]["exact_canonical_duplicate_rate"]),
            "distinct_source_documents_mean": average(lambda row: row["metrics"]["distinct_source_documents"]),
            "near_duplicate_rate": None,
            "candidate_counts_mean": {
                channel: mean(row["candidate_counts"][channel] for row in subset) for channel in ("semantic", "lexical")
            },
            "latency_ms_all_attempts": statistics([row["duration_ms"] for row in subset]),
            "latency_ms_successful": statistics([row["duration_ms"] for row in subset if row["status"] != "failed"]),
            "timings_ms": {key: statistics([row["timings"].get(key) for row in subset]) for key in timing_keys},
            "generation_calls": sum(row["generation_calls"] for row in subset),
            "generation_provider_requests_known": sum(row["generation_provider_requests"] or 0 for row in subset),
            "provider_request_count_complete": all(row["generation_provider_requests"] is not None for row in subset),
            "generation_call_ms": statistics([call["duration_ms"] for row in subset for call in row["generation"]]),
            "known_provider_usage": {
                key: sum((call.get("usage") or {}).get(key, 0) for row in subset for call in row["generation"])
                for key in ("prompt_tokens", "candidate_tokens", "thinking_tokens")
            },
            "generation_calls_with_usage": sum(
                call.get("usage") is not None for row in subset for call in row["generation"]
            ),
            "citation_structure": dict(Counter(str(row["citation_structure_valid"]) for row in subset)),
            "retrieval_actions": sum(row["retrieval_actions"] for row in subset),
            "repository_calls": sum(row["repository_calls"] for row in subset),
            "answer_exact_match": average(lambda row: row["answer_metrics"]["exact_match"])
            if any(row["answer_metrics"] for row in subset)
            else None,
            "answer_f1": average(lambda row: row["answer_metrics"]["f1"])
            if any(row["answer_metrics"] for row in subset)
            else None,
            "answer_reference_attempts": sum(row["answer_reference_available"] for row in subset),
        }
        result[mode].update(quality_summary(subset))
        result[mode]["answer_assessment"] = assessment_summary([row.get("answer_assessment") for row in subset])
        result[mode]["pipeline_fact_states"] = dict(
            Counter(fact["state"] for row in subset for fact in row.get("pipeline_facts", []))
        )
        result[mode]["channel_quality"] = {
            channel: {
                f"recall_at_{k}": mean(row["channel_metrics"][channel]["at_k"][str(k)]["recall"] or 0 for row in subset)
                for k in (5, 20)
            }
            for channel in ("semantic", "lexical")
            if any(row["candidate_counts"][channel] for row in subset)
        }
        result[mode]["by_question_type"] = {
            kind: quality_summary(group)
            for kind in sorted({row["question_type"] for row in subset})
            if (group := [row for row in subset if row["question_type"] == kind])
        }
        result[mode]["investigation"] = investigation_summary(subset)
        if result[mode]["investigation"] is not None:
            for kind, data in result[mode]["by_question_type"].items():
                group = [row for row in subset if row["question_type"] == kind]
                data["investigation"] = investigation_summary(group)
                data["answer_exact_match"] = mean(row["answer_metrics"]["exact_match"] or 0 for row in group)
                data["answer_f1"] = mean(row["answer_metrics"]["f1"] or 0 for row in group)
    return {
        "modes": result,
        "denominator": "All measured attempts, including errors, absent and unresolved gold. Undefined quality values contribute zero to operational means; annotated/reference counts are reported. Warmups are retained but excluded.",
        "percentiles": "Nearest rank p90/p95; ordinary median. Descriptive only, especially for small samples.",
        "grounding": "Citation identifier validity is structural only. Factual support and entity interpretation require manual review.",
        "answer_scoring_limit": "Official EM/F1 score the whole returned answer unchanged. Separate answer-focused metrics use only conservatively extracted values. Neither text score establishes factual correctness; explicit manual review is required.",
    }


def retrieval_comparison_tables(summary):
    modes = [mode for mode in ("semantic", "lexical", "hybrid") if mode in summary["modes"]]
    if not modes:
        return []
    lines = [
        "",
        "## Retrieval and admitted-context comparison",
        "",
        "| Metric | " + " | ".join(modes) + " |",
        "| --- | " + " | ".join("---:" for _ in modes) + " |",
    ]
    metrics = [
        (key, lambda data, key=key: data[key])
        for key in ("recall_at_5", "recall_at_20", "complete_at_5", "complete_at_20", "mrr_at_20")
    ]
    metrics += [
        ("admitted_recall", lambda data: data["serving"]["recall"]),
        ("admitted_complete_support", lambda data: data["serving"]["complete_support"]),
        ("average_admitted_passages", lambda data: data["serving"]["admitted_passages"]["mean"]),
        ("average_admitted_context_chars", lambda data: data["serving"]["context_chars"]["mean"]),
    ]
    for name, get in metrics:
        values = []
        for mode in modes:
            data = summary["modes"][mode]
            value = get(data) if data.get("measurement_available", True) else None
            values.append("unavailable" if value is None else f"{value:.3f}")
        lines.append("| " + name + " | " + " | ".join(values) + " |")
    lines += [
        "",
        "| Phase / mode | n | Minimum ms | Median ms | Mean ms | p90 ms | p95 ms | Maximum ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode in modes:
        data = summary["modes"][mode]
        for phase, stats in (
            ("ranked retrieval (20)", data["latency_ms_successful"]),
            ("actual serving preparation (5)", data["serving"]["duration_ms"]),
        ):
            values = [
                "unavailable" if stats[key] is None else f"{stats[key]:.2f}"
                for key in ("minimum", "median", "mean", "p90", "p95", "maximum")
            ]
            lines.append(f"| {phase} / {mode} | {stats['count']} | " + " | ".join(values) + " |")
    return lines


def answer_assessment_tables(summary):
    modes = {
        mode: data["answer_assessment"]
        for mode, data in summary.get("modes", {}).items()
        if data.get("answer_assessment") is not None
    }
    if not modes:
        return []

    def number(value):
        return f"{value:.6f}" if value is not None else "unavailable"

    lines = [
        "",
        "## Answer-focused metrics and manual factual review",
        "",
        "| Mode | Official whole-response EM | Official whole-response F1 | Focused EM | Focused F1 | Focused n / attempts | Ambiguous | Unreviewed | Reviewed accuracy | Reviewed n | Partial | Unverifiable |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode, data in modes.items():
        lines.append(
            f"| {mode} | {number(data['official_whole_response_em'])} | {number(data['official_whole_response_f1'])} | {number(data['answer_focused_em'])} | {number(data['answer_focused_f1'])} | {data['answer_focused_denominator']} / {data['attempts']} | {data['ambiguous_answers']} | {data['unreviewed_answers']} | {number(data['reviewed_answer_accuracy'])} | {data['reviewed_accuracy_denominator']} | {data['judgment_counts']['partially_correct']} | {data['judgment_counts']['unverifiable']} |"
        )
    lines += [
        "",
        next(iter(modes.values()))["denominators"],
        "",
        "Focused means are conditional on extraction availability and are not comparable to official all-attempt means without their denominators. Reliable extraction means a supported surface form was isolated, not that the answer is true. Factual judgments remain unverified until explicitly reviewed; pending reviews never become correct/incorrect automatically.",
        "",
    ]
    return lines


def investigation_comparison_tables(summary):
    modes = [(mode, data) for mode, data in summary["modes"].items() if data.get("investigation") is not None]
    if not modes:
        return []
    lines = [
        "",
        "## End-to-end Investigation comparison",
        "",
        "Actual InvestigateQuestion executions. Each configuration may generate different queries. Answer EM/F1 score the whole returned text; they are not factual correctness judgments. Missing/failed observations remain in operational denominators.",
        "",
        "| Configuration | Questions | Attempts | Official whole-response EM | Official whole-response F1 | Complete final context | Mean searches | Mean generation calls | Median ms | p95 ms | Failed/incomplete |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode, data in modes:
        inv = data["investigation"]
        lines.append(
            f"| {mode} | {data['questions']} | {data['attempts']} | {data['answer_exact_match']:.3f} | {data['answer_f1']:.3f} | {inv['coverage']['final_generation']['complete_all_attempts']:.3f} | {inv['average_searches']:.2f} | {inv['average_generation_calls']:.2f} | {data['latency_ms_all_attempts']['median']:.2f} | {data['latency_ms_all_attempts']['p95']:.2f} | {data['failed_or_incomplete']} |"
        )
    lines.extend(
        [
            "",
            "| Configuration | First-search recall | All-search recall | Accumulated recall | Final-context recall | Final contexts observed | Abstentions |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for mode, data in modes:
        stages = data["investigation"]["coverage"]
        cells = " | ".join(
            f"{stages[key]['recall_all_attempts']:.3f}"
            for key in ("first_search", "all_searches", "accumulated", "final_generation")
        )
        lines.append(f"| {mode} | {cells} | {stages['final_generation']['observed_attempts']} | {data['abstained']} |")
    lines.extend(
        [
            "",
            "Investigation's generic Recall@K fields describe returned accumulated registry order, not a single ranking or the final model context. Use the Investigation stage metrics above. No final context is invented for incomplete/provider-failed executions. Search durations include embedding; generation durations include pacing; component timings overlap. Inspect investigation_traces.jsonl for exact queries, per-search hits, each actual model-turn transcript, accumulated registry, final-decision context, scores, citations and usage. Full excerpts are retained only with --local-diagnostics.",
            "",
        ]
    )
    return lines


def report_markdown(summary, manifest):
    if summary["modes"] and all(data.get("investigation") is not None for data in summary["modes"].values()):
        lines = [
            "# Structured Investigation evaluation",
            "",
            f"Run: {manifest['run_id']}",
            f"Questions: {len(manifest['question_ids'])}; repetitions: {manifest['configuration']['repetitions']}; warmups: {manifest['configuration']['warmups']}; concurrency: 1.",
        ]
        lines += investigation_comparison_tables(summary)
        lines += answer_assessment_tables(summary)
        lines += [
            "",
            "| Configuration / type | Questions | Attempts | Official whole-response EM | Official whole-response F1 | Final-context recall | Complete final context |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for mode, data in summary["modes"].items():
            for kind, group in data["by_question_type"].items():
                final = group["investigation"]["coverage"]["final_generation"]
                lines.append(
                    f"| {mode} / {kind} | {group['questions']} | {group['attempts']} | {group['answer_exact_match']:.3f} | {group['answer_f1']:.3f} | {final['recall_all_attempts']:.3f} | {final['complete_all_attempts']:.3f} |"
                )
        lines += [
            "",
            "## Latency and usage",
            "",
            "| Configuration / phase | n | Min ms | Median ms | Mean ms | p90 ms | p95 ms | Max ms |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        usage_lines = []
        for mode, data in summary["modes"].items():
            inv = data["investigation"]
            for phase, values in (
                ("total Investigation", data["latency_ms_all_attempts"]),
                ("individual searches", inv["search_duration_ms"]),
                ("total retrieval", inv["total_retrieval_ms"]),
                ("individual generation calls", data["generation_call_ms"]),
                ("total generation", inv["total_generation_ms"]),
                ("total embedding", inv["total_embedding_ms"]),
            ):
                cells = " | ".join(
                    f"{values[key]:.3f}" if values[key] is not None else "unavailable"
                    for key in ("minimum", "median", "mean", "p90", "p95", "maximum")
                )
                lines.append(f"| {mode} / {phase} | {values['count']} | {cells} |")
            usage_lines.append(
                f"\n{mode}: actual generation RPCs known={data['generation_provider_requests_known']}; request accounting complete={data['provider_request_count_complete']}; cache hits={inv['embedding_cache_hits']}, misses={inv['embedding_cache_misses']}; reported token usage={data['known_provider_usage']}; failed/incomplete={data['failed_or_incomplete']}.\n"
            )
        lines += usage_lines
        lines += [
            "",
            summary["denominator"],
            "",
            summary["percentiles"],
            "",
            summary["grounding"],
            "",
            summary["answer_scoring_limit"],
            "",
            "See summary.json for all component timings, limits, availability and statuses; see the manifest for embedding RPC totals and cache fingerprints. Final context unavailable is distinct from observed zero coverage. All-attempt stage means conservatively score unavailable stages as zero. With one question per configuration, percentiles equal that single observed latency and do not describe a population. No statistical or causal comparison is implied.",
        ]
        return "\n".join(lines) + "\n"
    lines = [
        "# Structured evaluation",
        "",
        f"Run: {manifest['run_id']}",
        f"Questions: {len(manifest['question_ids'])}; repetitions: {manifest['configuration']['repetitions']}; warmups: {manifest['configuration']['warmups']}; concurrency: 1.",
        "",
        "| Mode | Attempts | Recall@5 | Recall@20 | Complete@20 | MRR@20 | Median ms | p95 ms | Failed/incomplete |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode, value in summary["modes"].items():
        latency = value["latency_ms_successful"]
        if not value.get("measurement_available", True):
            lines.append(
                f"| {mode} | {value['attempts']} | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | {value['failed_or_incomplete']} |"
            )
            continue
        lines.append(
            f"| {mode} | {value['attempts']} | {value['recall_at_5']:.3f} | {value['recall_at_20']:.3f} | {value['complete_at_20']:.3f} | {value['mrr_at_20']:.3f} | {latency['median']:.2f} | {latency['p95']:.2f} | {value['failed_or_incomplete']} |"
        )
    lines += [
        "",
        "Candidate ranking and actual serving admission are distinct measurements. Serving preparation performs a separate production-limit search; its latency includes retrieval and admission, with no generation.",
        "",
        "| Mode | Admitted recall | Admitted complete | Mean passages | Mean context chars | Mean facts lost | Preparation median ms | Preparation p95 ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode, value in summary["modes"].items():
        serving = value.get("serving", {})
        if not serving.get("observed_attempts"):
            lines.append(
                f"| {mode} | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable |"
            )
        else:
            lines.append(
                f"| {mode} | {serving['recall']:.3f} | {serving['complete_support']:.3f} | {serving['admitted_passages']['mean']:.2f} | {serving['context_chars']['mean']:.1f} | {serving['supporting_facts_lost_from_ranked']['mean']:.2f} | {serving['duration_ms']['median']:.2f} | {serving['duration_ms']['p95']:.2f} |"
            )
    lines += retrieval_comparison_tables(summary)
    lines += investigation_comparison_tables(summary)
    lines += [
        "",
        "| Mode / type | Questions | Measured attempts | Recall@20 | Complete@20 | Admitted recall | Admitted complete |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for mode, value in summary["modes"].items():
        for kind, data in value["by_question_type"].items():
            if not data["measurement_available"]:
                lines.append(
                    f"| {mode} / {kind} | {data['questions']} | {data['attempts']} | unavailable | unavailable | unavailable | unavailable |"
                )
            else:
                serving = data["serving"]
                lines.append(
                    f"| {mode} / {kind} | {data['questions']} | {data['attempts']} | {data['recall_at_20']:.3f} | {data['complete_at_20']:.3f} | {serving['recall']} | {serving['complete_support']} |"
                )
    answering = {mode: value for mode, value in summary["modes"].items() if value["answer_exact_match"] is not None}
    if answering:
        lines += [
            "",
            "| Mode | Official whole-response EM | Official whole-response F1 | Answered | Abstained | Generation calls |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for mode, value in answering.items():
            lines.append(
                f"| {mode} | {value['answer_exact_match']:.3f} | {value['answer_f1']:.3f} | {value['answered']} | {value['abstained']} | {value['generation_calls']} |"
            )
    lines += [
        "",
        summary["denominator"],
        "",
        summary["percentiles"],
        "",
        summary["grounding"],
        "",
        summary["answer_scoring_limit"],
        "",
        "Timing boundaries overlap; do not sum them. SQL timings are application-observed; EXPLAIN server time is unavailable unless collected separately.",
        "",
        "See summary.json for all latency statistics, channel counts, duplicates, type strata and errors. Detailed passage text is absent unless local diagnostics were explicitly enabled.",
    ]
    lines += answer_assessment_tables(summary)
    return "\n".join(lines) + "\n"


def compare_runs(left, right, *, left_mode, right_mode):
    left, right = Path(left), Path(right)
    manifests = [json.loads((directory / "manifest.json").read_text(encoding="utf-8")) for directory in (left, right)]
    summaries = [json.loads((directory / "summary.json").read_text(encoding="utf-8")) for directory in (left, right)]
    a, b = summaries[0]["modes"][left_mode], summaries[1]["modes"][right_mode]

    def grouped(directory, mode):
        groups = {}
        for filename in ("retrieval_results.jsonl", "answer_results.jsonl"):
            for row in iter_jsonl(directory / filename):
                if row["mode"] == mode and not row["warmup"]:
                    groups.setdefault(row["question_id"], []).append(
                        {
                            key: row.get(key)
                            for key in ("metrics", "selected_evidence", "answer_metrics", "status", "serving")
                        }
                    )
        return groups

    groups = [grouped(left, left_mode), grouped(right, right_mode)]
    ids = sorted(set(groups[0]) | set(groups[1]))
    changes = []
    for key in ids:
        first, second = groups[0].get(key, []), groups[1].get(key, [])

        def quality(rows):
            return (
                mean(row["metrics"]["at_k"]["20"]["complete"] for row in rows)
                if any(row["status"] != "failed" for row in rows)
                else None
            )

        qa, qb = quality(first), quality(second)

        def evidence(rows):
            return sorted(
                {
                    (hit["content_sha256"], hit["start"], hit["end"], hit["document_version_id"])
                    for row in rows
                    for hit in row["selected_evidence"]
                }
            )

        def answers(rows, metric):
            values = [row["answer_metrics"][metric] for row in rows if row["answer_metrics"]]
            return mean(value or 0 for value in values) if values else None

        def ordering(rows):
            return sorted(
                {
                    json.dumps(
                        [
                            (
                                hit["content_sha256"],
                                hit["start"],
                                hit["end"],
                                hit["rank"],
                                hit["score"],
                                hit["score_kind"],
                            )
                            for hit in row["selected_evidence"]
                        ]
                    )
                    for row in rows
                }
            )

        changes.append(
            {
                "question_id": key,
                "left_complete_rate": qa,
                "right_complete_rate": qb,
                "newly_successful": qa == 0 and qb is not None and qb > 0,
                "newly_failing": qa is not None and qa > 0 and qb == 0,
                "left_statuses": dict(Counter(row["status"] for row in first)),
                "right_statuses": dict(Counter(row["status"] for row in second)),
                "evidence_changed": evidence(first) != evidence(second),
                "left_answer_em": answers(first, "exact_match"),
                "right_answer_em": answers(second, "exact_match"),
                "left_answer_f1": answers(first, "f1"),
                "right_answer_f1": answers(second, "f1"),
                "left_admitted_recall": mean(
                    row["serving"]["recall"] or 0
                    for row in first
                    if row.get("serving") and not row["serving"].get("error")
                )
                if any(row.get("serving") and not row["serving"].get("error") for row in first)
                else None,
                "right_admitted_recall": mean(
                    row["serving"]["recall"] or 0
                    for row in second
                    if row.get("serving") and not row["serving"].get("error")
                )
                if any(row.get("serving") and not row["serving"].get("error") for row in second)
                else None,
                "ranks_or_scores_changed": ordering(first) != ordering(second),
                "missing_side": "left" if not first else "right" if not second else None,
                "left_evidence": evidence(first),
                "right_evidence": evidence(second),
            }
        )
    differences = {}
    for key in (
        "dataset",
        "question_ids",
        "corpus",
        "chunking",
        "embedding",
        "configuration",
        "reranker",
        "generation",
        "retrieval",
        "serving_observation",
        "answer_scorer",
        "code_sha256",
    ):
        if manifests[0].get(key) != manifests[1].get(key):
            differences[key] = True
    return {
        "left_run": manifests[0]["run_id"],
        "right_run": manifests[1]["run_id"],
        "left_mode": left_mode,
        "right_mode": right_mode,
        "manifest_differences": differences,
        "paired_question_ids": sorted(set(groups[0]) & set(groups[1])),
        "metric_deltas": {
            key: b[key] - a[key]
            if a[key] is not None
            and b[key] is not None
            and a.get("measurement_available", True)
            and b.get("measurement_available", True)
            else None
            for key in (
                "recall_at_5",
                "recall_at_20",
                "complete_at_5",
                "complete_at_20",
                "mrr_at_20",
                "answer_exact_match",
                "answer_f1",
                "exact_canonical_duplicate_rate",
            )
        },
        "latency_deltas_ms": {
            key: b["latency_ms_successful"][key] - a["latency_ms_successful"][key]
            if b["latency_ms_successful"][key] is not None and a["latency_ms_successful"][key] is not None
            else None
            for key in ("minimum", "median", "mean", "p90", "p95", "maximum")
        },
        "serving_metric_deltas": {
            key: b["serving"][key] - a["serving"][key]
            if a.get("serving", {}).get(key) is not None and b.get("serving", {}).get(key) is not None
            else None
            for key in ("recall", "complete_support")
        },
        "investigation_metric_deltas": {
            stage: {
                metric: b["investigation"]["coverage"][stage][metric] - a["investigation"]["coverage"][stage][metric]
                if a.get("investigation") is not None and b.get("investigation") is not None
                else None
                for metric in ("recall_all_attempts", "complete_all_attempts")
            }
            for stage in ("first_search", "all_searches", "accumulated", "final_generation")
        },
        "questions": changes,
        "caution": "Descriptive differences, not statistical evidence. Check manifest/configuration differences and unmatched IDs before attributing a change.",
    }


def comparison_markdown(result):
    lines = [
        "# Evaluation comparison",
        "",
        f"{result['left_run']} / {result['left_mode']} → {result['right_run']} / {result['right_mode']}",
        "",
        "| Metric | Right minus left |",
        "| --- | ---: |",
    ]
    lines += [f"| {key} | {value} |" for key, value in result["metric_deltas"].items()]
    lines += [
        f"| latency {key}, ms | {value if value is not None else 'unavailable'} |"
        for key, value in result["latency_deltas_ms"].items()
    ]
    lines += [
        f"| admitted {key} | {value if value is not None else 'unavailable'} |"
        for key, value in result.get("serving_metric_deltas", {}).items()
    ]
    lines += [
        f"| Investigation {stage} / {metric} | {value} |"
        for stage, metrics in result.get("investigation_metric_deltas", {}).items()
        for metric, value in metrics.items()
        if value is not None
    ]
    lines += [
        "",
        f"Newly successful: {[row['question_id'] for row in result['questions'] if row['newly_successful']]}",
        f"Newly failing: {[row['question_id'] for row in result['questions'] if row['newly_failing']]}",
        f"Evidence changed: {[row['question_id'] for row in result['questions'] if row['evidence_changed']]}",
        f"Answer EM/F1 changed: {[row['question_id'] for row in result['questions'] if (row['left_answer_em'], row['left_answer_f1']) != (row['right_answer_em'], row['right_answer_f1'])]}",
        f"Manifest differences: {sorted(result['manifest_differences'])}",
        "",
        result["caution"],
    ]
    return "\n".join(lines) + "\n"


def benchmark_analysis(directory):
    """Question-level findings use one recorded observation, never repetitions as new cases."""
    references = {row["id"]: row for row in iter_jsonl(directory / "questions.jsonl")}
    groups = {}
    for row in iter_jsonl(directory / "retrieval_results.jsonl"):
        if not row["warmup"]:
            key = row["question_id"], row["mode"]
            if key not in groups or groups[key]["status"] == "failed" and row["status"] == "ok":
                groups[key] = row
    findings = {
        key: []
        for key in (
            "semantic_higher_admitted_recall",
            "hybrid_higher_admitted_recall",
            "semantic_higher_ranked_recall",
            "hybrid_higher_ranked_recall",
            "bm25_unique_channel_facts",
            "rrf_excluded_channel_facts",
            "rrf_below_serving_limit",
            "serving_lost_facts",
            "unretrieved_facts",
            "similar_passages",
            "serving_retrieval_prefix_differences",
            "unavailable_semantic_hybrid_pairs",
        )
    }
    for question_id, reference in references.items():
        base = {
            "question_id": question_id,
            "question": reference["question"],
            "question_type": reference["question_type"],
        }
        semantic, hybrid = groups.get((question_id, "semantic")), groups.get((question_id, "hybrid"))
        if semantic and hybrid and semantic["status"] == hybrid["status"] == "ok":
            for name, get in (
                ("admitted", lambda row: (row.get("serving") or {}).get("recall")),
                ("ranked", lambda row: row["metrics"]["at_k"]["20"]["recall"]),
            ):
                left, right = get(semantic), get(hybrid)
                if left is not None and right is not None and left != right:
                    findings[f"{'semantic' if left > right else 'hybrid'}_higher_{name}_recall"].append(
                        {**base, "semantic": left, "hybrid": right}
                    )
        else:
            findings["unavailable_semantic_hybrid_pairs"].append(base)
        for mode in ("semantic", "lexical", "hybrid"):
            row = groups.get((question_id, mode))
            if not row or row["status"] != "ok":
                continue
            serving = row.get("serving")
            if serving and not serving.get("error") and not serving["retrieval_matches_ranked_prefix"]:
                findings["serving_retrieval_prefix_differences"].append(
                    {
                        **base,
                        "mode": mode,
                        "ranked_ids": [hit["evidence_id"] for hit in row["selected_evidence"]],
                        "serving_retrieved_ids": [hit["evidence_id"] for hit in serving["retrieved"]],
                    }
                )
            for fact in row.get("pipeline_facts", []):
                gold = reference["gold"][fact["gold_index"]]
                detail = {
                    **base,
                    "mode": mode,
                    **fact,
                    "supporting_title": gold.get("title"),
                    "sentence_index": gold["sentence_index"],
                }
                if fact["state"] == "ranked_excluded_from_serving":
                    findings["serving_lost_facts"].append(detail)
                    if (
                        mode == "hybrid"
                        and fact["exclusion_reason"] == "evidence_count_limit"
                        and any(
                            rank <= serving["max_evidence"]
                            for ranks in fact["channel_ranks"].values()
                            for rank in ranks
                        )
                        and all(rank > serving["max_evidence"] for rank in fact["ranked_ranks"])
                    ):
                        findings["rrf_below_serving_limit"].append(detail)
                if fact["state"] == "available_not_retrieved_by_channels":
                    findings["unretrieved_facts"].append(detail)
                if mode == "hybrid" and fact["channels"] == ["lexical"]:
                    findings["bm25_unique_channel_facts"].append(detail)
                if fact["state"] == "channel_evidence_excluded_from_ranked_top20":
                    findings["rrf_excluded_channel_facts"].append(detail)
            if (row.get("passage_similarity_heuristic") or {}).get("pairs"):
                findings["similar_passages"].append({**base, "mode": mode, **row["passage_similarity_heuristic"]})
    return {
        "basis": "First observed successful measured repetition per question/mode. See JSONL for all repetitions and failures; these findings are descriptive, not statistical.",
        "observed_questions_by_mode": dict(Counter(mode for (_, mode), row in groups.items() if row["status"] == "ok")),
        "cross_mode_analysis_available_questions": sum(
            1
            for key in references
            if all(groups.get((key, mode), {}).get("status") == "ok" for mode in ("semantic", "hybrid"))
        ),
        "findings": findings,
    }


def analysis_markdown(analysis):
    lines = [
        "",
        "## Per-question findings",
        "",
        analysis["basis"],
        f"Observed questions per mode: {analysis['observed_questions_by_mode']}. Comparable semantic/hybrid questions: {analysis['cross_mode_analysis_available_questions']}.",
    ]
    for key, items in analysis["findings"].items():
        lines += ["", f"### {key} — {len(items)} records"]
        for item in items[:8]:
            lines.append(
                f"- {item['question_id']} ({item.get('mode', 'comparison')}): {item['question']}"
                + (
                    f" Supporting fact: {item.get('supporting_title')}, sentence {item.get('sentence_index')}; {item.get('exclusion_reason') or item.get('state')}."
                    if "gold_index" in item
                    else ""
                )
            )
    lines += [
        "",
        "Empty cross-mode findings are unavailable when the required modes did not run. Similarity uses the explicitly approximate 5-token-shingle Jaccard heuristic, never an exact near-duplicate metric. Full records are in analysis.json.",
    ]
    return "\n".join(lines) + "\n"
