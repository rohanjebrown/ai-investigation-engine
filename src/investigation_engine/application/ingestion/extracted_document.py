from dataclasses import dataclass

from investigation_engine.domain.provenance import SourceReference
from investigation_engine.domain.source_span import SourceSpan
from investigation_engine.domain.structural_span import StructuralSpan


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtractedDocument:
    text: str
    source: SourceReference

    title: str | None = None
    structure: tuple[StructuralSpan, ...] = ()
    source_spans: tuple[SourceSpan, ...] = ()
