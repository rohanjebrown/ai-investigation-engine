"""Offline, result-bound factual reviews and rescoring of existing saved runs."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from shutil import copyfile

from investigation_engine.evaluation.answer_focus import EXTRACTOR_VERSION, assess_answer, assessment_summary
from investigation_engine.evaluation.grounding import review_fingerprint
from investigation_engine.evaluation.metrics import ANSWER_SCORER, answer_scores
from investigation_engine.evaluation.reporting import append_jsonl, iter_jsonl, write_json
from investigation_engine.evaluation.retrieval import fingerprint

JUDGMENTS = ("correct", "incorrect", "partially_correct", "unverifiable")
_MUTABLE = {"judgment", "reviewer", "explanation", "result_fingerprint"}


def result_identity(run_id, row):
    return {"run_id": run_id, **{key: row[key] for key in ("question_id", "mode", "repetition", "warmup")}}


def packet_fingerprint(packet):
    return fingerprint({key: value for key, value in packet.items() if key not in _MUTABLE})


def review_packet(run_id, row, reference, assessment, *, evidence=None, evidence_scope=None):
    context = {
        "id": row["question_id"],
        "question": reference["question"],
        "status": row["status"],
        "answer": row.get("answer"),
        "citations": row.get("citation_ids", []),
        "retrieved_evidence": evidence if evidence is not None else row.get("selected_evidence", []),
        "citation_evidence": [],
    }
    context["citation_evidence"] = [
        item
        for item in context["retrieved_evidence"]
        if item.get("citation_id", item.get("evidence_id")) in context["citations"]
    ]
    packet = {
        "review_schema_version": 1,
        "result_id": fingerprint(result_identity(run_id, row)),
        **result_identity(run_id, row),
        "question": reference["question"],
        "reference_answer": reference.get("reference_answer"),
        "generated_answer": row.get("answer"),
        "answer_status": row["status"],
        "extracted_answer": assessment["extraction"]["answer"],
        "extraction": assessment["extraction"],
        "official_whole_response_metrics": assessment["official_whole_response"],
        "answer_focused_metrics": assessment["answer_focused"],
        "citations": context["citations"],
        "supporting_evidence": context["retrieved_evidence"],
        "citation_evidence": context["citation_evidence"],
        "evidence_scope": evidence_scope or "returned evidence; final-context membership unverified",
        "evidence_text_available": bool(context["retrieved_evidence"])
        and all("text" in item for item in context["retrieved_evidence"]),
        "gold_supporting_fact_annotations": reference.get("gold", []),
        "answer_evidence_fingerprint": review_fingerprint(context),
        "judgment": None,
        "reviewer": None,
        "explanation": None,
    }
    packet["result_fingerprint"] = packet_fingerprint(packet)
    return packet


def import_reviews(paths, packets):
    """Allow explicit edits to judgment/reviewer/explanation only; no last-wins merge."""
    expected = {packet["result_id"]: packet for packet in packets}
    imported = {}
    for path in paths:
        for review in iter_jsonl(path):
            key = review.get("result_id")
            if key not in expected or key in imported:
                raise ValueError("Unknown or duplicate/conflicting factual review result ID")
            if (
                review.get("result_fingerprint") != expected[key]["result_fingerprint"]
                or packet_fingerprint(review) != expected[key]["result_fingerprint"]
            ):
                raise ValueError("Factual review does not match the saved answer, reference, extraction and evidence")
            judgment = review.get("judgment")
            if judgment is not None and judgment not in JUDGMENTS:
                raise ValueError("Unknown factual judgment; use correct, incorrect, partially_correct or unverifiable")
            if judgment is not None and (not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip()):
                raise ValueError("A completed factual review requires an attributed reviewer")
            if review.get("explanation") is not None and not isinstance(review["explanation"], str):
                raise ValueError("Review explanation must be text or null")
            imported[key] = review
    return imported


def _key(row):
    return tuple(row[key] for key in ("question_id", "mode", "repetition", "warmup"))


def _review_evidence(row, diagnostic):
    inv = row.get("investigation") or {}
    if inv.get("final_generation_evidence") is not None:
        evidence, scope = deepcopy(inv["final_generation_evidence"]), "observed final-decision model context"
    elif inv.get("accumulated_evidence") is not None:
        evidence, scope = (
            deepcopy(inv["accumulated_evidence"]),
            "accumulated/latest observed evidence; final context unavailable",
        )
    else:
        evidence, scope = (
            deepcopy(row.get("selected_evidence", [])),
            "returned evidence; final-context membership unverified",
        )
    if diagnostic is not None:
        if (
            diagnostic.get("generated_answer") != row.get("answer")
            or diagnostic.get("status", row["status"]) != row["status"]
        ):
            raise ValueError("Saved grounding diagnostics do not match this answer")
        # Enrich only the identical canonical passage. Do not add evidence that
        # was not present in the selected scope, or join by a citation ID alone.
        for item in evidence:
            matches = [
                other
                for other in diagnostic.get("evidence", [])
                if all(
                    item.get(key) is not None and item.get(key) == other.get(key)
                    for key in ("content_sha256", "start", "end")
                )
            ]
            if matches and "text" in matches[0]:
                item["text"] = matches[0]["text"]
                if "source_spans" in matches[0]:
                    item["source_spans"] = matches[0]["source_spans"]
    return evidence, scope


def rescore_saved_run(source, output, *, reviews=()):
    """Local files only. Original artifacts and historical metrics are never rewritten."""
    source, output = Path(source).resolve(), Path(output).resolve()
    if output == source or output.exists():
        raise ValueError("Choose a fresh output directory; source artifacts are immutable")
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    reference_rows = list(iter_jsonl(source / "questions.jsonl"))
    references = {row["id"]: row for row in reference_rows}
    if len(references) != len(reference_rows):
        raise ValueError("Duplicate saved question identity")
    rows = list(iter_jsonl(source / "answer_results.jsonl"))
    if not rows or len({_key(row) for row in rows}) != len(rows):
        raise ValueError("Saved answer rows must be nonempty and have distinct attempt identities")
    run_id = manifest.get("source_run_id", manifest["run_id"])
    diagnostic_path = source / "grounding_review.jsonl"
    diagnostics = {}
    if diagnostic_path.exists():
        for row in iter_jsonl(diagnostic_path):
            key = _key(row)
            if key in diagnostics:
                raise ValueError("Duplicate grounding diagnostic identity")
            diagnostics[key] = row
    packets = []
    for row in rows:
        reference = references[row["question_id"]]
        if "expected_answer" in row and row["expected_answer"] != reference.get("reference_answer"):
            raise ValueError("Saved expected answer differs from question reference")
        original = answer_scores(row.get("answer"), reference.get("reference_answer"))
        if row["answer_metrics"] != original:
            raise ValueError("Original official answer metrics differ from recomputation; no scores were overwritten")
        assessment = assess_answer(
            reference["question"], row.get("answer"), reference.get("reference_answer"), status=row["status"]
        )
        row["answer_assessment"] = assessment
        evidence, scope = _review_evidence(row, diagnostics.get(_key(row)))
        packets.append(review_packet(run_id, row, reference, assessment, evidence=evidence, evidence_scope=scope))
    imported = import_reviews(reviews, packets)
    for row, packet in zip(rows, packets, strict=True):
        review = imported.get(packet["result_id"])
        if review is not None:
            for key in ("judgment", "reviewer", "explanation"):
                packet[key] = review.get(key)
            row["answer_assessment"].update(
                manual_factual_judgment=review["judgment"],
                reviewer=review.get("reviewer"),
                review_explanation=review.get("explanation"),
                factual_correctness_basis="explicit imported reviewer judgment" if review["judgment"] else "unreviewed",
            )
    source_summary = source / "summary.json"
    summary = json.loads(source_summary.read_text(encoding="utf-8")) if source_summary.exists() else {"modes": {}}
    summary["answer_evaluation"] = {}
    measured = [row for row in rows if not row["warmup"]]
    for mode in sorted({row["mode"] for row in measured}):
        selected = [row for row in measured if row["mode"] == mode]
        value = assessment_summary([row["answer_assessment"] for row in selected])
        value["independent_questions"] = len({row["question_id"] for row in selected})
        summary["answer_evaluation"][mode] = value
        summary["modes"].setdefault(mode, {})["answer_assessment"] = value
    files = ["manifest.json", "questions.jsonl", "answer_results.jsonl"]
    files += [name for name in ("summary.json", "grounding_review.jsonl") if (source / name).exists()]
    receipt = {
        "schema_version": "offline-answer-rescore-v1",
        "run_id": run_id + "-answer-rescore",
        "source_run_id": run_id,
        "source_directory": str(source),
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "status": "finalizing",
        "extractor_version": EXTRACTOR_VERSION,
        "official_scorer": ANSWER_SCORER,
        "source_artifacts_sha256": {name: sha256((source / name).read_bytes()).hexdigest() for name in files},
        "review_imports": [
            {"path": str(Path(path).resolve()), "sha256": sha256(Path(path).read_bytes()).hexdigest()}
            for path in reviews
        ],
        "implementation_sha256": {
            path.name: sha256(path.read_bytes()).hexdigest()
            for path in (
                Path(__file__),
                Path(__file__).with_name("answer_focus.py"),
                Path(__file__).with_name("metrics.py"),
                Path(__file__).with_name("grounding.py"),
                Path(__file__).with_name("reporting.py"),
            )
        },
        "original_official_metrics_unchanged": True,
        "provider_requests_during_rescore": 0,
        "database_queries_during_rescore": 0,
        "question_ids": list(references),
        "measured_attempts": len(measured),
    }
    output.mkdir(parents=True, exist_ok=False)
    if diagnostic_path.exists():
        copyfile(diagnostic_path, output / "grounding_review.jsonl")
    for row in rows:
        append_jsonl(output / "answer_results.jsonl", row)
    for reference in references.values():
        append_jsonl(output / "questions.jsonl", reference)
    for packet in packets:
        append_jsonl(output / "review_packets.jsonl", packet)
    write_json(output / "summary.json", summary)
    (output / "report.md").write_text(rescore_markdown(rows, packets, summary, receipt), encoding="utf-8")
    write_json(output / "manifest.json", receipt)
    # Prove the offline pass did not mutate its inputs, including on Windows.
    if any(
        sha256((source / name).read_bytes()).hexdigest() != digest
        for name, digest in receipt["source_artifacts_sha256"].items()
    ):
        raise ValueError("Source artifacts changed during rescoring")
    receipt["status"] = "complete"
    write_json(output / "manifest.json", receipt)
    return receipt


def rescore_markdown(rows, packets, summary, receipt):
    from investigation_engine.evaluation.reporting import answer_assessment_tables

    def cell(value):
        return str(value if value is not None else "unavailable").replace("|", "\\|").replace("\n", " ")

    lines = [
        "# Offline answer evaluation",
        "",
        f"Source run: {receipt['source_run_id']}",
        "",
        "Original whole-response EM/F1 are verified unchanged. No provider calls or database queries were made.",
    ]
    lines += answer_assessment_tables(summary)
    lines += [
        "",
        "## Per-attempt answer comparison",
        "",
        "| Question / mode / repetition | Reference | Generated | Extracted | Extraction status | Official EM / F1 | Focused EM / F1 | Factual judgment |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row, packet in zip(rows, packets, strict=True):
        a = row["answer_assessment"]
        official, focused = a["official_whole_response"], a["answer_focused"]
        lines.append(
            "| "
            + " | ".join(
                map(
                    cell,
                    (
                        f"{row['question_id']} / {row['mode']} / {row['repetition']}"
                        + (" (warmup)" if row["warmup"] else ""),
                        packet["reference_answer"],
                        row.get("answer"),
                        a["extraction"]["answer"],
                        a["extraction"]["status"],
                        f"{official['exact_match']} / {official['f1']}",
                        f"{focused['exact_match']} / {focused['f1']}",
                        a["manual_factual_judgment"] or "unreviewed",
                    ),
                )
            )
            + " |"
        )
        lines += [
            "",
            f"Question: {cell(packet['question'])}",
            f"Extraction: {cell(a['extraction']['reason'])}; method={cell(a['extraction']['method'])}. Citations: {cell(packet['citations'])}. Review evidence: {cell(packet['evidence_scope'])}; full text available={packet['evidence_text_available']}.",
            f"Reviewer: {cell(a['reviewer'])}. Review explanation: {cell(a['review_explanation'])}.",
            "",
        ]
    lines += [
        "",
        "## Review workflow",
        "",
        "Copy review_packets.jsonl to your own local review file. Inspect the original question, reference, generated/extracted answers, citations and supporting excerpts. Edit only judgment, reviewer and optional explanation. Judgment is correct, incorrect, partially_correct or unverifiable; leave null while pending. Import with `scripts/evaluate.py rescore SOURCE --reviews YOUR_REVIEWS.jsonl --output FRESH_OUTPUT`. Reviewer attribution is a local attestation, not authenticated identity. Duplicate, unknown, stale or edited-context reviews are rejected. Extraction failure never implies factual incorrectness. Citation validity and annotated-fact coverage do not establish correctness.",
    ]
    return "\n".join(lines) + "\n"
