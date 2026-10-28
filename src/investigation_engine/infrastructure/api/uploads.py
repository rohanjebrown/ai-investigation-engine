"""Bounded, temporary-file based upload validation and extraction."""

import asyncio
import io
import zipfile
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from fastapi import HTTPException
from starlette.datastructures import UploadFile

from investigation_engine.application.ingestion.extracted_document import ExtractedDocument
from investigation_engine.infrastructure.ingestion.docx_adapter import extract_docx
from investigation_engine.infrastructure.ingestion.extraction_errors import (
    DocumentExtractionError, DocumentLimitExceededError, UnsupportedDocumentError,
)
from investigation_engine.infrastructure.ingestion.pdf_adapter import extract_pdf
from investigation_engine.infrastructure.ingestion.txt_adapter import extract_txt

if TYPE_CHECKING:
    from investigation_engine.infrastructure.config import Settings


def safe_filename(value: str | None) -> tuple[str, str]:
    if not isinstance(value, str) or not value or len(value) > 180 or any(ord(c) < 32 for c in value):
        raise HTTPException(422, detail={"code": "invalid_filename", "message": "A valid filename is required."})
    name = value.replace("\\", "/").rsplit("/", 1)[-1]
    if not name or name in (".", ".."):
        raise HTTPException(422, detail={"code": "invalid_filename", "message": "A valid filename is required."})
    suffix = Path(name).suffix.casefold()
    if suffix not in (".txt", ".pdf", ".docx"):
        raise HTTPException(415, detail={"code": "unsupported_format", "message": "Upload a .txt, .pdf or .docx file."})
    return name, suffix


def _check_docx(data: bytes, max_uncompressed: int) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 5000 or sum(entry.file_size for entry in entries) > max_uncompressed:
                raise DocumentLimitExceededError("DOCX package exceeds the configured expansion limit")
            if any(entry.flag_bits & 1 or entry.file_size > max(1_000_000, entry.compress_size * 100)
                   for entry in entries):
                raise DocumentLimitExceededError("DOCX package contains unsafe compressed entries")
            names = {entry.filename for entry in entries}
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise UnsupportedDocumentError("DOCX package is missing required document parts")
    except zipfile.BadZipFile as error:
        raise UnsupportedDocumentError("DOCX package is malformed") from error


async def extract_upload(upload: UploadFile, *, source_id: str, settings: "Settings") -> ExtractedDocument:
    try:
        name, suffix = safe_filename(upload.filename)
        data = await upload.read(settings.max_upload_bytes + 1)
    finally:
        await upload.close()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, detail={"code": "upload_too_large", "message": "Upload exceeds the configured byte limit."})
    if not data:
        raise HTTPException(422, detail={"code": "empty_upload", "message": "The uploaded file is empty."})
    if suffix == ".txt":
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise HTTPException(422, detail={"code": "invalid_document", "message": "TXT files must contain valid UTF-8."}) from None
        if "\x00" in text:
            raise HTTPException(422, detail={"code": "invalid_document", "message": "TXT files cannot contain NUL characters."})
        if len(text) > settings.max_extracted_text_chars:
            raise HTTPException(413, detail={"code": "text_too_large", "message": "Extracted text exceeds the configured limit."})
    elif suffix == ".pdf" and not data.startswith(b"%PDF-"):
        raise HTTPException(422, detail={"code": "invalid_document", "message": "PDF signature is invalid."})
    elif suffix == ".docx":
        try:
            _check_docx(data, settings.max_docx_uncompressed_bytes)
        except DocumentLimitExceededError:
            raise HTTPException(413, detail={"code": "document_limit", "message": "DOCX package exceeds configured limits."}) from None
        except DocumentExtractionError:
            raise HTTPException(422, detail={"code": "invalid_document", "message": "DOCX package is invalid."}) from None

    try:
        with TemporaryDirectory(prefix="investigation-upload-") as directory:
            path = Path(directory) / name
            path.write_bytes(data)
            if suffix == ".txt":
                extracted = await asyncio.to_thread(extract_txt, path, source_id=source_id,
                                                     max_text_chars=settings.max_extracted_text_chars)
            elif suffix == ".pdf":
                extracted = await asyncio.to_thread(extract_pdf, path, source_id=source_id,
                    max_pages=settings.max_pdf_pages, max_text_chars=settings.max_extracted_text_chars)
            else:
                extracted = await asyncio.to_thread(extract_docx, path, source_id=source_id,
                                                     max_text_chars=settings.max_extracted_text_chars)
    except DocumentLimitExceededError:
        raise HTTPException(413, detail={"code": "document_limit", "message": "Document exceeds configured extraction limits."}) from None
    except UnsupportedDocumentError:
        raise HTTPException(422, detail={"code": "invalid_document", "message": "This document is encrypted or unsupported."}) from None
    except DocumentExtractionError:
        raise HTTPException(422, detail={"code": "invalid_document", "message": "Document contents could not be extracted."}) from None
    source = replace(extracted.source, display_name=name,
                     metadata={**extracted.source.metadata, "filename": name, "format": suffix[1:]})
    return replace(extracted, source=source, title=extracted.title or name)
