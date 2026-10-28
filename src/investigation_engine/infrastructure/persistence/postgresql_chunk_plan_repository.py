"""Atomic chunk-plan storage using the existing derived tables only."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from investigation_engine.application.chunk_plan_validation import validate_chunk_plan
from investigation_engine.application.chunking import Chunk, ChunkingConfig, ChunkingStrategy, ChunkPlan
from investigation_engine.application.ports.repository_errors import ChunkPlanConflict, RepositoryError
from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
from investigation_engine.domain.content import Content
from investigation_engine.infrastructure.persistence._document_mapping import _content
from investigation_engine.infrastructure.persistence.models import (
    ChunkingConfigurationRow, ChunkMembershipRow, ContentRow, DocumentVersionRow,
    VersionChunkPlanRow,
)


def _configuration_values(config: ChunkingConfig) -> dict[str, str | int | bytes]:
    if not isinstance(config, ChunkingConfig):
        raise ValueError("A ChunkingConfig is required")
    config.__post_init__()
    config.tokenizer.__post_init__()
    if config.algorithm_version != "1":
        raise ValueError("Unsupported chunking algorithm version")
    return dict(
        strategy=config.strategy, algorithm_version=config.algorithm_version,
        tokenizer_name=config.tokenizer.name, tokenizer_revision=config.tokenizer.revision,
        tokenizer_artifact_sha256=bytes.fromhex(config.tokenizer.artifact_sha256),
        tokenizer_runtime_version=config.tokenizer.runtime_version,
        max_tokens=config.max_tokens, overlap=config.overlap,
    )


def _configuration(row: ChunkingConfigurationRow) -> ChunkingConfig:
    config = ChunkingConfig(
        strategy=ChunkingStrategy(row.strategy),
        tokenizer=TokenizerIdentity(
            name=row.tokenizer_name, revision=row.tokenizer_revision,
            artifact_sha256=row.tokenizer_artifact_sha256.hex(),
            runtime_version=row.tokenizer_runtime_version,
        ),
        max_tokens=row.max_tokens, overlap=row.overlap,
    )
    if config.algorithm_version != row.algorithm_version:
        raise ValueError("Unsupported stored chunking algorithm version")
    return config


class PostgreSQLChunkPlanRepository:
    """Repository-owned READ COMMITTED transactions; trusted internal callers.

    Saves lock the authoritative version before configuration/plan lookup. This
    serializes plans for that version without adding a configuration-unique key.
    Other versions may independently create equivalent configuration rows, as
    permitted by the schema. No registration, activation or embedding work occurs.
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def save(self, plan: ChunkPlan) -> ChunkPlan:
        if not isinstance(plan, ChunkPlan) or not isinstance(plan.document_version_id, UUID):
            raise ValueError("ChunkPlan requires a document version UUID")
        values = _configuration_values(plan.config)
        try:
            async with self._sessions() as session:
                async with session.begin():
                    await session.connection(execution_options={"isolation_level": "READ COMMITTED"})
                    version = await session.scalar(
                        select(DocumentVersionRow)
                        .where(DocumentVersionRow.id == plan.document_version_id)
                        .with_for_update()
                    )
                    if version is None:
                        raise ValueError("DocumentVersion does not exist")
                    content = await self._version_content(session, version)
                    validate_chunk_plan(plan, content)
                    existing = await self._find_plan(session, plan.document_version_id, values)
                    if existing is not None:
                        stored = await self._reconstruct(session, *existing, content)
                        if stored != plan:
                            raise ChunkPlanConflict("Chunk plan conflict: ordered ranges differ")
                    else:
                        configuration = await session.scalar(
                            select(ChunkingConfigurationRow).where(*(
                                getattr(ChunkingConfigurationRow, key) == value
                                for key, value in values.items()
                            )).order_by(ChunkingConfigurationRow.id).limit(1)
                        )
                        if configuration is None:
                            configuration = ChunkingConfigurationRow(**values)
                            session.add(configuration)
                            await session.flush()
                        row = VersionChunkPlanRow(
                            document_version_id=plan.document_version_id,
                            chunking_configuration_id=configuration.id,
                            expected_chunk_count=len(plan.chunks), created_at=datetime.now(UTC),
                        )
                        session.add(row)
                        await session.flush()
                        if plan.chunks:
                            await session.execute(insert(ChunkMembershipRow), [
                                dict(document_version_id=plan.document_version_id,
                                     chunking_configuration_id=configuration.id,
                                     ordinal=index, start=chunk.start, end=chunk.end)
                                for index, chunk in enumerate(plan.chunks)
                            ])
                        stored = await self._reconstruct(session, row, configuration, content)
            # Return only after commit and session cleanup.
            return stored
        except SQLAlchemyError as error:
            raise RepositoryError(
                "Chunk plan could not be saved; its commit outcome may be unknown. "
                "Retry using the same version and configuration."
            ) from error

    async def get(self, document_version_id: UUID, config: ChunkingConfig) -> ChunkPlan | None:
        if not isinstance(document_version_id, UUID):
            raise ValueError("A document version UUID is required")
        values = _configuration_values(config)
        try:
            async with self._sessions() as session:
                async with session.begin():
                    await session.connection(execution_options={"isolation_level": "READ COMMITTED"})
                    found = await self._find_plan(session, document_version_id, values)
                    if found is None:
                        result = None
                    else:
                        version = await session.get(DocumentVersionRow, document_version_id)
                        if version is None:
                            raise RepositoryError("Stored chunk plan references a missing version")
                        content = await self._version_content(session, version)
                        result = await self._reconstruct(session, *found, content)
            return result
        except SQLAlchemyError as error:
            raise RepositoryError("Chunk plan could not be retrieved") from error

    @staticmethod
    async def _find_plan(
        session: AsyncSession, version_id: UUID, values: dict[str, str | int | bytes],
    ) -> tuple[VersionChunkPlanRow, ChunkingConfigurationRow] | None:
        found = (await session.execute(
            select(VersionChunkPlanRow, ChunkingConfigurationRow)
            .join(ChunkingConfigurationRow)
            .where(VersionChunkPlanRow.document_version_id == version_id, *(
                getattr(ChunkingConfigurationRow, key) == value
                for key, value in values.items()
            ))
        )).all()
        if len(found) > 1:
            raise RepositoryError("Multiple chunk plans match the version/configuration")
        return (found[0][0], found[0][1]) if found else None

    @staticmethod
    async def _version_content(session: AsyncSession, version: DocumentVersionRow) -> Content:
        row = await session.get(ContentRow, version.content_sha256)
        if row is None:
            raise RepositoryError("DocumentVersion Content could not be resolved")
        try:
            return _content(row)
        except (TypeError, ValueError) as error:
            raise RepositoryError("DocumentVersion Content is invalid") from error

    @staticmethod
    async def _reconstruct(
        session: AsyncSession, row: VersionChunkPlanRow,
        configuration: ChunkingConfigurationRow, content: Content,
    ) -> ChunkPlan:
        members = (await session.scalars(
            select(ChunkMembershipRow).where(
                ChunkMembershipRow.document_version_id == row.document_version_id,
                ChunkMembershipRow.chunking_configuration_id == row.chunking_configuration_id,
            ).order_by(ChunkMembershipRow.ordinal)
        )).all()
        try:
            if len(members) != row.expected_chunk_count or any(
                member.ordinal != index for index, member in enumerate(members)
            ):
                raise ValueError("Incomplete chunk membership count or ordering")
            plan = ChunkPlan(
                document_version_id=row.document_version_id, config=_configuration(configuration),
                chunks=tuple(Chunk(content=content, start=m.start, end=m.end) for m in members),
            )
            validate_chunk_plan(plan, content)
            return plan
        except (TypeError, ValueError) as error:
            raise RepositoryError("Stored chunk plan is invalid or incomplete") from error
