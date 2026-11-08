"""Conservative question-aware text extraction, never reference-aware correctness."""

import re
from collections import Counter
from statistics import mean

from investigation_engine.evaluation.metrics import answer_scores

EXTRACTOR_VERSION = "answer-focus-v1"
_CITATION = re.compile(r"\[E[1-9][0-9]*\]")
_NEGATION = re.compile(
    r"\b(?:not|never|neither|nor|no|cannot|can't|isn't|aren't|wasn't|weren't|doesn't|didn't|don't|hasn't|haven't|hadn't|won't|wouldn't|shouldn't|couldn't|without)\b",
    re.I,
)
_UNCERTAIN = re.compile(
    r"\b(?:or|and|either|maybe|perhaps|possibly|probably|uncertain|unclear|unknown|allegedly|reportedly)\b", re.I
)
_CLAUSE = re.compile(
    r"\b(?:is|was|are|were|be|been|being|has|have|had|did|does|said|says|mentions?|mentioned|named|claims?|asserts?|according|regarding|reference|expected|question|answer|sources?|document|example|because|although|but|however|who|which|that|if)\b",
    re.I,
)


def _clean(value):
    return " ".join(value.replace("’", "'").split())


def _value(value):
    """A deliberately small grammar for values, not arbitrary sentence fragments."""
    value = value.strip().removesuffix(".").strip()
    if not value or len(value) > 160 or len(value.split()) > 20:
        return None
    if _NEGATION.search(value) or _UNCERTAIN.search(value) or _CLAUSE.search(value):
        return None
    if any(mark in value for mark in ';:!?()[]{}\n=<>"“”'):
        return None
    # Numeric values/dates (including ISO dates, decimals, percentages and units).
    if re.fullmatch(
        r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:%|\s+(?:BC|BCE|AD|CE|km|m|kg|million|billion))?", value, re.I
    ) or re.fullmatch(r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}\.\d{1,2}\.\d{4})", value):
        return value
    if re.fullmatch(
        r"(?:\d{1,2}\s+)?(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,4}(?:,?\s+\d{4})?(?:\s+(?:BC|BCE|AD|CE))?",
        value,
        re.I,
    ):
        return value
    # Proper names, titles, places and comparison options. This intentionally
    # abstains on lowercase clauses, lists, initials with uncertain punctuation,
    # aliases and compound answers. It is not an entity recognizer.
    tokens = value.split()
    if "/" in value or (
        "," in value and not re.fullmatch(r"[^,]+,\s*(?:\d+(?:st|nd|rd|th)\s+[^,]+|Jr\.?|Sr\.?)", value, re.I)
    ):
        return None
    connectors = {"of", "the", "de", "del", "van", "von", "da", "di", "du", "la", "le", "in", "at", "on"}
    if not any(char.isalpha() for char in value) or not tokens[0][0].isupper():
        return None
    if all(
        token in connectors or token[0].isupper() or re.fullmatch(r"\d+(?:st|nd|rd|th)?", token, re.I)
        for token in tokens
    ):
        return value
    return None


def extract_answer(question, answer, *, status="answered"):
    """No reference, gold, citations, evidence or source text enters this function.

    A reliable result means an explicit surface-form value was isolated under
    this narrow grammar. It does not mean the assertion is factually true.
    """

    def result(state, reason, text=None, method=None):
        return {"version": EXTRACTOR_VERSION, "status": state, "reason": reason, "answer": text, "method": method}

    if status in ("insufficient_evidence", "conflicting_evidence", "incomplete"):
        return result("not_applicable", "No completed affirmative answer")
    if status != "answered" or not isinstance(answer, str) or not answer.strip():
        return result("unavailable", "No generated answer available")
    if not isinstance(question, str) or not question.strip():
        return result("unavailable", "Question unavailable for answer-role validation")
    text = _clean(_CITATION.sub("", answer))
    q = _clean(question).removesuffix("?")
    # Accept only complete standalone yes/no/noanswer responses; never discard
    # an accompanying qualification or contradictory assertion to obtain one.
    if text.casefold().rstrip(".") in ("yes", "no", "noanswer"):
        if re.match(r"^(?:is|was|are|were|do|does|did|has|have|had|can|could|will|would|should)\b", q, re.I):
            return result("available", "Standalone Boolean response", text.rstrip("."), "standalone_boolean")
        return result("unavailable", "Boolean response to a non-Boolean question")
    if _NEGATION.search(text):
        return result("ambiguous", "Negation or exclusion cannot safely be reduced to one answer value")
    if _UNCERTAIN.search(text) or re.search(r"\b[A-Za-z][\w'-]*\s*/\s*[A-Za-z]", text):
        return result("ambiguous", "Multiple candidates, conjunction or uncertainty")
    if ";" in text or re.search(r"[.!?]\s+\S", text):
        return result("ambiguous", "Multiple assertions or uncertain sentence boundaries")
    candidate, method = None, None
    # Match the ENTIRE question predicate. Do not shorten a name, repair an
    # inverse relationship, equate father with father-in-law, or find a gold name.
    patterns = []
    if match := re.fullmatch(r"Where (was|were) (.+) (born|buried)", q, re.I):
        patterns.append((rf"{re.escape(match[2])}\s+{match[1]}\s+{match[3]}\s+(?:in|at)\s+(.+)", "question_location"))
    if match := re.fullmatch(r"When (was|were) (.+) (born|buried)", q, re.I):
        patterns.append((rf"{re.escape(match[2])}\s+{match[1]}\s+{match[3]}\s+(?:on|in)\s+(.+)", "question_date"))
    if match := re.fullmatch(r"(?:Who|What) (?:is|was|are|were) (.+)", q, re.I):
        patterns.append((rf"{re.escape(match[1])}\s+(?:is|was|are|were)\s+(.+)", "question_predicate"))
    if match := re.fullmatch(r"Where (is|was|are|were) (.+)", q, re.I):
        patterns.append((rf"{re.escape(match[2])}\s+{match[1]}\s+(?:in|at)\s+(.+)", "question_location"))
    if match := re.fullmatch(r"When (?:is|was|are|were) (.+)", q, re.I):
        patterns.append((rf"{re.escape(match[1])}\s+(?:is|was|are|were)\s+(?:on|in)\s+(.+)", "question_date"))
    # A limited comparative form yields only an exact candidate named in the
    # question and asserts that it has the requested comparative attribute.
    if match := re.fullmatch(r"Who (lived longer|was born earlier|was born later), (.+) or (.+)", q, re.I):
        for option in (match[2], match[3]):
            patterns.append((rf"({re.escape(option)})\s+{re.escape(match[1])}", "explicit_comparison"))
    for pattern, name in patterns:
        if match := re.fullmatch(pattern + r"\.?", text, re.I):
            candidate, method = _value(match[1]), name
            break
    if candidate is None:
        if match := re.fullmatch(r"(?:The answer is|Answer:)\s+(.+)", text, re.I):
            candidate, method = _value(match[1]), "explicit_answer_label"
    if candidate is None:
        candidate, method = _value(text), "standalone_value"
    if candidate is None:
        return result(
            "unavailable", "No supported unambiguous answer assertion; relationship and direction are not repaired"
        )
    return result("available", "Single asserted value under a supported surface grammar", candidate, method)


def assess_answer(question, answer, reference, *, status="answered"):
    extraction = extract_answer(question, answer, status=status)
    return {
        "official_whole_response": answer_scores(answer, reference),
        "extraction": extraction,
        "answer_focused": answer_scores(extraction["answer"], reference)
        if extraction["status"] == "available"
        else {"exact_match": None, "f1": None},
        "manual_factual_judgment": None,
        "reviewer": None,
        "review_explanation": None,
        "factual_correctness_basis": "unreviewed; text agreement does not establish factual correctness",
    }


def assessment_summary(assessments):
    values = [value for value in assessments if value is not None]
    if not values:
        return None
    focused = [value["answer_focused"] for value in values if value["answer_focused"]["exact_match"] is not None]
    judgments = Counter(value["manual_factual_judgment"] or "unreviewed" for value in values)
    denominator = sum(judgments[key] for key in ("correct", "incorrect", "partially_correct"))
    return {
        "attempts": len(values),
        "official_whole_response_em": mean(value["official_whole_response"]["exact_match"] or 0 for value in values),
        "official_whole_response_f1": mean(value["official_whole_response"]["f1"] or 0 for value in values),
        "answer_focused_em": mean(value["exact_match"] for value in focused) if focused else None,
        "answer_focused_f1": mean(value["f1"] for value in focused) if focused else None,
        "answer_focused_denominator": len(focused),
        "extraction_status_counts": dict(Counter(value["extraction"]["status"] for value in values)),
        "ambiguous_answers": sum(value["extraction"]["status"] == "ambiguous" for value in values),
        "judgment_counts": {
            key: judgments[key] for key in ("correct", "incorrect", "partially_correct", "unverifiable", "unreviewed")
        },
        "unreviewed_answers": judgments["unreviewed"],
        "reviewed_answer_accuracy": judgments["correct"] / denominator if denominator else None,
        "reviewed_accuracy_denominator": denominator,
        "denominators": "Official means retain all measured attempts (missing values contribute zero). Focused means use only available extracted values with a reference; failures are null, never factual errors. Reviewed accuracy=correct/(correct+incorrect+partially_correct); partial cases receive no full-credit and are separately counted. Unverifiable and unreviewed cases are excluded. No metric is an automated factual judgment.",
    }
