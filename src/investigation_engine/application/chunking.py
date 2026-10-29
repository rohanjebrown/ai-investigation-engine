"""Pure, deterministic chunk generation; no persistence or embedding calls."""

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from investigation_engine.application.chunking_ranges import CharacterRanges, _fixed_ranges, _sentence_ranges
from investigation_engine.application.ports.text_tokenizer import (
    TextTokenizer,
    TokenizerIdentity,
)
from investigation_engine.domain.content import Content
from investigation_engine.domain.document_version import DocumentVersion


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
        self._validate_token_limits()
        if self.strategy != ChunkingStrategy.FIXED_TOKENS and self.overlap:
            raise ValueError("Only fixed-token windows support overlap")

    def _validate_token_limits(self) -> None:
        if type(self.max_tokens) is not int or self.max_tokens < 1:  # noqa: WPS516 - reject bool budgets
            raise ValueError("Content token budget must be a positive integer")
        if type(self.overlap) is not int:  # noqa: WPS516 - reject bool overlap
            raise ValueError("Overlap must be an integer below the token budget")
        if not 0 <= self.overlap < self.max_tokens:
            raise ValueError("Overlap must be an integer below the token budget")


@dataclass(frozen=True, slots=True, kw_only=True)
class Chunk:
    """An exact range, without a new ID, title, provenance, or copied text."""

    content: Content = field(repr=False)  # noqa: WPS110 - this is the domain content field
    start: int
    end: int

    def __post_init__(self) -> None:
        # Reject bool values too; bool is a subclass of int.
        if type(self.start) is not int or type(self.end) is not int:  # noqa: WPS516
            raise ValueError("Chunk requires a nonempty range within Content")
        # A chained comparison states the canonical half-open character range.
        if not 0 <= self.start < self.end <= len(self.content.text):  # noqa: WPS228
            raise ValueError("Chunk requires a nonempty range within Content")

    @property
    def content_sha256(self) -> str:
        return self.content.sha256

    @property
    def text(self) -> str:
        return self.content.text[self.start : self.end]

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
    if text == "":
        ranges = ()
    elif config.strategy == ChunkingStrategy.SENTENCE_PACKING:
        ranges = _sentence_ranges(version, config.max_tokens, tokenizer)
    else:
        ranges = _fixed_ranges(text, config.max_tokens, config.overlap, tokenizer)

    chunks = _chunks_from_ranges(version.content, ranges)
    return ChunkPlan(
        document_version_id=version.id,
        config=config,
        chunks=chunks,
    )


def _chunks_from_ranges(source_content: Content, ranges: CharacterRanges) -> tuple[Chunk, ...]:
    chunks = []
    for start, end in ranges:
        chunks.append(Chunk(content=source_content, start=start, end=end))
    return tuple(chunks)
