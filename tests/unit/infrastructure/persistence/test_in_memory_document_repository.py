from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid7

import pytest

from investigation_engine.application.ports.repository_errors import ContentHashCollision, RegistrationConflict
from investigation_engine.domain.content import Content
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.domain.structural_span import StructuralSpan, StructureKind
from investigation_engine.infrastructure.persistence.in_memory_document_repository import InMemoryDocumentRepository


@pytest.fixture
def repository():
    return InMemoryDocumentRepository()


async def test_retry_preserves_identity_and_descriptions_while_independent_sources_share_content(
    repository, registration
):
    document, version = await repository.register(*registration())
    assert version.version_number == 1 and document.current_version_id is None
    retry_doc, retry_version = registration()
    retry_doc.title = "Ignored retry title"
    retry_version = replace(
        retry_version, source=replace(retry_version.source, metadata={"changed": True}, display_name="Ignored display")
    )
    retried, repeated = await repository.register(retry_doc, retry_version)
    other, independent = await repository.register(*registration(source_id="independent"))
    assert (document.id, version.id) == (retried.id, repeated.id)
    assert retried.title == document.title
    assert repeated.source.metadata == version.source.metadata
    assert repeated.source.display_name == version.source.display_name
    assert other.id != document.id and independent.id != version.id
    assert independent.content is version.content


@pytest.mark.parametrize("change", ["text", "structure", "source_spans"])
async def test_same_occurrence_cannot_change_text_or_mappings(repository, registration, change):
    original = await repository.register(*registration())
    doc, version = registration()
    if change == "text":
        version = replace(version, content=Content("Different"))
    elif change == "structure":
        version = replace(version, structure=(StructuralSpan(kind=StructureKind.SENTENCE, start=0, end=1),))
    else:
        version = replace(
            version,
            source_spans=(SourceSpan(start=0, end=1, locators=(SourceLocator(kind=LocatorKind.SENTENCE, index=0),)),),
        )
    with pytest.raises(RegistrationConflict, match="Registration conflict"):
        await repository.register(doc, version)
    assert (await repository.register(*registration())) == original


async def test_private_retry_is_authorized_before_conflict_details(repository, registration):
    await repository.register(*registration(owner_id="alice"))
    with pytest.raises(PermissionError, match="^Registration is not permitted$"):
        await repository.register(*registration(owner_id="bob", body="private conflict"))


async def test_deleted_retry_never_restores_registration(repository, registration):
    doc, version = registration()
    doc.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    stored, _ = await repository.register(doc, version)
    retried, _ = await repository.register(*registration())
    assert retried.id == stored.id and retried.deleted_at == stored.deleted_at


async def test_caller_mutations_do_not_change_stored_snapshots(repository, registration):
    doc, version = registration(locator={"nested": [1]})
    returned, returned_version = await repository.register(doc, version)
    doc.title = returned.title = "mutated"
    version.source.record_locator["nested"].append(2)
    returned_version.source.metadata["labels"].append("changed")
    retry, repeated = await repository.register(*registration(locator={"nested": [1]}))
    assert retry.title == "Original title"
    assert repeated.source.record_locator == {"nested": [1]}
    assert repeated.source.metadata == {"labels": ["original"]}


@pytest.mark.parametrize(
    "left,right,equal",
    [
        ({"a": 1, "b": [None]}, {"b": [None], "a": 1.0}, True),
        ({"a": True}, {"a": 1}, False),
        ({}, {"a": None}, False),
        ({"a": [1, 2]}, {"a": [2, 1]}, False),
        ({"a": "é"}, {"a": "e\u0301"}, False),
    ],
)
async def test_locator_comparison_preserves_jsonb_identity_rules(repository, registration, left, right, equal):
    first, _ = await repository.register(*registration(locator=left))
    second, _ = await repository.register(*registration(locator=right))
    assert (first.id == second.id) is equal


@pytest.mark.parametrize("locator", [{"value": float("nan")}, {1: "invalid key"}, {"value": (1, 2)}])
async def test_invalid_locator_cannot_become_a_registration(repository, registration, locator):
    with pytest.raises(ValueError, match="Source locator"):
        await repository.register(*registration(locator=locator))


async def test_absent_revision_is_not_a_wildcard_or_implicit_update(repository, registration):
    absent, _ = await repository.register(*registration())
    revised, version = await repository.register(*registration(revision="revision-2"))
    assert absent.id != revised.id and version.version_number == 1


async def test_version_must_belong_to_candidate_document(repository, registration):
    doc, version = registration()
    with pytest.raises(ValueError, match="different document"):
        await repository.register(doc, replace(version, document_id=uuid7()))


async def test_equal_digest_with_different_exact_text_is_a_collision(repository, registration):
    _, stored = await repository.register(*registration(body="original"))
    doc, version = registration(source_id="other", body="different")
    # Deliberately corrupt a test value; computing a real SHA-256 collision is unnecessary.
    object.__setattr__(version.content, "sha256", stored.content.sha256)
    with pytest.raises(ContentHashCollision, match="collision"):
        await repository.register(doc, version)


@pytest.mark.parametrize("identifier", ["document", "version"])
async def test_new_occurrence_cannot_reuse_an_existing_logical_id(repository, registration, identifier):
    stored_doc, stored_version = await repository.register(*registration())
    doc, version = registration(source_id="new occurrence")
    if identifier == "document":
        doc.id = stored_doc.id
        version = replace(version, document_id=doc.id)
        message = "Document already exists"
    else:
        version = replace(version, id=stored_version.id)
        message = "Document version already exists"
    with pytest.raises(ValueError, match=message):
        await repository.register(doc, version)


@pytest.mark.parametrize("invalid", ["version_number", "active_version"])
async def test_new_registration_requires_version_one_and_inactive_document(repository, registration, invalid):
    baseline = await repository.register(*registration())
    doc, version = registration(source_id="new occurrence", body="uncommitted candidate")
    if invalid == "version_number":
        version = replace(version, version_number=2)
    else:
        doc.current_version_id = uuid7()
    # The port has no listing API: snapshot all in-memory stores to detect
    # orphan Content or occurrence entries as well as document/version writes.
    before = deepcopy(vars(repository))
    with pytest.raises(ValueError, match="^New registrations require version 1 and no active version$"):
        await repository.register(doc, version)
    assert vars(repository) == before
    assert await repository.register(*registration()) == baseline

    # Correcting the same candidates must succeed without leftover IDs/indexes.
    stored, saved = await repository.register(
        replace(doc, current_version_id=None),
        replace(version, version_number=1),
    )
    assert stored.id == doc.id and saved.id == version.id
    assert saved.version_number == 1 and stored.current_version_id is None


@pytest.mark.parametrize("retry_has_pointer", [False, True])
async def test_active_registration_retry_is_resolved_before_initial_state_validation(
    repository,
    registration,
    retry_has_pointer,
):
    document, version = await repository.register(*registration(owner_id="alice"))
    # Seed a later lifecycle state without adding an activation operation.
    repository._documents[document.id].current_version_id = version.id
    retry_doc, retry_version = registration(owner_id="alice")
    if retry_has_pointer:
        retry_doc.current_version_id = version.id
    before = deepcopy(vars(repository))

    retried, repeated = await repository.register(retry_doc, retry_version)

    assert retried.id == document.id and repeated.id == version.id
    assert retried.current_version_id == version.id
    assert retried.owner_id == "alice" and repeated.content is version.content
    assert vars(repository) == before
