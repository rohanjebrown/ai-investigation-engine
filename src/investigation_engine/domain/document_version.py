from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid7

from investigation_engine.domain.content import Content
from investigation_engine.domain.provenance import SourceReference
from investigation_engine.domain.source_span import SourceSpan
from investigation_engine.domain.structural_span import StructuralSpan


@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentVersion:
    document_id: UUID
    version_number: int
    content: Content
    source: SourceReference

    structure: tuple[StructuralSpan, ...] = ()
    source_spans: tuple[SourceSpan, ...] = ()

    id: UUID = field(default_factory=uuid7)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )

    def __post_init__(self) -> None:
        if self.version_number < 1:
            raise ValueError(
                "Document version number must be positive"
            )

        text_length = len(self.content.text)

        for span in (*self.structure, *self.source_spans):
            if span.end > text_length:
                raise ValueError(
                    f"Span [{span.start}, {span.end}) exceeds "
                    f"content length {text_length}"
                )