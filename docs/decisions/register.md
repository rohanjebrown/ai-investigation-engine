# Architectural decision register

**Reconciled:** 2026-10-28\
**Owner of architectural decisions:** the user  
**Scope:** approved baseline, unresolved decisions, approval evidence, and links to existing ADRs. This register does not authorize implementation of an unresolved choice.

## Task 17 deployment implementation evidence

On **2026-10-28**, the user explicitly authorized production Docker packaging
and a GitHub `main` → Cloud Build → existing Cloud Run workflow in
[Task 17](C:/Users/merli/.codex/attachments/9a92f89c-7630-4bce-bdea-038a3c44d44c/Pasted%20text.txt).
This scopes U18 implementation to the existing Cloud Run/Cloud SQL/Artifact
Registry resources, locked Python 3.14/uv image, non-root single-process runtime,
bounded database pooling, managed SQL socket support, mandatory offline tests,
commit/digest deployment, preserved settings and controlled manual migrations.
It does not authorize Git staging/commit/push, model-default changes, database
replacement/reset, schema/ranking changes, unrestricted public Gemini, billing
changes or another deployment platform. The
[deployment guide](../design/cloud-run-deployment.md) distinguishes implemented
configuration and local evidence from the unverified first push/rollout.
Missing runtime secrets/model/profile remain activation blockers. Existing
runtime IAM is recorded, not silently replaced. This evidence does not broaden
earlier unrelated decisions or claim a successful production deployment.

The U18 trial/provisioning descriptions below are historical. The current Task
17 inventory verifies an existing billed project, Cloud Run placeholder and
Cloud SQL instance; trial eligibility and zero-cost operation remain unverified.

## Authority and status rules

The user's approval protocol applies to architectural choices of every size, including dependencies, schemas, interfaces, processing, errors/retries, concurrency, APIs, ranking, evaluation, and infrastructure. Routine code details may proceed only when they do not introduce a design choice, change an established design, or alter observable behavior.

- **CONFIRMED:** explicitly approved by the user; record the approval evidence and exact scope.
- **UNRESOLVED:** discussed without a final selection.
- **NEW:** newly identified and not yet discussed/selected.
- **SUPERSEDED:** a previously approved decision explicitly replaced by a later approved decision; record both the predecessor and replacement.

Implementation state, testing evidence, and deferral are separate from decision status. Existing code, an assistant recommendation, an ADR's historical label, and silence are not approval evidence. Do not relabel an unaccepted proposal as SUPERSEDED.

Before implementing a NEW or UNRESOLVED choice, present its context, alternatives, trade-offs, recommendation, and consequences, and wait for explicit user approval. Related decisions may be discussed together; a group approval confirms only its stated scope. Keep each entry current and add finer-grained entries when a group exposes another consequential choice. Unanswered questions never select a default.

The assignment defines mandatory outcomes; the checkout establishes implemented behavior; explicit user approval establishes architectural choices. If these conflict, report the conflict and ask the user rather than silently choosing. Recommendations in this register remain recommendations even though the user authorized creating the register.

## Approval evidence

These entries transcribe evidence from the current Codex chat. E01-E06 were recorded on **2026-10-26**; E07-E20 were recorded on **2026-10-25**. E21-E28 were recorded on **2026-10-27**; E29-E40 on **2026-10-28**. They are not claims that the complete older ChatGPT conversation was independently recovered.

### E01

The assistant asked whether the user explicitly confirmed the handoff's reported approvals: Python/uv/PyArrow, 2Wiki first, lightweight hexagonal monolith, UUIDv7, document/version separation, exact-text reuse without normalized or semantic merging, version-specific spans, and activation after indexing. The question expressly excluded database choices, RRF, new interfaces, and other proposals. The user selected **"Confirm baseline (Recommended)"**. This is direct approval evidence for A01-A09; historical ADR labels are not the evidence.

### E02

When asked whether to add focused tests now or keep them deferred, the user selected **"Keep tests deferred"**. This confirms A10's immediate sequencing preference, not permanent omission or a specific later testing milestone.

### E03

The assistant asked for the decision-register structure, expressly limiting the choice to documentation organization. The user selected **"Index + ADRs (Recommended)"**: use `docs/decisions/register.md` for status, approval evidence, alternatives, rationale, and consequences, with links to existing ADRs. This confirms A11.

### E04

The user subsequently requested **"PLEASE IMPLEMENT THIS PLAN"** and supplied the reconciled review and approval-process plan, including A01-A11, U01-U18, N01, and documentation-only acceptance criteria. This authorizes recording these decisions and reconciling ADR statuses. It does not approve unresolved technical recommendations or additional documentation organization.

### E05

The user supplied the Mandatory Architectural Decision Approval Protocol as their request. Its core rule is explicit user selection before implementation of an unresolved/new architectural choice. The [pasted request](C:/Users/merli/.codex/attachments/f9ef2529-e31c-4867-85f8-232a379ff3dd/Pasted%20text.txt) is external to the repository; the governing rules are summarized above.

### E06

The user explicitly approved the following in the current chat:

> **U01:** Use one logical Document and DocumentVersion per dataset context occurrence. Preserve independent source identities and reuse identical Content through our existing deduplication mechanism. Do not introduce a separate occurrence entity.

> **U02:** Assemble 2Wiki sentences using a single ASCII space. Preserve original sentence strings, exact character offsets, and source locators. Keep titles separate from canonical content.

For **U04**, the user selected two separate database-independent structured fields in SourceReference: **record_locator**, for source-specific information needed to identify the original record, and **metadata**, for additional descriptive source information. The 2Wiki locator contains dataset split, question ID, and context position. PostgreSQL JSONB is the **preferred persistence candidate**, not an approved physical schema.

The user authorized updating the register and requested inspection and a proposal before changing application code. At that point, concrete Python types, defaults, mutation protection, legacy-field compatibility, and validation were not selected (N02-N05); E07 records their subsequent approval. The earlier assistant proposal to encode coordinates in record_id was never approved; it is not a SUPERSEDED approved decision.

### E07

On **2026-10-25**, the user explicitly approved N02-N05 with the following choices and authorized implementation:
1. Replace unused record_id with record_locator; add metadata; preserve all other existing fields.
2. Use standard Python recursive JsonValue and JsonObject aliases supporting nested JSON-compatible structures.
3. Use field(default_factory=dict) for both fields.
4. Retain standard domain dataclasses; introduce no domain Pydantic or PostgreSQL dependency.
5. Use Pydantic where appropriate for external input validation while protecting domain invariants.
6. Prefer simple defensive copying over a custom deeply immutable JSON framework; no deep in-memory immutability guarantee is required now.
7. Preserve compatibility with the existing TXT adapter and registration workflow.

The scalar-only/read-only mapping proposal in N03 was never approved and has not been implemented. Standard-library deepcopy implements the approved defensive-copy approach without a new abstraction. The existing nonblank source_id invariant remains enforced. Type aliases describe JSON values but do not validate arbitrary runtime input; external validation belongs at input boundaries. No new external input boundary or Pydantic dependency is introduced by this change.

### E08

On **2026-10-25**, the user approved a 2Wiki-specific extraction policy: descriptive ValueError exceptions for malformed records, missing required fields, invalid types, empty passages, and empty/whitespace-only sentences; no text normalization, silent skip/repair/substitution, failed-record persistence, fallback, or added dependencies. Errors must include relevant identifiers/positions, and batch error handling stays outside the adapter. PDF/DOCX/OCR and other extraction policies remain unresolved.

The user authorized a 2Wiki adapter accepting a parsed record and split, yielding one ExtractedDocument per context occurrence with single-space assembly, exact sentence spans, original ordering and zero-based locators, structured provenance, and deterministic source identifiers. Gold question/answer/supporting-fact fields must not become source text. Existing registration integration is authorized where it needs no further architectural changes.

### E09

On **2026-10-25**, the user clarified SourceReference responsibilities in their [pasted request](C:/Users/merli/.codex/attachments/76ab6dfc-840a-472b-8077-d41a2f967a56/Pasted%20text.txt): kind categorizes the original source; source_id identifies that source, not an extracted passage; revision identifies its version when applicable; record_locator locates a record within it; display_name labels it for humans; metadata contains additional description. These meanings must stay consistent across formats. Source identity, revision, and the structured locator together identify a passage occurrence, separately from content or Document identity.

The user explicitly rejected the composite JSON-array source_id proposal. Source-specific information belongs in JsonObject/JsonValue fields; stable universal concepts retain explicit fields. Avoid duplicated information without a reason, encoded structured strings, source-specific domain fields, and unnecessary abstractions. The example locator uses split, example_id, and context_position. Use the real dataset identifier and revision, not illustrative values. PostgreSQL JSONB remains only a persistence candidate.

The user authorized proceeding with the adapter after inspecting existing identity assumptions and verifying compatibility. The inspection found no source_id uniqueness assumption in registration or exact-content reuse. N06 records the clarified contract; this is not approval for source-specific domain abstractions, persistence schemas, or retry behavior.

### E10

On **2026-10-25**, the user explicitly approved U03: use the complete 2WikiMultihopQA development split at the existing pinned revision; use unique exact-content passages as the conservative counting baseline for the assignment's 50,000-record requirement; hash original extracted UTF-8 text with SHA-256 without normalization or semantic deduplication. Preserve all 125,760 occurrences with individual provenance and reuse identical Content.

The user approved reporting 125,760 passage occurrences, 56,680 unique exact-content passages, 69,080 duplicate occurrences beyond the first, and 125,760 unique source-coordinate records separately, excluding chunks from the independent-record count. Exact-content uniqueness is not proof of semantic independence. Do not add splits or arbitrary subsets merely to meet the threshold. Preserve revision, split, example ID, and context position; keep question and answer labels outside the retrieval corpus. Evaluation methodology and potential leakage remain U17. This request authorizes documentation updates only, not additional functionality or changes to the adapter, domain, dependencies, or content reuse.

### E11

On **2026-10-25**, the user approved U08 implementation for sequential calls sharing one in-memory repository. Identical source occurrence/revision/text/spans returns the existing registration; different occurrences retain separate registrations and share identical Content. Changed text or structural/source spans under the same identity raises a descriptive conflict without modifying stored registrations. Descriptive title/display_name/metadata differences do not update the stored registration on retries.

Identity remains kind, source_id, revision, and record_locator. None is an absent revision, not a wildcard; callers preserve source IDs across retries and use different IDs for independent uploads. Locator dictionary order is insignificant, list order significant, missing differs from null, and strings compare exactly. The user refined the numeric proposal: 1 and 1.0 compare equally, but booleans remain distinct from numbers. Internal hashable representations stay private to the repository; adapters validate source-specific structures.

The user approved returning the actual stored Document/DocumentVersion pair, keeping lookup/conflict/registration in one repository operation, adding an occurrence index, and defensively copying mutable data without unnecessarily copying immutable Content. Cross-revision updates, durable/concurrent idempotency, transactional guarantees, and batch recovery are excluded. No new dependencies, persistence, bulk ingestion, domain identities, or automated test files are authorized.

### E12

On **2026-10-25**, the user explicitly selected PostgreSQL as the authoritative relational database and requested a documentation-only [PostgreSQL versus MySQL ADR](postgresql-vs-mysql.md), based on the actual domain, provenance, repository, U03, and U08 contracts. The [pasted request](C:/Users/merli/.codex/attachments/2cfba1b4-9f04-4d9f-947d-a4ebb18d5799/Pasted%20text.txt) authorizes recording U05 as CONFIRMED and verifying technical claims against official documentation. MySQL/InnoDB remains a valid alternative; the justification must emphasize architectural fit rather than unsupported performance superiority.

The approval selects no ORM/driver, physical schema, migrations, hosting, pooling, transaction implementation, batch recovery, vector database, or embedding storage. pgvector is expressly excluded from this comparison and rationale. Domain models remain independent of persistence types. Future derived retrieval stores do not replace PostgreSQL's authority for the relational application data covered by this decision. No application changes, dependencies, schemas, new abstractions, or persistence implementation are authorized by this task.

### E13

On **2026-10-25**, the user supplied a [documentation-only request](C:/Users/merli/.codex/attachments/833d661c-d940-4d8c-bc23-dbec1a3e7008/Pasted%20text.txt) explicitly approving the following technology directions and three ADRs:

- **U12:** PostgreSQL with pgvector for vector storage and semantic search; no separate vector database initially. Concrete retrieval interfaces, vector schema, chunking/model/dimensions, distances, ANN configuration, query parameters, targets, and evaluation remain unresolved.
- **U13:** Native PostgreSQL full-text keyword retrieval and a custom application-owned Reciprocal Rank Fusion implementation. Built-in ranking is not claimed to be BM25; detailed keyword/fusion behavior remains unresolved.
- **U18:** Google Cloud SQL PostgreSQL free trial as the intended database host for the demonstration, conditional on eligibility, actual resources/extension capability, and zero-cost feasibility. This does not select backend/frontend hosting or authorize provisioning, networking, automation, backups, monitoring, or paid services. Timeline assumptions are not confirmed interview dates.
- **U06:** SQLAlchemy 2.x ORM, psycopg 3, asynchronous PostgreSQL operations with AsyncSession, and Alembic migrations. Infrastructure owns ORM models; domain dataclasses remain independent. I/O interfaces are asynchronous, pure computation synchronous, with CPU-heavy work managed separately where needed. Current synchronous contracts still need a later implementation change. Schema/mappings, session lifecycle, pool sizes, transaction isolation, repository details, migration scripts, and deployment configuration remain unresolved.

The request preserves U05 and U08 and forbids application/domain/interface changes, dependencies, schemas, migrations, infrastructure, or retrieval implementation in this task. It authorizes documenting capabilities, approval boundaries, alternatives, risks, and deployment assumptions using official references. The three ADRs record these distinct scopes; their technology selections are not implementation evidence.

### E14

On **2026-10-25**, the user requested a [U07 design revision](C:/Users/merli/.codex/attachments/d5cf58d3-2e47-4714-bd60-cd5e15f3c64c/Pasted%20text.txt) and explicitly selected **initial soft deletion**: retain documents.deleted_at; a document is active when deleted_at IS NULL. Soft-deleted documents retain their logical record, historical versions, provenance, source spans, and shared Content, but are excluded from ordinary listings, semantic/keyword retrieval, and investigation evidence. Deleting one document must not make other active documents sharing its Content unavailable. Physical deletion and Content garbage collection are excluded from initial implementation.

The user established the intended access requirement: shared documents such as 2Wiki are accessible to all authorized users; private uploads are accessible only to their owners. Physical ownership representation, identity linkage, and authorization enforcement remain unresolved. The user favors the prior three-table U07 proposal but expressly states this is not authorization to implement it or confirm U07. Re-registration/restoration after deletion must be evaluated without silently changing U08. The current_version_id treatment on deletion, authority to delete shared documents, and transaction/concurrency mechanisms were not selected.

This task permits recording the approved scope in this register only. No new documentation file, application/domain/contract change, schema, migration, authentication, persistence, deletion, or retrieval implementation is authorized.

### E15

On **2026-10-25**, the user explicitly approved U07 and authorized the physical schema/ORM/Alembic foundation only. Use documents, document_versions, and contents; binary 32-byte SHA-256 Content primary keys with a text-consistency check; UUIDv7 document/version identities; positive per-document unique version numbers; Content/document FKs; nullable active-version pointer with composite ownership enforcement. Preserve immutable-history semantics at the application-contract level without triggers or permission schemes.

The user approved explicit SourceReference columns, text SourceKind validation, JSONB object locator/metadata fields, ordered JSONB structure/source-span arrays with top-level checks, and global UNIQUE NULLS NOT DISTINCT over kind/source_id/revision/record_locator. No owner-scoped identity, composite serialized ID, or derived identity fingerprint is selected. Nested span validation remains at the application/persistence boundary. Exact-text Content verification and collision errors remain required.

Ownership is nullable documents.owner_id TEXT: NULL is shared/system-managed; non-NULL identifies a stable private owner. Trusted creation must explicitly assign ownership; untrusted omission cannot grant shared access. No visibility column, users table, authentication, or permission framework is authorized. Soft deletion retains records, Content, provenance/spans, and current_version_id, while normal retrieval/evidence excludes deleted/inaccessible documents. An authorized identical retry after deletion returns the existing IDs/deleted state without restoration; changed text/spans conflict, and authorization must precede disclosure of another owner's registration or conflicts.

The user approved PK/ownership/version/occurrence/Content-reference indexes, but expressly withheld the owner/creation partial listing index and speculative GIN indexes. The request authorizes necessary approved-stack dependencies and schema/migrations, not a PostgreSQL repository, application-wide AsyncSession lifecycle, transaction/isolation/locking/retry policy, activation, authentication, endpoints, restoration, physical deletion, bulk ingestion, retrieval tables/indexes, or cloud provisioning. Temporary focused verification is authorized; no automated test files are to be added.

### E16

On **2026-10-25**, the user explicitly [approved implementation of eight registration-specific U14 choices](C:/Users/merli/.codex/attachments/aa2695bb-a696-46cd-8e37-ab94d10b0375/Pasted%20text.txt): repository-owned sessions/transactions; READ COMMITTED with existing constraints; targeted Content conflict handling and exact-text checks; full rollback followed by bounded winner resolution; one consistent async I/O contract; required trusted ownership; named application-facing errors; and no general automatic retries, with cancellation cleanup and uncertain-commit semantics.

The user requires an explicit owner_id on Document and RegisterDocument.execute, without an implicit shared default, blank principal rejection without normalization, and owner compatibility before disclosure. System-managed ingestion explicitly passes None; future uploads must obtain ownership from authenticated context. This contract is not authentication. Include deleted registrations in retry lookup and preserve their stored identities, descriptions, timestamps, deletion state, and current-version pointer. Keep domain dataclasses independent and ORM translation private. Do not change U07's schema or add dependencies, Unit of Work, public API, arbitrary timeout/pool settings, lifecycle/update/indexing mechanisms, or permanent automated test files. Real isolated PostgreSQL and temporary coordinated verification are explicitly authorized. U14 is not fully resolved.

### E17

On **2026-10-25**, the user [approved deterministic chunk generation and its immediate contracts](C:/Users/merli/.codex/attachments/9620594d-0a47-4ccd-82bf-6aff7f23bcd1/Pasted%20text.txt). Approve the whole-passage baseline with deterministic oversized splitting, fixed-token windows with initial experimental 128-token content budget/16-token overlap, and supplied-sentence packing with initial experimental 128/0. Preserve exact original Python-character slices, all source characters, original ordering, and version-specific structure. Never reconstruct canonical text by decoding token IDs or combine independent passages. Obtain approval for a new tokenizer dependency or consequential sentence fallback.

Approve conceptual range identity (content_sha256, start, end), distinct version/configuration memberships, reproducible configuration identity, and future embedding reuse only for equivalent effective inputs and encoding settings. Titles remain outside canonical Content. Hosted inference is preferred; Gemini Embedding 2/768 and cosine are experimental, not production selections, and title-aware inputs remain an unresolved comparison. Authorize updating the register/relevant design documents and a focused ADR. Exclude embedding calls/generation/storage, derived tables, vector/full-text indexes, retrieval/RRF, orchestration, activation, recovery, endpoints, LLM integration and deployment. Preserve registration, U07 schema/migrations, and Content reuse. Temporary real-data verification is permitted; A10 still excludes permanent test files.

### E18

After inspecting installed dependencies and TXT structure, the assistant presented three explicit choices in this chat. The user approved all three: **"Approve experimental tokenizer adapter and dependency (recommended)"**, **"Approve this deterministic fallback (recommended)"**, and **"Return no chunks for empty Content (recommended)"**.

This approves tokenizers==0.23.3 and a small file-loading adapter for the pinned Voyage reference artifact, without implicit runtime downloads or a production provider/tokenizer selection. Sentence packing falls back to fixed-token splitting with zero overlap when supplied sentence spans are absent or leave non-whitespace text uncovered. Preserve whitespace gaps; overlapping or out-of-bounds sentence spans raise ValueError. Empty canonical Content yields an empty tuple; nonempty whitespace-only text is preserved. None of these choices establishes indexing readiness.

### E19

On **2026-10-25**, the user specified DB_* process settings loaded locally with `uv run --env-file .env`, the existing Cloud SQL Auth Proxy at 127.0.0.1:15432, sslmode=disable for that local proxy connection, and SQLAlchemy URL.create. After the read-only cloud verification and proposal to share infrastructure connection settings between Alembic and future application bootstrap, the user approved proceeding and requested "a config file for all env variables". This authorizes centralizing the existing database/Gemini settings and wiring Alembic to them; it does not authorize migrations, derived schema, embedding calls, lifecycle changes, or new dependencies.

### E20

On **2026-10-25**, the user approved python-dotenv automatic .env loading,
process-environment precedence, a flat frozen standard-library dataclass and one
reusable imported settings instance. This supersedes E19's explicit factory and
uv-only loading approach, preserving approved proxy transport and domain separation.
Only currently needed fields and the python-dotenv dependency are approved.

### E21

On **2026-10-27**, the user [approved implementation of the revised five-table
storage proposal](C:/Users/merli/.codex/attachments/b0ea1a47-de6e-430c-9169-3d3c2ae300ea/Pasted%20text.txt):
chunking_configurations, embedding_configurations, version_chunk_plans,
chunk_memberships and chunk_embeddings. Membership Content derives through its
DocumentVersion; no additional version/Content key, chunk entity/copied text,
title/context hashes or indexing-state table. Approve the proposal's keys,
checks and essential indexes, maintained pgvector integration after Python 3.14
verification, a new migration after unchanged 0001, isolated verification and
focused documentation. The experimental profile is Gemini Developer API,
gemini-embedding-2, 768 dimensions, cosine, documented no-title document and
retrieval-query formats, VECTOR(768) and exact search. Production provider and
remaining U14 lifecycle remain unresolved. No cloud migration, embedding/API
calls, persistence workflows, retrieval, indexing, permanent tests, staging, commits or pushes
are authorized.

### E22

On **2026-10-27**, the user [approved introducing the permanent test suite](C:/Users/merli/.codex/attachments/453b1531-cb26-41f7-a60c-e73f50a7284c/Pasted%20text.txt), [reaffirmed in the latest request](C:/Users/merli/.codex/attachments/a0acced8-7dc1-4e8c-aeb9-f96b6dc88452/Pasted%20text.txt):
pytest, pytest-asyncio, pytest-cov, unit/integration/external categories,
Testcontainers with real PostgreSQL/pgvector, real commits with disposable cleanup,
deterministic ordinary-test fakes where an actual interface exists, optional
pinned-corpus checks, explicit opt-in live smoke tests, statement/branch coverage
without a global threshold, GitHub Actions and tests alongside future features.
This ends A10's temporary deferral. The authorization excludes cloud migration,
Gemini calls, unimplemented feature workflows and Git staging/commits/pushes.
Production defects must be reported rather than silently changing behavior.

### E23

On **2026-10-27**, the user [approved implementing hosted Gemini embedding generation](C:/Users/merli/.codex/attachments/9ee7e592-6da1-465d-b32d-f630ec9137d0/Pasted%20text.txt).
Use a minimal asynchronous application port with ordered immutable vectors and
the maintained google-genai SDK after Python 3.14 compatibility verification.
The initial profile is Gemini Developer API, gemini-embedding-2, 768 dimensions,
no client normalization, gemini_no_title_v1 document formatting and the versioned
retrieval-query envelope. Validate complete formatted inputs, model-specific
limits and responses; preserve cancellation, manage clients and expose meaningful
application errors. Investigate model-specific token counting without assuming
Voyage counts apply. Do not truncate, split, fall back or add a retry framework.
Add offline tests and an explicitly gated live smoke test, but do not execute
live calls. Existing configuration supplies credentials. This approval excludes
persistence, migrations, database/cloud access, another Docker download,
indexing/retrieval/orchestration, U14 lifecycle resolution, registration parity
fixes and Git staging/commits/pushes.

### E24

On **2026-10-27**, the user explicitly requested **"Align In-Memory and PostgreSQL
Document Registration Behavior"** in this chat. New registrations must use version
1 and have no active-version pointer. Preserve valid source retries, ownership,
Content reuse and atomicity; resolve existing registrations before initial-state
validation. Authorize the minimal correctness fix, conversion of the two xfails
and focused regression tests. Exclude new dependencies, migrations, unrelated
refactoring, external/cloud access, image downloads, infrastructure changes,
Git staging/commits/pushes and subsequent persistence features.

### E25

On **2026-10-27**, the user [approved persistent chunk-plan storage](C:/Users/merli/.codex/attachments/c146a595-3745-45f7-acfb-79502cf70857/Pasted%20text.txt).
Implement a focused application port and PostgreSQL persistence/reconstruction
using the existing configuration, plan and ordered membership tables. Identical
plans must be reused, conflicts rejected, partial writes rolled back and concurrent
saves protected using existing database mechanisms. Validate canonical Content,
version association, ranges and ordering without assuming non-overlap or modifying
text. Investigate blank chunks while preserving complete-plan contracts. Add pure
validation and real PostgreSQL tests; report unavailable-image verification limits
without downloading. No new dependencies/tables, implicit schema changes, embedding
persistence, activation, workers, retrieval, Gemini/Cloud SQL access, infrastructure
changes or Git staging/commits/pushes are authorized.

### E26

On **2026-10-27**, the user [approved resumable embedding persistence and safe activation](C:/Users/merli/.codex/attachments/a3f7824e-a5fd-4f43-be42-c9ccfef3a66e/Pasted%20text.txt).
Preserve complete plans but embed only nonblank ranges; empty/blank versions
return an explicit non-searchable outcome and cannot activate. Reuse exact
Content/range/configuration vectors, validate float32 storage and persist each
successful result incrementally. Compose provider-independent application ports
with short repository-owned transactions and no locks/sessions during external
calls. Resume from stored progress, use simple configurable bounded transient
retries, and transactionally verify complete selected-profile coverage before
activation. Prevent deletion/stale-version races and preserve prior active state
on failure. Reuse existing tables; a necessary schema change would require prior
explanation. Use synthetic providers and run all unit/real-PostgreSQL tests.
Exclude Gemini/Cloud SQL calls, cloud changes, semantic/hybrid retrieval, RAG,
endpoints, a full update workflow, distributed workers and Git staging/commits/pushes.

### E27

On **2026-10-27**, the user [authorized exact semantic retrieval with source citations](C:/Users/merli/.codex/attachments/37ddcc19-a59e-4757-be18-27a4263175f0/Pasted%20text.txt).
Use the existing query embedding port/format, one explicitly selected serving
chunking/embedding configuration, exact pgvector cosine search, deterministic ties
and bounded results. Ownership from trusted context, deletion, active version,
selected configurations and complete eligible embedding coverage must all filter
before top-K. Preserve independent source occurrences when vectors are shared;
return exact canonical text/offsets and original intersecting locators without
inventing source locations. Prefer the existing schema; explain any necessary
schema correction first. Add focused unit and real PostgreSQL end-to-end tests,
then run full suites using synthetic providers. No Gemini/Cloud SQL access,
dependencies, ANN indexes, hybrid/RRF, LLM, endpoints, workers, cloud changes or
Git staging/commits/pushes are authorized.

### E28

On **2026-10-27**, the user's typed request, **"Task: Implement Single-Step RAG
with Gemini and FastAPI"**, explicitly authorizes a provider-independent single
retrieval/generation workflow, structured Gemini output, configurable model,
bounded context/timeouts/retries, strict citation mapping and explicit abstention.
If no model is already approved, keep it configurable and report a proposed
default; this does not approve a production model. Introduce minimal FastAPI and
required dependencies, GET /health and POST /questions, validated request/response
models, safe error mapping and lifespan-owned resources without import-time
connections/clients. Unauthenticated requests must access only explicitly shared
sources; never trust arbitrary client owner IDs. Test with fake AI providers,
mocked SDK transport and real disposable PostgreSQL end to end, including no
transaction during generation, then run full regressions. No live Gemini/Cloud
SQL, schema changes, hybrid/RRF, multi-step investigation, uploads, UI, auth
framework, workers, cloud changes, staging, commits or pushes are authorized.

### E29

On **2026-10-28**, the user [authorized the first functional web interface](C:/Users/merli/.codex/attachments/9ef47d66-1522-410a-a94a-5a260ad10508/Pasted%20text.txt).
Use lightweight HTML/CSS/minimal vanilla JavaScript served by the existing
FastAPI app, preferably GET /. Preserve health/questions contracts and shared-only
visibility; reuse the existing RAG operation. Display answer/status, mapped
citations and inspectable exact source excerpts/metadata, with explicit abstention
and safe validation/loading/network/timeout/backend feedback. Prevent duplicate
submissions, preserve or clearly replace prior results, support keyboard and
mobile use, and render untrusted content safely without invented metadata, URLs
or downloads. Add appropriate deterministic tests and run unit/integration checks,
using real PostgreSQL where needed. No frontend framework/build tooling, business
logic, schema/migration changes, management/uploads, auth, hybrid/RRF, multi-step
investigation, live Gemini/Cloud SQL, deployment, cloud changes or Git operations
are authorized. Dependencies may be added only if necessary; none were needed.

### E30

On **2026-10-28**, the user [requested controlled live verification](C:/Users/merli/.codex/attachments/9ae94eff-3ece-48b0-9456-fc4e34934e0d/Pasted%20text.txt),
requiring explicit authorization before external inference. After offline review,
the user confirmed Free tier without billing: embedding-2 100 RPM/30k TPM/1k RPD;
3.6 Flash 5 RPM/250k TPM/20 RPD. The assistant proposed the exact pinned tokenizer
download plus at most seven Gemini requests: three countTokens, three embeddings
at 768 dimensions and one gemini-3.6-flash generation capped at 1,024 output tokens.
One browser question covers retrieval/generation/UI; no retries, substitution or
Cloud SQL, and stop on provider failure/additional chunks.

The user replied **"I authorize but also gemini flash 3.8 is available with the
same rate limiting"**. The authorized run retained 3.6 without substituting a
model. Both sources yielded one whole-passage/128 chunk. All seven real requests
succeeded, and the displayed answer was checked against both original sources.
The [verification report](../validation/live-rag-2026-10-28.md) records actual models,
usage, SQL persistence/ranking, factual support and cleanup. The budget is exhausted.
No production generation default, persistent serving profile, database mode,
architecture, dependency or application code change is selected by this approval.

### E31

On **2026-10-28**, the user authorized [hybrid retrieval and custom RRF](C:/Users/merli/.codex/attachments/1f0ec484-dcca-4f35-9cfc-4c4f7a0cae73/Pasted%20text.txt), then [completion of the interrupted implementation](C:/Users/merli/.codex/attachments/e69ef361-061e-4559-8dd6-eedf612aeb4b/Pasted%20text.txt). Scope: chunk-correct native PostgreSQL FTS, application-owned RRF (default k=60), independent bounded channels, occurrence identity, pre-limit eligibility, trusted semantic/hybrid configuration, regression tests and local scale measurements. Preserve RAG/UI and semantic mode. No Gemini, Cloud SQL, cloud changes, Git publication, new milestone, or unapproved schema migration. The [existing retrieval ADR](pgvector-selection.md#hybrid-retrieval-e31) records the implementation; the [benchmark](../validation/hybrid-retrieval-performance.md) records measured limitations. A persisted lexical index/maintenance design remains a recommendation requiring approval, not an authorized schema change.

### E32

On **2026-10-28**, the user authorized [automated retrieval evaluation](C:/Users/merli/.codex/attachments/d3d54da5-fbdc-4e28-a188-15ac656db798/Pasted%20text.txt): a reproducible 40-case 2Wiki/controlled suite, exact occurrence/sentence relevance, Recall@K/MRR/multihop coverage/latency, fixed-corpus semantic/hybrid comparison, inspectable reports and deterministic unit/real local PostgreSQL verification. Keep evaluation separate from production. Do not call external APIs, index the full corpus, change retrieval/schema/indexes/frontend, access Cloud SQL, deploy or publish Git changes. Answer-quality contracts may be prepared but unexecuted metrics must remain unavailable. See the [evaluation implementation/methodology](../validation/retrieval-evaluation.md). Controlled fake-vector results are not real-model quality evidence.

### E33

On **2026-10-28**, the user authorized [measured PostgreSQL retrieval optimization](C:/Users/merli/.codex/attachments/e7a1fd9b-8a4c-458d-968f-51b0b7f41c19/Pasted%20text.txt). Scope: reproduce 1k/10k/50k local baselines, measure medians/P95/plans/buffers/temp/CPU, investigate completeness/keyword/exact-vector costs, and implement focused schema-preserving changes with unchanged eligibility/ranking/provenance. Any new schema, migration, persistent derived representation or index requires separate approval. No real embeddings, Gemini, Cloud SQL, cloud changes, new feature or Git publication. The [performance checkpoint](../validation/hybrid-retrieval-performance.md#e33-schema-preserving-optimization-2026-10-28) records equivalent boundary-blankness and deferred candidate payload reconstruction, before/after evidence and remaining bottlenecks. Exact-range lexical storage remains a proposal, not a confirmed schema choice.

### E34

On **2026-10-28**, the user authorized [PDF and DOCX ingestion](C:/Users/merli/.codex/attachments/336aebe3-1e5d-48c4-a9be-ea2525fbc943/Pasted%20text.txt): select lightweight established Python libraries, extract readable PDF pages and DOCX main-body paragraphs/tables with metadata, Unicode and exact canonical offsets, reuse existing registration/chunking/indexing/citations, add realistic fixtures and real local PostgreSQL tests. No OCR, uploads, lifecycle operations, retrieval/RRF redesign, cloud access or Git publication. Selected pypdf/python-docx (plus transitive lxml), retaining domain-independent ExtractedDocument and adding only table/row/cell locator kinds. No schema change. The [format contract](../design/file-extraction.md) records parser versions, separators, page/structural coordinates, typed extraction failures, metadata and omissions. Broader parser/OCR, retention and upload security policies remain separate decisions.

### E35

On **2026-10-28**, the user authorized [application-level document updates, soft deletion and listing](C:/Users/merli/.codex/attachments/ca71479f-bae9-4d84-b847-3225bb629775/Pasted%20text.txt), including PostgreSQL/in-memory behavior, ownership checks, bounded pagination, immutable versions, lifecycle concurrency, activation safety and unit/integration tests. New versions require an explicit nonblank revision and retain the logical document's source kind, source ID, and record locator; they use an expected active-version ID. The repository locks the logical document row, allocates the next number, and permits only one pending version. A retry with the same source occurrence/revision and identical text/spans returns that version; changed text/spans conflict. Different revisions create new versions even for identical text, reusing Content. The prior active version remains searchable until activation. Soft deletion is owner-only, idempotent, preserves versions/Content/current pointer and excludes the document immediately; activation rejects deleted or superseded versions. Shared documents are readable but system-managed and cannot be changed by user actors. Listing filters shared plus actor-owned records in SQL and defaults to excluding deleted records. No schema change, HTTP routes, authentication framework, Gemini/Cloud SQL, frontend or Git publication. See the [lifecycle design](../design/document-lifecycle-and-indexing.md) and [test checkpoint](testing-strategy.md#e35-document-lifecycle-checkpoint).

### E36

On **2026-10-28**, the user authorized the [authenticated document-management milestone](C:/Users/merli/.codex/attachments/0f2991b7-7cda-4c6f-a76f-9364c0bcfa4d/Pasted%20text.txt): a single configured administrator, authenticated ownership-bound HTTP lifecycle routes, bounded temporary TXT/PDF/DOCX uploads, explicitly resumed bounded indexing, private-document RAG scope for that administrator, and a lightweight same-origin browser UI. Implementation reuses the existing application registration/update/delete/list/detail/indexing and retrieval use cases; it adds no schema or persistence models. Initial upload source IDs derive from the administrator's UUID `Idempotency-Key`; updates reuse source identity and use that key as explicit revision with `If-Match` concurrency. Browser bearer credentials are held in memory only. Original binary files are discarded after extraction. Indexing is request-bounded and keeps provider calls outside repository transactions. Public unauthenticated questions remain shared-only; valid administrator questions include shared plus that principal's documents. Shared documents remain readable/searchable but system-managed; this administrator cannot index, update or delete them. See [authenticated document management](../design/authenticated-document-management.md). Cloud SQL, live Gemini, migration execution, deployment and Git publication remain out of scope.

### E37

On **2026-10-28**, the user authorized a controlled [EXTERNAL-versus-PLAIN PostgreSQL embedding-storage benchmark](C:/Users/merli/.codex/attachments/a808c5df-4c04-4e64-9f69-1b1ec403f992/Pasted%20text.txt) on disposable local databases only. The [performance checkpoint](../validation/hybrid-retrieval-performance.md#e37-inline-embedding-storage-comparison-2026-10-28) records paired 50k semantic/keyword/hybrid results, exact result-signature matches, storage/TOAST sizes, plan counters, operational trade-offs, and limitations. The current optimized retrieval repository and existing migrations were used; the storage policy was applied before seeding each independent copy. No Cloud SQL, Gemini, production schema, or architectural decision status was changed. PLAIN is a promising candidate, but write/indexing and production rewrite costs remain unmeasured; the recommendation is further isolated validation before a separate migration approval.

### E38

On **2026-10-28**, the user authorized implementation and bounded live verification of a first multi-step investigation workflow. The application uses the existing SearchEvidence path through an application-owned `search_evidence` tool contract, bounded search/generation/evidence/context limits, and strict resolution of model citations to retrieved evidence. The Gemini adapter uses structured tool actions and structured final decisions; the HTTP endpoint and browser mode reuse the existing service lifecycle and source rendering. The [verification record](../validation/multi-step-investigation-2026-10-28.md) reports deterministic suite results, a live branching answer using real Gemini and disposable local PostgreSQL, an additional live response whose assertions completed but whose Unicode report printing failed, and a no-Gemini Edge replay. It is a smoke check, not broad answer-quality validation. No Cloud SQL, schema change, deployment, production model selection, or Git publication occurred.

### E39

On **2026-10-28**, the user authorized [Task 13: Automated Answer-Quality Evaluation](C:/Users/merli/.codex/attachments/cafc7b45-90ed-442d-8a43-8a4a5a0ab2ac/Pasted%20text.txt). This approves extending the existing 40-case evaluation to compare `AnswerQuestion` and `InvestigateQuestion`, conservatively score explicit answers, match citations to exact gold source occurrences/sentences with separate strict and partial policies, classify abstention/contradiction/incomplete/provider failures, record observable investigation behavior/usage, and produce reproducible UTF-8 JSON/Markdown without passing labels to providers. Semantic claim support remains human review; no LLM judge is the sole correctness source. The user separately confirmed the Free-tier quota values and billing-disabled status in chat; those account facts were user-reported, not independently queried. Live evaluation was authorized within a conservative budget and stop-on-error policy. The [validation report](../validation/answer-quality-evaluation.md) and [captured partial sample](../validation/answer-quality-live-2026-10-28.md) record one successful real single-step result, a provider error, and the resulting stop before multi-step calls. No Cloud SQL, deployment, model substitution, or Git publication occurred.

### E40

On **2026-10-28**, the user authorized [Task 14: Corpus-Scale Processing and Indexing](C:/Users/merli/.codex/attachments/32106be9-4cd6-4d14-a19b-d4ee95c0eef5/Pasted%20text.txt): deterministic pinned-source manifests, bounded incremental processing through existing services, durable progress/replay checks, disposable PostgreSQL scale validation, embedding feasibility, matched chunking comparisons, real-provider pilots within Free quotas, safe failure diagnostics, and tests/documentation. The user then explicitly instructed **“use gemini embedding 2 and gemini flash 3.6 or 3.8”**. The task uses the existing Gemini Embedding 2 profile and retains Flash 3.6 where generation is needed; no generation request is needed for the indexing pilot. It does not authorize paid Batch jobs, billing, Cloud SQL, public deployment, unapproved schema/storage/index changes, or Git publication. Full processing and actual semantic eligibility must be reported separately. The [validation record](../validation/corpus-scale-2026-10-28.md) preserves the manifest, CLI/checkpoint contract, measured limits, real Gemini pilot and unresolved full-indexing choices. U03's complete-development selection and exact-content counting policy remain unchanged.

### E41

On **2026-10-28**, the user authorized [Task 15: Gemini Embedding Batching and Complete Evaluation-Corpus Indexing](C:/Users/merli/.codex/attachments/03c50c82-ca8b-4c8b-80af-6a006afed948/Pasted%20text.txt). The scope permits synchronous real Gemini embeddings/generation, a separate persistent local PostgreSQL 18 evaluation database, existing application migrations/services, and evaluation of the fixed 40-question source-only corpus. It excludes the 125,760-occurrence corpus, billing changes, paid/async Batch API, Cloud SQL, public deployment, schema/ranking/vector-storage changes, and Git publication. The [Task 15 report](../validation/batched-gemini-evaluation-2026-10-28.md) records SDK behavior, measured batches, persistent indexing and restart verification, retrieval metrics, account-quota limits and tests. The 197 unique evaluation passages are indexed and searchable; full-corpus indexing and answer-generation evaluation remain incomplete.

## Confirmed decisions

### A01

**Python and environment**  
**Status:** CONFIRMED  
**Approval evidence:** [E01](#e01); included in the implementation request [E04](#e04).
- **Selected approach:** Python 3.14.9 initially with uv and a project-local environment.
- **Alternatives and reasoning:** Other Python versions; venv/pip, Poetry, or Conda. The existing uv setup keeps environment management consistent; compatibility still needs validation.
- **Dependencies and consequences:** Keep the existing runtime and packaging. This does not approve future runtime changes, dependency additions, hosting images, or CI policies.
- **Supporting records and implementation evidence:** [ADR-001](001-python-version.md), [ADR-002](002-environment-and-dependency-management.md); [runtime pin](../../.python-version), [package configuration](../../pyproject.toml), [lockfile](../../uv.lock).

### A02

**Parquet reader**  
**Status:** CONFIRMED  
**Approval evidence:** [E01](#e01); included in the implementation request [E04](#e04).
- **Selected approach:** PyArrow for Parquet processing.
- **Alternatives and reasoning:** pandas or pandas per batch. PyArrow provides direct metadata and batch access without requiring a DataFrame; retained audit state can still grow with corpus size.
- **Dependencies and consequences:** Retain the reader boundary. Text assembly is confirmed in U02; U03 now confirms the complete pinned dev split and exact-content counting baseline.
- **Supporting records and implementation evidence:** [ADR-003](003-parquet-ingestion-pyarrow-vs-pandas.md); [inspection](../../src/investigation_engine/ingestion/inspect_parquet.py), [audit](../../src/investigation_engine/ingestion/audit_contexts.py).

### A03

**Initial dataset**  
**Status:** CONFIRMED  
**Approval evidence:** [E01](#e01); included in the implementation request [E04](#e04).
- **Selected approach:** Start with 2WikiMultihopQA.
- **Alternatives and reasoning:** HotpotQA, RAGBench, and post-mortems were discussed. 2Wiki supplies multi-hop questions and supporting facts; it does not by itself prove corpus sufficiency.
- **Dependencies and consequences:** Keep dataset-specific processing outside the generic domain. U01/U02 confirm occurrence identity and assembly; U04 confirms the provenance field split. Final corpus/splits and independent-record counting remain U03; production ingestion is unimplemented.
- **Supporting records and implementation evidence:** [System overview](../architecture/system-overview.md), [historical audit](../validation/2wiki-dev-audit.md); [pinned downloader](../../src/investigation_engine/ingestion/download_dataset.py).

### A04

**Application architecture**  
**Status:** CONFIRMED  
**Approval evidence:** [E01](#e01); included in the implementation request [E04](#e04).
- **Selected approach:** Lightweight hexagonal modular monolith.
- **Alternatives and reasoning:** Tightly coupled layers, elaborate Clean/Onion scaffolding, or microservices. Small ports keep integrations separate while avoiding unnecessary deployment and abstraction overhead.
- **Dependencies and consequences:** Preserve inward dependencies; no blanket approval of new ports, frameworks, workers, or API contracts. Those belong to U06/U12/U14-U16.
- **Supporting records and implementation evidence:** [ADR-005](005-application-architecture.md); [registration use case](../../src/investigation_engine/application/use_cases/register_document.py), [repository port](../../src/investigation_engine/application/ports/document_repository.py).

### A05

**Document/version identifiers**  
**Status:** CONFIRMED  
**Approval evidence:** [E01](#e01); included in the implementation request [E04](#e04).
- **Selected approach:** UUIDv7 for document and version identifiers.
- **Alternatives and reasoning:** Content hashes or titles would conflate source identity with text or display labels; other UUID schemes were possible. Independent identifiers preserve independent sources.
- **Dependencies and consequences:** This approval does not choose a physical Content key (U07). Dataset occurrence identity is separately approved in U01.
- **Supporting records and implementation evidence:** [ADR-005](005-application-architecture.md); [Document](../../src/investigation_engine/domain/document.py), [DocumentVersion](../../src/investigation_engine/domain/document_version.py).

### A06

**Documents and revisions**  
**Status:** CONFIRMED  
**Approval evidence:** [E01](#e01); included in the implementation request [E04](#e04).
- **Selected approach:** Separate logical documents and immutable document versions.
- **Alternatives and reasoning:** One mutable document/version counter loses historical snapshots; independent text copies preserve history but duplicate exact text. Separate revisions preserve lineage.
- **Dependencies and consequences:** Independent uploads remain separate documents. Update sequencing, persistence constraints, unchanged updates, and retention require U07/U08/U10/U14.
- **Supporting records and implementation evidence:** [ADR-004](004-document-content-provenance.md); [Document](../../src/investigation_engine/domain/document.py), [DocumentVersion](../../src/investigation_engine/domain/document_version.py).

### A07

**Exact content reuse**  
**Status:** CONFIRMED  
**Approval evidence:** [E01](#e01); included in the implementation request [E04](#e04).
- **Selected approach:** Reuse exactly equal canonical text without normalized or semantic merging; preserve independent sources.
- **Alternatives and reasoning:** Sentence-list identity includes boundaries; whitespace normalization forces near-matches together; semantic merging may suppress conflicting facts. Exact text reuse separates content identity from source structure.
- **Dependencies and consequences:** Keep SHA-256 lookup plus equality verification already present. Canonical assembly is separately confirmed in U02; chunk/embedding reuse details remain U11/U12.
- **Supporting records and implementation evidence:** [ADR-004](004-document-content-provenance.md); [Content](../../src/investigation_engine/domain/content.py), [in-memory repository](../../src/investigation_engine/infrastructure/persistence/in_memory_document_repository.py).

### A08

**Version-specific structure and provenance**  
**Status:** CONFIRMED  
**Approval evidence:** [E01](#e01); included in the implementation request [E04](#e04).
- **Selected approach:** Keep structural spans and source spans specific to each version.
- **Alternatives and reasoning:** Putting locations on shared Content conflates sources; block-only or copied text representations complicate common offsets. Version-specific spans preserve differing locations over equal text.
- **Dependencies and consequences:** Retain existing spans; U04 confirms the structured provenance fields, with concrete representation/copy/default/validation-boundary choices confirmed in N02-N05. Parser policies, locator extensions, and physical layout remain U09/U07.
- **Supporting records and implementation evidence:** [ADR-004](004-document-content-provenance.md), [data contract](../design/document-data-contract.md); [version model](../../src/investigation_engine/domain/document_version.py), [TXT adapter](../../src/investigation_engine/infrastructure/ingestion/txt_adapter.py).

### A09

**Version activation**  
**Status:** CONFIRMED  
**Approval evidence:** [E01](#e01); included in the implementation request [E04](#e04).
- **Selected approach:** Activate a version only after successful indexing.
- **Alternatives and reasoning:** Immediate activation could expose a replacement before searchable evidence exists; delayed activation requires readiness and failure handling.
- **Dependencies and consequences:** Registration intentionally leaves current_version_id unset. Indexing, activation, concurrency, and recovery are not implemented or selected by this approval; see U14.
- **Supporting records and implementation evidence:** [ADR-004](004-document-content-provenance.md), [lifecycle design](../design/document-lifecycle-and-indexing.md); [registration](../../src/investigation_engine/application/use_cases/register_document.py).

### A10

**Immediate test sequencing**  
**Status:** SUPERSEDED by [E22](#e22), which resumes permanent software tests.\
**Approval evidence:** Original deferral [E02](#e02)/[E04](#e04); explicit replacement [E22](#e22).
- **Historical approach and reasoning:** Defer software tests during immediate functionality work. This was a temporary sequencing preference, never an assignment waiver.
- **Current approach:** Risk-based pytest unit/integration/opt-in external tests, Docker PostgreSQL/pgvector, real commits and cleanup, coverage without a global floor, and GitHub Actions. New features receive tests alongside implementation.
- **Evidence and limits:** [Testing ADR](testing-strategy.md) distinguishes implemented cases, executed checks, historical xfails resolved by E24 and blocked integration execution. The old empty test placeholder is removed. Automated evaluation remains separately unresolved under U17.

### A11

**Decision register organization**  
**Status:** CONFIRMED  
**Approval evidence:** [E03](#e03); included in the implementation request [E04](#e04).
- **Selected approach:** Central index at docs/decisions/register.md linked to existing ADRs.
- **Alternatives and reasoning:** A single detailed register would put all explanations in one place but duplicate existing ADRs. An index preserves longer ADR reasoning while making status and approvals visible.
- **Dependencies and consequences:** Maintain this register and annotate ADR scope. This does not approve additional documentation organization (N01) or any unresolved technical selection.
- **Supporting records and implementation evidence:** [Documentation index](../README.md), existing ADRs 001-005, and this register.

## Dataset and further decisions

U01, U02, and U04 are CONFIRMED by E06, N02-N05 by E07, U09-2W by E08, N06 by E09, U03 by E10, the scoped U08 policy by E11, U05 by E12, the scoped U06/U12/U13/U18 selections by E13, initial soft deletion by E14, and U07 plus the scoped U10 ownership/deleted-retry selections by E15, followed by registration-specific U14/ownership implementation approval in E16; their identifiers remain stable. Other entries retain their stated UNRESOLVED or NEW status. Approval of a parent choice does not select unresolved implementation details. Each entry identifies dependent work and approval boundaries.

### U01

**2Wiki logical identity**  
**Status:** CONFIRMED  
**Selected approach:** One logical Document and its DocumentVersion per dataset context occurrence; preserve independent occurrence provenance and reuse identical Content through the existing deduplication mechanism. No separate occurrence entity. Different occurrences from the same dataset share source_id; their structured locators distinguish them.  
**Approval evidence:** [E06](#e06), explicit U01 approval; [E09](#e09) clarifies source versus occurrence identity.
- **Context:** Repeated passages and titles must not erase original context positions.
- **Alternatives and trade-offs:** Occurrence-level documents fit the existing source-per-version model but create more rows. Grouping passages needs occurrence associations and potentially multiple sentence mappings. Title-based identity conflates sources and does not establish revision history.
- **Reasoning:** Preserve each occurrence's provenance without adding an entity; keep exact-content reuse separate from logical document identity.
- **Dependencies and consequences:** Identify a source occurrence using dataset, pinned revision, split, question ID, and context position; UUIDv7 document/version IDs remain independent. This describes initial per-occurrence registration, not a one-version-forever limit or inferred article revision history. Reimports/retries and unchanged updates remain U08; cross-revision continuity must not be inferred. U03 still governs independent-record counting; 125,760 occurrences do not prove that many independent source records.
- **Implementation state:** The [2Wiki adapter](../../src/investigation_engine/infrastructure/ingestion/twowiki_adapter.py) emits one ExtractedDocument per occurrence for the existing RegisterDocument service. E16 implements persistent registration, and E40 adds bounded operator batch orchestration through those same services, with independent source identities and unchanged exact-Content reuse.
- **Supporting records:** [ADR-004](004-document-content-provenance.md), [data contract](../design/document-data-contract.md), [dataset audit](../validation/2wiki-dev-audit.md).

### U02

**Canonical sentence assembly**  
**Status:** CONFIRMED  
**Selected approach:** Join unchanged 2Wiki sentence strings in original order with one ASCII space between them, preserving exact character offsets and original source locators. Keep titles separate from canonical content.  
**Approval evidence:** [E06](#e06), explicit U02 approval.
- **Context:** Exact-text reuse needs deterministic construction without erasing original segmentation.
- **Alternatives and trade-offs:** Direct concatenation can join words; newline separators introduce line breaks and make segmentation affect canonical text. Spaces support flowing text while spans retain boundaries.
- **Reasoning:** The real "Brink of Disaster!" variants produce identical text with different sentence mappings; content can be shared because those mappings remain version-specific under A08.
- **Dependencies and consequences:** No trimming, whitespace collapse, case/punctuation changes, added title, or leading/trailing separator. Preserve original zero-based sentence indices and Python string ranges. Inserted separator characters are outside the original sentence spans. U09-2W defines empty/failed 2Wiki extraction; other formats remain U09; chunking/embedding input configuration remains U11.
- **Implementation state:** Implemented in the 2Wiki adapter with sentence structural spans and source spans; no changes to existing span types.
- **Supporting records:** [ADR-003](003-parquet-ingestion-pyarrow-vs-pandas.md), [ADR-004](004-document-content-provenance.md), [observed evidence](#observed-evidence).

### U03

**Corpus selection and independence**  
**Status:** CONFIRMED  
**Selected approach:** Use the complete xanhho/2WikiMultihopQA dev split at revision 612bc5039a457880d9e7d84c3b0a4cf154b70e4f. Count distinct SHA-256 hashes of original extracted UTF-8 text as the conservative baseline for the 50,000-record requirement, without normalization or semantic deduplication. Preserve every occurrence and its provenance; reuse only identical Content.  
**Approval evidence:** [E10](#e10), explicit user approval of corpus selection, methodology, measured totals, and limitations.
- **Context:** Assignment page 5 requires processing at least 50,000 documents or independent text records, source preservation, and repeatable evaluation; reproducible justified subsets are permitted. It does not specify a semantic-independence test.
- **Alternatives and trade-offs:** Counting occurrences alone overstates distinct content; titles are not content identities; chunks are subdivisions and cannot increase this count. Additional splits add resources and overlap concerns; an arbitrary subset introduces another selection policy. The full pinned dev split already exceeds the approved baseline, so neither expansion nor arbitrary reduction is needed merely to meet the threshold.
- **Methodology and results:** Use U02's unchanged sentences joined by one ASCII space, then SHA-256 of text.encode("utf-8"). Report separately: 125,760 passage occurrences; 56,680 unique exact-content passages; 69,080 duplicate occurrences beyond the first; 125,760 unique source-coordinate records. Chunks are excluded. The 56,680 distinct texts exceed 50,000 by 6,680. Source coordinates comprise source identity, revision, split, example_id, and context_position; they remain separate from content identity.
- **Limitations:** Exact-content uniqueness is not proof of semantic independence. Text variants can describe the same source or subject, and some passages are incomplete. Meeting the numerical baseline does not certify retrieval quality, evaluation independence, durable ingestion, or complete assignment delivery.
- **Dependencies and consequences:** Preserve revision, split, example ID, and context position under U04/N06. Question and answer labels stay outside the retrieval corpus; existing exclusion of gold supporting facts/evidences remains intact. Evaluation methodology and potential leakage remain U17. No new splits, arbitrary subsets, normalized/semantic merging, schema, retry policy, or runtime behavior is approved here. U08/U14 still govern future retries/recovery; U05-U07 govern persistence.
- **Implementation evidence:** A full transient audit through the existing adapter measured the approved totals and checked equal hashes against exact text equality. The detailed method, duplicate distribution, snapshot fingerprint, and historical-count distinctions are in the existing dataset audit. No persistent audit runner, bulk ingestion service, or new tests are introduced by this documentation update.
- **E40 verification:** The [reproducible manifest](../../data/samples/2wiki-dev-corpus-manifest.json) reproduces the approved counts. Actual application processing in disposable PostgreSQL persisted 125,760 Documents/versions, 56,680 Contents and 125,760 complete plans, with every occurrence's exact text, metadata and source mappings compared to the original. Interruption, restart and retry checks passed. This verifies the approved conservative processing baseline, while full-corpus semantic eligibility remains zero. See [measurements and boundaries](../validation/corpus-scale-2026-10-28.md).
- **Supporting records:** [Dataset audit](../validation/2wiki-dev-audit.md#9-approved-u03-corpus-and-counting-policy), [assignment requirements](#assignment-requirements), [U01](#u01), [U02](#u02), [U04](#u04), [N06](#n06).

### U04

**Provenance representation**  
**Status:** CONFIRMED  
**Selected approach:** Two separate structured, database-independent fields in SourceReference: record_locator identifies the original source record; metadata holds additional descriptive source information. For 2Wiki, record_locator contains split, example_id (the record's _id/question ID), and context_position. Dataset identity belongs in source_id and the dataset version in revision, without duplicating either in metadata or the locator.  
**Approval evidence:** [E06](#e06), explicit approval of revised U04; [E09](#e09) clarifies consistent field responsibilities.
- **Context:** Original-record coordinates need a structured representation distinct from descriptive information.
- **Alternatives and trade-offs:** The assistant's earlier compact-JSON record_id proposal was not adopted; the user chose separate structured fields. A separate occurrence entity is excluded by U01. N02-N05 now confirm recursive JSON types, defensive copying, defaults, field compatibility, and validation boundaries.
- **Reasoning:** Separate identity coordinates from description while preserving source lineage and keeping the domain independent of storage products.
- **Dependencies and consequences:** Dataset identity and pinned revision remain necessary context for the 2Wiki locator. N02-N05 confirm replacement of record_id, preservation of remaining fields, recursive JSON aliases, default factories, defensive copying without deep immutability, and external validation boundaries. U04 itself did not select a database or physical schema; U05 and U07 subsequently approve PostgreSQL and the JSONB persistence representation without changing these domain responsibilities.
- **Implementation state:** SourceReference has kind, source_id, record_locator, metadata, display_name, and revision. The structured fields use recursive JSON aliases, independent default dictionaries, and defensive deep copies. The 2Wiki adapter supplies structured provenance. U07 implements separate ORM mappings and migrations; E16 adds PostgreSQL registration without changing provenance field responsibilities.
- **Supporting records:** [SourceReference](../../src/investigation_engine/domain/provenance.py), [data contract](../design/document-data-contract.md), [N02](#n02)-[N05](#n05).

### U05

**Authoritative database**  
**Status:** CONFIRMED  
**Selected approach:** PostgreSQL is the authoritative relational database for documents, versions, shared Content, provenance, and associated relational application data. Derived retrieval storage remains a separate decision.  
**Approval evidence:** [E12](#e12), explicit user selection and documentation-only instruction.
- **Context:** Assignment page 6 requires persistence after application restarts. Existing sequential in-memory registration implements exact Content reuse and U08 conflicts/retries but provides no durable or concurrent guarantees. U03 selects the complete pinned dev corpus; its occurrence/content counts motivate shared Content with independent provenance.
- **Alternatives and trade-offs:** PostgreSQL and MySQL/InnoDB both support production relational workloads. PostgreSQL more directly fits generic structured-locator identity through native JSONB equality and B-tree indexing, alongside relational integrity and transactions. MySQL is capable but requires another indexing/enforcement design for JSON identities. SQLite and other products were historical discussion alternatives, not selections or rejected approved decisions. No universal performance advantage is claimed.
- **Reasoning:** Strong document/version/Content relationships, flexible structured provenance, constraint-backed identity uniqueness, transactional registration capabilities, and mature Python tooling fit the existing architecture with relatively little additional complexity. JSONB is a persistence representation, not a domain type; exact physical mappings remain U07.
- **Dependencies and consequences:** Database constraints enforce critical uniqueness; U07 now approves NULLS NOT DISTINCT and requires PostgreSQL 15+ for that capability, without selecting a deployment major version. E16 now implements transactional concurrent/durable registration under the scoped U14 policy; remaining lifecycle guarantees stay unresolved. PostgreSQL adds administration, backup/restore, capacity, and configuration responsibilities; JSONB still needs semantic validation. A future second retrieval store adds synchronization responsibilities.
- **Scope boundary:** U05 itself selected no ORM/driver, schema, migrations, host, pooling, transactions, recovery, or retrieval technology; pgvector was excluded from its comparison. E13 subsequently approves the scoped U06/U12/U13/U18 choices in separate ADRs, and E15 approves U07's implemented schema foundation, without changing U05 rationale. Concrete retrieval/operational details remain unresolved. U08's original in-memory guarantee remains sequential/local; E16 now adds the PostgreSQL registration implementation under scoped U14 approval. Schema verification alone remains distinct from repository verification.
- **Supporting records:** [PostgreSQL versus MySQL ADR](postgresql-vs-mysql.md), [U03](#u03), [U08](#u08), [assignment requirements](#assignment-requirements), [ADR-005](005-application-architecture.md), [lifecycle design](../design/document-lifecycle-and-indexing.md). The ADR links official capability documentation and separates capabilities from implementation evidence.

### U06

**Driver, mapping, migrations, and I/O execution**  
**Status:** CONFIRMED technology/execution choices; concrete implementation details remain unresolved.  
**Selected approach:** SQLAlchemy 2.x ORM, psycopg 3, asynchronous PostgreSQL operations using SQLAlchemy AsyncSession, and Alembic migrations. Infrastructure ORM models translate to/from independent domain dataclasses. Async interfaces serve I/O; pure computation stays synchronous.  
**Approval evidence:** [E13](#e13), explicit selection and documentation-only authorization.
- **Context:** U05 establishes PostgreSQL. Document/version/Content relationships and U08 registration need maintainable persistence, while planned investigations combine database, retrieval, and external LLM I/O. Application/repository registration is now async under E16; pure extraction and domain computation remain synchronous.
- **Alternatives and trade-offs:** ORM versus Core/raw SQL, psycopg versus asyncpg, sync versus async, and Alembic versus manual migrations. ORM and async add mapping/loading/session complexity; Core remains available where explicit SQL is appropriate. psycopg is selected for sync/async and operational flexibility, not an unmeasured speed advantage over asyncpg. Autogenerated migrations require review.
- **Reasoning:** Related persistence objects, explicit repository translation, SQLAlchemy query/session facilities, driver flexibility, and reproducible schema evolution fit the architecture. Async can overlap I/O waits on limited backend resources but does not accelerate individual queries or CPU work.
- **Dependencies and consequences:** E16 converts application I/O contracts, callers, and both repositories to async consistently while preserving U08 results/conflicts and Content reuse. Use isolated sessions for concurrent tasks, explicit loading, small configurable pools, and short transactions that do not span external LLM/embedding calls. E15 now authorizes the installed uv-managed dependencies, verified Python 3.14 compatibility, and U07 ORM/migration foundation. E16 settles registration session/transaction ownership, READ COMMITTED, conflict resolution, and cancellation/error behavior under U14. Production pool settings, numeric deadlines, and remaining lifecycle work stay open. Migration-local connection setup is still separate.
- **Supporting records:** [Python persistence stack ADR](python-persistence-stack.md), [repository port](../../src/investigation_engine/application/ports/document_repository.py), [registration](../../src/investigation_engine/application/use_cases/register_document.py), [U05](#u05), [U08](#u08).

### U07

**Physical data model**  
**Status:** CONFIRMED for the approved three-table ORM/migration schema; application persistence and concurrency are outside this scope.  
**Selected approach:** documents, document_versions, and contents. Binary SHA-256 Content PK and exact text; UUIDv7 document/version values; positive per-document version numbers; version Content/document FKs and composite active-version ownership FK. SourceReference columns belong on versions, with JSONB locator/metadata objects and ordered structure/source-span arrays. Global source-occurrence uniqueness uses NULLS NOT DISTINCT. documents.owner_id is nullable TEXT; deleted_at and current_version_id are retained.  
**Approval evidence:** [E15](#e15), explicit implementation authorization and schema/index boundaries.
- **Derived extension:** [E21](#e21) approves five derived tables in [the storage ADR](embedding-storage.md), without modifying these authoritative tables or 0001. Embedding/indexing workflows are outside that schema milestone.
- **Context:** Existing domain/application contracts distinguish source identity, document/version identity, and exact Content identity. Three tables preserve those responsibilities without one table per dataclass. ORM mappings remain infrastructure representations; domain objects are unchanged.
- **Alternatives and reasoning:** Digest versus surrogate Content keys; provenance table versus version fields; relational versus JSONB spans; trigger versus composite FK; scoped identity versus the approved global fields. The approved choices minimize extra identities/joins while enforcing relational uniqueness and ownership. A matching digest still requires text equality; different text is a collision error, not shared Content.
- **Constraints and representation:** BYTEA digest length and built-in UTF-8 SHA-256 consistency checks; supported text SourceKind; positive number; object/array shape checks; unique document/number and document/version-ID pairs; global kind/source_id/revision/record_locator uniqueness with absent revisions treated equally. Identity scalar strings and owner references use C collation; JSONB numeric/object equality must retain U08 semantics. No nested-validation triggers or immutability permission scheme is added. PostgreSQL 15+ is required; PostgreSQL 17.2 was used for verification, not selected as the deployment major version.
- **Approved indexes only:** Three PK indexes, version-number unique index, owned-version unique index, occurrence unique index, and nonunique version Content-reference index. No redundant document_id index, owner/creation listing index, JSONB GIN, full-text, or vector index. Alembic's own version-tracking table/index is migration tooling state, not an additional application entity.
- **Ownership/deletion boundaries:** U10 records nullable owner semantics, trusted creation, retained pointers/history, and approved deleted-registration retries. These columns do not implement authorization or query eligibility. Ordinary reads/retrieval must eventually filter deleted/inaccessible documents through eligible versions, not Content existence. No cascade workflow, physical deletion, restoration, or ownership transfer is implemented.
- **Implementation state:** [SQLAlchemy mappings](../../src/investigation_engine/infrastructure/persistence/models.py), [Alembic environment](../../migrations/env.py), and [initial migration](../../migrations/versions/0001_document_registration.py) implement the foundation. The circular FK is added after both tables exist and removed before downgrade. Physical metadata maps to source_metadata. Explicit loading avoids implicit async I/O; current_version is viewonly. No create_all migration path is used. Dependencies are uv-managed; the lockfile resolves SQLAlchemy 2.1.6, psycopg 3.3.6 with binary distribution, Alembic 1.20.1, and required transitive packages on Python 3.14.9.
- **E15 schema verification (historical scope):** Mapper configuration/offline SQL passed. Actual temporary PostgreSQL 17.2 upgrade, Alembic check, downgrade, and re-upgrade passed; ORM metadata matched the migrated schema including types/defaults, and the seven application indexes were inspected. Focused database cases verified digest checks, root JSON shapes/defaults, version/occurrence uniqueness, nullable revision, numeric/key ordering, boolean/null/list/string distinctions, global identity across owners, composite active-version ownership, and retention of references/identity after a deletion timestamp. AsyncSession/domain-value reconstruction preserved UUIDv7, timestamps, exact Unicode/whitespace, digest bytes/hex, metadata, span ordering/locators, and the current-version join. No application registration, authorization, or concurrency guarantee was tested or added; no automated test files were created.
- **Dependencies and consequences:** E16 now implements registration session/transaction ownership, atomic registration, concurrent conflict resolution, and cancellation handling under scoped U14 approval, without modifying this schema. Ownership propagates explicitly; principal authentication remains U10/U16 and lifecycle races remain U14. U11/U12/U13 govern future retrieval schema/configuration. No hosting or pgvector objects are provisioned.
- **Supporting records:** [Physical schema ADR](postgresql-physical-schema.md), [U08](#u08), [U10](#u10), [U14](#u14), [repository contract](../../src/investigation_engine/application/ports/document_repository.py).

### U08

**Request retries and unchanged updates**  
**Status:** CONFIRMED for same-revision registration semantics; E16 implements durable/concurrent PostgreSQL registration. Cross-revision updates and batch guarantees remain deferred.  
**Selected approach:** Resolve registrations by kind, source_id, revision, and record_locator. Identical text and structural/source spans return the stored Document and DocumentVersion IDs and reuse Content. Different text or spans for the same identity raise descriptive ValueError conflicts without changing existing state. Different occurrences retain separate registrations and share exactly equal Content.  
**Approval evidence:** [E11](#e11), explicit user approval with numeric-comparison refinement; [E15](#e15) adds the approved authorized deleted-registration retry policy; [E16](#e16) approves its scoped async PostgreSQL implementation.
- **Context:** UUID identity, occurrence provenance, and exact Content equality have separate responsibilities. The previous implementation deduplicated Content but created new document/version IDs on every application call.
- **Alternatives and trade-offs:** Always creating documents makes retries duplicate registrations. Content-based identity would merge independent sources. Separate lookup and insertion would complicate future atomicity. The approved single repository operation resolves retries without new domain entities or externally serialized identifiers.
- **Locator comparison:** Dictionary key order is ignored recursively; arrays retain order; missing keys differ from explicit null; strings are exact. Boolean values remain distinct from numbers, while numerically equal integer/float values compare equally. A private tagged-tuple key uses standard-library Decimal(str(value)) for numeric values, matching the decimal values produced by ordinary JSON numeric serialization instead of exposing binary-float artifacts. Nonfinite numbers, non-string object keys, and non-JSON values are rejected by the lookup helper. Adapters remain responsible for source-specific validation. No locator values are normalized or rewritten in SourceReference.
- **Numeric rationale:** PostgreSQL documents JSONB numbers as numeric values, with trailing fractional zeroes insignificant for equality: [official JSON documentation](https://www.postgresql.org/docs/17/datatype-json.html). The in-memory key implements those approved semantics without claiming every PostgreSQL range/encoding constraint. E16's PostgreSQL lookup uses native JSONB equality and ordinary finite JSON values, preserving the same identity contract.
- **Retry descriptions and mutation protection:** Retain stored title, display_name, and metadata on retries. Copy mutable Document and provenance dictionaries on storage and return; share immutable Content and span values. Returned objects have stored identities but are not handles for modifying repository state. Explicit metadata updates remain deferred.
- **Revision boundary:** None means no explicitly identified revision. Different revision values identify separate registrations and are never automatically grouped under one Document. This create/register operation does not append versions; attempting to reuse an existing Document ID for a new registration remains rejected. Future cross-revision continuity requires an explicit update operation targeting an existing Document. No revision ordering, latest-revision lookup, or automatic activation is introduced.
- **Guarantee boundary:** The in-memory adapter remains sequential and instance-local. E16 adds concurrent/durable PostgreSQL registration using the same database, without batch atomicity, checkpoints, indexing recovery, or cross-revision updates. Existing activation rules remain A09. Stable source IDs are the caller's responsibility; never use content equality to decide upload identity.
- **Deletion boundary:** E15 approves authorized identical retries of a deleted occurrence returning the existing document/version IDs and deleted state, without restoration or duplicate registration. Changed text/spans remain conflicts. Authorize before revealing another owner's registration/conflict details. Ordinary reads must exclude deleted documents; E16's internal PostgreSQL retry lookup includes them and verifies ownership before disclosure. Registration implements these retry semantics; authentication, deletion endpoints, and restoration remain outside scope.
- **Implementation state:** Awaiting DocumentRepository.register returns tuple[Document, DocumentVersion]; RegisterDocument awaits and returns that pair. The in-memory repository maintains a private occurrence-to-version index, checks ownership/text/spans before writes, retains exact hash-assisted Content reuse, and returns defensive snapshots. E16 adds required trusted Document ownership and async execution; SourceReference, Content, spans, and extraction adapters are unchanged.
- **Verification:** Transient checks passed for repeat IDs, numeric/key-order equality, boolean/number separation, arrays/null/string distinctions, descriptive no-ops, input/output mutation isolation, text/span conflicts without writes, separate-source Content sharing, TXT/2Wiki integration, and rejection of updates to existing document IDs. No automated test files or persistent audit artifacts were added.
- **Supporting records:** [Registration](../../src/investigation_engine/application/use_cases/register_document.py), [repository contract](../../src/investigation_engine/application/ports/document_repository.py), [in-memory repository](../../src/investigation_engine/infrastructure/persistence/in_memory_document_repository.py), [N06](#n06), [lifecycle design](../design/document-lifecycle-and-indexing.md).

### U09

**Extraction and failure policy**  
**Status:** CONFIRMED for the scoped E34 PDF/DOCX extraction and U09-2W; future OCR/other formats and upload-boundary policies remain UNRESOLVED.
**Selected approach:** Keep format-specific adapters. E34 uses pypdf for readable PDF pages and python-docx for main-body paragraphs/tables; preserve parser text plus documented separators, page/structural provenance and metadata. Empty/no-text and malformed files have explicit ValueError-derived outcomes; no OCR or partial-success fallback. Apply the distinct approved 2Wiki behavior below.
**Approval evidence:** [E08](#e08) approves 2Wiki only; [E34](#e34) authorizes PDF/DOCX library selection and the extraction milestone.
- **Context:** Different source formats need explicit parsing, validation, and failure behavior without silently generalizing one adapter's policy.
- **Alternatives and trade-offs:** Parser/OCR choices, retaining failure records, skipping/repairing invalid input, and fallback extraction have different coverage and operational costs. 2Wiki retains its fail-fast policy. E34 adds strict PDF parsing and DOCX package parsing without substituting text or registering failures; there is no universal parser framework.
- **Dependencies and consequences:** E34 implements PDF/DOCX adapters with pypdf 6.21.3, python-docx 1.2.0 and transitive lxml 6.1.3 verified on Python 3.14. PDF pages are one-based; DOCX hierarchical table/row/cell/paragraph positions are zero-based. Existing JSONB handles added locator kinds without migrations. Batch handling, source updates, ownership, original retention, uploads, OCR and other formats remain separate. See the [complete format contract and limitations](../design/file-extraction.md).
- **Supporting records:** [TXT adapter](../../src/investigation_engine/infrastructure/ingestion/txt_adapter.py), [validation matrix](../validation/schema-acceptance-plan.md).

### U09-2W

**2Wiki extraction and failure policy**  
**Status:** CONFIRMED  
**Selected approach:** Reject malformed records with descriptive ValueError exceptions, including missing required fields, invalid types, empty passages, and empty or whitespace-only sentences. Preserve original sentences unchanged; never silently skip, repair, substitute, or fall back. Include available record identifiers and context positions in errors. The adapter returns extracted documents or raises; batch handling and failed-record persistence are outside its scope.  
**Approval evidence:** [E08](#e08), explicit user approval on 2026-10-25.
- **Context:** U01/U02/U04 define occurrence identity, assembly, and provenance; a dataset adapter needs a clear malformed-input policy.
- **Alternatives and trade-offs:** Skipping/repairing malformed inputs could allow a batch to continue but hide missing evidence or change source text. Fail-fast behavior exposes problems and leaves recovery decisions with the caller.
- **Reasoning:** Preserve trustworthy offsets and source attribution while keeping extraction independent of persistence and batch orchestration.
- **Dependencies and consequences:** Consume a parsed record; preserve passage and sentence order; return separate ExtractedDocument values. No chunking, embedding, deduplication, persistence, new dependencies, or automatic fallback. Existing sentence locators need no extension. Concrete source identifier/revision contract is N06; other format policies remain U09.
- **Implementation state:** Implemented in [extract_2wiki](../../src/investigation_engine/infrastructure/ingestion/twowiki_adapter.py). The extraction boundary validates a dictionary with nonblank _id, split, and revision, a nonempty parsed context list, [title, sentences] passage lists, nonblank titles, nonempty sentence lists, and nonblank string sentences. Question/answer/supporting-fact fields are not required or consumed. Validation uses the standard library and returns a complete list or raises ValueError; it performs no JSON decoding or batch recovery.
- **Supporting records:** [U01](#u01), [U02](#u02), [U04](#u04), [source spans](../../src/investigation_engine/domain/source_span.py).


### U10

**Retention and access**  
**Status:** CONFIRMED for initial ownership representation, soft-deletion behavior, deleted-registration retries, and E35's application-level access/list/update operations; principal authentication, remaining access/retention, and operational policies remain UNRESOLVED.
**Selected approach within approved scope:** Nullable documents.owner_id TEXT: NULL represents shared system-managed documents; non-NULL is a stable, unambiguous private principal. No users table or visibility column. Keep deleted_at and retain current_version_id on deletion, along with documents, versions, provenance, spans, and shared Content. Normal listings, semantic/keyword retrieval, and evidence exclude deleted/inaccessible documents. An authorized identical retry preserves existing IDs/deleted state and never restores; changed text/spans remain U08 conflicts.  
**Approval evidence:** [E14](#e14) establishes initial soft deletion and intended shared/private access; [E15](#e15) approves the physical ownership representation, retained pointer, and deleted retry behavior; [E16](#e16) approves required trusted ownership propagation and registration enforcement.
- **Trusted ownership requirement:** Shared documents are accessible to authorized users; private uploads only to their owner. A trusted workflow must assign ownership explicitly. Missing owner input from an untrusted upload must not create a shared document. Authorization must precede disclosure of another owner's registration or conflict details. No concrete authentication mechanism, principal source, user model, or permissions framework is selected.
- **Context:** Content is shared independently of document eligibility. A nondeleted document is distinct from having an indexed active version under A09. Deleting one reference must not make other active eligible documents sharing Content unavailable. Global source identity is unchanged; independent uploads receive distinct stable source IDs.
- **Remaining decisions:** Authentication and the actual principal source; authority/workflow for managing shared documents; historical/deleted citation access; original-byte retention; HTTP response/error details; and operational behavior for in-flight retrieval. Ownership transfer, restoration, physical deletion, and Content garbage collection remain excluded.
- **E35 implementation state:** Application `DocumentActor` carries a principal from a trusted caller; it is not authentication. Owners may update/delete their private documents; all actors may inspect shared documents, which users cannot mutate. PostgreSQL performs visibility-filtered listing, owner checks before details, row-locked soft deletion and update; in-memory behavior mirrors these contracts sequentially. Public endpoints and authenticated principal providers remain unimplemented.
- **Supporting records:** [Physical schema ADR](postgresql-physical-schema.md), [U07](#u07), [U08](#u08), [U14](#u14), [lifecycle design](../design/document-lifecycle-and-indexing.md).

### U11

**Chunking and embeddings**  
**Status:** CONFIRMED for deterministic chunking, initial configurations, memberships/reuse, E21 storage/profile, E23 generation, E25 plans and E26 foreground embedding persistence; remaining production choices are UNRESOLVED.\
**Approval evidence:** [E17](#e17), [E18](#e18), [E21](#e21), [E23](#e23), [E25](#e25), and persistence/activation approval [E26](#e26).
- **Selected approach:** Whole-passage baseline for short 2Wiki passages; fixed-token windows initially 128 content tokens/16 overlap; supplied-sentence packing initially 128/0. Oversized inputs are split deterministically, without truncation, decoding token IDs, rewriting text, or combining independent passages. Sentence structure comes from the relevant DocumentVersion, not shared Content alone.
- **Range and membership:** A range is (content_sha256, start, end), with exact half-open Python-character slices. No chunk UUID. Version/configuration membership is separate; multiple versions/configurations may produce the same range. Configuration records strategy, implementation version, exact tokenizer artifact/revision/runtime, budget and overlap. Boundary/fallback/oversized rules are fixed by implementation version; changing them requires a new version. E17 approved no physical storage; E21 now approves the five derived tables described in the [storage ADR](embedding-storage.md), with no separate chunk registry.
- **Future embedding reuse:** Requires equivalent complete effective input, provider/model/revision, document/query encoding, formatting, dimensions and normalization/output-affecting settings. Title/context changes can prevent reuse over equal ranges; different chunking strategies can reuse equivalent inputs. No normalized/semantic deduplication or embedding cache workflow is implemented; E21 adds storage mappings and a migration only.
- **Hosted experimental direction:** Prefer hosted embedding APIs. E21 approves Gemini Developer API, gemini-embedding-2, 768 dimensions, cosine and exact search for development, using gemini_no_title_v1 document formatting and the documented retrieval-query prefix; production provider remains unresolved. The eight-question pilot did not establish consistent gains from titles or smaller chunks. E23 implements generation through an SDK-independent async port and google-genai infrastructure adapter. E21 selects the no-title baseline and physical VECTOR(768) storage; title-aware persistence and retrieval remain deferred.
- **Alternatives and trade-offs:** Whole passages preserve context with fewer units; fixed windows bound content-token size but can cut sentences and repeat characters; sentence packing preserves supplied structure where possible but can leave token capacity unused. 256-token experiments remain historical measurements, not selected final configurations. Local model serving remains an alternative, not the preferred initial direction.
- **Implementation state:** Pure [application chunking](../../src/investigation_engine/application/chunking.py) returns immutable ChunkPlan/Chunk values; text is derived directly from immutable Content. One [tokenizer port](../../src/investigation_engine/application/ports/text_tokenizer.py) separates tokenization from selection. The [file-loading infrastructure adapter](../../src/investigation_engine/infrastructure/tokenization/huggingface_tokenizer.py) uses the approved experimental runtime. Registration, authoritative models/schema/migrations, source fields and source spans are unchanged. Verification is recorded in the focused ADR separately from approval.
- **Remaining UNRESOLVED:** Production embedding provider/tokenizer and upstream pinning; final chunking defaults; title-aware/new encodings; serving-profile selection; ANN/retrieval; background lifecycle and evaluation. E26 implements scoped foreground persistence/recovery/activation. E30 verifies real countTokens and embeddings for two passages and a query, without a local-token substitute; this does not establish universal modality support or future alias stability. U14/U17/U18 retain their remaining boundaries.
- **E23 generation implementation:** [EmbeddingProvider](../../src/investigation_engine/application/ports/embedding_provider.py) exposes document/query operations returning immutable vectors. [Gemini adapter](../../src/investigation_engine/infrastructure/embeddings/gemini_provider.py) uses sequential one-entry SDK calls, validated 8192-token counts, explicit 768 dimensions and versioned formats. It rejects malformed/non-finite/zero/truncated responses, maps sanitized errors, propagates cancellation and closes both SDK transports. [Generation details](embedding-storage.md#generation-adapter-e23) record the token-count uncertainty, offline verification and remaining boundaries. No cloud/API calls or persistence changes are part of this implementation.
- **E25 chunk-plan implementation:** [ChunkPlanRepository](../../src/investigation_engine/application/ports/chunk_plan_repository.py), pure [validation](../../src/investigation_engine/application/chunk_plan_validation.py), and [PostgreSQL storage](../../src/investigation_engine/infrastructure/persistence/postgresql_chunk_plan_repository.py) save/reconstruct complete immutable plans without copied chunk text. Configuration identity includes all definition fields; source versions anchor canonical Content. Empty/blank plans retain the current chunker contract. Twenty new unit cases pass; all 161 unit tests pass. The 18 database cases are collected but unexecuted because the pinned image is absent. [Detailed storage contract](embedding-storage.md#chunk-plan-persistence-e25) separates complete memberships from future embedding eligibility.
- **E26 implementation and verification:** [EmbeddingConfig/eligibility/float32 values](../../src/investigation_engine/application/embeddings.py), [EmbeddingRepository](../../src/investigation_engine/application/ports/embedding_repository.py), [IndexDocumentVersion](../../src/investigation_engine/application/use_cases/index_document_version.py), and PostgreSQL adapters implement reuse and incremental progress. Provider configuration declarations prevent cross-profile labeling. Complete plans retain blank ranges but require vectors only for nonblank memberships. Current verification supersedes E25's unavailable-image boundary: 196 unit and 101 integration tests pass in fresh PostgreSQL 18/pgvector 0.8.5 containers, including all prior tests. See [E26 storage and limits](embedding-storage.md#resumable-indexing-and-activation-e26).
- **E21 storage implementation:** [Five-table ADR](embedding-storage.md), infrastructure ORM mappings and 0002_embedding_storage implement configuration validation, immutable-plan representation, ordered version memberships and reusable range/configuration vectors. Only five PK indexes plus membership range uniqueness are added. No extra DocumentVersion key or membership Content column. pgvector 0.5.1 is verified under Python 3.14.9. The ADR records executed isolated migration/constraint checks and application-side validation still required.
- **Supporting records:** [Deterministic chunking ADR](deterministic-chunking.md), [N07](#n07)-[N09](#n09), [ADR-004](004-document-content-provenance.md), [data contract](../design/document-data-contract.md).

### U12

**Search storage and contracts**  
**Status:** CONFIRMED for pgvector, E21 VECTOR(768) storage and E27 scoped exact semantic retrieval; ANN, keyword indexes and evaluation targets remain unresolved.\
**Selected approach:** PostgreSQL/pgvector with application-owned SearchEvidence, Evidence and EvidenceRepository contracts. One explicit chunking/embedding pair, exact cosine similarity (1 minus distance), strict limit 1-100, and deterministic distance/document/version/range ordering. No separate vector database.\
**Approval evidence:** [E13](#e13), pgvector selection; [E21](#e21), storage/exact-search baseline; [E27](#e27), implementation scope.
- **Context:** U05 selects PostgreSQL. The corpus has 125,760 occurrences, 56,680 distinct texts and 400,892 sentence spans; corpus embeddings and full-scale retrieval are not yet measured. E27 implements synthetic-tested semantic retrieval, not corpus ingestion/evaluation.
- **Alternatives and trade-offs:** Qdrant provides specialized vector infrastructure, dense/sparse retrieval, payload filtering, and independently scalable search, at the cost of another service and synchronization responsibilities. Other dedicated engines remain possible future alternatives, not selections. Co-locating data reduces operational/consistency complexity but shares database resources with vector/full-text workloads.
- **Reasoning:** Native relational joins and shared transaction possibilities align with source eligibility and shared Content. They do not implement activation, deletion, or indexing recovery automatically. No latency or throughput superiority over Qdrant is claimed.
- **E27 contracts and eligibility:** SearchEvidence.execute(question, limit=10, *, owner_id) receives a trusted principal or explicit None (shared-only); principals see shared plus their own private documents. Query embedding uses embed_query, checks provider compatibility and validates float32 storage before opening a repository transaction. One SQL statement restricts active owned versions of nondeleted visible documents, exact selected plans, complete manifests/range coverage and every nonblank selected-profile vector before cosine ordering/LIMIT. This supplies scoped internal visibility, not authentication. Malformed returned provenance raises RepositoryError rather than post-filtering hits.
- **E27 evidence identity:** (document_version_id, start, end), retaining independent occurrences and overlapping ranges without semantic grouping. Shared Content/vectors can yield multiple attributable hits. Evidence holds the original intersecting source spans in original order; partial intersections do not imply that a complete page/sentence was retrieved. Scores are raw finite cosine similarities; no threshold/calibration is selected.
- **Dependencies and consequences:** The active pointer does not persist a serving profile. E27 recomputes complete selected-profile eligibility on each query using existing tables and one statement snapshot. Background lifecycle, principal authentication, profile publication/version pinning, ANN/index settings, recall/latency targets and evaluation remain U10/U11/U14/U17. Revisit storage only when measurements justify it. No schema/migration or indexing implementation changes are needed. See [retrieval implementation and verification](embedding-storage.md#exact-semantic-retrieval-e27).
- **E33 optimization:** Equivalent SQL boundary trimming for blankness and narrow pre-limit candidate projection with post-limit source payload joins; all eligibility remains before limits in one statement/snapshot. No schema/interface/scoring change. See measured before/after evidence in the performance checkpoint.
- **E31 extension:** The focused HybridEvidenceRepository returns semantic/keyword lists from one statement snapshot; SearchEvidence fuses them in Python. Existing semantic contract and shared-only public scope are preserved. Explicit score kinds prevent RRF/keyword scores being represented as cosine. No schema or lifecycle change.
- **Supporting records:** [pgvector selection ADR](pgvector-selection.md), [U13](#u13), [U18](#u18), [lifecycle design](../design/document-lifecycle-and-indexing.md).

### U13

**Keyword retrieval and custom ranking**  
**Status:** CONFIRMED for native full-text search and custom RRF, with the E31 experimental implementation below; production tuning/index design remains unresolved.
**Selected approach:** PostgreSQL native full-text keyword search plus semantic pgvector results, fused by a custom application-owned Reciprocal Rank Fusion implementation. Ranked evidence must resolve to eligible source/document versions.  
**Approval evidence:** [E13](#e13), explicit keyword and fusion selections; [E31](#e31), scoped implementation and verification.
- **Context:** Assignment pages 2-3 require semantic search, hybrid retrieval, and a project-owned algorithmic search/ranking component. Selecting the approach is not evidence that these requirements are implemented.
- **Alternatives and trade-offs:** BM25 was discussed but is not selected or equated with PostgreSQL built-in ranking. RRF combines ranks rather than uncalibrated raw scores; it still needs candidate identity, truncation, and tie policies. Other ranking algorithms were proposals, not previously approved choices eligible for SUPERSEDED status.
- **E31 implementation:** Exact chunk-slice English to_tsvector/plainto_tsquery with ts_rank; one-statement independently limited channels; application RRF sum(1/(k+rank)), default k=60, equal weights, compact first-duplicate ranks, single-channel retention, stable document/version/range ties and final limiting. Identity is (document_version_id,start,end), never Content or embedding. Trusted settings retain semantic default and optional hybrid; candidate defaults are 20 per channel. Score kinds and channel scores/ranks are explicit. All existing eligibility restrictions apply before each channel limit.
- **Dependencies and consequences:** No FTS/ANN index or migration is introduced. Production language/parameters, source diversity, evaluation and latency targets remain unresolved; measured runtime FTS and eligibility costs justify a separate index/maintenance design review. See the ADR and benchmark; no semantic quality improvement is claimed.
- **Supporting records:** [pgvector and hybrid ADR](pgvector-selection.md), [assignment requirements](#assignment-requirements), [U12](#u12), [system overview](../architecture/system-overview.md).

### U14

**Indexing recovery and concurrency**  
**Status:** CONFIRMED for E16 registration, E25 chunk-plan transactions, E26 foreground resumable indexing/activation, E35 lifecycle concurrency, and E40's operator-run corpus processing; background orchestration remains UNRESOLVED.\
**Approval evidence:** [E16](#e16), [E25](#e25), [E26](#e26), [E35](#e35), [E40](#e40). A09 remains the independent activation principle.

| Approved registration selection | Alternatives, reasoning, and consequences |
| --- | --- |
| Repository owns each session and registration transaction | An application Unit of Work could coordinate multiple operations, but no such operation is needed now. Bootstrap owns the process engine and session factory; the repository receives the factory, isolates concurrent calls, commits before returning, and closes sessions. Separate calls are separate transactions. |
| READ COMMITTED and existing unique constraints | Pre-insert reads alone are insufficient. Stronger isolation, advisory locks, row-lock protocols, and general transaction replay are excluded. Per-transaction isolation is explicitly established before queries. |
| Targeted Content insertion and exact equality | INSERT ON CONFLICT ON CONSTRAINT pk_contents DO NOTHING; a skipped insert is followed by a separate SELECT and exact text comparison. Hash equality alone is insufficient; no Content overwrite. |
| Full rollback and bounded winner resolution | Only SQLSTATE 23505 plus uq_document_versions_source_occurrence enters resolution. Exit the failed transaction/session, then query once in a fresh session and authorize/compare the winner. No orphan candidate Document/Content, savepoint protocol, write replay, or description-mutating UPSERT. Missing winner is RepositoryError. |
| One async registration contract | Port, use case, and in-memory register are async; callers await them. Dataclasses, hashing, extraction, comparisons, and mapping stay synchronous. No duplicate sync/async ports. |
| Required trusted ownership | Required keyword-only owner_id on Document and use-case execution. None is explicitly system-managed; nonblank strings are private principal IDs. Preserve exact identifiers and check stored ownership before Content/conflict details. This is not authentication. |
| Small application-facing error vocabulary | RegistrationConflict(ValueError), ContentHashCollision(ValueError), generic PermissionError, and RepositoryError are independent of SQLAlchemy/psycopg. Recognized ID reuse retains ValueError compatibility. Unexpected database errors are not source conflicts; preserve causes internally without exposing statements/parameters. |
| No general automatic retries; cleanup and uncertain commit | Standard transaction/session contexts roll back and close on failure; cancellation propagates and unusable connections are discarded by the driver/SQLAlchemy. Around COMMIT the outcome can be uncertain; a subsequent identical source identity resolves a committed result. No invented timeout durations or pool policy. |

- **Implementation state:** [PostgreSQLDocumentRepository](../../src/investigation_engine/infrastructure/persistence/postgresql_document_repository.py) implements atomic registration and winner resolution. [Private mapping](../../src/investigation_engine/infrastructure/persistence/_document_mapping.py) snapshots inputs before the first await and reconstructs typed domain values. Existing async in-memory/application contracts propagate ownership; no schema/dependency changes or public entry point were added.
- **Guarantee boundary:** Supported registration works across repository instances/processes against the same database and uses constraints under concurrent writers. It does not promise batch atomicity, automatic reconciliation, cross-revision continuity, or synchronization with future deletion/activation. Returned state is a transaction snapshot. Content reuse across sessions means a shared stored row, not Python object identity.
- **Verification:** Actual PostgreSQL 17.2 verification passed 18 temporary groups: exact domain round trips, new-repository lookup, descriptions/copies, text and both mapping conflicts, ownership and deleted retries, JSONB/null/revision identity, coordinated independent-connection races (identical/different text, mapping conflict, shared/different Content, cross-owner and generated-ID conflicts), complete rollback/connection cleanup, input mutation during an await, CHECK/FK errors, and actual lock-wait cancellation. Unchanged TXT and ten context passages from the actual pinned 2Wiki dev record registered successfully. A forged digest was rejected by the real consistency CHECK; controlled INSERT-parameter fault injection exercised ContentHashCollision without changing schema. No genuine SHA-256 collision was generated. Further executed checks are listed in observed evidence; no permanent tests were added.
- **E21 boundary:** version_chunk_plans describe immutable expected membership manifests, never indexing readiness. version_index_states, transitions, claims, recovery and activation are deferred; storage existence grants no readiness or activation. U14 is not fully resolved.
- **E24 registration alignment:** The in-memory repository now enforces the existing new-occurrence contract: version 1 and no active pointer, checked only after identical source retry resolution and before any write. The PostgreSQL implementation/transactions are unchanged. The two former xfails now pass, with atomic rejection/corrected-candidate and active-retry regression coverage; 28 focused and 141 total unit tests pass. The [testing ADR](testing-strategy.md#registration-alignment-and-remaining-gaps) records the remaining unavailable-image integration boundary. No indexing/update/lifecycle policy is added.
- **E25 scoped chunk-plan transactions:** Each save owns a READ COMMITTED transaction and locks the authoritative DocumentVersion before configuration/plan lookup and writes. This serializes cooperating saves for that version without a new constraint or global configuration registry. Manifest/configuration/memberships commit together; conflicting ranges never overwrite, and corrupt stored plans raise RepositoryError. Other versions may create equivalent configuration rows as E21 allows. This mechanism is confined to plan storage and does not select worker claims, embedding retries, readiness or activation. Real lock/rollback/concurrency execution remains unverified pending the required Docker image.
- **E26 foreground boundaries:** The use case holds no DB session during inference/delay, commits per missing eligible vector, resumes from committed cache, and bounds transient provider retries. New profile creation uses a brief existing-table lock/recheck; vectors use existing PK ON CONFLICT DO NOTHING and exact full-profile lookup. Activation locks Document then target version, revalidates selected-plan/vector coverage and non-deletion, compares the captured current pointer and prohibits downgrades. Same-target retries remain idempotent after revalidation. The serving profile is not stored on the Document pointer, and concurrent inference can duplicate before commit. These choices do not alter E16 registration transactions or add schema/state tables.
- **E27 retrieval boundary:** A single statement snapshot checks visible/nondeleted active versions and the complete explicitly selected profile before top-K; active pointers are never treated as universal configuration readiness. This does not persist profile publication, select a worker lifecycle or change activation/registration transactions.
- **E35 lifecycle policy:** A caller supplies the expected current version. A new version must have a nonblank explicit revision and retain the current version's `kind`, `source_id`, and JSONB-equal `record_locator`; PostgreSQL locks the document row, resolves an identical source-revision retry first, rejects stale pointers and any distinct update while a newer version is pending, and allocates `max(version_number)+1` inside that short transaction. No update transaction spans extraction or embedding. Activation now rejects an older target when a higher version exists; deletion and activation serialize on the document row, and deleted documents cannot be reactivated. Repeated update requests require the same source identity/revision, exact text, and identical structure/source spans. Different revisions are distinct versions even when Content is equal. No durable job state/cancellation/discard operation exists; a permanently unindexable pending version blocks another update until separately resolved.
- **E35 deletion and listing:** Soft deletion locks and marks the owned document without removing versions, Content, vectors, or active pointer. Existing SQL retrieval already filters `deleted_at IS NULL`; tests verify immediate exclusion and shared-content availability. Listing uses SQL visibility predicates (shared or exact owner), stable created/id order and bounded offset pagination; deleted rows are excluded unless explicitly requested by the application caller. Shared documents remain system-managed.
- **E40 operator processing:** A bounded foreground CLI streams the pinned source columns through existing extraction/registration/chunking/plan services. Disjoint foreground processes have separate checkpoints bound to input, configuration, shared ownership, range start and actual cluster/database identity. Commit-before-checkpoint and immutable batch progress records permit replay of interrupted work; they are not a distributed scheduler, lease or batch transaction. No schema change or full-corpus activation follows from registration/plan persistence. See [recovery and verification boundaries](../validation/corpus-scale-2026-10-28.md).
- **Remaining UNRESOLVED:** Background workers/claims/scheduling, distributed rate quotas, automatic recovery/discard of a permanently failed pending version, cross-revision continuity beyond the explicit update operation, persisted serving-profile publication, durable investigation jobs/recovery, production DB timeouts/pool sizing, public authentication/authorization and deployment. Physical connection loss at COMMIT remains an untested operational failure; callers can resolve uncertain outcomes with the same identities. No public access-control readiness is claimed.
- **Supporting records:** [Python persistence stack](python-persistence-stack.md), [physical schema ADR](postgresql-physical-schema.md), [U08](#u08), [U10](#u10), [lifecycle design](../design/document-lifecycle-and-indexing.md), [schema acceptance plan](../validation/schema-acceptance-plan.md).

### U15

**Investigation and LLM integration**  
**Status:** CONFIRMED for E28 single-step RAG and E38's bounded multi-step workflow with configurable Gemini generation; production model selection and broader investigation policy/evaluation remain UNRESOLVED.\
**Selected approach:** E28's AnswerQuestion composes existing SearchEvidence with one AnswerGenerator invocation. E38 adds an application-owned bounded tool loop over that same SearchEvidence path. Standard dataclasses carry bounded evidence, structured results and server-resolved citations; the Gemini SDK stays in infrastructure.\
**Approval evidence:** [E28](#e28), [E38](#e38).
- **Context:** The engine needs evidence-based multistep answers, contradictions, abstention, and structured tool invocation.
- **E28 single-step implementation:** Direct existing SDK, strict structured JSON, whole-excerpt character/count limits, application-controlled E IDs, exact trusted citation mapping, no-evidence/model abstention, and separate sanitized failures. Duplicate/malformed/unknown citations fail without repair. No framework or memory. Prompt instructions do not guarantee factual support; validation guarantees structure only.
- **Model:** No implicit generation default. HTTP startup requires GEMINI_GENERATION_MODEL; gemini-3.6-flash remains a proposed development default, not a confirmed production selection. E30 explicitly authorizes and verifies this model for one billing-disabled Free-tier run, with real structured output and checked factual support. Future access/quota, general semantic quality and upstream stability remain unverified. Embedding configuration is unchanged.
- **E38 bounded workflow:** [InvestigateQuestion](../../src/investigation_engine/application/investigations.py) orchestrates explicit search calls and final decisions with bounded turns, searches, evidence and request budgets. The [Gemini investigation adapter](../../src/investigation_engine/infrastructure/generation/gemini_investigation_model.py) maps structured function calls/results and preserves provider continuation state. Evidence IDs are application-controlled and citation IDs are validated/resolved against actual search results. The `/investigations` HTTP route and UI mode are configurable and use the existing application lifecycle; the browser does not select an owner, model or embedding profile. Limits and smoke evidence are in the [E38 validation record](../validation/multi-step-investigation-2026-10-28.md).
- **Dependencies and consequences:** This scoped workflow is not a general planner, durable job system, or proof of factual grounding. Broad planning behavior, semantic citation support, contradiction and abstention quality, production model/pinning, operational quota limits and answer-quality evaluation remain unresolved. See [single-step RAG ADR](single-step-rag.md) for its original boundaries and [E38 validation](../validation/multi-step-investigation-2026-10-28.md) for the extension. No hybrid-search or deployment decision follows from this implementation.
- **Supporting records:** [ADR-005](005-application-architecture.md), [assignment requirements](#assignment-requirements).

### U16

**API and UI**  
**Status:** CONFIRMED for E28 minimal FastAPI question API and E29 shared-corpus question interface; authenticated/document-management APIs and deployment remain UNRESOLVED.\
**Selected approach:** GET / browser UI with same-origin static HTML/CSS/vanilla JavaScript; unchanged GET /health and POST /questions, strict question-only input, validated output, server-fixed shared-only scope, error mapping and lifespan ownership.\
**Approval evidence:** [E28](#e28), [E29](#e29).
- **Context:** Users must manage documents, ask questions, and inspect sources.
- **Alternatives and trade-offs:** Server-rendered UI versus separate frontend; backend frameworks and endpoint contracts offer different complexity and interaction trade-offs.
- **Implementation:** FastAPI/Uvicorn and explicit HTTP Pydantic dependency are uv-managed. No global import-time clients, engine or configuration loading. Startup selects one existing persisted chunking configuration by server settings and the existing Gemini embedding profile; no migrations or automatic indexing. Original provenance is serialized from validated retrieval, not model output.
- **E29 interface:** Static presentation assets reside inside the API package, with a root FileResponse and static mount. JavaScript sends only the exact question; server citation entries drive [E1] source buttons and native disclosures. Plain-text rendering, fixed safe error messages, bounded question length and client timeout, duplicate suppression and mobile/keyboard states add no business logic. No new dependency/build step/CORS is needed. The wheel includes the assets automatically.
- **Dependencies and consequences:** No client owner/profile/model/cost controls are accepted. Shared-only access is intentionally unauthenticated; authentication, rate/admission policy, upload/list/delete endpoints and hosting remain separate decisions. The Windows launch uses the standard selector event loop required by psycopg. See [implementation and limitations](single-step-rag.md#browser-interface-e29).
- **Supporting records:** [ADR-005](005-application-architecture.md), [assignment requirements](#assignment-requirements).

### U17

**Evaluation and test sequencing**  
**Status:** CONFIRMED for E22 software testing and the scoped E32 retrieval / E39 answer-quality evaluations; held-out methodology and a complete real-model evaluation remain UNRESOLVED.\
**Selected approach:** pytest/pytest-asyncio/pytest-cov, unit/integration/external categories, Testcontainers PostgreSQL/pgvector with real commits/cleanup, optional pinned-corpus checks, explicitly gated live smoke tests, no global coverage minimum, GitHub Actions, and tests alongside future features.\
**Approval evidence:** [E22](#e22) supersedes A10's sequencing preference only; [E32](#e32) authorizes the scoped retrieval evaluation; [E39](#e39) authorizes the answer-quality evaluation extension and bounded live sample.
- **Scope:** No speculative provider fake before an interface exists. No live embedding calls, cloud database access or deployment. The default suite needs neither Docker nor dataset/API credentials.
- **Implementation and evidence:** [Testing ADR](testing-strategy.md), [tests](../../tests), [CI workflow](../../.github/workflows/tests.yml). Integration startup fails clearly rather than using an application database; the ADR records actual execution limitations and coverage.
- **E32 implementation:** 40 cases (8 factual projections, 12 annotated two-relation, 8 branching bridge comparisons, 6 controlled unanswerable, 6 contradictory), fixed 218-occurrence/197-Content corpus with distractors. Strict occurrence/hash/full-sentence locator matching, Recall@5/10/20, truncated MRR, fractional/all-required coverage, latency, failures/unavailable cases and paired comparisons. Ordinary Python, existing dependencies, JSON/Markdown and offline query cache; no production search changes. See the methodology for partial-sentence and duplicate handling.
- **E39 implementation:** The fixed suite carries evaluation-only explicit answer and conflict annotations; deterministic conservative answer matching, strict/partial source-sentence citation metrics, outcome/error categories, investigation usage/latency/search records, paired configuration checks, ASCII-safe console summaries, and inspectable JSON/Markdown. Real providers receive the question/evidence only, never answer/gold labels. Failures without a completed result produce unobserved evidence metrics rather than false zeroes. See [answer-quality methodology and live sample](../validation/answer-quality-evaluation.md).
- **E40 scope:** Matched structural comparisons retain the same 218 source records and 40 questions across the existing chunkers; a separately sized bounded real-Gemini pilot tests compatible profiles, source/owner eligibility and restart persistence. Neither structural coverage nor tiny-pilot Recall@K proves full-corpus semantic quality. Safe provider diagnostics add status/category/operation/model/counter information without retaining SDK payloads.
- **Remaining evaluation decisions:** Held-out sampling/leakage controls and broader real-model sample selection remain open. Semantic claim support beyond gold occurrence agreement requires human review; no judge score is inferred. The live sample is partial (one correct single-step answer, one provider failure, no multi-step pairs), not a completed 40-question evaluation. Controlled fake-vector retrieval measurements do not select a superior production retrieval mode. U03 remains unchanged.
- **Supporting records:** [Schema acceptance plan](../validation/schema-acceptance-plan.md), [assignment requirements](#assignment-requirements).

### U18

**Hosting and operations**  
**Status:** CONFIRMED intended database-hosting selection, conditional on feasibility; other hosting/operations remain unresolved.  
**Selected approach:** Google Cloud SQL for PostgreSQL free trial for the assignment demonstration, subject to trial eligibility, offered resources, extension support, connectivity, and actual zero-cost feasibility. Activate near deployment rather than consuming the serving period during preparation.  
**Approval evidence:** [E13](#e13), explicit conditional database-hosting selection; no provisioning or paid services authorized.
- **Context:** A public free-hosted application must retain data after restarts. The user expects submission/review within the trial period, but the interview date is unconfirmed. The database will eventually combine authoritative and retrieval work; neither cloud service nor persistent adapter currently exists.
- **Alternatives and trade-offs:** Azure PostgreSQL and Neon offer different free/trial lifetimes, resources, and quota behavior; neither was found incapable or selected as fallback. Cloud SQL's advertised trial resources motivate evaluation, not a capacity or performance guarantee. Provider/account/network conditions must be checked again at deployment.
- **Verification and blockers:** Official sources confirm the advertised resource figures and product-level pgvector availability; actual trial operation and user eligibility remain untested. Google documents chargeable public-internet/cross-region trial traffic, expiry, and backup limitations. Zero-cost connectivity has not been established. Necessary unavoidable paid resources would block provisioning under the assignment constraint and require revisiting the plan. See the ADR's official references and deployment validation list; do not interpret conditional selection as guaranteed free hosting.
- **Dependencies and consequences:** Backend/frontend hosting, region/network configuration, IP/transfer charges, instance provisioning, pool sizes, automation, backup/monitoring implementations, and paid services remain unapproved. SQLAlchemy/Alembic and repeatable ingestion aid portability but do not migrate data or eliminate extension/version/capacity constraints. Trial expiry/evaluation delay and migration remain genuine risks. U07/U11/U12/U14/U17 still govern capacity/configuration, transactions, recovery, and evaluation.
- **Supporting records:** [Cloud SQL hosting ADR](google-cloud-sql-hosting.md), [Python stack ADR](python-persistence-stack.md), [pgvector ADR](pgvector-selection.md), [assignment requirements](#assignment-requirements).

### N01

**Additional documentation organization**  
**Status:** NEW  
**Selected approach:** Unset.  
**Approval evidence:** None for a selection; [E04](#e04) authorizes recording the open decision only.
- **Context:** Requirements, ingestion, evaluation, and roadmap information need maintainable homes beyond this approved index.
- **Alternatives and trade-offs:** Extend existing documents versus create focused documents; the former reduces files, the latter separates responsibilities but can duplicate content.
- **Recommendation (not approved):** Propose a small file map for approval before creating these documents.
- **Dependencies and consequences:** Blocks additional document organization only. A11 authorizes this register and linked ADR reconciliation, not a complete new documentation hierarchy.
- **Supporting records:** [Documentation index](../README.md), [approval evidence E03](#e03).

### N02

**SourceReference field compatibility**  
**Status:** CONFIRMED  
**Selected approach:** Replace unused record_id with record_locator and add metadata; preserve kind, source_id, display_name, and revision.  
**Approval evidence:** [E07](#e07), explicit approval item 1 and compatibility requirement item 7.
- **Context:** U04 requires structured provenance without unrelated changes to existing workflows.
- **Alternatives and trade-offs:** Keeping record_id as a compatibility alias would retain two representations; moving display_name/revision would change current callers. The user approved replacement while preserving the remaining fields.
- **Reasoning:** No repository caller reads or supplies record_id. TXT supplies display_name, so its current call continues unchanged.
- **Dependencies and consequences:** External consumers using record_id must adopt record_locator; no compatibility alias or physical data migration is introduced. The dataset adapter now uses the approved structured fields.
- **Implementation state:** Implemented in SourceReference; existing TXT and registration code is unchanged and passed focused smoke checks.
- **Supporting records:** [U04](#u04), [SourceReference](../../src/investigation_engine/domain/provenance.py), [TXT adapter](../../src/investigation_engine/infrastructure/ingestion/txt_adapter.py).

### N03

**Structured value types and defensive copying**  
**Status:** CONFIRMED  
**Selected approach:** Standard recursive JsonValue and JsonObject type aliases, supporting nested JSON-compatible dictionaries/lists and scalar values. Use simple defensive copying, without a custom deeply immutable representation or a deep in-memory immutability guarantee.  
**Approval evidence:** [E07](#e07), explicit approval items 2, 4, and 6.
- **Context:** Frozen dataclasses do not freeze nested containers; nested source descriptions are now explicitly supported.
- **Alternatives and trade-offs:** The earlier flat scalar/read-only mapping proposal was not approved. Mutable recursive JSON is more expressive and simpler than recursive freezing, but owners can mutate contained values after construction.
- **Reasoning:** Retain ordinary Python values and standard dataclasses without Pydantic, PostgreSQL, or new domain dependencies. Standard-library deepcopy detaches caller-owned nested containers at construction.
- **Dependencies and consequences:** JsonObject is dict[str, JsonValue]; JsonValue includes string, integer, float, boolean, None, list[JsonValue], and JsonObject. No JSON strings inside record_locator, custom occurrence entity, mapping proxy, or immutable JSON framework. Runtime JSON compatibility is checked at external input boundaries, not guaranteed by these aliases; see N05.
- **Implementation state:** Aliases and deepcopy calls live in the existing provenance module. Caller mutation isolation and allowed owner mutation were checked with nested dictionaries/lists.
- **Supporting records:** [U04](#u04), [SourceReference](../../src/investigation_engine/domain/provenance.py).

### N04

**Absent locator and metadata defaults**  
**Status:** CONFIRMED  
**Selected approach:** field(default_factory=dict) for both record_locator and metadata.  
**Approval evidence:** [E07](#e07), explicit approval item 3.
- **Context:** The existing TXT adapter identifies uploads via source_id and does not supply structured locator/metadata.
- **Alternatives and trade-offs:** None would add optional handling; required fields would change callers. Default factories preserve current usage and avoid shared default dictionaries.
- **Reasoning:** Each field on each instance receives its own dictionary; N03's defensive copying also detaches explicitly supplied inputs.
- **Dependencies and consequences:** Empty mappings are valid generic defaults; source-specific required coordinates must be validated at the relevant input boundary. This does not define empty extracted-text behavior (U09).
- **Implementation state:** Implemented; separate field/instance defaults and existing TXT defaults passed focused checks.
- **Supporting records:** [U04](#u04), [SourceReference](../../src/investigation_engine/domain/provenance.py), [TXT adapter](../../src/investigation_engine/infrastructure/ingestion/txt_adapter.py).

### N05

**Validation boundaries and domain invariants**  
**Status:** CONFIRMED  
**Selected approach:** Keep standard dataclasses in the domain, preserve domain invariants, and use Pydantic where appropriate for external input validation. Do not introduce Pydantic or database dependencies into the domain.  
**Approval evidence:** [E07](#e07), explicit approval items 4 and 5.
- **Context:** External payload validation is distinct from domain invariants and Python type annotations.
- **Alternatives and trade-offs:** Domain Pydantic models would add a dependency and change architecture; a custom recursive runtime JSON validator would add machinery beyond this minimal change. The approved separation keeps boundary validation outside the domain.
- **Reasoning:** Preserve the nonblank source_id invariant and existing dataclass behavior; validate untrusted values before constructing domain objects. JsonValue/JsonObject annotations are not runtime validators.
- **Dependencies and consequences:** The 2Wiki adapter now validates its input using standard Python checks under U09-2W and the no-new-dependencies constraint. Its locator contains validated split/example_id strings and an enumerated context_position integer; metadata is empty. Pydantic remains an option where appropriate, not a mandatory dependency. Other format policies remain U09; retries remain U08. Direct internal callers remain responsible for correctly typed JSON values, including valid numeric values.
- **Implementation state:** Nonblank source_id validation remains intact; domain and application imports remain provider-independent. Source-specific validation is implemented only at the 2Wiki boundary; no generic JSON validator was added.
- **Supporting records:** [U04](#u04), [SourceReference](../../src/investigation_engine/domain/provenance.py), [ADR-005](005-application-architecture.md).

### N06

**2Wiki source identifier and dataset revision contract**  
**Status:** CONFIRMED  
**Selected approach:** source_id identifies the original dataset; revision identifies its version; record_locator identifies the passage within that version. Use the existing real source identifier xanhho/2WikiMultihopQA, the caller-supplied actual revision, and a locator containing split, example_id, and context_position. Use the passage title as display_name and ExtractedDocument.title; metadata defaults to an empty object because no extra description is needed. Do not serialize composite identity into source_id or duplicate dataset/revision/coordinates in metadata.  
**Approval evidence:** [E09](#e09), explicit field semantics, rejection of composite string IDs, and authorization to proceed after compatibility verification.
- **Context:** Original-source identity differs from extracted-document identity and exact-content identity. A parsed record does not establish its dataset revision.
- **Alternatives and trade-offs:** The proposed composite JSON-array source_id was explicitly rejected and was never approved; it is not a SUPERSEDED decision. Separate structured fields keep responsibilities clear. Callers must supply actual revision context instead of the adapter guessing it from record contents or defaulting to the currently downloaded snapshot.
- **Reasoning:** Preserve generic domain semantics and independent occurrence provenance without extra entities, optional source-specific fields, or encoded strings.
- **Dependencies and consequences:** extract_2wiki accepts a parsed record plus keyword split and revision. The pinned local dataset revision is 612bc5039a457880d9e7d84c3b0a4cf154b70e4f. The adapter checks revision is nonblank but cannot independently prove the caller's snapshot attribution. Complete occurrence identity is represented by source fields together: kind, source_id, revision, and record_locator; compare those fields structurally if needed. Do not use full dataclass equality (which includes labels/metadata) as an identity rule or add a hash/key abstraction without a concrete use case. U08 now implements a private occurrence lookup for sequential in-memory idempotency; this does not introduce a domain key or select a persistent schema (U07).
- **Compatibility evidence:** Registration creates UUIDv7 Document/DocumentVersion IDs and carries provenance unchanged. The in-memory repository indexes those IDs and exact Content hashes, never source_id. TXT identifies the original uploaded file using the caller-supplied source_id and is compatible unchanged. LocatorKind.SENTENCE already exists. Only the SourceReference docstring needs clarification; no domain field or application contract changes are required.
- **Supporting records:** [U01](#u01), [U04](#u04), [U09-2W](#u09-2w), [SourceReference](../../src/investigation_engine/domain/provenance.py), [2Wiki adapter](../../src/investigation_engine/infrastructure/ingestion/twowiki_adapter.py), [registration](../../src/investigation_engine/application/use_cases/register_document.py).

### N07

**Experimental reference tokenizer and dependency**  
**Status:** CONFIRMED for experimental chunking only; production tokenizer remains UNRESOLVED.  
**Approval evidence:** [E18](#e18), explicit first selection.
- **Selected approach:** tokenizers==0.23.3; one file-loading adapter using the Voyage 4 reference tokenizer at repository revision 44f3b2ae4ddf33403ed4dd66bec3fa48ff7dbbf9 and artifact SHA-256 c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539. No implicit downloads. The caller supplies a file and its pinned identity; mismatch fails.
- **Alternatives and reasoning:** Defer the concrete adapter/dependency and implement only abstract algorithms, or add an SDK/framework/local inference model. The approved small tokenizer runtime supplies actual token/offset mappings without embedding inference or a provider SDK. Changing tokenizers does not rewrite the selection algorithms.
- **Consequences:** Counts are reference-token counts, not exact Gemini counts. The artifact's NFC normalizer acts inside encoding; canonical strings, SHA-256 and character slices remain unchanged. Runtime and artifact identity are recorded for reproducibility; truncation/padding/added special tokens are disabled.
- **Implementation evidence:** [Tokenizer port](../../src/investigation_engine/application/ports/text_tokenizer.py), [adapter](../../src/investigation_engine/infrastructure/tokenization/huggingface_tokenizer.py), [dependency configuration](../../pyproject.toml), [lockfile](../../uv.lock), [chunking ADR](deterministic-chunking.md).

### N08

**Sentence-packing fallback**  
**Status:** CONFIRMED  
**Approval evidence:** [E18](#e18), explicit second selection.
- **Selected approach:** With absent supplied sentence spans or uncovered non-whitespace text, use fixed-token splitting at the configured content budget and zero overlap. Preserve all whitespace; reject unordered/overlapping/out-of-bounds sentence spans rather than repair them. No sentence detector or invented sentence indices.
- **Alternatives and reasoning:** Reject documents lacking sentence structure, including the current TXT adapter. The approved fallback makes TXT usable without changing extraction or claiming line spans are sentences.
- **Consequences:** Sentence packing still uses each version's actual structure; fallback does not modify it. Whitespace separators join the preceding sentence packing unit; leading whitespace joins the first. Oversized units split with zero overlap. Source intersections remain original-coordinate fragments.
- **Implementation evidence:** [Application chunking](../../src/investigation_engine/application/chunking.py), [chunking ADR](deterministic-chunking.md). Extraction policies for other formats remain U09.

### N09

**Empty Content in chunk generation**  
**Status:** CONFIRMED  
**Approval evidence:** [E18](#e18), explicit third selection.
- **Selected approach:** Return an empty tuple of chunks for empty Content; preserve nonempty whitespace-only text in nonempty chunks.
- **Alternatives and reasoning:** Raising ValueError would introduce a separate chunking rejection for a value currently representable in Content. No chunks is explicit and avoids empty retrievable units.
- **Consequences:** This is not an extraction acceptance policy or an indexing-success rule. Empty-input acceptance at format boundaries remains U09; readiness/activation remains U14.
- **Implementation evidence:** [Application chunking](../../src/investigation_engine/application/chunking.py), [chunking ADR](deterministic-chunking.md).

### N10

**Central infrastructure environment configuration**  
**Status:** CONFIRMED for the existing settings and approved local proxy connection.  
**Approval evidence:** [E19](#e19), [E20](#e20).
- **Selected approach:** One infrastructure module exposes a shared frozen `settings` dataclass. python-dotenv loads working-directory `.env` on first import, preserving process variables and literal credentials (interpolation disabled). DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD remain required; GEMINI_API_KEY defaults to None. DB_PORT is explicitly parsed and range-validated. No unused boolean settings are introduced. URL.create and the guarded 127.0.0.1:15432 proxy connection remain unchanged.
- **Alternatives and reasoning:** E20 supersedes E19's explicit load_settings() and uv-only loading. No Pydantic, nested configuration or duplicate loaders. Alembic imports settings only for online operation, keeping offline generation independent of credentials. Secrets remain excluded from repr; domain components must not import settings.
- **Implementation evidence:** [Configuration](../../src/investigation_engine/infrastructure/config.py), [Alembic environment](../../migrations/env.py), [configuration usage](postgresql-physical-schema.md#implementation-and-migration-use). Alembic retains its migration-local NullPool, async cleanup and Windows loop handling. Domain models and existing repositories are unchanged.
- **Scope:** No migration execution, production pool/timeout policy, authentication, implicit proxy startup, additional connection mode, provider choice, embedding calls or U14 lifecycle resolution is approved by this change.
- **E20 verification:** Temporary dummy-credential checks passed for automatic loading, process precedence, literal secrets, one shared frozen instance, required settings, port validation, optional Gemini key, hidden secret representations, syntax, domain separation and Git ignore rules. uv lock --check --offline passed. No database/API calls or permanent test files.
- **Historical E19 verification (before E20):** Temporary in-memory checks passed for immutable/fresh snapshots, required settings, invalid ports, exact password preservation, hidden secret representations, optional Gemini credentials and rejection of other endpoints. Syntax checks passed. The new helper connected through the existing proxy using SQLAlchemy in an explicitly read-only session: PostgreSQL 18.6, pgvector 0.8.5, and public.alembic_version still absent. No migrations, database writes or permanent test files were introduced.

## Assignment requirements

Source: the supplied eight-page [English assignment](C:/Users/merli/Downloads/Applied_AI_Investigation_Engine_Task_English.pdf), read in full during the review. The file is outside the repository, so these links are local evidence locations, not portable submission links. The Georgian original was not inspected.

| Pages | Mandatory outcome and distinction |
| --- | --- |
| 2 | Three or more file formats; text/metadata extraction; chunking; duplicate handling; add/update/delete; comparison of two or more chunking strategies. |
| 2-3 | Vector-capable storage, embeddings, semantic and keyword hybrid retrieval, and at least one custom search/ranking component. Ordinary SQL text search alone is insufficient. No specific vector product, BM25, or RRF is mandated. |
| 3 | At least one LLM with RAG, cross-document multistep reasoning, evidence and citations, contradiction attempt, abstention, provider error/limit handling, and tool calling or equivalent structured invocation. |
| 4 | Own automated evaluation with at least 40 questions, including simple, multistep, cross-document, unanswerable, and contradictory cases; quantitative retrieval/answer assessment; at least two configurations; failure analysis. Named metrics are examples, not fully specified scoring rules. Python backend and functional document/question/source UI. |
| 5 | Use one or more permitted datasets; reproducible justified subsets and supplemental public/synthetic material are allowed. Process at least 50,000 documents or independent text records while preserving sources and repeatable evaluation. 2Wiki is explicitly permitted. |
| 6 | Actual public URL on free infrastructure; persistent data; secrets protection; deployment/resource/cost documentation. Sleep/request limitations are acceptable only if documented and compatible with demonstration. |
| 7 | Modular Python, responsibility separation, error handling, automated software tests, integration, secure configuration, documentation, and performance/LLM-cost consideration. No mandatory framework or design pattern. Bonus features do not replace requirements. |
| 8 | GitHub repository/history, live demo, README, evaluation report, and AI usage report; ability to explain and modify the code without AI during the practical interview. |

**Test sequencing:** E22 ends A10's temporary deferral and selects the permanent software-testing strategy under U17. Both automated software tests (page 7) and automated evaluation (page 4) remain final delivery requirements; the evaluation methodology is still unresolved.

## Observed evidence

The following preserves historical review observations and temporary implementation verification. These are not approval evidence. E22's permanent suite and current execution results are recorded separately in the [testing ADR](testing-strategy.md).

- **E40 complete corpus (2026-10-28):** The application processed all 125,760 real pinned development occurrences into 56,680 reusable Contents and complete whole-passage plans in disposable PostgreSQL 18.4/pgvector 0.8.5. Exact source comparisons, interrupted-batch recovery, actual database restart, completed-checkpoint no-op and explicit 1,000-occurrence repository replay passed. Validation took 1,262.41 seconds and measured 253,613,759 database bytes. Full-corpus vectors/activation/searchability were zero. A separate real-Gemini pilot verified six stored vectors, four shared searchable sources, one separately owned duplicate, one excluded inactive record and restart-stable semantic/hybrid results. Task 14 made 12 token-count plus 12 embedding attempts, no generation requests. Complete suite: 620 Python plus 26 JavaScript tests passed, 7 opt-in Python checks skipped. Details: [report](../validation/corpus-scale-2026-10-28.md).
- **E41 evaluation corpus (2026-10-28):** Separate named-volume PostgreSQL 18.4/pgvector database, actual repository migrations, 218 exact-provenance source occurrences, 197 unique Contents/chunk embeddings, 218 active/searchable versions, and container-recreation search verification. Synchronous Gemini batches of 1/2/5/10/25 all returned one ordered 768-dimensional vector per input; 25 was the largest tested, not a provider maximum. Paired semantic/hybrid evaluation ran all 40 questions (34 evidence-scored) on persisted vectors. Full counts, retrieval/category metrics, requests, token-accounting caveat, tests and generation-evaluation deferral are in the [Task 15 validation report](../validation/batched-gemini-evaluation-2026-10-28.md). The 125,760-occurrence corpus remains untouched by embeddings.

- **E17/E18 chunk verification (2026-10-25):** Temporary Unicode/TXT/configuration checks passed; complete pinned-dev analysis checked 125,760 occurrences, 56,680 Contents, 56,702 unique sentence layouts and 400,892 original sentence spans. Whole/fixed128/sentence128 generated 125,760/167,949/169,760 occurrence memberships and 56,680/83,473/84,637 distinct ranges, exactly matching preceding distinct-range measurements. All 22 shared-Content/multiple-layout cases were checked; Carlos F. Borcosque demonstrates different sentence plans over equal Content. Detailed denominators, distributions, repeatability checks and limits are in the [chunking ADR](deterministic-chunking.md#verification-and-measurements). No embeddings, PostgreSQL corpus registration, permanent tests or retrieval/citation quality claims. The tokenizer normalizes internally during encoding; original Content strings/hashes and source offsets remain unchanged.

- **E16 real PostgreSQL registration verification (2026-10-25):** PostgreSQL 17.2 was initialized in an isolated loopback-only temporary cluster with the unchanged Alembic migration. Eighteen integration groups passed and passed again after a session-context cleanup correction; details are in U14. Races used coordinated independent backend connections, including forced initially absent occurrence reads, so they were not sequential retry demonstrations. Row counts and connection checkout counts verified complete losing-attempt rollback and cleanup.
- **E16 additional executed checks:** A separate Python process resolved the committed IDs/deleted state; a real database lock timeout and refused connection raised sanitized RepositoryError and released connections. Timeout values existed only in the temporary verifier, not production code/configuration. A controlled exception after actual COMMIT initially exposed a combined-context cleanup gap; separate outer session/inner transaction contexts corrected it. The corrected check passed with no checked-out connection, and an identical retry returned the committed IDs without duplicates.
- **E16 verification limits:** No genuine SHA-256 collision or real network interruption/cancellation at COMMIT was generated. The real digest CHECK rejected a forged digest; an INSERT-parameter fault injection exercised the hash-collision branch without changing schema. An after-commit injected failure simulated lost acknowledgement; actual cancellation was exercised while waiting on a PostgreSQL lock. No production load, authentication, lifecycle/indexing concurrency, cloud deployment, or whole-corpus registration was verified. A10 still defers permanent automated test files.

- **Initial review snapshot (historical):** domain models, application extraction/registration contracts, TXT adapter, and in-memory repository existed. No persistent database adapter, production 2Wiki/PDF/DOCX adapter, chunks, retrieval, LLM integration, API/UI, or deployment was found at that time. The 2Wiki adapter has since been implemented under E08/E09. The root README and test placeholder were empty; Git had no commits and project files were untracked.
- **Manual smoke checks:** separate document/source identities, shared exact in-memory Content, inactive newly registered versions, differing fingerprints for differing whitespace, and sample TXT span coverage passed. These do not establish concurrent or durable registration.
- **Local dataset:** pinned revision `612bc5039a457880d9e7d84c3b0a4cf154b70e4f`, 30,056,098 bytes, SHA-256 `c0d8b60b9026b728fb07ad74c5252a0f188f6942e8ba5c02df4dfa369502ea8d`. Batched read-only inspection reproduced 12,576 question rows, 125,760 occurrences, 54,957 titles, 56,702 serialized sentence lists, 56,709 title/list pairs, and 1,745 titles with variants.
- **Supporting-fact audit:** JSON-decoded each row's contexts and supporting facts, grouped context entries by title, and checked matching-title cardinality and sentence-index bounds. All 30,687 references resolved to one title occurrence with a valid index. There were 463 contexts with repeated titles elsewhere; no duplicate question IDs, empty sentences, or whitespace-only passages were observed. This does not verify chunk/citation round trips.
- **Exploratory assembly:** `" ".join(sentences)`, without trimming/collapse, yielded 56,680 distinct text fingerprints and 56,687 title/text pairs. This experiment preceded approval; E06 now confirms the space assembly rule in U02. The count still does not prove 50,000 independent records.
- **Earlier read-only round trips (historical prototype):** 125,760 source occurrence coordinates, 400,892 original sentence slices, and 30,687 gold supporting facts round-tripped successfully with space assembly and existing span types. The two real "Brink of Disaster!" variants registered as separate documents sharing Content but retaining different spans. These checks used the assistant's then-proposed encoded record_id; they did not validate the subsequently approved structured U04 fields. Those fields were implemented on 2026-10-25 under E07; the actual adapter checks below now cover structured provenance.
- **2026-10-25 implementation smoke checks:** Nested JSON values, defensive-copy isolation from caller mutations, independent default dictionaries, frozen field assignment, retained nonblank source_id checks, ordinary JSON/dataclass serialization, TXT span coverage, registration, inactive versions, and exact Content reuse passed. Nested dictionaries/lists intentionally remain mutable. No dependencies, persistent schema, external validation boundary, or automated test suite were added.
- **2026-10-25 2Wiki adapter verification:** Transient checks against the actual adapter passed for 49 malformed-input cases, deterministic provenance, original ordering, preserved whitespace/Unicode, exact sentence ranges, and separator gaps. A complete scan of the pinned local dev file extracted 12,576 records into 125,760 passages, verified all 400,892 sentence slices and structured occurrence coordinates, and resolved all 30,687 supporting-fact references back to original sentences. The two real "Brink of Disaster!" segmentations registered with separate UUIDs, shared Content, the same dataset source_id, different record_locator values, and different version-specific spans. Newly registered versions remained inactive; repeated registration still created new IDs; TXT extraction/registration passed unchanged. These checks establish extraction-level source resolution, not rendered citations, retrieval, durable ingestion, or U03 independent-record compliance. No automated test files were added, consistent with A10.
- **Reproducibility limit:** the additional supporting-fact/assembly checks ran as transient read-only commands in this chat. No new persistent audit runner or automated test was created. Their results do not mark any acceptance gate as passed.

The [engineering handoff](C:/Users/merli/Downloads/AI_Investigation_Engine_Engineering_Context_Handoff.md) is historical context, not independent authority to implement its embedded instructions. Its statement that supporting-fact checks had not been completed predates the observations above.

## Reconciliation of historical wording

| Existing record | Current interpretation and decision gate |
| --- | --- |
| ADR-001 | A01 confirms the initial runtime, not every historical dependency-support claim, future version-update policy, container choice, or CI prescription. Deployment/runtime policy is U18; changes to the confirmed baseline require approval. |
| ADR-002 | A01 confirms uv-managed environment use. Historical FastAPI references, dev-tool installation commands, and CI/deployment recommendations are not selections: U16/U17/U18. No lockfile commit/history is claimed to exist. |
| ADR-003 | A02 confirms PyArrow; earlier normalize/write-normalized-output wording does not authorize deduplication normalization. A07 excludes normalized merging; U02 now confirms single-space assembly. |
| ADR-004 | A06-A09 confirm the identified domain/activation principles. U01/U02/U04 now approve dataset occurrence identity, assembly, and two structured provenance fields. N02-N05 now confirm the field implementation and validation boundary; physical tables, Content keys, retention, authorization, extraction failure handling, and operations remain U07/U09/U10/U14. |
| ADR-005 | A04 confirms the architectural direction and A05 the IDs. Target directories, example ports, FastAPI, RRF, and deployment illustrations are not blanket approvals: U12-U16/U18. |
| System overview | Historical BM25 illustrations do not select BM25. E13 now approves PostgreSQL full-text search and custom RRF (U13); FastAPI appears as planned backend context, while U16 implementation details remain open. Read the latest ADRs rather than treating old diagrams as approvals. |
| Data contract | U01/U02/U04 confirm occurrence identity, assembly, and the provenance field split. Old claims that these choices are wholly open predate E06. N02-N05 now confirm types/defaults/copying and validation boundaries; physical schema and unrelated additions remain U07/U09/U10/U11. |
| Lifecycle design | A09 confirms delayed activation. U08 and E16 implement registration retries/transactions; indexing, activation, jobs, cleanup and remaining lifecycle races stay unresolved under U14. |
| Historical audit | Its not-yet-run supporting-fact note is historical; see Observed evidence above. U03 approves the measured 56,680 exact Contents as the conservative count baseline; semantic independence is not established by exact uniqueness. |
| Schema acceptance plan | The broader scenarios remain an acceptance backlog. E22 ends A10's deferral and adds permanent tests for implemented behavior; the testing ADR records actual execution. Unimplemented lifecycle, formats, retrieval and evaluation remain separate work. |

No SUPERSEDED entry is manufactured from an assistant suggestion or exploratory audit. If an earlier accepted decision is later shown to have been explicitly replaced, record its approval and replacement evidence before applying that status.

## Using the approved 2Wiki adapter

The caller supplies a parsed record (_id and context), the split, and the actual dataset revision. The local Parquet stores context as a JSON string; decode it explicitly before calling the adapter. Loading/decoding and batch failures stay outside extraction. For the existing pinned local dev file:

```python
import json

from investigation_engine.infrastructure.ingestion.twowiki_adapter import extract_2wiki

# row is a row read from the pinned local dev.parquet with PyArrow.
record = {**row, "context": json.loads(row["context"])}
extracted = extract_2wiki(
    record,
    split="dev",
    revision="612bc5039a457880d9e7d84c3b0a4cf154b70e4f",
)
# Within a trusted async caller; register is RegisterDocument(repository).
for extracted_document in extracted:
    document, version = await register.execute(extracted_document, owner_id=None)
```

This demonstrates the existing interface, not a new ingestion runner or retry policy. The adapter completes extraction before returning its list; registration of that list is not an atomic batch. The caller must use the revision of its actual input; the adapter performs no network verification.

## Next decision discussion

U07 remains confirmed and unchanged. E16 implements the scoped U14 registration foundation: async port/use case/adapters, required trusted ownership, isolated repository-owned READ COMMITTED transactions, exact Content reuse, bounded winner resolution, and cleanup/error semantics. There is no public/authenticated application bootstrap yet; ordinary dependency injection supplies the session factory, and engine creation/disposal belongs to the eventual composition root. No pool sizes or numeric timeouts were selected.

E25/E26 implement complete plans, resumable foreground persistence and safe activation; E27 implements exact selected-profile semantic retrieval; E38 implements bounded foreground investigation. Remaining U14 work concerns background recovery/claims, cross-revision updates, profile publication, durable investigation jobs and future lifecycle races. Keyword/hybrid contracts, retrieval quality/corpus-scale measurement and U17 evaluation remain separate. U10/U16 must select the authenticated principal source and public access boundaries before exposure to untrusted clients. This milestone selects no next feature or background lifecycle mechanism.
