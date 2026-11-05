from typing import Protocol
from uuid import UUID

from investigation_engine.application.chunking import ChunkingConfig, ChunkPlan
from investigation_engine.application.embeddings import EmbeddingConfig, EmbeddingVector
from investigation_engine.domain.document import Document
from investigation_engine.domain.document_version import DocumentVersion


class IndexedDocumentRepository(Protocol):
    async def commit_indexed(
        self,
        document: Document,
        version: DocumentVersion,
        plan: ChunkPlan,
        chunking: ChunkingConfig,
        embedding: EmbeddingConfig,
        vectors: dict[tuple[str, int, int], EmbeddingVector],
        *,
        actor_id: str,
        expected_current_version_id: UUID | None,
    ) -> tuple[Document, DocumentVersion]:
        """Persist, establish readiness, and activate in one transaction."""
        ...
