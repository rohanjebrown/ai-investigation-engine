"""Explicit offline query vectors; a cache miss never invokes an external model."""

from investigation_engine.application.embeddings import EmbeddingConfig, storage_vector
from investigation_engine.application.ports.embedding_errors import EmbeddingInputError


class CachedQueryEmbeddings:
    def __init__(self, configuration: EmbeddingConfig, vectors: dict[str, list[float]]) -> None:
        self.configuration = configuration
        self._vectors = {question: storage_vector(vector) for question, vector in vectors.items()}

    async def embed_query(self, question: str):
        try:
            return self._vectors[question]
        except KeyError:
            raise EmbeddingInputError("No offline query vector for the exact question") from None

    async def embed_documents(self, texts):
        raise EmbeddingInputError("Offline evaluation does not generate document embeddings")
