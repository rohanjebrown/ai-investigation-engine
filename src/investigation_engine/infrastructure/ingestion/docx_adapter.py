from pathlib import Path
from zipfile import BadZipFile

import docx
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml.etree import XMLSyntaxError

from investigation_engine.application.ingestion.extracted_document import ExtractedDocument
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.domain.structural_span import StructuralSpan, StructureKind
from investigation_engine.infrastructure.ingestion.extraction_errors import (
    DocumentExtractionError,
    DocumentLimitExceededError,
    InsufficientTextError,
)


def extract_docx(path: Path, *, source_id: str, max_text_chars: int | None = None) -> ExtractedDocument:
    """Main-body paragraphs/tables in XML order; no rendered page assumptions.

    Paragraphs/rows use LF, cells TAB, nested container blocks LF. Locators are
    hierarchical zero-based table/grid-row/grid-cell/paragraph coordinates.
    Empty paragraphs retain their position and separators but have no empty span.
    """
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("Source ID must be a nonblank stable identifier")
    parts, spans, structure = [], [], []
    offset = 0

    def emit(text):
        nonlocal offset
        if max_text_chars is not None and offset + len(text) > max_text_chars:
            raise DocumentLimitExceededError("DOCX extracted text exceeds the configured limit")
        parts.append(text)
        offset += len(text)

    def walk(container, prefix=()):
        paragraph_index = table_index = 0
        for block_index, block in enumerate(container.iter_inner_content()):
            if block_index:
                emit("\n")
            if isinstance(block, Paragraph):
                start = offset
                emit(block.text)
                if offset > start:
                    spans.append(
                        SourceSpan(
                            start=start,
                            end=offset,
                            locators=prefix + (SourceLocator(kind=LocatorKind.PARAGRAPH, index=paragraph_index),),
                        )
                    )
                    structure.append(StructuralSpan(kind=StructureKind.PARAGRAPH, start=start, end=offset))
                paragraph_index += 1
            elif isinstance(block, Table):
                start = offset
                structure_position = len(structure)
                seen = set()
                for row_index, row in enumerate(block.rows):
                    if row_index:
                        emit("\n")
                    for cell_index, cell in enumerate(row.cells, row.grid_cols_before):
                        if cell_index > row.grid_cols_before:
                            emit("\t")
                        # python-docx repeats merged cells in its grid view.
                        if cell._tc in seen:
                            continue
                        seen.add(cell._tc)
                        walk(
                            cell,
                            prefix
                            + (
                                SourceLocator(kind=LocatorKind.TABLE, index=table_index),
                                SourceLocator(kind=LocatorKind.ROW, index=row_index),
                                SourceLocator(kind=LocatorKind.CELL, index=cell_index),
                            ),
                        )
                if offset > start:
                    structure.insert(
                        structure_position, StructuralSpan(kind=StructureKind.TABLE, start=start, end=offset)
                    )
                table_index += 1

    with path.open("rb") as stream:
        try:
            document = docx.Document(stream)
            metadata = {}
            # Accessing core_properties when absent synthesizes default values.
            # Preserve absence instead of reporting those defaults as source metadata.
            if any(rel.reltype == RELATIONSHIP_TYPE.CORE_PROPERTIES for rel in document.part.package.rels.values()):
                properties = document.core_properties
                metadata = {
                    key: getattr(properties, key)
                    for key in (
                        "title",
                        "subject",
                        "author",
                        "keywords",
                        "comments",
                        "category",
                        "language",
                        "last_modified_by",
                        "revision",
                    )
                }
                for key in ("created", "modified", "last_printed"):
                    value = getattr(properties, key)
                    if value is not None:
                        metadata[key] = value.isoformat()
            walk(document)
        except (BadZipFile, XMLSyntaxError, KeyError, ValueError, TypeError, AttributeError, IndexError) as error:
            raise DocumentExtractionError(f"DOCX {path.name!r}: invalid or unsupported document package") from error
    text = "".join(parts)
    if not text.strip():
        raise InsufficientTextError(f"DOCX {path.name!r}: no extractable body text; image extraction is unsupported")
    title = metadata.get("title")
    return ExtractedDocument(
        text=text,
        title=title if title and title.strip() else path.name,
        source=SourceReference(
            kind=SourceKind.UPLOAD,
            source_id=source_id,
            display_name=path.name,
            metadata={
                "format": "docx",
                "filename": path.name,
                "core_properties": metadata,
                "extractor": "python-docx",
                "extractor_version": docx.__version__,
            },
        ),
        structure=tuple(structure),
        source_spans=tuple(spans),
    )
