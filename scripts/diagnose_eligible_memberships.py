"""Read-only EXPLAIN ANALYZE for the serving eligibility CTEs, without vector scoring."""

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from investigation_engine.application.embeddings import EmbeddingConfig  # noqa: E402
from investigation_engine.infrastructure.config import settings  # noqa: E402
from investigation_engine.infrastructure.embeddings.gemini_provider import (  # noqa: E402
    DIMENSIONS,
    DOCUMENT_FORMAT_VERSION,
    MODEL,
)
from investigation_engine.infrastructure.persistence.models import ChunkingConfigurationRow  # noqa: E402
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import (  # noqa: E402
    _configuration,
)
from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import (  # noqa: E402
    _eligible_memberships,
)


def _plan_nodes(root):
    nodes = []

    def visit(node, path):
        loops = node.get("Actual Loops", 0)
        actual_time = node.get("Actual Total Time", 0.0)
        plan_rows = node.get("Plan Rows", 0)
        actual_rows = node.get("Actual Rows")
        nodes.append(
            {
                "path": path,
                "node_type": node.get("Node Type"),
                "join_type": node.get("Join Type"),
                "strategy": node.get("Strategy"),
                "cte_name": node.get("CTE Name"),
                "subplan_name": node.get("Subplan Name"),
                "relation": node.get("Relation Name"),
                "index": node.get("Index Name"),
                "plan_rows": plan_rows,
                "actual_rows_per_loop": actual_rows,
                "actual_loops": loops,
                "estimated_rows_ratio": (
                    round(actual_rows / plan_rows, 3) if actual_rows is not None and plan_rows else None
                ),
                "inclusive_ms": round(actual_time * loops, 3),
                "shared_hit_blocks": node.get("Shared Hit Blocks", 0),
                "shared_read_blocks": node.get("Shared Read Blocks", 0),
                "shared_dirtied_blocks": node.get("Shared Dirtied Blocks", 0),
                "shared_written_blocks": node.get("Shared Written Blocks", 0),
                "temp_read_blocks": node.get("Temp Read Blocks", 0),
                "temp_written_blocks": node.get("Temp Written Blocks", 0),
                "sort_method": node.get("Sort Method"),
                "sort_space_type": node.get("Sort Space Type"),
                "sort_space_used_kb": node.get("Sort Space Used"),
            }
        )
        for index, child in enumerate(node.get("Plans", []), start=1):
            visit(child, f"{path}.{index}")

    visit(root, "1")
    return nodes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--owner-id",
        help="Private owner scope to include alongside shared documents; omit for the public shared-only scope.",
    )
    args = parser.parse_args()

    if settings.serving_chunking_configuration_id is None:
        raise RuntimeError("SERVING_CHUNKING_CONFIGURATION_ID must select the serving chunking profile")

    embedding = EmbeddingConfig(
        provider="gemini_developer_api",
        model_id=MODEL,
        dimensions=DIMENSIONS,
        document_format=DOCUMENT_FORMAT_VERSION,
    )
    engine = create_engine(
        settings.database_url(),
        hide_parameters=True,
        pool_size=1,
        max_overflow=0,
        connect_args={
            "options": (
                "-c default_transaction_read_only=on "
                f"-c statement_timeout={settings.db_statement_timeout_ms}"
            ),
        },
    )
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                if connection.scalar(text("SHOW transaction_read_only")) != "on":
                    raise RuntimeError("Database transaction is not read-only")

                config_row = connection.execute(
                    select(ChunkingConfigurationRow.__table__).where(
                        ChunkingConfigurationRow.id == settings.serving_chunking_configuration_id
                    )
                ).mappings().one_or_none()
                if config_row is None:
                    raise RuntimeError("Configured serving chunking profile was not found")
                chunking = _configuration(SimpleNamespace(**config_row))

                eligible = _eligible_memberships(chunking, embedding, args.owner_id)
                statement = select(*eligible.c)
                compiled = statement.compile(dialect=connection.dialect)
                sql = str(compiled)
                if "<=>" in sql or "semantic_scores" in sql:
                    raise RuntimeError("Eligibility diagnostic unexpectedly includes vector scoring")
                parameters = compiled.construct_params()
                for name, processor in compiled._bind_processors.items():
                    parameters[name] = processor(parameters[name])

                raw_plan = connection.exec_driver_sql(
                    "EXPLAIN (ANALYZE, BUFFERS, VERBOSE, FORMAT JSON) " + sql,
                    parameters,
                ).scalar_one()
                explain = raw_plan[0] if isinstance(raw_plan, list) else json.loads(raw_plan)[0]
                root = explain["Plan"]
                nodes = _plan_nodes(root)
                materialized = [
                    node
                    for node in nodes
                    if node["cte_name"]
                    or node["node_type"] in ("CTE Scan", "Materialize")
                    or (node["subplan_name"] and node["subplan_name"].startswith("CTE "))
                ]
                report = {
                    "database": connection.scalar(text("SELECT current_database()")),
                    "owner_scope": "shared_only" if args.owner_id is None else "shared_plus_owner",
                    "owner_id": args.owner_id,
                    "chunking_configuration_id": settings.serving_chunking_configuration_id,
                    "chunking_strategy": chunking.strategy.value,
                    "chunking_max_tokens": chunking.max_tokens,
                    "chunking_overlap": chunking.overlap,
                    "embedding_configuration": {
                        "provider": embedding.provider,
                        "model_id": embedding.model_id,
                        "model_revision": embedding.model_revision,
                        "dimensions": embedding.dimensions,
                        "document_format": embedding.document_format,
                        "normalization": embedding.normalization,
                    },
                    "planning_time_ms": explain.get("Planning Time"),
                    "execution_time_ms": explain.get("Execution Time"),
                    "root_rows": {
                        "estimated": root.get("Plan Rows"),
                        "actual_per_loop": root.get("Actual Rows"),
                        "loops": root.get("Actual Loops"),
                    },
                    "root_buffers": {
                        name: root.get(name, 0)
                        for name in (
                            "Shared Hit Blocks",
                            "Shared Read Blocks",
                            "Shared Dirtied Blocks",
                            "Shared Written Blocks",
                            "Temp Read Blocks",
                            "Temp Written Blocks",
                        )
                    },
                    "most_expensive_operations": sorted(
                        nodes,
                        key=lambda node: node["inclusive_ms"],
                        reverse=True,
                    )[:20],
                    "materialized_cte_operations": materialized,
                    "timing_note": "Node times and buffers are inclusive; do not sum parent and child values.",
                }
                print(json.dumps(report, ensure_ascii=False, indent=2))
            finally:
                if transaction.is_active:
                    transaction.rollback()
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
