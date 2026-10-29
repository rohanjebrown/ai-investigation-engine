from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid7

import pytest
from sqlalchemy import event, func, select, update

from investigation_engine.application.answers import AnswerStatus, GenerationResult
from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy, chunk_document
from investigation_engine.application.embeddings import EmbeddingConfig, embedding_eligible
from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
from investigation_engine.application.rrf import reciprocal_rank_fusion
from investigation_engine.application.use_cases.answer_question import AnswerQuestion
from investigation_engine.application.use_cases.index_document_version import IndexDocumentVersion
from investigation_engine.application.use_cases.search_evidence import SearchEvidence
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.infrastructure.persistence._document_mapping import _version_row
from investigation_engine.infrastructure.persistence.models import ChunkEmbeddingRow, DocumentRow, VersionChunkPlanRow
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import (
    PostgreSQLChunkPlanRepository,
)
from investigation_engine.infrastructure.persistence.postgresql_document_activation_repository import (
    PostgreSQLDocumentActivationRepository,
)
from investigation_engine.infrastructure.persistence.postgresql_embedding_repository import (
    PostgreSQLEmbeddingRepository,
)
from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import PostgreSQLEvidenceRepository


def vector(x=1, y=0):
    return (float(x), float(y)) + (0.0,) * 766


@pytest.fixture
def hybrid(sessions, repository, registration):
    class Tokenizer:
        identity = TokenizerIdentity(name="characters", revision="1", artifact_sha256="0" * 64, runtime_version="test")

        def offsets(self, text):
            return tuple((i, i + 1) for i in range(len(text)))

    class Provider:
        configuration = EmbeddingConfig(provider="synthetic", model_id="hybrid", model_revision="1")

        def __init__(self):
            self.vectors = {}
            self.queries = []

        async def embed_documents(self, texts):
            return tuple(self.vectors.get(value, vector()) for value in texts)

        async def embed_query(self, question):
            self.queries.append(question)
            return vector()

    provider = Provider()
    config = ChunkingConfig(strategy=ChunkingStrategy.WHOLE_PASSAGE, tokenizer=Tokenizer.identity, max_tokens=512)
    plans, embeddings = PostgreSQLChunkPlanRepository(sessions), PostgreSQLEmbeddingRepository(sessions)
    activation = PostgreSQLDocumentActivationRepository(sessions)
    indexing = IndexDocumentVersion(plans, embeddings, activation, provider, max_attempts=1)
    evidence = PostgreSQLEvidenceRepository(sessions)

    async def create(
        body="quasar archive", *, config=config, owner_id=None, active=True, store=False, source=None, spans=()
    ):
        document, version = registration(body=body, source_id=str(uuid7()), owner_id=owner_id, source_spans=spans)
        if source is not None:
            version = replace(version, source=source)
        document, version = await repository.register(document, version)
        plan = chunk_document(version, config=config, tokenizer=Tokenizer())
        await plans.save(plan)
        if active:
            await indexing.execute(document.id, version.id, config, provider.configuration)
        elif store:
            for chunk in plan.chunks:
                if embedding_eligible(chunk):
                    await embeddings.save(chunk, provider.configuration, vector())
        return SimpleNamespace(document=document, version=version, plan=plan, config=config)

    async def keyword(question, *, config=config, profile=None, owner_id=None, limit=10):
        return await evidence.search_keywords(
            question, config, profile or provider.configuration, owner_id=owner_id, limit=limit
        )

    return SimpleNamespace(
        create=create,
        keyword=keyword,
        config=config,
        provider=provider,
        plans=plans,
        embeddings=embeddings,
        indexing=indexing,
        evidence=evidence,
    )


@pytest.mark.parametrize(
    "body,question",
    [
        ("Quasar archive catalogue", "quasar"),
        ("Ada Lovelace wrote notes", "Ada Lovelace"),
        ("Café — naïve astronomy", "café naïve"),
        ("서울 school archive", "서울"),
        ("quasar archive", "quasar & archive"),
        ("archived observations", "observations"),
    ],
)
async def test_keyword_entities_unicode_punctuation_and_multiple_terms(hybrid, body, question):
    item = await hybrid.create(body)
    hits = await hybrid.keyword(question)
    assert len(hits) == 1 and hits[0].document_version_id == item.version.id
    assert hits[0].text == body and hits[0].score > 0 and hits[0].score_kind == "keyword_rank"


@pytest.mark.parametrize("question", ["unrelated", "the is", "!!!", "quas"])
async def test_no_keyword_or_empty_tsquery_has_no_matches(hybrid, question):
    await hybrid.create()
    assert await hybrid.keyword(question) == ()


async def test_document_matches_do_not_make_unrelated_chunks_match_and_fragments_are_searchable(hybrid):
    config = replace(hybrid.config, strategy=ChunkingStrategy.FIXED_TOKENS, max_tokens=12)
    item = await hybrid.create("quasar earth pluto mars", config=config)
    assert [(h.start, h.end, h.text) for h in await hybrid.keyword("quasar", config=config)] == [
        (0, 12, "quasar earth")
    ]
    assert [(h.start, h.end, h.text) for h in await hybrid.keyword("mars", config=config)] == [(12, 23, " pluto mars")]
    assert await hybrid.keyword("quasar mars", config=config) == ()
    fragment_config = replace(config, max_tokens=5)
    await hybrid.create("microelectronics", config=fragment_config)
    hit = (await hybrid.keyword("micro", config=fragment_config))[0]
    assert hit.text == "micro" and hit.start == 0 and hit.end == 5
    assert hit.document_version_id != item.version.id


async def test_keyword_visibility_and_lifecycle_filter_before_top_k(hybrid, db_engine):
    h = hybrid
    shared = await h.create("quasar weak match")
    alice = await h.create("quasar " * 8, owner_id="alice")
    spoof = replace(alice.version.source, source_id="bob", metadata={"owner_id": "alice"})
    bob = await h.create("quasar " * 10, owner_id="bob", source=spoof)
    deleted = await h.create("quasar " * 20)
    await h.create("quasar " * 18, active=False, store=True)
    with db_engine.begin() as connection:
        connection.execute(
            update(DocumentRow).where(DocumentRow.id == deleted.document.id).values(deleted_at=datetime.now(UTC))
        )
    for owner, expected in ((None, shared), ("alice", alice), ("bob", bob)):
        hits = await h.keyword("quasar", owner_id=owner, limit=1)
        assert len(hits) == 1 and hits[0].document_id == expected.document.id
    assert {x.document_id for x in await h.keyword("quasar", owner_id="alice")} == {
        shared.document.id,
        alice.document.id,
    }


async def test_shared_vector_keyword_ties_and_rrf_keep_independent_provenance(hybrid, db_engine):
    a = await hybrid.create()
    b = await hybrid.create()
    semantic, keyword = await hybrid.evidence.search_channels(
        vector(),
        "quasar",
        hybrid.config,
        hybrid.provider.configuration,
        owner_id=None,
        semantic_limit=10,
        keyword_limit=10,
    )
    representative = min((a, b), key=lambda item: item.document.id)
    assert [hit.document_id for hit in keyword] == [representative.document.id]
    assert [hit.rank for hit in keyword] == [1]
    fused = reciprocal_rank_fusion(semantic, keyword, limit=10)
    assert len(fused) == 1 and fused[0].source == representative.version.source
    with db_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(ChunkEmbeddingRow)) == 1


async def test_keyword_unicode_chunk_keeps_original_intersecting_sentence_locations(hybrid):
    body = "😀 quasar archive"
    spans = (
        SourceSpan(start=0, end=2, locators=(SourceLocator(kind=LocatorKind.SENTENCE, index=0),)),
        SourceSpan(start=2, end=len(body), locators=(SourceLocator(kind=LocatorKind.SENTENCE, index=1),)),
    )
    config = replace(hybrid.config, strategy=ChunkingStrategy.FIXED_TOKENS, max_tokens=8)
    item = await hybrid.create(body, config=config, spans=spans)
    hit = (await hybrid.keyword("quasar", config=config))[0]
    assert hit.document_version_id == item.version.id
    assert (hit.start, hit.end, hit.text) == (0, 8, body[:8])
    assert hit.source_spans == spans
    assert hit.source_spans[1].end > hit.end  # Retain native bounds, not fabricated clipped locators.


async def test_superseded_version_retains_only_current_version_specific_locations(hybrid, sessions):
    h = hybrid
    old = await h.create()
    span = SourceSpan(
        start=0, end=len(old.version.content.text), locators=(SourceLocator(kind=LocatorKind.PAGE, index=3),)
    )
    later = replace(
        old.version,
        id=uuid7(),
        version_number=2,
        source=replace(old.version.source, revision="2"),
        source_spans=(span,),
    )
    async with sessions() as session:
        async with session.begin():
            session.add(_version_row(later))
    await h.plans.save(replace(old.plan, document_version_id=later.id))
    await h.indexing.execute(old.document.id, later.id, h.config, h.provider.configuration)
    hits = await h.keyword("quasar")
    assert len(hits) == 1 and hits[0].document_version_id == later.id and hits[0].source_spans == (span,)


async def test_keyword_excludes_incomplete_selected_embedding_and_chunking_profiles(hybrid, db_engine):
    h = hybrid
    config = replace(h.config, strategy=ChunkingStrategy.FIXED_TOKENS, max_tokens=6)
    partial = await h.create("quasar mars", config=config)
    h.provider.configuration = replace(h.provider.configuration, model_revision="other")
    await h.embeddings.save(partial.plan.chunks[0], h.provider.configuration, vector())
    assert await h.keyword("quasar", config=config) == ()
    complete = await h.create("quasar", config=config)
    assert [x.document_id for x in await h.keyword("quasar", config=config, limit=1)] == [complete.document.id]
    assert await h.keyword("quasar", config=replace(config, max_tokens=5)) == ()
    assert await h.keyword("quasar", config=config, profile=replace(h.provider.configuration, model_id="absent")) == ()
    with db_engine.begin() as connection:
        connection.execute(
            update(VersionChunkPlanRow)
            .where(VersionChunkPlanRow.document_version_id == complete.version.id)
            .values(expected_chunk_count=2)
        )
    assert await h.keyword("quasar", config=config) == ()


async def test_hybrid_reads_both_channels_once_from_same_snapshot_before_concurrent_delete(hybrid, sessions, db_engine):
    h = hybrid
    item = await h.create()
    fired = False
    statements = []

    def after(connection, cursor, statement, parameters, context, executemany):
        nonlocal fired
        if statement.lstrip().startswith("WITH"):
            statements.append(statement)
            if not fired:
                fired = True
                with db_engine.begin() as writer:
                    writer.execute(
                        update(DocumentRow)
                        .where(DocumentRow.id == item.document.id)
                        .values(deleted_at=datetime.now(UTC))
                    )

    engine = sessions.kw["bind"].sync_engine
    event.listen(engine, "after_cursor_execute", after)
    try:
        sem, kw = await h.evidence.search_channels(
            vector(), "quasar", h.config, h.provider.configuration, owner_id=None, semantic_limit=1, keyword_limit=1
        )
    finally:
        event.remove(engine, "after_cursor_execute", after)
    assert len(statements) == 1 and "UNION ALL" in statements[0]
    assert len(sem) == len(kw) == 1 and sem[0].identity == kw[0].identity
    assert await h.evidence.search_channels(
        vector(), "quasar", h.config, h.provider.configuration, owner_id=None, semantic_limit=1, keyword_limit=1
    ) == ((), ())


async def test_real_rag_hybrid_promotes_keyword_supported_evidence_above_semantic_distractors(hybrid):
    h = hybrid
    target = "Zephyr Archive catalogue"
    h.provider.vectors = {
        target: vector(1, 1),
        "weather calendar ocean": vector(1, 0),
        "copper mineral belt": vector(-1, 0),
    }
    relevant = await h.create(target)
    await h.create("weather calendar ocean")
    await h.create("copper mineral belt")
    baseline = SearchEvidence(h.evidence, h.provider, h.config, h.provider.configuration)
    assert (await baseline.execute("Zephyr Archive", 1, owner_id=None))[0].text == "weather calendar ocean"
    search = SearchEvidence(
        h.evidence,
        h.provider,
        h.config,
        h.provider.configuration,
        mode="hybrid",
        hybrid_repository=h.evidence,
        semantic_candidates=3,
        keyword_candidates=1,
    )
    generator = SimpleNamespace(
        generate=AsyncMock(
            return_value=GenerationResult(
                status=AnswerStatus.ANSWERED, answer="Zephyr Archive has a catalogue.", citations=("E1",)
            )
        )
    )
    result = await AnswerQuestion(search, generator, max_evidence=1).execute("Zephyr Archive", owner_id=None)
    evidence = result.citations[0].evidence
    assert evidence.document_id == relevant.document.id and evidence.score_kind == "rrf"
    assert evidence.semantic_rank == 2 and evidence.keyword_rank == 1
    assert evidence.semantic_score == pytest.approx(2**-0.5) and evidence.keyword_score > 0
    assert generator.generate.await_args.args[0].evidence[0].text == target


async def test_duplicates_do_not_crowd_top_k_and_ineligible_copies_cannot_be_representatives(hybrid, db_engine):
    from sqlalchemy import delete

    from investigation_engine.infrastructure.persistence.models import ChunkMembershipRow, DocumentVersionRow

    private = await hybrid.create("quasar repeated", owner_id="other-user")
    incomplete = await hybrid.create("quasar repeated")
    with db_engine.begin() as connection:
        connection.execute(
            delete(ChunkMembershipRow).where(ChunkMembershipRow.document_version_id == incomplete.version.id)
        )
    shared = [await hybrid.create("quasar repeated") for _ in range(4)]
    distinct = await hybrid.create("quasar distinct")
    semantic, keyword = await hybrid.evidence.search_channels(
        vector(),
        "quasar",
        hybrid.config,
        hybrid.provider.configuration,
        owner_id=None,
        semantic_limit=2,
        keyword_limit=2,
    )
    representative = min(shared, key=lambda item: item.document.id)
    for channel in (semantic, keyword):
        assert len(channel) == 2
        assert {hit.document_id for hit in channel} == {representative.document.id, distinct.document.id}
        assert private.document.id not in {hit.document_id for hit in channel}
        assert incomplete.document.id not in {hit.document_id for hit in channel}
        assert (
            next(hit for hit in channel if hit.document_id == representative.document.id).source
            == representative.version.source
        )
    with db_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(DocumentVersionRow)) == 7
        assert connection.scalar(select(func.count()).select_from(ChunkEmbeddingRow)) == 2


async def test_single_chunk_fast_path_rejects_extra_membership_even_when_declared_count_is_one(hybrid, db_engine):
    from investigation_engine.infrastructure.persistence.models import ChunkMembershipRow

    bad = await hybrid.create("quasar invalid")
    good = await hybrid.create("quasar valid")
    with db_engine.begin() as connection:
        configuration_id = connection.scalar(
            select(VersionChunkPlanRow.chunking_configuration_id).where(
                VersionChunkPlanRow.document_version_id == bad.version.id
            )
        )
        connection.execute(
            ChunkMembershipRow.__table__.insert().values(
                document_version_id=bad.version.id,
                chunking_configuration_id=configuration_id,
                ordinal=1,
                start=1,
                end=3,
            )
        )
    semantic, keyword = await hybrid.evidence.search_channels(
        vector(),
        "quasar",
        hybrid.config,
        hybrid.provider.configuration,
        owner_id=None,
        semantic_limit=1,
        keyword_limit=1,
    )
    assert [hit.document_id for hit in semantic] == [good.document.id]
    assert [hit.document_id for hit in keyword] == [good.document.id]


async def test_compound_quick_answer_uses_both_facts_when_whole_question_lexical_match_is_empty(hybrid):
    first = "Maheen Khan worked on embroidery for Snow White and the Huntsman."
    second = "Alice Washburn was an actress who appeared in Snow White as her only known feature film."
    hybrid.provider.vectors = {first: vector(1, 0), second: vector(0, 1)}
    a, b = await hybrid.create(first), await hybrid.create(second)
    question = (
        "Who worked on embroidery for Snow White and the Huntsman, "
        "and which actress appeared in Snow White as her only known feature film?"
    )
    # A single all-terms lexical query cannot match either disjoint source.
    assert await hybrid.keyword(question) == ()
    assert await hybrid.keyword("embroidery Snow White Huntsman")
    assert await hybrid.keyword("actress Snow White only known feature film")

    async def embed_query(query):
        hybrid.provider.queries.append(query)
        return vector(1, 0) if "embroidery" in query else vector(0, 1)

    hybrid.provider.embed_query = embed_query
    search = SearchEvidence(
        hybrid.evidence,
        hybrid.provider,
        hybrid.config,
        hybrid.provider.configuration,
        mode="hybrid",
        hybrid_repository=hybrid.evidence,
    )
    generator = SimpleNamespace(
        generate=AsyncMock(
            return_value=GenerationResult(
                status=AnswerStatus.ANSWERED,
                answer="Maheen Khan [E1]; Alice Washburn [E2].",
                citations=("E1", "E2"),
            )
        )
    )
    result = await AnswerQuestion(search, generator, max_evidence=2).execute(question, owner_id=None)
    assert result.status == AnswerStatus.ANSWERED
    assert result.embedding_requests == 2 and result.generation_requests == 1
    assert [citation.evidence.document_version_id for citation in result.citations] == [a.version.id, b.version.id]
    assert [item.text for item in generator.generate.await_args.args[0].evidence] == [first, second]
