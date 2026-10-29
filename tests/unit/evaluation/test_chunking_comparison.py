import json
from pathlib import Path

from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
from investigation_engine.evaluation.chunking_comparison import compare_structural_chunking


class Characters:
    identity = TokenizerIdentity(
        name="comparison-character-test", revision="1", artifact_sha256="0" * 64, runtime_version="test"
    )

    def offsets(self, text):
        return tuple((i, i + 1) for i in range(len(text)))


def test_structural_report_matches_questions_and_never_claims_measured_retrieval():
    base = Path("data/samples/evaluation")
    suite = json.loads((base / "suite.json").read_text(encoding="utf-8"))
    corpus = json.loads((base / "corpus.json").read_text(encoding="utf-8"))
    original = json.dumps(corpus, sort_keys=True)
    result = compare_structural_chunking(suite, corpus, Characters())
    assert result["source_occurrences"] == 218 and result["question_count"] == 40
    whole = result["strategies"][0]
    assert whole["memberships"] == 218
    assert whole["required_gold_facts"] == whole["gold_facts_contained_in_one_chunk"] == 76
    for strategy in result["strategies"]:
        assert [row["id"] for row in strategy["cases"]] == [case["id"] for case in suite["cases"]]
        assert all(row["retrieval_quality"] is None for row in strategy["cases"])
    assert json.dumps(corpus, sort_keys=True) == original
