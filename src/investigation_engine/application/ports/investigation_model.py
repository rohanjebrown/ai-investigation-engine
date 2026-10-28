"""Provider-independent model action port for one investigation turn."""

from typing import Protocol

from investigation_engine.application.investigations import (
    InvestigationDecision,
    InvestigationTurn,
    SearchEvidenceCall,
)


class InvestigationModel(Protocol):
    async def next_action(self, turn: InvestigationTurn) -> SearchEvidenceCall | InvestigationDecision:
        """Return one model-requested search or a structured final decision."""
        ...
