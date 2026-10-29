"""Orchestrate bounded, evidence-led investigations."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from time import perf_counter
from typing import TYPE_CHECKING

from investigation_engine.application import investigation_decisions as _decisions
from investigation_engine.application import investigation_records as _records
from investigation_engine.application import investigation_values as _investigation_values
from investigation_engine.application.answers import MAX_QUESTION_CHARS
from investigation_engine.application.ports.generation_errors import GenerationResponseError
from investigation_engine.application.retrieval import Evidence, validate_search_question

NO_SEARCH_DURATION_MS = 0.0  # noqa: WPS358 - float duration for unperformed searches
MAX_CITATIONS = _decisions.MAX_CITATIONS
MAX_INVESTIGATION_ANSWER_CHARS = _decisions.MAX_INVESTIGATION_ANSWER_CHARS
ConflictClaim = _decisions.ConflictClaim
InvestigationDecision = _decisions.InvestigationDecision
_validate_inline_citations = _decisions._validate_inline_citations
IdentifiedEvidence = _records.IdentifiedEvidence
InvestigationResult = _records.InvestigationResult
InvestigationTurn = _records.InvestigationTurn
SearchExchange = _records.SearchExchange
SearchRecord = _records.SearchRecord
evidence_payload = _records.evidence_payload
MAX_SEARCH_QUERY_CHARS = _investigation_values.MAX_SEARCH_QUERY_CHARS
MAX_TOOL_CALL_ID_CHARS = _investigation_values.MAX_TOOL_CALL_ID_CHARS
SEARCH_EVIDENCE_TOOL_NAME = _investigation_values.SEARCH_EVIDENCE_TOOL_NAME
InvestigationStatus = _investigation_values.InvestigationStatus
SearchDisposition = _investigation_values.SearchDisposition
SearchEvidenceCall = _investigation_values.SearchEvidenceCall
TokenUsage = _investigation_values.TokenUsage
validate_search_query = _investigation_values.validate_search_query

if TYPE_CHECKING:
    from investigation_engine.application.ports.investigation_model import InvestigationModel


@dataclass(slots=True)
class _InvestigationState:
    """Mutable state carried across the bounded sequence of model turns."""

    registry: dict[tuple, IdentifiedEvidence] = field(default_factory=dict)
    exchanges: list[SearchExchange] = field(default_factory=list)
    records: list[SearchRecord] = field(default_factory=list)
    query_results: dict[str, tuple[str, ...]] = field(default_factory=dict)
    generation_requests: int = 0
    embedding_requests: int = 0
    total_usage: TokenUsage = TokenUsage()
    model_version: str | None = None


def _incomplete_result(
    registry: dict[tuple, IdentifiedEvidence],
    records: list[SearchRecord],
    generation_requests: int,
    embedding_requests: int,
    model_version: str | None,
    usage: TokenUsage,
    message: str,
) -> InvestigationResult:
    return InvestigationResult(
        status=InvestigationStatus.INCOMPLETE,
        answer=None,
        citations=(),
        conflicts=(),
        evidence=tuple(registry.values()),
        searches=tuple(records),
        generation_requests=generation_requests,
        embedding_requests=embedding_requests,
        message=message,
        model_version=model_version,
        usage=usage,
    )


def _decision_result(
    decision: InvestigationDecision,
    registry: dict[tuple, IdentifiedEvidence],
    records: list[SearchRecord],
    generation_requests: int,
    embedding_requests: int,
    model_version: str | None,
    usage: TokenUsage,
) -> InvestigationResult:
    return InvestigationResult(
        status=decision.status,
        answer=decision.answer,
        citations=decision.citations,
        conflicts=decision.conflicts,
        evidence=tuple(registry.values()),
        searches=tuple(records),
        generation_requests=generation_requests,
        embedding_requests=embedding_requests,
        model_version=model_version,
        usage=usage,
    )


def _record_search_rejection(
    records: list[SearchRecord],
    query: str,
    query_results: dict[str, tuple[str, ...]],
    max_searches: int,
    embedding_requests: int,
    max_embeddings: int,
) -> str | None:
    previous_results = query_results.get(query)
    if len(records) >= max_searches:
        disposition = SearchDisposition.SEARCH_LIMIT
        evidence_ids = ()
        message = "Investigation stopped at the search-call limit."
    elif previous_results is not None:  # noqa: WPS504 - an empty tuple still marks a repeated query
        disposition = SearchDisposition.REPEATED_QUERY
        evidence_ids = previous_results
        message = "Investigation stopped because the model repeated an identical search."
    elif embedding_requests >= max_embeddings:
        disposition = SearchDisposition.EMBEDDING_LIMIT
        evidence_ids = ()
        message = "Investigation stopped at the embedding-request limit."
    else:
        return None

    records.append(
        SearchRecord(
            sequence=len(records) + 1,
            tool_name=SEARCH_EVIDENCE_TOOL_NAME,
            query=query,
            evidence_ids=evidence_ids,
            result_count=0,
            duration_ms=NO_SEARCH_DURATION_MS,
            succeeded=False,
            disposition=disposition,
        )
    )
    return message


def _record_search_results(
    action: SearchEvidenceCall,
    hits: tuple[Evidence, ...],
    duration_ms: float,
    registry: dict[tuple, IdentifiedEvidence],
    max_evidence: int,
    records: list[SearchRecord],
    query_results: dict[str, tuple[str, ...]],
) -> tuple[SearchExchange, bool]:
    new_references, overflow = _collect_evidence_references(registry, hits, max_evidence)
    evidence_ids = tuple(dict.fromkeys(reference.evidence_id for reference in new_references))
    if overflow:
        disposition = SearchDisposition.EVIDENCE_LIMIT
    elif hits:
        disposition = SearchDisposition.SUCCEEDED
    else:
        disposition = SearchDisposition.EMPTY
    records.append(
        SearchRecord(
            sequence=len(records) + 1,
            tool_name=SEARCH_EVIDENCE_TOOL_NAME,
            query=action.query,
            evidence_ids=evidence_ids,
            result_count=len(hits),
            duration_ms=duration_ms,
            succeeded=True,
            disposition=disposition,
        )
    )
    query_results[action.query] = evidence_ids
    return SearchExchange(query=action.query, call_id=action.call_id, evidence=new_references), overflow


class InvestigateQuestion:
    """Small bounded state machine that executes each model-requested search."""

    def __init__(  # noqa: WPS211 - preserve the established per-budget constructor API
        self,
        search,
        model: InvestigationModel,
        *,
        max_turns: int = 6,
        max_search_calls: int = 5,
        max_results_per_search: int = 5,
        max_accumulated_evidence: int = 15,
        max_context_chars: int = 24000,
        max_generation_requests: int = 6,
        max_embedding_requests: int = 5,
    ) -> None:
        for name, budget, low, high in (
            ("max_turns", max_turns, 1, 12),
            ("max_search_calls", max_search_calls, 1, 10),
            ("max_results_per_search", max_results_per_search, 1, 10),
            ("max_accumulated_evidence", max_accumulated_evidence, 1, 30),
            ("max_context_chars", max_context_chars, 512, 64000),
            ("max_generation_requests", max_generation_requests, 1, 12),
            ("max_embedding_requests", max_embedding_requests, 1, 12),
        ):
            if type(budget) is not int or not low <= budget <= high:  # noqa: WPS516 - reject bool and integer subclasses
                raise ValueError(f"{name} must be an integer between {low} and {high}")
        self._search = search
        self._model = model
        self._max_turns = max_turns
        self._max_searches = max_search_calls
        self._max_results = max_results_per_search
        self._max_evidence = max_accumulated_evidence
        self._max_context = max_context_chars
        self._max_generations = max_generation_requests
        self._max_embeddings = max_embedding_requests

    async def execute(self, question: str, *, owner_id: str | None) -> InvestigationResult:
        validate_search_question(question)
        if len(question) > MAX_QUESTION_CHARS:
            raise ValueError("Question must contain no more than 4000 characters")
        state = _InvestigationState()

        for _ in range(self._max_turns):
            if state.generation_requests >= self._max_generations:
                return _incomplete_result(
                    state.registry,
                    state.records,
                    state.generation_requests,
                    state.embedding_requests,
                    state.model_version,
                    state.total_usage,
                    "Investigation stopped at the generation-request limit.",
                )
            turn = InvestigationTurn(question=question, exchanges=tuple(state.exchanges))
            if len(turn.context_json()) > self._max_context:
                return _incomplete_result(
                    state.registry,
                    state.records,
                    state.generation_requests,
                    state.embedding_requests,
                    state.model_version,
                    state.total_usage,
                    "Investigation stopped at the evidence-context limit.",
                )
            state.generation_requests += 1
            action = await self._request_action(turn, state)  # noqa: WPS476 - each turn needs the prior transcript
            if isinstance(action, InvestigationDecision):
                _validate_decision_citations(action, state.registry)
                return _decision_result(
                    action,
                    state.registry,
                    state.records,
                    state.generation_requests,
                    state.embedding_requests,
                    state.model_version,
                    state.total_usage,
                )
            incomplete = await self._process_search_action(  # noqa: WPS476 - the model selects this sequential search
                action,
                state,
                question,
                owner_id,
            )
            if incomplete is not None:
                return incomplete

        return _incomplete_result(
            state.registry,
            state.records,
            state.generation_requests,
            state.embedding_requests,
            state.model_version,
            state.total_usage,
            "Investigation stopped at the turn limit without a final conclusion.",
        )

    async def _request_action(
        self,
        turn: InvestigationTurn,
        state: _InvestigationState,
    ) -> InvestigationDecision | SearchEvidenceCall:
        action = await self._model.next_action(turn)
        if not isinstance(action, (InvestigationDecision, SearchEvidenceCall)):
            raise GenerationResponseError("Investigation model returned an unknown action")
        if isinstance(action.usage, TokenUsage):
            state.total_usage = _sum_usage(state.total_usage, action.usage)
        state.model_version = action.model_version or state.model_version
        return action

    async def _process_search_action(
        self,
        action: SearchEvidenceCall,
        state: _InvestigationState,
        question: str,
        owner_id: str | None,
    ) -> InvestigationResult | None:
        rejection = _record_search_rejection(
            state.records,
            action.query,
            state.query_results,
            self._max_searches,
            state.embedding_requests,
            self._max_embeddings,
        )
        if rejection is not None:
            return _incomplete_result(
                state.registry,
                state.records,
                state.generation_requests,
                state.embedding_requests,
                state.model_version,
                state.total_usage,
                rejection,
            )

        state.embedding_requests += 1
        hits, elapsed = await self._search_with_record(
            action.query,
            self._max_results,
            owner_id,
            state.records,
        )
        exchange, overflow = _record_search_results(
            action,
            hits,
            elapsed,
            state.registry,
            self._max_evidence,
            state.records,
            state.query_results,
        )
        if overflow:
            return _incomplete_result(
                state.registry,
                state.records,
                state.generation_requests,
                state.embedding_requests,
                state.model_version,
                state.total_usage,
                "Investigation stopped at the accumulated-evidence limit.",
            )

        state.exchanges.append(exchange)
        turn_context = InvestigationTurn(question=question, exchanges=tuple(state.exchanges)).context_json()
        if len(turn_context) > self._max_context:
            return _incomplete_result(
                state.registry,
                state.records,
                state.generation_requests,
                state.embedding_requests,
                state.model_version,
                state.total_usage,
                "Investigation stopped at the evidence-context limit.",
            )
        return None

    async def _search_with_record(
        self,
        query: str,
        max_results: int,
        owner_id: str | None,
        records: list[SearchRecord],
    ) -> tuple[tuple[Evidence, ...], float]:
        started = perf_counter()
        try:
            hits = await self._search.execute(query, max_results, owner_id=owner_id)
        except Exception:
            records.append(
                SearchRecord(
                    sequence=len(records) + 1,
                    tool_name=SEARCH_EVIDENCE_TOOL_NAME,
                    query=query,
                    evidence_ids=(),
                    result_count=0,
                    duration_ms=(perf_counter() - started) * 1000,
                    succeeded=False,
                    disposition=SearchDisposition.FAILED,
                )
            )
            raise
        return hits, (perf_counter() - started) * 1000


def _validate_decision_citations(decision: InvestigationDecision, registry: dict[tuple, IdentifiedEvidence]) -> None:
    known = {evidence_ref.evidence_id for evidence_ref in registry.values()}
    identifiers = list(decision.citations)
    for claim in decision.conflicts:
        identifiers.extend(claim.citations)
    if any(citation_id not in known for citation_id in identifiers):
        raise GenerationResponseError("Investigation cites evidence outside the retrieved evidence registry")
    if decision.answer:
        _validate_inline_citations(
            decision.answer,
            decision.citations,
            "Answer contains an unlisted inline citation",
        )
    for claim in decision.conflicts:
        _validate_inline_citations(
            claim.claim,
            claim.citations,
            "Conflict claim contains an unlisted inline citation",
        )


def _collect_evidence_references(
    registry: dict[tuple, IdentifiedEvidence],
    hits: tuple[Evidence, ...],
    max_evidence: int,
) -> tuple[tuple[IdentifiedEvidence, ...], bool]:
    """Register evidence in retrieval order and report any capacity overflow."""
    references = []
    overflow = False
    for hit in hits:
        identity = (hit.document_version_id, hit.start, hit.end)
        if identity not in registry:
            if len(registry) >= max_evidence:
                overflow = True
                continue
            evidence_number = len(registry) + 1
            evidence_id = f"E{evidence_number}"
            registry[identity] = IdentifiedEvidence(evidence_id=evidence_id, evidence=replace(hit))
        references.append(registry[identity])
    return tuple(references), overflow


def _sum_usage(left: TokenUsage, right: TokenUsage) -> TokenUsage:
    return TokenUsage(
        prompt_tokens=left.prompt_tokens + right.prompt_tokens,
        candidate_tokens=left.candidate_tokens + right.candidate_tokens,
        thinking_tokens=left.thinking_tokens + right.thinking_tokens,
    )
