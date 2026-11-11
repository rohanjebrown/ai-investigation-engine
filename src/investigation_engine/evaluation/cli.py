"""Operator CLI: pinned selections, cached-only retrieval, opt-in answers, saved comparisons."""

import argparse
import asyncio
import importlib.metadata
import json
import platform
import selectors
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from investigation_engine.application.embeddings import EmbeddingConfig
from investigation_engine.application.use_cases.search_evidence import SearchEvidence
from investigation_engine.evaluation.cached_queries import import_query_caches, load_query_cache
from investigation_engine.evaluation.dataset import (
    catalog,
    legacy_cases,
    load_cases,
    read_selection,
    select_questions,
    selection_manifest,
)
from investigation_engine.evaluation.investigation import (
    INVESTIGATION_MODES,
    ObservedInvestigationModel,
    ObservedInvestigationSearch,
    investigation_request_plan,
)
from investigation_engine.evaluation.live_queries import ReadThroughQueryEmbeddings
from investigation_engine.evaluation.metrics import ANSWER_SCORER, ANSWER_SCORER_SOURCE
from investigation_engine.evaluation.reporting import compare_runs, comparison_markdown, write_json
from investigation_engine.evaluation.retrieval import fingerprint
from investigation_engine.evaluation.runner import ANSWER_MODES, RETRIEVAL_MODES, EvaluationConfig, run_evaluation
from investigation_engine.evaluation.telemetry import ObservedGenerator, ObservedRepository, RequestBudget, observe_sql
from investigation_engine.infrastructure.ingestion.twowiki_adapter import DATASET_ID
from investigation_engine.ingestion.corpus_processing import FILE_SHA256, REVISION

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATASET = ROOT / "data/raw/2wiki/dev.parquet"
REGRESSION = ROOT / "data/samples/evaluation/regression-selection.json"
LEGACY = ROOT / "data/samples/evaluation/suite.json"
DEFAULT_CACHE = ROOT / ".cache/evaluation/query-embeddings.json"


def evaluation_generation_model(modes, gemini_model, investigation_provider, investigation_model):
    """Validate only models needed by the selected answer modes."""
    if not gemini_model and ("quick_answer" in modes or investigation_provider == "gemini"):
        raise ValueError("An explicit generation model is required")
    return gemini_model if "quick_answer" in modes else investigation_model


def software_manifest():
    versions = {"python": platform.python_version()}
    for name in ("sqlalchemy", "psycopg", "pgvector", "pyarrow", "google-genai", "tokenizers"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
            ).strip()
        )
    except OSError, subprocess.CalledProcessError:
        commit, dirty = None, None
    paths = [
        ROOT / "uv.lock",
        *sorted((ROOT / "src/investigation_engine/evaluation").glob("*.py")),
        ROOT / "src/investigation_engine/application/use_cases/search_evidence.py",
        ROOT / "src/investigation_engine/application/use_cases/answer_question.py",
        ROOT / "src/investigation_engine/application/investigations.py",
        ROOT / "src/investigation_engine/application/rrf.py",
        ROOT / "src/investigation_engine/infrastructure/persistence/postgresql_evidence_repository.py",
        *sorted((ROOT / "src/investigation_engine/infrastructure/generation").glob("*.py")),
    ]
    return {
        "git_commit": commit,
        "dirty_worktree": dirty,
        "versions": versions,
        "code_sha256": {
            str(path.relative_to(ROOT)).replace("\\", "/"): sha256(path.read_bytes()).hexdigest() for path in paths
        },
    }


def selected_cases(args):
    rows = catalog(args.dataset)  # Verify actual pinned bytes before any database/provider I/O.
    if args.selection:
        selection = read_selection(args.selection)
    elif args.question_set in ("smoke", "regression"):
        selection = read_selection(REGRESSION)
        if args.question_set == "smoke":
            selection["question_ids"] = selection["question_ids"][:3]
            selection["question_overrides"] = {
                key: value for key, value in selection["question_overrides"].items() if key in selection["question_ids"]
            }
    elif args.question_set == "legacy":
        return legacy_cases(LEGACY), {"legacy_suite_sha256": sha256(LEGACY.read_bytes()).hexdigest(), "seed": args.seed}
    else:
        if args.sample_size > 100 and not args.large_run:
            raise ValueError("More than 100 questions requires an explicit --large-run")
        selection = selection_manifest(select_questions(rows, args.sample_size, args.seed), args.seed)
    if len(selection["question_ids"]) > 100 and not args.large_run:
        raise ValueError("A larger persisted selection requires --large-run")
    if args.question_id:
        if len(set(args.question_id)) != len(args.question_id) or set(args.question_id) - set(
            selection["question_ids"]
        ):
            raise ValueError("Smoke question IDs must be distinct members of the selected set")
        selection = {
            **selection,
            "parent_selection_sha256": fingerprint(selection),
            "question_ids": [key for key in selection["question_ids"] if key in args.question_id],
        }
        selection["question_overrides"] = {
            key: value for key, value in selection.get("question_overrides", {}).items() if key in args.question_id
        }
    return load_cases(args.dataset, selection), selection


async def execute_run(args):
    # Validate authorization before constructing engines or provider clients.
    configuration = EvaluationConfig(
        modes=tuple(args.modes),
        repetitions=args.repetitions,
        warmups=args.warmups,
        seed=args.seed,
        final_evidence_limit=args.final_limit,
        semantic_candidates=args.semantic_candidates,
        lexical_candidates=args.lexical_candidates,
        semantic_backend=args.semantic_backend,
        hnsw_ef_search=args.hnsw_ef_search,
        hnsw_candidate_limits=tuple(args.hnsw_candidates),
        hnsw_iterative_scan=args.hnsw_iterative_scan,
        rrf_k=args.rrf_k,
        answer_retrieval_mode=args.answer_retrieval_mode,
        request_timeout_seconds=args.timeout or (900 if set(args.modes) & set(ANSWER_MODES) else 120),
        allow_generation=args.allow_generation,
        max_generation_requests=args.max_generation_requests,
        allow_live_embeddings=args.allow_live_embeddings,
        max_embedding_requests=args.max_embedding_requests,
        include_evidence_text=args.local_diagnostics,
        concurrency=args.concurrency,
        serving_max_evidence=args.serving_max_evidence,
        serving_max_context_chars=args.serving_max_context_chars,
    )
    cases, selection = selected_cases(args)
    if args.cache.exists():
        embedding, vectors = load_query_cache(args.cache)
        cache_sha = sha256(args.cache.read_bytes()).hexdigest()
    elif args.embedding_profile and set(configuration.modes) == {"lexical"}:
        embedding = EmbeddingConfig(**json.loads(args.embedding_profile.read_text(encoding="utf-8")))
        vectors, cache_sha = {}, None
    else:
        raise ValueError(
            "A profiled query cache is required; missing vectors are never generated. Lexical-only runs can supply --embedding-profile instead."
        )
    provider = ReadThroughQueryEmbeddings(embedding, vectors, cache_path=args.cache)
    embedding_audit = None
    if args.embedding_audit:
        supplied = json.loads(args.embedding_audit.read_text(encoding="utf-8"))
        if supplied["selected_question_ids"] != [case["id"] for case in cases] or supplied[
            "embedding_configuration"
        ] != asdict(embedding):
            raise ValueError("Embedding preparation audit does not match selected IDs/profile")
        embedding_audit = {
            key: supplied[key]
            for key in (
                "cache_coverage_before",
                "cache_coverage_after",
                "planned_max_provider_requests",
                "request_counts",
                "free_usage_verified",
                "project_tier",
                "remaining_quota",
                "active_rate_limits",
                "blocker",
            )
        }
        embedding_audit["audit_sha256"] = sha256(args.embedding_audit.read_bytes()).hexdigest()
        embedding_audit["verification_method"] = supplied.get(
            "verification_method", "No project-specific free usage verification was available"
        )
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
    output = args.output or ROOT / "reports/evaluation/structured" / run_id
    if output.exists():
        raise ValueError("Choose a fresh run output directory")

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from investigation_engine.evaluation.postgres import preflight
    from investigation_engine.infrastructure.config import settings
    from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import (
        PostgreSQLEvidenceRepository,
    )

    chunking_id = args.chunking_id or settings.serving_chunking_configuration_id
    investigation_provider = settings.investigation_model_provider
    if investigation_provider == "gemma4":
        from investigation_engine.infrastructure.generation.gemma4_investigation_model import (
            GEMMA4_MODEL,
            Gemma4InvestigationModel,
        )

        investigation_model_name = GEMMA4_MODEL
        investigation_model_type = Gemma4InvestigationModel
    else:
        from investigation_engine.infrastructure.generation.gemini_investigation_model import GeminiInvestigationModel

        investigation_model_name = args.generation_model or settings.gemini_generation_model
        investigation_model_type = GeminiInvestigationModel
    if chunking_id is None:
        raise ValueError("A persisted --chunking-id or SERVING_CHUNKING_CONFIGURATION_ID is required")
    engine = create_async_engine(
        settings.database_url(),
        hide_parameters=True,
        pool_size=1,
        max_overflow=0,
        connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=30000"},
    )
    clients = []
    try:
        chunking, corpus, availability = await preflight(
            engine,
            expected_database=args.expected_database or settings.db_name,
            chunking_id=chunking_id,
            embedding=embedding,
            cases=cases,
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        raw_repository = PostgreSQLEvidenceRepository(
            sessions,
            lexical_backend="bm25",
            semantic_backend=configuration.semantic_backend,
            hnsw_ef_search=configuration.hnsw_ef_search,
            hnsw_candidate_limits=configuration.hnsw_candidate_limits,
            hnsw_iterative_scan=configuration.hnsw_iterative_scan,
        )
        await raw_repository.validate_backend()
        if args.plan_only:
            modes = [
                configuration.answer_retrieval_mode if mode == "investigation" else mode.removeprefix("investigation_")
                for mode in configuration.modes
                if mode in INVESTIGATION_MODES
            ]
            if len(modes) != len(configuration.modes):
                raise ValueError("--plan-only currently requires Investigation modes")
            budgets = {
                key: getattr(settings, "investigation_" + key)
                for key in (
                    "max_turns",
                    "max_search_calls",
                    "max_results_per_search",
                    "max_accumulated_evidence",
                    "max_context_chars",
                    "max_generation_requests",
                    "max_embedding_requests",
                )
            }
            plan = {
                "status": "planned_no_provider_calls",
                "run_id": run_id,
                **software_manifest(),
                "dataset": {"source": DATASET_ID, "revision": REVISION, "split": "dev", "sha256": FILE_SHA256},
                "selection": selection,
                "question_ids": [case["id"] for case in cases],
                "selection_sha256": fingerprint(selection),
                "corpus": corpus,
                "configuration": configuration.json(),
                "embedding": asdict(embedding),
                "generation_model": investigation_model_name,
                "investigation_model_provider": investigation_provider,
                "investigation_model": investigation_model_name,
                "investigation_budgets": budgets,
                "cache_sha256": cache_sha,
                "cached_original_questions": sum(case["question"] in vectors for case in cases),
                "provider_requests": {"generation": 0, "count_tokens": 0, "embed_content": 0, "voyage": 0},
                "request_plan": investigation_request_plan(
                    len(cases),
                    modes,
                    budgets,
                    repetitions=configuration.repetitions,
                    warmups=configuration.warmups,
                    generation_attempts=settings.gemma4_retry_max_attempts if investigation_provider == "gemma4" else 1,
                ),
            }
            output.mkdir(parents=True, exist_ok=False)
            write_json(output / "plan.json", plan)
            print(
                json.dumps({"output": str(output), "status": plan["status"], "request_plan": plan["request_plan"]}),
                flush=True,
            )
            return
        if configuration.allow_live_embeddings:
            from investigation_engine.evaluation.live_providers import BudgetedGeminiEmbeddingProvider, ProviderPacer
            from investigation_engine.infrastructure.embeddings.gemini_provider import (
                DIMENSIONS,
                DOCUMENT_FORMAT_VERSION,
                MODEL,
            )

            if embedding != EmbeddingConfig(
                provider="gemini_developer_api",
                model_id=MODEL,
                dimensions=DIMENSIONS,
                document_format=DOCUMENT_FORMAT_VERSION,
            ):
                raise ValueError("Live embedding adapter is incompatible with the cached/persisted profile")
            live = BudgetedGeminiEmbeddingProvider(
                api_key=settings.gemini_api_key,
                maximum_requests=configuration.max_embedding_requests,
                pacer=ProviderPacer(args.embedding_rpm, args.embedding_tpm),
            )
            clients.append(live)
            provider = ReadThroughQueryEmbeddings(embedding, vectors, cache_path=args.cache, live=live, allow_live=True)
        repository = ObservedRepository(raw_repository)
        searches = {
            mode: SearchEvidence(
                repository,
                provider,
                chunking,
                embedding,
                mode="hybrid" if mode == "hybrid" else "semantic",
                hybrid_repository=repository,
                semantic_candidates=configuration.semantic_candidates,
                keyword_candidates=configuration.lexical_candidates,
                rrf_k=configuration.rrf_k,
            )
            for mode in set(RETRIEVAL_MODES) | {configuration.answer_retrieval_mode}
        }
        jobs = {mode: {"service": searches[mode]} for mode in configuration.modes if mode in RETRIEVAL_MODES}
        from investigation_engine.application.use_cases.answer_question import AnswerQuestion
        from investigation_engine.evaluation.serving import LexicalSearch

        for mode, job in jobs.items():
            job["preparer"] = AnswerQuestion(
                LexicalSearch(searches[mode]) if mode == "lexical" else searches[mode],
                None,
                max_evidence=configuration.serving_max_evidence,
                max_context_chars=configuration.serving_max_context_chars,
            )
        generation_settings = None
        if set(configuration.modes) & set(ANSWER_MODES):
            from investigation_engine.application.investigations import InvestigateQuestion
            from investigation_engine.application.use_cases.answer_question import AnswerQuestion
            from investigation_engine.evaluation.answer_quality import AnsweringRun
            from investigation_engine.infrastructure.generation.gemini_answer_generator import GeminiAnswerGenerator

            budget = RequestBudget(configuration.max_generation_requests)
            from investigation_engine.evaluation.live_providers import PacedInvestigationModel, ProviderPacer

            generation_pacer = ProviderPacer(args.generation_rpm, args.generation_tpm)
            model = args.generation_model or settings.gemini_generation_model
            recorded_model = evaluation_generation_model(
                configuration.modes, model, investigation_provider, investigation_model_name
            )
            answer_budgets = {
                "max_evidence": settings.rag_max_evidence,
                "max_context_chars": settings.rag_max_context_chars,
            }
            investigation_budgets = {
                key: getattr(settings, "investigation_" + key)
                for key in (
                    "max_turns",
                    "max_search_calls",
                    "max_results_per_search",
                    "max_accumulated_evidence",
                    "max_context_chars",
                    "max_generation_requests",
                    "max_embedding_requests",
                )
            }
            generation_settings = {
                "model": recorded_model,
                "investigation_model_provider": investigation_provider,
                "investigation_model": investigation_model_name
                if set(configuration.modes) & set(INVESTIGATION_MODES)
                else None,
                "max_output_tokens": settings.generation_max_output_tokens,
                "investigation_max_output_tokens": settings.investigation_max_output_tokens,
                "timeout_seconds": settings.generation_timeout_seconds,
                "attempts": 1,
                "temperature": settings.generation_temperature,
                "quick_answer_budgets": answer_budgets,
                "investigation_budgets": investigation_budgets,
            }
            for mode in configuration.modes:
                if mode not in ANSWER_MODES:
                    continue
                if mode == "quick_answer":
                    client = GeminiAnswerGenerator(
                        api_key=settings.gemini_api_key,
                        model=model,
                        max_attempts=1,
                        timeout_seconds=settings.generation_timeout_seconds,
                        max_output_tokens=settings.generation_max_output_tokens,
                        temperature=settings.generation_temperature,
                    )
                    clients.append(client)
                    service = AnswerQuestion(
                        LexicalSearch(searches[configuration.answer_retrieval_mode])
                        if configuration.answer_retrieval_mode == "lexical"
                        else searches[configuration.answer_retrieval_mode],
                        ObservedGenerator(client, budget),
                        **answer_budgets,
                    )
                else:
                    retrieval_mode = (
                        configuration.answer_retrieval_mode
                        if mode == "investigation"
                        else mode.removeprefix("investigation_")
                    )
                    retry_options = (
                        {
                            "retry_max_attempts": settings.gemma4_retry_max_attempts,
                            "retry_total_timeout_seconds": settings.gemma4_retry_total_timeout_seconds,
                        }
                        if investigation_provider == "gemma4"
                        else {}
                    )
                    client = investigation_model_type(
                        api_key=settings.gemini_api_key,
                        model=investigation_model_name,
                        timeout_seconds=settings.generation_timeout_seconds,
                        max_output_tokens=settings.investigation_max_output_tokens,
                        **retry_options,
                    )
                    generation_settings["investigation_adapter"] = (
                        f"{type(client).__module__}.{type(client).__qualname__}"
                    )
                    clients.append(client)
                    paced_model = PacedInvestigationModel(client, generation_pacer)
                    if investigation_provider == "gemma4":
                        generation_settings["investigation_retry_max_attempts"] = client.retry_max_attempts
                        generation_settings["investigation_retry_total_timeout_seconds"] = (
                            client.retry_total_timeout_seconds
                        )
                        client.retry_allowed = lambda: budget.used < budget.maximum

                        async def reserve_retry(turn, paced=paced_model):
                            budget.require(1)
                            await generation_pacer.wait(paced.current_reservation)
                            budget.consume()

                        client.before_retry = reserve_retry
                    service = InvestigateQuestion(
                        ObservedInvestigationSearch(
                            LexicalSearch(searches[retrieval_mode])
                            if retrieval_mode == "lexical"
                            else searches[retrieval_mode],
                            retrieval_mode,
                        ),
                        ObservedInvestigationModel(ObservedGenerator(paced_model, budget)),
                        **investigation_budgets,
                    )
                adapted = AnsweringRun(
                    name=mode,
                    mode="single_step" if mode == "quick_answer" else "multi_step",
                    service=service,
                    retrieval_mode=configuration.answer_retrieval_mode if mode == "quick_answer" else retrieval_mode,
                    chunking=asdict(chunking),
                    embedding=asdict(embedding),
                    generation_model=model if mode == "quick_answer" else investigation_model_name,
                    answering_budgets=answer_budgets,
                    investigation_budgets=investigation_budgets,
                )
                jobs[mode] = {"service": service, "answer_run": adapted}
        manifest = {
            "schema_version": 1,
            "run_id": run_id,
            "timestamp_utc": datetime.now(UTC).isoformat(),
            **software_manifest(),
            "dataset": {"source": DATASET_ID, "revision": REVISION, "split": "dev", "sha256": FILE_SHA256},
            "selection": selection,
            "selection_sha256": fingerprint(selection),
            "question_ids": [case["id"] for case in cases],
            "corpus": corpus,
            "chunking": asdict(chunking),
            "embedding": asdict(embedding),
            "reranker": {"backend": "none", "voyage_requests": 0},
            "generation": generation_settings,
            "retrieval": {
                "eligibility": "reference",
                "embedding_backend": "legacy_shared",
                "lexical": "bm25",
                "bm25_initial_candidates": 400,
                "bm25_completion": "exact",
                "semantic": configuration.semantic_backend,
            },
            "cache": {
                "sha256": cache_sha,
                "available_vectors": len(vectors),
                "selected_questions_missing": [case["id"] for case in cases if case["question"] not in vectors],
                "selected_questions_covered": sum(case["question"] in vectors for case in cases),
                "blocked_modes": [mode for mode in configuration.modes if mode in ("semantic", "hybrid")]
                if all(case["question"] not in vectors for case in cases)
                else [],
                "policy": "exact compatible hits reused; authorized bounded misses atomically persisted"
                if configuration.allow_live_embeddings
                else "exact text, cached only; no provider embedding calls, including Investigation followups",
            },
            "provider_requests": {"embedding_count_tokens": 0, "embedding_embed_content": 0, "voyage_reranking": 0},
            "provider_request_scope": "Benchmark execution only; any prior authorized embedding preparation is recorded separately",
            "embedding_preparation": embedding_audit,
            "live_limits": {
                "generation_rpm": args.generation_rpm,
                "generation_tpm_reservation": args.generation_tpm,
                "embedding_rpm_including_count_tokens": args.embedding_rpm,
                "embedding_tpm": args.embedding_tpm,
                "initial_cooldown_seconds": args.cooldown_seconds,
                "generation_request_cap": configuration.max_generation_requests,
                "embedding_rpc_cap_including_count_tokens": configuration.max_embedding_requests,
                "automatic_retries": settings.gemma4_retry_max_attempts - 1
                if investigation_provider == "gemma4" and set(configuration.modes) & set(INVESTIGATION_MODES)
                else 0,
            },
            "answer_scorer": {
                "version": ANSWER_SCORER,
                "source": ANSWER_SCORER_SOURCE,
                "alias_mapping": "unavailable in this pinned text-only snapshot",
            },
            "scope": "Shared-only existing indexed corpus. No ingestion, gold labels or answers sent to generation.",
            "serving_observation": {
                "boundary": "Actual AnswerQuestion.prepare_evidence without generation; a separate serving-limit retrieval follows the candidate-limit measurement",
                "evidence_limit": configuration.serving_max_evidence,
                "context_limit": configuration.serving_max_context_chars,
                "phases": "Candidate ranking (20) and production evidence preparation (5) are independently timed. Do not sum them as production latency.",
                "lexical_scope": "Lexical route supplied through the same Quick Answer search port; exact common admission, no generation",
            },
            "timing": {
                "clock": "perf_counter monotonic",
                "provider_embedding_network": configuration.allow_live_embeddings,
                "generation_network": bool(generation_settings),
                "explain_server_time": "not collected; not interchangeable with driver latency",
                "environment": "Local Python process against Cloud SQL via the configured proxy; includes network overhead, not deployed application latency",
            },
        }
        if args.cooldown_seconds:
            if not 0 < args.cooldown_seconds <= 3600:
                raise ValueError("Cooldown must be between zero and 3600 seconds")
            print(json.dumps({"initial_provider_cooldown_seconds": args.cooldown_seconds}), flush=True)
            await asyncio.sleep(args.cooldown_seconds)
        with observe_sql(engine.sync_engine):
            summary = await run_evaluation(
                cases,
                configuration,
                jobs,
                output=output,
                manifest=manifest,
                availability=availability,
                progress=lambda row: print(json.dumps(row), flush=True),
                query_provider=provider if args.cache.exists() else None,
            )
        print(json.dumps({"output": str(output), "modes": list(summary["modes"])}))
    finally:
        for client in clients:
            await client.aclose()
        await engine.dispose()


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    select = commands.add_parser("select", help="Persist a seeded type-stratified local dataset selection")
    select.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    select.add_argument("--sample-size", type=int, default=40)
    select.add_argument("--seed", type=int, default=20261106)
    select.add_argument("--output", type=Path, required=True)
    cache = commands.add_parser("cache", help="Import explicitly named local caches/traces, without API calls")
    cache.add_argument("--source", type=Path, action="append", required=True)
    cache.add_argument("--embedding-profile", type=Path)
    cache.add_argument("--output", type=Path, default=DEFAULT_CACHE)
    run = commands.add_parser("run", help="Evaluate the existing shared corpus read-only")
    run.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    run.add_argument("--set", dest="question_set", choices=("smoke", "regression", "sample", "legacy"), default="smoke")
    run.add_argument("--selection", type=Path)
    run.add_argument(
        "--question-id",
        action="append",
        help="Explicit smoke subset of the saved selection; original selection hash is retained",
    )
    run.add_argument("--sample-size", type=int, default=40)
    run.add_argument("--large-run", action="store_true")
    run.add_argument("--seed", type=int, default=20261106)
    run.add_argument("--modes", nargs="+", choices=RETRIEVAL_MODES + ANSWER_MODES, default=list(RETRIEVAL_MODES))
    run.add_argument(
        "--repetitions", type=int, help="Default: one for answer/Investigation runs, three for retrieval-only"
    )
    run.add_argument("--warmups", type=int, help="Default: zero for answer/Investigation runs, one for retrieval-only")
    run.add_argument("--concurrency", type=int, default=1)
    run.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    run.add_argument("--embedding-profile", type=Path)
    run.add_argument(
        "--embedding-audit",
        type=Path,
        help="Record the matching local preparation/allowance audit; this never authorizes inference",
    )
    run.add_argument("--output", type=Path)
    run.add_argument(
        "--plan-only",
        action="store_true",
        help="Verify dataset/shared corpus and save Investigation request bounds without constructing providers or making AI calls",
    )
    run.add_argument("--expected-database")
    run.add_argument("--chunking-id", type=int)
    run.add_argument("--semantic-backend", choices=("hnsw", "exact"), default="hnsw")
    run.add_argument("--hnsw-ef-search", type=int, default=400)
    run.add_argument("--hnsw-candidates", nargs="+", type=int, default=[400])
    run.add_argument("--hnsw-iterative-scan", action="store_true")
    run.add_argument("--semantic-candidates", type=int, default=20)
    run.add_argument("--lexical-candidates", type=int, default=20)
    run.add_argument("--final-limit", type=int, default=20)
    run.add_argument("--serving-max-evidence", type=int, default=5)
    run.add_argument("--serving-max-context-chars", type=int, default=12000)
    run.add_argument("--rrf-k", type=int, default=60)
    run.add_argument(
        "--timeout",
        type=float,
        help="Whole Investigation/request timeout including pacing; default 900s for answers, 120s for retrieval",
    )
    run.add_argument("--answer-retrieval-mode", choices=RETRIEVAL_MODES, default="hybrid")
    run.add_argument("--allow-generation", action="store_true")
    run.add_argument("--max-generation-requests", type=int, default=0)
    run.add_argument("--generation-model")
    run.add_argument("--allow-live-embeddings", action="store_true")
    run.add_argument(
        "--max-embedding-requests", type=int, default=0, help="Run-wide RPC cap including countTokens and embedContent"
    )
    run.add_argument("--generation-rpm", type=int, default=12)
    run.add_argument(
        "--generation-tpm",
        type=int,
        default=200000,
        help="Conservative rolling token reservation; actual usage is recorded separately",
    )
    run.add_argument("--embedding-rpm", type=int, default=60, help="Conservatively counts token-counting RPCs too")
    run.add_argument("--embedding-tpm", type=int, default=24000)
    run.add_argument(
        "--cooldown-seconds", type=float, default=0, help="Initial provider cooldown before benchmark timers"
    )
    run.add_argument(
        "--local-diagnostics",
        action="store_true",
        help="Explicitly retain local passage text and grounding review file",
    )
    compare = commands.add_parser("compare", help="Compare two saved runs, or two modes in the same run")
    compare.add_argument("left", type=Path)
    compare.add_argument("right", type=Path)
    compare.add_argument("--left-mode", required=True)
    compare.add_argument("--right-mode", required=True)
    compare.add_argument("--output", type=Path, required=True)
    rescore = commands.add_parser(
        "rescore",
        help="Offline answer extraction, official-score verification and result-bound factual-review import; no database/provider calls",
    )
    rescore.add_argument("source", type=Path)
    rescore.add_argument(
        "--reviews",
        type=Path,
        action="append",
        default=[],
        help="Completed copies of review_packets.jsonl; repeated files are merged strictly",
    )
    rescore.add_argument("--output", type=Path, required=True)
    return cli


def main():
    cli = parser()
    args = cli.parse_args()
    try:
        if args.command == "run":
            asyncio.run(execute_run(args), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
        else:
            if args.output.exists():
                raise ValueError("Output already exists; choose a fresh path")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            if args.command == "select":
                write_json(
                    args.output,
                    selection_manifest(select_questions(catalog(args.dataset), args.sample_size, args.seed), args.seed),
                )
            elif args.command == "cache":
                profile = (
                    EmbeddingConfig(**json.loads(args.embedding_profile.read_text(encoding="utf-8")))
                    if args.embedding_profile
                    else None
                )
                value = import_query_caches(args.source, profile)
                value["sources_sha256"] = [sha256(path.read_bytes()).hexdigest() for path in args.source]
                write_json(args.output, value)
            elif args.command == "rescore":
                from investigation_engine.evaluation.answer_reviews import rescore_saved_run

                value = rescore_saved_run(args.source, args.output, reviews=args.reviews)
                print(
                    json.dumps(
                        {
                            "output": str(args.output),
                            "source_run_id": value["source_run_id"],
                            "original_official_metrics_unchanged": True,
                            "provider_requests": 0,
                            "database_queries": 0,
                        }
                    )
                )
            else:
                value = compare_runs(args.left, args.right, left_mode=args.left_mode, right_mode=args.right_mode)
                write_json(args.output, value)
                args.output.with_suffix(".md").write_text(comparison_markdown(value), encoding="utf-8")
    except (ValueError, FileNotFoundError) as error:
        cli.exit(2, f"{type(error).__name__}: {error}\n")
    except Exception as error:
        # Provider/SQL messages can contain parameters. Exported errors are categorized per request.
        cli.exit(1, f"Evaluation setup failed: {type(error).__name__}; no raw provider/SQL diagnostics exported.\n")


if __name__ == "__main__":
    main()
