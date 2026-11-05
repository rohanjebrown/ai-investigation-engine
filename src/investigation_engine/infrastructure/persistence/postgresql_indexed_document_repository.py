"""One transaction for document persistence, both vector stores, readiness and activation."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from investigation_engine.application.chunk_plan_validation import validate_chunk_plan
from investigation_engine.application.chunking import Chunk, ChunkingConfig, ChunkPlan
from investigation_engine.application.embeddings import (
    EmbeddingConfig,
    EmbeddingVector,
    embedding_eligible,
    storage_vector,
)
from investigation_engine.application.ports.repository_errors import (
    ActivationConflict,
    ChunkPlanConflict,
    ContentHashCollision,
    DocumentUpdateConflict,
    RegistrationConflict,
    RepositoryError,
)
from investigation_engine.domain.document import Document
from investigation_engine.domain.document_version import DocumentVersion
from investigation_engine.infrastructure.persistence._document_mapping import (
    _content,
    _snapshot,
    _version_row,
)
from investigation_engine.infrastructure.persistence._embedding_mapping import (
    configuration_id,
    configuration_values,
    find_embeddings,
)
from investigation_engine.infrastructure.persistence.models import (
    ChunkEmbeddingRow,
    ChunkingConfigurationRow,
    ChunkMembershipRow,
    ContentRow,
    DocumentRow,
    DocumentVersionRow,
    MembershipChunkEmbeddingRow,
    SearchableChunkRow,
    VersionChunkPlanRow,
)
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import (
    PostgreSQLChunkPlanRepository,
    _configuration_values,
)
from investigation_engine.infrastructure.persistence.postgresql_document_activation_repository import (
    PostgreSQLDocumentActivationRepository,
)
from investigation_engine.infrastructure.persistence.postgresql_document_repository import (
    PostgreSQLDocumentRepository,
)

_OCCURRENCE_CONSTRAINT = "uq_document_versions_source_occurrence"


class PostgreSQLIndexedDocumentRepository:
    """Owns the short all-writes transaction; callers prepare vectors beforehand."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._documents = PostgreSQLDocumentRepository(sessions)

    async def commit_indexed(
        self,
        document: Document,
        version: DocumentVersion,
        plan: ChunkPlan,
        chunking: ChunkingConfig,
        embedding: EmbeddingConfig,
        vectors: dict[tuple[str, int, int], EmbeddingVector],
        *,
        actor_id: str,
        expected_current_version_id: UUID | None,
    ) -> tuple[Document, DocumentVersion]:
        document, version = _snapshot(document, version)
        if plan.document_version_id != version.id or plan.config != chunking:
            raise ValueError("The prepared chunk plan does not match the selected version/configuration")
        validate_chunk_plan(plan, version.content)
        eligible = tuple(chunk for chunk in plan.chunks if embedding_eligible(chunk))
        identities = {chunk.identity for chunk in eligible}
        if not identities or identities != set(vectors):
            raise ValueError("Complete nonblank chunk embeddings are required before persistence")
        checked_vectors = {identity: storage_vector(vector) for identity, vector in vectors.items()}
        configuration_values(embedding)
        try:
            try:
                return await self._commit_once(
                    document,
                    version,
                    plan,
                    chunking,
                    embedding,
                    checked_vectors,
                    eligible,
                    actor_id,
                    expected_current_version_id,
                )
            except IntegrityError as error:
                constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
                if getattr(error.orig, "sqlstate", None) != "23505" or constraint != _OCCURRENCE_CONSTRAINT:
                    raise
                # A concurrent idempotent request committed the same source
                # occurrence. Resolve it, then complete/reuse that winner.
                winner = await self._resolve_occurrence(document, version)
                actual_document, actual_version = winner
                return await self.commit_indexed(
                    actual_document,
                    actual_version,
                    replace(plan, document_version_id=actual_version.id),
                    chunking,
                    embedding,
                    checked_vectors,
                    actor_id=actor_id,
                    expected_current_version_id=expected_current_version_id,
                )
        except (
            ActivationConflict,
            ChunkPlanConflict,
            ContentHashCollision,
            DocumentUpdateConflict,
            RegistrationConflict,
        ):
            raise
        except SQLAlchemyError as error:
            raise RepositoryError(
                "Indexed document commit failed; retry with the same source identity to resolve its outcome."
            ) from error

    async def _commit_once(
        self,
        document: Document,
        version: DocumentVersion,
        plan: ChunkPlan,
        chunking: ChunkingConfig,
        embedding: EmbeddingConfig,
        vectors: dict[tuple[str, int, int], EmbeddingVector],
        eligible: tuple[Chunk, ...],
        actor_id: str,
        expected_current_version_id: UUID | None,
    ) -> tuple[Document, DocumentVersion]:
        async with self._sessions() as session:
            async with session.begin():
                await session.connection(execution_options={"isolation_level": "READ COMMITTED"})
                document, version = await self._register_or_resolve(
                    session,
                    document,
                    version,
                    actor_id=actor_id,
                    expected_current_version_id=expected_current_version_id,
                )
                plan = replace(plan, document_version_id=version.id)
                validate_chunk_plan(plan, version.content)
                chunking_configuration_id = await self._persist_plan(session, plan)
                await self._persist_embeddings(
                    session,
                    version,
                    chunking_configuration_id,
                    eligible,
                    embedding,
                    vectors,
                )
                await PostgreSQLDocumentActivationRepository.activate_in_session(
                    session,
                    document.id,
                    version.id,
                    chunking,
                    embedding,
                    expected_current_version_id=expected_current_version_id,
                )
                document_row = await session.get(DocumentRow, document.id)
                version_row = await session.get(DocumentVersionRow, version.id)
                if document_row is None or version_row is None:
                    raise RepositoryError("Committed document version could not be resolved")
                result = self._documents._reconstruct(document_row, version_row, version.content)
        return result

    async def _register_or_resolve(
        self,
        session: AsyncSession,
        document: Document,
        version: DocumentVersion,
        *,
        actor_id: str,
        expected_current_version_id: UUID | None,
    ) -> tuple[Document, DocumentVersion]:
        if expected_current_version_id is None:
            if document.owner_id != actor_id or version.version_number != 1:
                raise ValueError("Initial indexing requires version 1 and the authenticated owner")
            existing = await self._documents._existing(session, document, version)
            if existing is not None:
                return existing
            await self._insert_content(session, version)
            document_row = DocumentRow(
                id=document.id,
                owner_id=document.owner_id,
                title=document.title,
                current_version_id=None,
                created_at=document.created_at,
                deleted_at=document.deleted_at,
            )
            session.add(document_row)
            await session.flush()
            version_row = _version_row(version)
            session.add(version_row)
            await session.flush()
            return document, version

        document_row = await session.scalar(select(DocumentRow).where(DocumentRow.id == document.id).with_for_update())
        self._documents._authorize_mutation(document_row, actor_id)
        if document_row.deleted_at is not None:
            raise DocumentUpdateConflict("Deleted documents cannot be updated")
        if not isinstance(version.source.revision, str) or not version.source.revision.strip():
            raise DocumentUpdateConflict("A document update requires an explicit nonblank source revision")
        existing = await self._documents._existing(session, document, version)
        if existing is not None:
            if existing[0].id != document.id:
                raise RegistrationConflict("Source revision is already registered to another document")
            return existing
        if document_row.current_version_id != expected_current_version_id:
            raise DocumentUpdateConflict("The active document version changed; reload before updating")
        if document_row.current_version_id is None:
            raise DocumentUpdateConflict("A document must have an active version before it can be updated")
        active = await session.get(DocumentVersionRow, document_row.current_version_id)
        if active is None or active.document_id != document.id:
            raise RepositoryError("The active document version could not be resolved")
        same_source = await session.scalar(
            select(DocumentVersionRow.id).where(
                DocumentVersionRow.id == active.id,
                DocumentVersionRow.kind == version.source.kind,
                DocumentVersionRow.source_id == version.source.source_id,
                DocumentVersionRow.record_locator == version.source.record_locator,
            )
        )
        if same_source is None:
            raise DocumentUpdateConflict("An update must retain the document's original source identity")
        latest_number = await session.scalar(
            select(DocumentVersionRow.version_number)
            .where(DocumentVersionRow.document_id == document.id)
            .order_by(DocumentVersionRow.version_number.desc())
            .limit(1)
        )
        if latest_number != active.version_number:
            raise DocumentUpdateConflict("Another replacement is pending indexing")
        await self._insert_content(session, version)
        version = replace(version, version_number=latest_number + 1)
        version_row = _version_row(version)
        session.add(version_row)
        await session.flush()
        return document, version

    @staticmethod
    async def _insert_content(session: AsyncSession, version: DocumentVersion) -> None:
        digest = bytes.fromhex(version.content.sha256)
        inserted = await session.scalar(
            pg_insert(ContentRow)
            .values(sha256=digest, text=version.content.text)
            .on_conflict_do_nothing(constraint="pk_contents")
            .returning(ContentRow.sha256)
        )
        if inserted is None:
            row = await session.get(ContentRow, digest)
            if row is None:
                raise RepositoryError("Reusable Content could not be resolved")
            if row.text != version.content.text:
                raise ContentHashCollision("Content hash collision detected")

    async def _persist_plan(self, session: AsyncSession, plan: ChunkPlan) -> int:
        values = _configuration_values(plan.config)
        version_row = await session.get(DocumentVersionRow, plan.document_version_id)
        if version_row is None:
            raise RepositoryError("DocumentVersion was not persisted before its plan")
        content_row = await session.get(ContentRow, version_row.content_sha256)
        if content_row is None:
            raise RepositoryError("DocumentVersion Content could not be resolved")
        content = _content(content_row)
        configuration = await session.scalar(
            select(ChunkingConfigurationRow)
            .where(*(getattr(ChunkingConfigurationRow, key) == value for key, value in values.items()))
            .order_by(ChunkingConfigurationRow.id)
            .limit(1)
        )
        if configuration is None:
            await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": 7_341_902_862})
            configuration = await session.scalar(
                select(ChunkingConfigurationRow)
                .where(*(getattr(ChunkingConfigurationRow, key) == value for key, value in values.items()))
                .order_by(ChunkingConfigurationRow.id)
                .limit(1)
            )
            if configuration is None:
                configuration = ChunkingConfigurationRow(**values)
                session.add(configuration)
                await session.flush()
        found = await PostgreSQLChunkPlanRepository._find_plan(session, plan.document_version_id, values, lock=True)
        if found is None:
            row = VersionChunkPlanRow(
                document_version_id=plan.document_version_id,
                chunking_configuration_id=configuration.id,
                expected_chunk_count=len(plan.chunks),
                created_at=datetime.now(UTC),
            )
            session.add(row)
            await session.flush()
            if plan.chunks:
                await session.execute(
                    insert(ChunkMembershipRow),
                    [
                        dict(
                            document_version_id=plan.document_version_id,
                            chunking_configuration_id=configuration.id,
                            ordinal=index,
                            start=chunk.start,
                            end=chunk.end,
                        )
                        for index, chunk in enumerate(plan.chunks)
                    ],
                )
        else:
            stored = await PostgreSQLChunkPlanRepository._reconstruct(session, *found, content)
            if stored != plan:
                raise ChunkPlanConflict("Chunk plan conflict: ordered ranges differ")
            row, configuration = found
        for chunk in plan.chunks:
            text_value = content.text[chunk.start : chunk.end]
            await session.execute(
                pg_insert(SearchableChunkRow)
                .values(
                    content_sha256=bytes.fromhex(content.sha256),
                    start=chunk.start,
                    end=chunk.end,
                    text=text_value,
                )
                .on_conflict_do_nothing(index_elements=["content_sha256", "start", "end"])
            )
            search_row = await session.get(SearchableChunkRow, (bytes.fromhex(content.sha256), chunk.start, chunk.end))
            if search_row is None or search_row.text != text_value:
                raise RepositoryError("Canonical searchable chunk text conflicts with Content")
        return configuration.id

    async def _persist_embeddings(
        self,
        session: AsyncSession,
        version: DocumentVersion,
        chunking_configuration_id: int,
        eligible: tuple[Chunk, ...],
        config: EmbeddingConfig,
        vectors: dict[tuple[str, int, int], EmbeddingVector],
    ) -> None:
        stored = await find_embeddings(session, eligible, config, lock=True)
        config_id = await configuration_id(session, config)
        for chunk in eligible:
            vector = stored.get(chunk.identity)
            if vector is None:
                vector = vectors[chunk.identity]
                await session.execute(
                    pg_insert(ChunkEmbeddingRow)
                    .values(
                        embedding_configuration_id=config_id,
                        content_sha256=bytes.fromhex(chunk.content_sha256),
                        start=chunk.start,
                        end=chunk.end,
                        embedding=list(vector),
                        created_at=datetime.now(UTC),
                    )
                    .on_conflict_do_nothing(constraint="pk_chunk_embeddings")
                )
        stored = await find_embeddings(session, eligible, config, lock=True)
        if set(stored) != {chunk.identity for chunk in eligible}:
            raise RepositoryError("Legacy embedding coverage is incomplete")
        for chunk in eligible:
            # Eligible chunks retain their ordinal in the full plan, including
            # whitespace-only memberships; resolve it from the persisted range.
            ordinal = await session.scalar(
                select(ChunkMembershipRow.ordinal).where(
                    ChunkMembershipRow.document_version_id == version.id,
                    ChunkMembershipRow.chunking_configuration_id == chunking_configuration_id,
                    ChunkMembershipRow.start == chunk.start,
                    ChunkMembershipRow.end == chunk.end,
                )
            )
            if ordinal is None:
                raise RepositoryError("Persisted membership could not be resolved for its vector")
            vector = stored[chunk.identity]
            await session.execute(
                pg_insert(MembershipChunkEmbeddingRow)
                .values(
                    document_version_id=version.id,
                    chunking_configuration_id=chunking_configuration_id,
                    ordinal=ordinal,
                    embedding_configuration_id=config_id,
                    embedding=list(vector),
                    created_at=datetime.now(UTC),
                )
                .on_conflict_do_nothing(constraint="pk_membership_chunk_embeddings")
            )
            row = await session.get(
                MembershipChunkEmbeddingRow,
                (version.id, chunking_configuration_id, ordinal, config_id),
            )
            if row is None or tuple(storage_vector(tuple(float(value) for value in row.embedding))) != vector:
                raise RepositoryError("Membership-owned and shared embeddings disagree")

    async def _resolve_occurrence(self, document: Document, version: DocumentVersion):
        async with self._sessions() as session:
            async with session.begin():
                found = await self._documents._existing(session, document, version)
                if found is None:
                    raise RepositoryError("Concurrent indexed document winner could not be resolved")
                return found
