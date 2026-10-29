"""Low-overhead stage timings. Names and elapsed times only; never request data."""

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from time import perf_counter
from uuid import uuid4

from investigation_engine.application.ports.embedding_errors import (
    EmbeddingProviderError,
    EmbeddingRateLimitError,
    EmbeddingResponseError,
)
from investigation_engine.application.ports.generation_errors import GenerationProviderError, GenerationResponseError
from investigation_engine.application.ports.repository_errors import RepositoryError

logger = logging.getLogger(__name__)
_trace = ContextVar("timing_trace", default=None)


def log_event(event: str, *, level: int = logging.INFO, **fields) -> None:
    """Emit caller-selected, non-sensitive operational values on the shared trace."""
    logger.log(
        level,
        event,
        extra={
            "event": event,
            "stage": fields.pop("operation", event),
            "request_id": _trace.get(),
            "duration_ms": fields.pop("duration_ms", None),
            "succeeded": fields.pop("succeeded", True),
            **fields,
        },
    )


@contextmanager
def stage_timing(stage: str):
    token = _trace.set(_trace.get() or uuid4().hex)
    started = perf_counter()
    succeeded = False
    error_category = None
    try:
        yield
        succeeded = True
    except Exception as error:
        if isinstance(error, RepositoryError):
            error_category = "repository"
        elif isinstance(error, (EmbeddingProviderError, EmbeddingRateLimitError, GenerationProviderError)):
            error_category = "provider"
        elif isinstance(error, (EmbeddingResponseError, GenerationResponseError)):
            error_category = "invalid_response"
        else:
            error_category = "internal"
        raise
    finally:
        elapsed = (perf_counter() - started) * 1000
        level = logging.INFO if stage.endswith(".total") or stage == "http.request" else logging.DEBUG
        logger.log(
            level if succeeded else logging.WARNING,
            "stage_timing trace=%s stage=%s duration_ms=%.3f succeeded=%s",
            _trace.get(),
            stage,
            elapsed,
            succeeded,
            extra={
                "stage": stage,
                "duration_ms": elapsed,
                "succeeded": succeeded,
                "request_id": _trace.get(),
                "error_category": error_category,
            },
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
