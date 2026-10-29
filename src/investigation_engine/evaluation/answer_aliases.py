"""Reviewed evaluation-only aliases; never rewrite gold or production outputs."""

import re
import unicodedata

SCORER_VERSION = "answer-values-v2-nationality-aliases"
ALIAS_RULE = "nationality-united-states-american-v1"


def nationality_aliases(case: dict) -> tuple[str, ...]:
    # Country and demonym are interchangeable only for this explicit question
    # form and this reviewed gold value. Citizenship, residence, origin, and
    # broader geopolitical mappings are deliberately not inferred.
    question = " ".join(case.get("question", "").split())
    if (
        case.get("expected_behavior") == "answer"
        and case.get("expected_answer", "").strip().casefold() == "united states"
        and re.fullmatch(r"What nationality (?:is|was) .+\?", question, re.IGNORECASE)
    ):
        return ("American",)
    return ()


def _direct_words(answer: str) -> tuple[str, ...]:
    text = re.sub(r"\[E[1-9][0-9]*\]", "", answer)
    return tuple(re.findall(r"[^\W_]+", unicodedata.normalize("NFC", text).casefold()))


def matched_alias(case: dict, answer: str | None) -> str | None:
    if not isinstance(answer, str):
        return None
    # Match the whole value, not a substring such as South American, Native
    # American, American Samoa, a negation, or a multiple-choice guess.
    return next((alias for alias in nationality_aliases(case) if _direct_words(answer) == _direct_words(alias)), None)


def normalization_record(case: dict, answer: str | None) -> dict:
    aliases = nationality_aliases(case)
    return {
        "scorer_version": SCORER_VERSION,
        "gold_acceptable_answers": list(
            case.get("acceptable_answers") or ([case["expected_answer"]] if case.get("expected_answer") else [])
        ),
        "evaluation_only_aliases": list(aliases),
        "rule_ids": [ALIAS_RULE] if aliases else [],
        "matched_alias": matched_alias(case, answer),
        "policy": "Reviewed direct-value aliases only; no fuzzy, citizenship, residence, or geopolitical inference.",
    }
