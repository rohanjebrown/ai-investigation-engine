from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import func, select

from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy, chunk_document
from investigation_engine.application.embeddings import EmbeddingConfig
from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
from investigation_engine.application.use_cases.index_document_version import IndexDocumentVersion
from investigation_engine.application.use_cases.register_document import RegisterDocument
from investigation_engine.infrastructure.ingestion.docx_adapter import extract_docx
from investigation_engine.infrastructure.ingestion.pdf_adapter import extract_pdf
from investigation_engine.infrastructure.persistence.models import ContentRow
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import PostgreSQLChunkPlanRepository
from investigation_engine.infrastructure.persistence.postgresql_document_activation_repository import PostgreSQLDocumentActivationRepository
from investigation_engine.infrastructure.persistence.postgresql_embedding_repository import PostgreSQLEmbeddingRepository
from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import PostgreSQLEvidenceRepository


@pytest.mark.parametrize("extractor,filename", [(extract_pdf, "multipage.pdf"), (extract_docx, "paragraphs-and-table.docx")])
async def test_files_register_reconstruct_chunk_index_and_resolve_original_locations(
    extractor, filename, repository, sessions, db_engine,
):
    extracted = extractor(Path("data/samples/formats") / filename, source_id="first-upload")
    register = RegisterDocument(repository)
    document, version = await register.execute(extracted, owner_id=None)
    other, second = await register.execute(replace(extracted, source=replace(extracted.source, source_id="independent-upload")), owner_id=None)
    assert other.id != document.id and second.content == version.content
    with db_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(ContentRow)) == 1

    class Tokenizer:
        identity = TokenizerIdentity(name="characters", revision="1", artifact_sha256="0" * 64, runtime_version="file-test")

        def offsets(self, text):
            return tuple((i, i + 1) for i in range(len(text)))

    class Provider:
        configuration = EmbeddingConfig(provider="synthetic", model_id="file-ingestion-test")

        async def embed_documents(self, texts):
            return tuple((1.0,) + (0.0,) * 767 for _ in texts)

    config = ChunkingConfig(strategy=ChunkingStrategy.FIXED_TOKENS, tokenizer=Tokenizer.identity, max_tokens=35)
    plans = PostgreSQLChunkPlanRepository(sessions)
    plan = chunk_document(version, config=config, tokenizer=Tokenizer())
    await plans.save(plan)
    restored = await plans.get(version.id, config)
    assert "".join(chunk.text for chunk in restored.chunks) == extracted.text
    await IndexDocumentVersion(plans, PostgreSQLEmbeddingRepository(sessions),
        PostgreSQLDocumentActivationRepository(sessions), Provider(), max_attempts=1).execute(
            document.id, version.id, config, Provider.configuration)
    # A new registration call opens fresh sessions and reconstructs stored JSONB/Content.
    retried, reconstructed = await RegisterDocument(repository).execute(extracted, owner_id=None)
    assert retried.id == document.id and retried.current_version_id == version.id
    assert reconstructed.content.text == extracted.text
    assert reconstructed.source == extracted.source
    assert reconstructed.source_spans == extracted.source_spans
    assert reconstructed.structure == extracted.structure
    hits = await PostgreSQLEvidenceRepository(sessions).search((1.0,) + (0.0,) * 767,
        config, Provider.configuration, owner_id=None, limit=100)
    assert len(hits) == len(plan.chunks)
    for hit in hits:
        assert hit.text == extracted.text[hit.start:hit.end]
        assert hit.document_version_id == version.id
        assert hit.source == extracted.source
        assert hit.source_spans == tuple(s for s in extracted.source_spans if s.start < hit.end and hit.start < s.end)
