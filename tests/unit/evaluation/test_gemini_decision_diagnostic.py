"""Standalone decision harness, with fake SDK clients and offline SDK transport."""

import importlib.util
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from google import genai
from google.genai import errors, types

from investigation_engine.application.investigation_records import InvestigationTurn
from investigation_engine.application.retrieval import Evidence
from investigation_engine.infrastructure.generation import gemini_investigation_model as production

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/diagnose_gemini_investigation.py"
spec = importlib.util.spec_from_file_location("gemini_decision_diagnostic", SCRIPT)
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


@pytest.fixture
def settings():
    return SimpleNamespace(
        gemini_api_key="test-key-sensitive",
        db_password="db-sensitive",
        gemini_generation_model="gemini-3.5-flash-lite",
        generation_timeout_seconds=30,
        investigation_max_output_tokens=1024,
    )


@pytest.fixture
def source(tmp_path, registration):
    directory = tmp_path / "saved"
    directory.mkdir()
    records = []
    titles = [
        "William Jolliffe, 4th Baron Hylton",
        "Hylton Jolliffe, 3rd Baron Hylton",
        "Other 3",
        "Other 4",
        "Other 5",
    ]
    texts = [
        "William's father is Hylton Jolliffe, 3rd Baron Hylton.",
        "Hylton's father is Hedworth Jolliffe, 2nd Baron Hylton.",
        "Other passage.",
        "Another passage.",
        "Final passage.",
    ]
    for index, (title, text) in enumerate(zip(titles, texts, strict=True), 1):
        document, version = registration(body=text, source_id=str(index))
        hit = Evidence(
            document_id=document.id,
            document_version_id=version.id,
            content_sha256=version.content.sha256,
            start=0,
            end=len(text),
            text=text,
            source=version.source,
            source_spans=(),
            score=0.5,
            rank=index,
            title=title,
        )
        records.append(asdict(hit) | {"citation_id": f"E{index}"})
    trace = {
        "question_id": diagnostic.QUESTION_ID,
        "question": "Who is William Jolliffe, 4th Baron Hylton's paternal grandfather?",
        "turns": [{"exchanges": []}, {"exchanges": [{"query": "initial search", "evidence": records}]}],
    }
    for filename, value in {
        "investigation_traces.jsonl": trace,
        "answer_results.jsonl": {
            "question_id": diagnostic.QUESTION_ID,
            "expected_answer": "Hedworth Jolliffe, 2nd Baron Hylton",
        },
        "manifest.json": {"run_id": "saved-fixture"},
    }.items():
        (directory / filename).write_text(json.dumps(value, default=str), encoding="utf-8")
    return directory


def args(tmp_path, source, **updates):
    return SimpleNamespace(
        cases=["A", "B", "C", "D"],
        trials=1,
        max_generation_requests=None,
        execute=False,
        pause_seconds=0,
        source_run=source,
        output=tmp_path / "output",
        **updates,
    )


def final_response(answer="Hedworth Jolliffe, 2nd Baron Hylton", citations=("E1", "E2")):
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            text=json.dumps(
                                {
                                    "status": "answered",
                                    "answer": answer,
                                    "citations": list(citations),
                                    "conflicts": [],
                                }
                            )
                        )
                    ],
                ),
                finish_reason=types.FinishReason.STOP,
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=123, candidates_token_count=12),
        model_version="gemini-3.5-flash-lite-test",
    )


async def test_default_preview_all_cases_is_provider_free_and_reference_is_not_in_d(tmp_path, source, settings):
    factory = Mock(side_effect=AssertionError("Preview must not instantiate an SDK client"))
    options = args(tmp_path, source)
    await diagnostic.run(options, settings, client_factory=factory)
    factory.assert_not_called()
    records = [diagnostic.read_json(path) for path in sorted(options.output.glob("call-*.json"))]
    assert len(records) == 4 and all(record["status"] == "not_executed" for record in records)
    assert diagnostic.read_json(options.output / "comparison.json")["attempted_generation_requests"] == 0
    a, b, c, d = records
    assert a["request"]["config"]["tools"] is None and a["decision_mode"].startswith("forced")
    assert a["request"]["config"]["system_instruction"] == production.SYSTEM_INSTRUCTION + diagnostic.FINAL_ONLY_SUFFIX
    assert b["request"]["config"]["system_instruction"] == production.SYSTEM_INSTRUCTION
    b_request, c_request = b["request"], c["request"]
    b_request["config"].pop("system_instruction")
    c_request["config"].pop("system_instruction")
    assert b_request == c_request
    assert "Hedworth" not in json.dumps(d["request"])
    d_message = json.loads(d["request"]["contents"][0]["parts"][0]["text"])
    assert [item["evidence_id"] for item in d_message["saved_search_result"]["results"]] == ["E1"]
    assert all(len(record["request"]["contents"]) == 1 for record in records)
    assert all(record["request"]["config"]["automatic_function_calling"]["disable"] for record in records)
    assert settings.gemini_api_key not in "".join(path.read_text() for path in options.output.glob("*.json"))


async def test_b_config_matches_production_except_explicit_automatic_tool_disable(source, settings, monkeypatch):
    class Captured(Exception):
        pass

    generate = AsyncMock(side_effect=Captured)
    client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate), aclose=AsyncMock()), close=Mock()
    )
    monkeypatch.setattr(production.genai, "Client", Mock(return_value=client))
    provider = production.GeminiInvestigationModel(
        api_key=settings.gemini_api_key,
        model=settings.gemini_generation_model,
        timeout_seconds=settings.generation_timeout_seconds,
        max_output_tokens=settings.investigation_max_output_tokens,
    )
    try:
        with pytest.raises(Captured):
            await provider.next_action(InvestigationTurn(question="Question?", exchanges=()))
    finally:
        await provider.aclose()
    actual = diagnostic.make_request("B", diagnostic.load_evidence(source), settings)["config"].model_dump(mode="json")
    expected = generate.await_args.kwargs["config"].model_dump(mode="json")
    actual.pop("automatic_function_calling")
    expected.pop("automatic_function_calling")
    assert actual == expected


@pytest.mark.parametrize(
    "cases,trials,cap,execute",
    [
        (["B"], 1, None, True),
        (["B", "C"], 2, 3, True),
        (["B"], 1, 0, False),
        (["B", "B"], 1, 2, True),
        (["A"], 0, 1, False),
    ],
)
async def test_invalid_plans_fail_before_creating_client_or_output(
    tmp_path, source, settings, cases, trials, cap, execute
):
    options = args(tmp_path, source)
    options.cases, options.trials, options.max_generation_requests, options.execute = cases, trials, cap, execute
    factory = Mock()
    with pytest.raises(ValueError):
        await diagnostic.run(options, settings, client_factory=factory)
    factory.assert_not_called()
    assert not options.output.exists()


async def test_selected_repeated_trials_one_request_each_fresh_clients_full_parts_and_manual_review(
    tmp_path, source, settings, monkeypatch
):
    options = args(tmp_path, source)
    options.cases, options.trials, options.execute, options.max_generation_requests = ["B"], 2, True, 2
    tool_response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(text="Synthetic thought fixture", thought=True),
                        types.Part(
                            function_call=types.FunctionCall(
                                name="search_evidence", args={"query": "Hylton Jolliffe father"}, id="native-call"
                            ),
                            thought_signature=b"synthetic-signature",
                        ),
                    ],
                ),
                finish_reason=types.FinishReason.STOP,
            )
        ],
        sdk_http_response=types.HttpResponse(
            headers={"Authorization": "Bearer test-key-sensitive", "x-request-id": "keep-id"}
        ),
    )
    responses, clients, client_kwargs = iter([tool_response, final_response()]), [], []

    def factory(**kwargs):
        client_kwargs.append(kwargs)
        client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(generate_content=AsyncMock(return_value=next(responses))), aclose=AsyncMock()
            ),
            close=Mock(),
        )
        clients.append(client)
        return client

    sleep = AsyncMock()
    monkeypatch.setattr(diagnostic.asyncio, "sleep", sleep)
    await diagnostic.run(options, settings, client_factory=factory)
    assert len(clients) == 2 and clients[0] is not clients[1]
    assert all(client.aio.models.generate_content.await_count == 1 for client in clients)
    assert all(kwargs["http_options"].retry_options.attempts == 1 for kwargs in client_kwargs)
    first, second = (diagnostic.read_json(options.output / f"call-B-{i:03}.json") for i in (1, 2))
    assert first["response"]["returned_function_calls"][0]["id"] == "native-call"
    parts = first["response"]["sdk_response_representation"]["candidates"][0]["content"]["parts"]
    assert parts[0]["thought"] and parts[1]["thought_signature"]
    assert (
        first["response"]["sdk_response_representation"]["sdk_http_response"]["headers"]["Authorization"]
        == "[REDACTED]"
    )
    assert first["response"]["automatic_observations"]["requested_another_search"]
    assert (
        second["response"]["automatic_observations"]["reference_agreement_only"]["answer_focused"]["exact_match"] == 1
    )
    assert second["response"]["usage_metadata"]["prompt_token_count"] == 123
    result = diagnostic.read_json(options.output / "comparison.json")
    assert result["attempted_generation_requests"] == 2
    assert result["calls"][1]["manual_review"]["judgments"]["returned_correct_answer"] is None
    reviews = diagnostic.read_json(options.output / "review-template.json")
    reviews[1]["judgments"].update(
        returned_correct_answer="yes", produced_unsupported_claims="no", used_citations_correctly="yes"
    )
    reviews[1]["reviewer"] = "Test reviewer"
    review_path = tmp_path / "reviews.json"
    diagnostic.write_json(review_path, reviews)
    result, _ = diagnostic.comparison(options.output, review_path)
    assert result["calls"][1]["manual_review"]["judgments"]["returned_correct_answer"] == "yes"
    assert result["calls"][1]["returned_correct_answer"] == "yes"
    reviews[1]["record_fingerprint"] = "stale"
    diagnostic.write_json(review_path, reviews)
    with pytest.raises(ValueError, match="stale"):
        diagnostic.comparison(options.output, review_path)


async def test_real_sdk_offline_429_is_not_retried_or_followed_by_more_calls(tmp_path, source, settings):
    options = args(tmp_path, source)
    options.execute, options.max_generation_requests = True, 4
    requests = []

    async def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            429,
            json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "sensitive-sentinel"}},
            request=request,
        )

    def factory(**kwargs):
        kwargs["http_options"].httpx_async_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        return genai.Client(**kwargs)

    await diagnostic.run(options, settings, client_factory=factory)
    assert len(requests) == 1
    record = diagnostic.read_json(options.output / "call-A-001.json")
    assert record["generation_requests"] == 1 and record["error"]["http_status"] == 429
    assert "sensitive-sentinel" not in json.dumps(record)
    assert diagnostic.read_json(options.output / "comparison.json")["attempted_generation_requests"] == 1


def test_malformed_mixed_and_abstention_responses_remain_visible_and_unverified():
    response = final_response()
    response.candidates[0].content.parts.append(
        types.Part(function_call=types.FunctionCall(name="search_evidence", args={"query": "again"}))
    )
    record = diagnostic.inspect_response(response, "Question?", "Reference", ["E1"])
    assert record["production_parse_error"] and record["returned_function_calls"] and record["final_text"]
    assert record["automatic_observations"]["reference_agreement_only"]["answer_focused"]["exact_match"] is None
    response.candidates[0].content.parts = None
    assert diagnostic.inspect_response(response, "Q?", "Ref", ["E1"])["production_parse_error"]


def test_registered_citations_do_not_hide_invalid_inline_ids_or_assert_correctness():
    record = diagnostic.inspect_response(
        final_response("The answer is Reference. [E99]", ("E1",)), "Question?", "Reference", ["E1"]
    )
    observed = record["automatic_observations"]
    assert observed["citation_ids_registered_in_supplied_evidence"]
    assert not observed["citation_structure_valid"]
    assert observed["reference_agreement_only"]["manual_factual_judgment"] is None
    unknown = diagnostic.inspect_response(final_response(citations=("E2",)), "Q?", "Ref", ["E1"])
    assert not unknown["automatic_observations"]["citation_structure_valid"]


def test_sanitizer_preserves_token_counts_and_opaque_native_parts():
    value = {
        "api_key": "sensitive",
        "prompt_token_count": 12,
        "thought_signature": "opaque",
        "url": "https://x/?key=private&other=1",
        "text": "Bearer confidential and known-secret",
        "nested": {"Set-Cookie": "cookie"},
    }
    clean = diagnostic.sanitize(value, ("known-secret",))
    assert clean["prompt_token_count"] == 12 and clean["thought_signature"] == "opaque"
    assert "private" not in clean["url"] and "confidential" not in clean["text"] and "known-secret" not in clean["text"]
    assert clean["nested"]["Set-Cookie"] == "[REDACTED]"


def search_response(query="initial search", *, signed=True):
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(text="Offline thought fixture", thought=True),
                        types.Part(
                            function_call=types.FunctionCall(
                                name="search_evidence", args={"query": query}, id="original-native-id"
                            ),
                            thought_signature=b"\x00\xff-original-signature" if signed else None,
                        ),
                    ],
                ),
                finish_reason=types.FinishReason.STOP,
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=20),
    )


def scripted_clients(responses):
    iterator, clients, kwargs_seen = iter(responses), [], []

    def factory(**kwargs):
        item = next(iterator)  # A third request fails this test immediately.
        method = AsyncMock(side_effect=item) if isinstance(item, Exception) else AsyncMock(return_value=item)
        client = SimpleNamespace(
            aio=SimpleNamespace(models=SimpleNamespace(generate_content=method), aclose=AsyncMock()), close=Mock()
        )
        clients.append(client)
        kwargs_seen.append(kwargs)
        return client

    return factory, clients, kwargs_seen


def test_e_differs_from_b_only_by_tool_availability(source, settings):
    evidence = diagnostic.load_evidence(source)
    b = diagnostic.serialize_request(diagnostic.make_request("B", evidence, settings), settings)
    e = diagnostic.serialize_request(diagnostic.make_request("E", evidence, settings), settings)
    assert e["config"]["tools"] is None and e["config"]["tool_config"] is None
    assert e["config"]["system_instruction"] == production.SYSTEM_INSTRUCTION
    assert not json.loads(e["contents"][0]["parts"][0]["text"])["final_only"]
    assert diagnostic.FINAL_ONLY_SUFFIX not in e["config"]["system_instruction"]
    for value in (b, e):
        value["config"].pop("tools")
        value["config"].pop("tool_config")
    assert b == e


async def test_e_records_insufficient_evidence_without_inventing_a_reason(tmp_path, source, settings):
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests = ["E"], True, 1
    response = final_response()
    response.candidates[0].content.parts[0].text = json.dumps(
        {"status": "insufficient_evidence", "answer": None, "citations": [], "conflicts": []}
    )
    factory, clients, _ = scripted_clients([response])
    await diagnostic.run(options, settings, client_factory=factory)
    row = diagnostic.read_json(options.output / "comparison.json")["calls"][0]
    assert len(clients) == 1 and row["returned_insufficient_evidence"]
    assert row["final_status"] == "insufficient_evidence" and row["returned_correct_answer"] is None
    assert "unchanged v5" in row["case_limitation"]


async def test_six_case_preview_reserves_seven_requests_without_fabricating_f_history(tmp_path, source, settings):
    options = args(tmp_path, source)
    options.cases = list("ABCDEF")
    factory = Mock(side_effect=AssertionError("Must remain provider free"))
    await diagnostic.run(options, settings, client_factory=factory)
    plan = diagnostic.read_json(options.output / "plan.json")
    assert plan["planned_generation_requests"] == 7 and plan["maximum_requests_per_case_trial"]["F"] == 2
    first = diagnostic.read_json(options.output / "call-F-001-step1.json")
    second = diagnostic.read_json(options.output / "call-F-001-step2.json")
    assert json.loads(first["request"]["contents"][0]["parts"][0]["text"]) == {"question": plan["question"]}
    assert second["request"] is None and second["status"] == "conditional_preview"
    assert "original call ID/signature" in second["request_template"]["deferred"]
    factory.assert_not_called()


@pytest.mark.parametrize("selected,trials,cap", [(["F"], 1, 1), (["E", "F"], 1, 2), (["F"], 2, 3)])
async def test_budget_reserves_f_worst_case_before_creating_a_client(tmp_path, source, settings, selected, trials, cap):
    options = args(tmp_path, source)
    options.cases, options.trials, options.execute, options.max_generation_requests = selected, trials, True, cap
    factory = Mock()
    with pytest.raises(ValueError, match="cap"):
        await diagnostic.run(options, settings, client_factory=factory)
    factory.assert_not_called()
    assert not options.output.exists()


@pytest.mark.parametrize("query", ["initial search", "Hedworth Jolliffe parents"])
async def test_f_preserves_original_native_parts_and_matches_ids_with_honest_replay(
    tmp_path, source, settings, monkeypatch, query
):
    from investigation_engine.application.use_cases.search_evidence import SearchEvidence

    forbidden_search = AsyncMock(side_effect=AssertionError("No retrieval may execute"))
    monkeypatch.setattr(SearchEvidence, "execute", forbidden_search)
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests = ["F"], True, 2
    initial = search_response(query)
    original = initial.candidates[0].content.model_dump(mode="json", exclude_none=False)
    factory, clients, _ = scripted_clients([initial, final_response()])
    await diagnostic.run(options, settings, client_factory=factory)
    assert len(clients) == 2 and all(client.aio.models.generate_content.await_count == 1 for client in clients)
    first, second = (client.aio.models.generate_content.await_args.kwargs for client in clients)
    assert first["config"].model_dump(mode="json") == second["config"].model_dump(mode="json")
    assert [item.role for item in second["contents"]] == ["user", "model", "user"]
    assert second["contents"][0] == first["contents"][0]
    assert second["contents"][1].model_dump(mode="json", exclude_none=False) == original
    assert initial.candidates[0].content.model_dump(mode="json", exclude_none=False) == original
    assert second["contents"][1].parts[1].thought_signature == b"\x00\xff-original-signature"
    response = second["contents"][2].parts[0].function_response
    assert response.id == initial.candidates[0].content.parts[1].function_call.id == "original-native-id"
    assert response.name == "search_evidence"
    assert [item["evidence_id"] for item in response.response["results"]] == ["E1", "E2", "E3", "E4", "E5"]
    assert all("excerpt" in item for item in response.response["results"])
    replay = response.response["replay"]
    assert replay["requested_query"] == query and replay["saved_result_query"] == "initial search"
    assert replay["query_matches_saved_exactly"] == (query == "initial search")
    assert not replay["live_retrieval_performed"]
    record = diagnostic.read_json(options.output / "call-F-001-step2.json")
    assert record["replay"] == replay and record["function_response"]["id"] == response.id
    assert record["response"]["usage_metadata"]["prompt_token_count"] == 123
    assert record["response"]["automatic_observations"]["citation_structure_valid"]
    assert diagnostic.read_json(options.output / "comparison.json")["attempted_generation_requests"] == 2
    forbidden_search.assert_not_called()


async def test_f_stops_after_second_search_request(tmp_path, source, settings):
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests = ["F"], True, 10
    factory, clients, _ = scripted_clients([search_response(), search_response("Hedworth Jolliffe parents")])
    await diagnostic.run(options, settings, client_factory=factory)
    assert len(clients) == 2
    row = diagnostic.read_json(options.output / "comparison.json")["calls"][-1]
    assert row["requested_another_search"] and row["observed_queries"] == ["Hedworth Jolliffe parents"]
    assert row["stop_reason"] == "second_request_finished_no_third_request_or_search_execution"


async def test_mixed_e_f_budget_counts_all_three_generation_requests(tmp_path, source, settings):
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests = ["E", "F"], True, 3
    factory, clients, _ = scripted_clients([final_response(), search_response(), final_response()])
    await diagnostic.run(options, settings, client_factory=factory)
    result = diagnostic.read_json(options.output / "comparison.json")
    assert len(clients) == result["attempted_generation_requests"] == result["planned_call_count"] == 3
    assert all(row["status"] == "response_received" for row in result["calls"])


async def test_repeated_f_trials_start_fresh_and_count_four_requests(tmp_path, source, settings):
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests, options.trials = ["F"], True, 4, 2
    factory, clients, _ = scripted_clients([search_response(), final_response(), search_response(), final_response()])
    await diagnostic.run(options, settings, client_factory=factory)
    assert len(clients) == 4
    first, second_initial = (clients[index].aio.models.generate_content.await_args.kwargs for index in (0, 2))
    assert len(first["contents"]) == len(second_initial["contents"]) == 1
    assert first["contents"] == second_initial["contents"]
    result = diagnostic.read_json(options.output / "comparison.json")
    assert result["attempted_generation_requests"] == 4
    assert [row["call_id"] for row in result["calls"]] == ["F-001-step1", "F-001-step2", "F-002-step1", "F-002-step2"]


async def test_f_initial_final_answer_stops_at_one_call(tmp_path, source, settings):
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests = ["F"], True, 2
    factory, clients, _ = scripted_clients([final_response()])
    await diagnostic.run(options, settings, client_factory=factory)
    assert len(clients) == 1
    result = diagnostic.read_json(options.output / "comparison.json")
    assert result["attempted_generation_requests"] == 1
    assert result["calls"][1]["stop_reason"] == "initial_final_decision"
    # No evidence has been returned at this initial turn, regardless of matching names.
    assert not result["calls"][0]["citation_structure_valid"]


@pytest.mark.parametrize("invalid", ["unsigned", "unknown_tool", "multiple_calls", "bad_args", "bad_role"])
async def test_f_does_not_invent_supported_native_history(tmp_path, source, settings, invalid):
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests = ["F"], True, 2
    initial = search_response(signed=invalid != "unsigned")
    if invalid == "unknown_tool":
        initial.candidates[0].content.parts[1].function_call.name = "other_tool"
    elif invalid == "multiple_calls":
        initial.candidates[0].content.parts.append(initial.candidates[0].content.parts[1].model_copy(deep=True))
    elif invalid == "bad_args":
        initial.candidates[0].content.parts[1].function_call.args = {"wrong": "query"}
    elif invalid == "bad_role":
        initial.candidates[0].content.role = "user"
    factory, clients, _ = scripted_clients([initial])
    await diagnostic.run(options, settings, client_factory=factory)
    assert len(clients) == 1
    second = diagnostic.read_json(options.output / "call-F-001-step2.json")
    assert second["request"] is None and second["status"] == "not_attempted"
    assert second["stop_reason"] in {"unsupported_initial_response", "invalid_or_unsigned_native_model_content"}


@pytest.mark.parametrize("failure_step", [1, 2])
async def test_f_provider_errors_retain_partial_records(tmp_path, source, settings, failure_step):
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests = ["F"], True, 2
    error = errors.APIError(503, {"error": {"message": "sensitive-sentinel", "status": "UNAVAILABLE"}})
    factory, clients, _ = scripted_clients([error] if failure_step == 1 else [search_response(), error])
    result_code = await diagnostic.run(options, settings, client_factory=factory)
    assert result_code == 1 and len(clients) == failure_step
    failed = diagnostic.read_json(options.output / f"call-F-001-step{failure_step}.json")
    assert failed["generation_requests"] == 1 and failed["error"]["http_status"] == 503
    assert "sensitive-sentinel" not in json.dumps(failed)
    if failure_step == 2:
        first = diagnostic.read_json(options.output / "call-F-001-step1.json")
        assert first["response"]["sdk_response_representation"]["candidates"][0]["content"]["parts"][1][
            "thought_signature"
        ]
        assert failed["request"]["contents"][2]["parts"][0]["function_response"]["id"] == "original-native-id"
    assert diagnostic.read_json(options.output / "comparison.json")["attempted_generation_requests"] == failure_step


@pytest.mark.parametrize("case", ["F", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S"])
async def test_native_sdk_manual_function_history_over_offline_http_transport(tmp_path, source, settings, case):
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests = [case], True, 2
    native = {
        "role": "model",
        "parts": [
            {"text": "Offline thought fixture", "thought": True},
            {
                "functionCall": {
                    "name": "search_evidence",
                    "args": {"query": "different query"},
                    "id": "sdk-native-id",
                },
                "thoughtSignature": "c2lnbmF0dXJl",
            },
        ],
    }
    if case == "K":
        native["parts"][1]["functionCall"]["args"].update(
            missing_fact="Information required by the original question.", evidence_ids_considered=[]
        )
    replies = [
        {"candidates": [{"content": native, "finishReason": "STOP"}]},
        {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {
                                "text": json.dumps(
                                    {
                                        "status": "answered",
                                        "answer": "Hedworth Jolliffe, 2nd Baron Hylton",
                                        "citations": ["E1", "E2"],
                                        "conflicts": [],
                                    }
                                )
                            }
                        ],
                    },
                    "finishReason": "STOP",
                }
            ]
        },
    ]
    if case in {"N", "O"}:
        replies[1]["candidates"][0]["content"]["parts"] = [
            {
                "functionCall": {
                    "name": "submit_final_answer",
                    "id": "native-terminal-id",
                    "args": {
                        "status": "answered",
                        "answer": "Hedworth Jolliffe, 2nd Baron Hylton",
                        "citations": ["E1", "E2"],
                        "conflicts": [],
                    },
                },
                "thoughtSignature": "c2lnbmF0dXJl",
            }
        ]
    requests = []

    async def handle(request):
        assert request.url.path.endswith(":generateContent")
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=replies.pop(0), request=request)

    def factory(**kwargs):
        kwargs["http_options"].httpx_async_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        return genai.Client(**kwargs)

    await diagnostic.run(options, settings, client_factory=factory)
    assert len(requests) == 2
    assert requests[1]["contents"][0] == requests[0]["contents"][0]
    assert requests[1]["contents"][1] == native
    response = requests[1]["contents"][2]["parts"][0]["functionResponse"]
    assert response["id"] == "sdk-native-id" and not response["response"]["replay"]["query_matches_saved_exactly"]
    if case in {"N", "O"}:
        assert "responseJsonSchema" not in requests[1].get("generationConfig", {})
        assert "responseMimeType" not in requests[1].get("generationConfig", {})
        calling = requests[1]["toolConfig"]["functionCallingConfig"]
        assert calling["mode"] == "ANY" and set(calling["allowedFunctionNames"]) == {
            "search_evidence",
            "submit_final_answer",
        }
    if case in {"P", "Q"}:
        first_generation = requests[0]["generationConfig"].copy()
        assert first_generation.pop("responseMimeType") == "application/json"
        first_generation.pop("responseJsonSchema")
        assert requests[1]["generationConfig"] == first_generation
        assert requests[1]["toolConfig"] == requests[0]["toolConfig"]
        assert requests[1]["tools"] == requests[0]["tools"]
        assert requests[1]["systemInstruction"] == requests[0]["systemInstruction"]
    if case in {"R", "S"}:
        first_generation = requests[0]["generationConfig"]
        second_generation = requests[1]["generationConfig"].copy()
        assert "thinkingConfig" not in first_generation
        # The installed SDK copies ThinkingConfig using its nested field names.
        assert second_generation.pop("thinkingConfig") == {"thinking_level": "MEDIUM"}
        assert second_generation == first_generation
        assert requests[1]["tools"] == requests[0]["tools"]
        assert requests[1]["toolConfig"] == requests[0]["toolConfig"]
        assert requests[1]["systemInstruction"] == requests[0]["systemInstruction"]


def json_decision(action="search", **updates):
    value = {
        "action": action,
        "answer": None,
        "citations": [],
        "missing_fact": "The father of Hylton Jolliffe, 3rd Baron Hylton.",
        "search_query": '"Hylton Jolliffe, 3rd Baron Hylton" father',
    }
    if action == "answer":
        value.update(
            answer="Hedworth Jolliffe, 2nd Baron Hylton", citations=["E1", "E2"], missing_fact=None, search_query=None
        )
    elif action == "insufficient_evidence":
        value.update(
            missing_fact="The required identity cannot be investigated in the available corpus.", search_query=None
        )
    elif action == "conflicting_evidence":
        value.update(
            missing_fact="The same scoped claim is X [E1] and Y [E2].", citations=["E1", "E2"], search_query=None
        )
    value.update(updates)
    return value


def json_response(value):
    response = final_response()
    response.candidates[0].content.parts[0].text = json.dumps(value)
    return response


def test_g_h_differ_only_in_evidence_and_do_not_leak_the_reference_or_missing_link(source, settings):
    evidence = diagnostic.load_evidence(source)
    g, h = (diagnostic.serialize_request(diagnostic.make_request(case, evidence, settings), settings) for case in "GH")
    assert g["config"] == h["config"]
    assert g["config"]["system_instruction"] == diagnostic.STRUCTURED_SYSTEM_INSTRUCTION
    assert g["config"]["response_json_schema"] == diagnostic.DIAGNOSTIC_DECISION_SCHEMA
    assert g["config"]["tools"] is None and g["config"]["tool_config"] is None
    assert g["config"]["automatic_function_calling"]["disable"]
    for phrase in (
        "search_evidence",
        "final_only",
        "returned by the tool",
        evidence["reference_answer"],
        "father of Hylton Jolliffe",
    ):
        assert phrase not in json.dumps(g)
    g_message, h_message = (json.loads(request["contents"][0]["parts"][0]["text"]) for request in (g, h))
    g_search, h_search = g_message["saved_search_result"], h_message["saved_search_result"]
    assert [item["evidence_id"] for item in g_search["results"]] == ["E1"]
    assert [item["evidence_id"] for item in h_search["results"]] == ["E1", "E2", "E3", "E4", "E5"]
    assert g_search["results"][0] == h_search["results"][0]
    for request, message in ((g, g_message), (h, h_message)):
        for field in ("results", "new_evidence_ids"):
            message["saved_search_result"].pop(field)
        request["contents"][0]["parts"][0]["text"] = diagnostic.json_text(message)
    assert g == h
    assert "JSON outputs only" in diagnostic.STRUCTURED_SYSTEM_INSTRUCTION


@pytest.mark.parametrize("action", ["answer", "search", "insufficient_evidence", "conflicting_evidence"])
def test_diagnostic_schema_supports_all_actions_and_action_contracts(action):
    assert set(diagnostic.DIAGNOSTIC_DECISION_SCHEMA["properties"]) == {
        "action",
        "answer",
        "citations",
        "missing_fact",
        "search_query",
    }
    assert action in diagnostic.DIAGNOSTIC_DECISION_SCHEMA["properties"]["action"]["enum"]
    assert diagnostic.diagnostic_payload_errors(json_decision(action)) == []


@pytest.mark.parametrize(
    "value",
    [
        json_decision("search", answer="A guessed grandfather"),
        json_decision("search", missing_fact=None),
        json_decision("search", search_query=None),
        json_decision("search", search_query=" " * 4),
        json_decision("search", search_query="x" * 501),
        json_decision("answer", search_query="unnecessary search"),
        json_decision("answer", missing_fact="missing link"),
        json_decision("answer", answer=None),
        json_decision("answer", citations=[]),
        json_decision("answer", citations=["E1", "E1"]),
        json_decision("answer", citations=["invalid"]),
        json_decision("answer", citations=[{}]),
        json_decision("insufficient_evidence", search_query="investigable gap"),
        json_decision("insufficient_evidence", missing_fact=None),
        json_decision("conflicting_evidence", citations=["E1"]),
        json_decision("conflicting_evidence", answer="silently pick one claim"),
        json_decision("unknown"),
        {**json_decision("search"), "action": ["search"]},
        {"action": "search"},
        {**json_decision(), "extra": True},
        [json_decision()],
    ],
)
def test_invalid_structured_action_payloads_are_preserved_but_not_scored_as_factual_errors(value):
    response = diagnostic.inspect_response(json_response(value), "Question?", "Reference", ["E1", "E2"], case="G")
    assert response["diagnostic_reported_decision"] == value
    assert not response["diagnostic_payload_valid"] and response["diagnostic_validation_errors"]
    assert response["diagnostic_parsed_decision"] is None
    assert response["automatic_observations"]["expected_action_matches"] is None
    assert response["automatic_observations"]["reference_agreement_only"]["manual_factual_judgment"] is None


@pytest.mark.parametrize("case,action", [("G", "search"), ("H", "answer")])
async def test_g_h_make_one_call_log_tokens_and_never_execute_a_proposed_search(
    tmp_path, source, settings, monkeypatch, case, action
):
    from investigation_engine.application.use_cases.search_evidence import SearchEvidence

    forbidden = AsyncMock(side_effect=AssertionError("No real retrieval is permitted"))
    monkeypatch.setattr(SearchEvidence, "execute", forbidden)
    followup = Mock(side_effect=AssertionError("G/H cannot request a second generation"))
    monkeypatch.setattr(diagnostic, "native_followup", followup)
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests = [case], True, 1
    factory, clients, kwargs = scripted_clients([json_response(json_decision(action))])
    await diagnostic.run(options, settings, client_factory=factory)
    assert len(clients) == 1 and clients[0].aio.models.generate_content.await_count == 1
    assert kwargs[0]["http_options"].retry_options.attempts == 1
    request = clients[0].aio.models.generate_content.await_args.kwargs
    assert request["config"].tools is None and request["config"].tool_config is None
    result = diagnostic.read_json(options.output / "comparison.json")
    row = result["calls"][0]
    assert result["attempted_generation_requests"] == 1
    assert row["structured_action"] == action and row["expected_action_matches"]
    assert row["prompt_tokens"] == 123 and row["output_tokens"] == 12 and row["duration_ms"] >= 0
    assert row["citation_ids"] == ([] if case == "G" else ["E1", "E2"])
    assert row["proposed_search_query"] == (json_decision()["search_query"] if case == "G" else None)
    assert row["missing_fact"] == (json_decision()["missing_fact"] if case == "G" else None)
    assert row["matches_expected_evidence_sufficiency_behavior"] is None
    assert not row["requested_another_search"]  # No native call; JSON proposal is separate.
    forbidden.assert_not_called()
    followup.assert_not_called()


@pytest.mark.parametrize("selected,trials,cap", [(["G", "H"], 1, 1), (["G"], 2, 1)])
async def test_g_h_request_caps_are_checked_before_any_client(tmp_path, source, settings, selected, trials, cap):
    options = args(tmp_path, source)
    options.cases, options.trials, options.execute, options.max_generation_requests = selected, trials, True, cap
    factory = Mock()
    with pytest.raises(ValueError, match="cap"):
        await diagnostic.run(options, settings, client_factory=factory)
    factory.assert_not_called()


def test_action_agreement_does_not_establish_correctness_or_expected_relationship():
    wrong = json_response(json_decision("answer", answer="An Incorrect Person"))
    assessment = diagnostic.inspect_response(wrong, "Question?", "Reference", ["E1", "E2"], case="H")
    observed = assessment["automatic_observations"]
    assert observed["expected_action_matches"]
    assert observed["reference_agreement_only"]["answer_focused"]["exact_match"] == 0
    assert observed["reference_agreement_only"]["manual_factual_judgment"] is None
    search = diagnostic.inspect_response(
        json_response(
            json_decision("search", missing_fact="Unrelated birthplace", search_query="unrelated birthplace")
        ),
        "Question?",
        "Reference",
        ["E1"],
        case="G",
    )
    assert search["automatic_observations"]["expected_action_matches"]
    assert search["automatic_observations"]["reference_agreement_only"]["manual_factual_judgment"] is None


def test_unexpected_native_calls_and_duplicate_json_are_invalid_without_repair():
    native = diagnostic.inspect_response(search_response(), "Q?", "Ref", ["E1"], case="G")
    assert native["returned_function_calls"] and not native["diagnostic_payload_valid"]
    assert native["sdk_response_representation"]["candidates"][0]["content"]["parts"][1]["thought_signature"]
    response = json_response(json_decision())
    response.candidates[0].content.parts[0].text = '{"action":"search","action":"answer"}'
    result = diagnostic.inspect_response(response, "Q?", "Ref", ["E1"], case="G")
    assert "Invalid JSON or duplicate object keys" in result["diagnostic_validation_errors"]


def gap_response(query="initial search", missing_fact="Information required by the original question.", ids=None):
    response = search_response(query)
    response.candidates[0].content.parts[1].function_call.args.update(
        missing_fact=missing_fact, evidence_ids_considered=ids or []
    )
    return response


def test_i_j_share_production_configuration_and_differ_only_in_full_saved_evidence(source, settings):
    evidence = diagnostic.load_evidence(source)
    i, j, f = (diagnostic.make_request(case, evidence, settings) for case in "IJF")
    for request in (i, j):
        assert request["config"].model_dump(mode="json") == f["config"].model_dump(mode="json")
        assert request["config"].system_instruction == production.SYSTEM_INSTRUCTION
        assert request["config"].tools[0].function_declarations[0].name == production.TOOL_NAME
        assert request["contents"] == f["contents"]
    initial = search_response()
    before = initial.model_dump(mode="json")
    second_i, _ = diagnostic.native_followup(initial, i, evidence, case="I")
    second_j, _ = diagnostic.native_followup(initial, j, evidence, case="J")
    assert initial.model_dump(mode="json") == before
    left, right = (diagnostic.serialize_request(request, settings) for request in (second_i, second_j))
    assert left["contents"][:2] == right["contents"][:2]
    assert (
        second_i["contents"][1].parts[1].thought_signature == initial.candidates[0].content.parts[1].thought_signature
    )
    for request, ids in ((left, ["E1", "E2"]), (right, ["E1"])):
        response = request["contents"][2]["parts"][0]["function_response"]
        assert response["id"] == "original-native-id"
        body = response["response"]
        assert [item["evidence_id"] for item in body["results"]] == ids
        assert [item["excerpt"] for item in body["results"]] == [
            ref.evidence.text for ref in evidence["refs"][: len(ids)]
        ]
        assert body["replay"]["controlled_subset"] and not body["replay"]["live_retrieval_performed"]
        body.pop("results")
        body.pop("new_evidence_ids")
    assert left == right
    assert evidence["reference_answer"] not in json.dumps(diagnostic.serialize_request(second_j, settings))
    assert "father of Hylton Jolliffe" not in json.dumps(diagnostic.serialize_request(second_j, settings))


def test_k_changes_only_the_tool_argument_schema_and_instruction_addendum(source, settings):
    evidence = diagnostic.load_evidence(source)
    original = json.dumps(production.TOOL_DECLARATION, sort_keys=True)
    i, k = (diagnostic.serialize_request(diagnostic.make_request(case, evidence, settings), settings) for case in "IK")
    declaration = k["config"]["tools"][0]["function_declarations"][0]
    schema = declaration["parameters_json_schema"]
    assert set(schema["required"]) == {"query", "missing_fact", "evidence_ids_considered"}
    assert not schema["additionalProperties"]
    assert k["config"]["system_instruction"].startswith(production.SYSTEM_INSTRUCTION)
    assert "observable claims" in k["config"]["system_instruction"]
    assert k["config"]["tool_config"] == i["config"]["tool_config"]
    for request in (i, k):
        request["config"].pop("system_instruction")
        request["config"]["tools"][0]["function_declarations"][0].pop("parameters_json_schema")
    assert i == k
    assert json.dumps(production.TOOL_DECLARATION, sort_keys=True) == original


@pytest.mark.parametrize("case", ["I", "J", "K", "L", "M"])
async def test_new_native_controls_preserve_protocol_log_subsets_and_stop_at_two(
    tmp_path, source, settings, monkeypatch, case
):
    from investigation_engine.application.use_cases.search_evidence import SearchEvidence

    forbidden = AsyncMock(side_effect=AssertionError("No retrieval is permitted"))
    monkeypatch.setattr(SearchEvidence, "execute", forbidden)
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests = [case], True, 10
    first = gap_response() if case == "K" else search_response()
    next_search = (
        gap_response("Hedworth Jolliffe parents", "The parents of the grandfather.", ["E1", "E2"])
        if case == "K"
        else search_response("Hylton Jolliffe father")
    )
    factory, clients, client_options = scripted_clients([first, next_search])
    await diagnostic.run(options, settings, client_factory=factory)
    assert len(clients) == 2 and all(client.aio.models.generate_content.await_count == 1 for client in clients)
    assert all(value["http_options"].retry_options.attempts == 1 for value in client_options)
    second = clients[1].aio.models.generate_content.await_args.kwargs
    assert [content.role for content in second["contents"]] == ["user", "model", "user"]
    assert second["contents"][1] == first.candidates[0].content
    assert second["contents"][2].parts[0].function_response.id == first.candidates[0].content.parts[1].function_call.id
    assert (
        second["config"].tools
        and second["config"].tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.VALIDATED
    )
    result = diagnostic.read_json(options.output / "comparison.json")
    assert result["attempted_generation_requests"] == 2
    row = result["calls"][-1]
    assert row["requested_another_search"] and row["protocol_errors"] == [] and row["parsing_error"] is None
    assert [item["evidence_id"] for item in row["supplied_evidence"]] == (
        ["E1"] if case in {"J", "M"} else ["E1", "E2"]
    )
    assert all(item["source_id"] and item["source"] for item in row["supplied_evidence"])
    assert row["prompt_tokens"] == 100 and row["output_tokens"] == 20 and row["duration_ms"] >= 0
    if case == "K":
        assert row["search_gap_claims"][0]["missing_fact"] == "The parents of the grandfather."
        assert row["considered_ids_registered"] == [True]
        for field in (
            "requested_unnecessary_search",
            "identified_genuinely_missing_fact",
            "invented_missing_relationship",
            "investigated_unrelated_relationship",
        ):
            assert row[field] is None
    forbidden.assert_not_called()


@pytest.mark.parametrize("case", ["I", "J", "K", "L", "M"])
async def test_new_native_controls_keep_the_final_answer_option(tmp_path, source, settings, case):
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests = [case], True, 2
    initial = gap_response() if case == "K" else search_response()
    factory, clients, _ = scripted_clients(
        [initial, final_response(citations=("E1",) if case in {"J", "M"} else ("E1", "E2"))]
    )
    await diagnostic.run(options, settings, client_factory=factory)
    assert len(clients) == 2
    result = diagnostic.read_json(options.output / "comparison.json")
    assert result["calls"][-1]["final_answer"] == "Hedworth Jolliffe, 2nd Baron Hylton"
    assert not result["calls"][-1]["requested_another_search"]
    assert result["calls"][-1]["returned_correct_answer"] is None  # Even a matching J guess is not verified.


@pytest.mark.parametrize("case", ["I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S"])
async def test_new_native_controls_reserve_two_requests_and_can_stop_after_one(tmp_path, source, settings, case):
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests = [case], True, 1
    factory = Mock()
    with pytest.raises(ValueError, match="cap"):
        await diagnostic.run(options, settings, client_factory=factory)
    factory.assert_not_called()
    options.max_generation_requests = 2
    factory, clients, _ = scripted_clients([final_response()])
    await diagnostic.run(options, settings, client_factory=factory)
    assert len(clients) == 1
    assert (
        diagnostic.read_json(options.output / f"call-{case}-001-step2.json")["stop_reason"] == "initial_final_decision"
    )


@pytest.mark.parametrize(
    "update",
    [
        {"missing_fact": None},
        {"missing_fact": " "},
        {"evidence_ids_considered": ["E1", "E1"]},
        {"evidence_ids_considered": "E1"},
        {"evidence_ids_considered": ["unknown"]},
    ],
)
def test_invalid_k_search_arguments_are_detected_without_rewriting_native_parts(update):
    response = gap_response()
    response.candidates[0].content.parts[1].function_call.args.update(update)
    original = response.model_dump(mode="json")
    record = diagnostic.inspect_response(response, "Q?", "Ref", [], case="K")
    assert record["diagnostic_parse_error"] and record["returned_function_calls"]
    assert response.model_dump(mode="json") == original


def test_k_does_not_invent_missing_required_search_arguments():
    response = search_response()
    record = diagnostic.inspect_response(response, "Q?", "Ref", [], case="K")
    assert record["diagnostic_parse_error"]
    assert set(record["returned_function_calls"][0]["args"]) == {"query"}


@pytest.mark.parametrize("case,baseline", [("L", "I"), ("M", "J")])
def test_l_m_only_add_goal_meaning_to_corresponding_native_control(source, settings, case, baseline):
    evidence = diagnostic.load_evidence(source)
    control = diagnostic.make_request(baseline, evidence, settings)
    changed = diagnostic.make_request(case, evidence, settings)
    assert changed["config"].system_instruction == production.SYSTEM_INSTRUCTION + diagnostic.RELATIONSHIP_PATH_ADDENDUM
    assert diagnostic.FINAL_ONLY_SUFFIX not in changed["config"].system_instruction
    assert (
        changed["config"].tools[0].function_declarations[0].parameters_json_schema
        == production.TOOL_DECLARATION["parameters_json_schema"]
    )
    assert set(changed["config"].tools[0].function_declarations[0].parameters_json_schema["required"]) == {"query"}
    native = search_response()
    before = native.model_dump(mode="json")
    control_second, _ = diagnostic.native_followup(native, control, evidence, case=baseline)
    changed_second, _ = diagnostic.native_followup(native, changed, evidence, case=case)
    assert native.model_dump(mode="json") == before
    for original, modified in ((control, changed), (control_second, changed_second)):
        left, right = (diagnostic.serialize_request(request, settings) for request in (original, modified))
        left["config"].pop("system_instruction")
        right["config"].pop("system_instruction")
        assert left == right


def test_l_m_only_differ_in_evidence_without_model_facing_verdicts_or_expected_values(source, settings):
    evidence = diagnostic.load_evidence(source)
    addendum = diagnostic.RELATIONSHIP_PATH_ADDENDUM
    assert "exactly two father relationships" in addendum
    assert "subject -> father -> father" in addendum
    for hint in (
        evidence["reference_answer"],
        "Hedworth",
        "Hylton",
        "E1",
        "E2",
        '"Hylton Jolliffe, 3rd Baron Hylton" father',
    ):
        assert hint not in addendum
    initial = search_response()
    requests = [diagnostic.make_request(case, evidence, settings) for case in "LM"]
    assert requests[0]["contents"] == requests[1]["contents"]
    assert requests[0]["config"] == requests[1]["config"]
    second = [
        diagnostic.native_followup(initial, request, evidence, case=case)[0]
        for case, request in zip("LM", requests, strict=True)
    ]
    left, right = (diagnostic.serialize_request(request, settings) for request in second)
    for request, count in ((left, 2), (right, 1)):
        body = request["contents"][2]["parts"][0]["function_response"]["response"]
        assert [result["excerpt"] for result in body["results"]] == [
            ref.evidence.text for ref in evidence["refs"][:count]
        ]
        assert body["replay"]["controlled_subset"] and not body["replay"]["live_retrieval_performed"]
        body.pop("results")
        body.pop("new_evidence_ids")
    assert left == right
    assert evidence["reference_answer"] not in json.dumps(diagnostic.serialize_request(second[1], settings))


async def test_l_m_preview_is_provider_free_and_counts_four_generation_requests(tmp_path, source, settings):
    options = args(tmp_path, source)
    options.cases = ["L", "M"]
    factory = Mock(side_effect=AssertionError("Preview cannot construct a provider"))
    await diagnostic.run(options, settings, client_factory=factory)
    plan = diagnostic.read_json(options.output / "plan.json")
    comparison = diagnostic.read_json(options.output / "comparison.json")
    assert plan["planned_generation_requests"] == comparison["planned_call_count"] == 4
    assert plan["maximum_requests_per_case_trial"] == {"L": 2, "M": 2}
    assert comparison["attempted_generation_requests"] == 0
    assert all(comparison["calls"][index]["status"] == "conditional_preview" for index in (1, 3))
    factory.assert_not_called()


async def test_l_m_combined_cap_reserves_four_requests_before_provider_creation(tmp_path, source, settings):
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests = ["L", "M"], True, 3
    factory = Mock()
    with pytest.raises(ValueError, match="cap"):
        await diagnostic.run(options, settings, client_factory=factory)
    factory.assert_not_called()


def terminal_response(arguments=None):
    response = search_response()
    if arguments is None:
        arguments = {
            "status": "answered",
            "answer": "Hedworth Jolliffe, 2nd Baron Hylton",
            "citations": ["E1", "E2"],
            "conflicts": [],
        }
    response.candidates[0].content.parts[1].function_call = types.FunctionCall(
        name="submit_final_answer", id="native-terminal-id", args=arguments
    )
    return response


@pytest.mark.parametrize("case,control", [("N", "I"), ("O", "J")])
def test_n_o_initial_requests_match_i_j_and_followup_switches_only_interface(source, settings, case, control):
    evidence = diagnostic.load_evidence(source)
    initial = diagnostic.make_request(case, evidence, settings)
    baseline = diagnostic.make_request(control, evidence, settings)
    original_config = initial["config"].model_dump(mode="json")
    assert diagnostic.serialize_request(initial, settings) == diagnostic.serialize_request(baseline, settings)
    second, _ = diagnostic.native_followup(search_response(), initial, evidence, case=case)
    config = second["config"]
    assert config.system_instruction == diagnostic.DUAL_FUNCTION_INSTRUCTION
    assert config.system_instruction.startswith(production.SYSTEM_INSTRUCTION)
    assert diagnostic.RELATIONSHIP_PATH_ADDENDUM not in config.system_instruction
    assert config.response_json_schema is config.response_schema is config.response_mime_type is None
    functions = config.tools[0].function_declarations
    assert [item.name for item in functions] == ["search_evidence", "submit_final_answer"]
    assert functions[0].parameters_json_schema == production.TOOL_DECLARATION["parameters_json_schema"]
    assert functions[1].parameters_json_schema == production.FINAL_SCHEMA
    calling = config.tool_config.function_calling_config
    assert calling.mode == types.FunctionCallingConfigMode.ANY
    assert calling.allowed_function_names == ["search_evidence", "submit_final_answer"]
    assert config.automatic_function_calling.disable
    assert initial["config"].model_dump(mode="json") == original_config
    first_config, second_config = original_config.copy(), config.model_dump(mode="json")
    for field in (
        "system_instruction",
        "tools",
        "tool_config",
        "response_json_schema",
        "response_schema",
        "response_mime_type",
    ):
        first_config.pop(field)
        second_config.pop(field)
    assert first_config == second_config


def test_n_o_followups_differ_only_in_original_full_excerpts(source, settings):
    evidence = diagnostic.load_evidence(source)
    first = search_response()
    untouched = first.model_dump(mode="json")
    pair = [
        diagnostic.native_followup(first, diagnostic.make_request(case, evidence, settings), evidence, case=case)[0]
        for case in "NO"
    ]
    left, right = (diagnostic.serialize_request(request, settings) for request in pair)
    for request, count in ((left, 2), (right, 1)):
        assert request["contents"][1] == pair[0]["contents"][1].model_dump(mode="json", exclude_none=False)
        response = request["contents"][2]["parts"][0]["function_response"]
        assert response["id"] == "original-native-id"
        body = response["response"]
        assert [item["evidence_id"] for item in body["results"]] == ["E1", "E2"][:count]
        assert [item["excerpt"] for item in body["results"]] == [ref.evidence.text for ref in evidence["refs"][:count]]
        body.pop("results")
        body.pop("new_evidence_ids")
    assert left == right and first.model_dump(mode="json") == untouched
    assert evidence["reference_answer"] not in json.dumps(diagnostic.serialize_request(pair[1], settings))


@pytest.mark.parametrize(
    "arguments",
    [
        {"status": "answered", "answer": "Reference", "citations": ["E1", "E2"], "conflicts": []},
        {"status": "insufficient_evidence", "answer": None, "citations": [], "conflicts": []},
        {
            "status": "conflicting_evidence",
            "answer": None,
            "citations": [],
            "conflicts": [
                {"claim": "Claim one [E1]", "citations": ["E1"]},
                {"claim": "Claim two [E2]", "citations": ["E2"]},
            ],
        },
    ],
)
def test_native_final_function_preserves_production_final_decision_semantics(arguments):
    response = terminal_response(arguments)
    original = response.model_dump(mode="json")
    observed = diagnostic.inspect_response(response, "Q?", "Reference", ["E1", "E2"], case="N", step=2)
    assert observed["diagnostic_parsed_action"]["status"] == arguments["status"]
    assert observed["diagnostic_parse_error"] is None
    assert observed["automatic_observations"]["selected_native_function"] == "submit_final_answer"
    assert observed["automatic_observations"]["native_terminal_decision_valid"]
    assert observed["automatic_observations"]["requested_queries"] == []
    assert observed["automatic_observations"]["reference_agreement_only"]["manual_factual_judgment"] is None
    assert response.model_dump(mode="json") == original


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"status": "answered"},
        {"status": "unknown", "answer": "Reference", "citations": ["E1"], "conflicts": []},
        {"status": "answered", "answer": None, "citations": ["E1"], "conflicts": []},
        {"status": "answered", "answer": "Reference", "citations": [], "conflicts": []},
        {"status": "answered", "answer": "Reference", "citations": ["E1", "E1"], "conflicts": []},
        {"status": "answered", "answer": "Reference", "citations": ["invalid"], "conflicts": []},
        {"status": "answered", "answer": "Reference", "citations": "E1", "conflicts": []},
        {"status": "answered", "answer": "Reference", "citations": ["E1"], "conflicts": "invalid"},
        {"status": "insufficient_evidence", "answer": "Guess", "citations": [], "conflicts": []},
        {
            "status": "conflicting_evidence",
            "answer": None,
            "citations": [],
            "conflicts": [{"claim": "Claim", "citations": ["E1"]}],
        },
    ],
)
def test_malformed_native_final_arguments_are_retained_and_rejected(arguments):
    observed = diagnostic.inspect_response(
        terminal_response(arguments), "Q?", "Reference", ["E1", "E2"], case="N", step=2
    )
    assert observed["diagnostic_parse_error"] and observed["diagnostic_parsed_action"] is None
    assert observed["returned_function_calls"][0]["args"] == arguments
    assert not observed["automatic_observations"]["native_terminal_decision_valid"]


@pytest.mark.parametrize("answer,citations", [("Reference", ["E99"]), ("Reference [E99]", ["E1"])])
def test_unavailable_or_unlisted_final_citations_cannot_count_as_valid_finalization(answer, citations):
    response = terminal_response({"status": "answered", "answer": answer, "citations": citations, "conflicts": []})
    observed = diagnostic.inspect_response(response, "Q?", "Reference", ["E1", "E2"], case="N", step=2)[
        "automatic_observations"
    ]
    assert not observed["citation_structure_valid"] and not observed["native_terminal_decision_valid"]
    assert observed["native_terminal_validation_errors"]


def test_simultaneous_search_and_final_calls_are_ambiguous_not_successful_finalization():
    response = search_response("another search")
    response.candidates[0].content.parts.append(terminal_response().candidates[0].content.parts[1])
    result = diagnostic.inspect_response(response, "Q?", "Reference", ["E1", "E2"], case="N", step=2)
    observed = result["automatic_observations"]
    assert len(result["returned_function_calls"]) == 2
    assert observed["ambiguous_native_decision"] and observed["selected_native_function"] is None
    assert observed["native_final_arguments"] and observed["requested_queries"] == ["another search"]
    assert observed["structured_final_answer"] is None and not observed["native_terminal_decision_valid"]
    assert "ambiguous" in result["diagnostic_parse_error"]


def test_text_final_output_is_not_silently_converted_to_a_native_function_call():
    result = diagnostic.inspect_response(final_response(), "Q?", "Reference", ["E1", "E2"], case="N", step=2)
    assert result["final_text"] and result["returned_function_calls"] == [] and result["diagnostic_parse_error"]
    assert not result["automatic_observations"]["native_terminal_decision_valid"]


@pytest.mark.parametrize("case", ["N", "O"])
async def test_n_o_make_at_most_two_calls_and_never_execute_or_acknowledge_functions(
    tmp_path, source, settings, monkeypatch, case
):
    from investigation_engine.application.use_cases.search_evidence import SearchEvidence

    forbidden = AsyncMock(side_effect=AssertionError("Retrieval may not execute"))
    monkeypatch.setattr(SearchEvidence, "execute", forbidden)
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests = [case], True, 10
    expected = terminal_response() if case == "N" else search_response("Hylton Jolliffe father")
    factory, clients, _ = scripted_clients([search_response(), expected])
    await diagnostic.run(options, settings, client_factory=factory)
    assert len(clients) == 2 and all(client.aio.models.generate_content.await_count == 1 for client in clients)
    row = diagnostic.read_json(options.output / "comparison.json")["calls"][-1]
    assert row["selected_native_function"] == ("submit_final_answer" if case == "N" else "search_evidence")
    assert row["native_terminal_decision_valid"] == (case == "N")
    assert row["returned_correct_answer"] is None and row["protocol_errors"] == []
    assert row["prompt_tokens"] == 100 and row["output_tokens"] == 20 and row["duration_ms"] >= 0
    assert row["returned_function_calls"][0]["id"] == expected.candidates[0].content.parts[1].function_call.id
    assert len(clients[1].aio.models.generate_content.await_args.kwargs["contents"]) == 3
    forbidden.assert_not_called()


@pytest.mark.parametrize("selected,cap", [(["N"], 1), (["O"], 1), (["N", "O"], 3)])
async def test_n_o_worst_case_caps_fail_before_creating_any_client(tmp_path, source, settings, selected, cap):
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests = selected, True, cap
    factory = Mock()
    with pytest.raises(ValueError, match="cap"):
        await diagnostic.run(options, settings, client_factory=factory)
    factory.assert_not_called()


async def test_n_o_preview_has_second_stage_any_tools_without_fabricated_history(tmp_path, source, settings):
    options = args(tmp_path, source)
    options.cases = ["N", "O"]
    factory = Mock(side_effect=AssertionError("Preview must not initialize a client"))
    await diagnostic.run(options, settings, client_factory=factory)
    for case in "NO":
        second = diagnostic.read_json(options.output / f"call-{case}-001-step2.json")
        assert second["request"] is None and second["status"] == "conditional_preview"
        config = second["request_template"]["config"]
        assert config["tool_config"]["function_calling_config"]["mode"] == "ANY"
        assert config["response_mime_type"] is config["response_json_schema"] is config["response_schema"] is None
    assert diagnostic.read_json(options.output / "comparison.json")["attempted_generation_requests"] == 0
    factory.assert_not_called()


@pytest.mark.parametrize("case", ["N", "O"])
async def test_n_o_cannot_continue_after_an_unoffered_initial_final_function(tmp_path, source, settings, case):
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests = [case], True, 2
    factory, clients, _ = scripted_clients([terminal_response()])
    await diagnostic.run(options, settings, client_factory=factory)
    assert len(clients) == 1
    second = diagnostic.read_json(options.output / f"call-{case}-001-step2.json")
    assert second["request"] is None and second["stop_reason"] == "unsupported_initial_response"


def test_native_final_structure_does_not_establish_o_factual_support():
    response = terminal_response(
        {"status": "answered", "answer": "Hedworth Jolliffe, 2nd Baron Hylton", "citations": ["E1"], "conflicts": []}
    )
    result = diagnostic.inspect_response(
        response, "Question?", "Hedworth Jolliffe, 2nd Baron Hylton", ["E1"], case="O", step=2
    )
    observed = result["automatic_observations"]
    assert observed["native_terminal_decision_valid"]  # Only argument/ID validity; the second link is unsupported.
    assert observed["reference_agreement_only"]["answer_focused"]["exact_match"] == 1
    assert observed["reference_agreement_only"]["manual_factual_judgment"] is None


@pytest.mark.parametrize("case,control", [("P", "I"), ("Q", "J")])
def test_p_q_only_unset_the_two_second_request_generation_fields(source, settings, case, control):
    evidence = diagnostic.load_evidence(source)
    baseline = diagnostic.make_request(control, evidence, settings)
    request = diagnostic.make_request(case, evidence, settings)
    first = diagnostic.serialize_request(request, settings)
    assert first == diagnostic.serialize_request(baseline, settings)
    assert first["model"] == "gemini-3.5-flash-lite"
    initial = search_response()
    original = initial.model_dump(mode="json")
    before, _ = diagnostic.native_followup(initial, baseline, evidence, case=control)
    after, _ = diagnostic.native_followup(initial, request, evidence, case=case)
    left, right = (diagnostic.serialize_request(item, settings) for item in (before, after))
    assert right["config"]["response_mime_type"] is right["config"]["response_json_schema"] is None
    assert after["config"].system_instruction == production.SYSTEM_INSTRUCTION
    assert after["config"].tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.VALIDATED
    assert [item.name for item in after["config"].tools[0].function_declarations] == ["search_evidence"]
    assert after["config"].automatic_function_calling.disable
    for item in (left, right):
        item["config"].pop("response_mime_type")
        item["config"].pop("response_json_schema")
    assert left == right and initial.model_dump(mode="json") == original
    assert diagnostic.serialize_request(request, settings) == first  # First config is not mutated.


def test_p_q_only_differ_in_supplied_full_evidence(source, settings):
    evidence = diagnostic.load_evidence(source)
    initial = search_response()
    requests = [
        diagnostic.native_followup(initial, diagnostic.make_request(case, evidence, settings), evidence, case=case)[0]
        for case in "PQ"
    ]
    left, right = (diagnostic.serialize_request(item, settings) for item in requests)
    for item, count in ((left, 2), (right, 1)):
        response = item["contents"][2]["parts"][0]["function_response"]
        assert response["id"] == "original-native-id"
        assert [hit["excerpt"] for hit in response["response"]["results"]] == [
            ref.evidence.text for ref in evidence["refs"][:count]
        ]
        response["response"].pop("results")
        response["response"].pop("new_evidence_ids")
    assert left == right


def ordinary_response(text):
    response = final_response()
    response.candidates[0].content.parts[0].text = text
    return response


def test_p_plain_text_and_bracketed_citations_are_not_protocol_failures():
    question = "Who is William Jolliffe, 4th Baron Hylton's paternal grandfather?"
    text = (
        "William Jolliffe, 4th Baron Hylton's paternal grandfather was Hedworth Jolliffe, 2nd Baron Hylton. [E1] [E2]"
    )
    response = ordinary_response(text)
    original = response.model_dump(mode="json")
    result = diagnostic.inspect_response(
        response, question, "Hedworth Jolliffe, 2nd Baron Hylton", ["E1", "E2"], case="P", step=2
    )
    assert result["final_text"] == text and result["production_parse_error"] is None
    assert result["response_kind"] == "unstructured_text" and result["protocol_errors"] == []
    assert not result["structured_final_recognized"] and result["structured_final_parse_error"]
    observed = result["automatic_observations"]
    assert observed["citation_ids"] == ["E1", "E2"] and observed["citation_structure_valid"]
    assert observed["reference_agreement_only"]["answer_focused"]["exact_match"] == 1
    assert observed["reference_agreement_only"]["manual_factual_judgment"] is None
    assert response.model_dump(mode="json") == original


@pytest.mark.parametrize("style", ["json", "fenced_json", "split_json"])
def test_p_q_attempt_existing_final_json_without_rewriting_original_parts(style):
    response = final_response()
    text = response.candidates[0].content.parts[0].text
    if style == "fenced_json":
        response.candidates[0].content.parts[0].text = f"```json\n{text}\n```"
    elif style == "split_json":
        response.candidates[0].content.parts = [types.Part(text=text[:25]), types.Part(text=text[25:])]
    original = response.model_dump(mode="json")
    result = diagnostic.inspect_response(
        response, "Question?", "Hedworth Jolliffe, 2nd Baron Hylton", ["E1", "E2"], case="P", step=2
    )
    assert result["response_kind"] == "structured_final_json" and result["structured_final_recognized"]
    assert result["production_parse_error"] is result["structured_final_parse_error"] is None
    assert result["protocol_errors"] == []
    assert result["automatic_observations"]["structured_final_answer"] == "Hedworth Jolliffe, 2nd Baron Hylton"
    assert response.model_dump(mode="json") == original


@pytest.mark.parametrize(
    "text",
    [
        "An uncertain ordinary answer.",
        '{"answer":"incomplete JSON object","citations":["E1"]}',
        "The answer is Reference. [E1, E99]",
    ],
)
def test_q_unstructured_or_nonconforming_text_is_retained_without_factual_or_protocol_verdict(text):
    result = diagnostic.inspect_response(ordinary_response(text), "Question?", "Reference", ["E1"], case="Q", step=2)
    assert result["final_text"] == text and result["protocol_errors"] == []
    assert result["production_parse_error"] is None and result["response_kind"] == "unstructured_text"
    assert result["automatic_observations"]["reference_agreement_only"]["manual_factual_judgment"] is None
    if "E99" in text:
        assert result["automatic_observations"]["citation_ids"] == ["E1", "E99"]
        assert not result["automatic_observations"]["citation_structure_valid"]


@pytest.mark.parametrize("with_text", [False, True])
def test_q_native_search_parts_are_captured_even_with_accompanying_prose(with_text):
    response = search_response("Hylton Jolliffe father")
    if with_text:
        response.candidates[0].content.parts.append(types.Part(text="A further source is needed."))
    original = response.model_dump(mode="json")
    result = diagnostic.inspect_response(response, "Question?", "Reference", ["E1"], case="Q", step=2)
    assert result["response_kind"] == "native_search" and result["protocol_errors"] == []
    assert result["automatic_observations"]["requested_another_search"]
    assert result["returned_function_calls"][0]["args"] == {"query": "Hylton Jolliffe father"}
    assert response.model_dump(mode="json") == original


def test_p_q_keep_multiple_native_calls_and_incomplete_text_visible():
    response = search_response()
    response.candidates[0].content.parts.append(search_response("second").candidates[0].content.parts[1])
    result = diagnostic.inspect_response(response, "Q?", "Ref", ["E1"], case="P", step=2)
    assert len(result["returned_function_calls"]) == 2 and result["ambiguous_native_output"]
    assert result["protocol_errors"]
    text_response = ordinary_response("Partial text remains visible.")
    text_response.candidates[0].finish_reason = types.FinishReason.MAX_TOKENS
    result = diagnostic.inspect_response(text_response, "Q?", "Ref", ["E1"], case="P", step=2)
    assert result["final_text"] == "Partial text remains visible." and result["protocol_errors"]


@pytest.mark.parametrize("case", ["P", "Q"])
async def test_p_q_run_with_model_override_two_requests_no_execution_and_comparison_outputs(
    tmp_path, source, settings, monkeypatch, case
):
    from investigation_engine.application.use_cases.search_evidence import SearchEvidence

    forbidden = AsyncMock(side_effect=AssertionError("No actual search may run"))
    monkeypatch.setattr(SearchEvidence, "execute", forbidden)
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests = [case], True, 10
    options.model = "gemini-3.5-flash-lite"
    settings.gemini_generation_model = "configured-model-not-overwritten"
    terminal = (
        ordinary_response("Hedworth Jolliffe, 2nd Baron Hylton. [E1] [E2]")
        if case == "P"
        else search_response("Hylton Jolliffe father")
    )
    factory, clients, _ = scripted_clients([search_response(), terminal])
    code = await diagnostic.run(options, settings, client_factory=factory)
    assert code == 0 and len(clients) == 2
    assert all(client.aio.models.generate_content.await_count == 1 for client in clients)
    assert all(client.aio.models.generate_content.await_args.kwargs["model"] == options.model for client in clients)
    assert settings.gemini_generation_model == "configured-model-not-overwritten"
    result = diagnostic.read_json(options.output / "comparison.json")
    assert result["attempted_generation_requests"] == 2
    row = result["calls"][-1]
    assert row["protocol_errors"] == [] and row["parsing_error"] is None
    assert row["returned_correct_answer"] is None
    if case == "P":
        assert row["final_answer"] == terminal.candidates[0].content.parts[0].text
        assert row["observed_final_text"] == row["final_answer"]
    else:
        assert row["requested_another_search"] and row["observed_queries"] == ["Hylton Jolliffe father"]
    forbidden.assert_not_called()


@pytest.mark.parametrize("selected,cap", [(["P"], 1), (["Q"], 1), (["P", "Q"], 3)])
async def test_p_q_request_caps_are_reserved_before_clients(tmp_path, source, settings, selected, cap):
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests = selected, True, cap
    factory = Mock()
    with pytest.raises(ValueError, match="cap"):
        await diagnostic.run(options, settings, client_factory=factory)
    factory.assert_not_called()


async def test_explicit_model_preview_does_not_need_a_configured_model_or_client(tmp_path, source, settings):
    options = args(tmp_path, source)
    options.cases, options.model = ["P", "Q"], "gemini-3.5-flash-lite"
    settings.gemini_generation_model = None
    factory = Mock(side_effect=AssertionError("Provider-free preview"))
    await diagnostic.run(options, settings, client_factory=factory)
    plan = diagnostic.read_json(options.output / "plan.json")
    assert plan["generation_model"] == options.model and plan["planned_generation_requests"] == 4
    assert diagnostic.read_json(options.output / "call-P-001-step1.json")["request"]["model"] == options.model
    assert diagnostic.read_json(options.output / "call-P-001-step2.json")["request_template"]["model"] == options.model
    factory.assert_not_called()


@pytest.mark.parametrize("case,control", [("R", "I"), ("S", "J")])
def test_r_s_only_change_second_request_thinking_configuration(source, settings, case, control):
    evidence = diagnostic.load_evidence(source)
    baseline = diagnostic.make_request(control, evidence, settings)
    request = diagnostic.make_request(case, evidence, settings)
    first = diagnostic.serialize_request(request, settings)
    assert first == diagnostic.serialize_request(baseline, settings)
    assert request["config"].thinking_config is None
    fixture = search_response()
    original = fixture.model_dump(mode="json")
    original_second, _ = diagnostic.native_followup(fixture, baseline, evidence, case=control)
    changed_second, _ = diagnostic.native_followup(fixture, request, evidence, case=case)
    config = changed_second["config"]
    assert config.thinking_config == types.ThinkingConfig(thinking_level=types.ThinkingLevel.MEDIUM)
    assert config.thinking_config.model_dump(mode="json", exclude_none=True) == {"thinking_level": "MEDIUM"}
    assert config.response_json_schema == production.FINAL_SCHEMA and config.response_mime_type == "application/json"
    assert config.system_instruction == production.SYSTEM_INSTRUCTION
    assert config.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.VALIDATED
    assert (
        config.tools[0].function_declarations[0].parameters_json_schema
        == production.TOOL_DECLARATION["parameters_json_schema"]
    )
    assert config.automatic_function_calling.disable
    left, right = (diagnostic.serialize_request(item, settings) for item in (original_second, changed_second))
    assert "explicit thinking settings" in right["unset_settings"]
    for item in (left, right):
        item["config"].pop("thinking_config")
        item.pop("unset_settings")  # Accurate log explanation, outside the SDK kwargs.
    assert left == right
    assert fixture.model_dump(mode="json") == original
    assert diagnostic.serialize_request(request, settings) == first


def test_r_s_only_differ_in_original_full_evidence(source, settings):
    evidence = diagnostic.load_evidence(source)
    fixture = search_response()
    requests = [
        diagnostic.native_followup(fixture, diagnostic.make_request(case, evidence, settings), evidence, case=case)[0]
        for case in "RS"
    ]
    left, right = (diagnostic.serialize_request(item, settings) for item in requests)
    for request, count in ((left, 2), (right, 1)):
        body = request["contents"][2]["parts"][0]["function_response"]
        assert body["id"] == fixture.candidates[0].content.parts[1].function_call.id
        assert [hit["evidence_id"] for hit in body["response"]["results"]] == ["E1", "E2"][:count]
        assert [hit["excerpt"] for hit in body["response"]["results"]] == [
            ref.evidence.text for ref in evidence["refs"][:count]
        ]
        assert body["response"]["replay"]["controlled_subset"]
        body["response"].pop("results")
        body["response"].pop("new_evidence_ids")
    assert left == right


@pytest.mark.parametrize("thinking_tokens", [None, 0, 57])
@pytest.mark.parametrize("case", ["R", "S"])
async def test_r_s_log_reported_thinking_without_reasoning_inference(
    tmp_path, source, settings, monkeypatch, case, thinking_tokens
):
    from investigation_engine.application.use_cases.search_evidence import SearchEvidence

    forbidden = AsyncMock(side_effect=AssertionError("No actual retrieval may run"))
    monkeypatch.setattr(SearchEvidence, "execute", forbidden)
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests = [case], True, 10
    options.model = "gemini-3.5-flash-lite"
    terminal = final_response() if case == "R" else search_response("Hylton Jolliffe father")
    terminal.usage_metadata.thoughts_token_count = thinking_tokens
    terminal.model_version = "provider-reported-model-version"
    factory, clients, _ = scripted_clients([search_response(), terminal])
    await diagnostic.run(options, settings, client_factory=factory)
    assert len(clients) == 2 and all(client.aio.models.generate_content.await_count == 1 for client in clients)
    assert clients[0].aio.models.generate_content.await_args.kwargs["config"].thinking_config is None
    assert (
        clients[1].aio.models.generate_content.await_args.kwargs["config"].thinking_config.thinking_level
        == types.ThinkingLevel.MEDIUM
    )
    result = diagnostic.read_json(options.output / "comparison.json")
    first, second = result["calls"]
    assert result["attempted_generation_requests"] == 2
    assert first["requested_thinking_level"] is None
    assert second["requested_thinking_level"] == "MEDIUM" and second["generation_request_attempted"]
    assert second["requested_model"] == options.model and second["reported_model_version"] == terminal.model_version
    assert second["reported_thinking_tokens"] == thinking_tokens
    assert "zero is not proof of no reasoning" in second["thinking_usage_note"]
    assert second["selected_action"] == ("answered" if case == "R" else "search_evidence")
    assert second["prompt_tokens"] == (123 if case == "R" else 100)
    assert second["output_tokens"] == (12 if case == "R" else 20)
    assert second["duration_ms"] >= 0 and second["provider_error"] is None and second["parsing_error"] is None
    assert second["returned_correct_answer"] is None
    forbidden.assert_not_called()


async def test_r_s_preview_is_provider_free_and_marks_second_thinking_as_planned(tmp_path, source, settings):
    options = args(tmp_path, source)
    options.cases, options.model = ["R", "S"], "gemini-3.5-flash-lite"
    factory = Mock(side_effect=AssertionError("Preview cannot initialize a provider"))
    await diagnostic.run(options, settings, client_factory=factory)
    comparison = diagnostic.read_json(options.output / "comparison.json")
    assert comparison["planned_call_count"] == 4 and comparison["attempted_generation_requests"] == 0
    for index in (1, 3):
        row = comparison["calls"][index]
        assert row["requested_thinking_level"] is None and row["planned_thinking_level"] == "MEDIUM"
        assert not row["generation_request_attempted"] and row["reported_thinking_tokens"] is None
    factory.assert_not_called()


async def test_r_medium_provider_error_is_recorded_without_retry_or_fallback(tmp_path, source, settings):
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests = ["R"], True, 2
    error = errors.APIError(400, {"error": {"message": "sensitive-sentinel", "status": "INVALID_ARGUMENT"}})
    factory, clients, _ = scripted_clients([search_response(), error])
    assert await diagnostic.run(options, settings, client_factory=factory) == 1
    assert len(clients) == 2
    row = diagnostic.read_json(options.output / "comparison.json")["calls"][-1]
    assert row["requested_thinking_level"] == "MEDIUM" and row["provider_error"]["http_status"] == 400
    assert row["reported_thinking_tokens"] is None and row["reported_model_version"] is None
    assert "sensitive-sentinel" not in json.dumps(row)


async def test_r_s_combined_request_cap_reserves_four_before_clients(tmp_path, source, settings):
    options = args(tmp_path, source)
    options.cases, options.execute, options.max_generation_requests = ["R", "S"], True, 3
    factory = Mock()
    with pytest.raises(ValueError, match="cap"):
        await diagnostic.run(options, settings, client_factory=factory)
    factory.assert_not_called()
