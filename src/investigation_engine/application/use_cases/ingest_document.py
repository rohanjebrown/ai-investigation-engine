"""Prepare documents and vectors before one transactional indexed commit."""

import asyncio
from uuid import UUID

from investigation_engine.application.chunk_plan_validation import validate_chunk_plan
from investigation_engine.application.chunking import ChunkingConfig, ChunkPlan, chunk_document
from investigation_engine.application.document_access import DocumentActor
from investigation_engine.application.embeddings import EmbeddingConfig, embedding_eligible
from investigation_engine.application.ingestion.extracted_document import ExtractedDocument
from investigation_engine.application.ports.indexed_document_repository import IndexedDocumentRepository
from investigation_engine.application.ports.text_tokenizer import TextTokenizer
from investigation_engine.application.use_cases.index_document_version import IndexDocumentVersion
from investigation_engine.domain.content import Content
from investigation_engine.domain.document import Document
from investigation_engine.domain.document_version import DocumentVersion


class IngestDocument:
    def __init__(
        self,
        repository: IndexedDocumentRepository,
        indexer: IndexDocumentVersion,
        tokenizer: TextTokenizer,
        chunking: ChunkingConfig,
        embedding: EmbeddingConfig,
    ) -> None:
        self._repository = repository
        self._indexer = indexer
        self._tokenizer = tokenizer
        self._chunking = chunking
        self._embedding = embedding

    async def create(self, extracted: ExtractedDocument, *, actor: DocumentActor) -> tuple[Document, DocumentVersion]:
        if not isinstance(actor, DocumentActor):
            raise TypeError("A trusted DocumentActor is required")
        document = Document(owner_id=actor.principal_id, title=extracted.title)
        version = self._version(document.id, extracted)
        plan = self._plan(version)
        async with asyncio.timeout(self.maximum_request_budget_seconds(plan)):
            vectors = await self._indexer.prepare_plan(plan, self._embedding)
            return await self._repository.commit_indexed(
                document,
                version,
                plan,
                self._chunking,
                self._embedding,
                vectors,
                actor_id=actor.principal_id,
                expected_current_version_id=None,
            )

    async def replace(
        self,
        document_id: UUID,
        extracted: ExtractedDocument,
        *,
        actor: DocumentActor,
        expected_current_version_id: UUID,
    ) -> tuple[Document, DocumentVersion]:
        if not isinstance(actor, DocumentActor):
            raise TypeError("A trusted DocumentActor is required")
        document = Document(id=document_id, owner_id=actor.principal_id)
        version = self._version(document_id, extracted)
        plan = self._plan(version)
        async with asyncio.timeout(self.maximum_request_budget_seconds(plan)):
            vectors = await self._indexer.prepare_plan(plan, self._embedding)
            return await self._repository.commit_indexed(
                document,
                version,
                plan,
                self._chunking,
                self._embedding,
                vectors,
                actor_id=actor.principal_id,
                expected_current_version_id=expected_current_version_id,
            )

    def maximum_request_budget_seconds(self, plan: ChunkPlan) -> float:
        eligible = sum(embedding_eligible(chunk) for chunk in plan.chunks)
        return self._indexer.maximum_retry_budget_seconds(eligible) + 30.0

    def _plan(self, version: DocumentVersion) -> ChunkPlan:
        plan = chunk_document(version, config=self._chunking, tokenizer=self._tokenizer)
        validate_chunk_plan(plan, version.content)
        return plan

    @staticmethod
    def _version(document_id: UUID, extracted: ExtractedDocument) -> DocumentVersion:
        return DocumentVersion(
            document_id=document_id,
            version_number=1,
            content=Content(text=extracted.text),
            source=extracted.source,
            structure=extracted.structure,
            source_spans=extracted.source_spans,
        )
