import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from investigation_engine.application.answers import (
    AnswerResult, AnswerStatus, GenerationUsage, ResolvedCitation,
)
from investigation_engine.application.investigations import (
    ConflictClaim, IdentifiedEvidence, InvestigationResult, InvestigationStatus,
    SearchDisposition, SearchRecord, TokenUsage,
)
from investigation_engine.application.retrieval import Evidence
from investigation_engine.application.ports.generation_errors import GenerationRateLimitError
from investigation_engine.application.ports.provider_diagnostics import attach_diagnostic
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.evaluation.answer_quality import (
    AnsweringRun, _conflict_supported, _coverage, _failure_analysis, _observed_outcome,
    _single_step_conflict_supported, answer_correctness,
    _annotation_unavailable_reason, console_summary, evaluate_answers, export_answer_quality,
    validate_answer_suite,
)
from investigation_engine.evaluation.retrieval import fingerprint
from investigation_engine.evaluation.checkpoints import load_checkpoint, save_checkpoint


@pytest.fixture
def snapshot():
    root = Path("data/samples/evaluation")
    return tuple(json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
                 for name in ("suite", "corpus"))


def _hit(doc, number=1):
    source_data = doc["source"]
    spans = tuple(SourceSpan(start=span["start"], end=span["end"], locators=tuple(
        SourceLocator(kind=LocatorKind(locator["kind"]), index=locator["index"])
        for locator in span["locators"])) for span in doc["source_spans"])
    return Evidence(
        document_id=UUID(int=number), document_version_id=UUID(int=number),
        content_sha256=doc["content_sha256"], start=0, end=len(doc["text"]),
        text=doc["text"], source=SourceReference(**{
            **source_data, "kind": SourceKind(source_data["kind"]),
        }), source_spans=spans, rank=1, score=.9, title=doc["title"],
    )


def _doc_for_gold(corpus, fact):
    for doc in corpus["documents"]:
        if doc["source"] == fact["source"]:
            return doc
    raise AssertionError("gold source missing")


def test_suite_annotations_are_consistent_and_bridge_is_branching(snapshot):
    suite, corpus = snapshot
    validate_answer_suite(suite, corpus)
    assert suite["answer_quality_version"] == "answer-quality-v1"
    assert all(len(c["required_evidence"]) == 4 and "two subject-to-person-to-attribute chains" in c["category_basis"].casefold()
               for c in suite["cases"] if c["category"] == "three_plus_hop")
    assert all(len(c["expected_conflict_claims"]) == 2 for c in suite["cases"]
               if c["category"] == "contradictory")


def test_suite_rejects_bad_expected_behavior_conflict_labels_and_bridge_interpretation(snapshot):
    suite, corpus = snapshot
    contradictory = next(c for c in suite["cases"] if c["category"] == "contradictory")
    contradictory["expected_conflict_claims"].pop()
    validate_answer_suite(suite, corpus)
    assert _annotation_unavailable_reason(contradictory) == "missing_conflict_claim_annotations"
    suite, corpus = snapshot
    bridge = next(c for c in suite["cases"] if c["category"] == "three_plus_hop")
    bridge["category_basis"] = "four-hop linear path"
    with pytest.raises(ValueError, match="branching interpretation"):
        validate_answer_suite(suite, corpus)


@pytest.mark.parametrize("answer,expected", [
    ("The answer is Małgorzata Braunek. [E1]", "correct"),
    ("Paris", "incorrect"),
    ("The director's mother was Małgorzata Braunek, according to the source.", "correct"),
])
def test_conservative_answer_value_scoring_and_human_review(snapshot, answer, expected):
    suite, _ = snapshot
    case = next(c for c in suite["cases"] if c["id"] == "simple:8813f87c0bdd11eba7f7acde48001122")
    assert answer_correctness(case, "answered", answer) == expected


def test_answer_alternatives_are_explicit_and_negated_expected_value_gets_no_credit(snapshot):
    suite, _ = snapshot
    case = next(c for c in suite["cases"] if c["id"].startswith("simple:e2a3"))
    case["acceptable_answers"] = ["12 June 1516", "June 12, 1516"]
    assert answer_correctness(case, "answered", "June 12, 1516") == "correct"
    assert answer_correctness(case, "answered", "It was not June 12, 1516; it was another date.") == "human_review"
    assert answer_correctness(case, "insufficient_evidence", None) == "no_answer"
    galati = next(c for c in suite["cases"] if c["id"].startswith("simple:019bed"))
    assert answer_correctness(galati, "answered", "The answer is Galati, with the spelling shown in the source.") == "human_review"


def test_citation_strict_and_partial_sentence_policies_deduplicate(snapshot):
    suite, corpus = snapshot
    case = next(c for c in suite["cases"] if c["category"] == "simple_factual")
    fact = case["required_evidence"][0]
    doc = _doc_for_gold(corpus, fact)
    full = _hit(doc)
    assert _coverage([("E1", full), ("E2", full)], [fact])["strict_recall"] == 1
    start, end = fact["start"], fact["end"]
    partial = replace(full, start=start, end=end - 1, text=doc["text"][start:end - 1])
    scores = _coverage([("E1", partial)], [fact])
    assert scores["strict_recall"] == 0
    assert scores["partial_recall"] == 1
    assert scores["unique_evidence_count"] == 1


@pytest.mark.parametrize("case_category,status,expected", [
    ("unanswerable", "insufficient_evidence", "correct_abstention"),
    ("unanswerable", "answered", "unsupported_answer"),
    ("unanswerable", "conflicting_evidence", "false_conflict"),
    ("two_hop", "insufficient_evidence", "incorrect_abstention"),
    ("contradictory", "answered", "missed_contradiction"),
    ("contradictory", "incomplete", "incomplete"),
])
def test_outcome_confusion_classes_distinguish_abstention_conflict_and_incomplete(snapshot, case_category, status, expected):
    suite, _ = snapshot
    case = next(c for c in suite["cases"] if c["category"] == case_category)
    assert _observed_outcome(case, status, "some answer", [], None) == expected


def test_conflict_requires_both_labeled_values_cited_from_their_own_occurrences(snapshot):
    suite, corpus = snapshot
    case = next(c for c in suite["cases"] if c["category"] == "contradictory")
    ids = {}
    for i, fact in enumerate(case["required_evidence"], 1):
        ids[f"E{i}"] = _hit(_doc_for_gold(corpus, fact), i)
    claims = [
        {"claim": "The code is amber.", "citations": ["E1"]},
        {"claim": "The code is violet.", "citations": ["E2"]},
    ]
    assert _conflict_supported(case, claims, ids) is True
    wrong_source = [{"claim": "The code is amber.", "citations": ["E2"]}, claims[1]]
    assert _conflict_supported(case, wrong_source, ids) is False


def test_single_step_can_get_conflict_credit_only_when_both_claim_values_and_sources_are_present(snapshot):
    suite, corpus = snapshot
    case = next(c for c in suite["cases"] if c["category"] == "contradictory")
    ids = {f"E{i}": _hit(_doc_for_gold(corpus, fact), i) for i, fact in enumerate(case["required_evidence"], 1)}
    assert _single_step_conflict_supported(
        case, "The sources conflict: one says amber [E1] and the other says violet [E2].", ["E1", "E2"], ids,
    ) is True
    assert _single_step_conflict_supported(case, "The sources are inconclusive.", ["E1", "E2"], ids) is False
    assert _observed_outcome(case, "answered", "amber versus violet", [], True) == "correct_conflict"


async def test_runner_scores_single_step_conflict_with_source_bound_inline_citations(snapshot):
    suite, corpus = snapshot
    case = next(c for c in suite["cases"] if c["category"] == "contradictory")
    resolved = tuple(ResolvedCitation(evidence_id=f"E{i}", evidence=_hit(
        _doc_for_gold(corpus, fact), i,
    )) for i, fact in enumerate(case["required_evidence"], 1))
    answer = "The sources conflict: one says amber [E1] and the other says violet [E2]."
    service = SimpleNamespace(execute=AsyncMock(return_value=AnswerResult(
        status=AnswerStatus.ANSWERED, answer=answer, citations=resolved,
        retrieved_evidence=resolved,
    )))
    run = AnsweringRun(
        name="single-step", mode="single_step", service=service,
        retrieval_mode="semantic", chunking={"strategy": "whole"},
        embedding={"model": "test"}, generation_model="test",
        answering_budgets={"max_evidence": 3, "max_context_chars": 2000},
    )
    report = await evaluate_answers(
        suite, corpus, [run], indexed_corpus_fingerprint=fingerprint(corpus),
        case_ids=(case["id"],),
    )
    row = report["runs"][0]["questions"][0]
    assert row["outcome"] == "correct_conflict"
    assert row["conflict_supports_both_labeled_claims"] is True


def test_checkpoint_round_trips_unicode_and_unpaired_surrogates_and_checks_identity(tmp_path):
    path = tmp_path / "result.json"
    row = {"id": "case:1", "answer": "Małgorzata 🧭", "diagnostic": "bad\ud800value",
           "usage": {"prompt_tokens": 17, "candidate_tokens": 3}}
    config = {"generation_model": "test"}
    save_checkpoint(path, question_id="case:1", mode="single_step", row=row, configuration=config)
    assert load_checkpoint(path, question_id="case:1", mode="single_step", configuration=config) == row
    assert path.read_bytes().isascii()
    with pytest.raises(ValueError, match="identity mismatch"):
        load_checkpoint(path, question_id="case:2", mode="single_step", configuration=config)
    with pytest.raises(ValueError, match="identity mismatch"):
        load_checkpoint(path, question_id="case:1", mode="single_step", configuration={"generation_model": "changed"})


async def test_result_callback_persists_before_later_report_aggregation_failure(snapshot, tmp_path, monkeypatch):
    import investigation_engine.evaluation.answer_quality as answer_quality

    suite, corpus = snapshot
    case = next(c for c in suite["cases"] if c["category"] == "simple_factual")
    hit = _hit(_doc_for_gold(corpus, case["required_evidence"][0]))
    citation = ResolvedCitation(evidence_id="E1", evidence=hit)
    service = SimpleNamespace(execute=AsyncMock(return_value=AnswerResult(
        status=AnswerStatus.ANSWERED, answer="Małgorzata Braunek [E1]", citations=(citation,),
        retrieved_evidence=(citation,), generation_requests=1,
        usage=GenerationUsage(prompt_tokens=19, candidate_tokens=5),
    )))
    run = AnsweringRun(name="quick", mode="single_step", service=service,
        retrieval_mode="semantic", chunking={"whole": True}, embedding={"same": True},
        generation_model="test", answering_budgets={"max_evidence": 5, "max_context_chars": 2000})
    checkpoint = tmp_path / "quick.json"
    def persist(name, row):
        assert name == "quick"
        save_checkpoint(checkpoint, question_id=case["id"], mode="single_step", row=row,
                        configuration=run.configuration())

    monkeypatch.setattr(answer_quality, "_aggregate", lambda _rows: (_ for _ in ()).throw(RuntimeError("aggregation")))
    with pytest.raises(RuntimeError, match="aggregation"):
        await evaluate_answers(suite, corpus, [run], indexed_corpus_fingerprint=fingerprint(corpus),
            case_ids=(case["id"],), on_result=persist)
    saved = load_checkpoint(checkpoint, question_id=case["id"], mode="single_step",
                             configuration=run.configuration())
    assert saved["answer"] == "Małgorzata Braunek [E1]"
    assert saved["usage"] == {"prompt_tokens": 19, "candidate_tokens": 5, "thinking_tokens": 0}


async def test_paired_runner_records_both_modes_without_passing_gold_to_application(snapshot, tmp_path):
    suite, corpus = snapshot
    case = next(c for c in suite["cases"] if c["category"] == "two_hop")
    fact_doc = _doc_for_gold(corpus, case["required_evidence"][0])
    hit = _hit(fact_doc)
    evidence = ResolvedCitation(evidence_id="E1", evidence=hit)
    single = SimpleNamespace(execute=AsyncMock(return_value=AnswerResult(
        status=AnswerStatus.ANSWERED, answer=case["expected_answer"], citations=(evidence,),
        retrieved_evidence=(evidence,), generation_requests=1, embedding_requests=1,
        usage=GenerationUsage(prompt_tokens=50, candidate_tokens=8), model_version="test-model",
    )))
    multi = SimpleNamespace(execute=AsyncMock(return_value=InvestigationResult(
        status=InvestigationStatus.ANSWERED, answer=case["expected_answer"], citations=("E1",),
        conflicts=(), evidence=(IdentifiedEvidence(evidence_id="E1", evidence=hit),),
        searches=(SearchRecord(sequence=1, tool_name="search_evidence", query="first fact",
            evidence_ids=("E1",), result_count=1, duration_ms=2, succeeded=True,
            disposition=SearchDisposition.SUCCEEDED),),
        generation_requests=2, embedding_requests=1, usage=TokenUsage(prompt_tokens=30, candidate_tokens=7),
        model_version="test-model",
    )))
    shared = dict(retrieval_mode="semantic", chunking={"strategy": "whole"},
                  embedding={"model": "same"}, generation_model="test-model")
    runs = [
        AnsweringRun(name="rag", mode="single_step", service=single,
                     answering_budgets={"max_evidence": 3, "max_context_chars": 2000}, **shared),
        AnsweringRun(name="investigation", mode="multi_step", service=multi,
                     investigation_budgets={"turns": 4, "searches": 2}, **shared),
    ]
    report = await evaluate_answers(suite, corpus, runs,
        indexed_corpus_fingerprint=fingerprint(corpus), case_ids=(case["id"],))
    assert report["evaluated_case_count"] == 1
    assert [run["questions"][0]["answer_correctness"] for run in report["runs"]] == ["correct", "correct"]
    assert report["runs"][0]["questions"][0]["retrieval_coverage"]["strict_recall"] == .5
    assert report["runs"][1]["questions"][0]["investigation_behavior"]["generation_requests"] == 2
    assert report["runs"][0]["questions"][0]["semantic_claim_support"]["unsupported_claims"] is None
    assert report["paired_comparisons"][0]["paired_cases"] == 1
    assert single.execute.await_args.args == (case["question"],)
    assert single.execute.await_args.kwargs == {"owner_id": None}
    assert case["expected_answer"] not in json.dumps(single.execute.await_args.args)
    export_answer_quality(report, tmp_path / "one")
    export_answer_quality(report, tmp_path / "two")
    first = (tmp_path / "one" / "answer-quality.json").read_bytes()
    second = (tmp_path / "two" / "answer-quality.json").read_bytes()
    assert first == second
    assert "Małgorzata" in first.decode("utf-8")
    assert console_summary(report).isascii()


async def test_incomplete_is_not_scored_as_abstention_and_provider_errors_are_sanitized(snapshot):
    suite, corpus = snapshot
    case = next(c for c in suite["cases"] if c["category"] == "unanswerable")
    failed = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("SECRET_SENTINEL")))
    run = AnsweringRun(name="multi", mode="multi_step", service=failed,
        retrieval_mode="semantic", chunking={"whole": True}, embedding={"same": True},
        generation_model="test", investigation_budgets={"turns": 1})
    report = await evaluate_answers(suite, corpus, [run],
        indexed_corpus_fingerprint=fingerprint(corpus), case_ids=(case["id"],))
    row = report["runs"][0]["questions"][0]
    assert row["status"] == "failed"
    assert row["outcome"] == "execution_failure"
    assert row["retrieval_coverage"]["strict_recall"] is None
    assert row["retrieved_evidence"] is None
    assert "SECRET_SENTINEL" not in json.dumps(report)


async def test_missing_gold_annotations_are_unavailable_and_do_not_call_provider(snapshot):
    suite, corpus = snapshot
    case = next(c for c in suite["cases"] if c["category"] == "two_hop")
    case["required_evidence"] = []
    service = SimpleNamespace(execute=AsyncMock())
    run = AnsweringRun(name="rag", mode="single_step", service=service,
        retrieval_mode="semantic", chunking={"whole": True}, embedding={"same": True},
        generation_model="test", answering_budgets={"max_evidence": 3, "max_context_chars": 2000})
    report = await evaluate_answers(suite, corpus, [run],
        indexed_corpus_fingerprint=fingerprint(corpus), case_ids=(case["id"],))
    row = report["runs"][0]["questions"][0]
    assert row["status"] == "unavailable"
    assert row["reason"] == "missing_gold_annotations"
    service.execute.assert_not_called()


async def test_live_provider_failure_stops_remaining_paired_calls(snapshot):
    suite, corpus = snapshot
    selected = [next(c for c in suite["cases"] if c["category"] == "simple_factual"),
                next(c for c in suite["cases"] if c["category"] == "two_hop")]
    stop = {"value": False}
    async def fail(question, *, owner_id):
        stop["value"] = True
        raise attach_diagnostic(GenerationRateLimitError("quota limit"),
            operation="generate_content", model_id="test", http_status=429,
            category="rate_or_quota_limit", request_count=1)
    service = SimpleNamespace(execute=fail)
    metadata = dict(retrieval_mode="semantic", chunking={"whole": True},
        embedding={"same": True}, generation_model="test")
    runs = [AnsweringRun(name="rag", mode="single_step", service=service,
            answering_budgets={"max_evidence": 3, "max_context_chars": 2000}, **metadata),
        AnsweringRun(name="investigation", mode="multi_step", service=service,
            investigation_budgets={"turns": 2}, **metadata)]
    report = await evaluate_answers(suite, corpus, runs,
        indexed_corpus_fingerprint=fingerprint(corpus),
        case_ids=tuple(case["id"] for case in selected), stop_requested=lambda: stop["value"])
    assert report["stopped_early"] is True
    assert report["runs"][0]["questions"][0]["outcome"] == "provider_failure"
    assert report["runs"][0]["questions"][0]["provider_diagnostic"]["http_status"] == 429
    assert report["runs"][0]["questions"][1]["status"] == "not_run"
    assert all(row["status"] == "not_run" for row in report["runs"][1]["questions"])


async def test_provider_error_is_not_included_in_paired_quality_comparison(snapshot):
    suite, corpus = snapshot
    case = next(c for c in suite["cases"] if c["category"] == "simple_factual")

    async def abstain(question, *, owner_id):
        return AnswerResult(status=AnswerStatus.INSUFFICIENT_EVIDENCE, answer=None)

    async def fail(question, *, owner_id):
        raise attach_diagnostic(GenerationRateLimitError("quota limit"),
            operation="generate_content", model_id="test", http_status=429,
            category="rate_or_quota_limit", request_count=1)

    metadata = dict(retrieval_mode="semantic", chunking={"whole": True},
                    embedding={"same": True}, generation_model="test")
    runs = [
        AnsweringRun(name="quick", mode="single_step", service=SimpleNamespace(execute=abstain),
                     answering_budgets={"max_evidence": 3, "max_context_chars": 2000}, **metadata),
        AnsweringRun(name="investigation", mode="multi_step", service=SimpleNamespace(execute=fail),
                     investigation_budgets={"turns": 2}, **metadata),
    ]
    report = await evaluate_answers(suite, corpus, runs,
        indexed_corpus_fingerprint=fingerprint(corpus), case_ids=(case["id"],))
    assert report["runs"][0]["questions"][0]["outcome"] == "incorrect_abstention"
    assert report["runs"][1]["questions"][0]["status"] == "provider_error"
    assert report["paired_comparisons"][0]["paired_cases"] == 0


def test_missed_contradiction_is_not_misclassified_as_only_a_citation_failure():
    retrieval = {"strict_recall": 1.0, "strict_fact_count": 2, "gold_fact_count": 2}
    citations = {"strict_recall": 0.0, "strict_fact_count": 0, "gold_fact_count": 2}
    failure = _failure_analysis("missed_contradiction", "insufficient_evidence",
                                retrieval, citations, False)
    assert failure["classification"] == "reasoning_or_synthesis_failure"

