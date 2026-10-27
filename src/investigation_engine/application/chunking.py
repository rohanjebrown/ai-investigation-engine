"""Pure, deterministic chunk generation; no persistence or embedding calls."""

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from investigation_engine.application.ports.text_tokenizer import (
    TextTokenizer,
    TokenizerIdentity,
)
from investigation_engine.domain.content import Content
from investigation_engine.domain.document_version import DocumentVersion
from investigation_engine.domain.structural_span import StructureKind


class ChunkingStrategy(StrEnum):
    WHOLE_PASSAGE = "whole_passage"
    FIXED_TOKENS = "fixed_tokens"
    SENTENCE_PACKING = "sentence_packing"


@dataclass(frozen=True, slots=True, kw_only=True)
class ChunkingConfig:
    strategy: ChunkingStrategy
    tokenizer: TokenizerIdentity
    max_tokens: int
    overlap: int = 0
    # Covers the fixed boundary, oversized-unit, and sentence-fallback rules.
    # Changing any of those rules requires a new implementation version.
    algorithm_version: str = field(default="1", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.strategy, ChunkingStrategy):
            raise ValueError("A supported ChunkingStrategy is required")
        if not isinstance(self.tokenizer, TokenizerIdentity):
            raise ValueError("An immutable TokenizerIdentity is required")
        if type(self.max_tokens) is not int or self.max_tokens < 1:
            raise ValueError("Content token budget must be a positive integer")
        if type(self.overlap) is not int or not 0 <= self.overlap < self.max_tokens:
            raise ValueError("Overlap must be an integer below the token budget")
        if self.strategy != ChunkingStrategy.FIXED_TOKENS and self.overlap:
            raise ValueError("Only fixed-token windows support overlap")


@dataclass(frozen=True, slots=True, kw_only=True)
class Chunk:
    """An exact range, without a new ID, title, provenance, or copied text."""

    content: Content = field(repr=False)
    start: int
    end: int

    def __post_init__(self) -> None:
        if (
            type(self.start) is not int
            or type(self.end) is not int
            or not 0 <= self.start < self.end <= len(self.content.text)
        ):
            raise ValueError("Chunk requires a nonempty range within Content")

    @property
    def content_sha256(self) -> str:
        return self.content.sha256

    @property
    def text(self) -> str:
        return self.content.text[self.start:self.end]

    @property
    def identity(self) -> tuple[str, int, int]:
        return self.content.sha256, self.start, self.end


@dataclass(frozen=True, slots=True, kw_only=True)
class ChunkPlan:
    """One version/configuration's ordered memberships, not stored entities."""

    document_version_id: UUID
    config: ChunkingConfig
    chunks: tuple[Chunk, ...]


def chunk_document(
    version: DocumentVersion,
    *,
    config: ChunkingConfig,
    tokenizer: TextTokenizer,
) -> ChunkPlan:
    """Generate ranges using this version's structure and this tokenizer.

    Budgets count content tokens only. A future embedding adapter must check
    its complete effective input, including titles and task instructions.
    Empty Content yields no chunks, without establishing indexing readiness.
    Source locators stay on the version; partial coverage is a range/span
    intersection, not a claim that the whole original sentence was retrieved.
    """
    if config.tokenizer != tokenizer.identity:
        raise ValueError("Configuration and actual tokenizer identities differ")

    text = version.content.text
    if not text:
        ranges = ()
    elif config.strategy == ChunkingStrategy.SENTENCE_PACKING:
        ranges = _sentence_ranges(version, config.max_tokens, tokenizer)
    else:
        ranges = _fixed_ranges(text, config.max_tokens, config.overlap, tokenizer)

    return ChunkPlan(
        document_version_id=version.id,
        config=config,
        chunks=tuple(
            Chunk(content=version.content, start=start, end=end)
            for start, end in ranges
        ),
    )


def _offsets(text: str, tokenizer: TextTokenizer) -> tuple[tuple[int, int], ...]:
    offsets = tokenizer.offsets(text)
    previous_start = previous_end = 0
    for start, end in offsets:
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(text)
            or start < previous_start
            or end < previous_end
        ):
            raise ValueError("Tokenizer must return ordered Python-character offsets")
        previous_start, previous_end = start, end
    return offsets


def _fixed_ranges(
    text: str, budget: int, overlap: int, tokenizer: TextTokenizer,
) -> tuple[tuple[int, int], ...]:
    ranges = []
    start = 0
    while start < len(text):
        offsets = _offsets(text[start:], tokenizer)
        if len(offsets) <= budget:
            ranges.append((start, len(text)))
            break

        # A token start beyond the budget is an original-character boundary.
        # Duplicate starts are possible when several tokens encode one emoji.
        ends = sorted({start + a for a, _ in offsets[:budget + 1] if a > 0})
        while ends:
            end = ends.pop()
            chunk_offsets = _offsets(text[start:end], tokenizer)
            if len(chunk_offsets) <= budget:
                break
        else:
            raise ValueError(
                f"Token budget {budget} cannot fit an indivisible character "
                f"or token boundary at input offset {start}"
            )

        ranges.append((start, end))
        if overlap:
            overlap_index = max(0, len(chunk_offsets) - overlap)
            # Keep token boundaries in original-character units. A byte-token
            # overlap can contain more than the target count at one character.
            following = start + chunk_offsets[overlap_index][0]
            start = following if following > start else end
        else:
            start = end
    return tuple(ranges)


def _sentence_ranges(
    version: DocumentVersion, budget: int, tokenizer: TextTokenizer,
) -> tuple[tuple[int, int], ...]:
    text = version.content.text
    sentences = tuple(
        s for s in version.structure if s.kind == StructureKind.SENTENCE
    )
    previous_end = 0
    missing_text = not sentences
    for sentence in sentences:
        if (
            not 0 <= sentence.start < sentence.end <= len(text)
            or sentence.start < previous_end
        ):
            raise ValueError("Sentence spans must be ordered, nonoverlapping Content ranges")
        if text[previous_end:sentence.start].strip():
            missing_text = True
        previous_end = sentence.end
    if text[previous_end:].strip():
        missing_text = True
    if missing_text:
        return _fixed_ranges(text, budget, 0, tokenizer)

    ranges = []
    start = end = 0
    for index in range(len(sentences)):
        # Assign the separator to the preceding sentence's packing unit.
        # This changes neither authoritative spans nor original source indices.
        unit_end = (
            sentences[index + 1].start
            if index + 1 < len(sentences)
            else len(text)
        )
        if len(_offsets(text[start:unit_end], tokenizer)) <= budget:
            end = unit_end
            continue
        if end > start:
            ranges.append((start, end))
            start = end
        if len(_offsets(text[start:unit_end], tokenizer)) > budget:
            ranges.extend(
                (start + a, start + b)
                for a, b in _fixed_ranges(text[start:unit_end], budget, 0, tokenizer)
            )
            start = end = unit_end
        else:
            end = unit_end
    if end > start:
        ranges.append((start, end))
    return tuple(ranges)
