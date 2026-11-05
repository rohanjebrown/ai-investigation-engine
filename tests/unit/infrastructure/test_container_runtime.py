import io
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from investigation_engine.infrastructure.api import server
from investigation_engine.infrastructure.api.bootstrap import _engine_options
from investigation_engine.infrastructure.tokenization import fetch_runtime


def test_explicit_smoke_mode_starts_without_settings_and_disables_operations():
    with TestClient(server.create_app(service_factory=server.smoke_services)) as client:
        for path in ("/health", "/", "/static/app.js", "/static/admin.js", "/static/styles.css"):
            assert client.get(path).status_code == 200
        for path in ("/questions", "/investigations"):
            assert client.post(path, json={"question": "packaging check"}).status_code == 503
        assert client.get("/documents").status_code == 503


def test_entrypoint_reads_port_and_uses_one_worker(monkeypatch):
    run = Mock()
    monkeypatch.setattr(server.uvicorn, "run", run)
    monkeypatch.setenv("PORT", "9091")
    monkeypatch.setattr("sys.argv", ["server", "--smoke"])
    server.main()
    assert run.call_args.kwargs["port"] == 9091
    assert run.call_args.kwargs["host"] == "0.0.0.0"
    assert run.call_args.kwargs["workers"] == 1
    assert run.call_args.kwargs["timeout_graceful_shutdown"] < 10
    assert run.call_args.kwargs["log_config"]["loggers"]["investigation_engine.application.timing"] == {
        "handlers": ["timing_json"],
        "level": "INFO",
        "propagate": False,
    }


@pytest.mark.parametrize("port", ["invalid", "0", "65536"])
def test_invalid_port_fails_before_start(monkeypatch, port):
    monkeypatch.setenv("PORT", port)
    monkeypatch.setattr("sys.argv", ["server", "--smoke"])
    with pytest.raises(SystemExit, match="PORT must"):
        server.main()


def test_pool_is_bounded_and_statements_timeout():
    from types import SimpleNamespace

    options = _engine_options(SimpleNamespace())
    assert (options["pool_size"], options["max_overflow"], options["pool_timeout"]) == (3, 0, 5)
    assert options["connect_args"]["options"] == "-c statement_timeout=30000"


def test_tokenizer_fetch_rejects_unpinned_bytes_without_writing(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch_runtime, "urlopen", lambda *a, **kw: io.BytesIO(b"wrong artifact"))
    destination = tmp_path / "tokenizer.json"
    with pytest.raises(ValueError, match="pinned SHA"):
        fetch_runtime.fetch(destination)
    assert not destination.exists()


def test_tokenizer_fetch_verifies_before_writing(monkeypatch, tmp_path):
    from hashlib import sha256

    content = b'{"synthetic":"tokenizer fixture"}'
    monkeypatch.setattr(fetch_runtime, "urlopen", lambda *a, **kw: io.BytesIO(content))
    monkeypatch.setattr(fetch_runtime, "VOYAGE_REFERENCE_SHA256", sha256(content).hexdigest())
    destination = tmp_path / "nested" / "tokenizer.json"
    fetch_runtime.fetch(destination)
    assert destination.read_bytes() == content


def test_smoke_import_and_lifespan_need_no_database_environment(tmp_path):
    import os
    import subprocess
    import sys

    env = {key: value for key, value in os.environ.items() if not key.startswith(("DB_", "GEMINI_", "ADMIN_"))}
    program = """
from fastapi.testclient import TestClient
from investigation_engine.infrastructure.api.server import create_app, smoke_services
with TestClient(create_app(service_factory=smoke_services)) as client:
    assert client.get('/health').status_code == 200
    assert client.post('/questions', json={'question':'smoke'}).status_code == 503
print('isolated smoke passed')
"""
    result = subprocess.run(
        [sys.executable, "-c", program], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr
    assert "isolated smoke passed" in result.stdout
