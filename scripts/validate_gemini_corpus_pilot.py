"""Explicit, bounded Gemini Embedding 2 retrieval pilot on disposable PostgreSQL."""

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer
from validate_corpus_scale import IMAGE, local_url, peak_memory_bytes

from investigation_engine.application.chunking import chunk_document
from investigation_engine.application.ports.provider_diagnostics import safe_diagnostic
from investigation_engine.application.use_cases.index_document_version import IndexDocumentVersion
from investigation_engine.application.use_cases.prepare_document_indexing import PrepareDocumentIndexing
from investigation_engine.application.use_cases.register_document import RegisterDocument
from investigation_engine.application.use_cases.search_evidence import SearchEvidence
from investigation_engine.evaluation.cached_queries import CachedQueryEmbeddings
from investigation_engine.evaluation.retrieval import measure, validate_suite
from investigation_engine.infrastructure.embeddings.gemini_provider import GeminiEmbeddingProvider
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import (
    PostgreSQLChunkPlanRepository,
)
from investigation_engine.infrastructure.persistence.postgresql_document_activation_repository import (
    PostgreSQLDocumentActivationRepository,
)
from investigation_engine.infrastructure.persistence.postgresql_document_repository import PostgreSQLDocumentRepository
from investigation_engine.infrastructure.persistence.postgresql_embedding_repository import (
    PostgreSQLEmbeddingRepository,
)
from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import PostgreSQLEvidenceRepository
from investigation_engine.ingestion.corpus_processing import (
    atomic_json,
    configurations,
    fingerprint,
    source_documents,
    tokenizer_from_file,
    verify_input,
    version_for,
)

MAX_PILOT_EMBEDDING_INPUTS = 18
MIN_GEMINI_REQUEST_INTERVAL_SECONDS = 1.3
MAX_FORMATTED_TOKEN_COUNT = 10_000
POSTGRESQL_DEFAULT_PORT = 5432
SEARCH_CANDIDATE_LIMIT = 20
DATABASE_RESTART_ATTEMPTS = 30
DATABASE_RESTART_RETRY_SECONDS = 0.5
OWNER = "corpus-pilot-owner"


class BoundedGemini(GeminiEmbeddingProvider):
    def __init__(self, key, output, max_inputs):
        super().__init__(api_key=key)
        self.inputs = 0
        self.elapsed = 0.0
        self.last_start = None
        self.tokens = 0
        self.output = output
        self.max_inputs = max_inputs

    async def reserve(self, amount):
        if self.inputs + amount > self.max_inputs:
            raise RuntimeError("Pilot embedding input budget exhausted")
        self.inputs += amount
        now = asyncio.get_running_loop().time()
        if self.last_start is not None:
            await asyncio.sleep(max(0, MIN_GEMINI_REQUEST_INTERVAL_SECONDS - (now - self.last_start)))
        self.last_start = asyncio.get_running_loop().time()

    async def embed_documents(self, texts):
        await self.reserve(len(texts))
        begin = perf_counter()
        try:
            return await super().embed_documents(texts)
        finally:
            self.elapsed += perf_counter() - begin

    async def embed_query(self, question):
        await self.reserve(1)
        begin = perf_counter()
        try:
            return await super().embed_query(question)
        finally:
            self.elapsed += perf_counter() - begin

    async def _request(self, request, *, operation):
        try:
            response = await super()._request(request, operation=operation)
            if operation == "token counting":
                self.tokens += getattr(response, "total_tokens", 0) or 0
                if self.tokens > MAX_FORMATTED_TOKEN_COUNT:
                    raise RuntimeError("Pilot cumulative formatted-token budget exhausted")
            return response
        finally:
            sequence = sum(self.request_counts.values())
            atomic_json(
                self.output / "request-ledger" / f"{sequence:08d}.json",
                {
                    "model": "gemini-embedding-2",
                    "inputs": self.inputs,
                    "requests": self.request_counts,
                    "formatted_tokens_counted": self.tokens,
                    "generation_requests": 0,
                    "retries": 0,
                },
            )


def evidence_json(hit):
    return {
        "document_id": str(hit.document_id),
        "version_id": str(hit.document_version_id),
        "content_sha256": hit.content_sha256,
        "start": hit.start,
        "end": hit.end,
        "text": hit.text,
        "source": asdict(hit.source),
        "source_spans": [{**asdict(s), "locators": [asdict(loc) for loc in s.locators]} for s in hit.source_spans],
        "score": hit.score,
        "score_kind": hit.score_kind,
    }


async def run(args):
    verify_input(args.dataset)
    tokenizer = tokenizer_from_file(args.tokenizer)
    configs = configurations(tokenizer)[:2]
    base = Path("data/samples/evaluation")
    suite = json.loads((base / "suite.json").read_text(encoding="utf-8"))
    corpus = json.loads((base / "corpus.json").read_text(encoding="utf-8"))
    validate_suite(suite, corpus)
    example = "8813f87c0bdd11eba7f7acde48001122"
    cases = [case for case in suite["cases"] if case["id"] in (f"simple:{example}", f"2wiki:{example}")]
    # After the first attempt's local ledger failure, avoid re-embedding its
    # four distractors. Use two gold-bearing passages and two unused distractors.
    positions = (4, 7, 8, 9)
    selected = []
    for doc in source_documents(args.dataset):
        locator = doc.source.record_locator
        if locator["example_id"] == example and locator["context_position"] in positions:
            selected.append(doc)
        if len(selected) == len(positions):
            break
    assert len(selected) == 4 and len(cases) == 2
    selected_texts = {doc.text for doc in selected}
    private_duplicate = inactive = None
    for doc in source_documents(args.dataset):
        if doc.source.record_locator["example_id"] == example:
            continue
        if doc.text in selected_texts and private_duplicate is None:
            private_duplicate = doc
        elif doc.text not in selected_texts and inactive is None:
            inactive = doc
        if private_duplicate is not None and inactive is not None:
            break
    if private_duplicate is None:
        raise ValueError("No real independent duplicate occurrence found for this pilot")
    unique_ranges = {
        chunk.identity
        for doc in selected
        for config in configs
        for chunk in chunk_document(version_for(doc), config=config, tokenizer=tokenizer).chunks
    }
    if len(unique_ranges) + len(cases) > args.max_inputs:
        raise ValueError("Selected matched chunking pilot exceeds embedding budget before any API call")
    if not args.run_live:
        print(
            json.dumps(
                {
                    "preflight": "passed",
                    "source_occurrences": 6,
                    "maximum_embedding_inputs": len(unique_ranges) + len(cases),
                    "cap": args.max_inputs,
                },
                ensure_ascii=True,
            )
        )
        return 0
    if (
        (args.output / "report.json").exists()
        or (args.output / "request-ledger").exists()
        or (args.output / "request-ledger.json").exists()
    ):
        raise ValueError("Use a fresh output directory so prior API accounting is preserved")
    # Only the Gemini credential is consumed; application DB settings are never used.
    load_dotenv(override=False, interpolate=False)
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise ValueError("GEMINI_API_KEY is required for the explicitly opted-in pilot")
    os.environ["CORPUS_DB_PASSWORD"] = "disposable-corpus-validation-password"
    container = PostgresContainer(
        IMAGE,
        username="corpus_validation",
        password=os.environ["CORPUS_DB_PASSWORD"],
        dbname="corpus_validation",
        driver="psycopg",
    )
    container.ports[str(POSTGRESQL_DEFAULT_PORT)] = ("127.0.0.1", None)
    report = {
        "kind": "bounded-real-Gemini-retrieval-pilot",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "source": "pinned real 2Wiki contexts only",
        "generation_model_if_needed": "gemini-3.6-flash",
        "generation_requests": 0,
        "input_cap": args.max_inputs,
        "query_count": len(cases),
        "source_occurrences_registered": 6,
        "shared_active_occurrences": 4,
        "private_active_duplicate_occurrences": 1,
        "inactive_occurrences": 1,
        "budget": "User-reported billing-disabled Free tier: 100 RPM/30k TPM/1k RPD. No provider retries/model substitution. The explicit input cap applies to this invocation; other project usage must be deducted separately. Counted-token ceiling is 10k.",
        "selection_limitation": "Two gold-containing passages plus two unused distractors: a persistence/citation pilot, not an unbiased ranking benchmark.",
        "results": [],
    }
    started = perf_counter()
    provider = BoundedGemini(key, args.output, args.max_inputs)
    engine = sync = None
    try:
        with container:
            url = local_url(int(container.get_exposed_port(POSTGRESQL_DEFAULT_PORT)))
            sync = create_engine(url, hide_parameters=True)
            with sync.begin() as connection:
                connection.execute(text("CREATE EXTENSION vector"))
                cfg = Config("alembic.ini")
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, "head")
            engine = create_async_engine(url, hide_parameters=True)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            register = RegisterDocument(PostgreSQLDocumentRepository(sessions))
            registrations = [await register.execute(doc, owner_id=None) for doc in selected]
            registrations.append(await register.execute(private_duplicate, owner_id=OWNER))
            inactive_document, inactive_version = await register.execute(inactive, owner_id=None)
            plans = PostgreSQLChunkPlanRepository(sessions)
            embeddings = PostgreSQLEmbeddingRepository(sessions)
            indexer = IndexDocumentVersion(
                plans, embeddings, PostgreSQLDocumentActivationRepository(sessions), provider, max_attempts=1
            )
            prepare = PrepareDocumentIndexing(plans, indexer, tokenizer)
            for config in configs:
                await plans.save(chunk_document(inactive_version, config=config, tokenizer=tokenizer))
                for document, version in registrations:
                    result = await prepare.execute(
                        document, version, config, provider.configuration, max_new_embeddings=8
                    )
                    assert result.outcome.value in ("activated", "already_active")
            vectors = {case["question"]: await provider.embed_query(case["question"]) for case in cases}
            cache = CachedQueryEmbeddings(provider.configuration, vectors)
            atomic_json(
                args.output / "query-vectors.json",
                {"configuration": asdict(provider.configuration), "vectors": vectors},
            )
            report["query_cache_sha256"] = fingerprint(
                {"configuration": asdict(provider.configuration), "vectors": vectors}
            )

            async def searches():
                repository = PostgreSQLEvidenceRepository(sessions)
                results = []
                original = {document.id: version for document, version in registrations}
                for config in configs:
                    for mode in ("semantic", "hybrid"):
                        search = SearchEvidence(
                            repository,
                            cache,
                            config,
                            provider.configuration,
                            mode=mode,
                            hybrid_repository=repository,
                            semantic_candidates=SEARCH_CANDIDATE_LIMIT,
                            keyword_candidates=SEARCH_CANDIDATE_LIMIT,
                        )
                        for case in cases:
                            start = perf_counter()
                            hits = await search.execute(case["question"], SEARCH_CANDIDATE_LIMIT, owner_id=None)
                            duration = perf_counter() - start
                            assert all(
                                hit.document_id in original and hit.document_id != inactive_document.id for hit in hits
                            )
                            assert registrations[-1][0].id not in {hit.document_id for hit in hits}
                            for hit in hits:
                                version = original[hit.document_id]
                                assert hit.text == version.content.text[hit.start : hit.end]
                                assert hit.source == version.source
                            results.append(
                                {
                                    "strategy": config.strategy.value,
                                    "mode": mode,
                                    "question_id": case["id"],
                                    "metrics": measure(
                                        hits,
                                        case["required_evidence"],
                                        ks=(5, 10, SEARCH_CANDIDATE_LIMIT),
                                    ),
                                    "latency_seconds": duration,
                                    "evidence": [evidence_json(hit) for hit in hits],
                                }
                            )
                    keyword = await repository.search_keywords(
                        "film", config, provider.configuration, owner_id=None, limit=100
                    )
                    assert keyword
                    public = await repository.search(
                        vectors[cases[0]["question"]], config, provider.configuration, owner_id=None, limit=100
                    )
                    private = await repository.search(
                        vectors[cases[0]["question"]], config, provider.configuration, owner_id=OWNER, limit=100
                    )
                    assert len({hit.document_id for hit in public}) == 4
                    assert len({hit.document_id for hit in private}) == 5
                    assert registrations[-1][0].id in {hit.document_id for hit in private}
                return results

            report["results"] = await searches()
            with sync.connect() as connection:
                report["persisted_embeddings"] = connection.scalar(text("SELECT count(*) FROM chunk_embeddings"))
                report["persisted_memberships"] = connection.scalar(text("SELECT count(*) FROM chunk_memberships"))
                report["activated_versions"] = connection.scalar(
                    text("SELECT count(*) FROM documents WHERE current_version_id IS NOT NULL")
                )
                report["database_bytes"] = connection.scalar(text("SELECT pg_database_size(current_database())"))
                report["stored_vector_dimensions"] = list(
                    connection.scalars(text("SELECT DISTINCT vector_dims(embedding) FROM chunk_embeddings"))
                )
            await engine.dispose()
            sync.dispose()
            container.get_wrapped_container().restart()
            url = local_url(int(container.get_exposed_port(POSTGRESQL_DEFAULT_PORT)))
            engine = create_async_engine(url, hide_parameters=True)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            for attempt in range(DATABASE_RESTART_ATTEMPTS):
                try:
                    async with engine.connect() as connection:
                        await connection.execute(text("SELECT 1"))
                    break
                except Exception:
                    await asyncio.sleep(DATABASE_RESTART_RETRY_SECONDS)
            repeated = await searches()

            def signature(rows):
                return fingerprint([{**row, "latency_seconds": None} for row in rows])

            assert signature(repeated) == signature(report["results"])
            report["database_restart_preserved_results"] = True
            report["shared_content_reuse_and_owner_filtering"] = True
            report["inactive_version_excluded"] = True
    except Exception as error:
        report["failure"] = {"type": type(error).__name__, "provider_diagnostic": safe_diagnostic(error)}
    finally:
        if engine:
            await engine.dispose()
        if sync:
            sync.dispose()
        await provider.aclose()
        report["api_requests"] = provider.request_counts
        report["embedding_inputs"] = provider.inputs
        report["counted_formatted_tokens"] = provider.tokens
        report["provider_wait_seconds"] = provider.elapsed
        report["duration_seconds"] = perf_counter() - started
        report["peak_process_memory_bytes"] = peak_memory_bytes()
        report["embedding_configuration"] = asdict(provider.configuration)
        report["chunking_configurations"] = [asdict(config) for config in configs]
        atomic_json(args.output / "report.json", report)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "failure": report.get("failure"),
                "requests": report["api_requests"],
                "embeddings": report.get("persisted_embeddings"),
                "generation_requests": 0,
            },
            ensure_ascii=True,
        )
    )
    return 2 if report.get("failure") else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-live", action="store_true")
    parser.add_argument("--max-inputs", type=int, choices=range(1, MAX_PILOT_EMBEDDING_INPUTS + 1), default=8)
    parser.add_argument("--dataset", type=Path, default=Path("data/raw/2wiki/dev.parquet"))
    parser.add_argument("--tokenizer", type=Path, default=Path("artifacts/tokenizers/voyage-4/tokenizer.json"))
    parser.add_argument("--output", type=Path, default=Path(".cache/corpus-scale/gemini-pilot"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        raise SystemExit(runner.run(run(args)))
