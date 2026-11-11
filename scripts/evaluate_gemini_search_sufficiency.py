"""Evaluate Gemini search decisions on the pinned 40-question suite.

This is a bounded, preview-first diagnostic. It never runs retrieval and keeps
the native search function available while comparing diagnostic decision policies.
"""

import argparse
import asyncio
import json
import re
import unicodedata
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from google import genai
from google.genai import types

from investigation_engine.application.investigations import SearchEvidenceCall
from investigation_engine.application.ports.generation_errors import GenerationResponseError
from investigation_engine.infrastructure.config import settings
from investigation_engine.infrastructure.generation.gemini_investigation_model import (
    FINAL_SCHEMA,
    SYSTEM_INSTRUCTION,
    TOOL_DECLARATION,
    TOOL_NAME,
    GeminiInvestigationModel,
    _usage,
)
from investigation_engine.infrastructure.generation.gemma4_investigation_model import (
    GEMMA4_FINAL_FORMAT,
    Gemma4InvestigationModel,
)

ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = ROOT / "data/samples/evaluation/suite.json"
CORPUS_PATH = ROOT / "data/samples/evaluation/corpus.json"
CONDITIONAL_DESCRIPTION = (
    "Search the authorized document corpus only when the supplied evidence leaves a "
    "specific question-relevant fact unresolved. If the evidence already supports "
    "the complete answer, do not call this function."
)
CONDITIONS = ("complete", "incomplete")
ARMS = ("production", "conditional_tool_description")
IDENTITY_POLICY = "identity_normalization"
GAP_COMMITMENT_POLICY = "gap_commitment_schema"
GOAL_COMMITMENT_POLICY = "goal_commitment_schema"
EVIDENCE_TEXT_POLICY = "readable_evidence_duplicate"
TEMPERATURE_POLICY = "decision_temperature_zero"
EVIDENCE_COVERAGE_POLICY = "evidence_coverage_audit"
EVIDENCE_COVERAGE_EMBEDDED_POLICY = "evidence_coverage_embedded"
ANSWER_FIRST_POLICY = "answer_first_instruction"
EXPLICIT_FINALIZE_POLICY = "explicit_finalize_tool"
EXPLICIT_FINALIZE_VALIDATED_POLICY = "explicit_finalize_validated"
FINALIZE_TOOL_NAME = "finalize_investigation"
_SEARCH_ORIENTED_OPENING = (
    "You are conducting a bounded investigation using the application's search tool.\n"
    "Use search_evidence to retrieve facts. Inspect each actual tool result before deciding\n"
    "what to search next; later queries should use entities or facts learned from earlier\n"
    "results when that helps answer the question. You may request one search per model turn.\n"
    "Never claim a search occurred unless the application returned its function response."
)
_ANSWER_FIRST_OPENING = (
    "You are answering the user's original question from the admitted evidence.\n"
    "At each turn, decide whether the evidence already supports the answer or leaves a\n"
    "specific question-relevant fact unresolved. The search_evidence tool remains available\n"
    "when a missing fact, unresolved ambiguity, or material contradiction requires retrieval.\n"
    "You may request one search per model turn. Never claim a search occurred unless the\n"
    "application returned its function response."
)
ANSWER_FIRST_SYSTEM_INSTRUCTION = SYSTEM_INSTRUCTION.replace(_SEARCH_ORIENTED_OPENING, _ANSWER_FIRST_OPENING, 1)
EVIDENCE_COVERAGE_POLICIES = (EVIDENCE_COVERAGE_POLICY, EVIDENCE_COVERAGE_EMBEDDED_POLICY)
ALL_ARMS = (
    *ARMS,
    IDENTITY_POLICY,
    GAP_COMMITMENT_POLICY,
    GOAL_COMMITMENT_POLICY,
    EVIDENCE_TEXT_POLICY,
    TEMPERATURE_POLICY,
    EVIDENCE_COVERAGE_POLICY,
    EVIDENCE_COVERAGE_EMBEDDED_POLICY,
    ANSWER_FIRST_POLICY,
    EXPLICIT_FINALIZE_POLICY,
    EXPLICIT_FINALIZE_VALIDATED_POLICY,
)
IDENTITY_GUIDANCE = (
    "In judging whether excerpts describe the same entity, compare the full names and "
    "identifying context stated in the excerpt text. A shortened display title that "
    "omits part of a matching full name is not by itself an identity ambiguity. Do not "
    "search merely to reconfirm an identity that the source text states consistently; "
    "search when distinct entities remain plausible or material identifiers differ."
)
EVIDENCE_COVERAGE_INSTRUCTION = (
    "Assess only what the supplied passages establish for the user's question. Break the question into the "
    "smallest set of facts or relationships needed to answer it. For each requirement, report whether the "
    "passages support it, omit it, conflict about it, or leave it unclear. Cite only supplied evidence IDs and "
    "include a short exact quote for each supported claim. Do not answer the question, infer missing links, "
    "use outside knowledge, or recommend a search. This is an evidence audit, not an investigation decision."
)
EVIDENCE_COVERAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "requirements": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "requirement": {"type": "string", "minLength": 1, "maxLength": 240},
                    "status": {"type": "string", "enum": ["supported", "missing", "conflicting", "unclear"]},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
                    "quotes": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
                },
                "required": ["requirement", "status", "evidence_ids", "quotes"],
                "additionalProperties": False,
            },
        },
        "overall_status": {"type": "string", "enum": ["sufficient", "insufficient", "conflicting", "unclear"]},
    },
    "required": ["requirements", "overall_status"],
    "additionalProperties": False,
}


def load_cases(suite_path=SUITE_PATH, corpus_path=CORPUS_PATH):
    """Join the pinned question suite to its exact local source passages."""
    suite = json.loads(Path(suite_path).read_text(encoding="utf-8"))
    corpus = json.loads(Path(corpus_path).read_text(encoding="utf-8"))
    if suite.get("corpus_id") != corpus.get("corpus_id"):
        raise ValueError("Question suite and passage corpus IDs differ")
    passages = {item["content_sha256"]: item for item in corpus["documents"]}
    cases = []
    for item in suite["cases"]:
        required = item.get("required_evidence", [])
        excerpts = []
        for evidence in required:
            passage = passages.get(evidence["content_sha256"])
            if passage is None:
                raise ValueError(f"Missing pinned passage {evidence['content_sha256']}")
            start, end = evidence["start"], evidence["end"]
            if not 0 <= start < end <= len(passage["text"]):
                raise ValueError(f"Invalid source span for {item['id']}")
            excerpts.append(
                {
                    "title": passage["title"],
                    "text": passage["text"][start:end],
                    "full_text": passage["text"],
                    "source": passage["source"],
                    "content_sha256": evidence["content_sha256"],
                    "start": start,
                    "end": end,
                }
            )
        expected = item["expected_behavior"]
        cases.append(
            {
                "id": item["id"],
                "question": item["question"],
                "category": item["category"],
                "independent_target_id": item.get("independent_target_id"),
                "expected_behavior": expected,
                "expected_answer": item.get("expected_answer"),
                "annotation_triples": item.get("annotation_triples", []),
                "conditions": condition_records(item, excerpts),
                "source_fingerprint": sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            }
        )
    return {"corpus_id": suite["corpus_id"], "cases": cases}


def condition_records(item, excerpts):
    """Make complete and missing-link fixtures without consulting gold answers."""
    expected = item["expected_behavior"]
    if expected == "answer":
        if not excerpts:
            raise ValueError(f"Answerable case {item['id']} has no required evidence")
        partial = excerpts[:-1]
        missing_evidence = excerpts[len(partial) :]
        return {
            "complete": {
                "evidence": excerpts,
                "missing_evidence": [],
                "supported_fact_targets": triples_in_evidence(item.get("annotation_triples", []), excerpts),
                "expected_action": "final",
            },
            "incomplete": {
                "evidence": partial,
                "supported_fact_targets": triples_in_evidence(item.get("annotation_triples", []), partial),
                "missing_evidence": missing_evidence,
                "missing_fact_targets": triples_in_evidence(item.get("annotation_triples", []), missing_evidence),
                "expected_action": "search",
            },
        }
    if expected == "report_conflict":
        if len(excerpts) < 2:
            raise ValueError(f"Conflict case {item['id']} needs two cited claims")
        return {"conflict_control": {"evidence": excerpts, "expected_action": "conflict"}}
    if expected == "abstain":
        return {"no_evidence_control": {"evidence": excerpts, "expected_action": "search"}}
    raise ValueError(f"Unsupported expected behavior: {expected}")


def build_config(
    *,
    conditional=False,
    identity_guidance=False,
    gap_commitment=False,
    goal_commitment=False,
    function_calling_mode=types.FunctionCallingConfigMode.VALIDATED,
    temperature=None,
    answer_first=False,
    explicit_finalize=False,
    explicit_finalize_validated=False,
    response_schema=True,
    gemma4=False,
):
    declaration = deepcopy(TOOL_DECLARATION)
    if conditional:
        declaration["description"] = CONDITIONAL_DESCRIPTION
    if gap_commitment:
        declaration["parameters_json_schema"]["properties"]["unresolved_fact"] = {
            "type": "string",
            "minLength": 1,
            "maxLength": 240,
            "description": "The specific fact required by the question that the available evidence does not establish.",
        }
        declaration["parameters_json_schema"]["required"].append("unresolved_fact")
    if goal_commitment:
        declaration["parameters_json_schema"]["properties"]["required_facts"] = {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {"type": "string", "minLength": 1, "maxLength": 240},
            "description": (
                "List the distinct facts or relationships needed to answer the original question. "
                "Include only facts required for the answer, not helpful background."
            ),
        }
        declaration["parameters_json_schema"]["required"].append("required_facts")
    finalize_declaration = {
        "name": FINALIZE_TOOL_NAME,
        "description": "Return the evidence-based investigation conclusion with its citations.",
        "parameters_json_schema": FINAL_SCHEMA,
    }
    explicit_finalize_enabled = explicit_finalize or explicit_finalize_validated
    function_declarations = [types.FunctionDeclaration(**declaration)]
    if explicit_finalize_enabled:
        function_declarations.append(types.FunctionDeclaration(**finalize_declaration))
    options = {
        "system_instruction": (ANSWER_FIRST_SYSTEM_INSTRUCTION if answer_first else SYSTEM_INSTRUCTION)
        + ("\n" + IDENTITY_GUIDANCE if identity_guidance else "")
        + ("\n\n" + GEMMA4_FINAL_FORMAT if gemma4 else ""),
        "tools": [types.Tool(function_declarations=function_declarations)],
        "max_output_tokens": settings.investigation_max_output_tokens,
        "candidate_count": 1,
        "temperature": temperature,
    }
    if not gemma4:
        options.update(
            tool_config=(
                types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode=(
                            types.FunctionCallingConfigMode.ANY
                            if explicit_finalize
                            else (
                                types.FunctionCallingConfigMode.VALIDATED
                                if explicit_finalize_validated
                                else function_calling_mode
                            )
                        ),
                        allowed_function_names=(
                            [TOOL_NAME, FINALIZE_TOOL_NAME]
                            if explicit_finalize_enabled
                            else (
                                [TOOL_NAME]
                                if function_calling_mode == types.FunctionCallingConfigMode.VALIDATED
                                else None
                            )
                        ),
                    )
                )
                if function_calling_mode is not None
                else None
            ),
            response_mime_type="application/json" if response_schema and not explicit_finalize else None,
            response_json_schema=FINAL_SCHEMA if response_schema and not explicit_finalize else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
    return types.GenerateContentConfig(**options)


def make_initial_request(
    model,
    question,
    *,
    goal_commitment=False,
    function_calling_mode=types.FunctionCallingConfigMode.VALIDATED,
    response_schema=True,
):
    return {
        "model": model,
        "contents": [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=json.dumps({"question": question}, ensure_ascii=False))],
            )
        ],
        "config": build_config(
            goal_commitment=goal_commitment,
            function_calling_mode=function_calling_mode,
            response_schema=response_schema,
            gemma4=model.startswith("gemma-4-"),
        ),
    }


def make_evidence_coverage_request(model, question, condition, requested_query, *, context_view="required_sentence"):
    evidence = evidence_response(condition, requested_query, context_view=context_view)
    contents = {
        "question": question,
        "evidence": evidence["results"],
    }
    return {
        "model": model,
        "contents": [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=json.dumps(contents, ensure_ascii=False))],
            )
        ],
        "config": types.GenerateContentConfig(
            system_instruction=EVIDENCE_COVERAGE_INSTRUCTION,
            response_mime_type="application/json",
            response_json_schema=EVIDENCE_COVERAGE_SCHEMA,
            max_output_tokens=512,
            candidate_count=1,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    }


def parse_evidence_coverage_response(response):
    candidates = response.candidates or []
    if len(candidates) != 1 or candidates[0].finish_reason != types.FinishReason.STOP:
        raise ValueError("evidence coverage audit did not complete normally")
    content = candidates[0].content
    if content is None or content.role != "model":
        raise ValueError("evidence coverage audit is missing model content")
    text_parts = [part.text for part in content.parts or [] if part.text and not part.thought]
    if len(text_parts) != 1:
        raise ValueError("evidence coverage audit must return one JSON object")
    result = json.loads(text_parts[0])
    if not isinstance(result, dict) or result.get("overall_status") not in {
        "sufficient",
        "insufficient",
        "conflicting",
        "unclear",
    }:
        raise ValueError("evidence coverage audit returned an invalid status")
    if not isinstance(result.get("requirements"), list) or not result["requirements"]:
        raise ValueError("evidence coverage audit returned no requirements")
    return result


def action_from_initial(response, *, model, goal_commitment=False):
    """Return a supported initial search call and exact model content, if any."""
    candidates = response.candidates or []
    if len(candidates) != 1 or candidates[0].finish_reason != types.FinishReason.STOP:
        return None, "initial_response_not_completed"
    content = candidates[0].content
    if content is None or content.role != "model":
        return None, "initial_response_missing_model_content"
    call_parts = [part for part in content.parts or [] if part.function_call is not None]
    calls = [part.function_call for part in call_parts]
    visible = [part for part in content.parts or [] if part.text and part.text.strip() and not part.thought]
    if len(calls) != 1 or visible:
        return None, "initial_response_not_one_native_search_call"
    call = calls[0]
    expected_args = {"query", "required_facts"} if goal_commitment else {"query"}
    if call.name != TOOL_NAME or not isinstance(call.args, dict) or set(call.args) != expected_args:
        return None, "initial_search_call_invalid"
    if not isinstance(call.args["query"], str) or not call.args["query"].strip():
        return None, "initial_search_query_invalid"
    if goal_commitment and (
        not isinstance(call.args["required_facts"], list)
        or not 1 <= len(call.args["required_facts"]) <= 8
        or any(not isinstance(item, str) or not item.strip() or len(item) > 240 for item in call.args["required_facts"])
    ):
        return None, "initial_required_facts_invalid"
    if model.startswith("gemini-3") and not call_parts[0].thought_signature:
        return None, "initial_search_call_missing_required_signature"
    if len(content.model_dump_json()) > 64000:
        return None, "initial_native_model_content_exceeds_production_bound"
    return (content.model_copy(deep=True), call), None


def summarize_response_shape(response):
    """Record response structure without persisting hidden-thought text or full output."""
    candidates = response.candidates or []
    summary = {"candidate_count": len(candidates)}
    if len(candidates) != 1:
        return summary
    candidate = candidates[0]
    content = candidate.content
    summary.update(
        finish_reason=str(candidate.finish_reason),
        content_role=getattr(content, "role", None),
    )
    parts = []
    for part in (content.parts if content is not None else []) or []:
        function_call = getattr(part, "function_call", None)
        text = getattr(part, "text", None)
        thought = bool(getattr(part, "thought", False))
        fields = []
        if function_call is not None:
            fields.append("function_call")
        if isinstance(text, str) and text:
            fields.append("text")
        if getattr(part, "inline_data", None) is not None:
            fields.append("inline_data")
        if getattr(part, "file_data", None) is not None:
            fields.append("file_data")
        part_summary = {"fields": fields, "thought": thought}
        if function_call is not None:
            part_summary["function_name"] = getattr(function_call, "name", None)
            part_summary["function_call_id_present"] = getattr(function_call, "id", None) is not None
            part_summary["thought_signature_present"] = getattr(part, "thought_signature", None) is not None
        if isinstance(text, str) and text and not thought:
            part_summary["visible_text_chars"] = len(text)
            part_summary["visible_text_preview"] = text[:240]
        parts.append(part_summary)
    summary["parts"] = parts
    return summary


def evidence_response(condition, requested_query, *, context_view="required_sentence"):
    evidence = condition["evidence"]
    results = []
    for index, record in enumerate(evidence, 1):
        source = record["source"]
        excerpt = record["text"]
        if context_view == "full_source_passage":
            excerpt = record["full_text"]
        elif context_view == "sentence_with_neighbors":
            excerpt = sentence_context_window(record)
        results.append(
            {
                "evidence_id": f"E{index}",
                "title": record["title"],
                "excerpt": excerpt,
                "source": {
                    "kind": source["kind"],
                    "revision": source.get("revision"),
                    "display_name": source.get("display_name", record["title"]),
                    **({"metadata": source["metadata"]} if source.get("metadata") else {}),
                },
            }
        )
    return {
        "search_sequence": 1,
        "query": requested_query,
        "results": results,
        "new_evidence_ids": [item["evidence_id"] for item in results],
        "previously_available_evidence_ids": [],
        "no_new_evidence": not results,
    }


def render_evidence_text(result):
    """Duplicate returned evidence as plain text without adding facts or judgments."""
    lines = ["Readable copy of the search tool's evidence records (same IDs and text):"]
    if not result["results"]:
        lines.append("No evidence records were returned.")
    for item in result["results"]:
        lines.extend((f"[{item['evidence_id']}] {item['title']}", item["excerpt"]))
    return "\n".join(lines)


def sentence_context_window(record):
    """Return the annotated sentence plus at most one neighboring sentence per side."""
    text = record["full_text"]
    start, end = record["start"], record["end"]
    ranges = []
    sentence_start = 0
    for match in re.finditer(r"(?<=[.!?])\s+", text):
        ranges.append((sentence_start, match.start()))
        sentence_start = match.end()
    ranges.append((sentence_start, len(text)))
    overlapping = [index for index, (left, right) in enumerate(ranges) if left < end and right > start]
    if not overlapping:
        return record["text"]
    lower = max(0, overlapping[0] - 1)
    upper = min(len(ranges) - 1, overlapping[-1] + 1)
    return text[ranges[lower][0] : ranges[upper][1]].strip()


def make_decision_request(
    model,
    question,
    initial_content,
    call,
    condition,
    *,
    conditional,
    identity_guidance=False,
    gap_commitment=False,
    goal_commitment=False,
    evidence_text_reinforcement=False,
    context_view="required_sentence",
    function_calling_mode=types.FunctionCallingConfigMode.VALIDATED,
    temperature=None,
    evidence_coverage_audit=None,
    embed_coverage_audit=False,
    answer_first=False,
    explicit_finalize=False,
    explicit_finalize_validated=False,
    response_schema=True,
):
    first_result = evidence_response(condition, call.args["query"], context_view=context_view)
    if evidence_coverage_audit is not None and embed_coverage_audit:
        first_result["evidence_coverage_audit"] = evidence_coverage_audit
    function_response = types.FunctionResponse(
        id=call.id,
        name=call.name,
        response=first_result,
    )
    function_response_parts = [types.Part(function_response=function_response)]
    if evidence_text_reinforcement:
        function_response_parts.append(types.Part.from_text(text=render_evidence_text(first_result)))
    if evidence_coverage_audit is not None and not embed_coverage_audit:
        function_response_parts.append(
            types.Part.from_text(
                text=json.dumps({"evidence_coverage_audit": evidence_coverage_audit}, ensure_ascii=False)
            )
        )
    return {
        "model": model,
        "contents": [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=json.dumps({"question": question}, ensure_ascii=False))],
            ),
            initial_content.model_copy(deep=True),
            types.Content(role="user", parts=function_response_parts),
        ],
        "config": build_config(
            conditional=conditional,
            identity_guidance=identity_guidance,
            gap_commitment=gap_commitment,
            goal_commitment=goal_commitment,
            function_calling_mode=function_calling_mode,
            temperature=temperature,
            answer_first=answer_first,
            explicit_finalize=explicit_finalize,
            explicit_finalize_validated=explicit_finalize_validated,
            response_schema=response_schema,
            gemma4=model.startswith("gemma-4-"),
        ),
    }


def parse_decision(
    response,
    evidence_ids,
    *,
    gap_commitment=False,
    goal_commitment=False,
    explicit_finalize=False,
    explicit_finalize_validated=False,
    model=None,
):
    result = {"action": "invalid", "answer": None, "citations": [], "error": None}
    try:
        if explicit_finalize or explicit_finalize_validated:
            candidates = response.candidates or []
            if len(candidates) != 1 or candidates[0].finish_reason != types.FinishReason.STOP:
                raise ValueError("generation did not complete normally")
            parts = candidates[0].content.parts
            calls = [part.function_call for part in parts if getattr(part, "function_call", None) is not None]
            texts = [part.text for part in parts if part.text and part.text.strip() and not part.thought]
            if not calls and explicit_finalize_validated:
                parser = (
                    Gemma4InvestigationModel if model and model.startswith("gemma-4-") else GeminiInvestigationModel
                )
                decision = parser._parse_response(response)
                if isinstance(decision, SearchEvidenceCall):
                    result.update(action="search", search_query=decision.query)
                    return result
                result.update(
                    action=decision.status.value,
                    answer=decision.answer,
                    citations=list(decision.citations),
                    conflicts=[{"claim": item.claim, "citations": list(item.citations)} for item in decision.conflicts],
                )
                all_citations = [
                    *result["citations"],
                    *(citation for item in result["conflicts"] for citation in item["citations"]),
                ]
                result["invalid_citation_ids"] = sorted(set(all_citations) - set(evidence_ids))
                return result
            if len(calls) != 1 or texts:
                raise ValueError("expected exactly one native search or finalize function call")
            call = calls[0]
            if not isinstance(call.args, dict):
                raise ValueError("native function arguments must be an object")
            if call.name == TOOL_NAME:
                if (
                    set(call.args) != {"query"}
                    or not isinstance(call.args["query"], str)
                    or not call.args["query"].strip()
                ):
                    raise ValueError("search_evidence call must contain one nonempty query")
                result.update(action="search", search_query=call.args["query"])
                return result
            if call.name != FINALIZE_TOOL_NAME:
                raise ValueError("unexpected native function name")
            if set(call.args) != set(FINAL_SCHEMA["properties"]):
                raise ValueError("finalize_investigation arguments do not match the final schema")
            status = call.args["status"]
            answer = call.args["answer"]
            citations = call.args["citations"]
            conflicts = call.args["conflicts"]
            if status not in FINAL_SCHEMA["properties"]["status"]["enum"]:
                raise ValueError("finalize_investigation has an invalid status")
            if answer is not None and not isinstance(answer, str):
                raise ValueError("finalize_investigation answer must be a string or null")
            if not isinstance(citations, list) or any(not isinstance(item, str) for item in citations):
                raise ValueError("finalize_investigation citations must be strings")
            if not isinstance(conflicts, list) or any(
                not isinstance(item, dict)
                or set(item) != {"claim", "citations"}
                or not isinstance(item["claim"], str)
                or not isinstance(item["citations"], list)
                or any(not isinstance(citation, str) for citation in item["citations"])
                for item in conflicts
            ):
                raise ValueError("finalize_investigation conflicts are invalid")
            all_citations = [*citations, *(citation for item in conflicts for citation in item["citations"])]
            result.update(
                action=status,
                answer=answer,
                citations=citations,
                conflicts=conflicts,
                invalid_citation_ids=sorted(set(all_citations) - set(evidence_ids)),
            )
            return result
        if gap_commitment or goal_commitment:
            candidates = response.candidates or []
            if len(candidates) != 1 or candidates[0].finish_reason != types.FinishReason.STOP:
                raise ValueError("generation did not complete normally")
            parts = candidates[0].content.parts
            calls = [part.function_call for part in parts if part.function_call is not None]
            texts = [part.text for part in parts if part.text and part.text.strip() and not part.thought]
            if calls:
                if len(calls) != 1 or texts:
                    raise ValueError("expected one native function call or one final object")
                call = calls[0]
                expected_args = {"query", "required_facts"} if goal_commitment else {"query", "unresolved_fact"}
                if call.name != TOOL_NAME or not isinstance(call.args, dict):
                    raise ValueError("function call arguments do not match the experimental schema")
                if set(call.args) != expected_args:
                    if gap_commitment:
                        raise ValueError("function call lacks a specific unresolved fact")
                    raise ValueError("function call lacks a required-facts checklist")
                if not isinstance(call.args["query"], str) or not call.args["query"].strip():
                    raise ValueError("function call has an invalid query")
                if goal_commitment:
                    required_facts = call.args["required_facts"]
                    if (
                        not isinstance(required_facts, list)
                        or not 1 <= len(required_facts) <= 8
                        or any(
                            not isinstance(item, str) or not item.strip() or len(item) > 240 for item in required_facts
                        )
                    ):
                        raise ValueError("function call has an invalid required-facts checklist")
                    result.update(action="search", search_query=call.args["query"], required_facts=required_facts)
                else:
                    if not isinstance(call.args["unresolved_fact"], str) or not call.args["unresolved_fact"].strip():
                        raise ValueError("function call lacks a specific unresolved fact")
                    result.update(
                        action="search",
                        search_query=call.args["query"],
                        unresolved_fact=call.args["unresolved_fact"],
                    )
                return result
        parser = Gemma4InvestigationModel if model and model.startswith("gemma-4-") else GeminiInvestigationModel
        decision = parser._parse_response(response)
        if isinstance(decision, SearchEvidenceCall):
            result.update(action="search", search_query=decision.query)
            return result
        conflict_claims = [{"claim": item.claim, "citations": list(item.citations)} for item in decision.conflicts]
        all_citation_ids = list(decision.citations)
        all_citation_ids.extend(citation for item in decision.conflicts for citation in item.citations)
        result.update(
            action=decision.status.value,
            answer=decision.answer,
            citations=list(decision.citations),
            conflicts=conflict_claims,
            invalid_citation_ids=sorted(set(all_citation_ids) - set(evidence_ids)),
        )
    except (GenerationResponseError, TypeError, ValueError) as error:
        result["error"] = str(error)
    return result


def secret_values():
    return tuple(value for value in (settings.gemini_api_key, settings.db_password) if value)


def redact(value):
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        for secret in secret_values():
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"\bAIza[A-Za-z0-9_-]{30,}\b", "[REDACTED]", value)
        return re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
    return value


def save_json(path, value):
    path.write_text(json.dumps(redact(value), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


async def generate(client, request, budget, maximum, pause_seconds):
    if budget["attempted"] >= maximum:
        raise RuntimeError("Generation-request cap reached")
    if budget["attempted"]:
        await asyncio.sleep(pause_seconds)
    budget["attempted"] += 1
    start = perf_counter()
    response = await client.aio.models.generate_content(**request)
    duration_ms = (perf_counter() - start) * 1000
    usage = _usage(response.usage_metadata)
    return response, {
        "duration_ms": duration_ms,
        "reported_model_version": response.model_version,
        "usage": usage.__dict__
        if hasattr(usage, "__dict__")
        else {
            "prompt_tokens": usage.prompt_tokens,
            "candidate_tokens": usage.candidate_tokens,
            "thinking_tokens": usage.thinking_tokens,
        },
    }


def candidate_action(case, condition, parsed):
    expected = condition["expected_action"]
    if parsed["action"] == "search":
        return "search"
    if expected == "final" and parsed["action"] == "answered":
        return "final"
    if expected == "conflict" and parsed["action"] == "conflicting_evidence":
        return "conflict"
    if expected == "search" and parsed["action"] == "insufficient_evidence":
        return "insufficient_evidence"
    return parsed["action"]


def make_row(
    case,
    condition_name,
    arm,
    condition,
    parsed,
    metrics,
    *,
    requested_query,
    initial_ok,
    context_view="required_sentence",
):
    expected = condition["expected_action"]
    action = parsed["action"]
    if expected in {"final", "conflict"}:
        correct_decision = action == ("answered" if expected == "final" else "conflicting_evidence")
    else:
        correct_decision = action == "search"
    unsupported_final = action == "answered" and expected in {"search", "conflict"}
    final_answer_correct = None
    if action == "answered" and case.get("expected_answer"):
        final_answer_correct = expected_value_appears(parsed.get("answer"), case["expected_answer"])
    return {
        "case_id": case["id"],
        "category": case["category"],
        "condition": condition_name,
        "arm": arm,
        "context_view": context_view,
        "expected_action": expected,
        "observed_action": action,
        "proposed_search_query": parsed.get("search_query"),
        "proposed_unresolved_fact": parsed.get("unresolved_fact"),
        "unresolved_fact_matches_missing_annotation": query_matches_missing_fact(
            parsed.get("unresolved_fact"),
            condition.get("missing_evidence", []),
            condition.get("missing_fact_targets", []),
        ),
        "unresolved_fact_matches_supported_annotation": query_matches_annotated_fact(
            parsed.get("unresolved_fact"),
            condition.get("evidence", []),
            condition.get("supported_fact_targets", []),
        ),
        "unresolved_fact_classification": classify_unresolved_fact(condition, action, parsed.get("unresolved_fact")),
        "missing_evidence_targets": [item["title"] for item in condition.get("missing_evidence", [])],
        "search_query_matches_missing_target_title": query_matches_missing_target(
            parsed.get("search_query"), condition.get("missing_evidence", [])
        ),
        "missing_fact_targets": condition.get("missing_fact_targets", []),
        "search_query_matches_missing_fact_entity_and_relation": query_matches_missing_fact(
            parsed.get("search_query"),
            condition.get("missing_evidence", []),
            condition.get("missing_fact_targets", []),
        ),
        "search_target_classification": classify_search_target(parsed.get("search_query"), case, condition, action),
        "correct_decision": correct_decision,
        "answer": parsed.get("answer"),
        "expected_answer": case.get("expected_answer"),
        "reference_value_present_in_answer_text": final_answer_correct,
        "unsupported_final_answer": unsupported_final,
        "citation_ids": parsed.get("citations", []),
        "conflict_claims": parsed.get("conflicts", []),
        "invalid_citation_ids": parsed.get("invalid_citation_ids", []),
        "generation": metrics,
        "initial_native_search_available": initial_ok,
        "first_requested_query": requested_query,
        "error": parsed.get("error"),
    }


def review_coverage_audit(audit, condition):
    expected_status = {
        "final": "sufficient",
        "search": "insufficient",
        "conflict": "conflicting",
    }.get(condition["expected_action"])
    evidence = condition["evidence"]
    available_ids = {f"E{index}" for index in range(1, len(evidence) + 1)}
    invalid_ids = set()
    unsupported_quotes = 0
    for requirement in audit["requirements"]:
        evidence_ids = requirement.get("evidence_ids", [])
        invalid_ids.update(set(evidence_ids) - available_ids)
        quotes = requirement.get("quotes", [])
        if requirement.get("status") == "supported" and (not evidence_ids or not quotes):
            unsupported_quotes += 1
        cited_evidence = [item for index, item in enumerate(evidence, start=1) if f"E{index}" in evidence_ids]
        for quote in quotes:
            if not any(quote and quote in item["text"] for item in cited_evidence):
                unsupported_quotes += 1
    return {
        "overall_status": audit["overall_status"],
        "correct_for_fixture": audit["overall_status"] == expected_status,
        "invalid_evidence_ids": sorted(invalid_ids),
        "unsupported_quotes": unsupported_quotes,
    }


async def run(args, *, client_factory=genai.Client):
    suite_path = Path(getattr(args, "suite", SUITE_PATH))
    corpus_path = Path(getattr(args, "corpus", CORPUS_PATH))
    cases = load_cases(suite_path, corpus_path)
    if args.start_question_index < 0 or args.start_question_index >= len(cases["cases"]):
        raise ValueError("Start question index must be within the suite")
    stop = len(cases["cases"]) if args.limit_questions is None else args.start_question_index + args.limit_questions
    if stop > len(cases["cases"]):
        raise ValueError("Question slice exceeds the suite size")
    cases["cases"] = cases["cases"][args.start_question_index : stop]
    if args.case_ids:
        requested_ids = set(args.case_ids)
        cases["cases"] = [case for case in cases["cases"] if case["id"] in requested_ids]
        found_ids = {case["id"] for case in cases["cases"]}
        if found_ids != requested_ids:
            missing_ids = sorted(requested_ids - found_ids)
            raise ValueError(f"Unknown or out-of-slice question IDs: {missing_ids}")
        if not cases["cases"]:
            raise ValueError("Question selection is empty")
    if args.only_condition:
        for case in cases["cases"]:
            if args.only_condition not in case["conditions"]:
                raise ValueError(f"Condition {args.only_condition} is unavailable for {case['id']}")
            case["conditions"] = {args.only_condition: case["conditions"][args.only_condition]}
    if not args.execute:
        plan = make_plan(args, cases, mode="preview")
        args.output.mkdir(parents=True, exist_ok=False)
        save_json(args.output / "plan.json", plan)
        return {
            "output": str(args.output),
            "mode": "preview",
            "planned_generation_requests": plan["planned_generation_requests"],
        }
    if not settings.gemini_api_key:
        raise ValueError("Execution requires configured GEMINI_API_KEY")
    if not args.max_generation_requests:
        raise ValueError("Execution requires --max-generation-requests")
    plan = make_plan(args, cases, mode="execute")
    if plan["planned_generation_requests"] > args.max_generation_requests:
        raise ValueError("Planned calls exceed the explicit generation-request cap")
    args.output.mkdir(parents=True, exist_ok=False)
    save_json(args.output / "plan.json", plan)
    if args.candidate_policy == GOAL_COMMITMENT_POLICY and args.only_arm != "production":
        return await run_goal_commitment_paired(args, cases, plan, client_factory=client_factory)
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
    budget = {"attempted": 0}
    rows = []
    initial_metrics = []
    outcomes = []
    stop_run = False
    try:
        for case in cases["cases"]:
            initial_mode = {
                "VALIDATED": types.FunctionCallingConfigMode.VALIDATED,
                "AUTO": types.FunctionCallingConfigMode.AUTO,
                "DEFAULT": None,
            }[args.initial_mode]
            initial_request = make_initial_request(
                model,
                case["question"],
                function_calling_mode=initial_mode,
                response_schema=args.initial_schema == "production_json",
            )
            try:
                first_response, first_metrics = await generate(
                    client, initial_request, budget, args.max_generation_requests, args.pause_seconds
                )
            except Exception as error:
                outcomes.append(
                    {
                        "case_id": case["id"],
                        "phase": "initial",
                        "error_type": type(error).__name__,
                        "provider_code": getattr(error, "code", None) or getattr(error, "status_code", None),
                        "error_message": str(error)[:500],
                    }
                )
                stop_run = True
                break
            initial_metrics.append(first_metrics)
            native, stop_reason = action_from_initial(first_response, model=model)
            initial_row = {
                "case_id": case["id"],
                "generation": first_metrics,
                "has_native_search_call": native is not None,
                "stop_reason": stop_reason,
                "response_shape": summarize_response_shape(first_response),
            }
            with (args.output / "initial_requests.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(redact(initial_row), ensure_ascii=False) + "\n")
            if native is None:
                outcomes.append({"case_id": case["id"], "phase": "initial", "stop_reason": stop_reason})
                continue
            initial_content, call = native
            conditions = case["conditions"]
            for condition_name, condition in conditions.items():
                for context_view in context_views(args):
                    decision_arms = evaluation_arms(args)
                    coverage_audit = None
                    coverage_audit_metrics = None
                    if any(policy in decision_arms for policy in EVIDENCE_COVERAGE_POLICIES):
                        audit_request = make_evidence_coverage_request(
                            model,
                            case["question"],
                            condition,
                            call.args["query"],
                            context_view=context_view,
                        )
                        try:
                            audit_response, coverage_audit_metrics = await generate(
                                client, audit_request, budget, args.max_generation_requests, args.pause_seconds
                            )
                            coverage_audit = parse_evidence_coverage_response(audit_response)
                        except Exception as error:
                            outcomes.append(
                                {
                                    "case_id": case["id"],
                                    "condition": condition_name,
                                    "context_view": context_view,
                                    "phase": "evidence_coverage_audit",
                                    "error_type": type(error).__name__,
                                    "provider_code": getattr(error, "code", None)
                                    or getattr(error, "status_code", None),
                                    "error_message": str(error)[:500],
                                }
                            )
                            stop_run = True
                            break
                        with (args.output / "coverage_audits.jsonl").open("a", encoding="utf-8") as stream:
                            stream.write(
                                json.dumps(
                                    redact(
                                        {
                                            "case_id": case["id"],
                                            "condition": condition_name,
                                            "context_view": context_view,
                                            "audit": coverage_audit,
                                            "generation": coverage_audit_metrics,
                                        }
                                    ),
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )
                    if stop_run:
                        break
                    order = (
                        decision_arms
                        if (len(rows) // max(1, len(decision_arms))) % 2 == 0
                        else tuple(reversed(decision_arms))
                    )
                    for arm in order:
                        request = make_decision_request(
                            model,
                            case["question"],
                            initial_content,
                            call,
                            condition,
                            conditional=(arm == "conditional_tool_description"),
                            identity_guidance=(arm == IDENTITY_POLICY),
                            gap_commitment=(arm == GAP_COMMITMENT_POLICY),
                            evidence_text_reinforcement=(arm == EVIDENCE_TEXT_POLICY),
                            temperature=0.0 if arm == TEMPERATURE_POLICY else None,
                            evidence_coverage_audit=(coverage_audit if arm in EVIDENCE_COVERAGE_POLICIES else None),
                            embed_coverage_audit=arm == EVIDENCE_COVERAGE_EMBEDDED_POLICY,
                            answer_first=arm == ANSWER_FIRST_POLICY,
                            explicit_finalize=arm == EXPLICIT_FINALIZE_POLICY,
                            explicit_finalize_validated=arm == EXPLICIT_FINALIZE_VALIDATED_POLICY,
                            response_schema=args.decision_schema == "production_json",
                            context_view=context_view,
                            function_calling_mode=(
                                {
                                    "VALIDATED": types.FunctionCallingConfigMode.VALIDATED,
                                    "AUTO": types.FunctionCallingConfigMode.AUTO,
                                    "DEFAULT": None,
                                }[args.decision_mode]
                            ),
                        )
                        try:
                            response, metrics = await generate(
                                client, request, budget, args.max_generation_requests, args.pause_seconds
                            )
                        except Exception as error:
                            outcomes.append(
                                {
                                    "case_id": case["id"],
                                    "condition": condition_name,
                                    "arm": arm,
                                    "context_view": context_view,
                                    "phase": "decision",
                                    "error_type": type(error).__name__,
                                    "provider_code": getattr(error, "code", None)
                                    or getattr(error, "status_code", None),
                                    "error_message": str(error)[:500],
                                }
                            )
                            stop_run = True
                            break
                        evidence_ids = [f"E{i}" for i in range(1, len(condition["evidence"]) + 1)]
                        parsed = parse_decision(
                            response,
                            evidence_ids,
                            gap_commitment=(arm == GAP_COMMITMENT_POLICY),
                            explicit_finalize=(arm == EXPLICIT_FINALIZE_POLICY),
                            explicit_finalize_validated=(arm == EXPLICIT_FINALIZE_VALIDATED_POLICY),
                            model=model,
                        )
                        row = make_row(
                            case,
                            condition_name,
                            arm,
                            condition,
                            parsed,
                            metrics,
                            requested_query=call.args["query"],
                            initial_ok=True,
                            context_view=context_view,
                        )
                        row["trial_label"] = args.trial_label
                        row["response_shape"] = summarize_response_shape(response)
                        if arm in EVIDENCE_COVERAGE_POLICIES and coverage_audit is not None:
                            row["coverage_audit"] = coverage_audit
                            row["coverage_audit_review"] = review_coverage_audit(coverage_audit, condition)
                            row["coverage_audit_generation"] = coverage_audit_metrics
                        rows.append(row)
                        with (args.output / "decisions.jsonl").open("a", encoding="utf-8") as stream:
                            stream.write(json.dumps(redact(row), ensure_ascii=False) + "\n")
                    if budget["attempted"] >= args.max_generation_requests or stop_run:
                        break
                if budget["attempted"] >= args.max_generation_requests or stop_run:
                    break
            if budget["attempted"] >= args.max_generation_requests:
                break
            if stop_run:
                break
    finally:
        await client.aio.aclose()
    summary = summarize(rows, initial_metrics, outcomes, budget)
    if args.compare_context_views:
        summary["context_views"] = summarize_context_views(rows)
        summary["unnecessary_search_reduction_fraction"] = None
    save_json(args.output / "summary.json", summary)
    return {"output": str(args.output), "mode": "execute", **summary["accounting"]}


async def run_goal_commitment_paired(args, cases, plan, *, client_factory):
    """Compare production history with an initial-call checklist, retaining separate native histories."""
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
    arms = evaluation_arms(args)
    budget = {"attempted": 0}
    rows = []
    initial_metrics = []
    initial_metrics_by_arm = {arm: [] for arm in arms}
    outcomes = []
    stop_run = False
    try:
        for case_index, case in enumerate(cases["cases"]):
            initial_by_arm = {}
            initial_order = arms if case_index % 2 == 0 else tuple(reversed(arms))
            for arm in initial_order:
                goal_commitment = arm == GOAL_COMMITMENT_POLICY
                request = make_initial_request(model, case["question"], goal_commitment=goal_commitment)
                try:
                    first_response, first_metrics = await generate(
                        client, request, budget, args.max_generation_requests, args.pause_seconds
                    )
                except Exception as error:
                    outcomes.append(
                        {
                            "case_id": case["id"],
                            "arm": arm,
                            "phase": "initial",
                            "error_type": type(error).__name__,
                            "provider_code": getattr(error, "code", None) or getattr(error, "status_code", None),
                            "error_message": str(error)[:500],
                        }
                    )
                    stop_run = True
                    break
                initial_metrics.append(first_metrics)
                initial_metrics_by_arm[arm].append((case["id"], first_metrics))
                native, stop_reason = action_from_initial(first_response, model=model, goal_commitment=goal_commitment)
                initial_row = {
                    "case_id": case["id"],
                    "arm": arm,
                    "generation": first_metrics,
                    "has_native_search_call": native is not None,
                    "stop_reason": stop_reason,
                }
                if native is not None and goal_commitment:
                    initial_row["required_facts"] = native[1].args["required_facts"]
                with (args.output / "initial_requests.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(redact(initial_row), ensure_ascii=False) + "\n")
                if native is None:
                    outcomes.append({"case_id": case["id"], "arm": arm, "phase": "initial", "stop_reason": stop_reason})
                else:
                    initial_by_arm[arm] = native
            if stop_run:
                break

            for condition_name, condition in case["conditions"].items():
                for context_view in context_views(args):
                    decision_arms = [arm for arm in arms if arm in initial_by_arm]
                    order = (
                        decision_arms
                        if (len(rows) // max(1, len(decision_arms))) % 2 == 0
                        else tuple(reversed(decision_arms))
                    )
                    for arm in order:
                        initial_content, call = initial_by_arm[arm]
                        goal_commitment = arm == GOAL_COMMITMENT_POLICY
                        request = make_decision_request(
                            model,
                            case["question"],
                            initial_content,
                            call,
                            condition,
                            conditional=(arm == "conditional_tool_description"),
                            identity_guidance=(arm == IDENTITY_POLICY),
                            gap_commitment=(arm == GAP_COMMITMENT_POLICY),
                            goal_commitment=goal_commitment,
                            evidence_text_reinforcement=(arm == EVIDENCE_TEXT_POLICY),
                            temperature=0.0 if arm == TEMPERATURE_POLICY else None,
                            context_view=context_view,
                            function_calling_mode=(
                                types.FunctionCallingConfigMode.AUTO
                                if args.decision_mode == "AUTO"
                                else types.FunctionCallingConfigMode.VALIDATED
                            ),
                        )
                        try:
                            response, metrics = await generate(
                                client, request, budget, args.max_generation_requests, args.pause_seconds
                            )
                        except Exception as error:
                            outcomes.append(
                                {
                                    "case_id": case["id"],
                                    "condition": condition_name,
                                    "arm": arm,
                                    "context_view": context_view,
                                    "phase": "decision",
                                    "error_type": type(error).__name__,
                                    "provider_code": getattr(error, "code", None)
                                    or getattr(error, "status_code", None),
                                    "error_message": str(error)[:500],
                                }
                            )
                            stop_run = True
                            break
                        evidence_ids = [f"E{i}" for i in range(1, len(condition["evidence"]) + 1)]
                        parsed = parse_decision(
                            response,
                            evidence_ids,
                            gap_commitment=(arm == GAP_COMMITMENT_POLICY),
                            goal_commitment=goal_commitment,
                            model=model,
                        )
                        row = make_row(
                            case,
                            condition_name,
                            arm,
                            condition,
                            parsed,
                            metrics,
                            requested_query=call.args["query"],
                            initial_ok=True,
                            context_view=context_view,
                        )
                        if goal_commitment:
                            row["initial_required_facts"] = call.args["required_facts"]
                        row["trial_label"] = args.trial_label
                        row["response_shape"] = summarize_response_shape(response)
                        rows.append(row)
                        with (args.output / "decisions.jsonl").open("a", encoding="utf-8") as stream:
                            stream.write(json.dumps(redact(row), ensure_ascii=False) + "\n")
                    if budget["attempted"] >= args.max_generation_requests or stop_run:
                        break
                if budget["attempted"] >= args.max_generation_requests or stop_run:
                    break
            if budget["attempted"] >= args.max_generation_requests or stop_run:
                break
    finally:
        await client.aio.aclose()
    summary = summarize(rows, initial_metrics, outcomes, budget)
    for arm in arms:
        selected = [row for row in rows if row["arm"] == arm]
        initial_for_arm = initial_metrics_by_arm[arm]
        case_count = len({row["case_id"] for row in selected})
        initial_latency = sum(metric["duration_ms"] for _, metric in initial_for_arm)
        initial_prompt = sum(metric["usage"].get("prompt_tokens") or 0 for _, metric in initial_for_arm)
        initial_candidate = sum(metric["usage"].get("candidate_tokens") or 0 for _, metric in initial_for_arm)
        decision_latency = sum(row["generation"]["duration_ms"] for row in selected)
        decision_prompt = sum(row["generation"]["usage"].get("prompt_tokens") or 0 for row in selected)
        decision_candidate = sum(row["generation"]["usage"].get("candidate_tokens") or 0 for row in selected)
        arm_summary = summary["arms"][arm]
        arm_summary["prompt_tokens_including_shared_initial"] = initial_prompt + decision_prompt
        arm_summary["candidate_tokens_including_shared_initial"] = initial_candidate + decision_candidate
        arm_summary["mean_total_prompt_tokens_per_investigation"] = ratio(initial_prompt + decision_prompt, case_count)
        arm_summary["mean_total_candidate_tokens_per_investigation"] = ratio(
            initial_candidate + decision_candidate, case_count
        )
        arm_summary["mean_total_generation_latency_ms_per_investigation"] = ratio(
            initial_latency + decision_latency, case_count
        )
        arm_summary["generation_requests_per_investigation_including_shared_initial"] = ratio(
            len(initial_for_arm) + len(selected), case_count
        )
        arm_summary["search_calls_per_investigation_including_initial"] = ratio(
            sum(1 for _, _ in initial_for_arm) + sum(row["observed_action"] == "search" for row in selected),
            case_count,
        )
    summary["scope_note"] = (
        "The production and goal-checklist arms make separate initial requests and preserve their own native call "
        "history. Each arm makes one initial request per question and one decision request per selected evidence "
        "condition. The checklist is model-generated before evidence is returned; reference answers are never sent. "
        "Search proposals are never executed, and all query-target labels are post-hoc annotation-based proxies."
    )
    save_json(args.output / "summary.json", summary)
    return {"output": str(args.output), "mode": "execute", **summary["accounting"]}


def make_plan(args, cases, *, mode):
    suite_path = Path(getattr(args, "suite", SUITE_PATH))
    corpus_path = Path(getattr(args, "corpus", CORPUS_PATH))
    initial_mode_label = (
        f"initial={getattr(args, 'initial_mode', 'VALIDATED')}/{getattr(args, 'initial_schema', 'production_json')}"
    )
    decision_schema = getattr(args, "decision_schema", "production_json")
    planned_candidate_policy = (
        args.only_arm if args.only_arm and args.only_arm != "production" else args.candidate_policy
    )
    candidate_decision_mode = (
        "not-run"
        if args.only_arm == "production"
        else (
            "DEFAULT"
            if args.decision_mode == "DEFAULT"
            else (
                "ANY"
                if planned_candidate_policy == EXPLICIT_FINALIZE_POLICY
                else (
                    "VALIDATED"
                    if planned_candidate_policy == EXPLICIT_FINALIZE_VALIDATED_POLICY
                    else args.decision_mode
                )
            )
        )
    )
    candidate_decision_schema = "none" if planned_candidate_policy == EXPLICIT_FINALIZE_POLICY else decision_schema
    views = context_views(args)
    arms = evaluation_arms(args)
    initial_calls_per_case = 2 if args.candidate_policy == GOAL_COMMITMENT_POLICY and args.only_arm is None else 1
    planned = len(cases["cases"]) * initial_calls_per_case + sum(
        len(case["conditions"]) for case in cases["cases"]
    ) * len(views) * len(arms)
    if any(policy in arms for policy in EVIDENCE_COVERAGE_POLICIES):
        planned += sum(len(case["conditions"]) for case in cases["cases"]) * len(views)
    return {
        "schema_version": 1,
        "mode": mode,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "corpus_id": cases["corpus_id"],
        "suite_sha256": sha256(suite_path.read_bytes()).hexdigest(),
        "suite_path": str(suite_path),
        "corpus_path": str(corpus_path),
        "corpus_sha256": sha256(corpus_path.read_bytes()).hexdigest(),
        "question_count": len(cases["cases"]),
        "included_case_ids": [case["id"] for case in cases["cases"]],
        "start_question_index_zero_based": args.start_question_index,
        "question_categories": sorted({case["category"] for case in cases["cases"]}),
        "evidence_context_views": list(views),
        "condition_filter": args.only_condition,
        "baseline_arm": "production tool declaration and exact production system instruction",
        "candidate_arm": None
        if args.compare_context_views or args.only_arm == "production"
        else (
            "decision turn exposes search_evidence and finalize_investigation in VALIDATED mode with the production final JSON schema; either function selection or structured final text remains possible"
            if planned_candidate_policy == EXPLICIT_FINALIZE_VALIDATED_POLICY
            else (
                "decision turn exposes search_evidence and finalize_investigation as competing native functions; "
                + candidate_decision_mode
                + "/"
                + candidate_decision_schema
                + " controls selection, initial request/history/evidence otherwise unchanged"
                if planned_candidate_policy == EXPLICIT_FINALIZE_POLICY
                else (
                    "decision-only answer-first system instruction; initial request, tool schema, history and evidence unchanged"
                    if planned_candidate_policy == ANSWER_FIRST_POLICY
                    else (
                        "initial search-call schema includes required facts; separate baseline/candidate native histories"
                        if planned_candidate_policy == GOAL_COMMITMENT_POLICY
                        else (
                            "decision history adds a plain-text duplicate of the exact native tool evidence response"
                            if planned_candidate_policy == EVIDENCE_TEXT_POLICY
                            else (
                                "explicit temperature=0.0 on candidate decision requests only; production requests use model default"
                                if planned_candidate_policy == TEMPERATURE_POLICY
                                else (
                                    "tool-free evidence coverage report embedded in the native function response"
                                    if planned_candidate_policy == EVIDENCE_COVERAGE_EMBEDDED_POLICY
                                    else (
                                        "tool-free evidence coverage audit supplied as data to the native-tool decision turn"
                                        if planned_candidate_policy == EVIDENCE_COVERAGE_POLICY
                                        else f"one-variable decision-policy change: {planned_candidate_policy}"
                                    )
                                )
                            )
                        )
                    )
                )
            )
        ),
        "decision_arms": list(arms),
        "native_tool_mode": (
            "search_evidence available on every request; "
            + initial_mode_label
            + ", production decision="
            + args.decision_mode
            + "/"
            + decision_schema
            + ", candidate decision="
            + candidate_decision_mode
            + "/"
            + candidate_decision_schema
        ),
        "initial_native_search_calls_per_question": initial_calls_per_case,
        "planned_generation_requests": planned,
        "automatic_function_execution": False,
        "retrieval_calls": 0,
        "database_queries": 0,
        "retries": 0,
        "pause_seconds": args.pause_seconds,
        "model": args.model or settings.gemini_generation_model,
        "max_output_tokens": 1024,
        "trial_count": 1,
        "trial_label": args.trial_label,
        "limits": (
            "Baseline and candidate make separate initial native search proposals; each arm then makes one or two "
            "evidence sufficiency decisions. No returned search request is executed."
            if initial_calls_per_case == 2
            else "One initial native search proposal per question, then one or two paired evidence sufficiency decisions; no returned search request is executed."
        ),
        "reference_answers_sent_to_model": False,
        "fixture_evidence": "Pinned 2Wiki corpus passages; required_sentence presents only the annotated supporting span, while full_source_passage presents the entire source passage. Incomplete conditions omit one required evidence record.",
    }


def summarize(rows, initial_metrics, outcomes, budget):
    grouped = {}
    initial_latency = sum(item["duration_ms"] for item in initial_metrics)
    initial_prompt_tokens = sum((item["usage"].get("prompt_tokens") or 0) for item in initial_metrics)
    initial_candidate_tokens = sum((item["usage"].get("candidate_tokens") or 0) for item in initial_metrics)
    initial_thinking_tokens = sum((item["usage"].get("thinking_tokens") or 0) for item in initial_metrics)
    for arm in ALL_ARMS:
        selected = [row for row in rows if row["arm"] == arm]
        sufficient = [row for row in selected if row["expected_action"] in {"final", "conflict"}]
        insufficient = [row for row in selected if row["expected_action"] == "search"]
        auxiliary_generations = [
            row["coverage_audit_generation"] for row in selected if "coverage_audit_generation" in row
        ]
        auxiliary_prompt_tokens = sum(item["usage"].get("prompt_tokens") or 0 for item in auxiliary_generations)
        auxiliary_candidate_tokens = sum(item["usage"].get("candidate_tokens") or 0 for item in auxiliary_generations)
        grouped[arm] = {
            "n": len(selected),
            "sufficient_n": len(sufficient),
            "final_or_conflict_when_sufficient": sum(row["correct_decision"] for row in sufficient),
            "sufficient_decision_accuracy": ratio(sum(row["correct_decision"] for row in sufficient), len(sufficient)),
            "insufficient_n": len(insufficient),
            "correct_search_requests_when_insufficient": sum(row["correct_decision"] for row in insufficient),
            "searches_matching_missing_entity_and_relation": sum(
                row["observed_action"] == "search"
                and row["expected_action"] == "search"
                and row["search_query_matches_missing_fact_entity_and_relation"] is True
                for row in insufficient
            ),
            "missing_fact_query_relevance_rate": ratio(
                sum(
                    row["observed_action"] == "search"
                    and row["search_query_matches_missing_fact_entity_and_relation"] is True
                    for row in insufficient
                ),
                len(insufficient),
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
            "unresolved_fact_classifications": {
                classification: sum(row["unresolved_fact_classification"] == classification for row in selected)
                for classification in (
                    "names_missing_fact",
                    "does_not_name_missing_fact",
                    "claims_supported_fact_is_missing",
                    "unverified_gap_claim",
                )
            },
            "insufficient_search_request_accuracy": ratio(
                sum(row["correct_decision"] for row in insufficient), len(insufficient)
            ),
            "unnecessary_searches": sum(row["observed_action"] == "search" for row in sufficient),
            "unsupported_final_answers": sum(row["unsupported_final_answer"] for row in insufficient),
            "invalid_evidence_citation_ids": sum(bool(row["invalid_citation_ids"]) for row in selected),
            "reference_value_present_in_answer_text_count": sum(
                row["reference_value_present_in_answer_text"] is True for row in selected
            ),
            "answered_n": sum(row["reference_value_present_in_answer_text"] is not None for row in selected),
            "evidence_coverage_audit_count": len(auxiliary_generations),
            "evidence_coverage_audit_status_accuracy": ratio(
                sum(
                    row["coverage_audit_review"]["correct_for_fixture"]
                    for row in selected
                    if "coverage_audit_review" in row
                ),
                len(auxiliary_generations),
            ),
            "evidence_coverage_audit_invalid_ids": sum(
                len(row["coverage_audit_review"]["invalid_evidence_ids"])
                for row in selected
                if "coverage_audit_review" in row
            ),
            "evidence_coverage_audit_unsupported_quotes": sum(
                row["coverage_audit_review"]["unsupported_quotes"] for row in selected if "coverage_audit_review" in row
            ),
            "mean_evidence_coverage_audit_latency_ms": mean([item["duration_ms"] for item in auxiliary_generations]),
            "generation_requests": len(selected) + len(auxiliary_generations),
            "mean_generation_latency_ms": mean([row["generation"]["duration_ms"] for row in selected]),
            "generation_requests_per_investigation_including_shared_initial": ratio(
                2 * len(selected) + len(auxiliary_generations), len(selected)
            ),
            "mean_total_generation_latency_ms_per_investigation": ratio(
                len(selected) * (initial_latency / max(1, len(initial_metrics)))
                + sum(row["generation"]["duration_ms"] for row in selected)
                + sum(item["duration_ms"] for item in auxiliary_generations),
                len(selected),
            ),
            "search_calls_per_investigation_including_initial": ratio(
                len(selected) + sum(row["observed_action"] == "search" for row in selected), len(selected)
            ),
            "native_tool_available_decisions": len(selected),
            "native_tool_availability_rate": 1.0 if selected else None,
            "prompt_tokens_including_shared_initial": initial_prompt_tokens
            + auxiliary_prompt_tokens
            + sum((row["generation"]["usage"].get("prompt_tokens") or 0) for row in selected),
            "candidate_tokens_including_shared_initial": initial_candidate_tokens
            + auxiliary_candidate_tokens
            + sum((row["generation"]["usage"].get("candidate_tokens") or 0) for row in selected),
            "mean_total_prompt_tokens_per_investigation": ratio(initial_prompt_tokens, max(1, len(initial_metrics)))
            + ratio(
                sum((row["generation"]["usage"].get("prompt_tokens") or 0) for row in selected),
                max(1, len(selected)),
            )
            + ratio(auxiliary_prompt_tokens, max(1, len(selected))),
            "mean_total_candidate_tokens_per_investigation": ratio(
                initial_candidate_tokens, max(1, len(initial_metrics))
            )
            + ratio(
                sum((row["generation"]["usage"].get("candidate_tokens") or 0) for row in selected),
                max(1, len(selected)),
            )
            + ratio(auxiliary_candidate_tokens, max(1, len(selected))),
            "reported_thinking_tokens": sum(
                (row["generation"]["usage"].get("thinking_tokens") or 0) for row in selected
            )
            + sum((item["usage"].get("thinking_tokens") or 0) for item in auxiliary_generations)
            + ratio(initial_thinking_tokens, max(1, len(initial_metrics))) * len(selected),
        }
    baseline_searches = grouped["production"]["unnecessary_searches"]
    candidate_policy = next(
        (
            policy
            for policy in (
                EVIDENCE_TEXT_POLICY,
                EVIDENCE_COVERAGE_POLICY,
                EVIDENCE_COVERAGE_EMBEDDED_POLICY,
                GOAL_COMMITMENT_POLICY,
                GAP_COMMITMENT_POLICY,
                IDENTITY_POLICY,
                "conditional_tool_description",
            )
            if grouped[policy]["n"]
        ),
        "conditional_tool_description",
    )
    candidate_searches = grouped[candidate_policy]["unnecessary_searches"]
    reduction = (
        1 - candidate_searches / baseline_searches if baseline_searches and grouped[candidate_policy]["n"] else None
    )
    return {
        "accounting": {
            "generation_requests": budget["attempted"],
            "initial_request_count": len(initial_metrics),
            "decision_row_count": len(rows),
            "outcome_count": len(outcomes),
            "initial_mean_latency_ms": mean([item["duration_ms"] for item in initial_metrics]),
            "actual_prompt_tokens": initial_prompt_tokens
            + sum((row["generation"]["usage"].get("prompt_tokens") or 0) for row in rows)
            + sum(row.get("coverage_audit_generation", {}).get("usage", {}).get("prompt_tokens") or 0 for row in rows),
            "actual_candidate_tokens": initial_candidate_tokens
            + sum((row["generation"]["usage"].get("candidate_tokens") or 0) for row in rows)
            + sum(
                row.get("coverage_audit_generation", {}).get("usage", {}).get("candidate_tokens") or 0 for row in rows
            ),
        },
        "arms": grouped,
        "unnecessary_search_reduction_fraction": reduction,
        "outcomes": outcomes,
        "decision_rows": rows,
        "scope_note": "One stochastic trial per case. Reference-value-in-answer-text checks are post-hoc string matches, not factual judgments. Citation ID checks establish identifier availability, not claim support. The initial search call is shared by paired arms and allocated once per condition for per-investigation estimates. Evidence coverage audit calls are additional candidate-only generations and are included in candidate latency and token accounting.",
    }


def context_views(args):
    return (
        ("required_sentence", "sentence_with_neighbors", "full_source_passage")
        if args.compare_context_views
        else (args.context_view,)
    )


def evaluation_arms(args):
    if args.only_arm:
        return (args.only_arm,)
    return ("production",) if args.compare_context_views else ("production", args.candidate_policy)


def summarize_context_views(rows):
    result = {}
    for view in ("required_sentence", "sentence_with_neighbors", "full_source_passage"):
        selected = [row for row in rows if row["context_view"] == view]
        result[view] = {
            "n": len(selected),
            "final_when_sufficient": sum(
                row["correct_decision"] for row in selected if row["expected_action"] == "final"
            ),
            "sufficient_n": sum(row["expected_action"] == "final" for row in selected),
            "sufficient_decision_accuracy": ratio(
                sum(row["correct_decision"] for row in selected if row["expected_action"] == "final"),
                sum(row["expected_action"] == "final" for row in selected),
            ),
            "unnecessary_searches": sum(
                row["observed_action"] == "search" and row["expected_action"] == "final" for row in selected
            ),
            "missing_fact_query_target_matches": sum(
                row["search_query_matches_missing_fact_entity_and_relation"] is True
                for row in selected
                if row["expected_action"] == "search"
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
            "reference_values_present": sum(row["reference_value_present_in_answer_text"] is True for row in selected),
            "invalid_citation_ids": sum(bool(row["invalid_citation_ids"]) for row in selected),
            "mean_decision_latency_ms": mean([row["generation"]["duration_ms"] for row in selected]),
            "prompt_tokens": sum((row["generation"]["usage"].get("prompt_tokens") or 0) for row in selected),
            "candidate_tokens": sum((row["generation"]["usage"].get("candidate_tokens") or 0) for row in selected),
        }
    return result


def ratio(value, denominator):
    return value / denominator if denominator else None


def mean(values):
    return sum(values) / len(values) if values else None


def normalized_value(value):
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    unaccented = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^\w]+", "", unaccented, flags=re.UNICODE)


def normalized_tokens(value, *, minimum):
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    unaccented = "".join(char for char in decomposed if not unicodedata.combining(char))
    return set(re.findall(rf"[a-z0-9]{{{minimum},}}", unaccented))


def query_matches_missing_target(query, missing_evidence):
    """Conservative entity-title overlap check; not a semantic relevance judgment."""
    if not isinstance(query, str) or not missing_evidence:
        return None
    query_words = normalized_tokens(query, minimum=4)
    generic = {"film", "movie", "director", "born", "father", "mother", "wife", "husband"}
    query_words -= generic
    for item in missing_evidence:
        target_words = normalized_tokens(item["title"], minimum=4) - generic
        if query_words & target_words:
            return True
    return False


def triples_in_evidence(annotation_triples, missing_evidence):
    """Associate missing annotated facts with passages by subject/object mentions."""
    targets = []
    for evidence in missing_evidence:
        passage = normalized_value(evidence["full_text"])
        for triple in annotation_triples:
            if len(triple) != 3:
                continue
            subject, relation, object_ = triple
            if any(normalized_value(entity) in passage for entity in (subject, object_) if normalized_value(entity)):
                record = {"subject": subject, "relation": relation, "object": object_}
                if record not in targets:
                    targets.append(record)
    return targets


def query_matches_missing_fact(query, missing_evidence, targets):
    """Require both missing-entity and relation cue overlap; lexical proxy only."""
    return query_matches_annotated_fact(query, missing_evidence, targets)


def query_matches_annotated_fact(query, evidence, targets):
    """Match at least one evidence title and annotated relation in the query."""
    if not isinstance(query, str) or not evidence or not targets:
        return None
    if not query_matches_missing_target(query, evidence):
        return False
    query_words = normalized_tokens(query, minimum=3)
    relation_aliases = {
        "date of birth": {"birth", "born", "birthdate"},
        "date of death": {"death", "died", "deathdate"},
        "place of birth": {"birth", "born", "birthplace"},
        "place of death": {"death", "died", "deathplace"},
        "award received": {"award", "awards", "won"},
        "mother": {"mother", "parents"},
        "father": {"father", "parents"},
        "country of citizenship": {"nationality", "citizenship", "country"},
    }
    for target in targets:
        relation = target["relation"].casefold()
        cues = relation_aliases.get(
            relation,
            normalized_tokens(relation, minimum=3) - {"the", "and", "for", "from", "with"},
        )
        if query_words & cues:
            return True
    return False


def classify_search_target(query, case, condition, observed_action):
    if observed_action != "search":
        return None
    expected = condition["expected_action"]
    if expected == "final":
        matched = query_matches_annotated_fact(
            query, condition["evidence"], condition.get("supported_fact_targets", [])
        )
        if matched is None:
            return "unscored"
        return "redundant_to_supported_fact" if matched else "unrelated_to_supported_facts"
    if expected == "search":
        matched = query_matches_missing_fact(
            query, condition.get("missing_evidence", []), condition.get("missing_fact_targets", [])
        )
        if matched is None:
            return "unscored"
        return "targets_missing_fact" if matched else "does_not_target_missing_fact"
    return None


def classify_unresolved_fact(condition, observed_action, unresolved_fact):
    if observed_action != "search" or not unresolved_fact:
        return None
    if condition["expected_action"] == "search":
        matched = query_matches_missing_fact(
            unresolved_fact,
            condition.get("missing_evidence", []),
            condition.get("missing_fact_targets", []),
        )
        return "names_missing_fact" if matched else "does_not_name_missing_fact"
    if condition["expected_action"] == "final":
        matched = query_matches_annotated_fact(
            unresolved_fact,
            condition.get("evidence", []),
            condition.get("supported_fact_targets", []),
        )
        return "claims_supported_fact_is_missing" if matched else "unverified_gap_claim"
    return None


def expected_value_appears(answer, reference):
    """Post-hoc surface match; this does not establish factual or citation support."""
    if not isinstance(answer, str) or not isinstance(reference, str) or not reference.strip():
        return None
    expected = normalized_value(reference)
    if expected and expected in normalized_value(answer):
        return True
    months = "January|February|March|April|May|June|July|August|September|October|November|December"
    date = re.fullmatch(rf"(\d{{1,2}})\s+({months})\s+(\d{{4}})", reference.strip(), re.I)
    if date:
        day, month, year = date.groups()
        alternatives = (
            rf"\b{month.casefold()}\s+0?{int(day)}\s*,?\s*{year}\b",
            rf"\b0?{int(day)}\s+{month.casefold()}\s+{year}\b",
        )
        return any(re.search(pattern, answer.casefold()) for pattern in alternatives)
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Make bounded live Gemini requests")
    parser.add_argument("--max-generation-requests", type=int)
    parser.add_argument("--limit-questions", type=int, help="Limit the size of a question slice")
    parser.add_argument("--start-question-index", type=int, default=0, help="Start at this zero-based suite index")
    parser.add_argument(
        "--case-id", dest="case_ids", action="append", help="Select an exact pinned question ID; repeat as needed"
    )
    parser.add_argument("--trial-label", type=int, default=1, help="Label repeated runs for post-hoc comparison")
    parser.add_argument("--compare-context-views", action="store_true", help="Compare source spans with full passages")
    parser.add_argument(
        "--context-view",
        choices=("required_sentence", "sentence_with_neighbors", "full_source_passage"),
        default="required_sentence",
        help="Use one evidence width; ignored when comparing both views",
    )
    parser.add_argument(
        "--decision-mode",
        choices=("VALIDATED", "AUTO", "DEFAULT"),
        default="VALIDATED",
        help="Post-search tool selection; DEFAULT omits explicit ToolConfig",
    )
    parser.add_argument(
        "--decision-schema",
        choices=("production_json", "none"),
        default="production_json",
        help="Post-search response schema; none probes provider-native tool calling",
    )
    parser.add_argument(
        "--initial-mode",
        choices=("VALIDATED", "AUTO", "DEFAULT"),
        default="VALIDATED",
        help="Initial-turn native tool selection; DEFAULT omits explicit ToolConfig",
    )
    parser.add_argument(
        "--initial-schema",
        choices=("production_json", "none"),
        default="production_json",
        help="Initial-turn response schema; none probes native tool calling without a competing JSON schema",
    )
    parser.add_argument(
        "--only-arm",
        choices=ALL_ARMS,
        help="Run only one decision-policy arm",
    )
    parser.add_argument(
        "--candidate-policy",
        choices=(
            "conditional_tool_description",
            IDENTITY_POLICY,
            GAP_COMMITMENT_POLICY,
            GOAL_COMMITMENT_POLICY,
            EVIDENCE_TEXT_POLICY,
            TEMPERATURE_POLICY,
            EVIDENCE_COVERAGE_POLICY,
            EVIDENCE_COVERAGE_EMBEDDED_POLICY,
            ANSWER_FIRST_POLICY,
            EXPLICIT_FINALIZE_POLICY,
            EXPLICIT_FINALIZE_VALIDATED_POLICY,
        ),
        default="conditional_tool_description",
        help="Select the experimental decision policy to pair with production",
    )
    parser.add_argument(
        "--only-condition", choices=("complete", "incomplete", "conflict_control", "no_evidence_control")
    )
    parser.add_argument("--pause-seconds", type=float, default=5.0)
    parser.add_argument("--model", help="Diagnostic-only Gemini model override")
    parser.add_argument(
        "--suite",
        type=Path,
        default=SUITE_PATH,
        help="Pinned local evaluation suite; defaults to the historical 40-record suite",
    )
    parser.add_argument("--corpus", type=Path, default=CORPUS_PATH, help="Local corpus backing the selected suite")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "reports/generated/gemini-search-sufficiency"
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]),
    )
    args = parser.parse_args(argv)
    if args.pause_seconds < 0 or args.pause_seconds > 60:
        parser.error("--pause-seconds must be between 0 and 60")
    try:
        print(json.dumps(asyncio.run(run(args)), ensure_ascii=False))
    except (ValueError, RuntimeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
