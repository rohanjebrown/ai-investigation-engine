"""Run selected frozen cases on the persistent local evaluation database."""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from time import perf_counter

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from index_evaluation_corpus import (  # noqa: E402
    PROFILE,
    MeasuredGemini,
    database_counts,
    database_url,
    verify_registered_corpus,
)

from investigation_engine.application.investigations import InvestigateQuestion  # noqa: E402
from investigation_engine.application.ports.generation_errors import GenerationProviderError  # noqa: E402
from investigation_engine.application.use_cases.answer_question import AnswerQuestion  # noqa: E402
from investigation_engine.application.use_cases.search_evidence import SearchEvidence  # noqa: E402
from investigation_engine.evaluation.answer_quality import (  # noqa: E402
    AnsweringRun,
    _aggregate,
    _by_category,
    _unobserved_coverage,
    evaluate_answers,
    export_answer_quality,
)
from investigation_engine.evaluation.checkpoints import load_checkpoint, save_checkpoint  # noqa: E402
from investigation_engine.evaluation.retrieval import fingerprint  # noqa: E402
from investigation_engine.infrastructure.config import settings  # noqa: E402
from investigation_engine.infrastructure.generation.gemini_answer_generator import GeminiAnswerGenerator  # noqa: E402
from investigation_engine.infrastructure.generation.gemini_investigation_model import (  # noqa: E402
    INVESTIGATION_GUIDANCE_VERSION,
    GeminiInvestigationModel,
)
from investigation_engine.infrastructure.persistence.models import ChunkingConfigurationRow  # noqa: E402
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import _configuration  # noqa: E402
from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import PostgreSQLEvidenceRepository  # noqa: E402

DEFAULT_SELECTION = ROOT / "reports/evaluation/task16-2026-10-28/selection.json"
DEFAULT_OUTPUT = ROOT / "reports/evaluation/task16c-2026-10-28"
GENERATION_MODEL = settings.gemini_generation_model or "gemini-3.6-flash"
MAX_SAMPLE_GENERATION_REQUESTS = 30
GENERATION_INTERVAL_SECONDS = 12.5  # Below the prior user-reported 5 RPM ceiling.
MAX_EMBEDDING_INPUTS = 15
RESERVED_DAILY_REQUESTS = 1


class RequestBudget:
    def __init__(self, cap: int, *, prior_requests: int = 0, prior_by_mode: dict | None = None):
        self.cap = cap
        self.attempted = prior_requests
        self.failed = False
        self.last_request_at = None
        self.by_mode = {"single_step": 0, "multi_step": 0, **(prior_by_mode or {})}

    async def reserve(self, mode: str) -> None:
        if self.failed or self.attempted >= self.cap:
            raise GenerationProviderError("Evaluation generation request budget exhausted")
        now = asyncio.get_running_loop().time()
        if self.last_request_at is not None:
            await asyncio.sleep(max(0.0, GENERATION_INTERVAL_SECONDS - (now - self.last_request_at)))
        self.last_request_at = asyncio.get_running_loop().time()
        self.attempted += 1
        self.by_mode[mode] += 1


class BudgetedAnswerGenerator:
    def __init__(self, provider, budget: RequestBudget):
        self.provider, self.budget = provider, budget
        self.model = provider.model

    async def generate(self, request):
        await self.budget.reserve("single_step")
        try:
            return await self.provider.generate(request)
        except Exception:
            self.budget.failed = True
            raise


class BudgetedInvestigationModel:
    def __init__(self, provider, budget: RequestBudget):
        self.provider, self.budget = provider, budget
        self.model = provider.model

    async def next_action(self, turn):
        await self.budget.reserve("multi_step")
        try:
            return await self.provider.next_action(turn)
        except Exception:
            self.budget.failed = True
            raise


class BudgetedEmbeddings:
    def __init__(self, provider, cap: int, budget: RequestBudget):
        self.provider, self.configuration, self.cap, self.budget = provider, provider.configuration, cap, budget
        self.inputs = 0
        self.cache = {}

    async def embed_query(self, question: str):
        if question in self.cache:
            return self.cache[question]
        if self.inputs >= self.cap:
            raise RuntimeError("Evaluation query-embedding input budget exhausted")
        self.inputs += 1
        try:
            vector = await self.provider.embed_query(question)
        except Exception:
            self.budget.failed = True
            raise
        self.cache[question] = vector
        return vector

    async def embed_documents(self, texts):
        raise RuntimeError("Answer-quality evaluation cannot regenerate document embeddings")


class BoundedInvestigation:
    def __init__(
        self,
        search,
        model,
        *,
        max_turns: int = 6,
        max_search_calls: int = 5,
        max_results_per_search: int = 5,
        max_accumulated_evidence: int = 15,
        max_context_chars: int = 24000,
        max_generation_requests: int = 6,
        max_embedding_requests: int = 5,
    ):
        self.search, self.model = search, model
        self.max_turns = max_turns
        self.max_search_calls = max_search_calls
        self.max_results_per_search = max_results_per_search
        self.max_accumulated_evidence = max_accumulated_evidence
        self.max_context_chars = max_context_chars
        self.max_generation_requests = max_generation_requests
        self.max_embedding_requests = max_embedding_requests

        if max_generation_requests < max_search_calls + 1:
            raise ValueError("Generation budget must allow each search plus a final decision")
        if max_turns < max_search_calls + 1:
            raise ValueError("Turn budget must allow each search plus a final decision")
        if max_embedding_requests < max_search_calls:
            raise ValueError("Embedding budget must allow each configured search")

    async def execute(self, question, *, owner_id):
        return await InvestigateQuestion(
            self.search,
            self.model,
            max_turns=self.max_turns,
            max_search_calls=self.max_search_calls,
            max_results_per_search=self.max_results_per_search,
            max_accumulated_evidence=self.max_accumulated_evidence,
            max_context_chars=self.max_context_chars,
            max_generation_requests=self.max_generation_requests,
            max_embedding_requests=self.max_embedding_requests,
        ).execute(question, owner_id=owner_id)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _paired_comparison(reports: list[dict]) -> dict:
    left, right = reports
    right_rows = {row["id"]: row for row in right["questions"]}
    pairs = [
        (row, right_rows[row["id"]])
        for row in left["questions"]
        if row["id"] in right_rows
        and row.get("status") not in ("failed", "provider_error", "unavailable", "not_run")
        and right_rows[row["id"]].get("status") not in ("failed", "provider_error", "unavailable", "not_run")
    ]
    from statistics import mean

    def delta(get):
        vals = [(get(other) - get(base)) for base, other in pairs if get(base) is not None and get(other) is not None]
        return mean(vals) if vals else None

    return {
        "baseline": left["configuration"]["name"],
        "other": right["configuration"]["name"],
        "paired_cases": len(pairs),
        "paired_question_ids": [a["id"] for a, _ in pairs],
        "outcome_changes": [
            {"id": a["id"], "baseline": a["outcome"], "other": b["outcome"]}
            for a, b in pairs
            if a["outcome"] != b["outcome"]
        ],
        "answer_correctness_changes": [
            {"id": a["id"], "baseline": a["answer_correctness"], "other": b["answer_correctness"]}
            for a, b in pairs
            if a["answer_correctness"] != b["answer_correctness"]
        ],
        "strict_evidence_recall_delta": delta(lambda row: row["retrieval_coverage"]["strict_recall"]),
        "strict_citation_recall_delta": delta(lambda row: row["citation_coverage"]["strict_recall"]),
        "latency_delta_ms": delta(lambda row: row["investigation_behavior"]["latency_ms"]),
        "generation_request_delta": delta(lambda row: row["investigation_behavior"]["generation_requests"]),
        "interpretation": "Paired descriptive differences only; no statistical significance or superiority is inferred.",
    }


def _refresh_aggregates(report: dict) -> None:
    for run in report["runs"]:
        run["aggregate"] = _aggregate(run["questions"])
        run["by_category"] = _by_category(run["questions"])
    report["paired_comparisons"] = [_paired_comparison(report["runs"])] if len(report["runs"]) == 2 else []


def update_row(report: dict, mode: str, question_id: str, row: dict) -> None:
    run_report = next(item for item in report["runs"] if item["configuration"]["mode"] == mode)
    row_index = next(i for i, current in enumerate(run_report["questions"]) if current["id"] == question_id)
    run_report["questions"][row_index] = row


def _lost_result_row(case: dict) -> dict:
    unobserved = _unobserved_coverage(case["required_evidence"])
    return {
        "id": case["id"],
        "question": case["question"],
        "category": case["category"],
        "expected_behavior": case["expected_behavior"],
        "expected_answer": case["expected_answer"],
        "acceptable_answers": case.get("acceptable_answers")
        or ([case["expected_answer"]] if case["expected_answer"] else []),
        "required_evidence": case["required_evidence"],
        "status": "unavailable",
        "outcome": "unavailable",
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
        "failure_analysis": {
            "classification": "result_persistence_failure",
            "observation": "The Task 16 application calls completed, but a local report aggregator raised before their result rows were written.",
            "hypothesis": "Exact answers and evidence are unavailable. Calls were not repeated to avoid regenerating successful responses.",
        },
        "semantic_claim_support": {"status": "not_assessed", "supported_claims": None, "unsupported_claims": None},
        "investigation_behavior": {
            "search_count": None,
            "generation_requests": None,
            "embedding_query_requests": None,
            "latency_ms": None,
            "required_facts_retrieved": None,
            "required_facts_cited": None,
            "budget_exhausted": None,
            "evidence_gap_at_stop": None,
            "follow_up_searches_with_new_required_evidence": None,
        },
        "usage": None,
        "model_version": GENERATION_MODEL,
        "error_type": "LocalReportSerializationError",
        "provider_diagnostic": None,
    }


async def run(args) -> dict:
    if args.continue_pending_id:
        raise RuntimeError(
            "Saved evidence checkpoints contain no native Gemini conversation state. "
            "Continuation is unavailable; start a new investigation in a fresh quota window."
        )
    request_cap = min(MAX_SAMPLE_GENERATION_REQUESTS, max(0, args.daily_rpd_limit - args.reserve_rpd))
    prior_by_mode = {"single_step": args.prior_single_step_requests, "multi_step": args.prior_multi_step_requests}
    prior_requests = sum(prior_by_mode.values())
    if prior_requests >= request_cap:
        raise RuntimeError("Known prior generation attempts leave no allowance under the selected daily request cap")
    generation_model = args.generation_model
    selection = _read_json(args.selection)
    selected_ids = tuple(selection["question_ids"])
    suite = _read_json(ROOT / "data/samples/evaluation/suite.json")
    corpus = _read_json(ROOT / "data/samples/evaluation/corpus.json")
    if tuple(selected_ids) != tuple(item["id"] for item in selection["selection"]):
        raise ValueError("Frozen selection file has inconsistent question IDs")
    if args.case_id:
        if len(set(args.case_id)) != len(args.case_id) or any(item not in selected_ids for item in args.case_id):
            raise ValueError("Selected case IDs must be unique members of the frozen sample")
        selected_ids = tuple(args.case_id)
    url = database_url()
    verified = verify_registered_corpus(url, corpus)
    from sqlalchemy import create_engine, text

    sync_engine = create_engine(url, hide_parameters=True)
    try:
        with sync_engine.connect() as connection:
            if connection.scalar(text("SELECT current_database()")) != "ai_investigation_evaluation":
                raise RuntimeError("Refusing any database other than the persistent evaluation database")
            embedding_id = connection.scalar(
                text(
                    "SELECT id FROM embedding_configurations WHERE provider=:provider AND model_id=:model AND dimensions=768 AND document_format=:format AND normalization='none' AND model_revision IS NULL"
                ),
                {"provider": PROFILE["provider"], "model": PROFILE["model_id"], "format": PROFILE["document_format"]},
            )
            chunking_row = connection.execute(text("SELECT * FROM chunking_configurations WHERE id=1")).mappings().one()
        state = database_counts(url, 1, embedding_id)
    finally:
        sync_engine.dispose()
    required_counts = {
        "documents": 218,
        "document_versions": 218,
        "contents": 197,
        "chunk_embeddings": 197,
        "active_versions": 218,
        "active_searchable_occurrences": 218,
        "incomplete_planned_versions": 0,
    }
    if verified != 218 or any(state.get(key) != value for key, value in required_counts.items()):
        raise RuntimeError("Persistent evaluation corpus failed preflight; no generation request was made")
    if embedding_id is None or state["vector_dimensions"] != 768:
        raise RuntimeError("Compatible persisted embedding profile is missing")
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    # AI Studio remaining RPD is not exposed through this API key. Use the
    # previously reported daily ceiling and charge all known requests since the
    # Pacific reset before reserving any new calls.
    # The evaluation venv carries psycopg, but not the optional asyncpg driver.
    # SQLAlchemy's psycopg dialect selects AsyncConnection for create_async_engine.
    async_engine = create_async_engine(url, hide_parameters=True)
    sessions = async_sessionmaker(async_engine, expire_on_commit=False)
    selection_cases = [next(case for case in suite["cases"] if case["id"] == item) for item in selected_ids]
    budget = RequestBudget(request_cap, prior_requests=prior_requests, prior_by_mode=prior_by_mode)
    run_reports = None
    started = perf_counter()
    api_key = settings.gemini_api_key
    async with MeasuredGemini(api_key=api_key, batch_size=25, batch_token_limit=32768) as raw_embeddings:
        embeddings = BudgetedEmbeddings(raw_embeddings, MAX_EMBEDDING_INPUTS, budget)
        try:
            async with sessions() as session:
                chunking_row = await session.get(ChunkingConfigurationRow, 1)
                chunking = _configuration(chunking_row)
            evidence_repository = PostgreSQLEvidenceRepository(sessions)
            search = SearchEvidence(
                evidence_repository,
                embeddings,
                chunking,
                embeddings.configuration,
                mode="semantic",
                hybrid_repository=evidence_repository,
                semantic_candidates=20,
                keyword_candidates=20,
                rrf_k=60,
            )
            answer_provider = GeminiAnswerGenerator(
                api_key=api_key,
                model=generation_model,
                timeout_seconds=45,
                max_attempts=1,
                max_output_tokens=args.max_output_tokens,
            )
            investigation_provider = GeminiInvestigationModel(
                api_key=api_key, model=generation_model, timeout_seconds=60, max_output_tokens=args.max_output_tokens
            )
            try:
                single = AnswerQuestion(
                    search, BudgetedAnswerGenerator(answer_provider, budget), max_evidence=5, max_context_chars=12000
                )
                multi = BoundedInvestigation(
                    search,
                    BudgetedInvestigationModel(investigation_provider, budget),
                    max_turns=args.max_turns,
                    max_search_calls=args.max_search_calls,
                    max_results_per_search=args.max_results_per_search,
                    max_accumulated_evidence=args.max_accumulated_evidence,
                    max_context_chars=args.max_context_chars,
                    max_generation_requests=args.max_generation_requests,
                    max_embedding_requests=args.max_embedding_requests,
                )
                from dataclasses import asdict

                shared = {
                    "retrieval_mode": "semantic",
                    "chunking": asdict(chunking),
                    "embedding": {
                        "provider": PROFILE["provider"],
                        "model_id": PROFILE["model_id"],
                        "model_revision": None,
                        "dimensions": 768,
                        "document_format": PROFILE["document_format"],
                        "normalization": "none",
                    },
                    "generation_model": generation_model,
                }
                runs = [
                    AnsweringRun(
                        name="quick-answer-real-gemini",
                        mode="single_step",
                        service=single,
                        answering_budgets={"max_evidence": 5, "max_context_chars": 12000},
                        **shared,
                    ),
                    AnsweringRun(
                        name="investigation-real-gemini",
                        mode="multi_step",
                        service=multi,
                        investigation_budgets={
                            "max_turns": args.max_turns,
                            "max_generation_requests": args.max_generation_requests,
                            "max_search_calls": args.max_search_calls,
                            "max_results_per_search": args.max_results_per_search,
                            "max_accumulated_evidence": args.max_accumulated_evidence,
                            "max_context_chars": args.max_context_chars,
                            "max_embedding_requests": args.max_embedding_requests,
                        },
                        **shared,
                    ),
                ]
                if args.only_mode:
                    runs = [run for run in runs if run.mode == args.only_mode]
                # Seed the ordinary evaluator's output shape with explicit not-run rows.
                report = await evaluate_answers(
                    suite,
                    corpus,
                    runs,
                    annotation_corpus=corpus,
                    indexed_corpus_fingerprint=fingerprint(corpus),
                    case_ids=selected_ids,
                    stop_requested=lambda: True,
                )
                run_reports = report["runs"]
                for skipped_id in args.skip_id:
                    if skipped_id not in selected_ids:
                        raise ValueError(f"--skip-id is not in the frozen sample: {skipped_id}")
                    case = next(item for item in selection_cases if item["id"] == skipped_id)
                    for run_report in run_reports:
                        row_index = next(i for i, row in enumerate(run_report["questions"]) if row["id"] == skipped_id)
                        run_report["questions"][row_index] = _lost_result_row(case)
                report["execution"] = {
                    "kind": "persistent_local_frozen_sample",
                    "generation_model_requested": generation_model,
                    "investigation_guidance_version": INVESTIGATION_GUIDANCE_VERSION,
                    "generation_budget_model_scope": generation_model,
                    "generation_model_configuration_source": "explicit evaluation CLI configuration",
                    "max_output_tokens": args.max_output_tokens,
                    "generation_request_cap": request_cap,
                    "generation_requests_observed": prior_requests,
                    "generation_requests_by_mode": budget.by_mode,
                    "assumed_daily_rpd_limit": args.daily_rpd_limit,
                    "remaining_rpd_independently_verified": False,
                    "prior_generation_requests_this_daily_window": prior_requests,
                    "prior_single_step_requests_this_daily_window": args.prior_single_step_requests,
                    "prior_multi_step_requests_this_daily_window": args.prior_multi_step_requests,
                    "prior_embedding_request_totals": "unknown: the first paired response was lost before its local ledger was written",
                    "reserved_daily_requests": args.reserve_rpd,
                    "active_quota_not_independently_api_readable": True,
                    "generation_request_interval_seconds": GENERATION_INTERVAL_SECONDS,
                    "provider_retries": 0,
                    "embedding_model": PROFILE["model_id"],
                    "embedding_dimensions": PROFILE["dimensions"],
                    "embedding_configuration": PROFILE,
                    "embedding_inputs_observed": 0,
                    "embedding_text_cap": MAX_EMBEDDING_INPUTS,
                    "embedding_request_counts": {},
                    "counted_embedding_tokens": 0,
                    "database": state,
                    "verified_manifest_occurrences": verified,
                    "test_database": "persistent local PostgreSQL 18.4/pgvector evaluation database",
                    "corpus_fingerprint": fingerprint(corpus),
                    "suite_fingerprint": fingerprint(suite),
                    "selected_question_ids": list(selected_ids),
                    "task15_semantic_hybrid_metrics_equal": True,
                    "cloud_sql_contacted": False,
                    "budget_note": "Daily cap is the prior user-reported allowance minus a reserved request. Previously known same-window requests are charged before new calls. Investigation limits are independently configurable and must allow one final-decision turn after searches.",
                    "incomplete_reason": None,
                }
                checkpoint_dir = args.output / "question-checkpoints"
                checkpoint_dir.mkdir(parents=True, exist_ok=True)
                for case in selection_cases:
                    if case["id"] in args.skip_id:
                        continue
                    for run in runs:
                        mode = run.mode
                        checkpoint_path = checkpoint_dir / f"{case['id'].replace(':', '-')}.{mode}.json"
                        prior_row = load_checkpoint(
                            checkpoint_path, question_id=case["id"], mode=mode, configuration=run.configuration()
                        )
                        if prior_row is not None:
                            update_row(report, mode, case["id"], prior_row)
                            continue
                        needed = 1 if mode == "single_step" else args.max_generation_requests
                        if budget.cap - budget.attempted < needed or budget.failed:
                            report["execution"]["incomplete_reason"] = (
                                "Stopped before a mode lacking its full generation allowance or after a provider failure."
                            )
                            break

                        def persist_completed(run_name, row, *, _path=checkpoint_path, _mode=mode, _id=case["id"]):
                            save_checkpoint(
                                _path, question_id=_id, mode=_mode, row=row, configuration=run.configuration()
                            )

                        one = await evaluate_answers(
                            suite,
                            corpus,
                            [run],
                            annotation_corpus=corpus,
                            indexed_corpus_fingerprint=fingerprint(corpus),
                            case_ids=(case["id"],),
                            stop_requested=lambda: budget.failed,
                            on_result=persist_completed,
                        )
                        row = one["runs"][0]["questions"][0]
                        update_row(report, mode, case["id"], row)
                        report["stopped_early"] = budget.failed
                        report["execution"]["generation_requests_observed"] = budget.attempted
                        report["execution"]["generation_requests_by_mode"] = dict(budget.by_mode)
                        report["execution"]["embedding_inputs_observed"] = embeddings.inputs
                        report["execution"]["embedding_request_counts"] = raw_embeddings.request_counts.copy()
                        report["execution"]["counted_embedding_tokens"] = getattr(raw_embeddings, "counted_tokens", 0)
                        report["execution"]["generation_provider_requests"] = {
                            "quick_answer": answer_provider.provider_requests + args.prior_single_step_requests,
                            "investigation": investigation_provider.provider_requests + args.prior_multi_step_requests,
                        }
                        report["execution"]["total_generation_attempts_including_prior"] = budget.attempted
                        report["total_elapsed_ms"] = (perf_counter() - started) * 1000
                        _refresh_aggregates(report)
                        export_answer_quality(report, args.output)
                        if budget.failed:
                            report["execution"]["incomplete_reason"] = (
                                "Stopped after a provider failure; sanitized diagnostic is saved in the mode checkpoint."
                            )
                            break
                    if budget.failed or budget.attempted >= budget.cap:
                        break
                # Requests may be zero if quota preflight left too little room.
                report["stopped_early"] = report["stopped_early"] or any(
                    row["status"] == "not_run" for run_report in run_reports for row in run_report["questions"]
                )
                report["execution"]["generation_requests_observed"] = budget.attempted
                report["execution"]["generation_requests_by_mode"] = dict(budget.by_mode)
                report["execution"]["embedding_inputs_observed"] = embeddings.inputs
                report["execution"]["embedding_request_counts"] = raw_embeddings.request_counts.copy()
                report["execution"]["counted_embedding_tokens"] = getattr(raw_embeddings, "counted_tokens", 0)
                report["execution"]["generation_provider_requests"] = {
                    "quick_answer": answer_provider.provider_requests + args.prior_single_step_requests,
                    "investigation": investigation_provider.provider_requests + args.prior_multi_step_requests,
                }
                report["execution"]["generation_failures"] = [
                    row.get("provider_diagnostic")
                    for result in run_reports
                    for row in result["questions"]
                    if row.get("provider_diagnostic")
                ]
                report["total_elapsed_ms"] = (perf_counter() - started) * 1000
                _refresh_aggregates(report)
                export_answer_quality(report, args.output)
                return report
            finally:
                await answer_provider.aclose()
                await investigation_provider.aclose()
        finally:
            await async_engine.dispose()


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--daily-rpd-limit",
        type=int,
        default=20,
        help="Previously reported daily generation allowance (default 20); AI Studio remaining RPD is not API-readable",
    )
    parser.add_argument("--reserve-rpd", type=int, default=RESERVED_DAILY_REQUESTS)
    parser.add_argument("--prior-single-step-requests", type=int, required=True)
    parser.add_argument("--prior-multi-step-requests", type=int, required=True)
    parser.add_argument(
        "--case-id", action="append", default=[], help="Restrict execution to these existing frozen IDs"
    )
    parser.add_argument("--only-mode", choices=("single_step", "multi_step"))
    parser.add_argument("--generation-model", default=GENERATION_MODEL)
    parser.add_argument("--max-output-tokens", type=int, default=1024)
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument("--max-search-calls", type=int, default=5)
    parser.add_argument("--max-results-per-search", type=int, default=5)
    parser.add_argument("--max-accumulated-evidence", type=int, default=15)
    parser.add_argument("--max-context-chars", type=int, default=24000)
    parser.add_argument("--max-generation-requests", type=int, default=6)
    parser.add_argument("--max-embedding-requests", type=int, default=5)
    parser.add_argument(
        "--skip-id",
        action="append",
        default=[],
        help="Selected ID whose completed application responses could not be persisted",
    )
    parser.add_argument(
        "--continue-pending-id",
        default=None,
        help="Unsupported for old evidence-only checkpoints; rejected before any provider request",
    )
    args = parser.parse_args()
    if (
        args.daily_rpd_limit < 0
        or args.reserve_rpd < 0
        or args.prior_single_step_requests < 0
        or args.prior_multi_step_requests < 0
    ):
        parser.error("quota values must be nonnegative")
    if not 128 <= args.max_output_tokens <= 4096:
        parser.error("max output tokens must be between 128 and 4096")
    limits = (
        (args.max_turns, 1, 12),
        (args.max_search_calls, 1, 10),
        (args.max_results_per_search, 1, 10),
        (args.max_accumulated_evidence, 1, 30),
        (args.max_context_chars, 512, 64000),
        (args.max_generation_requests, 1, 12),
        (args.max_embedding_requests, 1, 12),
    )
    if any(not low <= value <= high for value, low, high in limits):
        parser.error("investigation budgets are outside the state-machine bounds")
    if (
        args.max_search_calls < 1
        or args.max_generation_requests < args.max_search_calls + 1
        or args.max_turns < args.max_search_calls + 1
        or args.max_embedding_requests < args.max_search_calls
    ):
        parser.error(
            "investigation search, embedding, turn and generation limits must allow all searches plus a final decision"
        )
    result = asyncio.run(run(args), loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None)
    from investigation_engine.evaluation.answer_quality import console_summary

    print(console_summary(result))


if __name__ == "__main__":
    main()
