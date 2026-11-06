"""Opt-in local payload diagnostics; never exported through production logging."""

import base64
import json
import math
import os
import re
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from time import perf_counter
from uuid import UUID

_current = ContextVar("debug_trace", default=None)
_secret = re.compile(
    r"password|passwd|secret|authorization|credential|api[_-]?key|access[_-]?token|refresh[_-]?token|cookie|thought[_-]?signature|private[_-]?key|^token$|^key$|admin[_-]?api[_-]?token",
    re.I,
)


def enabled():
    return (
        os.environ.get("APP_ENV") == "local"
        and os.environ.get("DEBUG_TRACE_ENABLED", "false").lower() == "true"
        and not any(os.environ.get(key) for key in ("K_SERVICE", "K_REVISION", "CLOUD_RUN_JOB"))
    )


def active():
    return _current.get() is not None


def _safe(value, secrets, seen=None):
    seen = set() if seen is None else seen
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)(bearer\s+)[\w.+=/\-]+", r"\1[REDACTED]", value)
        value = re.sub(r"AIza[\w-]{30,}", "[REDACTED]", value)
        value = re.sub(r"(\w+://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", value)
        return re.sub(
            r"""(?i)((?:api[_-]?key|password|secret|token)["']?\s*[=:]\s*["']?)[^\s"'&,;}]+""",
            r"\1[REDACTED]",
            value,
        )
    if isinstance(value, float) and not math.isfinite(value):
        return {"nonfinite": str(value)}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Enum):
        return _safe(value.value, secrets, seen)
    if isinstance(value, (UUID, date, Path)):
        return str(value)
    if isinstance(value, bytes):
        decoded = value.decode("utf-8", errors="replace")
        if _safe(decoded, secrets) != decoded:
            return "[REDACTED]"
        return {"base64": base64.b64encode(value).decode()}
    if id(value) in seen:
        return "[CYCLE]"
    identity = id(value)
    seen.add(identity)
    try:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="python")
        elif is_dataclass(value) and not isinstance(value, type):
            value = {field.name: getattr(value, field.name) for field in fields(value)}
        if isinstance(value, Mapping):
            return {
                str(k): "[REDACTED]" if _secret.search(str(k)) else _safe(v, secrets, seen) for k, v in value.items()
            }
        if isinstance(value, (tuple, list, set)):
            return [_safe(item, secrets, seen) for item in value]
        # Unknown objects are not repr'd: repr can contain credentials/addresses.
        return {"type": type(value).__name__}
    finally:
        seen.discard(identity)


class _Writer:
    def __init__(self, stream, request_id):
        self.stream, self.request_id = stream, request_id
        self.started, self.sequence = perf_counter(), 0
        self.secrets = sorted({v for k, v in os.environ.items() if _secret.search(k) and v}, key=len, reverse=True)

    def write(self, event, values):
        try:
            self.sequence += 1
            record = {
                "request_id": self.request_id,
                "sequence": self.sequence,
                "timestamp": datetime.now(UTC).isoformat(),
                "elapsed_ms": (perf_counter() - self.started) * 1000,
                "event": event,
                "data": _safe(values, self.secrets),
            }
            self.stream.write(json.dumps(record, ensure_ascii=True, allow_nan=False) + "\n")
            self.stream.flush()
            # Payloads deliberately never enter console/production log handlers.
            print(
                f"Local trace {self.request_id} {event} duration_ms={values.get('duration_ms', '-')}", file=sys.stderr
            )
        except Exception:
            # Diagnostics must never replace the request's result/exception.
            pass


def emit(event, **values):
    writer = _current.get()
    if writer is not None:
        writer.write(event, values)


@contextmanager
def request_trace(request_id, request_type):
    if not enabled():
        yield
        return
    stream = None
    token = None
    try:
        directory = Path(".debug/traces")
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        stream = (directory / f"{stamp}-{request_id}.jsonl").open("x", encoding="utf-8")
        token = _current.set(_Writer(stream, request_id))
    except OSError:
        pass
    try:
        emit("request.start", request_type=request_type)
        yield
    finally:
        emit("request.end")
        if token is not None:
            _current.reset(token)
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


async def provider_call(operation, call, **payload):
    """Submit the same SDK kwargs once; inspect actual objects, never .text."""
    if not active():
        return await call(**payload)
    call_id = _current.get().sequence + 1
    emit("gemini.request", call_id=call_id, operation=operation, payload=payload)
    started = perf_counter()
    try:
        response = await call(**payload)
    except BaseException as error:
        emit(
            "gemini.error",
            call_id=call_id,
            operation=operation,
            error_type=type(error).__name__,
            code=getattr(error, "code", None),
            duration_ms=(perf_counter() - started) * 1000,
        )
        raise
    emit(
        "gemini.response",
        call_id=call_id,
        operation=operation,
        response=response,
        duration_ms=(perf_counter() - started) * 1000,
    )
    return response
