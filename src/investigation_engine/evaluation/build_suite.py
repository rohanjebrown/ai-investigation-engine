"""Build the small, source-only evaluation snapshot from already downloaded data.

Run: python -m investigation_engine.evaluation.build_suite
No downloads, indexing, settings imports, or AI clients.
"""

import json
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

import pyarrow.parquet as pq

from investigation_engine.evaluation.retrieval import validate_suite
from investigation_engine.infrastructure.ingestion.twowiki_adapter import extract_2wiki
from investigation_engine.ingestion.download_dataset import DATASET_ID, DATASET_REVISION

DEV_PARQUET_SHA256 = "c0d8b60b9026b728fb07ad74c5252a0f188f6942e8ba5c02df4dfa369502ea8d"


def build(path: Path) -> tuple[dict, dict]:
    if sha256(path.read_bytes()).hexdigest() != DEV_PARQUET_SHA256:
        raise ValueError("Input does not match the inspected pinned development Parquet file")
    rows = pq.read_table(path).to_pylist()
    selected = {}
    documents = {}
    cases = []

    def unpack(row):
        return {**row, **{key: json.loads(row[key]) for key in ("context", "supporting_facts", "evidences")}}

    def include(row):
        if row["_id"] not in selected:
            selected[row["_id"]] = row
            for pos, doc in enumerate(extract_2wiki(row, split="dev", revision=DATASET_REVISION)):
                documents[(row["_id"], pos)] = {
                    "text": doc.text,
                    "title": doc.title,
                    "source": asdict(doc.source),
                    "structure": [asdict(structure_span) for structure_span in doc.structure],
                    "source_spans": [asdict(source_span) for source_span in doc.source_spans],
                    "content_sha256": sha256(doc.text.encode("utf8")).hexdigest(),
                }

    def gold(row, title, index):
        positions = [position for position, passage in enumerate(row["context"]) if passage[0] == title]
        if len(positions) != 1:
            raise ValueError("Ambiguous supporting title; do not silently choose an occurrence")
        doc = documents[(row["_id"], positions[0])]
        span = doc["source_spans"][index]
        return {
            "source": doc["source"],
            "content_sha256": doc["content_sha256"],
            "sentence_index": index,
            "start": span["start"],
            "end": span["end"],
        }

    counts = {"simple_factual": 0, "two_hop": 0, "three_plus_hop": 0}
    for raw in rows:
        row = unpack(raw)
        triples = row["evidences"]
        category = None
        if row["type"] == "compositional" and len(triples) == 2 and counts["two_hop"] < 12:
            category = "two_hop"
        elif (
            row["type"] == "bridge_comparison"
            and len(triples) == 4
            and len(row["supporting_facts"]) == 4
            and counts["three_plus_hop"] < 8
        ):
            category = "three_plus_hop"
        if category:
            include(row)
            facts = [gold(row, title, index) for title, index in row["supporting_facts"]]
            cases.append(
                {
                    "id": "2wiki:" + row["_id"],
                    "question": row["question"],
                    "category": category,
                    "expected_answer": row["answer"],
                    "required_evidence": facts,
                    "dataset_revision": DATASET_REVISION,
                    "split": "dev",
                    "original_question_id": row["_id"],
                    "annotation_triples": triples,
                    "expected_behavior": "answer",
                    "category_basis": "Two annotated compositional relations"
                    if category == "two_hop"
                    else "Bridge-comparison: two subject-to-person-to-attribute chains, then compare; not a claim of a linear four-hop path.",
                }
            )
            counts[category] += 1
        if row["type"] == "compositional" and counts["simple_factual"] < 8:
            subject, relation, answer = triples[-1]
            matches = [
                (title, index)
                for title, index in row["supporting_facts"]
                if any(
                    title == passage[0] and answer.casefold() in passage[1][index].casefold()
                    for passage in row["context"]
                )
            ]
            if len(matches) == 1:
                include(row)
                cases.append(
                    {
                        "id": "simple:" + row["_id"],
                        "question": f"What is {subject}'s {relation}?",
                        "category": "simple_factual",
                        "expected_answer": answer,
                        "required_evidence": [gold(row, *matches[0])],
                        "dataset_revision": DATASET_REVISION,
                        "split": "dev",
                        "original_question_id": row["_id"],
                        "annotation_triples": [triples[-1]],
                        "category_basis": "Derived single-relation question; annotated object occurs in exactly one supporting sentence.",
                        "expected_behavior": "answer",
                    }
                )
                counts["simple_factual"] += 1
        if counts == {"simple_factual": 8, "two_hop": 12, "three_plus_hop": 8}:
            break
    if counts != {"simple_factual": 8, "two_hop": 12, "three_plus_hop": 8}:
        raise ValueError("Insufficient appropriately annotated local examples")
    synthetic = []
    for control_index in range(6):

        def control(name, text):
            source = {
                "kind": "dataset",
                "source_id": "controlled-evaluation",
                "revision": "1",
                "record_locator": {"record": name},
                "display_name": name,
                "metadata": {},
            }
            doc = {
                "text": text,
                "title": name,
                "source": source,
                "structure": [],
                "source_spans": [{"start": 0, "end": len(text), "locators": [{"kind": "sentence", "index": 0}]}],
                "content_sha256": sha256(text.encode("utf8")).hexdigest(),
            }
            synthetic.append(doc)
            return {
                "source": source,
                "content_sha256": doc["content_sha256"],
                "sentence_index": 0,
                "start": 0,
                "end": len(text),
            }

        control(
            f"unanswerable-{control_index}",
            f"Fictional station Unlisted-{control_index} records wind speed. Its construction date is not recorded.",
        )
        cases.append(
            {
                "id": f"unanswerable:{control_index}",
                "question": f"On what date was fictional station Unlisted-{control_index} constructed?",
                "category": "unanswerable",
                "expected_answer": None,
                "required_evidence": [],
                "dataset_revision": "1",
                "split": "controlled",
                "expected_behavior": "abstain",
                "category_basis": "Controlled fictional entity; no construction date in the fixed corpus.",
            }
        )
        left = control(
            f"contradiction-{control_index}-a",
            f"The current access code of fictional vault Conflict-{control_index} is amber.",
        )
        right = control(
            f"contradiction-{control_index}-b",
            f"The current access code of fictional vault Conflict-{control_index} is violet.",
        )
        cases.append(
            {
                "id": f"contradictory:{control_index}",
                "question": f"What is the current access code of fictional vault Conflict-{control_index}?",
                "category": "contradictory",
                "expected_answer": None,
                "required_evidence": [left, right],
                "dataset_revision": "1",
                "split": "controlled",
                "expected_behavior": "report_conflict",
                "expected_conflict_claims": [
                    {"value": "amber", "required_evidence_index": 0},
                    {"value": "violet", "required_evidence_index": 1},
                ],
                "category_basis": "Two same-time assertions disagree; neither source has priority.",
            }
        )
    docs = list(documents.values()) + synthetic
    corpus_id = "2wiki-dev-controlled-v1"
    for case in cases:
        case["corpus_id"] = corpus_id
    corpus = {
        "corpus_id": corpus_id,
        "dataset": DATASET_ID,
        "dataset_revision": DATASET_REVISION,
        "split": "dev",
        "selection": "First eligible annotated cases in pinned Parquet order; all ten contexts per selected original record, plus controls.",
        "input_file_sha256": sha256(path.read_bytes()).hexdigest(),
        "original_record_ids": list(selected),
        "record_count": len(docs),
        "unique_content_count": len({document["content_sha256"] for document in docs}),
        "documents": docs,
    }
    suite = {
        "version": "retrieval-v1",
        "answer_quality_version": "answer-quality-v1",
        "corpus_id": corpus_id,
        "cases": cases,
    }
    validate_suite(suite, corpus)
    return suite, corpus


def main():
    suite, corpus = build(Path("data/raw/2wiki/dev.parquet"))
    target = Path("data/samples/evaluation")
    target.mkdir(parents=True, exist_ok=True)
    for name, value in (("suite", suite), ("corpus", corpus)):
        (target / f"{name}.json").write_text(
            json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf8", newline="\n"
        )


if __name__ == "__main__":
    main()
