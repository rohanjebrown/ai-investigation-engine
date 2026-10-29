"""Real disposable-backend disconnects; no injected retries or production access."""

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from investigation_engine.application.ports.repository_errors import RepositoryError
from investigation_engine.infrastructure.api.bootstrap import _engine_options
from investigation_engine.infrastructure.persistence.models import ContentRow, DocumentRow
from investigation_engine.infrastructure.persistence.postgresql_document_repository import PostgreSQLDocumentRepository


async def test_existing_api_pre_ping_replaces_a_dead_idle_connection(db_engine, registration):
    engine = create_async_engine(db_engine.url, **_engine_options(object()))
    try:
        async with engine.connect() as connection:
            backend = await connection.scalar(text("SELECT pg_backend_pid()"))
        with db_engine.begin() as admin:
            assert admin.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": backend})
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        repository = PostgreSQLDocumentRepository(sessions)
        document, version = await repository.register(*registration())
        retry = await repository.register(*registration())
        assert (retry[0].id, retry[1].id) == (document.id, version.id)
    finally:
        await engine.dispose()


async def test_mid_transaction_disconnect_preserves_cause_rolls_back_and_allows_explicit_replay(
    db_engine,
    registration,
):
    engine = create_async_engine(db_engine.url, **_engine_options(object()))
    repository = PostgreSQLDocumentRepository(async_sessionmaker(engine, expire_on_commit=False))
    interrupted = False

    def disconnect(connection, cursor, statement, parameters, context, executemany):
        nonlocal interrupted
        if not interrupted and statement.startswith("INSERT INTO documents"):
            interrupted = True
            cursor.execute("SELECT pg_backend_pid()")
            backend = cursor.fetchone()[0]
            with db_engine.begin() as admin:
                admin.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": backend})

    event.listen(engine.sync_engine, "before_cursor_execute", disconnect)
    try:
        with pytest.raises(RepositoryError) as caught:
            await repository.register(*registration())
        assert interrupted
        assert caught.value.__cause__.connection_invalidated
        assert caught.value.__cause__.orig is not None
        with db_engine.connect() as connection:
            for table in (DocumentRow, ContentRow):
                assert connection.scalar(select(func.count()).select_from(table)) == 0
        stored = await repository.register(*registration())
        repeated = await repository.register(*registration())
        assert (stored[0].id, stored[1].id) == (repeated[0].id, repeated[1].id)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", disconnect)
        await engine.dispose()
