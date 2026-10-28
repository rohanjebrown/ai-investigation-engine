import re
from dataclasses import replace

from investigation_engine.application.answers import (
    AnswerResult, AnswerStatus, GenerationEvidence, GenerationRequest, GenerationResult, GenerationUsage,
    INSUFFICIENT_MESSAGE, MAX_GENERATION_EVIDENCE, ResolvedCitation, validate_question,
)
from investigation_engine.application.ports.answer_generator import AnswerGenerator
from investigation_engine.application.ports.generation_errors import EvidenceContextLimitError, GenerationResponseError
from investigation_engine.application.use_cases.search_evidence import SearchEvidence


class AnswerQuestion:
    def __init__(
        self, search: SearchEvidence, generator: AnswerGenerator, *,
        max_evidence: int = 5, max_context_chars: int = 12000,
    ) -> None:
        if type(max_evidence) is not int or not 1 <= max_evidence <= MAX_GENERATION_EVIDENCE:
            raise ValueError("Evidence count must be an integer between 1 and 10")
        if type(max_context_chars) is not int or not 256 <= max_context_chars <= 64000:
            raise ValueError("Context budget must be an integer between 256 and 64000 characters")
        self._search, self._generator = search, generator
        self._max_evidence, self._max_context_chars = max_evidence, max_context_chars

    async def execute(self, question: str, *, owner_id: str | None) -> AnswerResult:
        validate_question(question)
        evidence = await self._search.execute(question, self._max_evidence, owner_id=owner_id)
        if not evidence:
            return self._abstain(embedding_requests=1)
        selected = {}
        inputs = []
        # Ranked, whole-item selection. Skip items that do not fit; never alter
        # excerpts or offsets. Bound the complete serialized question/context.
        for item in evidence[:self._max_evidence]:
            key = f"E{len(inputs) + 1}"
            candidate = GenerationEvidence(evidence_id=key, text=item.text)
            request = GenerationRequest(question=question, evidence=tuple(inputs + [candidate]))
            if len(request.context_json()) <= self._max_context_chars:
                inputs.append(candidate)
                selected[key] = replace(item)
        if not inputs:
            raise EvidenceContextLimitError("No intact evidence fits the configured context budget")
        request = GenerationRequest(question=question, evidence=tuple(inputs))
        result = await self._generator.generate(request)
        if not isinstance(result, GenerationResult):
            raise GenerationResponseError("Generator returned an invalid result type")
        # Revalidate even when a provider bypassed the dataclass constructor.
        result.__post_init__()
        resolved = tuple(ResolvedCitation(evidence_id=key, evidence=selected[key]) for key in selected)
        if result.status == AnswerStatus.INSUFFICIENT_EVIDENCE:
            return self._abstain(
                retrieved_evidence=resolved, generation_requests=result.provider_requests,
                embedding_requests=1, usage=result.usage, model_version=result.model_version,
            )
        if any(key not in selected for key in result.citations):
            raise GenerationResponseError("Answer cites evidence outside this question's context")
        # Structured references are authoritative; also reject spurious inline
        # [E...] markers rather than leaving misleading citation-looking text.
        for key in re.findall(r"\[(E[^\]\r\n]*)\]", result.answer):
            if key not in result.citations:
                raise GenerationResponseError("Answer contains an unlisted inline citation")
        return AnswerResult(status=result.status, answer=result.answer, citations=tuple(
            ResolvedCitation(evidence_id=key, evidence=selected[key]) for key in result.citations
        ), retrieved_evidence=resolved, generation_requests=result.provider_requests,
            embedding_requests=1, usage=result.usage, model_version=result.model_version)

    @staticmethod
    def _abstain(*, retrieved_evidence=(), generation_requests=0, embedding_requests=0,
                 usage=None, model_version=None) -> AnswerResult:
        return AnswerResult(
            status=AnswerStatus.INSUFFICIENT_EVIDENCE, answer=None, message=INSUFFICIENT_MESSAGE,
            retrieved_evidence=tuple(retrieved_evidence), generation_requests=generation_requests,
            embedding_requests=embedding_requests, usage=usage or GenerationUsage(), model_version=model_version,
        )
