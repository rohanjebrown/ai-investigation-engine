import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid7

import pytest
from sqlalchemy import func, select

from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy, chunk_document
from investigation_engine.application.document_access import DocumentActor
from investigation_engine.application.embeddings import embedding_eligible
from investigation_engine.application.ingestion.extracted_document import ExtractedDocument
from investigation_engine.application.ports.embedding_errors import EmbeddingResponseError
from investigation_engine.application.ports.repository_errors import (
    ActivationConflict, DocumentAccessDenied, DocumentUpdateConflict,
)
from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
from investigation_engine.application.use_cases.delete_document import DeleteDocument
from investigation_engine.application.use_cases.get_document import GetDocument
from investigation_engine.application.use_cases.index_document_version import IndexDocumentVersion, IndexingOutcome
from investigation_engine.application.use_cases.list_documents import ListDocuments
from investigation_engine.application.use_cases.search_evidence import SearchEvidence
from investigation_engine.application.use_cases.update_document import UpdateDocument
from investigation_engine.infrastructure.persistence.models import ChunkEmbeddingRow, ContentRow, DocumentRow
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import PostgreSQLChunkPlanRepository
from investigation_engine.infrastructure.persistence.postgresql_document_activation_repository import PostgreSQLDocumentActivationRepository
from investigation_engine.infrastructure.persistence.postgresql_embedding_repository import PostgreSQLEmbeddingRepository
from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import PostgreSQLEvidenceRepository


@pytest.fixture
def lifecycle_services(sessions, repository, registration, synthetic_provider):
    class CharacterTokenizer:
        identity = TokenizerIdentity(
            name="lifecycle-characters", revision="1", artifact_sha256="1" * 64,
            runtime_version="test",
        )

        def offsets(self, value):
            return tuple((index, index + 1) for index in range(len(value)))

    tokenizer = CharacterTokenizer()
    chunking = ChunkingConfig(
        strategy=ChunkingStrategy.WHOLE_PASSAGE, tokenizer=tokenizer.identity, max_tokens=256,
    )
    plans = PostgreSQLChunkPlanRepository(sessions)
    vectors = PostgreSQLEmbeddingRepository(sessions)
    activation = PostgreSQLDocumentActivationRepository(sessions)
    workflow = IndexDocumentVersion(plans, vectors, activation, synthetic_provider, max_attempts=1)
    evidence = PostgreSQLEvidenceRepository(sessions)
    search = SearchEvidence(evidence, synthetic_provider, chunking, synthetic_provider.configuration)

    async def index(document, version):
        plan = chunk_document(version, config=chunking, tokenizer=tokenizer)
        await plans.save(plan)
        return await workflow.execute(document.id, version.id, chunking, synthetic_provider.configuration)

    async def create(body="The timeout is 30 seconds.", *, owner="alice", source_id=None):
        document, version = await repository.register(
            *registration(body=body, owner_id=owner, source_id=source_id or str(uuid7()))
        )
        await index(document, version)
        return SimpleNamespace(document=document, version=version)

    return SimpleNamespace(
        repository=repository, provider=synthetic_provider, chunking=chunking, plans=plans,
        vectors=vectors, activation=activation, workflow=workflow, evidence=evidence,
        search=search, index=index, create=create,
    )


def active_version(db_engine, document_id):
    with db_engine.connect() as connection:
        return connection.scalar(select(DocumentRow.current_version_id).where(DocumentRow.id == document_id))


def embedding_count(db_engine):
    with db_engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(ChunkEmbeddingRow))


async def test_update_keeps_old_evidence_until_new_version_is_fully_indexed_then_switches(
    lifecycle_services, db_engine,
):
    services = lifecycle_services
    item = await services.create()
    old_hits = await services.search.execute("timeout", owner_id="alice")
    assert [hit.document_version_id for hit in old_hits] == [item.version.id]

    source = replace(item.version.source, revision="upload-revision-2")
    extracted = ExtractedDocument(text="The timeout is 45 seconds.", source=source, title="renamed")
    updated_document, second = await UpdateDocument(services.repository).execute(
        item.document.id, extracted, actor=DocumentActor("alice"),
        expected_current_version_id=item.version.id,
    )
    assert updated_document.id == item.document.id
    assert second.version_number == 2 and second.content.text == extracted.text
    assert active_version(db_engine, item.document.id) == item.version.id
    assert [hit.document_version_id for hit in await services.search.execute("timeout", owner_id="alice")] == [item.version.id]

    details = await GetDocument(services.repository).execute(item.document.id, actor=DocumentActor("alice"))
    assert [version.version_number for version in details.versions] == [1, 2]
    assert await services.index(updated_document, second) == IndexingOutcome.ACTIVATED
    assert active_version(db_engine, item.document.id) == second.id
    updated_hits = await services.search.execute("timeout", owner_id="alice")
    assert [hit.document_version_id for hit in updated_hits] == [second.id]
    assert updated_hits[0].text == extracted.text
    retry_document, retry_version = await UpdateDocument(services.repository).execute(
        item.document.id, extracted, actor=DocumentActor("alice"),
        expected_current_version_id=item.version.id,
    )
    assert retry_document.current_version_id == second.id and retry_version.id == second.id


async def test_identical_text_on_explicit_new_revision_creates_version_and_reuses_content(
    lifecycle_services, db_engine,
):
    services = lifecycle_services
    item = await services.create()
    different_json_type = ExtractedDocument(
        text="The timeout is 30 seconds.",
        source=replace(item.version.source, revision="wrong-locator-revision", record_locator={"record": True}),
    )
    with pytest.raises(DocumentUpdateConflict, match="retain the document's source"):
        await UpdateDocument(services.repository).execute(
            item.document.id, different_json_type, actor=DocumentActor("alice"),
            expected_current_version_id=item.version.id,
        )
    second_document, second = await UpdateDocument(services.repository).execute(
        item.document.id,
        ExtractedDocument(
            text=item.version.content.text,
            source=replace(item.version.source, revision="upload-revision-2"),
        ),
        actor=DocumentActor("alice"), expected_current_version_id=item.version.id,
    )
    assert second_document.id == item.document.id
    assert second.version_number == 2 and second.id != item.version.id
    assert second.content.sha256 == item.version.content.sha256
    with db_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(ContentRow)) == 1
        assert connection.scalar(select(func.count()).select_from(DocumentRow)) == 1
    details = await GetDocument(services.repository).execute(item.document.id, actor=DocumentActor("alice"))
    assert [version.source.revision for version in details.versions] == [None, "upload-revision-2"]


async def test_failed_replacement_indexing_preserves_active_version_and_retry_reuses_pending_version(
    lifecycle_services, db_engine,
):
    services = lifecycle_services
    item = await services.create()
    request = ExtractedDocument(
        text="The timeout is 45 seconds.",
        source=replace(item.version.source, revision="upload-revision-2"),
    )
    update = UpdateDocument(services.repository)
    document, pending = await update.execute(
        item.document.id, request, actor=DocumentActor("alice"),
        expected_current_version_id=item.version.id,
    )
    services.provider.failures = [EmbeddingResponseError("synthetic provider failure")]
    with pytest.raises(EmbeddingResponseError):
        await services.index(document, pending)
    assert active_version(db_engine, item.document.id) == item.version.id
    assert [hit.document_version_id for hit in await services.search.execute("timeout", owner_id="alice")] == [item.version.id]

    retried_document, retried_version = await update.execute(
        item.document.id, request, actor=DocumentActor("alice"),
        expected_current_version_id=item.version.id,
    )
    assert retried_document.id == document.id and retried_version.id == pending.id
    assert await services.index(retried_document, retried_version) == IndexingOutcome.ACTIVATED
    assert active_version(db_engine, item.document.id) == pending.id


async def test_only_one_pending_update_wins_concurrent_attempts(lifecycle_services):
    services = lifecycle_services
    item = await services.create()
    update = UpdateDocument(services.repository)
    actor = DocumentActor("alice")
    requests = (
        ExtractedDocument(text="replacement A", source=replace(item.version.source, revision="revision-A")),
        ExtractedDocument(text="replacement B", source=replace(item.version.source, revision="revision-B")),
    )
    results = await asyncio.gather(*(
        update.execute(item.document.id, request, actor=actor,
                       expected_current_version_id=item.version.id)
        for request in requests
    ), return_exceptions=True)
    successes = [value for value in results if isinstance(value, tuple)]
    failures = [value for value in results if isinstance(value, BaseException)]
    assert len(successes) == 1 and len(failures) == 1
    assert isinstance(failures[0], DocumentUpdateConflict)
    details = await GetDocument(services.repository).execute(item.document.id, actor=actor)
    assert len(details.versions) == 2


async def test_listing_authorization_deletion_and_shared_embeddings(lifecycle_services, db_engine):
    services = lifecycle_services
    alice = await services.create(owner="alice", source_id="alice-source")
    bob = await services.create(owner="bob", source_id="bob-source")
    shared = await services.create(owner=None, source_id="shared-source")
    before = embedding_count(db_engine)
    use_case = ListDocuments(services.repository)
    first_page = await use_case.execute(actor=DocumentActor("alice"), limit=1)
    assert first_page.total == 2 and len(first_page.items) == 1
    second_page = await use_case.execute(actor=DocumentActor("alice"), offset=1, limit=1)
    assert {item.document.owner_id for item in (*first_page.items, *second_page.items)} == {None, "alice"}
    with pytest.raises(DocumentAccessDenied):
        await GetDocument(services.repository).execute(bob.document.id, actor=DocumentActor("alice"))
    with pytest.raises(DocumentAccessDenied):
        await UpdateDocument(services.repository).execute(
            bob.document.id,
            ExtractedDocument(text="unauthorized", source=replace(bob.version.source, revision="unauthorized")),
            actor=DocumentActor("alice"), expected_current_version_id=bob.version.id,
        )
    with pytest.raises(DocumentAccessDenied):
        await DeleteDocument(services.repository).execute(shared.document.id, actor=DocumentActor("alice"))

    deleted = await DeleteDocument(services.repository).execute(alice.document.id, actor=DocumentActor("alice"))
    repeated = await DeleteDocument(services.repository).execute(alice.document.id, actor=DocumentActor("alice"))
    assert deleted.deleted_at == repeated.deleted_at and deleted.current_version_id == alice.version.id
    assert embedding_count(db_engine) == before
    visible = await services.search.execute("timeout", owner_id="alice")
    assert all(hit.document_id != alice.document.id for hit in visible)
    assert any(hit.document_id == shared.document.id for hit in visible)
    default_list = await use_case.execute(actor=DocumentActor("alice"), limit=10)
    assert all(item.document.id != alice.document.id for item in default_list.items)
    include_deleted = await use_case.execute(actor=DocumentActor("alice"), limit=10, include_deleted=True)
    assert any(item.document.id == alice.document.id and item.document.deleted_at for item in include_deleted.items)


async def test_deletion_during_provider_call_prevents_pending_activation_and_preserves_shared_content(
    lifecycle_services, db_engine,
):
    services = lifecycle_services
    private = await services.create(owner="alice", source_id="private-occurrence")
    shared = await services.create(owner=None, source_id="shared-occurrence")
    assert private.version.content.sha256 == shared.version.content.sha256
    request = ExtractedDocument(
        text="Replacement text unique to the pending revision.",
        source=replace(private.version.source, revision="private-revision-2"),
    )
    document, pending = await UpdateDocument(services.repository).execute(
        private.document.id, request, actor=DocumentActor("alice"),
        expected_current_version_id=private.version.id,
    )
    deleted = []

    async def delete_while_provider_is_running():
        if not deleted:
            deleted.append(await DeleteDocument(services.repository).execute(
                private.document.id, actor=DocumentActor("alice"),
            ))

    services.provider.before_call = delete_while_provider_is_running
    with pytest.raises(ActivationConflict, match="Deleted"):
        await services.index(document, pending)
    services.provider.before_call = None
    assert deleted and active_version(db_engine, private.document.id) == private.version.id
    shared_hits = await services.search.execute("timeout", owner_id="alice")
    assert {hit.document_id for hit in shared_hits} == {shared.document.id}

