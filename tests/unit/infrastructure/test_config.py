import os
import runpy
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def load_settings(tmp_path, monkeypatch):
    module = Path(__file__).resolve().parents[3] / "src/investigation_engine/infrastructure/config.py"
    monkeypatch.chdir(tmp_path)
    with patch.dict(os.environ, {}, clear=True):
        yield lambda: runpy.run_path(str(module), run_name="investigation_engine.infrastructure.config")["settings"]


@pytest.fixture
def dotenv_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text('DB_HOST=127.0.0.1\nDB_PORT=15432\nDB_NAME=synthetic\nDB_USER=synthetic\nDB_PASSWORD="dummy-${literal}-@secret"\n', encoding="utf-8")
    return path


def test_dotenv_loads_typed_frozen_settings_without_rewriting_secrets(load_settings, dotenv_file):
    settings = load_settings()
    assert settings.db_port == 15432 and type(settings.db_port) is int
    assert settings.gemini_api_key is None
    assert settings.db_password == "dummy-${literal}-@secret"
    assert "dummy-" not in repr(settings)
    assert settings.database_url().password == settings.db_password
    with pytest.raises(FrozenInstanceError):
        settings.db_port = 5432


def test_process_environment_wins_over_dotenv(load_settings, dotenv_file, monkeypatch):
    monkeypatch.setenv("DB_NAME", "process-wins")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key")
    assert load_settings().db_name == "process-wins"


def test_cloud_run_uses_managed_socket_without_tcp_authority(load_settings, dotenv_file, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "existing-service")
    monkeypatch.setenv("DB_CONNECTION_MODE", "cloudsql_socket")
    monkeypatch.setenv("DB_HOST", "/cloudsql/project-123:europe-west3:existing-db")
    monkeypatch.setenv("DB_PORT", "5432")
    url = load_settings().database_url()
    assert url.host is None and url.port is None
    assert url.query == {"host": "/cloudsql/project-123:europe-west3:existing-db",
                         "sslmode": "disable", "connect_timeout": "5"}
    assert url.password == "dummy-${literal}-@secret"


def test_cloud_run_rejects_development_proxy(load_settings, dotenv_file, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "existing-service")
    with pytest.raises(ValueError, match="Cloud Run requires"):
        load_settings()


@pytest.mark.parametrize("name,value", [("DB_POOL_SIZE", "0"), ("DB_MAX_OVERFLOW", "11"),
    ("DB_POOL_TIMEOUT_SECONDS", "31"), ("DB_CONNECT_TIMEOUT_SECONDS", "0"),
    ("DB_STATEMENT_TIMEOUT_MS", "999"), ("DB_CONNECTION_MODE", "tcp")])
def test_database_runtime_bounds(load_settings, dotenv_file, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        load_settings()


@pytest.mark.parametrize("port", ["bad", "0", "65536"])
def test_invalid_ports_fail_without_exposing_values(load_settings, dotenv_file, monkeypatch, port):
    monkeypatch.setenv("DB_PORT", port)
    with pytest.raises(ValueError, match="DB_PORT must be an integer") as caught:
        load_settings()
    assert "dummy-" not in str(caught.value)


def test_required_variables_are_reported_by_name(load_settings):
    with pytest.raises(ValueError, match="Missing required environment variables: DB_HOST, DB_PORT"):
        load_settings()


def test_url_rejects_the_local_postgres_port(load_settings, dotenv_file, monkeypatch):
    monkeypatch.setenv("DB_PORT", "5432")
    with pytest.raises(ValueError, match="Cloud SQL Auth Proxy"):
        load_settings().database_url()


def test_generation_defaults_and_explicit_typed_environment(load_settings, dotenv_file, monkeypatch):
    settings = load_settings()
    assert settings.gemini_generation_model is None and settings.serving_chunking_configuration_id is None
    assert settings.generation_max_attempts == 2 and settings.rag_max_evidence == 5
    monkeypatch.setenv("GEMINI_GENERATION_MODEL", "explicit-test-model")
    monkeypatch.setenv("SERVING_CHUNKING_CONFIGURATION_ID", "7")
    monkeypatch.setenv("RAG_MAX_CONTEXT_CHARS", "16000")
    selected = load_settings()
    assert selected.gemini_generation_model == "explicit-test-model" and selected.serving_chunking_configuration_id == 7
    assert selected.rag_max_context_chars == 16000


def test_embedding_batch_settings_are_typed_and_bounded(load_settings, dotenv_file, monkeypatch):
    initial = load_settings()
    assert initial.gemini_embedding_batch_size == 25
    assert initial.gemini_embedding_batch_token_limit == 32768
    monkeypatch.setenv("GEMINI_EMBEDDING_BATCH_SIZE", "10")
    monkeypatch.setenv("GEMINI_EMBEDDING_BATCH_TOKEN_LIMIT", "16000")
    selected = load_settings()
    assert selected.gemini_embedding_batch_size == 10
    assert selected.gemini_embedding_batch_token_limit == 16000


@pytest.mark.parametrize("name,value", [("GENERATION_MAX_ATTEMPTS", "4"), ("GENERATION_TIMEOUT_SECONDS", "bad"),
    ("GENERATION_MAX_OUTPUT_TOKENS", "0"), ("RAG_MAX_EVIDENCE", "11"),
    ("RAG_MAX_CONTEXT_CHARS", "64001"), ("SERVING_CHUNKING_CONFIGURATION_ID", "0"),
    ("GEMINI_EMBEDDING_BATCH_SIZE", "101"), ("GEMINI_EMBEDDING_BATCH_TOKEN_LIMIT", "0")])
def test_generation_setting_bounds_fail_by_name(load_settings, dotenv_file, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        load_settings()


def test_flat_retrieval_settings_preserve_semantic_default_and_allow_explicit_hybrid(load_settings, dotenv_file, monkeypatch):
    initial = load_settings()
    assert initial.retrieval_mode == "semantic" and initial.rrf_k == 60
    monkeypatch.setenv("RETRIEVAL_MODE", "hybrid")
    monkeypatch.setenv("SEMANTIC_CANDIDATE_LIMIT", "30")
    monkeypatch.setenv("KEYWORD_CANDIDATE_LIMIT", "40")
    monkeypatch.setenv("RRF_K", "0")
    selected = load_settings()
    assert selected.retrieval_mode == "hybrid"
    assert selected.semantic_candidate_limit == 30 and selected.keyword_candidate_limit == 40 and selected.rrf_k == 0


def test_investigation_is_disabled_by_default_and_limits_are_explicitly_typed(load_settings, dotenv_file, monkeypatch):
    initial = load_settings()
    assert initial.investigation_enabled is False
    assert initial.investigation_max_turns == 6 and initial.investigation_max_generation_requests == 6
    monkeypatch.setenv("INVESTIGATION_ENABLED", "true")
    monkeypatch.setenv("INVESTIGATION_MAX_TURNS", "4")
    monkeypatch.setenv("INVESTIGATION_MAX_CONTEXT_CHARS", "18000")
    selected = load_settings()
    assert selected.investigation_enabled is True
    assert selected.investigation_max_turns == 4 and type(selected.investigation_max_turns) is int
    assert selected.investigation_max_context_chars == 18000


@pytest.mark.parametrize("name,value", [("INVESTIGATION_ENABLED", "sometimes"),
    ("INVESTIGATION_MAX_TURNS", "0"), ("INVESTIGATION_MAX_SEARCH_CALLS", "11"),
    ("INVESTIGATION_MAX_CONTEXT_CHARS", "511"), ("INVESTIGATION_MAX_GENERATION_REQUESTS", "13"),
    ("INVESTIGATION_TIMEOUT_SECONDS", "181")])
def test_invalid_investigation_configuration_is_rejected_by_name(load_settings, dotenv_file, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        load_settings()


@pytest.mark.parametrize("name,value", [("RETRIEVAL_MODE","keyword"), ("SEMANTIC_CANDIDATE_LIMIT","101"),
    ("KEYWORD_CANDIDATE_LIMIT","0"), ("RRF_K","-1"), ("RRF_K","1.5")])
def test_invalid_retrieval_configuration_is_rejected_by_name(load_settings, dotenv_file, monkeypatch, name, value):
    monkeypatch.setenv(name,value)
    with pytest.raises(ValueError, match=name):
        load_settings()
