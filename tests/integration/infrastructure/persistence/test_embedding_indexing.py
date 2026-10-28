import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid7

import pytest
from sqlalchemy import event, func, insert, select, text, update
from sqlalchemy.exc import OperationalError

from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy, chunk_document
from investigation_engine.application.chunking import Chunk
from investigation_engine.application.embeddings import embedding_eligible, storage_vector
from investigation_engine.application.ports.embedding_errors import EmbeddingInputError, EmbeddingResponseError
from investigation_engine.application.ports.repository_errors import ActivationConflict, RepositoryError
from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
from investigation_engine.application.use_cases.index_document_version import IndexDocumentVersion, IndexingOutcome
from investigation_engine.domain.content import Content
from investigation_engine.infrastructure.persistence._document_mapping import _version_row
from investigation_engine.infrastructure.persistence.models import (
    ChunkEmbeddingRow, DocumentRow, EmbeddingConfigurationRow, VersionChunkPlanRow,
)
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import PostgreSQLChunkPlanRepository
from investigation_engine.infrastructure.persistence.postgresql_document_activation_repository import PostgreSQLDocumentActivationRepository
from investigation_engine.infrastructure.persistence.postgresql_embedding_repository import PostgreSQLEmbeddingRepository


@pytest.fixture
def services(sessions, synthetic_provider):
    plans = PostgreSQLChunkPlanRepository(sessions)
    vectors = PostgreSQLEmbeddingRepository(sessions)
    activation = PostgreSQLDocumentActivationRepository(sessions)
    workflow = IndexDocumentVersion(plans, vectors, activation, synthetic_provider, max_attempts=1,
                                   embedding_batch_size=1)
    return SimpleNamespace(plans=plans, vectors=vectors, activation=activation,
                           workflow=workflow, provider=synthetic_provider, profile=synthetic_provider.configuration)


@pytest.fixture
def registered_plan(repository, registration, services):
    class Tokenizer:
        identity = TokenizerIdentity(name="characters", revision="1", artifact_sha256="0" * 64, runtime_version="test")

        def offsets(self, text):
            return tuple((i, i + 1) for i in range(len(text)))

    config = ChunkingConfig(strategy=ChunkingStrategy.FIXED_TOKENS, tokenizer=Tokenizer.identity, max_tokens=1)

    async def create(body="a b", source_id="index-source"):
        document, version = await repository.register(*registration(body=body, source_id=source_id))
        plan = chunk_document(version, config=config, tokenizer=Tokenizer())
        await services.plans.save(plan)
        return SimpleNamespace(document=document, version=version, plan=plan, config=config)
    return create


async def index(services, item):
    return await services.workflow.execute(item.document.id, item.version.id, item.config, services.profile)


def vector_count(engine):
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(ChunkEmbeddingRow))


def state(engine, document_id):
    with engine.connect() as connection:
        return connection.execute(select(DocumentRow.current_version_id, DocumentRow.deleted_at)
                                  .where(DocumentRow.id == document_id)).one()


async def seed_later_version(sessions, services, item, number):
    # Test setup only: production cross-revision registration remains unsupported.
    version = replace(item.version, id=uuid7(), version_number=number,
                      source=replace(item.version.source, revision=f"revision-{number}"))
    async with sessions() as session:
        async with session.begin():
            session.add(_version_row(version))
    plan = replace(item.plan, document_version_id=version.id)
    await services.plans.save(plan)
    return SimpleNamespace(document=item.document, version=version, plan=plan, config=item.config)


async def store_complete(services, item, profile=None):
    for chunk in item.plan.chunks:
        if embedding_eligible(chunk):
            await services.vectors.save(chunk, profile or services.profile, (0.1, 2.0) + (0.0,) * 766)


async def test_float32_vectors_survive_new_repository_and_retries_never_overwrite(
    services, registered_plan, sessions, db_engine,
):
    item = await registered_plan()
    chunk = item.plan.chunks[0]
    vector = (0.1, 2.0) + (0.0,) * 766
    saved = await services.vectors.save(chunk, services.profile, vector)
    assert saved == storage_vector(vector)
    restarted = PostgreSQLEmbeddingRepository(sessions)
    assert await restarted.find((chunk,), services.profile) == {chunk.identity: saved}
    assert await restarted.save(chunk, services.profile, (5.0,) + (0.0,) * 767) == saved
    assert vector_count(db_engine) == 1


@pytest.mark.parametrize("changes", [dict(model_revision=None), dict(model_id="other-model"), dict(provider="other-provider")])
async def test_content_ranges_reuse_across_independent_versions_but_profiles_do_not(
    services, registered_plan, db_engine, changes,
):
    a, b = await registered_plan(source_id="first"), await registered_plan(source_id="second")
    first = a.plan.chunks[0]
    second = b.plan.chunks[0]
    assert a.version.id != b.version.id and first.identity == second.identity
    vector = await services.vectors.save(first, services.profile, (1.0,) + (0.0,) * 767)
    assert await services.vectors.find((second,), services.profile) == {second.identity: vector}
    other = replace(services.profile, **changes)
    assert await services.vectors.find((second,), other) == {}
    changed = await services.vectors.save(second, other, (2.0,) + (0.0,) * 767)
    assert changed != vector and vector_count(db_engine) == 2


async def test_legacy_equivalent_configuration_rows_are_reused_without_extra_vectors(
    services, registered_plan, db_engine,
):
    item = await registered_plan()
    chunk = item.plan.chunks[0]
    values = dict(provider=services.profile.provider, model_id=services.profile.model_id,
                  model_revision=services.profile.model_revision, dimensions=768,
                  document_format=services.profile.document_format, normalization="none", created_at=datetime.now(UTC))
    with db_engine.begin() as connection:
        connection.execute(insert(EmbeddingConfigurationRow).values(**values))
        duplicate_id = connection.scalar(insert(EmbeddingConfigurationRow).values(**values).returning(EmbeddingConfigurationRow.id))
        connection.execute(insert(ChunkEmbeddingRow).values(
            embedding_configuration_id=duplicate_id, content_sha256=bytes.fromhex(chunk.content_sha256),
            start=chunk.start, end=chunk.end, embedding=[7.0] + [0.0] * 767, created_at=datetime.now(UTC)))
    stored = await services.vectors.save(chunk, services.profile, (9.0,) + (0.0,) * 767)
    assert stored[0] == 7.0 and vector_count(db_engine) == 1


async def test_embedding_failure_rolls_back_new_configuration_and_vector(
    services, registered_plan, sessions, db_engine,
):
    item = await registered_plan()
    fired = False

    def fail(connection, cursor, statement, parameters, context, executemany):
        nonlocal fired
        compiled = context.compiled
        if compiled is not None and compiled.statement.is_insert and compiled.statement.table.name == "chunk_embeddings":
            fired = True
            raise OperationalError("injected embedding failure", {}, RuntimeError("injected"))

    engine = sessions.kw["bind"].sync_engine
    event.listen(engine, "after_cursor_execute", fail)
    try:
        with pytest.raises(RepositoryError, match="save failed"):
            await services.vectors.save(item.plan.chunks[0], services.profile, (1.0,) + (0.0,) * 767)
    finally:
        event.remove(engine, "after_cursor_execute", fail)
    assert fired and vector_count(db_engine) == 0
    with db_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(EmbeddingConfigurationRow)) == 0
    assert (await services.vectors.save(item.plan.chunks[0], services.profile, (1.0,) + (0.0,) * 767))[0] == 1.0


async def test_hash_match_alone_cannot_reuse_or_store_vectors_for_other_text(services, registered_plan, db_engine):
    item = await registered_plan()
    await services.vectors.save(item.plan.chunks[0], services.profile, (1.0,) + (0.0,) * 767)
    other = Content("x")
    object.__setattr__(other, "sha256", item.version.content.sha256)
    chunk = Chunk(content=other, start=0, end=1)
    with pytest.raises(ValueError, match="exact registered Content"):
        await services.vectors.find((chunk,), services.profile)
    with pytest.raises(ValueError, match="exact registered Content"):
        await services.vectors.save(chunk, services.profile, (2.0,) + (0.0,) * 767)
    assert vector_count(db_engine) == 1


@pytest.mark.parametrize("vector", [(1.0,) * 767, (0.0,) * 768, (float("nan"),) + (0.0,) * 767,
                                     (float("inf"),) + (0.0,) * 767, (1e100,) * 768, (1e-100,) * 768])
async def test_invalid_vectors_leave_no_configuration_or_embedding_rows(services, registered_plan, db_engine, vector):
    item = await registered_plan()
    with pytest.raises(ValueError):
        await services.vectors.save(item.plan.chunks[0], services.profile, vector)
    assert vector_count(db_engine) == 0
    with db_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(EmbeddingConfigurationRow)) == 0


async def test_concurrent_identical_saves_use_one_configuration_and_one_vector(
    services, registered_plan, sessions, db_engine,
):
    item = await registered_plan()
    chunk = item.plan.chunks[0]
    ready = asyncio.Event()
    pids = set()
    arrivals = 0

    class RacingSession(sessions.class_):
        async def scalars(self, statement, *args, **kwargs):
            nonlocal arrivals
            result = await super().scalars(statement, *args, **kwargs)
            if any(c.get("entity") is ChunkEmbeddingRow for c in getattr(statement, "column_descriptions", ())) and arrivals < 2:
                pids.add(await super().scalar(text("SELECT pg_backend_pid()")))
                arrivals += 1
                if arrivals == 2:
                    ready.set()
                await asyncio.wait_for(ready.wait(), 10)
            return result

    factory = type(sessions)(sessions.kw["bind"], class_=RacingSession, expire_on_commit=False)
    vector = (1.0,) + (0.0,) * 767
    result = await asyncio.wait_for(asyncio.gather(
        PostgreSQLEmbeddingRepository(factory).save(chunk, services.profile, vector),
        PostgreSQLEmbeddingRepository(factory).save(chunk, services.profile, vector),
    ), 20)
    assert result == [vector, vector] and len(pids) == 2
    assert vector_count(db_engine) == 1
    with db_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(EmbeddingConfigurationRow)) == 1


async def test_indexing_commits_complete_eligible_coverage_and_restarts_without_generation(
    services, registered_plan, sessions, db_engine, synthetic_provider,
):
    item = await registered_plan()
    assert await index(services, item) == IndexingOutcome.ACTIVATED
    assert services.provider.calls == ["a", "b"] and vector_count(db_engine) == 2
    assert state(db_engine, item.document.id).current_version_id == item.version.id
    synthetic_provider.calls.clear()
    restarted = IndexDocumentVersion(PostgreSQLChunkPlanRepository(sessions), PostgreSQLEmbeddingRepository(sessions),
                                    PostgreSQLDocumentActivationRepository(sessions), synthetic_provider)
    assert await restarted.execute(item.document.id, item.version.id, item.config, services.profile) == IndexingOutcome.ALREADY_ACTIVE
    assert not synthetic_provider.calls
    assert len((await services.plans.get(item.version.id, item.config)).chunks) == 3


async def test_partial_cache_generates_only_missing_chunk(services, registered_plan):
    item = await registered_plan()
    await services.vectors.save(item.plan.chunks[0], services.profile, (1.0,) + (0.0,) * 767)
    assert await index(services, item) == IndexingOutcome.ACTIVATED
    assert services.provider.calls == ["b"]


async def test_two_indexers_can_race_without_duplicate_storage_or_invalid_activation(
    services, registered_plan, db_engine,
):
    item = await registered_plan()
    ready = asyncio.Event()
    arrivals = 0

    async def meet():
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 10)

    services.provider.before_call = meet
    results = await asyncio.wait_for(asyncio.gather(index(services, item), index(services, item)), 20)
    assert set(results) == {IndexingOutcome.ACTIVATED, IndexingOutcome.ALREADY_ACTIVE}
    assert vector_count(db_engine) == 2
    assert state(db_engine, item.document.id).current_version_id == item.version.id


async def test_provider_failure_commits_partial_progress_and_retry_resumes(services, registered_plan, db_engine):
    item = await registered_plan()
    services.provider.failures = [None, EmbeddingResponseError("synthetic invalid response")]
    with pytest.raises(EmbeddingResponseError):
        await index(services, item)
    assert vector_count(db_engine) == 1 and state(db_engine, item.document.id).current_version_id is None
    services.provider.calls.clear()
    assert await index(services, item) == IndexingOutcome.ACTIVATED
    assert services.provider.calls == ["b"] and vector_count(db_engine) == 2


async def test_bulk_indexing_persists_unique_shared_vectors_and_resumes_across_documents(
    services, registered_plan, db_engine,
):
    first, second = await registered_plan(source_id="bulk-first"), await registered_plan(source_id="bulk-second")
    assert first.plan.chunks[0].identity == second.plan.chunks[0].identity
    services.workflow = IndexDocumentVersion(
        services.plans, services.vectors, services.activation, services.provider,
        max_attempts=1, embedding_batch_size=25,
    )
    targets = ((first.document.id, first.version.id), (second.document.id, second.version.id))
    partial = await services.workflow.execute_many(
        targets, first.config, services.profile, max_new_embeddings=1,
    )
    assert {row.outcome for row in partial} == {IndexingOutcome.IN_PROGRESS}
    assert vector_count(db_engine) == 1
    assert state(db_engine, first.document.id).current_version_id is None
    assert state(db_engine, second.document.id).current_version_id is None

    services.provider.call_batches.clear()
    complete = await services.workflow.execute_many(targets, first.config, services.profile)
    assert {row.outcome for row in complete} == {IndexingOutcome.ACTIVATED}
    assert services.provider.call_batches == [("b",)]
    assert vector_count(db_engine) == 2
    assert state(db_engine, first.document.id).current_version_id == first.version.id
    assert state(db_engine, second.document.id).current_version_id == second.version.id


async def test_no_connection_is_checked_out_during_provider_calls(services, registered_plan, sessions):
    item = await registered_plan()
    active = 0

    def checkout(*args):
        nonlocal active
        active += 1

    def checkin(*args):
        nonlocal active
        active -= 1

    async def verify():
        assert active == 0

    engine = sessions.kw["bind"].sync_engine
    event.listen(engine, "checkout", checkout)
    event.listen(engine, "checkin", checkin)
    services.provider.before_call = verify
    try:
        assert await index(services, item) == IndexingOutcome.ACTIVATED
        assert services.provider.calls == ["a", "b"] and active == 0
    finally:
        event.remove(engine, "checkout", checkout)
        event.remove(engine, "checkin", checkin)


async def test_cancelled_indexing_retains_committed_vectors_for_recovery(services, registered_plan, db_engine):
    item = await registered_plan()
    entered = asyncio.Event()

    async def pause():
        if len(services.provider.calls) == 2:
            entered.set()
            await asyncio.Future()

    services.provider.before_call = pause
    task = asyncio.create_task(index(services, item))
    try:
        await asyncio.wait_for(entered.wait(), 10)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert vector_count(db_engine) == 1 and state(db_engine, item.document.id).current_version_id is None
    services.provider.before_call = None
    services.provider.calls.clear()
    assert await index(services, item) == IndexingOutcome.ACTIVATED
    assert services.provider.calls == ["b"]


@pytest.mark.parametrize("body", ["", "   "])
async def test_no_searchable_content_neither_embeds_nor_activates(services, registered_plan, db_engine, body):
    item = await registered_plan(body=body)
    assert await index(services, item) == IndexingOutcome.NO_SEARCHABLE_CONTENT
    assert not services.provider.calls and vector_count(db_engine) == 0
    with pytest.raises(ActivationConflict, match="eligible"):
        await services.activation.activate(item.document.id, item.version.id, item.config, services.profile,
                                           expected_current_version_id=None)
    assert state(db_engine, item.document.id).current_version_id is None


async def test_incompatible_profile_and_incomplete_embeddings_cannot_activate(services, registered_plan, db_engine):
    item = await registered_plan()
    await services.vectors.save(item.plan.chunks[0], services.profile, (1.0,) + (0.0,) * 767)
    with pytest.raises(ActivationConflict):
        await services.activation.activate(item.document.id, item.version.id, item.config, services.profile,
                                           expected_current_version_id=None)
    await store_complete(services, item)
    other = replace(services.profile, model_revision="other")
    with pytest.raises(ActivationConflict):
        await services.activation.activate(item.document.id, item.version.id, item.config, other,
                                           expected_current_version_id=None)
    with pytest.raises(EmbeddingInputError):
        await services.workflow.execute(item.document.id, item.version.id, item.config, other)
    assert state(db_engine, item.document.id).current_version_id is None


async def test_deleted_missing_and_wrong_owner_version_are_rejected(services, registered_plan, db_engine):
    item = await registered_plan()
    other = await registered_plan(source_id="other")
    await store_complete(services, item)
    for document_id, version_id in ((uuid7(), item.version.id), (item.document.id, uuid7()),
                                     (other.document.id, item.version.id)):
        with pytest.raises(ActivationConflict):
            await services.activation.activate(document_id, version_id, item.config, services.profile,
                                               expected_current_version_id=None)
    with db_engine.begin() as connection:
        connection.execute(update(DocumentRow).where(DocumentRow.id == item.document.id).values(deleted_at=datetime.now(UTC)))
    with pytest.raises(ActivationConflict, match="Deleted"):
        await services.activation.activate(item.document.id, item.version.id, item.config, services.profile,
                                           expected_current_version_id=None)
    assert state(db_engine, item.document.id).current_version_id is None


async def test_corrupt_plan_cannot_be_activated(services, registered_plan, db_engine):
    item = await registered_plan()
    await store_complete(services, item)
    with db_engine.begin() as connection:
        connection.execute(update(VersionChunkPlanRow).values(expected_chunk_count=999))
    with pytest.raises(RepositoryError, match="incomplete"):
        await services.activation.activate(item.document.id, item.version.id, item.config, services.profile,
                                           expected_current_version_id=None)
    assert state(db_engine, item.document.id).current_version_id is None


async def test_missing_selected_plan_cannot_activate(services, registered_plan, repository, registration, db_engine):
    sample = await registered_plan()
    document, version = await repository.register(*registration(source_id="no-plan"))
    with pytest.raises(ActivationConflict, match="selected chunk plan"):
        await services.activation.activate(document.id, version.id, sample.config, services.profile,
                                           expected_current_version_id=None)
    assert state(db_engine, document.id).current_version_id is None


async def test_failed_replacement_preserves_previous_active_version(services, registered_plan, sessions, db_engine):
    old = await registered_plan()
    assert await index(services, old) == IndexingOutcome.ACTIVATED
    new = await seed_later_version(sessions, services, old, 2)
    services.profile = services.provider.configuration = replace(services.profile, model_revision="2")
    services.provider.failures = [None, EmbeddingResponseError("synthetic failure")]
    with pytest.raises(EmbeddingResponseError):
        await index(services, new)
    assert state(db_engine, old.document.id).current_version_id == old.version.id
    assert await index(services, new) == IndexingOutcome.ACTIVATED
    assert state(db_engine, old.document.id).current_version_id == new.version.id


async def test_stale_and_older_activations_cannot_replace_newer_version(services, registered_plan, sessions, db_engine):
    old = await registered_plan()
    new = await seed_later_version(sessions, services, old, 2)
    snapshot = await services.activation.current_version(old.document.id, old.version.id)
    await store_complete(services, new)
    assert await services.activation.activate(new.document.id, new.version.id, new.config, services.profile,
                                             expected_current_version_id=snapshot)
    for expected in (snapshot, new.version.id):
        with pytest.raises(ActivationConflict):
            await services.activation.activate(old.document.id, old.version.id, old.config, services.profile,
                                               expected_current_version_id=expected)
    assert state(db_engine, old.document.id).current_version_id == new.version.id


async def test_deletion_during_generation_prevents_activation(services, registered_plan, db_engine):
    item = await registered_plan()

    async def delete():
        if len(services.provider.calls) == 2:
            with db_engine.begin() as connection:
                connection.execute(update(DocumentRow).where(DocumentRow.id == item.document.id).values(deleted_at=datetime.now(UTC)))

    services.provider.before_call = delete
    with pytest.raises(ActivationConflict, match="Deleted"):
        await index(services, item)
    assert vector_count(db_engine) == 2
    assert state(db_engine, item.document.id).current_version_id is None


async def wait_for_lock(engine, pid):
    async def poll():
        while True:
            with engine.connect() as connection:
                waiting = connection.scalar(text("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"), {"pid": pid})
            if waiting:
                return
            await asyncio.sleep(0)
    await asyncio.wait_for(poll(), 10)


@pytest.mark.parametrize("first", ["delete", "activate"])
async def test_activation_and_deletion_serialize_on_the_document_row(
    services, registered_plan, sessions, db_engine, first,
):
    item = await registered_plan()
    await store_complete(services, item)
    reached = asyncio.Event()
    release = asyncio.Event()
    pid = None

    class ActivationSession(sessions.class_):
        async def scalar(self, statement, *args, **kwargs):
            nonlocal pid
            document_lock = getattr(statement, "_for_update_arg", None) is not None and any(
                c.get("entity") is DocumentRow for c in getattr(statement, "column_descriptions", ())
            )
            if document_lock and first == "delete":
                pid = await super().scalar(text("SELECT pg_backend_pid()"))
                reached.set()
            result = await super().scalar(statement, *args, **kwargs)
            if document_lock and first == "activate":
                reached.set()
                await asyncio.wait_for(release.wait(), 10)
            return result

    factory = type(sessions)(sessions.kw["bind"], class_=ActivationSession, expire_on_commit=False)

    async def activate():
        return await PostgreSQLDocumentActivationRepository(factory).activate(
            item.document.id, item.version.id, item.config, services.profile, expected_current_version_id=None)

    if first == "delete":
        async with sessions() as deletion:
            async with deletion.begin():
                await deletion.execute(update(DocumentRow).where(DocumentRow.id == item.document.id).values(deleted_at=datetime.now(UTC)))
                task = asyncio.create_task(activate())
                await asyncio.wait_for(reached.wait(), 10)
                await wait_for_lock(db_engine, pid)
        with pytest.raises(ActivationConflict, match="Deleted"):
            await asyncio.wait_for(task, 10)
        assert state(db_engine, item.document.id).current_version_id is None
    else:
        task = asyncio.create_task(activate())
        try:
            await asyncio.wait_for(reached.wait(), 10)
            async with sessions() as deletion:
                async with deletion.begin():
                    deletion_pid = await deletion.scalar(text("SELECT pg_backend_pid()"))
                    deleting = asyncio.create_task(deletion.execute(update(DocumentRow)
                        .where(DocumentRow.id == item.document.id).values(deleted_at=datetime.now(UTC))))
                    try:
                        await wait_for_lock(db_engine, deletion_pid)
                    finally:
                        release.set()
                    await asyncio.wait_for(deleting, 10)
            assert await asyncio.wait_for(task, 10)
        finally:
            release.set()
        assert state(db_engine, item.document.id).current_version_id == item.version.id
    assert state(db_engine, item.document.id).deleted_at is not None


@pytest.mark.parametrize("same_version", [True, False])
async def test_concurrent_activations_are_idempotent_or_reject_stale_snapshot(
    services, registered_plan, sessions, db_engine, same_version,
):
    old = await registered_plan()
    new = old if same_version else await seed_later_version(sessions, services, old, 2)
    await store_complete(services, old)
    ready = asyncio.Event()
    pids = set()

    class RacingActivation(sessions.class_):
        async def scalar(self, statement, *args, **kwargs):
            if getattr(statement, "_for_update_arg", None) is not None and any(
                c.get("entity") is DocumentRow for c in getattr(statement, "column_descriptions", ())
            ):
                pids.add(await super().scalar(text("SELECT pg_backend_pid()")))
                if len(pids) == 2:
                    ready.set()
                await asyncio.wait_for(ready.wait(), 10)
            return await super().scalar(statement, *args, **kwargs)

    factory = type(sessions)(sessions.kw["bind"], class_=RacingActivation, expire_on_commit=False)
    activator = PostgreSQLDocumentActivationRepository(factory)
    results = await asyncio.wait_for(asyncio.gather(*(
        activator.activate(item.document.id, item.version.id, item.config, services.profile, expected_current_version_id=None)
        for item in (old, new)
    ), return_exceptions=True), 20)
    assert len(pids) == 2
    assert sum(value is True for value in results) == 1
    if same_version:
        assert sum(value is False for value in results) == 1
    else:
        assert sum(isinstance(value, ActivationConflict) for value in results) == 1
    assert state(db_engine, old.document.id).current_version_id in (old.version.id, new.version.id)
