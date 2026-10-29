"""All database URLs originate here, exclusively from a disposable container."""

from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer

from investigation_engine.infrastructure.persistence.postgresql_document_repository import PostgreSQLDocumentRepository

# Build deploy/Dockerfile.postgres-test; this tag exists only on the CI/local daemon.
POSTGRES_IMAGE = "investigation-postgres-test:local"


@pytest.fixture(scope="session")
def postgres_url():
    container = None
    try:
        startup_error = None
        try:
            container = PostgresContainer(
                POSTGRES_IMAGE,
                username="engine_tests",
                password="disposable-test-password",
                dbname="engine_tests",
                driver="psycopg",
                docker_client_kw={"timeout": 10},
            )
            container.with_command("postgres -c shared_preload_libraries=pg_textsearch")
            container.start()
        except Exception as error:
            startup_error = type(error).__name__
        if startup_error is not None:
            pytest.fail(
                f"Docker/Testcontainers could not start the isolated database ({startup_error}). "
                "Start Docker and retry. Application settings and DATABASE_URL are never used.",
                pytrace=False,
            )
        url = URL.create(
            "postgresql+psycopg",
            username=container.username,
            password=container.password,
            host=container.get_container_host_ip(),
            port=int(container.get_exposed_port(5432)),
            database=container.dbname,
        )
        assert url.port not in (5432, 15432), "Refusing a normal application database port"
        yield url
    finally:
        if container is not None:
            container.stop()


@pytest.fixture(scope="session")
def migrate(pytestconfig):
    def run(engine, revision="head", *, downgrade=False):
        cfg = Config(str(pytestconfig.rootpath / "alembic.ini"))
        cfg.set_main_option("script_location", str(pytestconfig.rootpath / "migrations"))
        with engine.begin() as connection:
            cfg.attributes["connection"] = connection
            operation = command.downgrade if downgrade else command.upgrade
            operation(cfg, revision)

    return run


@pytest.fixture(scope="session")
def schema_engine(postgres_url, migrate):
    engine = create_engine(postgres_url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            # Provision only the disposable fixture database, never inside migrations.
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_textsearch"))
            assert connection.dialect.server_version_info[0] == 18
            assert connection.scalar(text("SELECT extversion FROM pg_extension WHERE extname='vector'")) == "0.8.5"
        migrate(engine)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_engine(schema_engine):
    def clean():
        with schema_engine.begin() as connection:
            # No other engine/URL can reach this cleanup; every test sees real commits.
            connection.execute(
                text(
                    "TRUNCATE searchable_chunks, chunk_embeddings, chunk_memberships, version_chunk_plans, "
                    "embedding_configurations, chunking_configurations, document_versions, "
                    "documents, contents RESTART IDENTITY CASCADE"
                )
            )

    clean()
    try:
        yield schema_engine
    finally:
        clean()


@pytest_asyncio.fixture
async def sessions(db_engine):
    engine = create_async_engine(db_engine.url, poolclass=NullPool, hide_parameters=True)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
def repository(sessions):
    return PostgreSQLDocumentRepository(sessions)


@pytest.fixture
def migration_database(postgres_url):
    """Schema-changing tests get a separate database within the same container."""
    name = "migration_" + uuid4().hex
    admin = create_engine(postgres_url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(postgres_url.set(database=name), hide_parameters=True)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()
