"""Passive ASGI/SQLAlchemy taps; payloads go only to the local trace writer."""

import json
from time import perf_counter
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.engine import Engine

from investigation_engine.application.debug_trace import active, emit, enabled, request_trace
from investigation_engine.application.timing import _trace


class DebugTraceMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") not in ("/questions", "/investigations") or not enabled():
            return await self.app(scope, receive, send)
        token = _trace.set(uuid4().hex)
        request_id = _trace.get()
        with request_trace(request_id, scope["path"]):
            try:
                settings = getattr(getattr(scope.get("app"), "state", None), "services", None)
                settings = getattr(settings, "settings", None)
                names = [
                    name
                    for name in dir(settings)
                    if name.startswith(
                        (
                            "retrieval_",
                            "semantic_",
                            "hnsw_",
                            "lexical_",
                            "generation_",
                            "investigation_",
                            "rag_",
                            "serving_chunking_",
                        )
                    )
                    or name == "gemini_generation_model"
                ]
                emit("request.configuration", configuration={name: getattr(settings, name) for name in names})
            except Exception:
                # Optional diagnostic introspection must not affect execution.
                emit("request.configuration_unavailable")

            async def traced_receive():
                message = await receive()
                if message["type"] == "http.request":
                    emit(
                        "http.request.body", body=_body(message.get("body", b"")), more=message.get("more_body", False)
                    )
                return message

            async def traced_send(message):
                if message["type"] == "http.response.start":
                    emit("http.response.status", status=message["status"])
                elif message["type"] == "http.response.body":
                    emit(
                        "http.response.body", body=_body(message.get("body", b"")), more=message.get("more_body", False)
                    )
                await send(message)

            try:
                await self.app(scope, traced_receive, traced_send)
            except BaseException as error:
                emit("request.error", error_type=type(error).__name__)
                raise
            finally:
                _trace.reset(token)


def _body(body):
    try:
        return json.loads(body)
    except ValueError, UnicodeError:
        # Malformed or split transport JSON cannot be reliably credential-redacted.
        # The validated business question is captured separately at the endpoint.
        return {"unparsed_fragment_bytes": len(body)}


@event.listens_for(Engine, "before_cursor_execute")
def _before(connection, cursor, statement, parameters, context, executemany):
    if active():
        context._debug_started = perf_counter()
        emit("db.execute", sql=statement, parameters=parameters, executemany=executemany)


@event.listens_for(Engine, "after_cursor_execute")
def _after(connection, cursor, statement, parameters, context, executemany):
    if active():
        started = getattr(context, "_debug_started", perf_counter())
        emit(
            "db.executed",
            duration_ms=(perf_counter() - started) * 1000,
            row_count=cursor.rowcount,
            scope="driver execution, may include buffered transfer; not server-only",
        )


@event.listens_for(Engine, "handle_error")
def _error(context):
    emit("db.error", error_type=type(context.original_exception).__name__)


@event.listens_for(Engine, "begin")
def _begin(connection):
    emit("db.transaction", operation="begin")


@event.listens_for(Engine, "commit")
def _commit(connection):
    emit("db.transaction", operation="commit")


@event.listens_for(Engine, "rollback")
def _rollback(connection):
    emit("db.transaction", operation="rollback")
