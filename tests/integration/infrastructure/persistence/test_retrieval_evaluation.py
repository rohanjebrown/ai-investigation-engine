"""Full fixed-corpus comparison with real PostgreSQL and deliberately fake vectors."""

import json
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import func, select

from investigation_engine.application.chunking import ChunkingConfig, ChunkingStrategy, chunk_document
from investigation_engine.application.embeddings import EmbeddingConfig
from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity
from investigation_engine.application.use_cases.index_document_version import IndexDocumentVersion
from investigation_engine.application.use_cases.search_evidence import SearchEvidence
from investigation_engine.domain.content import Content
from investigation_engine.domain.document import Document
from investigation_engine.domain.document_version import DocumentVersion
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.evaluation.retrieval import RetrievalRun, evaluate, export, fingerprint
from investigation_engine.infrastructure.persistence.models import DocumentRow, DocumentVersionRow
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import (
    PostgreSQLChunkPlanRepository,
)
from investigation_engine.infrastructure.persistence.postgresql_document_activation_repository import (
    PostgreSQLDocumentActivationRepository,
)
from investigation_engine.infrastructure.persistence.postgresql_embedding_repository import (
    PostgreSQLEmbeddingRepository,
)
from investigation_engine.infrastructure.persistence.postgresql_evidence_repository import PostgreSQLEvidenceRepository


async def test_fixed_40_question_suite_compares_real_postgresql_modes_with_fake_ai(
    sessions,
    repository,
    db_engine,
    tmp_path,
):
    root = Path("data/samples/evaluation")
    suite, corpus = (json.loads((root / f"{name}.json").read_text(encoding="utf8")) for name in ("suite", "corpus"))

    class Tokenizer:
        identity = TokenizerIdentity(
            name="characters", revision="1", artifact_sha256="0" * 64, runtime_version="evaluation-test"
        )

        def offsets(self, text):
            return tuple((i, i + 1) for i in range(len(text)))

    class Provider:
        configuration = EmbeddingConfig(provider="synthetic", model_id="evaluation-tied-vectors", model_revision="1")
        query_count = 0

        async def embed_documents(self, texts):
            return tuple((1.0,) + (0.0,) * 767 for _ in texts)

        async def embed_query(self, question):
            self.query_count += 1
            return (1.0,) + (0.0,) * 767

    config = ChunkingConfig(strategy=ChunkingStrategy.WHOLE_PASSAGE, tokenizer=Tokenizer.identity, max_tokens=100000)
    provider = Provider()
    plans = PostgreSQLChunkPlanRepository(sessions)
    index = IndexDocumentVersion(
        plans,
        PostgreSQLEmbeddingRepository(sessions),
        PostgreSQLDocumentActivationRepository(sessions),
        provider,
        max_attempts=1,
    )
    for doc in corpus["documents"]:
        spans = tuple(
            SourceSpan(
                start=s["start"],
                end=s["end"],
                locators=tuple(
                    SourceLocator(kind=LocatorKind(loc["kind"]), index=loc["index"]) for loc in s["locators"]
                ),
            )
            for s in doc["source_spans"]
        )
        document = Document(title=doc["title"], owner_id=None)
        version = DocumentVersion(
            document_id=document.id,
            version_number=1,
            content=Content(doc["text"]),
            source=SourceReference(**{**doc["source"], "kind": SourceKind(doc["source"]["kind"])}),
            source_spans=spans,
        )
        document, version = await repository.register(document, version)
        await plans.save(chunk_document(version, config=config, tokenizer=Tokenizer()))
        await index.execute(document.id, version.id, config, provider.configuration)
    # Attest the source snapshot from actual rows, not only a caller-provided label.
    with db_engine.connect() as connection:
        actual = (
            connection.execute(
                select(
                    DocumentVersionRow.source_id,
                    DocumentVersionRow.revision,
                    DocumentVersionRow.record_locator,
                    DocumentVersionRow.content_sha256,
                )
            )
            .mappings()
            .all()
        )
        assert (
            connection.scalar(
                select(func.count()).select_from(DocumentRow).where(DocumentRow.current_version_id.is_not(None))
            )
            == corpus["record_count"]
        )
    assert {
        (r["source_id"], r["revision"], fingerprint(r["record_locator"]), r["content_sha256"].hex()) for r in actual
    } == {
        (
            d["source"]["source_id"],
            d["source"]["revision"],
            fingerprint(d["source"]["record_locator"]),
            d["content_sha256"],
        )
        for d in corpus["documents"]
    }
    evidence = PostgreSQLEvidenceRepository(sessions)
    runs = [
        (
            RetrievalRun(name=mode, mode=mode, chunking=asdict(config), embedding=asdict(provider.configuration)),
            SearchEvidence(
                evidence,
                provider,
                config,
                provider.configuration,
                mode="semantic" if mode == "keyword" else mode,
                hybrid_repository=evidence,
            ),
        )
        for mode in ("semantic", "keyword", "hybrid")
    ]
    report = await evaluate(suite, corpus, runs, indexed_corpus_fingerprint=fingerprint(corpus))
    assert provider.query_count == 80
    assert all(run["aggregate"]["status_counts"] == {"ok": 40} for run in report["runs"])
    assert all(run["aggregate"]["scored_cases"] == 34 for run in report["runs"])
    assert report["comparisons"][0]["paired_cases"] == 34
    export(report, tmp_path)
    assert len(json.loads((tmp_path / "retrieval.json").read_text(encoding="utf8"))["runs"][0]["questions"]) == 40
    # Capture a small inspectable local result without treating tied fake vectors as model quality.
    output = Path(".cache/evaluation-controlled")
    export(report, output)
    print(
        json.dumps(
            {
                "corpus_records": corpus["record_count"],
                "unique_contents": corpus["unique_content_count"],
                "runs": [{"name": r["configuration"]["name"], "aggregate": r["aggregate"]} for r in report["runs"]],
                "comparisons": report["comparisons"],
            }
        )
    )
