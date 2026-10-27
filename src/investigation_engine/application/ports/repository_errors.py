"""Application-facing registration errors; independent of storage libraries."""


class RegistrationConflict(ValueError):
    """An occurrence was already registered with different text or mappings."""


class ContentHashCollision(ValueError):
    """A Content digest refers to different exact text."""


class RepositoryError(RuntimeError):
    """Persistence failed; the outcome may be uncertain around commit.

    Underlying causes are for trusted internal diagnosis, not client responses.
    A later identical request must retain its original source identity.
    """
