import importlib.util
import json
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from investigation_engine.application.retrieval import Evidence
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "evaluate_cloud_retrieval.py"
spec = importlib.util.spec_from_file_location("cloud_retrieval_script", SCRIPT)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def evidence(number):
    return Evidence(
        document_id=UUID(int=number),
        document_version_id=UUID(int=number),
        content_sha256="a" * 64,
        start=0,
        end=10,
        text="abcdefghij",
        score=0.5,
        rank=1,
        source=SourceReference(
            kind=SourceKind.DATASET, source_id="dataset", revision="r", record_locator={"example_id": str(number)}
        ),
        source_spans=(SourceSpan(start=0, end=10, locators=(SourceLocator(kind=LocatorKind.SENTENCE, index=0),)),),
    )


def gold(hit):
    return {
        "source": asdict(hit.source),
        "content_sha256": hit.content_sha256,
        "start": 0,
        "end": 10,
        "sentence_index": 0,
    }


def test_occurrence_and_canonical_scores_distinguish_duplicate_sources():
    first, second = evidence(1), evidence(2)
    labels = [gold(first), gold(second)]
    assert evaluation.measure([first], labels, strict=True)["at_k"]["5"]["recall"] == 0.5
    assert evaluation.measure([first], labels, strict=False)["at_k"]["5"]["recall"] == 1
    assert evaluation.measure([first], [], strict=False)["mrr"] is None


def test_duplicate_hits_do_not_consume_ranks():
    hit, other = evidence(1), evidence(2)
    measured = evaluation.measure([hit, hit, other], [gold(other)], strict=True)
    assert measured["mrr"] == 0.5


def test_paired_summary_excludes_failed_cases():
    metrics = {"mrr": 1.0, "at_k": {str(k): {"recall": 1.0, "all_required": True} for k in (5, 10, 20)}}

    def row(case, mode):
        return {
            "id": case,
            "category": "simple_factual",
            "mode": mode,
            "status": "ok",
            "gold_count": 1,
            "duration_ms": 2,
            "strict": metrics,
            "canonical": metrics,
        }

    rows = [row("a", mode) for mode in ("semantic", "hybrid", "keyword")]
    rows += [row("b", "semantic"), {"id": "b", "mode": "hybrid", "status": "failed"}, row("b", "keyword")]
    paired = evaluation.summarize_paired(rows)
    assert paired["case_ids"] == ["a"]
    assert paired["modes"]["semantic"]["successful"] == 1


def test_remote_database_cannot_silently_disable_tls(monkeypatch, tmp_path):
    for name, value in {"DB_HOST": "example.com", "DB_USER": "u", "DB_PASSWORD": "secret", "DB_NAME": "db"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("DB_SSLMODE", "disable")
    with pytest.raises(ValueError, match="TLS"):
        evaluation.read_env(tmp_path / "missing.env")


def test_code_fingerprint_is_available():
    assert len(evaluation.code_fingerprint()) == 64


def test_dataset_gold_validates_extractor_offsets_and_hash(monkeypatch, tmp_path):
    path = tmp_path / "fixture.parquet"
    pq.write_table(
        pa.Table.from_pylist([{"_id": "q", "context": json.dumps([["title", ["First.", "Second."]]])}]), path
    )
    monkeypatch.setattr(evaluation, "FILE_SHA256", sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(evaluation, "validate_suite", lambda suite, corpus: None)
    monkeypatch.setattr(evaluation, "missing_gold", lambda case, corpus: [])
    source = {
        "kind": "dataset",
        "source_id": evaluation.DATASET_ID,
        "revision": evaluation.REVISION,
        "record_locator": {"split": "dev", "example_id": "q", "context_position": 0},
    }
    label = {
        "source": source,
        "content_sha256": sha256(b"First. Second.").hexdigest(),
        "sentence_index": 1,
        "start": 7,
        "end": 14,
    }
    cases = [{"required_evidence": [label]}]
    assert evaluation.verify_dataset_gold(path, {}, {}, cases)["gold_sentences_verified"] == 1
    label["start"] = 6
    with pytest.raises(ValueError, match="offsets"):
        evaluation.verify_dataset_gold(path, {}, {}, cases)
