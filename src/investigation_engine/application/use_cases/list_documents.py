"""List shared and actor-owned documents with bounded pagination."""

from investigation_engine.application.document_access import DocumentActor
from investigation_engine.application.document_views import DocumentPage
from investigation_engine.application.ports.document_repository import DocumentRepository


class ListDocuments:
    def __init__(self, repository: DocumentRepository) -> None:
        self._repository = repository

    async def execute(
        self, *, actor: DocumentActor, offset: int = 0, limit: int = 50,
        include_deleted: bool = False,
    ) -> DocumentPage:
        if not isinstance(actor, DocumentActor):
            raise TypeError("A trusted DocumentActor is required")
        if type(offset) is not int or offset < 0:
            raise ValueError("Offset must be a nonnegative integer")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Limit must be between 1 and 100")
        if type(include_deleted) is not bool:
            raise ValueError("include_deleted must be a boolean")
        return await self._repository.list_documents(
            actor_id=actor.principal_id, offset=offset, limit=limit,
            include_deleted=include_deleted,
        )
