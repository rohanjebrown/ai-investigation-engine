"""Bounded, temporary-file based upload validation and extraction."""

import asyncio
import io
import zipfile
from dataclasses import replace
from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from fastapi import HTTPException
from starlette.datastructures import UploadFile

from investigation_engine.application.ingestion.extracted_document import ExtractedDocument
from investigation_engine.infrastructure.ingestion.docx_adapter import extract_docx
from investigation_engine.infrastructure.ingestion.extraction_errors import (
    DocumentExtractionError,
    DocumentLimitExceededError,
    UnsupportedDocumentError,
)
from investigation_engine.infrastructure.ingestion.pdf_adapter import extract_pdf
from investigation_engine.infrastructure.ingestion.txt_adapter import extract_txt

if TYPE_CHECKING:
    from investigation_engine.infrastructure.config import Settings


MAX_UPLOAD_FILENAME_CHARS = 180
MAX_UNICODE_CONTROL_CODEPOINT = 31
MAX_DOCX_ARCHIVE_ENTRIES = 5000
MIN_DOCX_SIZE_FOR_RATIO_LIMIT = 1_000_000
MAX_DOCX_EXPANSION_RATIO = 100


def safe_filename(filename: str | None) -> tuple[str, str]:
    if not isinstance(filename, str) or not filename or len(filename) > MAX_UPLOAD_FILENAME_CHARS:
        raise HTTPException(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_filename", "message": "A valid filename is required."},
        )
    if any(ord(character) <= MAX_UNICODE_CONTROL_CODEPOINT for character in filename):
        raise HTTPException(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_filename", "message": "A valid filename is required."},
        )
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if not name or name in (".", ".."):
        raise HTTPException(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_filename", "message": "A valid filename is required."},
        )
    suffix = Path(name).suffix.casefold()
    if suffix not in (".txt", ".pdf", ".docx"):
        raise HTTPException(
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            detail={"code": "unsupported_format", "message": "Upload a .txt, .pdf or .docx file."},
        )
    return name, suffix


def _check_docx(document_bytes: bytes, max_uncompressed: int) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(document_bytes)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_DOCX_ARCHIVE_ENTRIES or sum(entry.file_size for entry in entries) > max_uncompressed:
                raise DocumentLimitExceededError("DOCX package exceeds the configured expansion limit")
            if any(
                entry.flag_bits & 1
                or entry.file_size > max(MIN_DOCX_SIZE_FOR_RATIO_LIMIT, entry.compress_size * MAX_DOCX_EXPANSION_RATIO)
                for entry in entries
            ):
                raise DocumentLimitExceededError("DOCX package contains unsafe compressed entries")
            names = {entry.filename for entry in entries}
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise UnsupportedDocumentError("DOCX package is missing required document parts")
    except zipfile.BadZipFile as error:
        raise UnsupportedDocumentError("DOCX package is malformed") from error


def _invalid_upload(code: str, message: str, status: HTTPStatus = HTTPStatus.UNPROCESSABLE_ENTITY) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


def _validate_upload_content(document_bytes: bytes, suffix: str, settings: "Settings") -> None:
    if len(document_bytes) > settings.max_upload_bytes:
        raise HTTPException(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "upload_too_large", "message": "Upload exceeds the configured byte limit."},
        )
    if not document_bytes:
        raise _invalid_upload("empty_upload", "The uploaded file is empty.")
    if suffix == ".txt":
        _validate_text_upload(document_bytes, settings.max_extracted_text_chars)
    elif suffix == ".pdf" and not document_bytes.startswith(b"%PDF-"):
        raise _invalid_upload("invalid_document", "PDF signature is invalid.")
    elif suffix == ".docx":
        _validate_docx_upload(document_bytes, settings.max_docx_uncompressed_bytes)


def _validate_text_upload(document_bytes: bytes, max_text_chars: int) -> None:
    try:
        text = document_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _invalid_upload("invalid_document", "TXT files must contain valid UTF-8.") from None
    if "\x00" in text:
        raise _invalid_upload("invalid_document", "TXT files cannot contain NUL characters.")
    if len(text) > max_text_chars:
        raise HTTPException(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "text_too_large", "message": "Extracted text exceeds the configured limit."},
        )


def _validate_docx_upload(document_bytes: bytes, max_uncompressed: int) -> None:
    try:
        _check_docx(document_bytes, max_uncompressed)
    except DocumentLimitExceededError:
        raise HTTPException(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "document_limit", "message": "DOCX package exceeds configured limits."},
        ) from None
    except DocumentExtractionError:
        raise _invalid_upload("invalid_document", "DOCX package is invalid.") from None


def _extract_document(path: Path, suffix: str, source_id: str, settings: "Settings") -> ExtractedDocument:
    if suffix == ".txt":
        return extract_txt(path, source_id=source_id, max_text_chars=settings.max_extracted_text_chars)
    if suffix == ".pdf":
        return extract_pdf(
            path,
            source_id=source_id,
            max_pages=settings.max_pdf_pages,
            max_text_chars=settings.max_extracted_text_chars,
        )
    return extract_docx(path, source_id=source_id, max_text_chars=settings.max_extracted_text_chars)


async def extract_upload(upload: UploadFile, *, source_id: str, settings: "Settings") -> ExtractedDocument:
    try:
        name, suffix = safe_filename(upload.filename)
        document_bytes = await upload.read(settings.max_upload_bytes + 1)
    finally:
        await upload.close()
    _validate_upload_content(document_bytes, suffix, settings)

    try:
        with TemporaryDirectory(prefix="investigation-upload-") as directory:
            path = Path(directory) / name
            path.write_bytes(document_bytes)
            extracted = await asyncio.to_thread(_extract_document, path, suffix, source_id, settings)
    except DocumentLimitExceededError:
        raise HTTPException(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "document_limit", "message": "Document exceeds configured extraction limits."},
        ) from None
    except UnsupportedDocumentError:
        raise HTTPException(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_document", "message": "This document is encrypted or unsupported."},
        ) from None
    except DocumentExtractionError:
        raise HTTPException(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_document", "message": "Document contents could not be extracted."},
        ) from None
    source = replace(
        extracted.source,
        display_name=name,
        metadata={**extracted.source.metadata, "filename": name, "format": suffix[1:]},
    )
    return replace(extracted, source=source, title=extracted.title or name)
