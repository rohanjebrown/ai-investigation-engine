"""Observe existing application timers and repository calls without payload logging."""

import logging
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
from time import perf_counter

from sqlalchemy import event

_current = ContextVar("evaluation_observation", default=None)


class Observation:
    def __init__(self):
        self.stages = defaultdict(list)
        self.repository_ms = []
        self.channels = []
        self.sql = []
        self.generation = []
        self.embeddings = []
        self.investigation_searches = []
        self.investigation_turns = []

    def timings(self):
        def total(*names):
            found = [value for name in names for value in self.stages[name]]
            return sum(found) if found else None

        return {
            "query_embedding_ms": total("query_embedding"),
            "connection_acquisition_ms": total("connection_acquisition"),
            "database_setup_ms": sum(item["duration_ms"] for item in self.sql if item["kind"] == "setup"),
            "sql_observed_ms": sum(item["duration_ms"] for item in self.sql if item["kind"] == "retrieval"),
            "repository_ms": sum(self.repository_ms),
            "rrf_ms": total("hybrid_fusion"),
            "evidence_selection_ms": total("evidence_selection"),
            "total_retrieval_ms": total("retrieval.total", "retrieval.lexical_total"),
            "generation_ms": [item["duration_ms"] for item in self.generation],
            "postgres_explain_execution_ms": None,
            "stages_inclusive_ms": dict(self.stages),
            "sql_operations": self.sql,
            "semantics": "Inclusive boundaries overlap. Never sum stages to reconstruct total request time. SQL is driver-observed, not EXPLAIN server time.",
        }


class _TimingHandler(logging.Handler):
    def emit(self, record):
        observation = _current.get()
        if observation is not None and getattr(record, "stage", None) and record.msg.startswith("stage_timing"):
            observation.stages[record.stage].append(record.duration_ms)


@contextmanager
def capture_timings():
    logger = logging.getLogger("investigation_engine.application.timing")
    handler, previous = _TimingHandler(), logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


@contextmanager
def observe():
    observation = Observation()
    token = _current.set(observation)
    try:
        yield observation
    finally:
        _current.reset(token)


@contextmanager
def observe_sql(engine):
    """Never retain statement text, bind parameters or exception messages."""

    def before(connection, cursor, statement, parameters, context, executemany):
        context._evaluation_started = perf_counter()
        context._evaluation_setup = "set_config(" in statement.lower()

    def finished(context, succeeded):
        observation = _current.get()
        started = getattr(context, "_evaluation_started", None)
        if observation is not None and started is not None:
            observation.sql.append(
                {
                    "kind": "setup" if context._evaluation_setup else "retrieval",
                    "duration_ms": (perf_counter() - started) * 1000,
                    "succeeded": succeeded,
                }
            )

    def after(connection, cursor, statement, parameters, context, executemany):
        finished(context, True)

    def failed(exception_context):
        finished(exception_context.execution_context, False)

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    event.listen(engine, "handle_error", failed)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "after_cursor_execute", after)
        event.remove(engine, "handle_error", failed)


class ObservedRepository:
    def __init__(self, repository):
        self.repository = repository

    async def _call(self, method, *args, **kwargs):
        observation, started = _current.get(), perf_counter()
        item = {"semantic": (), "lexical": (), "status": "failed"}
        if observation is not None:
            observation.channels.append(item)
        try:
            result = await getattr(self.repository, method)(*args, **kwargs)
            if method == "search_channels":
                item["semantic"], item["lexical"] = result
            else:
                item["semantic" if method == "search" else "lexical"] = result
            item["status"] = "ok"
            return result
        finally:
            if observation is not None:
                observation.repository_ms.append((perf_counter() - started) * 1000)

    async def search(self, *args, **kwargs):
        return await self._call("search", *args, **kwargs)

    async def search_keywords(self, *args, **kwargs):
        return await self._call("search_keywords", *args, **kwargs)

    async def search_channels(self, *args, **kwargs):
        return await self._call("search_channels", *args, **kwargs)


class RequestBudgetExceeded(RuntimeError):
    pass


class RequestBudget:
    def __init__(self, maximum):
        if type(maximum) is not int or maximum < 1:
            raise ValueError("An explicit positive live request cap is required")
        self.maximum, self.used = maximum, 0

    def consume(self):
        if self.used >= self.maximum:
            raise RequestBudgetExceeded("Live generation request cap reached")
        self.used += 1

    def require(self, count):
        if self.used + count > self.maximum:
            raise RequestBudgetExceeded("Live provider request cap reached")


class ObservedGenerator:
    """Underlying adapters must be configured for one attempt, including SDK retries."""

    def __init__(self, provider, budget):
        self.provider, self.budget = provider, budget

    async def _call(self, method, value):
        self.budget.consume()
        before_requests = getattr(self.provider, "provider_requests", None)
        item = {"status": "failed", "usage": None}
        observation, started = _current.get(), perf_counter()
        api_stages = ("gemini.investigation.api_request",)
        before_stages = {key: len(observation.stages[key]) for key in api_stages} if observation else {}
        if observation is not None:
            observation.generation.append(item)
        try:
            result = await getattr(self.provider, method)(value)
            usage = getattr(result, "usage", None)
            item.update(
                status="ok",
                usage=asdict(usage) if is_dataclass(usage) else None,
                model_version=getattr(result, "model_version", None),
            )
            return result
        finally:
            item["duration_ms"] = (perf_counter() - started) * 1000
            api_values = (
                [value for key, count in before_stages.items() for value in observation.stages[key][count:]]
                if observation
                else []
            )
            item["api_duration_ms"] = sum(api_values) if api_values else None
            after_requests = getattr(self.provider, "provider_requests", None)
            item["provider_requests"] = (
                after_requests - before_requests
                if type(before_requests) is int and type(after_requests) is int
                else None
            )

    async def generate(self, request):
        return await self._call("generate", request)

    async def next_action(self, turn):
        return await self._call("next_action", turn)
