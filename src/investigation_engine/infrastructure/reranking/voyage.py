"""Async Voyage REST adapter with a process-lifetime request ceiling and no retries."""

import asyncio
import hashlib
import json
import logging
from collections.abc import Sequence
from math import isfinite
from time import perf_counter

import httpx

from investigation_engine.application.debug_trace import emit
from investigation_engine.application.ports.evidence_reranker import RerankedEvidence, RerankingError
from investigation_engine.application.retrieval import Evidence
from investigation_engine.application.timing import log_event

ENDPOINT = "https://api.voyageai.com/v1/rerank"


class VoyageReranker:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str = "rerank-3",
        timeout_seconds: float = 10.0,
        max_requests: int = 10,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if model != "rerank-3":
            raise ValueError("RERANKER_MODEL must be rerank-3")
        if not isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60:
            raise ValueError("Reranking timeout must be between 0 and 60 seconds")
        if type(max_requests) is not int or not 0 <= max_requests <= 100:
            raise ValueError("Reranking request budget must be between 0 and 100")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._remaining = max_requests
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False)
        self._owns_client = client is None
        # Bounded by max_requests. Cache safe failures too: no retry of uncertain charges.
        self._cache: dict[str, tuple[tuple[int, float], ...] | str] = {}
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def rerank(self, query: str, candidates: Sequence[Evidence]) -> tuple[RerankedEvidence, ...]:
        emit("rerank.configuration", model=self._model, remaining_requests=self._remaining)
        if not candidates:
            return ()
        documents = [f"{hit.title}\n\n{hit.text}" if hit.title else hit.text for hit in candidates]
        fingerprint = hashlib.sha256(json.dumps([query, documents], ensure_ascii=False).encode()).hexdigest()
        # Serialize the small optional budget, including concurrent duplicate requests.
        async with self._lock:
            cached = fingerprint in self._cache
            if cached:
                outcome = self._cache[fingerprint]
            else:
                if not self._api_key:
                    raise RerankingError("missing_credentials")
                if self._remaining == 0:
                    raise RerankingError("request_budget_exhausted")
                self._remaining -= 1  # Reserve before I/O; failures may still be billable.
                self._cache[fingerprint] = "cancelled_request"
                try:
                    outcome = await self._request(query, documents)
                except RerankingError as error:
                    outcome = str(error)
                self._cache[fingerprint] = outcome
        emit("rerank.cache", model=self._model, cached=cached)
        if isinstance(outcome, str):
            raise RerankingError(outcome)
        return tuple(RerankedEvidence(candidates[index], score) for index, score in outcome)

    async def _request(self, query: str, documents: list[str]) -> tuple[tuple[int, float], ...]:
        started = perf_counter()
        # Only title/passage text and query are user data in this request.
        payload = {"query": query, "documents": documents, "model": self._model, "truncation": False}
        emit("voyage.request", **payload)
        reason = None
        usage = None
        try:
            async with asyncio.timeout(self._timeout):
                response = await self._client.post(
                    ENDPOINT,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                    timeout=self._timeout,
                    follow_redirects=False,
                )
            if response.status_code != 200:
                raise RerankingError(f"http_{response.status_code}")
            try:
                body = response.json()
            except ValueError:
                raise RerankingError("invalid_json") from None
            if isinstance(body, dict):
                raw_usage = body.get("usage")
                if isinstance(raw_usage, dict) and type(raw_usage.get("total_tokens")) is int:
                    if raw_usage["total_tokens"] >= 0:
                        usage = raw_usage["total_tokens"]
            # Record safe usage even if ranking validation fails. Never log response bodies.
            return _parse_response(body, len(documents), self._model)
        except asyncio.CancelledError:
            reason = "cancelled"
            raise
        except TimeoutError, httpx.TimeoutException:
            reason = "timeout"
            raise RerankingError(reason) from None
        except httpx.RequestError:
            reason = "transport_error"
            raise RerankingError(reason) from None
        except RerankingError as error:
            reason = str(error)
            raise
        finally:
            duration_ms = (perf_counter() - started) * 1000
            emit("voyage.response", model=self._model, duration_ms=duration_ms, total_tokens=usage, error=reason)
            log_event(
                "rerank.provider_completed",
                level=logging.WARNING if reason else logging.INFO,
                model_id=self._model,
                duration_ms=duration_ms,
                total_tokens=usage,
                reason=reason,
                succeeded=reason is None,
            )


def _parse_response(body: object, count: int, model: str) -> tuple[tuple[int, float], ...]:
    if not isinstance(body, dict) or body.get("object") != "list" or body.get("model") != model:
        raise RerankingError("invalid_response")
    usage = body.get("usage")
    if not isinstance(usage, dict) or type(usage.get("total_tokens")) is not int or usage["total_tokens"] < 0:
        raise RerankingError("invalid_usage")
    rows = body.get("data")
    if not isinstance(rows, list) or len(rows) != count:
        raise RerankingError("incomplete_results")
    seen = set()
    parsed = []
    for row in rows:
        if not isinstance(row, dict):
            raise RerankingError("invalid_result")
        index, score = row.get("index"), row.get("relevance_score")
        if type(index) is not int or not 0 <= index < count or index in seen:
            raise RerankingError("invalid_indexes")
        if type(score) not in (int, float) or not 0 <= score <= 1 or not isfinite(score):
            raise RerankingError("invalid_score")
        seen.add(index)
        parsed.append((index, float(score)))
    # Deterministic ties retain input order, independent of provider row order.
    return tuple(sorted(parsed, key=lambda item: (-item[1], item[0])))
