import importlib.util
import json
import sys
from pathlib import Path

from google.genai import interactions

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/evaluate_gemini_interactions_sufficiency.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("gemini_interactions_sufficiency", SCRIPT)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)

interaction_history_with_result = evaluation.interaction_history_with_result
interaction_initial_call = evaluation.interaction_initial_call
interaction_request = evaluation.interaction_request
parse_interaction_decision = evaluation.parse_interaction_decision


def test_interactions_request_keeps_native_tool_and_disables_storage():
    request = interaction_request("gemini-3.1-flash-lite", [{"type": "user_input", "content": []}])

    assert request["store"] is False
    assert request["tools"][0]["name"] == "search_evidence"
    assert request["generation_config"]["tool_choice"]["allowed_tools"] == {
        "mode": "validated",
        "tools": ["search_evidence"],
    }
    assert request["response_format"]["mime_type"] == "application/json"


def test_initial_parser_preserves_function_call_step_and_signature():
    response = interactions.Interaction(
        status="completed",
        steps=[
            interactions.ThoughtStep(signature="dGVzdA=="),
            interactions.FunctionCallStep(
                type="function_call", id="call-1", name="search_evidence", arguments={"query": "Hylton"}
            ),
        ],
    )

    call, error = interaction_initial_call(response)

    assert error is None
    assert call.id == "call-1"
    assert call.arguments == {"query": "Hylton"}
    assert response.steps[0].signature == "dGVzdA=="


def test_function_result_history_appends_original_model_steps_and_pinned_evidence():
    thought = interactions.ThoughtStep(signature="dGVzdA==")
    call = interactions.FunctionCallStep(
        type="function_call", id="call-1", name="search_evidence", arguments={"query": "death date"}
    )
    history = interaction_history_with_result(
        "When did the person's father die?",
        [thought, call],
        call,
        {"evidence": []},
    )

    assert history[1]["type"] == "thought"
    assert history[1]["signature"] == "dGVzdA=="
    assert history[2]["type"] == "function_call"
    assert history[2]["id"] == "call-1"
    assert history[3]["type"] == "function_result"
    assert history[3]["call_id"] == "call-1"
    assert json.loads(history[3]["result"][0]["text"])["query"] == "death date"


def test_interactions_decision_parser_accepts_native_search_call():
    response = interactions.Interaction(
        status="completed",
        steps=[
            interactions.FunctionCallStep(
                type="function_call", id="call-2", name="search_evidence", arguments={"query": "father death"}
            )
        ],
    )

    parsed = parse_interaction_decision(response, {"E1"})

    assert parsed["action"] == "search"
    assert parsed["search_query"] == "father death"
    assert parsed["error"] is None


def test_interactions_decision_parser_checks_citation_ids():
    output = interactions.ModelOutputStep(
        content=[
            interactions.TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "answered",
                        "answer": "Hedworth Jolliffe",
                        "citations": ["E1", "E9"],
                        "conflicts": [],
                    }
                ),
            )
        ]
    )
    response = interactions.Interaction(status="completed", steps=[output])

    parsed = parse_interaction_decision(response, {"E1"})

    assert parsed["action"] == "answered"
    assert parsed["invalid_citation_ids"] == ["E9"]
