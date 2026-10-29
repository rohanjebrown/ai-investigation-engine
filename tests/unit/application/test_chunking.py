from dataclasses import replace
from uuid import uuid7

import pytest

from investigation_engine.application.chunking import Chunk, ChunkingConfig, ChunkingStrategy, chunk_document
from investigation_engine.domain.content import Content
from investigation_engine.domain.structural_span import StructuralSpan, StructureKind


def sentence(start, end):
    return StructuralSpan(kind=StructureKind.SENTENCE, start=start, end=end)


@pytest.mark.parametrize(
    "strategy,budget,overlap,expected",
    [
        (ChunkingStrategy.WHOLE_PASSAGE, 20, 0, [(0, 10)]),
        (ChunkingStrategy.WHOLE_PASSAGE, 4, 0, [(0, 4), (4, 8), (8, 10)]),
        (ChunkingStrategy.FIXED_TOKENS, 4, 0, [(0, 4), (4, 8), (8, 10)]),
        (ChunkingStrategy.FIXED_TOKENS, 4, 2, [(0, 4), (2, 6), (4, 8), (6, 10)]),
    ],
)
def test_exact_boundaries_and_overlap(registration, tokenizer, strategy, budget, overlap, expected):
    _, version = registration(body="abcdefghij")
    config = ChunkingConfig(strategy=strategy, tokenizer=tokenizer.identity, max_tokens=budget, overlap=overlap)
    plan = chunk_document(version, config=config, tokenizer=tokenizer)
    assert [(c.start, c.end) for c in plan.chunks] == expected
    assert [c.text for c in plan.chunks] == [version.content.text[a:b] for a, b in expected]
    assert chunk_document(version, config=config, tokenizer=tokenizer) == plan


def test_sentence_packing_keeps_separators_and_splits_oversized_sentences(registration, tokenizer):
    _, version = registration(body="ab cd ef", structure=(sentence(0, 2), sentence(3, 5), sentence(6, 8)))
    config = ChunkingConfig(strategy=ChunkingStrategy.SENTENCE_PACKING, tokenizer=tokenizer.identity, max_tokens=5)
    assert [c.text for c in chunk_document(version, config=config, tokenizer=tokenizer).chunks] == ["ab ", "cd ef"]
    oversized = replace(version, content=Content("abcdefgh"), structure=(sentence(0, 8),))
    assert [c.text for c in chunk_document(oversized, config=config, tokenizer=tokenizer).chunks] == ["abcde", "fgh"]


@pytest.mark.parametrize("structure", [(), (sentence(0, 2),), (sentence(3, 5),)])
def test_absent_or_incomplete_sentence_structure_uses_fixed_zero_overlap(registration, tokenizer, structure):
    _, version = registration(body="ab cd ef", structure=structure)
    config = ChunkingConfig(strategy=ChunkingStrategy.SENTENCE_PACKING, tokenizer=tokenizer.identity, max_tokens=3)
    assert [c.text for c in chunk_document(version, config=config, tokenizer=tokenizer).chunks] == ["ab ", "cd ", "ef"]


@pytest.mark.parametrize("structure", [(sentence(0, 4), sentence(3, 5)), (sentence(3, 5), sentence(0, 2))])
def test_overlapping_or_unordered_sentence_spans_are_rejected(registration, tokenizer, structure):
    _, version = registration(body="ab cd", structure=structure)
    config = ChunkingConfig(strategy=ChunkingStrategy.SENTENCE_PACKING, tokenizer=tokenizer.identity, max_tokens=3)
    with pytest.raises(ValueError, match="ordered, nonoverlapping"):
        chunk_document(version, config=config, tokenizer=tokenizer)


@pytest.mark.parametrize("strategy", list(ChunkingStrategy))
def test_empty_content_yields_no_chunks_and_whitespace_is_preserved(registration, tokenizer, strategy):
    config = ChunkingConfig(strategy=strategy, tokenizer=tokenizer.identity, max_tokens=2)
    _, empty = registration(body="")
    assert chunk_document(empty, config=config, tokenizer=tokenizer).chunks == ()
    _, whitespace = registration(body=" \t \n")
    chunks = chunk_document(whitespace, config=config, tokenizer=tokenizer).chunks
    assert "".join(c.text for c in chunks) == whitespace.content.text


def test_unicode_ranges_are_character_offsets_not_utf8_byte_offsets(registration, tokenizer):
    _, version = registration(body="A😀e\u0301Z")
    config = ChunkingConfig(strategy=ChunkingStrategy.FIXED_TOKENS, tokenizer=tokenizer.identity, max_tokens=2)
    chunks = chunk_document(version, config=config, tokenizer=tokenizer).chunks
    assert [(c.start, c.end, c.text) for c in chunks] == [(0, 2, "A😀"), (2, 4, "e\u0301"), (4, 5, "Z")]
    assert all(c.identity == (version.content.sha256, c.start, c.end) for c in chunks)


def test_same_content_with_different_sentence_layouts_has_version_specific_plans(registration, tokenizer):
    _, first = registration(body="abcdefgh", structure=(sentence(0, 4), sentence(4, 8)))
    second = replace(first, id=uuid7(), structure=(sentence(0, 3), sentence(3, 6), sentence(6, 8)))
    config = ChunkingConfig(strategy=ChunkingStrategy.SENTENCE_PACKING, tokenizer=tokenizer.identity, max_tokens=5)
    first_plan, second_plan = (chunk_document(v, config=config, tokenizer=tokenizer) for v in (first, second))
    assert first.content is second.content
    assert [(c.start, c.end) for c in first_plan.chunks] == [(0, 4), (4, 8)]
    assert [(c.start, c.end) for c in second_plan.chunks] == [(0, 3), (3, 8)]
    assert first_plan.document_version_id != second_plan.document_version_id


@pytest.mark.parametrize("start,end", [(-1, 1), (0, 0), (0, 4), (True, 2)])
def test_chunk_cannot_reference_an_invalid_content_range(start, end):
    with pytest.raises(ValueError, match="nonempty range within Content"):
        Chunk(content=Content("abc"), start=start, end=end)


@pytest.mark.parametrize(
    "budget,overlap,strategy",
    [
        (0, 0, ChunkingStrategy.FIXED_TOKENS),
        (4, 4, ChunkingStrategy.FIXED_TOKENS),
        (4, 1, ChunkingStrategy.SENTENCE_PACKING),
    ],
)
def test_invalid_chunking_configurations_are_rejected(tokenizer, budget, overlap, strategy):
    with pytest.raises(ValueError):
        ChunkingConfig(strategy=strategy, tokenizer=tokenizer.identity, max_tokens=budget, overlap=overlap)


def test_actual_tokenizer_must_match_the_recorded_configuration(registration, tokenizer):
    _, version = registration()
    config = ChunkingConfig(
        strategy=ChunkingStrategy.WHOLE_PASSAGE, tokenizer=replace(tokenizer.identity, revision="other"), max_tokens=5
    )
    with pytest.raises(ValueError, match="identities differ"):
        chunk_document(version, config=config, tokenizer=tokenizer)


def test_indivisible_multi_token_character_is_rejected_instead_of_dropping_text(registration, tokenizer):
    class ByteLikeTokenizer:
        identity = tokenizer.identity

        def offsets(self, text):
            return tuple(pair for i, char in enumerate(text) for pair in [(i, i + 1)] * (2 if char == "😀" else 1))

    _, version = registration(body="😀x")
    config = ChunkingConfig(strategy=ChunkingStrategy.FIXED_TOKENS, tokenizer=tokenizer.identity, max_tokens=1)
    with pytest.raises(ValueError, match="indivisible character"):
        chunk_document(version, config=config, tokenizer=ByteLikeTokenizer())


@pytest.mark.parametrize("offsets", [((0, 9),), ((2, 3), (0, 1)), ((0, 0),)])
def test_invalid_tokenizer_offsets_are_rejected_before_constructing_chunks(registration, tokenizer, offsets):
    class BrokenTokenizer:
        identity = tokenizer.identity

        def offsets(self, text):
            return offsets

    _, version = registration(body="abc")
    config = ChunkingConfig(strategy=ChunkingStrategy.FIXED_TOKENS, tokenizer=tokenizer.identity, max_tokens=2)
    with pytest.raises(ValueError, match="ordered Python-character offsets"):
        chunk_document(version, config=config, tokenizer=BrokenTokenizer())
