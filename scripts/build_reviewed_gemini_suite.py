"""Build a reviewed, expanded Gemini sufficiency suite from the pinned corpus."""

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD_SUITE = ROOT / "data/samples/evaluation/suite.json"
CORPUS = ROOT / "data/samples/evaluation/corpus.json"
AUDIT = ROOT / "data/samples/evaluation/evidence_sufficiency_audit.json"
OUTPUT = ROOT / "data/samples/evaluation/suite_sufficiency_reviewed.json"
INDEPENDENT_OUTPUT = ROOT / "data/samples/evaluation/suite_sufficiency_independent_31.json"

NEW_FACTS = (
    {
        "slug": "maheen_khan_profession",
        "title": "Maheen Khan",
        "question": "What profession is Maheen Khan known for?",
        "answer": "fashion and costume designer",
        "triple": ["Maheen Khan", "profession", "fashion and costume designer"],
        "support_phrase": "Pakistani fashion and costume designer",
    },
    {
        "slug": "alice_washburn_occupation",
        "title": "Alice Washburn",
        "question": "What kind of performer was Alice Washburn?",
        "answer": "stage and film actress",
        "triple": ["Alice Washburn", "occupation", "stage and film actress"],
        "support_phrase": "American stage and film actress",
    },
    {
        "slug": "waldemar_i_death_date",
        "title": "Waldemar I, Prince of Anhalt-Zerbst",
        "question": "When did Waldemar I, Prince of Anhalt-Zerbst die?",
        "answer": "7 January 1368",
        "triple": ["Waldemar I, Prince of Anhalt-Zerbst", "date of death", "7 January 1368"],
        "support_phrase": "died 7 January 1368",
    },
    {
        "slug": "michael_govan_museum",
        "title": "Michael Govan",
        "question": "Which museum has Michael Govan directed since 2006?",
        "answer": "Los Angeles County Museum of Art",
        "triple": ["Michael Govan", "director of", "Los Angeles County Museum of Art"],
        "support_phrase": "director of the Los Angeles County Museum of Art since 2006",
    },
    {
        "slug": "john_donatich_press",
        "title": "John Donatich",
        "question": "Which publishing organization does John Donatich direct?",
        "answer": "Yale University Press",
        "triple": ["John Donatich", "director of", "Yale University Press"],
        "support_phrase": "Director of Yale University Press",
    },
    {
        "slug": "dana_blankstein_academy",
        "title": "Dana Blankstein",
        "question": "Which academy does Dana Blankstein-Cohen direct?",
        "answer": "Israeli Academy of Film and Television",
        "triple": ["Dana Blankstein-Cohen", "director of", "Israeli Academy of Film and Television"],
        "support_phrase": "director of the Israeli Academy of Film and Television",
    },
    {
        "slug": "jason_moore_birth_date",
        "title": "Jason Moore (director)",
        "question": "When was film director Jason Moore born?",
        "answer": "October 22, 1970",
        "triple": ["Jason Moore", "date of birth", "October 22, 1970"],
        "support_phrase": "born October 22, 1970",
    },
    {
        "slug": "olav_aaraas_birth_place",
        "title": "Olav Aaraas",
        "question": "Where was Olav Aaraas born?",
        "answer": "Fredrikstad",
        "triple": ["Olav Aaraas", "place of birth", "Fredrikstad"],
        "support_phrase": "He was born in Fredrikstad",
    },
    {
        "slug": "luigi_torchi_invention",
        "title": "Luigi Torchi",
        "question": "What machine did Luigi Torchi invent in 1834?",
        "answer": "the first direct multiplication machine",
        "triple": ["Luigi Torchi", "invented", "the first direct multiplication machine"],
        "support_phrase": "invented the first direct multiplication machine in 1834",
    },
    {
        "slug": "anders_henriksson_poker_award",
        "title": "Anders Henriksson (poker player)",
        "question": "What did poker player Anders Henriksson win in 2006?",
        "answer": "a bracelet at the World Series of Poker",
        "triple": ["Anders Henriksson", "won", "a bracelet at the World Series of Poker in 2006"],
        "support_phrase": "won a bracelet at the World Series of Poker in 2006",
    },
    {
        "slug": "anthony_mendleson_nominated_film",
        "title": "Anthony Mendleson",
        "question": "For which film was Anthony Mendleson nominated for an Academy Award in 1972?",
        "answer": "Young Winston",
        "triple": ["Anthony Mendleson", "Academy Award nomination for", "Young Winston"],
        "support_phrase": "nominated for the Academy Award for Best Costume Design in 1972 for",
    },
    {
        "slug": "henri_spouse",
        "title": "Maria Teresa, Grand Duchess of Luxembourg",
        "question": "Who is the spouse of Grand Duke Henri?",
        "answer": "Maria Teresa",
        "triple": ["Grand Duke Henri", "spouse", "Maria Teresa"],
        "support_phrase": "is the spouse of Grand Duke Henri",
    },
    {
        "slug": "marie_louise_title",
        "title": "Marie-Louise Coidavid",
        "question": "What title did Marie Louise Coidavid hold in Haiti from 1811 to 1820?",
        "answer": "Queen of the Kingdom of Haiti",
        "triple": ["Marie Louise Coidavid", "title held", "Queen of the Kingdom of Haiti"],
        "support_phrase": "was the Queen of the Kingdom of Haiti 1811",
    },
    {
        "slug": "richard_jefferson_legislature",
        "title": "Richard H. Jefferson",
        "question": "Which state legislature did Richard H. Jefferson serve in from 1987 to 1999?",
        "answer": "Minnesota House of Representatives",
        "triple": ["Richard H. Jefferson", "served in", "Minnesota House of Representatives from 1987 to 1999"],
        "support_phrase": "served in the Minnesota House of Representatives from 1987 to 1999",
    },
)

INVALID_COMPLETE_IDS = {
    "2wiki:464cfa460bd911eba7f7acde48001122",
    "simple:464cfa460bd911eba7f7acde48001122",
    "2wiki:6ebe05740bde11eba7f7acde48001122",
    "2wiki:2207e9060bda11eba7f7acde48001122",
}


def target_id(triple: list[str]) -> str:
    canonical = "|".join(" ".join(value.casefold().split()) for value in triple)
    return sha256(canonical.encode("utf-8")).hexdigest()[:16]


def supporting_sentence(text: str, phrase: str) -> tuple[int, int]:
    """Return the exact sentence containing a reviewed support phrase."""
    phrase_start = text.casefold().find(phrase.casefold())
    if phrase_start < 0:
        raise ValueError(f"Support phrase not found: {phrase}")
    previous_boundary = text.rfind(". ", 0, phrase_start)
    start = 0 if previous_boundary < 0 else previous_boundary + 2
    next_boundary = text.find(". ", phrase_start)
    end = len(text) if next_boundary < 0 else next_boundary + 1
    while start < phrase_start and text[start].isspace():
        start += 1
    while end > phrase_start and text[end - 1].isspace():
        end -= 1
    if not text[start:end].endswith("."):
        raise ValueError(f"Could not find a complete sentence for: {phrase}")
    return start, end


def build_suite(old_suite: dict, corpus: dict, audit: dict) -> dict:
    passages = {}
    for passage in corpus["documents"]:
        passages.setdefault(passage["title"], passage)
    reviewed = deepcopy(old_suite["cases"])
    for case in reviewed:
        if case["id"] in INVALID_COMPLETE_IDS:
            case["expected_behavior"] = "abstain"
            case["expected_answer"] = None
            case["annotation_triples"] = []
            case.pop("independent_target_id", None)
            continue
        if case["expected_behavior"] == "answer":
            if not case.get("annotation_triples"):
                raise ValueError(f"Answer case lacks target fact: {case['id']}")
            case["independent_target_id"] = target_id(case["annotation_triples"][-1])

    audit_cases = audit["cases"]
    if set(audit_cases) != INVALID_COMPLETE_IDS:
        raise ValueError("Evidence audit manifest and reviewed-suite exclusions differ")

    for index, fact in enumerate(NEW_FACTS, 1):
        passage = passages.get(fact["title"])
        if passage is None:
            raise ValueError(f"Corpus title not found: {fact['title']}")
        start, end = supporting_sentence(passage["text"], fact["support_phrase"])
        excerpt = passage["text"][start:end]
        if fact["answer"].casefold() not in excerpt.casefold():
            raise ValueError(f"Expected answer is not stated in the selected sentence: {fact['slug']}")
        reviewed.append(
            {
                "id": f"independent:{index:02d}:{fact['slug']}",
                "question": fact["question"],
                "category": "simple_factual",
                "expected_behavior": "answer",
                "expected_answer": fact["answer"],
                "annotation_triples": [fact["triple"]],
                "independent_target_id": target_id(fact["triple"]),
                "required_evidence": [
                    {
                        "content_sha256": passage["content_sha256"],
                        "start": start,
                        "end": end,
                    }
                ],
            }
        )

    return {
        "corpus_id": old_suite["corpus_id"],
        "suite_version": "sufficiency-reviewed-v2",
        "cases": reviewed,
    }


def independent_subset(suite: dict) -> dict:
    """Keep one reviewed answerable question per distinct target fact."""
    selected = {}
    for case in suite["cases"]:
        target = case.get("independent_target_id")
        if target:
            selected.setdefault(target, case)
    cases = list(selected.values())
    if len(cases) < 30:
        raise ValueError(f"The reviewed suite contains only {len(cases)} independent answer targets")
    return {
        "corpus_id": suite["corpus_id"],
        "suite_version": "sufficiency-independent-31-v1",
        "cases": cases,
    }


def main() -> None:
    old_suite = json.loads(OLD_SUITE.read_text(encoding="utf-8"))
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    result = build_suite(old_suite, corpus, audit)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    independent = independent_subset(result)
    INDEPENDENT_OUTPUT.write_text(json.dumps(independent, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    answer_cases = [case for case in result["cases"] if case["expected_behavior"] == "answer"]
    unique_targets = {case["independent_target_id"] for case in answer_cases}
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "case_count": len(result["cases"]),
                "answer_cases": len(answer_cases),
                "independent_targets": len(unique_targets),
                "independent_suite": str(INDEPENDENT_OUTPUT),
                "independent_suite_cases": len(independent["cases"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
