import asyncio
from dataclasses import replace
from uuid import uuid7

import pytest
from sqlalchemy import event, func, select, text, update
from sqlalchemy.exc import OperationalError

from investigation_engine.application.chunking import Chunk, ChunkingConfig, ChunkingStrategy, ChunkPlan, chunk_document
from investigation_engine.application.ports.repository_errors import ChunkPlanConflict, RepositoryError
from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
from investigation_engine.domain.content import Content
from investigation_engine.domain.structural_span import StructuralSpan, StructureKind
from investigation_engine.infrastructure.persistence.models import (
    ChunkingConfigurationRow, ChunkMembershipRow, DocumentRow, DocumentVersionRow, VersionChunkPlanRow,
)
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import PostgreSQLChunkPlanRepository


@pytest.fixture
def tokenizer():
    class CharacterTokenizer:
        identity = TokenizerIdentity(name="characters", revision="1",
                                     artifact_sha256="0" * 64, runtime_version="test")

        def offsets(self, text):
            return tuple((index, index + 1) for index in range(len(text)))
    return CharacterTokenizer()


@pytest.fixture
def config(tokenizer):
    return ChunkingConfig(strategy=ChunkingStrategy.FIXED_TOKENS,
                          tokenizer=tokenizer.identity, max_tokens=3, overlap=1)


@pytest.fixture
def plans(sessions):
    return PostgreSQLChunkPlanRepository(sessions)


def counts(engine):
    with engine.connect() as connection:
        return tuple(connection.scalar(select(func.count()).select_from(row)) for row in (
            ChunkingConfigurationRow, VersionChunkPlanRow, ChunkMembershipRow,
        ))


async def test_configuration_registration_is_idempotent_and_creates_no_documents_or_plans(
    plans, db_engine, config,
):
    first, concurrent = await asyncio.gather(
        plans.register_configuration(config), plans.register_configuration(replace(config)),
    )
    repeated = await plans.register_configuration(config)
    assert first == concurrent == repeated
    assert counts(db_engine) == (1, 0, 0)


async def test_generated_plan_commits_and_new_instance_reconstructs_unicode_and_overlap(
    repository, plans, sessions, db_engine, registration, config, tokenizer,
):
    document, version = await repository.register(*registration())
    plan = chunk_document(version, config=config, tokenizer=tokenizer)
    assert [(c.start, c.end, c.text) for c in plan.chunks] == [(0, 3, "A😀 "), (2, 5, " B.")]
    assert await plans.get(version.id, config) is None
    assert await plans.save(plan) == plan
    loaded = await PostgreSQLChunkPlanRepository(sessions).get(version.id, replace(config))
    assert loaded == plan and loaded.config.tokenizer == config.tokenizer
    assert counts(db_engine) == (1, 1, 2)
    with db_engine.connect() as connection:
        assert connection.scalar(select(DocumentRow.current_version_id).where(DocumentRow.id == document.id)) is None


async def test_identical_saves_reuse_rows_without_duplicate_configuration(
    repository, plans, db_engine, registration, config, tokenizer,
):
    _, version = await repository.register(*registration())
    plan = chunk_document(version, config=config, tokenizer=tokenizer)
    first = await plans.save(plan)
    assert await plans.save(replace(plan, config=replace(config))) == first
    assert counts(db_engine) == (1, 1, 2)


async def test_conflicting_plan_never_overwrites_the_committed_plan(
    repository, plans, db_engine, registration, config, tokenizer,
):
    _, version = await repository.register(*registration())
    plan = chunk_document(version, config=config, tokenizer=tokenizer)
    await plans.save(plan)
    changed = replace(plan, chunks=tuple(
        Chunk(content=version.content, start=start, end=end)
        for start, end in ((0, 2), (1, 4), (3, 5))
    ))
    with pytest.raises(ChunkPlanConflict):
        await plans.save(changed)
    assert await plans.get(version.id, config) == plan
    assert counts(db_engine) == (1, 1, 2)


async def test_different_configuration_is_a_separate_plan(
    repository, plans, db_engine, registration, config, tokenizer,
):
    _, version = await repository.register(*registration())
    for selected in (config, replace(config, max_tokens=4)):
        plan = chunk_document(version, config=selected, tokenizer=tokenizer)
        assert await plans.save(plan) == await plans.get(version.id, selected) == plan
    assert counts(db_engine) == (2, 2, 4)
    other_tokenizer = replace(config.tokenizer, revision="not stored")
    assert await plans.get(version.id, replace(config, tokenizer=other_tokenizer)) is None


async def test_shared_content_keeps_source_specific_sentence_plans(
    repository, plans, registration, config, tokenizer,
):
    sentence_config = replace(config, strategy=ChunkingStrategy.SENTENCE_PACKING, max_tokens=4, overlap=0)
    versions = []
    for source, ranges in (("first", ((0, 2), (3, 5))), ("second", ((0, 5),))):
        _, version = await repository.register(*registration(
            source_id=source, structure=tuple(
                StructuralSpan(kind=StructureKind.SENTENCE, start=start, end=end)
                for start, end in ranges
            ),
        ))
        versions.append(version)
    assert versions[0].content == versions[1].content
    assert versions[0].id != versions[1].id
    loaded = []
    for version in versions:
        plan = chunk_document(version, config=sentence_config, tokenizer=tokenizer)
        await plans.save(plan)
        loaded.append(await plans.get(version.id, sentence_config))
    assert [[(c.start, c.end) for c in p.chunks] for p in loaded] == [
        [(0, 3), (3, 5)], [(0, 4), (4, 5)],
    ]
    assert [p.document_version_id for p in loaded] == [v.id for v in versions]


@pytest.mark.parametrize("invalid", ["content", "range", "missing_version"])
async def test_invalid_plan_rolls_back_without_configuration_or_membership_rows(
    repository, plans, db_engine, registration, config, tokenizer, invalid,
):
    _, version = await repository.register(*registration())
    plan = chunk_document(version, config=config, tokenizer=tokenizer)
    if invalid == "content":
        plan = replace(plan, chunks=(Chunk(content=Content("other"), start=0, end=5),))
    elif invalid == "range":
        object.__setattr__(plan.chunks[-1], "end", 6)
    else:
        plan = replace(plan, document_version_id=uuid7())
    with pytest.raises(ValueError):
        await plans.save(plan)
    assert counts(db_engine) == (0, 0, 0)
    assert await plans.get(version.id, config) is None


@pytest.mark.parametrize("body", ["", " \t\n ", "a  b"])
async def test_empty_and_whitespace_plans_remain_complete_without_embedding_filtering(
    repository, plans, db_engine, registration, config, tokenizer, body,
):
    _, version = await repository.register(*registration(body=body))
    selected = replace(config, max_tokens=1, overlap=0)
    plan = chunk_document(version, config=selected, tokenizer=tokenizer)
    assert await plans.save(plan) == plan
    loaded = await plans.get(version.id, selected)
    assert loaded == plan
    assert "".join(chunk.text for chunk in loaded.chunks) == body
    if body:
        assert any(not chunk.text.strip() for chunk in loaded.chunks)
    assert counts(db_engine) == (1, 1, len(plan.chunks))
    with db_engine.connect() as connection:
        assert connection.scalar(select(VersionChunkPlanRow.expected_chunk_count)) == len(plan.chunks)


async def test_failure_after_membership_insert_rolls_back_the_entire_plan(
    repository, plans, db_engine, sessions, registration, config, tokenizer,
):
    _, version = await repository.register(*registration())
    plan = chunk_document(version, config=config, tokenizer=tokenizer)
    fired = False

    def fail_after_insert(connection, cursor, statement, parameters, context, executemany):
        nonlocal fired
        compiled = context.compiled
        if compiled is not None and compiled.statement.is_insert and compiled.statement.table.name == "chunk_memberships":
            fired = True
            raise OperationalError("injected membership failure", {}, RuntimeError("injected"))

    engine = sessions.kw["bind"].sync_engine
    event.listen(engine, "after_cursor_execute", fail_after_insert)
    try:
        with pytest.raises(RepositoryError, match="could not be saved"):
            await plans.save(plan)
    finally:
        event.remove(engine, "after_cursor_execute", fail_after_insert)
    assert fired and counts(db_engine) == (0, 0, 0)
    assert await plans.get(version.id, config) is None
    assert await plans.save(plan) == plan


@pytest.mark.parametrize("corruption", ["count", "ordinal", "range"])
async def test_incomplete_or_corrupt_stored_plan_is_not_returned_or_repaired(
    repository, plans, db_engine, registration, config, tokenizer, corruption,
):
    _, version = await repository.register(*registration())
    plan = chunk_document(version, config=config, tokenizer=tokenizer)
    await plans.save(plan)
    with db_engine.begin() as connection:
        if corruption == "count":
            connection.execute(update(VersionChunkPlanRow).values(expected_chunk_count=3))
        elif corruption == "ordinal":
            connection.execute(update(ChunkMembershipRow).where(ChunkMembershipRow.ordinal == 0).values(ordinal=9))
        else:
            connection.execute(update(ChunkMembershipRow).where(ChunkMembershipRow.ordinal == 1).values(end=6))
    with pytest.raises(RepositoryError, match="invalid or incomplete"):
        await plans.get(version.id, config)
    with pytest.raises(RepositoryError, match="invalid or incomplete"):
        await plans.save(plan)


@pytest.mark.parametrize("scenario", ["identical", "conflicting", "different_config"])
async def test_concurrent_sessions_serialize_same_version_saves(
    repository, sessions, db_engine, registration, config, tokenizer, scenario,
):
    _, version = await repository.register(*registration())
    first = chunk_document(version, config=config, tokenizer=tokenizer)
    second = first
    if scenario == "conflicting":
        second = replace(first, chunks=tuple(
            Chunk(content=version.content, start=start, end=end)
            for start, end in ((0, 2), (1, 4), (3, 5))
        ))
    elif scenario == "different_config":
        second = chunk_document(version, config=replace(config, max_tokens=4), tokenizer=tokenizer)
    both_ready = asyncio.Event()
    pids = set()
    arrivals = 0

    class CoordinatedSession(sessions.class_):
        async def scalar(self, statement, *args, **kwargs):
            nonlocal arrivals
            if getattr(statement, "_for_update_arg", None) is not None:
                pids.add(await super().scalar(text("SELECT pg_backend_pid()")))
                arrivals += 1
                if arrivals == 2:
                    both_ready.set()
                await asyncio.wait_for(both_ready.wait(), timeout=15)
            return await super().scalar(statement, *args, **kwargs)

    factory = type(sessions)(sessions.kw["bind"], class_=CoordinatedSession, expire_on_commit=False)
    results = await asyncio.wait_for(asyncio.gather(
        PostgreSQLChunkPlanRepository(factory).save(first),
        PostgreSQLChunkPlanRepository(factory).save(second), return_exceptions=True,
    ), timeout=30)
    assert arrivals == 2 and len(pids) == 2
    reader = PostgreSQLChunkPlanRepository(sessions)
    if scenario == "conflicting":
        assert sum(isinstance(r, ChunkPlanConflict) for r in results) == 1
        winners = [r for r in results if isinstance(r, ChunkPlan)]
        assert len(winners) == 1
        assert await reader.get(version.id, config) == winners[0]
        assert counts(db_engine) == (1, 1, len(winners[0].chunks))
    else:
        assert results == [first, second]
        assert await reader.get(version.id, first.config) == first
        assert await reader.get(version.id, second.config) == second
        assert counts(db_engine) == ((2, 2, 4) if scenario == "different_config" else (1, 1, 2))
