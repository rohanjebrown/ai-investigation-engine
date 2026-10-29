import json
from dataclasses import replace
from hashlib import sha256

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy
from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
from investigation_engine.infrastructure.persistence.in_memory_document_repository import InMemoryDocumentRepository
from investigation_engine.ingestion import corpus_processing as module
from investigation_engine.ingestion.corpus_processing import audit_corpus, process_corpus, source_documents


class Characters:
    identity = TokenizerIdentity(name="unit-characters", revision="1", artifact_sha256="0" * 64, runtime_version="test")

    def offsets(self, text):
        return tuple((i, i + 1) for i in range(len(text)))


class Plans:
    def __init__(self):
        self.values = {}
        self.fail_once = False

    async def save(self, plan):
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("SECRET_SENTINEL")
        self.values[plan.document_version_id] = plan
        return plan


@pytest.fixture
def fixture_corpus(tmp_path):
    path = tmp_path / "dev.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {"_id": "a", "context": json.dumps([["First", ["Exact 😀."]], ["Duplicate", ["Exact 😀."]]])},
                {"_id": "b", "context": json.dumps([["Third", ["Different."]]])},
            ]
        ),
        path,
    )
    return path, sha256(path.read_bytes()).hexdigest()


def test_manifest_is_deterministic_counts_occurrences_not_chunks_and_preserves_text(fixture_corpus):
    path, digest = fixture_corpus
    first = audit_corpus(path, Characters(), expected_sha256=digest, revision="fixture")
    assert first == audit_corpus(path, Characters(), expected_sha256=digest, revision="fixture")
    assert (first["passage_occurrences"], first["unique_exact_contents"], first["duplicate_occurrences"]) == (3, 2, 1)
    docs = list(source_documents(path, revision="fixture"))
    assert docs[0].text == "Exact 😀." and docs[0].source.record_locator["context_position"] == 0
    assert docs[1].source.record_locator["context_position"] == 1


async def test_partial_commit_replays_safely_and_completed_checkpoint_is_noop(fixture_corpus, tmp_path):
    path, digest = fixture_corpus
    repo, plans, tokenizer = InMemoryDocumentRepository(), Plans(), Characters()
    config = ChunkingConfig(strategy=ChunkingStrategy.WHOLE_PASSAGE, tokenizer=tokenizer.identity, max_tokens=100)
    checkpoint = tmp_path / "checkpoint.json"
    kwargs = dict(
        checkpoint_path=checkpoint,
        target_identity="database-A",
        batch_size=2,
        concurrency=2,
        expected_sha256=digest,
        revision="fixture",
        min_free_bytes=0,
    )
    plans.fail_once = True
    failed = await process_corpus(path, repo, plans, tokenizer, config, **kwargs)
    assert failed["completed_occurrences"] == 0 and failed["failure"]["failed_operations"] == 1
    assert "SECRET_SENTINEL" not in json.dumps(failed)
    assert failed["failure"]["operation"] == "save_chunk_plan"
    assert failed["failure"]["underlying_exception_type"] == "RuntimeError"
    assert not checkpoint.exists() and len(repo._documents) == 1
    original_ids = set(repo._documents)
    completed = await process_corpus(path, repo, plans, tokenizer, config, **kwargs)
    assert completed["completed_occurrences"] == 3
    assert len(repo._documents) == 3 and len(repo._contents) == 2 and len(plans.values) == 3
    assert original_ids <= set(repo._documents)
    replay = await process_corpus(path, repo, plans, tokenizer, config, **kwargs)
    assert replay["attempted_operations_this_run"] == 0
    assert replay["embeddings_generated"] == 0
    assert all(doc.current_version_id is None for doc in repo._documents.values())


async def test_changed_checkpoint_binding_and_input_fail_before_writes(fixture_corpus, tmp_path):
    path, digest = fixture_corpus
    repo, plans, tokenizer = InMemoryDocumentRepository(), Plans(), Characters()
    config = ChunkingConfig(strategy=ChunkingStrategy.WHOLE_PASSAGE, tokenizer=tokenizer.identity, max_tokens=100)
    kwargs = dict(
        checkpoint_path=tmp_path / "cursor.json",
        target_identity="A",
        batch_size=1,
        expected_sha256=digest,
        revision="fixture",
        min_free_bytes=0,
    )
    await process_corpus(path, repo, plans, tokenizer, config, stop_after=1, **kwargs)
    ids = set(repo._documents)
    with pytest.raises(ValueError, match="different"):
        await process_corpus(path, repo, plans, tokenizer, config, **{**kwargs, "target_identity": "B"})
    with pytest.raises(ValueError, match="different"):
        await process_corpus(path, repo, plans, tokenizer, replace(config, max_tokens=99), **kwargs)
    with pytest.raises(ValueError, match="pinned"):
        await process_corpus(path, repo, plans, tokenizer, config, **{**kwargs, "expected_sha256": "f" * 64})
    assert set(repo._documents) == ids


async def test_disk_guard_never_starts_registration(fixture_corpus, tmp_path):
    path, digest = fixture_corpus
    repo, plans, tokenizer = InMemoryDocumentRepository(), Plans(), Characters()
    config = ChunkingConfig(strategy=ChunkingStrategy.WHOLE_PASSAGE, tokenizer=tokenizer.identity, max_tokens=100)
    result = await process_corpus(
        path,
        repo,
        plans,
        tokenizer,
        config,
        checkpoint_path=tmp_path / "cursor.json",
        target_identity="A",
        expected_sha256=digest,
        revision="fixture",
        min_free_bytes=10**30,
    )
    assert result["failure"]["category"] == "local_disk_guard"
    assert not repo._documents


def test_atomic_checkpoint_retries_local_windows_lock_without_repeating_application_work(tmp_path, monkeypatch):
    path = tmp_path / "checkpoint.json"
    module.atomic_json(path, {"cursor": 1})
    original = type(path).replace
    attempts = []

    def locked_then_replace(self, target):
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError("simulated scanner lock")
        return original(self, target)

    monkeypatch.setattr(type(path), "replace", locked_then_replace)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    module.atomic_json(path, {"cursor": 2})
    assert len(attempts) == 3 and json.loads(path.read_text()) == {"cursor": 2}
    monkeypatch.setattr(type(path), "replace", lambda self, target: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(PermissionError):
        module.atomic_json(path, {"cursor": 3})
    assert json.loads(path.read_text()) == {"cursor": 2}


async def test_disjoint_source_ranges_form_complete_corpus_and_keep_original_positions(fixture_corpus, tmp_path):
    path, digest = fixture_corpus
    repository, plans, tokenizer = InMemoryDocumentRepository(), Plans(), Characters()
    config = ChunkingConfig(strategy=ChunkingStrategy.WHOLE_PASSAGE, tokenizer=tokenizer.identity, max_tokens=100)
    for start, end in ((0, 1), (1, 3)):
        result = await process_corpus(
            path,
            repository,
            plans,
            tokenizer,
            config,
            checkpoint_path=tmp_path / f"range-{start}.json",
            target_identity="A",
            expected_sha256=digest,
            revision="fixture",
            start_at=start,
            stop_after=end,
            min_free_bytes=0,
        )
        assert result["completed_occurrences"] == end
        assert result["successful_operations_this_run"] == end - start
    assert len(repository._documents) == 3 and len(repository._contents) == 2
    assert len(plans.values) == 3


def test_immutable_checkpoint_records_do_not_replace_locked_previous_records(tmp_path, monkeypatch):
    path = tmp_path / "progress.json"
    original = type(path).replace

    def reject_overwrite(self, target):
        if type(path)(target).exists():
            raise PermissionError("old file held by reader")
        return original(self, target)

    monkeypatch.setattr(type(path), "replace", reject_overwrite)
    for cursor in (1, 2, 3):
        module.save_checkpoint(path, {"format_version": 1, "identity": "A", "completed_occurrences": cursor})
    assert module.load_checkpoint(path)["completed_occurrences"] == 3
    folder = path.with_name(path.name + ".d")
    (folder / "incomplete.tmp").write_text('{"partial":')
    assert module.load_checkpoint(path)["completed_occurrences"] == 3
