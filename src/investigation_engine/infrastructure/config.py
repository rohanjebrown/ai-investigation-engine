"""Shared process settings, with optional .env loading from the working directory."""

import os
import re
import math
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
    db_connection_mode: str = "proxy"
    db_pool_size: int = 3
    db_max_overflow: int = 0
    db_pool_timeout_seconds: int = 5
    db_connect_timeout_seconds: int = 5
    db_statement_timeout_ms: int = 30000
    gemini_api_key: str | None = field(default=None, repr=False)
    gemini_embedding_batch_size: int = 25
    gemini_embedding_batch_token_limit: int = 32768
    gemini_generation_model: str | None = None
    serving_chunking_configuration_id: int | None = None
    generation_timeout_seconds: int = 30
    generation_max_attempts: int = 2
    generation_max_output_tokens: int = 1024
    generation_temperature: float | None = None
    rag_max_evidence: int = 5
    rag_max_context_chars: int = 12000
    investigation_enabled: bool = False
    investigation_max_turns: int = 6
    investigation_max_search_calls: int = 5
    investigation_max_results_per_search: int = 5
    investigation_max_accumulated_evidence: int = 15
    investigation_max_context_chars: int = 24000
    investigation_max_generation_requests: int = 6
    investigation_max_embedding_requests: int = 5
    investigation_max_output_tokens: int = 1024
    investigation_timeout_seconds: int = 120
    retrieval_mode: str = "semantic"
    semantic_candidate_limit: int = 20
    keyword_candidate_limit: int = 20
    rrf_k: int = 60
    admin_api_token: str | None = field(default=None, repr=False)
    admin_principal_id: str = "administrator"
    admin_require_https: bool = True
    max_upload_bytes: int = 10 * 1024 * 1024
    max_extracted_text_chars: int = 1_000_000
    max_docx_uncompressed_bytes: int = 50 * 1024 * 1024
    max_pdf_pages: int = 500
    max_index_embeddings_per_request: int = 2
    chunking_tokenizer_path: Path | None = Path("artifacts/tokenizers/voyage-4/tokenizer.json")

    def database_url(self) -> URL:
        """Both modes use an authenticated Cloud SQL proxy/attachment for TLS."""
        if self.db_connection_mode == "cloudsql_socket":
            if self.db_port != 5432 or re.fullmatch(
                r"/cloudsql/[a-z][a-z0-9-]+:[a-z][a-z0-9-]+:[a-z][a-z0-9-]+", self.db_host
            ) is None:
                raise ValueError("Cloud SQL socket mode requires /cloudsql/PROJECT:REGION:INSTANCE and DB_PORT=5432")
            return URL.create("postgresql+psycopg", username=self.db_user,
                password=self.db_password, database=self.db_name,
                query={"host": self.db_host, "sslmode": "disable",
                       "connect_timeout": str(self.db_connect_timeout_seconds)})
        if self.db_connection_mode != "proxy":
            raise ValueError("DB_CONNECTION_MODE must be proxy or cloudsql_socket")
        if (self.db_host, self.db_port) != ("127.0.0.1", 15432):
            raise ValueError("Expected the Cloud SQL Auth Proxy at 127.0.0.1:15432")
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={"sslmode": "disable", "connect_timeout": str(self.db_connect_timeout_seconds)},
        )


def _integer_setting(name: str, default: int | None, low: int, high: int) -> int | None:
    raw = os.environ.get(name)
    if raw is None or (raw == "" and default is None):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer between {low} and {high}") from None
    if not low <= value <= high:
        raise ValueError(f"{name} must be an integer between {low} and {high}")
    return value


def _boolean_setting(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().casefold()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be a boolean value")


def _temperature_setting() -> float | None:
    raw = os.environ.get("GENERATION_TEMPERATURE")
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except ValueError:
        raise ValueError("GENERATION_TEMPERATURE must be between 0 and 1") from None
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("GENERATION_TEMPERATURE must be between 0 and 1")
    return value


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
    mode = os.environ.get("RETRIEVAL_MODE", "semantic")
    if mode not in ("semantic", "hybrid"):
        raise ValueError("RETRIEVAL_MODE must be semantic or hybrid")
    connection_mode = os.environ.get("DB_CONNECTION_MODE", "proxy")
    if connection_mode not in ("proxy", "cloudsql_socket"):
        raise ValueError("DB_CONNECTION_MODE must be proxy or cloudsql_socket")
    if os.environ.get("K_SERVICE") and connection_mode != "cloudsql_socket":
        raise ValueError("Cloud Run requires DB_CONNECTION_MODE=cloudsql_socket; a development loopback proxy is not a production connection")

    admin_token = os.environ.get("ADMIN_API_TOKEN") or None
    if admin_token is not None and (len(admin_token) < 32 or not admin_token.isascii()):
        raise ValueError("ADMIN_API_TOKEN must be an ASCII token of at least 32 characters")
    admin_principal = os.environ.get("ADMIN_PRINCIPAL_ID", "administrator")
    if not admin_principal.strip():
        raise ValueError("ADMIN_PRINCIPAL_ID must be nonblank")
    tokenizer_path_value = os.environ.get("CHUNKING_TOKENIZER_PATH", "artifacts/tokenizers/voyage-4/tokenizer.json")

    return Settings(
        db_host=values["DB_HOST"],
        db_port=port,
        db_name=values["DB_NAME"],
        db_user=values["DB_USER"],
        db_password=values["DB_PASSWORD"],
        db_connection_mode=connection_mode,
        db_pool_size=_integer_setting("DB_POOL_SIZE", 3, 1, 20),
        db_max_overflow=_integer_setting("DB_MAX_OVERFLOW", 0, 0, 10),
        db_pool_timeout_seconds=_integer_setting("DB_POOL_TIMEOUT_SECONDS", 5, 1, 30),
        db_connect_timeout_seconds=_integer_setting("DB_CONNECT_TIMEOUT_SECONDS", 5, 1, 30),
        db_statement_timeout_ms=_integer_setting("DB_STATEMENT_TIMEOUT_MS", 30000, 1000, 120000),
        # Database-only commands do not require an embedding API credential.
        gemini_api_key=os.environ.get("GEMINI_API_KEY") or None,
        gemini_embedding_batch_size=_integer_setting("GEMINI_EMBEDDING_BATCH_SIZE", 25, 1, 100),
        gemini_embedding_batch_token_limit=_integer_setting("GEMINI_EMBEDDING_BATCH_TOKEN_LIMIT", 32768, 1, 262144),
        gemini_generation_model=os.environ.get("GEMINI_GENERATION_MODEL") or None,
        serving_chunking_configuration_id=_integer_setting("SERVING_CHUNKING_CONFIGURATION_ID", None, 1, 2147483647),
        generation_timeout_seconds=_integer_setting("GENERATION_TIMEOUT_SECONDS", 30, 1, 120),
        generation_max_attempts=_integer_setting("GENERATION_MAX_ATTEMPTS", 2, 1, 3),
        generation_max_output_tokens=_integer_setting("GENERATION_MAX_OUTPUT_TOKENS", 1024, 128, 4096),
        generation_temperature=_temperature_setting(),
        rag_max_evidence=_integer_setting("RAG_MAX_EVIDENCE", 5, 1, 10),
        rag_max_context_chars=_integer_setting("RAG_MAX_CONTEXT_CHARS", 12000, 256, 64000),
        investigation_enabled=_boolean_setting("INVESTIGATION_ENABLED", False),
        investigation_max_turns=_integer_setting("INVESTIGATION_MAX_TURNS", 6, 1, 12),
        investigation_max_search_calls=_integer_setting("INVESTIGATION_MAX_SEARCH_CALLS", 5, 1, 10),
        investigation_max_results_per_search=_integer_setting("INVESTIGATION_MAX_RESULTS_PER_SEARCH", 5, 1, 10),
        investigation_max_accumulated_evidence=_integer_setting("INVESTIGATION_MAX_ACCUMULATED_EVIDENCE", 15, 1, 30),
        investigation_max_context_chars=_integer_setting("INVESTIGATION_MAX_CONTEXT_CHARS", 24000, 512, 64000),
        investigation_max_generation_requests=_integer_setting("INVESTIGATION_MAX_GENERATION_REQUESTS", 6, 1, 12),
        investigation_max_embedding_requests=_integer_setting("INVESTIGATION_MAX_EMBEDDING_REQUESTS", 5, 1, 12),
        investigation_max_output_tokens=_integer_setting("INVESTIGATION_MAX_OUTPUT_TOKENS", 1024, 128, 4096),
        investigation_timeout_seconds=_integer_setting("INVESTIGATION_TIMEOUT_SECONDS", 120, 1, 180),
        retrieval_mode=mode,
        semantic_candidate_limit=_integer_setting("SEMANTIC_CANDIDATE_LIMIT", 20, 1, 100),
        keyword_candidate_limit=_integer_setting("KEYWORD_CANDIDATE_LIMIT", 20, 1, 100),
        rrf_k=_integer_setting("RRF_K", 60, 0, 10000),
        admin_api_token=admin_token,
        admin_principal_id=admin_principal,
        admin_require_https=_boolean_setting("ADMIN_REQUIRE_HTTPS", True),
        max_upload_bytes=_integer_setting("MAX_UPLOAD_BYTES", 10 * 1024 * 1024, 1024, 50 * 1024 * 1024),
        max_extracted_text_chars=_integer_setting("MAX_EXTRACTED_TEXT_CHARS", 1_000_000, 1, 5_000_000),
        max_docx_uncompressed_bytes=_integer_setting(
            "MAX_DOCX_UNCOMPRESSED_BYTES", 50 * 1024 * 1024, 1024 * 1024, 500 * 1024 * 1024,
        ),
        max_pdf_pages=_integer_setting("MAX_PDF_PAGES", 500, 1, 10_000),
        max_index_embeddings_per_request=_integer_setting(
            "MAX_INDEX_EMBEDDINGS_PER_REQUEST", 2, 1, 10,
        ),
        chunking_tokenizer_path=Path(tokenizer_path_value) if tokenizer_path_value else None,
    )


settings = _load_settings()
