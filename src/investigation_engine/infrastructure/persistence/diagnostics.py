"""Allowlisted repository diagnostics; never serialize SQL or driver messages."""

import re

from sqlalchemy.exc import TimeoutError as PoolTimeoutError

_MESSAGES = {
    "57P01": "Database connection terminated by administrator.",
    "57014": "Database statement canceled or timed out.",
    "53300": "Database connection limit reached.",
    "55P03": "Database lock unavailable.",
    "23505": "Database uniqueness constraint failed.",
    "23503": "Database foreign key constraint failed.",
    "23514": "Database check constraint failed.",
    "40001": "Database serialization failure.",
    "40P01": "Database deadlock detected.",
}


def repository_diagnostic(error: BaseException, operation: str) -> dict:
    pending, visited, chain = [error], set(), []
    while pending and len(chain) < 32:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        chain.append(current)
        for attribute in ("__context__", "__cause__", "orig"):
            nested = getattr(current, attribute, None)
            if isinstance(nested, BaseException):
                pending.append(nested)
    states = [(item, getattr(item, "sqlstate", None)) for item in chain]
    states = [(item, state) for item, state in states if isinstance(state, str) and re.fullmatch(r"[A-Z0-9]{5}", state)]
    underlying, state = states[0] if states else (chain[-1], None)
    message = _MESSAGES.get(state, "Database operation failed; untrusted details withheld.")
    if state and state.startswith("08"):
        message = "Database connection failure."
    if any(isinstance(item, PoolTimeoutError) for item in chain):
        message = "Timed out acquiring a database connection from the pool."
    return {
        "operation": operation,
        "underlying_exception_type": type(underlying).__name__,
        "sqlstate": state,
        "database_message": message,
        "connection_invalidated": any(getattr(item, "connection_invalidated", False) is True for item in chain),
    }
