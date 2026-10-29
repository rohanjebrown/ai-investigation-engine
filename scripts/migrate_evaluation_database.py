"""Apply this repository's migrations to the dedicated local evaluation DB only."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[1]
DATABASE = "ai_investigation_evaluation"
USER = "evaluation_engine"
PORT = 55433


def main() -> None:
    values = dotenv_values(ROOT / ".env.evaluation")
    password = values.get("EVALUATION_DB_PASSWORD")
    if not password:
        raise SystemExit("Set EVALUATION_DB_PASSWORD in the ignored .env.evaluation file")
    url = URL.create(
        "postgresql+psycopg",
        username=USER,
        password=password,
        host="127.0.0.1",
        port=PORT,
        database=DATABASE,
        query={"connect_timeout": "5"},
    )
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            version = connection.scalar(text("SHOW server_version_num"))
            if not str(version).startswith("180"):
                raise RuntimeError("Evaluation database must be PostgreSQL 18")
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            print(f"PostgreSQL {connection.scalar(text('SHOW server_version'))}; pgvector installed")
        config = Config(str(ROOT / "alembic.ini"))
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        with engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            print(f"Application migrations applied: {revision}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
