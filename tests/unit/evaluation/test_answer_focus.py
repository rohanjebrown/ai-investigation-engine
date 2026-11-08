import inspect
import json
import sys
from copy import deepcopy
from hashlib import sha256
from unittest.mock import Mock

import pytest

from investigation_engine.evaluation.answer_focus import assess_answer, assessment_summary, extract_answer
from investigation_engine.evaluation.answer_reviews import import_reviews, rescore_saved_run, review_packet
from investigation_engine.evaluation.metrics import answer_scores
from investigation_engine.evaluation.reporting import read_jsonl, write_json

QUESTION = "Who is William Jolliffe, 4th Baron Hylton's paternal grandfather?"
REFERENCE = "Hedworth Jolliffe, 2nd Baron Hylton"
CORRECT = "William Jolliffe, 4th Baron Hylton's paternal grandfather was Hedworth Jolliffe, 2nd Baron Hylton."


def test_sentence_extraction_preserves_official_metrics_and_has_no_reference_input():
    result = assess_answer(QUESTION, CORRECT, REFERENCE)
    assert result["official_whole_response"] == {"exact_match": 0, "f1": 5 / 9}
    assert result["extraction"]["status"] == "available"
    assert result["extraction"]["answer"] == REFERENCE
    assert result["answer_focused"] == {"exact_match": 1, "f1": 1}
    assert result["manual_factual_judgment"] is None
    assert set(inspect.signature(extract_answer).parameters) == {"question", "answer", "status"}
    unrelated = assess_answer(QUESTION, CORRECT, "Completely unrelated reference")
    assert unrelated["extraction"] == result["extraction"]
    assert unrelated["answer_focused"]["exact_match"] == 0


@pytest.mark.parametrize(
    "question,answer,expected",
    [
        (QUESTION, CORRECT + " [E1] [E2]", REFERENCE),
        (QUESTION.replace("'", "’"), CORRECT.replace("'", "’"), REFERENCE),
        ("Who is Wisigard's father-in-law?", "Wisigard's father-in-law was Theuderich I.", "Theuderich I"),
        ("Who is Wisigard's father-in-law?", "Theuderich I [E1]", "Theuderich I"),
        ("Who won?", "The answer is Ada Lovelace.", "Ada Lovelace"),
        ("Who won?", "Answer: Ada Lovelace.", "Ada Lovelace"),
        ("Where was Ada born?", "Ada was born in London.", "London"),
        ("When was Ada born?", "Ada was born on 10 December 1815.", "10 December 1815"),
        ("When was the event?", "The event was on June 12, 1516.", "June 12, 1516"),
        ("When was it?", "1516-06-12", "1516-06-12"),
        ("When was it?", "12/06/1516", "12/06/1516"),
        ("How many were there?", "42.", "42"),
        ("What was the change?", "-3.14%", "-3.14%"),
        ("What was the distance?", "12 km", "12 km"),
        (
            "Who lived longer, Friedrich Paschen or Ernst Bessey?",
            "Friedrich Paschen lived longer.",
            "Friedrich Paschen",
        ),
        ("Who lived longer, Friedrich Paschen or Ernst Bessey?", "Ernst Bessey", "Ernst Bessey"),
        ("Was he born there?", "Yes. [E1]", "Yes"),
        ("Was he born there?", "No.", "No"),
        ("Did that happen?", "noanswer", "noanswer"),
    ],
)
def test_supported_values_are_extracted_without_reference(question, answer, expected):
    actual = extract_answer(question, answer)
    assert actual["status"] == "available" and actual["answer"] == expected


@pytest.mark.parametrize(
    "question,answer",
    [
        ("Who is Wisigard's father-in-law?", "Wisigard's father was Wacho."),
        ("Who is Wisigard's father-in-law?", "Theuderich I's father-in-law was Wisigard."),
        ("Who is Wisigard's father-in-law?", "Wisigard's father was Wacho. The reference mentions Theuderich I."),
        (QUESTION, "William Jolliffe's paternal grandfather was William George Hylton Jolliffe, 1st Baron Hylton."),
        (QUESTION, "The reference mentions Hedworth Jolliffe, 2nd Baron Hylton."),
        (QUESTION, "Regarding Hedworth Jolliffe, 2nd Baron Hylton"),
        (QUESTION, "It was not Hedworth Jolliffe, 2nd Baron Hylton."),
        (QUESTION, "The answer is not Hedworth Jolliffe, 2nd Baron Hylton; it was William Jolliffe."),
        (QUESTION, "Hedworth Jolliffe or William Jolliffe"),
        (QUESTION, "Hedworth Jolliffe / William Jolliffe"),
        (QUESTION, "Hedworth Jolliffe, William Jolliffe"),
        (QUESTION, "Perhaps Hedworth Jolliffe, 2nd Baron Hylton"),
        (QUESTION, CORRECT + " His grandfather was William George Hylton Jolliffe."),
        (QUESTION, CORRECT + " However, that is incorrect."),
        ("Was he born there?", "Yes, but he was not born there."),
        ("Was he born there?", "Yes or no."),
        ("Who was there?", "No."),
        ("How many were there?", "12 18"),
        (QUESTION, "He doesn't have that grandfather."),
    ],
)
def test_relationship_negation_candidates_and_nonasserted_references_abstain(question, answer):
    extraction = extract_answer(question, answer)
    assert extraction["status"] in ("ambiguous", "unavailable")
    assert extraction["answer"] is None
    assessment = assess_answer(question, answer, REFERENCE)
    assert assessment["answer_focused"] == {"exact_match": None, "f1": None}
    assert assessment["manual_factual_judgment"] is None


def test_wrong_asserted_entity_is_not_selected_by_reference_occurrence_or_valid_citation():
    wrong = "William Jolliffe, 4th Baron Hylton's paternal grandfather was William George Hylton Jolliffe, 1st Baron Hylton. [E1]"
    result = assess_answer(QUESTION, wrong, REFERENCE)
    assert result["extraction"]["answer"] == "William George Hylton Jolliffe, 1st Baron Hylton"
    assert result["answer_focused"]["exact_match"] == 0
    assert result["manual_factual_judgment"] is None
    mentioned = wrong + " The reference calls him Hedworth Jolliffe, 2nd Baron Hylton."
    assert assess_answer(QUESTION, mentioned, REFERENCE)["answer_focused"]["exact_match"] is None


@pytest.mark.parametrize("status", ["insufficient_evidence", "conflicting_evidence", "incomplete", "failed"])
def test_abstention_failures_and_conflicts_never_get_invented_focused_scores(status):
    a = assess_answer(QUESTION, None, REFERENCE, status=status)
    assert a["extraction"]["answer"] is None
    assert a["answer_focused"] == {"exact_match": None, "f1": None}
    assert a["manual_factual_judgment"] is None


def test_yes_no_official_handling_is_unchanged_and_not_silently_repaired():
    assert answer_scores("Yes indeed", "yes") == {"exact_match": 0, "f1": 0}
    assert answer_scores("No", "yes") == {"exact_match": 0, "f1": 0}
    a = assess_answer("Did he?", "Yes, he did.", "yes")
    assert a["official_whole_response"] == {"exact_match": 0, "f1": 0}
    assert a["answer_focused"]["exact_match"] is None


def saved_run(tmp_path, *, answer=CORRECT, question=QUESTION, reference=REFERENCE, rows=1):
    source = tmp_path / "source"
    source.mkdir()
    write_json(source / "manifest.json", {"run_id": "saved-run", "status": "complete"})
    (source / "questions.jsonl").write_text(
        json.dumps({"id": "q1", "question": question, "reference_answer": reference, "gold": []}) + "\n"
    )
    entries = [
        {
            "question_id": "q1",
            "mode": "investigation_hybrid",
            "repetition": n + 1,
            "warmup": False,
            "status": "answered",
            "answer": answer,
            "expected_answer": reference,
            "answer_metrics": answer_scores(answer, reference),
            "citation_ids": ["E1"],
            "citation_structure_valid": True,
            "selected_evidence": [
                {"evidence_id": "v1:0:4", "citation_id": "E1", "content_sha256": "a" * 64, "start": 0, "end": 4}
            ],
        }
        for n in range(rows)
    ]
    (source / "answer_results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in entries))
    diagnostics = [
        {
            "question_id": "q1",
            "mode": row["mode"],
            "repetition": row["repetition"],
            "warmup": False,
            "generated_answer": answer,
            "evidence": [{**row["selected_evidence"][0], "text": "fact"}],
        }
        for row in entries
    ]
    (source / "grounding_review.jsonl").write_text("".join(json.dumps(row) + "\n" for row in diagnostics))
    write_json(
        source / "summary.json",
        {
            "modes": {
                "investigation_hybrid": {
                    "answer_exact_match": entries[0]["answer_metrics"]["exact_match"],
                    "answer_f1": entries[0]["answer_metrics"]["f1"],
                }
            }
        },
    )
    return source


def test_offline_rescore_preserves_originals_and_imports_explicit_manual_review(tmp_path):
    source = saved_run(tmp_path)
    hashes = {path.name: sha256(path.read_bytes()).hexdigest() for path in source.iterdir()}
    output = tmp_path / "rescored"
    receipt = rescore_saved_run(source, output)
    assert receipt["provider_requests_during_rescore"] == receipt["database_queries_during_rescore"] == 0
    row = read_jsonl(output / "answer_results.jsonl")[0]
    assert row["answer_metrics"] == {"exact_match": 0, "f1": 5 / 9}
    assert row["answer_assessment"]["answer_focused"]["exact_match"] == 1
    packet = read_jsonl(output / "review_packets.jsonl")[0]
    assert packet["judgment"] is None and packet["reference_answer"] == REFERENCE
    assert packet["supporting_evidence"][0]["text"] == "fact"
    assert packet["citation_evidence"][0]["citation_id"] == "E1"
    summary = json.loads((output / "summary.json").read_text())["answer_evaluation"]["investigation_hybrid"]
    assert summary["reviewed_answer_accuracy"] is None and summary["unreviewed_answers"] == 1
    review = tmp_path / "reviews.jsonl"
    packet.update(
        judgment="correct",
        reviewer="Unit test reviewer",
        explanation="Reviewed asserted grandfather; citation validity alone is insufficient.",
    )
    review.write_text(json.dumps(packet) + "\n")
    reviewed = tmp_path / "reviewed"
    rescore_saved_run(source, reviewed, reviews=[review])
    assessed = json.loads((reviewed / "summary.json").read_text())["answer_evaluation"]["investigation_hybrid"]
    assert assessed["reviewed_answer_accuracy"] == 1 and assessed["reviewed_accuracy_denominator"] == 1
    assert assessed["unreviewed_answers"] == 0
    assert {path.name: sha256(path.read_bytes()).hexdigest() for path in source.iterdir()} == hashes
    assert "Official whole-response EM" in (reviewed / "report.md").read_text()


@pytest.mark.parametrize(
    "answer,question,reference",
    [
        ("Wisigard's father was Wacho.", "Who is Wisigard's father-in-law?", "Theuderich I"),
        (
            "William Jolliffe's paternal grandfather was William George Hylton Jolliffe, 1st Baron Hylton.",
            QUESTION,
            REFERENCE,
        ),
    ],
)
def test_incorrect_relationship_is_reviewable_as_incorrect_not_citation_or_mention_credit(
    tmp_path, answer, question, reference
):
    source = saved_run(tmp_path, answer=answer, question=question, reference=reference)
    out = tmp_path / "unreviewed"
    rescore_saved_run(source, out)
    packet = read_jsonl(out / "review_packets.jsonl")[0]
    assert packet["extraction"]["status"] != "available"
    assert packet["judgment"] is None
    packet.update(judgment="incorrect", reviewer="Regression reviewer")
    path = tmp_path / "review.jsonl"
    path.write_text(json.dumps(packet) + "\n")
    rescore_saved_run(source, tmp_path / "reviewed", reviews=[path])
    row = read_jsonl(tmp_path / "reviewed/answer_results.jsonl")[0]
    assert row["citation_structure_valid"]
    assert row["answer_assessment"]["manual_factual_judgment"] == "incorrect"
    assert row["answer_assessment"]["answer_focused"]["exact_match"] is None
    assert (
        json.loads((tmp_path / "reviewed/summary.json").read_text())["answer_evaluation"]["investigation_hybrid"][
            "reviewed_answer_accuracy"
        ]
        == 0
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("generated_answer", "changed"),
        ("question", "changed"),
        ("reference_answer", "changed"),
        ("extracted_answer", "changed"),
        ("citations", []),
        ("supporting_evidence", []),
    ],
)
def test_stale_or_changed_review_context_is_rejected(field, value, monkeypatch):
    row = {
        "question_id": "q1",
        "mode": "quick_answer",
        "repetition": 1,
        "warmup": False,
        "status": "answered",
        "answer": CORRECT,
        "citation_ids": ["E1"],
        "selected_evidence": [{"citation_id": "E1", "content_sha256": "a" * 64, "start": 0, "end": 4}],
    }
    reference = {"question": QUESTION, "reference_answer": REFERENCE}
    packet = review_packet("run", row, reference, assess_answer(QUESTION, CORRECT, REFERENCE))
    changed = deepcopy(packet)
    changed[field] = value
    # Even replacing the displayed fingerprint with the old expected value
    # cannot conceal a changed question/reference/answer/evidence body.
    import investigation_engine.evaluation.answer_reviews as module

    monkeypatch.setattr(module, "iter_jsonl", lambda path: iter([changed]))
    with pytest.raises(ValueError, match="does not match"):
        import_reviews(["review"], [packet])


def test_accuracy_denominator_excludes_unreviewed_unverifiable_and_counts_partial_separately():
    values = [assess_answer(QUESTION, CORRECT, REFERENCE) for _ in range(5)]
    for value, judgment in zip(
        values, ["correct", "incorrect", "partially_correct", "unverifiable", None], strict=True
    ):
        value["manual_factual_judgment"] = judgment
    summary = assessment_summary(values)
    assert summary["reviewed_accuracy_denominator"] == 3
    assert summary["reviewed_answer_accuracy"] == 1 / 3
    assert summary["judgment_counts"]["partially_correct"] == 1
    assert summary["judgment_counts"]["unverifiable"] == 1
    assert summary["unreviewed_answers"] == 1
    unavailable = assessment_summary([assess_answer(QUESTION, "It was not Hedworth Jolliffe.", REFERENCE)])
    assert unavailable["answer_focused_em"] is None and unavailable["answer_focused_denominator"] == 0
    assert unavailable["ambiguous_answers"] == 1


def test_changed_original_official_scores_fail_closed_before_output(tmp_path):
    source = saved_run(tmp_path)
    rows = read_jsonl(source / "answer_results.jsonl")
    rows[0]["answer_metrics"]["exact_match"] = 1
    (source / "answer_results.jsonl").write_text(json.dumps(rows[0]) + "\n")
    with pytest.raises(ValueError, match="Original official"):
        rescore_saved_run(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("problem", ["duplicate", "unknown", "unattributed", "unknown_judgment"])
def test_review_import_is_explicit_attributed_and_never_last_wins(tmp_path, problem):
    source = saved_run(tmp_path)
    rescore_saved_run(source, tmp_path / "packets")
    packet = read_jsonl(tmp_path / "packets/review_packets.jsonl")[0]
    packet.update(judgment="correct", reviewer="Reviewer")
    if problem == "unknown":
        packet["result_id"] = "another-run"
    elif problem == "unattributed":
        packet["reviewer"] = None
    elif problem == "unknown_judgment":
        packet["judgment"] = "probably_correct"
    path = tmp_path / "reviews.jsonl"
    path.write_text((json.dumps(packet) + "\n") * (2 if problem == "duplicate" else 1))
    with pytest.raises(ValueError):
        rescore_saved_run(source, tmp_path / "rejected", reviews=[path])
    assert not (tmp_path / "rejected").exists()


def test_review_round_trip_from_derived_run_and_warmup_denominators(tmp_path):
    source = saved_run(tmp_path, rows=2)
    entries = read_jsonl(source / "answer_results.jsonl")
    entries[0]["warmup"] = True
    (source / "answer_results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in entries))
    diagnostics = read_jsonl(source / "grounding_review.jsonl")
    diagnostics[0]["warmup"] = True
    (source / "grounding_review.jsonl").write_text("".join(json.dumps(row) + "\n" for row in diagnostics))
    first = tmp_path / "first"
    rescore_saved_run(source, first)
    packets = read_jsonl(first / "review_packets.jsonl")
    packets[0].update(judgment="incorrect", reviewer="Warmup reviewer")
    packets[1].update(judgment="correct", reviewer="Measured reviewer")
    review = tmp_path / "review.jsonl"
    review.write_text("".join(json.dumps(row) + "\n" for row in packets))
    second = tmp_path / "second"
    rescore_saved_run(first, second, reviews=[review])
    summary = json.loads((second / "summary.json").read_text())["answer_evaluation"]["investigation_hybrid"]
    assert summary["attempts"] == summary["independent_questions"] == 1
    assert summary["reviewed_answer_accuracy"] == 1 and summary["reviewed_accuracy_denominator"] == 1
    assert summary["official_whole_response_f1"] == 5 / 9


async def test_future_runner_attaches_focus_without_changing_official_scores_or_factual_judgment(tmp_path):
    from unittest.mock import AsyncMock

    from tests.unit.evaluation.test_structured_evaluation import case, manifest, service

    from investigation_engine.application.answers import AnswerStatus, GenerationResult
    from investigation_engine.application.use_cases.answer_question import AnswerQuestion
    from investigation_engine.evaluation.answer_quality import AnsweringRun
    from investigation_engine.evaluation.runner import EvaluationConfig, run_evaluation
    from investigation_engine.evaluation.telemetry import ObservedGenerator, RequestBudget

    search, _ = service(cache={QUESTION: [1.0] + [0.0] * 767})
    generator = type(
        "FakeGenerator",
        (),
        {
            "generate": AsyncMock(
                return_value=GenerationResult(status=AnswerStatus.ANSWERED, answer=CORRECT, citations=("E1",))
            )
        },
    )()
    application = AnswerQuestion(search, ObservedGenerator(generator, RequestBudget(1)))
    adapted = AnsweringRun(
        name="quick_answer",
        mode="single_step",
        service=application,
        retrieval_mode="semantic",
        chunking={},
        embedding={},
        generation_model="fake",
        answering_budgets={},
    )
    reference = {**case(), "question": QUESTION, "reference_answer": REFERENCE}
    summary = await run_evaluation(
        [reference],
        EvaluationConfig(modes=("quick_answer",), allow_generation=True, max_generation_requests=1),
        {"quick_answer": {"service": application, "answer_run": adapted}},
        output=tmp_path / "run",
        manifest=manifest(),
    )
    row = read_jsonl(tmp_path / "run/answer_results.jsonl")[0]
    assert row["answer_metrics"] == {"exact_match": 0, "f1": 5 / 9}
    assert row["answer_assessment"]["answer_focused"] == {"exact_match": 1, "f1": 1}
    assert row["answer_assessment"]["manual_factual_judgment"] is None
    assert summary["modes"]["quick_answer"]["answer_assessment"]["reviewed_answer_accuracy"] is None


def test_rescore_cli_is_offline_and_cannot_overwrite_source(tmp_path, monkeypatch):
    import sqlalchemy.ext.asyncio
    from google import genai

    from investigation_engine.evaluation import cli

    source = saved_run(tmp_path)
    forbidden = Mock(side_effect=AssertionError("No database or provider clients allowed"))
    monkeypatch.setattr(genai, "Client", forbidden)
    monkeypatch.setattr(sqlalchemy.ext.asyncio, "create_async_engine", forbidden)
    monkeypatch.setattr(cli, "selected_cases", forbidden)
    monkeypatch.setattr(sys, "argv", ["evaluate.py", "rescore", str(source), "--output", str(tmp_path / "out")])
    cli.main()
    forbidden.assert_not_called()
    with pytest.raises(ValueError, match="fresh output"):
        rescore_saved_run(source, source)
