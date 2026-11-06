"""Pinned local 2Wiki selections and gold coordinates, separate from ingestion."""

import json
import random
from collections import defaultdict
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

import pyarrow.parquet as pq

from investigation_engine.evaluation.retrieval import fingerprint
from investigation_engine.infrastructure.ingestion.twowiki_adapter import extract_2wiki
from investigation_engine.ingestion.corpus_processing import FILE_SHA256, REVISION, verify_input


def catalog(path):
    verify_input(path, FILE_SHA256)
    rows = []
    for batch in pq.ParquetFile(path).iter_batches(columns=["_id", "question", "type"]):
        rows.extend(batch.to_pylist())
    if len({row["_id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate dataset question IDs")
    return rows


def select_questions(rows, size, seed):
    """Round-robin shuffled type strata; stable across input ordering."""
    if type(size) is not int or not 1 <= size <= len(rows):
        raise ValueError("Sample size must be between one and dataset size")
    groups = defaultdict(list)
    for row in sorted(rows, key=lambda item: item["_id"]):
        groups[row["type"]].append(row["_id"])
    rng = random.Random(seed)
    for group in groups.values():
        rng.shuffle(group)
    order = sorted(groups)
    rng.shuffle(order)
    result = []
    while len(result) < size:
        for kind in order:
            if groups[kind] and len(result) < size:
                result.append(groups[kind].pop())
    return result


def selection_manifest(ids, seed, *, overrides=None):
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("Selection requires distinct question IDs")
    return {
        "version": 1,
        "dataset_revision": REVISION,
        "dataset_sha256": FILE_SHA256,
        "seed": seed,
        "question_ids": ids,
        "question_overrides": overrides or {},
    }


def read_selection(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if value.get("dataset_revision") != REVISION or value.get("dataset_sha256") != FILE_SHA256:
        raise ValueError("Selection does not identify the pinned dataset")
    selection_manifest(value["question_ids"], value["seed"])
    if set(value.get("question_overrides", {})) - set(value["question_ids"]):
        raise ValueError("Question override outside selection")
    return value


def case_from_record(row, *, question=None):
    context = json.loads(row["context"]) if isinstance(row["context"], str) else row["context"]
    facts = json.loads(row["supporting_facts"]) if isinstance(row["supporting_facts"], str) else row["supporting_facts"]
    documents = extract_2wiki({"_id": row["_id"], "context": context}, split="dev", revision=REVISION)
    gold = []
    seen = set()
    for title, index in facts:
        if (title, index) in seen:
            continue
        seen.add((title, index))
        item = {"title": title, "sentence_index": index}
        positions = [i for i, passage in enumerate(context) if passage[0] == title]
        if len(positions) != 1:
            item["mapping_error"] = "missing_or_ambiguous_context_title"
        else:
            doc = documents[positions[0]]
            if type(index) is not int or not 0 <= index < len(doc.source_spans):
                item["mapping_error"] = "invalid_sentence_index"
            else:
                span = doc.source_spans[index]
                item.update(
                    source=asdict(doc.source),
                    content_sha256=sha256(doc.text.encode("utf-8")).hexdigest(),
                    start=span.start,
                    end=span.end,
                )
        gold.append(item)
    if question is not None and question.casefold() != row["question"].casefold():
        raise ValueError("Maintained question spelling must differ only in case from its dataset question")
    return {
        "id": row["_id"],
        "question": question or row["question"],
        "dataset_question": row["question"],
        "question_type": row["type"],
        "reference_answer": row["answer"],
        "gold": gold,
        "annotation_triples": json.loads(row["evidences"])
        if isinstance(row.get("evidences"), str)
        else row.get("evidences"),
        "duplicate_gold_labels": len(facts) - len(gold),
    }


def load_cases(path, selection):
    wanted = set(selection["question_ids"])
    found = {}
    for batch in pq.ParquetFile(path).iter_batches(batch_size=128):
        for row in batch.to_pylist():
            if row["_id"] in wanted:
                found[row["_id"]] = case_from_record(
                    row, question=selection.get("question_overrides", {}).get(row["_id"])
                )
    if set(found) != wanted:
        raise ValueError("Selected question IDs are absent from the pinned dataset")
    return [found[key] for key in selection["question_ids"]]


def legacy_cases(path):
    """Retain the existing derived, insufficient-evidence and conflict controls."""
    suite = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        {
            "id": case["id"],
            "question": case["question"],
            "question_type": case["category"],
            "reference_answer": case.get("expected_answer"),
            "gold": case["required_evidence"],
            "expected_behavior": case.get("expected_behavior"),
            "legacy_suite_fingerprint": fingerprint(suite),
        }
        for case in suite["cases"]
    ]
