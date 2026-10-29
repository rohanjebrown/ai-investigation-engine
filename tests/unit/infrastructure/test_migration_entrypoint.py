import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

ENTRYPOINT = Path(__file__).parents[3] / "deploy" / "entrypoint.py"
SPEC = importlib.util.spec_from_file_location("migration_entrypoint", ENTRYPOINT)
entrypoint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(entrypoint)


@pytest.mark.parametrize("migration_state", ["pending", "already_at_head"])
def test_successful_migration_starts_server_with_exec(monkeypatch, migration_state):
    run = Mock(return_value=Mock(returncode=0))
    execv = Mock(side_effect=RuntimeError("exec called"))
    monkeypatch.setattr(entrypoint.subprocess, "run", run)
    monkeypatch.setattr(entrypoint.os, "execv", execv)

    with pytest.raises(RuntimeError, match="exec called"):
        entrypoint.main()

    assert run.call_args.args[0] == [sys.executable, "-m", "alembic", "upgrade", "head"]
    assert execv.call_args.args == (
        sys.executable,
        [sys.executable, "-m", "investigation_engine.infrastructure.api.server"],
    )


def test_migration_failure_exits_without_starting_server(monkeypatch):
    monkeypatch.setattr(entrypoint.subprocess, "run", Mock(return_value=Mock(returncode=1)))
    execv = Mock()
    monkeypatch.setattr(entrypoint.os, "execv", execv)

    with pytest.raises(SystemExit) as result:
        entrypoint.main()

    assert result.value.code == 1
    execv.assert_not_called()


def test_explicit_smoke_mode_skips_database_and_starts_smoke_server(monkeypatch):
    monkeypatch.setattr(entrypoint.sys, "argv", [str(ENTRYPOINT), "--smoke"])
    run = Mock()
    execv = Mock(side_effect=RuntimeError("exec called"))
    monkeypatch.setattr(entrypoint.subprocess, "run", run)
    monkeypatch.setattr(entrypoint.os, "execv", execv)

    with pytest.raises(RuntimeError, match="exec called"):
        entrypoint.main()

    run.assert_not_called()
    assert execv.call_args.args[1][-1] == "--smoke"
