from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from investigation_engine.application.ports.repository_errors import (
    ContentHashCollision,
    DocumentAccessDenied,
    DocumentNotFound,
    DocumentUpdateConflict,
    RegistrationConflict,
)
from investigation_engine.application.document_views import DocumentDetails, DocumentListItem, DocumentPage
from investigation_engine.domain.content import Content
from investigation_engine.domain.document import Document
from investigation_engine.domain.document_version import DocumentVersion
from investigation_engine.domain.provenance import JsonValue
from investigation_engine.domain.provenance import SourceReference
from investigation_engine.domain.source_span import SourceSpan
from investigation_engine.domain.structural_span import StructuralSpan


def _locator_key(value: JsonValue) -> tuple:
    """Private lookup representation, not a source ID or domain value.

    Decimal text conversion matches JSON number values without introducing
    binary-float artifacts; booleans remain a separate JSON type.
    """
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float)):
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError("Source locator numbers must be finite")
        return ("number", number)
    if isinstance(value, str):
        return ("string", value)
    if isinstance(value, list):
        return ("array", tuple(_locator_key(item) for item in value))
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("Source locator object keys must be strings")
        return (
            "object",
            tuple((key, _locator_key(value[key])) for key in sorted(value)),
        )
    raise ValueError("Source locator must contain JSON-compatible values")


def _copy_registration(
    document: Document,
    version: DocumentVersion,
) -> tuple[Document, DocumentVersion]:
    # SourceReference construction defensively copies its JSON dictionaries.
    # Content and span tuples are immutable and can remain shared.
    return replace(document), replace(version, source=replace(version.source))


class InMemoryDocumentRepository:
    """Sequential registration within this instance; no durability or locking.

    Different source revisions have separate identities. They are not
    automatically linked as versions of one Document; updates are unsupported.
    """

    def __init__(self) -> None:
        self._documents: dict[UUID, Document] = {}
        self._versions: dict[UUID, DocumentVersion] = {}
        self._contents: dict[str, Content] = {}
        self._occurrences: dict[tuple, UUID] = {}

    async def register(
        self,
        document: Document,
        version: DocumentVersion,
    ) -> tuple[Document, DocumentVersion]:
        document, version = _copy_registration(document, version)

        if version.document_id != document.id:
            raise ValueError(
                "DocumentVersion references a different document"
            )

        source = version.source
        occurrence_key = (
            source.kind,
            source.source_id,
            source.revision,
            _locator_key(source.record_locator),
        )
        existing_version_id = self._occurrences.get(occurrence_key)
        if existing_version_id is not None:
            existing_version = self._versions[existing_version_id]
            existing_document = self._documents[existing_version.document_id]
            if existing_document.owner_id != document.owner_id:
                raise PermissionError("Registration is not permitted")
            conflict = None
            if existing_version.content.text != version.content.text:
                conflict = "extracted text differs"
            elif (
                existing_version.structure != version.structure
                or existing_version.source_spans != version.source_spans
            ):
                conflict = "structural or source spans differ"
            if conflict is not None:
                raise RegistrationConflict(
                    f"Registration conflict for {source.kind!s} source "
                    f"{source.source_id!r}, revision {source.revision!r}, "
                    f"locator {source.record_locator!r}: {conflict}"
                )
            return _copy_registration(
                existing_document, existing_version
            )

        if version.version_number != 1 or document.current_version_id is not None:
            raise ValueError("New registrations require version 1 and no active version")

        if document.id in self._documents:
            raise ValueError("Document already exists")

        if version.id in self._versions:
            raise ValueError("Document version already exists")

        content = version.content

        existing_content = self._contents.get(content.sha256)

        if existing_content is not None:
            if existing_content.text != content.text:
                raise ContentHashCollision("Content hash collision detected")

            version = replace(
                version,
                content=existing_content,
            )
        stored_document, stored_version = _copy_registration(document, version)
        result = _copy_registration(stored_document, stored_version)
        self._contents[content.sha256] = stored_version.content
        self._documents[stored_document.id] = stored_document
        self._versions[stored_version.id] = stored_version
        self._occurrences[occurrence_key] = stored_version.id

        return result

    async def append_version(
        self, document_id: UUID, *, actor_id: str, expected_current_version_id: UUID,
        content: Content, source: SourceReference,
        structure: tuple[StructuralSpan, ...] = (),
        source_spans: tuple[SourceSpan, ...] = (),
    ) -> tuple[Document, DocumentVersion]:
        document = self._documents.get(document_id)
        self._authorize_mutation(document, actor_id)
        if document.deleted_at is not None:
            raise DocumentUpdateConflict("Deleted documents cannot be updated")
        if not isinstance(source.revision, str) or not source.revision.strip():
            raise DocumentUpdateConflict("A document update requires an explicit nonblank source revision")
        key = (source.kind, source.source_id, source.revision, _locator_key(source.record_locator))
        existing_id = self._occurrences.get(key)
        if existing_id is not None:
            existing = self._versions[existing_id]
            if existing.document_id != document_id:
                raise RegistrationConflict("Source revision is already registered to another document")
            if existing.content.text != content.text:
                raise RegistrationConflict("Registration conflict: extracted text differs")
            if existing.structure != structure or existing.source_spans != source_spans:
                raise RegistrationConflict("Registration conflict: structural or source spans differ")
            return _copy_registration(document, existing)

        if document.current_version_id != expected_current_version_id:
            raise DocumentUpdateConflict("The active document version changed; reload before updating")
        if document.current_version_id is None:
            raise DocumentUpdateConflict("A document must have an active version before it can be updated")
        active = self._versions.get(document.current_version_id)
        if active is None or active.document_id != document_id:
            raise DocumentUpdateConflict("The active document version could not be resolved")
        if (
            source.kind != active.source.kind
            or source.source_id != active.source.source_id
            or _locator_key(source.record_locator) != _locator_key(active.source.record_locator)
        ):
            raise DocumentUpdateConflict(
                "An update must retain the document's source kind, source ID, and record locator"
            )
        versions = [v for v in self._versions.values() if v.document_id == document_id]
        latest = max(v.version_number for v in versions)
        if latest > active.version_number:
            raise DocumentUpdateConflict(
                "Another document update is pending indexing; retry it or resolve it before starting another"
            )
        existing_content = self._contents.get(content.sha256)
        if existing_content is not None and existing_content.text != content.text:
            raise ContentHashCollision("Content hash collision detected")
        saved_content = existing_content or content
        version = DocumentVersion(
            document_id=document_id, version_number=latest + 1, content=saved_content,
            source=source, structure=structure, source_spans=source_spans,
        )
        stored = _copy_registration(document, version)[1]
        self._contents[content.sha256] = saved_content
        self._versions[stored.id] = stored
        self._occurrences[key] = stored.id
        return _copy_registration(document, stored)

    async def soft_delete(self, document_id: UUID, *, actor_id: str) -> Document:
        document = self._documents.get(document_id)
        self._authorize_mutation(document, actor_id)
        if document.deleted_at is None:
            document.deleted_at = datetime.now(UTC)
        return replace(document)

    async def get_details(self, document_id: UUID, *, actor_id: str) -> DocumentDetails:
        document = self._documents.get(document_id)
        self._authorize_read(document, actor_id)
        versions = tuple(sorted(
            (value for value in self._versions.values() if value.document_id == document_id),
            key=lambda value: value.version_number,
        ))
        return DocumentDetails(
            document=replace(document),
            versions=tuple(_copy_registration(document, version)[1] for version in versions),
        )

    async def list_documents(
        self, *, actor_id: str, offset: int, limit: int, include_deleted: bool = False,
    ) -> DocumentPage:
        documents = [
            document for document in self._documents.values()
            if (document.owner_id is None or document.owner_id == actor_id)
            and (include_deleted or document.deleted_at is None)
        ]
        documents.sort(key=lambda item: (item.created_at, str(item.id)), reverse=True)
        total = len(documents)
        items = []
        for document in documents[offset:offset + limit]:
            versions = [v for v in self._versions.values() if v.document_id == document.id]
            current = next((v for v in versions if v.id == document.current_version_id), None)
            copied_doc = replace(document)
            copied_current = _copy_registration(document, current)[1] if current is not None else None
            items.append(DocumentListItem(copied_doc, copied_current, len(versions)))
        return DocumentPage(tuple(items), total, offset, limit)

    @staticmethod
    def _authorize_mutation(document: Document | None, actor_id: str) -> None:
        if document is None:
            raise DocumentNotFound("Document was not found")
        if document.owner_id is None or document.owner_id != actor_id:
            raise DocumentAccessDenied("Document access is not permitted")

    @staticmethod
    def _authorize_read(document: Document | None, actor_id: str) -> None:
        if document is None:
            raise DocumentNotFound("Document was not found")
        if document.owner_id is not None and document.owner_id != actor_id:
            raise DocumentAccessDenied("Document access is not permitted")
