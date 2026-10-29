"""Register, plan, embed and activate the source-only fixed evaluation corpus."""

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from investigation_engine.application.ingestion.extracted_document import ExtractedDocument  # noqa: E402
from investigation_engine.application.ports.embedding_errors import (  # noqa: E402
    EmbeddingInputError,
    EmbeddingProviderError,
    EmbeddingRateLimitError,
    EmbeddingResponseError,
)
from investigation_engine.application.ports.provider_diagnostics import safe_diagnostic  # noqa: E402
from investigation_engine.application.use_cases.index_document_version import IndexDocumentVersion  # noqa: E402
from investigation_engine.application.use_cases.register_document import RegisterDocument  # noqa: E402
from investigation_engine.domain.provenance import SourceKind, SourceReference  # noqa: E402
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan  # noqa: E402
from investigation_engine.domain.structural_span import StructuralSpan, StructureKind  # noqa: E402
from investigation_engine.evaluation.retrieval import fingerprint, validate_suite  # noqa: E402
from investigation_engine.infrastructure.embeddings.gemini_provider import GeminiEmbeddingProvider  # noqa: E402
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import (  # noqa: E402
    PostgreSQLChunkPlanRepository,
)
from investigation_engine.infrastructure.persistence.postgresql_document_activation_repository import (  # noqa: E402
    PostgreSQLDocumentActivationRepository,
)
from investigation_engine.infrastructure.persistence.postgresql_document_repository import PostgreSQLDocumentRepository  # noqa: E402
from investigation_engine.infrastructure.persistence.postgresql_embedding_repository import (  # noqa: E402
    PostgreSQLEmbeddingRepository,
)
from investigation_engine.ingestion.corpus_processing import configurations, tokenizer_from_file  # noqa: E402

DB_NAME, DB_USER, DB_PORT = "ai_investigation_evaluation", "evaluation_engine", 55433
EXPECTED_MIGRATION = "0002_embedding_storage"
PROFILE = {
    "provider": "gemini_developer_api",
    "model_id": "gemini-embedding-2",
    "dimensions": 768,
    "document_format": "gemini_no_title_v1",
    "normalization": "none",
}


class MeasuredGemini(GeminiEmbeddingProvider):
    def __init__(self, *, api_key: str, batch_size: int, batch_token_limit: int):
        super().__init__(api_key=api_key, batch_size=batch_size, batch_token_limit=batch_token_limit)
        self.counted_tokens = 0

    async def _request(self, request, *, operation):
        response = await super()._request(request, operation=operation)
        if operation == "token counting":
            self.counted_tokens += response.total_tokens
        return response


def database_url():
    password = dotenv_values(ROOT / ".env.evaluation").get("EVALUATION_DB_PASSWORD")
    if not password:
        raise RuntimeError("Missing EVALUATION_DB_PASSWORD in .env.evaluation")
    return URL.create(
        "postgresql+psycopg",
        username=DB_USER,
        password=password,
        host="127.0.0.1",
        port=DB_PORT,
        database=DB_NAME,
        query={"connect_timeout": "5"},
    )


def source_document(row: dict) -> ExtractedDocument:
    source = row["source"]
    return ExtractedDocument(
        text=row["text"],
        title=row.get("title"),
        source=SourceReference(
            kind=SourceKind(source["kind"]),
            source_id=source["source_id"],
            revision=source.get("revision"),
            record_locator=source["record_locator"],
            display_name=source.get("display_name"),
            metadata=source.get("metadata", {}),
        ),
        structure=tuple(
            StructuralSpan(kind=StructureKind(item["kind"]), start=item["start"], end=item["end"])
            for item in row.get("structure", [])
        ),
        source_spans=tuple(
            SourceSpan(
                start=item["start"],
                end=item["end"],
                locators=tuple(
                    SourceLocator(kind=LocatorKind(locator["kind"]), index=locator["index"])
                    for locator in item["locators"]
                ),
            )
            for item in row.get("source_spans", [])
        ),
    )


def database_counts(url, chunking_id: int, embedding_id: int) -> dict:
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as c:
            tables = (
                "documents",
                "document_versions",
                "contents",
                "version_chunk_plans",
                "chunk_memberships",
                "chunk_embeddings",
            )
            counts = {name: c.scalar(text(f"SELECT count(*) FROM {name}")) for name in tables}
            counts.update(
                active_versions=c.scalar(text("SELECT count(*) FROM documents WHERE current_version_id IS NOT NULL")),
                active_searchable_occurrences=c.scalar(
                    text("""
                    SELECT count(*) FROM documents d JOIN document_versions v ON v.id=d.current_version_id
                    WHERE d.deleted_at IS NULL AND EXISTS (SELECT 1 FROM version_chunk_plans p
                      WHERE p.document_version_id=v.id AND p.chunking_configuration_id=:chunking_id)
                      AND NOT EXISTS (
                        SELECT 1 FROM chunk_memberships m LEFT JOIN chunk_embeddings e ON
                          (e.embedding_configuration_id,e.content_sha256,e.start,e.end)=
                          (:embedding_id,v.content_sha256,m.start,m.end)
                        WHERE m.document_version_id=v.id AND m.chunking_configuration_id=:chunking_id
                          AND e.embedding_configuration_id IS NULL)
                """),
                    {"chunking_id": chunking_id, "embedding_id": embedding_id},
                ),
                incomplete_planned_versions=c.scalar(
                    text("""
                    SELECT count(DISTINCT p.document_version_id) FROM version_chunk_plans p
                    JOIN chunk_memberships m ON (m.document_version_id,m.chunking_configuration_id)=
                      (p.document_version_id,p.chunking_configuration_id)
                    JOIN document_versions v ON v.id=p.document_version_id
                    LEFT JOIN chunk_embeddings e ON (e.embedding_configuration_id,e.content_sha256,e.start,e.end)=
                      (:embedding_id,v.content_sha256,m.start,m.end)
                    WHERE p.chunking_configuration_id=:chunking_id AND e.embedding_configuration_id IS NULL
                """),
                    {"chunking_id": chunking_id, "embedding_id": embedding_id},
                ),
                database_bytes=c.scalar(text("SELECT pg_database_size(current_database())")),
                migration=c.scalar(text("SELECT version_num FROM alembic_version")),
                postgres_version=c.scalar(text("SHOW server_version")),
                vector_dimensions=c.scalar(text("SELECT vector_dims(embedding) FROM chunk_embeddings LIMIT 1")),
            )
            return counts
    finally:
        engine.dispose()


def verify_registered_corpus(url, corpus: dict) -> int:
    """Compare persisted source occurrence metadata and text with the pinned manifest."""
    expected = {}
    for item in corpus["documents"]:
        source = item["source"]
        key = (
            source["kind"],
            source["source_id"],
            source.get("revision"),
            json.dumps(source["record_locator"], sort_keys=True, separators=(",", ":")),
        )
        expected[key] = item
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as connection:
            rows = (
                connection.execute(
                    text("""
                SELECT v.kind, v.source_id, v.revision, v.record_locator, v.display_name,
                       v.metadata, v.structure, v.source_spans, d.title, c.text,
                       encode(c.sha256, 'hex') AS content_sha256
                FROM document_versions v
                JOIN documents d ON d.id=v.document_id
                JOIN contents c ON c.sha256=v.content_sha256
                WHERE v.source_id IN ('xanhho/2WikiMultihopQA','controlled-evaluation')
            """)
                )
                .mappings()
                .all()
            )
        actual = {}
        for row in rows:
            key = (
                row["kind"],
                row["source_id"],
                row["revision"],
                json.dumps(row["record_locator"], sort_keys=True, separators=(",", ":")),
            )
            if key in actual:
                raise RuntimeError(f"Duplicate persisted source occurrence: {key[:3]}")
            actual[key] = row
        if actual.keys() != expected.keys():
            raise RuntimeError(
                f"Persisted source occurrence keys differ: expected {len(expected)}, found {len(actual)}"
            )
        for key, item in expected.items():
            row = actual[key]
            source = item["source"]
            comparisons = {
                "display_name": (row["display_name"], source.get("display_name")),
                "metadata": (row["metadata"], source.get("metadata", {})),
                "structure": (row["structure"], item.get("structure", [])),
                "source_spans": (row["source_spans"], item.get("source_spans", [])),
                "title": (row["title"], item.get("title")),
                "text": (row["text"], item["text"]),
                "content_sha256": (row["content_sha256"], item["content_sha256"]),
            }
            mismatched = [name for name, (found, wanted) in comparisons.items() if found != wanted]
            if mismatched:
                raise RuntimeError(f"Persisted occurrence {key[:3]} differs from corpus in {mismatched}")
        return len(actual)
    finally:
        engine.dispose()


async def run(args) -> dict:
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    validate_suite(suite, corpus)
    unique_contents = {row["content_sha256"] for row in corpus["documents"]}
    if len(corpus["documents"]) != 218 or len(unique_contents) != 197 or len(suite["cases"]) != 40:
        raise ValueError("The pinned evaluation manifest differs from its reviewed 218/197/40 counts")
    url = database_url()
    sync_engine = create_engine(url, hide_parameters=True)
    try:
        with sync_engine.connect() as c:
            if not str(c.scalar(text("SHOW server_version_num"))).startswith("180"):
                raise RuntimeError("Refusing a non-PostgreSQL-18 database")
            if (
                c.scalar(text("SELECT current_database()")) != DB_NAME
                or c.scalar(text("SELECT current_user")) != DB_USER
            ):
                raise RuntimeError("Database identity does not match the dedicated evaluation configuration")
            if c.scalar(text("SELECT version_num FROM alembic_version")) != EXPECTED_MIGRATION:
                raise RuntimeError("Run scripts/migrate_evaluation_database.py before indexing")
    finally:
        sync_engine.dispose()

    tokenizer = tokenizer_from_file(args.tokenizer)
    chunking = configurations(tokenizer)[0]
    engine = create_async_engine(url, hide_parameters=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    documents = PostgreSQLDocumentRepository(sessions)
    plans = PostgreSQLChunkPlanRepository(sessions)
    vectors = PostgreSQLEmbeddingRepository(sessions)
    activation = PostgreSQLDocumentActivationRepository(sessions)
    registered = []
    started = perf_counter()
    try:
        register = RegisterDocument(documents)
        for row in corpus["documents"]:
            doc, version = await register.execute(source_document(row), owner_id=None)
            registered.append((doc, version))
        key = os.environ.get("GEMINI_API_KEY") or dotenv_values(ROOT / ".env").get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        provider = MeasuredGemini(api_key=key, batch_size=args.batch_size, batch_token_limit=args.batch_token_limit)
        try:
            indexer = IndexDocumentVersion(plans, vectors, activation, provider, embedding_batch_size=args.batch_size)
            # Persist every source occurrence and plan before the first provider call.
            for document, version in registered:
                from investigation_engine.application.chunking import chunk_document

                await plans.save(chunk_document(version, config=chunking, tokenizer=tokenizer))
            sync_engine = create_engine(url, hide_parameters=True)
            with sync_engine.connect() as c:
                chunking_id = c.scalar(
                    text(
                        "SELECT id FROM chunking_configurations WHERE strategy='whole_passage' AND tokenizer_name=:name AND tokenizer_revision=:rev AND tokenizer_artifact_sha256=decode(:digest,'hex') AND tokenizer_runtime_version=:runtime AND max_tokens=32000 AND overlap=0 AND algorithm_version='1'"
                    ),
                    {
                        "name": chunking.tokenizer.name,
                        "rev": chunking.tokenizer.revision,
                        "digest": chunking.tokenizer.artifact_sha256,
                        "runtime": chunking.tokenizer.runtime_version,
                    },
                )
                if chunking_id is None:
                    raise RuntimeError("Persisted whole-passage configuration could not be resolved")
            report = {
                "kind": "persistent_gemini_evaluation_corpus_index",
                "timestamp_utc": datetime.now(UTC).isoformat(),
                "suite_fingerprint": fingerprint(suite),
                "corpus_fingerprint": fingerprint(corpus),
                "source_occurrences": 218,
                "unique_contents": 197,
                "questions": 40,
                "chunking": {
                    "strategy": chunking.strategy.value,
                    "max_tokens": chunking.max_tokens,
                    "tokenizer": {
                        "name": chunking.tokenizer.name,
                        "revision": chunking.tokenizer.revision,
                        "artifact_sha256": chunking.tokenizer.artifact_sha256,
                        "runtime_version": chunking.tokenizer.runtime_version,
                    },
                },
                "embedding_configuration": PROFILE,
                "batch_size": args.batch_size,
                "batch_token_limit": args.batch_token_limit,
                "max_new_embeddings": args.max_new_embeddings,
                "provider_request_counts": provider.request_counts,
            }
            before = database_counts(url, chunking_id, -1)
            # Configuration identity is written by the first save; resolve it by all immutable fields.
            with sync_engine.connect() as c:
                embedding_id = c.scalar(
                    text(
                        "SELECT id FROM embedding_configurations WHERE provider=:provider AND model_id=:model AND dimensions=768 AND document_format=:format AND normalization='none' AND model_revision IS NULL"
                    ),
                    {
                        "provider": PROFILE["provider"],
                        "model": PROFILE["model_id"],
                        "format": PROFILE["document_format"],
                    },
                )
            if embedding_id is None:
                # No vector exists yet; the indexer will establish this profile on first persist.
                embedding_id = -1
            failure = None
            try:
                reports = await indexer.execute_many(
                    tuple((doc.id, version.id) for doc, version in registered),
                    chunking,
                    provider.configuration,
                    max_new_embeddings=args.max_new_embeddings,
                )
            except (
                EmbeddingInputError,
                EmbeddingProviderError,
                EmbeddingRateLimitError,
                EmbeddingResponseError,
            ) as error:
                reports = ()
                failure = safe_diagnostic(error) or {"error_type": type(error).__name__}
            finally:
                sync_engine.dispose()
            sync_engine = create_engine(url, hide_parameters=True)
            try:
                with sync_engine.connect() as c:
                    embedding_id = c.scalar(
                        text(
                            "SELECT id FROM embedding_configurations WHERE provider=:provider AND model_id=:model AND dimensions=768 AND document_format=:format AND normalization='none' AND model_revision IS NULL"
                        ),
                        {
                            "provider": PROFILE["provider"],
                            "model": PROFILE["model_id"],
                            "format": PROFILE["document_format"],
                        },
                    )
                    registered_count = c.scalar(
                        text(
                            "SELECT count(*) FROM document_versions WHERE source_id='xanhho/2WikiMultihopQA' OR source_id='controlled-evaluation'"
                        )
                    )
            finally:
                sync_engine.dispose()
            report["outcomes"] = {
                name: sum(r.outcome.value == name for r in reports) for name in {r.outcome.value for r in reports}
            }
            report["eligible_embeddings"] = sum(r.eligible_embeddings for r in reports)
            report["indexed_memberships"] = sum(r.indexed_embeddings for r in reports)
            report["requested_embeddings"] = sum(r.requested_embeddings for r in reports)
            report["provider_request_counts"] = provider.request_counts
            report["total_counted_input_tokens"] = provider.counted_tokens
            report["failure"] = failure
            report["elapsed_seconds"] = round(perf_counter() - started, 3)
            report["database_before"] = before
            report["database_after"] = database_counts(url, chunking_id, embedding_id)
            report["database_after"]["evaluation_registered_documents"] = registered_count
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            return report
        finally:
            await provider.aclose()
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=ROOT / "data/samples/evaluation/corpus.json")
    parser.add_argument("--suite", type=Path, default=ROOT / "data/samples/evaluation/suite.json")
    parser.add_argument("--tokenizer", type=Path, default=ROOT / "artifacts/tokenizers/voyage-4/tokenizer.json")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--batch-token-limit", type=int, default=32768)
    parser.add_argument("--max-new-embeddings", type=int)
    parser.add_argument("--output", type=Path, default=ROOT / ".cache/evaluation/indexing-progress.json")
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 25 or not 1 <= args.batch_token_limit <= 32768:
        parser.error("batch limits may not exceed the experimentally supported 25 inputs / 32,768 tokens")
    if args.max_new_embeddings is not None and args.max_new_embeddings < 1:
        parser.error("--max-new-embeddings must be positive")
    report = asyncio.run(run(args), loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None)
    print(json.dumps(report, indent=2))
    if report.get("failure"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
