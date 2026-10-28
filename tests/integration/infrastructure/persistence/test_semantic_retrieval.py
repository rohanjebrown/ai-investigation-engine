from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from math import sqrt
from statistics import median
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid7

import pytest
from sqlalchemy import bindparam, delete, event, func, insert, select, text, update

from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy, chunk_document
from investigation_engine.application.embeddings import EmbeddingConfig, embedding_eligible
from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
from investigation_engine.application.use_cases.index_document_version import IndexDocumentVersion
from investigation_engine.application.use_cases.search_evidence import SearchEvidence
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.infrastructure.persistence._document_mapping import _version_row
from investigation_engine.infrastructure.persistence.models import (
    ChunkEmbeddingRow, ChunkMembershipRow, ChunkingConfigurationRow, ContentRow, DocumentRow,
    DocumentVersionRow, EmbeddingConfigurationRow, VersionChunkPlanRow,
)
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import PostgreSQLChunkPlanRepository
from investigation_engine.infrastructure.persistence.postgresql_document_activation_repository import PostgreSQLDocumentActivationRepository
from investigation_engine.infrastructure.persistence.postgresql_embedding_repository import PostgreSQLEmbeddingRepository
from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import PostgreSQLEvidenceRepository


def vector(x=1.0, y=0.0):
    return (float(x), float(y)) + (0.0,) * 766


@pytest.fixture
def retrieval(sessions, repository, registration):
    class Tokenizer:
        identity = TokenizerIdentity(name="characters", revision="1", artifact_sha256="0" * 64, runtime_version="test")

        def offsets(self, text):
            return tuple((i, i + 1) for i in range(len(text)))

    class Provider:
        configuration = EmbeddingConfig(provider="synthetic", model_id="known-cosine", model_revision="1")

        def __init__(self):
            self.documents = []
            self.queries = []
            self.vectors = {}
            self.before_query = None

        async def embed_documents(self, texts):
            self.documents.extend(texts)
            return tuple(self.vectors.get(text, vector()) for text in texts)

        async def embed_query(self, question):
            self.queries.append(question)
            if self.before_query is not None:
                self.before_query()
            return vector()

    provider = Provider()
    config = ChunkingConfig(strategy=ChunkingStrategy.WHOLE_PASSAGE, tokenizer=Tokenizer.identity, max_tokens=100)
    plans = PostgreSQLChunkPlanRepository(sessions)
    embeddings = PostgreSQLEmbeddingRepository(sessions)
    activation = PostgreSQLDocumentActivationRepository(sessions)
    workflow = IndexDocumentVersion(plans, embeddings, activation, provider, max_attempts=1)
    evidence = PostgreSQLEvidenceRepository(sessions)

    async def create(body="passage", *, owner_id=None, config=config, activate=True, store=False, spans=(), source=None):
        document, version = registration(body=body, source_id=str(uuid7()), owner_id=owner_id, source_spans=spans)
        if source is not None:
            version = replace(version, source=source)
        document, version = await repository.register(document, version)
        plan = chunk_document(version, config=config, tokenizer=Tokenizer())
        await plans.save(plan)
        if activate:
            await workflow.execute(document.id, version.id, config, provider.configuration)
        elif store:
            for chunk in plan.chunks:
                if embedding_eligible(chunk):
                    await embeddings.save(chunk, provider.configuration, provider.vectors.get(chunk.text, vector()))
        return SimpleNamespace(document=document, version=version, plan=plan, config=config)

    def search(config=config, profile=None):
        return SearchEvidence(evidence, provider, config, profile or provider.configuration)

    return SimpleNamespace(create=create, config=config, plans=plans, embeddings=embeddings,
                           activation=activation, workflow=workflow, provider=provider, evidence=evidence, search=search)


async def test_end_to_end_known_cosine_order_limits_ties_and_restarted_search(retrieval, sessions, db_engine):
    r = retrieval
    r.provider.vectors = {"same direction": vector(2), "scaled direction": vector(7),
                          "diagonal": vector(1, 1), "orthogonal": vector(0, 3), "opposite": vector(-1)}
    items = [await r.create(body) for body in r.provider.vectors]
    before = tuple(r.provider.documents)
    hits = await r.search().execute("Which direction?", owner_id=None)
    tied = sorted(items[:2], key=lambda item: item.document.id)
    assert [hit.document_id for hit in hits] == [item.document.id for item in tied + items[2:]]
    assert [hit.score for hit in hits] == pytest.approx([1, 1, 1 / sqrt(2), 0, -1])
    assert [hit.rank for hit in hits] == [1, 2, 3, 4, 5]
    assert [hit.text for hit in hits] == [item.version.content.text for item in tied + items[2:]]
    assert all(hit.source_spans == () for hit in hits)
    assert r.provider.documents == list(before) and r.provider.queries == ["Which direction?"]
    restarted = SearchEvidence(PostgreSQLEvidenceRepository(sessions), r.provider, r.config, r.provider.configuration)
    assert await restarted.execute("Which direction?", 2, owner_id=None) == hits[:2]
    with db_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(ChunkEmbeddingRow)) == 5


async def test_filter_before_top_k_with_owner_metadata_deletion_and_inactive_versions(retrieval, db_engine):
    r = retrieval
    r.provider.vectors["eligible farther"] = vector(1, 2)
    shared = await r.create("eligible farther")
    alice = await r.create("alice private", owner_id="principal:alice")
    bob_source = replace(alice.version.source, source_id="bob-source", metadata={"owner_id": "principal:alice"})
    bob = await r.create("bob private", owner_id="principal:bob", source=bob_source)
    deleted = await r.create("deleted nearest")
    await r.create("inactive nearest", activate=False, store=True)
    with db_engine.begin() as connection:
        connection.execute(update(DocumentRow).where(DocumentRow.id == deleted.document.id).values(deleted_at=datetime.now(UTC)))
    assert [hit.document_id for hit in await r.search().execute("q", 1, owner_id=None)] == [shared.document.id]
    assert [hit.document_id for hit in await r.search().execute("q", 1, owner_id="principal:alice")] == [alice.document.id]
    assert [hit.document_id for hit in await r.search().execute("q", 1, owner_id="principal:bob")] == [bob.document.id]
    assert {hit.document_id for hit in await r.search().execute("q", owner_id="principal:alice")} == {shared.document.id, alice.document.id}
    assert {hit.document_id for hit in await r.search().execute("q", owner_id="principal:bob")} == {shared.document.id, bob.document.id}


async def test_shared_vector_retains_independent_source_occurrences_and_exact_provenance(retrieval, db_engine):
    r = retrieval
    a = await r.create("identical source content")
    source = replace(a.version.source, source_id="another dataset occurrence", revision="pinned-revision",
                     record_locator={"split": "dev", "question_id": "question-2", "context_position": 9},
                     metadata={"title": "Distinct passage title"})
    b = await r.create("identical source content", source=source)
    hits = await r.search().execute("q", owner_id=None)
    assert {hit.identity for hit in hits} == {(a.version.id, 0, 24), (b.version.id, 0, 24)}
    by_version = {hit.document_version_id: hit for hit in hits}
    assert by_version[a.version.id].source == a.version.source
    assert by_version[b.version.id].source == source
    assert by_version[a.version.id].content_sha256 == by_version[b.version.id].content_sha256
    with db_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(ChunkEmbeddingRow)) == 1
    hits[0].source.metadata["modified"] = True
    assert all("modified" not in hit.source.metadata for hit in await r.search().execute("q", owner_id=None))


async def test_unicode_ranges_resolve_multiple_original_partial_spans(retrieval):
    r = retrieval
    body = "A😀 BC. DéF"
    spans = (
        SourceSpan(start=0, end=2, locators=(SourceLocator(kind=LocatorKind.PAGE, index=0),)),
        SourceSpan(start=3, end=6, locators=(SourceLocator(kind=LocatorKind.SENTENCE, index=4),)),
        SourceSpan(start=7, end=10, locators=(SourceLocator(kind=LocatorKind.PARAGRAPH, index=2),
                                          SourceLocator(kind=LocatorKind.LINE, index=8))),
    )
    config = replace(r.config, strategy=ChunkingStrategy.FIXED_TOKENS, max_tokens=5)
    item = await r.create(body, config=config, spans=spans)
    hits = await r.search(config).execute("q", owner_id=None)
    assert [(hit.start, hit.end, hit.text) for hit in hits] == [(0, 5, body[:5]), (5, 10, body[5:])]
    assert hits[0].source_spans == spans[:2]
    assert hits[1].source_spans == spans[1:]
    assert all(hit.document_version_id == item.version.id for hit in hits)
    assert hits[1].source_spans[0].start == 3  # Original span, not a fabricated clipped sentence.


async def test_overlapping_fixed_ranges_retain_distinct_canonical_identities(retrieval):
    r = retrieval
    config = replace(r.config, strategy=ChunkingStrategy.FIXED_TOKENS, max_tokens=4, overlap=2)
    item = await r.create("abcdefgh", config=config)
    hits = await r.search(config).execute("q", owner_id=None)
    assert [(hit.start, hit.end, hit.text) for hit in hits] == [(0, 4, "abcd"), (2, 6, "cdef"), (4, 8, "efgh")]
    assert len({hit.identity for hit in hits}) == 3
    assert all(hit.document_version_id == item.version.id for hit in hits)


async def test_superseded_version_and_old_locations_are_excluded(retrieval, sessions):
    r = retrieval
    old = await r.create("same content")
    span = SourceSpan(start=0, end=12, locators=(SourceLocator(kind=LocatorKind.PAGE, index=7),))
    # Fixture setup only: appending source revisions is outside the registration API.
    version = replace(old.version, id=uuid7(), version_number=2, source=replace(old.version.source, revision="2"),
                      source_spans=(span,))
    async with sessions() as session:
        async with session.begin():
            session.add(_version_row(version))
    await r.plans.save(replace(old.plan, document_version_id=version.id))
    await r.workflow.execute(old.document.id, version.id, old.config, r.provider.configuration)
    hits = await r.search().execute("q", owner_id=None)
    assert len(hits) == 1 and hits[0].document_version_id == version.id
    assert hits[0].source_spans == (span,) and hits[0].source.revision == "2"


async def test_selected_embedding_profile_requires_every_eligible_vector_before_top_k(retrieval):
    r = retrieval
    config = replace(r.config, strategy=ChunkingStrategy.FIXED_TOKENS, max_tokens=1)
    partial = await r.create("ab", config=config)
    complete = await r.create("c", config=config)
    r.provider.configuration = replace(r.provider.configuration, model_revision="alternate")
    await r.embeddings.save(partial.plan.chunks[0], r.provider.configuration, vector())
    await r.embeddings.save(complete.plan.chunks[0], r.provider.configuration, vector(1, 2))
    hits = await r.search(config).execute("q", 1, owner_id=None)
    assert [hit.document_id for hit in hits] == [complete.document.id]
    await r.embeddings.save(partial.plan.chunks[1], r.provider.configuration, vector())
    assert len(await r.search(config).execute("q", owner_id=None)) == 3
    r.provider.configuration = replace(r.provider.configuration, model_id="missing-model")
    assert await r.search(config).execute("q", owner_id=None) == ()


@pytest.mark.parametrize("changes", [dict(provider="other-provider"), dict(model_id="other-model"), dict(model_revision=None)])
async def test_exact_serving_embedding_identity_prevents_cross_profile_search(retrieval, changes):
    r = retrieval
    await r.create()
    r.provider.configuration = replace(r.provider.configuration, **changes)
    assert await r.search().execute("q", owner_id=None) == ()


async def test_selected_chunking_profile_requires_complete_plan(retrieval):
    r = retrieval
    item = await r.create("ab")
    other = replace(r.config, strategy=ChunkingStrategy.FIXED_TOKENS, max_tokens=1)
    assert await r.search(other).execute("q", owner_id=None) == ()
    plan = replace(item.plan, config=other, chunks=tuple(
        replace(item.plan.chunks[0], start=i, end=i + 1) for i in range(2)))
    await r.plans.save(plan)
    await r.embeddings.save(plan.chunks[0], r.provider.configuration, vector())
    assert await r.search(other).execute("q", owner_id=None) == ()
    await r.embeddings.save(plan.chunks[1], r.provider.configuration, vector())
    assert len(await r.search(other).execute("q", owner_id=None)) == 2


@pytest.mark.parametrize("corruption", ["missing", "ordinal", "count", "gap", "outside"])
async def test_invalid_active_plan_is_excluded_before_limit(retrieval, db_engine, corruption):
    r = retrieval
    config = replace(r.config, strategy=ChunkingStrategy.FIXED_TOKENS, max_tokens=2)
    bad = await r.create("abcd", config=config)
    r.provider.vectors["valid"] = vector(1, 3)
    good = await r.create("valid", config=config)
    with db_engine.begin() as connection:
        member = ChunkMembershipRow.document_version_id == bad.version.id
        if corruption == "missing":
            connection.execute(delete(ChunkMembershipRow).where(member, ChunkMembershipRow.ordinal == 1))
        elif corruption == "ordinal":
            connection.execute(update(ChunkMembershipRow).where(member, ChunkMembershipRow.ordinal == 1).values(ordinal=2))
        elif corruption == "count":
            connection.execute(update(VersionChunkPlanRow).where(VersionChunkPlanRow.document_version_id == bad.version.id)
                               .values(expected_chunk_count=3))
        elif corruption == "gap":
            connection.execute(update(ChunkMembershipRow).where(member, ChunkMembershipRow.ordinal == 1).values(start=3))
        else:
            connection.execute(update(ChunkMembershipRow).where(member, ChunkMembershipRow.ordinal == 1).values(end=5))
    hits = await r.search(config).execute("q", 1, owner_id=None)
    assert len(hits) == 1 and hits[0].document_id == good.document.id


async def test_python_unicode_whitespace_agrees_with_indexer_and_empty_active_states_are_hidden(retrieval, db_engine):
    r = retrieval
    config = replace(r.config, strategy=ChunkingStrategy.FIXED_TOKENS, max_tokens=1)
    mixed = await r.create("\u0085\xa0\x1c😀\u2007", config=config)
    blank = await r.create("\u0085\xa0\x1c", config=config, activate=False)
    empty = await r.create("", config=config, activate=False)
    # Legacy invalid pointers must not make blank/empty documents searchable.
    with db_engine.begin() as connection:
        for item in (blank, empty):
            connection.execute(update(DocumentRow).where(DocumentRow.id == item.document.id)
                               .values(current_version_id=item.version.id))
    hits = await r.search(config).execute("q", owner_id=None)
    assert [(hit.document_id, hit.start, hit.end, hit.text) for hit in hits] == [(mixed.document.id, 3, 4, "😀")]


async def test_equivalent_embedding_rows_do_not_duplicate_or_change_reused_vector(retrieval, db_engine):
    r = retrieval
    item = await r.create()
    with db_engine.begin() as connection:
        values = connection.execute(select(EmbeddingConfigurationRow)).mappings().one()
        duplicate_id = connection.scalar(insert(EmbeddingConfigurationRow).values(
            **{k: v for k, v in values.items() if k != "id"}).returning(EmbeddingConfigurationRow.id))
        chunk = item.plan.chunks[0]
        connection.execute(insert(ChunkEmbeddingRow).values(
            embedding_configuration_id=duplicate_id, content_sha256=bytes.fromhex(chunk.content_sha256),
            start=chunk.start, end=chunk.end, embedding=list(vector(-1)), created_at=datetime.now(UTC)))
    hits = await r.search().execute("q", owner_id=None)
    assert len(hits) == 1 and hits[0].score == pytest.approx(1)


async def test_ambiguous_equivalent_chunk_plans_are_not_served(retrieval, db_engine):
    r = retrieval
    item = await r.create()
    with db_engine.begin() as connection:
        values = connection.execute(select(ChunkingConfigurationRow)).mappings().one()
        duplicate_id = connection.scalar(insert(ChunkingConfigurationRow).values(
            **{k: v for k, v in values.items() if k != "id"}).returning(ChunkingConfigurationRow.id))
        connection.execute(insert(VersionChunkPlanRow).values(document_version_id=item.version.id,
                            chunking_configuration_id=duplicate_id, expected_chunk_count=1, created_at=datetime.now(UTC)))
        connection.execute(insert(ChunkMembershipRow).values(document_version_id=item.version.id,
                            chunking_configuration_id=duplicate_id, ordinal=0, start=0, end=len(item.version.content.text)))
    assert await r.search().execute("q", owner_id=None) == ()


async def test_query_embedding_has_no_open_database_session(retrieval, sessions):
    r = retrieval
    await r.create()
    engine = sessions.kw["bind"].sync_engine
    active = 0

    def checkout(*args):
        nonlocal active
        active += 1

    def checkin(*args):
        nonlocal active
        active -= 1

    def before_query():
        assert active == 0

    r.provider.before_query = before_query
    event.listen(engine, "checkout", checkout)
    event.listen(engine, "checkin", checkin)
    try:
        assert len(await r.search().execute("q", owner_id=None)) == 1
        assert active == 0
    finally:
        event.remove(engine, "checkout", checkout)
        event.remove(engine, "checkin", checkin)


async def test_empty_database_and_query_dimension_validation(retrieval):
    r = retrieval
    assert await r.search().execute("q", owner_id=None) == ()
    with pytest.raises(ValueError, match="768"):
        await r.evidence.search((1.0,) * 767, r.config, r.provider.configuration, owner_id=None, limit=1)


async def test_bounded_single_statement_search_over_one_thousand_distinct_vectors(retrieval, sessions, db_engine):
    r = retrieval
    base = await r.create()
    # Bulk fixture construction only; application ingestion still uses its real
    # repositories, as covered by the end-to-end tests above.
    with db_engine.begin() as connection:
        doc = dict(connection.execute(select(DocumentRow)).mappings().one())
        version = dict(connection.execute(select(DocumentVersionRow)).mappings().one())
        plan = dict(connection.execute(select(VersionChunkPlanRow)).mappings().one())
        stored = dict(connection.execute(select(ChunkEmbeddingRow)).mappings().one())
        contents, documents, versions, plans, memberships, embeddings, pointers = [], [], [], [], [], [], []
        for i in range(1000):
            body = f"synthetic passage {i:04}"
            digest = sha256(body.encode("utf-8")).digest()
            doc_id, version_id = uuid7(), uuid7()
            contents.append(dict(sha256=digest, text=body))
            documents.append({**doc, "id": doc_id, "current_version_id": None})
            versions.append({**version, "id": version_id, "document_id": doc_id,
                             "content_sha256": digest, "source_id": f"synthetic:{i}"})
            plans.append({**plan, "document_version_id": version_id})
            memberships.append(dict(document_version_id=version_id, chunking_configuration_id=plan["chunking_configuration_id"],
                                    ordinal=0, start=0, end=len(body)))
            embeddings.append({**stored, "content_sha256": digest, "end": len(body),
                               "embedding": list(vector(i % 10 + 1, 10))})
            pointers.append(dict(doc_key=doc_id, version_key=version_id))
        for model, rows in ((ContentRow, contents), (DocumentRow, documents), (DocumentVersionRow, versions),
                            (VersionChunkPlanRow, plans), (ChunkMembershipRow, memberships), (ChunkEmbeddingRow, embeddings)):
            connection.execute(insert(model.__table__), rows)
        connection.execute(update(DocumentRow.__table__).where(DocumentRow.id == bindparam("doc_key"))
                           .values(current_version_id=bindparam("version_key")), pointers)
        # Fresh bulk fixtures have no autovacuum statistics yet. Analyze only
        # these disposable tables so the timing uses representative estimates.
        for model in (ContentRow, DocumentRow, DocumentVersionRow, VersionChunkPlanRow,
                      ChunkMembershipRow, ChunkEmbeddingRow, ChunkingConfigurationRow, EmbeddingConfigurationRow):
            connection.execute(text(f"ANALYZE {model.__tablename__}"))

    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    engine = sessions.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    times = []
    try:
        for _ in range(5):
            started = perf_counter()
            hits = await r.search().execute("q", 10, owner_id=None)
            times.append((perf_counter() - started) * 1000)
            assert len(hits) == 10 and hits[0].document_id == base.document.id
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(statements) == 5  # One bounded SQL result, no per-result fetches.
    statement, parameters = statements[-1]
    with db_engine.connect() as connection:
        explain = connection.exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, parameters).scalar_one()[0]
    assert explain["Plan"]["Actual Rows"] == 10
    print(f"\nSynthetic exact search: 1,001 active documents / distinct vectors, limit 10, "
          f"5 consecutive end-to-end calls median={median(times):.2f} ms, "
          f"EXPLAIN execution={explain['Execution Time']:.2f} ms, "
          f"planning={explain['Planning Time']:.2f} ms. Not a full-corpus benchmark.")
