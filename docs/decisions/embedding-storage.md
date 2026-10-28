# Derived chunk-plan and embedding storage

**Status:** CONFIRMED for the initial storage schema by [E21](register.md#e21),
2026-10-27. Production provider selection and the remaining U14 lifecycle are
unresolved. E23 adds generation, E25 chunk-plan persistence and E26 resumable
foreground embedding persistence/activation. E27 adds exact semantic retrieval
with selected-profile completeness, trusted visibility and source citations,
verified using disposable PostgreSQL 18/pgvector 0.8.5. E31 implements hybrid
retrieval and E28 adds single-step RAG. E38 adds a bounded multi-step application
workflow; multi-step quality evaluation remains incomplete, and no cloud migration
has run.
E37's separate [inline-storage experiment](../validation/hybrid-retrieval-performance.md#e37-inline-embedding-storage-comparison-2026-10-28)
compares PLAIN and EXTERNAL without changing the confirmed schema or selecting a
production column policy.

**E30 verification (2026-10-28):** [One authorized live run](../validation/live-rag-2026-10-28.md)
confirms actual countTokens, two persisted document embeddings and one query
embedding on gemini-embedding-2/768, transactional activation and cosine retrieval,
followed by structured 3.6 Flash generation and browser citation inspection.
All seven Gemini requests succeeded. Earlier unverified notes are historical;
general quality/scale and model stability are not established by this small case.

## Scope and responsibilities

Five derived tables extend the unchanged three authoritative tables. No chunks
table, chunk UUID, copied chunk text, title/context digest, indexing-state table,
or additional DocumentVersion key is introduced. The conceptual chunk identity
remains `(content_sha256, start, end)`.

| Table | Columns (all required except model_revision) | Key |
|---|---|---|
| chunking_configurations | id INTEGER GENERATED ALWAYS AS IDENTITY; strategy TEXT; algorithm_version TEXT; tokenizer_name TEXT; tokenizer_revision TEXT; tokenizer_artifact_sha256 BYTEA; tokenizer_runtime_version TEXT; max_tokens INTEGER; overlap INTEGER | PK id |
| embedding_configurations | id INTEGER GENERATED ALWAYS AS IDENTITY; provider TEXT; model_id TEXT; model_revision TEXT NULL; dimensions INTEGER; document_format TEXT; normalization TEXT; created_at TIMESTAMPTZ | PK id |
| version_chunk_plans | document_version_id UUID; chunking_configuration_id INTEGER; expected_chunk_count INTEGER; created_at TIMESTAMPTZ | PK (document_version_id, chunking_configuration_id) |
| chunk_memberships | document_version_id UUID; chunking_configuration_id INTEGER; ordinal INTEGER; start INTEGER; end INTEGER | PK (document_version_id, chunking_configuration_id, ordinal); UNIQUE (document_version_id, chunking_configuration_id, start, end) |
| chunk_embeddings | embedding_configuration_id INTEGER; content_sha256 BYTEA; start INTEGER; end INTEGER; embedding VECTOR(768); created_at TIMESTAMPTZ | PK (embedding_configuration_id, content_sha256, start, end) |

Configuration IDs are infrastructure references, not domain chunk identities.
Definitions are immutable by the future persistence contract; changing any
output-affecting setting requires a new row. Equivalent duplicate configuration
definitions are not prohibited: they reduce reuse but cannot enable incorrect
reuse. No configuration-fingerprint registry is needed. No configuration rows
are seeded by the migration. Timestamps are supplied explicitly by callers.

Chunking configurations enforce supported strategies, nonblank algorithm and
tokenizer identifiers, a 32-byte tokenizer digest, positive token budgets,
`0 <= overlap < max_tokens`, and zero overlap outside fixed-token chunking.
Boundary/fallback rules remain encoded by algorithm_version, matching the
[existing chunking contract](deterministic-chunking.md).

Embedding configurations enforce nonblank provider/model identifiers and an
absent or nonblank model revision, dimensions 768, document_format
`gemini_no_title_v1`, and normalization `none` (no client-side normalization).
Provider/model strings are not a production-provider selection. Another supported
768-dimensional profile can receive a new configuration; another format or
dimension requires a separately approved extension to the current constraints.

Plans reference DocumentVersion and chunking configuration and enforce a
nonnegative expected count. Memberships reference the complete composite plan
key, enforce nonnegative zero-based ordinals and `0 <= start < end`, and prohibit
repeated ranges within a plan. Their Content is derived solely through:

```text
membership -> plan -> DocumentVersion -> Content
```

This removes a duplicated Content identifier and the need for the earlier
proposed composite version/Content foreign key. The trade-off is a join when
resolving range memberships. Embeddings reference Content and encoding
configuration, enforce valid nonempty ranges, and reject zero vectors through
`vector_norm(embedding) > 0`. VECTOR(768) enforces dimensions and pgvector rejects
non-finite components. Stored vectors may be shared across versions and chunking
strategies only when their range and encoding configuration match.

Only five primary-key indexes and the membership range UNIQUE index are added.
The existing document_versions Content index remains available. No extra FK,
GIN, full-text, HNSW or IVFFlat indexes, triggers or cascading deletion are added.

## Encoding baseline

The approved experimental profile is Gemini Developer API, gemini-embedding-2,
768 dimensions and cosine distance, with exact search initially. Production
provider, quotas, deployment suitability and final evaluation remain unresolved.

Document input (`gemini_no_title_v1`):

```text
title: none | text: {exact_chunk_text}
```

Query input:

```text
task: search result | query: {question}
```

These are [Google's documented retrieval formats](https://ai.google.dev/gemini-api/docs/embeddings).
Formatting preserves original chunk strings and never modifies canonical Content.
The prior pilot compared this envelope with title-aware input; it did not test
literal body-only input or prove that the envelope is better. Title-aware
persistence is deferred and has no placeholder columns. Query formatting and
cosine selection are not duplicated in document-vector configuration rows.

The maintained [pgvector Python integration](https://github.com/pgvector/pgvector-python)
supplies the SQLAlchemy VECTOR type; pgvector 0.5.1 is locked and verified under
Python 3.14.9. E21 introduced no provider SDK or custom vector adapter; E23's
generation SDK is recorded separately below.

## Required future boundary checks

Ordinary row checks cannot verify the following across rows/tables. At E21 these
were future persistence responsibilities. E25 implements the chunk-plan storage
checks described below; embedding checks and exact tokenizer-specific generation
remain distinct responsibilities:

- Each chunk's exact Content belongs to the selected DocumentVersion.
- Character offsets are half-open Python-character coordinates, with
  `end <= len(content.text)`; PostgreSQL substring uses one-based starts.
- Membership count matches expected_chunk_count, ordinals are contiguous, and
  ordering/coverage/overlap match the selected strategy.
- Plans and configurations are immutable; an empty committed plan is distinct
  from missing data and establishes no indexing readiness.
- Provider response count, dimensions, finite/nonzero values and complete
  effective-input limits are validated; existing vectors are not silently replaced.

Future retrieval must join ranges to eligible memberships, versions and documents,
filtering ownership, soft deletion, active version and serving configuration
before top-K. Shared embeddings convey neither access rights nor a single source.
Citations resolve through that version's provenance and source-span intersections.
Readiness/activation, hybrid-search completion, work claiming, retry/recovery,
reindexing and deletion/activation races remain U14 decisions. No derived row
implicitly activates a version. version_index_states is deliberately deferred.

## Migration and verification

[0002_embedding_storage](../../migrations/versions/0002_embedding_storage.py)
follows the unchanged 0001_document_registration. It checks that pgvector and
vector_norm/vector_dims are installed and visible before creating derived tables.
It never installs extensions. Downgrade drops only the five derived tables,
retaining authoritative rows and the vector extension. Offline SQL includes the
same preflight. PostgreSQL 15+ remains required by 0001.

Executed temporary verification on **2026-10-27**, with Python 3.14.9,
**isolated PostgreSQL 17.2 and pgvector 0.8.5**, passed:

- Initial migration, absent-extension rejection with no partial derived schema,
  upgrade, downgrade to 0001 and re-upgrade.
- ORM/schema comparison, configuration identities and relationships, required
  PK/FK/UNIQUE/CHECK enforcement, all eight table constraint catalogs, exactly six
  derived indexes, invalid configuration/range rejection, dimension
  mismatch, non-finite values, zero-vector rejection, and cosine self-distance.
- A shared Content range with two independent version/source associations,
  preserving the exact Unicode slice `A😀`.
- Authoritative data and extension retention during downgrade, and full-chain
  offline SQL generation.
- Unchanged TXT extraction plus ten passages from an actual pinned 2Wiki record,
  retry/conflict and shared-Content behavior, and all three chunkers: 33 plans
  each through in-memory and isolated PostgreSQL registration, with exact slice
  coverage and token-budget checks. Existing source outside ORM mappings and
  the initial migration are unchanged from HEAD; offline uv lock validation passed.

The existing local compiler built pgvector in a temporary portable PostgreSQL
tree; the installed PostgreSQL and cloud instance were not modified. Docker was
unavailable and low disk space required a reduced temporary runtime. Raw temporary
checks are not permanent test coverage. A10 applied at that milestone; E22 later
ended the deferral and added the permanent suite.

Cloud SQL's previously verified PostgreSQL 18.6/pgvector 0.8.5 still needs the full
0001 -> 0002 chain. No cloud inspection or migration was performed in this task.
The migration is prepared for a separately authorized deployment after refreshing
the actual migration state and reviewing the target and operational safeguards.

## Task 15 batching update (2026-10-28)

The adapter now accepts separate `Content` objects in one synchronous
`batchEmbedContents` call. It pre-counts each formatted input, verifies the
8,192-token per-input bound, and packs inputs under configurable batch-count and
aggregate-token budgets (defaults 25 and 32,768). Returned vector count,
dimensions, finite values and order are validated before the application sees
the tuple. Existing task-specific formatting, 768-dimensional configuration
identity, and individual query behavior are unchanged. IndexDocumentVersion
gathers only missing compatible chunk vectors, requests bounded groups, and
commits each returned vector independently; complete coverage remains required
before activation. The database is not held during provider requests.

Installed-SDK mock-transport verification confirms multiple `Content` entries
serialize to separate requests in `batchEmbedContents`. The official embedding
guide likewise states separate `Content` inputs produce separate vectors; it
publishes an 8,192-token per-input limit, but no synchronous maximum input count
or aggregate batch-token limit. The selected application batch defaults are
therefore conservative configuration values, not measured provider limits.
The authorized live 1/2/5/10/25 passage experiment and evaluation indexing have
not run in this workspace; see the [Task 15 status](../validation/batched-gemini-evaluation-2026-10-28.md).

## Generation adapter (E23)

[E23](register.md#e23) authorizes generation through
[EmbeddingProvider](../../src/investigation_engine/application/ports/embedding_provider.py),
with `embed_documents(Sequence[str]) -> tuple[EmbeddingVector, ...]` and
`embed_query(str) -> EmbeddingVector`. A vector is `tuple[float, ...]`.
The port has no SDK, storage, chunk or provider-profile wrapper types. The caller
retains chunk identities and input order. No persistence/reuse workflow is added.

The [Gemini adapter](../../src/investigation_engine/infrastructure/embeddings/gemini_provider.py)
locks google-genai 2.33.0. The [maintained package](https://pypi.org/project/google-genai/)
declares Python 3.14 support; installation, imports and offline SDK transport tests
were verified with Python 3.14.9. HTTPX is explicitly declared for SDK transport
exception handling; all production requests still use the official SDK.
Pydantic is a transitive SDK dependency, not a
domain or settings framework. Settings retain the existing optional GEMINI_API_KEY;
the adapter receives it explicitly and does not import settings or load .env.

The formats above are explicit constants: DOCUMENT_FORMAT_VERSION is
gemini_no_title_v1 and QUERY_FORMAT_VERSION is gemini_search_query_v1.
Each input uses a single Content with one text Part and a separate awaited SDK
embedding operation. The installed SDK serializes this as batchEmbedContents
with exactly one request entry. It creates no asynchronous batch jobs. This avoids
the [documented aggregation behavior](https://ai.google.dev/gemini-api/docs/embeddings#embedding-aggregation)
of multiple flat inputs. Input order is preserved without a concurrency scheduler.

Non-string, blank and invalid UTF-8 inputs are rejected; nonblank strings retain
every original character. All local document validation precedes network IO.
Before each embedding, the same complete formatted Content is passed to
count_tokens using model gemini-embedding-2. Require a positive integer count no
greater than the [documented 8192-token limit](https://ai.google.dev/gemini-api/docs/models/gemini-embedding-2).
The [countTokens API](https://ai.google.dev/api/tokens) is model-specific, but its
reference does not explicitly guarantee this model's availability or exact
correspondence with embedContent framing. No live call is authorized in this
milestone, so those properties remain unverified. If counting is unavailable or
returns invalid data, embedding is refused. There is no Voyage/local-token
substitute or automatic fallback. An unsupported endpoint requires provider
clarification or a separately reviewed input policy, not a bypass of this guard.

The SDK rejects auto_truncate=False for the Developer API. It is therefore not
sent. Successful preflight counting guards the complete input, and responses
explicitly reporting truncated=True are rejected. This does not establish live
provider compliance when truncation statistics are absent. No text splitting,
truncation, title augmentation or client normalization occurs.

Require exactly one embedding, exactly 768 numeric values, finite components
and a finite nonzero norm. Return immutable float tuples with provider values
preserved. Earlier remote calls can complete before a later input fails; the
method exposes no partial output. Future persistence must still validate storage
conversion and consistency before committing.

Four application exceptions distinguish input errors, rate/quota limits,
provider/transport failures and invalid responses. Raw SDK exceptions can include
request data and response echoes; raised diagnostic causes instead retain only
the operation, HTTP status or failure class. Cancellation propagates unchanged.
The client explicitly selects the Developer API endpoint with a 60-second request
timeout and one SDK attempt. Use async with or await aclose(); both async and sync
SDK clients are closed. No retry/fallback framework or application-global client
lifecycle is introduced.

All 48 feature tests passed; the full unit suite passed with 137 successes and
the same two known registration xfails on Python 3.14.9. Offline tests cover
formatting, ordering, complete-input limits, malformed and
invalid vectors, errors/redaction, actual task cancellation and resource cleanup.
A real SDK test uses in-memory HTTP transport to verify serialized requests,
without sockets or credentials. The live smoke test under tests/external/embeddings
requires --run-external and was not executed. It checks synthetic document/query
responses, authentication by successful requests and 768-dimensional finite,
nonzero vectors; it does not establish retrieval quality or semantic compatibility.

Live counting, authentication, model access, quotas, latency and response behavior
remain unverified. U14 lifecycle, persistent plans/embeddings, configuration-row
selection, float32 storage validation and reuse/transaction policy remain separate
work. No database tests, mappings, migrations, cloud resources or registration
validation behavior are changed by E23.

## Chunk-plan persistence (E25)

[E25](register.md#e25) adds a focused async
[ChunkPlanRepository](../../src/investigation_engine/application/ports/chunk_plan_repository.py):
save(plan) returns the complete stored ChunkPlan; get(document_version_id, config)
returns that plan or None. Infrastructure configuration IDs do not cross the port.
The [PostgreSQL adapter](../../src/investigation_engine/infrastructure/persistence/postgresql_chunk_plan_repository.py)
uses only chunking_configurations, version_chunk_plans and chunk_memberships,
anchored through the existing DocumentVersion/Content relationship. No schema,
migration, dependency, embedding, activation or registration changes are required.

Each operation owns a session and READ COMMITTED transaction from the injected
factory. Save locks the existing DocumentVersion row with SELECT FOR UPDATE
before configuration/plan lookup and any writes. Competing saves for that version
serialize, including different configurations. This deliberately trades some
per-version parallelism for a small, clear correctness boundary. No distributed
lock, advisory-lock registry, global lock or automatic transaction replay exists.
The lock is scoped to this operation and selects no future U14 worker protocol.

Configuration identity compares every strategy, algorithm, tokenizer artifact/
revision/runtime, budget and overlap field exactly. Equivalent existing rows are
reused, preferring the lowest ID when a new plan needs a row. A previously stored
plan is found through all equivalent configuration rows before creating anything.
Equivalent configurations may still be created concurrently for different
versions, as permitted by E21; they cannot merge memberships or change correctness.
The same-version guarantee requires writes through this repository. Ambiguous
multiple stored plans for one logical version/configuration are rejected as
RepositoryError rather than repaired or selected silently.

Save validates against Content loaded from the authoritative version, never
from a caller-supplied Content ID alone. The pure
[application validator](../../src/investigation_engine/application/chunk_plan_validation.py)
checks exact Content equality, valid half-open Python-character ranges, strictly
increasing starts, no gaps, no unconfigured overlap and complete character
coverage. Overlap is allowed for fixed-token profiles with positive overlap;
it is not assumed to be a fixed number of characters. Endpoints may overlap.
Token budgets and exact strategy/sentence boundaries remain the deterministic
generator's responsibility: storage does not retokenize or download an artifact.

Configuration insertion, the expected-count manifest and ordinal/range rows all
commit together. A failed attempt rolls back new configuration rows too.
Identical retries return the stored plan; different ranges raise
ChunkPlanConflict without overwriting. SQLAlchemy failures use RepositoryError
with uncertain-commit semantics; retry the same version/configuration. Cancellation
propagates through normal session/transaction cleanup.

Reads explicitly order memberships by ordinal, require the exact manifest count
and contiguous ordinals starting at zero, reconstruct Chunk/ChunkingConfig values,
and revalidate ranges/coverage against version Content. Corrupt/incomplete data
raises RepositoryError, including on save retries; it is never returned as a
complete plan or automatically repaired. The repository is an internal trusted
boundary, not a public source-authorization or retrieval API. The version ID
retains the path to provenance and future ownership/eligibility checks.

### Whitespace and completeness

The existing chunker preserves every canonical character, including whitespace.
E25 preserves that contract and stores all memberships. expected_chunk_count is
always the total generated membership count. Empty Content has an explicit
zero-member manifest; a missing manifest still returns None. Whitespace-only
Content has real ranges and must not be represented by an empty plan.

The initial Gemini adapter rejects blank text. Its potential nonblank embedding
inputs are therefore a subset of complete plan memberships, and still need
provider input-limit validation. No filtering, eligibility flag, vector count or
readiness rule is implemented here. Before embedding persistence, approve how
blank ranges are excluded from generation and how eligible embedding coverage is
assessed without redefining the complete plan or mutating offsets. Plan presence
alone never activates a version or proves indexing readiness.

### E25 verification and limits

On 2026-10-27, **20 new pure validation cases passed; all 161 unit tests passed**.
Actual configuration/plan lookup SQL compiles for PostgreSQL. The 18 new real-DB
tests collect successfully and cover committed reconstruction, idempotency,
conflicts, configuration distinctions, Unicode/overlap, source-specific layouts,
invalid input, empty/blank plans, rollback after real membership INSERTs, corrupt
stored plans and three coordinated independent-session concurrency scenarios.

Those 18 database tests were **not executed**: read-only Docker inspection found
the pinned pgvector/PostgreSQL image still absent. No image download, Cloud SQL
connection or alternate database was attempted. SQL compilation and unit tests
do not verify persistence, transactions, lock behavior or real query results.
The existing schema/model/migration files and dependency versions are unchanged.

Remaining decisions concern embedding eligibility/persistence/reuse, durable
orchestration, activation/readiness, lifecycle races and public authorization.
Production sizing, timeouts, configuration lookup performance and non-cooperating
direct database writers are not covered by this milestone's guarantees.

## Resumable indexing and activation (E26)

[E26](register.md#e26) implements the existing-schema foreground workflow; the
historical E25 unavailable-image note above no longer describes current execution.
The cached images are available and all existing/new database tests run against
fresh disposable containers. No schema, migration or dependency changes are made.

### Interfaces and effective identity

The frozen application EmbeddingConfig mirrors existing configuration columns:
provider, model_id, optional model_revision, dimensions, document_format and
normalization. Current schema support is 768 dimensions, gemini_no_title_v1 and
no client normalization. Other providers may implement that profile; a new input
format/dimension requires separately reviewed schema support. The provider port
now declares its complete configuration. IndexDocumentVersion rejects mismatches
before work and checks again around generation to prevent mislabeling vectors.
The Gemini adapter declares its fixed initial settings without exposing its key.

Effective document input is the exact canonical range wrapped in that versioned
document format. Identity is Content SHA-256, start, end and all configuration
fields, not vector equality, titles, source identity or chunking strategy.
Absent model revisions match only absent revisions. A mutable upstream alias
with unknown revision is not proof of immutable model weights. No upstream version
discovery or silent alias-change detection is implemented; production pinning or
explicit profile rotation remains an operational decision.

EmbeddingRepository.find(chunks, config) returns committed immutable vectors
keyed by Chunk.identity. save(chunk, config, vector) owns a short transaction,
validates the exact registered Content and float32 representation, then inserts
with ON CONFLICT DO NOTHING on the existing composite PK. A compatible committed
winner is reused without overwriting, even if another valid proposed vector
differs. Rounding to float32 is storage conversion, not L2 normalization. Reject
wrong dimensions, nonnumeric/nonfinite values, float32 overflow and all-zero
vectors after underflow. No canonical text is copied into embedding rows.

Equivalent configuration rows were allowed by E21. Reads consider all matching
rows, so legacy aliases can reuse existing vectors. When no definition exists,
creation takes a brief SHARE ROW EXCLUSIVE lock on embedding_configurations,
rechecks and inserts in the same transaction. Existing definitions need no table
lock. This avoids a new natural-key constraint/fingerprint registry and prevents
cooperating creators from multiplying profile rows. Configuration creation and
vector insertion roll back together. Direct writers that bypass these contracts
are not granted global logical-uniqueness guarantees.

### Eligibility, progress and retry boundaries

One application function defines eligibility: bool(chunk.text.strip()). It never
modifies the submitted text. Plans retain every membership; expected_chunk_count
continues to count all ranges. Blank ranges need no vectors. Empty/all-blank plans
return the explicit no_searchable_content outcome and cannot activate a version.
Provider token limits remain an additional requirement for nonblank candidates.

IndexDocumentVersion snapshots current_version_id, obtains the complete persisted
plan, checks compatible vectors and generates one missing eligible chunk per
provider call. Every successful result commits immediately. Final verification
and activation require every eligible identity. Repeated requests/new repository
instances query persisted progress; no job/lifecycle table or ephemeral cache is
needed to resume when the caller supplies the same target/configurations.

All repository sessions/transactions finish before provider IO or retry delays.
Default request limits are three attempts, one-second fixed delay and a 60-second
whole-provider-call timeout, configurable through constructor arguments. Retry
only rate/quota failures, explicitly retryable transport/server errors and request
timeouts. The adapter marks authentication/not-found failures permanent; input
and invalid-response errors are never retried. SDK attempts remain one. Failure
or cancellation leaves earlier committed vectors available and does not activate
the target. Database errors are not replayed automatically; after an uncertain
commit, retry the same immutable operation identity.

Concurrent callers can both infer a missing vector before either commits. Avoiding
that duplication would require a separate claim/lease design; E26 guarantees
idempotent storage, not exactly-once provider calls or distributed rate limits.

### Transactional activation

DocumentActivationRepository.current_version verifies document/version ownership
and non-deletion, returning an explicit nullable expected-current snapshot.
activate receives both selected configurations and that expected pointer. In one
READ COMMITTED transaction it locks the Document then target DocumentVersion,
reconstructs/validates the selected plan and checks the compatible stored vectors
for exactly the eligible ranges. Missing/corrupt plans, empty eligible sets and
incomplete/incompatible coverage cannot change the pointer.

An already-current target is idempotent only after completeness and deletion
checks. Otherwise current_version_id must still equal the captured value, and
the target version number must exceed any current version number. Thus a stale
indexer cannot replace a version activated while it was generating, nor downgrade
to an older version after taking a fresh snapshot. The pointer changes only at
commit. A failed replacement leaves the previous active version intact.

Soft-deletion UPDATEs serialize on the same Document row. If deletion wins,
activation refuses; if activation wins, subsequent deletion retains the pointer
but makes the document ineligible under the existing deletion policy. This adds
no deletion/restoration or cross-revision registration workflow. Tests seed later
versions directly to exercise invariants; production registration remains create-only.

The pointer records a version, not the serving profile. Activation proves selected
configuration coverage at that transaction; it does not claim every possible
profile is ready. Future retrieval must explicitly select profile/memberships
and enforce ownership, deleted_at and current_version_id eligibility. These are
trusted internal ports, not public authorization. Immutability depends on approved
writers: no vector/configuration/plan mutation or deletion API is introduced.

### E26 verification and remaining work

Complete execution: **196 unit tests and 101 PostgreSQL integration tests passed**
(297 total), with one existing upstream SDK typing deprecation warning. The
PostgreSQL fixture verifies major 18 and pgvector 0.8.5 and applies actual migrations
to a fresh disposable database. Synthetic provider outputs are deterministic;
no live Gemini, Cloud SQL or other production database is accessed.

New tests cover float32 persistence/readback, legacy equivalent configurations,
nullable revision/provider/model distinctions, concurrent first-writer storage,
rollback after actual embedding INSERT, exact-text checks, resumed partial work,
actual task cancellation, no checked-out connection during inference, all-blank
content, incompatible/incomplete activation, prior-pointer retention, stale/older
versions, two racing indexers, concurrent activations and both deletion orderings.
Race tests use independent backend sessions, events and observed PostgreSQL lock
waits; they do not rely on arbitrary sleep durations.

Remaining work includes authorized live-provider verification, production model
pinning, serving-profile/retrieval selection, public authorization, document-update
registration, background scheduling/claims, distributed quotas, operational database
timeouts and load testing. Physical connection loss at COMMIT is not simulated.
No semantic/hybrid retrieval, RAG, endpoints or deployment is implemented here.

### E26 file map

New application files:

- application/embeddings.py (profile, vector representation and eligibility).
- application/ports/embedding_repository.py and document_activation_repository.py.
- application/use_cases/index_document_version.py.

New infrastructure files:

- infrastructure/persistence/_embedding_mapping.py.
- infrastructure/persistence/postgresql_embedding_repository.py.
- infrastructure/persistence/postgresql_document_activation_repository.py.

Updated source files are application/ports/embedding_provider.py,
embedding_errors.py and repository_errors.py, plus the Gemini adapter. New tests
are unit/application/test_embedding_storage_values.py,
unit/application/test_index_document_version.py and
integration/infrastructure/persistence/test_embedding_indexing.py. The shared
synthetic fixture and existing Gemini unit tests are extended.

README, documentation index, decision register, this ADR, testing strategy,
system overview and data contract reflect E26. Domain entities, existing
registration/chunk-plan adapters, ORM mappings, migrations, configuration files,
dependencies and CI configuration are unchanged by this task.

## Exact semantic retrieval (E27)

[E27](register.md#e27) authorizes the initial exact semantic workflow using the
existing eight tables, without migrations, new dependencies or indexing changes.
Hybrid retrieval, RRF, answers, endpoints, authentication and workers are excluded.

### Application boundary

SearchEvidence(repository, provider, chunking, embedding) selects one explicit
serving pair. Its async execute(question, limit=10, *, owner_id) validates nonblank
UTF-8 text, strict integer limits 1-100 and explicit trusted visibility before IO.
None permits shared documents only; a principal permits shared plus exactly that
principal's private documents. Source metadata never supplies authority.

The existing provider's embed_query handles query formatting, preserving Gemini's
`task: search result | query: {question}` envelope. Provider configuration must
match before and after inference; returned vectors must fit finite nonzero
768-dimensional float32 storage. No document embeddings are regenerated during
search and no DB connection is held during query embedding. No application-side
retry/fallback mechanism is added. Provider errors propagate; database failures
become RepositoryError through the existing error boundary.

EvidenceRepository.search(vector, chunking, embedding, *, owner_id, limit) returns
a bounded tuple of application Evidence values, never ORM rows. The PostgreSQL
adapter owns one short READ COMMITTED transaction and one SELECT statement;
all eligibility uses the same statement snapshot. No per-hit database fetches or
Python vector scans occur.

### SQL eligibility before top-K

The query uses four named CTEs and the existing primary/foreign-key indexes:

1. compatible_embedding_keys matches all provider/model/revision/dimension/format/
   normalization fields, including absent revisions. Equivalent legacy profile
   rows resolve per range to the lowest stored configuration ID, matching cache
   reads; vectors are not duplicated or loaded into application memory.
2. visible_serving_plans joins nondeleted visible Documents to their owned active
   DocumentVersion, exact selected chunking configuration, plan and canonical
   Content. Multiple equivalent plans for one version are ambiguous and excluded.
3. serving_memberships joins every expected version/configuration membership,
   derives its vector key through that version's Content, and computes coverage
   geometry and nonblank eligibility. The explicit SQL whitespace set equals
   Python str.strip(), including NBSP, NEL and information separators; text is
   never normalized. Whitespace memberships remain part of plan completeness.
4. complete_serving_plans requires the exact expected count, contiguous ordinals,
   ordered valid in-Content bounds, full character coverage, overlap only when
   configured, at least one nonblank range and vectors for every nonblank range.

Only eligible memberships of complete selected plans join vectors for exact
`<=>` cosine ordering. Tie keys are document UUID, version UUID, start and end;
LIMIT applies after every visibility/lifecycle/configuration/completeness filter.
Only bounded hits with exact SQL substrings and version provenance return to
Python. Empty, wholly blank, inactive, deleted, superseded and partially indexed
selected configurations yield no evidence. A corrupt manifest cannot leak its
present nearest vector while another required vector is missing.

### Evidence and citation semantics

Result identity is (document_version_id, start, end). Each hit includes document
and version UUIDs, Content SHA-256, exact half-open canonical character range,
exact text, SourceReference, original intersecting SourceSpans, optional title,
cosine similarity (1 minus distance) and one-based rank. No similarity threshold
or score calibration is selected; floating-point rounding is not clamped.

Independent source occurrences remain independent hits even when Content and
vectors are shared. Overlapping ranges remain distinct. Source dictionaries are
defensively copied; mutable caller changes do not alter persisted provenance.
Spans retain original bounds, order, locator kinds and zero-based indices. For a
partial span, the retrieved portion is [max(hit.start, span.start),
min(hit.end, span.end)); the complete native sentence/page is not implied.
Boundary-touching spans are excluded. Missing locations remain an empty tuple;
no URL, page or line is invented. Invalid stored provenance fails the operation
with RepositoryError rather than silently dropping hits after top-K. Rendered
citations and access to historical/original source bytes remain later work.

### Serving-profile limitation

current_version_id still identifies an active version without publishing a
serving profile. Retrieval recomputes selected-plan/vector completeness on every
statement. A version active under another profile is visible only if the selected
profile is independently complete; partial alternatives are excluded. This does
not imply that the selected profile was the one used for activation, establish
atomic corpus-wide profile publication, pin an unversioned upstream model alias,
or promise an authorization snapshot across later requests. Future publication
and background lifecycle policy remain separate decisions. No schema correction
was necessary for this initial contract.

### E27 verification and files

Executed on Python 3.14.9 with synthetic providers and the cached disposable
PostgreSQL 18/pgvector 0.8.5 image, migrated using the actual Alembic revisions:

- **225 unit + 123 integration = 348 passing tests** (51 new cases).
- Statement coverage **1,294/1,362 = 95.01%**; branch coverage
  **355/404 = 87.87%**; combined coverage.py display **93.37%**.
- Known cosine ordering, differing magnitudes, deterministic ties, limits,
  incompatible profiles and dimensions, independent shared-vector attribution,
  canonical Unicode ranges and multiple partial source spans are covered.
- Shared/private owner isolation, untrusted metadata, deletion, inactive and
  superseded versions, missing/partial/ambiguous profiles, corrupt membership
  manifests, empty/Unicode-whitespace states and configured overlaps are covered.
- Real end-to-end registration, plan persistence, indexing, activation and query
  search pass; a newly constructed repository/use case reads persisted evidence.
  Connection instrumentation verifies no checkout during query inference.
- A non-coverage timing run over **1,001 active documents/distinct vectors**,
  one membership per document, limit 10 and refreshed disposable fixture statistics
  measured **81.32 ms median across five consecutive application calls**;
  EXPLAIN ANALYZE measured **25.32 ms execution / 8.33 ms planning**. Each search
  issued one statement and returned ten rows. Before fixture ANALYZE, stale/absent
  estimates gave substantially worse plans. This is a synthetic local measurement,
  not a 50,000-record benchmark, concurrency/load test or latency commitment.

The sole warning is the existing upstream Google SDK typing deprecation. No
Gemini/external-service or Cloud SQL calls occurred. No schema, dependency,
configuration, registration, chunk-plan, embedding-store or activation code changed.

New files:

- application/retrieval.py: Evidence and shared validation/intersection rules.
- application/ports/evidence_repository.py: exact scoped search contract.
- application/use_cases/search_evidence.py: provider-independent orchestration.
- infrastructure/persistence/postgresql_evidence_repository.py: SQL and mapping.
- tests/unit/application/test_search_evidence.py: 29 unit cases.
- tests/integration/infrastructure/persistence/test_semantic_retrieval.py: 22 cases.

README, documentation index, register, this ADR, testing strategy, system overview
and data contract record E27. Corpus-scale cost/quality, model drift, live provider
behavior, public authentication, citation rendering, keyword/hybrid retrieval and
profile publication remain unverified or unimplemented.
