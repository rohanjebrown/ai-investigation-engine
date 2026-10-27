# ADR: PostgreSQL as the authoritative relational database

**Date:** 2026-10-25  
**Status:** CONFIRMED by explicit user selection; persistence is not implemented.  
**Decision register:** [U05](register.md#u05), approval [E12](register.md#e12).  
**Alternatives:** PostgreSQL and MySQL using InnoDB.  
**Scope:** Authoritative relational application data. Vector retrieval, including pgvector, is excluded from this comparison and decision.

**Subsequent decisions (2026-10-25):** [E13](register.md#e13) separately approves the [Python persistence stack](python-persistence-stack.md), [pgvector/full-text/custom RRF](pgvector-selection.md), and [conditional Cloud SQL hosting](google-cloud-sql-hosting.md). References below to choices not selected by this ADR describe U05's original scope, not their current register status. PostgreSQL authority and this relational comparison remain unchanged; retrieval is not added retrospectively as a reason for U05.

## Context

The AI Investigation Engine uses a Python backend with domain dataclasses, an application repository port, and infrastructure adapters. The assignment requires data to survive application restarts, database integration, public free-tier deployment, and modular Python with technical documentation. It does not mandate a database product. See the [assignment requirements](register.md#assignment-requirements), particularly pages 6-7 of the supplied assignment.

The following requirements follow from the actual implementation and approved decisions, rather than hypothetical features:

| Area | Implemented or approved now | Persistence work still required |
| --- | --- | --- |
| Documents and versions | UUIDv7 Document/DocumentVersion IDs; versions reference documents; content and spans belong to versions. Registration creates version 1. | Persist relationships and enforce integrity. Explicit cross-revision updates remain unsupported. |
| Content | Immutable exact text with SHA-256 over UTF-8; hash-assisted lookup verifies text equality before reuse. Multiple registrations share Content. | Durable reuse and concurrency-safe uniqueness without changing equality semantics. |
| Provenance | SourceReference separates kind, source_id, revision, record_locator, display_name, and metadata. Structured values use JsonObject/JsonValue. | Store and query heterogeneous provenance while preserving its meaning. |
| Registration | U08 returns the existing document/version for the same source identity, text, and spans. Changed text/spans conflict; descriptions are not overwritten on retries. | Database-enforced identity uniqueness and an atomic registration transaction across concurrent writes. |
| State and recovery | One in-memory repository instance supports sequential idempotency and defensive copies. A09 delays activation until indexing succeeds. | Persistence across restarts, concurrent registration, transaction implementation, and recovery/orchestration. |

The approved [U03 corpus](register.md#u03) is the complete pinned 2Wiki dev split: 125,760 occurrences and 56,680 unique exact-content passages. This demonstrates why separate source registrations must reference shared Content. It is a measured corpus, not a database throughput benchmark or proof of semantic independence. See the [dataset audit](../validation/2wiki-dev-audit.md#9-approved-u03-corpus-and-counting-policy).

Implementation evidence: [Document](../../src/investigation_engine/domain/document.py), [DocumentVersion](../../src/investigation_engine/domain/document_version.py), [Content](../../src/investigation_engine/domain/content.py), [SourceReference](../../src/investigation_engine/domain/provenance.py), [repository port](../../src/investigation_engine/application/ports/document_repository.py), [registration service](../../src/investigation_engine/application/use_cases/register_document.py), and [in-memory repository](../../src/investigation_engine/infrastructure/persistence/in_memory_document_repository.py). No database adapter, driver, ORM, or migrations currently exist in the project configuration.

## Decision

Use **PostgreSQL as the authoritative relational database** for documents, versions, shared Content, provenance, and the application relationships covered by this decision.

The primary reason is architectural fit with relatively little additional complexity: relational integrity, structured JSONB values with whole-value equality/indexing, and transactional constraint enforcement align with our existing registration contract. Mature Python integration and production tooling support maintainability. This is a project-specific judgment, not a claim of universal superiority or higher raw performance.

JSONB is a natural persistence representation for the existing structured provenance. The exact column layout, constraints, indexes, database version, and transaction algorithm still require approval under U06/U07/U14. PostgreSQL remains authoritative if a separate derived retrieval store is selected later.

## Alternatives and comparison

Both PostgreSQL and MySQL/InnoDB are mature production relational systems capable of supporting this application. MySQL is a valid alternative. PostgreSQL is preferred because whole-locator uniqueness can use native JSONB equality without first projecting source-specific locator fields into another identity representation.

### Relational integrity

PostgreSQL provides foreign keys, primary/unique keys, and check constraints. These can enforce ownership and references among documents, versions, and Content. Choosing PostgreSQL does not establish how the active-version pointer or immutable history will be enforced; physical mappings and any cross-row invariants remain U07. [PostgreSQL constraints](https://www.postgresql.org/docs/18/ddl-constraints.html).

MySQL/InnoDB also supports foreign keys and relational constraints. It can model the same relationships; its referential-action and constraint details must be accommodated during schema design. Relational integrity alone does not eliminate MySQL as an option. [MySQL foreign keys](https://dev.mysql.com/doc/refman/8.4/en/create-table-foreign-keys.html).

### Transactions, concurrent writes, and conflicts

Both systems support ACID transactions and concurrent writes. PostgreSQL offers transaction isolation and constraint-backed conflict handling; InnoDB offers transactions, isolation, and locking. Durability in either system depends on suitable configuration and storage. [PostgreSQL isolation](https://www.postgresql.org/docs/18/transaction-iso.html), [InnoDB ACID model](https://dev.mysql.com/doc/refman/8.4/en/mysql-acid.html), [InnoDB locking](https://dev.mysql.com/doc/refman/8.4/en/innodb-locks-set.html).

For this project, a persistent repository must make Content reuse and registration identity checks consistent with committed document/version state. A check followed by an unprotected insert is insufficient under concurrency. Constraints must ultimately arbitrate duplicate identities; the application must distinguish an unchanged retry from conflicting text or spans and return the actual stored pair.

PostgreSQL's ON CONFLICT facilities are available, but choosing an upsert does not by itself implement U08. A blind overwrite would violate the conflict policy. A statement-level atomic outcome also does not define the whole multi-record registration transaction. Transaction boundaries, isolation, conflicting-writer resolution, and database-error retries remain design work. [PostgreSQL INSERT](https://www.postgresql.org/docs/18/sql-insert.html).

Neither engine automatically supplies application idempotency just because transactions are available. No durable or concurrent guarantee has been implemented here.

### Structured JSON data and indexing

| Criterion | PostgreSQL | MySQL/InnoDB |
| --- | --- | --- |
| Storage/querying | JSONB provides structured storage and JSON operators. | Native JSON provides validated structured storage and JSON functions. |
| Provenance search | GIN supports selected containment/key/path queries; expression indexes can target specific paths. | Generated-column or functional indexes can target extracted values; multi-valued indexes serve certain array queries. |
| Whole-locator equality | JSONB has equality and B-tree support, suitable for considering the whole locator in an identity constraint. | JSON equality exists, but JSON columns cannot be indexed directly. Whole-object identity requires another enforcement design. |

The important distinction is equality versus containment. Occurrence identity requires complete locator equality, including array order; containment queries must not stand in for it. GIN search indexes do not enforce uniqueness. PostgreSQL currently supports unique indexes through B-tree. [PostgreSQL JSON types](https://www.postgresql.org/docs/18/datatype-json.html), [unique indexes](https://www.postgresql.org/docs/18/indexes-unique.html), [MySQL JSON](https://dev.mysql.com/doc/refman/8.4/en/json.html), [MySQL index expressions](https://dev.mysql.com/doc/refman/8.4/en/create-index.html).

### Source identity, JSON equality, and nullable revisions

Our identity fields remain **kind, source_id, revision, and record_locator**. Document UUIDs identify registrations; Content hashes identify exact extracted text. Descriptive metadata and labels are not identity. No serialized composite source_id, redundant domain identity column, or dataset-specific domain rule is introduced.

In PostgreSQL, a multi-column uniqueness constraint can include a JSONB locator using whole-value equality. A bounded locator can therefore remain structured without a separate serialized identity field. This is a capability, not an approved schema. Index-entry size limits mean arbitrary large JSON objects cannot be promised as directly indexable identity values; U07 must validate locator sizes and the eventual constraint design. [PostgreSQL unique indexes](https://www.postgresql.org/docs/18/indexes-unique.html), [B-tree index limits](https://www.postgresql.org/docs/18/btree.html).

PostgreSQL JSONB ignores object key order, retains array ordering for equality, distinguishes boolean and numeric values, and compares numbers numerically; fractional trailing zeroes do not distinguish 1 from 1.0. U08's private in-memory key follows that numeric intent using decimal values. Exact string semantics, serialization precision, numeric range, encoding restrictions, and collation must be checked when implementing persistence; compatibility cannot be inferred solely from the JSONB type name. [PostgreSQL JSON types](https://www.postgresql.org/docs/18/datatype-json.html).

MySQL also compares JSON objects independently of key order and arrays by corresponding positions, with type-specific numeric/boolean rules. The difficulty here is indexing generic complete locators for uniqueness, not an inability to compare JSON. Source-specific extracted scalar keys can be indexed, but do not automatically cover every heterogeneous locator. Functional unique indexes are available with expression/type restrictions. No generated identity projection or substitute schema is selected by this ADR. [MySQL JSON equality](https://dev.mysql.com/doc/refman/8.4/en/json.html), [functional indexes](https://dev.mysql.com/doc/refman/8.4/en/create-index.html).

Optional revision needs deliberate handling in either database. SQL NULL is not the same as a JSON null inside record_locator. U08 treats two absent revisions as the same identity when the other fields match; the default PostgreSQL unique constraint treats nulls as distinct. **NULLS NOT DISTINCT**, introduced in PostgreSQL 15, can support the required uniqueness treatment. This feature's availability is documented; neither its selection nor a minimum server version is approved here. [PostgreSQL constraints](https://www.postgresql.org/docs/18/ddl-constraints.html), [PostgreSQL 15 release notes](https://www.postgresql.org/docs/15/release-15.html).

MySQL unique indexes also allow multiple NULL values, so ordinary uniqueness would need additional treatment for absent revisions. Null-safe query comparisons alone do not change unique-index behavior. No sentinel revision value, extra identity column, or NULL workaround is introduced. [MySQL unique indexes and NULL](https://dev.mysql.com/doc/refman/8.4/en/create-index.html).

These capabilities make PostgreSQL a more direct fit for the approved generic identity, subject to U07's final constraints. MySQL can satisfy the contract with a carefully designed alternative, generally involving more work around JSON identity and nullable revisions.

### Query capabilities and full-text search

Both support joins, aggregations, relational filtering, and complex queries. The expected application queries include document/version relationships, Content reuse counts, and provenance filtering; no actual database query performance has been measured.

Both also offer full-text search: PostgreSQL provides linguistic text-search facilities and relevance ranking; MySQL provides FULLTEXT indexes and search functions. This ADR selects neither keyword engine nor ranking behavior. Full-text search alone does not satisfy the separate hybrid retrieval requirements. [PostgreSQL text search](https://www.postgresql.org/docs/18/textsearch-intro.html), [MySQL full-text search](https://dev.mysql.com/doc/refman/8.4/en/fulltext-search.html).

### Python integration and migrations

Both fit the repository boundary. SQLAlchemy documents PostgreSQL drivers including Psycopg and MySQL drivers including PyMySQL/mysqlclient. Direct SQL and SQLAlchemy-based mapping are viable approaches; migration tooling can be evaluated for either. Specific package versions and support for the project's Python 3.14 runtime must be verified during U06. None is installed or selected by this decision. [SQLAlchemy PostgreSQL dialect](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html), [SQLAlchemy MySQL dialect](https://docs.sqlalchemy.org/en/20/dialects/mysql.html).

### Operations, scaling, and performance

Either database adds deployment, credentials, upgrades, monitoring, capacity management, connection management, backup, and tested restore procedures. PostgreSQL documents dump and physical/continuous-archive recovery approaches; MySQL documents logical backup and point-in-time recovery. Available service features and costs depend on the eventual provider. [PostgreSQL backup](https://www.postgresql.org/docs/18/backup.html), [MySQL backup](https://dev.mysql.com/doc/refman/8.4/en/backup-and-recovery.html).

Concurrent writes can contend in either engine. Scaling must be evaluated against actual registration transactions, provenance/index sizes, hardware, and service limits. Neither read replicas nor a larger machine automatically resolve write contention or idempotency semantics.

There is no project benchmark comparing these engines, and PostgreSQL is not claimed to be universally faster. JSON indexes add storage and write costs; foreign keys, transaction duration, indexing choices, query plans, configuration, and workload shape affect performance. Capacity measurement on the selected corpus remains necessary; counts alone do not predict memory, throughput, or latency.

## Consequences and architectural boundaries

Positive consequences:

- Relational integrity and native structured-locator equality align with the existing document/version/Content and U08 contracts.
- Flexible provenance can coexist with explicit universal source fields, reducing pressure for source-specific domain columns or duplicate identity representations.
- A mature Python and operational ecosystem supports a maintainable infrastructure adapter.

Costs and responsibilities:

- PostgreSQL requires deployment and administration, whether self-managed or delegated to a hosting service. No free-tier capacity or provider promise is made here.
- JSONB validates JSON representation, not our source-specific semantics. Adapters still validate locators; database constraints must ultimately enforce critical stored invariants and uniqueness.
- Nullable revisions, exact comparison semantics, locator sizes, Content uniqueness, foreign keys, and active-version integrity require physical design and verification.
- Correct concurrent registration still needs an approved transactional implementation and failure handling. Source updates and metadata changes remain separate future operations.
- A second retrieval database would introduce synchronization, rebuild, and reconciliation responsibilities; no distributed transaction or recovery strategy is assumed.

Domain models keep standard Python types and dataclasses. JSONB belongs in persistence mapping, not in the domain. Each field retains its established responsibility. Source identity, document identity, and Content identity remain separate; independent registrations preserve provenance while sharing identical text. The persistent adapter must respect the existing repository return values and conflict behavior.

## Scope and decisions still required

This confirms only PostgreSQL as the authoritative relational database. It selects **no ORM/driver, physical schema, migration framework, hosting provider, pooling strategy, transaction implementation, batch recovery strategy, vector database, or embedding storage strategy**. It also selects no database server version or keyword retrieval implementation. No database schema or application changes accompany this ADR.

At U05 approval, U06/U12/U13/U18 were unresolved. E13 subsequently selected their scoped technologies and intended host in the linked ADRs above. The next concrete work is **U07** (physical model, comparisons, nullable-revision uniqueness, and constraints), alongside U06's remaining mappings/session implementation and **U14** transaction/concurrency/recovery mechanics. Retention/access remains U10, retrieval configuration U11/U12/U13, hosting feasibility and application deployment U18, and evaluation/test sequencing U17. These later approvals do not change U05's original decision scope.

## Verification record

Reviewed the current source files linked above and U03/U05/U08, and rechecked assignment pages 6-7. Database capability claims were checked against the linked official PostgreSQL, MySQL, and SQLAlchemy documentation on 2026-10-25. PostgreSQL 18 and MySQL 8.4 documentation provide comparison references, not selected deployment versions. No database was started, schema created, concurrency benchmark run, driver installed, or persistent guarantee tested.
