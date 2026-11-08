"""Prompt-content and forwarding checks; not evidence of live Gemini adherence."""

import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from google.genai import types

from investigation_engine.application.investigations import InvestigationStatus, InvestigationTurn
from investigation_engine.infrastructure.generation import gemini_investigation_model as module

ROOT = Path(__file__).resolve().parents[3]


def test_original_prompt_preserved_and_revised_snapshot_matches_active_instruction():
    old = (ROOT / "docs/validation/investigation-stopping-prompt-v4.txt").read_bytes()
    assert sha256(old).hexdigest() == "544430c1ce4f48827d9c45079b715eb7f61591ce73db61b748094a34900f87a2"
    new = (ROOT / "docs/validation/investigation-stopping-prompt-v5.txt").read_text(encoding="utf-8")
    assert new == module.SYSTEM_INSTRUCTION
    assert module.INVESTIGATION_GUIDANCE_VERSION == "grounding-v5-evidence-based-stopping"
    assert "when enough searches have been performed" not in new


@pytest.mark.parametrize(
    "requirement",
    [
        "There is no minimum search count; one search may establish multiple required links.",
        "Finalize as soon as admitted evidence supports ALL required relationships and conclusions.",
        "specific missing fact, unresolved ambiguity, or material\ncontradiction relevant to the ORIGINAL question",
        "Do not investigate an entity's unrelated relationships merely because it appears in a result.",
        "Avoid repeated or rephrased searches\nthat do not address such a gap.",
        "Never invent a relationship to finish early.",
        "If a required fact remains unsupported, search for it while a relevant bounded search\nremains; otherwise return insufficient_evidence rather than a partial or guessed answer.",
    ],
)
def test_prompt_states_evidence_based_finish_and_continuation_requirements(requirement):
    assert requirement in module.SYSTEM_INSTRUCTION


def test_identity_untrusted_source_citation_and_conflict_safeguards_preserved():
    old = (ROOT / "docs/validation/investigation-stopping-prompt-v4.txt").read_text(encoding="utf-8")
    old_paragraphs, new_paragraphs = old.split("\n\n"), module.SYSTEM_INSTRUCTION.split("\n\n")
    assert old_paragraphs[2] == new_paragraphs[2]  # Same-entity corroboration.
    assert old_paragraphs[3] == new_paragraphs[3]  # Untrusted sources/citations.
    assert old.split("answered only when", 1)[1] == module.SYSTEM_INSTRUCTION.split("answered only when", 1)[1]
    assert all(
        name not in module.SYSTEM_INSTRUCTION.casefold()
        for name in ("jolliffe", "wisigard", "alexandra", "2wiki", "reference answer", "gold fact")
    )


async def test_revised_instruction_is_forwarded_without_forcing_search_or_changing_configuration(monkeypatch):
    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                finish_reason=types.FinishReason.STOP,
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            text=json.dumps(
                                {"status": "insufficient_evidence", "answer": None, "citations": [], "conflicts": []}
                            )
                        )
                    ],
                ),
            )
        ]
    )
    models = SimpleNamespace(generate_content=AsyncMock(return_value=response))
    client = SimpleNamespace(aio=SimpleNamespace(models=models, aclose=AsyncMock()), close=Mock())
    monkeypatch.setattr(module.genai, "Client", Mock(return_value=client))
    provider = module.GeminiInvestigationModel(api_key="offline-only", model="gemini-3.5-flash-lite")
    decision = await provider.next_action(InvestigationTurn(question="Unknown question?", exchanges=()))
    config = models.generate_content.await_args.kwargs["config"]
    assert decision.status == InvestigationStatus.INSUFFICIENT_EVIDENCE
    assert config.system_instruction == module.SYSTEM_INSTRUCTION
    assert config.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.VALIDATED
    assert (
        config.tools[0].function_declarations[0].parameters_json_schema
        == module.TOOL_DECLARATION["parameters_json_schema"]
    )
    assert config.response_json_schema == module.FINAL_SCHEMA and config.max_output_tokens == 1024
    assert models.generate_content.await_count == 1
    await provider.aclose()
