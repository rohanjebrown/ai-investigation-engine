from typing import Protocol

from investigation_engine.domain.document import Document
from investigation_engine.domain.document_version import DocumentVersion


class DocumentRepository(Protocol):

    async def register(
        self,
        document: Document,
        version: DocumentVersion,
    ) -> tuple[Document, DocumentVersion]:
        """Return the stored registration, reusing an identical source retry.

        Changed text or spans for the same source occurrence/revision conflict.
        Ownership must come from a trusted caller and match on retries.
        Expected failures are RegistrationConflict, ContentHashCollision,
        PermissionError, and invalid-input ValueError. Persistence failures use
        RepositoryError; cancellation propagates. No general retry is promised.
        This operation does not append revisions to an existing document.
        """
        ...
