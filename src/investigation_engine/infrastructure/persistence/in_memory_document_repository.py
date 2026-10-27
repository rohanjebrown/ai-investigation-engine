from dataclasses import replace
from decimal import Decimal
from uuid import UUID

from investigation_engine.application.ports.repository_errors import (
    ContentHashCollision,
    RegistrationConflict,
)
from investigation_engine.domain.content import Content
from investigation_engine.domain.document import Document
from investigation_engine.domain.document_version import DocumentVersion
from investigation_engine.domain.provenance import JsonValue


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
