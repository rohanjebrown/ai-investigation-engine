# ADR: SQLAlchemy ORM, psycopg 3, async I/O, and Alembic

**Date:** 2026-10-25  
**Status:** CONFIRMED stack; U07 mappings/migrations and scoped U14 async registration implemented. Remaining lifecycle and deployment decisions are open.  
**Decision owner:** [U06](register.md#u06), approval [E13](register.md#e13).  
**Related:** [U05 PostgreSQL](postgresql-vs-mysql.md), [U08 registration behavior](register.md#u08), [retrieval](pgvector-selection.md), [intended database hosting](google-cloud-sql-hosting.md).

## Context and requirements

The engine needs persistent documents, versions, shared exact Content, structured provenance, and idempotent registration. The domain uses ordinary dataclasses and generic JSON-compatible values. Infrastructure implements a repository port; the application returns the stored domain Document/DocumentVersion pair.

At the original E13 selection, registration was synchronous and PostgreSQL persistence was absent. U07/E15 subsequently implemented the mappings and Alembic migration. [E16](register.md#e16) now implements the async [port](../../src/investigation_engine/application/ports/document_repository.py), [use case](../../src/investigation_engine/application/use_cases/register_document.py), [in-memory adapter](../../src/investigation_engine/infrastructure/persistence/in_memory_document_repository.py), and [PostgreSQL repository](../../src/investigation_engine/infrastructure/persistence/postgresql_document_repository.py). TXT/2Wiki extraction and domain computation remain synchronous. The in-memory adapter remains instance-local; PostgreSQL registration uses isolated transactions and existing constraints. No API backend or lifecycle workflow exists.

The planned investigation workload includes database/retrieval I/O and external LLM requests. The user's rationale describes a FastAPI-based backend; this ADR records the async persistence direction compatible with that plan, not an implemented API or a complete U16 endpoint/UI decision. Assignment page 7 requires modular Python, responsibility separation, database integration, and maintainability, without requiring a particular ORM or driver.

## Decision and responsibilities

| Selected component | Responsibility |
| --- | --- |
| SQLAlchemy 2.x ORM | Infrastructure object-relational mapping, relationships, query construction, and session-based persistence. |
| psycopg 3 | PostgreSQL driver, using asynchronous operations for application database I/O. |
| SQLAlchemy AsyncSession | Async application access to the ORM and its transaction/session facilities. |
| Alembic | Version-controlled, reviewed schema migrations using SQLAlchemy integration. |
| Execution convention | Async interfaces for I/O; synchronous pure computation; CPU-intensive work managed separately where needed. |

E15 installed the uv-managed SQLAlchemy/psycopg/Alembic dependencies and verified Python 3.14.9 compatibility. E16 implements the registration I/O contracts and transaction boundary below. The existing lockfile records exact versions; this task adds no dependencies. Production pool/deadline settings and deployment remain separate decisions.

## Alternatives considered

| Choice | Benefits | Costs and decision |
| --- | --- | --- |
| SQLAlchemy ORM | Explicit persistence entities, relationships, session/unit-of-work facilities, and query composition. | Adds mapping and loading complexity. Selected for the existing related document/version/Content model. |
| SQLAlchemy Core | Explicit SQL expression construction, useful for focused relational/bulk/conflict operations. | More manual object translation. Available alongside the ORM rather than excluded. |
| Raw SQL | Direct statement control and little ORM machinery. | More manual mapping and query maintenance across related persistence objects. Remains available for justified operations, not the default stack. |
| psycopg 3 | Async and sync APIs, SQLAlchemy integration, configurable preparation, and pooling flexibility. | Settings and provider/pool compatibility still require validation. Selected for operational flexibility. |
| asyncpg | Mature PostgreSQL-specific asyncio driver with SQLAlchemy support. | Another valid async choice; not rejected for deficient performance. No comparative benchmark was run. |
| Synchronous access | Simpler control flow and debugging for sequential workloads. | Requires care to avoid blocking an async application's event loop. Not selected for application database I/O. |
| Async access | Can overlap waiting for database, retrieval, and LLM I/O. | Await propagation, loading rules, cancellation, and session ownership add complexity. Selected for the intended application workload. |
| Alembic | Versioned upgrade/downgrade scripts and SQLAlchemy metadata integration. | Generated changes require review; operational migration procedures still need design. Selected. |
| Manual SQL migrations | Transparent database-specific SQL and full control. | Ordering, history, repeatable application, and drift need a maintained process. Not selected as the migration framework; Alembic revisions may still use explicit SQL. |

SQLAlchemy documents both [psycopg and asyncpg dialects](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html). asyncpg's own [documentation](https://magicstack.github.io/asyncpg/current/) describes its PostgreSQL/asyncio interface. No driver is claimed universally faster.

## Technical justification

### ORM and domain independence

Documents own versions; versions reference shared Content and provenance. Infrastructure ORM entities can express these relations, while repositories translate to and from existing domain values. SQLAlchemy supports mapping classes to relational structures; mappings do not replace database constraints. [ORM mapping overview](https://docs.sqlalchemy.org/en/20/orm/mapping_styles.html).

The ORM provides session-based change tracking, queries, flushes, and transaction participation. These facilities can organize persistence, but cannot infer the application's idempotency semantics or correct ownership of a multi-step operation. [Session basics](https://docs.sqlalchemy.org/en/20/orm/session_basics.html).

Keep domain models free of SQLAlchemy base classes, annotations, sessions, lazy relationships, and database-specific JSON/vector types. ORM models and private row/domain conversion belong in infrastructure; application interfaces exchange domain objects. Document now has the separately approved required owner_id field; no additional entity or generalized mapping framework is introduced.

ORM costs include another representation of the data, relationship-loading choices, implicit SQL, possible N+1 queries, and memory overhead. Inspect emitted SQL and query plans; use Core where explicit SQL is clearer. ORM convenience is not a reason to bypass U08 conflicts, omit exact Content equality checks, or overwrite provenance on retries.

### Asynchronous execution and limited hosting resources

Async supports overlap among investigation I/O waits instead of dedicating execution capacity to each wait. It does not accelerate one SQL query, embedding computation, parsing, hashing, or other CPU work. More concurrent requests also do not create database CPU/RAM or unlimited connections.

SQLAlchemy's async ORM requires explicit care around lazy/expired-attribute I/O. AsyncSession is mutable transaction state and must not be shared between concurrently running tasks; each task needs isolated session ownership. [SQLAlchemy asyncio guidance](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html).

The approved convention keeps I/O interfaces asynchronous and pure transformations synchronous. Extraction and ranking computation need not become async merely because callers are async. Any blocking file/parsing work or heavy CPU computation must be managed so it does not monopolize the event loop; no worker/process framework is selected here.

For limited backend CPU/RAM, operational pool/concurrency settings still require review. Do not keep a transaction or checked-out connection open while awaiting an external LLM or embedding response. E16 selects registration-owned isolated sessions/transactions and cleanup/cancellation behavior. Pool sizes, numeric deadlines, worker counts, and backpressure remain unresolved; this implementation sets none and adds no request-scoped FastAPI dependency.

### psycopg 3 and operational flexibility

psycopg supplies native async connections/cursors as well as synchronous APIs useful for operational tooling. Multiple operations sharing one connection do not become independent parallel database transactions. [psycopg concurrency](https://www.psycopg.org/psycopg3/docs/advanced/async.html).

Prepared-statement behavior is configurable, including disabling automatic preparation. Proxy/pooler support depends on versions and settings; psycopg is not automatically compatible with every transaction-pooling configuration. [Prepared statements](https://www.psycopg.org/psycopg3/docs/advanced/prepare.html).

Psycopg also offers sync/async pooling facilities; selecting the driver does not select these over SQLAlchemy-managed pooling or a provider pooler. Avoid stacking pools without an approved ownership design. [psycopg pools](https://www.psycopg.org/psycopg3/docs/advanced/pool.html).

This flexibility, together with SQLAlchemy support, motivates choosing psycopg over asyncpg here. No latency/throughput superiority is asserted, and the actual Python/libpq/proxy combination remains to be validated.

### Alembic migrations

Alembic manages version-controlled schema revision scripts through SQLAlchemy, supporting reproducible upgrade paths and downgrades where valid. A downgrade may be lossy or unsupported for a particular change; migration history is not a data backup. [Alembic tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html).

Autogeneration produces candidate changes that require developer review; it does not guarantee correct renames, data migrations, extension handling, constraints, or operational safety. [Autogeneration limitations](https://alembic.sqlalchemy.org/en/latest/autogenerate.html). U07's reviewed initial migration and environment now exist; E16 does not alter them or add automatic deployment.

## Existing implementation compatibility and consequences

E16 converts the existing repository port, registration use case, and in-memory register to async consistently. Use await for registration; do not wrap extraction or domain construction in unnecessary coroutines. No other executable registration caller existed in the checkout. U08 return values, conflict rules, Content reuse, and copy isolation remain intact. Database constraints and the algorithm below establish the PostgreSQL registration guarantees, not awaitability alone.

U07 enforces document/version integrity, shared Content identity, structured locator equality, and absent-revision uniqueness. The required Document owner_id is E16's explicit ownership-contract approval, not a mechanical ORM conversion. AsyncSession and an ORM identity map do not replace database-enforced uniqueness.

## Approved registration execution under E16

Bootstrap owns one process-level AsyncEngine and async_sessionmaker. PostgreSQLDocumentRepository receives the factory, creates an isolated session per attempt, explicitly establishes READ COMMITTED before queries, and commits before returning domain snapshots. Separate outer session and inner transaction context managers ensure closure even if transaction completion raises. No custom Unit of Work, transaction replay, lock protocol, or application session manager is added.

Registration snapshots mutable inputs before its first database await, then looks up the complete occurrence identity (including deleted registrations) using JSONB equality and null-safe revision comparison. Stored owner compatibility is checked before loading Content or reporting payload conflicts. Identical retries return stored values; text or typed span differences conflict; descriptive differences do not write updates.

For new occurrences, a targeted INSERT ON CONFLICT ON CONSTRAINT pk_contents DO NOTHING either inserts Content or is followed by a separate SELECT and exact-text check. Documents and version 1 are then inserted inactive. A failure on SQLSTATE 23505 plus uq_document_versions_source_occurrence rolls back all candidate writes and closes that session before one fresh-session winner lookup. It never replays insertion. Other constraints are not mistaken for occurrence retries. No unused Content or candidate Document is committed on the losing path.

RegistrationConflict and ContentHashCollision retain ValueError compatibility; generic PermissionError handles owner mismatch. RepositoryError sanitizes unexpected database failures while preserving internal causes. Returned values are independent dataclasses; conversion reconstitutes enum/tuple span values and validates through existing domain constructors. Nested provenance is copied. Content sharing across sessions means one database row, not globally interned Python objects.

Cancellation propagates after normal cleanup; SQLAlchemy/psycopg handle invalidated connections. No general automatic retry or entire-operation shielding is added. A failure around COMMIT may have an uncertain outcome; do not promise rollback merely because no result reached the caller. A subsequent identical source identity resolves a committed registration. Numeric timeouts and pool configuration remain open.

Ordinary dependency injection in a trusted composition root can use this pattern inside its process-level async entry point:

```python
engine = create_async_engine(configured_database_url)
sessions = async_sessionmaker(engine)
registration = RegisterDocument(PostgreSQLDocumentRepository(sessions))
try:
    stored = await registration.execute(extracted, owner_id=trusted_owner_id)
finally:
    await engine.dispose()
```

The imports come from SQLAlchemy and the existing application/infrastructure modules. This illustrates engine ownership; it is not a public endpoint or a pool/deadline configuration. Windows psycopg execution needs a selector loop as documented in the migration environment. No server bootstrap exists yet. An eventual authenticated upload handler must derive trusted_owner_id from authenticated context; only trusted system ingestion explicitly passes None. Missing ownership cannot default to shared, and explicit input alone does not establish authentication.

## Risks, boundaries, and unresolved work

The stack adds dependency compatibility, two representations of data, async debugging, relationship-loading discipline, and migration review responsibilities. Transaction and connection leaks can exhaust limited resources. Provider-specific pool/proxy behavior, prepared statements, extension versions, and Python 3.14 support require concrete verification before implementation/deployment.

U07 and scoped U14 registration are implemented, but indexing, activation/recovery, cross-revision updates, deletion/retrieval races, production pool/deadline settings, and deployment are not selected by E16. It selects no CPU worker strategy or backend/frontend host. The authentication/API/UI contract remains U10/U16; performance/evaluation and software-test return remain U17. There is no complete access-control system or indexing lifecycle.

## Verification record

At E13, official stack capability references were reviewed without implementation. E15 verified U07 against actual PostgreSQL 17.2. E16's temporary checks exercised real async registration, independent-session races and rollback, ownership, deleted retries, JSONB identity, cancellation, and pinned TXT/2Wiki adapter integration. Detailed executed checks and controlled fault-injection limits are recorded in [U14](register.md#u14) and [observed evidence](register.md#observed-evidence). No permanent automated test files were added; A10 remains in effect.
