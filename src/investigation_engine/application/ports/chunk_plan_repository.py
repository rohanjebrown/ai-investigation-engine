"""Persistence boundary for complete immutable version/configuration plans."""

from typing import Protocol
from uuid import UUID

from investigation_engine.application.chunking import ChunkingConfig, ChunkPlan


class ChunkPlanRepository(Protocol):
    async def save(self, plan: ChunkPlan) -> ChunkPlan:
        """Commit a complete plan or return an identical stored plan.

        Invalid input or a missing version raises ValueError; different ordered
        ranges for an existing version/configuration raise ChunkPlanConflict.
        RepositoryError covers persistence failure or invalid stored data.
        Failures at commit may be uncertain; retry the same version/configuration.
        Saving never activates a version or defines embedding readiness.
        """
        ...

    async def get(self, document_version_id: UUID, config: ChunkingConfig) -> ChunkPlan | None:
        """Reconstruct the complete ordered plan, or None when none exists.

        Configuration identity is its full definition, not an infrastructure ID.
        Corrupt/incomplete stored plans raise RepositoryError, never look complete.
        This is a trusted internal boundary, not public retrieval authorization.
        """
        ...
