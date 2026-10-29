"""Short, committed, first-writer-wins embedding operations."""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from investigation_engine.application.chunking import Chunk
from investigation_engine.application.embeddings import EmbeddingConfig, storage_vector
from investigation_engine.application.ports.embedding_provider import EmbeddingVector
from investigation_engine.application.ports.repository_errors import RepositoryError
from investigation_engine.infrastructure.persistence._embedding_mapping import (
    configuration_id,
    configuration_values,
    find_embeddings,
)
from investigation_engine.infrastructure.persistence.models import ChunkEmbeddingRow


class PostgreSQLEmbeddingRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def find(
        self,
        chunks: Sequence[Chunk],
        config: EmbeddingConfig,
    ) -> dict[tuple[str, int, int], EmbeddingVector]:
        chunks = tuple(chunks)
        configuration_values(config)
        try:
            async with self._sessions() as session:
                async with session.begin():
                    await session.connection(execution_options={"isolation_level": "READ COMMITTED"})
                    found = await find_embeddings(session, chunks, config)
            return found
        except SQLAlchemyError as error:
            raise RepositoryError("Embeddings could not be read") from error

    async def save(self, chunk: Chunk, config: EmbeddingConfig, vector: EmbeddingVector) -> EmbeddingVector:
        vector = storage_vector(vector)
        configuration_values(config)
        try:
            async with self._sessions() as session:
                async with session.begin():
                    await session.connection(execution_options={"isolation_level": "READ COMMITTED"})
                    existing = await find_embeddings(session, (chunk,), config)
                    if chunk.identity not in existing:
                        key = await configuration_id(session, config)
                        await session.execute(
                            insert(ChunkEmbeddingRow)
                            .values(
                                embedding_configuration_id=key,
                                content_sha256=bytes.fromhex(chunk.content_sha256),
                                start=chunk.start,
                                end=chunk.end,
                                embedding=list(vector),
                                created_at=datetime.now(UTC),
                            )
                            .on_conflict_do_nothing(constraint="pk_chunk_embeddings")
                        )
                        existing = await find_embeddings(session, (chunk,), config)
                    if chunk.identity not in existing:
                        raise RepositoryError("Committed embedding winner could not be resolved")
                    stored = existing[chunk.identity]
            return stored
        except SQLAlchemyError as error:
            raise RepositoryError(
                "Embedding save failed; commit outcome may be unknown. Retry the same chunk/configuration."
            ) from error
