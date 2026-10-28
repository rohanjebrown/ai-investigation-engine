from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from investigation_engine.application.answers import AnswerStatus, GenerationResult
from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy
from investigation_engine.application.embeddings import EmbeddingConfig
from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
from investigation_engine.application.use_cases.answer_question import AnswerQuestion
from investigation_engine.application.use_cases.delete_document import DeleteDocument
from investigation_engine.application.use_cases.get_document import GetDocument
from investigation_engine.application.use_cases.index_document_version import IndexDocumentVersion
from investigation_engine.application.use_cases.list_documents import ListDocuments
from investigation_engine.application.use_cases.prepare_document_indexing import PrepareDocumentIndexing
from investigation_engine.application.use_cases.register_document import RegisterDocument
from investigation_engine.application.use_cases.search_evidence import SearchEvidence
from investigation_engine.application.use_cases.update_document import UpdateDocument
from investigation_engine.infrastructure.api.app import create_app
from investigation_engine.infrastructure.api.bootstrap import APIServices
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import PostgreSQLChunkPlanRepository
from investigation_engine.infrastructure.persistence.postgresql_document_activation_repository import PostgreSQLDocumentActivationRepository
from investigation_engine.infrastructure.persistence.postgresql_embedding_repository import PostgreSQLEmbeddingRepository
from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import PostgreSQLEvidenceRepository

TOKEN = "integration-only-" + "x" * 40


class CharacterTokenizer:
    identity = TokenizerIdentity(name="http-test", revision="1", artifact_sha256="0" * 64, runtime_version="test")
    def offsets(self, text):
        return tuple((index, index + 1) for index in range(len(text)))


class FakeEmbedding:
    configuration = EmbeddingConfig(provider="synthetic", model_id="http-doc-management", model_revision="1")
    def __init__(self): self.calls = []
    async def embed_documents(self, texts):
        self.calls.extend(texts)
        return tuple((1.0,) + (0.0,) * 767 for _ in texts)
    async def embed_query(self, query): return (1.0,) + (0.0,) * 767


class FakeGenerator:
    def __init__(self): self.texts = []
    async def generate(self, request):
        self.texts.extend(item.text for item in request.evidence)
        return GenerationResult(status=AnswerStatus.ANSWERED, answer="The source supports this. [E1]", citations=("E1",))


@pytest.mark.parametrize("filename", ["multipage.pdf", "paragraphs-and-table.docx"])
async def test_pdf_and_docx_register_through_authenticated_http(filename, repository, sessions):
    app = _app(_services(repository, sessions, CharacterTokenizer(), FakeEmbedding(), FakeGenerator()))
    data = (Path("data/samples/formats") / filename).read_bytes()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/documents", headers=_auth("f170d3bc-e052-4405-b873-e817388e7ec0"),
                files={"file": (filename, data, "application/octet-stream")})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "registered_not_indexed"
    assert response.json()["original_retained"] is False


async def test_http_private_upload_index_question_replace_reactivate_delete_lifecycle(repository, sessions):
    tokenizer, embedding, generator = CharacterTokenizer(), FakeEmbedding(), FakeGenerator()
    app = _app(_services(repository, sessions, tokenizer, embedding, generator))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            anonymous = await client.post("/documents", files={"file": ("private.txt", b"secret fact")})
            assert anonymous.status_code == 401

            first_key = "3fef85d3-9737-43b4-a1df-b0f73891e930"
            uploaded = await client.post("/documents", headers=_auth(first_key),
                files={"file": ("facts.txt", b"The private project code is ORCHID.", "text/plain")})
            assert uploaded.status_code == 200
            document, version = uploaded.json()["document"], uploaded.json()["version"]
            assert document["owner_id"] == "administrator-integration"
            assert uploaded.json()["status"] == "registered_not_indexed"

            first_index = await client.post(f"/documents/{document['id']}/index", headers=_bearer())
            assert first_index.status_code == 200 and first_index.json()["status"] == "activated"
            assert first_index.json()["requested_embeddings"] == 1
            public = await client.post("/questions", json={"question": "What is the project code?"})
            assert public.json()["status"] == "insufficient_evidence"
            private = await client.post("/questions", headers=_bearer(), json={"question": "What is the project code?"})
            assert private.status_code == 200 and private.json()["status"] == "answered"
            assert private.json()["citations"][0]["evidence"]["source"]["source_id"].startswith("admin-upload:")

            replacement_key = "0ce1f327-e3c7-4b2c-9f88-1a7909dc550c"
            replacement = await client.put(f"/documents/{document['id']}",
                headers={**_auth(replacement_key), "If-Match": f"\"{version['id']}\""},
                files={"file": ("facts.txt", b"The private project code is TULIP.", "text/plain")})
            assert replacement.status_code == 200 and replacement.json()["status"] == "pending_replacement", replacement.text
            before_activation = await client.post("/questions", headers=_bearer(), json={"question": "What is the project code?"})
            assert before_activation.json()["citations"][0]["evidence"]["text"] == "The private project code is ORCHID."
            activated = await client.post(f"/documents/{document['id']}/index", headers=_bearer())
            assert activated.status_code == 200 and activated.json()["status"] == "activated", activated.text
            after_activation = await client.post("/questions", headers=_bearer(), json={"question": "What is the project code?"})
            assert after_activation.json()["citations"][0]["evidence"]["text"] == "The private project code is TULIP."

            deleted = await client.delete(f"/documents/{document['id']}", headers=_bearer())
            assert deleted.status_code == 200 and deleted.json()["status"] == "deleted"
            after_delete = await client.post("/questions", headers=_bearer(), json={"question": "What is the project code?"})
            assert after_delete.json()["status"] == "insufficient_evidence"


def _services(repository, sessions, tokenizer, embedding, generator):
    plans = PostgreSQLChunkPlanRepository(sessions)
    embedding_repository = PostgreSQLEmbeddingRepository(sessions)
    activation = PostgreSQLDocumentActivationRepository(sessions)
    evidence = PostgreSQLEvidenceRepository(sessions)
    chunking = ChunkingConfig(strategy=ChunkingStrategy.WHOLE_PASSAGE, tokenizer=tokenizer.identity, max_tokens=256)
    indexer = IndexDocumentVersion(plans, embedding_repository, activation, embedding, max_attempts=1)
    search = SearchEvidence(evidence, embedding, chunking, embedding.configuration)
    settings = SimpleNamespace(admin_api_token=TOKEN, admin_principal_id="administrator-integration",
        admin_require_https=False, max_upload_bytes=5_000_000, max_extracted_text_chars=1_000_000,
        max_docx_uncompressed_bytes=50_000_000, max_pdf_pages=100, max_index_embeddings_per_request=2)
    return APIServices(settings=settings, questions=AnswerQuestion(search, generator),
        register_document=RegisterDocument(repository), update_document=UpdateDocument(repository),
        delete_document=DeleteDocument(repository), list_documents=ListDocuments(repository),
        get_document=GetDocument(repository), prepare_indexing=PrepareDocumentIndexing(plans, indexer, tokenizer),
        chunking=chunking, embedding_configuration=embedding.configuration)


def _app(services):
    @asynccontextmanager
    async def factory():
        yield services
    return create_app(service_factory=factory)


def _bearer(): return {"Authorization": f"Bearer {TOKEN}"}
def _auth(key): return {**_bearer(), "Idempotency-Key": key}
