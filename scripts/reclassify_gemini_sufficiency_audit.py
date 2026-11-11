"""Recompute decision metrics after reviewing complete-evidence fixture support.

This is a post-hoc evaluator. The review manifest is never sent to Gemini and
does not change the production request, tool availability, or model output.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path


def reclassify(summary: dict, audit: dict) -> dict:
    """Return reviewed per-arm decision counts without mutating raw results."""
    reviewed = audit.get("cases", {})
    totals = defaultdict(
        lambda: {
            "n": 0,
            "sufficient_n": 0,
            "correct_final_when_sufficient": 0,
            "insufficient_n": 0,
            "correct_search_when_insufficient": 0,
            "unnecessary_searches": 0,
            "premature_finals": 0,
            "reclassified_complete_rows": 0,
        }
    )
    for row in summary.get("decision_rows", []):
        expected = row["expected_action"]
        case_review = reviewed.get(row["case_id"])
        if (
            row["condition"] == "complete"
            and expected == "final"
            and case_review is not None
            and case_review.get("complete_condition_status") == "insufficient"
        ):
            expected = "search"
        observed = row["observed_action"]
        counts = totals[row["arm"]]
        counts["n"] += 1
        if expected in {"final", "conflict"}:
            counts["sufficient_n"] += 1
            counts["correct_final_when_sufficient"] += observed in {
                "answered",
                "final",
                "conflict",
                "conflicting_evidence",
            }
        else:
            counts["insufficient_n"] += 1
            counts["correct_search_when_insufficient"] += observed == "search"
            counts["premature_finals"] += observed in {"answered", "final", "conflict", "conflicting_evidence"}
        if expected == "final" and observed == "search":
            counts["unnecessary_searches"] += 1
        if expected == "search" and row["expected_action"] == "final":
            counts["reclassified_complete_rows"] += 1
    return {
        "reviewed_cases": reviewed,
        "arms": dict(totals),
        "scope_note": (
            "Post-hoc evidence-support reclassification only. Raw generation outputs and original expected labels are preserved."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = reclassify(
        json.loads(args.summary.read_text(encoding="utf-8")),
        json.loads(args.audit.read_text(encoding="utf-8")),
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
