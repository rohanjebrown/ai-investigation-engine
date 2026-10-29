"""Low-overhead stage timings. Names and elapsed times only; never request data."""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from time import perf_counter
from uuid import uuid4

logger = logging.getLogger(__name__)
_trace = ContextVar("timing_trace", default=None)


@contextmanager
def stage_timing(stage: str):
    token = _trace.set(_trace.get() or uuid4().hex)
    started = perf_counter()
    succeeded = False
    try:
        yield
        succeeded = True
    finally:
        elapsed = (perf_counter() - started) * 1000
        logger.log(
            logging.INFO if succeeded else logging.WARNING,
            "stage_timing trace=%s stage=%s duration_ms=%.3f succeeded=%s",
            _trace.get(),
            stage,
            elapsed,
            succeeded,
            extra={"stage": stage, "duration_ms": elapsed, "succeeded": succeeded, "request_id": _trace.get()},
        )
        _trace.reset(token)


def timed(stage: str):
    """Time an async operation, sharing a trace with its nested stages."""

    def decorate(operation):
        @wraps(operation)
        async def run(*args, **kwargs):
            with stage_timing(stage):
                return await operation(*args, **kwargs)

        return run

    return decorate


def retrieval_completed(
    mode: str, result_count: int, semantic_count: int, lexical_count: int, duration_ms: float
) -> None:
    logger.info(
        "retrieval completed",
        extra={
            "event": "retrieval.completed",
            "stage": "retrieval",
            "request_id": _trace.get(),
            "duration_ms": duration_ms,
            "succeeded": True,
            "mode": mode,
            "result_count": result_count,
            "semantic_count": semantic_count,
            "lexical_count": lexical_count,
        },
    )
