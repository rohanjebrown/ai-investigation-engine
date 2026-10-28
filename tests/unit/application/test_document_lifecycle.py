from dataclasses import replace
from uuid import uuid7

import pytest

from investigation_engine.application.document_access import DocumentActor
from investigation_engine.application.ingestion.extracted_document import ExtractedDocument
from investigation_engine.application.ports.repository_errors import (
    DocumentAccessDenied, DocumentUpdateConflict, RegistrationConflict,
)
from investigation_engine.application.use_cases.delete_document import DeleteDocument
from investigation_engine.application.use_cases.get_document import GetDocument
from investigation_engine.application.use_cases.list_documents import ListDocuments
from investigation_engine.application.use_cases.update_document import UpdateDocument
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.infrastructure.persistence.in_memory_document_repository import InMemoryDocumentRepository


@pytest.fixture
def repository():
    return InMemoryDocumentRepository()


@pytest.fixture
def lifecycle(repository, registration):
    async def create(*, owner="alice", body="old text", source_id="lifecycle", revision=None):
        document, version = await repository.register(
            *registration(owner_id=owner, body=body, source_id=source_id, revision=revision)
        )
        # The in-memory adapter intentionally has no indexing/activation operation.
        repository._documents[document.id].current_version_id = version.id
        return document, version
    return create


def extracted(version, *, body, revision, source_id=None, metadata=None):
    source = replace(
        version.source,
        source_id=source_id or version.source.source_id,
        revision=revision,
        metadata=metadata if metadata is not None else version.source.metadata,
    )
    return ExtractedDocument(text=body, source=source, title="Ignored logical-title change")


async def test_update_appends_pending_immutable_version_and_retry_is_idempotent(lifecycle, repository):
    doc, first = await lifecycle(owner="alice")
    update = UpdateDocument(repository)
    actor = DocumentActor("alice")
    request = extracted(first, body="new text", revision="revision-2")
    updated_doc, second = await update.execute(
        doc.id, request, actor=actor, expected_current_version_id=first.id,
    )
    assert updated_doc.id == doc.id and updated_doc.current_version_id == first.id
    assert second.document_id == doc.id and second.version_number == 2
    assert first.content.text == "old text" and second.content.text == "new text"
    retry, repeated = await update.execute(
        doc.id, extracted(first, body="new text", revision="revision-2", metadata={"retry": True}),
        actor=actor, expected_current_version_id=first.id,
    )
    assert (retry.id, repeated.id) == (updated_doc.id, second.id)
    assert repeated.source.metadata == second.source.metadata
    details = await repository.get_details(doc.id, actor_id="alice")
    assert len(details.versions) == 2


async def test_same_content_new_revision_keeps_version_provenance_and_reuses_content(lifecycle, repository):
    doc, first = await lifecycle(body="unchanged")
    source_span = SourceSpan(
        start=0, end=len(first.content.text),
        locators=(SourceLocator(kind=LocatorKind.PARAGRAPH, index=4),),
    )
    request = replace(
        extracted(first, body="unchanged", revision="revision-2"),
        source_spans=(source_span,),
    )
    _, second = await UpdateDocument(repository).execute(
        doc.id, request,
        actor=DocumentActor("alice"), expected_current_version_id=first.id,
    )
    assert second.version_number == 2 and second.id != first.id
    assert second.content is first.content
    assert second.source.revision == "revision-2"
    assert second.source_spans == (source_span,)


async def test_pending_stale_and_changed_retry_are_rejected_without_state_changes(lifecycle, repository):
    doc, first = await lifecycle()
    use_case = UpdateDocument(repository)
    actor = DocumentActor("alice")
    with pytest.raises(DocumentUpdateConflict, match="explicit nonblank source revision"):
        await use_case.execute(
            doc.id, extracted(first, body="unversioned", revision=None),
            actor=actor, expected_current_version_id=first.id,
        )
    with pytest.raises(DocumentUpdateConflict, match="retain the document's source"):
        await use_case.execute(
            doc.id, extracted(first, body="different source", revision="revision-2", source_id="another-source"),
            actor=actor, expected_current_version_id=first.id,
        )
    bool_locator = replace(
        extracted(first, body="JSONB number distinction", revision="revision-2"),
        source=replace(first.source, revision="revision-2", record_locator={"record": True}),
    )
    with pytest.raises(DocumentUpdateConflict, match="retain the document's source"):
        await use_case.execute(
            doc.id, bool_locator, actor=actor, expected_current_version_id=first.id,
        )
    assert len(repository._versions) == 1
    pending = extracted(first, body="replacement", revision="revision-2")
    _, version = await use_case.execute(doc.id, pending, actor=actor, expected_current_version_id=first.id)
    before = (dict(repository._documents), dict(repository._versions), dict(repository._contents))
    with pytest.raises(DocumentUpdateConflict, match="pending indexing"):
        await use_case.execute(
            doc.id, extracted(first, body="third", revision="revision-3"),
            actor=actor, expected_current_version_id=first.id,
        )
    with pytest.raises(RegistrationConflict, match="text differs"):
        await use_case.execute(
            doc.id, extracted(first, body="changed retry", revision="revision-2"),
            actor=actor, expected_current_version_id=first.id,
        )
    with pytest.raises(DocumentUpdateConflict, match="active document version changed"):
        await use_case.execute(
            doc.id, extracted(first, body="third", revision="revision-3"),
            actor=actor, expected_current_version_id=uuid7(),
        )
    assert len(repository._versions) == len(before[1]) == 2
    assert version.id in repository._versions and repository._documents[doc.id].current_version_id == first.id


async def test_delete_is_idempotent_blocks_updates_and_hides_from_default_listing(lifecycle, repository):
    doc, first = await lifecycle()
    delete = DeleteDocument(repository)
    actor = DocumentActor("alice")
    deleted = await delete.execute(doc.id, actor=actor)
    deleted_again = await delete.execute(doc.id, actor=actor)
    assert deleted.deleted_at == deleted_again.deleted_at
    assert deleted.current_version_id == first.id
    with pytest.raises(DocumentUpdateConflict, match="Deleted"):
        await UpdateDocument(repository).execute(
            doc.id, extracted(first, body="later", revision="revision-2"),
            actor=actor, expected_current_version_id=first.id,
        )
    listing = await ListDocuments(repository).execute(actor=actor)
    assert listing.total == 0
    deleted_listing = await ListDocuments(repository).execute(actor=actor, include_deleted=True)
    assert deleted_listing.total == 1 and deleted_listing.items[0].document.deleted_at is not None
    detail = await GetDocument(repository).execute(doc.id, actor=actor)
    assert len(detail.versions) == 1 and detail.document.deleted_at is not None


async def test_listing_and_details_enforce_visibility_and_bound_pagination(registration, repository):
    shared, _ = await repository.register(*registration(owner_id=None, source_id="shared"))
    alice, _ = await repository.register(*registration(owner_id="alice", source_id="alice"))
    await repository.register(*registration(owner_id="bob", source_id="bob"))
    listing = await ListDocuments(repository).execute(actor=DocumentActor("alice"), limit=1)
    assert listing.total == 2 and len(listing.items) == 1
    page = await ListDocuments(repository).execute(actor=DocumentActor("alice"), offset=1, limit=1)
    assert {item.document.id for item in (*listing.items, *page.items)} == {shared.id, alice.id}
    assert page.items[0].version_count == 1 and page.items[0].current_version is None
    with pytest.raises(DocumentAccessDenied):
        await GetDocument(repository).execute(alice.id, actor=DocumentActor("bob"))
    with pytest.raises(DocumentAccessDenied):
        await DeleteDocument(repository).execute(shared.id, actor=DocumentActor("alice"))
    with pytest.raises(ValueError, match="Limit"):
        await ListDocuments(repository).execute(actor=DocumentActor("alice"), limit=101)

