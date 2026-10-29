from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum

type JsonValue = str | int | float | bool | None | list[JsonValue] | JsonObject
type JsonObject = dict[str, JsonValue]


class SourceKind(StrEnum):
    UPLOAD = "upload"
    DATASET = "dataset"


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceReference:
    """Source provenance with defensively copied, mutable JSON objects.

    kind categorizes the original source; source_id identifies that source,
    not an extracted document. revision identifies its version when applicable.
    record_locator identifies a record or location within that source revision.
    Together these fields preserve occurrence provenance without a composite ID.
    display_name is a human-readable label; metadata holds extra description.

    External inputs must be validated before constructing this domain value.
    Frozen fields do not make the nested dictionaries and lists immutable.
    """

    kind: SourceKind
    source_id: str

    record_locator: JsonObject = field(default_factory=dict)
    metadata: JsonObject = field(default_factory=dict)
    display_name: str | None = None
    revision: str | None = None

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("Source ID cannot be empty")

        object.__setattr__(self, "record_locator", deepcopy(self.record_locator))
        object.__setattr__(self, "metadata", deepcopy(self.metadata))
