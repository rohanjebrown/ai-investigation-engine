"""Trusted caller context for application document operations.

Construct this value from an authenticated or otherwise trusted application
boundary. Its principal ID is identity data, not proof of authentication.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DocumentActor:
    principal_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.principal_id, str) or not self.principal_id.strip():
            raise ValueError("Actor principal ID must be nonblank")
