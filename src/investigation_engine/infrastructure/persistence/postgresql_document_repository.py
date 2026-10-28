"""Repository-owned document registration, lifecycle mutation, and reads."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from investigation_engine.application.ports.repository_errors import (
    ContentHashCollision,
    DocumentAccessDenied,
    DocumentNotFound,
    DocumentUpdateConflict,
    RegistrationConflict,
    RepositoryError,
)
from investigation_engine.application.document_views import DocumentDetails, DocumentListItem, DocumentPage
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

    async def append_version(
        self, document_id: UUID, *, actor_id: str, expected_current_version_id: UUID,
        content: Content, source, structure=(), source_spans=(),
    ) -> tuple[Document, DocumentVersion]:
        candidate_document = Document(id=document_id, owner_id=actor_id)
        candidate_version = DocumentVersion(
            document_id=document_id, version_number=1, content=content, source=source,
            structure=structure, source_spans=source_spans,
        )
        candidate_document, candidate_version = _snapshot(candidate_document, candidate_version)
        try:
            try:
                return await self._append_version_once(
                    candidate_document, candidate_version, expected_current_version_id,
                )
            except IntegrityError as error:
                constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
                if getattr(error.orig, "sqlstate", None) == "23505" and constraint == _OCCURRENCE_CONSTRAINT:
                    return await self._resolve_append_retry(
                        candidate_document, candidate_version,
                    )
                raise
        except SQLAlchemyError as error:
            raise RepositoryError(
                "Document update could not be completed; its commit outcome may be unknown. "
                "Retry using the same source revision."
            ) from error

    async def _append_version_once(
        self, document: Document, version: DocumentVersion, expected_current_version_id: UUID,
    ) -> tuple[Document, DocumentVersion]:
        async with self._sessions() as session:
            async with session.begin():
                await session.connection(execution_options={"isolation_level": "READ COMMITTED"})
                stored_document = await session.scalar(
                    select(DocumentRow).where(DocumentRow.id == document.id).with_for_update()
                )
                self._authorize_mutation(stored_document, document.owner_id)
                if stored_document.deleted_at is not None:
                    raise DocumentUpdateConflict("Deleted documents cannot be updated")
                if not isinstance(version.source.revision, str) or not version.source.revision.strip():
                    raise DocumentUpdateConflict("A document update requires an explicit nonblank source revision")

                # Resolve a precise retry before optimistic-version and pending checks.
                # Its stored revision remains the operation identity across restarts.
                existing = await self._existing(session, document, version)
                if existing is not None:
                    if existing[0].id != document.id:
                        raise RegistrationConflict("Source revision is already registered to another document")
                    result = existing
                else:
                    if stored_document.current_version_id != expected_current_version_id:
                        raise DocumentUpdateConflict("The active document version changed; reload before updating")
                    if stored_document.current_version_id is None:
                        raise DocumentUpdateConflict("A document must have an active version before it can be updated")
                    active = await session.get(DocumentVersionRow, stored_document.current_version_id)
                    if active is None or active.document_id != document.id:
                        raise RepositoryError("The active document version could not be resolved")
                    same_source = await session.scalar(select(DocumentVersionRow.id).where(
                        DocumentVersionRow.id == active.id,
                        DocumentVersionRow.kind == version.source.kind,
                        DocumentVersionRow.source_id == version.source.source_id,
                        DocumentVersionRow.record_locator == version.source.record_locator,
                    ))
                    if same_source is None:
                        raise DocumentUpdateConflict(
                            "An update must retain the document's source kind, source ID, and record locator"
                        )
                    latest_number = await session.scalar(
                        select(func.max(DocumentVersionRow.version_number)).where(
                            DocumentVersionRow.document_id == document.id,
                        )
                    )
                    if latest_number > active.version_number:
                        raise DocumentUpdateConflict(
                            "Another document update is pending indexing; retry it or resolve it before starting another"
                        )
                    version_number = latest_number + 1
                    digest = bytes.fromhex(version.content.sha256)
                    inserted = await session.scalar(
                        insert(ContentRow).values(sha256=digest, text=version.content.text)
                        .on_conflict_do_nothing(constraint="pk_contents")
                        .returning(ContentRow.sha256)
                    )
                    if inserted is None:
                        stored_content = await session.get(ContentRow, digest)
                        if stored_content is None:
                            raise RepositoryError("Reusable Content could not be resolved")
                        if stored_content.text != version.content.text:
                            raise ContentHashCollision("Content hash collision detected")
                    new_version = DocumentVersion(
                        id=version.id, document_id=document.id, version_number=version_number,
                        content=version.content, source=version.source, structure=version.structure,
                        source_spans=version.source_spans, created_at=version.created_at,
                    )
                    row = _version_row(new_version)
                    session.add(row)
                    await session.flush()
                    result = self._reconstruct(stored_document, row, version.content)
        return result

    async def _resolve_append_retry(
        self, document: Document, version: DocumentVersion,
    ) -> tuple[Document, DocumentVersion]:
        async with self._sessions() as session:
            async with session.begin():
                await session.connection(execution_options={"isolation_level": "READ COMMITTED"})
                stored_document = await session.scalar(
                    select(DocumentRow).where(DocumentRow.id == document.id).with_for_update()
                )
                self._authorize_mutation(stored_document, document.owner_id)
                if stored_document.deleted_at is not None:
                    raise DocumentUpdateConflict("Deleted documents cannot be updated")
                existing = await self._existing(session, document, version)
                if existing is None or existing[0].id != document.id:
                    raise RegistrationConflict("Source revision is already registered to another document")
                result = existing
        return result

    @staticmethod
    def _authorize_mutation(document: DocumentRow | None, actor_id: str) -> None:
        if document is None:
            raise DocumentNotFound("Document was not found")
        # Shared documents are system-managed, never implicitly mutable by a user.
        if document.owner_id is None or document.owner_id != actor_id:
            raise DocumentAccessDenied("Document access is not permitted")

    @staticmethod
    def _authorize_read(document: DocumentRow | None, actor_id: str) -> None:
        if document is None:
            raise DocumentNotFound("Document was not found")
        if document.owner_id is not None and document.owner_id != actor_id:
            raise DocumentAccessDenied("Document access is not permitted")

    async def soft_delete(self, document_id: UUID, *, actor_id: str) -> Document:
        try:
            async with self._sessions() as session:
                async with session.begin():
                    await session.connection(execution_options={"isolation_level": "READ COMMITTED"})
                    row = await session.scalar(
                        select(DocumentRow).where(DocumentRow.id == document_id).with_for_update()
                    )
                    self._authorize_mutation(row, actor_id)
                    if row.deleted_at is None:
                        row.deleted_at = datetime.now(UTC)
                    result = Document(
                        id=row.id, owner_id=row.owner_id, title=row.title,
                        current_version_id=row.current_version_id, created_at=row.created_at,
                        deleted_at=row.deleted_at,
                    )
            return result
        except SQLAlchemyError as error:
            raise RepositoryError("Document deletion failed; its commit outcome may be unknown") from error

    async def get_details(self, document_id: UUID, *, actor_id: str) -> DocumentDetails:
        try:
            async with self._sessions() as session:
                async with session.begin():
                    await session.connection(execution_options={"isolation_level": "READ COMMITTED"})
                    document_row = await session.get(DocumentRow, document_id)
                    self._authorize_read(document_row, actor_id)
                    rows = (await session.execute(
                        select(DocumentVersionRow, ContentRow)
                        .join(ContentRow, ContentRow.sha256 == DocumentVersionRow.content_sha256)
                        .where(DocumentVersionRow.document_id == document_id)
                        .order_by(DocumentVersionRow.version_number)
                    )).all()
                    document = Document(
                        id=document_row.id, owner_id=document_row.owner_id, title=document_row.title,
                        current_version_id=document_row.current_version_id, created_at=document_row.created_at,
                        deleted_at=document_row.deleted_at,
                    )
                    versions = tuple(self._reconstruct(document_row, version, self._checked_content(content))[1]
                                     for version, content in rows)
                    result = DocumentDetails(document=document, versions=versions)
            return result
        except SQLAlchemyError as error:
            raise RepositoryError("Document details could not be read") from error

    async def list_documents(
        self, *, actor_id: str, offset: int, limit: int, include_deleted: bool = False,
    ) -> DocumentPage:
        visibility = or_(DocumentRow.owner_id.is_(None), DocumentRow.owner_id == actor_id)
        filters = [visibility]
        if not include_deleted:
            filters.append(DocumentRow.deleted_at.is_(None))
        count_versions = (
            select(func.count(DocumentVersionRow.id))
            .where(DocumentVersionRow.document_id == DocumentRow.id)
            .correlate(DocumentRow).scalar_subquery()
        )
        statement = (
            select(DocumentRow, DocumentVersionRow, ContentRow, count_versions.label("version_count"))
            .outerjoin(DocumentVersionRow, DocumentVersionRow.id == DocumentRow.current_version_id)
            .outerjoin(ContentRow, ContentRow.sha256 == DocumentVersionRow.content_sha256)
            .where(*filters)
            .order_by(DocumentRow.created_at.desc(), DocumentRow.id.desc())
            .offset(offset).limit(limit)
        )
        try:
            async with self._sessions() as session:
                async with session.begin():
                    await session.connection(execution_options={"isolation_level": "READ COMMITTED"})
                    total = await session.scalar(select(func.count()).select_from(DocumentRow).where(*filters))
                    rows = (await session.execute(statement)).all()
                    items = []
                    for document_row, version_row, content_row, version_count in rows:
                        document = Document(
                            id=document_row.id, owner_id=document_row.owner_id, title=document_row.title,
                            current_version_id=document_row.current_version_id, created_at=document_row.created_at,
                            deleted_at=document_row.deleted_at,
                        )
                        current = None
                        if version_row is not None:
                            current = self._reconstruct(document_row, version_row, self._checked_content(content_row))[1]
                        items.append(DocumentListItem(document, current, version_count))
                    result = DocumentPage(tuple(items), total, offset, limit)
            return result
        except SQLAlchemyError as error:
            raise RepositoryError("Document listing failed") from error

    @staticmethod
    def _checked_content(row: ContentRow | None) -> Content:
        if row is None:
            raise RepositoryError("Stored Content could not be resolved")
        try:
            return _content(row)
        except (TypeError, ValueError) as error:
            raise RepositoryError("Stored Content is invalid") from error

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
