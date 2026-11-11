"""Per-investigation cap on actual generation attempts, isolated by async context."""

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class GenerationAttemptBudget:
    maximum: int
    used: int = 0

    def consume(self):
        if self.used >= self.maximum:
            raise RuntimeError("Investigation generation attempt budget exhausted")
        self.used += 1


current_generation_budget = ContextVar("investigation_generation_attempt_budget", default=None)
