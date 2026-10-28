"""Retrieve an accessible document and its immutable version history."""

from uuid import UUID

from investigation_engine.application.document_access import DocumentActor
from investigation_engine.application.document_views import DocumentDetails
from investigation_engine.application.ports.document_repository import DocumentRepository


class GetDocument:
    def __init__(self, repository: DocumentRepository) -> None:
        self._repository = repository

    async def execute(self, document_id: UUID, *, actor: DocumentActor) -> DocumentDetails:
        if not isinstance(actor, DocumentActor):
            raise TypeError("A trusted DocumentActor is required")
        return await self._repository.get_details(document_id, actor_id=actor.principal_id)
