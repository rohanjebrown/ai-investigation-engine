"""Offline construction checks for the broader Gemini decision evaluation."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from google.genai import types

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/evaluate_gemini_search_sufficiency.py"
spec = importlib.util.spec_from_file_location("gemini_search_sufficiency", SCRIPT)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)
REVIEWED_SUITE = SCRIPT.parents[1] / "data/samples/evaluation/suite_sufficiency_reviewed.json"
INDEPENDENT_SUITE = SCRIPT.parents[1] / "data/samples/evaluation/suite_sufficiency_independent_31.json"


def test_pinned_suite_builds_40_distinct_question_records_with_paired_answer_conditions():
    cases = evaluation.load_cases()
    assert len(cases["cases"]) == 40
    assert len({case["question"] for case in cases["cases"]}) == 40
    assert {case["category"] for case in cases["cases"]} == {
        "simple_factual",
        "two_hop",
        "three_plus_hop",
        "contradictory",
        "unanswerable",
    }
    answers = [case for case in cases["cases"] if case["expected_behavior"] == "answer"]
    assert len(answers) == 28
    assert all(set(case["conditions"]) == {"complete", "incomplete"} for case in answers)
    assert sum(len(case["conditions"]) for case in cases["cases"]) == 68


def test_response_shape_summary_reports_visible_parts_but_never_thought_text():
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                finish_reason=types.FinishReason.STOP,
                content=SimpleNamespace(
                    role="model",
                    parts=[
                        SimpleNamespace(
                            function_call=SimpleNamespace(name="search_evidence", id="call-id"),
                            thought_signature="signature",
                            text=None,
                            thought=False,
                            inline_data=None,
                            file_data=None,
                        ),
                        SimpleNamespace(
                            function_call=None,
                            text="private reasoning that should not be logged",
                            thought=True,
                            inline_data=None,
                            file_data=None,
                        ),
                    ],
                ),
            )
        ]
    )

    summary = evaluation.summarize_response_shape(response)

    assert summary["candidate_count"] == 1
    assert summary["content_role"] == "model"
    assert summary["parts"][0]["function_name"] == "search_evidence"
    assert summary["parts"][0]["function_call_id_present"] is True
    assert summary["parts"][0]["thought_signature_present"] is True
    assert "visible_text_preview" not in summary["parts"][1]


def test_initial_default_tool_probe_retains_native_function_without_tool_mode_or_json_schema():
    request = evaluation.make_initial_request(
        "gemma-4-26b-a4b-it",
        "Where was Olav Aaraas born?",
        function_calling_mode=None,
        response_schema=False,
    )

    assert request["config"].tools[0].function_declarations[0].name == evaluation.TOOL_NAME
    assert request["config"].tool_config is None
    assert request["config"].response_mime_type is None
    assert request["config"].response_json_schema is None


def test_gemma_evaluation_config_uses_tools_without_gemini_calling_or_json_constraints():
    request = evaluation.make_initial_request(
        "gemma-4-26b-a4b-it",
        "Question?",
        function_calling_mode=None,
        response_schema=False,
    )
    config = request["config"]
    assert config.tools[0].function_declarations[0].name == "search_evidence"
    assert config.tool_config is None
    assert config.response_json_schema is None and config.response_mime_type is None
    assert config.automatic_function_calling is None
    assert "STATUS: answered" in config.system_instruction


def test_gemma_text_final_is_scored_and_citations_are_checked():
    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                finish_reason=types.FinishReason.STOP,
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="STATUS: answered\nANSWER: Hedworth Jolliffe. [E1]")],
                ),
            )
        ]
    )
    valid = evaluation.parse_decision(response, ["E1"], model="gemma-4-26b-a4b-it")
    invalid = evaluation.parse_decision(response, ["E2"], model="gemma-4-26b-a4b-it")
    assert valid["action"] == "answered" and valid["citations"] == ["E1"]
    assert valid["invalid_citation_ids"] == []
    assert invalid["action"] == "answered" and invalid["invalid_citation_ids"] == ["E1"]


def test_gemma_search_call_is_retained_as_a_native_decision():
    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                finish_reason=types.FinishReason.STOP,
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                name="search_evidence", args={"query": "missing relationship"}, id="native-call"
                            )
                        )
                    ],
                ),
            )
        ]
    )
    parsed = evaluation.parse_decision(response, [], model="gemma-4-26b-a4b-it")
    assert parsed["action"] == "search" and parsed["search_query"] == "missing relationship"


def test_reviewed_suite_has_30_plus_independent_answer_facts_with_supported_complete_evidence():
    cases = evaluation.load_cases(REVIEWED_SUITE, evaluation.CORPUS_PATH)
    answers = [case for case in cases["cases"] if case["expected_behavior"] == "answer"]
    target_ids = {case["independent_target_id"] for case in answers}

    assert len(cases["cases"]) == 54
    assert len(answers) == 38
    assert len(target_ids) == 31
    assert all(case["conditions"]["complete"]["expected_action"] == "final" for case in answers)
    assert all(case["conditions"]["incomplete"]["expected_action"] == "search" for case in answers)
    for case in answers:
        complete_text = "\n".join(item["text"] for item in case["conditions"]["complete"]["evidence"])
        assert case["expected_answer"].casefold() in complete_text.casefold()

    unsupported_ids = {
        "2wiki:464cfa460bd911eba7f7acde48001122",
        "simple:464cfa460bd911eba7f7acde48001122",
        "2wiki:6ebe05740bde11eba7f7acde48001122",
        "2wiki:2207e9060bda11eba7f7acde48001122",
    }
    reviewed = {case["id"]: case for case in cases["cases"]}
    assert all(reviewed[case_id]["expected_behavior"] == "abstain" for case_id in unsupported_ids)


def test_independent_suite_has_one_case_per_target_fact():
    cases = evaluation.load_cases(INDEPENDENT_SUITE, evaluation.CORPUS_PATH)["cases"]
    target_ids = [case["independent_target_id"] for case in cases]

    assert len(cases) == 31
    assert len(set(target_ids)) == 31
    assert all(case["expected_behavior"] == "answer" for case in cases)


def test_full_source_passage_view_changes_only_evidence_excerpt_length():
    case = evaluation.load_cases()["cases"][0]
    condition = case["conditions"]["complete"]
    minimal = evaluation.evidence_response(condition, "query", context_view="required_sentence")
    full = evaluation.evidence_response(condition, "query", context_view="full_source_passage")
    assert len(full["results"][0]["excerpt"]) >= len(minimal["results"][0]["excerpt"])
    assert minimal["results"][0]["evidence_id"] == full["results"][0]["evidence_id"] == "E1"
    assert minimal["query"] == full["query"] == "query"


def test_sentence_neighbor_context_keeps_supporting_span_and_caps_context_locally():
    full_text = "Prior context. Support relationship. Adjacent context. Distant unrelated sentence."
    support = "Support relationship."
    start = full_text.index(support)
    record = {
        "full_text": full_text,
        "text": support,
        "start": start,
        "end": start + len(support),
        "title": "Example source",
        "source": {"kind": "document"},
    }
    window = evaluation.sentence_context_window(record)
    assert window == "Prior context. Support relationship. Adjacent context."
    minimal = evaluation.evidence_response({"evidence": [record]}, "query", context_view="required_sentence")
    local = evaluation.evidence_response({"evidence": [record]}, "query", context_view="sentence_with_neighbors")
    full = evaluation.evidence_response({"evidence": [record]}, "query", context_view="full_source_passage")
    assert (
        minimal["results"][0]["evidence_id"] == local["results"][0]["evidence_id"] == full["results"][0]["evidence_id"]
    )
    assert local["results"][0]["excerpt"] == window


def test_candidate_preserves_production_prompt_schema_and_calling_mode():
    baseline = evaluation.build_config()
    candidate = evaluation.build_config(conditional=True)
    before = baseline.model_dump(mode="json", exclude_none=False)
    after = candidate.model_dump(mode="json", exclude_none=False)
    before["tools"][0]["function_declarations"][0]["description"] = "changed"
    after["tools"][0]["function_declarations"][0]["description"] = "changed"
    assert before == after
    assert candidate.system_instruction == baseline.system_instruction
    assert candidate.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.VALIDATED
    assert candidate.tool_config.function_calling_config.allowed_function_names == [evaluation.TOOL_NAME]
    assert candidate.response_json_schema == baseline.response_json_schema


def test_answer_first_candidate_changes_only_the_decision_instruction_opening():
    baseline = evaluation.build_config()
    candidate = evaluation.build_config(answer_first=True)

    assert candidate.system_instruction == evaluation.ANSWER_FIRST_SYSTEM_INSTRUCTION
    assert "Use search_evidence to retrieve facts" not in candidate.system_instruction
    assert candidate.system_instruction.endswith(
        baseline.system_instruction[len(evaluation._SEARCH_ORIENTED_OPENING) :]
    )
    assert candidate.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.VALIDATED
    assert candidate.tool_config.function_calling_config.allowed_function_names == [evaluation.TOOL_NAME]
    assert candidate.tools[0].function_declarations[0].name == evaluation.TOOL_NAME
    assert candidate.response_json_schema == baseline.response_json_schema
    assert evaluation.make_initial_request("gemini-3.1-flash-lite", "Who is Alice?")["config"].system_instruction == (
        evaluation.SYSTEM_INSTRUCTION
    )


def test_explicit_finalize_candidate_keeps_search_and_final_actions_selectable_as_native_functions():
    candidate = evaluation.build_config(explicit_finalize=True)
    declarations = candidate.tools[0].function_declarations

    assert [item.name for item in declarations] == [evaluation.TOOL_NAME, evaluation.FINALIZE_TOOL_NAME]
    assert candidate.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.ANY
    assert candidate.tool_config.function_calling_config.allowed_function_names == [
        evaluation.TOOL_NAME,
        evaluation.FINALIZE_TOOL_NAME,
    ]
    assert candidate.response_json_schema is None


def test_explicit_finalize_parser_accepts_a_cited_native_final_function():
    response = SimpleNamespace(
        usage_metadata=None,
        model_version="gemini-3.1-flash-lite",
        candidates=[
            SimpleNamespace(
                finish_reason=types.FinishReason.STOP,
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            function_call=SimpleNamespace(
                                name=evaluation.FINALIZE_TOOL_NAME,
                                args={
                                    "status": "answered",
                                    "answer": "Fredrikstad",
                                    "citations": ["E1"],
                                    "conflicts": [],
                                },
                            ),
                            text=None,
                            thought=False,
                        )
                    ]
                ),
            )
        ],
    )

    parsed = evaluation.parse_decision(response, ["E1"], explicit_finalize=True)

    assert parsed["action"] == "answered"
    assert parsed["answer"] == "Fredrikstad"
    assert parsed["citations"] == ["E1"]
    assert parsed["invalid_citation_ids"] == []


def test_explicit_finalize_parser_preserves_cited_conflicting_claims():
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                finish_reason=types.FinishReason.STOP,
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            function_call=SimpleNamespace(
                                name=evaluation.FINALIZE_TOOL_NAME,
                                args={
                                    "status": "conflicting_evidence",
                                    "answer": None,
                                    "citations": [],
                                    "conflicts": [
                                        {"claim": "Vault code is amber", "citations": ["E1"]},
                                        {"claim": "Vault code is cobalt", "citations": ["E2"]},
                                    ],
                                },
                            ),
                            text=None,
                            thought=False,
                        )
                    ]
                ),
            )
        ]
    )

    parsed = evaluation.parse_decision(response, ["E1", "E2"], explicit_finalize=True)

    assert parsed["action"] == "conflicting_evidence"
    assert parsed["conflicts"] == [
        {"claim": "Vault code is amber", "citations": ["E1"]},
        {"claim": "Vault code is cobalt", "citations": ["E2"]},
    ]
    assert parsed["invalid_citation_ids"] == []


def test_explicit_finalize_parser_keeps_search_action_available_for_empty_evidence():
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                finish_reason=types.FinishReason.STOP,
                content=SimpleNamespace(
                    parts=[
                        SimpleNamespace(
                            function_call=SimpleNamespace(
                                name=evaluation.TOOL_NAME,
                                args={"query": "construction date of fictional station Unlisted-0"},
                            ),
                            text=None,
                            thought=False,
                        )
                    ]
                ),
            )
        ]
    )

    parsed = evaluation.parse_decision(response, [], explicit_finalize=True)

    assert parsed["action"] == "search"
    assert parsed["search_query"] == "construction date of fictional station Unlisted-0"


def test_validated_explicit_finalize_arm_keeps_production_json_final_and_adds_both_native_functions():
    candidate = evaluation.build_config(explicit_finalize_validated=True)

    assert [item.name for item in candidate.tools[0].function_declarations] == [
        evaluation.TOOL_NAME,
        evaluation.FINALIZE_TOOL_NAME,
    ]
    assert candidate.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.VALIDATED
    assert candidate.tool_config.function_calling_config.allowed_function_names == [
        evaluation.TOOL_NAME,
        evaluation.FINALIZE_TOOL_NAME,
    ]
    assert candidate.response_json_schema == evaluation.FINAL_SCHEMA


def test_validated_explicit_finalize_parser_accepts_the_production_json_final_response():
    final_text = json.dumps({"status": "answered", "answer": "Fredrikstad", "citations": ["E1"], "conflicts": []})
    response = SimpleNamespace(
        usage_metadata=None,
        model_version="gemini-3.1-flash-lite",
        candidates=[
            SimpleNamespace(
                finish_reason=types.FinishReason.STOP,
                content=SimpleNamespace(parts=[SimpleNamespace(function_call=None, text=final_text, thought=False)]),
            )
        ],
    )

    parsed = evaluation.parse_decision(response, ["E1"], explicit_finalize_validated=True)

    assert parsed["action"] == "answered"
    assert parsed["answer"] == "Fredrikstad"
    assert parsed["citations"] == ["E1"]
    assert parsed["invalid_citation_ids"] == []


def test_auto_function_mode_still_exposes_native_search_without_forcing_it():
    candidate = evaluation.build_config(function_calling_mode=types.FunctionCallingConfigMode.AUTO)
    assert candidate.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.AUTO
    assert candidate.tool_config.function_calling_config.allowed_function_names is None
    assert candidate.tools[0].function_declarations[0].name == evaluation.TOOL_NAME
    assert candidate.response_json_schema == evaluation.FINAL_SCHEMA


def test_decision_temperature_candidate_changes_only_generation_temperature():
    baseline = evaluation.build_config()
    candidate = evaluation.build_config(temperature=0.0)
    before = baseline.model_dump(mode="json", exclude_none=False)
    after = candidate.model_dump(mode="json", exclude_none=False)
    assert before.pop("temperature") is None
    assert after.pop("temperature") == 0.0
    assert before == after
    assert candidate.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.VALIDATED
    assert candidate.tools[0].function_declarations[0].name == evaluation.TOOL_NAME


def test_evidence_coverage_request_is_tool_free_and_uses_only_question_and_passages():
    case = evaluation.load_cases(INDEPENDENT_SUITE, evaluation.CORPUS_PATH)["cases"][0]
    condition = case["conditions"]["complete"]
    request = evaluation.make_evidence_coverage_request(
        "gemini-3.1-flash-lite", case["question"], condition, "local search query"
    )
    config = request["config"]
    assert config.tools is None
    assert config.response_json_schema == evaluation.EVIDENCE_COVERAGE_SCHEMA
    assert config.max_output_tokens == 512
    payload = json.loads(request["contents"][0].parts[0].text)
    assert payload["question"] == case["question"]
    assert payload["evidence"]
    assert "expected_answer" not in payload
    assert all("evidence_id" in item and "excerpt" in item for item in payload["evidence"])


def test_coverage_audit_decision_keeps_native_search_available_and_adds_audit_as_data():
    model_content = types.Content(
        role="model",
        parts=[
            types.Part(
                function_call=types.FunctionCall(id="audit-call", name=evaluation.TOOL_NAME, args={"query": "source"})
            )
        ],
    )
    case = evaluation.load_cases(INDEPENDENT_SUITE, evaluation.CORPUS_PATH)["cases"][0]
    condition = case["conditions"]["complete"]
    audit = {"overall_status": "sufficient", "requirements": []}
    request = evaluation.make_decision_request(
        "gemini-3.1-flash-lite",
        "Who?",
        model_content,
        model_content.parts[0].function_call,
        condition,
        conditional=False,
        evidence_coverage_audit=audit,
    )
    parts = request["contents"][2].parts
    assert parts[0].function_response.response["results"][0]["excerpt"] == condition["evidence"][0]["text"]
    assert json.loads(parts[1].text) == {"evidence_coverage_audit": audit}
    assert request["config"].tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.VALIDATED
    assert request["config"].tools[0].function_declarations[0].name == evaluation.TOOL_NAME

    embedded_request = evaluation.make_decision_request(
        "gemini-3.1-flash-lite",
        "Who?",
        model_content,
        model_content.parts[0].function_call,
        condition,
        conditional=False,
        evidence_coverage_audit=audit,
        embed_coverage_audit=True,
    )
    embedded_parts = embedded_request["contents"][2].parts
    function_result = embedded_parts[0].function_response.response
    assert function_result["evidence_coverage_audit"] == audit
    assert len(embedded_parts) == 1
    assert embedded_request["config"].tool_config.function_calling_config.mode == (
        types.FunctionCallingConfigMode.VALIDATED
    )
    assert embedded_request["config"].tools[0].function_declarations[0].name == evaluation.TOOL_NAME


def test_coverage_audit_parse_and_post_hoc_quote_and_status_review():
    audit = {
        "requirements": [
            {
                "requirement": "Alice's profession",
                "status": "supported",
                "evidence_ids": ["E1"],
                "quotes": ["She was a stage and film actress."],
            }
        ],
        "overall_status": "sufficient",
    }
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                finish_reason=types.FinishReason.STOP,
                content=types.Content(
                    role="model",
                    parts=[types.Part.from_text(text=json.dumps(audit))],
                ),
            )
        ]
    )
    parsed = evaluation.parse_evidence_coverage_response(response)
    reviewed = evaluation.review_coverage_audit(
        parsed,
        {
            "expected_action": "final",
            "evidence": [{"text": "She was a stage and film actress."}],
        },
    )
    assert reviewed == {
        "overall_status": "sufficient",
        "correct_for_fixture": True,
        "invalid_evidence_ids": [],
        "unsupported_quotes": 0,
    }


def test_coverage_audit_plan_includes_the_extra_candidate_generations():
    case = evaluation.load_cases(INDEPENDENT_SUITE, evaluation.CORPUS_PATH)["cases"][0]
    args = SimpleNamespace(
        candidate_policy=evaluation.EVIDENCE_COVERAGE_POLICY,
        only_arm=None,
        start_question_index=0,
        compare_context_views=False,
        context_view="required_sentence",
        only_condition=None,
        decision_mode="VALIDATED",
        model="gemini-3.1-flash-lite",
        trial_label=1,
        pause_seconds=0,
        suite=INDEPENDENT_SUITE,
        corpus=evaluation.CORPUS_PATH,
    )
    plan = evaluation.make_plan(args, {"cases": [case], "corpus_id": "2wiki-dev-controlled-v1"}, mode="preview")
    assert plan["planned_generation_requests"] == 7
    assert plan["decision_arms"] == ["production", evaluation.EVIDENCE_COVERAGE_POLICY]
    assert plan["native_tool_mode"].startswith("search_evidence available on every request")

    args.candidate_policy = evaluation.EVIDENCE_COVERAGE_EMBEDDED_POLICY
    embedded_plan = evaluation.make_plan(
        args, {"cases": [case], "corpus_id": "2wiki-dev-controlled-v1"}, mode="preview"
    )
    assert embedded_plan["planned_generation_requests"] == 7
    assert embedded_plan["decision_arms"] == ["production", evaluation.EVIDENCE_COVERAGE_EMBEDDED_POLICY]


def test_coverage_audit_costs_are_included_in_candidate_summary():
    case = evaluation.load_cases(INDEPENDENT_SUITE, evaluation.CORPUS_PATH)["cases"][0]
    condition = case["conditions"]["complete"]
    metrics = {"duration_ms": 100.0, "usage": {"prompt_tokens": 10, "candidate_tokens": 5, "thinking_tokens": 0}}
    row = evaluation.make_row(
        case,
        "complete",
        evaluation.EVIDENCE_COVERAGE_POLICY,
        condition,
        {
            "action": "answered",
            "answer": case["expected_answer"],
            "citations": ["E1"],
            "conflicts": [],
            "invalid_citation_ids": [],
        },
        metrics,
        requested_query="query",
        initial_ok=True,
    )
    row["coverage_audit_generation"] = metrics
    row["coverage_audit_review"] = {
        "correct_for_fixture": True,
        "invalid_evidence_ids": [],
        "unsupported_quotes": 0,
    }
    summary = evaluation.summarize([row], [metrics], [], {"attempted": 3})
    candidate = summary["arms"][evaluation.EVIDENCE_COVERAGE_POLICY]
    assert candidate["generation_requests"] == 2
    assert candidate["generation_requests_per_investigation_including_shared_initial"] == 3
    assert candidate["mean_total_generation_latency_ms_per_investigation"] == 300.0
    assert candidate["prompt_tokens_including_shared_initial"] == 30
    assert candidate["candidate_tokens_including_shared_initial"] == 15
    assert summary["accounting"]["actual_prompt_tokens"] == 30
    assert summary["accounting"]["actual_candidate_tokens"] == 15


def test_identity_context_candidate_changes_only_system_guidance():
    baseline = evaluation.build_config()
    candidate = evaluation.build_config(identity_guidance=True)
    assert candidate.system_instruction.startswith(baseline.system_instruction)
    assert "shortened display title" in candidate.system_instruction
    assert candidate.tools == baseline.tools
    assert candidate.tool_config.function_calling_config == baseline.tool_config.function_calling_config
    assert candidate.response_json_schema == baseline.response_json_schema


def test_gap_commitment_candidate_adds_required_generic_fact_field_without_forcing_calls():
    baseline = evaluation.build_config()
    candidate = evaluation.build_config(gap_commitment=True)
    declaration = candidate.tools[0].function_declarations[0]
    assert candidate.system_instruction == baseline.system_instruction
    assert declaration.name == evaluation.TOOL_NAME
    assert set(declaration.parameters_json_schema["required"]) == {"query", "unresolved_fact"}
    assert candidate.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.VALIDATED
    assert candidate.tool_config.function_calling_config.allowed_function_names == [evaluation.TOOL_NAME]
    assert candidate.response_json_schema == baseline.response_json_schema
    assert declaration.parameters_json_schema["additionalProperties"] is False


def test_goal_commitment_candidate_adds_a_required_question_fact_checklist():
    baseline = evaluation.build_config()
    candidate = evaluation.build_config(goal_commitment=True)
    declaration = candidate.tools[0].function_declarations[0]
    assert declaration.name == evaluation.TOOL_NAME
    assert set(declaration.parameters_json_schema["required"]) == {"query", "required_facts"}
    assert declaration.parameters_json_schema["properties"]["required_facts"]["maxItems"] == 8
    assert candidate.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.VALIDATED
    assert candidate.tool_config.function_calling_config.allowed_function_names == [evaluation.TOOL_NAME]
    assert candidate.response_json_schema == baseline.response_json_schema
    assert candidate.system_instruction == baseline.system_instruction


def test_goal_commitment_plan_counts_separate_native_initial_histories():
    case = next(
        item for item in evaluation.load_cases()["cases"] if item["id"] == "2wiki:8813f87c0bdd11eba7f7acde48001122"
    )
    args = SimpleNamespace(
        candidate_policy=evaluation.GOAL_COMMITMENT_POLICY,
        only_arm=None,
        start_question_index=0,
        compare_context_views=False,
        context_view="full_source_passage",
        only_condition=None,
        decision_mode="VALIDATED",
        model="gemini-3.1-flash-lite",
        trial_label=1,
        pause_seconds=0,
    )
    plan = evaluation.make_plan(args, {"cases": [case], "corpus_id": "2wiki-dev-controlled-v1"}, mode="preview")
    assert plan["initial_native_search_calls_per_question"] == 2
    assert plan["planned_generation_requests"] == 6
    assert plan["automatic_function_execution"] is False
    assert plan["reference_answers_sent_to_model"] is False


def test_native_decision_request_replays_signed_model_content_and_keeps_search_available():
    model_content = types.Content(
        role="model",
        parts=[
            types.Part(
                function_call=types.FunctionCall(id="call-1", name=evaluation.TOOL_NAME, args={"query": "source query"})
            )
        ],
    )
    call = model_content.parts[0].function_call
    request = evaluation.make_decision_request(
        "gemini-3.5-flash-lite",
        "Who?",
        model_content,
        call,
        {"evidence": []},
        conditional=True,
    )
    assert [item.role for item in request["contents"]] == ["user", "model", "user"]
    replayed = request["contents"][1]
    assert replayed is not model_content
    response = request["contents"][2].parts[0].function_response
    assert response.id == "call-1"
    assert response.name == evaluation.TOOL_NAME
    assert response.response["results"] == []
    assert request["config"].tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.VALIDATED
    assert request["config"].tools[0].function_declarations[0].name == evaluation.TOOL_NAME

    deterministic_request = evaluation.make_decision_request(
        "gemini-3.1-flash-lite",
        "Who?",
        model_content,
        call,
        {"evidence": []},
        conditional=False,
        temperature=0.0,
    )
    assert deterministic_request["config"].temperature == 0.0
    assert deterministic_request["config"].tool_config.function_calling_config.mode == (
        types.FunctionCallingConfigMode.VALIDATED
    )
    assert deterministic_request["config"].tools[0].function_declarations[0].name == evaluation.TOOL_NAME

    gap_request = evaluation.make_decision_request(
        "gemini-3.5-flash-lite",
        "Who?",
        model_content,
        call,
        {"evidence": []},
        conditional=False,
        gap_commitment=True,
    )
    assert [item.role for item in gap_request["contents"]] == ["user", "model", "user"]
    gap_function = gap_request["config"].tools[0].function_declarations[0]
    assert gap_function.name == evaluation.TOOL_NAME
    assert "unresolved_fact" in gap_function.parameters_json_schema["required"]

    goal_content = types.Content(
        role="model",
        parts=[
            types.Part(
                function_call=types.FunctionCall(
                    id="call-goal",
                    name=evaluation.TOOL_NAME,
                    args={"query": "director film", "required_facts": ["director", "director's mother"]},
                )
            )
        ],
    )
    goal_request = evaluation.make_decision_request(
        "gemini-3.1-flash-lite",
        "Who is the director's mother?",
        goal_content,
        goal_content.parts[0].function_call,
        {"evidence": []},
        conditional=False,
        goal_commitment=True,
    )
    goal_function = goal_request["config"].tools[0].function_declarations[0]
    assert [item.role for item in goal_request["contents"]] == ["user", "model", "user"]
    assert goal_request["contents"][1].parts[0].function_call.args["required_facts"] == [
        "director",
        "director's mother",
    ]
    assert goal_function.parameters_json_schema["required"] == ["query", "required_facts"]
    assert goal_request["config"].tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.VALIDATED


def test_readable_evidence_candidate_duplicates_without_replacing_native_response():
    case = next(
        item for item in evaluation.load_cases()["cases"] if item["id"] == "simple:0cd3bdea0bde11eba7f7acde48001122"
    )
    initial_content = types.Content(
        role="model",
        parts=[
            types.Part(
                function_call=types.FunctionCall(
                    id="call-evidence", name=evaluation.TOOL_NAME, args={"query": "Wunna award"}
                ),
                thought_signature=b"signature",
            )
        ],
    )
    call = initial_content.parts[0].function_call
    condition = case["conditions"]["complete"]
    baseline = evaluation.make_decision_request(
        "gemini-3.1-flash-lite",
        case["question"],
        initial_content,
        call,
        condition,
        conditional=False,
        context_view="full_source_passage",
    )
    candidate = evaluation.make_decision_request(
        "gemini-3.1-flash-lite",
        case["question"],
        initial_content,
        call,
        condition,
        conditional=False,
        evidence_text_reinforcement=True,
        context_view="full_source_passage",
    )
    baseline_parts = baseline["contents"][2].parts
    candidate_parts = candidate["contents"][2].parts
    assert len(baseline_parts) == 1
    assert len(candidate_parts) == 2
    assert candidate_parts[0].function_response.id == baseline_parts[0].function_response.id == "call-evidence"
    assert candidate_parts[0].function_response.response == baseline_parts[0].function_response.response
    assert candidate_parts[1].text.startswith("Readable copy of the search tool's evidence records")
    assert "[E1] Wunna" in candidate_parts[1].text
    assert case["conditions"]["complete"]["evidence"][0]["full_text"] in candidate_parts[1].text
    declaration = candidate["config"].tools[0].function_declarations[0]
    assert declaration.name == evaluation.TOOL_NAME
    assert candidate["config"].tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.VALIDATED
    assert candidate["config"].tool_config.function_calling_config.allowed_function_names == [evaluation.TOOL_NAME]


def test_goal_commitment_parser_captures_checklist_on_native_search():
    content = types.Content(
        role="model",
        parts=[
            types.Part(
                function_call=types.FunctionCall(
                    id="call-goal",
                    name=evaluation.TOOL_NAME,
                    args={
                        "query": "Xawery Żuławski mother",
                        "required_facts": ["Identify Xawery Żuławski", "Name his mother"],
                    },
                )
            )
        ],
    )
    response = SimpleNamespace(
        candidates=[SimpleNamespace(finish_reason=types.FinishReason.STOP, content=content)],
        usage_metadata=None,
        model_version=None,
    )
    parsed = evaluation.parse_decision(response, ["E1"], goal_commitment=True)
    assert parsed["action"] == "search"
    assert parsed["search_query"] == "Xawery Żuławski mother"
    assert parsed["required_facts"] == ["Identify Xawery Żuławski", "Name his mother"]


def test_goal_commitment_initial_parser_requires_a_nonempty_signed_checklist():
    content = types.Content(
        role="model",
        parts=[
            types.Part(
                function_call=types.FunctionCall(
                    id="call-goal",
                    name=evaluation.TOOL_NAME,
                    args={"query": "Xawery mother", "required_facts": ["Name Xawery's mother"]},
                ),
                thought_signature=b"signature",
            )
        ],
    )
    response = SimpleNamespace(candidates=[SimpleNamespace(finish_reason=types.FinishReason.STOP, content=content)])
    native, error = evaluation.action_from_initial(response, model="gemini-3.1-flash-lite", goal_commitment=True)
    assert error is None
    assert native[1].args["required_facts"] == ["Name Xawery's mother"]


def test_empty_search_result_is_an_explicit_no_new_evidence_response():
    result = evaluation.evidence_response({"evidence": []}, "missing fact")
    assert result["query"] == "missing fact"
    assert result["no_new_evidence"] is True
    assert result["new_evidence_ids"] == []
    assert result["results"] == []


def test_final_decision_flags_unknown_citation_ids():
    content = types.Content(
        role="model",
        parts=[types.Part.from_text(text='{"status":"answered","answer":"Blue","citations":["E2"],"conflicts":[]}')],
    )
    response = SimpleNamespace(
        candidates=[SimpleNamespace(finish_reason=types.FinishReason.STOP, content=content)],
        usage_metadata=None,
        model_version=None,
    )
    parsed = evaluation.parse_decision(response, ["E1"])
    assert parsed["action"] == "answered"
    assert parsed["invalid_citation_ids"] == ["E2"]


def test_gap_commitment_parser_captures_native_query_and_unresolved_fact():
    content = types.Content(
        role="model",
        parts=[
            types.Part(
                function_call=types.FunctionCall(
                    id="call-gap",
                    name=evaluation.TOOL_NAME,
                    args={"query": "Ernest I date of death", "unresolved_fact": "Ernest I's date of death"},
                )
            )
        ],
    )
    response = SimpleNamespace(
        candidates=[SimpleNamespace(finish_reason=types.FinishReason.STOP, content=content)],
        usage_metadata=None,
        model_version=None,
    )
    parsed = evaluation.parse_decision(response, ["E1"], gap_commitment=True)
    assert parsed["action"] == "search"
    assert parsed["search_query"] == "Ernest I date of death"
    assert parsed["unresolved_fact"] == "Ernest I's date of death"


def test_gap_commitment_parser_rejects_search_without_an_unresolved_fact():
    content = types.Content(
        role="model",
        parts=[
            types.Part(
                function_call=types.FunctionCall(
                    id="call-gap", name=evaluation.TOOL_NAME, args={"query": "Ernest I date of death"}
                )
            )
        ],
    )
    response = SimpleNamespace(
        candidates=[SimpleNamespace(finish_reason=types.FinishReason.STOP, content=content)],
        usage_metadata=None,
        model_version=None,
    )
    parsed = evaluation.parse_decision(response, ["E1"], gap_commitment=True)
    assert parsed["action"] == "invalid"
    assert parsed["error"] == "function call lacks a specific unresolved fact"


def test_conflict_claim_citations_are_included_in_invalid_id_checks():
    raw = {
        "status": "conflicting_evidence",
        "answer": None,
        "citations": [],
        "conflicts": [
            {"claim": "The code is blue.", "citations": ["E1"]},
            {"claim": "The code is green.", "citations": ["E3"]},
        ],
    }
    content = types.Content(
        role="model",
        parts=[types.Part.from_text(text=json.dumps(raw))],
    )
    response = SimpleNamespace(
        candidates=[SimpleNamespace(finish_reason=types.FinishReason.STOP, content=content)],
        usage_metadata=None,
        model_version=None,
    )
    parsed = evaluation.parse_decision(response, ["E1", "E2"])
    assert parsed["action"] == "conflicting_evidence"
    assert parsed["invalid_citation_ids"] == ["E3"]


def test_answer_correctness_is_answer_focused_and_reference_stays_post_decision():
    case = {
        "id": "case-1",
        "question": "Who is William Jolliffe, 4th Baron Hylton's paternal grandfather?",
        "category": "simple_factual",
        "expected_answer": "Hedworth Jolliffe, 2nd Baron Hylton",
    }
    condition = {"expected_action": "final", "evidence": []}
    row = evaluation.make_row(
        case,
        "complete",
        "production",
        condition,
        {
            "action": "answered",
            "answer": "Hedworth Jolliffe, 2nd Baron Hylton.",
            "citations": ["E1"],
            "error": None,
        },
        {"duration_ms": 1.0, "usage": {"prompt_tokens": 4}},
        requested_query="Who is William Jolliffe, 4th Baron Hylton's paternal grandfather?",
        initial_ok=True,
    )
    assert row["reference_value_present_in_answer_text"] is True
    assert row["expected_answer"] == "Hedworth Jolliffe, 2nd Baron Hylton"


def test_reference_surface_match_accepts_a_different_written_date_order():
    assert evaluation.expected_value_appears("The date was June 12, 1516.", "12 June 1516") is True


def test_missing_target_query_check_records_entity_overlap_only_as_a_proxy():
    missing = [{"title": "Xawery Żuławski"}]
    assert evaluation.query_matches_missing_target("Xawery mother", missing) is True
    assert evaluation.query_matches_missing_target("film director award", missing) is False
    assert evaluation.query_matches_missing_target("Xawery mother", []) is None


def test_missing_fact_query_proxy_requires_both_entity_and_relation():
    case = next(
        item for item in evaluation.load_cases()["cases"] if item["id"] == "2wiki:e2a3bf2a0bdd11eba7f7acde48001122"
    )
    condition = case["conditions"]["incomplete"]
    targets = condition["missing_fact_targets"]
    assert any(item["relation"] == "date of death" for item in targets)
    assert (
        evaluation.query_matches_missing_fact(
            "Ernest I Prince of Anhalt Dessau death date", condition["missing_evidence"], targets
        )
        is True
    )
    assert (
        evaluation.query_matches_missing_fact(
            "Ernest I Prince of Anhalt Dessau birthplace", condition["missing_evidence"], targets
        )
        is False
    )


def test_missing_fact_query_proxy_accepts_generic_relation_paraphrases():
    cases = {case["id"]: case for case in evaluation.load_cases()["cases"]}
    mother_case = cases["2wiki:8813f87c0bdd11eba7f7acde48001122"]["conditions"]["incomplete"]
    assert (
        evaluation.query_matches_missing_fact(
            "Xawery Żuławski parents",
            mother_case["missing_evidence"],
            mother_case["missing_fact_targets"],
        )
        is True
    )
    citizenship_case = cases["2wiki:2207e9060bda11eba7f7acde48001122"]["conditions"]["incomplete"]
    assert (
        evaluation.query_matches_missing_fact(
            "Eric Mueller nationality",
            citizenship_case["missing_evidence"],
            citizenship_case["missing_fact_targets"],
        )
        is True
    )


def test_search_target_classification_separates_missing_facts_from_redundant_queries():
    case = next(
        item for item in evaluation.load_cases()["cases"] if item["id"] == "simple:0cd3bdea0bde11eba7f7acde48001122"
    )
    query = "Maung Wunna Myanmar Motion Picture Academy Awards"
    assert evaluation.classify_search_target(query, case, case["conditions"]["complete"], "search") == (
        "redundant_to_supported_fact"
    )
    assert (
        evaluation.classify_search_target("Eric Mueller nationality", case, case["conditions"]["complete"], "search")
        == "unrelated_to_supported_facts"
    )


def test_gap_claim_is_checked_against_missing_or_already_supported_annotations():
    case = next(
        item for item in evaluation.load_cases()["cases"] if item["id"] == "2wiki:e2a3bf2a0bdd11eba7f7acde48001122"
    )
    assert (
        evaluation.classify_unresolved_fact(case["conditions"]["incomplete"], "search", "Ernest I date of death")
        == "names_missing_fact"
    )
    assert (
        evaluation.classify_unresolved_fact(case["conditions"]["complete"], "search", "Ernest I date of death")
        == "claims_supported_fact_is_missing"
    )


def test_initial_search_requires_supported_signed_native_tool_call():
    content = types.Content(
        role="model",
        parts=[
            types.Part(
                function_call=types.FunctionCall(id="call-1", name=evaluation.TOOL_NAME, args={"query": "who"}),
                thought_signature=b"signature",
            )
        ],
    )
    response = SimpleNamespace(candidates=[SimpleNamespace(finish_reason=types.FinishReason.STOP, content=content)])
    native, error = evaluation.action_from_initial(response, model="gemini-3.5-flash-lite")
    assert error is None
    assert native[0].parts[0].thought_signature == b"signature"
    assert native[1].id == "call-1"
