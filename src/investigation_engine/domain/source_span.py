from dataclasses import dataclass
from enum import StrEnum


class LocatorKind(StrEnum):
    PAGE = "page"
    PARAGRAPH = "paragraph"
    SENTENCE = "sentence"
    LINE = "line"
    TABLE = "table"
    ROW = "row"
    CELL = "cell"


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceLocator:
    kind: LocatorKind
    index: int

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("Source location index cannot be negative")


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceSpan:
    start: int
    end: int
    locators: tuple[SourceLocator, ...]

    def __post_init__(self) -> None:
        if self.start < 0:
            raise ValueError("Start offset cannot be negative")

        if self.end <= self.start:
            raise ValueError(
                "End offset must be greater than start offset"
            )

        if not self.locators:
            raise ValueError(
                "SourceSpan requires at least one source locator"
            )
