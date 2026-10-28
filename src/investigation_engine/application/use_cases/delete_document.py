"""Soft-delete an actor-owned document."""

from uuid import UUID

from investigation_engine.application.document_access import DocumentActor
from investigation_engine.application.ports.document_repository import DocumentRepository
from investigation_engine.domain.document import Document


class DeleteDocument:
    def __init__(self, repository: DocumentRepository) -> None:
        self._repository = repository

    async def execute(self, document_id: UUID, *, actor: DocumentActor) -> Document:
        if not isinstance(actor, DocumentActor):
            raise TypeError("A trusted DocumentActor is required")
        return await self._repository.soft_delete(document_id, actor_id=actor.principal_id)
