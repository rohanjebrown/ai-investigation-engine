from copy import deepcopy

import pytest

from investigation_engine.infrastructure.ingestion.twowiki_adapter import DATASET_ID, extract_2wiki


@pytest.fixture
def record():
    return {
        "_id": "question-7",
        "context": [["First title", [" A😀 ", "Second."]], ["Other title", [" A😀 ", "Second."]]],
        "question": "GOLD QUESTION",
        "answer": "GOLD ANSWER",
        "supporting_facts": [["First title", 0]],
    }


def test_passages_preserve_text_offsets_order_and_independent_provenance(record):
    original = deepcopy(record)
    documents = extract_2wiki(record, split="dev", revision="pinned")
    assert record == original and len(documents) == 2
    assert [d.title for d in documents] == ["First title", "Other title"]
    assert documents[0].text == documents[1].text == " A😀  Second."
    for position, doc in enumerate(documents):
        assert doc.source.source_id == DATASET_ID and doc.source.revision == "pinned"
        assert doc.source.record_locator == {"split": "dev", "example_id": "question-7", "context_position": position}
        assert [(s.start, s.end) for s in doc.structure] == [(0, 4), (5, 12)]
        for index, span in enumerate(doc.source_spans):
            assert doc.text[span.start : span.end] == record["context"][position][1][index]
            assert span.locators[0].index == index
        assert all(not (s.start <= 4 < s.end) for s in doc.source_spans)
        assert "GOLD" not in doc.text and doc.title not in doc.text
    assert extract_2wiki(record, split="dev", revision="pinned") == documents


@pytest.mark.parametrize(
    "passage",
    [None, ["only title"], ["", ["text"]], ["title", []], ["title", [""]], ["title", [" \t"]], ["title", [7]]],
)
def test_malformed_passage_fails_with_record_and_position(record, passage):
    record["context"][1] = passage
    with pytest.raises(ValueError, match="question-7.*context position 1"):
        extract_2wiki(record, split="dev", revision="pinned")


@pytest.mark.parametrize("record", [{}, {"_id": "q"}, {"_id": 1, "context": []}, {"_id": "q", "context": "not parsed"}])
def test_missing_or_wrong_record_fields_are_not_repaired(record):
    with pytest.raises(ValueError, match="2Wiki record"):
        extract_2wiki(record, split="dev", revision="pinned")
