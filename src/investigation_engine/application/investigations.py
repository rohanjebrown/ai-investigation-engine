"""Provider-independent values for bounded, evidence-led investigations."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from math import isfinite
from time import perf_counter
from typing import TYPE_CHECKING

from investigation_engine.application.ports.generation_errors import GenerationResponseError
from investigation_engine.application.answers import MAX_QUESTION_CHARS
from investigation_engine.application.retrieval import Evidence, validate_search_question

if TYPE_CHECKING:
    from investigation_engine.application.ports.investigation_model import InvestigationModel


MAX_SEARCH_QUERY_CHARS = 500
MAX_CITATIONS = 30


class InvestigationStatus(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    INCOMPLETE = "incomplete"


class SearchDisposition(StrEnum):
    SUCCEEDED = "succeeded"
    EMPTY = "empty"
    FAILED = "failed"
    REPEATED_QUERY = "repeated_query"
    SEARCH_LIMIT = "search_limit"
    EMBEDDING_LIMIT = "embedding_limit"
    EVIDENCE_LIMIT = "evidence_limit"
    CONTEXT_LIMIT = "context_limit"


@dataclass(frozen=True, slots=True, kw_only=True)
class IdentifiedEvidence:
    evidence_id: str
    evidence: Evidence


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchExchange:
    query: str = field(repr=False)
    call_id: str
    evidence: tuple[IdentifiedEvidence, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class InvestigationTurn:
    question: str = field(repr=False)
    exchanges: tuple[SearchExchange, ...]

    def context_json(self) -> str:
        """Bounded, provider-neutral representation of the complete tool transcript."""
        return json.dumps({
            "question": self.question,
            "searches": [{
                "query": item.query,
                "results": [evidence_payload(hit) for hit in item.evidence],
            } for item in self.exchanges],
        }, ensure_ascii=False, separators=(",", ":"), default=str)


@dataclass(frozen=True, slots=True, kw_only=True)
class TokenUsage:
    prompt_tokens: int = 0
    candidate_tokens: int = 0
    thinking_tokens: int = 0

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 0 for value in (
            self.prompt_tokens, self.candidate_tokens, self.thinking_tokens,
        )):
            raise GenerationResponseError("Invalid provider token usage")


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchEvidenceCall:
    query: str = field(repr=False)
    call_id: str
    usage: TokenUsage = TokenUsage()
    model_version: str | None = None

    def __post_init__(self) -> None:
        validate_search_query(self.query)
        if not isinstance(self.call_id, str) or not self.call_id or len(self.call_id) > 128:
            raise GenerationResponseError("Tool call has an invalid call identifier")


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
        _validate_citation_ids(self.citations)
        if self.status == InvestigationStatus.ANSWERED:
            if not _valid_answer(self.answer) or not self.citations or self.conflicts:
                raise GenerationResponseError("Answered outcome requires answer text and citations")
        elif self.status == InvestigationStatus.INSUFFICIENT_EVIDENCE:
            if self.answer is not None or self.citations or self.conflicts:
                raise GenerationResponseError("Insufficient-evidence outcome cannot assert an answer")
        elif self.status == InvestigationStatus.CONFLICTING_EVIDENCE:
            if self.answer is not None or len(self.conflicts) < 2 or self.citations:
                raise GenerationResponseError("Conflicting outcome requires at least two cited claims")
            if any(not _valid_answer(item.claim) or not item.citations for item in self.conflicts):
                raise GenerationResponseError("Each conflicting claim requires evidence citations")
            for item in self.conflicts:
                _validate_citation_ids(item.citations)
        else:
            raise GenerationResponseError("Incomplete is an application resource-limit outcome")


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchRecord:
    sequence: int
    tool_name: str
    query: str = field(repr=False)
    evidence_ids: tuple[str, ...]
    result_count: int
    duration_ms: float
    succeeded: bool
    disposition: SearchDisposition

    def __post_init__(self) -> None:
        if self.sequence < 1 or self.tool_name != "search_evidence" or self.result_count < 0:
            raise ValueError("Invalid investigation search record")
        if not isfinite(self.duration_ms) or self.duration_ms < 0:
            raise ValueError("Invalid search duration")


@dataclass(frozen=True, slots=True, kw_only=True)
class InvestigationResult:
    status: InvestigationStatus
    answer: str | None = field(repr=False)
    citations: tuple[str, ...]
    conflicts: tuple[ConflictClaim, ...]
    evidence: tuple[IdentifiedEvidence, ...]
    searches: tuple[SearchRecord, ...]
    generation_requests: int
    embedding_requests: int
    message: str | None = None
    model_version: str | None = None
    usage: TokenUsage = TokenUsage()


class InvestigateQuestion:
    """Small bounded state machine that executes each model-requested search."""

    def __init__(
        self, search, model: InvestigationModel, *, max_turns: int = 6,
        max_search_calls: int = 5, max_results_per_search: int = 5,
        max_accumulated_evidence: int = 15, max_context_chars: int = 24000,
        max_generation_requests: int = 6, max_embedding_requests: int = 5,
    ) -> None:
        for name, value, low, high in (
            ("max_turns", max_turns, 1, 12), ("max_search_calls", max_search_calls, 1, 10),
            ("max_results_per_search", max_results_per_search, 1, 10),
            ("max_accumulated_evidence", max_accumulated_evidence, 1, 30),
            ("max_context_chars", max_context_chars, 512, 64000),
            ("max_generation_requests", max_generation_requests, 1, 12),
            ("max_embedding_requests", max_embedding_requests, 1, 12),
        ):
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer between {low} and {high}")
        self._search, self._model = search, model
        self._budgets = (
            max_turns, max_search_calls, max_results_per_search,
            max_accumulated_evidence, max_context_chars,
            max_generation_requests, max_embedding_requests,
        )

    async def execute(self, question: str, *, owner_id: str | None) -> InvestigationResult:
        validate_search_question(question)
        if len(question) > MAX_QUESTION_CHARS:
            raise ValueError("Question must contain no more than 4000 characters")
        (max_turns, max_searches, max_results, max_evidence, max_context,
         max_generations, max_embeddings) = self._budgets
        registry: dict[tuple, IdentifiedEvidence] = {}
        exchanges: list[SearchExchange] = []
        records: list[SearchRecord] = []
        query_results: dict[str, tuple[str, ...]] = {}
        generation_requests = embedding_requests = 0
        total_usage = TokenUsage()
        model_version = None

        def incomplete(message: str) -> InvestigationResult:
            return InvestigationResult(
                status=InvestigationStatus.INCOMPLETE, answer=None, citations=(), conflicts=(),
                evidence=tuple(registry.values()), searches=tuple(records),
                generation_requests=generation_requests, embedding_requests=embedding_requests,
                message=message, model_version=model_version, usage=total_usage,
            )

        for _ in range(max_turns):
            if generation_requests >= max_generations:
                return incomplete("Investigation stopped at the generation-request limit.")
            turn = InvestigationTurn(question=question, exchanges=tuple(exchanges))
            if len(turn.context_json()) > max_context:
                return incomplete("Investigation stopped at the evidence-context limit.")
            generation_requests += 1
            action = await self._model.next_action(turn)
            if not isinstance(action, (InvestigationDecision, SearchEvidenceCall)):
                raise GenerationResponseError("Investigation model returned an unknown action")
            if isinstance(action.usage, TokenUsage):
                total_usage = _sum_usage(total_usage, action.usage)
            model_version = action.model_version or model_version
            if isinstance(action, InvestigationDecision):
                _validate_decision_citations(action, registry)
                return InvestigationResult(
                    status=action.status, answer=action.answer, citations=action.citations,
                    conflicts=action.conflicts, evidence=tuple(registry.values()), searches=tuple(records),
                    generation_requests=generation_requests, embedding_requests=embedding_requests,
                    model_version=model_version, usage=total_usage,
                )
            if len(records) >= max_searches:
                records.append(SearchRecord(sequence=len(records) + 1, tool_name="search_evidence",
                    query=action.query, evidence_ids=(), result_count=0, duration_ms=0.0,
                    succeeded=False, disposition=SearchDisposition.SEARCH_LIMIT))
                return incomplete("Investigation stopped at the search-call limit.")
            if action.query in query_results:
                records.append(SearchRecord(
                    sequence=len(records) + 1, tool_name="search_evidence", query=action.query,
                    evidence_ids=query_results[action.query], result_count=0, duration_ms=0.0,
                    succeeded=False, disposition=SearchDisposition.REPEATED_QUERY,
                ))
                return incomplete("Investigation stopped because the model repeated an identical search.")
            if embedding_requests >= max_embeddings:
                records.append(SearchRecord(sequence=len(records) + 1, tool_name="search_evidence",
                    query=action.query, evidence_ids=(), result_count=0, duration_ms=0.0,
                    succeeded=False, disposition=SearchDisposition.EMBEDDING_LIMIT))
                return incomplete("Investigation stopped at the embedding-request limit.")
            embedding_requests += 1
            started = perf_counter()
            try:
                hits = await self._search.execute(action.query, max_results, owner_id=owner_id)
            except Exception:
                records.append(SearchRecord(sequence=len(records) + 1, tool_name="search_evidence",
                    query=action.query, evidence_ids=(), result_count=0,
                    duration_ms=(perf_counter() - started) * 1000, succeeded=False,
                    disposition=SearchDisposition.FAILED))
                raise
            elapsed = (perf_counter() - started) * 1000
            new_refs = []
            overflow = False
            for hit in hits:
                identity = (hit.document_version_id, hit.start, hit.end)
                reference = registry.get(identity)
                if reference is None:
                    if len(registry) >= max_evidence:
                        overflow = True
                        continue
                    reference = IdentifiedEvidence(evidence_id=f"E{len(registry) + 1}", evidence=replace(hit))
                    registry[identity] = reference
                new_refs.append(reference)
            ids = tuple(dict.fromkeys(item.evidence_id for item in new_refs))
            disposition = SearchDisposition.EVIDENCE_LIMIT if overflow else (
                SearchDisposition.EMPTY if not hits else SearchDisposition.SUCCEEDED
            )
            records.append(SearchRecord(
                sequence=len(records) + 1, tool_name="search_evidence", query=action.query,
                evidence_ids=ids, result_count=len(hits), duration_ms=elapsed,
                succeeded=True, disposition=disposition,
            ))
            query_results[action.query] = ids
            if overflow:
                return incomplete("Investigation stopped at the accumulated-evidence limit.")
            exchange = SearchExchange(
                query=action.query, call_id=action.call_id,
                evidence=tuple(registry[(hit.document_version_id, hit.start, hit.end)] for hit in hits),
            )
            exchanges.append(exchange)
            if len(InvestigationTurn(question=question, exchanges=tuple(exchanges)).context_json()) > max_context:
                return incomplete("Investigation stopped at the evidence-context limit.")

        return incomplete("Investigation stopped at the turn limit without a final conclusion.")


def validate_search_query(query: str) -> None:
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_SEARCH_QUERY_CHARS:
        raise GenerationResponseError("Search query must contain 1-500 characters")
    try:
        query.encode("utf-8")
    except UnicodeEncodeError:
        raise GenerationResponseError("Search query must be valid UTF-8 text") from None


def _valid_answer(value: object) -> bool:
    if not isinstance(value, str) or not value.strip() or len(value) > 8000:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _validate_citation_ids(citations: tuple[str, ...]) -> None:
    if not isinstance(citations, tuple) or len(citations) > MAX_CITATIONS:
        raise GenerationResponseError("Malformed citation list")
    if len(set(citations)) != len(citations) or any(
        not isinstance(item, str) or re.fullmatch(r"E[1-9][0-9]*", item) is None for item in citations
    ):
        raise GenerationResponseError("Malformed or duplicate citation identifiers")


def _validate_decision_citations(decision: InvestigationDecision, registry: dict[tuple, IdentifiedEvidence]) -> None:
    known = {item.evidence_id for item in registry.values()}
    identifiers = list(decision.citations)
    for claim in decision.conflicts:
        identifiers.extend(claim.citations)
    if any(item not in known for item in identifiers):
        raise GenerationResponseError("Investigation cites evidence outside the retrieved evidence registry")
    if decision.answer:
        for inline in re.findall(r"\[(E[^\]\r\n]*)\]", decision.answer):
            if inline not in decision.citations:
                raise GenerationResponseError("Answer contains an unlisted inline citation")
    for claim in decision.conflicts:
        for inline in re.findall(r"\[(E[^\]\r\n]*)\]", claim.claim):
            if inline not in claim.citations:
                raise GenerationResponseError("Conflict claim contains an unlisted inline citation")


def evidence_payload(item: IdentifiedEvidence) -> dict:
    hit = item.evidence
    return {
        "evidence_id": item.evidence_id,
        "excerpt": hit.text,
        "document_id": str(hit.document_id),
        "document_version_id": str(hit.document_version_id),
        "content_sha256": hit.content_sha256,
        "location": {"start": hit.start, "end": hit.end},
        "source": {
            "kind": hit.source.kind.value,
            "source_id": hit.source.source_id,
            "revision": hit.source.revision,
            "display_name": hit.source.display_name,
            "record_locator": hit.source.record_locator,
            "metadata": hit.source.metadata,
        },
        "source_spans": [
            {"start": span.start, "end": span.end,
             "locators": [{"kind": locator.kind.value, "index": locator.index} for locator in span.locators]}
            for span in hit.source_spans
        ],
    }


def _sum_usage(left: TokenUsage, right: TokenUsage) -> TokenUsage:
    return TokenUsage(prompt_tokens=left.prompt_tokens + right.prompt_tokens,
                      candidate_tokens=left.candidate_tokens + right.candidate_tokens,
                      thinking_tokens=left.thinking_tokens + right.thinking_tokens)
