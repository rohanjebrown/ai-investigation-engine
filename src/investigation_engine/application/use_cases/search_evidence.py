import logging
from math import isfinite
from time import perf_counter

from investigation_engine.application.chunking import ChunkingConfig
from investigation_engine.application.debug_trace import emit
from investigation_engine.application.embeddings import EmbeddingConfig, storage_vector
from investigation_engine.application.ports.embedding_errors import EmbeddingInputError, EmbeddingResponseError
from investigation_engine.application.ports.embedding_provider import EmbeddingProvider
from investigation_engine.application.ports.evidence_repository import EvidenceRepository, HybridEvidenceRepository
from investigation_engine.application.ports.evidence_reranker import EvidenceReranker, RerankingError
from investigation_engine.application.retrieval import Evidence, validate_search_question, validate_search_scope
from investigation_engine.application.rrf import reciprocal_rank_fusion, validate_rrf_k
from investigation_engine.application.timing import log_event, retrieval_completed, stage_timing, timed


class SearchEvidence:
    """One explicit serving configuration; caller supplies trusted owner scope."""

    def __init__(  # noqa: WPS211 - keep explicit retrieval configuration keyword API
        self,
        repository: EvidenceRepository,
        provider: EmbeddingProvider,
        chunking: ChunkingConfig,
        embedding: EmbeddingConfig,
        *,
        mode: str = "semantic",
        hybrid_repository: HybridEvidenceRepository | None = None,
        semantic_candidates: int = 20,
        keyword_candidates: int = 20,
        rrf_k: int = 60,
        reranker: EvidenceReranker | None = None,
    ) -> None:
        if not isinstance(chunking, ChunkingConfig) or not isinstance(embedding, EmbeddingConfig):
            raise ValueError("Explicit chunking and embedding configurations are required")
        chunking.__post_init__()
        chunking.tokenizer.__post_init__()
        embedding.__post_init__()
        if chunking.algorithm_version != "1":
            raise ValueError("Unsupported chunking algorithm version")
        if mode not in ("semantic", "hybrid") or (mode == "hybrid" and hybrid_repository is None):
            raise ValueError("Select semantic or hybrid retrieval with the required repository")
        validate_search_scope(None, semantic_candidates)
        validate_search_scope(None, keyword_candidates)
        validate_rrf_k(rrf_k)
        self._repository = repository
        self._provider = provider
        self._chunking = chunking
        self._embedding = embedding
        self._mode = mode
        self._hybrid = hybrid_repository
        self._semantic_candidates = semantic_candidates
        self._keyword_candidates = keyword_candidates
        self._rrf_k = rrf_k
        self._reranker = reranker

    @timed("retrieval.lexical_total")
    async def execute_keywords(self, question: str, limit: int = 10, *, owner_id: str | None) -> tuple[Evidence, ...]:
        """Explicit exact-term route: no query embedding or semantic ranking.

        Does not automatically substitute lexical retrieval for semantic/hybrid.
        The selected stored embedding profile still governs serving completeness.
        """
        validate_search_scope(owner_id, limit)
        validate_search_question(question)
        emit("retrieval.lexical_search", question=question, owner_id=owner_id, limit=limit)
        return await self._repository.search_keywords(
            question,
            self._chunking,
            self._embedding,
            owner_id=owner_id,
            limit=limit,
        )

    @timed("retrieval.total")
    async def execute(
        self,
        question: str,
        limit: int = 10,
        *,
        owner_id: str | None,
    ) -> tuple[Evidence, ...]:
        validate_search_scope(owner_id, limit)
        validate_search_question(question)
        emit(
            "retrieval.search",
            question=question,
            mode=self._mode,
            owner_id=owner_id,
            limit=limit,
            chunking=self._chunking,
            embedding=self._embedding,
        )
        if self._provider.configuration != self._embedding:
            raise EmbeddingInputError("Serving embedding configuration does not match the query provider")
        started = perf_counter()
        with stage_timing("query_embedding"):
            vector = await self._provider.embed_query(question)
        if self._provider.configuration != self._embedding:
            raise EmbeddingInputError("Provider configuration changed during query embedding")
        try:
            vector = storage_vector(vector)
        except ValueError:
            raise EmbeddingResponseError("Query vector is incompatible with serving storage") from None
        emit("retrieval.query_vector", vector=vector, configuration=self._embedding)
        if self._mode == "hybrid":
            semantic, keyword = await self._hybrid.search_channels(
                vector,
                question,
                self._chunking,
                self._embedding,
                owner_id=owner_id,
                semantic_limit=self._semantic_candidates,
                keyword_limit=self._keyword_candidates,
            )
            emit("retrieval.channels", semantic=semantic, lexical=keyword)
            with stage_timing("hybrid_fusion"):
                result = reciprocal_rank_fusion(
                    semantic, keyword, limit=None if self._reranker is not None else limit, k=self._rrf_k
                )
            result = await self._select(question, result, limit)
            retrieval_completed("hybrid", len(result), len(semantic), len(keyword), (perf_counter() - started) * 1000)
            emit("retrieval.selected", evidence=result)
            return result
        result = await self._repository.search(
            vector,
            self._chunking,
            self._embedding,
            owner_id=owner_id,
            limit=max(limit, self._semantic_candidates) if self._reranker is not None else limit,
        )
        candidate_count = len(result)
        result = await self._select(question, result, limit)
        emit("retrieval.selected", evidence=result)
        retrieval_completed("semantic", len(result), candidate_count, 0, (perf_counter() - started) * 1000)
        return result

    async def _select(self, question: str, candidates: tuple[Evidence, ...], limit: int) -> tuple[Evidence, ...]:
        emit("rerank.candidates", query=question, candidates=candidates)
        ordered = candidates
        reason = "disabled" if self._reranker is None else "empty_candidates" if not candidates else None
        if self._reranker is not None and candidates:
            try:
                with stage_timing("evidence_reranking"):
                    ranked = await self._reranker.rerank(question, candidates)
                    # Enforce the application port: original objects, exactly once.
                    if len(ranked) != len(candidates) or {id(row.evidence) for row in ranked} != {
                        id(hit) for hit in candidates
                    }:
                        raise RerankingError("invalid_candidates")
                    if any(
                        type(row.relevance_score) not in (int, float) or not isfinite(row.relevance_score)
                        for row in ranked
                    ):
                        raise RerankingError("invalid_score")
                    ordered = tuple(row.evidence for row in ranked)
                    emit(
                        "rerank.ranking",
                        ranking=[
                            {
                                "identity": row.evidence.identity,
                                "original_position": row.evidence.rank,
                                "reranked_position": position,
                                "relevance_score": row.relevance_score,
                            }
                            for position, row in enumerate(ranked, 1)
                        ],
                    )
            except RerankingError as error:
                reason = str(error)
                log_event("rerank.fallback", level=logging.WARNING, reason=reason, succeeded=False)
        selected = ordered[:limit]
        emit("rerank.selection", fallback=reason is not None, reason=reason, selected=selected)
        return selected
