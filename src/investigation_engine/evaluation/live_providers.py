"""Evaluation-only provider limits and pacing; ordinary adapters still submit requests."""

import asyncio
import json
from collections import deque
from contextvars import ContextVar
from time import monotonic

from investigation_engine.evaluation.telemetry import RequestBudget
from investigation_engine.infrastructure.embeddings.gemini_provider import GeminiEmbeddingProvider


class ProviderPacer:
    """Rolling RPM/TPM reservations shared by all jobs in a sequential run.

    Token reservations are conservative input bounds, not reported token usage.
    Other processes using the same project are outside this limiter's scope.
    """

    def __init__(self, rpm, tpm, *, clock=monotonic, sleep=asyncio.sleep):
        if type(rpm) is not int or rpm < 1 or type(tpm) is not int or tpm < 1:
            raise ValueError("Positive integer RPM and TPM limits are required")
        self.rpm, self.tpm, self.clock, self.sleep = rpm, tpm, clock, sleep
        self.requests = deque()
        self.last = None

    async def wait(self, tokens=0):
        if not 0 <= tokens <= self.tpm:
            raise ValueError("Single request exceeds the configured token-per-minute reservation")
        while True:
            now = self.clock()
            while self.requests and now - self.requests[0][0] >= 60:
                self.requests.popleft()
            delay = max(0, 60 / self.rpm - (now - self.last)) if self.last is not None else 0
            if len(self.requests) >= self.rpm or sum(item[1] for item in self.requests) + tokens > self.tpm:
                delay = max(delay, 60 - (now - self.requests[0][0]))
            if delay <= 0:
                self.requests.append((now, tokens))
                self.last = now
                return
            await self.sleep(delay)


class BudgetedGeminiEmbeddingProvider(GeminiEmbeddingProvider):
    """Same query formatting/vector parser; cap includes countTokens and embedContent."""

    def __init__(self, *, api_key, maximum_requests, pacer):
        self.budget = RequestBudget(maximum_requests)
        self.pacer = pacer
        self.query_tokens = 0
        super().__init__(api_key=api_key)

    async def _count_tokens(self, content):
        self.query_tokens = 0
        self.query_tokens = await super()._count_tokens(content)
        return self.query_tokens

    async def _request(self, request, *, operation):
        try:
            self.budget.require(1)
            await self.pacer.wait(self.query_tokens if operation == "embedding generation" else 0)
            self.budget.consume()
        except BaseException:
            # The adapter creates the coroutine before handing it to _request.
            # Close an unsubmitted request when pacing/caps cancel it.
            request.close()
            raise
        return await super()._request(request, operation=operation)


class PacedInvestigationModel:
    """Pass the original turn to the original Gemini model without changing its payload."""

    def __init__(self, provider, pacer):
        self.provider, self.pacer = provider, pacer
        self._current_reservation = ContextVar("investigation_pacer_reservation", default=None)

    @property
    def provider_requests(self):
        return self.provider.provider_requests

    def reservation(self, turn):
        from investigation_engine.infrastructure.generation.gemini_investigation_model import (
            FINAL_SCHEMA,
            SYSTEM_INSTRUCTION,
            TOOL_DECLARATION,
        )

        # _contents is the adapter's side-effect-free native transcript builder.
        # Include actual native call/signature envelopes in a conservative byte
        # reservation; never export them or infer hidden reasoning. No count RPC.
        contents = self.provider._contents(turn)
        reservation = sum(len(item.model_dump_json(exclude_none=True).encode("utf-8")) for item in contents)
        reservation += len(json.dumps([SYSTEM_INSTRUCTION, TOOL_DECLARATION, FINAL_SCHEMA]).encode("utf-8"))
        reservation += self.provider.max_output_tokens + 2048
        return reservation

    async def next_action(self, turn):
        reservation = self.reservation(turn)
        await self.pacer.wait(reservation)
        token = self._current_reservation.set(reservation)
        try:
            return await self.provider.next_action(turn)
        finally:
            self._current_reservation.reset(token)

    @property
    def current_reservation(self):
        value = self._current_reservation.get()
        if value is None:
            raise RuntimeError("No paced investigation request is active")
        return value
