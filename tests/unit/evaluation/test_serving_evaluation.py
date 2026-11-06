import json
from dataclasses import asdict, replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from tests.unit.evaluation.test_structured_evaluation import case, hit, manifest, service

from investigation_engine.application.answers import AnswerStatus, GenerationResult
from investigation_engine.application.ports.generation_errors import EvidenceContextLimitError
from investigation_engine.application.use_cases.answer_question import AnswerQuestion, _select_generation_evidence
from investigation_engine.evaluation.reporting import compare_runs, read_jsonl, write_json
from investigation_engine.evaluation.runner import EvaluationConfig, run_evaluation
from investigation_engine.evaluation.serving import pipeline_facts, serving_record, similar_passages


class RankedSearch:
    def __init__(self, hits):
        self.hits, self.calls = hits, []

    async def execute(self, question, limit, *, owner_id):
        self.calls.append((question, limit, owner_id))
        return self.hits[:limit]


def test_disk_full_during_manifest_replacement_preserves_last_valid_file(tmp_path, monkeypatch):
    target = tmp_path / "manifest.json"
    write_json(target, {"status": "running"})

    def disk_full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "replace", disk_full)
    with pytest.raises(OSError):
        write_json(target, {"status": "complete"})
    assert target.read_text(encoding="utf-8").strip() == '{\n  "status": "running"\n}'
    assert list(tmp_path.iterdir()) == [target]


async def test_report_failure_does_not_mark_run_complete(tmp_path, monkeypatch):
    from investigation_engine.evaluation import runner

    original = runner.write_json

    def fail_analysis(path, value):
        if path.name == "analysis.json":
            raise OSError(28, "No space left on device")
        return original(path, value)

    monkeypatch.setattr(runner, "write_json", fail_analysis)
    search, _ = service()
    output = tmp_path / "run"
    with pytest.raises(OSError):
        await run_evaluation(
            [case()],
            EvaluationConfig(modes=("semantic",), warmups=0, repetitions=1),
            {"semantic": {"service": search}},
            output=output,
            manifest=manifest(),
        )
    assert json.loads((output / "manifest.json").read_text())["status"] == "finalizing"


def gold(item):
    return {
        "source": asdict(item.source),
        "content_sha256": item.content_sha256,
        "start": 0,
        "end": min(11, item.end),
        "sentence_index": 0,
    }


def passage(number, text):
    return replace(hit(number), text=text, end=len(text), content_sha256=sha256(text.encode()).hexdigest())


async def test_actual_serving_retrieval_count_cutoff_and_lost_support():
    ranked = tuple(hit(i) for i in range(1, 9))
    search = RankedSearch(ranked)
    preparer = AnswerQuestion(search, None, max_evidence=3)
    prepared = await preparer.prepare_evidence("Question?", owner_id=None)
    facts = [gold(ranked[0]), gold(ranked[3])]
    result = serving_record(prepared, ranked, facts, max_evidence=3)
    assert search.calls == [("Question?", 3, None)]
    assert result["admitted_passages"] == 3 and result["recall"] == 0.5
    assert result["supporting_facts_lost_from_ranked"] == 1
    assert result["rejected_evidence_count_limit"] == 5
    assert result["rejected_context_budget"] == 0
    assert result["distinct_source_documents"] == 3
    assert result["context_chars"] == len(prepared.request.context_json())


async def test_complete_serialized_budget_and_no_count_limit_refill():
    first, second = passage(1, "a" * 120), passage(2, "b" * 120)
    prepared = await AnswerQuestion(RankedSearch((first, second)), None, max_context_chars=256).prepare_evidence(
        "Question?", owner_id=None
    )
    result = serving_record(prepared, (first, second), [gold(first), gold(second)], max_evidence=5)
    assert 120 + 120 < 256  # Text alone would incorrectly admit both.
    assert result["admitted_passages"] == 1 and result["rejected_context_budget"] == 1
    assert len(prepared.request.context_json()) <= 256

    ranked = tuple(passage(i, str(i) * 400) for i in range(1, 6)) + (hit(6),)
    generator = SimpleNamespace(generate=AsyncMock())
    answer = AnswerQuestion(RankedSearch(ranked), generator, max_context_chars=256)
    prepared = await answer.prepare_evidence("Question?", owner_id=None)
    result = serving_record(prepared, ranked, [gold(ranked[-1])], max_evidence=5)
    assert result["admitted_passages"] == 0 and result["status"] == "context_exhausted"
    assert result["rejected_context_budget"] == 5 and result["rejected_evidence_count_limit"] == 1
    assert not result["would_generate"]
    with pytest.raises(EvidenceContextLimitError):
        await answer.execute("Question?", owner_id=None)
    generator.generate.assert_not_called()


async def test_canonical_duplicates_consume_serving_retrieval_positions_without_refill():
    a, b, c = hit(1), hit(2), hit(3)
    ranked = (a, a, b, c)
    prepared = await AnswerQuestion(RankedSearch(ranked), None, max_evidence=3).prepare_evidence(
        "Question?", owner_id=None
    )
    result = serving_record(prepared, ranked, [gold(a), gold(b), gold(c)], max_evidence=3)
    assert result["admitted_passages"] == 2
    assert result["rejected_canonical_duplicates"] == 1
    assert result["rejected_evidence_count_limit"] == 1
    assert result["recall"] == 2 / 3 and not result["complete_support"]


async def test_empty_preparation_and_observed_boundary_matches_generation_request():
    generator = SimpleNamespace(
        generate=AsyncMock(
            return_value=GenerationResult(status=AnswerStatus.ANSWERED, answer="Answer.", citations=("E1",))
        )
    )
    answer = AnswerQuestion(RankedSearch((hit(),)), generator)
    prepared = await answer.prepare_evidence("Question?", owner_id=None)
    generator.generate.assert_not_called()
    response = await answer.execute("Question?", owner_id=None)
    assert generator.generate.await_args.args[0] == prepared.request
    assert response.retrieved_evidence[0].evidence == prepared.selected["E1"]
    empty = await AnswerQuestion(RankedSearch(()), None).prepare_evidence("Question?", owner_id=None)
    record = serving_record(empty, (), [gold(hit())], max_evidence=5)
    assert record["context_chars"] == 0 and record["recall"] == 0
    assert record["status"] == "empty_context" and not record["would_generate"]


@pytest.mark.parametrize("context_limit", [256, 1000, 12000])
def test_observation_callback_preserves_selector_outputs_and_range_text(context_limit):
    hits = (passage(1, 'Quote " \n😀 ' * 30), passage(2, "fact " * 10), hit(3))
    original = _select_generation_evidence("Unicode 😀 question?", hits, 2, context_limit)
    observations = []
    observed = _select_generation_evidence(
        "Unicode 😀 question?",
        hits,
        2,
        context_limit,
        on_decision=lambda item, reason: observations.append((item, reason)),
    )
    assert observed == original
    assert observations[-1] == (hits[2], "evidence_count")
    assert hits[0].text == 'Quote " \n😀 ' * 30


async def test_fact_stage_taxonomy_count_context_channel_fusion_and_corpus():
    a, b, c, d, absent, ineligible = tuple(hit(i) for i in range(1, 7))
    facts = [gold(item) for item in (a, b, c, d, absent, ineligible)] + [
        {"mapping_error": "ambiguous", "sentence_index": 0}
    ]
    prepared = await AnswerQuestion(RankedSearch((a, b)), None, max_evidence=1).prepare_evidence(
        "Question?", owner_id=None
    )
    serving = serving_record(prepared, (a, b), facts, max_evidence=1)
    availability = {str(i): "eligible" for i in range(4)} | {
        "4": "absent_from_shared_corpus",
        "5": "present_but_ineligible",
    }
    result = pipeline_facts(
        (a, b), {"semantic": (a, b), "lexical": (c,)}, facts, serving, availability, retrieval_observed=True
    )
    assert [item["state"] for item in result] == [
        "included_in_admitted_context",
        "ranked_excluded_from_serving",
        "channel_evidence_excluded_from_ranked_top20",
        "available_not_retrieved_by_channels",
        "absent_from_shared_corpus",
        "present_but_ineligible",
        "unresolvable_gold_mapping",
    ]
    assert result[1]["exclusion_reason"] == "evidence_count_limit"
    long = passage(1, "source fact " * 100)
    prepared = await AnswerQuestion(RankedSearch((long, b)), None, max_context_chars=256).prepare_evidence(
        "Question?", owner_id=None
    )
    serving = serving_record(prepared, (long, b), [gold(long)], max_evidence=5)
    result = pipeline_facts(
        (long, b),
        {"semantic": (long,), "lexical": ()},
        [gold(long)],
        serving,
        {"0": "eligible"},
        retrieval_observed=True,
    )
    assert result[0]["exclusion_reason"] == "context_budget"


async def test_different_actual_serving_retrieval_is_observed_and_not_misattributed():
    ranked = (hit(1), hit(2))
    prepared = await AnswerQuestion(RankedSearch((hit(3),)), None, max_evidence=1).prepare_evidence(
        "Question?", owner_id=None
    )
    result = serving_record(prepared, ranked, [gold(ranked[0])], max_evidence=1)
    assert not result["retrieval_matches_ranked_prefix"]
    assert result["rejected_evidence_count_limit"] is None
    facts = pipeline_facts(
        ranked,
        {"semantic": ranked, "lexical": ()},
        [gold(ranked[0])],
        result,
        {"0": "eligible"},
        retrieval_observed=True,
    )
    assert facts[0]["exclusion_reason"] == "serving_retrieval_difference_or_canonical_balancing"


def test_similar_passage_heuristic_has_explicit_definition_and_no_exact_claim():
    first = passage(1, "alpha beta gamma delta epsilon zeta eta theta")
    second = passage(2, "ALPHA  beta gamma delta epsilon zeta eta theta")
    result = similar_passages((first, second))
    assert result["pairs"][0]["jaccard"] == 1
    assert "Approximate" in result["method"]


async def test_runner_serving_aggregation_failed_modes_and_saved_comparison(tmp_path):
    search, raw = service((hit(1), hit(2)))
    prepare = AnswerQuestion(search, None, max_evidence=1)
    cfg = EvaluationConfig(modes=("semantic",), serving_max_evidence=1, warmups=1, repetitions=2)
    first = tmp_path / "first"
    summary = await run_evaluation(
        [case()], cfg, {"semantic": {"service": search, "preparer": prepare}}, output=first, manifest=manifest()
    )
    assert raw.search.await_count == 6  # 20-result observation + actual preparation, including warmup.
    serving = summary["modes"]["semantic"]["serving"]
    assert serving["observed_attempts"] == 2 and serving["observed_questions"] == 1
    assert serving["recall"] == 1 and serving["complete_support"] == 1
    assert serving["admission_ms"]["count"] == 2
    assert read_jsonl(first / "retrieval_results.jsonl")[1]["candidate_counts"]["semantic"] == 2
    assert (first / "analysis.json").exists()
    assert "source fact" not in (first / "summary.json").read_text()

    failed, _ = service(cache={})
    never = SimpleNamespace(prepare_evidence=AsyncMock())
    second = tmp_path / "second"
    unavailable = await run_evaluation(
        [case()], cfg, {"semantic": {"service": failed, "preparer": never}}, output=second, manifest=manifest()
    )
    never.prepare_evidence.assert_not_called()
    assert unavailable["modes"]["semantic"]["blocked_missing_cache"]
    assert unavailable["modes"]["semantic"]["serving"]["recall"] is None
    comparison = compare_runs(first, second, left_mode="semantic", right_mode="semantic")
    assert comparison["serving_metric_deltas"]["recall"] is None
    assert comparison["metric_deltas"]["recall_at_20"] is None
