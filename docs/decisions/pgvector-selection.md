# ADR: pgvector, PostgreSQL full-text search, and custom RRF

**E21 update (2026-10-27):** The [embedding storage ADR](embedding-storage.md) now governs the approved and implemented five-table storage mappings/migration and experimental no-title Gemini 768/cosine baseline. Statements below deferring physical derived schema describe the earlier milestone. Persistence workflows, generation, retrieval, production provider and remaining U14 lifecycle stay unresolved; no cloud migration has run.

**Date:** 2026-10-25  
**Status:** CONFIRMED technology selection; semantic retrieval (E27) and the scoped hybrid baseline (E31) are implemented.
**Decision owners:** [U12](register.md#u12) for storage and [U13](register.md#u13) for keyword retrieval/fusion; approval [E13](register.md#e13).

## Context and requirements

**Current implementation:** The historical sections below retain the technology-selection
reasoning. The [E31 implementation](#hybrid-retrieval-e31) supersedes their statements
that retrieval, keyword configuration and fusion contracts remain entirely unimplemented.

[U05](register.md#u05) already selects PostgreSQL as the authoritative relational database. This ADR adds retrieval technology without repeating the [PostgreSQL versus MySQL rationale](postgresql-vs-mysql.md).

The application has domain/provenance models, immutable shared Content, TXT/2Wiki extraction, async in-memory/PostgreSQL registration, authoritative ORM/migration definitions and deterministic chunking. The separately provisioned Cloud SQL instance has pgvector, but no application tables yet. Embeddings, derived storage, retrieval, update/delete workflows, indexing and activation remain unimplemented. References to those capabilities below describe requirements, not completed functionality.

The [assignment requirements](register.md#assignment-requirements), pages 2-3, require embeddings and semantic search, hybrid semantic/keyword retrieval, and a project-owned search/ranking component. Ordinary SQL text search is insufficient. The approved corpus has 125,760 occurrences, 56,680 distinct exact-content passages, and 400,892 sentence spans; see the [audit](../validation/2wiki-dev-audit.md#9-approved-u03-corpus-and-counting-policy).

## Decision

Use **PostgreSQL with pgvector** for vector storage and semantic similarity search, **PostgreSQL native full-text search** for keyword retrieval, and a **custom Reciprocal Rank Fusion (RRF)** implementation to combine ranked results. Do not introduce a separate vector database at this stage.

This selects technology and algorithm families. It does not select schemas, embedding dimensions, index configurations, retrieval parameters, or application interface signatures.

## Alternatives considered

| Alternative | Advantages | Costs and reason not selected initially |
| --- | --- | --- |
| PostgreSQL + pgvector | Co-locates authoritative and derived data; relational filtering and one database operational boundary. | Search competes with registration for database resources; suitability must be measured. Selected for architectural simplicity. |
| PostgreSQL + Qdrant | Specialized vector infrastructure, payload filtering, dense/sparse vectors, and independently scalable search. | Another service, duplicated search metadata, synchronization/rebuild responsibilities, and separate failure modes. Remains a credible alternative if measurements justify it. |
| PostgreSQL + another dedicated engine, such as a managed vector service | Could provide operational or specialized search advantages for a demonstrated requirement. | Requires a separate capability/cost evaluation and synchronization design. No additional engine is selected or claimed to be inferior. |

Qdrant documents [dense/sparse vectors](https://qdrant.tech/documentation/manage-data/vectors/), [payload filtering](https://qdrant.tech/documentation/search/filtering/), and [distributed deployment](https://qdrant.tech/documentation/scaling/distributed_deployment/). Independent scaling is an architectural advantage of separating services, not evidence that Qdrant is faster for this workload.

## Technical justification

### One database and consistency

Chunks and embeddings are derived from document content and processing configuration. Relational references must still resolve them to eligible document versions and original provenance. Sharing text must never erase occurrence-level sources.

Co-location makes it possible to commit related relational records and vector rows within one PostgreSQL transaction and query their relationships without a second system. It reduces coordination work for future version activation and deletion eligibility. PostgreSQL transactions provide the mechanism, but the application's boundaries, indexes, and failure handling still need design. [PostgreSQL transactions](https://www.postgresql.org/docs/18/tutorial-transactions.html).

Embedding generation involves external or computational work; it is not made atomic by storing its result in PostgreSQL. A09 still requires activation only after successful indexing. Co-location does not implement job recovery, prevent stale eligibility, or approve holding transactions open during embedding/LLM calls. These remain U14 concerns.

### Scale and available vector capabilities

The measured corpus provides a reasonable initial workload to evaluate inside the database already selected. It does not demonstrate a need for independently scaled search. This is a simplicity judgment, not a capacity certification. Final vector count depends on chunking, overlap, embedding configuration, and any approved derived-data reuse; it must not be equated with passages or sentence spans.

The following are verified pgvector capabilities, not selected configurations:

| Capability | Relevant behavior |
| --- | --- |
| Exact nearest-neighbor search | Available without an approximate index; useful as a correctness baseline. |
| Approximate search | HNSW and IVFFlat trade recall against query/build resources. |
| HNSW | Graph-based; no training step; construction and memory costs matter. |
| IVFFlat | Partitions vectors into lists; benefits from representative data for construction. |
| Distances | L2, inner product, cosine, and L1; available operator/index combinations depend on type and version. |
| Filtering | SQL filters and relational joins are available. Approximate scans can return too few filtered results; scan/index strategy needs evaluation. |

See the [official pgvector documentation](https://github.com/pgvector/pgvector). No metric, vector type, index family, list count, graph parameter, scan setting, or extension version is chosen here. Hosted extension availability is addressed in the [Cloud SQL ADR](google-cloud-sql-hosting.md).

### Planned hybrid retrieval

1. Semantic retrieval returns ranked candidates using pgvector.
2. Keyword retrieval returns ranked candidates using PostgreSQL full-text search.
3. Application-owned RRF combines candidate ranks.
4. The investigation engine receives ranked evidence with resolvable source/version references.

PostgreSQL provides text-search parsing and ranking functions, including ts_rank and ts_rank_cd; these are not claimed to implement BM25. Language configuration, query construction, ranking function, and keyword indexes remain open. [PostgreSQL text-search controls](https://www.postgresql.org/docs/18/textsearch-controls.html).

RRF combines positions in result lists rather than requiring vector and keyword scores to share a scale. The standard form sums `1 / (k + rank)` across contributing lists. The project will implement the fusion component itself; k, list sizes, candidate identity, ties, missing candidates, weights, and source diversity remain unresolved. The original research is evidence for the method, not a guarantee of gains here. [Cormack, Clarke, and Buettcher, RRF paper](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf).

Together, these planned components address semantic, hybrid, and custom-ranking requirements. Selecting them is not completion evidence: implementation, evaluation, citations, and failure analysis are still required.

## Architectural consequences

Domain objects and application logic must not depend on pgvector types, SQLAlchemy models, or provider payloads. Infrastructure implements retrieval through application interfaces approved for concrete workflows. Do not add an engine-switching framework solely for hypothetical future databases. The existing registration repository remains distinct from any future retrieval contract.

Updates/deletions must preserve shared Content and reject ineligible source versions during retrieval. Storing everything together simplifies enforcement opportunities; it does not make that logic automatic. No new table, search port, or cleanup mechanism is defined by this ADR.

## Trade-offs, risks, and revisit criteria

- Vector queries and index builds share CPU, RAM, I/O, storage, and connection capacity with authoritative registration and full-text queries.
- Approximate recall, filtered retrieval, and build resources need measurement. No pgvector/Qdrant performance comparison has been run.
- Hosting must support the required extension/index operations, and the eventual model must fit supported dimensions/types.
- A separate vector store may become preferable if measured latency, recall at latency, indexing time, resource contention, or independent scaling requirements cannot be met economically in PostgreSQL.

Record corpus/vector counts, index-build duration, CPU/RAM/I/O, ingest throughput, filtered recall, and query latency under representative concurrency before reconsidering. Numeric targets and acceptance thresholds remain U17/U18 decisions, not invented benchmarks. Moving to another engine would require an explicit decision and synchronization design.

## Unresolved details and verification

U11 retains chunking, model, dimensions, and effective-input/reuse configuration. U07/U12 retain physical vector schema, indexes, metrics, parameters, candidate/source-resolution contracts, and filtering behavior. U13 retains fusion and keyword parameters. U14 retains lifecycle/recovery mechanics. U17 retains targets, evaluation configurations, and results.

Official capability sources were checked on 2026-10-25. No extension, database, vector index, embedding pipeline, or retrieval implementation was created. This record approves the direction while preserving the stated implementation boundaries.

## Hybrid retrieval (E31)

Implemented on 2026-10-28 under the original hybrid task and its resume instruction.
This is a correctness and evaluation baseline, with significant measured scale limitations.
No migration, dependency, provider, indexing, answer-generation or UI change was needed.

### Keyword SQL and eligibility

PostgreSQL constructs `to_tsvector('pg_catalog.english', substr(text, start+1, end-start))`
for each exact canonical chunk slice. It matches a bound `plainto_tsquery` and ranks
with descending `ts_rank`. This is native FTS, not BM25. English stemming and stopword
removal affect derived search only. All meaningful query terms must match the same
chunk; punctuation/stopword-only queries produce no lexical hits. Titles are not indexed.
Canonical text, character ranges and native source locators remain unchanged.

Both channels share the existing selected-profile eligibility CTEs: trusted shared/owner
visibility, nondeleted documents, current versions belonging to the document, unambiguous
selected chunking plan, complete valid membership geometry and every required selected
embedding. Restrictions apply before each independent top-K. Incomplete configurations
are excluded from keyword results too. Legacy equivalent embedding configurations retain
the existing canonical-key handling.

`HybridEvidenceRepository.search_channels` returns two independently ranked bounded
lists. PostgreSQL executes both branches in one UNION ALL statement/snapshot and one
repository-owned transaction. Python does not fetch all vectors or filter a global top-K.
The transaction closes before answer generation; a channel error fails the request,
without silently falling back to a different retrieval mode.

### Application-owned RRF

`reciprocal_rank_fusion` sums `1 / (k + rank)` over available channels, with equal weights
and one-based list positions. Default k is 60. It never combines incomparable raw scores.
Identity is `(document_version_id, start, end)`: independent occurrences sharing Content
or a vector stay separate, as do overlapping ranges. The first duplicate per channel wins;
duplicates contribute once and remaining unique ranks are compacted. Conflicting source
payloads under the same identity raise an error. Single-channel hits remain eligible.
Ties use document ID, version ID, start and end; the final limit applies after fusion.
Candidate-list truncation affects results and must be recorded during evaluation.

Evidence `score_kind` distinguishes `cosine_similarity`, `keyword_rank` and `rrf`.
Fused evidence also exposes nullable `semantic_score`, `keyword_score`, `semantic_rank`
and `keyword_rank`; absent channels have null values. These are additive HTTP fields,
not probabilities, calibrated relevance, or proof of factual support. Citations continue
resolving only through trusted version-specific evidence and original intersecting spans.

### Configuration and integration

Flat trusted settings: RETRIEVAL_MODE defaults to semantic (alternative hybrid),
SEMANTIC_CANDIDATE_LIMIT and KEYWORD_CANDIDATE_LIMIT default to 20 (each 1-100),
and RRF_K defaults to 60 (0-10000). Zero is supported explicitly for experiments.
Semantic mode retains its existing requested result limit and exact cosine behavior.
Hybrid embeds the query once, retrieves configured channel candidates and fuses to the
requested final limit. The public question endpoint still accepts only a question and
always uses shared-only scope. No model, owner, profile or retrieval knobs are client-owned.
Existing RAG evidence selection, structural citation validation, prompts and browser assets
are unchanged. Automated HTTP composition verifies both modes with real disposable
PostgreSQL and fake AI, including private isolation and released generation-time connections.

### Performance and remaining approval

See the [local benchmark](../validation/hybrid-retrieval-performance.md). Current runtime
FTS has no materialized lexeme/GIN index, and exact vector search has no ANN index.
Existing PK/FK indexes support joins; they do not avoid corpus-wide eligibility and scoring
work. The baseline is available for controlled quality evaluation, not certified for
interactive corpus-scale service. Index design and any derived-storage migration need
separate review/approval. Full evaluation, production language/parameters and latency targets
remain U17/U18; lifecycle publication remains U14.

A full-Content GIN prefilter is unsafe for arbitrary chunk boundaries: the chunk `micro`
from `microelectronics` has a lexeme absent from the full-Content vector. Matching another
chunk must also never qualify an unrelated range. The recommended next performance design
is a persisted lexeme representation of exact reusable `(Content, start, end)` ranges with
explicit FTS configuration and GIN indexing, joined back to eligible version memberships.
It needs a maintenance/backfill contract and cannot be added as a cross-table expression
index over the current membership table. No such schema or index is implemented here.
Eligibility costs must be measured separately; a GIN index alone will not remove them.

### E33 retrieval optimization

The schema and serving semantics above are unchanged. E33 replaces whole-chunk whitespace removal with equivalent boundary trimming for blankness and defers title/source/excerpt joins until after each fully eligible channel limit. Both channels still execute in one snapshot; completeness and visibility are not post-filtered. The [performance checkpoint](../validation/hybrid-retrieval-performance.md#e33-schema-preserving-optimization-2026-10-28) records reproducible before/after measurements. No readiness cache, new index/table, approximate search or lifecycle change is selected.
