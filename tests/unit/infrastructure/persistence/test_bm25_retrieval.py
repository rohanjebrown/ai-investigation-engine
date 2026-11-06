from sqlalchemy import column, select, table

from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import _bm25_statement


def test_indexed_bm25_has_bounded_discovery_strict_frontier_and_disjoint_completion():
    from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy
    from investigation_engine.application.embeddings import EmbeddingConfig
    from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
    from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import _indexed_bm25_statement

    chunking = ChunkingConfig(
        strategy=ChunkingStrategy.WHOLE_PASSAGE,
        tokenizer=TokenizerIdentity(name="test", revision="1", artifact_sha256="0" * 64, runtime_version="test"),
        max_tokens=1024,
    )
    statement = _indexed_bm25_statement(
        chunking, EmbeddingConfig(provider="test", model_id="test"), "alice", "quasar", 20
    )
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "LIMIT 400" in sql
    assert "bm25_index_candidates AS MATERIALIZED" in sql
    assert "diverse_serving_memberships_bm25_first AS MATERIALIZED" in sql
    assert "bm25_frontier AS MATERIALIZED" in sql
    assert "bm25_remaining_contents" in sql and "NOT IN (SELECT bm25_first_contents.content_sha256" in sql
    assert "complete_serving_plans_bm25_first" in sql and "complete_serving_plans_bm25_rest" in sql
    assert "documents.owner_id = 'alice'" in sql
    # Canonical range ties stay after eligibility; strict score certification
    # decides whether the unchanged all-eligible ranking needs completion.
    assert "max(bm25_first_hits.ordering_value) < (SELECT max(bm25_index_candidates.score)" in sql
    assert "bm25_complete_memberships" in sql


def test_bm25_query_uses_operator_and_joins_eligible_candidates_before_limit():
    source = table(
        "eligible",
        column("content_sha256"),
        column("start"),
        column("end"),
        column("document_id"),
        column("version_id"),
        column("content_length"),
    )
    eligible = select(*source.c).cte("eligible")
    sql = str(_bm25_statement(eligible, "partial query", 7).compile(compile_kwargs={"literal_binds": True}))
    assert "<@> to_bm25query('partial query', 'searchable_chunks_bm25_idx')" in sql
    assert "JOIN eligible" in sql
    assert sql.index("JOIN eligible") < sql.index("LIMIT 7")
    assert "ORDER BY" in sql
