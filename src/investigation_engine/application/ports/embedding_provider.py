"""The provider-independent boundary for document and retrieval-query vectors."""

from collections.abc import Sequence
from typing import Protocol

from investigation_engine.application.embeddings import EmbeddingConfig, EmbeddingVector


class EmbeddingProvider(Protocol):
    @property
    def configuration(self) -> EmbeddingConfig:
        """Complete output-affecting document encoding identity, excluding secrets."""
        ...

    async def embed_documents(self, texts: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        """Embed nonblank exact texts in input order, or raise without partial output.

        Formatting is the provider profile's responsibility. Callers keep their
        own chunk identities; this operation neither splits nor persists chunks.
        Earlier remote requests may have completed when a later input fails.
        """
        ...

    async def embed_query(self, query: str) -> EmbeddingVector:
        """Embed a nonblank retrieval query in the documents' embedding space."""
        ...
