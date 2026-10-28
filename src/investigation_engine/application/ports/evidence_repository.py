from typing import Protocol

from investigation_engine.application.chunking import ChunkingConfig
from investigation_engine.application.embeddings import EmbeddingConfig, EmbeddingVector
from investigation_engine.application.retrieval import Evidence


class EvidenceRepository(Protocol):
    async def search(
        self, vector: EmbeddingVector, chunking: ChunkingConfig, embedding: EmbeddingConfig,
        *, owner_id: str | None, limit: int,
    ) -> tuple[Evidence, ...]:
        """Exact ranked search; apply every eligibility restriction BEFORE top-K.

        None owner permits only shared documents; a trusted principal also sees
        its private documents. Require active, nondeleted versions with complete
        selected plans and all selected-profile eligible vectors. Return separate
        source occurrences with deterministic ties; do not deduplicate semantics.
        """
        ...


class HybridEvidenceRepository(Protocol):
    async def search_channels(
        self, vector: EmbeddingVector, question: str, chunking: ChunkingConfig, embedding: EmbeddingConfig,
        *, owner_id: str | None, semantic_limit: int, keyword_limit: int,
    ) -> tuple[tuple[Evidence, ...], tuple[Evidence, ...]]:
        """Independent ordered channels, fully eligible within ONE read snapshot.

        Both limits apply after visibility/active-version/complete-profile checks.
        No Python post-filtering, implicit fallback, or fusion in persistence.
        """
        ...
