"""Single-step RAG values and strict, SDK-independent response validation."""

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum

from investigation_engine.application.ports.generation_errors import GenerationResponseError
from investigation_engine.application.retrieval import Evidence

MAX_QUESTION_CHARS = 4000
MAX_ANSWER_CHARS = 8000
MAX_GENERATION_EVIDENCE = 10
INSUFFICIENT_MESSAGE = "The available sources do not establish an answer."


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerationUsage:
    prompt_tokens: int = 0
    candidate_tokens: int = 0
    thinking_tokens: int = 0

    def __post_init__(self) -> None:
        # Exact checks exclude bool, which subclasses int.
        if any(
            type(token_count) is not int or token_count < 0  # noqa: WPS516
            for token_count in (
                self.prompt_tokens,
                self.candidate_tokens,
                self.thinking_tokens,
            )
        ):
            raise GenerationResponseError("Invalid generation token usage")


def validate_question(question: str) -> None:
    # Keep the type, blank-text, and size checks in their original short-circuit order.
    if (
        not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION_CHARS  # noqa: WPS221
    ):
        raise ValueError("Question must contain 1-4000 characters and non-whitespace text")
    try:
        question.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Question must be valid UTF-8 text") from None


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerationEvidence:
    evidence_id: str
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerationRequest:
    question: str = field(repr=False)
    evidence: tuple[GenerationEvidence, ...]

    def context_json(self) -> str:
        """Exact user-context payload; no model-created source metadata."""
        return json.dumps(
            {
                "question": self.question,
                "evidence": [
                    {"evidence_id": evidence.evidence_id, "text": evidence.text} for evidence in self.evidence
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerationResult:
    status: AnswerStatus
    answer: str | None = field(repr=False)
    citations: tuple[str, ...]
    usage: GenerationUsage = GenerationUsage()
    provider_requests: int = 1
    model_version: str | None = None

    def __post_init__(self) -> None:
        _validate_generation_result_fields(self)
        _validate_generation_result_citations(self)
        _validate_generation_result_outcome(self)
        _validate_generation_result_encoding(self)


def generation_result(value: object) -> GenerationResult:  # noqa: WPS110 - keep the existing keyword parameter stable
    """Strict parsing, shared with adapter validation; no coercion or repair."""
    required_fields = {"status", "answer", "citations"}
    has_expected_shape = isinstance(value, dict) and set(value) == required_fields
    if not has_expected_shape:
        raise GenerationResponseError("Generation response has an invalid object shape")
    if not isinstance(value["citations"], list):
        raise GenerationResponseError("Generation citations must be an array")
    try:
        status = AnswerStatus(value["status"])
    except TypeError, ValueError:
        raise GenerationResponseError("Generation response has an invalid status") from None
    return GenerationResult(
        status=status,
        answer=value["answer"],
        citations=tuple(value["citations"]),
    )


def _validate_generation_result_fields(generation: GenerationResult) -> None:
    if (
        not isinstance(generation.status, AnswerStatus)
        or not isinstance(generation.citations, tuple)
        or not isinstance(generation.usage, GenerationUsage)
    ) or (
        type(generation.provider_requests) is not int  # noqa: WPS516 - reject bool request counts
        or generation.provider_requests < 1
        or (generation.model_version is not None and not isinstance(generation.model_version, str))
    ):
        raise GenerationResponseError("Invalid structured generation result")


def _validate_generation_result_citations(generation: GenerationResult) -> None:
    if len(generation.citations) > MAX_GENERATION_EVIDENCE:
        raise GenerationResponseError("Malformed or duplicate citation identifiers")
    if any(
        not isinstance(citation_id, str) or re.fullmatch(r"E[1-9][0-9]*", citation_id) is None
        for citation_id in generation.citations
    ):
        raise GenerationResponseError("Malformed or duplicate citation identifiers")
    if len(set(generation.citations)) != len(generation.citations):
        raise GenerationResponseError("Malformed or duplicate citation identifiers")


def _validate_generation_result_outcome(generation: GenerationResult) -> None:
    if generation.status == AnswerStatus.INSUFFICIENT_EVIDENCE:
        if generation.answer is not None or generation.citations:
            raise GenerationResponseError("Abstention must not contain an answer or citations")
        return
    has_invalid_answer = (
        not isinstance(generation.answer, str)
        or not generation.answer.strip()
        or len(generation.answer) > MAX_ANSWER_CHARS
        or not generation.citations
    )
    if has_invalid_answer:
        raise GenerationResponseError("An answer requires text and supporting citation identifiers")


def _validate_generation_result_encoding(generation: GenerationResult) -> None:
    if generation.answer is None:
        return
    try:
        generation.answer.encode("utf-8")
    except UnicodeEncodeError:
        raise GenerationResponseError("Answer must be valid UTF-8 text") from None


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedCitation:
    evidence_id: str
    evidence: Evidence


@dataclass(frozen=True, slots=True, kw_only=True)
class AnswerResult:
    status: AnswerStatus
    answer: str | None = field(repr=False)
    citations: tuple[ResolvedCitation, ...] = ()
    message: str | None = None
    retrieved_evidence: tuple[ResolvedCitation, ...] = ()
    generation_requests: int = 0
    embedding_requests: int = 0
    usage: GenerationUsage = GenerationUsage()
    model_version: str | None = None
