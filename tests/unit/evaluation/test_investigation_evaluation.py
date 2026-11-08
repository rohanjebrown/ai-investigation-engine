import json
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from tests.unit.evaluation.test_structured_evaluation import case, hit, manifest, service

from investigation_engine.application.embeddings import EmbeddingConfig
from investigation_engine.application.investigations import (
    InvestigateQuestion,
    InvestigationDecision,
    InvestigationStatus,
    SearchEvidenceCall,
)
from investigation_engine.application.ports.generation_errors import GenerationProviderError
from investigation_engine.application.ports.provider_diagnostics import attach_diagnostic
from investigation_engine.application.use_cases.search_evidence import SearchEvidence
from investigation_engine.evaluation.answer_quality import AnsweringRun
from investigation_engine.evaluation.cached_queries import CachedQueryMiss, load_query_cache
from investigation_engine.evaluation.investigation import (
    ObservedInvestigationModel,
    ObservedInvestigationSearch,
    investigation_record,
    investigation_request_plan,
)
from investigation_engine.evaluation.live_providers import (
    BudgetedGeminiEmbeddingProvider,
    PacedInvestigationModel,
    ProviderPacer,
)
from investigation_engine.evaluation.live_queries import ReadThroughQueryEmbeddings
from investigation_engine.evaluation.reporting import compare_runs, read_jsonl, write_json
from investigation_engine.evaluation.runner import EvaluationConfig, run_evaluation
from investigation_engine.evaluation.telemetry import ObservedGenerator, RequestBudget, RequestBudgetExceeded, observe
from investigation_engine.infrastructure.embeddings import gemini_provider

VECTOR = [1.0] + [0.0] * 767
PROFILE = EmbeddingConfig(
    provider="gemini_developer_api", model_id="gemini-embedding-2", dimensions=768, document_format="gemini_no_title_v1"
)


def query_cache(tmp_path, vectors=None):
    path = tmp_path / "queries.json"
    write_json(
        path,
        {
            "version": 1,
            "configuration": asdict(PROFILE),
            "vectors": vectors or {"Question?": VECTOR},
            "origin": "preserve",
        },
    )
    return path


def live_provider(monkeypatch, maximum=4):
    models = SimpleNamespace(
        count_tokens=AsyncMock(return_value=SimpleNamespace(total_tokens=12)),
        embed_content=AsyncMock(
            return_value=SimpleNamespace(embeddings=[SimpleNamespace(values=VECTOR, statistics=None)])
        ),
    )
    client = SimpleNamespace(aio=SimpleNamespace(models=models, aclose=AsyncMock()), close=Mock())
    monkeypatch.setattr(gemini_provider.genai, "Client", Mock(return_value=client))
    provider = BudgetedGeminiEmbeddingProvider(
        api_key="unit-only", maximum_requests=maximum, pacer=SimpleNamespace(wait=AsyncMock())
    )
    return provider, models


async def test_read_through_reuses_preserves_and_persists_exact_compatible_queries(tmp_path, monkeypatch):
    path = query_cache(tmp_path)
    live, models = live_provider(monkeypatch)
    provider = ReadThroughQueryEmbeddings(PROFILE, {"Question?": VECTOR}, cache_path=path, live=live, allow_live=True)
    with observe() as observation:
        original = await provider.embed_query("Question?")
        generated = await provider.embed_query("Dynamically discovered entity birthplace")
        repeated = await provider.embed_query("Dynamically discovered entity birthplace")
    assert original == generated == repeated
    assert provider.hits == 2 and provider.misses == 1
    assert live.request_counts == {"count_tokens": 1, "embed_content": 1}
    assert [call["cache_hit"] for call in observation.embeddings] == [True, False, True]
    assert observation.embeddings[1]["provider_requests"] == live.request_counts
    assert load_query_cache(path)[1]["Question?"] == VECTOR
    assert json.loads(path.read_text())["origin"] == "preserve"
    assert len(load_query_cache(path)[1]) == 2
    payload = models.embed_content.await_args.kwargs
    assert payload["model"] == "gemini-embedding-2"
    assert payload["contents"].parts[0].text == "task: search result | query: Dynamically discovered entity birthplace"
    assert payload["config"].output_dimensionality == 768
    assert payload["config"].task_type is None


async def test_offline_miss_and_profile_or_permission_guard_never_call_live(tmp_path, monkeypatch):
    path = query_cache(tmp_path)
    provider = ReadThroughQueryEmbeddings(PROFILE, {}, cache_path=path)
    with observe() as observation, pytest.raises(CachedQueryMiss):
        await provider.embed_query("unseen")
    assert observation.embeddings[0]["provider_requests"] == {"count_tokens": 0, "embed_content": 0}
    live, models = live_provider(monkeypatch)
    for profile, allowed in ((PROFILE, False), (replace(PROFILE, model_id="different-model"), True)):
        with pytest.raises(ValueError, match="authorization|profile"):
            ReadThroughQueryEmbeddings(profile, {}, cache_path=path, live=live, allow_live=allowed)
    models.count_tokens.assert_not_called()
    with pytest.raises(Exception, match="document embeddings"):
        await provider.embed_documents(["never generate documents"])


async def test_rpc_cap_includes_counting_and_never_starts_unfunded_query(tmp_path, monkeypatch):
    live, models = live_provider(monkeypatch, maximum=3)
    path = query_cache(tmp_path)
    provider = ReadThroughQueryEmbeddings(PROFILE, {}, cache_path=path, live=live, allow_live=True)
    await provider.embed_query("new")
    with pytest.raises(RequestBudgetExceeded):
        await provider.embed_query("another")
    assert models.count_tokens.await_count == models.embed_content.await_count == 1
    assert live.budget.used == 2
    assert load_query_cache(path)[1]["new"] == VECTOR


async def test_persisting_miss_preserves_raw_existing_cache_encoding(tmp_path, monkeypatch):
    raw = [0.12345678912345678] + [0.0] * 767
    path = query_cache(tmp_path, {"original": raw})
    live, _ = live_provider(monkeypatch, maximum=2)
    provider = ReadThroughQueryEmbeddings(PROFILE, {"original": raw}, cache_path=path, live=live, allow_live=True)
    await provider.embed_query("new")
    assert load_query_cache(path)[1]["original"] == raw


async def test_provider_failure_accounted_and_does_not_cache_failed_vector(tmp_path, monkeypatch):
    live, models = live_provider(monkeypatch)
    models.embed_content.side_effect = gemini_provider.httpx.ConnectError("transport")
    path = query_cache(tmp_path)
    before = path.read_bytes()
    provider = ReadThroughQueryEmbeddings(PROFILE, {}, cache_path=path, live=live, allow_live=True)
    with observe() as observation, pytest.raises(Exception):
        await provider.embed_query("failure")
    assert path.read_bytes() == before
    assert live.request_counts == {"count_tokens": 1, "embed_content": 1}
    assert observation.embeddings[0]["status"] == "failed"
    assert models.embed_content.await_count == 1  # No automatic retry.


async def test_pacer_enforces_rolling_rpm_tpm_without_real_sleep():
    now = [0.0]
    waits = []

    async def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds

    pacer = ProviderPacer(10, 100, clock=lambda: now[0], sleep=sleep)
    await pacer.wait(60)
    await pacer.wait(60)
    assert now[0] == 60 and waits == [60]
    await pacer.wait(10)
    assert now[0] == 66
    with pytest.raises(ValueError):
        await pacer.wait(101)


async def test_real_workflow_live_missing_follow_up_uses_cache_and_counts_rpc(tmp_path, monkeypatch):
    original, raw = service()
    raw.search.side_effect = [(hit(),), (hit(2),)]
    live, _ = live_provider(monkeypatch, maximum=2)
    path = query_cache(tmp_path)
    provider = ReadThroughQueryEmbeddings(PROFILE, {"Question?": VECTOR}, cache_path=path, live=live, allow_live=True)
    search = SearchEvidence(original._repository, provider, original._chunking, PROFILE)
    model = ScriptedModel(
        [
            SearchEvidenceCall(query="Question?", call_id="a"),
            SearchEvidenceCall(query="learned entity birthplace", call_id="b"),
            decision(),
        ]
    )
    result, record = await execute_record(search, model)
    assert result.status == InvestigationStatus.ANSWERED
    assert record["coverage"]["final_generation"]["complete"]
    assert [item["cache_hit"] for item in record["embedding_calls"]] == [True, False]
    assert live.request_counts == {"count_tokens": 1, "embed_content": 1}
    assert [item["repository_calls"] for item in record["searches"]] == [1, 1]
    assert record["searches"][0]["accumulated_coverage_after"]["recall"] == 0.5
    assert "learned entity birthplace" in load_query_cache(path)[1]


class ScriptedModel:
    def __init__(self, actions):
        self.actions = list(actions)
        self.turns = []
        self.provider_requests = 0

    async def next_action(self, turn):
        self.turns.append(turn)
        self.provider_requests += 1
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action(turn) if callable(action) else action


class QuerySearch:
    def __init__(self, mapping):
        self.mapping, self.calls = mapping, []

    async def execute(self, query, limit, *, owner_id):
        self.calls.append((query, limit, owner_id))
        value = self.mapping[query]
        if isinstance(value, Exception):
            raise value
        return tuple(value)[:limit]


def decision(status=InvestigationStatus.ANSWERED):
    return InvestigationDecision(
        status=status,
        answer="Answer" if status == InvestigationStatus.ANSWERED else None,
        citations=("E1", "E2") if status == InvestigationStatus.ANSWERED else (),
    )


def two_fact_case():
    value = case()
    value["reference_answer"] = "Answer"
    value["gold"].append({**value["gold"][0], "content_sha256": hit(2).content_sha256, "source": asdict(hit(2).source)})
    return value


async def execute_record(search, model, *, budgets=None):
    actual = InvestigateQuestion(
        ObservedInvestigationSearch(search, "hybrid"),
        ObservedInvestigationModel(ObservedGenerator(model, RequestBudget(12))),
        **(budgets or {}),
    )
    with observe() as observation:
        result = await actual.execute("Question?", owner_id=None)
    return result, investigation_record(
        observation, result, two_fact_case()["gold"], retrieval_mode="hybrid", budgets=budgets
    )


async def test_real_investigation_dynamic_searches_accumulation_and_final_context():
    search = QuerySearch({"initial": (hit(), hit()), "learned entity birthplace": (hit(), hit(2))})

    def follow(turn):
        assert turn.exchanges[0].evidence[0].evidence == hit()
        return SearchEvidenceCall(query="learned entity birthplace", call_id="b")

    model = ScriptedModel([SearchEvidenceCall(query="initial", call_id="a"), follow, decision()])
    result, record = await execute_record(search, model)
    assert result.status == InvestigationStatus.ANSWERED
    assert record["search_tool_calls"] == record["executed_searches"] == 2
    assert record["retrieved_passages"] == 4 and record["distinct_retrieved_passages"] == 2
    assert record["accumulated_count"] == 2
    assert record["coverage"]["first_search"]["recall"] == 0.5
    assert all(record["coverage"][key]["complete"] for key in ("all_searches", "accumulated", "final_generation"))
    assert record["searches"][1]["query"] == "learned entity birthplace"
    assert len(record["turns"][2]["admitted_evidence"]) == 2
    assert len(record["searches"][0]["admitted_to_transcript"]) == 1
    assert model.provider_requests == 3


@pytest.mark.parametrize("context_limited", [False, True])
async def test_retrieved_evidence_is_not_necessarily_accumulated_or_supplied(context_limited):
    long = replace(hit(), text="x" * 900, end=900)
    search = QuerySearch({"initial": (long,) if context_limited else (hit(), hit(2))})
    model = ScriptedModel(
        [SearchEvidenceCall(query="initial", call_id="a"), decision(InvestigationStatus.INSUFFICIENT_EVIDENCE)]
    )
    budgets = {"max_context_chars": 512} if context_limited else {"max_accumulated_evidence": 1}
    result, record = await execute_record(search, model, budgets=budgets)
    assert result.status == InvestigationStatus.INSUFFICIENT_EVIDENCE
    assert record["retrieved_passages"] > record["accumulated_count"]
    assert record["coverage"]["all_searches"]["recall"] > record["coverage"]["final_generation"]["recall"]
    assert record["limits_observed"]["context" if context_limited else "evidence"]


@pytest.mark.parametrize("reason", ["search", "generation", "turn"])
async def test_incomplete_and_limits_preserve_observed_state_without_inventing_final_context(reason):
    actions = [SearchEvidenceCall(query="initial", call_id="a"), SearchEvidenceCall(query="follow", call_id="b")]
    budgets = (
        {"max_search_calls": 1}
        if reason == "search"
        else {"max_generation_requests": 1}
        if reason == "generation"
        else {"max_turns": 1}
    )
    result, record = await execute_record(QuerySearch({"initial": (hit(),)}), ScriptedModel(actions), budgets=budgets)
    assert result.status == InvestigationStatus.INCOMPLETE
    assert record["limits_observed"][reason]
    assert record["coverage"]["accumulated"]["recall"] == 0.5
    assert record["final_generation_evidence"] is None
    assert not record["coverage"]["final_generation"]["observed"]


async def test_wrapper_behavior_matches_uninstrumented_real_use_case():
    def actions():
        return [
            SearchEvidenceCall(query="initial", call_id="a"),
            SearchEvidenceCall(query="follow", call_id="b"),
            decision(),
        ]

    mapping = {"initial": (hit(),), "follow": (hit(2),)}
    raw_search, observed_search = QuerySearch(mapping), QuerySearch(mapping)
    raw_model, observed_model = ScriptedModel(actions()), ScriptedModel(actions())
    expected = await InvestigateQuestion(raw_search, raw_model).execute("Question?", owner_id=None)
    actual, _ = await execute_record(observed_search, observed_model)
    assert replace(actual, searches=()) == replace(expected, searches=())
    assert [replace(item, duration_ms=0) for item in actual.searches] == [
        replace(item, duration_ms=0) for item in expected.searches
    ]
    assert raw_search.calls == observed_search.calls and raw_model.turns == observed_model.turns


@pytest.mark.parametrize("retrieval_mode", ["semantic", "lexical", "hybrid"])
async def test_runner_reports_actual_investigation_scores_usage_and_traces(tmp_path, retrieval_mode):
    search = QuerySearch({"initial": (hit(),), "follow": (hit(2),)})
    model = ScriptedModel(
        [SearchEvidenceCall(query="initial", call_id="a"), SearchEvidenceCall(query="follow", call_id="b"), decision()]
    )
    service = InvestigateQuestion(
        ObservedInvestigationSearch(search, retrieval_mode),
        ObservedInvestigationModel(ObservedGenerator(model, RequestBudget(3))),
    )
    mode = "investigation_" + retrieval_mode
    adapted = AnsweringRun(
        name=mode,
        mode="multi_step",
        service=service,
        retrieval_mode=retrieval_mode,
        chunking={},
        embedding={},
        generation_model="fake",
        investigation_budgets={},
    )
    cfg = EvaluationConfig(modes=(mode,), allow_generation=True, max_generation_requests=3)
    assert cfg.repetitions == 1 and cfg.warmups == 0
    out = tmp_path / "run"
    summary = await run_evaluation(
        [two_fact_case()], cfg, {mode: {"service": service, "answer_run": adapted}}, output=out, manifest=manifest()
    )
    row = read_jsonl(out / "answer_results.jsonl")[0]
    trace = read_jsonl(out / "investigation_traces.jsonl")[0]
    assert row["answer_metrics"] == {"exact_match": 1, "f1": 1}
    assert trace["correct_by_normalized_exact_match"]
    assert trace["expected_answer"] == trace["answer"] == "Answer"
    assert trace["citations"] == ["E1", "E2"]
    assert row["generation_provider_requests"] == 3
    inv = summary["modes"][mode]["investigation"]
    assert inv["average_searches"] == 2 and inv["average_generation_calls"] == 3
    assert inv["coverage"]["final_generation"]["complete_all_attempts"] == 1
    assert "End-to-end Investigation comparison" in (out / "report.md").read_text()
    assert "Candidate ranking and actual serving admission" not in (out / "report.md").read_text()
    comparison = compare_runs(out, out, left_mode=mode, right_mode=mode)
    assert comparison["metric_deltas"]["answer_exact_match"] == 0
    assert comparison["investigation_metric_deltas"]["final_generation"]["recall_all_attempts"] == 0


@pytest.mark.parametrize("failure", ["provider", "search", "budget"])
async def test_failed_runs_retain_partial_searches_context_and_all_attempt_denominator(tmp_path, failure):
    mapping = {"initial": (hit(),)}
    provider_error = attach_diagnostic(
        GenerationProviderError("RAW_PROVIDER_MESSAGE_MUST_NOT_EXPORT"),
        operation="generate_content",
        model_id="fake",
        http_status=503,
        category="availability",
        request_count=2,
    )
    model = ScriptedModel([SearchEvidenceCall(query="initial", call_id="a"), provider_error])
    if failure == "search":
        mapping["initial"] = RuntimeError("search failure")
    budget = RequestBudget(1 if failure == "budget" else 3)
    service = InvestigateQuestion(
        ObservedInvestigationSearch(QuerySearch(mapping), "semantic"),
        ObservedInvestigationModel(ObservedGenerator(model, budget)),
    )
    adapted = AnsweringRun(
        name="investigation",
        mode="multi_step",
        service=service,
        retrieval_mode="semantic",
        chunking={},
        embedding={},
        generation_model="fake",
        investigation_budgets={},
    )
    cfg = EvaluationConfig(modes=("investigation",), allow_generation=True, max_generation_requests=budget.maximum)
    out = tmp_path / "run"
    summary = await run_evaluation(
        [two_fact_case()],
        cfg,
        {"investigation": {"service": service, "answer_run": adapted}},
        output=out,
        manifest=manifest(),
    )
    trace = read_jsonl(out / "investigation_traces.jsonl")[0]
    assert trace["status"] == "failed" and trace["stop_kind"] == "error"
    assert trace["final_generation_evidence"] is None
    assert len(trace["searches"]) == 1
    assert trace["coverage"]["accumulated"]["recall"] == (0 if failure == "search" else 0.5)
    assert trace["external_request_limit_reached"] == (failure == "budget")
    if failure == "provider":
        assert trace["error"]["provider_diagnostic"]["http_status"] == 503
        assert "RAW_PROVIDER_MESSAGE_MUST_NOT_EXPORT" not in (out / "investigation_traces.jsonl").read_text()
    assert summary["modes"]["investigation"]["failed_or_incomplete"] == 1
    assert summary["modes"]["investigation"]["answer_exact_match"] == 0


def test_live_guards_and_retrieval_defaults():
    assert EvaluationConfig().repetitions == 3 and EvaluationConfig().warmups == 1
    with pytest.raises(ValueError, match="answer mode"):
        EvaluationConfig(allow_live_embeddings=True, max_embedding_requests=2)
    with pytest.raises(ValueError, match="allow-live-embeddings"):
        EvaluationConfig(max_embedding_requests=2)
    with pytest.raises(ValueError, match="at least two"):
        EvaluationConfig(
            modes=("investigation",),
            allow_generation=True,
            max_generation_requests=2,
            allow_live_embeddings=True,
            max_embedding_requests=1,
        )


@pytest.mark.parametrize("include_text", [True, False])
async def test_incremental_wire_trace_keeps_raw_repeated_retrieval_and_coverage(tmp_path, include_text):
    first = tuple(hit(index) for index in range(1, 6))
    second = (hit(), hit(6), hit(2), hit(3), hit(4))
    model = ScriptedModel(
        [
            SearchEvidenceCall(query="initial", call_id="a"),
            SearchEvidenceCall(query="follow", call_id="b"),
            InvestigationDecision(status=InvestigationStatus.ANSWERED, answer="Answer", citations=("E1", "E6")),
        ]
    )
    service = InvestigateQuestion(
        ObservedInvestigationSearch(QuerySearch({"initial": first, "follow": second}), "hybrid"),
        ObservedInvestigationModel(ObservedGenerator(model, RequestBudget(3))),
    )
    adapted = AnsweringRun(
        name="investigation_hybrid",
        mode="multi_step",
        service=service,
        retrieval_mode="hybrid",
        chunking={},
        embedding={},
        generation_model="fake",
        investigation_budgets={},
    )
    reference = two_fact_case()
    reference["gold"][1].update(content_sha256=hit(6).content_sha256, source=asdict(hit(6).source))
    out = tmp_path / "trace"
    await run_evaluation(
        [reference],
        EvaluationConfig(
            modes=("investigation_hybrid",),
            allow_generation=True,
            max_generation_requests=3,
            include_evidence_text=include_text,
        ),
        {"investigation_hybrid": {"service": service, "answer_run": adapted}},
        output=out,
        manifest=manifest(),
    )
    trace = read_jsonl(out / "investigation_traces.jsonl")[0]
    assert [len(item["retrieved"]) for item in trace["searches"]] == [5, 5]
    wire = trace["turns"][-1]["model_facing_search_responses"]
    if include_text:
        assert trace["searches"][1]["retrieved"][0]["text"] == hit().text
        assert "excerpt" not in wire[1]["results"][0]
        assert wire[0]["results"][0]["excerpt"] == hit().text
        assert wire[1]["results"][1]["excerpt"] == hit(6).text
    else:
        assert "text" not in trace["searches"][1]["retrieved"][0]
        assert wire is None
    assert trace["coverage"]["first_search"]["recall"] == 0.5
    assert trace["coverage"]["final_generation"]["recall"] == 1
    assert trace["accumulated_count"] == 6 and trace["retrieved_passages"] == 10


def test_worst_case_request_plan_counts_each_configuration_and_count_tokens():
    budgets = {"max_turns": 6, "max_generation_requests": 6, "max_search_calls": 5, "max_embedding_requests": 5}
    full = investigation_request_plan(40, ["semantic", "lexical", "hybrid"], budgets)
    assert full["attempts_including_warmups"] == 120
    assert full["maximum_generation_rpcs"] == 720
    assert full["maximum_embedding_rpcs_including_count_tokens"] == 800
    smoke = investigation_request_plan(1, ["semantic", "lexical", "hybrid"], budgets)
    assert smoke["maximum_generation_rpcs"] == 18
    assert smoke["maximum_embedding_rpcs_including_count_tokens"] == 20


async def test_paced_model_forwards_original_turn_and_never_exports_native_history():
    from google.genai import types

    from investigation_engine.application.investigation_records import InvestigationTurn

    turn = InvestigationTurn(question="Question?", exchanges=())
    provider = SimpleNamespace(
        _contents=Mock(return_value=[types.Content(parts=[types.Part(text="Question?")])]),
        next_action=AsyncMock(return_value=SearchEvidenceCall(query="Question?", call_id="a")),
        provider_requests=0,
        max_output_tokens=1024,
    )
    limiter = SimpleNamespace(wait=AsyncMock())
    action = await PacedInvestigationModel(provider, limiter).next_action(turn)
    assert action.query == "Question?"
    provider.next_action.assert_awaited_once_with(turn)
    provider._contents.assert_called_once_with(turn)
    assert limiter.wait.await_args.args[0] > 1024
