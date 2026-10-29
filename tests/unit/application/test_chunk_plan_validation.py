from dataclasses import replace

import pytest

from investigation_engine.application.chunk_plan_validation import validate_chunk_plan
from investigation_engine.application.chunking import Chunk, ChunkingConfig, ChunkingStrategy, ChunkPlan, chunk_document
from investigation_engine.domain.content import Content
from investigation_engine.domain.structural_span import StructuralSpan, StructureKind


@pytest.mark.parametrize("strategy", list(ChunkingStrategy))
def test_generated_unicode_plans_preserve_complete_character_coverage(registration, tokenizer, strategy):
    _, version = registration(
        body="A😀 B.",
        structure=(
            StructuralSpan(kind=StructureKind.SENTENCE, start=0, end=2),
            StructuralSpan(kind=StructureKind.SENTENCE, start=3, end=5),
        ),
    )
    config = ChunkingConfig(
        strategy=strategy,
        tokenizer=tokenizer.identity,
        max_tokens=3,
        overlap=1 if strategy == ChunkingStrategy.FIXED_TOKENS else 0,
    )
    plan = chunk_document(version, config=config, tokenizer=tokenizer)
    validate_chunk_plan(plan, version.content)
    assert all(chunk.text == version.content.text[chunk.start : chunk.end] for chunk in plan.chunks)


@pytest.mark.parametrize("text", ["", " \t\n ", "a  b"])
def test_empty_and_whitespace_memberships_keep_the_complete_generated_plan(registration, tokenizer, text):
    _, version = registration(body=text)
    config = ChunkingConfig(strategy=ChunkingStrategy.FIXED_TOKENS, tokenizer=tokenizer.identity, max_tokens=1)
    plan = chunk_document(version, config=config, tokenizer=tokenizer)
    validate_chunk_plan(plan, version.content)
    assert bool(plan.chunks) == bool(text)
    assert "".join(chunk.text for chunk in plan.chunks) == text
    if text:
        assert any(not chunk.text.strip() for chunk in plan.chunks)


def test_content_equality_includes_exact_text_not_only_its_digest(registration, tokenizer):
    _, version = registration(body="original")
    other = Content("different")
    object.__setattr__(other, "sha256", version.content.sha256)
    config = ChunkingConfig(strategy=ChunkingStrategy.WHOLE_PASSAGE, tokenizer=tokenizer.identity, max_tokens=20)
    plan = ChunkPlan(
        document_version_id=version.id, config=config, chunks=(Chunk(content=other, start=0, end=len(other.text)),)
    )
    with pytest.raises(ValueError, match="exact Content"):
        validate_chunk_plan(plan, version.content)


@pytest.mark.parametrize("start,end", [(-1, 1), (0, 0), (0, 10), (True, 2)])
def test_invalid_ranges_cannot_cross_the_persistence_boundary(registration, tokenizer, start, end):
    _, version = registration(body="abcde")
    config = ChunkingConfig(strategy=ChunkingStrategy.FIXED_TOKENS, tokenizer=tokenizer.identity, max_tokens=5)
    chunk = Chunk(content=version.content, start=0, end=5)
    # Simulate corruption after construction to exercise the boundary validator.
    object.__setattr__(chunk, "start", start)
    object.__setattr__(chunk, "end", end)
    plan = ChunkPlan(document_version_id=version.id, config=config, chunks=(chunk,))
    with pytest.raises(ValueError, match="ranges must lie within"):
        validate_chunk_plan(plan, version.content)


@pytest.mark.parametrize("ranges", [((0, 3), (0, 3)), ((0, 4), (2, 5), (1, 5))])
def test_duplicate_or_reordered_memberships_are_rejected(registration, tokenizer, ranges):
    _, version = registration(body="abcde")
    config = ChunkingConfig(
        strategy=ChunkingStrategy.FIXED_TOKENS, tokenizer=tokenizer.identity, max_tokens=4, overlap=1
    )
    plan = ChunkPlan(
        document_version_id=version.id,
        config=config,
        chunks=tuple(Chunk(content=version.content, start=start, end=end) for start, end in ranges),
    )
    with pytest.raises(ValueError, match="ordered"):
        validate_chunk_plan(plan, version.content)


@pytest.mark.parametrize("strategy", list(ChunkingStrategy))
def test_unconfigured_overlap_is_rejected(registration, tokenizer, strategy):
    _, version = registration(body="abcde")
    config = ChunkingConfig(strategy=strategy, tokenizer=tokenizer.identity, max_tokens=3)
    plan = ChunkPlan(
        document_version_id=version.id,
        config=config,
        chunks=(
            Chunk(content=version.content, start=0, end=3),
            Chunk(content=version.content, start=2, end=5),
        ),
    )
    with pytest.raises(ValueError, match="unconfigured overlap"):
        validate_chunk_plan(plan, version.content)


@pytest.mark.parametrize("ranges", [(), ((0, 2),), ((0, 2), (3, 5))])
def test_incomplete_coverage_including_a_whitespace_gap_is_rejected(registration, tokenizer, ranges):
    _, version = registration(body="ab cd")
    config = ChunkingConfig(strategy=ChunkingStrategy.FIXED_TOKENS, tokenizer=tokenizer.identity, max_tokens=3)
    plan = ChunkPlan(
        document_version_id=version.id,
        config=config,
        chunks=tuple(Chunk(content=version.content, start=start, end=end) for start, end in ranges),
    )
    with pytest.raises(ValueError, match="complete Content|gap"):
        validate_chunk_plan(plan, version.content)


def test_mutable_membership_collections_are_rejected(registration, tokenizer):
    _, version = registration()
    config = ChunkingConfig(strategy=ChunkingStrategy.WHOLE_PASSAGE, tokenizer=tokenizer.identity, max_tokens=20)
    plan = chunk_document(version, config=config, tokenizer=tokenizer)
    with pytest.raises(ValueError, match="immutable tuple"):
        validate_chunk_plan(replace(plan, chunks=list(plan.chunks)), version.content)
