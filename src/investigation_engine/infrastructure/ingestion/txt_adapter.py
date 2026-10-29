from pathlib import Path

from investigation_engine.application.ingestion.extracted_document import (
    ExtractedDocument,
)
from investigation_engine.domain.provenance import (
    SourceKind,
    SourceReference,
)
from investigation_engine.domain.source_span import (
    LocatorKind,
    SourceLocator,
    SourceSpan,
)
from investigation_engine.infrastructure.ingestion.extraction_errors import DocumentLimitExceededError


def extract_txt(path: Path, *, source_id: str, max_text_chars: int | None = None) -> ExtractedDocument:
    with path.open("r", encoding="utf-8", newline="") as file:
        text = file.read()
    if max_text_chars is not None and len(text) > max_text_chars:
        raise DocumentLimitExceededError("TXT extracted text exceeds the configured limit")

    source = SourceReference(
        kind=SourceKind.UPLOAD,
        source_id=source_id,
        display_name=path.name,
    )

    source_spans = []
    offset = 0

    for line_number, line in enumerate(
        text.splitlines(keepends=True),
        start=1,
    ):
        end = offset + len(line)

        source_spans.append(
            SourceSpan(
                start=offset,
                end=end,
                locators=(
                    SourceLocator(
                        kind=LocatorKind.LINE,
                        index=line_number,
                    ),
                ),
            )
        )

        offset = end

    return ExtractedDocument(
        text=text,
        title=path.name,
        source=source,
        source_spans=tuple(source_spans),
    )
