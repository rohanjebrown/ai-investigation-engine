"""Persist the deterministic plan, then advance existing bounded indexing."""

from investigation_engine.application.chunking import ChunkingConfig, chunk_document
from investigation_engine.application.embeddings import EmbeddingConfig
from investigation_engine.application.ports.chunk_plan_repository import ChunkPlanRepository
from investigation_engine.application.ports.text_tokenizer import TextTokenizer
from investigation_engine.application.use_cases.index_document_version import (
    IndexDocumentVersion,
    IndexingProgress,
    IndexingReport,
)
from investigation_engine.domain.document import Document
from investigation_engine.domain.document_version import DocumentVersion


class PrepareDocumentIndexing:
    def __init__(
        self,
        plans: ChunkPlanRepository,
        indexer: IndexDocumentVersion,
        tokenizer: TextTokenizer,
    ) -> None:
        self._plans = plans
        self._indexer = indexer
        self._tokenizer = tokenizer

    async def execute(
        self,
        document: Document,
        version: DocumentVersion,
        chunking: ChunkingConfig,
        embedding: EmbeddingConfig,
        *,
        max_new_embeddings: int,
    ) -> IndexingReport:
        plan = chunk_document(version, config=chunking, tokenizer=self._tokenizer)
        await self._plans.save(plan)
        return await self._indexer.execute_bounded(
            document.id,
            version.id,
            chunking,
            embedding,
            max_new_embeddings=max_new_embeddings,
        )

    async def inspect(
        self,
        document: Document,
        version: DocumentVersion,
        chunking: ChunkingConfig,
        embedding: EmbeddingConfig,
    ) -> IndexingProgress:
        return await self._indexer.inspect(document, version, chunking, embedding)
