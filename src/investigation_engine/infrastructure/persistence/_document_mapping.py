"""Private, synchronous translations at the PostgreSQL persistence boundary."""

import json
from dataclasses import replace
from math import isfinite

from investigation_engine.domain.content import Content
from investigation_engine.domain.document import Document
from investigation_engine.domain.document_version import DocumentVersion
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.domain.structural_span import StructuralSpan, StructureKind
from investigation_engine.infrastructure.persistence.models import (
    ContentRow,
    DocumentRow,
    DocumentVersionRow,
)


def _snapshot(
    document: Document, version: DocumentVersion,
) -> tuple[Document, DocumentVersion]:
    document = replace(document)
    version = replace(version, source=replace(version.source))
    if version.document_id != document.id:
        raise ValueError("DocumentVersion references a different document")
    _validate_json_object(version.source.record_locator)
    _validate_json_object(version.source.metadata)
    return document, version


def _validate_json_object(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("Source locator and metadata must be JSON objects")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("Source values must contain finite, acyclic JSON") from error
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if not all(isinstance(key, str) for key in item):
                raise ValueError("JSON object keys must be strings")
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, float):
            if not isfinite(item):
                raise ValueError("JSON numbers must be finite")
        elif item is not None and not isinstance(item, (str, bool, int)):
            raise ValueError("Source values must be JSON-compatible")


def _version_row(version: DocumentVersion) -> DocumentVersionRow:
    return DocumentVersionRow(
        id=version.id,
        document_id=version.document_id,
        version_number=version.version_number,
        content_sha256=bytes.fromhex(version.content.sha256),
        created_at=version.created_at,
        kind=version.source.kind,
        source_id=version.source.source_id,
        revision=version.source.revision,
        display_name=version.source.display_name,
        record_locator=version.source.record_locator,
        source_metadata=version.source.metadata,
        structure=[
            {"kind": span.kind, "start": span.start, "end": span.end}
            for span in version.structure
        ],
        source_spans=[
            {
                "start": span.start, "end": span.end,
                "locators": [
                    {"kind": locator.kind, "index": locator.index}
                    for locator in span.locators
                ],
            }
            for span in version.source_spans
        ],
    )


def _content(row: ContentRow) -> Content:
    content = Content(text=row.text)
    if bytes.fromhex(content.sha256) != row.sha256:
        raise ValueError("Stored Content digest is inconsistent")
    return content


def _domain_registration(
    document: DocumentRow, version: DocumentVersionRow, content: Content,
) -> tuple[Document, DocumentVersion]:
    _validate_json_object(version.record_locator)
    _validate_json_object(version.source_metadata)
    structure = tuple(
        StructuralSpan(**{**span, "kind": StructureKind(span["kind"])})
        for span in version.structure
    )
    source_spans = tuple(
        SourceSpan(**{
            **span,
            "locators": tuple(
                SourceLocator(**{**locator, "kind": LocatorKind(locator["kind"])})
                for locator in span["locators"]
            ),
        })
        for span in version.source_spans
    )
    return (
        Document(
            id=document.id, owner_id=document.owner_id, title=document.title,
            current_version_id=document.current_version_id,
            created_at=document.created_at, deleted_at=document.deleted_at,
        ),
        DocumentVersion(
            id=version.id, document_id=version.document_id,
            version_number=version.version_number, content=content,
            created_at=version.created_at,
            source=SourceReference(
                kind=SourceKind(version.kind), source_id=version.source_id,
                revision=version.revision, display_name=version.display_name,
                record_locator=version.record_locator,
                metadata=version.source_metadata,
            ),
            structure=structure, source_spans=source_spans,
        ),
    )
