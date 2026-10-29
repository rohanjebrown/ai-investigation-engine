import subprocess
import sys
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
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
        assert set(inspect(connection).get_table_names()) == {
            "documents",
            "document_versions",
            "contents",
            "alembic_version",
        }
        connection.execute(
            text("INSERT INTO contents (sha256,text) VALUES (:digest,:text)"),
            {"digest": sha256(b"retained").digest(), "text": "retained"},
        )
        connection.execute(
            text("INSERT INTO documents (id,owner_id,created_at) VALUES (:id,NULL,:now)"),
            {"id": uuid7(), "now": datetime.now(UTC)},
        )
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


def test_concurrent_upgrade_attempts_are_serialized(migration_database):
    with migration_database.begin() as connection:
        connection.execute(text("CREATE EXTENSION vector"))
    worker_path = Path(__file__).with_name("upgrade_migration_process.py")
    config_path = str(Path(__file__).resolve().parents[3] / "alembic.ini")
    database_url = migration_database.url.render_as_string(hide_password=False)
    with TemporaryDirectory() as directory:
        gate_path = Path(directory)
        start_path = gate_path / "start"
        ready_paths = [gate_path / f"ready_{index}" for index in range(2)]
        processes = [
            subprocess.Popen(
                [sys.executable, str(worker_path), database_url, config_path, str(ready_path), str(start_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for ready_path in ready_paths
        ]
        try:
            deadline = time.monotonic() + 30
            while not all(path.exists() for path in ready_paths):
                exited = [process for process in processes if process.poll() is not None]
                assert not exited, "A migration worker exited before reaching the start gate"
                assert time.monotonic() < deadline, "Migration workers did not reach the start gate"
                time.sleep(0.01)
            start_path.touch()
            outputs = [process.communicate(timeout=60) for process in processes]
            assert [process.returncode for process in processes] == [0, 0], outputs
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                process.communicate()
    with migration_database.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002_embedding_storage"
        assert connection.scalar(text("SELECT count(*) FROM chunking_configurations")) == 0
