"""Opt-in, offline checks of the approved corpus; no gold labels are loaded."""

import hashlib
import json
from pathlib import Path
from uuid import uuid7

import pyarrow.parquet as pq
import pytest

from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy, chunk_document
from investigation_engine.domain.content import Content
from investigation_engine.domain.document_version import DocumentVersion
from investigation_engine.infrastructure.ingestion.twowiki_adapter import extract_2wiki
from investigation_engine.infrastructure.tokenization.huggingface_tokenizer import (
    VOYAGE_REFERENCE_NAME,
    VOYAGE_REFERENCE_REVISION,
    VOYAGE_REFERENCE_SHA256,
    HuggingFaceTokenizer,
)
from investigation_engine.ingestion.download_dataset import DATASET_REVISION

pytestmark = pytest.mark.dataset
CORPUS_SHA256 = "c0d8b60b9026b728fb07ad74c5252a0f188f6942e8ba5c02df4dfa369502ea8d"
LAYOUT_SHA256 = "c860f288a7ec50391a78092a0cfc2b7605ca0b7d59415cfca249ee1247f0a18c"


@pytest.fixture(scope="module")
def corpus_audit(pytestconfig):
    path = Path(pytestconfig.getoption("--dataset-path"))
    if not path.is_file():
        pytest.skip("Pinned 2Wiki dev.parquet is unavailable; tests never download it")
    with path.open("rb") as file:
        assert hashlib.file_digest(file, "sha256").hexdigest() == CORPUS_SHA256, (
            "Dataset does not match the approved pinned revision"
        )
    rows = occurrences = 0
    hashes, coordinates, layouts = set(), set(), {}
    for batch in pq.ParquetFile(path).iter_batches(batch_size=128, columns=["_id", "context"]):
        for record in batch.to_pylist():
            rows += 1
            record["context"] = json.loads(record["context"])
            for doc in extract_2wiki(record, split="dev", revision=DATASET_REVISION):
                occurrences += 1
                digest = hashlib.sha256(doc.text.encode("utf-8")).hexdigest()
                hashes.add(digest)
                locator = doc.source.record_locator
                coordinates.add((locator["split"], locator["example_id"], locator["context_position"]))
                sentences = record["context"][locator["context_position"]][1]
                assert [doc.text[s.start : s.end] for s in doc.source_spans] == sentences
                assert [s.locators[0].index for s in doc.source_spans] == list(range(len(sentences)))
                if digest == LAYOUT_SHA256:
                    layouts[(locator["example_id"], locator["context_position"])] = doc
    return rows, occurrences, len(hashes), len(coordinates), layouts


def test_pinned_development_corpus_counts_exact_content_and_sentence_offsets(corpus_audit):
    rows, occurrences, unique_texts, source_coordinates, _ = corpus_audit
    assert (rows, occurrences, unique_texts, source_coordinates) == (12576, 125760, 56680, 125760)
    assert occurrences - unique_texts == 69080


def test_known_shared_content_retains_different_sentence_chunk_plans(corpus_audit, pytestconfig):
    path = Path(pytestconfig.getoption("--tokenizer-path"))
    if not path.is_file():
        pytest.skip("Pinned reference tokenizer unavailable; supply --tokenizer-path; never downloaded")
    tokenizer = HuggingFaceTokenizer(
        path, name=VOYAGE_REFERENCE_NAME, revision=VOYAGE_REFERENCE_REVISION, expected_sha256=VOYAGE_REFERENCE_SHA256
    )
    config = ChunkingConfig(strategy=ChunkingStrategy.SENTENCE_PACKING, tokenizer=tokenizer.identity, max_tokens=128)
    layouts = corpus_audit[-1]
    examples = [
        (("c289098a0bdc11eba7f7acde48001122", 1), [(0, 499), (499, 658)]),
        (("abaa3fca08e611ebbda5ac1f6bf848b6", 4), [(0, 464), (464, 658)]),
    ]
    versions = []
    for locator, expected in examples:
        doc = layouts[locator]
        version = DocumentVersion(
            document_id=uuid7(),
            version_number=1,
            content=Content(doc.text),
            source=doc.source,
            structure=doc.structure,
            source_spans=doc.source_spans,
        )
        versions.append(version)
        plan = chunk_document(version, config=config, tokenizer=tokenizer)
        assert [(c.start, c.end) for c in plan.chunks] == expected
    assert versions[0].content == versions[1].content
    assert versions[0].source.record_locator != versions[1].source.record_locator
