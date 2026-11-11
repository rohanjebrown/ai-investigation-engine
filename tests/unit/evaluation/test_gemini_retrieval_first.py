"""Construction checks for the experimental retrieval-first decision request."""

import importlib.util
from pathlib import Path

from google.genai import types

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/evaluate_gemini_retrieval_first.py"
spec = importlib.util.spec_from_file_location("gemini_retrieval_first", SCRIPT)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def test_retrieval_first_keeps_native_search_available_with_full_initial_evidence():
    case = evaluation.load_cases()["cases"][0]
    condition = case["conditions"]["complete"]
    request = evaluation.request_for("gemini-3.5-flash-lite", case, condition)
    contents = request["contents"]
    assert len(contents) == 1
    assert contents[0].role == "user"
    assert "initial_search_response" in contents[0].parts[0].text
    config = request["config"]
    assert config.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.VALIDATED
    assert config.tool_config.function_calling_config.allowed_function_names == [evaluation.TOOL_NAME]
    assert config.tools[0].function_declarations[0].name == evaluation.TOOL_NAME
    assert config.automatic_function_calling.disable is True


def test_retrieval_first_provides_explicit_empty_search_result_for_missing_evidence():
    case = next(item for item in evaluation.load_cases()["cases"] if item["expected_behavior"] == "abstain")
    request = evaluation.request_for("gemini-3.5-flash-lite", case, case["conditions"]["no_evidence_control"])
    assert '"no_new_evidence": true' in request["contents"][0].parts[0].text
