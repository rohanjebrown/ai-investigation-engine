"""Transactional completeness and document-state checks for trusted indexing."""

from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from investigation_engine.application.chunking import ChunkingConfig
from investigation_engine.application.embeddings import EmbeddingConfig, embedding_eligible
from investigation_engine.application.ports.repository_errors import ActivationConflict, RepositoryError
from investigation_engine.infrastructure.persistence._embedding_mapping import (
    configuration_filter,
    configuration_id,
    configuration_values,
    find_embeddings,
)
from investigation_engine.infrastructure.persistence.models import (
    ChunkPlanReadinessRow,
    ContentRow,
    DocumentRow,
    DocumentVersionRow,
    EmbeddingConfigurationRow,
)
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

    @staticmethod
    async def _verify_and_record(
        session: AsyncSession,
        version: DocumentVersionRow,
        chunking: ChunkingConfig,
        embedding: EmbeddingConfig,
    ) -> int:
        """Validate under locks and atomically record the canonical profile pair."""
        values = _configuration_values(chunking)
        found = await PostgreSQLChunkPlanRepository._find_plan(session, version.id, values, lock=True)
        if found is None:
            raise ActivationConflict("Activation requires a complete selected chunk plan")
        await session.scalar(
            select(ContentRow).where(ContentRow.sha256 == version.content_sha256).with_for_update(read=True)
        )
        content = await PostgreSQLChunkPlanRepository._version_content(session, version)
        plan = await PostgreSQLChunkPlanRepository._reconstruct(session, *found, content)
        eligible = tuple(c for c in plan.chunks if embedding_eligible(c))
        # Lock every equivalent profile row and supporting vector until commit;
        # the database invalidation triggers serialize direct mutations with us.
        await session.execute(text("LOCK TABLE embedding_configurations IN SHARE MODE"))
        await session.scalars(
            select(EmbeddingConfigurationRow)
            .where(*configuration_filter(embedding))
            .order_by(EmbeddingConfigurationRow.id)
            .with_for_update(read=True)
        )
        if not eligible or set(await find_embeddings(session, eligible, embedding, lock=True)) != {
            c.identity for c in eligible
        }:
            raise ActivationConflict("Activation requires complete eligible embeddings")
        embedding_id = await configuration_id(session, embedding)
        readiness_key = (version.id, found[0].chunking_configuration_id, embedding_id)
        if await session.get(ChunkPlanReadinessRow, readiness_key) is None:
            await session.execute(
                pg_insert(ChunkPlanReadinessRow)
                .values(
                    document_version_id=version.id,
                    chunking_configuration_id=found[0].chunking_configuration_id,
                    embedding_configuration_id=embedding_id,
                )
                .on_conflict_do_nothing()
            )
        return embedding_id

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
        configuration_values(embedding)
        try:
            async with self._sessions() as session:
                async with session.begin():
                    await session.connection(execution_options={"isolation_level": "READ COMMITTED"})
                    changed = await self.activate_in_session(
                        session,
                        document_id,
                        version_id,
                        chunking,
                        embedding,
                        expected_current_version_id=expected_current_version_id,
                    )
            return changed
        except SQLAlchemyError as error:
            raise RepositoryError("Activation failed; its commit outcome may be unknown") from error

    @classmethod
    async def activate_in_session(
        cls,
        session: AsyncSession,
        document_id: UUID,
        version_id: UUID,
        chunking: ChunkingConfig,
        embedding: EmbeddingConfig,
        *,
        expected_current_version_id: UUID | None,
    ) -> bool:
        """Apply the normal activation checks inside a caller-owned transaction."""
        configuration_values(embedding)
        document = await session.scalar(select(DocumentRow).where(DocumentRow.id == document_id).with_for_update())
        version = await session.scalar(
            select(DocumentVersionRow).where(DocumentVersionRow.id == version_id).with_for_update()
        )
        cls._check(document, version, document_id)
        await cls._verify_and_record(session, version, chunking, embedding)
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

    async def backfill_active(
        self, chunking: ChunkingConfig, embedding: EmbeddingConfig
    ) -> AsyncIterator[dict[str, str]]:
        """Verify one profile pair in bounded keyset batches and stream each result."""
        last_document_id = None
        while True:
            async with self._sessions() as session:
                query = select(DocumentRow.id, DocumentRow.current_version_id).where(
                    DocumentRow.current_version_id.is_not(None), DocumentRow.deleted_at.is_(None)
                )
                if last_document_id is not None:
                    query = query.where(DocumentRow.id > last_document_id)
                candidates = (await session.execute(query.order_by(DocumentRow.id).limit(200))).all()
            if not candidates:
                return
            last_document_id = candidates[-1].id
            for document_id, version_id in candidates:
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
                            if (
                                document is None
                                or document.deleted_at is not None
                                or document.current_version_id != version_id
                                or version is None
                                or version.document_id != document_id
                            ):
                                raise ActivationConflict("Active document version changed during readiness backfill")
                            embedding_id = await self._verify_and_record(session, version, chunking, embedding)
                    yield {
                        "document_version_id": str(version_id),
                        "status": "verified",
                        "embedding_configuration_id": str(embedding_id),
                    }
                except ActivationConflict as error:
                    yield {"document_version_id": str(version_id), "status": "skipped", "reason": str(error)}
                except (RepositoryError, SQLAlchemyError, ValueError) as error:
                    yield {
                        "document_version_id": str(version_id),
                        "status": "failed",
                        "reason": type(error).__name__,
                    }
