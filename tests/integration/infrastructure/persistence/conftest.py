from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid7

import pytest
from sqlalchemy import insert

from investigation_engine.infrastructure.persistence.models import (
    ChunkingConfigurationRow,
    ContentRow,
    DocumentRow,
    DocumentVersionRow,
    EmbeddingConfigurationRow,
    VersionChunkPlanRow,
)


@pytest.fixture
def chunking_values():
    return dict(
        strategy="fixed_tokens",
        algorithm_version="1",
        tokenizer_name="synthetic",
        tokenizer_revision="1",
        tokenizer_artifact_sha256=b"t" * 32,
        tokenizer_runtime_version="test",
        max_tokens=4,
        overlap=1,
    )


@pytest.fixture
def embedding_values():
    return dict(
        provider="gemini_developer_api",
        model_id="gemini-embedding-2",
        model_revision=None,
        dimensions=768,
        document_format="gemini_no_title_v1",
        normalization="none",
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def derived_rows(db_engine, chunking_values, embedding_values):
    """One exact Content and version/plan, for direct schema-constraint checks."""
    now = datetime.now(UTC)
    document_id, version_id = uuid7(), uuid7()
    content = "A😀 B."
    digest = sha256(content.encode()).digest()
    with db_engine.begin() as connection:
        connection.execute(insert(ContentRow).values(sha256=digest, text=content))
        connection.execute(insert(DocumentRow).values(id=document_id, owner_id=None, created_at=now))
        connection.execute(
            insert(DocumentVersionRow).values(
                id=version_id,
                document_id=document_id,
                version_number=1,
                content_sha256=digest,
                created_at=now,
                kind="dataset",
                source_id="synthetic",
                record_locator={"position": 0},
            )
        )
        chunking_id = connection.scalar(
            insert(ChunkingConfigurationRow).values(**chunking_values).returning(ChunkingConfigurationRow.id)
        )
        embedding_id = connection.scalar(
            insert(EmbeddingConfigurationRow).values(**embedding_values).returning(EmbeddingConfigurationRow.id)
        )
        connection.execute(
            insert(VersionChunkPlanRow).values(
                document_version_id=version_id,
                chunking_configuration_id=chunking_id,
                expected_chunk_count=1,
                created_at=now,
            )
        )
    return dict(
        document_id=document_id,
        version_id=version_id,
        digest=digest,
        chunking_id=chunking_id,
        embedding_id=embedding_id,
        now=now,
    )
