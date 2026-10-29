import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from investigation_engine.application.chunking import Chunk, ChunkingConfig, ChunkingStrategy, ChunkPlan
from investigation_engine.application.ports.embedding_errors import (
    EmbeddingInputError,
    EmbeddingProviderError,
    EmbeddingRateLimitError,
    EmbeddingResponseError,
)
from investigation_engine.application.use_cases.index_document_version import IndexDocumentVersion, IndexingOutcome


@pytest.fixture
def workflow(registration, tokenizer, synthetic_provider):
    document, version = registration(body="a b")
    config = ChunkingConfig(strategy=ChunkingStrategy.FIXED_TOKENS, tokenizer=tokenizer.identity, max_tokens=1)
    plan = ChunkPlan(
        document_version_id=version.id,
        config=config,
        chunks=tuple(Chunk(content=version.content, start=i, end=i + 1) for i in range(3)),
    )
    values = {}

    async def find(chunks, configuration):
        return {c.identity: values[c.identity] for c in chunks if c.identity in values}

    async def save(chunk, configuration, vector):
        return values.setdefault(chunk.identity, vector)

    plans = SimpleNamespace(get=AsyncMock(return_value=plan))
    store = SimpleNamespace(find=AsyncMock(side_effect=find), save=AsyncMock(side_effect=save))
    activation = SimpleNamespace(current_version=AsyncMock(return_value=None), activate=AsyncMock(return_value=True))
    usecase = IndexDocumentVersion(plans, store, activation, synthetic_provider, max_attempts=1, embedding_batch_size=1)
    return SimpleNamespace(
        document=document,
        version=version,
        config=config,
        plan=plan,
        provider=synthetic_provider,
        values=values,
        plans=plans,
        store=store,
        activation=activation,
        usecase=usecase,
    )


async def run(w):
    return await w.usecase.execute(w.document.id, w.version.id, w.config, w.provider.configuration)


async def test_indexes_only_nonblank_memberships_and_activates_after_full_coverage(workflow):
    w = workflow
    assert await run(w) == IndexingOutcome.ACTIVATED
    assert w.provider.calls == ["a", "b"] and len(w.values) == 2
    assert len(w.plan.chunks) == 3
    w.activation.activate.assert_awaited_once_with(
        w.document.id, w.version.id, w.config, w.provider.configuration, expected_current_version_id=None
    )


async def test_indexing_batches_missing_chunks_and_reuses_existing_embeddings(workflow):
    w = workflow
    w.usecase = IndexDocumentVersion(
        w.plans, w.store, w.activation, w.provider, max_attempts=1, embedding_batch_size=25
    )
    w.values[w.plan.chunks[0].identity] = (1.0,) + (0.0,) * 767
    assert await run(w) == IndexingOutcome.ACTIVATED
    assert w.provider.call_batches == [("b",)]
    assert len(w.values) == 2


async def test_bulk_indexing_batches_across_documents_and_reuses_equal_content(workflow, registration):
    w = workflow
    w.usecase = IndexDocumentVersion(
        w.plans, w.store, w.activation, w.provider, max_attempts=1, embedding_batch_size=25
    )
    second_document, second_version = registration(body="a b", locator={"record": 2})
    second_plan = replace(
        w.plan,
        document_version_id=second_version.id,
        chunks=tuple(Chunk(content=second_version.content, start=c.start, end=c.end) for c in w.plan.chunks),
    )

    async def get_plan(version_id, config):
        return w.plan if version_id == w.version.id else second_plan

    w.plans.get.side_effect = get_plan
    reports = await w.usecase.execute_many(
        ((w.document.id, w.version.id), (second_document.id, second_version.id)),
        w.config,
        w.provider.configuration,
    )
    assert w.provider.call_batches == [("a", "b")]
    assert [report.outcome for report in reports] == [IndexingOutcome.ACTIVATED] * 2
    assert [report.requested_embeddings for report in reports] == [2, 2]
    assert w.activation.activate.await_count == 2


async def test_bulk_indexing_preserves_progress_and_resumes_before_activation(workflow, registration):
    w = workflow
    w.usecase = IndexDocumentVersion(
        w.plans, w.store, w.activation, w.provider, max_attempts=1, embedding_batch_size=25
    )
    second_document, second_version = registration(body="a b", locator={"record": 2})
    second_plan = replace(
        w.plan,
        document_version_id=second_version.id,
        chunks=tuple(Chunk(content=second_version.content, start=c.start, end=c.end) for c in w.plan.chunks),
    )

    async def get_plan(version_id, config):
        return w.plan if version_id == w.version.id else second_plan

    w.plans.get.side_effect = get_plan
    partial = await w.usecase.execute_many(
        ((w.document.id, w.version.id), (second_document.id, second_version.id)),
        w.config,
        w.provider.configuration,
        max_new_embeddings=1,
    )
    assert all(report.outcome == IndexingOutcome.IN_PROGRESS for report in partial)
    w.activation.activate.assert_not_awaited()
    w.provider.call_batches.clear()
    resumed = await w.usecase.execute_many(
        ((w.document.id, w.version.id), (second_document.id, second_version.id)),
        w.config,
        w.provider.configuration,
    )
    assert w.provider.call_batches == [("b",)]
    assert [report.outcome for report in resumed] == [IndexingOutcome.ACTIVATED] * 2


@pytest.mark.parametrize("existing", [1, 2])
async def test_reuses_existing_vectors_and_repeated_work_is_idempotent(workflow, existing):
    w = workflow
    for chunk in (w.plan.chunks[0], w.plan.chunks[2])[:existing]:
        w.values[chunk.identity] = (1.0,) + (0.0,) * 767
    assert await run(w) == IndexingOutcome.ACTIVATED
    assert w.provider.calls == (["b"] if existing == 1 else [])
    w.activation.activate.return_value = False
    assert await run(w) == IndexingOutcome.ALREADY_ACTIVE
    assert len(w.provider.calls) == (1 if existing == 1 else 0)


async def test_failure_preserves_incremental_progress_and_new_workflow_resumes(workflow):
    w = workflow
    w.provider.failures = [None, EmbeddingResponseError("invalid response")]
    with pytest.raises(EmbeddingResponseError):
        await run(w)
    assert set(w.values) == {w.plan.chunks[0].identity}
    w.activation.activate.assert_not_awaited()
    w.provider.calls.clear()
    w.usecase = IndexDocumentVersion(w.plans, w.store, w.activation, w.provider, embedding_batch_size=1)
    assert await run(w) == IndexingOutcome.ACTIVATED
    assert w.provider.calls == ["b"]


async def test_cancellation_preserves_committed_progress_without_activation(workflow):
    w = workflow
    entered = asyncio.Event()

    async def pause_second():
        if len(w.provider.calls) == 2:
            entered.set()
            await asyncio.Future()

    w.provider.before_call = pause_second
    task = asyncio.create_task(run(w))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(w.values) == 1
    w.activation.activate.assert_not_awaited()


@pytest.mark.parametrize(
    "error",
    [EmbeddingInputError("permanent"), EmbeddingResponseError("invalid"), EmbeddingProviderError("unauthorized")],
)
async def test_permanent_errors_are_not_retried(workflow, error):
    w = workflow
    w.provider.failures = [error]
    w.usecase = IndexDocumentVersion(
        w.plans, w.store, w.activation, w.provider, max_attempts=3, retry_delay_seconds=0, embedding_batch_size=1
    )
    with pytest.raises(type(error)):
        await run(w)
    assert w.provider.calls == ["a"] and not w.values
    w.activation.activate.assert_not_awaited()


@pytest.mark.parametrize(
    "error", [EmbeddingRateLimitError("quota"), EmbeddingProviderError("unavailable", retryable=True)]
)
async def test_transient_errors_have_bounded_configurable_retries(workflow, error, monkeypatch):
    w = workflow
    delays = []

    async def wait(delay):
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", wait)
    w.provider.failures = [error, None]
    w.usecase = IndexDocumentVersion(
        w.plans, w.store, w.activation, w.provider, max_attempts=2, retry_delay_seconds=0.25, embedding_batch_size=1
    )
    assert await run(w) == IndexingOutcome.ACTIVATED
    assert w.provider.calls == ["a", "a", "b"] and delays == [0.25]


async def test_timeout_exhausts_attempts_without_activating(workflow):
    w = workflow

    async def never_returns():
        await asyncio.Future()

    w.provider.before_call = never_returns
    w.usecase = IndexDocumentVersion(
        w.plans,
        w.store,
        w.activation,
        w.provider,
        max_attempts=2,
        retry_delay_seconds=0,
        request_timeout_seconds=0.001,
        embedding_batch_size=1,
    )
    with pytest.raises(EmbeddingProviderError, match="timed out"):
        await run(w)
    assert w.provider.calls == ["a", "a"] and not w.values
    w.activation.activate.assert_not_awaited()


@pytest.mark.parametrize("body", ["", "   "])
async def test_no_searchable_content_is_an_explicit_outcome(workflow, registration, body):
    w = workflow
    _, version = registration(body=body)
    chunks = () if not body else (Chunk(content=version.content, start=0, end=len(body)),)
    w.plans.get.return_value = replace(w.plan, chunks=chunks)
    assert await run(w) == IndexingOutcome.NO_SEARCHABLE_CONTENT
    assert not w.provider.calls
    w.store.save.assert_not_awaited()
    w.activation.activate.assert_not_awaited()


async def test_configuration_mismatch_and_missing_plan_are_rejected(workflow):
    w = workflow
    with pytest.raises(EmbeddingInputError, match="configuration"):
        await w.usecase.execute(
            w.document.id, w.version.id, w.config, replace(w.provider.configuration, model_revision="other")
        )
    w.activation.current_version.assert_not_awaited()
    w.plans.get.return_value = None
    with pytest.raises(ValueError, match="persisted chunk plan"):
        await run(w)
    assert not w.provider.calls


@pytest.mark.parametrize("result", [(), ((0.0,) * 768,), ((float("nan"),) + (0.0,) * 767,)])
async def test_invalid_provider_output_is_not_persisted_or_activated(workflow, result):
    w = workflow
    w.provider.embed_documents = AsyncMock(return_value=result)
    with pytest.raises(EmbeddingResponseError):
        await run(w)
    w.store.save.assert_not_awaited()
    w.activation.activate.assert_not_awaited()


async def test_final_completeness_check_cannot_be_bypassed_by_a_bad_store(workflow):
    w = workflow
    w.store.save.side_effect = None
    with pytest.raises(EmbeddingResponseError, match="coverage"):
        await run(w)
    w.activation.activate.assert_not_awaited()


async def test_provider_configuration_change_cannot_mislabel_generated_vectors(workflow):
    w = workflow

    async def change_profile():
        w.provider.configuration = replace(w.provider.configuration, model_revision="changed")

    w.provider.before_call = change_profile
    selected = w.provider.configuration
    with pytest.raises(EmbeddingInputError, match="configuration changed"):
        await w.usecase.execute(w.document.id, w.version.id, w.config, selected)
    w.store.save.assert_not_awaited()
    w.activation.activate.assert_not_awaited()


async def test_wrong_persisted_plan_cannot_be_indexed_for_another_request(workflow):
    w = workflow
    w.plans.get.return_value = replace(w.plan, config=replace(w.config, max_tokens=2))
    with pytest.raises(ValueError, match="complete persisted"):
        await run(w)
    assert not w.provider.calls
