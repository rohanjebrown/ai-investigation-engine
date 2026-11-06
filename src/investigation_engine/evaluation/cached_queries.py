"""Explicit offline query vectors; a cache miss never invokes an external model."""

import json
from dataclasses import asdict
from pathlib import Path

from investigation_engine.application.embeddings import EmbeddingConfig, storage_vector
from investigation_engine.application.ports.embedding_errors import EmbeddingInputError


class CachedQueryMiss(EmbeddingInputError):
    """An exact question has no stored vector; inference is never a fallback."""


class CachedQueryEmbeddings:
    def __init__(self, configuration: EmbeddingConfig, vectors: dict[str, list[float]]) -> None:
        self.configuration = configuration
        self._vectors = {question: storage_vector(vector) for question, vector in vectors.items()}

    async def embed_query(self, question: str):
        try:
            return self._vectors[question]
        except KeyError:
            raise CachedQueryMiss("No offline query vector for the exact question") from None

    async def embed_documents(self, texts):
        raise EmbeddingInputError("Offline evaluation does not generate document embeddings")


def load_query_cache(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    configuration = EmbeddingConfig(**value["configuration"])
    vectors = value["vectors"]
    CachedQueryEmbeddings(configuration, vectors)  # Validate every stored vector.
    return configuration, vectors


def import_query_caches(paths, configuration=None):
    """Explicit local imports; traces are never auto-discovered or re-embedded.

    Flat legacy dictionaries require a supplied profile or a preceding profiled
    cache. Trace vectors must declare that same embedding configuration.
    """
    vectors = {}
    for path in map(Path, paths):
        if path.suffix == ".jsonl":
            question, incoming = None, {}
            for line in path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if event["event"] == "retrieval.search":
                    question = event["data"]["question"]
                if event["event"] == "retrieval.query_vector" and question:
                    profile = EmbeddingConfig(**event["data"]["configuration"])
                    configuration = configuration or profile
                    if profile != configuration:
                        raise ValueError("Trace query embedding profiles differ")
                    incoming[question] = event["data"]["vector"]
        else:
            value = json.loads(path.read_text(encoding="utf-8"))
            if "configuration" in value:
                profile = EmbeddingConfig(**value["configuration"])
                configuration = configuration or profile
                if profile != configuration:
                    raise ValueError("Cached query embedding profiles differ")
                incoming = value["vectors"]
            else:
                incoming = value
        if configuration is None:
            raise ValueError("Unprofiled legacy cache requires an explicit embedding profile")
        for question, vector in incoming.items():
            vector = storage_vector(vector)
            if question in vectors and vectors[question] != vector:
                raise ValueError("Conflicting cached vectors for the same exact question")
            vectors[question] = vector
    if configuration is None:
        raise ValueError("No cache embedding profile found")
    return {"version": 1, "configuration": asdict(configuration), "vectors": vectors}
