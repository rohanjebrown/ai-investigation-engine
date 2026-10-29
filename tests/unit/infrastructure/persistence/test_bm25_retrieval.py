from sqlalchemy import column, select, table

from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import _bm25_statement


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
