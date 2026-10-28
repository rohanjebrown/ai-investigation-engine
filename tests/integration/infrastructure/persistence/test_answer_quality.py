"""Application-to-PostgreSQL answer evaluation with deterministic fake providers."""

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.pool import Pool

from investigation_engine.application.answers import (
    AnswerStatus, GenerationRequest, GenerationResult, ResolvedCitation,
)
from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy
from investigation_engine.application.embeddings import EmbeddingConfig
from investigation_engine.application.ingestion.extracted_document import ExtractedDocument
from investigation_engine.application.investigations import (
    InvestigationDecision, InvestigationStatus, SearchEvidenceCall, TokenUsage,
)
from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
from investigation_engine.application.use_cases.answer_question import AnswerQuestion
from investigation_engine.application.use_cases.index_document_version import IndexDocumentVersion
from investigation_engine.application.investigations import InvestigateQuestion
from investigation_engine.application.use_cases.prepare_document_indexing import PrepareDocumentIndexing
from investigation_engine.application.use_cases.register_document import RegisterDocument
from investigation_engine.application.use_cases.search_evidence import SearchEvidence
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.domain.structural_span import StructuralSpan, StructureKind
from investigation_engine.evaluation.answer_quality import AnsweringRun, evaluate_answers
from investigation_engine.evaluation.retrieval import fingerprint
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import PostgreSQLChunkPlanRepository
from investigation_engine.infrastructure.persistence.postgresql_document_activation_repository import PostgreSQLDocumentActivationRepository
from investigation_engine.infrastructure.persistence.postgresql_document_repository import PostgreSQLDocumentRepository
from investigation_engine.infrastructure.persistence.postgresql_embedding_repository import PostgreSQLEmbeddingRepository
from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import PostgreSQLEvidenceRepository


class CharacterTokenizer:
    identity = TokenizerIdentity(name="answer-evaluation-char", revision="1",
        artifact_sha256="a" * 64, runtime_version="test")

    def offsets(self, text):
        return tuple((index, index + 1) for index in range(len(text)))


class FakeEmbeddings:
    configuration = EmbeddingConfig(provider="synthetic", model_id="answer-evaluation-fake", model_revision="1")

    async def embed_documents(self, texts):
        return tuple((1.0,) + (0.0,) * 767 for _ in texts)

    async def embed_query(self, text):
        return (1.0,) + (0.0,) * 767


class FakeAnswerGenerator:
    model = "deterministic-fake"

    def __init__(self, active_connections):
        self.active_connections = active_connections
        self.seen_requests = []

    async def generate(self, request: GenerationRequest):
        assert self.active_connections() == 0
        await asyncio.sleep(0)
        assert self.active_connections() == 0
        self.seen_requests.append(request)
        assert "Małgorzata Braunek" in request.evidence[0].text
        return GenerationResult(status=AnswerStatus.ANSWERED,
            answer="Małgorzata Braunek [E1]", citations=("E1",), model_version=self.model)


class FakeInvestigationModel:
    model = "deterministic-fake"

    def __init__(self, active_connections):
        self.active_connections = active_connections

    async def next_action(self, turn):
        assert self.active_connections() == 0
        await asyncio.sleep(0)
        assert self.active_connections() == 0
        if not turn.exchanges:
            return SearchEvidenceCall(query=turn.question, call_id="fake-search-1", model_version=self.model)
        item = turn.exchanges[-1].evidence[0]
        assert "Małgorzata Braunek" in item.evidence.text
        return InvestigationDecision(status=InvestigationStatus.ANSWERED,
            answer="Małgorzata Braunek [E1]", citations=(item.evidence_id,),
            usage=TokenUsage(prompt_tokens=10, candidate_tokens=3), model_version=self.model)


async def test_paired_answer_quality_runs_real_postgresql_and_existing_use_cases(
    db_engine, sessions, repository,
):
    suite_path = Path("data/samples/evaluation")
    suite = json.loads((suite_path / "suite.json").read_text(encoding="utf-8"))
    manifest = json.loads((suite_path / "corpus.json").read_text(encoding="utf-8"))
    case = next(row for row in suite["cases"] if row["id"] == "simple:8813f87c0bdd11eba7f7acde48001122")
    gold = case["required_evidence"][0]
    document = next(row for row in manifest["documents"] if row["source"] == gold["source"])
    indexed_corpus = {**manifest, "documents": [document], "record_count": 1, "unique_content_count": 1}

    spans = tuple(SourceSpan(start=row["start"], end=row["end"], locators=tuple(
        SourceLocator(kind=LocatorKind(locator["kind"]), index=locator["index"])
        for locator in row["locators"])) for row in document["source_spans"])
    structure = tuple(StructuralSpan(kind=StructureKind(row["kind"]), start=row["start"], end=row["end"])
                      for row in document["structure"])
    source = SourceReference(**{**document["source"], "kind": SourceKind(document["source"]["kind"])})
    extracted = ExtractedDocument(text=document["text"], title=document["title"], source=source,
        structure=structure, source_spans=spans)
    registered_document, version = await RegisterDocument(repository).execute(extracted, owner_id=None)

    tokenizer = CharacterTokenizer()
    chunking = ChunkingConfig(strategy=ChunkingStrategy.WHOLE_PASSAGE,
        tokenizer=tokenizer.identity, max_tokens=100_000)
    embeddings = FakeEmbeddings()
    plans = PostgreSQLChunkPlanRepository(sessions)
    indexer = IndexDocumentVersion(plans, PostgreSQLEmbeddingRepository(sessions),
        PostgreSQLDocumentActivationRepository(sessions), embeddings, max_attempts=1)
    indexing = PrepareDocumentIndexing(plans, indexer, tokenizer)
    report = await indexing.execute(registered_document, version, chunking,
        embeddings.configuration, max_new_embeddings=1)
    assert report.outcome.value in ("activated", "already_active")

    active = 0
    def checkout(*args):
        nonlocal active
        active += 1
    def checkin(*args):
        nonlocal active
        active -= 1
    def active_connections():
        return active

    event.listen(Pool, "checkout", checkout)
    event.listen(Pool, "checkin", checkin)
    try:
        evidence_repository = PostgreSQLEvidenceRepository(sessions)
        search = SearchEvidence(evidence_repository, embeddings, chunking, embeddings.configuration,
            mode="semantic", semantic_candidates=10)
        generator = FakeAnswerGenerator(active_connections)
        model = FakeInvestigationModel(active_connections)
        single = AnswerQuestion(search, generator, max_evidence=3, max_context_chars=12000)
        multi = InvestigateQuestion(search, model, max_turns=3, max_search_calls=2,
            max_results_per_search=2, max_accumulated_evidence=4, max_context_chars=12000,
            max_generation_requests=3, max_embedding_requests=2)
        config = {
            "retrieval_mode": "semantic",
            "chunking": asdict(chunking),
            "embedding": asdict(embeddings.configuration),
            "generation_model": "deterministic-fake",
        }
        runs = [
            AnsweringRun(name="single-step", mode="single_step", service=single,
                answering_budgets={"max_evidence": 3, "max_context_chars": 12000}, **config),
            AnsweringRun(name="multi-step", mode="multi_step", service=multi,
                investigation_budgets={"max_turns": 3, "max_search_calls": 2,
                    "max_generation_requests": 3, "max_embedding_requests": 2}, **config),
        ]
        result = await evaluate_answers(suite, indexed_corpus, runs,
            annotation_corpus=manifest, indexed_corpus_fingerprint=fingerprint(indexed_corpus),
            case_ids=(case["id"],))
        assert active == 0
    finally:
        event.remove(Pool, "checkout", checkout)
        event.remove(Pool, "checkin", checkin)

    assert [run["questions"][0]["outcome"] for run in result["runs"]] == ["correct_answer", "correct_answer"]
    assert all(run["questions"][0]["retrieval_coverage"]["strict_recall"] == 1 for run in result["runs"])
    assert all(run["questions"][0]["citation_coverage"]["strict_recall"] == 1 for run in result["runs"])
    assert result["paired_comparisons"][0]["paired_cases"] == 1
    assert len(generator.seen_requests) == 1
    # Gold answer is not an additional input field; generation receives only question/evidence.
    assert set(json.loads(generator.seen_requests[0].context_json())) == {"question", "evidence"}

