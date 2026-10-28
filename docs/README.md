# AI Investigation Engine — Architecture documentation

**Status:** Confirmed baseline and unresolved choices reconciled through 28 October 2026; technology approvals are distinct from implementation.\
**Audience:** Developer, reviewers, and Upgaming technical interviewers.  
**Scope:** Architecture, approval evidence, and implementation/validation records; documentation does not imply completed implementation.

## Reading order

For installation and a database-free smoke check, start with the root [README](../README.md). The [AI usage record](../AI_USAGE.md) records assistance without claiming completed assignment delivery. Some historical approval attachments and temporary raw measurements are referenced through local paths; the register/ADRs preserve their relevant summaries. Those local references are not runtime dependencies or files available in a fresh clone.

1. [Architectural decision register](decisions/register.md) — start here for CONFIRMED decisions, approval evidence, unresolved implementation blockers, assignment requirements, and reconciliation notes.
2. [System overview](architecture/system-overview.md) — component boundaries and target pipeline; proposed technologies in diagrams remain subject to the register.
3. [ADR-004](decisions/004-document-content-provenance.md) — document, version, content, structure, and provenance reasoning.
4. [Document data contract](design/document-data-contract.md) — logical responsibilities, scoped chunk contracts and historical candidate fields; the U07 ADR governs authoritative physical storage.
5. [Document lifecycle and indexing](design/document-lifecycle-and-indexing.md) — intended lifecycle behavior and unresolved recovery mechanisms.
6. [2Wiki development audit](validation/2wiki-dev-audit.md) — historical dataset measurements. Later read-only reference checks are recorded in the register's [observed evidence](decisions/register.md#observed-evidence).
7. [Schema acceptance plan](validation/schema-acceptance-plan.md) — planned scenarios and evidence needed for acceptance; not passing test results.
8. [Answer-quality evaluation](validation/answer-quality-evaluation.md) — E39 scoring rules, offline verification, and limitations of the partial live sample.
9. [Corpus-scale processing](validation/corpus-scale-2026-10-28.md) — E40's complete pinned-corpus processing, recovery, chunking comparison, actual searchable pilot size, and Gemini quota plan.
10. [Batched Gemini evaluation corpus](validation/batched-gemini-evaluation-2026-10-28.md) — E41's synchronous batch experiment, persistent PostgreSQL 18 evaluation corpus, restart verification, real-vector semantic/hybrid metrics, and quota-limited answer-quality status.

## Decision status definitions

- **CONFIRMED:** The user explicitly approved the stated choice; approval evidence and scope are recorded.
- **UNRESOLVED:** Discussed without a final selection.
- **NEW:** Newly identified and not yet discussed/selected.
- **SUPERSEDED:** A previously approved decision was explicitly replaced by a later approved decision; both approvals must be traceable.

Historical labels such as Agreed, Planned, Provisional, Open, or Accepted are retained where useful for context, but do not independently establish user approval. Implementation progress, verification, and deferral are separate from decision status. Assistant recommendations, example code, existing implementation, or silence must not be treated as approval.

For a NEW or UNRESOLVED choice, explain context, alternatives, trade-offs, recommendation, and consequences and obtain explicit approval before dependent implementation. Existing confirmed decisions are not reopened without a reason. See the register's [authority rules](decisions/register.md#authority-and-status-rules).

## Existing decision records

- [ADR-001](decisions/001-python-version.md): initial runtime; confirmation scoped by A01.
- [ADR-002](decisions/002-environment-and-dependency-management.md): uv environment; confirmation scoped by A01.
- [ADR-003](decisions/003-parquet-ingestion-pyarrow-vs-pandas.md): PyArrow; A02 does not approve normalization or sentence assembly.
- [ADR-004](decisions/004-document-content-provenance.md): domain/provenance principles; A06-A09 do not select physical persistence or recovery mechanisms.
- [ADR-005](decisions/005-application-architecture.md): architectural direction and identifiers; A04/A05 do not select frameworks, search contracts, or hosting.
- [PostgreSQL versus MySQL](decisions/postgresql-vs-mysql.md): U05 confirms PostgreSQL as the authoritative relational database; compares official capabilities and records the scope, trade-offs, and persistence decisions still required. Vector retrieval is excluded.
- [pgvector selection](decisions/pgvector-selection.md): U12 selects PostgreSQL/pgvector; U13 selects native full-text keyword search and custom RRF. Models, schemas, interfaces, index settings, and evaluation remain separate decisions.
- [Google Cloud SQL hosting](decisions/google-cloud-sql-hosting.md): U18's conditional hosting choice and original rationale. The user subsequently provisioned the instance; read-only PostgreSQL/pgvector connectivity is verified. Trial resources, costs, expiry and application hosting remain unverified or unresolved.
- [Python persistence stack](decisions/python-persistence-stack.md): U06's stack and scoped U14 implementation: async registration, isolated repository-owned READ COMMITTED transactions, conflict resolution, errors/cleanup, trusted ownership, and ordinary factory injection. Production pool/deadline settings remain open.
- [PostgreSQL physical schema](decisions/postgresql-physical-schema.md): U07's unchanged three-table ORM/Alembic foundation, ownership/deletion representation, constraints, and migration use. E16 adds PostgreSQL registration; authentication, activation, and remaining lifecycle concurrency remain separate work.
- [Deterministic chunking](decisions/deterministic-chunking.md): E17/E18's whole-passage, fixed-token and supplied-sentence configurations, exact ranges and transient version memberships; experimental file-loaded tokenizer, Unicode behavior and scoped verification. E21 subsequently approves initial derived storage; production embedding and indexing decisions remain open.

- [Embedding storage](decisions/embedding-storage.md): E21 schema, E23 generation, E25 plans, E26 indexing/activation and E27 exact semantic retrieval with selected-profile eligibility and source citations. Background orchestration, hybrid retrieval and cloud migration remain unimplemented.

- [Testing strategy](decisions/testing-strategy.md): E22 ends A10's temporary deferral and introduces permanent risk-based unit/integration/opt-in tests, disposable database safety, CI, coverage and honest execution evidence.
- [Authenticated document management](design/authenticated-document-management.md): E36's administrator boundary, upload/indexing bounds, lifecycle routes, retry identity and retrieval scope.
- [Single-step RAG and browser interface](decisions/single-step-rag.md): E28's answer workflow/Gemini adapter and E29's same-origin page. E30 verifies one authorized real-Gemini/local-PostgreSQL/browser case. E38 adds a bounded multi-step workflow; production model selection and broader investigation quality remain open.
- [Live Gemini verification](validation/live-rag-2026-10-28.md): E30's user-confirmed Free tier, seven-request budget, actual models/usage, source-only corpus, persisted vectors, SQL ranks, generated answer, factual source check and disposable-resource cleanup.

Each ADR now has a reconciliation note. Historical reasoning is retained; the register identifies the exact approval and open details.

## Implementation and assignment evidence

The register separates [observed implementation and audit evidence](decisions/register.md#observed-evidence) from [verified assignment requirements](decisions/register.md#assignment-requirements). Extraction, single-step RAG, hybrid retrieval, application lifecycle operations, and E36's administrator HTTP/browser workflow are implemented. E30 verifies one real-Gemini two-source browser question, not general answer quality or corpus-scale performance. E38 adds a bounded multi-step workflow and two live smoke cases; E39 adds answer-quality evaluation, while a complete real-model evaluation, full user authentication, background workers, and deployment remain incomplete.

[E22](decisions/register.md#e22) supersedes A10's temporary deferral and resumes automated software tests. The assignment still requires automated software tests and a separate automated evaluation system with at least 40 questions. [U17](decisions/register.md#u17) records the approved testing strategy while evaluation details remain open.

Authoritative document/source history and derived search data have separate responsibilities; no physical database, hosting provider, model, or schema is selected by an example. Benchmark answers and gold evidence remain outside the searchable evidence corpus.

## Documentation scope

[A11](decisions/register.md#a11) approves the central register alongside ADRs. Additional requirements, ingestion, evaluation, and roadmap document organization remains [N01](decisions/register.md#n01); no new hierarchy is implicitly approved.

Record new approvals in the register, update affected ADRs without erasing history, and keep measurement/configuration evidence distinguishable from architectural intent. Do not label conceptual walkthroughs or transient smoke checks as completed automated acceptance gates.

## E31 current checkpoint

Hybrid retrieval and custom RRF are now implemented; earlier statements deferring them are historical. See the existing [retrieval ADR](decisions/pgvector-selection.md#hybrid-retrieval-e31), [performance checkpoint](validation/hybrid-retrieval-performance.md) and [test results](decisions/testing-strategy.md#e31-hybrid-retrieval-checkpoint). Full results: 487 Python + 24 JavaScript tests passed. No live AI/cloud access or migration occurred; corpus-scale interactive performance and derived lexical indexing remain open.

## E32 retrieval evaluation

The [retrieval methodology](validation/retrieval-evaluation.md) documents the 40-case committed snapshot, strict source/sentence mapping, fixed source-only corpus, offline query cache, configuration comparisons and example real-PostgreSQL/fake-vector results. E32 is retrieval-only; E39 adds answer/citation/abstention/contradiction scoring in a separate [answer-quality report](validation/answer-quality-evaluation.md). Gold annotations remain outside searchable text. The live sample is partial; held-out evaluation and broad real-model quality remain open.

## E33 measured retrieval optimization

See the [updated performance checkpoint](validation/hybrid-retrieval-performance.md#e33-schema-preserving-optimization-2026-10-28) for schema-preserving SQL changes, same-database baseline comparisons, median/P95/CPU/buffer/temp evidence and the still-unapproved exact-range lexical table/index proposal. Public interfaces, pre-limit eligibility, exact scoring and source identity are preserved.

## Task 17 production deployment

See the [Cloud Run deployment guide](design/cloud-run-deployment.md) for the
production image, isolated checks, existing-resource inventory, runtime/secret
activation blockers, main-trigger setup, immutable deployment and rollback.
Prepared configuration is distinguished from an actual push-triggered rollout.

## E34 file extraction

The [TXT/PDF/DOCX format contract](design/file-extraction.md) documents adapter interfaces, chosen dependencies, canonical text and offsets, PDF page numbering, DOCX hierarchical table coordinates, metadata, typed failures and known omissions. It links the extraction milestone to existing registration/chunking/indexing without implementing uploads, lifecycle management, OCR or a schema change.

## E35 application document lifecycle

The [lifecycle design](design/document-lifecycle-and-indexing.md) and [register](decisions/register.md#e35) record immutable version updates, one pending version per document, stale-version rejection, activation and deletion locking, owner-scoped operations, visibility-filtered pagination, and the trust boundary. No migration or HTTP/frontend change was required.

## E36 authenticated document management

See the [document-management design](design/authenticated-document-management.md) for the one-administrator bearer boundary, upload bounds and temporary-file retention, idempotent source identities, indexing batch limits, document routes, and public/shared versus administrator/private question scope.

## E37 inline embedding storage experiment

The [performance checkpoint](validation/hybrid-retrieval-performance.md#e37-inline-embedding-storage-comparison-2026-10-28) compares PLAIN and EXTERNAL on independent disposable PostgreSQL 18.4/pgvector 0.8.5 databases at 50k records, including exact result-signature checks and TOAST/plan measurements. PLAIN improved semantic and hybrid medians, but the write-path and production table-rewrite costs still need isolated measurement. This experiment changes no architectural decision status and authorizes no migration.

## E38 multi-step investigation

See the [multi-step investigation verification](validation/multi-step-investigation-2026-10-28.md) for the bounded live Gemini/local PostgreSQL cases, usage counts, deterministic browser replay, automated suite results, and the first live case's Windows console reporting limitation. This is smoke verification only; multi-step answer quality remains incomplete. E39's paired evaluation stopped before any multi-step live calls.

## E39 answer-quality evaluation

See [answer-quality evaluation](validation/answer-quality-evaluation.md) for the
40-case annotations, conservative correctness and citation metrics, paired-run
contract, full offline test results, and error analysis. The [captured live JSON
and Markdown](validation/answer-quality-live-2026-10-28.md) document one accurate
single-step result followed by a provider failure; the multi-step pair was
stopped and no full live comparison is claimed.

## E41 Task 16 real answer-quality pilot

The [Task 16 report](validation/answer-quality-task16-2026-10-28.md) records
the complete Task 15 retrieval baseline, fixed five-category sample, persistent
database attestation, paired Quick Answer/investigation outcomes, provider
diagnostics, request/token usage and test results. The sample is inconclusive
for comparative answer quality: three investigations stopped at the explicit
search budget, one provider request returned HTTP 503, and one pair's local
report output was lost and not regenerated. No deployment or retrieval tuning
was performed.

## E40 corpus-scale processing

The [corpus-scale record](validation/corpus-scale-2026-10-28.md) verifies 125,760
real source occurrences, 56,680 exact Contents and complete plans through the
application services in disposable PostgreSQL. It separates those inactive
records from the four public searchable source occurrences in the real-Gemini
pilot, records source/restart/replay integrity, compares chunking structurally,
and explains the unresolved full-indexing schedule. The full suite passed 620
Python plus 26 JavaScript tests; ordinary tests called no external AI or Cloud
SQL. No migration, paid service or deployment was introduced.
