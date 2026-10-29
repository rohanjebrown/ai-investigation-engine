import importlib.util
from pathlib import Path

import pytest

from investigation_engine.application.investigation_decisions import ConflictClaim, InvestigationDecision
from investigation_engine.application.investigation_values import InvestigationStatus
from investigation_engine.application.ports.generation_errors import GenerationResponseError


def module_at(name):
    spec = importlib.util.spec_from_file_location("test_deploy_" + name, Path("deploy") / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_trigger_removes_inline_deploy_and_preserves_identity():
    current = {
        "github": {"owner": "rohanjebrown", "name": "ai-investigation-engine", "push": {"branch": "^main$"}},
        "serviceAccount": "existing",
        "build": {"steps": [{"id": "Deploy"}]},
    }
    updated = module_at("gate_existing_trigger").gated_trigger(current)
    assert "build" not in updated and updated["filename"] == "cloudbuild.yaml"
    assert updated["github"] == current["github"] and updated["serviceAccount"] == "existing"
    assert "build" in current


def test_repeated_identical_claim_is_not_a_conflict():
    with pytest.raises(GenerationResponseError, match="distinct claims"):
        InvestigationDecision(
            status=InvestigationStatus.CONFLICTING_EVIDENCE,
            answer=None,
            citations=(),
            conflicts=(
                ConflictClaim(claim="Event happened Monday", citations=("E1",)),
                ConflictClaim(claim=" event  happened MONDAY ", citations=("E2",)),
            ),
        )
