"""Storage-independent checks on complete plans; no tokenizer or provider IO."""

from uuid import UUID

from investigation_engine.application.chunking import Chunk, ChunkingConfig, ChunkingStrategy, ChunkPlan
from investigation_engine.domain.content import Content


def validate_chunk_plan(plan: ChunkPlan, content: Content) -> None:
    """Validate against the authoritative Content of the plan's version.

    Cover every character, including whitespace. Overlap is permitted for fixed
    windows configured with overlap. Token budgets and exact algorithm boundaries
    are the generating chunker's responsibility; validation does not retokenize.
    """
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

    allow_overlap = (
        plan.config.strategy == ChunkingStrategy.FIXED_TOKENS
        and plan.config.overlap > 0
    )
    covered_end = 0
    previous_start = -1
    for chunk in plan.chunks:
        if not isinstance(chunk, Chunk) or chunk.content != content:
            raise ValueError("Every chunk must reference the version's exact Content")
        if (
            type(chunk.start) is not int or type(chunk.end) is not int
            or not 0 <= chunk.start < chunk.end <= len(content.text)
        ):
            raise ValueError("Chunk ranges must lie within the version's Content")
        if chunk.start <= previous_start:
            raise ValueError("Chunk ranges must be ordered with distinct increasing starts")
        if chunk.start > covered_end or (not allow_overlap and chunk.start < covered_end):
            raise ValueError("Chunk coverage has a gap or an unconfigured overlap")
        previous_start = chunk.start
        covered_end = max(covered_end, chunk.end)
    if covered_end != len(content.text):
        raise ValueError("ChunkPlan must cover the complete Content, including whitespace")
