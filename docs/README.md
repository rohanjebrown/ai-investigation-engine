# AI Investigation Engine — Architecture documentation

**Status:** Confirmed baseline and unresolved choices reconciled through 25 October 2026; technology approvals are distinct from implementation.  
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
- [Deterministic chunking](decisions/deterministic-chunking.md): E17/E18's whole-passage, fixed-token and supplied-sentence configurations, exact ranges and transient version memberships; experimental file-loaded tokenizer, Unicode behavior and scoped verification. Production embeddings/derived schema/indexing remain unresolved.

Each ADR now has a reconciliation note. Historical reasoning is retained; the register identifies the exact approval and open details.

## Implementation and assignment evidence

The register separates [observed implementation and audit evidence](decisions/register.md#observed-evidence) from [verified assignment requirements](decisions/register.md#assignment-requirements). Domain/TXT/2Wiki adapters, async in-memory/PostgreSQL registration, U07's ORM/migration foundation and scoped deterministic chunk generation exist. Temporary chunk verification and real-database registration checks are recorded separately from schema verification. Embeddings, derived persistence, retrieval, investigation, authentication, API/UI, and deployment remain unimplemented.

[A10](decisions/register.md#a10) defers immediate automated software tests only. The assignment still requires automated software tests and a separate automated evaluation system with at least 40 questions. [U17](decisions/register.md#u17) keeps the test return milestone and evaluation details open.

Authoritative document/source history and derived search data have separate responsibilities; no physical database, hosting provider, model, or schema is selected by an example. Benchmark answers and gold evidence remain outside the searchable evidence corpus.

## Documentation scope

[A11](decisions/register.md#a11) approves the central register alongside ADRs. Additional requirements, ingestion, evaluation, and roadmap document organization remains [N01](decisions/register.md#n01); no new hierarchy is implicitly approved.

Record new approvals in the register, update affected ADRs without erasing history, and keep measurement/configuration evidence distinguishable from architectural intent. Do not label conceptual walkthroughs or transient smoke checks as completed automated acceptance gates.
