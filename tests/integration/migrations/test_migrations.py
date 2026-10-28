from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid7

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from investigation_engine.infrastructure.persistence.models import Base


def test_full_chain_downgrade_and_reupgrade_preserve_authoritative_data(migration_database, migrate):
    engine = migration_database
    migrate(engine, "0001_document_registration")
    with engine.begin() as connection:
        assert set(inspect(connection).get_table_names()) == {"documents", "document_versions", "contents", "alembic_version"}
        connection.execute(text("INSERT INTO contents (sha256,text) VALUES (:digest,:text)"), {"digest": sha256(b"retained").digest(), "text": "retained"})
        connection.execute(text("INSERT INTO documents (id,owner_id,created_at) VALUES (:id,NULL,:now)"), {"id": uuid7(), "now": datetime.now(UTC)})
        connection.execute(text("CREATE EXTENSION vector"))
    migrate(engine)
    with engine.connect() as connection:
        assert not compare_metadata(MigrationContext.configure(connection), Base.metadata)
    migrate(engine, "0001_document_registration", downgrade=True)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT text FROM contents")) == "retained"
        assert connection.scalar(text("SELECT count(*) FROM documents")) == 1
        assert connection.scalar(text("SELECT extversion FROM pg_extension WHERE extname='vector'")) == "0.8.5"
        assert connection.scalar(text("SELECT to_regclass('chunk_embeddings')")) is None
    migrate(engine)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002_embedding_storage"
        assert not compare_metadata(MigrationContext.configure(connection), Base.metadata)


def test_missing_pgvector_fails_without_partial_tables_or_installing_extension(migration_database, migrate):
    engine = migration_database
    migrate(engine, "0001_document_registration")
    with pytest.raises(DBAPIError, match="pgvector must already be installed and visible"):
        migrate(engine)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0001_document_registration"
        assert connection.scalar(text("SELECT to_regclass('chunking_configurations')")) is None
        assert connection.scalar(text("SELECT count(*) FROM pg_extension WHERE extname='vector'")) == 0


def test_concurrent_upgrade_attempts_are_serialized(migration_database, migrate):
    with migration_database.begin() as connection:
        connection.execute(text("CREATE EXTENSION vector"))
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: migrate(migration_database), range(2)))
    assert results == [None, None]
    with migration_database.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002_embedding_storage"
        assert connection.scalar(text("SELECT count(*) FROM chunking_configurations")) == 0
