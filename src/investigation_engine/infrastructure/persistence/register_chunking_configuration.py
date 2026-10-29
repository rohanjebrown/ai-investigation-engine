"""Register a serving chunking profile without creating document data."""

import argparse
import asyncio
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import (
    PostgreSQLChunkPlanRepository,
)
from investigation_engine.infrastructure.tokenization.huggingface_tokenizer import (
    VOYAGE_REFERENCE_NAME,
    VOYAGE_REFERENCE_REVISION,
    VOYAGE_REFERENCE_SHA256,
    HuggingFaceTokenizer,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=[strategy.value for strategy in ChunkingStrategy], required=True)
    parser.add_argument("--max-tokens", type=int, required=True)
    parser.add_argument("--overlap", type=int, default=0)
    return parser.parse_args()


async def _register(args: argparse.Namespace) -> int:
    from investigation_engine.infrastructure.config import settings

    if settings.chunking_tokenizer_path is None:
        raise ValueError("CHUNKING_TOKENIZER_PATH must identify the pinned tokenizer artifact")
    tokenizer = HuggingFaceTokenizer(
        settings.chunking_tokenizer_path,
        name=VOYAGE_REFERENCE_NAME,
        revision=VOYAGE_REFERENCE_REVISION,
        expected_sha256=VOYAGE_REFERENCE_SHA256,
    )
    config = ChunkingConfig(
        strategy=ChunkingStrategy(args.strategy),
        tokenizer=tokenizer.identity,
        max_tokens=args.max_tokens,
        overlap=args.overlap,
    )
    engine = create_async_engine(settings.database_url(), poolclass=NullPool, hide_parameters=True)
    try:
        repository = PostgreSQLChunkPlanRepository(async_sessionmaker(engine, expire_on_commit=False))
        return await repository.register_configuration(config)
    finally:
        await engine.dispose()


def main() -> None:
    args = _arguments()
    configuration_id = asyncio.run(
        _register(args),
        loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None,
    )
    print(configuration_id)


if __name__ == "__main__":
    main()
