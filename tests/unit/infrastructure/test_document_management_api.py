from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from investigation_engine.application.answers import AnswerResult, AnswerStatus
from investigation_engine.application.use_cases.delete_document import DeleteDocument
from investigation_engine.application.use_cases.get_document import GetDocument
from investigation_engine.application.use_cases.index_document_version import IndexingProgress, IndexingState
from investigation_engine.application.use_cases.list_documents import ListDocuments
from investigation_engine.application.use_cases.register_document import RegisterDocument
from investigation_engine.application.use_cases.update_document import UpdateDocument
from investigation_engine.infrastructure.api.app import create_app
from investigation_engine.infrastructure.config import Settings
from investigation_engine.infrastructure.persistence.in_memory_document_repository import InMemoryDocumentRepository

ADMIN_TOKEN = "test-only-" + "x" * 40


@pytest.fixture
def management_app():
    repository = InMemoryDocumentRepository()
    settings = Settings(
        db_host="127.0.0.1",
        db_port=15432,
        db_name="unit",
        db_user="unit",
        db_password="hidden",
        admin_api_token=ADMIN_TOKEN,
        admin_principal_id="admin-test",
        max_upload_bytes=1024 * 1024,
        max_extracted_text_chars=10000,
        max_docx_uncompressed_bytes=1024 * 1024,
        max_pdf_pages=20,
        max_index_embeddings_per_request=2,
        admin_require_https=False,
    )
    question_service = SimpleNamespace(
        execute=AsyncMock(
            return_value=AnswerResult(
                status=AnswerStatus.INSUFFICIENT_EVIDENCE, answer=None, citations=(), message="No evidence"
            )
        )
    )

    async def unused(*args, **kwargs):
        raise AssertionError("Not expected in this test")

    services = SimpleNamespace(
        settings=settings,
        questions=question_service,
        register_document=RegisterDocument(repository),
        update_document=UpdateDocument(repository),
        delete_document=DeleteDocument(repository),
        list_documents=ListDocuments(repository),
        get_document=GetDocument(repository),
        prepare_indexing=SimpleNamespace(
            execute=AsyncMock(),
            inspect=AsyncMock(
                return_value=IndexingProgress(IndexingState.REGISTERED_NOT_INDEXED, None, 0, False, False)
            ),
        ),
        chunking=object(),
        embedding_configuration=object(),
    )

    @asynccontextmanager
    async def factory():
        yield services

    return SimpleNamespace(app=create_app(service_factory=factory), repository=repository, services=services)


async def client_for(value):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=value.app), base_url="http://test")


@pytest.mark.parametrize("headers,status", [({}, 401), ({"Authorization": "Bearer invalid"}, 401)])
async def test_document_management_requires_admin_authentication(management_app, headers, status):
    async with management_app.app.router.lifespan_context(management_app.app):
        async with await client_for(management_app) as client:
            response = await client.get("/documents", headers=headers)
    assert response.status_code == status
    assert response.json()["detail"]["code"] == "unauthorized"


async def test_upload_registers_private_document_and_stable_retry_is_idempotent(management_app):
    headers = {"Authorization": f"Bearer {ADMIN_TOKEN}", "Idempotency-Key": "1ac9d0c9-69a1-4aef-8593-3c6e136c02bd"}
    async with management_app.app.router.lifespan_context(management_app.app):
        async with await client_for(management_app) as client:
            first = await client.post(
                "/documents",
                headers=headers,
                files={"file": ("private.txt", b"Exact text.\n", "application/octet-stream")},
            )
            second = await client.post(
                "/documents",
                headers=headers,
                files={"file": ("private.txt", b"Exact text.\n", "application/octet-stream")},
            )
            page = await client.get("/documents", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
            response = await client.get(
                f"/documents/{first.json()['document']['id']}", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
            )
    assert first.status_code == second.status_code == 200, first.text
    assert first.json()["status"] == "registered_not_indexed"
    assert first.json()["original_retained"] is False
    assert first.json()["document"]["owner_id"] == "admin-test"
    assert first.json()["document"]["id"] == second.json()["document"]["id"]
    assert first.json()["version"]["content"]["text"] == "Exact text.\n"
    assert page.json()["total"] == 1
    assert response.status_code == 200
    assert response.json()["versions"][0]["content"]["text"] == "Exact text.\n"
    assert page.json()["items"][0]["current_version"] is None


async def test_distinct_upload_keys_keep_independent_sources_and_reuse_content(management_app):
    async with management_app.app.router.lifespan_context(management_app.app):
        async with await client_for(management_app) as client:
            first = await client.post(
                "/documents",
                headers=_auth("1ac9d0c9-69a1-4aef-8593-3c6e136c02bd"),
                files={"file": ("one.txt", b"Identical body")},
            )
            second = await client.post(
                "/documents",
                headers=_auth("2bc9d0c9-69a1-4aef-8593-3c6e136c02bd"),
                files={"file": ("two.txt", b"Identical body")},
            )
    assert first.status_code == second.status_code == 200
    assert first.json()["document"]["id"] != second.json()["document"]["id"]
    assert first.json()["version"]["source"]["source_id"] != second.json()["version"]["source"]["source_id"]
    assert first.json()["version"]["content"]["sha256"] == second.json()["version"]["content"]["sha256"]
    assert len(management_app.repository._contents) == 1


@pytest.mark.parametrize(
    "filename,payload,status,code",
    [
        ("note.exe", b"hello", 415, "unsupported_format"),
        ("note.txt", b"\xff", 422, "invalid_document"),
        ("empty.txt", b"", 422, "empty_upload"),
    ],
)
async def test_upload_rejects_unsupported_malformed_and_empty_files(management_app, filename, payload, status, code):
    async with management_app.app.router.lifespan_context(management_app.app):
        async with await client_for(management_app) as client:
            response = await client.post(
                "/documents",
                headers={
                    "Authorization": f"Bearer {ADMIN_TOKEN}",
                    "Idempotency-Key": "1ac9d0c9-69a1-4aef-8593-3c6e136c02bd",
                },
                files={"file": (filename, payload)},
            )
    assert response.status_code == status, response.text
    assert response.json()["detail"]["code"] == code
    page = await management_app.repository.list_documents(actor_id="admin-test", offset=0, limit=10)
    assert page.total == 0


async def test_upload_rejects_configured_byte_and_text_limits(management_app):
    management_app.services.settings = replace(management_app.services.settings, max_upload_bytes=1024)
    async with management_app.app.router.lifespan_context(management_app.app):
        async with await client_for(management_app) as client:
            headers = _auth("1ac9d0c9-69a1-4aef-8593-3c6e136c02bd")
            too_big = await client.post("/documents", headers=headers, files={"file": ("large.txt", b"x" * 1025)})
            management_app.services.settings = replace(
                management_app.services.settings, max_upload_bytes=2048, max_extracted_text_chars=4
            )
            too_long = await client.post("/documents", headers=headers, files={"file": ("long.txt", b"12345")})
    assert too_big.status_code == 413 and too_big.json()["detail"]["code"] == "upload_too_large"
    assert too_long.status_code == 413 and too_long.json()["detail"]["code"] == "text_too_large"


async def test_authenticated_question_scope_uses_server_principal(management_app):
    async with management_app.app.router.lifespan_context(management_app.app):
        async with await client_for(management_app) as client:
            response = await client.post(
                "/questions",
                headers={"Authorization": f"Bearer {ADMIN_TOKEN}", "X-Owner-ID": "attacker"},
                json={"question": "question"},
            )
    assert response.status_code == 200 and response.json()["status"] == "insufficient_evidence"
    management_app.services.questions.execute.assert_awaited_once_with("question", owner_id="admin-test")


async def test_admin_cannot_index_a_shared_system_managed_document(management_app):
    from investigation_engine.domain.content import Content
    from investigation_engine.domain.document import Document
    from investigation_engine.domain.document_version import DocumentVersion
    from investigation_engine.domain.provenance import SourceKind, SourceReference

    document = Document(owner_id=None)
    version = DocumentVersion(
        document_id=document.id,
        version_number=1,
        content=Content("Shared corpus content."),
        source=SourceReference(kind=SourceKind.DATASET, source_id="shared-source"),
    )
    await management_app.repository.register(document, version)
    async with management_app.app.router.lifespan_context(management_app.app):
        async with await client_for(management_app) as client:
            response = await client.post(
                f"/documents/{document.id}/index", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
            )
    assert response.status_code == 404
    management_app.services.prepare_indexing.execute.assert_not_awaited()


def _auth(key):
    return {"Authorization": f"Bearer {ADMIN_TOKEN}", "Idempotency-Key": key}
