"""Storage-independent checks on complete plans; no tokenizer or provider IO."""

from uuid import UUID

from investigation_engine.application.chunking import Chunk, ChunkingConfig, ChunkingStrategy, ChunkPlan
from investigation_engine.domain.content import Content


def validate_chunk_plan(plan: ChunkPlan, content: Content) -> None:  # noqa: WPS110 - preserve established domain parameter
    """Validate against the authoritative Content of the plan's version.

    Cover every character, including whitespace. Overlap is permitted for fixed
    windows configured with overlap. Token budgets and exact algorithm boundaries
    are the generating chunker's responsibility; validation does not retokenize.
    """
    _validate_plan_header(plan)
    _validate_membership_coverage(plan.chunks, content, plan.config)


def _validate_plan_header(plan: ChunkPlan) -> None:
    if not isinstance(plan, ChunkPlan) or not isinstance(plan.document_version_id, UUID):
        raise ValueError("ChunkPlan requires a document version UUID")
    if not isinstance(plan.config, ChunkingConfig):
        raise ValueError("ChunkPlan requires a ChunkingConfig")
    plan.config.__post_init__()
    plan.config.tokenizer.__post_init__()
    if plan.config.algorithm_version != "1":
        raise ValueError("Unsupported chunking algorithm version")
    if not isinstance(plan.chunks, tuple):
        raise ValueError("ChunkPlan memberships must be an immutable tuple")


def _validate_membership_coverage(
    chunks: tuple[Chunk, ...], authoritative_content: Content, config: ChunkingConfig
) -> None:
    allow_overlap = config.strategy == ChunkingStrategy.FIXED_TOKENS and config.overlap > 0
    covered_end = 0
    previous_start = -1
    for chunk in chunks:
        _validate_membership_range(chunk, authoritative_content)
        previous_start, covered_end = _advance_coverage(chunk, allow_overlap, previous_start, covered_end)
    if covered_end != len(authoritative_content.text):
        raise ValueError("ChunkPlan must cover the complete Content, including whitespace")


def _validate_membership_range(chunk: Chunk, authoritative_content: Content) -> None:
    if not isinstance(chunk, Chunk) or chunk.content != authoritative_content:
        raise ValueError("Every chunk must reference the version's exact Content")
    # Reject bool values too; bool is a subclass of int in Python.
    # Keep the complete half-open range invariant in one chained comparison.
    if (
        type(chunk.start) is not int  # noqa: WPS516
        or type(chunk.end) is not int  # noqa: WPS516
        or not 0 <= chunk.start < chunk.end <= len(authoritative_content.text)  # noqa: WPS228
    ):
        raise ValueError("Chunk ranges must lie within the version's Content")


def _advance_coverage(chunk: Chunk, allow_overlap: bool, previous_start: int, covered_end: int) -> tuple[int, int]:
    if chunk.start <= previous_start:
        raise ValueError("Chunk ranges must be ordered with distinct increasing starts")
    if chunk.start > covered_end or (not allow_overlap and chunk.start < covered_end):
        raise ValueError("Chunk coverage has a gap or an unconfigured overlap")
    return chunk.start, max(covered_end, chunk.end)
