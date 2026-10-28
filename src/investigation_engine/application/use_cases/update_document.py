"""Create a new immutable version while keeping the active one searchable."""

from uuid import UUID

from investigation_engine.application.document_access import DocumentActor
from investigation_engine.application.ingestion.extracted_document import ExtractedDocument
from investigation_engine.application.ports.document_repository import DocumentRepository
from investigation_engine.domain.content import Content
from investigation_engine.domain.document import Document
from investigation_engine.domain.document_version import DocumentVersion


class UpdateDocument:
    def __init__(self, repository: DocumentRepository) -> None:
        self._repository = repository

    async def execute(
        self, document_id: UUID, extracted: ExtractedDocument, *, actor: DocumentActor,
        expected_current_version_id: UUID,
    ) -> tuple[Document, DocumentVersion]:
        if not isinstance(actor, DocumentActor):
            raise TypeError("A trusted DocumentActor is required")
        return await self._repository.append_version(
            document_id, actor_id=actor.principal_id,
            expected_current_version_id=expected_current_version_id,
            content=Content(text=extracted.text), source=extracted.source,
            structure=extracted.structure, source_spans=extracted.source_spans,
        )
