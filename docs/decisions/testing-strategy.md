# Risk-based automated verification

**Status:** CONFIRMED by [E22](register.md#e22), 2026-10-27. This approval ends
[A10](register.md#a10)'s temporary software-test deferral. It does not select the
separate >=40-question evaluation methodology under U17.

Use pytest, pytest-asyncio, pytest-cov and Testcontainers. Dependencies live in the
uv dev group and are locked. Tests protect observable invariants, failures and
committed state; no global coverage minimum is imposed. Add tests alongside
future features, rather than postponing them to submission.

## Layout and fixtures

```text
tests/
  conftest.py                     # category/opt-in hooks, small candidate builder
  unit/
    conftest.py                   # block service connections
    domain/
    application/
      conftest.py                 # deterministic character tokenizer
    infrastructure/
      ingestion/
      persistence/
      tokenization/
  integration/
    conftest.py                   # disposable database, migrations, cleanup, sessions
    infrastructure/persistence/
      conftest.py                 # schema-specific synthetic rows
    migrations/
  external/
    ingestion/                    # optional local pinned-corpus checks
```

E22 introduced no speculative embedding interface. E23 subsequently implements
the embedding port and Gemini adapter with mocked SDK and HTTP-transport tests.
The character
tokenizer has intentionally obvious offsets; the concrete tokenizer adapter is
also checked with a tiny locally generated tokenizer artifact. Fixtures use
Unicode, independent occurrences, shared exact Content, conflicting mappings,
private/shared owners and deleted registrations.

Unit tests cover domain ranges and exact UTF-8 identity, explicit ownership,
registration candidate construction/error propagation, in-memory idempotency and
conflicts, JSONB-compatible locator equality, defensive snapshots, extraction,
configuration and all three chunkers. They block outbound socket connections.
Windows asyncio's private socketpair construction is allowed synchronously;
arbitrary loopback service connections remain blocked. Settings tests use only
temporary .env files and dummy credentials, restoring the environment afterward.

## Database safety and integration

The session-scoped container uses the published image
`pgvector/pgvector:0.8.5-pg18-trixie`, pinned to multi-platform manifest
`sha256:9d2e61c7352b9e9f4798df5fd9a498f043f4cda1cdacc707de3d198650f4321e`.
The [Docker Hub tag API](https://hub.docker.com/v2/repositories/pgvector/pgvector/tags/0.8.5-pg18-trixie)
was checked before selection on 2026-10-27. The fixture verifies PostgreSQL major
18 and pgvector 0.8.5 at runtime.

The URL is constructed directly from that container's mapped host/port and
explicit disposable credentials. There is no application-settings import,
DATABASE_URL fallback, .env lookup, SQLite substitution or alternate-server mode.
The normal PostgreSQL and Cloud SQL proxy ports are explicitly rejected. Docker
failure is an error for an explicitly requested integration run; the default
unit-only run needs no Docker. Testcontainers removes its container and volumes.

Apply actual Alembic revisions through their standard caller-supplied Connection
path. This is the only runtime infrastructure adjustment: migrations/env.py
accepts config.attributes['connection'] before importing application settings.
Normal CLI migration behavior is unchanged; neither migration revision is edited.
The fixture enables pgvector only in its disposable database; 0002 still refuses
to install an extension itself.

Repository calls really commit. Tests verify persistence through new sessions,
retry identity, exact Content reuse, distinct sentence layouts and ordered source
locators reloaded through new sessions, immutable descriptions, conflicts, JSONB/null
identity, owner checks, deleted retries and rollback without orphan candidates.
An injected SQLAlchemy session subclass coordinates the two initial occurrence
reads, then allows real INSERTs/commits; two distinct backend PIDs are required.
There are no sleep-based races or mocked PostgreSQL results.

Committed test data is truncated between tests, only through the container-derived
engine. Schema-changing tests get separate randomly named databases inside that
same container. Downgrade/re-upgrade, absent-extension rollback, ORM/schema parity,
FK/UNIQUE/CHECK enforcement and vector validity are tested against PostgreSQL.

## Optional and future external checks

Default `pytest` discovers only tests/unit. Explicit external-directory selection
still skips local dataset checks without --run-dataset. They verify the pinned
Parquet digest, exact counts, original sentence offsets and the recorded Carlos
F. Borcosque layouts. Only _id/context columns are loaded; gold questions/answers
and supporting facts never become retrieval input. No downloads occur. Missing
files skip with a clear reason; a wrong file digest fails.

Live smoke tests belong under tests/external and carry the external marker.
They require --run-external even if credentials exist. Dataset checks have their
own independent --run-dataset gate. E23 adds a Gemini smoke test for synthetic
document/query inputs under tests/external/embeddings; it was not executed during
implementation. Normal collection and CI still make no live API calls. It checks
successful authentication and valid dimensions/finite/nonzero vectors, without
claiming retrieval quality. Offline tests cover formatting and safe errors.

The next persistence feature must use a deterministic fake at its actual provider
interface to verify input order, call counts, deduplication/reuse, malformed and
partial responses, rate limits and commit consistency. Do not build that fake
framework before the interface exists.

## CI, coverage and current evidence

[GitHub Actions](../../.github/workflows/tests.yml) uses a Linux runner, pinned
checkout/setup-uv actions, Python 3.14.9, uv 0.11.17 and locked dev dependencies.
It runs unit and Docker integration tests and prints coverage. Permissions are
contents:read; checkout does not retain credentials. No secrets, cloud access,
external-service tests, releases or deployment are configured. Remote execution
has not been performed by this task.

Coverage measures statements and branches in the application package, excluding
package initializers and standalone exploratory/download CLIs. Migrations and
test infrastructure are outside the measured source. Read missing branches and
failure scenarios alongside percentages; declarative imports are not evidence
that database constraints have been exercised.

E22 baseline executed locally on **2026-10-27**, Python 3.14.9:

- Default suite: **89 passed, 2 strict expected failures**.
- Optional corpus tests: **2 passed**, including actual pinned tokenizer/layout
  verification; without opt-in, **2 skipped**. Live opt-in alone does not enable
  dataset checks.
- Discovery: 91 unit cases, 50 integration cases and 2 optional dataset cases.
- Unit-only statement coverage: **423/674 = 62.76%**; branch coverage:
  **139/198 = 70.20%**. Combined coverage.py display rounds to 64%.
- Domain modules, registration use case, in-memory repository and settings are
  exercised thoroughly; chunking's combined measure is 96%. The PostgreSQL
  repository, ORM and mapping boundary remain at 0% in the unit-only report
  because their tests require the separate integration run.
- Integration execution remains blocked before database access. Docker initially
  failed at startup, then recovered and responded as engine 29.1.2. Testcontainers
  started its cleanup helper but could not finish pulling the PostgreSQL image.
  Direct Docker pulls first reported `lease does not exist: not found`, then,
  after downloading several layers, failed with `short read: expected 29785419
  bytes but got 22482952: unexpected EOF`. The user reported no Docker Desktop
  error and a possibly slow connection. No Docker/network settings were changed.
  **The 50 integration cases are implemented and collected, but their assertions
  have not yet executed.** Earlier isolated native database checks recorded in
  other ADRs are not substituted for this suite. Retry the documented integration
  command once the pinned image can be downloaded successfully.

E23 generation checkpoint on the same date/runtime:

- **48 new offline Gemini cases passed**; full unit suite **137 passed, 2 strict
  expected failures**. The registration mismatch is preserved.
- The real installed SDK is exercised through in-memory HTTP transport, and
  cancellation tests cancel tasks blocked at both remote-operation boundaries.
  No sockets, live Gemini calls or database connections are needed.
- Unit-only statements: **537/788 = 68.15%**; branches:
  **173/232 = 74.57%**. Adapter statements and branches are fully covered locally;
  these figures establish no live provider or database guarantees.
- Live smoke selection without --run-external: **1 skipped**. The opted-in body
  was not executed. Database tests and image downloads were not attempted.
- One upstream google-genai typing deprecation warning is visible on Python
  3.14; it is not globally suppressed. No formatter/linter is configured;
  syntax, lockfile and whitespace checks are used.

## Registration alignment and remaining gaps

E24 resolves the two registration xfails recorded in the historical checkpoints
above. New in-memory occurrences now require version 1 and no current_version_id,
matching PostgreSQL and the normal use case. The guard follows existing-source
lookup/authorization/conflict checks and precedes all writes. The two tests now
pass normally and check unchanged stores plus successful registration of corrected
candidates. Two further cases seed an active stored document and retry it with
and without an active pointer on the candidate, preserving IDs, ownership,
Content and the active pointer without mutation. No activation API was added.

E24 verification on 2026-10-27: **28 focused tests passed; all 141 unit tests
passed, with no expected failures**. The pre-fix run reproduced both missing
ValueError failures. The only runtime change is the in-memory guard; the port
documents the shared contract. PostgreSQL implementation, transactions and
migrations are unchanged. Its pinned Docker image is still absent locally, as
confirmed by read-only image inspection, so database integration was not run.
No image download, external API or cloud access was attempted. The existing
upstream SDK typing warning remains visible.

E25 chunk-plan storage adds 20 pure application validation cases and 18 real
PostgreSQL cases. **All 161 unit tests pass**. The 18 new database tests collect
but were not executed: read-only inspection confirms the pinned image remains
absent, and no download or alternate database is used. They cover reconstruction,
idempotency/conflicts, configuration identity, Unicode/overlap, source-version
layouts, empty/blank plans, rollback after INSERT, corrupt manifests and three
coordinated independent-session concurrency scenarios. The actual lookup SQL
compiles, which is not evidence that its transactions or locks have run. See the
[E25 storage record](embedding-storage.md#chunk-plan-persistence-e25).

E26 adds 35 unit and 33 PostgreSQL cases for embedding values, resumable indexing
and activation. **All 196 unit and 101 integration tests pass** (297 total), using
fresh disposable PostgreSQL 18/pgvector 0.8.5 and actual migrations. Prior image
unavailability is no longer a blocker. The new shared synthetic provider implements
the actual profile-aware application port without network IO. Tests exercise
incremental commits/resumption, float32 rejection, immutable first-writer storage,
legacy configuration aliases, exact text checks, rollback after INSERT, task
cancellation, no checked-out connection during provider calls, cache-only retries,
blank membership policy, invalid/incomplete activation, prior active state and
stale-version protection. Independent-session races cover duplicate persistence,
two whole indexers, same/different target activation and both soft-deletion
orderings, with actual pg_stat_activity lock waits observed. The live smoke test
is not executed. See [E26](embedding-storage.md#resumable-indexing-and-activation-e26).

Remaining untested risks include PostgreSQL cancellation while blocked, uncertain commit
outcomes, corrupted stored payloads and fault-injected hash-collision recovery.
Background claim/recovery has no tests because it is not implemented.
E27 adds retrieval coverage below. Live provider behavior remains unverified.
Evaluation quality, citation rendering, authorization
at public entry points and the automated evaluation system remain separate work.

### E27 semantic retrieval checkpoint

Full execution: `uv run --no-sync pytest tests/unit tests/integration -q
--cov=investigation_engine --cov-report=term --cov-report=json` completed with
**348 passed (225 unit, 123 PostgreSQL integration)** and one existing upstream
SDK typing deprecation warning. Statement coverage is **1,294/1,362 = 95.01%**;
branches **355/404 = 87.87%**, combined **93.37%**. The preceding E26 checkpoint
remains historical evidence; the current package includes the retrieval code.

The 29 new unit cases cover query/scope/count validation, provider/query-format
separation, profile compatibility, malformed vectors, error propagation and
defensive source mapping. The 22 new database cases cover exact known cosine
ranking and magnitudes, deterministic ties/limits, ownership-before-top-K,
active/deleted/superseded versions, selected-profile completeness, corrupted and
ambiguous plans, legacy profile aliases, Unicode offsets/whitespace/overlaps and
independent provenance over a shared vector. Registration through indexing,
activation and retrieval runs against the real disposable database; constructing
a new repository reads persisted hits. Provider calls hold no checked-out session.

Cached Docker images and the actual migrations were used without downloads,
Cloud SQL access or live Gemini calls. A separate non-coverage 1,001-vector fixture
check measured 81.32 ms median across five application calls and 25.32 ms EXPLAIN
execution / 8.33 ms planning after refreshing fixture statistics. No hard timing
assertion is used. Full-corpus retrieval quality, cost/concurrency and rendered
citations remain unverified. See [the implementation record](embedding-storage.md#exact-semantic-retrieval-e27).

### E28 single-step RAG checkpoint

Full regression command: `uv run --no-sync pytest tests/unit tests/integration -q
--cov=investigation_engine --cov-report=term --cov-report=json --tb=short`.
**436 passed: 311 unit + 125 PostgreSQL integration**, with the existing upstream
SDK typing deprecation warning. This adds 86 unit and two database cases.
Statement coverage: **1,610/1,692 = 95.15%**; branch coverage **428/488 = 87.70%**;
combined **93.49%**. No new coverage threshold was selected.

Application tests cover grounded answers, multiple references, strict/unknown/
missing/duplicate citations, malformed results and provider bypass, abstention,
retrieval failures, provider unavailability/timeouts and intact context limits.
SDK tests exercise actual google-genai response/configuration types with mocked
async calls: schema/prompts, malformed/truncated/blocked responses, bounded
transient/quota retries, sanitized failures, timeout/cancellation and client close.

ASGI tests exercise serialization, forbidden owner/model controls, invalid/missing/
long questions, shared-only scope despite headers, response validation, health,
safe errors and startup/shutdown. Configuration tests use temporary dotenv files.
Lifecycle tests check reverse-order cleanup and partial startup failure. The
documented Uvicorn selector-loop configuration is checked for Windows psycopg.

Two real PostgreSQL-to-HTTP tests use the actual application lifespan/composition,
all registration/chunking/indexing/activation/retrieval components, and fake AI
constructors. They verify exact Unicode excerpts/native sentence locators,
independent provenance over shared Content, private exclusion even for a closer
private vector, private-only abstention without LLM calls, and zero checked-out
database connections while generation waits. Resource closure is asserted.
Every database URL comes from the disposable fixture; application .env values
are never used for these tests. Cached Docker images were reused, without image
downloads, Gemini calls, Cloud SQL access or infrastructure changes.

Live model compatibility/quota, factual/semantic citation accuracy, adversarial
prompt resistance, full-corpus quality/cost and HTTP load/admission controls remain
unverified. [The RAG ADR](single-step-rag.md) records these limits. Single-step RAG
does not complete the assignment's multi-step investigation/evaluation requirements.
E38 later adds a bounded multi-step workflow and two live smoke cases; broad answer
and citation quality evaluation remains incomplete.

### E29 browser interface checkpoint

Full regression: `uv run --no-sync pytest tests/unit tests/integration -q --tb=short`
completed with **441 passed (316 unit, 125 PostgreSQL integration)** and the existing
SDK typing deprecation warning. Five new Python tests cover root/static assets,
security headers, labels/status markup, non-interpolated HTML, traversal/missing
files, unchanged JSON contracts, multiple source identities, shared scope,
abstention/errors and working-directory independence. Existing real PostgreSQL
RAG tests additionally load the page/script through the actual composition.

`node --test tests/frontend/test_web.cjs` completed with **24 passed** using only
Node built-ins, fake fetch and a small DOM/event double. The actual production
JavaScript is executed with no test hooks or exports. Cases cover exact same-origin
request shape, answer/source mapping, reversed citation order, multiple/absent
metadata, native-disclosure/focus calls, literal malicious-looking text, Unicode
counts, invalid questions, loading/duplicate suppression, previous response state,
abstention, HTTP/network/timeout errors, malformed success and non-JSON errors.
These tests do not emulate browser layout; they run separately from Python CI
and require no frontend packages, runtime dependency or build step.

Actual browser verification used a temporary loopback server with fake AI and
synthetic source data, without loading the real .env. Desktop (1100x900) and mobile
(390x844) checks covered submission, keyboard source expansion, validation,
insufficient evidence and provider error recovery. Injection-shaped source/answer
strings remained literal text, with zero injected img/script nodes. Mobile content
did not overflow horizontally. Screenshots were saved, viewport reset, preview
tab closed and server stopped. This is not a full browser/accessibility audit.

The wheel was built offline with cached uv tools; ZIP inspection confirmed the
three web assets and packaging required no configuration change. Final presentation
adjustments were followed by all 24 JavaScript and five page-route tests passing.
No dependencies, schema, migration, application use case or configuration changed.
No live Gemini/Cloud SQL, deployment, cloud changes or Git operations occurred.
See [the E29 implementation record](single-step-rag.md#browser-interface-e29).

### E30 authorized live verification checkpoint

The [live report](../validation/live-rag-2026-10-28.md) records explicit user
authorization, confirmed billing-disabled Free tier, a seven-request ceiling,
no retries and a disposable local database. **All seven real Gemini requests
succeeded:** three countTokens and three single-input synchronous embedding calls
on gemini-embedding-2, plus one structured gemini-3.6-flash generation. Two real
document vectors committed and activated; a transient real query vector drove
SQL cosine retrieval. The browser displayed both sources and a factually supported
answer. Exact canonical text/hashes/spans/locators and UUIDv7 identities matched
the originals. This is one curated smoke case, not an automated quality benchmark.

**93 focused offline tests passed** before live execution: embedding/generation
adapters, HTTP API, disposable PostgreSQL-to-RAG composition and migration chains.
The real SDK also completed an offline seven-call MockTransport serialization/
parsing check with a dummy key. No production compatibility defect or code fix
was found, so no new regression test was necessary. The preceding full baseline
remains **441 Python + 24 JavaScript**; no ordinary test now calls a live service.
The existing --run-external gate is unchanged. The ad hoc runner required explicit
authorization/free-tier flags, enforced its network budget and rejected replay
after consumed requests. Its sanitized ledger/screenshot remain ignored artifacts.
Token-count support, generation schema compatibility and real browser behavior
are verified for this case; broader factual/semantic support still needs U17.

### E31 hybrid retrieval checkpoint

Full offline run: **487 Python tests passed** (342 unit and 145 PostgreSQL integration), plus **24 JavaScript tests passed**. Real disposable PostgreSQL verifies exact chunk FTS, Unicode/native source spans, pre-limit ownership/deletion/current-version/configuration filtering, incomplete profiles, shared vectors with independent sources, same-statement consistency during concurrent deletion, RRF-to-RAG and both HTTP composition modes. Unit tests verify hand-calculated fusion, duplicate/tie/identity behavior, limits, error propagation and trusted settings. One upstream google-genai typing deprecation warning remains. No test called Gemini or Cloud SQL. No assertions were weakened. The [benchmark](../validation/hybrid-retrieval-performance.md) records 1k/10k/50k synthetic measurements; it is separate from answer-quality evaluation.

### E32 retrieval evaluation checkpoint

**365 unit and 146 PostgreSQL integration tests passed (511 Python total), plus
24 JavaScript tests.** The final combined run passed 507; four further focused
unit tests then passed in the complete 365-test unit rerun. Twenty-three new unit
cases cover sentence-level recall, truncated MRR, partial/full multihop coverage,
duplicates, multiple sentences/sources, wrong occurrence/hash/partial spans,
null no-gold metrics, missing corpus labels, failures, positive/negative/negligible
paired differences, exports, manifest validation and an offline query cache with
no live fallback. The new PostgreSQL test runs both modes on the same real indexed
218-occurrence corpus for all 40 questions. All vectors are explicitly synthetic;
these measurements verify evaluation plumbing, not semantic model quality.
No Gemini, Cloud SQL, dependency/schema/index/frontend or retrieval changes.
See [methodology and measured example](../validation/retrieval-evaluation.md).

### E39 answer-quality evaluation checkpoint

**445 unit and 159 PostgreSQL integration tests passed, 4 opt-in tests skipped,
plus 26 JavaScript tests.** The new disposable-PostgreSQL test runs both
answering use cases over the same indexed source and checks that provider calls
do not hold a database connection. The opt-in live Gemini sample indexed 12
source occurrences and used 17 embedding inputs, then recorded one successful
single-step answer and one generation provider failure. Its stop-on-error guard
left all multi-step sample cases not run; this is not a paired quality result.
The [answer-quality report](../validation/answer-quality-evaluation.md) records
the score definitions, API counts, failure, and unresolved measurements.
No Cloud SQL or production database was used. An existing API test fixture was
updated to return the documented `AnswerResult` type instead of a dictionary;
no endpoint behavior changed.

### E33 retrieval optimization checkpoint

Full combined run: **512 Python tests passed** (365 unit, 147 PostgreSQL
integration), plus **24 JavaScript tests passed**. One existing upstream SDK
typing deprecation warning remains. Semantic/hybrid eligibility, corrupt or
incomplete plans, profile aliases, deleted/private/current-version handling,
shared vectors/independent provenance, exact chunk FTS, RRF, RAG and the fixed
40-question evaluator all remain passing. The new real-PostgreSQL test checks
optimized boundary-blankness against Python and the previous predicate over
the complete Python whitespace set and mixed Unicode cases.

Separate 20-sample benchmarks compare ordered old/new result signatures for
semantic, keyword and fused hybrid paths on the same 1k/10k/50k rows. They cover
IDs, ranges, hashes, text, sources/spans and raw/fused scores/channel ranks; no
ranking difference was found. The 1k repeat also passes equivalence. See the
[performance checkpoint](../validation/hybrid-retrieval-performance.md#e33-schema-preserving-optimization-2026-10-28).
No assertion was weakened. No live AI, Cloud SQL, schema/index change, additional
dependency, frontend change or Git publication occurred. Existing migrations
ran only inside disposable benchmark/test databases.

### E40 corpus-scale checkpoint

The complete ordinary suite passed **620 Python tests** (460 unit and 160
PostgreSQL integration), with **7 opt-in checks skipped**, plus **26 JavaScript
tests**. New coverage checks deterministic source selection, exact reuse,
interrupted registration/plan gaps, cancellation, checkpoint target/configuration
binding, immutable progress records under Windows file locks, disjoint ranges,
disk limits, structural comparison, and sanitized provider status/usage export.
No ordinary test called Gemini or Cloud SQL. The existing SDK typing warning
remains.

Separate operator validations processed the entire real pinned development split
through the existing application services and tested actual PostgreSQL restart,
source integrity and replay. A bounded real-Gemini pilot exercised two chunking
configurations with genuine vectors and verified restart/visibility/provenance.
These measurements are recorded in the [corpus-scale report](../validation/corpus-scale-2026-10-28.md),
which separates complete-corpus processing from the small searchable pilot.

### E34 file extraction checkpoint

**534 Python tests passed** (385 unit, 149 PostgreSQL integration), plus **24 JavaScript tests**. Twenty new unit cases cover PDF Unicode/pages/empty pages/metadata/strict failures/encryption/stream cleanup and DOCX order/blank paragraphs/tables/nesting/merges/absent properties/malformed XML, plus existing registration retries/Content reuse/chunk reconstruction. Two real PostgreSQL format tests exercise exact text/metadata/structure/locator round trips, deduplication, chunk plans, fake indexing/activation and source-correct retrieval. No real AI or Cloud SQL. The Unicode PDF fixture was rendered and visually inspected; authoring tools are not project dependencies. No retrieval/RRF/schema/migration/frontend change or weakened assertion. See the [format contract](../design/file-extraction.md).

### E35 document lifecycle checkpoint

**545 Python tests passed** (390 unit and 155 PostgreSQL integration), plus **24
JavaScript tests passed**. Five new unit cases exercise immutable update/retry,
same-text revision Content reuse, explicit revision and source-coordinate rules,
pending/stale/conflicting requests, soft deletion, owner/shared visibility,
details and pagination. Six new PostgreSQL tests cover active-version continuity
through failed/successful indexing, retry after activation, explicit same-text
revision reuse, row-lock concurrency, access/listing/deletion, retained shared
embeddings, and deletion during a provider call that prevents reactivation. No
Gemini or Cloud SQL calls; migration 0002 remains unchanged. The sole warning is
the existing google-genai `_UnionGenericAlias` deprecation warning. `git diff
--check` passes. No assertions were weakened.
