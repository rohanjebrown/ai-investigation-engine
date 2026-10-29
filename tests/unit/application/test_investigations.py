import pytest

from investigation_engine.application.investigations import (
    ConflictClaim,
    InvestigateQuestion,
    InvestigationDecision,
    InvestigationStatus,
    SearchDisposition,
    SearchEvidenceCall,
)
from investigation_engine.application.ports.generation_errors import GenerationResponseError
from investigation_engine.application.retrieval import Evidence


class ScriptedModel:
    def __init__(self, actions):
        self.actions = list(actions)
        self.turns = []

    async def next_action(self, turn):
        self.turns.append(turn)
        action = self.actions.pop(0)
        return action(turn) if callable(action) else action


class QuerySearch:
    def __init__(self, hits_by_query):
        self.hits_by_query = hits_by_query
        self.calls = []

    async def execute(self, query, limit, *, owner_id):
        self.calls.append((query, limit, owner_id))
        return tuple(self.hits_by_query.get(query, ()))[:limit]


def make_hit(registration, *, source_id, body):
    document, version = registration(body=body, source_id=source_id)
    return Evidence(
        document_id=document.id,
        document_version_id=version.id,
        content_sha256=version.content.sha256,
        start=0,
        end=len(body),
        text=body,
        source=version.source,
        source_spans=(),
        score=0.9,
        rank=1,
    )


async def test_two_hop_searches_are_model_directed_and_citations_resolve(registration):
    first = make_hit(registration, source_id="director", body="Move was directed by Stuart Rosenberg.")
    second = make_hit(registration, source_id="nationality", body="Stuart Rosenberg was American.")
    model = ScriptedModel(
        [
            SearchEvidenceCall(query="Move 1970 film director", call_id="call-1"),
            lambda turn: SearchEvidenceCall(
                query=(
                    "Stuart Rosenberg"
                    if "Stuart Rosenberg" in turn.exchanges[0].evidence[0].evidence.text
                    else "unknown"
                )
                + " nationality",
                call_id="call-2",
            ),
            InvestigationDecision(
                status=InvestigationStatus.ANSWERED,
                answer="Stuart Rosenberg was American. [E1] [E2]",
                citations=("E1", "E2"),
            ),
        ]
    )
    search = QuerySearch({"Move 1970 film director": (first,), "Stuart Rosenberg nationality": (second,)})
    result = await InvestigateQuestion(
        search,
        model,
        max_turns=3,
        max_search_calls=2,
        max_results_per_search=5,
        max_accumulated_evidence=10,
        max_context_chars=24000,
        max_generation_requests=3,
        max_embedding_requests=2,
    ).execute("What was the director's nationality?", owner_id=None)
    assert [call[0] for call in search.calls] == ["Move 1970 film director", "Stuart Rosenberg nationality"]
    assert len(model.turns[1].exchanges) == 1 and len(model.turns[2].exchanges) == 2
    assert result.status == InvestigationStatus.ANSWERED
    assert result.citations == ("E1", "E2")
    assert result.searches[0].evidence_ids == ("E1",)
    assert result.searches[1].evidence_ids == ("E2",)
    assert result.generation_requests == 3
    assert result.embedding_requests == 2


async def test_three_hop_search_depends_on_each_previous_result(registration):
    hits = [
        make_hit(registration, source_id="a", body="A points to B."),
        make_hit(registration, source_id="b", body="B points to C."),
        make_hit(registration, source_id="c", body="C is the answer."),
    ]
    queries = ["initial", "B detail", "C detail"]
    model = ScriptedModel(
        [
            SearchEvidenceCall(query=queries[0], call_id="1"),
            lambda turn: SearchEvidenceCall(
                query=queries[1] if "B" in turn.exchanges[-1].evidence[0].evidence.text else "wrong", call_id="2"
            ),
            lambda turn: SearchEvidenceCall(
                query=queries[2] if "C" in turn.exchanges[-1].evidence[0].evidence.text else "wrong", call_id="3"
            ),
            InvestigationDecision(
                status=InvestigationStatus.ANSWERED, answer="C is the answer [E3]", citations=("E3",)
            ),
        ]
    )
    search = QuerySearch(dict(zip(queries, [(hits[0],), (hits[1],), (hits[2],)])))
    result = await InvestigateQuestion(search, model).execute("Trace the chain", owner_id="trusted-admin")
    assert [query for query, _, owner in search.calls] == queries
    assert all(owner == "trusted-admin" for _, _, owner in search.calls)
    assert [entry.evidence_id for entry in result.evidence] == ["E1", "E2", "E3"]


async def test_independent_search_branches_are_combined_in_final_answer(registration):
    first = make_hit(registration, source_id="film-a", body="Film A was directed by Alpha Person.")
    second = make_hit(registration, source_id="film-b", body="Film B was directed by Beta Person.")
    third = make_hit(registration, source_id="alpha", body="Alpha Person was born in 1900.")
    fourth = make_hit(registration, source_id="beta", body="Beta Person was born in 1980.")
    queries = ("Film A director", "Film B director", "Alpha Person birth", "Beta Person birth")
    model = ScriptedModel(
        [
            SearchEvidenceCall(query=queries[0], call_id="1"),
            SearchEvidenceCall(query=queries[1], call_id="2"),
            SearchEvidenceCall(query=queries[2], call_id="3"),
            SearchEvidenceCall(query=queries[3], call_id="4"),
            InvestigationDecision(
                status=InvestigationStatus.ANSWERED,
                answer="Film B's director was born later. [E3] [E4]",
                citations=("E3", "E4"),
            ),
        ]
    )
    search = QuerySearch(dict(zip(queries, [(first,), (second,), (third,), (fourth,)])))
    result = await InvestigateQuestion(search, model).execute("Compare the two directors", owner_id=None)
    assert [call[0] for call in search.calls] == list(queries)
    assert result.citations == ("E3", "E4")
    assert all(len(turn.exchanges) == index for index, turn in enumerate(model.turns))


async def test_same_content_reuses_evidence_slot_and_preserves_first_occurrence_citation(registration):
    first = make_hit(registration, source_id="one", body="Same shared passage")
    second = make_hit(registration, source_id="two", body="Same shared passage")
    model = ScriptedModel(
        [
            SearchEvidenceCall(query="first", call_id="1"),
            SearchEvidenceCall(query="second", call_id="2"),
            InvestigationDecision(status=InvestigationStatus.ANSWERED, answer="Supported [E1]", citations=("E1",)),
        ]
    )
    search = QuerySearch({"first": (first, first), "second": (first, second)})
    result = await InvestigateQuestion(search, model).execute("Compare sources", owner_id=None)
    assert [item.evidence_id for item in result.evidence] == ["E1"]
    assert result.evidence[0].evidence.source.source_id == first.source.source_id
    assert result.searches[0].evidence_ids == ("E1",)
    assert result.searches[1].evidence_ids == ("E1",)


async def test_empty_retrieval_abstention_and_conflicts_are_distinct():
    abstain = InvestigationDecision(status=InvestigationStatus.INSUFFICIENT_EVIDENCE, answer=None, citations=())
    result = await InvestigateQuestion(
        QuerySearch({}),
        ScriptedModel(
            [
                SearchEvidenceCall(query="nothing", call_id="1"),
                abstain,
            ]
        ),
    ).execute("Question", owner_id=None)
    assert result.status == InvestigationStatus.INSUFFICIENT_EVIDENCE
    assert result.searches[0].disposition == SearchDisposition.EMPTY

    from investigation_engine.application.retrieval import Evidence
    from investigation_engine.domain.content import Content
    from investigation_engine.domain.document import Document
    from investigation_engine.domain.document_version import DocumentVersion
    from investigation_engine.domain.provenance import SourceKind, SourceReference

    def hit(source_id, text):
        document = Document(owner_id=None)
        version = DocumentVersion(
            document_id=document.id,
            version_number=1,
            content=Content(text),
            source=SourceReference(kind=SourceKind.DATASET, source_id=source_id, record_locator={}),
        )
        return Evidence(
            document_id=document.id,
            document_version_id=version.id,
            content_sha256=version.content.sha256,
            start=0,
            end=len(text),
            text=text,
            source=version.source,
            source_spans=(),
            rank=1,
            score=1.0,
        )

    conflicting = InvestigationDecision(
        status=InvestigationStatus.CONFLICTING_EVIDENCE,
        answer=None,
        citations=(),
        conflicts=(ConflictClaim(claim="yes", citations=("E1",)), ConflictClaim(claim="no", citations=("E2",))),
    )
    result = await InvestigateQuestion(
        QuerySearch({"yes": (hit("yes", "The result is yes."),), "no": (hit("no", "The result is no."),)}),
        ScriptedModel(
            [
                SearchEvidenceCall(query="yes", call_id="1"),
                SearchEvidenceCall(query="no", call_id="2"),
                conflicting,
            ]
        ),
    ).execute("Question", owner_id=None)
    assert result.status == InvestigationStatus.CONFLICTING_EVIDENCE
    assert [claim.citations for claim in result.conflicts] == [("E1",), ("E2",)]


@pytest.mark.parametrize(
    "decision",
    [
        InvestigationDecision(status=InvestigationStatus.ANSWERED, answer="fabricated [E8]", citations=("E8",)),
        InvestigationDecision(status=InvestigationStatus.ANSWERED, answer="Known [E999]", citations=("E1",)),
    ],
)
async def test_unknown_and_unlisted_citations_are_rejected(registration, decision):
    hit = make_hit(registration, source_id="one", body="Known fact")
    with pytest.raises(GenerationResponseError):
        await InvestigateQuestion(
            QuerySearch({"q": (hit,)}),
            ScriptedModel(
                [
                    SearchEvidenceCall(query="q", call_id="1"),
                    decision,
                ]
            ),
        ).execute("Question", owner_id=None)


async def test_resource_exhaustion_is_incomplete_and_does_not_rewrite_prior_search(registration):
    hit = make_hit(registration, source_id="one", body="Known fact")
    result = await InvestigateQuestion(
        QuerySearch({"first": (hit,)}),
        ScriptedModel(
            [
                SearchEvidenceCall(query="first", call_id="1"),
                SearchEvidenceCall(query="second", call_id="2"),
            ]
        ),
        max_search_calls=1,
    ).execute("Question", owner_id=None)
    assert result.status == InvestigationStatus.INCOMPLETE
    assert result.searches[0].disposition == SearchDisposition.SUCCEEDED
    assert len(result.searches) == 1
    assert result.embedding_requests == 1


async def test_repeated_query_stops_without_embedding_again():
    search = QuerySearch({})
    result = await InvestigateQuestion(
        search,
        ScriptedModel(
            [
                SearchEvidenceCall(query="same", call_id="1"),
                SearchEvidenceCall(query="same", call_id="2"),
            ]
        ),
    ).execute("Question", owner_id=None)
    assert len(search.calls) == 1
    assert result.status == InvestigationStatus.INCOMPLETE
    assert result.searches[-1].disposition == SearchDisposition.REPEATED_QUERY


@pytest.mark.parametrize("count", [2, 3])
@pytest.mark.parametrize("abstain", [False, True])
async def test_full_or_overflow_evidence_has_one_final_only_opportunity(registration, count, abstain):
    hits = tuple(make_hit(registration, source_id=str(index), body=f"Fact {index}") for index in range(count))
    final = InvestigationDecision(
        status=InvestigationStatus.INSUFFICIENT_EVIDENCE if abstain else InvestigationStatus.ANSWERED,
        answer=None if abstain else "Both established [E1] [E2]",
        citations=() if abstain else ("E1", "E2"),
    )
    model = ScriptedModel([SearchEvidenceCall(query="facts", call_id="1"), final])
    search = QuerySearch({"facts": hits})
    result = await InvestigateQuestion(search, model, max_accumulated_evidence=2).execute("Question", owner_id=None)
    assert result.status == final.status
    assert len(result.evidence) == 2 and len(search.calls) == 1
    assert model.turns[-1].final_only
    assert [ref.evidence_id for ref in model.turns[-1].exchanges[-1].evidence] == ["E1", "E2"]
    expected = SearchDisposition.EVIDENCE_LIMIT if count == 3 else SearchDisposition.SUCCEEDED
    assert result.searches[0].disposition == expected


@pytest.mark.parametrize("limits", [{"max_turns": 1}, {"max_generation_requests": 1}])
async def test_no_final_budget_is_incomplete_not_abstention(registration, limits):
    hit = make_hit(registration, source_id="one", body="Known fact")
    model = ScriptedModel([SearchEvidenceCall(query="q", call_id="1")])
    result = await InvestigateQuestion(QuerySearch({"q": (hit,)}), model, max_accumulated_evidence=1, **limits).execute(
        "Question", owner_id=None
    )
    assert result.status == InvestigationStatus.INCOMPLETE
    assert len(result.evidence) == 1 and len(model.turns) == 1


async def test_final_only_tool_request_does_not_execute_search(registration):
    hit = make_hit(registration, source_id="one", body="Known fact")
    search = QuerySearch({"q": (hit,)})
    model = ScriptedModel([SearchEvidenceCall(query="q", call_id="1"), SearchEvidenceCall(query="more", call_id="2")])
    result = await InvestigateQuestion(search, model, max_accumulated_evidence=1).execute("Question", owner_id=None)
    assert result.status == InvestigationStatus.INCOMPLETE and len(search.calls) == 1
    assert result.generation_requests == 2


async def test_final_only_cannot_cite_dropped_evidence(registration):
    hits = tuple(make_hit(registration, source_id=str(index), body=f"Fact {index}") for index in range(2))
    model = ScriptedModel(
        [
            SearchEvidenceCall(query="q", call_id="1"),
            InvestigationDecision(status=InvestigationStatus.ANSWERED, answer="Dropped [E2]", citations=("E2",)),
        ]
    )
    with pytest.raises(GenerationResponseError):
        await InvestigateQuestion(QuerySearch({"q": hits}), model, max_accumulated_evidence=1).execute(
            "Question", owner_id=None
        )


@pytest.mark.parametrize("second_count", [5, 6])
async def test_fifteen_evidence_trace_finalizes_without_losing_last_tool_response(registration, second_count):
    hits = tuple(make_hit(registration, source_id=str(index), body=f"Unique fact {index}") for index in range(16))
    search = QuerySearch({"first": hits[:10], "second": hits[10 : 10 + second_count]})
    model = ScriptedModel(
        [
            SearchEvidenceCall(query="first", call_id="1"),
            SearchEvidenceCall(query="second", call_id="2"),
            InvestigationDecision(status=InvestigationStatus.ANSWERED, answer="Established [E15]", citations=("E15",)),
        ]
    )
    result = await InvestigateQuestion(search, model, max_results_per_search=10).execute("Question", owner_id=None)
    assert result.status == InvestigationStatus.ANSWERED and len(result.evidence) == 15
    assert result.embedding_requests == 2 and result.generation_requests == 3
    assert model.turns[-1].final_only and len(model.turns[-1].exchanges[-1].evidence) == 5
    assert result.evidence[-1].evidence.text == hits[14].text


async def test_independent_compound_fact_search_targets_unresolved_clause(registration):
    first = make_hit(
        registration, source_id="embroidery", body="Maheen Khan worked on embroidery for Snow White and the Huntsman."
    )
    second = make_hit(
        registration, source_id="actress", body="Alice Washburn appeared in Snow White as her only known feature film."
    )

    def unresolved(turn):
        assert "Maheen Khan" in turn.exchanges[0].evidence[0].evidence.text
        return SearchEvidenceCall(query="Snow White actress only known feature film", call_id="2")

    model = ScriptedModel(
        [
            SearchEvidenceCall(query="Snow White and the Huntsman embroidery", call_id="1"),
            unresolved,
            InvestigationDecision(
                status=InvestigationStatus.ANSWERED,
                answer="Maheen Khan [E1]; Alice Washburn [E2].",
                citations=("E1", "E2"),
            ),
        ]
    )
    search = QuerySearch(
        {"Snow White and the Huntsman embroidery": (first,), "Snow White actress only known feature film": (second,)}
    )
    result = await InvestigateQuestion(search, model, max_search_calls=2).execute(
        "Who worked on embroidery for Snow White and the Huntsman, and which actress appeared in Snow White as her only known feature film?",
        owner_id=None,
    )
    assert result.status == InvestigationStatus.ANSWERED and len(search.calls) == 2
    assert model.turns[-1].final_only and result.citations == ("E1", "E2")
