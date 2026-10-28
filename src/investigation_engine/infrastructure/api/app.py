"""Shared-corpus API with private scope available only to a verified administrator."""

import asyncio
import hmac
from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import FastAPI, Request, UploadFile, HTTPException, Query, Header
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from investigation_engine.application.answers import (
    MAX_ANSWER_CHARS, MAX_QUESTION_CHARS, ResolvedCitation, validate_question,
)
from investigation_engine.application.ports.embedding_errors import (
    EmbeddingInputError, EmbeddingProviderError, EmbeddingRateLimitError, EmbeddingResponseError,
)
from investigation_engine.application.ports.generation_errors import (
    EvidenceContextLimitError, GenerationProviderError, GenerationResponseError, GenerationTimeoutError,
)
from investigation_engine.application.ports.repository_errors import (
    ActivationConflict, ContentHashCollision, DocumentAccessDenied, DocumentNotFound,
    DocumentUpdateConflict, RegistrationConflict, RepositoryError,
)
from investigation_engine.application.use_cases.answer_question import AnswerQuestion
from investigation_engine.application.document_access import DocumentActor
from investigation_engine.infrastructure.api.bootstrap import application_service
from investigation_engine.infrastructure.api.uploads import extract_upload
from investigation_engine.infrastructure.ingestion.extraction_errors import DocumentExtractionError
from investigation_engine.domain.provenance import SourceReference


class QuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)

    @field_validator("question")
    @classmethod
    def valid_question(cls, value):
        validate_question(value)
        return value


class QuestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["answered", "insufficient_evidence"]
    answer: str | None = Field(max_length=MAX_ANSWER_CHARS)
    citations: tuple[ResolvedCitation, ...]
    message: str | None

    @model_validator(mode="after")
    def consistent_outcome(self):
        if self.status == "answered":
            if not self.answer or not self.answer.strip() or not self.citations or self.message is not None:
                raise ValueError("An answer requires citations")
        elif self.answer is not None or self.citations or not self.message:
            raise ValueError("Abstention cannot contain an answer")
        return self


class InvestigationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["answered", "insufficient_evidence", "conflicting_evidence", "incomplete"]
    answer: str | None
    citations: tuple[str, ...]
    conflicts: tuple[dict[str, Any], ...]
    evidence: tuple[dict[str, Any], ...]
    searches: tuple[dict[str, Any], ...]
    generation_requests: int
    embedding_requests: int
    message: str | None
    model_version: str | None
    usage: dict[str, int]


def _authorized_actor(request: Request) -> DocumentActor:
    services = request.app.state.services
    config = getattr(services, "settings", None)
    header = request.headers.get("authorization", "")
    if config is None or not config.admin_api_token:
        if header:
            raise HTTPException(401, detail={"code": "unauthorized", "message": "Administrator authentication is unavailable."}, headers={"WWW-Authenticate": "Bearer"})
        raise HTTPException(503, detail={"code": "admin_disabled", "message": "Document management is not configured."})
    if config.admin_require_https and request.url.scheme != "https":
        raise HTTPException(400, detail={"code": "https_required", "message": "Use HTTPS for administrator operations."})
    scheme, _, supplied = header.partition(" ")
    if scheme.casefold() != "bearer" or not supplied or not hmac.compare_digest(supplied, config.admin_api_token):
        raise HTTPException(401, detail={"code": "unauthorized", "message": "Administrator authentication is required."}, headers={"WWW-Authenticate": "Bearer"})
    return DocumentActor(config.admin_principal_id)


def _serial(value):
    if is_dataclass(value):
        return {key: _serial(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: _serial(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_serial(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value


class UploadBodyLimitMiddleware:
    """Enforce streamed request bounds even when Content-Length is absent."""
    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in ("POST", "PUT") or not scope.get("path", "").startswith("/documents"):
            await self.app(scope, receive, send)
            return
        limit = self.max_bytes + 65536
        headers = dict(scope.get("headers", ()))
        try:
            if int(headers.get(b"content-length", b"0")) > limit:
                await JSONResponse(status_code=413, content={"error": {"code": "upload_too_large", "message": "Request exceeds the configured upload limit."}})(scope, receive, send)
                return
        except ValueError:
            await JSONResponse(status_code=400, content={"error": {"code": "invalid_content_length", "message": "Invalid request length."}})(scope, receive, send)
            return
        total = 0
        async def bounded_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > limit:
                    raise _RequestTooLarge
            return message
        try:
            await self.app(scope, bounded_receive, send)
        except _RequestTooLarge:
            await JSONResponse(status_code=413, content={"error": {"code": "upload_too_large", "message": "Request exceeds the configured upload limit."}})(scope, receive, send)


class _RequestTooLarge(Exception):
    pass


def create_app(*, service_factory=application_service) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        async with service_factory() as service:
            if isinstance(service, AnswerQuestion):
                # Retain the existing test/integration injection contract.
                app.state.services = type("QuestionOnlyServices", (), {"questions": service, "settings": None})()
            else:
                app.state.services = service
            app.state.questions = getattr(app.state.services, "questions", None)
            try:
                yield
            finally:
                del app.state.questions
                del app.state.services

    app = FastAPI(title="AI Investigation Engine", lifespan=lifespan)
    # Actual per-file bytes are bounded again after multipart parsing.
    # Absolute cap is the highest configurable file limit plus multipart overhead.
    # The smaller per-file cap is applied after parsing from the active Settings.
    app.add_middleware(UploadBodyLimitMiddleware, max_bytes=50 * 1024 * 1024)
    web_root = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=web_root), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(web_root / "index.html", media_type="text/html", headers={
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        })

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        # Do not echo submitted bodies, unknown owner IDs or validation context.
        return JSONResponse(status_code=422, content={"error": {"code": "invalid_request", "message": "Request fields or parameters are invalid; check required values and limits."}})

    @app.get("/health")
    async def health():
        # Process liveness only; never invokes Gemini or probes the database.
        return {"status": "ok"}

    @app.exception_handler(ResponseValidationError)
    async def invalid_response(request, error):
        return JSONResponse(status_code=500, content={"error": {"code": "internal_error", "message": "The question could not be completed."}})

    @app.post("/questions", response_model=QuestionResponse)
    async def questions(body: QuestionRequest, request: Request):
        service = request.app.state.services.questions
        auth = request.headers.get("authorization")
        owner_id = None
        if auth is not None:
            actor = _authorized_actor(request)
            owner_id = actor.principal_id
        try:
            async with asyncio.timeout(90):
                result = await service.execute(body.question, owner_id=owner_id)
            # Keep evaluation telemetry and unselected retrieval context internal.
            return QuestionResponse(
                status=result.status.value, answer=result.answer,
                citations=result.citations, message=result.message,
            )
        except (TimeoutError, GenerationTimeoutError):
            status, code = 504, "request_timeout"
        except (EmbeddingRateLimitError, EmbeddingProviderError, RepositoryError):
            status, code = 503, "retrieval_unavailable"
        except GenerationProviderError:
            status, code = 503, "generation_unavailable"
        except (GenerationResponseError, EmbeddingResponseError, EmbeddingInputError):
            status, code = 502, "invalid_provider_response"
        except EvidenceContextLimitError:
            status, code = 503, "evidence_context_limit"
        except Exception:
            status, code = 500, "internal_error"
        return JSONResponse(status_code=status, content={"error": {"code": code, "message": "The question could not be completed."}})

    @app.post("/investigations", response_model=InvestigationResponse)
    async def investigations(body: QuestionRequest, request: Request):
        services = request.app.state.services
        service = getattr(services, "investigations", None)
        config = getattr(services, "settings", None)
        if service is None or not getattr(config, "investigation_enabled", False):
            raise HTTPException(503, detail={"code": "investigation_disabled", "message": "Multi-step investigation is not enabled."})
        owner_id = None
        if request.headers.get("authorization") is not None:
            owner_id = _authorized_actor(request).principal_id
        try:
            timeout = getattr(config, "investigation_timeout_seconds", 120)
            async with asyncio.timeout(timeout):
                result = await service.execute(body.question, owner_id=owner_id)
            return InvestigationResponse(
                status=result.status.value, answer=result.answer, citations=result.citations,
                conflicts=tuple(_serial(item) for item in result.conflicts),
                evidence=tuple({"evidence_id": item.evidence_id, "evidence": _serial(item.evidence)}
                               for item in result.evidence),
                searches=tuple(_serial(item) for item in result.searches),
                generation_requests=result.generation_requests, embedding_requests=result.embedding_requests,
                message=result.message, model_version=result.model_version, usage=_serial(result.usage),
            )
        except (TimeoutError, GenerationTimeoutError):
            status, code = 504, "investigation_timeout"
        except (EmbeddingRateLimitError, EmbeddingProviderError, RepositoryError):
            status, code = 503, "retrieval_unavailable"
        except GenerationProviderError:
            status, code = 503, "generation_unavailable"
        except (GenerationResponseError, EmbeddingResponseError, EmbeddingInputError):
            status, code = 502, "invalid_provider_response"
        except Exception:
            status, code = 500, "investigation_failed"
        return JSONResponse(status_code=status, content={"error": {"code": code, "message": "The investigation could not be completed."}})

    def actor(request: Request) -> DocumentActor:
        return _authorized_actor(request)

    async def form_upload(request: Request) -> UploadFile:
        try:
            form = await request.form(max_files=1, max_fields=0, max_part_size=65536)
        except _RequestTooLarge:
            raise HTTPException(413, detail={"code": "upload_too_large", "message": "Request exceeds the configured upload limit."}) from None
        except Exception:
            raise HTTPException(400, detail={"code": "invalid_multipart", "message": "A single multipart file is required."}) from None
        files = [value for _, value in form.multi_items()
                 if isinstance(value, UploadFile) or (hasattr(value, "filename") and hasattr(value, "read"))]
        if len(files) != 1 or len(form) != 1:
            for value in files:
                await value.close()
            raise HTTPException(400, detail={"code": "invalid_multipart", "message": "Submit exactly one file part."})
        return files[0]

    def upload_id(value: str | None) -> str:
        if value is None:
            raise HTTPException(400, detail={"code": "idempotency_key_required", "message": "Provide an Idempotency-Key UUID."})
        try:
            parsed = UUID(value)
        except ValueError:
            raise HTTPException(400, detail={"code": "invalid_idempotency_key", "message": "Idempotency-Key must be a UUID."}) from None
        return str(parsed)

    @app.post("/documents")
    async def upload_document(request: Request, idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None):
        trusted = actor(request)
        services = request.app.state.services
        if services.settings is None:
            raise HTTPException(503, detail={"code": "document_management_disabled", "message": "Document management is unavailable."})
        key = upload_id(idempotency_key)
        source_id = f"admin-upload:{key}"
        extracted = await extract_upload(await form_upload(request), source_id=source_id, settings=services.settings)
        try:
            document, version = await services.register_document.execute(extracted, owner_id=trusted.principal_id)
        except Exception as error:
            return _document_error(error)
        return {"status": "deleted" if document.deleted_at is not None else "registered_not_indexed",
                "document": _serial(document), "version": _serial(version), "original_retained": False}

    @app.get("/documents")
    async def list_documents(request: Request, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), include_deleted: bool = False):
        trusted = actor(request)
        try:
            page = await request.app.state.services.list_documents.execute(actor=trusted, offset=offset, limit=limit, include_deleted=include_deleted)
            return {
                "items": [{
                    "document": _serial(item.document),
                    "current_version": None if item.current_version is None else {
                        "id": str(item.current_version.id),
                        "version_number": item.current_version.version_number,
                        "content_sha256": item.current_version.content.sha256,
                        "created_at": item.current_version.created_at,
                        "source": _serial(item.current_version.source),
                    },
                    "version_count": item.version_count,
                } for item in page.items],
                "total": page.total, "offset": page.offset, "limit": page.limit,
            }
        except Exception as error:
            return _document_error(error)

    @app.get("/documents/{document_id}")
    async def get_document(document_id: UUID, request: Request):
        trusted = actor(request)
        try:
            details = await request.app.state.services.get_document.execute(document_id, actor=trusted)
            services = request.app.state.services
            latest = details.versions[-1] if details.versions else None
            if latest and services.prepare_indexing is None:
                raise HTTPException(503, detail={"code": "indexing_unavailable", "message": "Document indexing is not configured."})
            progress = await services.prepare_indexing.inspect(details.document, latest, services.chunking, services.embedding_configuration) if latest else None
            return {"document": _serial(details.document), "versions": _serial(details.versions), "indexing": _serial(progress)}
        except Exception as error:
            return _document_error(error)

    @app.put("/documents/{document_id}")
    async def replace_document(document_id: UUID, request: Request,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        if_match: Annotated[str | None, Header(alias="If-Match")] = None):
        trusted = actor(request)
        key = upload_id(idempotency_key)
        try:
            expected = UUID(if_match.strip('"')) if if_match else None
        except ValueError:
            raise HTTPException(400, detail={"code": "invalid_if_match", "message": "If-Match must contain the active version UUID."}) from None
        if expected is None:
            raise HTTPException(428, detail={"code": "if_match_required", "message": "Provide the active version in If-Match."})
        services = request.app.state.services
        try:
            details = await services.get_document.execute(document_id, actor=trusted)
            document = details.document
            if document.deleted_at is not None:
                raise DocumentUpdateConflict("Deleted documents cannot be updated")
            active = next((version for version in details.versions if version.id == document.current_version_id), None)
            retry_version = next((version for version in details.versions if version.source.revision == key), None)
            if active is None:
                raise DocumentUpdateConflict("Document does not have an active version")
            if document.current_version_id != expected and retry_version is None:
                raise DocumentUpdateConflict("Reload the current document version before replacement")
            identity_source = retry_version.source if retry_version is not None else active.source
            source_id = identity_source.source_id
            extracted = await extract_upload(await form_upload(request), source_id=source_id, settings=services.settings)
            source = SourceReference(kind=identity_source.kind, source_id=source_id, revision=key,
                record_locator=identity_source.record_locator, display_name=extracted.source.display_name,
                metadata=extracted.source.metadata)
            from dataclasses import replace
            extracted = replace(extracted, source=source)
            stored, version = await services.update_document.execute(document_id, extracted, actor=trusted, expected_current_version_id=expected)
            return {"status": "pending_replacement", "document": _serial(stored), "version": _serial(version), "original_retained": False}
        except Exception as error:
            return _document_error(error)

    @app.post("/documents/{document_id}/index")
    async def index_document(document_id: UUID, request: Request):
        trusted = actor(request)
        services = request.app.state.services
        if services.prepare_indexing is None:
            raise HTTPException(503, detail={"code": "indexing_unavailable", "message": "Document indexing is not configured."})
        try:
            details = await services.get_document.execute(document_id, actor=trusted)
            if details.document.deleted_at is not None:
                raise DocumentUpdateConflict("Deleted documents cannot be indexed")
            if details.document.owner_id != trusted.principal_id:
                # Shared records are readable and searchable, but remain system-managed.
                raise DocumentNotFound("Document was not found")
            version = details.versions[-1]
            async with asyncio.timeout(90):
                report = await services.prepare_indexing.execute(details.document, version, services.chunking,
                    services.embedding_configuration, max_new_embeddings=services.settings.max_index_embeddings_per_request)
            progress = await services.prepare_indexing.inspect(details.document, version, services.chunking, services.embedding_configuration)
            return {"status": report.outcome, "progress": _serial(progress), "requested_embeddings": report.requested_embeddings}
        except TimeoutError:
            raise HTTPException(504, detail={"code": "indexing_timeout", "message": "Indexing request timed out; retry to resume."}) from None
        except (EmbeddingProviderError, EmbeddingRateLimitError):
            raise HTTPException(503, detail={"code": "provider_unavailable", "message": "Indexing provider unavailable; retry to resume."}) from None
        except EmbeddingResponseError:
            raise HTTPException(502, detail={"code": "provider_response_invalid", "message": "Indexing provider returned an invalid response."}) from None
        except Exception as error:
            return _document_error(error)

    @app.delete("/documents/{document_id}", status_code=200)
    async def delete_document(document_id: UUID, request: Request):
        trusted = actor(request)
        try:
            deleted = await request.app.state.services.delete_document.execute(document_id, actor=trusted)
            return {"status": "deleted", "document": _serial(deleted)}
        except Exception as error:
            return _document_error(error)

    return app


def _document_error(error):
    if isinstance(error, (DocumentNotFound, DocumentAccessDenied, PermissionError)):
        raise HTTPException(404, detail={"code": "document_not_found", "message": "Document was not found."}) from None
    if isinstance(error, (ActivationConflict, DocumentUpdateConflict, RegistrationConflict, ContentHashCollision)):
        raise HTTPException(409, detail={"code": "document_conflict", "message": "The requested document change conflicts with its current state."}) from None
    if isinstance(error, RepositoryError):
        raise HTTPException(503, detail={"code": "storage_unavailable", "message": "Document storage is temporarily unavailable."}) from None
    if isinstance(error, DocumentExtractionError):
        raise HTTPException(422, detail={"code": "invalid_document", "message": "Document contents could not be extracted."}) from None
    if isinstance(error, ValueError):
        raise HTTPException(422, detail={"code": "invalid_document", "message": "Document data is invalid."}) from None
    raise HTTPException(500, detail={"code": "internal_error", "message": "The document operation could not be completed."}) from None
