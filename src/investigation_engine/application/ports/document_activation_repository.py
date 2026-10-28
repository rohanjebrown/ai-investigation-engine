from typing import Protocol
from uuid import UUID

from investigation_engine.application.chunking import ChunkingConfig
from investigation_engine.application.embeddings import EmbeddingConfig


class DocumentActivationRepository(Protocol):
    async def current_version(self, document_id: UUID, version_id: UUID) -> UUID | None:
        """Check live document/version ownership and return a concurrency snapshot."""
        ...

    async def activate(
        self, document_id: UUID, version_id: UUID, chunking: ChunkingConfig,
        embedding: EmbeddingConfig, *, expected_current_version_id: UUID | None,
    ) -> bool:
        """Atomically verify complete eligible embeddings and compare-and-set.

        Return True on activation, False on a verified already-current retry.
        ActivationConflict rejects missing/deleted/stale/incomplete state.
        Trusted internal operation, not public authentication/authorization.
        """
        ...
