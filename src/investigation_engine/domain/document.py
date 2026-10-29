from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid7


@dataclass(slots=True, kw_only=True)
class Document:
    # Required even for shared documents: a trusted caller must supply None.
    owner_id: str | None
    id: UUID = field(default_factory=uuid7)
    title: str | None = None
    current_version_id: UUID | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.owner_id is not None and (not isinstance(self.owner_id, str) or not self.owner_id.strip()):
            raise ValueError("Owner ID must be a nonblank principal identifier or None")
