"""Occurrence-level supporting-sentence metrics and reproducible run exports."""

import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from statistics import mean
from time import perf_counter

from investigation_engine.application.retrieval import Evidence
from investigation_engine.domain.source_span import LocatorKind


def fingerprint(value) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")
    ).hexdigest()


def occurrence(source: dict) -> tuple:
    """Private comparison only; no new production source identifier."""
    return (
        source["kind"],
        source["source_id"],
        source.get("revision"),
        json.dumps(source["record_locator"], sort_keys=True, ensure_ascii=False),
    )


def covered(hit: Evidence, gold: dict) -> bool:
    return (
        occurrence(asdict(hit.source)) == occurrence(gold["source"])
        and hit.content_sha256 == gold["content_sha256"]
        and hit.start <= gold["start"] < gold["end"] <= hit.end
        and any(
            span.start == gold["start"]
            and span.end == gold["end"]
            and any(loc.kind == LocatorKind.SENTENCE and loc.index == gold["sentence_index"] for loc in span.locators)
            for span in hit.source_spans
        )
    )


def gold_identity(item):
    return (occurrence(item["source"]), item["content_sha256"], item["sentence_index"], item["start"], item["end"])


def measure(hits, gold, ks=(5, 10, 20)) -> dict:
    if not ks or any(type(k) is not int or not 1 <= k <= 100 for k in ks):
        raise ValueError("Top-K values must be integers from 1 to 100")
    if len({gold_identity(item) for item in gold}) != len(gold):
        raise ValueError("Duplicate gold evidence labels")
    unique = []
    seen = set()
    for hit in hits:
        if hit.identity not in seen:
            seen.add(hit.identity)
            unique.append(hit)
    matches = [{i for i, item in enumerate(gold) if covered(hit, item)} for hit in unique]
    reciprocal = next((1 / rank for rank, found in enumerate(matches[: max(ks)], 1) if found), 0.0)
    result = {"gold_count": len(gold), "unique_hits": len(unique), "mrr": reciprocal if gold else None, "at_k": {}}
    for k in sorted(set(ks)):
        found = set().union(*matches[:k]) if matches[:k] else set()
        result["at_k"][str(k)] = {
            "recall": len(found) / len(gold) if gold else None,
            "evidence_fraction": len(found) / len(gold) if gold else None,
            "all_required": len(found) == len(gold) if gold else None,
        }
    return result


def validate_suite(suite: dict, corpus: dict) -> None:
    if suite["corpus_id"] != corpus["corpus_id"]:
        raise ValueError("Suite and corpus IDs differ")
    cases = suite["cases"]
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Question IDs must be unique")
    sources = [occurrence(doc["source"]) for doc in corpus["documents"]]
    if len(set(sources)) != len(sources):
        raise ValueError("Corpus source occurrences must be unique")
    for doc in corpus["documents"]:
        if sha256(doc["text"].encode("utf8")).hexdigest() != doc["content_sha256"]:
            raise ValueError("Corpus text hash mismatch")
        for span in doc["source_spans"]:
            if not 0 <= span["start"] < span["end"] <= len(doc["text"]):
                raise ValueError("Corpus source span outside canonical text")
    if corpus["record_count"] != len(sources) or corpus["unique_content_count"] != len(
        {d["content_sha256"] for d in corpus["documents"]}
    ):
        raise ValueError("Corpus counts disagree with included records")
    for case in cases:
        if not case["question"].strip() or case["category"] not in (
            "simple_factual",
            "two_hop",
            "three_plus_hop",
            "unanswerable",
            "contradictory",
        ):
            raise ValueError("Invalid evaluation case")
        if case.get("corpus_id", suite["corpus_id"]) != corpus["corpus_id"]:
            raise ValueError("Case corpus ID differs")
        for gold in case["required_evidence"]:
            if (
                type(gold["sentence_index"]) is not int
                or gold["sentence_index"] < 0
                or type(gold["start"]) is not int
                or type(gold["end"]) is not int
                or not 0 <= gold["start"] < gold["end"]
            ):
                raise ValueError("Invalid gold sentence bounds/index")
        measure((), case["required_evidence"])


def missing_gold(case, corpus):
    documents = {occurrence(doc["source"]): doc for doc in corpus["documents"]}
    missing = []
    for gold in case["required_evidence"]:
        doc = documents.get(occurrence(gold["source"]))
        if (
            doc is None
            or doc["content_sha256"] != gold["content_sha256"]
            or not any(
                span["start"] == gold["start"]
                and span["end"] == gold["end"]
                and any(
                    loc["kind"] == "sentence" and loc["index"] == gold["sentence_index"] for loc in span["locators"]
                )
                for span in doc["source_spans"]
            )
        ):
            missing.append(gold)
    return missing


@dataclass(frozen=True, kw_only=True)
class RetrievalRun:
    name: str
    mode: str
    chunking: dict
    embedding: dict
    semantic_candidates: int = 20
    keyword_candidates: int = 20
    rrf_k: int = 60


async def evaluate(
    suite: dict,
    corpus: dict,
    runs: list[tuple[RetrievalRun, object]],
    *,
    ks=(5, 10, 20),
    owner_id=None,
    indexed_corpus_fingerprint: str,
) -> dict:
    """Services are existing SearchEvidence instances, supplied by a trusted caller.

    No engine, settings, provider construction, indexing or network fallback here.
    The caller verifies the indexed snapshot against the manifest before running.
    """
    validate_suite(suite, corpus)
    if indexed_corpus_fingerprint != fingerprint(corpus):
        raise ValueError("Indexed corpus attestation does not match the manifest")
    if not runs or len({cfg.name for cfg, _ in runs}) != len(runs):
        raise ValueError("Distinct named retrieval runs are required")
    for cfg, _ in runs:
        if cfg.mode not in ("semantic", "hybrid"):
            raise ValueError("Unsupported retrieval mode")
        if cfg.chunking != runs[0][0].chunking or cfg.embedding != runs[0][0].embedding:
            raise ValueError("Paired comparison requires identical chunking and embedding")
        if (
            not 1 <= cfg.semantic_candidates <= 100
            or not 1 <= cfg.keyword_candidates <= 100
            or not 0 <= cfg.rrf_k <= 10000
        ):
            raise ValueError("Invalid retrieval parameters")
    measure((), (), ks)  # Validate before any retrieval.
    started = perf_counter()
    report = {
        "suite_version": suite["version"],
        "suite_fingerprint": fingerprint(suite),
        "corpus_manifest": corpus,
        "corpus_fingerprint": fingerprint(corpus),
        "timestamp": datetime.now(UTC).isoformat(),
        "top_k": sorted(set(ks)),
        "owner_scope": owner_id,
        "categories": dict(Counter(c["category"] for c in suite["cases"])),
        "answer_metrics": {
            key: None for key in ("correctness", "citation_accuracy", "abstention", "contradiction_detection")
        },
        "runs": [],
    }
    for cfg, service in runs:
        rows = []
        for case in suite["cases"]:
            row = {
                "id": case["id"],
                "question": case["question"],
                "category": case["category"],
                "metrics": None,
                "latency_ms": None,
            }
            absent = missing_gold(case, corpus)
            if absent:
                row.update(status="unavailable", reason="gold_missing_from_corpus", missing_gold=absent)
            elif not case["required_evidence"] and case["category"] != "unanswerable":
                row.update(status="unavailable", reason="missing_gold_annotations")
            else:
                begin = perf_counter()
                try:
                    hits = await service.execute(case["question"], max(ks), owner_id=owner_id)
                    row["latency_ms"] = (perf_counter() - begin) * 1000
                    row.update(
                        status="ok",
                        metrics=measure(hits, case["required_evidence"], ks),
                        retrieved=[asdict(hit) for hit in hits],
                    )
                except Exception as error:
                    row.update(status="failed", error_type=type(error).__name__)
                    # No raw provider/SQL exception messages in exported reports.
            rows.append(row)
        scored = [r["metrics"] for r in rows if r["metrics"] and r["metrics"]["gold_count"]]
        aggregate = {
            "scored_cases": len(scored),
            "status_counts": dict(Counter(r["status"] for r in rows)),
            "mrr": mean(m["mrr"] for m in scored) if scored else None,
            "mean_latency_ms": mean(r["latency_ms"] for r in rows if r["latency_ms"] is not None)
            if any(r["latency_ms"] is not None for r in rows)
            else None,
            "at_k": {
                str(k): {
                    key: mean(m["at_k"][str(k)][key] for m in scored) if scored else None
                    for key in ("recall", "evidence_fraction", "all_required")
                }
                for k in sorted(set(ks))
            },
        }
        category_metrics = {}
        for category in report["categories"]:
            subset = [
                r["metrics"] for r in rows if r["category"] == category and r["metrics"] and r["metrics"]["gold_count"]
            ]
            category_metrics[category] = {
                "scored_cases": len(subset),
                "mrr": mean(m["mrr"] for m in subset) if subset else None,
                "at_k": {
                    str(k): {
                        key: mean(m["at_k"][str(k)][key] for m in subset) if subset else None
                        for key in ("recall", "evidence_fraction", "all_required")
                    }
                    for k in sorted(set(ks))
                },
            }
        report["runs"].append(
            {
                "configuration": asdict(cfg),
                "aggregate": aggregate,
                "by_category": category_metrics,
                "questions": rows,
                "index_manifest": {
                    "corpus_fingerprint": fingerprint(corpus),
                    "chunking": cfg.chunking,
                    "embedding": cfg.embedding,
                },
            }
        )
    # Compare only mutually successful scored cases; failures cannot improve a mode's mean.
    report["comparisons"] = []
    base = report["runs"][0]
    for other in report["runs"][1:]:
        paired = [
            (a, b)
            for a, b in zip(base["questions"], other["questions"])
            if a["metrics"] and b["metrics"] and a["metrics"]["gold_count"]
        ]
        delta = mean(b["metrics"]["mrr"] - a["metrics"]["mrr"] for a, b in paired) if paired else None
        report["comparisons"].append(
            {
                "baseline": base["configuration"]["name"],
                "other": other["configuration"]["name"],
                "paired_cases": len(paired),
                "mrr_delta": delta,
                "at_k_deltas": {
                    str(k): {
                        key: mean(
                            b["metrics"]["at_k"][str(k)][key] - a["metrics"]["at_k"][str(k)][key] for a, b in paired
                        )
                        if paired
                        else None
                        for key in ("recall", "evidence_fraction", "all_required")
                    }
                    for k in sorted(set(ks))
                },
                "direction": "unavailable"
                if delta is None
                else "negligible"
                if abs(delta) < 1e-9
                else "positive"
                if delta > 0
                else "negative",
            }
        )
    report["total_elapsed_ms"] = (perf_counter() - started) * 1000
    return report


def export(report: dict, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "retrieval.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str, allow_nan=False), encoding="utf8"
    )
    lines = [
        "# Retrieval evaluation",
        "",
        f"Suite: {report['suite_version']}",
        f"Corpus: {report['corpus_fingerprint']}",
        "",
        "| Run | Scored cases | MRR (max K) | Mean latency ms |",
        "| --- | ---: | ---: | ---: |",
    ]
    for run in report["runs"]:
        a = run["aggregate"]
        lines.append(f"| {run['configuration']['name']} | {a['scored_cases']} | {a['mrr']} | {a['mean_latency_ms']} |")
    lines += [
        "",
        "See JSON for Recall@K, all-evidence coverage, per-question hits, failures and paired comparisons.",
        "Answer/citation/abstention/contradiction quality: unavailable; no generation evaluated.",
    ]
    (directory / "retrieval.md").write_text("\n".join(lines) + "\n", encoding="utf8")
