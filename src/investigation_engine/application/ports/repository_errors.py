"""Application-facing repository errors; independent of storage libraries."""


class RegistrationConflict(ValueError):
    """An occurrence was already registered with different text or mappings."""


class ContentHashCollision(ValueError):
    """A Content digest refers to different exact text."""


class ChunkPlanConflict(ValueError):
    """A version/configuration already has different immutable chunk ranges."""


class ActivationConflict(ValueError):
    """Document state or selected-profile completeness forbids activation."""


class DocumentNotFound(LookupError):
    """No document exists for the requested identity."""


class DocumentAccessDenied(PermissionError):
    """The trusted actor cannot access or mutate the requested document."""


class DocumentUpdateConflict(ValueError):
    """A stale, deleted, or already-pending document update was rejected."""


class RepositoryError(RuntimeError):
    """Persistence failed; the outcome may be uncertain around commit.

    Underlying causes are for trusted internal diagnosis, not client responses.
    A later identical request must retain its operation identity: the source
    occurrence for registration, or version/configuration for a chunk plan.
    """
