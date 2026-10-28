"""Small application read models for document management."""

from dataclasses import dataclass

from investigation_engine.domain.document import Document
from investigation_engine.domain.document_version import DocumentVersion


@dataclass(frozen=True, slots=True)
class DocumentListItem:
    document: Document
    current_version: DocumentVersion | None
    version_count: int


@dataclass(frozen=True, slots=True)
class DocumentPage:
    items: tuple[DocumentListItem, ...]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class DocumentDetails:
    document: Document
    versions: tuple[DocumentVersion, ...]
