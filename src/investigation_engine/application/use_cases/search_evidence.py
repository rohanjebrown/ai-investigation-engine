from time import perf_counter

from investigation_engine.application.chunking import ChunkingConfig
from investigation_engine.application.embeddings import EmbeddingConfig, storage_vector
from investigation_engine.application.ports.embedding_errors import EmbeddingInputError, EmbeddingResponseError
from investigation_engine.application.ports.embedding_provider import EmbeddingProvider
from investigation_engine.application.ports.evidence_repository import EvidenceRepository, HybridEvidenceRepository
from investigation_engine.application.retrieval import Evidence, validate_search_question, validate_search_scope
from investigation_engine.application.rrf import reciprocal_rank_fusion, validate_rrf_k
from investigation_engine.application.timing import retrieval_completed, stage_timing, timed


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

    @timed("retrieval.lexical_total")
    async def execute_keywords(self, question: str, limit: int = 10, *, owner_id: str | None) -> tuple[Evidence, ...]:
        """Explicit exact-term route: no query embedding or semantic ranking.

        Does not automatically substitute lexical retrieval for semantic/hybrid.
        The selected stored embedding profile still governs serving completeness.
        """
        validate_search_scope(owner_id, limit)
        validate_search_question(question)
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
            with stage_timing("hybrid_fusion"):
                result = reciprocal_rank_fusion(semantic, keyword, limit=limit, k=self._rrf_k)
            retrieval_completed("hybrid", len(result), len(semantic), len(keyword), (perf_counter() - started) * 1000)
            return result
        result = await self._repository.search(
            vector,
            self._chunking,
            self._embedding,
            owner_id=owner_id,
            limit=limit,
        )
        retrieval_completed("semantic", len(result), len(result), 0, (perf_counter() - started) * 1000)
        return result
