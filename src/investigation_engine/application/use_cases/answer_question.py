import logging
import re
from dataclasses import replace
from itertools import zip_longest

from investigation_engine.application import answers as answer_types
from investigation_engine.application.ports.answer_generator import AnswerGenerator
from investigation_engine.application.ports.generation_errors import EvidenceContextLimitError, GenerationResponseError
from investigation_engine.application.timing import stage_timing, timed
from investigation_engine.application.use_cases.search_evidence import SearchEvidence

logger = logging.getLogger(__name__)

MIN_CONTEXT_CHARS = 256
MAX_CONTEXT_CHARS = 64_000
CONTEXT_BUDGET_ERROR = "Context budget must be an integer between {} and {} characters"


def _independent_queries(question):
    """Split only two explicit interrogative clauses; leave dependent references intact.

    This is bounded query decomposition, not a general natural-language planner.
    Questions needing a discovered entity remain the investigation workflow's job.
    """
    clauses = re.split(r",?\s+and\s+(?=(?:who|what|which|where|when|how)\b)", question, flags=re.IGNORECASE)
    if len(clauses) != 2 or re.search(
        r"\b(?:he|she|it|they|his|its|their|that|this|those)\b", clauses[1], re.IGNORECASE
    ):
        return (question,)
    return tuple(clauses)


def _balanced_evidence(channels):
    seen = set()
    selected = []
    for row in zip_longest(*channels):
        for hit in row:
            if hit is None:
                continue
            identity = (hit.content_sha256, hit.start, hit.end)
            if identity not in seen:
                selected.append(hit)
                seen.add(identity)
    return tuple(selected)


def _select_generation_evidence(question, evidence, max_evidence, max_context_chars):
    selected = {}
    inputs = []

    # Ranked, whole-item selection. Skip items that do not fit; never alter
    # excerpts or offsets. Bound the complete serialized question/context.
    for retrieved in evidence[:max_evidence]:
        evidence_number = len(inputs) + 1
        evidence_id = f"E{evidence_number}"
        candidate = answer_types.GenerationEvidence(evidence_id=evidence_id, text=retrieved.text)
        request = answer_types.GenerationRequest(question=question, evidence=tuple(inputs + [candidate]))

        if len(request.context_json()) <= max_context_chars:
            inputs.append(candidate)
            selected[evidence_id] = replace(retrieved)

    return answer_types.GenerationRequest(question=question, evidence=tuple(inputs)), selected


def _validate_generated_result(generated):
    if not isinstance(generated, answer_types.GenerationResult):
        logger.error(
            "Question generation failed: invalid result type=%s",
            type(generated).__name__,
        )
        raise GenerationResponseError("Generator returned an invalid result type")

    # Revalidate even when a provider bypassed the dataclass constructor.
    try:
        generated.__post_init__()
    except Exception as error:
        logger.error(
            "Question generation failed: result validation raised %s",
            type(error).__name__,
        )
        raise

    return generated


def _resolve_citations(evidence_ids, selected):
    return tuple(
        answer_types.ResolvedCitation(evidence_id=evidence_id, evidence=selected[evidence_id])
        for evidence_id in evidence_ids
    )


def _validate_citation_references(generated, selected):
    if any(evidence_id not in selected for evidence_id in generated.citations):
        logger.error("Question generation failed: citations reference unselected evidence")
        raise GenerationResponseError("Answer cites evidence outside this question's context")

    # Structured references are authoritative; reject spurious inline markers.
    for evidence_id in re.findall(r"\[(E[^\]\r\n]*)\]", generated.answer):
        if evidence_id not in generated.citations:
            logger.error("Question generation failed: unlisted inline citation")
            raise GenerationResponseError("Answer contains an unlisted inline citation")


class AnswerQuestion:
    def __init__(
        self,
        search: SearchEvidence,
        generator: AnswerGenerator,
        *,
        max_evidence: int = 5,
        max_context_chars: int = 12000,
    ) -> None:
        if type(max_evidence) is not int or not 1 <= max_evidence <= answer_types.MAX_GENERATION_EVIDENCE:  # noqa: WPS516 - reject bool and int subclasses
            raise ValueError("Evidence count must be an integer between 1 and 10")
        if type(max_context_chars) is not int or not MIN_CONTEXT_CHARS <= max_context_chars <= MAX_CONTEXT_CHARS:  # noqa: WPS516 - reject bool and int subclasses
            raise ValueError(CONTEXT_BUDGET_ERROR.format(MIN_CONTEXT_CHARS, MAX_CONTEXT_CHARS))
        self._search = search
        self._generator = generator
        self._max_evidence = max_evidence
        self._max_context_chars = max_context_chars

    @timed("answer.total")
    async def execute(self, question: str, *, owner_id: str | None) -> answer_types.AnswerResult:
        answer_types.validate_question(question)

        queries = _independent_queries(question)
        channels = []
        for query in queries:
            channels.append(await self._search.execute(query, self._max_evidence, owner_id=owner_id))
        evidence = _balanced_evidence(channels)
        embedding_requests = len(queries)

        logger.info(
            "Question evidence retrieval completed: retrieved=%d",
            len(evidence),
        )

        if not evidence:
            logger.info("Question answering abstained: no evidence retrieved")
            return self._abstain(embedding_requests=embedding_requests)

        with stage_timing("evidence_selection"):
            request, selected = _select_generation_evidence(
                question,
                evidence,
                self._max_evidence,
                self._max_context_chars,
            )

        logger.info(
            "Question evidence selection completed: retrieved=%d selected=%d",
            len(evidence),
            len(request.evidence),
        )

        if not request.evidence:
            logger.warning("Question answering failed: no evidence fits context budget")
            raise EvidenceContextLimitError("No intact evidence fits the configured context budget")

        with stage_timing("answer_generation"):
            generated = _validate_generated_result(await self._generator.generate(request))

        logger.info(
            "Question generation completed: status=%s citations=%d provider_requests=%d",
            generated.status.value,
            len(generated.citations),
            generated.provider_requests,
        )

        resolved = _resolve_citations(selected, selected)

        if generated.status == answer_types.AnswerStatus.INSUFFICIENT_EVIDENCE:
            logger.info("Question answering abstained: insufficient evidence")

            return self._abstain(
                retrieved_evidence=resolved,
                generation_requests=generated.provider_requests,
                embedding_requests=embedding_requests,
                usage=generated.usage,
                model_version=generated.model_version,
            )

        with stage_timing("citation_validation"):
            _validate_citation_references(generated, selected)

        logger.info(
            "Question answering succeeded: citations=%d",
            len(generated.citations),
        )

        return answer_types.AnswerResult(
            status=generated.status,
            answer=generated.answer,
            citations=_resolve_citations(generated.citations, selected),
            retrieved_evidence=resolved,
            generation_requests=generated.provider_requests,
            embedding_requests=embedding_requests,
            usage=generated.usage,
            model_version=generated.model_version,
        )

    def _abstain(
        self, *, retrieved_evidence=(), generation_requests=0, embedding_requests=0, usage=None, model_version=None
    ) -> answer_types.AnswerResult:
        return answer_types.AnswerResult(
            status=answer_types.AnswerStatus.INSUFFICIENT_EVIDENCE,
            answer=None,
            message=answer_types.INSUFFICIENT_MESSAGE,
            retrieved_evidence=tuple(retrieved_evidence),
            generation_requests=generation_requests,
            embedding_requests=embedding_requests,
            usage=usage or answer_types.GenerationUsage(),
            model_version=model_version,
        )
