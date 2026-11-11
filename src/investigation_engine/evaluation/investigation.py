"""Observe the real Investigation search/model ports; no orchestration or gold inputs."""

from dataclasses import asdict
from time import perf_counter

from investigation_engine.application.investigations import InvestigationDecision, SearchEvidenceCall
from investigation_engine.evaluation.metrics import evidence_record, fact_covered, statistics
from investigation_engine.evaluation.telemetry import _current

INVESTIGATION_MODES = ("investigation", "investigation_semantic", "investigation_lexical", "investigation_hybrid")


def investigation_request_plan(questions, retrieval_modes, budgets, *, repetitions=1, warmups=0, generation_attempts=1):
    turns = min(budgets["max_turns"], budgets["max_generation_requests"])
    generation_rpcs = min(budgets["max_turns"] * generation_attempts, budgets["max_generation_requests"])
    searches = min(turns, budgets["max_search_calls"], budgets["max_embedding_requests"])
    attempts = questions * (repetitions + warmups)
    misses = attempts * searches * sum(mode != "lexical" for mode in retrieval_modes)
    return {
        "questions_per_configuration": questions,
        "retrieval_configurations": list(retrieval_modes),
        "attempts_including_warmups": attempts * len(retrieval_modes),
        "maximum_generation_rpcs": attempts * len(retrieval_modes) * generation_rpcs,
        "maximum_dynamic_query_cache_misses": misses,
        "maximum_embedding_count_tokens_rpcs": misses,
        "maximum_embedding_embed_content_rpcs": misses,
        "maximum_embedding_rpcs_including_count_tokens": 2 * misses,
        "assumptions": f"Worst case: {generation_attempts} generation attempts per turn, bounded by the per-investigation request cap and before the run-wide cap; no dynamic cache reuse and two embedding RPCs per miss. Lexical searches require no query embedding.",
    }


class ObservedInvestigationSearch:
    def __init__(self, search, retrieval_mode):
        self.search, self.retrieval_mode = search, retrieval_mode

    async def execute(self, query, limit, *, owner_id):
        observation = _current.get()
        item = {"query": query, "retrieval_mode": self.retrieval_mode, "limit": limit, "status": "failed", "hits": ()}
        before = len(observation.channels) if observation is not None else 0
        if observation is not None:
            turns = observation.investigation_turns
            item["requested_at_turn"] = len(turns)
            action = turns[-1].get("action") if turns else None
            item["call_id"] = action.call_id if isinstance(action, SearchEvidenceCall) else None
            observation.investigation_searches.append(item)
        started = perf_counter()
        try:
            hits = await self.search.execute(query, limit, owner_id=owner_id)
            item.update(status="ok", hits=hits)
            return hits
        finally:
            item["duration_ms"] = (perf_counter() - started) * 1000
            item["repository_calls"] = len(observation.channels) - before if observation is not None else None


class ObservedInvestigationModel:
    def __init__(self, model):
        self.model = model

    async def next_action(self, turn):
        observation = _current.get()
        item = {"turn": turn, "status": "failed", "action": None}
        if observation is not None:
            observation.investigation_turns.append(item)
        started = perf_counter()
        try:
            action = await self.model.next_action(turn)
            item.update(action=action, status="ok")
            return action
        finally:
            item["duration_ms"] = (perf_counter() - started) * 1000


def distinct(hits):
    return tuple({(hit.content_sha256, hit.start, hit.end): hit for hit in hits}.values())


def coverage(hits, gold, *, observed=True):
    matches = [
        {"gold_index": index, "ranks": [rank for rank, hit in enumerate(hits, 1) if fact_covered(hit, fact)]}
        for index, fact in enumerate(gold)
    ]
    count = sum(bool(item["ranks"]) for item in matches)
    return {
        "observed": observed,
        "gold_count": len(gold),
        "covered_facts": count if observed else None,
        "recall": count / len(gold) if gold and observed else None,
        "complete": bool(gold) and count == len(gold) if observed else None,
        "matches": matches if observed else [],
    }


def investigation_record(observation, result, gold, *, retrieval_mode, include_text=False, budgets=None):
    """Score only after execution; failed runs retain observed partial searches/turns."""
    searches, turns = observation.investigation_searches, observation.investigation_turns

    def records(hits):
        return [
            evidence_record(hit, rank, include_text=include_text) | {"title": hit.title}
            for rank, hit in enumerate(hits, 1)
        ]

    def references(refs):
        return [
            record | {"citation_id": ref.evidence_id}
            for record, ref in zip(records([ref.evidence for ref in refs]), refs, strict=True)
        ]

    exchanges = {exchange.call_id: exchange for item in turns for exchange in item["turn"].exchanges}
    last_turn = turns[-1]["turn"] if turns else None
    final = turns[-1] if turns and isinstance(turns[-1]["action"], InvestigationDecision) else None
    last_refs = distinct_refs(last_turn) if last_turn else ()
    accumulated = tuple(result.evidence) if result is not None else last_refs
    final_refs = distinct_refs(final["turn"]) if final else ()
    all_hits = distinct(tuple(hit for search in searches for hit in search["hits"]))
    first = searches[0] if searches else None
    search_rows = []
    for index, search in enumerate(searches, 1):
        exchange = exchanges.get(search["call_id"])
        next_index = search["requested_at_turn"]
        after = (
            distinct_refs(turns[next_index]["turn"])
            if next_index < len(turns)
            else accumulated
            if result is not None and index == len(searches)
            else None
        )
        search_rows.append(
            {
                **{key: value for key, value in search.items() if key != "hits"},
                "sequence": index,
                "retrieved": records(search["hits"]),
                "retrieved_count": len(search["hits"]),
                "coverage": coverage(search["hits"], gold, observed=search["status"] == "ok"),
                "admitted_to_transcript": references(exchange.evidence) if exchange else None,
                "accumulated_after": references(after) if after is not None else None,
                "accumulated_coverage_after": coverage(
                    [ref.evidence for ref in after] if after is not None else (), gold, observed=after is not None
                ),
            }
        )
    turn_rows = []
    for index, item in enumerate(turns, 1):
        turn, action = item["turn"], item["action"]
        turn_rows.append(
            {
                "sequence": index,
                "status": item["status"],
                "duration_ms": item["duration_ms"],
                "final_only": turn.final_only,
                "context_chars": len(turn.context_json()),
                "model_facing_search_responses": turn.search_responses() if include_text else None,
                "admitted_evidence": references(distinct_refs(turn)),
                "exchanges": [
                    {"query": exchange.query, "call_id": exchange.call_id, "evidence": references(exchange.evidence)}
                    for exchange in turn.exchanges
                ],
                "action": asdict(action) if action else None,
                "action_kind": "search"
                if isinstance(action, SearchEvidenceCall)
                else "decision"
                if isinstance(action, InvestigationDecision)
                else None,
            }
        )
    dispositions = [record.disposition.value for record in result.searches] if result is not None else []
    message = result.message if result is not None else None
    limits = {
        "search": "search_limit" in dispositions,
        "embedding": "embedding_limit" in dispositions,
        "context": "context_limit" in dispositions,
        "evidence": "evidence_limit" in dispositions,
        "generation": bool(message and "generation-request limit" in message),
        "turn": bool(message and "turn limit" in message),
    }
    budgets = budgets or {}
    if result is not None:
        limits["search"] |= len(result.searches) >= budgets.get("max_search_calls", float("inf"))
        limits["embedding"] |= result.embedding_requests >= budgets.get("max_embedding_requests", float("inf"))
        limits["evidence"] |= len(result.evidence) >= budgets.get("max_accumulated_evidence", float("inf"))
        limits["generation"] |= result.generation_requests >= budgets.get("max_generation_requests", float("inf"))
    # final_only can be triggered without a rejected search record. The recorded
    # actual configuration supplies the reason, independently of model choices.
    return {
        "retrieval_mode": retrieval_mode,
        "search_tool_calls": len(result.searches) if result is not None else len(searches),
        "executed_searches": len(searches),
        "searches": search_rows,
        "turns": turn_rows,
        "retrieved_passages": sum(len(search["hits"]) for search in searches),
        "distinct_retrieved_passages": len(all_hits),
        "accumulated_evidence": references(accumulated),
        "accumulated_count": len(accumulated),
        "accumulated_observation": "returned_registry"
        if result is not None
        else "latest_model_turn_only; later state unavailable",
        "final_generation_evidence": references(final_refs) if final else None,
        "final_generation_context_chars": len(final["turn"].context_json()) if final else None,
        "latest_generation_evidence": references(last_refs) if last_turn else None,
        "coverage": {
            "first_search": coverage(
                first["hits"] if first else (), gold, observed=bool(first and first["status"] == "ok")
            ),
            "all_searches": coverage(all_hits, gold, observed=any(search["status"] == "ok" for search in searches)),
            "accumulated": coverage(
                [ref.evidence for ref in accumulated], gold, observed=result is not None or last_turn is not None
            ),
            "final_generation": coverage([ref.evidence for ref in final_refs], gold, observed=final is not None),
        },
        "limits_observed": limits,
        "message": message,
        "stopped_normally": result is not None and result.status.value != "incomplete",
        "stop_kind": "error" if result is None else "incomplete" if result.status.value == "incomplete" else "decision",
        "application_generation_requests": result.generation_requests if result is not None else None,
        "application_embedding_requests": result.embedding_requests if result is not None else None,
        "correct_by_normalized_exact_match": None,  # Added by the runner after deterministic answer scoring.
        "embedding_calls": observation.embeddings,
        "total_search_ms": sum(item["duration_ms"] for item in searches),
        "total_generation_ms": sum(item["duration_ms"] for item in observation.generation),
        "total_embedding_ms": sum(item["duration_ms"] for item in observation.embeddings),
        "timing_semantics": "Search includes embedding; generation includes pacing. Do not add overlapping durations to total Investigation latency.",
    }


def distinct_refs(turn):
    return tuple({ref.evidence_id: ref for exchange in turn.exchanges for ref in exchange.evidence}.values())


def investigation_summary(rows):
    total = len(rows)
    observed = [row["investigation"] for row in rows if row.get("investigation")]
    if not observed:
        return None
    stages = {}
    for stage in ("first_search", "all_searches", "accumulated", "final_generation"):
        values = [item["coverage"][stage] for item in observed]
        stages[stage] = {
            "observed_attempts": sum(value["observed"] for value in values),
            "recall_all_attempts": sum(value["recall"] or 0 for value in values) / total,
            "complete_all_attempts": sum(value["complete"] or False for value in values) / total,
        }
    return {
        "attempts": total,
        "coverage": stages,
        "average_searches": sum(item["search_tool_calls"] for item in observed) / total,
        "average_generation_calls": sum(row["generation_calls"] for row in rows) / total,
        "average_accumulated_passages": sum(item["accumulated_count"] for item in observed) / total,
        "embedding_cache_hits": sum(call["cache_hit"] for item in observed for call in item["embedding_calls"]),
        "embedding_cache_misses": sum(not call["cache_hit"] for item in observed for call in item["embedding_calls"]),
        "limit_reached_counts": {
            key: sum(item["limits_observed"][key] for item in observed) for key in observed[0]["limits_observed"]
        },
        "search_duration_ms": statistics([search["duration_ms"] for item in observed for search in item["searches"]]),
        "total_retrieval_ms": statistics([item["total_search_ms"] for item in observed]),
        "total_generation_ms": statistics([item["total_generation_ms"] for item in observed]),
        "total_embedding_ms": statistics([item["total_embedding_ms"] for item in observed]),
    }
