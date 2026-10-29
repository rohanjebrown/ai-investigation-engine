"""Decision values and citation validation for investigations."""

import re
from dataclasses import dataclass, field

from investigation_engine.application.investigation_values import InvestigationStatus, TokenUsage
from investigation_engine.application.ports.generation_errors import GenerationResponseError

MAX_CITATIONS = 30
MAX_INVESTIGATION_ANSWER_CHARS = 8_000


@dataclass(frozen=True, slots=True, kw_only=True)
class ConflictClaim:
    claim: str
    citations: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class InvestigationDecision:
    status: InvestigationStatus
    answer: str | None = field(repr=False)
    citations: tuple[str, ...]
    conflicts: tuple[ConflictClaim, ...] = ()
    usage: TokenUsage = TokenUsage()
    model_version: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, InvestigationStatus) or not isinstance(self.usage, TokenUsage):
            raise GenerationResponseError("Invalid investigation decision")
        self._validate_citation_ids(self.citations)
        if self.status == InvestigationStatus.ANSWERED:
            self._validate_answered()
        elif self.status == InvestigationStatus.INSUFFICIENT_EVIDENCE:
            self._validate_insufficient()
        elif self.status == InvestigationStatus.CONFLICTING_EVIDENCE:
            self._validate_conflicting()
        else:
            raise GenerationResponseError("Incomplete is an application resource-limit outcome")

    def _valid_answer(self, answer_text: object) -> bool:
        # Preserve the short-circuit validation order for untrusted model output.
        if (
            not isinstance(answer_text, str)
            or not answer_text.strip()
            or len(answer_text) > MAX_INVESTIGATION_ANSWER_CHARS
        ):
            return False
        try:
            answer_text.encode("utf-8")
        except UnicodeEncodeError:
            return False
        return True

    def _validate_citation_ids(self, citations: tuple[str, ...]) -> None:
        if not isinstance(citations, tuple) or len(citations) > MAX_CITATIONS:
            raise GenerationResponseError("Malformed citation list")
        if len(set(citations)) != len(citations):
            raise GenerationResponseError("Malformed or duplicate citation identifiers")
        has_invalid_identifier = any(
            not isinstance(citation_id, str) or re.fullmatch(r"E[1-9][0-9]*", citation_id) is None
            for citation_id in citations
        )
        if has_invalid_identifier:
            raise GenerationResponseError("Malformed or duplicate citation identifiers")

    def _validate_answered(self) -> None:
        if not self._valid_answer(self.answer) or not self.citations or self.conflicts:
            raise GenerationResponseError("Answered outcome requires answer text and citations")

    def _validate_insufficient(self) -> None:
        if self.answer is not None or self.citations or self.conflicts:
            raise GenerationResponseError("Insufficient-evidence outcome cannot assert an answer")

    def _validate_conflicting(self) -> None:
        if self.answer is not None or len(self.conflicts) < 2 or self.citations:
            raise GenerationResponseError("Conflicting outcome requires at least two cited claims")
        for conflict_claim in self.conflicts:
            if not self._valid_answer(conflict_claim.claim) or not conflict_claim.citations:
                raise GenerationResponseError("Each conflicting claim requires evidence citations")
        for conflict_claim in self.conflicts:
            self._validate_citation_ids(conflict_claim.citations)


def _validate_inline_citations(text: str, citations: tuple[str, ...], error_message: str) -> None:
    for inline_citation in re.findall(r"\[(E[^\]\r\n]*)\]", text):
        if inline_citation not in citations:
            raise GenerationResponseError(error_message)
