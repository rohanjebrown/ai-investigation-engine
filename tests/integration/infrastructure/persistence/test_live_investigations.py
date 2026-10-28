"""Opt-in, quota-counted live Gemini investigation through disposable PostgreSQL.

Run only with ``uv run --env-file .env pytest --run-external -s
tests/integration/infrastructure/persistence/test_live_investigations.py``.
The DB URL is exclusively Testcontainers' disposable database, never Settings.
"""

import asyncio
import json
import os
import socket
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import httpx
import pyarrow.parquet as pq
import pytest
import uvicorn
from dotenv import load_dotenv
from sqlalchemy import event
from sqlalchemy.pool import Pool

from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy
from investigation_engine.application.embeddings import EmbeddingConfig
from investigation_engine.application.investigations import (
    ConflictClaim, IdentifiedEvidence, InvestigateQuestion, InvestigationDecision,
    InvestigationResult, InvestigationStatus, SearchDisposition, SearchEvidenceCall,
    SearchRecord, TokenUsage,
)
from investigation_engine.application.retrieval import Evidence
from investigation_engine.application.use_cases.index_document_version import IndexDocumentVersion
from investigation_engine.application.use_cases.prepare_document_indexing import PrepareDocumentIndexing
from investigation_engine.application.use_cases.register_document import RegisterDocument
from investigation_engine.application.use_cases.search_evidence import SearchEvidence
from investigation_engine.infrastructure.api.app import create_app
from investigation_engine.infrastructure.embeddings.gemini_provider import GeminiEmbeddingProvider
from investigation_engine.infrastructure.generation.gemini_investigation_model import GeminiInvestigationModel
from investigation_engine.infrastructure.ingestion.twowiki_adapter import extract_2wiki
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import PostgreSQLChunkPlanRepository
from investigation_engine.infrastructure.persistence.postgresql_document_activation_repository import PostgreSQLDocumentActivationRepository
from investigation_engine.infrastructure.persistence.postgresql_document_repository import PostgreSQLDocumentRepository
from investigation_engine.infrastructure.persistence.postgresql_embedding_repository import PostgreSQLEmbeddingRepository
from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import PostgreSQLEvidenceRepository
from investigation_engine.infrastructure.tokenization.huggingface_tokenizer import (
    HuggingFaceTokenizer, VOYAGE_REFERENCE_NAME, VOYAGE_REFERENCE_REVISION, VOYAGE_REFERENCE_SHA256,
)
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.domain.content import Content
from investigation_engine.domain.document import Document
from investigation_engine.domain.document_version import DocumentVersion
from uuid import UUID


DATASET = Path("data/raw/2wiki/dev.parquet")
DATASET_REVISION = "612bc5039a457880d9e7d84c3b0a4cf154b70e4f"
DATASET_SHA256 = "c0d8b60b9026b728fb07ad74c5252a0f188f6942e8ba5c02df4dfa369502ea8d"
# This is the model used in the preceding successful live RAG verification.
# GEMINI_GENERATION_MODEL remains unset/unchanged; this is test-only composition.
GENERATION_MODEL = "gemini-3.6-flash"
_last_generation_at = 0.0


class CountingEmbeddingProvider:
    def __init__(self, provider):
        self.provider = provider
        self.embed_content_requests = 0
        self.query_count = 0

    @property
    def configuration(self):
        return self.provider.configuration

    async def embed_documents(self, texts):
        self.embed_content_requests += len(texts)
        return await self.provider.embed_documents(texts)

    async def embed_query(self, query):
        self.query_count += 1
        self.embed_content_requests += 1
        return await self.provider.embed_query(query)

    async def aclose(self):
        await self.provider.aclose()


class CountingModel:
    def __init__(self, provider, pool_counter):
        self.provider = provider
        self.pool_counter = pool_counter
        self.requests = 0
        self.turns = []

    async def next_action(self, turn):
        assert self.pool_counter.active == 0, "DB connection remained checked out during model generation"
        global _last_generation_at
        now = time.monotonic()
        # User-confirmed Flash limit is 5 RPM; pace every live generation request.
        if _last_generation_at:
            wait = 12.5 - (now - _last_generation_at)
            if wait > 0:
                await asyncio.sleep(wait)
        _last_generation_at = time.monotonic()
        self.requests += 1
        self.turns.append({
            "prior_searches": [{"query": exchange.query,
                "evidence": [{"id": item.evidence_id, "excerpt": item.evidence.text}
                             for item in exchange.evidence]} for exchange in turn.exchanges],
        })
        action = await self.provider.next_action(turn)
        self.turns[-1]["action"] = ({"tool": "search_evidence", "query": action.query}
                                    if isinstance(action, SearchEvidenceCall)
                                    else {"final_status": action.status.value})
        return action

    async def aclose(self):
        await self.provider.aclose()


@pytest.fixture(scope="module")
def source_rows():
    if not DATASET.is_file():
        pytest.skip("Pinned local 2Wiki dev.parquet is unavailable; no download is automatic")
    if sha256(DATASET.read_bytes()).hexdigest() != DATASET_SHA256:
        pytest.fail("Local 2Wiki file does not match the pinned dataset audit")
    return {row["_id"]: row for row in pq.read_table(DATASET).to_pylist()}


class PoolCounter:
    active = 0

    def checkout(self, *args): self.active += 1
    def checkin(self, *args): self.active -= 1


async def _run_case(*, db_engine, sessions, repository, source_rows, case_id, positions, distractors,
                    question, answer_fragment, minimum_searches, minimum_new_entity, browser_replay=False):
    load_dotenv(Path.cwd() / ".env", override=False, interpolate=False)
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        pytest.skip("GEMINI_API_KEY is unavailable; no live calls were attempted")
    if not DATASET.is_file():
        pytest.skip("Pinned local 2Wiki file is unavailable")
    assert db_engine.url.port not in (5432, 15432), "Refusing local or Cloud SQL application ports"
    if not db_engine.dialect.server_version_info or db_engine.dialect.server_version_info[0] != 18:
        pytest.fail("Live verification requires the already-pinned disposable PostgreSQL 18 test fixture")

    raw = source_rows[case_id]
    parsed = {"_id": raw["_id"], "context": json.loads(raw["context"])}
    extracted = extract_2wiki(parsed, split="dev", revision=DATASET_REVISION)
    selected = tuple(dict.fromkeys((*positions, *distractors)))
    selected_docs = [extracted[index] for index in selected]
    assert all(doc.title and doc.text and doc.source.record_locator["context_position"] == index
               for doc, index in zip(selected_docs, selected, strict=True))
    tokenizer = HuggingFaceTokenizer(Path("artifacts/tokenizers/voyage-4/tokenizer.json"),
        name=VOYAGE_REFERENCE_NAME, revision=VOYAGE_REFERENCE_REVISION,
        expected_sha256=VOYAGE_REFERENCE_SHA256)
    chunking = ChunkingConfig(strategy=ChunkingStrategy.WHOLE_PASSAGE,
        tokenizer=tokenizer.identity, max_tokens=2048)
    embedding = CountingEmbeddingProvider(GeminiEmbeddingProvider(api_key=api_key))
    model_provider = GeminiInvestigationModel(api_key=api_key, model=GENERATION_MODEL,
        timeout_seconds=45, max_output_tokens=1024)
    plans = PostgreSQLChunkPlanRepository(sessions)
    embedding_rows = PostgreSQLEmbeddingRepository(sessions)
    activation = PostgreSQLDocumentActivationRepository(sessions)
    indexer = IndexDocumentVersion(plans, embedding_rows, activation, embedding,
        max_attempts=1, request_timeout_seconds=60)
    indexing = PrepareDocumentIndexing(plans, indexer, tokenizer)
    register = RegisterDocument(repository)
    versions = []
    for doc in selected_docs:
        document, version = await register.execute(doc, owner_id=None)
        report = await indexing.execute(document, version, chunking, embedding.configuration,
            max_new_embeddings=100)
        assert report.outcome.value in ("activated", "already_active")
        versions.append((document, version))

    pool = PoolCounter()
    event.listen(Pool, "checkout", pool.checkout)
    event.listen(Pool, "checkin", pool.checkin)
    try:
        evidence_repo = PostgreSQLEvidenceRepository(sessions)
        search = SearchEvidence(evidence_repo, embedding, chunking, embedding.configuration,
            mode="semantic", semantic_candidates=10)
        model = CountingModel(model_provider, pool)
        usecase = InvestigateQuestion(search, model, max_turns=6, max_search_calls=5,
            max_results_per_search=1, max_accumulated_evidence=8, max_context_chars=24000,
            max_generation_requests=6, max_embedding_requests=5)
        config = SimpleNamespace(investigation_enabled=True, investigation_timeout_seconds=180,
            admin_api_token=None, admin_principal_id="administrator", admin_require_https=True)
        services = SimpleNamespace(settings=config, questions=None, investigations=usecase)

        @asynccontextmanager
        async def factory():
            yield services

        app = create_app(service_factory=factory)
        start = time.perf_counter()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                    base_url="http://test", timeout=190) as client:
                response = await client.post("/investigations", json={"question": question})
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    finally:
        event.remove(Pool, "checkout", pool.checkout)
        event.remove(Pool, "checkin", pool.checkin)
        await model_provider.aclose()
        await embedding.aclose()

    assert response.status_code == 200, f"Investigation endpoint returned {response.status_code}: {response.json()}"
    result = response.json()
    by_id = {item["evidence_id"]: item["evidence"] for item in result["evidence"]}
    summary = {
        "example_id": case_id, "dataset": "xanhho/2WikiMultihopQA", "split": "dev",
        "revision": DATASET_REVISION, "question": question, "status": result["status"],
        "answer": result["answer"], "generation_model": result["model_version"] or GENERATION_MODEL,
        "embedding_model": embedding.configuration.model_id,
        "retrieval_mode": "semantic", "indexed_passages": len(selected_docs),
        "selected_context_positions": list(selected),
        "search_sequence": [{"sequence": item["sequence"], "query": item["query"],
            "evidence_ids": item["evidence_ids"], "result_count": item["result_count"]}
            for item in result["searches"]],
        "citations": [{"evidence_id": key, "version_id": by_id[key]["document_version_id"],
            "source_locator": by_id[key]["source"]["record_locator"], "excerpt": by_id[key]["text"]}
            for key in result["citations"]],
        "generation_requests": result["generation_requests"],
        "generation_attempts_observed": model.requests,
        "embedding_query_count": embedding.query_count,
        "embed_content_requests_including_indexing": embedding.embed_content_requests,
        "count_tokens_requests_including_indexing": embedding.embed_content_requests,
        "indexing_embedding_requests": embedding.embed_content_requests - embedding.query_count,
        "usage": result["usage"], "elapsed_ms": elapsed_ms,
        "model_turns": model.turns,
    }
    print("LIVE_INVESTIGATION_REPORT=" + json.dumps(summary, ensure_ascii=True, separators=(",", ":")))
    assert result["status"] == "answered", json.dumps(summary, ensure_ascii=True)
    assert answer_fragment.casefold() in result["answer"].casefold(), result["answer"]
    assert len(result["searches"]) >= minimum_searches, result["searches"]
    assert len(result["searches"]) <= 5 and result["generation_requests"] <= 6
    assert result["embedding_requests"] == len(result["searches"])
    assert model.requests == result["generation_requests"]
    assert embedding.embed_content_requests <= 36
    assert model.requests <= 12
    assert all(search["succeeded"] and search["evidence_ids"] for search in result["searches"])
    assert set(result["citations"]).issubset(by_id)
    assert result["citations"]
    # Every returned source is a registered, selected passage; no generated provenance.
    expected_versions = {str(version.id) for _, version in versions}
    assert all(by_id[item]["document_version_id"] in expected_versions for item in result["citations"])
    assert any(minimum_new_entity.casefold() in query["query"].casefold()
               for query in result["searches"][1:]), result["searches"]
    if browser_replay:
        await _browser_replay(question, result)
    return result


def _result_from_http(body):
    evidence = []
    for item in body["evidence"]:
        raw = item["evidence"]
        source_data = raw["source"]
        source = SourceReference(kind=SourceKind(source_data["kind"]), source_id=source_data["source_id"],
            revision=source_data["revision"], display_name=source_data["display_name"],
            record_locator=source_data["record_locator"], metadata=source_data["metadata"])
        spans = tuple(SourceSpan(start=span["start"], end=span["end"], locators=tuple(
            SourceLocator(kind=LocatorKind(locator["kind"]), index=locator["index"])
            for locator in span["locators"])) for span in raw["source_spans"])
        hit = Evidence(document_id=UUID(raw["document_id"]), document_version_id=UUID(raw["document_version_id"]),
            content_sha256=raw["content_sha256"], start=raw["start"], end=raw["end"], text=raw["text"],
            source=source, source_spans=spans, score=raw["score"], rank=raw["rank"], title=raw["title"],
            score_kind=raw["score_kind"], semantic_score=raw["semantic_score"],
            keyword_score=raw["keyword_score"], semantic_rank=raw["semantic_rank"], keyword_rank=raw["keyword_rank"])
        evidence.append(IdentifiedEvidence(evidence_id=item["evidence_id"], evidence=hit))
    searches = tuple(SearchRecord(sequence=row["sequence"], tool_name=row["tool_name"], query=row["query"],
        evidence_ids=tuple(row["evidence_ids"]), result_count=row["result_count"], duration_ms=row["duration_ms"],
        succeeded=row["succeeded"], disposition=SearchDisposition(row["disposition"])) for row in body["searches"])
    conflicts = tuple(ConflictClaim(claim=row["claim"], citations=tuple(row["citations"])) for row in body["conflicts"])
    usage = body["usage"]
    return InvestigationResult(status=InvestigationStatus(body["status"]), answer=body["answer"],
        citations=tuple(body["citations"]), conflicts=conflicts, evidence=tuple(evidence), searches=searches,
        generation_requests=body["generation_requests"], embedding_requests=body["embedding_requests"],
        message=body["message"], model_version=body["model_version"],
        usage=TokenUsage(prompt_tokens=usage["prompt_tokens"], candidate_tokens=usage["candidate_tokens"],
            thinking_tokens=usage["thinking_tokens"]))


async def _browser_replay(question, live_result):
    """Exercise Edge against the real app/API using Test A's response, no new AI calls."""
    edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    if not edge.is_file():
        pytest.skip("No local Edge browser is available for the real browser verification")
    class Replay:
        async def execute(self, _question, *, owner_id):
            assert owner_id is None
            assert _question == question
            return live_result if isinstance(live_result, InvestigationResult) else _result_from_http(live_result)
    services = SimpleNamespace(settings=SimpleNamespace(investigation_enabled=True,
        investigation_timeout_seconds=120, admin_api_token=None), investigations=Replay(), questions=None)

    @asynccontextmanager
    async def factory(): yield services

    app = create_app(service_factory=factory)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", access_log=False))
    server_socket = socket.socket()
    server_socket.bind(("127.0.0.1", 0))
    server_socket.listen(128)
    port = server_socket.getsockname()[1]
    cdp_socket = socket.socket()
    cdp_socket.bind(("127.0.0.1", 0))
    cdp_port = cdp_socket.getsockname()[1]
    cdp_socket.close()
    with tempfile.TemporaryDirectory(prefix="investigation-edge-") as profile:
        browser = subprocess.Popen([str(edge), "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", "--disable-background-networking",
            f"--remote-debugging-port={cdp_port}", "--remote-allow-origins=*",
            f"--user-data-dir={profile}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        server_task = asyncio.create_task(server.serve(sockets=[server_socket]))
        try:
            async with httpx.AsyncClient() as client:
                for _ in range(100):
                    if server.started:
                        break
                    await asyncio.sleep(0.05)
                assert server.started, "local FastAPI browser-test server failed to start"
                base_url = f"http://127.0.0.1:{port}"
                result = await client.get(f"{base_url}/health")
                assert result.status_code == 200
            process = await asyncio.to_thread(subprocess.run,
                ["node", str(Path("tests/frontend/browser_investigation.cjs").resolve()),
                 str(cdp_port), f"http://127.0.0.1:{port}", question],
                capture_output=True, timeout=45, check=False)
            if process.returncode:
                pytest.fail("Headless Edge browser check failed: " + process.stderr.decode("utf-8", errors="replace"))
            replay_output = process.stdout.decode("utf-8", errors="replace").strip()
            print("LIVE_BROWSER_REPLAY=" + replay_output.encode("ascii", "backslashreplace").decode("ascii"))
        finally:
            server.should_exit = True
            await server_task
            browser.terminate()
            try:
                browser.wait(timeout=5)
            except subprocess.TimeoutExpired:
                browser.kill()
                browser.wait(timeout=5)


@pytest.mark.external
async def test_live_two_hop_polish_russian_war_mother(
    db_engine, sessions, repository, source_rows,
):
    result = await _run_case(db_engine=db_engine, sessions=sessions, repository=repository,
        source_rows=source_rows, case_id="8813f87c0bdd11eba7f7acde48001122",
        positions=(4, 7), distractors=(0, 1, 3),
        question="Who is the mother of the director of film Polish-Russian War (Film)?",
        answer_fragment="Małgorzata Braunek", minimum_searches=2, minimum_new_entity="Xawery",
        browser_replay=True)
    assert result["status"] == "answered"


@pytest.mark.external
async def test_live_branching_three_plus_hop_director_birth_comparison(
    db_engine, sessions, repository, source_rows,
):
    result = await _run_case(db_engine=db_engine, sessions=sessions, repository=repository,
        source_rows=source_rows, case_id="298f23b8088a11ebbd6eac1f6bf848b6",
        positions=(1, 4, 7, 9), distractors=(0, 3, 8),
        question="Which film has the director who was born later, El Extraño Viaje or Love In Pawn?",
        answer_fragment="El Extraño Viaje", minimum_searches=3,
        minimum_new_entity="Fernando")
    assert result["status"] == "answered"


@pytest.mark.external
async def test_real_edge_browser_renders_two_hop_investigation_replay(source_rows):
    """Browser QA replays actual 2Wiki source text without spending more Gemini quota."""
    raw = source_rows["8813f87c0bdd11eba7f7acde48001122"]
    extracted = extract_2wiki({"_id": raw["_id"], "context": json.loads(raw["context"])},
        split="dev", revision=DATASET_REVISION)
    evidence = []
    versions = []
    for evidence_id, position in zip(("E1", "E2"), (4, 7), strict=True):
        source_doc = extracted[position]
        document = Document(owner_id=None, title=source_doc.title)
        version = DocumentVersion(document_id=document.id, version_number=1, content=Content(source_doc.text),
            source=source_doc.source, structure=source_doc.structure, source_spans=source_doc.source_spans)
        hit = Evidence(document_id=document.id, document_version_id=version.id,
            content_sha256=version.content.sha256, start=0, end=len(version.content.text),
            text=version.content.text, source=version.source, source_spans=version.source_spans,
            rank=1, score=0.9, title=source_doc.title)
        evidence.append(IdentifiedEvidence(evidence_id=evidence_id, evidence=hit))
        versions.append(version)
    question = "Who is the mother of the director of film Polish-Russian War (Film)?"
    result = InvestigationResult(status=InvestigationStatus.ANSWERED,
        answer="The director is Xawery Żuławski, whose mother is Małgorzata Braunek. [E1] [E2]",
        citations=("E1", "E2"), conflicts=(), evidence=tuple(evidence),
        searches=(SearchRecord(sequence=1, tool_name="search_evidence", query="Polish-Russian War director",
            evidence_ids=("E1",), result_count=1, duration_ms=4.2, succeeded=True,
            disposition=SearchDisposition.SUCCEEDED),
            SearchRecord(sequence=2, tool_name="search_evidence", query="Xawery Zulawski mother",
            evidence_ids=("E2",), result_count=1, duration_ms=3.5, succeeded=True,
            disposition=SearchDisposition.SUCCEEDED)), generation_requests=3, embedding_requests=2,
        message=None, model_version=GENERATION_MODEL, usage=TokenUsage())
    await _browser_replay(question, result)
