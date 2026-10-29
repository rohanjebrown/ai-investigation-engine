from collections.abc import Sequence
from typing import Protocol

from investigation_engine.application.chunking import Chunk
from investigation_engine.application.embeddings import EmbeddingConfig
from investigation_engine.application.ports.embedding_provider import EmbeddingVector


class EmbeddingRepository(Protocol):
    async def find(
        self,
        chunks: Sequence[Chunk],
        config: EmbeddingConfig,
    ) -> dict[tuple[str, int, int], EmbeddingVector]:
        """Read compatible committed vectors by existing Chunk.identity."""
        ...

    async def save(self, chunk: Chunk, config: EmbeddingConfig, vector: EmbeddingVector) -> EmbeddingVector:
        """Commit one float32 vector or reuse a compatible winner, never overwrite.

        Own/close a short transaction before returning; no provider calls occur.
        Invalid input raises ValueError; persistence/corruption uses RepositoryError.
        """
        ...
