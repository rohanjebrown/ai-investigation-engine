from typing import Protocol
from uuid import UUID

from investigation_engine.application.document_views import DocumentDetails, DocumentPage
from investigation_engine.domain.content import Content
from investigation_engine.domain.document import Document
from investigation_engine.domain.document_version import DocumentVersion
from investigation_engine.domain.provenance import SourceReference
from investigation_engine.domain.source_span import SourceSpan
from investigation_engine.domain.structural_span import StructuralSpan


class DocumentRepository(Protocol):
    async def register(
        self,
        document: Document,
        version: DocumentVersion,
    ) -> tuple[Document, DocumentVersion]:
        """Return the stored registration, reusing an identical source retry.

        Changed text or spans for the same source occurrence/revision conflict.
        Ownership must come from a trusted caller and match on retries.
        A new occurrence requires version 1 and no current_version_id. Resolve
        identical retries first, preserving the stored active-version pointer.
        Expected failures are RegistrationConflict, ContentHashCollision,
        PermissionError, and invalid-input ValueError. Persistence failures use
        RepositoryError; cancellation propagates. No general retry is promised.
        This operation does not append revisions to an existing document.
        """
        ...

    async def append_version(
        self,
        document_id: UUID,
        *,
        actor_id: str,
        expected_current_version_id: UUID,
        content: Content,
        source: SourceReference,
        structure: tuple[StructuralSpan, ...] = (),
        source_spans: tuple[SourceSpan, ...] = (),
    ) -> tuple[Document, DocumentVersion]:
        """Append one immutable, initially inactive version, or resolve its retry."""
        ...

    async def soft_delete(self, document_id: UUID, *, actor_id: str) -> Document:
        """Soft-delete an owned document; repeated deletion returns its state."""
        ...

    async def get_details(self, document_id: UUID, *, actor_id: str) -> DocumentDetails:
        """Return a shared or actor-owned document and its versions."""
        ...

    async def list_documents(
        self,
        *,
        actor_id: str,
        offset: int,
        limit: int,
        include_deleted: bool = False,
    ) -> DocumentPage:
        """List visible documents using repository-side visibility filtering."""
        ...
