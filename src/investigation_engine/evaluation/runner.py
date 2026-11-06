"""Sequential reproducible runs using supplied, ordinary application services."""

import asyncio
from dataclasses import asdict, dataclass
from time import perf_counter

from investigation_engine.application.ports.embedding_errors import EmbeddingProviderError, EmbeddingResponseError
from investigation_engine.application.ports.generation_errors import GenerationProviderError, GenerationResponseError
from investigation_engine.application.ports.repository_errors import RepositoryError
from investigation_engine.evaluation.answer_quality import _response
from investigation_engine.evaluation.cached_queries import CachedQueryMiss
from investigation_engine.evaluation.metrics import answer_scores, evidence_record, retrieval_metrics
from investigation_engine.evaluation.reporting import (
    analysis_markdown,
    append_jsonl,
    benchmark_analysis,
    report_markdown,
    summarize,
    write_json,
)
from investigation_engine.evaluation.serving import pipeline_facts, serving_record, similar_passages
from investigation_engine.evaluation.telemetry import RequestBudgetExceeded, capture_timings, observe

RETRIEVAL_MODES = ("semantic", "lexical", "hybrid")
ANSWER_MODES = ("quick_answer", "investigation")


@dataclass(frozen=True, kw_only=True)
class EvaluationConfig:
    modes: tuple[str, ...] = RETRIEVAL_MODES
    repetitions: int = 3
    warmups: int = 1
    seed: int = 20261106
    final_evidence_limit: int = 20
    semantic_candidates: int = 20
    lexical_candidates: int = 20
    semantic_backend: str = "hnsw"
    lexical_backend: str = "bm25"
    hnsw_ef_search: int = 400
    hnsw_candidate_limits: tuple[int, ...] = (400,)
    hnsw_iterative_scan: bool = False
    rrf_k: int = 60
    answer_retrieval_mode: str = "hybrid"
    request_timeout_seconds: float = 120
    allow_generation: bool = False
    max_generation_requests: int = 0
    include_evidence_text: bool = False
    concurrency: int = 1
    serving_max_evidence: int = 5
    serving_max_context_chars: int = 12000

    def __post_init__(self):
        if (
            not self.modes
            or len(set(self.modes)) != len(self.modes)
            or set(self.modes) - set(RETRIEVAL_MODES + ANSWER_MODES)
        ):
            raise ValueError("Distinct supported evaluation modes are required")
        if (
            type(self.repetitions) is not int
            or self.repetitions < 1
            or type(self.warmups) is not int
            or self.warmups < 0
        ):
            raise ValueError("Positive repetitions and nonnegative warmups are required")
        if self.concurrency != 1:
            raise ValueError("This benchmark deliberately supports sequential execution only")
        if (
            type(self.serving_max_evidence) is not int
            or not 1 <= self.serving_max_evidence <= 10
            or type(self.serving_max_context_chars) is not int
            or not 256 <= self.serving_max_context_chars <= 64000
        ):
            raise ValueError("Invalid serving evidence/context budgets")
        if any(
            type(value) is not int or not 1 <= value <= 100
            for value in (self.final_evidence_limit, self.semantic_candidates, self.lexical_candidates)
        ):
            raise ValueError("Evidence and channel limits must be integers from one to 100")
        if (
            type(self.rrf_k) is not int
            or not 0 <= self.rrf_k <= 10000
            or self.semantic_backend not in ("hnsw", "exact")
            or self.lexical_backend != "bm25"
        ):
            raise ValueError("Select a valid semantic backend, BM25 and RRF configuration")
        if (
            type(self.hnsw_ef_search) is not int
            or not 1 <= self.hnsw_ef_search <= 1000
            or not self.hnsw_candidate_limits
            or any(type(value) is not int or not 1 <= value <= 100000 for value in self.hnsw_candidate_limits)
            or tuple(sorted(set(self.hnsw_candidate_limits))) != self.hnsw_candidate_limits
            or self.semantic_backend == "hnsw"
            and not self.hnsw_iterative_scan
            and max(self.hnsw_candidate_limits) > self.hnsw_ef_search
        ):
            raise ValueError("Invalid HNSW candidate/ef configuration")
        if self.answer_retrieval_mode not in ("semantic", "hybrid") or not 0 < self.request_timeout_seconds <= 3600:
            raise ValueError("Invalid answer retrieval mode or request timeout")
        if set(self.modes) & set(ANSWER_MODES) and (not self.allow_generation or self.max_generation_requests < 1):
            raise ValueError(
                "Answer evaluation requires --allow-generation and an explicit positive --max-generation-requests"
            )

    def json(self):
        return asdict(self)


def error_category(error):
    seen, current = set(), error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if (
            isinstance(current, TimeoutError)
            or "Timeout" in type(current).__name__
            or getattr(current, "sqlstate", None) == "57014"
        ):
            return "timeout"
        current = current.__cause__ or current.__context__
    if isinstance(error, CachedQueryMiss):
        return "missing_cached_embedding"
    if isinstance(error, RequestBudgetExceeded):
        return "request_budget"
    if "RateLimit" in type(error).__name__:
        return "rate_limit"
    if isinstance(error, (GenerationProviderError, EmbeddingProviderError)):
        return "provider"
    if isinstance(error, (GenerationResponseError, EmbeddingResponseError)):
        return "invalid_response"
    if isinstance(error, RepositoryError):
        return "repository"
    return "validation" if isinstance(error, ValueError) else "internal"


async def run_evaluation(cases, configuration, jobs, *, output, manifest, availability=None, progress=None):
    """Jobs contain services, never gold. References are accessed only after execution.

    A job is {service, answer_run?}; answer_run reuses the existing result adapter.
    Warmup calls consume the same live request budget as measured calls.
    """
    if len({case["id"] for case in cases}) != len(cases) or set(jobs) != set(configuration.modes):
        raise ValueError("Distinct question IDs and exactly the configured jobs are required")
    output.mkdir(parents=True, exist_ok=False)
    manifest = {**manifest, "configuration": configuration.json(), "status": "running"}
    write_json(output / "manifest.json", manifest)
    if "selection" in manifest:
        write_json(output / "selection.json", manifest["selection"])
    for filename in ("retrieval_results.jsonl", "answer_results.jsonl", "questions.jsonl"):
        (output / filename).touch()
    if configuration.include_evidence_text:
        (output / "grounding_review.jsonl").touch()
    for case in cases:
        append_jsonl(output / "questions.jsonl", case)
    rows = []
    interrupted = True
    try:
        with capture_timings():
            for trial in range(configuration.warmups + configuration.repetitions):
                # Alternate mode order to reduce systematic first/last bias.
                order = configuration.modes if trial % 2 == 0 else tuple(reversed(configuration.modes))
                for case in cases:
                    for mode in order:
                        job = jobs[mode]
                        started = perf_counter()
                        hits, response, error = (), None, None
                        status = "failed"
                        with observe() as observation:
                            try:
                                async with asyncio.timeout(configuration.request_timeout_seconds):
                                    service = job["service"]
                                    if mode in ANSWER_MODES:
                                        result = await service.execute(case["question"], owner_id=None)
                                        response = _response(job["answer_run"], result)
                                        hits = tuple(item for _, item in response.evidence)
                                        status = response.status
                                    elif mode == "lexical":
                                        hits = await service.execute_keywords(
                                            case["question"], configuration.final_evidence_limit, owner_id=None
                                        )
                                        status = "ok"
                                    else:
                                        hits = await service.execute(
                                            case["question"], configuration.final_evidence_limit, owner_id=None
                                        )
                                        status = "ok"
                            except Exception as caught:
                                error = {"category": error_category(caught), "type": type(caught).__name__}
                        elapsed = (perf_counter() - started) * 1000
                        channels = {
                            name: [hit for item in observation.channels for hit in item[name]]
                            for name in ("semantic", "lexical")
                        }
                        candidates = tuple(channels["semantic"] + channels["lexical"])
                        selected = [evidence_record(hit, rank, include_text=False) for rank, hit in enumerate(hits, 1)]
                        if response:
                            for item, (citation_id, _) in zip(selected, response.evidence, strict=True):
                                item["citation_id"] = citation_id
                        metrics = retrieval_metrics(
                            hits,
                            case["gold"],
                            candidates=candidates,
                            availability=(availability or {}).get(case["id"], {}),
                            final_observed=status != "failed",
                        )
                        serving = None
                        if status == "ok" and mode in RETRIEVAL_MODES and job.get("preparer") is not None:
                            serving_started = perf_counter()
                            prepared, serving_error = None, None
                            with observe() as serving_observation:
                                try:
                                    async with asyncio.timeout(configuration.request_timeout_seconds):
                                        prepared = await job["preparer"].prepare_evidence(
                                            case["question"], owner_id=None
                                        )
                                except Exception as caught:
                                    serving_error = {"category": error_category(caught), "type": type(caught).__name__}
                            serving_duration = (perf_counter() - serving_started) * 1000
                            serving = (
                                serving_record(
                                    prepared,
                                    hits,
                                    case["gold"],
                                    max_evidence=configuration.serving_max_evidence,
                                    availability=(availability or {}).get(case["id"], {}),
                                )
                                if prepared
                                else {"status": "failed"}
                            )
                            serving.update(
                                error=serving_error,
                                duration_ms=serving_duration,
                                timings=serving_observation.timings(),
                                max_context_chars=configuration.serving_max_context_chars,
                                repository_calls=len(serving_observation.channels),
                            )
                        row = {
                            "question_id": case["id"],
                            "question_type": case["question_type"],
                            "mode": mode,
                            "repetition": trial - configuration.warmups + 1,
                            "warmup": trial < configuration.warmups,
                            "configuration": configuration.json(),
                            "status": status,
                            "request_status": "failed" if error else "completed",
                            "duration_ms": elapsed,
                            "timings": observation.timings(),
                            "error": error,
                            "metrics": metrics,
                            "channel_metrics": {
                                name: retrieval_metrics(items, case["gold"]) for name, items in channels.items()
                            },
                            "serving": serving,
                            "pipeline_facts": pipeline_facts(
                                hits,
                                channels,
                                case["gold"],
                                serving,
                                (availability or {}).get(case["id"], {}),
                                retrieval_observed=status == "ok",
                            ),
                            "passage_similarity_heuristic": similar_passages(hits)
                            if not (trial < configuration.warmups) and mode in RETRIEVAL_MODES
                            else None,
                            "selected_evidence": selected,
                            "candidates": {
                                name: [evidence_record(hit, rank) for rank, hit in enumerate(items, 1)]
                                for name, items in channels.items()
                            },
                            "candidate_counts": {name: len(items) for name, items in channels.items()},
                            "retrieval_actions": max(
                                len(observation.channels),
                                len(observation.stages.get("retrieval.total", ()))
                                + len(observation.stages.get("retrieval.lexical_total", ())),
                            ),
                            "repository_calls": len(observation.channels),
                            "investigation_search_records": len(response.searches)
                            if response and mode == "investigation"
                            else None,
                            "generation_calls": len(observation.generation),
                            "generation_provider_requests": sum(
                                call["provider_requests"] for call in observation.generation
                            )
                            if all(call["provider_requests"] is not None for call in observation.generation)
                            else None,
                            "generation": observation.generation,
                            "answer_reference_available": case.get("reference_answer") is not None,
                            "answer_metrics": answer_scores(
                                response.answer if response else None, case.get("reference_answer")
                            )
                            if mode in ANSWER_MODES
                            else None,
                            "answer": response.answer if response else None,
                            "evidence_ids": [key for key, _ in response.evidence] if response else [],
                            "citation_ids": response.citations if response else [],
                            "citation_structure_valid": (
                                len(set(response.citations)) == len(response.citations)
                                and set(response.citations) <= {key for key, _ in response.evidence}
                            )
                            if response
                            else None,
                            "factual_support": "requires_manual_review"
                            if response and response.citations
                            else "not_assessed",
                            "usage": response.usage if response else None,
                            "model_version": response.model_version if response else None,
                            "searches": response.searches if response else [],
                            "cache_status": "cached_only; misses fail; no embedding network calls",
                            "provider_network_included": bool(observation.generation),
                        }
                        # Passage/candidate exports go straight to disk. Keep only
                        # summary inputs in memory for explicitly larger samples.
                        rows.append(
                            {
                                key: value
                                for key, value in row.items()
                                if key
                                not in (
                                    "selected_evidence",
                                    "candidates",
                                    "searches",
                                    "configuration",
                                    "answer",
                                    "evidence_ids",
                                    "citation_ids",
                                    "usage",
                                )
                            }
                        )
                        filename = "answer_results.jsonl" if mode in ANSWER_MODES else "retrieval_results.jsonl"
                        append_jsonl(output / filename, row)
                        if configuration.include_evidence_text:
                            append_jsonl(
                                output / "grounding_review.jsonl",
                                {
                                    "question_id": case["id"],
                                    "question": case["question"],
                                    "reference_answer": case.get("reference_answer"),
                                    "generated_answer": row["answer"],
                                    "mode": mode,
                                    "repetition": row["repetition"],
                                    "warmup": row["warmup"],
                                    "citation_ids": row["citation_ids"],
                                    "review_status": "pending",
                                    "correctness": None,
                                    "factual_support": None,
                                    "evidence": [
                                        {
                                            **evidence_record(hit, rank, include_text=True),
                                            "evidence_id": response.evidence[rank - 1][0] if response else None,
                                        }
                                        for rank, hit in enumerate(hits, 1)
                                    ],
                                },
                            )
                        if progress:
                            progress(
                                {
                                    "question_id": case["id"],
                                    "mode": mode,
                                    "warmup": row["warmup"],
                                    "status": status,
                                    "duration_ms": round(elapsed, 2),
                                    "error": error,
                                }
                            )
        interrupted = False
    finally:
        manifest["status"] = "interrupted" if interrupted else "finalizing"
        manifest["generation_requests_observed"] = sum(row["generation_calls"] for row in rows)
        manifest["generation_provider_requests_known"] = sum(row["generation_provider_requests"] or 0 for row in rows)
        manifest["provider_request_count_complete"] = all(
            row["generation_provider_requests"] is not None for row in rows
        )
        write_json(output / "manifest.json", manifest)
        summary = summarize(rows)
        write_json(output / "summary.json", summary)
        analysis = benchmark_analysis(output)
        write_json(output / "analysis.json", analysis)
        (output / "report.md").write_text(
            report_markdown(summary, manifest) + analysis_markdown(analysis), encoding="utf-8"
        )
        if not interrupted:
            manifest["status"] = "complete"
            write_json(output / "manifest.json", manifest)
    return summary
