# ADR: Physical PostgreSQL document registration schema

**E21 extension:** The [embedding storage ADR](embedding-storage.md) records the subsequent five-table derived-schema approval and 0002 migration. The three authoritative tables and initial migration described here are unchanged. Cloud execution and remaining U14 lifecycle still require separate approval.

**Date:** 2026-10-25  
**Status:** CONFIRMED for the approved U07 schema and ORM/migration foundation.  
**Approval:** [E15](register.md#e15); [U07](register.md#u07), scoped ownership/deletion [U10](register.md#u10), retry semantics [U08](register.md#u08).  
**Related:** [PostgreSQL selection](postgresql-vs-mysql.md), [Python persistence stack](python-persistence-stack.md).

## Context

The existing domain separates logical Documents, immutable DocumentVersions, exact reusable Content, and version-specific provenance/spans. At U07 approval only sequential in-memory registration existed. E16 now implements the scoped [U14](register.md#u14) PostgreSQL registration repository and required ownership propagation on this unchanged schema. This decision maps data responsibilities without turning domain dataclasses into ORM entities.

The approved initial corpus has 125,760 occurrences and 56,680 distinct exact texts. Separate registrations can therefore reference shared text while preserving their source identity and differing sentence mappings. Ownership and soft deletion control document/version eligibility; Content existence does not establish access rights.

## Decision and alternatives

Use exactly three application tables. Alembic also creates its standard migration revision tracking table; this is tooling state, not an additional domain entity.

| Table | Columns | Responsibility |
| --- | --- | --- |
| contents | sha256 BYTEA primary key; text TEXT, both required | Shared immutable exact extracted text. |
| documents | id UUID primary key; nullable owner_id TEXT, title TEXT, current_version_id UUID, deleted_at TIMESTAMPTZ; required created_at TIMESTAMPTZ | Logical identity, document-level ownership, active-version pointer, and deletion timestamp. |
| document_versions | id UUID primary key; required document_id UUID, version_number INTEGER, content_sha256 BYTEA, created_at TIMESTAMPTZ, kind TEXT, source_id TEXT; nullable revision TEXT and display_name TEXT; required record_locator/metadata/structure/source_spans JSONB | Version-specific Content reference, provenance snapshot, and ordered mappings. |

UUIDv7 and timestamps are supplied by existing domain/application values; storage does not invent different IDs or creation times. UUID columns do not independently enforce UUID version 7. No surrogate Content ID, user table, visibility field, source table, or span table is added.

### Content identity

Use the 32-byte SHA-256 digest as the primary key, instead of a surrogate plus a second unique lookup key. This matches the current Content representation and avoids another identity/index. The domain keeps its hexadecimal string; infrastructure storage uses bytes.

Check both octet length and digest consistency with `pg_catalog.sha256(pg_catalog.convert_to(text, 'UTF8'))`. PostgreSQL provides the required [binary hashing function](https://www.postgresql.org/docs/18/functions-binarystring.html); no extra database extension is needed.

A matching digest is still only a candidate for exact Content reuse. The future repository must load and compare exact text and report a collision if it differs. Database primary-key rejection alone is not that application collision classification. Neither normalized nor semantic merging is permitted.

### Provenance and spans

Flatten SourceReference's universal fields onto document_versions; preserve their meanings. record_locator and metadata are JSONB objects with empty-object defaults. structure and source_spans are ordered JSONB arrays with empty-array defaults. Database shape checks prevent the wrong top-level containers; nested typed reconstruction, valid offsets, and locator validation belong at the future persistence boundary.

A separate provenance entity would introduce another identity/join without an independent operation. Independent span rows would add ordering, joins, and identifiers without a currently implemented independent span query. Arrays retain the existing version-specific value-object semantics. A change in source mappings under an existing occurrence must still conflict under U08, even when the text matches.

The physical metadata column is mapped as `DocumentVersionRow.source_metadata` to avoid SQLAlchemy's reserved declarative metadata attribute. JSONB columns use SQL NULL for a top-level Python None, so it cannot bypass the required object/array shapes. JSON null inside a valid object/array remains a distinct value.

### Version ownership and active version

Foreign keys enforce each version's document and Content references. A positive-number check and unique (document_id, version_number) constraint enforce version numbering uniqueness. They do not allocate future numbers or guarantee gapless history.

A composite foreign key from documents(id, current_version_id) to document_versions(document_id, id) prevents selecting another document's version. Its supporting unique (document_id, id) constraint justifies an additional index despite id already being globally unique. A simple current_version_id foreign key would not enforce ownership; a trigger would add unnecessary procedural machinery.

The migration creates documents without the circular FK, creates document_versions, then adds that named FK. It reverses this order for downgrade. ORM metadata marks the circular FK for separate creation. The current-version relationship is read-only/viewonly; it does not synchronize activation through relationship assignment. A09 still requires a future workflow to activate only successfully indexed versions. No readiness field, trigger, or activation operation is added.

### Occurrence identity

Enforce global uniqueness on kind, source_id, revision, and record_locator using UNIQUE NULLS NOT DISTINCT. This requires PostgreSQL 15 or newer. No owner-scoped key, sentinel revision, derived fingerprint, or serialized composite source ID is introduced. The constraint remains in force after soft deletion.

Identity text columns use the C collation for exact comparisons; owner references also use exact text comparison. JSONB provides whole-object structural equality, not containment. Numeric equality must preserve the approved 1/1.0 equivalence and boolean distinction. The actual database's default comparison semantics, serializer precision, Unicode representation, and locator size limits must remain compatible with U08. [PostgreSQL JSON types](https://www.postgresql.org/docs/18/datatype-json.html), [unique indexes](https://www.postgresql.org/docs/18/indexes-unique.html).

No arbitrary locator length limit is invented. Very large future identities still need a reviewed acceptance envelope because B-tree entries are bounded; current 2Wiki locators are compact. No GIN index is created.

### Ownership and deletion

Nullable documents.owner_id is the approved representation: NULL identifies shared system-managed documents; a non-NULL value identifies the private owner through a stable principal identifier. There is no users table or owner foreign key. E16 requires explicit trusted ownership in Document and registration and checks owner compatibility on retries; it provides no authentication/public upload workflow. An eventual upload handler must derive ownership from authenticated context. A nullable column alone cannot enforce that trust boundary.

Keep deleted_at and current_version_id on soft deletion. Retain versions, provenance, spans, and Content. Ordinary listings, both retrieval branches, and investigation evidence must exclude deleted documents and inaccessible private documents through their associated versions. These queries and authorization checks are not implemented by this schema. Shared Content remains available through other eligible registrations.

An authorized identical retry of a deleted occurrence returns its existing IDs and deleted state, without restoring it. Changed text/spans still conflict. E16 implements these registration semantics, checking owner compatibility before exposing Content/conflicts. Authentication and ordinary retrieval eligibility are not implemented. Soft-deletion endpoints and coordination with concurrent lifecycle changes remain outside registration.

## Constraints and indexes

The three primary keys, three version unique constraints (number, owned ID, occurrence), and one nonunique version Content-reference index produce seven application indexes. Do not add a duplicate document_id index: the composite version keys already provide a document-leading access path. The owned-ID unique key supports active-version referential integrity. The Content-reference index supports resolving source versions that share Content.

The owner/creation listing index is explicitly not approved; revisit actual listing queries/plans. No JSONB GIN, vector, full-text, tenant, or speculative lifecycle indexes are added.

Foreign keys have ordinary non-cascading behavior. ORM relationships do not delete-orphan or delete shared entities, and do not null child references when an owner is removed. This protects relationships but is not a database permission scheme against arbitrary SQL deletion. No physical deletion operation exists. Historical immutability remains an application contract; no immutability triggers or permission framework are added.

## Implementation and migration use

- [ORM mappings](../../src/investigation_engine/infrastructure/persistence/models.py) use a standard SQLAlchemy DeclarativeBase, UUID/bytea/JSONB mappings, explicit relationships, and explicit loading (`lazy="raise"`) to avoid accidental I/O in future async use.
- [Initial migration](../../migrations/versions/0001_document_registration.py) is explicit and independently reproducible; it does not import current ORM models or call create_all. Its frozen definitions were reviewed against the mappings.
- [Alembic environment](../../migrations/env.py) uses a short-lived async psycopg connection with NullPool for the migration process only. This does not select an application pooling or session policy. E19 replaces the earlier DATABASE_URL-only configuration with [central infrastructure settings](../../src/investigation_engine/infrastructure/config.py): required DB_HOST, DB_PORT, DB_NAME, DB_USER and DB_PASSWORD, plus optional GEMINI_API_KEY. E20 supersedes that loading mechanism: a shared frozen settings instance automatically loads working-directory .env through python-dotenv with process-environment precedence and literal values. Alembic delays importing settings until online operation. SQLAlchemy URL.create preserves passwords without manual URL interpolation. Password/API-key fields are excluded from the settings representation, and Alembic hides SQL parameters.
- The approved local connection is 127.0.0.1:15432 through the existing Cloud SQL Auth Proxy, with sslmode=disable only for this guarded endpoint. The proxy secures its remote tunnel; settings do not start it, configure Google Cloud, or fall back to the local PostgreSQL installation on 5432. Other connection paths require a separately reviewed transport configuration. The approved python-dotenv dependency and import-time settings snapshot add no engine/session abstraction or embedding integration.
- On Windows, psycopg async connections need a selector event loop. The migration uses Python 3.14's loop_factory rather than changing the global event-loop policy. [psycopg Windows guidance](https://www.psycopg.org/psycopg3/docs/advanced/async.html).

Offline SQL generation:

```powershell
uv run alembic upgrade head --sql
```

Online revision inspection through the already running Cloud SQL Auth Proxy:

```powershell
uv run alembic current
```

The configuration change does not authorize upgrade/downgrade execution. The initial downgrade drops the application tables; it is a schema reversal, not soft deletion or an application retention operation. No Cloud SQL provisioning or hosting configuration is included. The prior read-only cloud inspection found PostgreSQL 18.6, pgvector 0.8.5 in public, and no application tables or alembic_version table; the initial schema is not currently installed/tracked there.

## Boundaries and subsequent decisions

The approved schema remains unchanged. E16 separately implements async registration in both repositories, required trusted Document ownership, and private typed row/domain translation. PostgreSQLDocumentRepository receives a standard async_sessionmaker; bootstrap owns engine/factory creation and disposal. No authentication, public API, listing, deletion endpoint, restoration, or ownership transfer is implemented.

U14/E16 settles repository-owned registration transactions/sessions, READ COMMITTED, exact Content reuse, full rollback and one winner-resolution read, application errors, cancellation cleanup, and no general automatic retries. Indexing/activation/recovery, cross-revision updates, lifecycle races, numeric deadlines, and pool settings remain unresolved. Authentication/principal source and remaining retention/historical-access rules stay U10/U16. U11 must settle chunking, model, dimensions, and derived-data reuse before retrieval tables or indexes are added.

## Verification

Verification results are recorded with the implementation evidence in [U07](register.md#u07). Mapper configuration, migration SQL, actual database constraints, JSON semantics, and migration/schema parity must be distinguished from application registration or concurrency guarantees. Focused checks are transient; A10 still defers committed automated test files. No create_all-based database verification substitutes for running Alembic.
