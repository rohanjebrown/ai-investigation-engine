"""Gemma 4 native-tool investigation provider using the shared Google GenAI lifecycle."""

import asyncio
import logging
import math
import random
import re
from email.utils import parsedate_to_datetime
from time import perf_counter

import httpx
from google.genai import errors, types

from investigation_engine.application.generation_budget import current_generation_budget
from investigation_engine.application.investigations import (
    ConflictClaim,
    InvestigationDecision,
    InvestigationStatus,
    InvestigationTurn,
    SearchEvidenceCall,
)
from investigation_engine.application.ports.generation_errors import (
    GenerationProviderError,
    GenerationResponseError,
    GenerationTimeoutError,
)
from investigation_engine.application.ports.provider_diagnostics import attach_diagnostic, safe_diagnostic
from investigation_engine.application.timing import log_event
from investigation_engine.infrastructure.generation.failure_logging import (
    capture_generation_failure,
    log_generation_failure,
)
from investigation_engine.infrastructure.generation.gemini_answer_generator import _retry_details
from investigation_engine.infrastructure.generation.gemini_investigation_model import (
    SYSTEM_INSTRUCTION,
    GoogleGenAIInvestigationModel,
    _prepared_retry_turn,
    _usage,
)

GEMMA4_MODEL = "gemma-4-26b-a4b-it"
_STATUS_LINE = re.compile(r"STATUS: (answered|insufficient_evidence|conflicting_evidence)")
_CITATION = re.compile(r"\[(E[1-9][0-9]*)\]")
_GROUPED_CITATIONS = re.compile(r"\[(E[1-9][0-9]*(?:\s*,\s*E[1-9][0-9]*)+)\]")

GEMMA4_FINAL_FORMAT = """
When you finalize instead of requesting search_evidence, return only this plain-text form.
For an answer, write exactly two sections:
STATUS: answered
ANSWER: <concise answer with supporting citations such as [E1] or [E1, E2]>
For insufficient evidence, write exactly one line:
STATUS: insufficient_evidence
For a genuine conflict, write:
STATUS: conflicting_evidence
CLAIM: <first incompatible claim with its citation, such as [E1]>
CLAIM: <second incompatible claim with its citation, such as [E2]>
Add one CLAIM line per distinct supported claim. Use only evidence IDs supplied by the application.
Do not add markdown fences, preambles, citation lists, or reasoning to a final response.
""".strip()


def _retry_event(event, **fields):
    try:
        log_event(event, **fields)
    except Exception:
        pass  # A logging failure must not alter the model outcome.


def _has_retry_hint(error):
    headers = getattr(getattr(error, "response", None), "headers", None)
    raw = headers.get("Retry-After") if headers is not None else None
    if isinstance(raw, str):
        try:
            value = float(raw)
            return math.isfinite(value) and value >= 0
        except ValueError:
            try:
                return parsedate_to_datetime(raw).tzinfo is not None
            except TypeError, ValueError, OverflowError:
                pass
    details = getattr(error, "details", None)
    envelope = details.get("error", details) if isinstance(details, dict) else {}
    entries = envelope.get("details", []) if isinstance(envelope, dict) else []
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and entry.get("@type") == "type.googleapis.com/google.rpc.RetryInfo":
            value = entry.get("retryDelay")
            if isinstance(value, str) and value.endswith("s"):
                try:
                    seconds = float(value[:-1])
                    return math.isfinite(seconds) and seconds >= 0
                except ValueError:
                    pass
    return False


class Gemma4InvestigationModel(GoogleGenAIInvestigationModel):
    """Gemma 4 configuration: tools only, provider-default tool selection, plain-text finals."""

    def __init__(self, *, retry_max_attempts=5, retry_total_timeout_seconds=None, **kwargs):
        if type(retry_max_attempts) is not int or not 1 <= retry_max_attempts <= 10:
            raise ValueError("Gemma retry attempts must be between 1 and 10")
        timeout = kwargs.get("timeout_seconds", 45)
        if type(timeout) is not int or not 1 <= timeout <= 120:
            raise ValueError("Investigation timeout must be between 1 and 120 seconds")
        total = timeout if retry_total_timeout_seconds is None else retry_total_timeout_seconds
        if not isinstance(total, (int, float)) or isinstance(total, bool) or not 0 < total <= timeout:
            raise ValueError("Gemma retry deadline must fit the generation timeout")
        super().__init__(**kwargs)
        self.retry_max_attempts = retry_max_attempts
        self.retry_total_timeout_seconds = float(total)
        self.retry_allowed = None
        self.before_retry = None

    async def next_action(self, turn):
        token = _prepared_retry_turn.set({})
        try:
            return await self._retry_action(turn)
        finally:
            _prepared_retry_turn.reset(token)

    async def _retry_action(self, turn):
        started = perf_counter()
        deadline = asyncio.get_running_loop().time() + self.retry_total_timeout_seconds
        step = len(turn.exchanges) + 1
        for attempt in range(1, self.retry_max_attempts + 1):
            remaining = deadline - asyncio.get_running_loop().time()
            captured = []
            try:
                with capture_generation_failure() as captured:
                    async with asyncio.timeout(remaining):
                        result = await super().next_action(turn)
            except Exception as error:
                failure = error
                if isinstance(error, TimeoutError):
                    failure = attach_diagnostic(
                        GenerationTimeoutError("Investigation generation request timed out"),
                        operation="generate_content",
                        model_id=self.model,
                        category="timeout",
                        request_count=self.provider_requests,
                        investigation_step=step,
                    )
                    if not captured:
                        captured.append(
                            dict(source_error=error, failure=failure, model=self.model, started=started, turn=step)
                        )
                diagnostic = safe_diagnostic(failure) or {}
                status = diagnostic.get("http_status")
                category = diagnostic.get("category")
                source = captured[0]["source_error"] if captured else None
                retryable = isinstance(failure, GenerationProviderError) and (
                    status in (500, 502, 503, 504)
                    or category == "timeout"
                    or category == "transport"
                    and isinstance(source, (httpx.NetworkError, httpx.RemoteProtocolError))
                )
                retry_after = 0.0
                if isinstance(source, errors.APIError):
                    daily, retry_after = _retry_details(source)
                    if daily:
                        retryable = False
                    elif status == 429:
                        retryable = _has_retry_hint(source)
                delay = max(random.uniform(0, min(8.0, 2.0 ** (attempt - 1))), retry_after) if retryable else 0.0
                remaining = deadline - asyncio.get_running_loop().time()
                attempt_budget = current_generation_budget.get()
                allowed = (attempt_budget is None or attempt_budget.used < attempt_budget.maximum) and (
                    self.retry_allowed is None or self.retry_allowed()
                )
                will_retry = retryable and attempt < self.retry_max_attempts and delay < remaining and allowed
                for item in captured:
                    log_generation_failure(
                        **item,
                        attempt=attempt,
                        max_attempts=self.retry_max_attempts,
                        will_retry=will_retry,
                        retry_delay_seconds=delay if will_retry else None,
                        retryable_override=retryable,
                    )
                if not will_retry:
                    if retryable:
                        _retry_event(
                            "generation.retry_exhausted",
                            level=logging.WARNING,
                            succeeded=False,
                            model_id=self.model,
                            provider="gemma4",
                            turn_number=step,
                            attempts=attempt,
                            max_attempts=self.retry_max_attempts,
                            reason=(
                                "attempt_limit"
                                if attempt >= self.retry_max_attempts
                                else "generation_budget"
                                if not allowed
                                else "deadline"
                            ),
                            duration_ms=(perf_counter() - started) * 1000,
                        )
                    if failure is not error:
                        raise failure from None
                    raise
                try:
                    async with asyncio.timeout(max(0, deadline - asyncio.get_running_loop().time())):
                        await asyncio.sleep(delay)
                        if self.before_retry is not None:
                            await self.before_retry(turn)
                except TimeoutError:
                    _retry_event(
                        "generation.retry_exhausted",
                        level=logging.WARNING,
                        succeeded=False,
                        model_id=self.model,
                        provider="gemma4",
                        turn_number=step,
                        attempts=attempt,
                        max_attempts=self.retry_max_attempts,
                        reason="deadline",
                        duration_ms=(perf_counter() - started) * 1000,
                    )
                    raise failure from None
                if deadline - asyncio.get_running_loop().time() <= 0:
                    _retry_event(
                        "generation.retry_exhausted",
                        level=logging.WARNING,
                        succeeded=False,
                        model_id=self.model,
                        provider="gemma4",
                        turn_number=step,
                        attempts=attempt,
                        max_attempts=self.retry_max_attempts,
                        reason="deadline",
                        duration_ms=(perf_counter() - started) * 1000,
                    )
                    raise failure
            else:
                if attempt > 1:
                    _retry_event(
                        "generation.retry_recovered",
                        model_id=self.model,
                        provider="gemma4",
                        turn_number=step,
                        attempts=attempt,
                        max_attempts=self.retry_max_attempts,
                        duration_ms=(perf_counter() - started) * 1000,
                    )
                return result
        raise AssertionError("unreachable retry boundary")

    def _build_config(self, turn: InvestigationTurn, tool: types.Tool) -> types.GenerateContentConfig:
        instruction = SYSTEM_INSTRUCTION + "\n\n" + GEMMA4_FINAL_FORMAT
        if turn.final_only:
            instruction += "\n\nfinal_only is enabled. Do not request another search; finalize from admitted evidence."
        return types.GenerateContentConfig(
            system_instruction=instruction,
            tools=[] if turn.final_only else [tool],
            max_output_tokens=self.max_output_tokens,
            candidate_count=1,
        )

    @classmethod
    def _parse_response(cls, response) -> SearchEvidenceCall | InvestigationDecision:
        # Retain compatibility with a valid structured final while using natural-language
        # output as Gemma's normal response contract. Native calls share strict validation.
        try:
            return super()._parse_response(response)
        except GenerationResponseError:
            return cls._parse_text_decision(response)

    @classmethod
    def _parse_text_decision(cls, response) -> InvestigationDecision:
        try:
            candidates = response.candidates
            if not candidates or len(candidates) != 1 or candidates[0].finish_reason != types.FinishReason.STOP:
                raise ValueError("generation did not complete normally")
            content = candidates[0].content
            if content.role != "model":
                raise ValueError("final content has an invalid role")
            parts = content.parts
            if any(getattr(part, "function_call", None) is not None for part in parts):
                raise ValueError("mixed tool and final response")
            visible = [
                part.text
                for part in parts
                if isinstance(getattr(part, "text", None), str)
                and part.text.strip()
                and not getattr(part, "thought", False)
            ]
            if len(visible) != 1 or len(visible[0]) > 12000:
                raise ValueError("expected one bounded final text")
            lines = visible[0].strip().splitlines()
            match = _STATUS_LINE.fullmatch(lines[0].strip()) if lines else None
            if match is None:
                raise ValueError("missing final status marker")
            status = InvestigationStatus(match.group(1))
            usage = _usage(response.usage_metadata)
            model_version = getattr(response, "model_version", None)

            if status == InvestigationStatus.INSUFFICIENT_EVIDENCE:
                if len(lines) != 1:
                    raise ValueError("insufficient-evidence final has unexpected content")
                return InvestigationDecision(
                    status=status,
                    answer=None,
                    citations=(),
                    usage=usage,
                    model_version=model_version,
                )

            if status == InvestigationStatus.ANSWERED:
                if len(lines) < 2 or not lines[1].startswith("ANSWER: "):
                    raise ValueError("answered final is missing its answer")
                answer = _normalize_grouped_citations("\n".join((lines[1][len("ANSWER: ") :], *lines[2:])).strip())
                citations = tuple(dict.fromkeys(_CITATION.findall(answer)))
                return InvestigationDecision(
                    status=status,
                    answer=answer,
                    citations=citations,
                    usage=usage,
                    model_version=model_version,
                )

            claims = []
            for line in lines[1:]:
                if not line.startswith("CLAIM: "):
                    raise ValueError("conflicting final contains an unsupported line")
                claim = _normalize_grouped_citations(line[len("CLAIM: ") :].strip())
                citations = tuple(dict.fromkeys(_CITATION.findall(claim)))
                claims.append(ConflictClaim(claim=claim, citations=citations))
            return InvestigationDecision(
                status=status,
                answer=None,
                citations=(),
                conflicts=tuple(claims),
                usage=usage,
                model_version=model_version,
            )
        except AttributeError, IndexError, KeyError, TypeError, ValueError, GenerationResponseError:
            raise GenerationResponseError(
                "Gemma investigation model returned malformed or unsupported output"
            ) from None


def _normalize_grouped_citations(text: str) -> str:
    """Expand a grouped citation list without changing or inferring evidence IDs."""
    return _GROUPED_CITATIONS.sub(
        lambda match: " ".join(f"[{value.strip()}]" for value in match.group(1).split(",")), text
    )
