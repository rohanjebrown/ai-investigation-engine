"""Gemini Developer API text embeddings; no persistence or chunk transformation."""

from collections.abc import Awaitable, Sequence
from math import hypot, isfinite
from types import TracebackType
from typing import Self

import httpx
from google import genai
from google.genai import errors, types

from investigation_engine.application.embeddings import EmbeddingConfig
from investigation_engine.application.ports.provider_diagnostics import attach_diagnostic, status_category

from investigation_engine.application.ports.embedding_errors import (
    EmbeddingInputError,
    EmbeddingProviderError,
    EmbeddingRateLimitError,
    EmbeddingResponseError,
)
from investigation_engine.application.ports.embedding_provider import EmbeddingVector


MODEL = "gemini-embedding-2"
DIMENSIONS = 768
INPUT_TOKEN_LIMIT = 8192
DEFAULT_BATCH_SIZE = 25
DEFAULT_BATCH_TOKEN_LIMIT = 32_768
DOCUMENT_FORMAT_VERSION = "gemini_no_title_v1"
QUERY_FORMAT_VERSION = "gemini_search_query_v1"


class GeminiEmbeddingProvider:
    """Own a reusable SDK client; use async with or explicitly await aclose().

    Each text gets its own token-count request. Multiple separately represented
    Content objects are sent in one embed request. No fallback, truncation,
    splitting, normalization or hidden batch retries.
    Calls must run within the owning async application's client lifetime.
    """

    def __init__(self, *, api_key: str | None,
                 batch_size: int = DEFAULT_BATCH_SIZE,
                 batch_token_limit: int = DEFAULT_BATCH_TOKEN_LIMIT) -> None:
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        if type(batch_token_limit) is not int or batch_token_limit < 1:
            raise ValueError("batch_token_limit must be a positive integer")
        if not isinstance(api_key, str) or not api_key.strip():
            raise EmbeddingProviderError("GEMINI_API_KEY is required for Gemini embeddings")
        self._client = genai.Client(
            api_key=api_key,
            enterprise=False,
            http_options=types.HttpOptions(
                base_url="https://generativelanguage.googleapis.com",
                api_version="v1beta", timeout=60_000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        self._closed = False
        self._batch_size = batch_size
        self._batch_token_limit = batch_token_limit
        self._token_count_requests = 0
        self._embed_content_requests = 0

    @property
    def configuration(self) -> EmbeddingConfig:
        return EmbeddingConfig(provider="gemini_developer_api", model_id=MODEL,
                               dimensions=DIMENSIONS, document_format=DOCUMENT_FORMAT_VERSION)

    @property
    def request_counts(self) -> dict[str, int]:
        return {"count_tokens": self._token_count_requests,
                "embed_content": self._embed_content_requests}

    async def __aenter__(self) -> Self:
        self._ensure_open()
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None,
        exc: BaseException | None, traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                await self._client.aio.aclose()
            finally:
                self._client.close()

    async def embed_documents(self, texts: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        self._ensure_open()
        if isinstance(texts, (str, bytes)) or not isinstance(texts, Sequence) or not texts:
            raise EmbeddingInputError("Document inputs must be a nonempty sequence of texts")
        # Snapshot caller-owned lists, and reject all local input errors before IO.
        inputs = tuple(texts)
        for index, text in enumerate(inputs):
            _validate_text(text, label=f"Document input {index}")
        contents = tuple(types.Content(parts=[types.Part.from_text(text=f"title: none | text: {text}")])
                         for text in inputs)
        vectors = []
        group, group_tokens = [], 0
        for content in contents:
            tokens = await self._count_tokens(content)
            if tokens > self._batch_token_limit:
                raise EmbeddingInputError("Input exceeds configured batch token limit")
            if group and (len(group) >= self._batch_size or group_tokens + tokens > self._batch_token_limit):
                vectors.extend(await self._send_batch(group))
                group, group_tokens = [], 0
            group.append(content)
            group_tokens += tokens
        if group:
            vectors.extend(await self._send_batch(group))
        return tuple(vectors)

    async def embed_query(self, query: str) -> EmbeddingVector:
        self._ensure_open()
        _validate_text(query, label="Query")
        return await self._embed_one(f"task: search result | query: {query}")

    def _ensure_open(self) -> None:
        if self._closed:
            raise EmbeddingProviderError("Gemini embedding provider is closed")

    async def _embed_one(self, formatted_text: str) -> EmbeddingVector:
        content = types.Content(parts=[types.Part.from_text(text=formatted_text)])
        tokens = await self._count_tokens(content)
        if tokens > self._batch_token_limit:
            raise EmbeddingInputError("Input exceeds configured batch token limit")
        return (await self._send_batch((content,)))[0]

    async def _count_tokens(self, content: types.Content) -> int:
        count = await self._request(
            self._client.aio.models.count_tokens(model=MODEL, contents=content),
            operation="token counting",
        )
        total = getattr(count, "total_tokens", None)
        if type(total) is not int or total <= 0:
            raise self._response_error("Gemini returned an invalid token count", operation="count_tokens")
        if total > INPUT_TOKEN_LIMIT:
            raise EmbeddingInputError("Formatted input exceeds Gemini's 8192-token limit")
        return total

    async def _send_batch(self, contents: Sequence[types.Content]) -> tuple[EmbeddingVector, ...]:
        response = await self._request(
            self._client.aio.models.embed_content(
                model=MODEL, contents=contents[0] if len(contents) == 1 else list(contents),
                config=types.EmbedContentConfig(output_dimensionality=DIMENSIONS),
            ), operation="embedding generation",
        )
        return self._parse_embeddings(response, expected=len(contents))

    def _parse_embeddings(self, response, *, expected: int = 1) -> tuple[EmbeddingVector, ...]:
        embeddings = getattr(response, "embeddings", None)
        if not isinstance(embeddings, (list, tuple)) or len(embeddings) != expected:
            raise self._response_error(f"Gemini must return exactly {expected} embedding(s) for the input batch")
        vectors = []
        for embedding in embeddings:
            statistics = getattr(embedding, "statistics", None)
            if getattr(statistics, "truncated", False) is True:
                raise self._response_error("Gemini reported truncated embedding input")
            values = getattr(embedding, "values", None)
            if not isinstance(values, (list, tuple)) or len(values) != DIMENSIONS:
                raise self._response_error("Gemini embedding must have 768 dimensions")
            if any(type(value) not in (int, float) for value in values):
                raise self._response_error("Gemini embedding must contain finite numbers")
            try:
                vector = tuple(float(value) for value in values)
            except OverflowError:
                raise self._response_error("Gemini embedding must contain finite numbers") from None
            if not all(isfinite(value) for value in vector):
                raise self._response_error("Gemini embedding must contain finite numbers")
            norm = hypot(*vector)
            if not isfinite(norm) or norm == 0:
                raise self._response_error("Gemini embedding must have a finite nonzero norm")
            vectors.append(vector)
        return tuple(vectors)

    def _response_error(self, message, *, operation="embed_content"):
        return attach_diagnostic(EmbeddingResponseError(message), operation=operation,
            model_id=MODEL, http_status=200, category="invalid_response",
            request_count=self._token_count_requests if operation == "count_tokens" else self._embed_content_requests)

    async def _request[T](self, request: Awaitable[T], *, operation: str) -> T:
        status = None
        if operation == "token counting":
            self._token_count_requests += 1
        elif operation == "embedding generation":
            self._embed_content_requests += 1
        try:
            return await request
        except errors.APIError as error:
            code = error.code
            status, category = code, status_category(code)
            cause = RuntimeError(f"Gemini API failure during {operation} (HTTP {code})")
            if code == 429:
                failure = EmbeddingRateLimitError("Gemini rate or quota limit exceeded")
            elif code in (400, 413, 422):
                failure = EmbeddingInputError("Gemini rejected the embedding input")
            else:
                failure = EmbeddingProviderError(
                    f"Gemini {operation} failed", retryable=code in (408, 425, 500, 502, 503, 504),
                )
        except httpx.RequestError as error:
            category = "timeout" if isinstance(error, httpx.TimeoutException) else "transport"
            cause = RuntimeError(f"Gemini transport failure during {operation} ({type(error).__name__})")
            failure = EmbeddingProviderError(f"Gemini {operation} could not be completed", retryable=True)
        except (ValueError, TypeError) as error:
            category = "invalid_response"
            cause = RuntimeError(f"Gemini response decoding failed during {operation} ({type(error).__name__})")
            failure = EmbeddingResponseError("Gemini returned an invalid response")
        # Raise outside the handlers: raw SDK errors can contain keys, request
        # bodies and response echoes. Keep only a sanitized diagnostic cause.
        # CancelledError is not caught and propagates unchanged.
        count = self._token_count_requests if operation == "token counting" else self._embed_content_requests
        raise attach_diagnostic(failure,
            operation="count_tokens" if operation == "token counting" else "embed_content",
            model_id=MODEL, http_status=status, category=category,
            retryable=getattr(failure, "retryable", status == 429),
            request_count=count) from cause


def _validate_text(text: str, *, label: str) -> None:
    if not isinstance(text, str) or not text.strip():
        raise EmbeddingInputError(f"{label} must be nonblank text")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise EmbeddingInputError(f"{label} must be valid UTF-8 text") from None
