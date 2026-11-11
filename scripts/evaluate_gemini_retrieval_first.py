"""Compare retrieval-first evidence input with the native tool-response history.

This bounded diagnostic reuses the pinned suite and corpus. It performs no
retrieval, keeps the native search function available, and sends evidence as
first-turn application context rather than a model-call/function-response pair.
"""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_gemini_search_sufficiency import (  # noqa: E402
    classify_search_target,
    evidence_response,
    expected_value_appears,
    load_cases,
    parse_decision,
    query_matches_missing_fact,
    query_matches_missing_target,
    redact,
    save_json,
)

from investigation_engine.infrastructure.config import settings  # noqa: E402
from investigation_engine.infrastructure.generation.gemini_investigation_model import (  # noqa: E402
    SYSTEM_INSTRUCTION,
    TOOL_NAME,
    _usage,
)

RETRIEVAL_FIRST_GUIDANCE = (
    "\nThe user message contains initial_search_response, an application-provided result "
    "from the first search of the bounded investigation. Treat only its cited results "
    "as evidence. Decide whether they support every fact required by the original "
    "question. The search_evidence function remains available for any specific "
    "unresolved fact; return a final decision when the results suffice."
)


def request_for(model, case, condition):
    result = evidence_response(condition, case["question"])
    payload = {
        "question": case["question"],
        "initial_search_response": result,
    }
    return {
        "model": model,
        "contents": [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=json.dumps(payload, ensure_ascii=False))],
            )
        ],
        "config": types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION + RETRIEVAL_FIRST_GUIDANCE,
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=TOOL_NAME,
                            description="Search the authorized document corpus for evidence relevant to a bounded query.",
                            parameters_json_schema={
                                "type": "object",
                                "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 500}},
                                "required": ["query"],
                                "additionalProperties": False,
                            },
                        )
                    ]
                )
            ],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.VALIDATED,
                    allowed_function_names=[TOOL_NAME],
                )
            ),
            response_mime_type="application/json",
            response_json_schema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["answered", "insufficient_evidence", "conflicting_evidence"],
                    },
                    "answer": {"type": ["string", "null"]},
                    "citations": {"type": "array", "items": {"type": "string"}},
                    "conflicts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "claim": {"type": "string"},
                                "citations": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["claim", "citations"],
                        },
                    },
                },
                "required": ["status", "answer", "citations", "conflicts"],
            },
            max_output_tokens=settings.investigation_max_output_tokens,
            candidate_count=1,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    }


async def run(args, *, client_factory=genai.Client):
    cases = load_cases()["cases"]
    end = len(cases) if args.limit_questions is None else args.start_question_index + args.limit_questions
    if args.start_question_index < 0 or end > len(cases):
        raise ValueError("Question slice is outside the pinned suite")
    cases = cases[args.start_question_index : end]
    planned = sum(len(case["conditions"]) for case in cases)
    if not args.execute:
        print(json.dumps({"mode": "preview", "planned_requests": planned}, indent=2))
        return
    if not settings.gemini_api_key or not args.max_generation_requests:
        raise ValueError("Execution needs a configured API key and explicit request cap")
    if planned > args.max_generation_requests:
        raise ValueError("Planned requests exceed the explicit cap")
    args.output.mkdir(parents=True, exist_ok=False)
    model = args.model or settings.gemini_generation_model
    client = client_factory(
        api_key=settings.gemini_api_key,
        enterprise=False,
        http_options=types.HttpOptions(
            base_url="https://generativelanguage.googleapis.com",
            api_version="v1beta",
            timeout=settings.generation_timeout_seconds * 1000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    rows, errors = [], []
    prompt_tokens = candidate_tokens = 0
    for case in cases:
        for condition_name, condition in case["conditions"].items():
            await asyncio.sleep(args.pause_seconds)
            start = perf_counter()
            try:
                response = await client.aio.models.generate_content(**request_for(model, case, condition))
                duration = (perf_counter() - start) * 1000
                usage = _usage(response.usage_metadata)
                prompt_tokens += usage.prompt_tokens
                candidate_tokens += usage.candidate_tokens
                parsed = parse_decision(response, {f"E{i}" for i in range(1, len(condition["evidence"]) + 1)})
            except Exception as error:  # bounded execution stops on the first provider error
                errors.append({"case_id": case["id"], "condition": condition_name, "error_type": type(error).__name__})
                break
            expected = condition["expected_action"]
            expected_observed = {
                "final": "answered",
                "search": "search",
                "conflict": "conflicting_evidence",
                "insufficient_evidence": "insufficient_evidence",
            }[expected]
            query = parsed.get("search_query")
            evidence_ids = {f"E{i}" for i in range(1, len(condition["evidence"]) + 1)}
            citations = parsed.get("citations", [])
            row = {
                "case_id": case["id"],
                "category": case["category"],
                "condition": condition_name,
                "expected_action": expected_observed,
                "observed_action": parsed["action"],
                "correct_decision": parsed["action"] == expected_observed,
                "answer": parsed.get("answer"),
                "expected_answer": case.get("expected_answer"),
                "reference_value_present": expected_value_appears(parsed.get("answer"), case.get("expected_answer")),
                "citation_ids": citations,
                "conflict_claims": parsed.get("conflicts", []),
                "invalid_citation_ids": sorted(set(citations) - evidence_ids),
                "search_query": query,
                "missing_evidence_targets": [item["title"] for item in condition.get("missing_evidence", [])],
                "query_matches_missing_target_title": query_matches_missing_target(
                    query, condition.get("missing_evidence", [])
                ),
                "missing_fact_targets": condition.get("missing_fact_targets", []),
                "query_matches_missing_fact_entity_and_relation": query_matches_missing_fact(
                    query,
                    condition.get("missing_evidence", []),
                    condition.get("missing_fact_targets", []),
                ),
                "search_target_classification": classify_search_target(query, case, condition, parsed["action"]),
                "duration_ms": duration,
                "usage": usage.__dict__
                if hasattr(usage, "__dict__")
                else {
                    "prompt_tokens": usage.prompt_tokens,
                    "candidate_tokens": usage.candidate_tokens,
                    "thinking_tokens": usage.thinking_tokens,
                },
                "native_search_available": True,
            }
            rows.append(row)
            with (args.output / "decisions.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(redact(row), ensure_ascii=False) + "\n")
        if errors:
            break
    by_condition = {}
    for condition in ("complete", "incomplete", "conflict_control", "no_evidence_control"):
        selected = [item for item in rows if item["condition"] == condition]
        by_condition[condition] = {
            "n": len(selected),
            "correct_decisions": sum(row["correct_decision"] for row in selected),
            "searches": sum(row["observed_action"] == "search" for row in selected),
            "unnecessary_searches": sum(
                row["observed_action"] == "search" and row["expected_action"] == "answered" for row in selected
            ),
            "premature_answers": sum(
                row["observed_action"] == "answered"
                and row["expected_action"] in {"search", "insufficient_evidence", "conflicting_evidence"}
                for row in selected
            ),
            "answers_with_reference_value": sum(row["reference_value_present"] is True for row in selected),
            "citation_rows_with_invalid_ids": sum(bool(row["invalid_citation_ids"]) for row in selected),
            "missing_target_title_matches": sum(row["query_matches_missing_target_title"] is True for row in selected),
            "missing_fact_entity_relation_matches": sum(
                row["query_matches_missing_fact_entity_and_relation"] is True for row in selected
            ),
            "search_target_classifications": {
                classification: sum(row["search_target_classification"] == classification for row in selected)
                for classification in (
                    "targets_missing_fact",
                    "does_not_target_missing_fact",
                    "redundant_to_supported_fact",
                    "unrelated_to_supported_facts",
                )
            },
            "mean_latency_ms": sum(row["duration_ms"] for row in selected) / len(selected) if selected else None,
        }
    summary = {
        "approach": "retrieval_first_same_generation_decision",
        "model": model,
        "system_instruction": "production instruction plus retrieval-first input protocol note",
        "request_count": len(rows) + len(errors),
        "completed_decisions": len(rows),
        "prompt_tokens": prompt_tokens,
        "candidate_tokens": candidate_tokens,
        "by_condition": by_condition,
        "errors": errors,
        "decision_rows": rows,
        "limitations": [
            "No retrieval was executed; the pinned local evidence was supplied directly.",
            "Search target relevance is represented only by conservative overlap with missing passage titles, not human semantic review.",
            "One stochastic trial per case; provider failure stops the run without retry.",
        ],
    }
    save_json(args.output / "summary.json", summary)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "requests": summary["request_count"],
                "completed": len(rows),
                "errors": len(errors),
            }
        )
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-generation-requests", type=int)
    parser.add_argument("--limit-questions", type=int)
    parser.add_argument("--start-question-index", type=int, default=0)
    parser.add_argument("--pause-seconds", type=float, default=5.0)
    parser.add_argument("--model")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "reports/generated"
        / ("gemini-retrieval-first-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:6]),
    )
    args = parser.parse_args(argv)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
