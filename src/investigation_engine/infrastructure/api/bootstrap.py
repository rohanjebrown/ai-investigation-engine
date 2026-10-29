"""Own HTTP application resources; no settings/client/connection at import time."""

from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from investigation_engine.application.chunking import ChunkingConfig
from investigation_engine.application.embeddings import EmbeddingConfig
from investigation_engine.application.investigations import InvestigateQuestion
from investigation_engine.application.use_cases.answer_question import AnswerQuestion
from investigation_engine.application.use_cases.delete_document import DeleteDocument
from investigation_engine.application.use_cases.get_document import GetDocument
from investigation_engine.application.use_cases.index_document_version import IndexDocumentVersion
from investigation_engine.application.use_cases.list_documents import ListDocuments
from investigation_engine.application.use_cases.prepare_document_indexing import PrepareDocumentIndexing
from investigation_engine.application.use_cases.register_document import RegisterDocument
from investigation_engine.application.use_cases.search_evidence import SearchEvidence
from investigation_engine.application.use_cases.update_document import UpdateDocument
from investigation_engine.infrastructure.embeddings.gemini_provider import GeminiEmbeddingProvider
from investigation_engine.infrastructure.generation.gemini_answer_generator import GeminiAnswerGenerator
from investigation_engine.infrastructure.generation.gemini_investigation_model import GeminiInvestigationModel
from investigation_engine.infrastructure.persistence.models import ChunkingConfigurationRow
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import (
    PostgreSQLChunkPlanRepository,
    _configuration,
)
from investigation_engine.infrastructure.persistence.postgresql_document_activation_repository import (
    PostgreSQLDocumentActivationRepository,
)
from investigation_engine.infrastructure.persistence.postgresql_document_repository import PostgreSQLDocumentRepository
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


@dataclass(frozen=True, slots=True)
class APIServices:
    settings: object
    questions: AnswerQuestion
    register_document: RegisterDocument
    update_document: UpdateDocument
    delete_document: DeleteDocument
    list_documents: ListDocuments
    get_document: GetDocument
    prepare_indexing: PrepareDocumentIndexing | None
    chunking: ChunkingConfig
    embedding_configuration: EmbeddingConfig
    investigations: InvestigateQuestion | None = None


def _engine_options(config):
    return {
        "hide_parameters": True,
        "pool_pre_ping": True,
        "pool_size": getattr(config, "db_pool_size", 3),
        "max_overflow": getattr(config, "db_max_overflow", 0),
        "pool_timeout": getattr(config, "db_pool_timeout_seconds", 5),
        "pool_recycle": 1800,
        "connect_args": {"options": f"-c statement_timeout={getattr(config, 'db_statement_timeout_ms', 30000)}"},
    }


@asynccontextmanager
async def application_service():
    """Compose HTTP use cases and provider resources during app lifespan."""
    from investigation_engine.infrastructure.config import settings

    if (
        not settings.gemini_api_key
        or not settings.gemini_generation_model
        or settings.serving_chunking_configuration_id is None
    ):
        raise RuntimeError(
            "Application startup requires Gemini credentials, generation model and serving configuration"
        )
    try:
        async with AsyncExitStack() as resources:
            engine = create_async_engine(settings.database_url(), **_engine_options(settings))
            resources.push_async_callback(engine.dispose)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            embedding = GeminiEmbeddingProvider(
                api_key=settings.gemini_api_key,
                batch_size=getattr(settings, "gemini_embedding_batch_size", 25),
                batch_token_limit=getattr(settings, "gemini_embedding_batch_token_limit", 32768),
            )
            resources.push_async_callback(embedding.aclose)
            generation = GeminiAnswerGenerator(
                api_key=settings.gemini_api_key,
                model=settings.gemini_generation_model,
                timeout_seconds=settings.generation_timeout_seconds,
                max_attempts=settings.generation_max_attempts,
                max_output_tokens=settings.generation_max_output_tokens,
                temperature=getattr(settings, "generation_temperature", None),
            )
            resources.push_async_callback(generation.aclose)
            investigation_model = None
            if getattr(settings, "investigation_enabled", False):
                investigation_model = GeminiInvestigationModel(
                    api_key=settings.gemini_api_key,
                    model=settings.gemini_generation_model,
                    timeout_seconds=settings.generation_timeout_seconds,
                    max_output_tokens=getattr(settings, "investigation_max_output_tokens", 1024),
                )
                resources.push_async_callback(investigation_model.aclose)
            async with sessions() as session:
                row = await session.get(ChunkingConfigurationRow, settings.serving_chunking_configuration_id)
                if row is None:
                    raise ValueError("Selected serving chunking configuration does not exist")
                chunking = _configuration(row)
            tokenizer_path = getattr(settings, "chunking_tokenizer_path", None)
            tokenizer = None
            if tokenizer_path is not None:
                tokenizer = HuggingFaceTokenizer(
                    tokenizer_path,
                    name=VOYAGE_REFERENCE_NAME,
                    revision=VOYAGE_REFERENCE_REVISION,
                    expected_sha256=VOYAGE_REFERENCE_SHA256,
                )
                if tokenizer.identity != chunking.tokenizer:
                    raise ValueError("Configured tokenizer artifact does not match the serving profile")
            documents = PostgreSQLDocumentRepository(sessions)
            plans = PostgreSQLChunkPlanRepository(sessions)
            embeddings = PostgreSQLEmbeddingRepository(sessions)
            activation = PostgreSQLDocumentActivationRepository(sessions)
            indexer = IndexDocumentVersion(
                plans,
                embeddings,
                activation,
                embedding,
                max_attempts=1,
                request_timeout_seconds=60,
            )
            evidence = PostgreSQLEvidenceRepository(
                sessions, lexical_backend=getattr(settings, "lexical_search_backend", "postgres_fts")
            )
            await evidence.validate_backend()
            search = SearchEvidence(
                evidence,
                embedding,
                chunking,
                embedding.configuration,
                mode=settings.retrieval_mode,
                hybrid_repository=evidence,
                semantic_candidates=settings.semantic_candidate_limit,
                keyword_candidates=settings.keyword_candidate_limit,
                rrf_k=settings.rrf_k,
            )
            services = APIServices(
                settings=settings,
                questions=AnswerQuestion(
                    search,
                    generation,
                    max_evidence=settings.rag_max_evidence,
                    max_context_chars=settings.rag_max_context_chars,
                ),
                register_document=RegisterDocument(documents),
                update_document=UpdateDocument(documents),
                delete_document=DeleteDocument(documents),
                list_documents=ListDocuments(documents),
                get_document=GetDocument(documents),
                prepare_indexing=PrepareDocumentIndexing(plans, indexer, tokenizer) if tokenizer is not None else None,
                chunking=chunking,
                embedding_configuration=embedding.configuration,
                investigations=(
                    InvestigateQuestion(
                        search,
                        investigation_model,
                        max_turns=getattr(settings, "investigation_max_turns", 6),
                        max_search_calls=getattr(settings, "investigation_max_search_calls", 5),
                        max_results_per_search=getattr(settings, "investigation_max_results_per_search", 5),
                        max_accumulated_evidence=getattr(settings, "investigation_max_accumulated_evidence", 15),
                        max_context_chars=getattr(settings, "investigation_max_context_chars", 24000),
                        max_generation_requests=getattr(settings, "investigation_max_generation_requests", 6),
                        max_embedding_requests=getattr(settings, "investigation_max_embedding_requests", 5),
                    )
                    if investigation_model is not None
                    else None
                ),
            )
            yield services
    except Exception:
        raise RuntimeError(
            "Application lifecycle failed; verify settings, persisted profiles and local service availability"
        ) from None


@asynccontextmanager
async def question_service():
    """Compatibility composition used by question-only callers and tests."""
    from investigation_engine.infrastructure.config import settings

    if (
        not settings.gemini_api_key
        or not settings.gemini_generation_model
        or settings.serving_chunking_configuration_id is None
    ):
        raise RuntimeError(
            "RAG startup requires GEMINI_API_KEY, GEMINI_GENERATION_MODEL and SERVING_CHUNKING_CONFIGURATION_ID"
        )
    try:
        async with AsyncExitStack() as resources:
            engine = create_async_engine(settings.database_url(), **_engine_options(settings))
            resources.push_async_callback(engine.dispose)
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            embedding = GeminiEmbeddingProvider(
                api_key=settings.gemini_api_key,
                batch_size=getattr(settings, "gemini_embedding_batch_size", 25),
                batch_token_limit=getattr(settings, "gemini_embedding_batch_token_limit", 32768),
            )
            resources.push_async_callback(embedding.aclose)
            generation = GeminiAnswerGenerator(
                api_key=settings.gemini_api_key,
                model=settings.gemini_generation_model,
                timeout_seconds=settings.generation_timeout_seconds,
                max_attempts=settings.generation_max_attempts,
                max_output_tokens=settings.generation_max_output_tokens,
                temperature=getattr(settings, "generation_temperature", None),
            )
            resources.push_async_callback(generation.aclose)
            async with sessions() as session:
                row = await session.get(ChunkingConfigurationRow, settings.serving_chunking_configuration_id)
                if row is None:
                    raise ValueError("Selected serving chunking configuration does not exist")
                chunking = _configuration(row)
            evidence = PostgreSQLEvidenceRepository(
                sessions, lexical_backend=getattr(settings, "lexical_search_backend", "postgres_fts")
            )
            await evidence.validate_backend()
            search = SearchEvidence(
                evidence,
                embedding,
                chunking,
                embedding.configuration,
                mode=settings.retrieval_mode,
                hybrid_repository=evidence,
                semantic_candidates=settings.semantic_candidate_limit,
                keyword_candidates=settings.keyword_candidate_limit,
                rrf_k=settings.rrf_k,
            )
            yield AnswerQuestion(
                search,
                generation,
                max_evidence=settings.rag_max_evidence,
                max_context_chars=settings.rag_max_context_chars,
            )
    except Exception:
        raise RuntimeError(
            "RAG lifecycle failed; verify settings, persisted profile and service availability"
        ) from None
