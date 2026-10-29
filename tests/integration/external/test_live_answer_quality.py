"""Opt-in, tightly bounded real-Gemini answer-quality sample.

Run only with ``pytest --run-external``. PostgreSQL fixtures always create a
disposable container; this test never reads or connects to the application DB.
"""

import asyncio
import json
from contextlib import AsyncExitStack
from dataclasses import asdict
from pathlib import Path

import pytest

from investigation_engine.application.answers import GenerationRequest
from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy, chunk_document
from investigation_engine.application.ingestion.extracted_document import ExtractedDocument
from investigation_engine.application.investigations import InvestigateQuestion
from investigation_engine.application.use_cases.answer_question import AnswerQuestion
from investigation_engine.application.use_cases.index_document_version import IndexDocumentVersion
from investigation_engine.application.use_cases.prepare_document_indexing import PrepareDocumentIndexing
from investigation_engine.application.use_cases.register_document import RegisterDocument
from investigation_engine.application.use_cases.search_evidence import SearchEvidence
from investigation_engine.domain.content import Content
from investigation_engine.domain.document import Document
from investigation_engine.domain.document_version import DocumentVersion
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.domain.structural_span import StructuralSpan, StructureKind
from investigation_engine.evaluation.answer_quality import (
    AnsweringRun,
    evaluate_answers,
    export_answer_quality,
)
from investigation_engine.evaluation.retrieval import fingerprint
from investigation_engine.infrastructure.embeddings.gemini_provider import GeminiEmbeddingProvider
from investigation_engine.infrastructure.generation.gemini_answer_generator import GeminiAnswerGenerator
from investigation_engine.infrastructure.generation.gemini_investigation_model import GeminiInvestigationModel
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
from investigation_engine.infrastructure.tokenization.huggingface_tokenizer import (
    VOYAGE_REFERENCE_NAME,
    VOYAGE_REFERENCE_REVISION,
    VOYAGE_REFERENCE_SHA256,
    HuggingFaceTokenizer,
)

GENERATION_MODEL = "gemini-3.6-flash"
MAX_GENERATION_REQUESTS = 7
MAX_EMBEDDING_TEXTS = 22
GENERATION_INTERVAL_SECONDS = 12.5
SAMPLE_IDS = (
    "2wiki:8813f87c0bdd11eba7f7acde48001122",
    "contradictory:0",
)


class BudgetedGeminiEmbeddings:
    def __init__(self, provider, state):
        self.provider, self.state = provider, state
        self.configuration = provider.configuration
        self.inputs = 0

    def _reserve(self, amount):
        if self.state["failed"] or self.inputs + amount > MAX_EMBEDDING_TEXTS:
            self.state["failed"] = True
            raise RuntimeError("Live evaluation embedding input budget exhausted")
        self.inputs += amount

    async def embed_documents(self, texts):
        self._reserve(len(texts))
        try:
            return await self.provider.embed_documents(texts)
        except Exception:
            self.state["failed"] = True
            raise

    async def embed_query(self, text):
        self._reserve(1)
        try:
            return await self.provider.embed_query(text)
        except Exception:
            self.state["failed"] = True
            raise


class BudgetedAnswerGenerator:
    def __init__(self, provider, state):
        self.provider, self.state = provider, state
        self.model = provider.model

    async def generate(self, request: GenerationRequest):
        await self.state.reserve_generation("single_step", request.question)
        try:
            return await self.provider.generate(request)
        except Exception:
            self.state["failed"] = True
            raise


class BudgetedInvestigationModel:
    def __init__(self, provider, state):
        self.provider, self.state = provider, state
        self.model = provider.model

    async def next_action(self, turn):
        await self.state.reserve_generation("multi_step", turn.question)
        try:
            return await self.provider.next_action(turn)
        except Exception:
            self.state["failed"] = True
            raise


class LiveRequestState(dict):
    def __init__(self, multi_question_caps):
        super().__init__(failed=False)
        self["generation_requests"] = 0
        self["last_generation_at"] = None
        self["generation_requests_by_case"] = {}
        self["multi_question_caps"] = multi_question_caps

    async def reserve_generation(self, mode, question):
        if self["failed"] or self["generation_requests"] >= MAX_GENERATION_REQUESTS:
            self["failed"] = True
            raise RuntimeError("Live evaluation generation request budget exhausted")
        case_key = (mode, question)
        case_count = self["generation_requests_by_case"].get(case_key, 0)
        case_cap = 1 if mode == "single_step" else self["multi_question_caps"].get(question, 0)
        if case_count >= case_cap:
            self["failed"] = True
            raise RuntimeError("Live evaluation per-question generation budget exhausted")
        now = asyncio.get_running_loop().time()
        previous = self["last_generation_at"]
        if previous is not None:
            await asyncio.sleep(max(0.0, GENERATION_INTERVAL_SECONDS - (now - previous)))
        self["last_generation_at"] = asyncio.get_running_loop().time()
        self["generation_requests"] += 1
        self["generation_requests_by_case"][case_key] = case_count + 1


class BoundedInvestigationUseCase:
    """Compose the existing use case with the per-category approved request cap."""

    def __init__(self, search, provider, state):
        self.search, self.provider, self.state = search, provider, state

    async def execute(self, question, *, owner_id):
        max_generations = self.state["multi_question_caps"][question]
        service = InvestigateQuestion(
            self.search,
            BudgetedInvestigationModel(self.provider, self.state),
            max_turns=max_generations,
            max_search_calls=max_generations - 1,
            max_results_per_search=5,
            max_accumulated_evidence=10,
            max_context_chars=24000,
            max_generation_requests=max_generations,
            max_embedding_requests=max_generations - 1,
        )
        return await service.execute(question, owner_id=owner_id)


def _extract(document_row):
    source_data = document_row["source"]
    source = SourceReference(
        **{
            **source_data,
            "kind": SourceKind(source_data["kind"]),
        }
    )
    structure = tuple(
        StructuralSpan(
            kind=StructureKind(item["kind"]),
            start=item["start"],
            end=item["end"],
        )
        for item in document_row["structure"]
    )
    source_spans = tuple(
        SourceSpan(
            start=item["start"],
            end=item["end"],
            locators=tuple(
                SourceLocator(kind=LocatorKind(locator["kind"]), index=locator["index"]) for locator in item["locators"]
            ),
        )
        for item in document_row["source_spans"]
    )
    return ExtractedDocument(
        text=document_row["text"],
        title=document_row["title"],
        source=source,
        structure=structure,
        source_spans=source_spans,
    )


def _controlled_passage(locator, value):
    text = f"The current access code of fictional vault Conflict-0 is {value}."
    source = SourceReference(
        kind=SourceKind.DATASET,
        source_id="controlled-evaluation",
        revision="1",
        record_locator={"record": locator},
        display_name=locator,
    )
    end = len(text)
    return ExtractedDocument(
        text=text,
        title=locator,
        source=source,
        structure=(),
        source_spans=(SourceSpan(start=0, end=end, locators=(SourceLocator(kind=LocatorKind.SENTENCE, index=0),)),),
    )


def _manifest_row(extracted):
    return {
        "title": extracted.title,
        "text": extracted.text,
        "content_sha256": Content(extracted.text).sha256,
        "source": {
            "kind": extracted.source.kind.value,
            "source_id": extracted.source.source_id,
            "record_locator": extracted.source.record_locator,
            "metadata": extracted.source.metadata,
            "display_name": extracted.source.display_name,
            "revision": extracted.source.revision,
        },
        "structure": [{"kind": item.kind.value, "start": item.start, "end": item.end} for item in extracted.structure],
        "source_spans": [
            {
                "start": item.start,
                "end": item.end,
                "locators": [{"kind": locator.kind.value, "index": locator.index} for locator in item.locators],
            }
            for item in extracted.source_spans
        ],
    }


@pytest.mark.asyncio
async def test_live_paired_answer_quality_within_free_tier_budget(db_engine, sessions, repository):
    from investigation_engine.infrastructure.config import settings

    if not settings.gemini_api_key:
        pytest.skip("GEMINI_API_KEY is not configured; no live request was made")
    tokenizer_path = Path("artifacts/tokenizers/voyage-4/tokenizer.json")
    if not tokenizer_path.is_file():
        pytest.skip("Pinned reference tokenizer is missing; no live request was made")

    base = Path("data/samples/evaluation")
    suite = json.loads((base / "suite.json").read_text(encoding="utf-8"))
    annotation_corpus = json.loads((base / "corpus.json").read_text(encoding="utf-8"))
    cases = {row["id"]: row for row in suite["cases"]}
    selected_cases = [cases[key] for key in SAMPLE_IDS]
    source_example_id = selected_cases[0]["original_question_id"]
    source_documents = [
        row
        for row in annotation_corpus["documents"]
        if row["source"]["record_locator"].get("example_id") == source_example_id
    ]
    if len(source_documents) != 10:
        pytest.fail("The selected source record no longer has its ten reviewed context passages")
    selected_docs = source_documents + [
        _controlled_passage("contradiction-0-a", "amber"),
        _controlled_passage("contradiction-0-b", "violet"),
    ]
    extracted_docs = [_extract(row) if isinstance(row, dict) else row for row in selected_docs]
    tokenizer = HuggingFaceTokenizer(
        tokenizer_path,
        name=VOYAGE_REFERENCE_NAME,
        revision=VOYAGE_REFERENCE_REVISION,
        expected_sha256=VOYAGE_REFERENCE_SHA256,
    )
    chunking = ChunkingConfig(
        strategy=ChunkingStrategy.WHOLE_PASSAGE,
        tokenizer=tokenizer.identity,
        max_tokens=128,
    )
    # Confirm the request budget before any provider call; each selected source
    # passage is expected to fit one whole-passage chunk (maximum two per input).
    planned_chunk_count = 0
    for row in extracted_docs:
        plan = chunk_document(
            DocumentVersion(
                document_id=Document(owner_id=None).id,
                version_number=1,
                content=Content(row.text),
                source=row.source,
                structure=row.structure,
                source_spans=row.source_spans,
            ),
            config=chunking,
            tokenizer=tokenizer,
        )
        if len(plan.chunks) > 2:
            pytest.fail("A source passage would exceed the pre-authorized chunk bound")
        planned_chunk_count += len(plan.chunks)
    # Two single-step questions plus at most two bounded multi-step searches
    # each use six query embedding inputs in total.
    if planned_chunk_count + 6 > MAX_EMBEDDING_TEXTS:
        pytest.fail("The sample's worst-case source/query embeddings exceed the pre-authorized budget")

    corpus_docs = [_manifest_row(row) for row in extracted_docs]
    registrations = []
    for document in extracted_docs:
        registrations.append(await RegisterDocument(repository).execute(document, owner_id=None))

    # The controlled source rows above are not part of the committed annotation
    # corpus, so build the indexed-manifest records from the registered source data.
    indexed_corpus = {
        **annotation_corpus,
        "documents": corpus_docs,
        "record_count": len(corpus_docs),
        "unique_content_count": len({row["content_sha256"] for row in corpus_docs}),
    }
    multi_question_caps = {case["question"]: 3 if case["category"] == "two_hop" else 2 for case in selected_cases}
    state = LiveRequestState(multi_question_caps)
    async with GeminiEmbeddingProvider(api_key=settings.gemini_api_key) as raw_embedding:
        embeddings = BudgetedGeminiEmbeddings(raw_embedding, state)
        chunk_plans = PostgreSQLChunkPlanRepository(sessions)
        indexer = IndexDocumentVersion(
            chunk_plans,
            PostgreSQLEmbeddingRepository(sessions),
            PostgreSQLDocumentActivationRepository(sessions),
            embeddings,
            max_attempts=1,
        )
        preparer = PrepareDocumentIndexing(chunk_plans, indexer, tokenizer)
        for document, (stored_document, version) in zip(extracted_docs, registrations, strict=True):
            await preparer.execute(stored_document, version, chunking, embeddings.configuration, max_new_embeddings=2)

        evidence = PostgreSQLEvidenceRepository(sessions)
        search = SearchEvidence(
            evidence, embeddings, chunking, embeddings.configuration, mode="semantic", semantic_candidates=20
        )
        answer_provider = GeminiAnswerGenerator(
            api_key=settings.gemini_api_key,
            model=GENERATION_MODEL,
            timeout_seconds=45,
            max_attempts=1,
            max_output_tokens=1024,
        )
        investigation_provider = GeminiInvestigationModel(
            api_key=settings.gemini_api_key,
            model=GENERATION_MODEL,
            timeout_seconds=60,
            max_output_tokens=1024,
        )
        async with AsyncExitStack() as provider_stack:
            provider_stack.push_async_callback(answer_provider.aclose)
            provider_stack.push_async_callback(investigation_provider.aclose)
            single = AnswerQuestion(
                search,
                BudgetedAnswerGenerator(answer_provider, state),
                max_evidence=5,
                max_context_chars=12000,
            )
            multi = BoundedInvestigationUseCase(search, investigation_provider, state)
            shared = {
                "retrieval_mode": "semantic",
                "chunking": asdict(chunking),
                "embedding": asdict(embeddings.configuration),
                "generation_model": GENERATION_MODEL,
            }
            runs = [
                AnsweringRun(
                    name="single-step-real-gemini",
                    mode="single_step",
                    service=single,
                    answering_budgets={"max_evidence": 5, "max_context_chars": 12000},
                    **shared,
                ),
                AnsweringRun(
                    name="multi-step-real-gemini",
                    mode="multi_step",
                    service=multi,
                    investigation_budgets={
                        "per_question_generation_caps": {"two_hop": 3, "contradictory": 2},
                        "max_search_calls": 2,
                        "max_results_per_search": 5,
                        "max_accumulated_evidence": 10,
                        "max_context_chars": 24000,
                        "max_embedding_requests_per_question": 2,
                    },
                    **shared,
                ),
            ]
            report = await evaluate_answers(
                suite,
                indexed_corpus,
                runs,
                annotation_corpus=annotation_corpus,
                indexed_corpus_fingerprint=fingerprint(indexed_corpus),
                case_ids=SAMPLE_IDS,
                stop_requested=lambda: state["failed"],
            )
            report["execution"] = {
                "kind": "partial_live_real_gemini_sample",
                "generation_model_requested": GENERATION_MODEL,
                "generation_model_versions": sorted(
                    {
                        row.get("model_version")
                        for result in report["runs"]
                        for row in result["questions"]
                        if row.get("model_version")
                    }
                ),
                "generation_requests_observed": state["generation_requests"],
                "generation_request_cap": MAX_GENERATION_REQUESTS,
                "generation_rpm_user_reported": 5,
                "generation_tpm_user_reported": 250000,
                "generation_rpd_user_reported": 20,
                "previous_generation_request_upper_bound_user_attested": 12,
                "known_generation_requests_upper_bound_with_this_run": 14,
                "untracked_project_usage_independently_verified": False,
                "embedding_model": "gemini-embedding-2",
                "embedding_dimensions": 768,
                "source_occurrences_indexed": len(corpus_docs),
                "unique_contents_indexed": indexed_corpus["unique_content_count"],
                "dataset_revision": annotation_corpus["dataset_revision"],
                "indexed_question_categories": [case["category"] for case in selected_cases],
                "embedding_inputs_observed": embeddings.inputs,
                "embedding_text_cap": MAX_EMBEDDING_TEXTS,
                "embedding_request_counts": raw_embedding.request_counts,
                "embedding_rpm_user_reported": 100,
                "embedding_tpm_user_reported": 30000,
                "embedding_rpd_user_reported": 1000,
                "billing_status": "free tier, billing disabled (user reported)",
                "provider_retries": 0,
                "cloud_sql_contacted": False,
                "test_database": "disposable local PostgreSQL/pgvector only",
                "budget_note": "Hard stop at 7 generation requests and 22 embedding text inputs; provider failures stop later evaluation calls.",
            }
            export_answer_quality(report, Path(".cache/evaluation-answer-quality-live"))
            assert state["generation_requests"] <= MAX_GENERATION_REQUESTS
            assert embeddings.inputs <= MAX_EMBEDDING_TEXTS
            assert len(report["paired_comparisons"]) == 1
            print(
                json.dumps(
                    {
                        "report_directory": ".cache/evaluation-answer-quality-live",
                        "generation_requests": state["generation_requests"],
                        "embedding_inputs": embeddings.inputs,
                        "embedding_api_requests": raw_embedding.request_counts,
                        "outcomes": [
                            {"run": run["configuration"]["name"], "aggregate": run["aggregate"]}
                            for run in report["runs"]
                        ],
                        "stopped_early": report["stopped_early"],
                    },
                    ensure_ascii=True,
                    allow_nan=False,
                )
            )
