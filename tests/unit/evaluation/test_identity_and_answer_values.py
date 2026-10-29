"""Answer values and source grounding are independent, reviewed boundaries."""

import json
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from investigation_engine.application.answers import AnswerResult, AnswerStatus, ResolvedCitation
from investigation_engine.application.investigations import (
    InvestigateQuestion,
    InvestigationDecision,
    InvestigationStatus,
    SearchEvidenceCall,
)
from investigation_engine.application.retrieval import Evidence
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.evaluation.answer_aliases import normalization_record
from investigation_engine.evaluation.answer_quality import (
    AnsweringRun,
    _coverage,
    _evidence_dict,
    answer_correctness,
    evaluate_answers,
    export_answer_quality,
)
from investigation_engine.evaluation.grounding import (
    apply_grounding_review,
    independent_assessment,
    review_fingerprint,
)
from investigation_engine.evaluation.rescore import rescore_saved_report
from investigation_engine.evaluation.retrieval import fingerprint


def case(question="What nationality is the lead of Project Orion?", gold="United States"):
    return {
        "id": "synthetic:identity",
        "question": question,
        "expected_behavior": "answer",
        "expected_answer": gold,
        "acceptable_answers": [gold],
    }


@pytest.mark.parametrize("answer", ["American", "American.", "American [E1] [E2]", "United States"])
def test_reviewed_nationality_values_match_without_mutating_gold(answer):
    annotation = case()
    original = deepcopy(annotation)
    assert answer_correctness(annotation, "answered", answer) == "correct"
    assert annotation == original
    record = normalization_record(annotation, answer)
    assert record["gold_acceptable_answers"] == ["United States"]
    assert record["evaluation_only_aliases"] == ["American"]
    record["gold_acceptable_answers"].append("changed report metadata")
    assert annotation == original


@pytest.mark.parametrize(
    "answer",
    [
        "South American",
        "North American",
        "Native American",
        "American Samoa",
        "Not American",
        "American or Canadian",
        "Canadian",
        "British",
    ],
)
def test_alias_does_not_accept_broader_geography_negation_or_guesses(answer):
    assert answer_correctness(case(), "answered", answer) != "correct"


@pytest.mark.parametrize(
    "question,gold,answer",
    [
        ("Where does the lead live?", "United States", "American"),
        ("What citizenship does the lead hold?", "United States", "American"),
        ("What nationality is the lead?", "United Kingdom", "British"),
        ("What nationality is the lead?", "Canada", "Canadian"),
        ("What nationality is the lead?", "United States of America", "American"),
    ],
)
def test_alias_rule_requires_reviewed_gold_and_explicit_question_context(question, gold, answer):
    assert answer_correctness(case(question, gold), "answered", answer) != "correct"
    assert normalization_record(case(question, gold), answer)["evaluation_only_aliases"] == []


class ScriptedModel:
    """Fixed decisions exercise application plumbing, not prompt effectiveness."""

    def __init__(self, actions):
        self.actions, self.turns = list(actions), []

    async def next_action(self, turn):
        self.turns.append(turn)
        return self.actions.pop(0)


class Search:
    def __init__(self, hits):
        self.hits, self.calls = hits, []

    async def execute(self, query, limit, *, owner_id):
        self.calls.append((query, owner_id))
        return self.hits[query][:limit]


def evidence(registration, source_id, text):
    span = SourceSpan(start=0, end=len(text), locators=(SourceLocator(kind=LocatorKind.SENTENCE, index=0),))
    document, version = registration(body=text, source_id=source_id, source_spans=(span,))
    return Evidence(
        document_id=document.id,
        document_version_id=version.id,
        content_sha256=version.content.sha256,
        start=0,
        end=len(text),
        text=text,
        source=version.source,
        source_spans=version.source_spans,
        rank=1,
        score=0.9,
    )


async def investigation(registration, *, explicit_link, answer="American", citations=("E1", "E2"), abstain=False):
    first = evidence(registration, "project", "Project Orion is led by Alex Reed, a botanist at Cedar Lab.")
    second = evidence(
        registration,
        "biography",
        (
            "Alex Reed, the Cedar Lab botanist leading Project Orion, is American."
            if explicit_link
            else "Alex Reed, a composer at Summit Music unrelated to the Cedar Lab botanist of the same name, is American."
        ),
    )
    irrelevant = evidence(registration, "unrelated", "Alex Reed, a dancer born in 1910, was Canadian.")
    decision = InvestigationDecision(
        status=InvestigationStatus.INSUFFICIENT_EVIDENCE if abstain else InvestigationStatus.ANSWERED,
        answer=None if abstain else answer,
        citations=() if abstain else citations,
    )
    model = ScriptedModel(
        [
            SearchEvidenceCall(query="Project Orion lead", call_id="1"),
            SearchEvidenceCall(query="Alex Reed Cedar Lab nationality", call_id="2"),
            decision,
        ]
    )
    search = Search({"Project Orion lead": (first,), "Alex Reed Cedar Lab nationality": (second, irrelevant)})
    result = await InvestigateQuestion(search, model).execute(case()["question"], owner_id="trusted-owner")
    return result, model, search, first, second


def result_row(result, gold_hits):
    identified = {item.evidence_id: item.evidence for item in result.evidence}
    gold = [
        {
            "source": asdict(hit.source),
            "content_sha256": hit.content_sha256,
            "start": hit.start,
            "end": hit.end,
            "sentence_index": 0,
        }
        for hit in gold_hits
    ]
    return {
        **case(),
        "status": result.status.value,
        "answer": result.answer,
        "citations": list(result.citations),
        "retrieved_evidence": [_evidence_dict(key, hit) for key, hit in identified.items()],
        "citation_evidence": [_evidence_dict(key, identified[key]) for key in result.citations],
        "citation_coverage": _coverage([(key, identified[key]) for key in result.citations], gold),
        "answer_correctness": answer_correctness(case(), result.status.value, result.answer),
    }


async def test_same_name_different_contexts_remain_distinct_and_can_end_unresolved(registration):
    result, model, search, first, second = await investigation(registration, explicit_link=False, abstain=True)
    assert result.status == InvestigationStatus.INSUFFICIENT_EVIDENCE
    assert result.answer is None and result.citations == ()
    assert len(result.evidence) == 3
    turn = model.turns[-1]
    assert turn.exchanges[0].evidence[0].evidence.source.source_id == "project"
    assert turn.exchanges[1].evidence[0].evidence.source.source_id == "biography"
    assert "botanist at Cedar Lab" in turn.exchanges[0].evidence[0].evidence.text
    assert "composer at Summit Music" in turn.exchanges[1].evidence[0].evidence.text
    assert all(owner == "trusted-owner" for _, owner in search.calls)
    # A scripted abstention proves this unresolved outcome is representable;
    # it does not claim that real models always obey the identity guidance.


async def test_gold_matching_correct_answer_can_still_have_unsupported_identity_join(registration):
    result, _, _, first, second = await investigation(registration, explicit_link=False)
    row = result_row(result, (first, second))
    axes = independent_assessment(row)
    assert axes["answer_correctness"] == "correct" and axes["citation_identifier_validity"] == "valid"
    assert axes["gold_supporting_fact_matching"]["strict_recall"] == 1
    assert axes["semantic_evidence_support"] == "requires_review"
    reviewed = apply_grounding_review(
        row,
        expected_fingerprint=review_fingerprint(row),
        evidence_support="unsupported",
        entity_identity="unresolved",
        rationale="The project lead is a botanist; the cited nationality belongs to a composer with no identity link.",
        reviewer="synthetic source-review fixture",
    )
    assert reviewed["independent_assessment"]["answer_correctness"] == "correct"
    assert reviewed["independent_assessment"]["semantic_evidence_support"] == "unsupported"
    assert "grounding_review" not in row


async def test_explicit_contextual_identity_can_be_reviewed_as_supported(registration):
    result, _, _, first, second = await investigation(registration, explicit_link=True)
    row = result_row(result, (first, second))
    assert independent_assessment(row)["semantic_evidence_support"] == "requires_review"
    reviewed = apply_grounding_review(
        row,
        expected_fingerprint=review_fingerprint(row),
        evidence_support="supported",
        entity_identity="established",
        rationale="The nationality passage explicitly repeats the organization, role, and project relationship.",
        reviewer="synthetic source-review fixture",
    )
    assert reviewed["independent_assessment"]["answer_correctness"] == "correct"
    assert reviewed["independent_assessment"]["semantic_evidence_support"] == "supported"


@pytest.mark.parametrize(
    "answer,citations,correctness,recall",
    [
        ("American", ("E3",), "correct", 0),
        ("Canadian", ("E1", "E2"), "incorrect", 1),
    ],
)
async def test_real_but_irrelevant_citations_are_separate_from_answer_value(
    registration, answer, citations, correctness, recall
):
    result, _, _, first, second = await investigation(
        registration, explicit_link=True, answer=answer, citations=citations
    )
    row = result_row(result, (first, second))
    axes = independent_assessment(row)
    assert axes["answer_correctness"] == correctness
    assert axes["citation_identifier_validity"] == "valid"
    assert axes["gold_supporting_fact_matching"]["strict_recall"] == recall
    assert axes["semantic_evidence_support"] == "requires_review"
    reviewed = apply_grounding_review(
        row,
        expected_fingerprint=review_fingerprint(row),
        evidence_support="unsupported",
        entity_identity="unresolved" if citations == ("E3",) else "established",
        rationale="The cited passage either concerns another profile or states a different nationality from the answer.",
        reviewer="synthetic source-review fixture",
    )
    assert reviewed["independent_assessment"]["answer_correctness"] == correctness
    assert reviewed["independent_assessment"]["semantic_evidence_support"] == "unsupported"


async def test_grounding_review_is_invalidated_by_changed_answer_or_evidence(registration):
    result, _, _, first, second = await investigation(registration, explicit_link=True)
    row = result_row(result, (first, second))
    reviewed = apply_grounding_review(
        row,
        expected_fingerprint=review_fingerprint(row),
        evidence_support="supported",
        entity_identity="established",
        rationale="Explicit identity context.",
        reviewer="fixture",
    )
    for changed in ("answer", "evidence"):
        mutated = deepcopy(reviewed)
        if changed == "answer":
            mutated["answer"] = "Canadian"
        else:
            mutated["citation_evidence"][0]["text"] = "Different evidence"
        with pytest.raises(ValueError, match="does not match"):
            independent_assessment(mutated)
    assert (
        independent_assessment({"status": "provider_error", "answer_correctness": "unavailable"})[
            "semantic_evidence_support"
        ]
        == "unavailable"
    )


async def test_actual_evaluator_records_alias_and_requires_review_even_for_all_gold_sources(tmp_path):
    root = Path("data/samples/evaluation")
    suite = json.loads((root / "suite.json").read_text(encoding="utf-8"))
    corpus = json.loads((root / "corpus.json").read_text(encoding="utf-8"))
    annotation = next(
        c
        for c in suite["cases"]
        if c["expected_answer"] == "United States" and c["question"].startswith("What nationality")
    )
    before = deepcopy(annotation)
    hits = []
    for index, fact in enumerate(annotation["required_evidence"], 1):
        doc = next(d for d in corpus["documents"] if d["source"] == fact["source"])
        spans = tuple(
            SourceSpan(
                start=s["start"],
                end=s["end"],
                locators=tuple(
                    SourceLocator(kind=LocatorKind(loc["kind"]), index=loc["index"]) for loc in s["locators"]
                ),
            )
            for s in doc["source_spans"]
        )
        hits.append(
            Evidence(
                document_id=UUID(int=index),
                document_version_id=UUID(int=index),
                content_sha256=doc["content_sha256"],
                start=0,
                end=len(doc["text"]),
                text=doc["text"],
                source=SourceReference(**{**doc["source"], "kind": SourceKind(doc["source"]["kind"])}),
                source_spans=spans,
                rank=index,
                score=0.9,
            )
        )
    refs = tuple(ResolvedCitation(evidence_id=f"E{i}", evidence=hit) for i, hit in enumerate(hits, 1))
    service = SimpleNamespace(
        execute=AsyncMock(
            return_value=AnswerResult(
                status=AnswerStatus.ANSWERED, answer="American", citations=refs, retrieved_evidence=refs
            )
        )
    )
    run = AnsweringRun(
        name="test",
        mode="single_step",
        service=service,
        retrieval_mode="semantic",
        chunking={},
        embedding={},
        generation_model="fake",
        answering_budgets={"max_evidence": 5},
    )
    report = await evaluate_answers(
        suite, corpus, [run], indexed_corpus_fingerprint=fingerprint(corpus), case_ids=(annotation["id"],)
    )
    row = report["runs"][0]["questions"][0]
    assert row["answer_correctness"] == "correct" and row["outcome"] == "correct_answer"
    assert row["answer_normalization"]["matched_alias"] == "American"
    assert row["acceptable_answers"] == ["United States"] and annotation == before
    assert row["independent_assessment"]["semantic_evidence_support"] == "requires_review"
    assert row["citation_coverage"]["strict_recall"] == 1
    export_answer_quality(report, tmp_path)
    text = (tmp_path / "answer-quality.md").read_text(encoding="utf-8")
    assert "Semantic evidence support" in text and "requires_review" in text
    legacy = deepcopy(report)
    legacy.pop("answer_scorer_version")
    old_row = legacy["runs"][0]["questions"][0]
    old_row["answer_correctness"], old_row["outcome"] = "incorrect", "incorrect_answer"
    original = deepcopy(legacy)
    reviews = [
        {
            "mode": "single_step",
            "case_id": annotation["id"],
            "expected_fingerprint": review_fingerprint(old_row),
            "evidence_support": "unsupported",
            "entity_identity": "unresolved",
            "rationale": "The cited profiles do not establish their identity link.",
            "reviewer": "offline fixture source audit",
        }
    ]
    rescored = rescore_saved_report(legacy, suite, corpus, reviews=reviews)
    revised = rescored["runs"][0]["questions"][0]
    assert legacy == original
    assert revised["answer"] == old_row["answer"] and revised["citation_evidence"] == old_row["citation_evidence"]
    assert revised["previous_answer_scoring"]["answer_correctness"] == "incorrect"
    assert revised["answer_correctness"] == "correct"
    assert revised["independent_assessment"]["semantic_evidence_support"] == "unsupported"
    export_answer_quality(rescored, tmp_path / "rescored")
    reviewed_text = (tmp_path / "rescored" / "answer-quality.md").read_text(encoding="utf-8")
    assert "unsupported" in reviewed_text and "Source review (offline fixture source audit)" in reviewed_text
    with pytest.raises(ValueError, match="unchanged suite"):
        rescore_saved_report({**legacy, "suite_fingerprint": "wrong"}, suite, corpus)
