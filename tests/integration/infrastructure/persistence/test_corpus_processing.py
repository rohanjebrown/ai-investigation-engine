import asyncio
import json
from hashlib import sha256

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import text

from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy
from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import (
    PostgreSQLChunkPlanRepository,
)
from investigation_engine.ingestion.corpus_processing import process_corpus


class Characters:
    identity = TokenizerIdentity(
        name="corpus-integration-characters", revision="1", artifact_sha256="0" * 64, runtime_version="test"
    )

    def offsets(self, text):
        return tuple((i, i + 1) for i in range(len(text)))


async def test_cancelled_registration_plan_gap_resumes_in_postgresql_without_duplicate_ids(
    tmp_path,
    db_engine,
    sessions,
    repository,
):
    dataset = tmp_path / "dev.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "_id": "a",
                    "context": json.dumps([["One", ["Same text."]], ["Two", ["Same text."]]]),
                    "question": "SECRET_QUESTION",
                    "answer": "SECRET_GOLD",
                },
                {
                    "_id": "b",
                    "context": json.dumps([["Three", ["Different text."]]]),
                    "question": "SECRET_QUESTION",
                    "answer": "SECRET_GOLD",
                },
            ]
        ),
        dataset,
    )
    tokenizer = Characters()
    config = ChunkingConfig(strategy=ChunkingStrategy.WHOLE_PASSAGE, tokenizer=tokenizer.identity, max_tokens=100)
    plans = PostgreSQLChunkPlanRepository(sessions)
    committed = asyncio.Event()

    class PausedPlans:
        async def save(self, plan):
            committed.set()
            await asyncio.Event().wait()

    checkpoint = tmp_path / "checkpoint.json"
    options = dict(
        checkpoint_path=checkpoint,
        target_identity="this-disposable-database",
        expected_sha256=sha256(dataset.read_bytes()).hexdigest(),
        revision="fixture",
        batch_size=2,
        concurrency=2,
        min_free_bytes=0,
    )
    pending = asyncio.create_task(process_corpus(dataset, repository, PausedPlans(), tokenizer, config, **options))
    await asyncio.wait_for(committed.wait(), timeout=10)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    with db_engine.connect() as connection:
        old_ids = set(connection.scalars(text("SELECT id FROM documents")))
        assert len(old_ids) == 1
        assert connection.scalar(text("SELECT count(*) FROM version_chunk_plans")) == 0
    assert not checkpoint.exists()
    resumed = await process_corpus(dataset, repository, plans, tokenizer, config, **options)
    assert resumed["completed_occurrences"] == 3 and resumed["failure"] is None
    replay = await process_corpus(
        dataset, repository, plans, tokenizer, config, **{**options, "checkpoint_path": tmp_path / "forced-replay.json"}
    )
    assert replay["successful_operations_this_run"] == 3
    with db_engine.connect() as connection:
        assert old_ids <= set(connection.scalars(text("SELECT id FROM documents")))
        assert connection.scalar(text("SELECT count(*) FROM documents")) == 3
        assert connection.scalar(text("SELECT count(*) FROM document_versions")) == 3
        assert connection.scalar(text("SELECT count(*) FROM contents")) == 2
        assert connection.scalar(text("SELECT count(*) FROM version_chunk_plans")) == 3
        assert connection.scalar(text("SELECT count(*) FROM chunk_memberships")) == 3
        assert connection.scalar(text("SELECT count(*) FROM chunk_embeddings")) == 0
        assert connection.scalar(text("SELECT count(*) FROM documents WHERE current_version_id IS NOT NULL")) == 0
        assert connection.scalar(text("SELECT count(*) FROM contents WHERE text LIKE '%SECRET%'")) == 0
