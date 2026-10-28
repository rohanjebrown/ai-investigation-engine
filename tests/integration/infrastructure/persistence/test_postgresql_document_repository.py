import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid7

import pytest
from sqlalchemy import event, func, select, text, update

from investigation_engine.application.ports.repository_errors import RegistrationConflict, RepositoryError
from investigation_engine.domain.content import Content
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.domain.structural_span import StructuralSpan, StructureKind
from investigation_engine.infrastructure.persistence.models import ContentRow, DocumentRow, DocumentVersionRow
from investigation_engine.infrastructure.persistence.postgresql_document_repository import PostgreSQLDocumentRepository


def counts(engine):
    with engine.connect() as connection:
        return tuple(connection.scalar(select(func.count()).select_from(row)) for row in (DocumentRow, DocumentVersionRow, ContentRow))


async def test_registration_commits_and_new_repository_resolves_identical_retry(repository, sessions, db_engine, registration):
    document, version = await repository.register(*registration(owner_id="principal"))
    with db_engine.connect() as connection:
        assert connection.scalar(select(DocumentVersionRow.document_id).where(DocumentVersionRow.id == version.id)) == document.id
    retried, repeated = await PostgreSQLDocumentRepository(sessions).register(*registration(owner_id="principal"))
    assert (retried.id, repeated.id) == (document.id, version.id)
    assert document.current_version_id is None and document.owner_id == "principal"
    assert repeated.content == version.content and repeated.source == version.source
    assert counts(db_engine) == (1, 1, 1)


async def test_independent_occurrences_share_content_without_merging_sources(repository, sessions, db_engine, registration):
    structure = tuple(
        StructuralSpan(kind=StructureKind.SENTENCE, start=start, end=end)
        for start, end in ((0, 2), (3, 5))
    )
    source_spans = tuple(
        SourceSpan(start=span.start, end=span.end,
                   locators=(SourceLocator(kind=LocatorKind.SENTENCE, index=i),))
        for i, span in enumerate(structure)
    )
    other_structure = (StructuralSpan(kind=StructureKind.SENTENCE, start=0, end=5),)
    other_spans = (SourceSpan(start=0, end=5, locators=(SourceLocator(kind=LocatorKind.SENTENCE, index=0),)),)
    candidates = (
        registration(locator={"position": 0}, structure=structure, source_spans=source_spans),
        registration(locator={"position": 1}, structure=other_structure, source_spans=other_spans),
    )
    first, a = await repository.register(*candidates[0])
    second, b = await repository.register(*candidates[1])
    assert first.id != second.id and a.id != b.id
    assert a.content == b.content and a.source.record_locator != b.source.record_locator
    # Retry through new sessions to exercise JSONB serialization/reconstruction,
    # including ordered original locators and distinct layouts over equal text.
    reloaded = [
        await PostgreSQLDocumentRepository(sessions).register(*candidate)
        for candidate in candidates
    ]
    assert reloaded == [(first, a), (second, b)]
    assert [a.content.text[s.start:s.end] for s in reloaded[0][1].structure] == ["A😀", "B."]
    assert reloaded[0][1].source_spans == source_spans
    assert reloaded[1][1].structure == other_structure and reloaded[1][1].source_spans == other_spans
    assert counts(db_engine) == (2, 2, 1)


async def test_retry_preserves_descriptions_and_defensive_snapshots(repository, registration):
    doc, version = registration()
    stored, saved = await repository.register(doc, version)
    stored.title = doc.title = "mutated"
    saved.source.metadata["labels"].append("mutated")
    retry_doc, retry_version = registration()
    retry_doc.title = "new retry title"
    retry_version = replace(retry_version, source=replace(retry_version.source, metadata={"changed": True}, display_name="new display"))
    retried, repeated = await repository.register(retry_doc, retry_version)
    assert retried.title == "Original title"
    assert repeated.source.metadata == {"labels": ["original"]}
    assert repeated.source.display_name == "Original display"


@pytest.mark.parametrize("change", ["text", "structure", "source_spans"])
async def test_conflicting_retries_leave_no_partial_candidates(repository, db_engine, registration, change):
    stored = await repository.register(*registration())
    doc, version = registration()
    if change == "text":
        version = replace(version, content=Content("not committed after conflict"))
    elif change == "structure":
        version = replace(version, structure=(StructuralSpan(kind=StructureKind.SENTENCE, start=0, end=1),))
    else:
        version = replace(version, source_spans=(SourceSpan(start=0, end=1, locators=(SourceLocator(kind=LocatorKind.SENTENCE, index=9),)),))
    with pytest.raises(RegistrationConflict):
        await repository.register(doc, version)
    assert counts(db_engine) == (1, 1, 1)
    assert await repository.register(*registration()) == stored


async def test_insert_failure_rolls_back_new_content_and_document_and_releases_sessions(repository, sessions, db_engine, registration):
    _, saved = await repository.register(*registration())
    doc, version = registration(source_id="independent", body="new content must roll back")
    closed = []

    def on_close(connection, record):
        closed.append(connection.driver_connection)

    engine = sessions.kw["bind"].sync_engine
    event.listen(engine, "close", on_close)
    try:
        with pytest.raises(ValueError, match="Document version already exists"):
            await repository.register(doc, replace(version, id=saved.id))
    finally:
        event.remove(engine, "close", on_close)
    assert closed and all(connection.closed for connection in closed)
    assert counts(db_engine) == (1, 1, 1)
    # A following successful transaction verifies the failed session did not poison the factory.
    await repository.register(doc, version)
    assert counts(db_engine) == (2, 2, 2)


async def test_database_check_failure_is_a_repository_error_and_rolls_back(repository, db_engine, registration):
    doc, version = registration()
    object.__setattr__(version.content, "sha256", "0" * 64)
    with pytest.raises(RepositoryError) as caught:
        await repository.register(doc, version)
    assert caught.value.__cause__.orig.diag.constraint_name == "ck_contents_sha256_matches_text"
    assert counts(db_engine) == (0, 0, 0)
    await repository.register(*registration())


@pytest.mark.parametrize("owner", [None, "alice"])
async def test_deleted_retry_preserves_ownership_and_never_restores(repository, db_engine, registration, owner):
    doc, version = await repository.register(*registration(owner_id=owner))
    deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    with db_engine.begin() as connection:
        connection.execute(update(DocumentRow).where(DocumentRow.id == doc.id).values(deleted_at=deleted_at, current_version_id=version.id))
    retried, repeated = await repository.register(*registration(owner_id=owner))
    assert retried.id == doc.id and repeated.id == version.id
    assert retried.deleted_at == deleted_at and retried.owner_id == owner
    assert retried.current_version_id == version.id
    with pytest.raises(PermissionError, match="^Registration is not permitted$"):
        await repository.register(*registration(owner_id="other", body="changed private text"))


@pytest.mark.parametrize("left,right,equal", [
    ({"a": 1, "b": [None]}, {"b": [None], "a": 1.0}, True),
    ({"a": True}, {"a": 1}, False),
    ({}, {"a": None}, False),
    ({"a": [1, 2]}, {"a": [2, 1]}, False),
])
async def test_actual_jsonb_occurrence_identity(repository, registration, left, right, equal):
    first, _ = await repository.register(*registration(locator=left))
    second, _ = await repository.register(*registration(locator=right))
    assert (first.id == second.id) is equal


async def test_null_revision_matches_only_absent_revision(repository, registration):
    original, _ = await repository.register(*registration())
    retry, _ = await repository.register(*registration())
    revised, version = await repository.register(*registration(revision="revision-2"))
    assert retry.id == original.id != revised.id and version.version_number == 1


@pytest.mark.parametrize("invalid", ["version_number", "active_version"])
async def test_new_registration_requires_initial_inactive_state(repository, registration, invalid):
    doc, version = registration()
    if invalid == "version_number":
        version = replace(version, version_number=2)
    else:
        doc.current_version_id = uuid7()
    with pytest.raises(ValueError, match="New registrations require"):
        await repository.register(doc, version)


@pytest.mark.parametrize("scenario,expected_counts", [
    ("identical", (1, 1, 1)), ("conflicting", (1, 1, 1)),
    ("shared_content", (2, 2, 1)), ("different_content", (2, 2, 2)),
])
async def test_concurrent_independent_sessions_resolve_unique_constraint_races(sessions, db_engine, registration, scenario, expected_counts):
    """Both first lookups finish before either task can issue its INSERTs."""
    both_read = asyncio.Event()
    arrivals = 0
    backend_pids = set()

    # Wrap the factory's session.execute to pause only the initial occurrence lookup.
    original_class = sessions.class_

    class CoordinatedSession(original_class):
        async def execute(self, statement, *args, **kwargs):
            result = await super().execute(statement, *args, **kwargs)
            nonlocal arrivals
            is_occurrence_lookup = any(
                column.get("entity") is DocumentVersionRow
                for column in getattr(statement, "column_descriptions", ())
            )
            if statement.is_select and is_occurrence_lookup and arrivals < 2:
                backend_pids.add(await super().scalar(text("SELECT pg_backend_pid()")))
                arrivals += 1
                if arrivals == 2:
                    both_read.set()
                await asyncio.wait_for(both_read.wait(), timeout=15)
            return result

    coordinated = type(sessions)(sessions.kw["bind"], class_=CoordinatedSession, expire_on_commit=False)
    first = registration()
    second = registration(
        source_id="other" if scenario in ("shared_content", "different_content") else "synthetic-source",
        body="changed" if scenario in ("conflicting", "different_content") else "A😀 B.",
    )
    results = await asyncio.wait_for(asyncio.gather(
        PostgreSQLDocumentRepository(coordinated).register(*first),
        PostgreSQLDocumentRepository(coordinated).register(*second),
        return_exceptions=True,
    ), timeout=30)
    assert arrivals == 2
    assert len(backend_pids) == 2
    if scenario == "conflicting":
        assert sum(isinstance(r, RegistrationConflict) for r in results) == 1
        assert sum(isinstance(r, tuple) for r in results) == 1
    else:
        assert all(isinstance(r, tuple) for r in results), [type(r).__name__ for r in results]
        assert (results[0][0].id == results[1][0].id) is (scenario == "identical")
    assert counts(db_engine) == expected_counts
