"""Incremental wire presentation with a complete, unchanged evidence registry."""

import json
from dataclasses import replace
from uuid import UUID

from tests.unit.application.test_investigations import QuerySearch, ScriptedModel, make_hit

from investigation_engine.application.investigations import (
    ConflictClaim,
    IdentifiedEvidence,
    InvestigateQuestion,
    InvestigationDecision,
    InvestigationStatus,
    InvestigationTurn,
    SearchDisposition,
    SearchEvidenceCall,
    SearchExchange,
)


def refs(hits, start=1):
    return tuple(IdentifiedEvidence(evidence_id=f"E{index}", evidence=hit) for index, hit in enumerate(hits, start))


def exchange(query, evidence, call_id="call"):
    return SearchExchange(query=query, call_id=call_id, evidence=evidence)


def five(registration):
    return tuple(
        make_hit(registration, source_id=str(i), body=f"Distinct source fact {i}. " + "detail " * 80) for i in range(5)
    )


def decision(*ids):
    return InvestigationDecision(status=InvestigationStatus.ANSWERED, answer="Supported [E1]", citations=ids or ("E1",))


def test_first_search_five_new_passages_have_full_text_and_sources(registration):
    hits = five(registration)
    turn = InvestigationTurn(question="Question?", exchanges=(exchange("first", refs(hits)),))
    response = turn.search_responses()[0]
    assert response["new_evidence_ids"] == ["E1", "E2", "E3", "E4", "E5"]
    assert response["previously_available_evidence_ids"] == [] and not response["no_new_evidence"]
    assert [item["excerpt"] for item in response["results"]] == [hit.text for hit in hits]
    assert all("source" in item and "evidence_id" in item for item in response["results"])
    assert json.loads(turn.context_json())["searches"] == turn.search_responses()


def test_same_five_passages_become_references_without_mutating_earlier_response(registration):
    evidence = refs(five(registration))
    first = exchange("first", evidence, "1")
    initial = InvestigationTurn(question="Question?", exchanges=(first,)).search_responses()[0]
    later = InvestigationTurn(question="Question?", exchanges=(first, exchange("rephrased", evidence, "2")))
    a, b = later.search_responses()
    assert a == initial
    assert b["no_new_evidence"] and b["new_evidence_ids"] == []
    assert b["previously_available_evidence_ids"] == [ref.evidence_id for ref in evidence]
    assert all(
        "excerpt" not in item and item["previously_available"] and item["first_seen_search"] == 1
        for item in b["results"]
    )
    assert [item["source"] for item in b["results"]] == [item["source"] for item in a["results"]]
    assert all(ref.evidence.text for ref in later.exchanges[1].evidence)


def test_mixed_search_preserves_order_and_first_seen_search_for_new_passages(registration):
    first, added = (
        make_hit(registration, source_id="one", body="First."),
        make_hit(registration, source_id="two", body="Second."),
    )
    a, b = refs((first, added))
    turns = (exchange("initial", (a,), "1"), exchange("follow", (a, b), "2"), exchange("again", (b, a), "3"))
    payloads = InvestigationTurn(question="Q?", exchanges=turns).search_responses()
    assert payloads[1]["new_evidence_ids"] == ["E2"] and payloads[1]["previously_available_evidence_ids"] == ["E1"]
    assert [item["evidence_id"] for item in payloads[1]["results"]] == ["E1", "E2"]
    assert "excerpt" not in payloads[1]["results"][0] and payloads[1]["results"][1]["excerpt"] == added.text
    assert [item["first_seen_search"] for item in payloads[2]["results"]] == [2, 1]


def test_canonical_copy_references_first_source_not_new_document_occurrence(registration):
    first = make_hit(registration, source_id="first-occurrence", body="Identical canonical fact.")
    copy = replace(
        first,
        document_id=UUID(int=99),
        document_version_id=UUID(int=100),
        source=replace(first.source, source_id="other-occurrence"),
    )
    turn = InvestigationTurn(
        question="Q", exchanges=(exchange("one", refs((first,)), "1"), exchange("two", refs((copy,), 9), "2"))
    )
    a, b = turn.search_responses()
    assert b["results"][0]["evidence_id"] == "E1" and "excerpt" not in b["results"][0]
    assert b["results"][0]["source"] == a["results"][0]["source"]
    assert first.source.source_id == "first-occurrence" and copy.source.source_id == "other-occurrence"


def test_same_document_and_title_different_ranges_are_not_deduplicated(registration):
    whole = replace(make_hit(registration, source_id="same", body="First fact. Second fact."), title="Same title")
    first = replace(whole, text="First fact.", start=0, end=11)
    second = replace(whole, text="Second fact.", start=12, end=24)
    a, b = refs((first, second))
    payloads = InvestigationTurn(
        question="Q", exchanges=(exchange("one", (a,), "1"), exchange("two", (a, b), "2"))
    ).search_responses()
    assert first.document_version_id == second.document_version_id and first.content_sha256 == second.content_sha256
    assert payloads[1]["new_evidence_ids"] == ["E2"]
    assert payloads[1]["results"][1]["excerpt"] == "Second fact."


def test_empty_search_distinct_from_repeated_results_and_does_not_reset_seen(registration):
    evidence = refs((make_hit(registration, source_id="known", body="Known."),))
    payloads = InvestigationTurn(
        question="Q",
        exchanges=(
            exchange("first", evidence, "1"),
            exchange("empty", (), "2"),
            exchange("known again", evidence, "3"),
        ),
    ).search_responses()
    assert payloads[1]["results"] == [] and payloads[1]["no_new_evidence"]
    assert payloads[1]["new_evidence_ids"] == payloads[1]["previously_available_evidence_ids"] == []
    assert payloads[2]["results"][0]["first_seen_search"] == 1
    # A different conversation must introduce the text again.
    assert (
        "excerpt"
        in InvestigationTurn(question="Other Q", exchanges=(exchange("first", evidence),)).search_responses()[0][
            "results"
        ][0]
    )


async def test_context_admission_uses_compact_representation_and_cites_earlier_source(registration):
    hits = five(registration)
    evidence = refs(hits)
    first, second = exchange("first", evidence, "1"), exchange("repeat", evidence, "2")
    budget = len(InvestigationTurn(question="Q?", exchanges=(first, second)).context_json())
    model = ScriptedModel(
        [SearchEvidenceCall(query="first", call_id="1"), SearchEvidenceCall(query="repeat", call_id="2"), decision()]
    )
    result = await InvestigateQuestion(
        QuerySearch({"first": hits, "repeat": hits}), model, max_context_chars=budget
    ).execute("Q?", owner_id=None)
    assert result.status == InvestigationStatus.ANSWERED and len(result.evidence) == 5
    assert result.searches[-1].disposition == SearchDisposition.SUCCEEDED
    assert not model.turns[-1].final_only and len(model.turns[-1].context_json()) <= budget
    assert result.citations == ("E1",) and result.evidence[0].evidence == hits[0]
    assert all(item.evidence.source == hit.source for item, hit in zip(result.evidence, hits, strict=True))


async def test_repeated_result_does_not_stop_multihop_before_new_evidence(registration):
    first = make_hit(registration, source_id="parent", body="Alexandra's mother is Caroline.")
    second = make_hit(registration, source_id="grandparent", body="Caroline's father is Rainier III.")
    model = ScriptedModel(
        [
            SearchEvidenceCall(query="parent", call_id="1"),
            SearchEvidenceCall(query="parent again", call_id="2"),
            SearchEvidenceCall(query="grandparent", call_id="3"),
            decision("E1", "E2"),
        ]
    )
    result = await InvestigateQuestion(
        QuerySearch({"parent": (first,), "parent again": (first,), "grandparent": (second,)}), model
    ).execute("Who is the maternal grandfather?", owner_id=None)
    assert result.status == InvestigationStatus.ANSWERED and len(result.searches) == 3
    assert model.turns[2].search_responses()[-1]["no_new_evidence"]
    assert model.turns[3].search_responses()[-1]["new_evidence_ids"] == ["E2"]
    assert result.generation_requests == 4 and result.evidence[1].evidence == second


async def test_contradictory_distinct_passages_remain_full_and_citable(registration):
    first = make_hit(registration, source_id="report-a", body="The museum opened in 1900.")
    second = replace(make_hit(registration, source_id="report-b", body="The museum opened in 1910."), title=first.title)
    final = InvestigationDecision(
        status=InvestigationStatus.CONFLICTING_EVIDENCE,
        answer=None,
        citations=(),
        conflicts=(ConflictClaim(claim="1900", citations=("E1",)), ConflictClaim(claim="1910", citations=("E2",))),
    )
    model = ScriptedModel(
        [SearchEvidenceCall(query="first", call_id="1"), SearchEvidenceCall(query="other report", call_id="2"), final]
    )
    result = await InvestigateQuestion(
        QuerySearch({"first": (first,), "other report": (first, second)}), model
    ).execute("When did the museum open?", owner_id=None)
    assert result.status == InvestigationStatus.CONFLICTING_EVIDENCE and len(result.evidence) == 2
    assert model.turns[-1].search_responses()[1]["results"][1]["excerpt"] == second.text
    assert result.conflicts[0].citations == ("E1",) and result.conflicts[1].citations == ("E2",)
