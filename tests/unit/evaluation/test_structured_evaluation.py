import asyncio
import json
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from investigation_engine.application.answers import AnswerStatus, GenerationResult
from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy
from investigation_engine.application.embeddings import EmbeddingConfig
from investigation_engine.application.investigations import (
    InvestigateQuestion,
    InvestigationDecision,
    InvestigationStatus,
    SearchEvidenceCall,
)
from investigation_engine.application.ports.generation_errors import GenerationRateLimitError
from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
from investigation_engine.application.retrieval import Evidence
from investigation_engine.application.use_cases.answer_question import AnswerQuestion
from investigation_engine.application.use_cases.search_evidence import SearchEvidence
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.evaluation.answer_quality import AnsweringRun
from investigation_engine.evaluation.cached_queries import CachedQueryEmbeddings, import_query_caches, load_query_cache
from investigation_engine.evaluation.dataset import case_from_record, legacy_cases, read_selection, select_questions
from investigation_engine.evaluation.metrics import answer_scores, normalize_answer, retrieval_metrics, statistics
from investigation_engine.evaluation.reporting import compare_runs, read_jsonl, write_json
from investigation_engine.evaluation.runner import EvaluationConfig, error_category, run_evaluation
from investigation_engine.evaluation.telemetry import (
    ObservedGenerator,
    ObservedRepository,
    RequestBudget,
    RequestBudgetExceeded,
    capture_timings,
    observe,
    observe_sql,
)


def hit(number=1):
    return Evidence(
        document_id=UUID(int=number),
        document_version_id=UUID(int=number),
        content_sha256=str(number) * 64,
        text="source fact",
        start=0,
        end=11,
        score=0.8,
        rank=1,
        source=SourceReference(
            kind=SourceKind.DATASET, source_id="dataset", revision="pinned", record_locator={"example_id": str(number)}
        ),
        source_spans=(SourceSpan(start=0, end=11, locators=(SourceLocator(kind=LocatorKind.SENTENCE, index=0),)),),
    )


def case():
    item = hit()
    return {
        "id": "q1",
        "question": "Question?",
        "question_type": "inference",
        "reference_answer": "REFERENCE_ONLY_SENTINEL",
        "gold": [
            {
                "source": asdict(item.source),
                "content_sha256": item.content_sha256,
                "sentence_index": 0,
                "start": 0,
                "end": 11,
            }
        ],
    }


def service(items=(hit(),), *, cache=None):
    profile = EmbeddingConfig(provider="synthetic", model_id="offline")
    provider = CachedQueryEmbeddings(profile, {"Question?": [1.0] + [0.0] * 767} if cache is None else cache)
    config = ChunkingConfig(
        strategy=ChunkingStrategy.WHOLE_PASSAGE,
        tokenizer=TokenizerIdentity(name="test", revision="1", artifact_sha256="0" * 64, runtime_version="test"),
        max_tokens=100,
    )
    raw = SimpleNamespace(
        search=AsyncMock(return_value=items),
        search_keywords=AsyncMock(return_value=items),
        search_channels=AsyncMock(return_value=(items, ())),
    )
    repo = ObservedRepository(raw)
    return SearchEvidence(repo, provider, config, profile), raw


def manifest(ids=("q1",)):
    return {
        "run_id": "fake-run",
        "question_ids": list(ids),
        "dataset": {"revision": "pinned"},
        "corpus": {"snapshot": "fake"},
        "chunking": {},
        "embedding": {},
    }


@pytest.mark.parametrize(
    "prediction,reference,em,f1",
    [
        ("The, Theuderic I!", "theuderic i", 1, 1),
        ("The answer is Theuderic I", "Theuderic I", 0, 2 / 3),
        ("Yes indeed", "yes", 0, 0),
        (None, "answer", 0, 0),
        ("no", "no", 1, 1),
        ("word word other", "word word", 0, 0.8),
    ],
)
def test_official_answer_text_metrics(prediction, reference, em, f1):
    actual = answer_scores(prediction, reference)
    assert actual["exact_match"] == em
    assert actual["f1"] == pytest.approx(f1)
    assert answer_scores(None, None) == {"exact_match": None, "f1": None}
    assert normalize_answer(" A Cat's THE  cat.") == "cats cat"


def test_latency_statistics_nearest_rank_and_empty():
    stats = statistics(list(range(1, 21)))
    assert stats == {"count": 20, "minimum": 1, "median": 10.5, "mean": 10.5, "p90": 18, "p95": 19, "maximum": 20}
    assert statistics([])["count"] == 0
    assert statistics([])["p95"] is None


def test_sentence_mapping_not_title_and_partial_chunk_or_occurrence():
    relevant, gold = hit(), case()["gold"]
    partial = replace(relevant, start=1, text="ource fact")
    assert retrieval_metrics((partial,), gold)["at_k"]["20"]["recall"] == 0
    other_source = replace(relevant, source=replace(relevant.source, record_locator={"example_id": "other"}))
    actual = retrieval_metrics((other_source,), gold)
    assert actual["at_k"]["20"]["recall"] == 1
    assert actual["at_k"]["20"]["strict_occurrence_recall"] == 0
    assert retrieval_metrics((hit(2),), gold)["at_k"]["20"]["recall"] == 0


def test_duplicate_positions_counts_and_multi_fact_denominator():
    gold = case()["gold"] + [{"mapping_error": "invalid_sentence_index", "sentence_index": 100}]
    good = hit()
    duplicate = replace(good, document_id=UUID(int=9), document_version_id=UUID(int=9))
    actual = retrieval_metrics((good, duplicate), gold)
    assert actual["exact_canonical_duplicate_rate"] == 0.5
    assert actual["distinct_source_documents"] == 2
    assert actual["near_duplicate_rate"] is None
    assert actual["at_k"]["20"]["recall"] == 0.5
    assert not actual["at_k"]["20"]["complete"]
    ranked = retrieval_metrics((hit(2),) * 6 + (good,), case()["gold"])
    assert ranked["at_k"]["5"]["recall"] == 0
    assert ranked["mrr_at_20"] == 1 / 7


def test_empty_absent_candidate_only_and_unresolved_remain_visible():
    gold = case()["gold"]
    assert retrieval_metrics((), gold)["mrr_at_20"] == 0
    assert retrieval_metrics((), [])["mrr_at_20"] is None
    absent = retrieval_metrics((), gold, availability={"0": "absent_from_shared_corpus"})
    assert absent["supporting_facts"][0]["state"] == "absent_from_shared_corpus"
    candidates = retrieval_metrics((), gold, candidates=(hit(),), availability={"0": "eligible"})
    assert candidates["supporting_facts"][0]["state"] == "candidate_only_excluded_from_final"
    present = retrieval_metrics((), gold, availability={"0": "eligible"})
    assert present["supporting_facts"][0]["state"] == "present_not_retrieved"


def record():
    return {
        "_id": "dataset-q",
        "question": "Original Question?",
        "type": "inference",
        "answer": "reference",
        "context": json.dumps([["Title", ["First fact.", "Second fact."]]]),
        "supporting_facts": json.dumps([["Title", 1]]),
        "evidences": "[]",
    }


def test_actual_adapter_offsets_ambiguous_titles_and_invalid_sentence():
    value = record()
    mapped = case_from_record(value, question="original question?")
    assert mapped["gold"][0]["start"] == len("First fact.") + 1
    assert mapped["gold"][0]["end"] == len("First fact. Second fact.")
    value["supporting_facts"] = '[["Title", 9]]'
    assert case_from_record(value)["gold"][0]["mapping_error"] == "invalid_sentence_index"
    value["context"] = '[["Title", ["one"]], ["Title", ["two"]]]'
    assert case_from_record(value)["gold"][0]["mapping_error"] == "missing_or_ambiguous_context_title"
    with pytest.raises(ValueError, match="only in case"):
        case_from_record(record(), question="invented question")


def test_type_stratification_reproducible_under_reordering_and_preserved_regressions():
    rows = [{"_id": str(i), "type": str(i % 4)} for i in range(40)]
    selected = select_questions(rows, 8, 42)
    assert selected == select_questions(list(reversed(rows)), 8, 42)
    assert selected != select_questions(rows, 8, 43)
    assert {
        sum(next(row for row in rows if row["_id"] == key)["type"] == kind for key in selected)
        for kind in ("0", "1", "2", "3")
    } == {2}
    regression = read_selection(Path("data/samples/evaluation/regression-selection.json"))
    assert len(regression["question_ids"]) == 5
    legacy = legacy_cases(Path("data/samples/evaluation/suite.json"))
    assert len(legacy) == 40
    assert {item["expected_behavior"] for item in legacy} >= {"answer", "abstain"}


def test_cache_profile_validation_conflicts_no_fallback_and_serialization(tmp_path):
    profile = EmbeddingConfig(provider="synthetic", model_id="offline")
    first, flat = tmp_path / "cache.json", tmp_path / "flat.json"
    write_json(first, {"configuration": asdict(profile), "vectors": {"q": [1.0] + [0.0] * 767}})
    write_json(flat, {"q2": [0.0, 1.0] + [0.0] * 766})
    value = import_query_caches([first, flat])
    write_json(tmp_path / "merged.json", value)
    assert load_query_cache(tmp_path / "merged.json")[0] == profile
    assert len(value["vectors"]) == 2
    with pytest.raises(ValueError, match="profile"):
        import_query_caches([flat])
    write_json(flat, {"q": [0.0, 1.0] + [0.0] * 766})
    with pytest.raises(ValueError, match="Conflicting"):
        import_query_caches([first, flat])


def test_live_guards_and_configuration_round_trip():
    with pytest.raises(ValueError, match="allow-generation"):
        EvaluationConfig(modes=("quick_answer",))
    with pytest.raises(ValueError, match="positive"):
        EvaluationConfig(modes=("investigation",), allow_generation=True)
    value = EvaluationConfig(modes=("hybrid",), seed=7, repetitions=2)
    payload = json.loads(json.dumps(value.json()))
    payload["modes"], payload["hnsw_candidate_limits"] = (
        tuple(payload["modes"]),
        tuple(payload["hnsw_candidate_limits"]),
    )
    assert EvaluationConfig(**payload) == value
    with pytest.raises(ValueError, match="sequential"):
        EvaluationConfig(concurrency=2)


def test_gemma_investigation_does_not_require_or_report_the_gemini_model():
    from investigation_engine.evaluation.cli import evaluation_generation_model

    modes = ("investigation_hybrid",)
    assert evaluation_generation_model(modes, None, "gemma4", "gemma-4-26b-a4b-it") == "gemma-4-26b-a4b-it"
    assert evaluation_generation_model(modes, "gemini-example", "gemma4", "gemma-4-26b-a4b-it") == "gemma-4-26b-a4b-it"
    with pytest.raises(ValueError, match="explicit generation model"):
        evaluation_generation_model(("quick_answer", *modes), None, "gemma4", "gemma-4-26b-a4b-it")
    with pytest.raises(ValueError, match="explicit generation model"):
        evaluation_generation_model(modes, None, "gemini", None)


async def test_runner_warmups_error_denominators_redaction_and_comparison(tmp_path):
    good, raw = service()
    failing, _ = service(cache={})
    cases = [case()]
    settings = EvaluationConfig(modes=("semantic",), warmups=1, repetitions=2)
    outputs = [tmp_path / "good", tmp_path / "bad"]
    await run_evaluation(cases, settings, {"semantic": {"service": good}}, output=outputs[0], manifest=manifest())
    bad = await run_evaluation(
        cases, settings, {"semantic": {"service": failing}}, output=outputs[1], manifest=manifest()
    )
    assert raw.search.await_count == 3
    assert bad["modes"]["semantic"]["attempts"] == 2
    assert bad["modes"]["semantic"]["recall_at_20"] == 0
    assert bad["modes"]["semantic"]["failed_or_incomplete"] == 2
    assert bad["modes"]["semantic"]["error_categories"] == {"missing_cached_embedding": 2}
    failed_row = read_jsonl(outputs[1] / "retrieval_results.jsonl")[-1]
    assert failed_row["retrieval_actions"] == 1 and failed_row["repository_calls"] == 0
    row = read_jsonl(outputs[0] / "retrieval_results.jsonl")[1]
    assert row["timings"]["total_retrieval_ms"] >= row["timings"]["repository_ms"]
    assert row["timings"]["query_embedding_ms"] is not None
    assert row["candidate_counts"] == {"semantic": 1, "lexical": 0}
    assert "source fact" not in (outputs[0] / "retrieval_results.jsonl").read_text()
    assert "source fact" not in (outputs[0] / "summary.json").read_text()
    assert not (outputs[0] / "grounding_review.jsonl").exists()
    comparison = compare_runs(*outputs, left_mode="semantic", right_mode="semantic")
    # A wholly unavailable mode has no measured quality or latency delta.
    assert comparison["metric_deltas"]["recall_at_20"] is None
    assert comparison["latency_deltas_ms"]["median"] is None
    assert not comparison["questions"][0]["newly_failing"]
    assert comparison["questions"][0]["right_statuses"] == {"failed": 2}
    assert comparison["questions"][0]["evidence_changed"]


async def test_timeout_and_exception_categories_are_sanitized(tmp_path):
    async def slow(*args, **kwargs):
        await asyncio.sleep(0.05)

    fake = SimpleNamespace(execute=slow)
    cfg = EvaluationConfig(modes=("semantic",), warmups=0, repetitions=1, request_timeout_seconds=0.001)
    summary = await run_evaluation(
        [case()], cfg, {"semantic": {"service": fake}}, output=tmp_path / "timeout", manifest=manifest()
    )
    assert summary["modes"]["semantic"]["error_categories"] == {"timeout": 1}
    assert error_category(ValueError("PRIVATE_SENTINEL")) == "validation"
    fake.execute = AsyncMock(side_effect=RuntimeError("PRIVATE_SENTINEL"))
    await run_evaluation(
        [case()],
        replace(cfg, request_timeout_seconds=1),
        {"semantic": {"service": fake}},
        output=tmp_path / "error",
        manifest=manifest(),
    )
    assert "PRIVATE_SENTINEL" not in (tmp_path / "error/retrieval_results.jsonl").read_text()


async def test_interrupted_run_preserves_progress_and_unattempted_cases(tmp_path):
    search, _ = service()
    cases = [case(), {**case(), "id": "q2"}]
    cfg = EvaluationConfig(modes=("semantic",), warmups=0, repetitions=1)

    def cancel_after_first(_):
        raise asyncio.CancelledError()

    output = tmp_path / "interrupted"
    with pytest.raises(asyncio.CancelledError):
        await run_evaluation(
            cases,
            cfg,
            {"semantic": {"service": search}},
            output=output,
            manifest=manifest(("q1", "q2")),
            progress=cancel_after_first,
        )
    summary = json.loads((output / "summary.json").read_text())
    assert summary["progress"]["reason"] == "CancelledError"
    assert summary["progress"]["attempted_measured"] == 1
    assert summary["progress"]["unattempted_measured"] == 1
    assert summary["modes"]["semantic"]["attempts"] == 1
    assert len(read_jsonl(output / "retrieval_results.jsonl")) == 1
    assert "CancelledError" in (output / "report.md").read_text()


async def test_rate_limit_stops_run_after_recording_failure(tmp_path):
    fake = SimpleNamespace(execute=AsyncMock(side_effect=GenerationRateLimitError("quota")))
    cases = [case(), {**case(), "id": "q2"}]
    cfg = EvaluationConfig(modes=("semantic",), warmups=0, repetitions=1)
    output = tmp_path / "rate-limit"
    summary = await run_evaluation(
        cases, cfg, {"semantic": {"service": fake}}, output=output, manifest=manifest(("q1", "q2"))
    )
    assert fake.execute.await_count == 1
    assert summary["progress"]["reason"] == "rate_or_quota_limit"
    assert summary["progress"]["failed_or_incomplete_measured"] == 1
    assert summary["progress"]["unattempted_measured"] == 1
    assert summary["modes"]["semantic"]["error_categories"] == {"rate_limit": 1}


async def test_cancelled_in_flight_is_not_reported_as_unattempted(tmp_path):
    fake = SimpleNamespace(execute=AsyncMock(side_effect=asyncio.CancelledError()))
    cfg = EvaluationConfig(modes=("semantic",), warmups=0, repetitions=1)
    output = tmp_path / "in-flight"
    with pytest.raises(asyncio.CancelledError):
        await run_evaluation([case()], cfg, {"semantic": {"service": fake}}, output=output, manifest=manifest())
    progress = json.loads((output / "summary.json").read_text())["progress"]
    assert progress["started_without_recorded_outcome"] == 1
    assert progress["unattempted_measured"] == 0
    assert progress["in_flight_attempt"]["question_id"] == "q1"


@pytest.mark.parametrize("abstain", [False, True])
async def test_quick_answer_uses_existing_representation_no_gold_in_generation(tmp_path, abstain):
    search, _ = service()
    generator = SimpleNamespace(
        generate=AsyncMock(
            return_value=GenerationResult(
                status=AnswerStatus.INSUFFICIENT_EVIDENCE if abstain else AnswerStatus.ANSWERED,
                answer=None if abstain else "Answer.",
                citations=() if abstain else ("E1",),
            )
        )
    )
    answer = AnswerQuestion(search, ObservedGenerator(generator, RequestBudget(2)))
    adapted = AnsweringRun(
        name="quick_answer",
        mode="single_step",
        service=answer,
        retrieval_mode="semantic",
        chunking={},
        embedding={},
        generation_model="fake",
        answering_budgets={},
    )
    cfg = EvaluationConfig(
        modes=("quick_answer",),
        allow_generation=True,
        max_generation_requests=2,
        warmups=0,
        repetitions=1,
        include_evidence_text=True,
    )
    result = await run_evaluation(
        [case()],
        cfg,
        {"quick_answer": {"service": answer, "answer_run": adapted}},
        output=tmp_path / "run",
        manifest=manifest(),
    )
    row = read_jsonl(tmp_path / "run/answer_results.jsonl")[0]
    assert row["generation_calls"] == 1 and row["retrieval_actions"] == 1
    assert row["citation_structure_valid"]
    assert row["timings"]["evidence_selection_ms"] is not None
    payload = generator.generate.await_args.args[0].context_json()
    assert "REFERENCE_ONLY_SENTINEL" not in payload and "gold" not in payload
    assert "source fact" in payload
    assert result["modes"]["quick_answer"]["abstained"] == int(abstain)
    assert read_jsonl(tmp_path / "run/grounding_review.jsonl")[0]["review_status"] == "pending"


async def test_investigation_unchanged_searches_generation_usage_and_budget(tmp_path):
    search, _ = service()
    model = SimpleNamespace(
        next_action=AsyncMock(
            side_effect=[
                SearchEvidenceCall(query="Question?", call_id="call1"),
                InvestigationDecision(status=InvestigationStatus.ANSWERED, answer="Answer.", citations=("E1",)),
            ]
        )
    )
    budget = RequestBudget(2)
    investigation = InvestigateQuestion(search, ObservedGenerator(model, budget))
    adapted = AnsweringRun(
        name="investigation",
        mode="multi_step",
        service=investigation,
        retrieval_mode="semantic",
        chunking={},
        embedding={},
        generation_model="fake",
        investigation_budgets={},
    )
    cfg = EvaluationConfig(
        modes=("investigation",), allow_generation=True, max_generation_requests=2, warmups=0, repetitions=1
    )
    await run_evaluation(
        [case()],
        cfg,
        {"investigation": {"service": investigation, "answer_run": adapted}},
        output=tmp_path / "run",
        manifest=manifest(),
    )
    row = read_jsonl(tmp_path / "run/answer_results.jsonl")[0]
    assert row["status"] == "answered" and row["generation_calls"] == 2 and row["retrieval_actions"] == 1
    assert row["citation_structure_valid"] and row["citation_ids"] == ["E1"]
    assert row["timings"]["evidence_selection_ms"] is not None
    assert all(
        "REFERENCE_ONLY_SENTINEL" not in call.args[0].context_json() for call in model.next_action.await_args_list
    )
    with pytest.raises(RequestBudgetExceeded):
        await ObservedGenerator(model, budget).next_action(None)
    assert model.next_action.await_count == 2


async def test_observation_scopes_do_not_leak_between_requests():
    search, _ = service()
    with capture_timings(), observe() as first:
        await search.execute("Question?", 20, owner_id=None)
    with capture_timings(), observe() as second:
        pass
    assert len(first.repository_ms) == 1 and not second.repository_ms
    assert first.stages["retrieval.total"] and not second.stages


async def test_cli_generation_guard_precedes_any_dataset_database_or_provider_io(monkeypatch):
    from investigation_engine.evaluation import cli

    def forbidden(*args):
        pytest.fail("No I/O is authorized without both generation flags")

    monkeypatch.setattr(cli, "selected_cases", forbidden)
    args = cli.parser().parse_args(["run", "--modes", "quick_answer"])
    with pytest.raises(ValueError, match="allow-generation"):
        await cli.execute_run(args)
    args = cli.parser().parse_args(["run", "--modes", "investigation", "--allow-generation"])
    with pytest.raises(ValueError, match="positive"):
        await cli.execute_run(args)


async def test_generation_cap_includes_warmups_and_preserves_failed_denominator(tmp_path):
    search, _ = service()
    generator = SimpleNamespace(
        generate=AsyncMock(
            return_value=GenerationResult(status=AnswerStatus.ANSWERED, answer="Answer.", citations=("E1",))
        )
    )
    answering = AnswerQuestion(search, ObservedGenerator(generator, RequestBudget(1)))
    adapted = AnsweringRun(
        name="quick_answer",
        mode="single_step",
        service=answering,
        retrieval_mode="semantic",
        chunking={},
        embedding={},
        generation_model="fake",
        answering_budgets={},
    )
    cfg = EvaluationConfig(
        modes=("quick_answer",), allow_generation=True, max_generation_requests=1, warmups=1, repetitions=1
    )
    summary = await run_evaluation(
        [case()],
        cfg,
        {"quick_answer": {"service": answering, "answer_run": adapted}},
        output=tmp_path / "run",
        manifest=manifest(),
    )
    assert generator.generate.await_count == 1
    assert summary["modes"]["quick_answer"]["error_categories"] == {"request_budget": 1}
    assert summary["modes"]["quick_answer"]["answer_exact_match"] == 0
    final = read_jsonl(tmp_path / "run/answer_results.jsonl")[-1]
    assert final["metrics"]["supporting_facts"][0]["state"] == "candidate_only_final_selection_unavailable"
    assert json.loads((tmp_path / "run/manifest.json").read_text())["generation_requests_observed"] == 1


def test_sql_observation_records_no_statements_parameters_or_raw_errors():
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import SQLAlchemyError

    engine = create_engine("sqlite://")
    try:
        with observe_sql(engine), observe() as observation, engine.connect() as connection:
            connection.execute(text("SELECT :value"), {"value": "PRIVATE_SQL_PARAMETER"})
            with pytest.raises(SQLAlchemyError):
                connection.execute(text("SELECT * FROM PRIVATE_TABLE_SENTINEL"))
        assert len(observation.sql) == 2
        assert observation.sql[0]["succeeded"] and not observation.sql[1]["succeeded"]
        assert "PRIVATE" not in json.dumps(observation.timings())
    finally:
        engine.dispose()


async def test_actual_provider_counters_are_separate_from_adapter_attempts():
    class Provider:
        provider_requests = 0

        async def generate(self, request):
            if request is None:
                raise ValueError("Before network")
            self.provider_requests += 1
            return SimpleNamespace(usage=None, model_version="fake")

    wrapped = ObservedGenerator(Provider(), RequestBudget(2))
    with observe() as observation:
        await wrapped.generate("request")
        with pytest.raises(ValueError):
            await wrapped.generate(None)
    assert [item["provider_requests"] for item in observation.generation] == [1, 0]
