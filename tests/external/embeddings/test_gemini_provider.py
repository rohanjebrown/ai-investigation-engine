"""Live smoke check: explicitly opt in; ordinary collection never loads .env."""

from math import hypot, isfinite

import pytest


async def test_gemini_document_and_query_embeddings():
    # This body is skipped by the existing --run-external gate unless opted in.
    from investigation_engine.infrastructure.config import settings
    from investigation_engine.infrastructure.embeddings.gemini_provider import (
        GeminiEmbeddingProvider,
    )

    if not settings.gemini_api_key:
        pytest.skip("Set GEMINI_API_KEY in the existing application configuration")
    async with GeminiEmbeddingProvider(api_key=settings.gemini_api_key) as provider:
        documents = await provider.embed_documents(["A small synthetic document about rivers."])
        query = await provider.embed_query("What is the document about?")
    assert len(documents) == 1
    for vector in (*documents, query):
        assert len(vector) == 768
        assert all(isfinite(value) for value in vector)
        assert hypot(*vector) > 0
