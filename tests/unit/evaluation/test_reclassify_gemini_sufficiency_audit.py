"""Offline checks for post-hoc complete-evidence review metrics."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/reclassify_gemini_sufficiency_audit.py"
spec = importlib.util.spec_from_file_location("reclassify_gemini_sufficiency_audit", SCRIPT)
audit_script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_script)


def test_reclassifies_only_reviewed_complete_rows_and_preserves_raw_labels():
    summary = {
        "decision_rows": [
            {
                "case_id": "unsupported-complete-fixture",
                "condition": "complete",
                "arm": "production",
                "expected_action": "final",
                "observed_action": "search",
            },
            {
                "case_id": "unsupported-complete-fixture",
                "condition": "incomplete",
                "arm": "production",
                "expected_action": "search",
                "observed_action": "search",
            },
            {
                "case_id": "valid-complete-fixture",
                "condition": "complete",
                "arm": "production",
                "expected_action": "final",
                "observed_action": "answered",
            },
        ]
    }
    audit = {
        "cases": {
            "unsupported-complete-fixture": {"complete_condition_status": "insufficient"},
        }
    }

    result = audit_script.reclassify(summary, audit)

    assert result["arms"]["production"] == {
        "n": 3,
        "sufficient_n": 1,
        "correct_final_when_sufficient": 1,
        "insufficient_n": 2,
        "correct_search_when_insufficient": 2,
        "unnecessary_searches": 0,
        "premature_finals": 0,
        "reclassified_complete_rows": 1,
    }
    assert summary["decision_rows"][0]["expected_action"] == "final"
