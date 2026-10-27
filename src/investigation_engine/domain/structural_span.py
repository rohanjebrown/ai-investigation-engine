from dataclasses import dataclass
from enum import StrEnum


class StructureKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    SENTENCE = "sentence"
    TABLE = "table"
    LIST_ITEM = "list_item"


@dataclass(frozen=True, slots=True, kw_only=True)
class StructuralSpan:
    kind: StructureKind
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ValueError("Start offset cannot be negative")

        if self.end <= self.start:
            raise ValueError(
                "End offset must be greater than start offset"
            )