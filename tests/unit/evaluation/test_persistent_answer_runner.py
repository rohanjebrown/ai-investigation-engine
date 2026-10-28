"""Provider-free preflight checks for checkpoint and quota refusal."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def runner():
    path = Path("scripts/evaluate_persistent_answer_quality.py")
    spec = importlib.util.spec_from_file_location("persistent_answer_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_evidence_only_continuation_is_refused_before_any_external_work(runner, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Rejected checkpoint must not open a database or provider")
    monkeypatch.setattr(runner, "database_url", forbidden)
    monkeypatch.setattr(runner, "_read_json", forbidden)
    with pytest.raises(RuntimeError, match="no native Gemini conversation state"):
        await runner.run(SimpleNamespace(continue_pending_id="saved-case"))


async def test_known_twenty_requests_block_generation_before_database_or_key_access(runner, monkeypatch):
    monkeypatch.setattr(runner, "database_url", lambda: pytest.fail("No database should be opened"))
    monkeypatch.setattr(runner, "_read_json", lambda path: pytest.fail("No artifacts should be changed"))
    args = SimpleNamespace(continue_pending_id=None, daily_rpd_limit=20, reserve_rpd=1,
                           prior_single_step_requests=6, prior_multi_step_requests=14)
    with pytest.raises(RuntimeError, match="no allowance"):
        await runner.run(args)
