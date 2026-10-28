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
        if any(type(value) is not int or value < 0 for value in (
            self.prompt_tokens, self.candidate_tokens, self.thinking_tokens,
        )):
            raise GenerationResponseError("Invalid generation token usage")


def validate_question(question: str) -> None:
    if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION_CHARS:
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
        return json.dumps({"question": self.question, "evidence": [
            {"evidence_id": item.evidence_id, "text": item.text} for item in self.evidence
        ]}, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerationResult:
    status: AnswerStatus
    answer: str | None = field(repr=False)
    citations: tuple[str, ...]
    usage: GenerationUsage = GenerationUsage()
    provider_requests: int = 1
    model_version: str | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.status, AnswerStatus) or not isinstance(self.citations, tuple)
            or not isinstance(self.usage, GenerationUsage) or type(self.provider_requests) is not int
            or self.provider_requests < 1
            or (self.model_version is not None and not isinstance(self.model_version, str))):
            raise GenerationResponseError("Invalid structured generation result")
        if len(self.citations) > MAX_GENERATION_EVIDENCE or any(
            not isinstance(item, str) or re.fullmatch(r"E[1-9][0-9]*", item) is None
            for item in self.citations
        ) or len(set(self.citations)) != len(self.citations):
            raise GenerationResponseError("Malformed or duplicate citation identifiers")
        if self.status == AnswerStatus.INSUFFICIENT_EVIDENCE:
            if self.answer is not None or self.citations:
                raise GenerationResponseError("Abstention must not contain an answer or citations")
        elif not isinstance(self.answer, str) or not self.answer.strip() or len(self.answer) > MAX_ANSWER_CHARS or not self.citations:
            raise GenerationResponseError("An answer requires text and supporting citation identifiers")
        if self.answer is not None:
            try:
                self.answer.encode("utf-8")
            except UnicodeEncodeError:
                raise GenerationResponseError("Answer must be valid UTF-8 text") from None


def generation_result(value: object) -> GenerationResult:
    """Strict parsing, shared with adapter validation; no coercion or repair."""
    if not isinstance(value, dict) or set(value) != {"status", "answer", "citations"}:
        raise GenerationResponseError("Generation response has an invalid object shape")
    if not isinstance(value["citations"], list):
        raise GenerationResponseError("Generation citations must be an array")
    try:
        status = AnswerStatus(value["status"])
    except (TypeError, ValueError):
        raise GenerationResponseError("Generation response has an invalid status") from None
    return GenerationResult(status=status, answer=value["answer"], citations=tuple(value["citations"]))


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
