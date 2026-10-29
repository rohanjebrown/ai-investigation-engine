"""Resumable synchronous-in-request indexing, without provider or database types."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from uuid import UUID

from investigation_engine.application.chunking import Chunk, ChunkingConfig
from investigation_engine.application.embeddings import EmbeddingConfig, embedding_eligible, storage_vector
from investigation_engine.application.ports.chunk_plan_repository import ChunkPlanRepository
from investigation_engine.application.ports.document_activation_repository import DocumentActivationRepository
from investigation_engine.application.ports.embedding_errors import (
    EmbeddingInputError,
    EmbeddingProviderError,
    EmbeddingRateLimitError,
    EmbeddingResponseError,
)
from investigation_engine.application.ports.embedding_provider import EmbeddingProvider, EmbeddingVector
from investigation_engine.application.ports.embedding_repository import EmbeddingRepository
from investigation_engine.domain.document import Document
from investigation_engine.domain.document_version import DocumentVersion


class IndexingOutcome(StrEnum):
    ACTIVATED = "activated"
    ALREADY_ACTIVE = "already_active"
    NO_SEARCHABLE_CONTENT = "no_searchable_content"
    IN_PROGRESS = "in_progress"


class IndexingState(StrEnum):
    REGISTERED_NOT_INDEXED = "registered_not_indexed"
    PARTIALLY_INDEXED = "partially_indexed"
    READY_TO_ACTIVATE = "ready_to_activate"
    SEARCHABLE = "searchable"
    PENDING_REPLACEMENT = "pending_replacement"
    INSUFFICIENT_CONTENT = "insufficient_content"
    DELETED = "deleted"
    INDEXING_INCOMPLETE = "indexing_incomplete"


@dataclass(frozen=True, slots=True)
class IndexingReport:
    outcome: IndexingOutcome
    eligible_embeddings: int
    indexed_embeddings: int
    requested_embeddings: int


@dataclass(frozen=True, slots=True)
class IndexingProgress:
    state: IndexingState
    eligible_embeddings: int | None
    indexed_embeddings: int
    plan_exists: bool
    active: bool


@dataclass(frozen=True, slots=True)
class _PreparedIndexingTarget:
    document_id: UUID
    version_id: UUID
    expected_version_id: UUID | None
    eligible_chunks: tuple[Chunk, ...]


def _is_valid_document_version_pair(candidate: object) -> bool:
    if not isinstance(candidate, tuple):
        return False
    return len(candidate) == 2 and all(isinstance(identifier, UUID) for identifier in candidate)


async def _prepare_bulk_targets(
    plans: ChunkPlanRepository,
    activation: DocumentActivationRepository,
    targets: Sequence[tuple[UUID, UUID]],
    chunking: ChunkingConfig,
) -> tuple[tuple[_PreparedIndexingTarget, ...], dict[tuple[str, int, int], Chunk]]:
    prepared = []
    unique_chunks = {}
    for document_id, version_id in targets:
        expected = await activation.current_version(document_id, version_id)
        plan = await plans.get(version_id, chunking)
        if plan is None or plan.document_version_id != version_id or plan.config != chunking:
            raise ValueError("A complete persisted chunk plan is required")
        eligible = tuple(chunk for chunk in plan.chunks if embedding_eligible(chunk))
        for chunk in eligible:
            unique_chunks.setdefault(chunk.identity, chunk)
        prepared.append(_PreparedIndexingTarget(document_id, version_id, expected, eligible))
    return tuple(prepared), unique_chunks


async def _create_bulk_reports(
    activation: DocumentActivationRepository,
    prepared: Sequence[_PreparedIndexingTarget],
    chunks: Sequence[Chunk],
    stored: dict[tuple[str, int, int], EmbeddingVector],
    requested: set[tuple[str, int, int]],
    chunking: ChunkingConfig,
    embedding: EmbeddingConfig,
) -> tuple[IndexingReport, ...]:
    complete = {chunk.identity for chunk in chunks} == set(stored)
    reports = []
    for target in prepared:
        eligible = target.eligible_chunks
        if not eligible:
            reports.append(IndexingReport(IndexingOutcome.NO_SEARCHABLE_CONTENT, 0, 0, 0))
            continue
        identities = {chunk.identity for chunk in eligible}
        indexed = len(identities & set(stored))
        generated = len(identities & requested)
        if not complete or indexed != len(identities):
            reports.append(IndexingReport(IndexingOutcome.IN_PROGRESS, len(eligible), indexed, generated))
            continue
        changed = await activation.activate(
            target.document_id,
            target.version_id,
            chunking,
            embedding,
            expected_current_version_id=target.expected_version_id,
        )
        outcome = IndexingOutcome.ACTIVATED if changed else IndexingOutcome.ALREADY_ACTIVE
        reports.append(IndexingReport(outcome, len(eligible), indexed, generated))
    return tuple(reports)


class IndexDocumentVersion:
    def __init__(
        self,
        plans: ChunkPlanRepository,
        embeddings: EmbeddingRepository,
        activation: DocumentActivationRepository,
        provider: EmbeddingProvider,
        *,
        max_attempts: int = 3,
        retry_delay_seconds: float = 1.0,
        request_timeout_seconds: float = 60.0,
        embedding_batch_size: int = 25,
    ) -> None:
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        for retry_setting in (retry_delay_seconds, request_timeout_seconds):
            if type(retry_setting) not in (int, float):  # noqa: WPS516 - accept only built-in int/float values
                raise ValueError("Retry delay and timeout must be finite nonnegative numbers")
            if not isfinite(retry_setting) or retry_setting < 0:
                raise ValueError("Retry delay and timeout must be finite nonnegative numbers")
        if request_timeout_seconds == 0:
            raise ValueError("Request timeout must be positive")
        if type(embedding_batch_size) is not int or embedding_batch_size < 1:
            raise ValueError("embedding_batch_size must be a positive integer")
        self._plans = plans
        self._embeddings = embeddings
        self._activation = activation
        self._provider = provider
        self._attempts = max_attempts
        self._delay = retry_delay_seconds
        self._timeout = request_timeout_seconds
        self._batch_size = embedding_batch_size

    async def execute(
        self,
        document_id: UUID,
        version_id: UUID,
        chunking: ChunkingConfig,
        embedding: EmbeddingConfig,
    ) -> IndexingOutcome:
        report = await self._execute(
            document_id,
            version_id,
            chunking,
            embedding,
            max_new_embeddings=None,
        )
        return report.outcome

    async def execute_bounded(
        self,
        document_id: UUID,
        version_id: UUID,
        chunking: ChunkingConfig,
        embedding: EmbeddingConfig,
        *,
        max_new_embeddings: int,
    ) -> IndexingReport:
        if type(max_new_embeddings) is not int or max_new_embeddings < 1:
            raise ValueError("max_new_embeddings must be a positive integer")
        return await self._execute(
            document_id,
            version_id,
            chunking,
            embedding,
            max_new_embeddings=max_new_embeddings,
        )

    async def execute_many(
        self,
        document_versions: Sequence[tuple[UUID, UUID]],
        chunking: ChunkingConfig,
        embedding: EmbeddingConfig,
        *,
        max_new_embeddings: int | None = None,
    ) -> tuple[IndexingReport, ...]:
        """Index multiple persisted plans together, deduplicating shared ranges.

        This is the bulk counterpart to execute(): provider batches may span
        documents, while each vector save and each document activation retain
        their existing repository transaction and completeness rules.
        """
        targets = tuple(document_versions)
        if not targets or any(not _is_valid_document_version_pair(candidate) for candidate in targets):
            raise ValueError("At least one document/version UUID pair is required")
        if len({document_id for document_id, _ in targets}) != len(targets):
            raise ValueError("Each document may appear only once in a bulk indexing call")
        if max_new_embeddings is not None and (type(max_new_embeddings) is not int or max_new_embeddings < 1):
            raise ValueError("max_new_embeddings must be a positive integer when provided")
        if embedding != self._provider.configuration:
            raise EmbeddingInputError("Selected embedding configuration does not match the provider")

        prepared, unique_chunks = await _prepare_bulk_targets(self._plans, self._activation, targets, chunking)
        chunks = tuple(unique_chunks.values())
        existing = await self._embeddings.find(chunks, embedding) if chunks else {}
        missing = tuple(chunk for chunk in chunks if chunk.identity not in existing)
        if max_new_embeddings is not None:
            missing = missing[:max_new_embeddings]
        requested = set(await self._embed_missing_chunks(missing, embedding))

        stored = await self._embeddings.find(chunks, embedding) if chunks else {}
        return await _create_bulk_reports(
            self._activation,
            prepared,
            chunks,
            stored,
            requested,
            chunking,
            embedding,
        )

    async def _execute(
        self,
        document_id: UUID,
        version_id: UUID,
        chunking: ChunkingConfig,
        embedding: EmbeddingConfig,
        *,
        max_new_embeddings: int | None,
    ) -> IndexingReport:
        if embedding != self._provider.configuration:
            raise EmbeddingInputError("Selected embedding configuration does not match the provider")
        expected = await self._activation.current_version(document_id, version_id)
        plan = await self._plans.get(version_id, chunking)
        if plan is None or plan.document_version_id != version_id or plan.config != chunking:
            raise ValueError("A complete persisted chunk plan is required")
        eligible = tuple(chunk for chunk in plan.chunks if embedding_eligible(chunk))
        if not eligible:
            return IndexingReport(IndexingOutcome.NO_SEARCHABLE_CONTENT, 0, 0, 0)
        existing = await self._embeddings.find(eligible, embedding)
        missing = tuple(chunk for chunk in eligible if chunk.identity not in existing)
        if max_new_embeddings is not None:
            missing = missing[:max_new_embeddings]
        requested = len(await self._embed_missing_chunks(missing, embedding))
        stored = await self._embeddings.find(eligible, embedding)
        if set(stored) != {chunk.identity for chunk in eligible}:
            if max_new_embeddings is not None:
                return IndexingReport(
                    IndexingOutcome.IN_PROGRESS,
                    len(eligible),
                    len(stored),
                    requested,
                )
            raise EmbeddingResponseError("Persisted embedding coverage is incomplete")
        changed = await self._activation.activate(
            document_id,
            version_id,
            chunking,
            embedding,
            expected_current_version_id=expected,
        )
        outcome = IndexingOutcome.ACTIVATED if changed else IndexingOutcome.ALREADY_ACTIVE
        return IndexingReport(outcome, len(eligible), len(stored), requested)

    async def _embed_missing_chunks(
        self,
        missing: Sequence[Chunk],
        embedding: EmbeddingConfig,
    ) -> tuple[tuple[str, int, int], ...]:
        requested = []
        for start in range(0, len(missing), self._batch_size):
            batch = missing[start : start + self._batch_size]
            await self._embed_batch(batch, embedding, requested)
        return tuple(requested)

    async def _embed_batch(
        self,
        batch: Sequence[Chunk],
        embedding: EmbeddingConfig,
        requested: list[tuple[str, int, int]],
    ) -> None:
        for attempt in range(self._attempts):
            if attempt:
                existing = await self._embeddings.find(batch, embedding)
                batch = tuple(chunk for chunk in batch if chunk.identity not in existing)
                if not batch:
                    return
            try:
                vectors = await self._request_batch(batch, embedding)
            except TimeoutError:
                failure = EmbeddingProviderError("Embedding request timed out", retryable=True)
            except EmbeddingRateLimitError as error:
                failure = error
            except EmbeddingProviderError as error:
                if not error.retryable:
                    raise
                failure = error
            else:
                await self._save_vectors(batch, vectors, embedding, requested)
                return
            if attempt + 1 == self._attempts:
                raise failure
            await asyncio.sleep(self._delay)

    async def _request_batch(self, batch: Sequence[Chunk], embedding: EmbeddingConfig) -> tuple[EmbeddingVector, ...]:
        if embedding != self._provider.configuration:
            raise EmbeddingInputError("Provider configuration changed during indexing")
        async with asyncio.timeout(self._timeout):
            vectors = await self._provider.embed_documents(tuple(chunk.text for chunk in batch))
        if embedding != self._provider.configuration:
            raise EmbeddingInputError("Provider configuration changed during generation")
        if not isinstance(vectors, tuple) or len(vectors) != len(batch):
            raise EmbeddingResponseError("Provider must return one vector per requested chunk")
        return vectors

    async def _save_vectors(
        self,
        batch: Sequence[Chunk],
        vectors: Sequence[EmbeddingVector],
        embedding: EmbeddingConfig,
        requested: list[tuple[str, int, int]],
    ) -> None:
        for chunk, vector in zip(batch, vectors, strict=True):
            try:
                stored_vector = storage_vector(vector)
            except ValueError:
                raise EmbeddingResponseError("Provider vector is incompatible with embedding storage") from None
            await self._embeddings.save(chunk, embedding, stored_vector)
            requested.append(chunk.identity)

    async def inspect(
        self,
        document: Document,
        version: DocumentVersion,
        chunking: ChunkingConfig,
        embedding: EmbeddingConfig,
    ) -> IndexingProgress:
        if version.document_id != document.id:
            raise ValueError("Document version does not belong to the document")
        if embedding != self._provider.configuration:
            raise EmbeddingInputError("Selected embedding configuration does not match the provider")
        active = document.current_version_id == version.id
        pending_replacement = document.current_version_id is not None and document.current_version_id != version.id
        if document.deleted_at is not None:
            return IndexingProgress(IndexingState.DELETED, None, 0, False, False)
        plan = await self._plans.get(version.id, chunking)
        if plan is None:
            if active:
                state = IndexingState.INDEXING_INCOMPLETE
            elif pending_replacement:
                state = IndexingState.PENDING_REPLACEMENT
            else:
                state = IndexingState.REGISTERED_NOT_INDEXED
            return IndexingProgress(state, None, 0, False, active)
        eligible = tuple(chunk for chunk in plan.chunks if embedding_eligible(chunk))
        if not eligible:
            return IndexingProgress(IndexingState.INSUFFICIENT_CONTENT, 0, 0, True, active)
        indexed = len(await self._embeddings.find(eligible, embedding))
        if active and indexed == len(eligible):
            state = IndexingState.SEARCHABLE
        elif active:
            state = IndexingState.INDEXING_INCOMPLETE
        elif pending_replacement:
            state = IndexingState.PENDING_REPLACEMENT
        elif indexed == len(eligible):
            state = IndexingState.READY_TO_ACTIVATE
        elif indexed:
            state = IndexingState.PARTIALLY_INDEXED
        else:
            state = IndexingState.REGISTERED_NOT_INDEXED
        return IndexingProgress(state, len(eligible), indexed, True, active)
