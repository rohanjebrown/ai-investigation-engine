"""Explicitly verify active document versions for one stored profile pair."""

import argparse
import asyncio
import json
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy
from investigation_engine.application.embeddings import EmbeddingConfig
from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
from investigation_engine.infrastructure.persistence.models import ChunkingConfigurationRow, EmbeddingConfigurationRow
from investigation_engine.infrastructure.persistence.postgresql_document_activation_repository import (
    PostgreSQLDocumentActivationRepository,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunking-configuration-id", type=int, required=True)
    parser.add_argument("--embedding-configuration-id", type=int, required=True)
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> dict[str, int]:
    from investigation_engine.infrastructure.config import settings

    engine = create_async_engine(settings.database_url(), poolclass=NullPool, hide_parameters=True)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            chunk_row = await session.get(ChunkingConfigurationRow, args.chunking_configuration_id)
            embedding_row = await session.get(EmbeddingConfigurationRow, args.embedding_configuration_id)
        if chunk_row is None or embedding_row is None:
            raise ValueError("Both configuration IDs must exist in the database")
        chunking = ChunkingConfig(
            strategy=ChunkingStrategy(chunk_row.strategy),
            tokenizer=TokenizerIdentity(
                name=chunk_row.tokenizer_name,
                revision=chunk_row.tokenizer_revision,
                artifact_sha256=chunk_row.tokenizer_artifact_sha256.hex(),
                runtime_version=chunk_row.tokenizer_runtime_version,
            ),
            max_tokens=chunk_row.max_tokens,
            overlap=chunk_row.overlap,
        )
        embedding = EmbeddingConfig(
            provider=embedding_row.provider,
            model_id=embedding_row.model_id,
            model_revision=embedding_row.model_revision,
            dimensions=embedding_row.dimensions,
            document_format=embedding_row.document_format,
            normalization=embedding_row.normalization,
        )
        counts = {"verified": 0, "skipped": 0, "failed": 0}
        backfill = PostgreSQLDocumentActivationRepository(sessions).backfill_active(chunking, embedding)
        async for result in backfill:
            print(json.dumps(result), flush=True)
            counts[result["status"]] += 1
        return counts
    finally:
        await engine.dispose()


def main() -> None:
    args = _arguments()
    counts = asyncio.run(
        _run(args),
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None,
    )
    print(json.dumps({"summary": counts}))


if __name__ == "__main__":
    main()
