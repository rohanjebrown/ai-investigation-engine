"""Shared process settings, with optional .env loading from the working directory."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL


@dataclass(frozen=True, slots=True, kw_only=True)
class Settings:
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str = field(repr=False)
    gemini_api_key: str | None = field(default=None, repr=False)

    def database_url(self) -> URL:
        """Use the approved local proxy, whose remote connection uses TLS."""
        if (self.db_host, self.db_port) != ("127.0.0.1", 15432):
            raise ValueError("Expected the Cloud SQL Auth Proxy at 127.0.0.1:15432")
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={"sslmode": "disable"},
        )


def _load_settings() -> Settings:
    """Load local defaults, preserving existing process environment values."""
    load_dotenv(Path.cwd() / ".env", override=False, interpolate=False)
    required = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
    values = {name: os.environ.get(name, "") for name in required}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError("Missing required environment variables: " + ", ".join(missing))

    try:
        port = int(values["DB_PORT"])
    except ValueError:
        raise ValueError("DB_PORT must be an integer between 1 and 65535") from None
    if not 1 <= port <= 65535:
        raise ValueError("DB_PORT must be an integer between 1 and 65535")

    return Settings(
        db_host=values["DB_HOST"],
        db_port=port,
        db_name=values["DB_NAME"],
        db_user=values["DB_USER"],
        db_password=values["DB_PASSWORD"],
        # Database-only commands do not require an embedding API credential.
        gemini_api_key=os.environ.get("GEMINI_API_KEY") or None,
    )


settings = _load_settings()
