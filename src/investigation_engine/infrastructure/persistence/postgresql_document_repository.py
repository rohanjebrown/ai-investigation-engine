"""Atomic create/register only; no updates, activation, or access-control API."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from investigation_engine.application.ports.repository_errors import (
    ContentHashCollision,
    RegistrationConflict,
    RepositoryError,
)
from investigation_engine.domain.content import Content
from investigation_engine.domain.document import Document
from investigation_engine.domain.document_version import DocumentVersion
from investigation_engine.infrastructure.persistence._document_mapping import (
    _content,
    _domain_registration,
    _snapshot,
    _version_row,
)
from investigation_engine.infrastructure.persistence.models import (
    ContentRow,
    DocumentRow,
    DocumentVersionRow,
)


_OCCURRENCE_CONSTRAINT = "uq_document_versions_source_occurrence"


class PostgreSQLDocumentRepository:
    """Each call owns isolated sessions/transactions from the injected factory.

    Ownership must be assigned by trusted application code. This repository is
    not authentication and must not be exposed as an unrestricted public API.
    No automatic transaction replay; failures during commit may be ambiguous.
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def register(
        self, document: Document, version: DocumentVersion,
    ) -> tuple[Document, DocumentVersion]:
        # All caller-owned mutable data is copied before the first await.
        document, version = _snapshot(document, version)
        try:
            try:
                return await self._register_once(document, version)
            except IntegrityError as error:
                # _register_once has exited its transaction/session contexts:
                # even newly inserted Content and candidate Documents rolled back.
                constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
                if getattr(error.orig, "sqlstate", None) != "23505":
                    raise
                if constraint == _OCCURRENCE_CONSTRAINT:
                    return await self._resolve_winner(document, version)
                if constraint == "pk_documents":
                    raise ValueError("Document already exists") from error
                if constraint == "pk_document_versions":
                    raise ValueError("Document version already exists") from error
                raise
        except SQLAlchemyError as error:
            raise RepositoryError(
                "Document registration could not be completed; its commit outcome "
                "may be unknown. Resolve using the same source identity."
            ) from error

    async def _register_once(
        self, document: Document, version: DocumentVersion,
    ) -> tuple[Document, DocumentVersion]:
        async with self._sessions() as session:
            async with session.begin():
                await session.connection(execution_options={"isolation_level": "READ COMMITTED"})
                existing = await self._existing(session, document, version)
                if existing is not None:
                    result = existing
                else:
                    if version.version_number != 1 or document.current_version_id is not None:
                        raise ValueError("New registrations require version 1 and no active version")
                    digest = bytes.fromhex(version.content.sha256)
                    inserted = await session.scalar(
                        insert(ContentRow).values(sha256=digest, text=version.content.text)
                        .on_conflict_do_nothing(constraint="pk_contents")
                        .returning(ContentRow.sha256)
                    )
                    if inserted is None:
                        # A separate statement sees a concurrent committed winner
                        # even when it was invisible to the INSERT's snapshot.
                        stored_content = await session.get(ContentRow, digest)
                        if stored_content is None:
                            raise RepositoryError("Reusable Content could not be resolved")
                        if stored_content.text != version.content.text:
                            raise ContentHashCollision("Content hash collision detected")
                    stored_document = DocumentRow(
                        id=document.id, owner_id=document.owner_id, title=document.title,
                        current_version_id=None, created_at=document.created_at,
                        deleted_at=document.deleted_at,
                    )
                    stored_version = _version_row(version)
                    session.add(stored_document)
                    await session.flush()
                    session.add(stored_version)
                    await session.flush()
                    result = self._reconstruct(stored_document, stored_version, version.content)
        # Context exit commits and closes before any success is returned.
        return result

    async def _resolve_winner(
        self, document: Document, version: DocumentVersion,
    ) -> tuple[Document, DocumentVersion]:
        async with self._sessions() as session:
            async with session.begin():
                await session.connection(execution_options={"isolation_level": "READ COMMITTED"})
                result = await self._existing(session, document, version)
                if result is None:
                    raise RepositoryError("The winning registration could not be resolved")
        return result

    async def _existing(
        self, session: AsyncSession, document: Document, version: DocumentVersion,
    ) -> tuple[Document, DocumentVersion] | None:
        source = version.source
        found = (await session.execute(
            select(DocumentRow, DocumentVersionRow)
            .join(DocumentVersionRow, DocumentVersionRow.document_id == DocumentRow.id)
            .where(
                DocumentVersionRow.kind == source.kind,
                DocumentVersionRow.source_id == source.source_id,
                DocumentVersionRow.revision.is_not_distinct_from(source.revision),
                DocumentVersionRow.record_locator == source.record_locator,
            )
        )).one_or_none()
        if found is None:
            return None
        stored_document, stored_version = found
        # Includes deleted registrations; authorize before content/conflict details.
        if stored_document.owner_id != document.owner_id:
            raise PermissionError("Registration is not permitted")
        stored_content = await session.get(ContentRow, stored_version.content_sha256)
        if stored_content is None:
            raise RepositoryError("Stored Content could not be resolved")
        try:
            content = _content(stored_content)
        except (TypeError, ValueError) as error:
            raise RepositoryError("Stored Content is invalid") from error
        result = self._reconstruct(stored_document, stored_version, content)
        if result[1].content.text != version.content.text:
            raise RegistrationConflict("Registration conflict: extracted text differs")
        if (
            result[1].structure != version.structure
            or result[1].source_spans != version.source_spans
        ):
            raise RegistrationConflict("Registration conflict: structural or source spans differ")
        return result

    @staticmethod
    def _reconstruct(
        document: DocumentRow, version: DocumentVersionRow, content: Content,
    ) -> tuple[Document, DocumentVersion]:
        try:
            return _domain_registration(document, version, content)
        except (KeyError, TypeError, ValueError) as error:
            raise RepositoryError("Stored document registration is invalid") from error
