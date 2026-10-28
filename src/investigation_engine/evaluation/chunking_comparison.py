"""Matched structural comparison of existing chunkers; no retrieval-quality claim."""

from collections import Counter
from dataclasses import asdict
import argparse
import json
from pathlib import Path
from investigation_engine.application.chunking import chunk_document
from investigation_engine.application.ingestion.extracted_document import ExtractedDocument
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.domain.structural_span import StructuralSpan, StructureKind
from investigation_engine.evaluation.retrieval import occurrence, validate_suite, fingerprint
from investigation_engine.ingestion.corpus_processing import (
    atomic_json, configurations, distribution, tokenizer_from_file, version_for,
)


def extracted_from_manifest(row):
    return ExtractedDocument(text=row["text"], title=row["title"],
        source=SourceReference(**{**row["source"], "kind":SourceKind(row["source"]["kind"])}),
        structure=tuple(StructuralSpan(kind=StructureKind(s["kind"]), start=s["start"], end=s["end"])
                        for s in row["structure"]),
        source_spans=tuple(SourceSpan(start=s["start"], end=s["end"], locators=tuple(
            SourceLocator(kind=LocatorKind(loc["kind"]), index=loc["index"]) for loc in s["locators"]))
            for s in row["source_spans"]))


def compare_structural_chunking(suite, corpus, tokenizer):
    validate_suite(suite, corpus)
    results = []
    for config in configurations(tokenizer):
        plans = {}
        chars, tokens = Counter(), Counter()
        unique_ranges = set()
        total_chars = 0
        for row in corpus["documents"]:
            version = version_for(extracted_from_manifest(row))
            plan = chunk_document(version, config=config, tokenizer=tokenizer)
            plans[occurrence(row["source"])] = plan
            total_chars += len(row["text"])
            for chunk in plan.chunks:
                chars[len(chunk.text)] += 1
                tokens[len(tokenizer.offsets(chunk.text))] += 1
                unique_ranges.add(chunk.identity)
        cases = []
        for case in suite["cases"]:
            covered_facts = 0
            for fact in case["required_evidence"]:
                plan = plans[occurrence(fact["source"])]
                covered_facts += any(chunk.content_sha256 == fact["content_sha256"]
                    and chunk.start <= fact["start"] < fact["end"] <= chunk.end for chunk in plan.chunks)
            cases.append({"id":case["id"], "required_facts":len(case["required_evidence"]),
                "facts_wholly_contained_in_one_chunk":covered_facts,
                "all_required_facts_covered":covered_facts == len(case["required_evidence"]) if case["required_evidence"] else None,
                "retrieval_quality":None})
        results.append({"configuration":asdict(config), "memberships":sum(chars.values()),
            "distinct_content_ranges":len(unique_ranges), "character_sizes":distribution(chars),
            "reference_token_sizes":distribution(tokens),
            "repeated_characters_from_overlap":sum(k*v for k,v in chars.items())-total_chars,
            "raw_vector_value_bytes_estimate":len(unique_ranges)*768*4,
            "required_gold_facts":sum(c["required_facts"] for c in cases),
            "gold_facts_contained_in_one_chunk":sum(c["facts_wholly_contained_in_one_chunk"] for c in cases),
            "cases":cases})
    return {"kind":"structural_only", "suite_fingerprint":fingerprint(suite),
        "corpus_fingerprint":fingerprint(corpus), "source_occurrences":len(corpus["documents"]),
        "question_count":len(suite["cases"]), "strategies":results,
        "limitation":"All source ranges are available structurally; no ranking or semantic retrieval is performed. Vector byte estimates exclude row/index/TOAST overhead."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite",type=Path,default=Path("data/samples/evaluation/suite.json"))
    parser.add_argument("--corpus",type=Path,default=Path("data/samples/evaluation/corpus.json"))
    parser.add_argument("--tokenizer",type=Path,default=Path("artifacts/tokenizers/voyage-4/tokenizer.json"))
    parser.add_argument("--output",type=Path,default=Path(".cache/corpus-scale/chunking-comparison.json"))
    args = parser.parse_args()
    result = compare_structural_chunking(json.loads(args.suite.read_text(encoding="utf-8")),
        json.loads(args.corpus.read_text(encoding="utf-8")),tokenizer_from_file(args.tokenizer))
    atomic_json(args.output,result)
    print(json.dumps({"source_occurrences":result["source_occurrences"],"questions":result["question_count"],
        "strategies":[{"strategy":s["configuration"]["strategy"],"memberships":s["memberships"],
                       "gold_facts_contained":s["gold_facts_contained_in_one_chunk"]} for s in result["strategies"]]}))
