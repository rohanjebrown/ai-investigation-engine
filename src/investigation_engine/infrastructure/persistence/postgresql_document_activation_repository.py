"""Transactional completeness and document-state checks for trusted indexing."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from investigation_engine.application.chunking import ChunkingConfig
from investigation_engine.application.embeddings import EmbeddingConfig, embedding_eligible
from investigation_engine.application.ports.repository_errors import ActivationConflict, RepositoryError
from investigation_engine.infrastructure.persistence._embedding_mapping import configuration_values, find_embeddings
from investigation_engine.infrastructure.persistence.models import DocumentRow, DocumentVersionRow
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import (
    PostgreSQLChunkPlanRepository,
    _configuration_values,
)


class PostgreSQLDocumentActivationRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @staticmethod
    def _check(document: DocumentRow | None, version: DocumentVersionRow | None, document_id: UUID) -> None:
        if document is None or version is None or version.document_id != document_id:
            raise ActivationConflict("Document/version ownership could not be verified")
        if document.deleted_at is not None:
            raise ActivationConflict("Deleted documents cannot be activated")

    async def current_version(self, document_id: UUID, version_id: UUID) -> UUID | None:
        try:
            async with self._sessions() as session:
                async with session.begin():
                    await session.connection(execution_options={"isolation_level": "READ COMMITTED"})
                    document = await session.get(DocumentRow, document_id)
                    version = await session.get(DocumentVersionRow, version_id)
                    self._check(document, version, document_id)
                    pointer = document.current_version_id
            return pointer
        except SQLAlchemyError as error:
            raise RepositoryError("Document indexing state could not be read") from error

    async def activate(
        self,
        document_id: UUID,
        version_id: UUID,
        chunking: ChunkingConfig,
        embedding: EmbeddingConfig,
        *,
        expected_current_version_id: UUID | None,
    ) -> bool:
        values = _configuration_values(chunking)
        configuration_values(embedding)
        try:
            async with self._sessions() as session:
                async with session.begin():
                    await session.connection(execution_options={"isolation_level": "READ COMMITTED"})
                    document = await session.scalar(
                        select(DocumentRow).where(DocumentRow.id == document_id).with_for_update()
                    )
                    version = await session.scalar(
                        select(DocumentVersionRow).where(DocumentVersionRow.id == version_id).with_for_update()
                    )
                    self._check(document, version, document_id)
                    found = await PostgreSQLChunkPlanRepository._find_plan(session, version_id, values)
                    if found is None:
                        raise ActivationConflict("Activation requires a complete selected chunk plan")
                    content = await PostgreSQLChunkPlanRepository._version_content(session, version)
                    plan = await PostgreSQLChunkPlanRepository._reconstruct(session, *found, content)
                    eligible = tuple(c for c in plan.chunks if embedding_eligible(c))
                    if not eligible or set(await find_embeddings(session, eligible, embedding)) != {
                        c.identity for c in eligible
                    }:
                        raise ActivationConflict("Activation requires complete eligible embeddings")
                    changed = document.current_version_id != version_id
                    if changed:
                        if document.current_version_id != expected_current_version_id:
                            raise ActivationConflict("The active version changed during indexing")
                        latest_version_number = await session.scalar(
                            select(func.max(DocumentVersionRow.version_number)).where(
                                DocumentVersionRow.document_id == document_id,
                            )
                        )
                        if version.version_number != latest_version_number:
                            raise ActivationConflict("A newer document version superseded this indexing operation")
                        if document.current_version_id is not None:
                            current = await session.get(DocumentVersionRow, document.current_version_id)
                            if (
                                current is None
                                or current.document_id != document_id
                                or version.version_number <= current.version_number
                            ):
                                raise ActivationConflict("Activation cannot replace a newer active version")
                        document.current_version_id = version_id
            return changed
        except SQLAlchemyError as error:
            raise RepositoryError("Activation failed; its commit outcome may be unknown") from error
