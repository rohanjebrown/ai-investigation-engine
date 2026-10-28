from pathlib import Path

import pypdf

from investigation_engine.application.ingestion.extracted_document import ExtractedDocument
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.infrastructure.ingestion.extraction_errors import (
    DocumentExtractionError, DocumentLimitExceededError, InsufficientTextError, UnsupportedDocumentError,
)


def extract_pdf(
    path: Path, *, source_id: str, max_pages: int | None = None,
    max_text_chars: int | None = None,
) -> ExtractedDocument:
    """Plain PDF text, two newlines between readable pages, original 1-based pages.

    Preserve parser text verbatim; inserted separators belong to no page span.
    No OCR, passwords, repair fallback or partial success after a parser failure.
    """
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("Source ID must be a nonblank stable identifier")
    parts, spans, empty_pages = [], [], []
    offset = 0
    with path.open("rb") as stream:
        try:
            reader = pypdf.PdfReader(stream, strict=True)
            if reader.is_encrypted:
                raise UnsupportedDocumentError(f"PDF {path.name!r}: encrypted documents are unsupported")
            metadata = reader.metadata or {}
            fields = {name: str(metadata[key]) for name, key in (
                ("title", "/Title"), ("author", "/Author"), ("subject", "/Subject"),
                ("keywords", "/Keywords"), ("creator", "/Creator"), ("producer", "/Producer"),
                ("creation_date", "/CreationDate"), ("modification_date", "/ModDate"),
            ) if isinstance(metadata.get(key), str)}
            page_count = len(reader.pages)
            if max_pages is not None and page_count > max_pages:
                raise DocumentLimitExceededError("PDF page count exceeds the configured limit")
            for number, page in enumerate(reader.pages, 1):
                text = page.extract_text(extraction_mode="plain") or ""
                if not text.strip():
                    empty_pages.append(number)
                    continue
                next_length = offset + (2 if parts else 0) + len(text)
                if max_text_chars is not None and next_length > max_text_chars:
                    raise DocumentLimitExceededError("PDF extracted text exceeds the configured limit")
                if parts:
                    offset += 2
                spans.append(SourceSpan(start=offset, end=offset + len(text),
                    locators=(SourceLocator(kind=LocatorKind.PAGE, index=number),)))
                parts.append(text)
                offset += len(text)
        except DocumentExtractionError:
            raise
        except (pypdf.errors.PdfReadError, ValueError, TypeError, KeyError, IndexError, NotImplementedError) as error:
            raise DocumentExtractionError(f"PDF {path.name!r}: invalid or unsupported PDF content") from error
    if not parts:
        raise InsufficientTextError(f"PDF {path.name!r}: no extractable text; OCR is not supported")
    title = fields.get("title")
    return ExtractedDocument(text="\n\n".join(parts), title=title if title and title.strip() else path.name,
        source=SourceReference(kind=SourceKind.UPLOAD, source_id=source_id, display_name=path.name,
            metadata={"format": "pdf", "filename": path.name, "pdf": fields,
                      "page_count": page_count, "empty_pages": empty_pages,
                      "extractor": "pypdf", "extractor_version": pypdf.__version__}),
        source_spans=tuple(spans))
