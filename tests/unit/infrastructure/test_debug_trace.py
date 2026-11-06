"""Full tracing uses only synthetic inputs and mocked providers."""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.genai import types
from sqlalchemy import create_engine, text

from investigation_engine.application import debug_trace as trace
from investigation_engine.application.investigations import (
    InvestigateQuestion,
    InvestigationDecision,
    InvestigationStatus,
    SearchEvidenceCall,
)
from investigation_engine.application.timing import log_event
from investigation_engine.infrastructure.api.app import create_app


@pytest.fixture
def local(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DEBUG_TRACE_ENABLED", "true")
    for name in ("K_SERVICE", "K_REVISION", "CLOUD_RUN_JOB"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def events(root):
    return [
        json.loads(line)
        for path in sorted(root.glob(".debug/traces/*.jsonl"))
        for line in path.read_text().splitlines()
    ]


@pytest.mark.parametrize("environment", [None, "staging", "production", "development"])
def test_full_trace_requires_explicit_local(local, monkeypatch, environment):
    if environment is None:
        monkeypatch.delenv("APP_ENV")
    else:
        monkeypatch.setenv("APP_ENV", environment)
    with trace.request_trace("disabled", "question"):
        trace.emit("private", question="private")
    assert events(local) == []


def test_disabled_by_default_and_cloud_run_denied(local, monkeypatch):
    monkeypatch.delenv("DEBUG_TRACE_ENABLED")
    assert not trace.enabled()
    monkeypatch.setenv("DEBUG_TRACE_ENABLED", "true")
    monkeypatch.setenv("K_SERVICE", "stage")
    assert not trace.enabled()


async def test_actual_sdk_function_parts_and_secret_redaction(local, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key-value")
    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                name="search_evidence", args={"query": "a fact"}, id="call-1"
                            )
                        )
                    ]
                )
            )
        ]
    )
    calls = []

    async def provider(**kwargs):
        calls.append(kwargs)
        return response

    with trace.request_trace("sdk", "investigation"):
        assert (
            await trace.provider_call(
                "generate_content",
                provider,
                model="synthetic",
                contents="question",
                config=types.GenerateContentConfig(system_instruction="instructions"),
            )
            is response
        )
        trace.emit(
            "credentials",
            authorization="Bearer hidden",
            password="hidden",
            nested={"api_key": "hidden"},
            content="synthetic-key-value",
            url="postgresql://user:pass@host/db",
        )
    raw = json.dumps(events(local))
    assert "hidden" not in raw and "synthetic-key-value" not in raw and "user:pass" not in raw
    assert "call-1" in raw and "search_evidence" in raw and "a fact" in raw and "instructions" in raw
    assert len(calls) == 1
    assert "a fact" not in capsys.readouterr().err


def test_parameterized_sql_transactions_and_order(local):
    engine = create_engine("sqlite://")
    with trace.request_trace("sql", "question"), engine.begin() as connection:
        result = connection.execute(text("SELECT :value AS value"), {"value": "synthetic vector"})
        assert result.scalar_one() == "synthetic vector"
    rows = events(local)
    assert [x["sequence"] for x in rows] == list(range(1, len(rows) + 1))
    execution = next(x for x in rows if x["event"] == "db.execute")
    assert "?" in execution["data"]["sql"]
    assert execution["data"]["parameters"] == ["synthetic vector"]
    assert [x["data"]["operation"] for x in rows if x["event"] == "db.transaction"] == ["begin", "commit"]
    engine.dispose()


async def test_concurrent_traces_are_separate(local):
    async def run(identity):
        with trace.request_trace(identity, "question"):
            await asyncio.sleep(0)
            trace.emit("payload", question=identity)

    await asyncio.gather(run("first"), run("second"))
    files = list(local.glob(".debug/traces/*.jsonl"))
    assert len(files) == 2
    for file in files:
        records = [json.loads(x) for x in file.read_text().splitlines()]
        assert len({x["request_id"] for x in records}) == 1
        assert next(x for x in records if x["event"] == "payload")["data"]["question"] == records[0]["request_id"]


@pytest.mark.parametrize("failure", ["open", "write", "serialize"])
def test_trace_failures_do_not_change_results(local, monkeypatch, failure):
    if failure == "open":
        monkeypatch.setattr(Path, "open", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk full")))
    elif failure == "serialize":
        monkeypatch.setattr(trace, "_safe", lambda *a, **kw: (_ for _ in ()).throw(ValueError("invalid")))
    with trace.request_trace("failure", "question"):
        if failure == "write" and trace.active():
            trace._current.get().stream.close()
        trace.emit("data", question="question")
        result = 42
    assert result == 42 and not trace.active()


def test_api_contract_and_correlated_response(local, monkeypatch, caplog):
    caplog.set_level("INFO", logger="investigation_engine.application.timing")

    class Questions:
        async def execute(self, question, *, owner_id):
            log_event("synthetic.operation")
            return SimpleNamespace(
                status=InvestigationStatus.INSUFFICIENT_EVIDENCE,
                answer=None,
                citations=(),
                message="Insufficient evidence.",
            )

    @asynccontextmanager
    async def factory():
        yield SimpleNamespace(
            questions=Questions(), settings=SimpleNamespace(semantic_search_backend="hnsw", hnsw_ef_search=400)
        )

    app = create_app(service_factory=factory)
    with TestClient(app) as client:
        on = client.post("/questions", json={"question": "synthetic question"})
        monkeypatch.setenv("DEBUG_TRACE_ENABLED", "false")
        off = client.post("/questions", json={"question": "synthetic question"})
    assert on.status_code == off.status_code == 200 and on.json() == off.json()
    rows = events(local)
    assert len({x["request_id"] for x in rows}) == 1
    request_id = rows[0]["request_id"]
    assert any(getattr(record, "request_id", None) == request_id for record in caplog.records)
    assert any(
        x["event"] == "http.request.body" and x["data"]["body"]["question"] == "synthetic question" for x in rows
    )
    assert any(x["event"] == "http.response.body" and x["data"]["body"] == on.json() for x in rows)
    assert any(
        x["event"] == "request.configuration" and x["data"]["configuration"]["semantic_search_backend"] == "hnsw"
        for x in rows
    )


@pytest.mark.parametrize("repeated", [False, True])
async def test_investigation_records_actual_termination(local, repeated):
    class Model:
        async def next_action(self, turn):
            if repeated:
                return SearchEvidenceCall(query="same", call_id="call")
            return InvestigationDecision(status=InvestigationStatus.INSUFFICIENT_EVIDENCE, answer=None, citations=())

    class Search:
        async def execute(self, *args, **kwargs):
            return ()

    with trace.request_trace("investigation", "investigation"):
        result = await InvestigateQuestion(Search(), Model()).execute("question", owner_id=None)
    rows = events(local)
    completed = next(x["data"] for x in rows if x["event"] == "investigation.completed")
    if repeated:
        assert completed["reason"] == "repeated_query"
    else:
        assert completed["status"] == "insufficient_evidence"
        assert next(x for x in rows if x["event"] == "investigation.decision")["data"]["origin"] == "model"
    assert result.answer is None
