# System architecture and project decisions

**Project:** Upgaming Applied AI — AI Investigation Engine  
**Date:** 26 October 2026  
**Status:** Agreed architecture with planned implementation choices

**Approval reconciliation (2026-10-25):** Use the [decision register](../decisions/register.md) for approval scope and actual implementation. PostgreSQL authority, pgvector, native full-text search and custom RRF are selected. Async in-memory/PostgreSQL registration, authoritative ORM/migration definitions, central infrastructure settings and deterministic chunking are implemented. Extraction, domain computation and chunking remain synchronous. The user has provisioned Cloud SQL and read-only connectivity is verified; its application schema is not yet installed. Zero-cost deployment feasibility remains unresolved. Historical target descriptions below do not imply additional implementation or approval.

## 1. Goal and assignment alignment

Build a Python-backed, publicly accessible investigation application that ingests and manages documents, answers questions using evidence from multiple sources, performs multistep investigation, cites sources, attempts contradiction detection, and abstains when evidence is insufficient.

The assignment requires, at minimum:

| Area | Requirement | Direction |
| --- | --- | --- |
| Input | At least 3 file formats; extract text/metadata; add/update/delete | PDF, DOCX, TXT adapters; separate 2Wiki adapter |
| Preparation | Deduplicate information; compare at least 2 chunking approaches | Exact-content reuse; independent configurable chunkers |
| Search | Embeddings, vector database, semantic and keyword/hybrid retrieval | pgvector + PostgreSQL native full-text search; custom RRF selected, implementation pending |
| Reasoning | RAG, multi-document/multistep synthesis, citations, contradiction attempt, abstention | Evidence-driven investigation and structured tool invocation |
| Evaluation | At least 40 simple/multi-hop/unanswerable/contradictory questions; numerical retrieval and answer metrics; configurations compared | Separate evaluation dataset and repeatable run manifests |
| Scale | At least 50,000 independent documents/text records processed | Audit unique source records; not established merely by 2Wiki question count |
| Product | Python backend + UI for document management, questions, sources | API/UI design pending |
| Delivery | Persistent public free-host deployment, code history, README, evaluation and AI-use report | Deployment and storage feasibility to be measured |

## 2. Decisions already agreed

1. **Benchmark/data:** Start with `xanhho/2WikiMultihopQA` as the initial corpus and reasoning benchmark. Do not couple the engine to it. RAGBench is optional later, not the primary benchmark now.
2. **Source adapters:** Separate input adapters for 2Wiki Parquet, PDF, DOCX and TXT; all feed the same domain representation.
3. **Benchmark isolation:** `context` passages are potential evidence; `question`, `answer`, `supporting_facts`, and `evidences` belong in evaluation/annotation storage, **not** the search index as answers or gold reasoning paths.
4. **Project environment:** Single Python repository and `src/investigation_engine` package; CPython 3.14.9, `uv`/`.venv`, `pyproject.toml` and committed `uv.lock` (see existing environment ADRs).
5. **Parquet I/O:** PyArrow with context-managed `ParquetFile` and streaming `iter_batches` (see ADR-003).
6. **Document ownership:** Independently received documents retain independent logical identity and provenance even if their text is identical.
7. **Historical versions:** Retain `DocumentVersion` history. A different user's document is **not** a new version of the first just because the text matches.
8. **Content reuse:** Exactly equal **canonical extracted text** may share one immutable `Content` record. Original structure and source mappings remain version-specific. A hash enables lookup; it does not replace equality verification.
9. **Deduplication exclusions:** Do **not** perform whitespace-normalized, fuzzy, or semantic deduplication as an ingestion/index-deletion policy. Semantic similarity can later be used for retrieval-time result grouping or diversification; it should not automatically suppress distinct evidence.
10. **Extraction-before-chunking:** One complete canonical text per document version, alongside structure and version-specific source mapping. Chunking and embedding are subsequent transformations.
11. **Offsets:** Use Python string indices (Unicode code-point positions), zero-based and half-open `[start, end)` within the exact canonical text. External services' offset units must be converted.
12. **Retirement:** Historical versions remain available according to retention policy, but only *current, nondeleted, authorized* versions are eligible for ordinary retrieval. Shared vectors remain while some eligible source needs them.
13. **Source of truth:** PostgreSQL is authoritative for documents, versions, content and provenance. Rebuildable vector/full-text representations will use pgvector and PostgreSQL full-text search; physical mappings and lifecycle mechanics remain open.

## 3. Proposed component map

```text
2Wiki dataset ──┐
PDF upload ─────┼──> Adapters / extraction ──> Canonical text
DOCX upload ────┤                                  + version-specific structure
TXT upload ─────┘                                  + source-location mappings
                                                       |
                             Document / DocumentVersion registry
                                       |               |
                                       v               v
                               Exact text reuse    Provenance store
                                       |
                                       v
                            Content-addressed text storage
                                       |
                                       v
                           Chunking (>= 2 strategies)
                                       |
                                       v
                         Embeddings + keyword indexing
                                       |
                            +----------+----------+
                            |                     |
                    Eligible vector search  Eligible keyword search
                            +----------+----------+
                                       |
                                 Custom RRF
                                       |
                         Version-specific citation resolution
                                       |
                     Optional retrieval-time result diversity
                                       |
                             Multistep evidence work
                                       |
                             LLM + tool invocation
                                       |
                         Evidence-backed response/citations
                                       |
                              Python API + UI

Benchmark Q&A and gold annotations ──> Separate evaluation subsystem
```

This is a **logical** diagram, not a deployment diagram. PostgreSQL and pgvector are now selected for authoritative relational data and derived vector representations respectively; their responsibilities remain distinct. Physical mappings, indexes, and lifecycle mechanics are not selected by the diagram.

## 4. Provenance principle

Any displayed claim should be traceable along a chain resembling:

`answer → retrieved item → chunk → canonical content range(s) → eligible document version → original source location(s)`.

A single indexed vector may correspond to several independently managed documents. Resolve those associations using authoritative eligibility rules; **do not** assume a shared vector's payload identifies the only source. Citation evidence should include a version and precise locators where available.

## 5. Dataset-specific boundary

U03 selects the complete pinned 2Wiki dev split: 12,576 questions, 125,760 context occurrences, and 56,680 distinct exact-content passages as the approved conservative scale baseline. Titles, occurrences, and chunks are not interchangeable count units. Exact-content uniqueness does not prove semantic independence; see the [audit](../validation/2wiki-dev-audit.md#9-approved-u03-corpus-and-counting-policy).

A source identity with revision, split, example ID, and context position identifies an individual occurrence, not a globally stable Wikipedia article. U01 assigns each occurrence its own logical document and initial version, while exact Content may be shared. U08 resolves identical same-revision retries; cross-revision document continuity is not inferred. Original sentence indices and spans remain specific to each version.

## 6. Planned retrieval and evaluation

- **Selected, not implemented:** pgvector semantic search, PostgreSQL native full-text keyword search, and custom RRF. Built-in full-text ranking is not equated with BM25. Investigation/LLM workflow details remain U15.
- **Implemented chunk foundation (E17/E18):** whole-passage, fixed-token and supplied-sentence plans over exact Content slices, with version/configuration memberships and a file-loaded experimental tokenizer. Initial 128/16 and 128/0 parameters are experimental. See the [chunking ADR](../decisions/deterministic-chunking.md); no embeddings, derived database schema, retrieval or activation are implemented by this milestone.
- **Planned:** at least 40 test questions covering simple, multihop, unanswerable and contradictory examples; report Recall@K, MRR, answer correctness, citation accuracy, failure analysis and configurations.
- **Planned:** record evaluation snapshot/manifest (source revisions, active document versions/corpus, chunking, embedding/retrieval/LLM settings); avoid ground-truth leakage.

## 7. Not decided / not claimed

- Derived range/membership/vector schema, keyword index configuration, object storage, application hosting, and the feasibility of a zero-cost database connection path. U07's authoritative three-table schema is already implemented.
- Remaining parser policies, derived ORM mappings, additional dependencies/indexes, indexing/activation transactions and outbox/recovery strategy. Domain dataclasses, the authoritative ORM and registration-specific session/transaction ownership already have distinct approved roles.
- Source-original retention/deletion policy, privacy model, tenant boundaries.
- Cross-revision updates, batch recovery, and final evaluation/leakage policy. U01 occurrence identity and U03's conservative exact-content counting baseline are approved, with semantic-independence limitations retained.
- Final chunking defaults/budgets and production tokenizer, embedding model/dimensions/provider/input formatting, UI/framework and production SLA. The deterministic strategy families and conceptual range/reuse rules are now approved; Gemini Embedding 2/768/cosine remains an experimental candidate.

## 8. Senior-engineering quality bar

This design reflects useful production patterns: immutable derived artifacts, stable identity, explicit lineage, independent source provenance, rebuildable indexes, and tests for update/delete races. **It is a design, not evidence of a production-grade implementation.** Before treating it as such, verify citation offsets on real inputs, permission isolation, index consistency/failure recovery, actual 50k scale, deployment persistence, and benchmark integrity.

## References (industry patterns, not prescriptions)

- Microsoft Azure AI Search, parent/chunk index projections: https://learn.microsoft.com/en-us/azure/search/search-how-to-define-index-projections
- Azure Document Intelligence, extracted text and spans: https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout
- Google Cloud Document AI, `Document.text` / `TextAnchor`: https://docs.cloud.google.com/document-ai/docs/reference/rest/v1/Document
- W3C PROV-O provenance vocabulary: https://www.w3.org/TR/prov-o/
- Qdrant multi-tenant indexing/filtering concepts: https://qdrant.tech/documentation/manage-data/multitenancy/
