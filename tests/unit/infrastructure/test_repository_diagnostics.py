import json

import pytest
from sqlalchemy.exc import OperationalError, TimeoutError

from investigation_engine.application.ports.repository_errors import RepositoryError
from investigation_engine.infrastructure.persistence.diagnostics import repository_diagnostic


class DriverFailure(RuntimeError):
    sqlstate = "57P01"


@pytest.mark.parametrize("state", ["57P01", "08006", "57014", "23514", "99999", "unsafe-state-secret"])
def test_chained_diagnostics_keep_classes_and_safe_states_but_no_driver_payload(state):
    driver = DriverFailure("password=secret private-document-text SQL parameters")
    driver.sqlstate = state
    sql = OperationalError("INSERT sensitive SQL", {"secret": "key"}, driver, connection_invalidated=True)
    error = RepositoryError("untrusted repository message")
    error.__cause__ = sql
    result = repository_diagnostic(error, "register_document")
    assert result["operation"] == "register_document"
    assert result["underlying_exception_type"] == "DriverFailure"
    assert result["connection_invalidated"] is True
    assert result["sqlstate"] == (state if len(state) == 5 else None)
    serialized = json.dumps(result)
    for secret in (
        "password",
        "private-document",
        "INSERT",
        "parameters",
        "untrusted repository",
        "unsafe-state-secret",
    ):
        assert secret not in serialized


def test_pool_timeout_and_cyclic_exception_chains_are_bounded():
    error = RepositoryError("secret")
    timeout = TimeoutError("secret connection string")
    error.__cause__ = timeout
    timeout.__context__ = error
    result = repository_diagnostic(error, "save_chunk_plan")
    assert result["sqlstate"] is None
    assert result["database_message"] == "Timed out acquiring a database connection from the pool."
