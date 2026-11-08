"""Preview-first, bounded Gemini decision diagnostics; never execute search tools."""

import argparse
import asyncio
import json
import os
import re
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

from google import genai
from google.genai import errors, types

from investigation_engine.application.investigation_decisions import _validate_inline_citations
from investigation_engine.application.investigation_records import (
    IdentifiedEvidence,
    InvestigationTurn,
    SearchExchange,
)
from investigation_engine.application.investigation_values import validate_search_query
from investigation_engine.application.investigations import (
    ConflictClaim,
    InvestigationDecision,
    InvestigationStatus,
    SearchEvidenceCall,
)
from investigation_engine.application.ports.generation_errors import GenerationResponseError
from investigation_engine.application.retrieval import Evidence
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.evaluation.answer_focus import assess_answer
from investigation_engine.infrastructure.generation.gemini_investigation_model import (
    FINAL_SCHEMA,
    INVESTIGATION_GUIDANCE_VERSION,
    MAX_FINAL_CHARS,
    SYSTEM_INSTRUCTION,
    TOOL_DECLARATION,
    TOOL_NAME,
    GeminiInvestigationModel,
    _unique_object,
    _usage,
)

ROOT = Path(__file__).resolve().parents[1]
QUESTION_ID = "5d6a128c0bb011ebab90acde48001122"
ALTERNATIVE_SYSTEM_INSTRUCTION = (
    SYSTEM_INSTRUCTION
    + """
Before deciding whether another search is necessary, check ALL relationships
required by the original question against the evidence already provided. For
a multi-hop relationship, check each link in its correct direction and scope,
including the identity of the linking person. A supported linking relationship
does not make the next relationship missing: check the other passages first.
If every required relationship is supported, return the final structured answer.
If a required relationship is unsupported or materially ambiguous, request a
search specifically for that missing relationship; do not search to recheck
relationships already established. Do not expose hidden reasoning.
"""
)
FINAL_ONLY_SUFFIX = "\nfinal_only is enabled. Search is disabled. Return your final structured decision now."
STRUCTURED_CASES = frozenset({"G", "H"})
DIAGNOSTIC_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["answer", "search", "insufficient_evidence", "conflicting_evidence"]},
        "answer": {"type": ["string", "null"]},
        "citations": {"type": "array", "items": {"type": "string"}},
        "missing_fact": {"type": ["string", "null"]},
        "search_query": {"type": ["string", "null"]},
    },
    "required": ["action", "answer", "citations", "missing_fact", "search_query"],
    "additionalProperties": False,
}


def structured_instructions():
    """Reuse v5's identity/source safeguards, adapting its native-tool assumptions."""
    paragraphs = SYSTEM_INSTRUCTION.split("\n\n")
    identity = next(item for item in paragraphs if item.startswith("A shared name"))
    identity = identity.replace(
        "If a necessary\nidentity link remains unestablished, return insufficient_evidence rather than guessing.",
        "If a necessary identity link remains unestablished, propose a JSON search for that gap when a relevant query could resolve it; never guess.",
    )
    sources = next(item for item in paragraphs if item.startswith("Retrieved excerpts"))
    sources = sources.replace("tool results", "saved results").replace(
        "Cite only the\nserver-assigned evidence IDs returned by the tool. The server resolves citations.",
        "Cite only server-assigned evidence IDs supplied with the excerpts. Citations must support their associated claims.",
    )
    return (
        """Make one evidence-sufficiency decision for the ORIGINAL question using only
the supplied evidence. Determine whether ALL required relationships are established,
checking identity, relationship direction, time and scope across passages. Never
investigate unrelated relationships. Never supply an unsupported relationship or
answer from background knowledge. Do not expose hidden reasoning.

There are no executable search tools. Search decisions are JSON outputs only:
they propose a later investigation and do not execute or claim a new retrieval.
When all necessary relationships are supported, choose action=answer and cite
the evidence establishing the complete chain. When a necessary relationship is
missing and a relevant search could resolve it, choose action=search, identify
the unknown fact in missing_fact and propose a specific relevant search_query.
Do not guess the missing value. Do not return insufficient_evidence merely
because current evidence is incomplete when a relevant search could resolve it.
Use insufficient_evidence only when no relevant investigable search can resolve
the gap. For material incompatible claims about the same entity, time and scope,
use conflicting_evidence; do not silently select a claim or merge distinct contexts.

"""
        + identity
        + "\n\n"
        + sources
        + """

Return exactly the five fields in the response schema. Use these combinations:
- answer: a nonempty answer and supporting citations; missing_fact/search_query null.
- search: answer null, nonempty missing_fact and search_query (at most 500 characters).
  Citations may identify established supporting facts, never the unknown value.
- insufficient_evidence: answer/search_query null; missing_fact describes the
  unresolved gap and why a relevant search cannot resolve it; citations optional.
- conflicting_evidence: answer/search_query null; missing_fact describes each
  incompatible cited claim; cite at least two distinct evidence IDs. Associate
  each claim with its citation. Verify identity, time and scope before declaring conflict.
Use unique E-number citation IDs. Inline references must be listed in citations.
Citation ID validity alone does not establish factual support.
"""
    )


STRUCTURED_SYSTEM_INSTRUCTION = structured_instructions()
DUAL_FUNCTION_CASES = frozenset({"N", "O"})
RELAXED_TEXT_CASES = frozenset({"P", "Q"})
MEDIUM_THINKING_CASES = frozenset({"R", "S"})
NATIVE_CASES = frozenset({"F", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S"})
CONTROLLED_NATIVE_CASES = NATIVE_CASES - {"F"}
FINAL_FUNCTION_NAME = "submit_final_answer"
FINAL_FUNCTION_DECLARATION = {
    "name": FINAL_FUNCTION_NAME,
    "description": "Submit a terminal investigation decision; this diagnostic declaration performs no external operation.",
    "parameters_json_schema": deepcopy(FINAL_SCHEMA),
}
DUAL_FUNCTION_INSTRUCTION = (
    SYSTEM_INSTRUCTION
    + """
Diagnostic two-function interface for this decision: use search_evidence when a
fact required by the ORIGINAL question remains unsupported and a relevant search
can resolve it. Use submit_final_answer when the evidence supports an answer,
establishes a material conflict, or cannot support a further useful investigation.
Do not search for unrelated relationships or answer using unsupported facts.
Return final decisions as submit_final_answer arguments, not JSON text.
Choosing submit_final_answer terminates the investigation without an external operation.
"""
)
RELATIONSHIP_PATH_ADDENDUM = """
Diagnostic question meaning: paternal grandfather means the father of the
subject's father. The required path is subject -> father -> father, containing
exactly two father relationships. Search only if a relationship required by
that path remains unsupported. Once the complete path is supported, answer
using the evidence. Do not investigate ancestors beyond the requested relationship.
"""
RELATIONSHIP_PATH_INSTRUCTION = SYSTEM_INSTRUCTION + RELATIONSHIP_PATH_ADDENDUM
GAP_TOOL_DECLARATION = deepcopy(TOOL_DECLARATION)
GAP_TOOL_DECLARATION["parameters_json_schema"]["properties"].update(
    missing_fact={"type": "string", "minLength": 1},
    evidence_ids_considered={"type": "array", "items": {"type": "string", "pattern": "^E[1-9][0-9]*$"}},
)
GAP_TOOL_DECLARATION["parameters_json_schema"]["required"] = ["query", "missing_fact", "evidence_ids_considered"]
GAP_SYSTEM_INSTRUCTION = (
    SYSTEM_INSTRUCTION
    + """
Diagnostic search arguments: when calling search_evidence, provide query,
missing_fact (the specific information you believe is still required to answer
the ORIGINAL question), and evidence_ids_considered (IDs of previously supplied
evidence considered when identifying that gap). Before any evidence is supplied,
use an empty evidence_ids_considered array. These fields are observable claims,
not hidden reasoning. Final structured decisions remain unchanged: you are free
to answer instead of calling the search tool.
"""
)
CASE_DESCRIPTIONS = {
    "A": "Complete saved first-search evidence; forced final-decision mode without a search tool.",
    "B": "Complete saved first-search evidence; free answer/search choice with production v5 instructions.",
    "C": "Identical to B except the explicit relationship-check system instruction.",
    "D": "Only E1 identifying the subject's father; free answer/search choice with production v5 instructions.",
    "E": "Identical to B except tools/tool_config are absent; final_only remains false and v5 still mentions searching.",
    "F": "Fresh question, then at most one native tool-result replay and a second decision; no retrieval or third call.",
    "G": "E1 only; diagnostic JSON search-or-answer decision without native tools.",
    "H": "E1–E5; identical to G except supplied evidence; no native tools.",
    "I": "Native two-turn control with full saved E1/E2 excerpts and production v5 tools/instructions.",
    "J": "Identical to I except only E1 is supplied; native tools remain enabled.",
    "K": "Native E1/E2 control with diagnostic gap-reporting search arguments; final answers remain available.",
    "L": "Identical to I with a diagnostic two-father goal-path instruction; full E1/E2 excerpts.",
    "M": "Identical to J with the same diagnostic goal-path instruction as L; E1 only.",
    "N": "Original native initial search, then E1/E2 and symmetric native search/final functions under ANY.",
    "O": "Identical to N except E1 only; both native functions remain available on the second request.",
    "P": "Identical to I except second-request response_mime_type and response_json_schema are unset.",
    "Q": "Identical to J with the same two second-request settings unset; E1 only.",
    "R": "Identical to I except second-request thinking_level=MEDIUM; full E1/E2 and original JSON schema.",
    "S": "Identical to J except second-request thinking_level=MEDIUM; E1 only and original JSON schema.",
}
CASE_REQUEST_LIMITS = {case: 2 if case in NATIVE_CASES else 1 for case in CASE_DESCRIPTIONS}
REVIEW_FIELDS = (
    "returned_correct_answer",
    "identified_specific_missing_relationship",
    "produced_unsupported_claims",
    "used_citations_correctly",
)
REVIEW_VALUES = {None, "yes", "no", "unverifiable", "not_applicable"}
_SECRET_KEY = re.compile(
    r"^(?:authorization|proxy.authorization|x.goog.api.key|x.api.key|(?:(?:gemini|google|openai).?)?api.?key|password|(?:client.?)?secret|private.key|credentials|access.?token|refresh.?token|id.?token|cookie|set.cookie)$",
    re.I,
)


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return sha256(json_text(value).encode()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sanitize(value, secrets=()):
    """Keep native parts/usage; redact credentials, auth/cookie headers and key URLs."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _SECRET_KEY.fullmatch(key) else sanitize(item, secrets) for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)([?&](?:key|api_key|access_token)=)[^&\s\"']+", r"\1[REDACTED]", value)
        value = re.sub(r"\bAIza[A-Za-z0-9_-]{30,}\b", "[REDACTED]", value)
        return re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
    return value


def write_json(path, value, secrets=()):
    path.write_text(json.dumps(sanitize(value, secrets), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def evidence_reference(record):
    source = dict(record["source"])
    source["kind"] = SourceKind(source["kind"])
    source.setdefault("display_name", record["title"])
    spans = tuple(
        SourceSpan(
            start=span["start"],
            end=span["end"],
            locators=tuple(
                SourceLocator(kind=LocatorKind(item["kind"]), index=item["index"]) for item in span["locators"]
            ),
        )
        for span in record["source_spans"]
    )
    hit = Evidence(
        document_id=UUID(record["document_id"]),
        document_version_id=UUID(record["document_version_id"]),
        content_sha256=record["content_sha256"],
        text=record["text"],
        start=record["start"],
        end=record["end"],
        source=SourceReference(**source),
        source_spans=spans,
        score=record["score"],
        rank=record["rank"],
        title=record["title"],
        score_kind=record["score_kind"],
        semantic_rank=record.get("semantic_rank"),
        keyword_rank=record.get("keyword_rank"),
    )
    return IdentifiedEvidence(evidence_id=record["citation_id"], evidence=hit)


def load_evidence(directory):
    paths = {name: directory / name for name in ("investigation_traces.jsonl", "answer_results.jsonl", "manifest.json")}
    trace, row, manifest = (read_json(paths[name]) for name in paths)
    if trace["question_id"] != QUESTION_ID or row["question_id"] != QUESTION_ID:
        raise ValueError("Source must be a single-question saved Jolliffe evaluation")
    exchange = next(turn["exchanges"][0] for turn in trace["turns"] if turn["exchanges"])
    records = exchange["evidence"]
    refs = tuple(evidence_reference(record) for record in records)
    if len(refs) != 5 or [ref.evidence_id for ref in refs[:2]] != ["E1", "E2"]:
        raise ValueError("Expected the five original first-search passages with stable E1/E2 IDs")
    if (
        refs[0].evidence.title != "William Jolliffe, 4th Baron Hylton"
        or refs[1].evidence.title != "Hylton Jolliffe, 3rd Baron Hylton"
    ):
        raise ValueError("Saved E1/E2 do not identify the expected father/grandfather chain")
    return {
        "question": trace["question"],
        "reference_answer": row["expected_answer"],
        "refs": refs,
        "search_query": exchange["query"],
        "provenance": records,
        "source": {
            "directory": str(directory.resolve()),
            "run_id": manifest["run_id"],
            "sha256": {name: sha256(path.read_bytes()).hexdigest() for name, path in paths.items()},
        },
    }


def supplied_refs(case, evidence):
    count = 1 if case in {"D", "G", "J", "M", "O", "Q", "S"} else 2 if case in {"I", "K", "L", "N", "P", "R"} else 5
    return evidence["refs"][:count]


def make_request(case, evidence, settings):
    """Mirror production config, importing its exact prompts/tool/final schema."""
    forced = case == "A"
    refs = supplied_refs(case, evidence)
    saved = InvestigationTurn(
        question=evidence["question"],
        exchanges=(SearchExchange(query=evidence["search_query"], call_id="saved-search-1", evidence=refs),),
    ).search_responses()[0]
    message = {
        "question": evidence["question"],
        "final_only": forced,
        "evidence_origin": "Saved server-assigned evidence supplied as data in a fresh conversation; no live search has occurred.",
        "saved_search_result": {"search_sequence": 1, **saved},
    }
    if case in STRUCTURED_CASES:
        message.pop("final_only")  # Native finalization state does not apply to JSON search proposals.
    config = types.GenerateContentConfig(
        system_instruction=(
            STRUCTURED_SYSTEM_INSTRUCTION
            if case in STRUCTURED_CASES
            else RELATIONSHIP_PATH_INSTRUCTION
            if case in {"L", "M"}
            else GAP_SYSTEM_INSTRUCTION
            if case == "K"
            else ALTERNATIVE_SYSTEM_INSTRUCTION
            if case == "C"
            else SYSTEM_INSTRUCTION
        )
        + (FINAL_ONLY_SUFFIX if forced else ""),
        tools=None
        if forced or case in {"E", "G", "H"}
        else [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(**(GAP_TOOL_DECLARATION if case == "K" else TOOL_DECLARATION))
                ]
            )
        ],
        tool_config=None
        if forced or case in {"E", "G", "H"}
        else types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode.VALIDATED,
                allowed_function_names=[TOOL_NAME],
            )
        ),
        response_mime_type="application/json",
        response_json_schema=DIAGNOSTIC_DECISION_SCHEMA if case in STRUCTURED_CASES else FINAL_SCHEMA,
        max_output_tokens=settings.investigation_max_output_tokens,
        candidate_count=1,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    return {
        "model": settings.gemini_generation_model,
        "contents": [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text=json_text({"question": evidence["question"]} if case in NATIVE_CASES else message)
                    )
                ],
            )
        ],
        "config": config,
    }


def diagnostic_call_id(case, trial, step=1):
    return f"{case}-{trial:03}" + (f"-step{step}" if case in NATIVE_CASES else "")


def parse_native_action(response, case):
    """K validates extra arguments locally; signed SDK parts are never rewritten."""
    if case != "K" or not any(
        part.function_call is not None
        for candidate in response.candidates or []
        for part in ((candidate.content.parts or []) if candidate.content else [])
    ):
        return GeminiInvestigationModel._parse_response(response)
    candidates = response.candidates or []
    if len(candidates) != 1 or candidates[0].finish_reason != types.FinishReason.STOP:
        raise GenerationResponseError("Gap-reporting search requires one completed candidate")
    parts = candidates[0].content.parts or []
    calls = [part.function_call for part in parts if part.function_call is not None]
    if len(calls) != 1 or any(part.text and part.text.strip() and not part.thought for part in parts):
        raise GenerationResponseError("Expected exactly one gap-reporting call or a final decision")
    call = calls[0]
    if (
        call.name != TOOL_NAME
        or not isinstance(call.args, dict)
        or set(call.args) != {"query", "missing_fact", "evidence_ids_considered"}
    ):
        raise GenerationResponseError(
            "Gap-reporting call requires exactly query, missing_fact and evidence_ids_considered"
        )
    validate_search_query(call.args["query"])
    ids = call.args["evidence_ids_considered"]
    if not isinstance(call.args["missing_fact"], str) or not call.args["missing_fact"].strip():
        raise GenerationResponseError("missing_fact must be a nonempty observable claim")
    if not isinstance(ids, list) or not all(
        isinstance(item, str) and re.fullmatch(r"E[1-9][0-9]*", item) for item in ids
    ):
        raise GenerationResponseError("evidence_ids_considered must contain evidence IDs")
    if len(ids) != len(set(ids)):
        raise GenerationResponseError("evidence_ids_considered must be unique")
    return SearchEvidenceCall(
        query=call.args["query"],
        call_id=str(uuid4()),
        usage=_usage(response.usage_metadata),
        model_version=response.model_version,
    )


def followup_config(initial_config, case):
    config = initial_config.model_copy(deep=True)
    if case in MEDIUM_THINKING_CASES:
        config.thinking_config = types.ThinkingConfig(thinking_level=types.ThinkingLevel.MEDIUM)
    elif case in RELAXED_TEXT_CASES:
        config.response_mime_type = None
        config.response_json_schema = None
    elif case in DUAL_FUNCTION_CASES:
        config.system_instruction = DUAL_FUNCTION_INSTRUCTION
        config.tools = [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(**TOOL_DECLARATION),
                    types.FunctionDeclaration(**FINAL_FUNCTION_DECLARATION),
                ]
            )
        ]
        config.tool_config = types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode.ANY,
                allowed_function_names=[TOOL_NAME, FINAL_FUNCTION_NAME],
            )
        )
        config.response_mime_type = None
        config.response_json_schema = None
        config.response_schema = None
    return config


def final_decision_from_arguments(raw, response, *, label="Native final"):
    """Apply existing final value semantics without manufacturing SDK message parts."""
    try:
        if not isinstance(raw, dict) or set(raw) != set(FINAL_SCHEMA["required"]):
            raise ValueError("Invalid final field set")
        if not isinstance(raw["citations"], list) or not isinstance(raw["conflicts"], list):
            raise ValueError("Invalid citation/conflict types")
        if any(
            not isinstance(item, dict) or set(item) != {"claim", "citations"} or not isinstance(item["citations"], list)
            for item in raw["conflicts"]
        ):
            raise ValueError("Invalid conflict claim")
        return InvestigationDecision(
            status=InvestigationStatus(raw["status"]),
            answer=raw["answer"],
            citations=tuple(raw["citations"]),
            conflicts=tuple(
                ConflictClaim(claim=item["claim"], citations=tuple(item["citations"])) for item in raw["conflicts"]
            ),
            usage=_usage(response.usage_metadata),
            model_version=response.model_version,
        )
    except KeyError, TypeError, ValueError, GenerationResponseError:
        raise GenerationResponseError(f"{label} arguments violate the production final-decision contract") from None


def parse_dual_function_action(response):
    """Validate native decisions through production values, without rewriting SDK parts."""
    candidates = response.candidates or []
    if len(candidates) != 1 or candidates[0].finish_reason != types.FinishReason.STOP or not candidates[0].content:
        raise GenerationResponseError("Expected one completed native decision candidate")
    parts = candidates[0].content.parts or []
    calls = [part.function_call for part in parts if part.function_call is not None]
    if len(calls) != 1:
        raise GenerationResponseError("Expected exactly one native decision call; multiple calls are ambiguous")
    if any(part.text and part.text.strip() and not part.thought for part in parts):
        raise GenerationResponseError("Native decision cannot mix function calls with final JSON text")
    call = calls[0]
    if call.name == TOOL_NAME:
        return GeminiInvestigationModel._parse_response(response)
    if call.name != FINAL_FUNCTION_NAME:
        raise GenerationResponseError("Unexpected native decision function")
    return final_decision_from_arguments(call.args, response)


def native_followup(first_response, first_request, evidence, *, case="F"):
    """Preserve actual SDK model Content; never rebuild signed parts from JSON."""
    try:
        action = parse_native_action(first_response, case)
    except GenerationResponseError:
        return None, {"stop_reason": "unsupported_initial_response"}
    if not isinstance(action, SearchEvidenceCall):
        return None, {"stop_reason": "initial_final_decision"}
    content = first_response.candidates[0].content
    part = next(part for part in content.parts if part.function_call is not None)
    if content.role != "model" or (first_request["model"].startswith("gemini-3") and not part.thought_signature):
        return None, {"stop_reason": "invalid_or_unsigned_native_model_content"}
    if len(content.model_dump_json()) > 64000:  # Match the production adapter's native-message bound.
        return None, {"stop_reason": "native_model_content_exceeds_production_bound"}
    call = part.function_call
    requested_query = call.args["query"]
    replay = {
        "requested_query": requested_query,
        "saved_result_query": evidence["search_query"],
        "query_matches_saved_exactly": requested_query == evidence["search_query"],
        "live_retrieval_performed": False,
        "source_run_id": evidence["source"]["run_id"],
        "notice": "Simulated replay of saved first-search evidence, not live retrieval for the requested query. Query mismatch does not establish retrieval relevance.",
    }
    if case in CONTROLLED_NATIVE_CASES:
        replay.update(
            controlled_subset=True,
            notice="Controlled subset of saved first-search evidence, not live retrieval for the requested query. Query mismatch does not establish retrieval relevance.",
        )
    payload = InvestigationTurn(
        question=evidence["question"],
        exchanges=(
            SearchExchange(query=requested_query, call_id="native-replay", evidence=supplied_refs(case, evidence)),
        ),
    ).search_responses()[0]
    function_response = types.FunctionResponse(
        id=call.id, name=call.name, response={"search_sequence": 1, **payload, "replay": replay}
    )
    request = {
        "model": first_request["model"],
        "config": followup_config(first_request["config"], case),
        "contents": [
            *first_request["contents"],
            content.model_copy(deep=True),
            types.Content(role="user", parts=[types.Part(function_response=function_response)]),
        ],
    }
    return request, {
        "stop_reason": None,
        "replay": replay,
        "native_function_call_id": call.id,
        "function_response": function_response.model_dump(mode="json", exclude_none=False),
    }


def serialize_request(request, settings):
    return {
        "representation": "Sanitized Python SDK call arguments/model_dump(mode=json); NOT raw HTTP wire payloads.",
        "model": request["model"],
        "contents": [content.model_dump(mode="json", exclude_none=False) for content in request["contents"]],
        "config": request["config"].model_dump(mode="json", exclude_none=False),
        "client_options": {
            "enterprise": False,
            "base_url": "https://generativelanguage.googleapis.com",
            "api_version": "v1beta",
            "timeout_ms": settings.generation_timeout_seconds * 1000,
            "retry_options": {"attempts": 1},
        },
        "unset_settings": "Temperature, top_p, top_k and seed are unset; explicit thinking settings are recorded in config, with other provider defaults not inferred."
        if request["config"].thinking_config is not None
        else "Temperature, top_p, top_k, thinking and seed are unset, as in production; provider defaults are not inferred.",
    }


def diagnostic_payload_errors(raw):
    """Validate JSON/action consistency only; never infer whether claims are true."""
    if not isinstance(raw, dict) or set(raw) != set(DIAGNOSTIC_DECISION_SCHEMA["required"]):
        return ["Expected exactly the five diagnostic decision fields"]
    errors_found = []
    action = raw["action"]
    if not isinstance(action, str) or action not in DIAGNOSTIC_DECISION_SCHEMA["properties"]["action"]["enum"]:
        errors_found.append("Unknown diagnostic action")
    for field in ("answer", "missing_fact", "search_query"):
        if raw[field] is not None and (not isinstance(raw[field], str) or not raw[field].strip()):
            errors_found.append(f"{field} must be null or a nonblank string")
    citations = raw["citations"]
    if not isinstance(citations, list) or not all(
        isinstance(item, str) and re.fullmatch(r"E[1-9][0-9]*", item) for item in citations
    ):
        errors_found.append("citations must be an array of E-number identifiers")
    elif len(citations) != len(set(citations)):
        errors_found.append("Citation identifiers must be unique")
    if errors_found:
        return errors_found
    if action == "answer":
        if raw["answer"] is None or not citations or raw["missing_fact"] is not None or raw["search_query"] is not None:
            errors_found.append("answer requires answer text/citations and null missing_fact/search_query")
    else:
        if raw["answer"] is not None or raw["missing_fact"] is None:
            errors_found.append("Non-answer actions require null answer and a nonempty missing_fact")
        if action == "search":
            try:
                validate_search_query(raw["search_query"])
            except GenerationResponseError:
                errors_found.append("search requires a nonblank 1–500 character query")
        elif raw["search_query"] is not None:
            errors_found.append("Only a search action may propose a search_query")
        if action == "conflicting_evidence" and len(citations) < 2:
            errors_found.append("conflicting_evidence requires at least two distinct citation IDs")
    return errors_found


def inspect_structured_response(response, native, calls, texts, finishes, question, reference, evidence_ids, case):
    """Keep complete SDK data and separate action agreement from factual judgments."""
    problems, raw = [], None
    candidates = response.candidates or []
    if len(candidates) != 1 or candidates[0].finish_reason != types.FinishReason.STOP:
        problems.append("Expected one normally completed candidate")
    if calls or len(texts) != 1:
        problems.append("Expected one JSON text decision and no native function calls")
    if len(texts) == 1:
        if len(texts[0]["text"]) > MAX_FINAL_CHARS:
            problems.append("Diagnostic JSON text exceeds the production final-text bound")
        else:
            try:
                raw = json.loads(texts[0]["text"], object_pairs_hook=_unique_object)
            except ValueError:
                problems.append("Invalid JSON or duplicate object keys")
    problems.extend(diagnostic_payload_errors(raw))
    reported = raw if isinstance(raw, dict) else {}
    valid = not problems
    citations = reported.get("citations")
    citation_list = (
        citations if isinstance(citations, list) and all(isinstance(item, str) for item in citations) else None
    )
    citation_valid = None
    if valid:
        citation_valid = not (set(citation_list) - set(evidence_ids))
        try:
            for field in ("answer", "missing_fact"):
                if reported[field]:
                    _validate_inline_citations(reported[field], tuple(citation_list), "Unlisted inline citation")
        except GenerationResponseError:
            citation_valid = False
    expected = "search" if case == "G" else "answer"
    assessment = assess_answer(
        question,
        reported.get("answer") if valid else None,
        reference,
        status="answered" if valid and reported["action"] == "answer" else "unavailable",
    )
    return {
        "sdk_response_representation": native,
        "finish_reasons": finishes,
        "returned_function_calls": calls,
        "final_text_parts": texts,
        "final_text": "\n".join(item["text"] for item in texts),
        "response_metadata": {key: value for key, value in native.items() if key != "candidates"},
        "usage_metadata": native.get("usage_metadata"),
        "diagnostic_reported_decision": raw,
        "diagnostic_parsed_decision": raw if valid else None,
        "diagnostic_payload_valid": valid,
        "diagnostic_validation_errors": problems,
        "production_parsed_action": None,
        "production_parse_error": None,
        "automatic_observations": {
            "requested_another_search": any(call["name"] == TOOL_NAME for call in calls),
            "requested_queries": [call["args"].get("query") for call in calls if isinstance(call["args"], dict)],
            "structured_action": reported.get("action"),
            "structured_final_answer": reported.get("answer"),
            "missing_fact": reported.get("missing_fact"),
            "proposed_search_query": reported.get("search_query"),
            "proposed_json_search": valid and reported["action"] == "search",
            "returned_insufficient_evidence": reported.get("action") == "insufficient_evidence" if valid else None,
            "expected_action": expected,
            "expected_action_matches": reported["action"] == expected if valid else None,
            "expected_behavior_basis": "Action agreement only; missing-fact relevance, factual support and complete expected behavior require manual review.",
            "reference_agreement_only": assessment,
            "citation_ids": citations,
            "citation_ids_registered_in_supplied_evidence": not (set(citation_list) - set(evidence_ids))
            if citation_list is not None
            else None,
            "citation_structure_valid": citation_valid,
            "expected_chain_citation_ids_present": {"E1", "E2"}.issubset(citation_list)
            if case == "H" and citation_list is not None
            else None,
            "unsupported_or_correct_claims": "Unverified: JSON action, reference agreement and citation IDs do not establish factual correctness.",
        },
    }


def inspect_relaxed_text(response, result, question, reference, evidence_ids):
    """An unconstrained text format is not a native-protocol error."""
    text = "".join(item["text"] for item in result["final_text_parts"])
    result["final_text"] = text
    calls = result["returned_function_calls"]
    if calls:
        problems, action = [], None
        candidates = response.candidates or []
        try:
            if len(candidates) != 1 or candidates[0].finish_reason != types.FinishReason.STOP or len(calls) != 1:
                raise GenerationResponseError("Expected one completed native search call")
            call = calls[0]
            if call["name"] != TOOL_NAME or not isinstance(call["args"], dict) or set(call["args"]) != {"query"}:
                raise GenerationResponseError("Unexpected function or malformed production search arguments")
            validate_search_query(call["args"]["query"])
            action = SearchEvidenceCall(
                query=call["args"]["query"],
                call_id=str(uuid4()),
                usage=_usage(response.usage_metadata),
                model_version=response.model_version,
            )
        except GenerationResponseError as error:
            problems.append(str(error))
        result.update(
            response_kind="native_search" if action else "invalid_native_output",
            structured_final_recognized=False,
            structured_final_parse_error=None,
            protocol_errors=problems,
            accompanying_text=text,
            production_format_note=result["production_parse_error"],
            production_parse_error=None,
            diagnostic_parsed_action=asdict(action) if action else None,
            diagnostic_parse_error="; ".join(problems) if problems else None,
            ambiguous_native_output=len(calls) > 1,
        )
        result["automatic_observations"]["observed_final_text"] = None
        return result
    candidates = response.candidates or []
    protocol_errors = []
    if len(candidates) != 1 or candidates[0].finish_reason != types.FinishReason.STOP:
        protocol_errors.append("Expected one normally completed candidate")
    if not text.strip():
        protocol_errors.append("No visible final text or native function call")
    if len(candidates) == 1 and candidates[0].content and candidates[0].content.role not in (None, "model"):
        protocol_errors.append("Final text content has an invalid role")
    payload = text.strip()
    fenced = re.fullmatch(r"```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```", payload, re.I)
    if fenced:
        payload = fenced[1]
    raw, decision, structured_error = None, None, None
    try:
        if not payload or len(payload) > MAX_FINAL_CHARS:
            raise ValueError("Text is outside the structured final parser bounds")
        raw = json.loads(payload, object_pairs_hook=_unique_object)
        decision = final_decision_from_arguments(raw, response, label="Text final")
    except ValueError, GenerationResponseError:
        structured_error = "Text is not a complete valid production final object; its original form is retained."
    inline = list(
        dict.fromkeys(
            value
            for marker in re.findall(r"\[(E[^\]\r\n]*)\]", text)
            for value in re.split(r"[,;\s]+", marker.strip())
            if value
        )
    )
    mentioned = list(dict.fromkeys(re.findall(r"\bE[1-9][0-9]*\b", text)))
    declared = raw.get("citations") if isinstance(raw, dict) else None
    cited = list(decision.citations) if decision else inline
    if decision:
        cited.extend(item for claim in decision.conflicts for item in claim.citations)
    registered = not (set(cited) - set(evidence_ids)) if cited or decision else None
    citation_valid = registered
    if decision:
        try:
            for claim_text, ids in [
                (decision.answer, decision.citations),
                *[(claim.claim, claim.citations) for claim in decision.conflicts],
            ]:
                if claim_text:
                    _validate_inline_citations(claim_text, ids, "Unlisted inline citation")
        except GenerationResponseError:
            citation_valid = False
    elif cited and not all(re.fullmatch(r"E[1-9][0-9]*", item) for item in cited):
        citation_valid = False
    assessment = assess_answer(
        question,
        decision.answer if decision else text,
        reference,
        status=decision.status.value if decision else "answered" if not protocol_errors else "unavailable",
    )
    result.update(
        response_kind="structured_final_json" if decision else "unstructured_text",
        structured_final_recognized=decision is not None,
        structured_final_parse_error=structured_error,
        reported_json_value=raw,
        protocol_errors=protocol_errors,
        ambiguous_native_output=False,
        production_parsed_action=asdict(decision) if decision else None,
        production_parse_error=None,
    )
    result["automatic_observations"].update(
        observed_final_text=text,
        observed_inline_citation_ids=inline,
        observed_declared_citation_ids=declared,
        observed_mentioned_evidence_ids=mentioned,
        structured_final_answer=decision.answer if decision else None,
        structured_final_status=decision.status.value if decision else None,
        returned_insufficient_evidence=decision.status == InvestigationStatus.INSUFFICIENT_EVIDENCE
        if decision
        else None,
        reference_agreement_only=assessment,
        citation_ids=cited,
        citation_ids_registered_in_supplied_evidence=registered,
        citation_structure_valid=citation_valid,
    )
    return result


def inspect_response(response, question, reference, evidence_ids, *, case=None, step=1):
    """Inspect all candidate parts directly. Never use response.text."""
    native = response.model_dump(mode="json", exclude_none=False)
    calls, texts, finishes = [], [], []
    for index, candidate in enumerate(response.candidates or []):
        finishes.append(
            {"candidate": index, "finish_reason": candidate.finish_reason, "finish_message": candidate.finish_message}
        )
        for part in (candidate.content.parts or []) if candidate.content else []:
            if part.function_call is not None:
                calls.append({"candidate": index, **part.function_call.model_dump(mode="json", exclude_none=False)})
            if part.text is not None and not part.thought:
                texts.append({"candidate": index, "text": part.text})
    if case in STRUCTURED_CASES:
        return inspect_structured_response(
            response, native, calls, texts, finishes, question, reference, evidence_ids, case
        )
    action, parse_error = None, None
    dual_decision = case in DUAL_FUNCTION_CASES and step == 2
    try:
        action = parse_dual_function_action(response) if dual_decision else parse_native_action(response, case)
    except GenerationResponseError as error:
        parse_error = (
            str(error)
            if case == "K" or dual_decision
            else "Production response parser rejected this output; complete SDK response retained."
        )
    decision = action if isinstance(action, InvestigationDecision) else None
    assessment = assess_answer(
        question,
        decision.answer if decision else None,
        reference,
        status=decision.status.value if decision else "unavailable",
    )
    cited = list(decision.citations) if decision else []
    if decision:
        cited.extend(citation for claim in decision.conflicts for citation in claim.citations)
    citation_structure_valid = None
    if decision:
        citation_structure_valid = not (set(cited) - set(evidence_ids))
        try:
            if decision.answer:
                _validate_inline_citations(decision.answer, decision.citations, "Unlisted inline citation")
            for claim in decision.conflicts:
                _validate_inline_citations(claim.claim, claim.citations, "Unlisted inline citation")
        except GenerationResponseError:
            citation_structure_valid = False
    result = {
        "sdk_response_representation": native,
        "finish_reasons": finishes,
        "returned_function_calls": calls,
        "final_text_parts": texts,
        "final_text": "\n".join(item["text"] for item in texts),
        "response_metadata": {key: value for key, value in native.items() if key != "candidates"},
        "usage_metadata": native.get("usage_metadata"),
        "production_parsed_action": asdict(action) if action and case != "K" and not dual_decision else None,
        "production_parse_error": parse_error if case != "K" and not dual_decision else None,
        "automatic_observations": {
            "requested_another_search": any(call["name"] == TOOL_NAME for call in calls),
            "requested_queries": [call["args"].get("query") for call in calls if isinstance(call["args"], dict)],
            "structured_final_answer": decision.answer if decision else None,
            "structured_final_status": decision.status.value if decision else None,
            "returned_insufficient_evidence": decision.status.value == "insufficient_evidence" if decision else None,
            "reference_agreement_only": assessment,
            "citation_ids_registered_in_supplied_evidence": not (set(cited) - set(evidence_ids)) if decision else None,
            "citation_structure_valid": citation_structure_valid,
            "citation_ids": cited,
            "unsupported_or_correct_claims": "Unverified: matching a reference or registered citation does not establish factual support.",
        },
    }
    if case == "K":
        claims = [call["args"] for call in calls if isinstance(call["args"], dict)]
        result.update(diagnostic_parsed_action=asdict(action) if action else None, diagnostic_parse_error=parse_error)
        result["automatic_observations"].update(
            search_gap_claims=claims,
            considered_ids_registered=[
                not (set(item["evidence_ids_considered"]) - set(evidence_ids))
                if isinstance(item.get("evidence_ids_considered"), list)
                and all(isinstance(value, str) for value in item["evidence_ids_considered"])
                else None
                for item in claims
            ],
            gap_claim_basis="Observable model claims only; missing-fact truth, relevance and unsupported relationships require manual review.",
        )
    if dual_decision:
        terminal_errors = [parse_error] if parse_error else []
        if decision and not citation_structure_valid:
            terminal_errors.append("Final citations contain unavailable or unlisted evidence IDs")
        result.update(diagnostic_parsed_action=asdict(action) if action else None, diagnostic_parse_error=parse_error)
        result["automatic_observations"].update(
            selected_native_function=calls[0]["name"] if len(calls) == 1 else None,
            requested_queries=[
                call["args"].get("query")
                for call in calls
                if call["name"] == TOOL_NAME and isinstance(call["args"], dict)
            ],
            ambiguous_native_decision=len(calls) > 1,
            native_final_arguments=[call["args"] for call in calls if call["name"] == FINAL_FUNCTION_NAME],
            native_terminal_decision_valid=bool(decision and citation_structure_valid and not terminal_errors),
            native_terminal_validation_errors=terminal_errors,
            native_terminal_validation_basis="Argument and citation-structure checks only; factual support remains unreviewed.",
        )
    if case in RELAXED_TEXT_CASES and step == 2:
        return inspect_relaxed_text(response, result, question, reference, evidence_ids)
    return result


def comparison(directory, reviews_path=None):
    records = [read_json(path) for path in sorted(directory.glob("call-*.json"))]
    for record in records:
        if record.get("record_fingerprint") != fingerprint(
            {key: value for key, value in record.items() if key != "record_fingerprint"}
        ):
            raise ValueError("Saved diagnostic record has changed or is incomplete")
    expected = {record["call_id"]: record for record in records}
    if len(expected) != len(records):
        raise ValueError("Duplicate saved diagnostic call IDs")
    reviews = {}
    if reviews_path:
        for review in read_json(reviews_path):
            call_id = review["call_id"]
            if (
                call_id not in expected
                or call_id in reviews
                or review["record_fingerprint"] != expected[call_id]["record_fingerprint"]
            ):
                raise ValueError("Unknown, duplicate or stale diagnostic review")
            if set(review) != {"call_id", "record_fingerprint", "judgments", "reviewer", "explanation"}:
                raise ValueError("Unexpected review fields")
            if set(review["judgments"]) != set(review_fields(expected[call_id]["case"])) or any(
                value not in REVIEW_VALUES for value in review["judgments"].values()
            ):
                raise ValueError("Invalid manual diagnostic judgments")
            if (
                any(value is not None for value in review["judgments"].values())
                and not str(review.get("reviewer") or "").strip()
            ):
                raise ValueError("Completed judgments require a reviewer")
            if (
                any(value is not None for value in review["judgments"].values())
                and expected[call_id]["status"] != "response_received"
            ):
                raise ValueError("Cannot judge a preview or a failed call without a completed inspected response")
            reviews[call_id] = review
    rows, templates = [], []
    for record in records:
        template = {
            "call_id": record["call_id"],
            "record_fingerprint": record["record_fingerprint"],
            "judgments": dict.fromkeys(review_fields(record["case"])),
            "reviewer": None,
            "explanation": None,
        }
        review = reviews.get(record["call_id"], template)
        observations = (record.get("response") or {}).get("automatic_observations", {})
        rows.append(
            {
                "call_id": record["call_id"],
                "case": record["case"],
                "trial": record["trial"],
                "step": record.get("step", 1),
                "status": record["status"],
                "decision_mode": record["decision_mode"],
                "requested_another_search": observations.get("requested_another_search"),
                "observed_queries": observations.get("requested_queries"),
                "final_answer": observations.get("structured_final_answer"),
                "final_status": observations.get("structured_final_status"),
                "returned_insufficient_evidence": observations.get("returned_insufficient_evidence"),
                "replay": record.get("replay"),
                "stop_reason": record.get("stop_reason"),
                "case_limitation": record.get("case_limitation"),
                "reference_agreement_only": observations.get("reference_agreement_only"),
                "citation_ids_registered": observations.get("citation_ids_registered_in_supplied_evidence"),
                "citation_structure_valid": observations.get("citation_structure_valid"),
                "expected_missing_relationship": record["expected_missing_relationship"],
                **review["judgments"],
                "manual_review": review,
            }
        )
        if record["case"] in STRUCTURED_CASES:
            usage = (record.get("response") or {}).get("usage_metadata") or {}
            rows[-1].update(
                structured_action=observations.get("structured_action"),
                missing_fact=observations.get("missing_fact"),
                proposed_search_query=observations.get("proposed_search_query"),
                citation_ids=observations.get("citation_ids"),
                expected_action=observations.get("expected_action"),
                expected_action_matches=observations.get("expected_action_matches"),
                expected_behavior_basis=observations.get("expected_behavior_basis"),
                expected_chain_citation_ids_present=observations.get("expected_chain_citation_ids_present"),
                diagnostic_payload_valid=(record.get("response") or {}).get("diagnostic_payload_valid"),
                diagnostic_validation_errors=(record.get("response") or {}).get("diagnostic_validation_errors"),
                prompt_tokens=usage.get("prompt_token_count"),
                output_tokens=usage.get("candidates_token_count"),
                duration_ms=record.get("duration_ms"),
            )
        if record["case"] in CONTROLLED_NATIVE_CASES:
            usage = (record.get("response") or {}).get("usage_metadata") or {}
            rows[-1].update(
                citation_ids=observations.get("citation_ids"),
                search_gap_claims=observations.get("search_gap_claims"),
                considered_ids_registered=observations.get("considered_ids_registered"),
                supplied_evidence=record.get("supplied_evidence"),
                protocol_errors=(record.get("response") or {}).get("protocol_errors"),
                parsing_error=(record.get("response") or {}).get("diagnostic_parse_error")
                or (record.get("response") or {}).get("production_parse_error"),
                prompt_tokens=usage.get("prompt_token_count"),
                output_tokens=usage.get("candidates_token_count"),
                duration_ms=record.get("duration_ms"),
            )
            if record["case"] in DUAL_FUNCTION_CASES:
                rows[-1].update(
                    selected_native_function=observations.get("selected_native_function"),
                    ambiguous_native_decision=observations.get("ambiguous_native_decision"),
                    native_final_arguments=observations.get("native_final_arguments"),
                    native_terminal_decision_valid=observations.get("native_terminal_decision_valid"),
                    native_terminal_validation_errors=observations.get("native_terminal_validation_errors"),
                    returned_function_calls=(record.get("response") or {}).get("returned_function_calls"),
                )
            if record["case"] in RELAXED_TEXT_CASES:
                reply = record.get("response") or {}
                rows[-1].update(
                    response_kind=reply.get("response_kind"),
                    observed_final_text=observations.get("observed_final_text"),
                    observed_inline_citation_ids=observations.get("observed_inline_citation_ids"),
                    observed_declared_citation_ids=observations.get("observed_declared_citation_ids"),
                    observed_mentioned_evidence_ids=observations.get("observed_mentioned_evidence_ids"),
                    accompanying_text=reply.get("accompanying_text"),
                    structured_final_recognized=reply.get("structured_final_recognized"),
                    structured_final_parse_error=reply.get("structured_final_parse_error"),
                    ambiguous_native_output=reply.get("ambiguous_native_output"),
                )
                if reply.get("response_kind") == "unstructured_text" and not reply.get("protocol_errors"):
                    rows[-1]["final_answer"] = observations.get("observed_final_text")
            if record["case"] in MEDIUM_THINKING_CASES:
                request = record.get("request") or {}
                template = record.get("request_template") or {}
                reply = record.get("response") or {}
                returned = reply.get("sdk_response_representation") or {}
                calls = reply.get("returned_function_calls") or []
                rows[-1].update(
                    requested_model=request.get("model", template.get("model")),
                    reported_model_version=returned.get("model_version"),
                    requested_thinking_level=((request.get("config") or {}).get("thinking_config") or {}).get(
                        "thinking_level"
                    ),
                    planned_thinking_level=((template.get("config") or {}).get("thinking_config") or {}).get(
                        "thinking_level"
                    )
                    if not request
                    else None,
                    generation_request_attempted=bool(record["generation_requests"]),
                    selected_action=calls[0]["name"]
                    if len(calls) == 1
                    else "ambiguous_native_calls"
                    if len(calls) > 1
                    else observations.get("structured_final_status"),
                    reported_thinking_tokens=usage.get("thoughts_token_count"),
                    thinking_usage_note="Provider-reported tokens only: zero is not proof of no reasoning; null means unreported.",
                    provider_error=record.get("error"),
                )
        templates.append(template)
    plan = read_json(directory / "plan.json")
    planned_ids = [
        diagnostic_call_id(case, trial, step)
        for trial in range(1, plan["trials"] + 1)
        for case in plan["cases"]
        for step in range(1, CASE_REQUEST_LIMITS[case] + 1)
    ]
    return {
        "calls": rows,
        "planned_call_count": len(planned_ids),
        "not_attempted_call_ids": [
            call_id
            for call_id in planned_ids
            if call_id not in expected or not expected[call_id]["generation_requests"]
        ],
        "attempted_generation_requests": sum(record["generation_requests"] for record in records),
        "assessment_basis": "Search requests are observed. Other yes/no judgments require attributed manual review; null is unreviewed.",
        "limitations": "Native controls replay saved evidence with explicit query mismatch reporting. K adds gap claims; L/M add a question-specific path. N/O jointly change calling mode and final representation. P/Q only remove second-request MIME/JSON-schema constraints. R/S only set second-request thinking to MEDIUM; zero reported thinking tokens does not establish no reasoning. Output-format and citation validity are not factual correctness. Repetitions are independent, not retries.",
    }, templates


def review_fields(case):
    if case == "K":
        return (
            *REVIEW_FIELDS,
            "requested_unnecessary_search",
            "identified_genuinely_missing_fact",
            "invented_missing_relationship",
            "investigated_unrelated_relationship",
        )
    return (
        (*REVIEW_FIELDS, "matches_expected_evidence_sufficiency_behavior")
        if case in STRUCTURED_CASES
        else REVIEW_FIELDS
    )


def new_record(case, trial, step, request, settings):
    mode = {
        "A": "forced_final_decision_no_search_tool",
        "E": "tools_absent_unforced",
        "F": "native_initial_question" if step == 1 else "native_post_replayed_tool_response",
        "I": "native_initial_question" if step == 1 else "native_post_controlled_subset",
        "J": "native_initial_question" if step == 1 else "native_post_controlled_subset",
        "K": "native_gap_reporting_initial" if step == 1 else "native_gap_reporting_post_subset",
        "L": "native_initial_question" if step == 1 else "native_post_controlled_subset",
        "M": "native_initial_question" if step == 1 else "native_post_controlled_subset",
        "N": "native_initial_question" if step == 1 else "native_two_function_decision",
        "O": "native_initial_question" if step == 1 else "native_two_function_decision",
        "P": "native_initial_question" if step == 1 else "native_unconstrained_text_decision",
        "Q": "native_initial_question" if step == 1 else "native_unconstrained_text_decision",
        "R": "native_initial_question" if step == 1 else "native_medium_thinking_decision",
        "S": "native_initial_question" if step == 1 else "native_medium_thinking_decision",
        "G": "structured_json_decision_no_native_tools",
        "H": "structured_json_decision_no_native_tools",
    }
    return {
        "schema_version": 2,
        "call_id": diagnostic_call_id(case, trial, step),
        "case": case,
        "trial": trial,
        "step": step,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "decision_mode": mode.get(case, "free_answer_or_search"),
        "expected_missing_relationship": "The father of Hylton Jolliffe, 3rd Baron Hylton (the subject's father)."
        if case == "D"
        else None,
        "case_limitation": "The unchanged v5 prompt still references searching despite absent tools; no forced-finalization suffix is added."
        if case == "E"
        else None,
        "request": serialize_request(request, settings) if request else None,
        "status": "not_executed",
        "generation_requests": 0,
        "response": None,
        "error": None,
    }


def save_record(args, record, secrets):
    sanitized = sanitize(record, secrets)
    sanitized["record_fingerprint"] = fingerprint(
        {key: value for key, value in sanitized.items() if key != "record_fingerprint"}
    )
    write_json(args.output / f"call-{record['call_id']}.json", sanitized)
    consolidated, templates = comparison(args.output)
    write_json(args.output / "comparison.json", consolidated)
    write_json(args.output / "review-template.json", templates)


async def submit_request(request, record, args, settings, evidence, ids, budget, secrets, client_factory):
    """Exactly one budgeted generateContent invocation; return original SDK response in memory."""
    path = args.output / f"call-{record['call_id']}.json"
    write_json(path, record, secrets)
    if budget["attempted"] >= args.max_generation_requests:
        raise RuntimeError("Generation-request cap reached")
    if budget["attempted"]:
        await asyncio.sleep(args.pause_seconds)
    client, native = None, None
    started = perf_counter()
    try:
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
        budget["attempted"] += 1
        record.update(status="request_started", generation_requests=1)
        write_json(path, record, secrets)
        async with asyncio.timeout(settings.generation_timeout_seconds):
            native = await client.aio.models.generate_content(**request)
        record["response"] = {"sdk_response_representation": native.model_dump(mode="json", exclude_none=False)}
        record["response"] = inspect_response(
            native, evidence["question"], evidence["reference_answer"], ids, case=record["case"], step=record["step"]
        )
        if record["case"] in CONTROLLED_NATIVE_CASES:
            protocol_errors = list(record["response"].get("protocol_errors", []))
            for candidate in native.candidates or []:
                content = candidate.content
                calls = [part for part in content.parts or [] if part.function_call is not None] if content else []
                if calls and content.role != "model":
                    protocol_errors.append("Native function-call content has an invalid role")
                if calls and request["model"].startswith("gemini-3") and not calls[0].thought_signature:
                    protocol_errors.append("Native function call lacks its required thought signature")
                if content and len(content.model_dump_json()) > 64000:
                    protocol_errors.append("Native model content exceeds the production bound")
            record["response"]["protocol_errors"] = protocol_errors
            if record["case"] in DUAL_FUNCTION_CASES and record["step"] == 2 and protocol_errors:
                record["response"]["automatic_observations"]["native_terminal_decision_valid"] = False
                record["response"]["automatic_observations"]["native_terminal_validation_errors"].extend(
                    protocol_errors
                )
        record["status"] = "response_received"
    except Exception as error:
        record["status"] = "failed"
        record["error"] = {
            "type": type(error).__name__,
            "http_status": error.code if isinstance(error, errors.APIError) else None,
            "message": "No retry performed; provider error bodies and exception strings are not exported.",
        }
        budget["rate_limited"] |= record["error"]["http_status"] == 429
    finally:
        record["duration_ms"] = (perf_counter() - started) * 1000
        if client is not None:
            try:
                try:
                    await client.aio.aclose()
                finally:
                    client.close()
            except Exception as error:
                record["cleanup_error"] = {"type": type(error).__name__}
    return native


async def run(args, settings, *, client_factory=genai.Client):
    if args.trials < 1 or len(set(args.cases)) != len(args.cases):
        raise ValueError("Use positive trials and distinct cases")
    planned = sum(CASE_REQUEST_LIMITS[case] for case in args.cases) * args.trials
    if args.max_generation_requests is not None and (
        args.max_generation_requests < 1 or planned > args.max_generation_requests
    ):
        raise ValueError("Planned calls exceed the explicit positive generation-request cap")
    if args.execute and args.max_generation_requests is None:
        raise ValueError("Execution requires --max-generation-requests")
    if args.execute and not settings.gemini_api_key:
        raise ValueError("Execution requires the configured GEMINI_API_KEY; never pass it on the command line")
    selected_model = (
        getattr(args, "model", None) if getattr(args, "model", None) is not None else settings.gemini_generation_model
    )
    if not isinstance(selected_model, str) or not selected_model.strip() or selected_model != selected_model.strip():
        raise ValueError("Configure GEMINI_GENERATION_MODEL as for production")
    if (
        not 1 <= settings.generation_timeout_seconds <= 120
        or not 128 <= settings.investigation_max_output_tokens <= 4096
    ):
        raise ValueError("Configured Investigation model settings fall outside production adapter limits")
    if not 0 <= args.pause_seconds <= 3600:
        raise ValueError("Pause must be between zero and 3600 seconds")
    evidence = load_evidence(args.source_run)
    secrets = tuple(
        value
        for value in (settings.gemini_api_key, getattr(settings, "db_password", None), os.getenv("ADMIN_API_TOKEN"))
        if value
    )
    args.output.mkdir(parents=True, exist_ok=False)
    plan = {
        "mode": "execute" if args.execute else "provider_free_preview",
        "planned_generation_requests": planned,
        "maximum_generation_requests": args.max_generation_requests,
        "trials": args.trials,
        "cases": args.cases,
        "generation_model": selected_model,
        "model_override": getattr(args, "model", None),
        "maximum_requests_per_case_trial": {case: CASE_REQUEST_LIMITS[case] for case in args.cases},
        "pause_between_calls_seconds": args.pause_seconds,
        "automatic_retries": False,
        "automatic_tool_execution": False,
        "source": evidence["source"],
        "question": evidence["question"],
        "reference_answer_not_sent_to_model": evidence["reference_answer"],
        "evidence_provenance": evidence["provenance"],
        "guidance_version": INVESTIGATION_GUIDANCE_VERSION,
        "production_instruction_sha256": sha256(SYSTEM_INSTRUCTION.encode()).hexdigest(),
        "alternative_instruction_sha256": sha256(ALTERNATIVE_SYSTEM_INSTRUCTION.encode()).hexdigest(),
        "structured_instruction_sha256": sha256(STRUCTURED_SYSTEM_INSTRUCTION.encode()).hexdigest(),
        "gap_instruction_sha256": sha256(GAP_SYSTEM_INSTRUCTION.encode()).hexdigest(),
        "relationship_path_instruction_sha256": sha256(RELATIONSHIP_PATH_INSTRUCTION.encode()).hexdigest(),
        "dual_function_instruction_sha256": sha256(DUAL_FUNCTION_INSTRUCTION.encode()).hexdigest(),
        "case_descriptions": CASE_DESCRIPTIONS,
        "embedding_requests": 0,
        "database_queries": 0,
        "google_genai_version": version("google-genai"),
    }
    write_json(args.output / "plan.json", plan, secrets)
    budget = {"attempted": 0, "rate_limited": False}
    for trial in range(1, args.trials + 1):
        for case in args.cases:
            request = make_request(case, evidence, settings)
            request["model"] = selected_model
            record = new_record(case, trial, 1, request, settings)
            ids = [ref.evidence_id for ref in supplied_refs(case, evidence)]
            native = None
            if args.execute:
                native = await submit_request(
                    request,
                    record,
                    args,
                    settings,
                    evidence,
                    [] if case in NATIVE_CASES else ids,
                    budget,
                    secrets,
                    client_factory,
                )
            save_record(args, record, secrets)
            if case in NATIVE_CASES:
                followup = new_record(case, trial, 2, None, settings)
                followup["request_template"] = {
                    "model": request["model"],
                    "config": followup_config(request["config"], case).model_dump(mode="json", exclude_none=False),
                    "history_roles": ["user", "model", "user"],
                    "deferred": "Requires the actual first SDK response Content and its original call ID/signature; none are invented in preview.",
                    "saved_result_query": evidence["search_query"],
                    "saved_evidence_ids": ids,
                    "replay_metadata_required": True,
                }
                second = None
                if not args.execute:
                    followup.update(
                        status="conditional_preview", stop_reason="Awaiting a valid first native search request"
                    )
                elif native is None or record["status"] != "response_received":
                    followup.update(status="not_attempted", stop_reason="initial_provider_or_inspection_error")
                else:
                    second, outcome = native_followup(native, request, evidence, case=case)
                    followup.update(outcome)
                    followup["status"] = "not_attempted" if second is None else "not_executed"
                if second is not None:
                    if case in CONTROLLED_NATIVE_CASES:
                        followup["supplied_evidence"] = [
                            {
                                "evidence_id": ref.evidence_id,
                                "source_id": ref.evidence.source.source_id,
                                "document_id": str(ref.evidence.document_id),
                                "document_version_id": str(ref.evidence.document_version_id),
                                "content_sha256": ref.evidence.content_sha256,
                                "start": ref.evidence.start,
                                "end": ref.evidence.end,
                                "source": asdict(ref.evidence.source),
                            }
                            for ref in supplied_refs(case, evidence)
                        ]
                    followup["request"] = serialize_request(second, settings)
                    await submit_request(
                        second, followup, args, settings, evidence, ids, budget, secrets, client_factory
                    )
                    followup["stop_reason"] = "second_request_finished_no_third_request_or_search_execution"
                save_record(args, followup, secrets)
            if budget["rate_limited"]:
                break
        if budget["rate_limited"]:
            break
    consolidated = read_json(args.output / "comparison.json")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "mode": plan["mode"],
                "planned_maximum_requests": planned,
                "generation_requests": budget["attempted"],
            }
        )
    )
    return 1 if any(row["status"] == "failed" for row in consolidated["calls"]) else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", nargs="+", choices=tuple(CASE_DESCRIPTIONS), default=list(CASE_DESCRIPTIONS))
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument(
        "--model", help="Diagnostic-only model override for both requests; leaves production settings unchanged"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Permit generation explicitly: at most two requests per native-history case, one for each other case",
    )
    parser.add_argument("--max-generation-requests", type=int)
    parser.add_argument("--pause-seconds", type=float, default=60)
    parser.add_argument("--source-run", type=Path, default=ROOT / "reports/evaluation/structured/jolliffe-v5")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "reports/generated/gemini-investigation-decisions"
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]),
    )
    parser.add_argument(
        "--summarize", type=Path, help="Offline: rebuild a comparison from an existing diagnostic directory"
    )
    parser.add_argument(
        "--reviews", type=Path, help="Offline: import an edited copy of review-template.json with --summarize"
    )
    args = parser.parse_args(argv)
    if args.summarize:
        if args.execute:
            parser.error("--summarize cannot be combined with --execute")
        result, _ = comparison(args.summarize, args.reviews)
        write_json(args.summarize / "comparison.json", result)
        return
    if args.reviews:
        parser.error("--reviews requires --summarize")
    from investigation_engine.infrastructure.config import settings

    try:
        result = asyncio.run(run(args, settings))
    except Exception as error:
        parser.exit(
            2,
            f"Diagnostic preparation failed ({type(error).__name__}); check settings, saved Jolliffe artifacts, cap and fresh output path.\n",
        )
    else:
        raise SystemExit(result)


if __name__ == "__main__":
    main()
