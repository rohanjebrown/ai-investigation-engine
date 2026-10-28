"""Shared embedding identity/read checks inside already-owned transactions."""

from datetime import UTC, datetime

from sqlalchemy import select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from investigation_engine.application.chunking import Chunk
from investigation_engine.application.embeddings import EmbeddingConfig, EmbeddingVector, embedding_eligible, storage_vector
from investigation_engine.application.ports.repository_errors import RepositoryError
from investigation_engine.infrastructure.persistence._document_mapping import _content
from investigation_engine.infrastructure.persistence.models import ChunkEmbeddingRow, ContentRow, EmbeddingConfigurationRow


def configuration_values(config: EmbeddingConfig) -> dict[str, str | int | None]:
    if not isinstance(config, EmbeddingConfig):
        raise ValueError("An EmbeddingConfig is required")
    config.__post_init__()
    return dict(provider=config.provider, model_id=config.model_id, model_revision=config.model_revision,
                dimensions=config.dimensions, document_format=config.document_format, normalization=config.normalization)


def configuration_filter(config: EmbeddingConfig):
    return tuple(getattr(EmbeddingConfigurationRow, key).is_not_distinct_from(value)
                 for key, value in configuration_values(config).items())


async def configuration_id(session: AsyncSession, config: EmbeddingConfig) -> int:
    query = select(EmbeddingConfigurationRow.id).where(*configuration_filter(config)).order_by(EmbeddingConfigurationRow.id).limit(1)
    found = await session.scalar(query)
    if found is not None:
        return found
    # No natural UNIQUE key exists. Serialize rare configuration creation only;
    # this transaction never spans provider IO. Recheck after acquiring the lock.
    await session.execute(text("LOCK TABLE embedding_configurations IN SHARE ROW EXCLUSIVE MODE"))
    found = await session.scalar(query)
    if found is None:
        row = EmbeddingConfigurationRow(**configuration_values(config), created_at=datetime.now(UTC))
        session.add(row)
        await session.flush()
        found = row.id
    return found


async def validate_chunks(session: AsyncSession, chunks: tuple[Chunk, ...]) -> None:
    for chunk in chunks:
        if not isinstance(chunk, Chunk):
            raise ValueError("An exact Chunk is required")
        chunk.__post_init__()
        if not embedding_eligible(chunk):
            raise ValueError("Whitespace-only chunks are not embedding eligible")
    if not chunks:
        return
    rows = (await session.scalars(select(ContentRow).where(
        ContentRow.sha256.in_({bytes.fromhex(c.content_sha256) for c in chunks})
    ))).all()
    try:
        contents = {row.sha256.hex(): _content(row) for row in rows}
    except (TypeError, ValueError) as error:
        raise RepositoryError("Stored embedding Content is invalid") from error
    if any(contents.get(chunk.content_sha256) != chunk.content for chunk in chunks):
        raise ValueError("Embedding chunk must reference exact registered Content")


async def find_embeddings(
    session: AsyncSession, chunks: tuple[Chunk, ...], config: EmbeddingConfig,
) -> dict[tuple[str, int, int], EmbeddingVector]:
    filters = configuration_filter(config)
    await validate_chunks(session, chunks)
    found = {}
    # Bound SQL parameter counts without introducing a batch/job abstraction.
    keys = list(dict.fromkeys((bytes.fromhex(c.content_sha256), c.start, c.end) for c in chunks))
    for offset in range(0, len(keys), 500):
        rows = (await session.scalars(
            select(ChunkEmbeddingRow).join(EmbeddingConfigurationRow)
            .where(*filters, tuple_(ChunkEmbeddingRow.content_sha256, ChunkEmbeddingRow.start,
                                    ChunkEmbeddingRow.end).in_(keys[offset:offset + 500]))
            .order_by(ChunkEmbeddingRow.embedding_configuration_id)
        )).all()
        for row in rows:
            try:
                vector = storage_vector(tuple(float(value) for value in row.embedding))
            except (TypeError, ValueError, OverflowError) as error:
                raise RepositoryError("Stored embedding vector is invalid") from error
            found.setdefault((row.content_sha256.hex(), row.start, row.end), vector)
    return found
