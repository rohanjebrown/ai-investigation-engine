# AI Investigation Engine

Production container and CI/CD instructions: [Cloud Run deployment guide](docs/design/cloud-run-deployment.md).

A Python foundation for investigating questions across documents, developed for
the **Upgaming Applied AI Engineer practical assignment**. The intended system
will combine retrieval and multi-step reasoning with traceable evidence.

## Current implementation

- Database-independent document, immutable version, Content, and provenance models.
- TXT, PDF, DOCX and 2WikiMultihopQA extraction with original character offsets and locators.
- Exact UTF-8 SHA-256 Content reuse while preserving independent source identities.
- Async registration through in-memory and PostgreSQL repositories, including
  same-source retries, conflict detection, and trusted ownership assignment.
- SQLAlchemy ORM mappings and Alembic migrations for the authoritative schema
  and five-table chunk-plan/embedding storage foundation.
- Whole-passage, fixed-token, and sentence-aware chunking over exact Content ranges.
- PostgreSQL chunk-plan storage with immutable ordered memberships, atomic saves
  and idempotent lookup.
- An explicitly loaded, checksum-verified experimental tokenizer adapter.
- Application-level document version updates, owner-scoped listing/details, and
  transactional soft deletion, exposed through the authenticated administrator API.
- An async embedding port and Gemini Developer API adapter with input/response
  validation and versioned document/query formatting.
- Resumable embedding persistence, shared float32 vectors, and transactional
  activation after complete nonblank-chunk coverage with stale/deleted-state checks.
- Exact pgvector semantic retrieval with selected-profile completeness, trusted
  ownership scope, deterministic ranking and canonical source citations.
- Single-step grounded answer generation with a configurable Gemini adapter,
  validated evidence references, explicit abstention and FastAPI question API.
- Optional one-administrator document management for private uploads, bounded
  resumable indexing, replacement versions, listing/details and soft deletion.
- A same-origin HTML/CSS/vanilla-JavaScript question page with expandable source
  excerpts, accessible submission states and safe plain-text rendering.

PostgreSQL with pgvector, native full-text search, and application-owned RRF are
selected technologies. **Full account authentication, background indexing,
multi-step investigation, and deployment are not implemented.**
The approved derived schema is implemented in migration 0002; it has
not been applied to Cloud SQL. See the [storage ADR](docs/decisions/embedding-storage.md).

## Setup

Use Python **3.14** (initially 3.14.9) and [uv](https://docs.astral.sh/uv/).
From the repository root, in PowerShell:

```powershell
uv sync --locked
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Dependency installation requires access to the configured package sources unless
the packages are cached. Exact dependency versions are recorded in `uv.lock`.
The wheel contains the Python library; the source archive additionally includes
migrations, documentation, configuration examples and the small sample input.

## Configuration and PostgreSQL

Edit your ignored `.env` using `.env.example` as the variable-name reference.
`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD` are required when
importing settings. `GEMINI_API_KEY` is required only for explicit Gemini embedding
generation and remains optional for database-only work.

Run commands from the repository root. python-dotenv automatically loads `.env`
from the working directory without overriding process variables. Values are
literal (interpolation disabled), preserving credentials containing `${...}`.

```python
from investigation_engine.infrastructure.config import settings
```

This shared frozen dataclass is initialized on first import. Required DB variables
are validated immediately; DB_PORT is parsed as an integer in 1–65535. The optional
Gemini key defaults to None. No boolean settings are currently needed. Restart
after configuration changes. Secret fields are hidden from repr. URL.create builds
the database URL; imports open no connections. Domain components receive
dependencies rather than importing settings. Offline Alembic does not load settings.

The current connection helper supports the existing **Cloud SQL Auth Proxy at
127.0.0.1:15432**. Its local PostgreSQL connection uses `sslmode=disable`; the
proxy provides the encrypted remote tunnel. Start your already configured proxy
separately. There is no fallback to local PostgreSQL on port 5432 or to another
connection mode. Other deployment paths need a reviewed configuration.

The cloud instance was inspected read-only on 25 October 2026: PostgreSQL 18.6,
pgvector 0.8.5, and no application tables or Alembic revision table. The schema is
implemented in migration files, **not yet applied to that database**. Migration
execution is a separate operator action; the local smoke check below needs no DB.

## Local verification

Install locked development dependencies with `uv sync --locked --group dev`.
The default command runs deterministic unit tests with service connections blocked.
Integration runs require working Docker and use only disposable Testcontainers
PostgreSQL/pgvector; they never load the application's database credentials.

```powershell
# Default safe suite / explicit unit selection
uv run --locked --no-env-file pytest
uv run --locked --no-env-file pytest tests/unit

# Real PostgreSQL/pgvector, commits, cleanup and migrations
uv run --locked --no-env-file pytest tests/integration

# Statement and branch coverage, with no global minimum
uv run --locked --no-env-file pytest tests/unit --cov --cov-report=term-missing --cov-report=html
uv run --locked --no-env-file pytest tests/unit tests/integration --cov --cov-report=term-missing

# Optional existing pinned corpus; no downloads
uv run --locked --no-env-file pytest -c pyproject.toml tests/external --run-dataset
# To include the known real-tokenizer layout check, append:
# --tokenizer-path=artifacts/tokenizers/voyage-4/tokenizer.json

# Explicit live Gemini smoke check (makes API calls; never part of normal CI)
uv run --locked --no-env-file pytest tests/external/embeddings --run-external
```

API-key presence never enables live tests. Dataset and live-service opt-ins are
independent. Missing optional files skip clearly; a wrong corpus digest fails.
Coverage HTML is generated in ignored `htmlcov/`. Migration/test code and standalone
inspection/download CLIs are excluded from application coverage.

The [testing ADR](docs/decisions/testing-strategy.md) records fixtures, safety,
current results, known gaps and CI. Both repositories require inactive version 1
for new registrations while resolving identical retries first. The former two
expected failures are now ordinary passing regression tests. GitHub Actions is
configured to run unit and Docker integration tests
without cloud/Gemini credentials. No formatter, linter or type checker is configured.
`main.py` remains the uv scaffold, not a web server. The separate >=40-question
evaluation design remains unresolved under U17.

## Dataset and tokenizer

The approved corpus is the complete pinned 2Wiki development split: 125,760
occurrences and 56,680 unique exact-text Contents. Exact uniqueness is a
conservative counting baseline, not proof of semantic independence. Questions,
answers, and gold evidence stay outside the retrieval corpus. See the
[dataset audit](docs/validation/2wiki-dev-audit.md).

Raw data is excluded from Git. An explicit download (not required by the smoke
check) is available through `uv run python -m investigation_engine.ingestion.download_dataset`.
It requires network access and uses the revision pinned in that module. The older
`audit_contexts` script compares sentence lists and exploratory normalized text;
it does not reproduce the approved exact extracted-text count.

Chunking requires a separately obtained reference tokenizer. To download it
explicitly, with network access:

```powershell
uv run python -c "from huggingface_hub import hf_hub_download; hf_hub_download('MongoDB/voyage-4', filename='tokenizer.json', revision='44f3b2ae4ddf33403ed4dd66bec3fa48ff7dbbf9', local_dir='artifacts/tokenizers/voyage-4')"
```

The adapter verifies SHA-256
`c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539`
before loading. It performs no implicit downloads. These artifacts are ignored.
This Voyage tokenizer defines experimental chunk boundaries; its token counts are
not Gemini token counts. See [chunking usage and verification](docs/decisions/deterministic-chunking.md).

## Chunk-plan storage

The focused application port exposes save(plan) and get(version_id, config).
Inject the existing async session factory into PostgreSQLChunkPlanRepository;
each call owns its session and READ COMMITTED transaction. No settings are loaded
by the repository itself.

```python
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import PostgreSQLChunkPlanRepository

# Inside an async application with an already registered version and generated plan:
plans = PostgreSQLChunkPlanRepository(sessions)
stored = await plans.save(plan)
loaded = await plans.get(plan.document_version_id, plan.config)
```

Configuration identity includes every chunking/tokenizer field. Saves lock the
DocumentVersion before lookup and writes, serialize competing saves for that
version, and reuse identical plans. Different ordered ranges raise
ChunkPlanConflict. Configuration, expected membership count and all ordered
ranges commit together. Reads reconstruct slices from the version's exact Content
and reject incomplete/corrupt stored plans. No chunk text or Content is copied
into membership rows, and saving does not activate the document.

Complete plans retain whitespace-only ranges. Empty Content has a committed
zero-member plan, distinct from a missing plan. The Gemini adapter rejects blank
text, so embedding candidates are a subset of memberships; plan persistence
does not filter them or change expected_chunk_count. Indexing and activation
use the same nonblank rule. Token budgets/exact algorithm
boundaries remain the generating chunker's responsibility; storage validates
Content ownership, ranges, ordering, overlap permission and complete coverage.
See the [storage boundary and verification](docs/decisions/embedding-storage.md#chunk-plan-persistence-e25).

## Gemini embedding generation

Document embeddings use bounded synchronous requests. The provider passes one
`google.genai.types.Content` per passage so Gemini Embedding 2 returns separate
vectors in input order. Inputs are counted individually, then packed to the
configured `GEMINI_EMBEDDING_BATCH_SIZE` (default 25) and
`GEMINI_EMBEDDING_BATCH_TOKEN_LIMIT` (default 32,768). Both settings are
configurable in `.env`; each input is still checked against the model's 8,192
token limit. These aggregate batch defaults are conservative application
budgets, not a claimed Google request maximum or quota accounting rule.

Pass the existing settings value into the infrastructure adapter. Importing the
adapter loads no .env and opens no database connection. The shared settings
module still requires the existing DB_* configuration values even for a
generation-only script; the adapter itself requires only its injected API key.

```python
from investigation_engine.infrastructure.config import settings
from investigation_engine.infrastructure.embeddings.gemini_provider import GeminiEmbeddingProvider

# Inside your async application, only when generation is explicitly wanted:
async with GeminiEmbeddingProvider(api_key=settings.gemini_api_key) as provider:
    vectors = await provider.embed_documents(tuple(chunk.text for chunk in plan.chunks))
    query_vector = await provider.embed_query("Which sources describe the event?")
```

The application port returns ordered tuples of immutable float tuples. Empty
sequences and blank texts are rejected; skip generation for an empty ChunkPlan.
Original nonblank input is preserved exactly. The initial profile uses
gemini-embedding-2, 768 dimensions, no client normalization and these versioned
formats:

- `gemini_no_title_v1`: `title: none | text: {exact_chunk_text}`
- `gemini_search_query_v1`: `task: search result | query: {query}`

Each text receives a model-specific countTokens request over its complete
formatted Content; multiple separate Content objects are embedded in one
synchronous SDK call. Inputs are packed under configurable count and aggregate
token limits. Counts above 8,192 are rejected. Counting failure prevents
embedding; no local Voyage-token estimate or fallback is used. E30 verified
model-specific countTokens and real embeddings for two source passages and one
query. The official guide confirms that separate Content objects yield separate
vectors. The chosen batch defaults are application safety bounds, not a verified
Google maximum. The SDK's Developer API does not support
auto_truncate=False; reported truncation is also rejected. See the
[generation boundary and verification limits](docs/decisions/embedding-storage.md#generation-adapter-e23).

The adapter validates response count, 768 finite numeric values and finite nonzero
norm, preserving provider values. It neither splits nor truncates text. Reuse the
client with async with or await aclose(), which closes both SDK transports. Calls
have a 60-second request timeout and one SDK attempt, with cancellation propagated.
The adapter itself adds no retries or fallback; the indexing use case below
provides bounded transient retries. Earlier remote calls may
complete before a later document fails; no partial vector tuple is returned.

Application-facing errors distinguish invalid input, rate/quota limits, provider
failures and invalid responses. Diagnostic causes retain only the operation,
HTTP status or failure type, excluding raw SDK payloads. The SDK uses Pydantic
internally; application settings and domain models remain standard dataclasses.

Task 15's measured batch behavior, dedicated persistent evaluation database
commands, indexed source counts, restart check, and paired retrieval results are
recorded in the [evaluation report](docs/validation/batched-gemini-evaluation-2026-10-28.md).

## Resumable indexing and activation

IndexDocumentVersion composes the existing chunk-plan port, EmbeddingRepository,
DocumentActivationRepository and EmbeddingProvider. The provider now declares an
immutable EmbeddingConfig containing its provider/model/revision, dimensions,
document format and normalization. A selected configuration must match it.

```python
from investigation_engine.application.use_cases.index_document_version import IndexDocumentVersion
from investigation_engine.infrastructure.persistence.postgresql_embedding_repository import PostgreSQLEmbeddingRepository
from investigation_engine.infrastructure.persistence.postgresql_document_activation_repository import PostgreSQLDocumentActivationRepository

# Inside an async application, with a persisted plan and an explicitly supplied provider:
workflow = IndexDocumentVersion(
    plans, PostgreSQLEmbeddingRepository(sessions),
    PostgreSQLDocumentActivationRepository(sessions), provider,
    max_attempts=3, retry_delay_seconds=1.0, request_timeout_seconds=60.0,
)
outcome = await workflow.execute(document_id, version_id, chunking_config, provider.configuration)
```

The use case returns activated, already_active or no_searchable_content. Empty or
whitespace-only documents generate nothing and cannot be activated. Missing
nonblank chunks are submitted in bounded provider batches and each returned
vector commits independently, so a later request resumes after failure or
cancellation. All database
transactions close before provider calls or retry delays. Existing compatible
vectors win without overwrites; identity is exact Content/range plus every
embedding configuration field. Float32 rounding is validated without L2 normalization.

Only rate limits, explicitly transient provider failures and request timeouts are
retried, up to the configured attempt count. Permanent input, authentication and
response errors propagate. Database failures are not automatically replayed;
after an uncertain commit, rerun with the same version/configuration.

Activation locks the document/version, reconstructs the selected complete plan,
checks every eligible vector under the selected profile, rejects deletion and
changed current-version snapshots, and prohibits downgrades by version number.
The active pointer changes only after these checks in one transaction. Previously
active versions survive failed replacement indexing. Version-update registration,
background jobs and public authorization are not implemented.

Complete unit and PostgreSQL 18/pgvector 0.8.5 suites pass using synthetic
providers: **196 unit + 101 integration = 297 tests**. No live Gemini or Cloud SQL
calls are part of that verification. Concurrent workers can duplicate inference
before either commits; storage remains idempotent. Unversioned upstream model
aliases can change silently, and the active pointer does not record a serving
profile. See [E26 guarantees and limits](docs/decisions/embedding-storage.md#resumable-indexing-and-activation-e26).

## Semantic evidence retrieval

`SearchEvidence(repository, provider, chunking, embedding)` selects one explicit
serving pair. The caller supplies a trusted principal on every query:

```python
hits = await search.execute("What happened?", limit=10, owner_id=trusted_principal)
# owner_id=None explicitly restricts this internal operation to shared documents.
```

The provider's `embed_query` retains the query envelope; it never uses document
formatting. `PostgreSQLEvidenceRepository` uses one SQL statement and exact cosine
distance, filtering ownership, deletion, current version, configuration and full
eligible embedding coverage **before LIMIT**. Limits are strict integers 1-100.
Scores are cosine similarities; ties use document/version/range order.

Each hit identifies `(document_version_id, start, end)` and includes document ID,
Content digest, exact text, SourceReference, original intersecting SourceSpans,
title, score and rank. Identical vectors can yield separate source occurrences.
Spans retain their original bounds and locators, including partial intersections;
missing locations remain empty. There is no rendered citation UI or authentication.

The active pointer still does not record a serving profile. Retrieval therefore
rechecks complete selected-plan/vector coverage at each statement snapshot.
It exposes no partial alternative profile and makes no profile-publication or
cross-request snapshot guarantee. No migration or indexing changes were needed.

**Verification:** 225 unit + 123 PostgreSQL integration = **348 passing tests**,
using synthetic providers and disposable PostgreSQL 18/pgvector 0.8.5. A 1,001-vector
synthetic check with refreshed fixture statistics measured 81.32 ms median across
five application calls and 25.32 ms EXPLAIN execution (8.33 ms planning).
This is not a full-corpus benchmark or latency SLA.
See [E27 details](docs/decisions/embedding-storage.md#exact-semantic-retrieval-e27).

## Single-step RAG API

The HTTP API retrieves once through SearchEvidence and generates once through
AnswerGenerator. It does not implement multi-step investigation or hybrid search.

Before explicitly starting it, configure the existing DB_* and GEMINI_API_KEY
settings plus **GEMINI_GENERATION_MODEL** and **SERVING_CHUNKING_CONFIGURATION_ID**.
The latter must identify an existing persisted chunking configuration compatible
with the corpus's Gemini embedding profile. Startup reads that profile and never
creates tables, applies migrations, indexes documents or generates embeddings.
Schema and indexed shared documents must already exist in the selected database.
Starting the configured application can connect to that database; question calls
invoke Gemini. Neither was performed against live services during implementation.

There is no implicit generation-model selection. Proposed development default:
`gemini-3.6-flash`; account access, free-tier quota and quality remain unverified.
Set the chosen model explicitly after review. Defaults cap evidence at five items,
serialized question/evidence context at 12,000 characters, output at 1,024 tokens,
and generation at two 30-second attempts. Questions are at most 4,000 characters;
the HTTP operation has a 90-second total deadline. See [.env.example](.env.example)
and the [RAG decision record](docs/decisions/single-step-rag.md) for exact bounds.

After configuration and separate authorization for the intended database/API use:

```powershell
uv run uvicorn investigation_engine.infrastructure.api.app:create_app --factory --host 127.0.0.1 --port 8000 --loop asyncio:SelectorEventLoop
```

The explicit standard-library selector loop supports psycopg on Windows. Resources
are created in lifespan, closed on failure/shutdown, and absent at import time.

- `GET /health` returns `{"status":"ok"}`. It is liveness, not dependency readiness.
- `POST /questions` accepts only `{"question":"Where is Paris?"}`.
- `/docs` exposes FastAPI's generated API reference while the server runs.

The endpoint always queries shared documents only. Owner IDs, model parameters
and profiles are not accepted in request bodies; headers cannot grant private
access. There is no authentication or public deployment in this milestone.

Answered responses contain status, answer, a list of `{"evidence_id":"E1",
"evidence":...}` citations and null message. Each evidence object contains the
exact excerpt/range, document and version IDs, Content digest, SourceReference,
original intersecting source spans, retrieval rank/score and optional title.
Unknown, duplicate or malformed citation identifiers fail validation. A model
cannot supply trusted source metadata. Valid citations establish structural
mapping, not semantic support for the answer.

No evidence, or an explicit model abstention, returns HTTP 200:

```json
{"status":"insufficient_evidence","answer":null,"citations":[],"message":"The available sources do not establish an answer."}
```

Invalid requests return 422; unusable provider output 502; unavailable services
or a whole-evidence context-budget failure 503; timeouts 504. Failures return a
safe `{"error":{"code":"...","message":"..."}}` object, never a guessed answer
or an insufficient-evidence classification. Context selection skips whole items
that do not fit; it never clips source text or offsets.

**RAG checkpoint (E28):** 311 unit + 125 real PostgreSQL integration = **436 passing
tests**, using mocked SDK/fake providers and disposable PostgreSQL 18/pgvector
0.8.5. No live Gemini or Cloud SQL requests occurred. The end-to-end test uses
real registration, chunking, indexing, activation, retrieval, application
composition and HTTP serialization, checking private isolation and no checked-out
database connection while generation waits.

## Web interface

With the existing application configured and running, open **http://127.0.0.1:8000/**.
The page is served by FastAPI and calls the existing POST /questions with only
the question. There is no separate frontend server, build step, package manager,
new runtime dependency or CORS configuration. Startup prerequisites and the
shared-only visibility policy remain unchanged; no schema/indexing work happens
when the page loads.

Enter a question and select **Find an answer**. Loading disables duplicate
submissions. Editing keeps the previous result visible; a new valid submission
clears it and announces progress. Generated answers are plain text. Citation
buttons such as **[E1] Source title** open and focus the corresponding native
source disclosure, using the server's citation array rather than parsing answer
markers. Cards retain exact excerpts, separate document/version IDs, Content
hashes, source identity/revision, record coordinates, metadata and original
zero-based locations. Missing titles/locations have explicit fallbacks; no URL
or download is invented.

Insufficient evidence is a separate result, not a technical error. Validation,
network, timeout and backend failures receive fixed friendly feedback without
raw error messages. The browser stops waiting after 100 seconds, allowing for
the server's 90-second deadline, and never automatically retries. An aborted
browser request does not guarantee cancellation of server/provider work.

Untrusted content uses textContent and DOM text nodes, with no HTML/Markdown
interpretation. Root-page CSP restricts scripts/styles/connections to the same
origin. No model output controls source links or DOM identifiers. Citation
mapping is structural and does not prove semantic support. The page has labels,
keyboard-accessible controls, live status/error announcements, visible focus,
reduced-motion handling and desktop/mobile layouts.

**Verification:** 316 unit + 125 PostgreSQL integration = **441 Python tests**,
plus **24 JavaScript tests**. Browser checks with synthetic data exercised desktop
and 390-pixel mobile layouts, submission, keyboard source expansion, abstention,
errors and injection-shaped text. No live Gemini/Cloud SQL calls occurred.
The wheel was built offline and checked for all three assets.

The optional JavaScript tests use Node's built-in test runner only; Node is not
needed to run the application and there are no npm packages or frontend builds:

```powershell
node --test tests/frontend/test_web.cjs
```

See the [browser implementation record](docs/decisions/single-step-rag.md#browser-interface-e29)
and [testing checkpoint](docs/decisions/testing-strategy.md#e29-browser-interface-checkpoint).

## Real Gemini verification

On **2026-10-28**, an explicitly authorized smoke test passed through real Gemini
embeddings, disposable local PostgreSQL, indexing/activation, SQL retrieval,
structured Gemini generation and the existing browser interface. Two actual
pinned 2Wiki passages supported the answer that North Marion High School is in
the United States and Seoul High School is in South Korea.

Exactly **7 Gemini requests** succeeded: three token counts, three embeddings
(two persisted documents plus one transient query), and one generation using
gemini-3.6-flash. All vectors had 768 finite values; structured output and source
mapping passed, and both excerpts were inspected in the browser. Expected cost
was $0 based on user-confirmed billing-disabled Free tier; billing was not
independently audited. No Cloud SQL access or production code/configuration change
was needed. The approved pinned tokenizer artifact was restored and checksum-checked.
The disposable database/server were removed after the test.

See the [full evidence, request ledger summary, scores and limitations](docs/validation/live-rag-2026-10-28.md).
This establishes one small live case, not general answer quality or corpus-scale
performance. The request budget is exhausted; further live calls need authorization.
Production generation-model/profile settings remain deliberately unchanged.

## Architecture and next work

The package uses domain dataclasses, application use cases/ports, and infrastructure
adapters. `src/investigation_engine/ingestion/` contains standalone dataset
inspection/download utilities; it is not the production extraction boundary.

Start with the [documentation index](docs/README.md),
[decision register](docs/decisions/register.md), and [AI usage record](AI_USAGE.md).
Next work includes explicit generation-model review, a broader real-model
answer/citation evaluation, corpus-scale retrieval measurement, full authentication and
background worker design. Remaining U14 lifecycle policies
require their own approval.
Cloud migration execution requires separate authorization.

## Hybrid retrieval (E31)

Set `RETRIEVAL_MODE=hybrid` in trusted server configuration to combine exact pgvector search with PostgreSQL English full-text search and custom Python RRF. The default remains `semantic`. `SEMANTIC_CANDIDATE_LIMIT=20` and `KEYWORD_CANDIDATE_LIMIT=20` each accept 1-100; `RRF_K=60` accepts 0-10000. Restart the existing application after configuration changes. No migration or extra dependency is required. These are server settings, not request fields. Unauthenticated question requests remain shared-only; the authenticated administrator can search shared plus administrator-owned documents.

Lexical search matches the actual chunk slice, uses stemming/stopwords and requires all meaningful query terms in that chunk; it is not BM25. Fused identity is `(document_version_id, start, end)`, preserving independent occurrences sharing Content. Evidence JSON adds `score_kind` and nullable channel scores/ranks; RRF and keyword scores are not cosine similarities or confidence values. Existing trusted citations/excerpts remain intact.

Verification: **487 Python + 24 JavaScript tests passed**, with fake AI and disposable local PostgreSQL only. At 50,000 synthetic records/memberships/embedding rows, local median semantic/keyword/hybrid times were **4.22/5.48/6.55 seconds**. This unindexed baseline is ready for controlled quality evaluation, not certified for interactive corpus-scale use. A chunk-range lexical index and maintenance design needs separate approval; no schema change was made. See the [implementation](docs/decisions/pgvector-selection.md#hybrid-retrieval-e31) and [benchmark/limitations](docs/validation/hybrid-retrieval-performance.md).

## Automated retrieval evaluation (E32)

The committed `data/samples/evaluation/` snapshot contains 40 categorized cases and a fixed source-only corpus of 218 occurrences / 197 exact Contents, including distractors. Offline tooling in `investigation_engine.evaluation` computes strict source-sentence Recall@K, MRR, fractional/complete multihop coverage and latency. It exports JSON/Markdown and compares existing semantic/hybrid SearchEvidence services without constructing external clients. An explicit cached-query provider rejects misses without live fallback.

```powershell
uv run --no-sync pytest tests/unit/evaluation -q
uv run --no-sync pytest tests/integration/infrastructure/persistence/test_retrieval_evaluation.py -q -s
```

The second command requires existing Docker resources and uses a disposable database plus fake vectors. It exercises all 40 cases in both modes and writes ignored `.cache/evaluation-controlled/retrieval.json` and `.md` reports. It does not measure real semantic quality. No-gold questions have null retrieval metrics. E32 is retrieval-only; see E39 below for answer/citation/abstention/contradiction evaluation. This subset does not replace the approved full ingestion corpus.

## Answer-quality evaluation (E39)

The same 40-case suite now supports conservative answer scoring, strict/partial
gold-citation matching, abstention and contradiction outcomes, and paired
single-step versus multi-step reporting. Offline unit tests and a disposable
PostgreSQL/fake-provider integration test cover the evaluator; they do not call
Gemini. A small authorized real-Gemini attempt completed one correct two-hop
single-step answer, then stopped after a provider error before the paired
multi-step run. The contradiction and paired comparison remain unmeasured. See
the [methodology and offline results](docs/validation/answer-quality-evaluation.md)
and the [captured JSON/Markdown sample](docs/validation/answer-quality-live-2026-10-28.md).

## Retrieval optimization (E33)

The PostgreSQL retrieval hot path uses an equivalent boundary-trimming blankness check and fetches source payloads only for already eligible ranked candidates. Exact cosine, chunk-level FTS, RRF, ownership, completeness, ties and citations are unchanged. No migration/index is needed. `scripts/benchmark_retrieval.py` measures 1k/10k/50k disposable synthetic records with median/P95, EXPLAIN/buffers/temp and container CPU; it requires cached local Docker images and never loads application database settings. See the [before/after checkpoint and unapproved lexical-index proposal](docs/validation/hybrid-retrieval-performance.md#e33-schema-preserving-optimization-2026-10-28). No corpus-scale interactive SLA is claimed.

At 50k, the new paired benchmark measured semantic median 4.95 → 1.48 seconds
and hybrid 6.42 → 3.43 seconds (20 samples each). The sub-second aspiration is
not met; completeness sorting/temp I/O and runtime lexical parsing remain.
Verification: **365 unit + 147 PostgreSQL integration = 512 Python tests passed**,
plus **24 JavaScript tests**. No real embeddings/API/cloud access was used.

## PDF and DOCX extraction (E34)

TXT, readable PDF and DOCX are supported through infrastructure adapters returning the same ExtractedDocument contract. Use `extract_pdf(path, source_id=...)` or `extract_docx(path, source_id=...)`, then the existing RegisterDocument, chunking and indexing interfaces. The trusted caller assigns stable source identity and explicit ownership; no upload endpoint or format dispatcher is added. PDF citations retain original one-based pages (including numbering across blank pages). DOCX citations retain zero-based hierarchical paragraph/table coordinates, without invented pages.

The uv lock includes pypdf/python-docx and lxml. There is no OCR or Office runtime dependency. Empty/no-readable-text files, malformed documents and unsupported encrypted PDFs fail explicitly before registration. Read the [format contract](docs/design/file-extraction.md) for exact separators, metadata, source locators and unsupported content. Existing TXT behavior, exact-content reuse, retrieval, RRF and schema are unchanged.


## Authenticated document management (E36)

The browser provides optional one-administrator document management when ADMIN_API_TOKEN is configured. Configure an unpredictable token of at least 32 ASCII characters, set ADMIN_PRINCIPAL_ID to the stable trusted principal, and require HTTPS for public deployment. The bearer token is held only in page memory; refreshing requires reauthentication. The public question endpoint remains shared-only; an authenticated administrator searches shared and their own documents. Uploads support bounded TXT, PDF and DOCX extraction; original binary files are temporary and not retained. Indexing is explicitly advanced in small bounded batches. See [authenticated document management](docs/design/authenticated-document-management.md) for route contracts, limitations and safety behavior.

## Corpus-scale processing (E40)

The complete pinned 2Wiki development split has been processed through existing
application services in disposable PostgreSQL: **125,760 Documents/versions,
56,680 unique exact Contents, and 125,760 complete whole-passage memberships**.
The final validation took 21.04 minutes and used 253.6 MB of database storage.
Source/text/span integrity, deliberate interruption, resume, database restart and
stable retry counts/IDs passed. This meets the approved exact-content processing
baseline; it does not prove semantic independence.

The full validation corpus had zero embeddings and zero active/searchable
versions. A separate bounded Gemini Embedding 2 pilot persisted six genuine
vectors and verified semantic/keyword/hybrid retrieval, ownership, provenance
and restart with four public searchable source occurrences. Task 14 used 12
token-count calls and 12 embedding calls across its two attempts, with zero
generation calls. The disposable databases were removed after verification.

See the [manifest](data/samples/2wiki-dev-corpus-manifest.json),
[commands, measurements, recovery policy and indexing plan](docs/validation/corpus-scale-2026-10-28.md),
and [complete database result](docs/validation/corpus-processing-results-2026-10-28.json).
The same report compares all three chunkers and explains why the current
one-input Gemini adapter needs at least 57 inference-quota days for all 56,680
whole-passage vectors at the user-reported 1,000 RPD. Full embedding/batching and
deployment require further review; no Cloud SQL, schema or storage-policy change
was made. **620 Python and 26 JavaScript tests passed**, with 7 opt-in Python
checks skipped.
