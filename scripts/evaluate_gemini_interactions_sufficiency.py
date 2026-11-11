"""Compare Gemini GenerateContent with stateless Interactions on native search decisions.

The experiment reuses pinned evidence, sends no reference answers to Gemini, never executes
the proposed search, and keeps ``search_evidence`` available in VALIDATED mode on every call.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from google import genai

from investigation_engine.infrastructure.config import settings
from investigation_engine.infrastructure.generation.gemini_investigation_model import (
    FINAL_SCHEMA,
    SYSTEM_INSTRUCTION,
    TOOL_DECLARATION,
    TOOL_NAME,
)

try:
    from scripts import evaluate_gemini_search_sufficiency as _evaluator
except ImportError:
    import evaluate_gemini_search_sufficiency as _evaluator

CORPUS_PATH = _evaluator.CORPUS_PATH
ROOT = _evaluator.ROOT
SUITE_PATH = _evaluator.SUITE_PATH
action_from_initial = _evaluator.action_from_initial
evidence_response = _evaluator.evidence_response
generate = _evaluator.generate
load_cases = _evaluator.load_cases
make_decision_request = _evaluator.make_decision_request
make_initial_request = _evaluator.make_initial_request
make_row = _evaluator.make_row
parse_decision = _evaluator.parse_decision
redact = _evaluator.redact
save_json = _evaluator.save_json

PROTOCOLS = ("generate_content", "interactions_stateless")


def interaction_tool():
    """Map the existing native function declaration to the Interactions schema."""
    return {
        "type": "function",
        "name": TOOL_DECLARATION["name"],
        "description": TOOL_DECLARATION["description"],
        "parameters": TOOL_DECLARATION["parameters_json_schema"],
    }


def interaction_request(model, history):
    """Build one stateless Interactions request equivalent to a production model turn."""
    return {
        "model": model,
        "store": False,
        "input": history,
        "system_instruction": SYSTEM_INSTRUCTION,
        "tools": [interaction_tool()],
        "response_format": {"type": "text", "mime_type": "application/json", "schema": FINAL_SCHEMA},
        "generation_config": {
            "max_output_tokens": settings.investigation_max_output_tokens,
            "tool_choice": {"allowed_tools": {"mode": "validated", "tools": [TOOL_NAME]}},
        },
    }


def initial_interaction_history(question):
    return [
        {
            "type": "user_input",
            "content": [{"type": "text", "text": json.dumps({"question": question}, ensure_ascii=False)}],
        }
    ]


def interaction_initial_call(response):
    """Return the sole native search call and the unmodified model steps."""
    steps = response.steps or []
    calls = [step for step in steps if step.type == "function_call"]
    outputs = [
        content.text.strip()
        for step in steps
        if step.type == "model_output"
        for content in step.content or []
        if content.type == "text" and content.text.strip()
    ]
    if len(calls) != 1 or outputs:
        return None, "initial_response_not_one_native_search_call"
    call = calls[0]
    if call.name != TOOL_NAME or not isinstance(call.arguments, dict):
        return None, "initial_search_call_invalid"
    if set(call.arguments) != {"query"}:
        return None, "initial_search_query_schema_invalid"
    query = call.arguments["query"]
    if not isinstance(query, str) or not query.strip():
        return None, "initial_search_query_invalid"
    return call, None


def interaction_history_with_result(question, model_steps, call, condition):
    """Append the exact initial model steps and a fixture-backed function result."""
    history = initial_interaction_history(question)
    history.extend(step.model_dump(mode="json") for step in model_steps)
    result = evidence_response(condition, call.arguments["query"])
    history.append(
        {
            "type": "function_result",
            "name": call.name,
            "call_id": call.id,
            "result": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
        }
    )
    return history


def parse_interaction_decision(response, evidence_ids):
    parsed = {"action": "invalid", "answer": None, "citations": [], "invalid_citation_ids": [], "error": None}
    steps = response.steps or []
    calls = [step for step in steps if step.type == "function_call"]
    outputs = [
        content.text.strip()
        for step in steps
        if step.type == "model_output"
        for content in step.content or []
        if content.type == "text" and content.text.strip()
    ]
    try:
        if calls:
            if len(calls) != 1 or outputs:
                raise ValueError("expected one native search call or one final object")
            call = calls[0]
            if call.name != TOOL_NAME or not isinstance(call.arguments, dict) or set(call.arguments) != {"query"}:
                raise ValueError("decision call does not match the native search schema")
            query = call.arguments["query"]
            if not isinstance(query, str) or not query.strip():
                raise ValueError("decision search query is empty")
            parsed.update(action="search", search_query=query)
            return parsed
        if len(outputs) != 1:
            raise ValueError("decision did not return one structured model output")
        value = json.loads(outputs[0])
        status = value.get("status")
        if status not in {"answered", "insufficient_evidence", "conflicting_evidence"}:
            raise ValueError("decision status is invalid")
        citations = value.get("citations", [])
        if not isinstance(citations, list) or any(not isinstance(item, str) for item in citations):
            raise ValueError("decision citations are invalid")
        parsed.update(
            action=status,
            answer=value.get("answer"),
            citations=citations,
            conflicts=value.get("conflicts", []),
            invalid_citation_ids=sorted(set(citations) - set(evidence_ids)),
        )
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as error:
        parsed["error"] = str(error)
    return parsed


def interaction_metrics(response, duration_ms):
    usage = response.usage
    return {
        "duration_ms": duration_ms,
        "reported_model_version": response.model,
        "usage": {
            "prompt_tokens": usage.total_input_tokens if usage else None,
            "candidate_tokens": usage.total_output_tokens if usage else None,
            "thinking_tokens": usage.total_thought_tokens if usage else None,
        },
    }


async def call_interactions(client, request, budget, maximum, pause_seconds):
    if budget["attempted"] >= maximum:
        raise RuntimeError("Generation-request cap reached")
    if budget["attempted"]:
        await asyncio.sleep(pause_seconds)
    budget["attempted"] += 1
    started = perf_counter()
    response = await client.aio.interactions.create(**request)
    return response, interaction_metrics(response, (perf_counter() - started) * 1000)


def make_plan(args, selected_cases, mode):
    request_count = len(selected_cases) * 6
    return {
        "schema_version": 1,
        "mode": mode,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "model": args.model or settings.gemini_generation_model,
        "protocols": list(PROTOCOLS),
        "question_count": len(selected_cases),
        "included_case_ids": [case["id"] for case in selected_cases],
        "question_categories": sorted({case["category"] for case in selected_cases}),
        "conditions_per_question": ["complete", "incomplete"],
        "planned_generation_requests": request_count,
        "maximum_generation_requests": args.max_generation_requests,
        "native_tool_mode": "search_evidence available on every initial and decision request; VALIDATED",
        "interactions_storage": "store=false; model steps preserved client-side in stateless history",
        "retrieval_calls": 0,
        "database_queries": 0,
        "retries": 0,
        "reference_answers_sent_to_model": False,
        "evidence_source": "pinned local corpus fixtures; no live retrieval",
        "pause_seconds": args.pause_seconds,
    }


def summarize(rows, outcomes, budget, initial_metrics):
    arms = {}
    for protocol in PROTOCOLS:
        selected = [row for row in rows if row["protocol"] == protocol]
        protocol_initials = [item for item in initial_metrics if item["protocol"] == protocol]
        sufficient = [row for row in selected if row["expected_action"] == "final"]
        insufficient = [row for row in selected if row["expected_action"] == "search"]
        decision_usage = [row["generation"]["usage"] for row in selected]
        initial_usage = [item["usage"] for item in protocol_initials]
        prompt_tokens = sum(item["prompt_tokens"] or 0 for item in decision_usage + initial_usage)
        candidate_tokens = sum(item["candidate_tokens"] or 0 for item in decision_usage + initial_usage)
        investigations = len({row["case_id"] for row in selected})
        arms[protocol] = {
            "n": len(selected),
            "sufficient_n": len(sufficient),
            "final_when_sufficient": sum(row["observed_action"] == "answered" for row in sufficient),
            "unnecessary_searches": sum(row["observed_action"] == "search" for row in sufficient),
            "insufficient_n": len(insufficient),
            "search_when_insufficient": sum(row["observed_action"] == "search" for row in insufficient),
            "search_query_matches_missing_fact_lexically": sum(
                row["search_query_matches_missing_fact_entity_and_relation"] is True for row in insufficient
            ),
            "answers_matching_reference_surface": sum(
                row["reference_value_present_in_answer_text"] is True for row in selected
            ),
            "unsupported_final_answers": sum(row["unsupported_final_answer"] for row in selected),
            "invalid_citations": sum(len(row["invalid_citation_ids"]) for row in selected),
            "generation_requests": len(selected) + len(protocol_initials),
            "prompt_tokens_including_initial": prompt_tokens,
            "candidate_tokens_including_initial": candidate_tokens,
            "mean_prompt_tokens_per_investigation": prompt_tokens / investigations if investigations else None,
            "mean_candidate_tokens_per_investigation": candidate_tokens / investigations if investigations else None,
            "mean_decision_latency_ms": (
                sum(row["generation"]["duration_ms"] for row in selected) / len(selected) if selected else None
            ),
            "mean_total_latency_ms_per_condition": (
                sum(row["total_latency_ms"] for row in selected) / len(selected) if selected else None
            ),
        }
    return {
        "accounting": {
            "generation_requests": budget["attempted"],
            "initial_request_count": len(initial_metrics),
            "decision_row_count": len(rows),
            "outcome_count": len(outcomes),
        },
        "arms": arms,
        "initial_metrics": initial_metrics,
        "outcomes": outcomes,
        "scope_note": (
            "One trial per protocol/case/condition. Search proposals are never executed. "
            "Reference answers are used only post hoc; surface matches and lexical query matches are proxies. "
            "The Interactions arm sets store=false and resends model-generated steps exactly."
        ),
    }


async def run(args, *, client_factory=genai.Client):
    cases = load_cases(args.suite, args.corpus)
    if args.case_ids:
        requested = set(args.case_ids)
        cases["cases"] = [case for case in cases["cases"] if case["id"] in requested]
        found = {case["id"] for case in cases["cases"]}
        if found != requested:
            raise ValueError(f"Unknown case IDs: {sorted(requested - found)}")
    if not cases["cases"]:
        raise ValueError("No cases selected")
    planned = len(cases["cases"]) * 6
    if not args.execute:
        if args.output.exists():
            raise ValueError("Output directory already exists")
        args.output.mkdir(parents=True)
        plan = make_plan(args, cases["cases"], "preview")
        save_json(args.output / "plan.json", plan)
        return {"output": str(args.output), "mode": "preview", "planned_generation_requests": planned}
    if not settings.gemini_api_key:
        raise ValueError("Execution requires configured GEMINI_API_KEY")
    if args.max_generation_requests is None or planned > args.max_generation_requests:
        raise ValueError("Planned calls exceed the explicit generation-request cap")
    if args.output.exists():
        raise ValueError("Output directory already exists")
    args.output.mkdir(parents=True)
    plan = make_plan(args, cases["cases"], "execute")
    save_json(args.output / "plan.json", plan)
    model = args.model or settings.gemini_generation_model
    client = client_factory(
        api_key=settings.gemini_api_key,
        enterprise=False,
    )
    budget = {"attempted": 0}
    rows, outcomes, initial_metrics = [], [], []
    try:
        for case in cases["cases"]:
            starts = {}
            for protocol in PROTOCOLS:
                try:
                    if protocol == "generate_content":
                        response, metrics = await generate(
                            client,
                            make_initial_request(model, case["question"]),
                            budget,
                            args.max_generation_requests,
                            args.pause_seconds,
                        )
                        result, stop_reason = action_from_initial(response, model=model)
                        if result is None:
                            raise ValueError(stop_reason)
                        native_content, call = result
                        starts[protocol] = {"native": native_content, "call": call, "query": call.args["query"]}
                    else:
                        history = initial_interaction_history(case["question"])
                        response, metrics = await call_interactions(
                            client,
                            interaction_request(model, history),
                            budget,
                            args.max_generation_requests,
                            args.pause_seconds,
                        )
                        call, stop_reason = interaction_initial_call(response)
                        if call is None:
                            raise ValueError(stop_reason)
                        starts[protocol] = {
                            "steps": list(response.steps or []),
                            "call": call,
                            "query": call.arguments["query"],
                        }
                    metrics["protocol"] = protocol
                    metrics["case_id"] = case["id"]
                    initial_metrics.append(metrics)
                except Exception as error:
                    outcomes.append(
                        {
                            "case_id": case["id"],
                            "protocol": protocol,
                            "phase": "initial",
                            "error_type": type(error).__name__,
                            "message": str(error)[:300],
                        }
                    )
                    continue
            for condition_name in ("complete", "incomplete"):
                condition = case["conditions"][condition_name]
                for protocol in PROTOCOLS:
                    if protocol not in starts:
                        continue
                    start = starts[protocol]
                    try:
                        evidence_ids = [f"E{index}" for index in range(1, len(condition["evidence"]) + 1)]
                        if protocol == "generate_content":
                            request = make_decision_request(
                                model,
                                case["question"],
                                start["native"],
                                start["call"],
                                condition,
                                conditional=False,
                            )
                            response, metrics = await generate(
                                client, request, budget, args.max_generation_requests, args.pause_seconds
                            )
                            parsed = parse_decision(response, evidence_ids)
                        else:
                            history = interaction_history_with_result(
                                case["question"], start["steps"], start["call"], condition
                            )
                            response, metrics = await call_interactions(
                                client,
                                interaction_request(model, history),
                                budget,
                                args.max_generation_requests,
                                args.pause_seconds,
                            )
                            parsed = parse_interaction_decision(response, evidence_ids)
                        total_latency = metrics["duration_ms"] + next(
                            item["duration_ms"]
                            for item in reversed(initial_metrics)
                            if item["protocol"] == protocol and item["case_id"] == case["id"]
                        ) / len(case["conditions"])
                        row = make_row(
                            case,
                            condition_name,
                            protocol,
                            condition,
                            parsed,
                            metrics,
                            requested_query=start["query"],
                            initial_ok=True,
                        )
                        row["protocol"] = protocol
                        row["total_latency_ms"] = total_latency
                        row["native_tool_available"] = True
                        rows.append(row)
                        with (args.output / "decisions.jsonl").open("a", encoding="utf-8") as stream:
                            stream.write(json.dumps(redact(row), ensure_ascii=False) + "\n")
                    except Exception as error:
                        outcomes.append(
                            {
                                "case_id": case["id"],
                                "condition": condition_name,
                                "protocol": protocol,
                                "phase": "decision",
                                "error_type": type(error).__name__,
                                "message": str(error)[:300],
                            }
                        )
            if budget["attempted"] >= args.max_generation_requests:
                break
    finally:
        await client.aio.aclose()
    summary = summarize(rows, outcomes, budget, initial_metrics)
    summary.update({"model": model, "suite_path": str(args.suite), "corpus_id": cases["corpus_id"]})
    save_json(args.output / "summary.json", summary)
    return {"output": str(args.output), "mode": "execute", **summary["accounting"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-generation-requests", type=int)
    parser.add_argument("--case-id", dest="case_ids", action="append")
    parser.add_argument("--pause-seconds", type=float, default=5.0)
    parser.add_argument("--model")
    parser.add_argument("--suite", type=Path, default=SUITE_PATH)
    parser.add_argument("--corpus", type=Path, default=CORPUS_PATH)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "reports/generated/gemini-interactions-sufficiency"
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]),
    )
    args = parser.parse_args(argv)
    if not 0 <= args.pause_seconds <= 60:
        parser.error("--pause-seconds must be between 0 and 60")
    try:
        print(json.dumps(asyncio.run(run(args)), ensure_ascii=False))
    except (ValueError, RuntimeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
