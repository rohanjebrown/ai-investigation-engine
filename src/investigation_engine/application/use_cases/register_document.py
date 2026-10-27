from investigation_engine.application.ingestion.extracted_document import (
    ExtractedDocument,
)
from investigation_engine.application.ports.document_repository import (
    DocumentRepository,
)
from investigation_engine.domain.content import Content
from investigation_engine.domain.document import Document
from investigation_engine.domain.document_version import DocumentVersion


class RegisterDocument:

    def __init__(self, repository: DocumentRepository) -> None:
        self.repository = repository

    async def execute(
        self,
        extracted: ExtractedDocument,
        *,
        owner_id: str | None,
    ) -> tuple[Document, DocumentVersion]:
        """Register using ownership explicitly assigned by a trusted caller.

        This is not authentication. Upload entry points must derive the owner
        from authenticated context; system ingestion explicitly supplies None.
        """

        content = Content(text=extracted.text)

        document = Document(
            owner_id=owner_id,
            title=extracted.title,
        )

        version = DocumentVersion(
            document_id=document.id,
            version_number=1,
            content=content,
            source=extracted.source,
            structure=extracted.structure,
            source_spans=extracted.source_spans,
        )

        return await self.repository.register(
            document=document,
            version=version,
        )
