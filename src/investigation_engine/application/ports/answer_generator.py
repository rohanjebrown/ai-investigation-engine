from typing import Protocol

from investigation_engine.application.answers import GenerationRequest, GenerationResult


class AnswerGenerator(Protocol):
    async def generate(self, request: GenerationRequest) -> GenerationResult:
        """Generate one grounded answer or abstain; failures remain exceptions."""
        ...
