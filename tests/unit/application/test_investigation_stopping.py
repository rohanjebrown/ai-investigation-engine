"""Characterize existing stop mechanics with scripted decisions, never an optimizer.

These fakes establish that safe decision paths are representable. They do not
measure Gemini's reasoning, prompt adherence, or semantic citation validation.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from google.genai import types
from tests.unit.application.test_investigations import QuerySearch, ScriptedModel, make_hit

from investigation_engine.application.investigations import (
    ConflictClaim,
    InvestigateQuestion,
    InvestigationDecision,
    InvestigationStatus,
    SearchDisposition,
    SearchEvidenceCall,
)
from investigation_engine.infrastructure.generation import gemini_investigation_model as adapter


def search(query, number):
    return SearchEvidenceCall(query=query, call_id=str(number))


def answered(text, *citations):
    return InvestigationDecision(status=InvestigationStatus.ANSWERED, answer=text, citations=citations)


def texts(turn):
    return [ref.evidence.text for exchange in turn.exchanges for ref in exchange.evidence]


def jolliffe_hits(registration):
    # Short explicit source fixtures representing the saved passage relationships;
    # no benchmark answers or gold annotations are passed into the application.
    return (
        make_hit(
            registration,
            source_id="4th-baron",
            body="William George Hervey Jolliffe, 4th Baron Hylton, was the son of Hylton Jolliffe, 3rd Baron Hylton, and Lady Alice Adeliza Hervey.",
        ),
        make_hit(
            registration,
            source_id="3rd-baron",
            body="Hylton Jolliffe, 3rd Baron Hylton, was the eldest son of Hedworth Jolliffe, 2nd Baron Hylton, and Lady Agnes Mary Byng.",
        ),
    )


async def test_jolliffe_one_search_can_finalize_before_any_limit(registration):
    first, second = jolliffe_hits(registration)

    def finish(turn):
        assert not turn.final_only
        assert "4th Baron" in texts(turn)[0] and "3rd Baron" in texts(turn)[0]
        assert "3rd Baron" in texts(turn)[1] and "Hedworth" in texts(turn)[1]
        return answered("Hedworth Jolliffe, 2nd Baron Hylton. [E1] [E2]", "E1", "E2")

    model = ScriptedModel([search("Jolliffe paternal grandfather", 1), finish])
    repository = QuerySearch({"Jolliffe paternal grandfather": (first, second)})
    result = await InvestigateQuestion(repository, model).execute(
        "Who is William Jolliffe, 4th Baron Hylton's paternal grandfather?", owner_id=None
    )
    assert result.status == InvestigationStatus.ANSWERED
    assert result.generation_requests == 2 and len(repository.calls) == 1
    assert result.citations == ("E1", "E2")
    assert [item.evidence for item in result.evidence] == [first, second]


async def test_distinct_queries_can_repeat_same_passages_until_search_limit(registration):
    hits = jolliffe_hits(registration)
    queries = [
        "Jolliffe paternal grandfather",
        "Hedworth parents",
        "Hedworth father mother",
        "Hedworth son of William",
        "Hedworth father",
    ]
    model = ScriptedModel(
        [
            *(search(query, index) for index, query in enumerate(queries, 1)),
            answered("Hedworth Jolliffe [E1] [E2]", "E1", "E2"),
        ]
    )
    repository = QuerySearch(dict.fromkeys(queries, hits))
    result = await InvestigateQuestion(repository, model).execute("Who is the paternal grandfather?", owner_id=None)
    assert len(repository.calls) == 5 and result.generation_requests == 6
    assert all(record.succeeded and record.disposition == SearchDisposition.SUCCEEDED for record in result.searches)
    assert len(result.evidence) == 2
    assert [len(turn.exchanges) for turn in model.turns] == list(range(6))
    assert all(not turn.final_only for turn in model.turns[:5]) and model.turns[-1].final_only
    assert [len(texts(turn)) for turn in model.turns] == [0, 2, 4, 6, 8, 10]
    assert result.evidence[0].evidence.source == hits[0].source


async def test_wisigard_scripted_decision_uses_spouse_parent_not_own_father(registration):
    family = make_hit(
        registration,
        source_id="marriage",
        body="Wisigard married Theudebert I. Theuderich I was Theudebert I's father.",
    )
    distractor = make_hit(registration, source_id="own-father", body="Wacho was Wisigard's father.")

    def finish(turn):
        assert "married Theudebert I" in texts(turn)[0]
        assert "Theuderich I was Theudebert I's father" in texts(turn)[0]
        return answered("Theuderich I [E1]", "E1")

    model = ScriptedModel([search("Wisigard spouse and parents", 1), finish])
    result = await InvestigateQuestion(
        QuerySearch({"Wisigard spouse and parents": (family, distractor)}), model
    ).execute("Who is Wisigard's father-in-law?", owner_id=None)
    assert result.answer == "Theuderich I [E1]" and result.citations == ("E1",)
    assert len(result.searches) == 1 and not model.turns[-1].final_only


async def test_citation_validator_does_not_detect_semantically_wrong_relationship(registration):
    own_father = make_hit(registration, source_id="own-father", body="Wacho was Wisigard's father.")
    result = await InvestigateQuestion(
        QuerySearch({"Wisigard": (own_father,)}), ScriptedModel([search("Wisigard", 1), answered("Wacho [E1]", "E1")])
    ).execute("Who is Wisigard's father-in-law?", owner_id=None)
    # Characterization of an existing limitation, NOT an accuracy success: the
    # server validates registered citations, not father vs father-in-law semantics.
    assert result.status == InvestigationStatus.ANSWERED and result.answer == "Wacho [E1]"


async def test_princess_alexandra_requires_model_directed_dependent_follow_up(registration):
    mother = make_hit(
        registration,
        source_id="alexandra-1999",
        body="Princess Alexandra of Hanover, born in 1999, is the daughter of Caroline, Princess of Hanover, and Ernst August of Hanover.",
    )
    grandfather = make_hit(
        registration,
        source_id="caroline-monaco",
        body="Caroline, Princess of Hanover, is the daughter of Rainier III, Prince of Monaco, and Grace Kelly.",
    )

    def follow(turn):
        assert "Caroline, Princess of Hanover" in texts(turn)[0]
        assert not any("Rainier" in text for text in texts(turn))
        return search("Caroline Princess of Hanover Monaco father", 2)

    def finish(turn):
        assert "Rainier III" in texts(turn)[1]
        return answered("Rainier III [E1] [E2]", "E1", "E2")

    repository = QuerySearch(
        {"Alexandra born 1999 mother": (mother,), "Caroline Princess of Hanover Monaco father": (grandfather,)}
    )
    model = ScriptedModel([search("Alexandra born 1999 mother", 1), follow, finish])
    result = await InvestigateQuestion(repository, model).execute(
        "Who is Princess Alexandra of Hanover (born 1999)'s maternal grandfather?", owner_id=None
    )
    assert len(repository.calls) == 2 and result.generation_requests == 3
    assert result.answer == "Rainier III [E1] [E2]"
    assert result.evidence[0].evidence.source == mother.source
    assert result.evidence[1].evidence.source == grandfather.source


async def test_empty_search_can_be_followed_by_a_useful_search(registration):
    detail = make_hit(registration, source_id="detail", body="The museum opened in 1900.")
    model = ScriptedModel(
        [search("generic museum", 1), search("specific museum opening register", 2), answered("1900 [E1]", "E1")]
    )
    result = await InvestigateQuestion(QuerySearch({"specific museum opening register": (detail,)}), model).execute(
        "When did the museum open?", owner_id=None
    )
    assert result.status == InvestigationStatus.ANSWERED
    assert result.searches[0].disposition == SearchDisposition.EMPTY
    assert not model.turns[1].final_only
    assert result.generation_requests == 3


async def test_no_new_passages_does_not_prove_sufficiency_or_forbid_useful_alternative(registration):
    parent = make_hit(registration, source_id="parent", body="Alexandra's mother is Caroline.")
    detail = make_hit(registration, source_id="grandparent", body="Caroline's father is Rainier III.")
    model = ScriptedModel(
        [
            search("Alexandra mother", 1),
            search("Alexandra parents detail", 2),
            search("Caroline Monaco father", 3),
            answered("Rainier III [E1] [E2]", "E1", "E2"),
        ]
    )
    result = await InvestigateQuestion(
        QuerySearch(
            {"Alexandra mother": (parent,), "Alexandra parents detail": (parent,), "Caroline Monaco father": (detail,)}
        ),
        model,
    ).execute("Who is Alexandra's maternal grandfather?", owner_id=None)
    assert result.status == InvestigationStatus.ANSWERED and len(result.searches) == 3
    assert result.searches[0].evidence_ids == result.searches[1].evidence_ids == ("E1",)
    assert result.searches[2].evidence_ids == ("E2",)
    assert not model.turns[2].final_only


@pytest.mark.parametrize("resolved", [False, True])
async def test_conflicts_require_resolution_or_explicit_conflict_outcome(registration, resolved):
    old = make_hit(registration, source_id="record-a", body="The museum opened in 1900.")
    other = make_hit(registration, source_id="record-b", body="The same museum opened in 1910.")
    correction = make_hit(
        registration,
        source_id="correction",
        body="The 1910 date is a printing error; the museum opening register establishes 1900.",
    )

    def resolve(turn):
        assert len(texts(turn)) == 2 and "1900" in texts(turn)[0] and "1910" in texts(turn)[1]
        return search("museum original opening register correction", 2)

    def finish(turn):
        if any("printing error" in text for text in texts(turn)):
            return answered("1900 [E1] [E3]", "E1", "E3")
        return InvestigationDecision(
            status=InvestigationStatus.CONFLICTING_EVIDENCE,
            answer=None,
            citations=(),
            conflicts=(ConflictClaim(claim="1900", citations=("E1",)), ConflictClaim(claim="1910", citations=("E2",))),
        )

    result = await InvestigateQuestion(
        QuerySearch(
            {
                "museum opening": (old, other),
                "museum original opening register correction": (correction,) if resolved else (),
            }
        ),
        ScriptedModel([search("museum opening", 1), resolve, finish]),
    ).execute("When did the museum open?", owner_id=None)
    assert len(result.searches) == 2
    assert result.status == (InvestigationStatus.ANSWERED if resolved else InvestigationStatus.CONFLICTING_EVIDENCE)
    if not resolved:
        assert result.answer is None and len(result.conflicts) == 2


async def test_identical_query_terminates_incomplete_without_second_search_or_forced_answer(registration):
    parent = make_hit(registration, source_id="parent", body="Alexandra's mother is Caroline.")
    repository = QuerySearch({"Alexandra mother": (parent,)})
    result = await InvestigateQuestion(
        repository, ScriptedModel([search("Alexandra mother", 1), search("Alexandra mother", 2)])
    ).execute("Who is Alexandra's maternal grandfather?", owner_id=None)
    assert result.status == InvestigationStatus.INCOMPLETE and result.answer is None
    assert len(repository.calls) == 1 and result.embedding_requests == 1
    assert result.searches[-1].disposition == SearchDisposition.REPEATED_QUERY


async def test_whitespace_variant_is_not_an_identical_query():
    repository = QuerySearch({})
    model = ScriptedModel(
        [
            search("same", 1),
            search("same ", 2),
            InvestigationDecision(status=InvestigationStatus.INSUFFICIENT_EVIDENCE, answer=None, citations=()),
        ]
    )
    result = await InvestigateQuestion(repository, model).execute("Question?", owner_id=None)
    assert len(repository.calls) == 2 and result.status == InvestigationStatus.INSUFFICIENT_EVIDENCE


async def test_real_adapter_allows_early_json_decision_while_search_is_enabled(registration, monkeypatch):
    hit = make_hit(registration, source_id="known", body="Known supported value.")

    def response(part):
        return types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    finish_reason=types.FinishReason.STOP, content=types.Content(role="model", parts=[part])
                )
            ]
        )

    models = SimpleNamespace(
        generate_content=AsyncMock(
            side_effect=[
                response(
                    types.Part(
                        function_call=types.FunctionCall(
                            name="search_evidence", args={"query": "known value"}, id="native-1"
                        ),
                        thought_signature=b"offline-test-signature",
                    )
                ),
                response(
                    types.Part(
                        text=json.dumps(
                            {
                                "status": "answered",
                                "answer": "Known supported value [E1]",
                                "citations": ["E1"],
                                "conflicts": [],
                            }
                        )
                    )
                ),
            ]
        )
    )
    client = SimpleNamespace(aio=SimpleNamespace(models=models, aclose=AsyncMock()), close=Mock())
    monkeypatch.setattr(adapter.genai, "Client", Mock(return_value=client))
    model = adapter.GeminiInvestigationModel(api_key="offline-only", model="gemini-3.5-flash-lite")
    repository = QuerySearch({"known value": (hit,)})
    result = await InvestigateQuestion(repository, model).execute("What is known?", owner_id=None)
    assert result.status == InvestigationStatus.ANSWERED and result.generation_requests == 2
    assert len(repository.calls) == 1 and models.generate_content.await_count == 2
    configs = [call.kwargs["config"] for call in models.generate_content.await_args_list]
    assert all(
        config.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.VALIDATED
        for config in configs
    )
    assert all("Search is disabled" not in config.system_instruction for config in configs)
    contents = models.generate_content.await_args_list[1].kwargs["contents"]
    assert contents[2].parts[0].function_response.response["results"][0]["excerpt"] == hit.text
    await model.aclose()
