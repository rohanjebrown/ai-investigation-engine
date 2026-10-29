from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects import postgresql

from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy, chunk_document
from investigation_engine.application.document_access import DocumentActor
from investigation_engine.application.embeddings import EmbeddingConfig
from investigation_engine.application.ingestion.extracted_document import ExtractedDocument
from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
from investigation_engine.application.use_cases.index_document_version import IndexDocumentVersion
from investigation_engine.application.use_cases.update_document import UpdateDocument
from investigation_engine.infrastructure.persistence.models import ChunkEmbeddingRow, DocumentRow, SearchableChunkRow
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


@pytest.fixture
def bm25_repository(sessions, repository, registration):
    class CharacterTokenizer:
        identity = TokenizerIdentity(name="bm25-test", revision="1", artifact_sha256="0" * 64, runtime_version="test")

        def offsets(self, text):
            return tuple((index, index + 1) for index in range(len(text)))

    class Provider:
        configuration = EmbeddingConfig(provider="test", model_id="bm25-test", model_revision="1")

        async def embed_documents(self, texts):
            return tuple((1.0,) + (0.0,) * 767 for _ in texts)

    provider = Provider()
    tokenizer = CharacterTokenizer()
    config = ChunkingConfig(
        strategy=ChunkingStrategy.WHOLE_PASSAGE, tokenizer=CharacterTokenizer.identity, max_tokens=512
    )
    plans = PostgreSQLChunkPlanRepository(sessions)
    activation = PostgreSQLDocumentActivationRepository(sessions)
    indexing = IndexDocumentVersion(
        plans, PostgreSQLEmbeddingRepository(sessions), activation, provider, max_attempts=1
    )
    evidence = PostgreSQLEvidenceRepository(sessions, lexical_backend="bm25")

    async def create(body, owner_id=None, active=True, selected_config=None):
        selected_config = selected_config or config
        document, version = registration(body=body, source_id=body + (owner_id or "shared"), owner_id=owner_id)
        document, version = await repository.register(document, version)
        plan = chunk_document(version, config=selected_config, tokenizer=tokenizer)
        await plans.save(plan)
        if active:
            await indexing.execute(document.id, version.id, selected_config, provider.configuration)
        return type("Item", (), {"document": document, "version": version, "plan": plan, "config": selected_config})()

    return type(
        "Fixture",
        (),
        {
            "create": staticmethod(create),
            "config": config,
            "provider": provider,
            "evidence": evidence,
            "plans": plans,
            "indexing": indexing,
            "repository": repository,
            "tokenizer": tokenizer,
        },
    )()


async def test_bm25_partial_terms_canonical_persistence_and_provenance(bm25_repository, db_engine):
    h = bm25_repository
    repository = h.evidence
    first = await h.create("Director received the Golden Umbrella award for the Velvet Slippers film.")
    duplicate = await h.create("Director received the Golden Umbrella award for the Velvet Slippers film.")
    unrelated = await h.create("Astronomy records describe stars and distant planets.")
    hits = await repository.search_keywords(
        "What award did the director of Velvet Slippers win?",
        h.config,
        h.provider.configuration,
        owner_id=None,
        limit=3,
    )
    assert hits
    assert hits[0].content_sha256 == first.version.content.sha256
    assert hits[0].source == min((first, duplicate), key=lambda item: item.document.id).version.source
    unrelated_rank = next(
        (index for index, hit in enumerate(hits, 1) if hit.content_sha256 == unrelated.version.content.sha256),
        len(hits) + 1,
    )
    assert unrelated_rank > 1
    with db_engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(SearchableChunkRow)
                .where(SearchableChunkRow.content_sha256 == bytes.fromhex(first.version.content.sha256))
            )
            == 1
        )


async def test_bm25_visibility_deleted_and_hybrid_rrf(bm25_repository, db_engine):
    h = bm25_repository
    repository = h.evidence
    secret = await h.create("Director won the rare Velvet Slippers award for the Golden Umbrella.", owner_id="alice")
    public = await h.create("Director won an award for Velvet Slippers.")
    deleted = await h.create("Director won a Velvet Slippers award for the Golden Umbrella.")
    with db_engine.begin() as connection:
        connection.execute(
            update(DocumentRow).where(DocumentRow.id == deleted.document.id).values(deleted_at=datetime.now(UTC))
        )
    lexical = await repository.search_keywords(
        "Velvet Slippers award Golden Umbrella", h.config, h.provider.configuration, owner_id=None, limit=1
    )
    assert lexical and lexical[0].document_id == public.document.id
    semantic, lexical = await repository.search_channels(
        (1.0, 0.0) + (0.0,) * 766,
        "Velvet Slippers award",
        h.config,
        h.provider.configuration,
        owner_id=None,
        semantic_limit=5,
        keyword_limit=5,
    )
    from investigation_engine.application.rrf import reciprocal_rank_fusion

    fused = reciprocal_rank_fusion(semantic, lexical, limit=5)
    assert fused and all(
        hit.document_id != secret.document.id and hit.document_id != deleted.document.id for hit in fused
    )
    assert any(hit.semantic_rank is not None and hit.keyword_rank is not None for hit in fused)


async def test_bm25_authorization_incomplete_and_configuration_filtering(bm25_repository, db_engine):
    h = bm25_repository
    secret = await h.create("Umbrella award Velvet Slippers director", owner_id="alice")
    public = await h.create("Umbrella award Velvet Slippers director public")
    incomplete = await h.create("Umbrella award Velvet Slippers director incomplete")
    alternative = replace(h.config, max_tokens=256)
    other_profile = await h.create(
        "Umbrella award Velvet Slippers director other configuration", selected_config=alternative
    )
    with db_engine.begin() as connection:
        from investigation_engine.infrastructure.persistence.models import VersionChunkPlanRow

        connection.execute(
            update(VersionChunkPlanRow)
            .where(VersionChunkPlanRow.document_version_id == incomplete.version.id)
            .values(expected_chunk_count=2)
        )
    shared_hits = await h.evidence.search_keywords(
        "Umbrella award Velvet Slippers director", h.config, h.provider.configuration, owner_id=None, limit=1
    )
    alice_hits = await h.evidence.search_keywords(
        "Umbrella award Velvet Slippers director", h.config, h.provider.configuration, owner_id="alice", limit=5
    )
    assert shared_hits and shared_hits[0].document_id == public.document.id
    assert any(hit.document_id == secret.document.id for hit in alice_hits)
    assert all(hit.document_id != incomplete.document.id for hit in shared_hits)
    assert all(hit.document_id != other_profile.document.id for hit in shared_hits)


async def test_bm25_update_hides_superseded_version_and_missing_embeddings(bm25_repository, db_engine):
    h = bm25_repository
    original = await h.create("Rare emerald constellation clue in the old report", owner_id="alice")
    updated_source = replace(original.version.source, revision="revision-2")
    updated_document, updated_version = await UpdateDocument(h.repository).execute(
        original.document.id,
        ExtractedDocument(text="Rare sapphire observatory clue in the revised report", source=updated_source),
        actor=DocumentActor("alice"),
        expected_current_version_id=original.version.id,
    )
    updated_plan = chunk_document(updated_version, config=h.config, tokenizer=h.tokenizer)
    await h.plans.save(updated_plan)
    await h.indexing.execute(updated_document.id, updated_version.id, h.config, h.provider.configuration)

    current_hits = await h.evidence.search_keywords(
        "sapphire observatory revised report", h.config, h.provider.configuration, owner_id="alice", limit=5
    )
    old_hits = await h.evidence.search_keywords(
        "emerald constellation old report", h.config, h.provider.configuration, owner_id="alice", limit=5
    )
    assert current_hits and current_hits[0].document_version_id == updated_version.id
    assert all(hit.document_version_id != original.version.id for hit in old_hits)

    with db_engine.begin() as connection:
        connection.execute(
            delete(ChunkEmbeddingRow).where(
                ChunkEmbeddingRow.content_sha256 == bytes.fromhex(updated_version.content.sha256)
            )
        )
    missing_embedding_hits = await h.evidence.search_keywords(
        "sapphire observatory revised report", h.config, h.provider.configuration, owner_id="alice", limit=5
    )
    assert all(hit.document_version_id != updated_version.id for hit in missing_embedding_hits)


async def test_actual_bm25_repository_query_explain_analyze(bm25_repository, db_engine):
    h = bm25_repository
    await h.create("Director won the Velvet Slippers award for the Golden Umbrella.")
    await h.create("Astronomy studies distant planets and stars.")
    await h.create("Film director received the international Velvet prize.")
    from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import (
        _bm25_statement,
        _eligible_memberships,
        _hydrate,
    )

    members = _eligible_memberships(h.config, h.provider.configuration, None)
    statement = _hydrate(_bm25_statement(members, "Velvet Slippers award", 10))
    compiled = statement.compile(dialect=postgresql.dialect())
    with db_engine.connect() as connection:
        try:
            plan = connection.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + compiled.string, compiled.params
            ).scalar_one()
        except Exception as error:
            original = getattr(error, "orig", None)
            pytest.fail(
                f"BM25 repository EXPLAIN failed: {getattr(original, 'sqlstate', type(error).__name__)} "
                f"{getattr(getattr(original, 'diag', None), 'message_primary', '')}",
                pytrace=False,
            )
    plan_root = plan[0]["Plan"]
    nodes = []
    pending = [plan_root]
    while pending:
        node = pending.pop()
        nodes.append(
            {
                "node": node["Node Type"],
                "index": node.get("Index Name"),
                "actual_ms": node.get("Actual Total Time"),
                "actual_rows": node.get("Actual Rows"),
                "shared_hit_blocks": node.get("Shared Hit Blocks", 0),
                "shared_read_blocks": node.get("Shared Read Blocks", 0),
                "temp_blocks": node.get("Temp Read Blocks", 0) + node.get("Temp Written Blocks", 0),
            }
        )
        pending.extend(node.get("Plans", []))
    print(
        "BM25_REPOSITORY_EXPLAIN",
        {
            "execution_ms": plan[0]["Execution Time"],
            "bm25_index_used": any(node["index"] == "searchable_chunks_bm25_idx" for node in nodes),
            "indexed_scans": [node for node in nodes if node["index"]],
            "temp_blocks": sum(node["temp_blocks"] for node in nodes),
            "slowest_nodes": sorted(nodes, key=lambda node: node["actual_ms"] or 0, reverse=True)[:5],
        },
    )
    assert "Execution Time" in plan[0]
