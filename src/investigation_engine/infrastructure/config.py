"""Shared process settings, with optional .env loading from the working directory."""

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL

DB_CONNECTION_MODE_PROXY = "proxy"
MIN_ADMIN_API_TOKEN_LENGTH = 32


@dataclass(frozen=True, slots=True, kw_only=True)
class Settings:
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str = field(repr=False)
    db_connection_mode: str = DB_CONNECTION_MODE_PROXY
    db_pool_size: int = 3
    db_max_overflow: int = 0
    db_pool_timeout_seconds: int = 5
    db_connect_timeout_seconds: int = 5
    db_statement_timeout_ms: int = 30000
    gemini_api_key: str | None = field(default=None, repr=False)
    gemini_embedding_batch_size: int = 25
    gemini_embedding_batch_token_limit: int = 32768
    embedding_max_attempts: int = 3
    embedding_retry_base_delay_seconds: float = 1.0
    embedding_retry_max_delay_seconds: float = 30.0
    embedding_request_timeout_seconds: float = 60.0
    gemini_generation_model: str | None = None
    investigation_model_provider: str = "gemini"
    gemma4_retry_max_attempts: int = 5
    gemma4_retry_total_timeout_seconds: int | None = None
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
    retrieval_embedding_backend: str = "legacy_shared"
    semantic_search_backend: str = "exact"
    hnsw_ef_search: int = 400
    hnsw_candidate_limits: tuple[int, ...] = (400,)
    hnsw_iterative_scan: bool = False
    lexical_search_backend: str = "postgres_fts"
    semantic_candidate_limit: int = 20
    keyword_candidate_limit: int = 20
    rrf_k: int = 60
    reranker_backend: str = "none"
    reranker_model: str = "rerank-3"
    voyage_api_key: str | None = field(default=None, repr=False)
    reranker_timeout_seconds: float = 10.0
    reranker_max_requests: int = 10
    admin_api_token: str | None = field(default=None, repr=False)
    admin_principal_id: str = "administrator"
    admin_require_https: bool = True
    max_upload_bytes: int = 10 * 1024 * 1024
    max_extracted_text_chars: int = 1_000_000
    max_docx_uncompressed_bytes: int = 50 * 1024 * 1024
    max_pdf_pages: int = 500
    max_index_embeddings_per_request: int = 2
    chunking_tokenizer_path: Path | None = Path("artifacts/tokenizers/voyage-4/tokenizer.json")

    otel_enabled: bool = False
    otel_endpoint: str = "http://127.0.0.1:4318/v1/traces"

    def database_url(self) -> URL:
        """Both modes use an authenticated Cloud SQL proxy/attachment for TLS."""
        if self.db_connection_mode == "cloudsql_socket":
            if (
                self.db_port != 5432
                or re.fullmatch(r"/cloudsql/[a-z][a-z0-9-]+:[a-z][a-z0-9-]+:[a-z][a-z0-9-]+", self.db_host) is None
            ):
                raise ValueError("Cloud SQL socket mode requires /cloudsql/PROJECT:REGION:INSTANCE and DB_PORT=5432")
            return URL.create(
                "postgresql+psycopg",
                username=self.db_user,
                password=self.db_password,
                database=self.db_name,
                query={
                    "host": self.db_host,
                    "sslmode": "disable",
                    "connect_timeout": str(self.db_connect_timeout_seconds),
                },
            )
        if self.db_connection_mode != DB_CONNECTION_MODE_PROXY:
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
        parsed_integer = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer between {low} and {high}") from None
    if not low <= parsed_integer <= high:
        raise ValueError(f"{name} must be an integer between {low} and {high}")
    return parsed_integer


def _integer_list_setting(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        values = tuple(int(value.strip()) for value in raw.split(","))
    except ValueError:
        raise ValueError(f"{name} must be a comma-separated list of increasing positive integers") from None
    if not values or any(value < 1 or value > 100000 for value in values) or tuple(sorted(set(values))) != values:
        raise ValueError(f"{name} must be a comma-separated list of increasing positive integers")
    return values


def _boolean_setting(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    boolean_text = raw.strip().casefold()
    if boolean_text in ("1", "true", "yes", "on"):
        return True
    if boolean_text in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be a boolean value")


def _float_setting(name: str, default: float, low: float, high: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        parsed = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number between {low} and {high}") from None
    if not math.isfinite(parsed) or not low <= parsed <= high:
        raise ValueError(f"{name} must be a number between {low} and {high}")
    return parsed


def _temperature_setting() -> float | None:
    raw = os.environ.get("GENERATION_TEMPERATURE")
    if raw is None or raw == "":
        return None
    try:
        temperature = float(raw)
    except ValueError:
        raise ValueError("GENERATION_TEMPERATURE must be between 0 and 1") from None
    if not math.isfinite(temperature) or not 0 <= temperature <= 1:
        raise ValueError("GENERATION_TEMPERATURE must be between 0 and 1")
    return temperature


def _investigation_model_provider() -> str:
    raw = os.environ.get("INVESTIGATION_MODEL_PROVIDER", "gemini").strip().casefold()
    if raw not in {"gemini", "gemma4"}:
        raise ValueError("INVESTIGATION_MODEL_PROVIDER must be gemini or gemma4")
    return raw


def _load_settings() -> Settings:
    """Load local defaults, preserving existing process environment values."""
    load_dotenv(Path.cwd() / ".env", override=False, interpolate=False)
    required = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
    required_values = {name: os.environ.get(name, "") for name in required}
    missing = [name for name, environment_value in required_values.items() if not environment_value]
    if missing:
        missing_names = ", ".join(missing)
        raise ValueError(f"Missing required environment variables: {missing_names}")

    try:
        port = int(required_values["DB_PORT"])
    except ValueError:
        raise ValueError("DB_PORT must be an integer between 1 and 65535") from None
    if not 1 <= port <= 65535:
        raise ValueError("DB_PORT must be an integer between 1 and 65535")
    mode = os.environ.get("RETRIEVAL_MODE", "semantic")
    if mode not in ("semantic", "hybrid"):
        raise ValueError("RETRIEVAL_MODE must be semantic or hybrid")
    lexical_backend = os.environ.get("LEXICAL_SEARCH_BACKEND", "postgres_fts")
    reranker_backend = os.environ.get("RERANKER_BACKEND", "none")
    if reranker_backend not in ("none", "voyage"):
        raise ValueError("RERANKER_BACKEND must be none or voyage")
    reranker_model = os.environ.get("RERANKER_MODEL", "rerank-3")
    if reranker_model != "rerank-3":
        raise ValueError("RERANKER_MODEL must be rerank-3")
    if lexical_backend not in ("postgres_fts", "bm25"):
        raise ValueError("LEXICAL_SEARCH_BACKEND must be postgres_fts or bm25")
    retrieval_embedding_backend = os.environ.get("RETRIEVAL_EMBEDDING_BACKEND", "legacy_shared")
    if retrieval_embedding_backend not in ("legacy_shared", "membership_owned"):
        raise ValueError("RETRIEVAL_EMBEDDING_BACKEND must be legacy_shared or membership_owned")
    semantic_search_backend = os.environ.get("SEMANTIC_SEARCH_BACKEND", "exact")
    if semantic_search_backend not in ("exact", "hnsw"):
        raise ValueError("SEMANTIC_SEARCH_BACKEND must be exact or hnsw")
    connection_mode = os.environ.get("DB_CONNECTION_MODE", DB_CONNECTION_MODE_PROXY)
    if connection_mode not in (DB_CONNECTION_MODE_PROXY, "cloudsql_socket"):
        raise ValueError("DB_CONNECTION_MODE must be proxy or cloudsql_socket")
    if os.environ.get("K_SERVICE") and connection_mode != "cloudsql_socket":
        raise ValueError(
            "Cloud Run requires DB_CONNECTION_MODE=cloudsql_socket; a development loopback proxy is not a production connection"
        )

    admin_token = os.environ.get("ADMIN_API_TOKEN") or None
    has_valid_admin_token = admin_token is None or (
        len(admin_token) >= MIN_ADMIN_API_TOKEN_LENGTH and admin_token.isascii()
    )
    if not has_valid_admin_token:
        raise ValueError("ADMIN_API_TOKEN must be an ASCII token of at least 32 characters")
    admin_principal = os.environ.get("ADMIN_PRINCIPAL_ID", "administrator")
    if not admin_principal.strip():
        raise ValueError("ADMIN_PRINCIPAL_ID must be nonblank")
    tokenizer_path_value = os.environ.get("CHUNKING_TOKENIZER_PATH", "artifacts/tokenizers/voyage-4/tokenizer.json")

    return Settings(
        db_host=required_values["DB_HOST"],
        db_port=port,
        db_name=required_values["DB_NAME"],
        db_user=required_values["DB_USER"],
        db_password=required_values["DB_PASSWORD"],
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
        embedding_max_attempts=_integer_setting("EMBEDDING_MAX_ATTEMPTS", 3, 1, 5),
        embedding_retry_base_delay_seconds=_float_setting("EMBEDDING_RETRY_BASE_DELAY_SECONDS", 1.0, 0.0, 30.0),
        embedding_retry_max_delay_seconds=_float_setting("EMBEDDING_RETRY_MAX_DELAY_SECONDS", 30.0, 0.0, 120.0),
        embedding_request_timeout_seconds=_float_setting("EMBEDDING_REQUEST_TIMEOUT_SECONDS", 60.0, 1.0, 180.0),
        gemini_generation_model=os.environ.get("GEMINI_GENERATION_MODEL") or None,
        investigation_model_provider=_investigation_model_provider(),
        gemma4_retry_max_attempts=_integer_setting("GEMMA4_RETRY_MAX_ATTEMPTS", 5, 1, 10),
        gemma4_retry_total_timeout_seconds=_integer_setting("GEMMA4_RETRY_TOTAL_TIMEOUT_SECONDS", None, 1, 120),
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
        retrieval_embedding_backend=retrieval_embedding_backend,
        semantic_search_backend=semantic_search_backend,
        hnsw_ef_search=_integer_setting("HNSW_EF_SEARCH", 400, 1, 1000),
        hnsw_candidate_limits=_integer_list_setting("HNSW_CANDIDATE_LIMITS", (400,)),
        hnsw_iterative_scan=_boolean_setting("HNSW_ITERATIVE_SCAN", False),
        lexical_search_backend=lexical_backend,
        semantic_candidate_limit=_integer_setting("SEMANTIC_CANDIDATE_LIMIT", 20, 1, 100),
        keyword_candidate_limit=_integer_setting("KEYWORD_CANDIDATE_LIMIT", 20, 1, 100),
        rrf_k=_integer_setting("RRF_K", 60, 0, 10000),
        reranker_backend=reranker_backend,
        reranker_model=reranker_model,
        voyage_api_key=os.environ.get("VOYAGE_API_KEY") or None,
        reranker_timeout_seconds=_float_setting("RERANKER_TIMEOUT_SECONDS", 10.0, 0.1, 60.0),
        reranker_max_requests=_integer_setting("RERANKER_MAX_REQUESTS", 10, 0, 100),
        admin_api_token=admin_token,
        admin_principal_id=admin_principal,
        admin_require_https=_boolean_setting("ADMIN_REQUIRE_HTTPS", True),
        max_upload_bytes=_integer_setting(
            "MAX_UPLOAD_BYTES",
            10 * 1024 * 1024,
            1024,
            50 * 1024 * 1024,
        ),
        max_extracted_text_chars=_integer_setting("MAX_EXTRACTED_TEXT_CHARS", 1_000_000, 1, 5_000_000),
        max_docx_uncompressed_bytes=_integer_setting(
            "MAX_DOCX_UNCOMPRESSED_BYTES",
            50 * 1024 * 1024,
            1024 * 1024,
            500 * 1024 * 1024,
        ),
        max_pdf_pages=_integer_setting("MAX_PDF_PAGES", 500, 1, 10_000),
        max_index_embeddings_per_request=_integer_setting(
            "MAX_INDEX_EMBEDDINGS_PER_REQUEST",
            2,
            1,
            10,
        ),
        chunking_tokenizer_path=Path(tokenizer_path_value) if tokenizer_path_value else None,
        otel_enabled=_boolean_setting("OTEL_ENABLED", False),
        otel_endpoint=os.environ.get(
            "OTEL_ENDPOINT",
            "http://127.0.0.1:4318/v1/traces",
        ),
    )


settings = _load_settings()
