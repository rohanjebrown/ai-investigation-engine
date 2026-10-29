import json
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from investigation_engine.application.embeddings import EmbeddingConfig
from investigation_engine.application.ports.embedding_errors import EmbeddingInputError
from investigation_engine.application.retrieval import Evidence
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.evaluation.cached_queries import CachedQueryEmbeddings
from investigation_engine.evaluation.retrieval import (
    RetrievalRun,
    covered,
    evaluate,
    export,
    fingerprint,
    measure,
    missing_gold,
    validate_suite,
)


def hit(number=1, start=0, end=10):
    return Evidence(
        document_id=UUID(int=number),
        document_version_id=UUID(int=number),
        content_sha256="a" * 64,
        text="x" * (end - start),
        start=start,
        end=end,
        score=0.8,
        rank=1,
        source=SourceReference(
            kind=SourceKind.DATASET,
            source_id="dataset",
            revision="pinned",
            record_locator={"split": "dev", "example_id": str(number), "context_position": 0},
        ),
        source_spans=tuple(
            SourceSpan(start=a, end=b, locators=(SourceLocator(kind=LocatorKind.SENTENCE, index=i),))
            for i, (a, b) in enumerate(((0, 4), (5, 10)))
        ),
    )


def gold(item, index=0):
    span = item.source_spans[index]
    return {
        "source": asdict(item.source),
        "content_sha256": item.content_sha256,
        "sentence_index": index,
        "start": span.start,
        "end": span.end,
    }


def test_hand_calculated_recall_mrr_partial_and_complete_multi_hop():
    a, b, irrelevant = hit(1), hit(2), hit(3)
    metrics = measure((irrelevant, a, b), (gold(a), gold(a, 1), gold(b)), ks=(1, 2, 3))
    assert metrics["mrr"] == 0.5
    assert metrics["at_k"]["1"] == dict(recall=0, evidence_fraction=0, all_required=False)
    assert metrics["at_k"]["2"]["recall"] == pytest.approx(2 / 3)
    assert metrics["at_k"]["3"] == dict(recall=1, evidence_fraction=1, all_required=True)


def test_duplicates_do_not_spend_unique_top_k_or_repeat_gold_credit():
    a, b = hit(1), hit(2)
    assert measure((a, a, b), (gold(a), gold(b)), ks=(2,))["at_k"]["2"]["recall"] == 1
    assert measure((a, replace(a, start=1, text="x" * 9)), (gold(a),), ks=(2,))["at_k"]["2"]["recall"] == 1


def test_empty_missing_and_no_gold_are_distinct():
    assert measure((), (gold(hit()),))["mrr"] == 0
    assert measure((hit(2),), (gold(hit()),))["at_k"]["20"]["recall"] == 0
    assert measure((), ())["mrr"] is None
    assert measure((hit(),), ())["at_k"]["5"]["all_required"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"source": replace(hit().source, revision="other")},
        {
            "source": replace(
                hit().source, record_locator={"split": "dev", "example_id": "other", "context_position": 0}
            )
        },
        {"content_sha256": "b" * 64},
        {"source_spans": ()},
        {"start": 1, "text": "x" * 9},
        {"end": 3, "text": "xxx"},
    ],
)
def test_wrong_occurrence_hash_or_partial_sentence_does_not_count(change):
    a = hit()
    assert not covered(replace(a, **change), gold(a))


def test_dictionary_order_and_multi_sentence_intersection():
    a = hit()
    assert covered(
        replace(a, source=replace(a.source, record_locator=dict(reversed(list(a.source.record_locator.items()))))),
        gold(a),
    )
    assert measure((a,), (gold(a), gold(a, 1)), ks=(1,))["at_k"]["1"]["recall"] == 1


@pytest.mark.parametrize("ks", [(), (0,), (101,), (True,)])
def test_bad_top_k_rejected(ks):
    with pytest.raises(ValueError):
        measure((), (), ks)


def test_duplicate_gold_rejected():
    with pytest.raises(ValueError, match="Duplicate gold"):
        measure((), (gold(hit()), gold(hit())))


@pytest.fixture
def snapshot():
    root = Path("data/samples/evaluation")
    return tuple(json.loads((root / f"{name}.json").read_text(encoding="utf8")) for name in ("suite", "corpus"))


def test_committed_suite_has_40_grounded_cases_and_source_only_corpus(snapshot):
    suite, corpus = snapshot
    validate_suite(suite, corpus)
    from collections import Counter

    assert Counter(c["category"] for c in suite["cases"]) == dict(
        simple_factual=8, two_hop=12, three_plus_hop=8, unanswerable=6, contradictory=6
    )
    assert all(not missing_gold(case, corpus) for case in suite["cases"])
    assert corpus["record_count"] == len(corpus["documents"])
    assert len(corpus["documents"]) > 200
    assert all(
        "question" not in doc and "answer" not in doc and "required_evidence" not in doc for doc in corpus["documents"]
    )
    assert len({fingerprint(doc["source"]) for doc in corpus["documents"]}) == corpus["record_count"]


async def test_runner_exports_failures_unavailable_cases_null_answers_and_paired_delta(snapshot, tmp_path):
    suite, corpus = deepcopy(snapshot)
    suite["cases"] = suite["cases"][:2] + [next(c for c in suite["cases"] if c["category"] == "unanswerable")]
    suite["cases"][1]["required_evidence"][0]["source"]["revision"] = "absent"
    one = SimpleNamespace(execute=AsyncMock(return_value=()))
    two = SimpleNamespace(execute=AsyncMock(side_effect=[RuntimeError("PRIVATE_PROVIDER_ERROR_SENTINEL"), ()]))
    configs = [
        RetrievalRun(name=mode, mode=mode, chunking={"strategy": "whole"}, embedding={"model": "fake"})
        for mode in ("semantic", "hybrid")
    ]
    report = await evaluate(
        suite, corpus, list(zip(configs, (one, two))), indexed_corpus_fingerprint=fingerprint(corpus)
    )
    assert report["runs"][0]["aggregate"]["scored_cases"] == 1
    assert report["runs"][1]["questions"][0]["status"] == "failed"
    assert report["runs"][0]["questions"][1]["status"] == "unavailable"
    assert report["runs"][0]["questions"][2]["metrics"]["mrr"] is None
    assert report["comparisons"][0]["paired_cases"] == 0
    assert all(v is None for v in report["answer_metrics"].values())
    export(report, tmp_path)
    assert "PRIVATE_PROVIDER_ERROR_SENTINEL" not in (tmp_path / "retrieval.json").read_text(encoding="utf8")
    assert (tmp_path / "retrieval.md").is_file()
    # Only questions/scope/limit reach retrieval, never expected answers or gold.
    assert one.execute.await_args_list[0].args == (suite["cases"][0]["question"], 20)


async def test_runner_refuses_mismatched_manifests_and_configs_before_io(snapshot):
    suite, corpus = snapshot
    fake = SimpleNamespace(execute=AsyncMock())
    cfg = RetrievalRun(name="semantic", mode="semantic", chunking={}, embedding={})
    with pytest.raises(ValueError, match="attestation"):
        await evaluate(suite, corpus, [(cfg, fake)], indexed_corpus_fingerprint="wrong")
    with pytest.raises(ValueError, match="identical"):
        await evaluate(
            suite,
            corpus,
            [(cfg, fake), (replace(cfg, name="hybrid", mode="hybrid", chunking={"different": 1}), fake)],
            indexed_corpus_fingerprint=fingerprint(corpus),
        )
    fake.execute.assert_not_called()


async def test_cached_queries_validate_vectors_and_never_fall_back_to_live_inference():
    profile = EmbeddingConfig(provider="synthetic", model_id="offline")
    original = [1.0] + [0.0] * 767
    provider = CachedQueryEmbeddings(profile, {"Exact question?": original})
    original[0] = 2.0
    assert (await provider.embed_query("Exact question?"))[0] == 1
    with pytest.raises(EmbeddingInputError, match="exact question"):
        await provider.embed_query("exact question?")
    with pytest.raises(EmbeddingInputError, match="does not generate"):
        await provider.embed_documents(["text"])
    with pytest.raises(ValueError):
        CachedQueryEmbeddings(profile, {"q": [0.0] * 768})


@pytest.mark.parametrize(
    "ordering,delta,direction", [("better", 0.5, "positive"), ("worse", -0.5, "negative"), ("same", 0, "negligible")]
)
async def test_paired_comparison_reports_actual_metric_differences(snapshot, ordering, delta, direction):
    suite, corpus = deepcopy(snapshot)
    case = next(c for c in suite["cases"] if c["category"] == "simple_factual")
    suite["cases"] = [case]
    ref = case["required_evidence"][0]
    doc = next(d for d in corpus["documents"] if d["source"] == ref["source"])
    relevant = Evidence(
        document_id=UUID(int=50),
        document_version_id=UUID(int=50),
        content_sha256=doc["content_sha256"],
        text=doc["text"],
        start=0,
        end=len(doc["text"]),
        score=0.8,
        rank=1,
        source=SourceReference(**{**doc["source"], "kind": SourceKind.DATASET}),
        source_spans=tuple(
            SourceSpan(
                start=s["start"],
                end=s["end"],
                locators=tuple(
                    SourceLocator(kind=LocatorKind(loc["kind"]), index=loc["index"]) for loc in s["locators"]
                ),
            )
            for s in doc["source_spans"]
        ),
    )
    good, poor = (relevant, hit(999)), (hit(999), relevant)
    baseline, other = (poor, good) if ordering == "better" else (good, poor) if ordering == "worse" else (good, good)
    configs = [RetrievalRun(name=mode, mode=mode, chunking={}, embedding={}) for mode in ("semantic", "hybrid")]
    services = [SimpleNamespace(execute=AsyncMock(return_value=items)) for items in (baseline, other)]
    report = await evaluate(
        suite, corpus, list(zip(configs, services)), ks=(1, 2), indexed_corpus_fingerprint=fingerprint(corpus)
    )
    comparison = report["comparisons"][0]
    assert comparison["mrr_delta"] == delta and comparison["direction"] == direction
    assert comparison["paired_cases"] == 1
    assert comparison["at_k_deltas"]["2"]["recall"] == 0


def test_invalid_snapshot_counts_or_bounds_fail_validation(snapshot):
    suite, corpus = deepcopy(snapshot)
    corpus["record_count"] += 1
    with pytest.raises(ValueError, match="counts"):
        validate_suite(suite, corpus)
    corpus["record_count"] -= 1
    corpus["documents"][0]["source_spans"][0]["end"] = 10**9
    with pytest.raises(ValueError, match="span"):
        validate_suite(suite, corpus)
