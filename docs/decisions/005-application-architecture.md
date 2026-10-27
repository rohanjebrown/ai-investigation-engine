# ADR-005 — Modular Monolith with Lightweight Hexagonal Architecture

**Project:** Upgaming Applied AI — AI Investigation Engine  
**Date:** 2026-10-26  
**Status:** CONFIRMED for the lightweight hexagonal modular monolith in [A04](register.md#a04); concrete integrations and contracts remain UNRESOLVED.  
**Scope:** Application organization, dependency direction, integration boundaries, and deployment model

## Approval reconciliation (2026-10-26)

The user explicitly ratified the architectural direction ([A04](register.md#a04)) and UUIDv7 document/version IDs ([A05](register.md#a05)); approval evidence is [E01](register.md#e01). The historical "Accepted architectural direction" status is limited to that baseline, not every integration shown below.

FastAPI, PostgreSQL/pgvector/Qdrant, custom RRF, new search interfaces, and worker/job arrangements remain proposals or open decisions: [U05](register.md#u05), [U12](register.md#u12)-[U16](register.md#u16). Target directories and example workflows are explanatory, not authorization to create abstractions. Hosting is [U18](register.md#u18); test sequencing is [A10](register.md#a10)/[U17](register.md#u17). Current implementation evidence is recorded in the [register](register.md#observed-evidence), separately from acceptance criteria.

## 1. Context

The AI Investigation Engine must ingest and manage multiple document formats, retain reliable provenance and document versions, index evidence for keyword and semantic retrieval, and investigate questions using an LLM. The assignment also requires a Python backend, functional UI, reproducible evaluation, and a publicly accessible deployment with persistent storage.

The project starts as a single Python repository and is being developed by one engineer under practical time and hosting constraints. It needs to be extensible enough to add data adapters or replace external providers, without taking on unnecessary distributed-systems overhead.

Important existing decisions are recorded in:

- [ADR-004 — Document identity, versions, reusable content and provenance](004-document-content-provenance.md)
- [Document data contract](../design/document-data-contract.md)
- [Document lifecycle and indexing](../design/document-lifecycle-and-indexing.md)

The architecture selected here **organizes those decisions**; it does not replace their domain semantics.

## 2. Decision

**Build a modular monolith using a pragmatic Ports-and-Adapters (Hexagonal) architecture, with domain, application, infrastructure, and presentation boundaries.**

- **Modular monolith:** The backend is developed and initially deployed as one application/codebase, not as independently deployed microservices.
- **Ports and adapters:** Business workflows depend on contracts defined in the application core; concrete databases, file parsers, vector indexes, and LLM clients implement those contracts.
- **Dependency inversion:** The domain does not import infrastructure, API frameworks, databases, or provider SDKs. Application use cases depend on domain concepts and ports, not on concrete adapters.
- **Pragmatic scope:** Introduce a port only when an actual use case needs an external capability or an independently replaceable boundary. Do not create one interface per class or function.
- **Separate concerns:** Document management, ingestion, search, investigation, and evaluation remain distinct functional modules inside the same application.

This combines Onion/Clean Architecture's inward dependency rule with Hexagonal Architecture's emphasis on integration boundaries. These terms overlap; we are adopting useful constraints, **not** implementing a framework's prescribed folder hierarchy for its own sake.

## 3. Responsibilities and allowed dependencies

| Boundary | Owns | May depend on | Must not depend on |
| --- | --- | --- | --- |
| **Domain** | `Document`, `DocumentVersion`, `Content`, provenance/value objects, and domain invariants | Python standard library and other deliberately framework-neutral code | FastAPI, ORM models, Qdrant/pgvector SDKs, Hugging Face clients, LLM APIs |
| **Application** | Use cases, orchestration, transaction boundaries, external capability **ports**, indexing eligibility rules, investigation workflow | Domain and abstract ports | Concrete SQL queries, file parsing SDKs, vector DB clients, vendor-specific LLM calls |
| **Infrastructure** | Repository implementations, document-format/dataset adapters, persistent storage, keyword/vector search clients, embedding and LLM implementations | Domain and application-defined contracts | HTTP response formatting and unrelated UI behavior |
| **Presentation** | FastAPI routes, request/response validation, authentication entry points, error mapping and UI/API delivery | Application use cases and transport DTOs | Document-versioning or deduplication rules implemented directly in controllers |
| **Composition root** | Construct/configure concrete adapters and inject them into use cases at startup | All required layers | Domain-specific business logic |

**Import direction (source-code dependencies):**

```text
Presentation ────────────────> Application ────────> Domain
                                  ^                   ^
                                  |                   |
Infrastructure ───────────────────+───────────────────+

Composition root ───> assembles presentation, application and infrastructure
```

**Execution flow is different from import direction:** An application use case can invoke a PostgreSQL or vector-search adapter through a port supplied at runtime, even though the application code does not import that adapter.

The frontend is a client of the backend API and need not use the backend's internal Python layers.

## 4. Functional modules within the monolith

### 4.1 Document management

Owns logical document creation, version registration, current-version activation, deletion/retention policies, and independently attributable source identity.

Per ADR-004, two uploads with exactly the same text are **two documents**, not versions of one document. A new version belongs to an existing logical document only when that document is explicitly updated.

### 4.2 Ingestion

Coordinates source adapters, text extraction, the canonical text representation, structure/location mappings, exact-content lookup and reuse, and scheduling downstream processing.

- Initial sources: 2WikiMultihopQA Parquet, PDF, DOCX and TXT.
- Adapters convert format-specific input into common domain/application data contracts.
- A document version references one complete canonical extracted-text representation.
- Exact duplicate reuse is based on **identical canonical text**, with a SHA-256 fingerprint used for lookup; no whitespace-normalized or semantic duplicate merging.
- Structure and source-location mappings belong to the corresponding **document version**, even when content is shared.
- Dataset questions, benchmark answers and gold evidence annotations stay in **evaluation**, not the searchable evidence corpus.

### 4.3 Search and indexing

Owns derived chunks, chunking strategy/configuration identity, embeddings, semantic retrieval, keyword retrieval, and planned custom Reciprocal Rank Fusion (RRF).

Search storage is **rebuildable** from authoritative documents/content and recorded processing configurations. The physical solution may use separate databases or combine relational and vector functionality (for example, PostgreSQL + pgvector); the boundary is conceptual and does not require different products.

Do not assume one shared content record always means one reusable chunk set: structure-aware chunking may depend on a document version's structure. Embedding reuse additionally requires equal effective inputs and model/configuration.

### 4.4 Investigation

Owns multi-step question processing, retrieval coordination, evidence resolution, source attribution, attempts to identify contradictory evidence, abstention for insufficient evidence, structured tool invocation, and LLM answer generation.

An evidence citation must resolve through eligible source associations, rather than treating a vector hit as a self-sufficient source of truth:

```text
Answer → retrieved result → chunk/span(s) → canonical content
       → eligible document version → source location(s) → cited source
```

Semantic redundancy reduction may be added **at retrieval time** after evaluation. We do not automatically delete semantically similar material from the index.

### 4.5 Evaluation

Owns question sets, ground truth, retrieval/answer metrics, configuration comparisons, failure analysis and reproducible run manifests. It must not leak expected answers or gold reasoning paths into the retrieval corpus.

## 5. Initial package organization

The following is a **target layout**, not a requirement to create empty directories immediately:

```text
ai-investigation-engine/
├── docs/
│   ├── architecture/
│   ├── decisions/
│   ├── design/
│   └── validation/
├── data/
├── src/
│   └── investigation_engine/
│       ├── domain/
│       │   ├── content.py
│       │   ├── document.py
│       │   ├── document_version.py
│       │   └── provenance.py
│       ├── application/
│       │   ├── ports/
│       │   ├── use_cases/
│       │   ├── ingestion/
│       │   ├── search/
│       │   └── investigation/
│       ├── infrastructure/
│       │   ├── ingestion/          # PDF, DOCX, TXT, 2Wiki adapters
│       │   ├── persistence/        # database/object-store adapters
│       │   ├── search/             # keyword/vector implementations
│       │   ├── embeddings/
│       │   └── llm/
│       └── presentation/
│           └── api/                # FastAPI routers and DTOs
├── scripts/                       # exploration and maintenance utilities
├── tests/                         # added as functionality matures
├── pyproject.toml
└── uv.lock
```

- Keep existing `src/investigation_engine/domain/content.py` in the domain layer.
- Use **UUIDv7** for newly generated logical document and version identifiers (Python 3.14 built-in `uuid7`); do not use content hashes as user-facing document identities.
- The existing `src/investigation_engine/ingestion/` exploration scripts need **not** be moved immediately. Relocate them only when their responsibilities are clearer and imports can be updated safely.
- Keep implementation and relevant tests close to real use cases; defer empty ports, placeholder repositories and unused abstractions.

## 6. Example workflow: update and re-index a document

1. Presentation receives an update request for an existing logical document.
2. Application authorizes/validates the operation and invokes the appropriate source adapter.
3. Adapter extracts canonical text and version-specific structure/provenance.
4. Application finds or registers immutable `Content` using **exact** text identity.
5. Application persists a **new** `DocumentVersion`; the old version and its provenance remain historical.
6. Chunking/indexing prepares the new searchable representation, reusing safe derived artifacts where applicable.
7. After successful preparation, application changes the authoritative current-version pointer **atomically within its database transaction**.
8. Normal retrieval immediately excludes the old version using authoritative eligibility checks; obsolete vector/index associations are cleaned up later. Shared vectors remain if an active permitted document still requires them.
9. Failures are recorded and retryable; an incomplete new version must not silently become active.

This is a **target consistency policy**, not a claim that distributed database and vector-index updates form one atomic transaction. Background jobs, retry/outbox techniques and exact transaction mechanics remain implementation decisions.

## 7. Deployment and operational policy

- **Initially one backend deployment** and a functional UI; independent microservices are not justified by current scale or team structure.
- Persistent authoritative storage is required across restarts; do not rely on ephemeral process memory or the vector index as the only record of source documents.
- An ingestion worker may later run separately from the API **using the same codebase and use cases** if latency/hosting measurements justify it. This does not require microservices.
- Keep secrets and provider-specific settings in infrastructure/configuration, not domain entities.
- Track ingestion/indexing state, failures, retry attempts and basic performance metrics so operational failures are diagnosable.
- Where users have distinct access permissions, resolve shared vectors only to **authorized, current, nondeleted** source versions. Content sharing must not imply permission sharing.

## 8. Alternatives considered

| Alternative | Advantages | Why not selected for this project |
| --- | --- | --- |
| **Simple tightly coupled layered CRUD application** | Fastest initial implementation, little boilerplate | Risks placing database/LLM/indexing logic in routes and complicates multiple adapters and source/version rules |
| **Full enterprise Onion/Clean Architecture with extensive interfaces and services** | Strict boundaries, many extension points | Extra indirection and files without demonstrated need; risks overengineering a time-limited assignment |
| **Microservices for ingestion, retrieval, and investigation** | Independent deployments and scaling | Unnecessary network, consistency, observability, deployment and cost overhead for the initial scope |
| **Provider/framework-centric application** | Convenient SDK integration | Makes provider substitution, evaluation configurations and persistence changes harder |
| **Selected: modular monolith + lightweight hexagonal boundaries** | Clear ownership, replaceable external integrations, straightforward deployment | Requires consistent dependency discipline and a small amount of explicit orchestration |

## 9. Consequences and trade-offs

**Benefits**

- Dataset and file-format adapters can evolve independently of document identity/versioning rules.
- Database, vector index, embedding model and LLM providers remain replaceable behind useful boundaries.
- Search/index records can be rebuilt without losing original source lineage.
- Use cases are explainable and can be exercised without requiring live external services.
- One backend is simpler to deploy, debug and operate under free-hosting constraints.

**Costs and risks**

- Port contracts and dependency injection add limited but real overhead.
- A modular monolith relies on developer discipline; mere directory boundaries do not prevent accidental cross-layer imports.
- Multiple external stores create consistency/retry issues even inside one backend deployment.
- Shared content/vector reuse complicates version-aware citations and permission filtering; these must be designed explicitly rather than inferred from index payloads.

## 10. Acceptance criteria (to be demonstrated during implementation)

1. Domain models import no FastAPI, ORM, vector database, or LLM SDKs.
2. API routes call application use cases instead of implementing versioning, deduplication, or retrieval ranking directly.
3. At least two source adapters feed the same ingestion contract without changes to domain entities; target is 2Wiki, PDF, DOCX and TXT.
4. An application use case is able to work through a repository/search port without importing concrete backend classes.
5. Updating a document does not make outdated evidence eligible for ordinary retrieval, even if index cleanup is delayed.
6. Two independent documents with identical canonical text can reuse content while retaining distinct versions, structure, permissions and citations.
7. Changing chunking strategy or embedding provider does not alter original document identity/provenance.
8. Benchmark ground truth is never indexed as a shortcut for answering evaluation questions.
9. Backend and UI can be publicly deployed with persistent authoritative state; actual hosting and load capability are verified separately.

These are **design acceptance criteria**, not completed test results. Automated tests can be added after the initial functional implementation, as currently planned.

## 11. Open choices (not decided by this ADR)

- Authoritative persistence provider and schema (PostgreSQL, SQLite for prototyping, etc.).
- Vector search provider, BM25 implementation, embedding/LLM providers and model versions.
- API/frontend framework details beyond the proposed Python/FastAPI direction.
- Synchronous ingestion versus a persistent queue/worker and exact retry/outbox mechanism.
- Concrete port signatures, extraction libraries, authentication policy and production permissions.
- Deployment host, resource budgeting and storage retention periods.

**Decision rule:** Adopt an additional layer, process or abstraction only when a concrete use case, reliability requirement or measurement justifies it. Record consequential changes in a new ADR or amendment.

## References

- [ADR-004 — Document identity, versions, reusable content and provenance](004-document-content-provenance.md)
- [Jeffrey Palermo — The Onion Architecture](https://jeffreypalermo.com/2008/07/the-onion-architecture-part-1/)
- [AWS Prescriptive Guidance — Hexagonal architecture](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/hexagonal-architecture.html)
- [FastAPI — Bigger applications](https://fastapi.tiangolo.com/tutorial/bigger-applications/)
