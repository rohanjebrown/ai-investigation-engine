"""Offline 2Wiki audit and bounded application-service orchestration.

This is a trusted operator utility, not an upload API or a background worker.
Canonical text, identities, chunking and transactions remain in existing code.
"""

import argparse
import asyncio
import json
import math
import os
import shutil
import tempfile
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import asdict
from hashlib import file_digest, sha256
from pathlib import Path
from time import perf_counter
from uuid import uuid7

import pyarrow.parquet as pq

from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy, chunk_document
from investigation_engine.application.ingestion.extracted_document import ExtractedDocument
from investigation_engine.application.use_cases.register_document import RegisterDocument
from investigation_engine.domain.content import Content
from investigation_engine.domain.document_version import DocumentVersion
from investigation_engine.infrastructure.ingestion.twowiki_adapter import DATASET_ID, extract_2wiki
from investigation_engine.infrastructure.persistence.diagnostics import repository_diagnostic
from investigation_engine.infrastructure.tokenization.huggingface_tokenizer import (
    VOYAGE_REFERENCE_NAME,
    VOYAGE_REFERENCE_REVISION,
    VOYAGE_REFERENCE_SHA256,
    HuggingFaceTokenizer,
)

REVISION = "612bc5039a457880d9e7d84c3b0a4cf154b70e4f"
FILE_SHA256 = "c0d8b60b9026b728fb07ad74c5252a0f188f6942e8ba5c02df4dfa369502ea8d"


def fingerprint(value) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            json.dump(value, output, indent=2, ensure_ascii=False, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        # Windows scanners/readers can briefly deny replacement even after the
        # writer closes. Retry only this local file operation, never a DB/API call.
        for attempt in range(10):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # A stale temporary file is never used as a checkpoint.


def verify_input(path: Path, expected_sha256: str = FILE_SHA256) -> str:
    with Path(path).open("rb") as file:
        actual = file_digest(file, "sha256").hexdigest()
    if actual != expected_sha256:
        raise ValueError("Dataset file does not match the selected pinned snapshot")
    return actual


def load_checkpoint(path: Path) -> dict | None:
    """Read the newest immutable batch record, also accepting an older single file."""
    path = Path(path)
    folder = path.with_name(path.name + ".d")
    records = sorted(folder.glob("[0-9]*.json")) if folder.exists() else []
    candidates = ([path] if path.is_file() else []) + (records[-1:] if records else [])
    values = [json.loads(candidate.read_text(encoding="utf-8")) for candidate in candidates]
    return max(values, key=lambda value: value["completed_occurrences"]) if values else None


def save_checkpoint(path: Path, value: dict) -> None:
    # Do not replace a frequently observed Windows file. Each completed batch
    # gets one immutable small record; a partial temporary write is ignored.
    path = Path(path)
    folder = path.with_name(path.name + ".d")
    folder.mkdir(parents=True, exist_ok=True)
    record = folder / f"{value['completed_occurrences']:020d}.json"
    if record.exists():
        if json.loads(record.read_text(encoding="utf-8")) != value:
            raise ValueError("Conflicting checkpoint record")
        return
    atomic_json(record, value)


def source_documents(path: Path, *, revision: str = REVISION, batch_size: int = 64) -> Iterator[ExtractedDocument]:
    """Only _id/context columns are read; JSON parsing never normalizes text."""
    seen = set()
    with pq.ParquetFile(path) as parquet:
        for batch in parquet.iter_batches(batch_size=batch_size, columns=["_id", "context"]):
            for row in batch.to_pylist():
                record_id = row["_id"]
                try:
                    context = json.loads(row["context"])
                except TypeError, ValueError:
                    raise ValueError(f"2Wiki record {record_id!r}: invalid context JSON") from None
                extracted = extract_2wiki({"_id": record_id, "context": context}, split="dev", revision=revision)
                if record_id in seen:
                    raise ValueError("Duplicate 2Wiki example ID in selected snapshot")
                seen.add(record_id)
                yield from extracted


def tokenizer_from_file(path: Path) -> HuggingFaceTokenizer:
    return HuggingFaceTokenizer(
        Path(path),
        name=VOYAGE_REFERENCE_NAME,
        revision=VOYAGE_REFERENCE_REVISION,
        expected_sha256=VOYAGE_REFERENCE_SHA256,
    )


def configurations(tokenizer) -> tuple[ChunkingConfig, ...]:
    return (
        ChunkingConfig(strategy=ChunkingStrategy.WHOLE_PASSAGE, tokenizer=tokenizer.identity, max_tokens=32000),
        ChunkingConfig(
            strategy=ChunkingStrategy.FIXED_TOKENS, tokenizer=tokenizer.identity, max_tokens=128, overlap=16
        ),
        ChunkingConfig(strategy=ChunkingStrategy.SENTENCE_PACKING, tokenizer=tokenizer.identity, max_tokens=128),
    )


def version_for(extracted: ExtractedDocument) -> DocumentVersion:
    return DocumentVersion(
        document_id=uuid7(),
        version_number=1,
        content=Content(extracted.text),
        source=extracted.source,
        structure=extracted.structure,
        source_spans=extracted.source_spans,
    )


def distribution(histogram):
    total = sum(histogram.values())
    if not total:
        return {"count": 0, "min": None, "median": None, "p95": None, "max": None, "mean": None}

    def percentile(fraction):
        target, cumulative = math.ceil(fraction * total), 0
        for value, count in sorted(histogram.items()):
            cumulative += count
            if cumulative >= target:
                return value

    return {
        "count": total,
        "min": min(histogram),
        "median": percentile(0.5),
        "p95": percentile(0.95),
        "max": max(histogram),
        "mean": sum(key * count for key, count in histogram.items()) / total,
    }


def audit_corpus(path, tokenizer, *, expected_sha256=FILE_SHA256, revision=REVISION, records_path=None):
    digest = verify_input(path, expected_sha256)
    configs = configurations(tokenizer)
    contents, layouts, questions = set(), set(), set()
    ranges = [set() for _ in configs]
    cache = {}
    metrics = [
        {
            "memberships": 0,
            "split_occurrences": 0,
            "sentence_count": 0,
            "whole_sentences_covered": 0,
            "chars": Counter(),
            "tokens": Counter(),
        }
        for _ in configs
    ]
    total_bytes = unique_bytes = occurrences = 0
    ordered = sha256()
    output = Path(records_path).open("w", encoding="utf-8", newline="\n") if records_path else None
    try:
        for doc in source_documents(path, revision=revision):
            version = version_for(doc)
            content_hash = version.content.sha256
            size = len(doc.text.encode("utf-8"))
            total_bytes += size
            if content_hash not in contents:
                contents.add(content_hash)
                unique_bytes += size
            questions.add(doc.source.record_locator["example_id"])
            occurrences += 1
            structure_key = tuple((s.kind.value, s.start, s.end) for s in doc.structure)
            layout = (content_hash, structure_key)
            layouts.add(layout)
            if layout not in cache:
                plans = []
                for config in configs:
                    plan = chunk_document(version, config=config, tokenizer=tokenizer)
                    plans.append(tuple((c.start, c.end, len(tokenizer.offsets(c.text))) for c in plan.chunks))
                cache[layout] = tuple(plans)
            descriptor = {
                "record_locator": doc.source.record_locator,
                "content_sha256": content_hash,
                "characters": len(doc.text),
                "utf8_bytes": size,
                "sentence_spans_sha256": fingerprint([(s.start, s.end) for s in doc.source_spans]),
                "chunk_counts": [len(p) for p in cache[layout]],
            }
            line = json.dumps(descriptor, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
            ordered.update(line.encode("utf-8"))
            if output:
                output.write(line)
            for index, plan in enumerate(cache[layout]):
                metric = metrics[index]
                metric["memberships"] += len(plan)
                metric["split_occurrences"] += len(plan) > 1
                metric["sentence_count"] += len(doc.source_spans)
                metric["whole_sentences_covered"] += sum(
                    any(a <= span.start < span.end <= b for a, b, _ in plan) for span in doc.source_spans
                )
                for start, end, tokens in plan:
                    ranges[index].add((content_hash, start, end))
                    metric["chars"][end - start] += 1
                    metric["tokens"][tokens] += 1
    finally:
        if output:
            output.close()
    return {
        "manifest_version": "2wiki-corpus-v1",
        "dataset": DATASET_ID,
        "revision": revision,
        "splits": ["dev"],
        "selection": "complete pinned development split, original Parquet row/context order",
        "input_sha256": digest,
        "question_rows": len(questions),
        "passage_occurrences": occurrences,
        "unique_source_occurrences": occurrences,
        "unique_exact_contents": len(contents),
        "duplicate_occurrences": occurrences - len(contents),
        "excluded_records": 0,
        "occurrence_text_utf8_bytes": total_bytes,
        "unique_text_utf8_bytes": unique_bytes,
        "unique_sentence_layouts": len(layouts),
        "ordered_records_sha256": ordered.hexdigest(),
        "counting_limitation": "Exact-text uniqueness is a conservative baseline, not proof of semantic independence. Chunks are excluded.",
        "chunking": [
            {
                "configuration": asdict(config),
                "memberships": metric["memberships"],
                "unique_content_ranges": len(keys),
                "split_occurrences": metric["split_occurrences"],
                "sentences": metric["sentence_count"],
                "whole_sentences_covered": metric["whole_sentences_covered"],
                "characters": distribution(metric["chars"]),
                "reference_tokens": distribution(metric["tokens"]),
            }
            for config, metric, keys in zip(configs, metrics, ranges, strict=True)
        ],
    }


async def process_corpus(
    path,
    repository,
    plans,
    tokenizer,
    config,
    *,
    checkpoint_path,
    target_identity,
    batch_size=128,
    concurrency=8,
    stop_after=None,
    expected_sha256=FILE_SHA256,
    revision=REVISION,
    min_free_bytes=512 * 1024**2,
    progress=None,
    start_at=0,
):
    """Commit-before-checkpoint. Replay an interrupted batch with existing idempotent operations.

    One operator process owns the checkpoint. No distributed job/lease guarantee
    is implied. target_identity must identify the actual cluster and database.
    """
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError("Batch size must be between 1 and 1000")
    if type(concurrency) is not int or not 1 <= concurrency <= 32:
        raise ValueError("Concurrency must be between 1 and 32")
    if stop_after is not None and (type(stop_after) is not int or stop_after < 0):
        raise ValueError("stop_after must be a nonnegative absolute occurrence limit")
    if type(start_at) is not int or start_at < 0 or (stop_after is not None and start_at > stop_after):
        raise ValueError("Invalid source range")
    if not isinstance(target_identity, str) or not target_identity:
        raise ValueError("An explicit database identity is required")
    digest = verify_input(path, expected_sha256)
    identity_values = {
        "file_sha256": digest,
        "dataset_revision": revision,
        "selection": "complete-dev",
        "config": asdict(config),
        "owner_id": None,
        "target_identity": target_identity,
    }
    # Preserve this task's earlier unpartitioned checkpoints. Both signatures
    # bind the same input/configuration/database; the old form implies start 0.
    allowed_identities = {fingerprint(identity_values)} if start_at == 0 else set()
    identity = fingerprint({**identity_values, "range_start": start_at})
    allowed_identities.add(identity)
    checkpoint_path = Path(checkpoint_path)
    saved = load_checkpoint(checkpoint_path) or {
        "format_version": 1,
        "identity": identity,
        "completed_occurrences": start_at,
    }
    if saved.get("format_version") != 1 or saved.get("identity") not in allowed_identities:
        raise ValueError("Checkpoint belongs to a different dataset, configuration, owner or database")
    cursor = saved["completed_occurrences"]
    if type(cursor) is not int or cursor < start_at:
        raise ValueError("Invalid checkpoint cursor")
    started = perf_counter()
    timings = {
        "registration_worker_seconds": 0.0,
        "chunking_worker_seconds": 0.0,
        "plan_persistence_worker_seconds": 0.0,
    }
    attempted = succeeded = 0
    failure = None
    failure_diagnostics = {}
    semaphore = asyncio.Semaphore(concurrency)
    register = RegisterDocument(repository)

    async def one(extracted):
        nonlocal succeeded, attempted
        async with semaphore:
            attempted += 1
            operation = "register_document"
            try:
                begin = perf_counter()
                document, version = await register.execute(extracted, owner_id=None)
                timings["registration_worker_seconds"] += perf_counter() - begin
                operation = "build_chunk_plan"
                begin = perf_counter()
                plan = chunk_document(version, config=config, tokenizer=tokenizer)
                timings["chunking_worker_seconds"] += perf_counter() - begin
                operation = "save_chunk_plan"
                begin = perf_counter()
                await plans.save(plan)
                timings["plan_persistence_worker_seconds"] += perf_counter() - begin
            except Exception as error:
                failure_diagnostics[id(extracted)] = repository_diagnostic(error, operation)
                raise
            succeeded += 1

    async def flush(batch):
        nonlocal cursor, attempted, failure
        if shutil.disk_usage(checkpoint_path.resolve().parent).free < min_free_bytes:
            failure = {"category": "local_disk_guard", "error_type": "InsufficientFreeDisk"}
            return False
        # Establish the existing configuration once before parallel version
        # transactions; no uniqueness contract or schema change is introduced.
        if cursor == 0:
            results = await asyncio.gather(one(batch[0]), return_exceptions=True)
            if not isinstance(results[0], BaseException):
                results += await asyncio.gather(*(one(item) for item in batch[1:]), return_exceptions=True)
            else:
                results += [None] * (len(batch) - 1)
        else:
            results = await asyncio.gather(*(one(item) for item in batch), return_exceptions=True)
        errors = [
            (item, result) for item, result in zip(batch, results, strict=True) if isinstance(result, BaseException)
        ]
        if errors:
            item, error = errors[0]
            failure = {
                "category": "batch_failure",
                "error_type": type(error).__name__,
                **failure_diagnostics.get(id(item), {}),
                "record_locator": item.source.record_locator,
                "failed_operations": len(errors),
            }
            return False
        cursor += len(batch)
        save_checkpoint(checkpoint_path, {"format_version": 1, "identity": identity, "completed_occurrences": cursor})
        if progress:
            progress(cursor)
        return True

    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    batch = []
    seen = 0
    for ordinal, extracted in enumerate(source_documents(path, revision=revision)):
        seen = ordinal + 1
        if ordinal < saved["completed_occurrences"]:
            continue
        if stop_after is not None and ordinal >= stop_after:
            break
        batch.append(extracted)
        if len(batch) == batch_size:
            if not await flush(batch):
                batch = []
                break
            batch = []
    if batch and failure is None:
        await flush(batch)
    if cursor > seen:
        raise ValueError("Checkpoint exceeds the selected dataset")
    return {
        "input_sha256": digest,
        "checkpoint_identity": identity,
        "range_start": start_at,
        "resumed_from": saved["completed_occurrences"],
        "completed_occurrences": cursor,
        "successful_operations_this_run": succeeded,
        "attempted_operations_this_run": attempted,
        "duration_seconds": perf_counter() - started,
        **timings,
        "failure": failure,
        "embeddings_generated": 0,
        "activation_performed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("data/raw/2wiki/dev.parquet"))
    parser.add_argument("--tokenizer", type=Path, default=Path("artifacts/tokenizers/voyage-4/tokenizer.json"))
    parser.add_argument("--output", type=Path, default=Path(".cache/corpus-scale"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    start = perf_counter()
    manifest = audit_corpus(
        args.dataset, tokenizer_from_file(args.tokenizer), records_path=args.output / "records.jsonl"
    )
    atomic_json(args.output / "manifest.json", manifest)
    print(
        json.dumps(
            {
                "occurrences": manifest["passage_occurrences"],
                "unique_contents": manifest["unique_exact_contents"],
                "duration_seconds": perf_counter() - start,
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
