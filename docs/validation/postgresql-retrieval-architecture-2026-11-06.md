# PostgreSQL retrieval architecture investigation — 6 November 2026

Scope: Wisigard and Princess Alexandra, cached query vectors, shared-only scope, 20 candidates per channel, RRF k=60, HNSW ef=400/candidates=400/iterative off, existing BM25 index. No Gemini/Voyage requests, broad evaluation, deployment, commits/pushes, staging data writes or permanent database settings changes. The runtime change is limited to batching two existing transaction-local HNSW settings into one statement. BM25 SQL, HNSW discovery, eligibility, RRF, reranking, evidence selection, prompts and Investigation orchestration are unchanged.

## A. Current architecture

Inspected the actual repository, schema, migrations, activation/indexing paths, application composition and retrieval callers. Baseline source commit: `7be08592693a1f4069178bf89ee2da46647780b6`. The staging database is `investigation_stage`, PostgreSQL 18.6, pgvector 0.8.5, pg_textsearch 1.3.1, migration `0008_chunk_embeddings_hnsw`. It contains 125,761 active documents/readiness markers and 57,901 canonical searchable ranges; the selected shared/complete profile admits **57,899** representatives. The disposable test image has pg_textsearch 1.5.1; staging performance conclusions use the installed 1.3.1, while isolated mutation tests validate application/schema behavior on the existing test image.

### Actual execution flow

| Step | Execution and boundary |
| --- | --- |
| 1. Query | Quick Answer may split two independent interrogative clauses; Investigation supplies each active tool-search query. Both use `SearchEvidence.execute`. |
| 2. Query vector | Validate question/scope/profile, await one query embedding, validate the storage vector. Experiments substitute the already cached vector; no provider is instantiated. |
| 3. Connection | SQLAlchemy opens one repository-owned async session/transaction and acquires a pooled connection. This is distinct from SQL execution and may initialize/check a connection. |
| 4. Isolation | Ordinary lexical/exact reads use READ COMMITTED, with eligibility and hydration in one statement. HNSW/hybrid retries use REPEATABLE READ so all attempts see the same snapshot. |
| 5. HNSW settings | Before each configured ANN attempt, set `hnsw.ef_search` and `hnsw.iterative_scan` using transaction-local `set_config(..., true)`. Originally two SELECTs; now one SELECT containing both calls. Values, retry loop and isolation are unchanged. |
| 6. Semantic discovery | `hnsw_candidates AS MATERIALIZED` orders compatible shared vectors by cosine distance and limits to the configured ANN candidate budget. This remains bounded ANN, not exact full-corpus nearest-neighbor search. |
| 7. Completeness | Semantic eligibility is restricted to candidate **contents**, while retaining every sibling range of each selected plan. Lexical eligibility reconstructs the full selected corpus. Counts, ordinals, geometry/coverage, blankness and complete compatible embedding coverage are checked. |
| 8. Visibility | Require current version belonging to the document, nondeleted document and shared-or-trusted-owner visibility. Require exactly one equivalent serving plan. These predicates apply before eligible result limits. |
| 9. Compatibility/diversity | Match every configured embedding attribute; select the lowest equivalent embedding ID per range. Choose the lowest authorized complete document/version occurrence for each exact content-hash/start/end. |
| 10. Lexical ranking | `_bm25_statement` joins all eligible representatives to canonical `searchable_chunks`, evaluates negative BM25 scores, selects the score/hash/range top-K, then rejoins the representative CTE and orders by score/document/version/range. The lexical branch is independent of HNSW candidates. |
| 11. Hydration | `_hybrid_statement` UNION ALLs semantic and lexical candidates, then `_hydrate` joins only selected hits to canonical Content, document title and that occurrence's version/source metadata/spans. Python converts these rows into validated `Evidence` values. |
| 12. Fusion/selection | Application RRF deduplicates source-version/range identities and uses unchanged channel ranks/formula. Existing optional reranking remains after fusion and before the final limit. Quick Answer/Investigation retain their existing intact-passage context admission, stable citation IDs and provenance handling. |

The many named CTEs are **parts of one retrieval SQL statement**, not independent application round trips. Hybrid is ordinary UNION ALL/Append inside PostgreSQL, followed by Python RRF; it does not issue concurrent semantic/BM25 application queries. For an HNSW attempt there are now two explicit SELECT operations: one settings statement and one retrieval statement, plus driver/transaction/pool protocol work. Previously there were three. Lexical-only retrieval has one explicit retrieval SELECT. Additional ANN attempts repeat settings and the hybrid retrieval statement, including its lexical branch; current settings have one 400-candidate attempt.

This design keeps authorization, plan validation, representative selection and provenance hydration in a consistent statement/snapshot; it also avoids N+1 hydration queries and transferring vectors/corpus text into Python. Candidate-content scoping prevents HNSW from reconstructing unrelated plans without dropping noncandidate sibling checks. In exact semantic hybrid mode, the code already reuses the full lexical eligibility CTE; HNSW gets a separately scoped CTE because its candidate discovery differs. PostgreSQL CTE materialization affects physical execution but does not turn CTEs into client requests. See [PostgreSQL WITH/materialization](https://www.postgresql.org/docs/18/queries-with.html).

## B. Confirmed bottlenecks

Fresh `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` of actual repository-built BM25 SQL:

| Measured node/property | Wisigard | Princess Alexandra |
| --- | ---: | ---: |
| Total server execution, ms | 6,916.068 | 7,079.625 |
| `membership_values` rows | 127,893 | 127,893 |
| `membership_values` inclusive time, ms | 971.326 | 963.005 |
| Eligible representative CTE rows | 57,899 | 57,899 |
| Representative CTE inclusive time, ms | 1,465.426 | 1,444.443 |
| Primary-key probes of `searchable_chunks` | 57,899 | 57,899 |
| Top-20 rejoin rejected comparisons | 1,157,960 | 1,157,960 |
| Shared buffer hits | 1,505,045 | 1,505,165 |
| Shared buffer reads | 0 | 0 |
| Temporary blocks read/written | 25,697 / 9,345 | 25,697 / 9,345 |
| Ordered BM25 index scan | No | No |

Times are inclusive; do not sum parent/child nodes or independent medians. The repeated top-20 CTE scan is roughly 4 ms per loop × 20, while the rejected-comparison parent includes the entire preceding scoring/eligibility path. Flattening that join improves median complete Core retrieval by only about 1–2%; **the million comparisons are real but are not the dominant six-second cost**. Scoring all eligible ranges remains the dominant work after the rewrite. This is predominantly warm-cache CPU/scoring and relational/temp work, not cold disk access or pool acquisition.

### Why the ordered index is not selected

The existing BM25 index is valid and usable. Controls on the same staging server/query:

| Control | Server ms | Ordered BM25 index |
| --- | ---: | --- |
| Unfiltered score ordering, projected score, LIMIT 20 | 3.949 | Yes |
| Same, with deterministic hash/start/end tie keys | 3.024 | Yes |
| Add full eligible-membership EXISTS, retain score-only ordering | 6,761.036 | No |

The second control uses an ordered index path with incremental sorting; deterministic ties are **not** the reason the real query loses the index. The eligibility join/semijoin shape makes the planner drive from the full eligible relation, probe canonical chunks by primary key, score every eligible range and sort. Replacing JOIN with EXISTS did not restore index-first execution. Resolving `to_bm25query` in an explicit InitPlan also remained about 6.8 seconds; it did not remove the full scoring cost. No planner settings were disabled to manufacture an improvement.

The earlier 0.259 ms unfiltered control projected only identities; the fresh controls also project scores. Neither bypasses the need for eligibility in a serving query. Both are diagnostic controls, not replacements. The installed extension documents finite top-K/post-filter limitations; it can return fewer rows after filtering, and its no-LIMIT default is capped. See [pg_textsearch v1.3.1 filtering and LIMIT behavior](https://github.com/timescale/pg_textsearch/blob/v1.3.1/README.md#filtering-with-where-clauses).

## C. Alternative implementations

One warm-up followed by five warm samples per variant/query, alternating forward/reverse variant order, identical cached vectors/settings and no investigation-initiated data writes. All staging engines enforce `default_transaction_read_only=on`, a 30-second statement timeout and an expected database-name assertion. Complete evidence payloads are compared, including identities, scores, ranks, text, source and locators. SQL variants run one statement per transaction; sequential/concurrent split variants explicitly preserve a shared REPEATABLE READ snapshot. Independent timed repetitions use fresh read-only transactions rather than holding one exported snapshot throughout the entire benchmark. Observed results remained equivalent throughout.

### Lexical alternatives

These are **complete SQLAlchemy Core retrieval medians**, including acquisition/transaction completion, buffered transfer and Evidence construction, but with statement objects prepared before timing. They exclude query embedding, generation, reranking and application RRF. Server EXPLAIN times are separately reported above/artifacted; neither metric is renamed as API latency.

| BM25 variant | Wisigard ms | Alexandra ms | BM25 index | Exactness / decision |
| --- | ---: | ---: | --- | --- |
| Existing eligible-before-limit baseline | 6,852.4 | 7,049.8 | No | Reference semantics |
| Flat join carrying selected provenance keys | 6,724.7 | 6,975.1 | No | Equivalent; only 1–2% improvement, not adopted |
| Eligible EXISTS plus score-boundary WITH TIES | 6,859.9 | 7,106.9 | No | Equivalent; no benefit |
| Global 400-prefix, full reference eligibility | 1,650.1 | 1,572.3 | Yes | Sample-equivalent, generally approximate; not adopted |
| Global 400-prefix, candidate-content reference eligibility | 627.9 | 472.8 | Yes | Sample-equivalent, generally approximate; not adopted |
| Existing readiness eligibility backend | 6,294.0 | 6,597.4 | No | Current data equivalent; still full scoring, lifecycle caveats below; not enabled |

The scoped prefix preserves owner/current-version/profile filters, deterministic representative selection and **full sibling-plan completeness** inside the selected prefix. Its approximation comes from candidate discovery before eligibility, not from dropping authorization or validation. It is never used by production code.

Bounded iterative diagnostic caps 20/40/100/400 were also tested. Both real questions match baseline at all caps. At 20 the selected boundary equals the raw boundary, so merely filling K slots is insufficient to certify ties. At 40, both have a strict score gap to the unexamined frontier. Two-step 20→40 candidate-query times total about 560 ms/507 ms, **excluding the extra frontier diagnostic queries**. These are single diagnostic runs, not five-sample serving medians or a complete exact implementation. An exact adaptive design would need a documented complete global prefix, a strict boundary certificate covering ties, one stable snapshot, bounded work and full reference fallback on exhaustion, no-match/zero-score tails or uncertainty. A fixed cap and “enough eligible hits” alone are not equivalent to current semantics. No adaptive serving path is introduced.

### Combined versus split hybrid

The end-to-end application experiment uses the actual `SearchEvidence` workflow with cached embeddings, 20/20 channel limits and final limit 20. It includes storage-vector checks, repository SQL/transaction work, full Evidence conversion, unchanged RRF and ordinary selection. No remote embedding/generation/reranking requests are included.

| Hybrid variant | Wisigard ms | Alexandra ms | Peak connections | Explicit SQL operations per attempt |
| --- | ---: | ---: | ---: | ---: |
| Combined baseline | 7,118.0 | 7,401.8 | 1 | 3 |
| Combined, batched settings (implemented) | 7,059.7 | 7,259.4 | 1 | 2 |
| Separate sequential, same snapshot | 7,248.7 | 7,450.4 | 1 | 4 |
| Separate concurrent, exported snapshot | 7,091.2 | 7,342.6 | 3 | 7 |
| Combined, shared full eligibility | 7,062.1 | 7,243.5 | 1 | 3 |

The counts exclude pool/BEGIN/COMMIT/isolation protocol work. Concurrent count includes one snapshot export SELECT and two snapshot import SET statements. None of the split variants improves median latency by even 1%; shared eligibility saves only about 0.8%/2.1%. The implemented batching variant is about 0.8%/1.9% faster in these hybrid samples. These small changes do not remove the BM25 bottleneck or justify a split/shared-eligibility redesign.

Sequential splitting uses one connection and one REPEATABLE READ transaction. Concurrent splitting uses an exported snapshot held by a coordinator and two worker transactions importing it, so all three pool slots are occupied for one request. Simply calling two repositories independently/concurrently would allow different snapshots; that is not a correctness-equivalent comparison. The prototype cancels and awaits workers on errors before releasing the exporter. An AsyncSession is not safe for concurrent independent tasks; concurrent channel SQL requires separate sessions/connections. See [SQLAlchemy's concurrent-task guidance](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-asyncsession-with-concurrent-tasks). Snapshot guarantees follow [PostgreSQL transaction isolation](https://www.postgresql.org/docs/18/transaction-iso.html) and [snapshot synchronization](https://www.postgresql.org/docs/18/functions-admin.html#FUNCTIONS-SNAPSHOT-SYNCHRONIZATION).

Compared with combined hybrid, sequential split adds one retrieval round trip and splits hydration, with duplicated eligibility work remaining. Snapshot-correct concurrent split adds export/import/transaction operations and uses three connections; in the application's default three-connection pool it would serialize other requests or risk starvation under concurrent coordinators without extra admission control. It overlaps a relatively short HNSW branch with a dominant BM25 branch, so substantial speedup is not expected. Separate statements do not themselves fix the lexical plan. Shared-eligibility hybrid reuses one full representative CTE for both branches while retaining the HNSW index; the saved candidate-specific validation is small compared with full BM25 scoring. None of these alternatives is enabled.

The existing combined statement fails as a whole if either channel fails, and SQLAlchemy errors become `RepositoryError`. A split implementation would need to discard partial results, cancel/await remaining work and preserve bounded ANN retries without silently substituting one channel. Repeated hybrid ANN attempts currently redo lexical work; a split design could cache that branch inside one snapshot, but current settings have only one attempt, so no repeated-attempt benefit was measured.

## D. Completeness validation and persisted state

### What is already enforced

- Chunk construction and `validate_chunk_plan` check exact authoritative Content, nonempty in-bounds ranges, increasing starts, configured overlap and complete coverage including whitespace. It does not retokenize to prove tokenizer boundaries or token counts; those remain the generating chunker's responsibility.
- Chunk-plan persistence locks the version, reconstructs/validates the stored plan, checks idempotent conflicts, and transactionally persists expected count, ordinals/ranges and canonical `searchable_chunks` text. Database constraints enforce nonnegative ordinals, valid range shape, unique ranges and foreign keys, not full coverage by themselves.
- Indexing stores validated compatible vectors and requires every nonblank selected chunk before activation. Activation locks document/version, plan/content/profile/vector inputs, revalidates completeness, records the canonical plan/profile readiness key and changes the current-version pointer in the same transaction. Optimistic expected-current/latest-version checks reject stale publication. Atomic file ingestion persists Content, plan, both vector representations, readiness and publication in one transaction.
- `chunk_plan_readiness` persists the verified version/chunking/embedding tuple. Migration 0006 validates readiness INSERT/UPDATE in the database, including unambiguous equivalent plan, full geometry/count/order/coverage and compatible vector coverage. Staging has this enabled guard and invalidation triggers. It is not just a boolean set by an unverified application call.

### What search must still check dynamically

Current version and ownership, deletion, trusted visibility, selected chunking/embedding profile, current membership/vector existence, nonblank selected ranges, deterministic authorized representative and provenance hydration remain search responsibilities. Old verified versions must stay hidden after update/delete. A per-profile readiness certificate cannot replace those checks. The membership-owned backend also separately requires every nonblank membership-owned vector; its legacy readiness certificate alone is insufficient.

Counts, ordinal continuity, boundary/coverage geometry and all-sibling compatible-vector completeness are stable **only while their validated inputs remain unchanged**. They can move out of the hot path only when readiness invalidation/publication covers every permitted mutation and missing/stale certificates fail closed. The existing reference path deliberately rechecks them and remains the application default.

### Audit and stale-marker failure

A full one-snapshot EXCEPT ALL audit of document/version/hash/range/content-length/canonical-embedding representatives returned:

| Check | Count |
| --- | ---: |
| Reference representatives | 57,899 |
| Readiness representatives | 57,899 |
| Reference-only rows | 0 |
| Readiness-only rows | 0 |

This proves current selected-profile equivalence, not that the marker can never become stale. Existing integration tests validate normal membership/vector mutation invalidation, locked concurrent changes, publication rollback, source reuse and current-version behavior.

The new **disposable** adversarial experiment found a concrete unsupported-mutation gap: create two valid plans for one version under different chunking configurations, then update the second configuration to equal the first. The invalidation function clears the **old** profile's markers, but leaves one marker for the newly ambiguous profile. Reference search correctly rejects the version because two equivalent serving plans exist; readiness search admits it. No staging configuration or marker was changed. This is a completeness failure, not evidence of an authorization bypass: readiness still applies live owner/current/deletion checks.

Other trust-boundary concerns include trigger bypass/disabled triggers, TRUNCATE/bulk maintenance and incomplete backfills. For example, row/statement DML invalidation does not by itself establish TRUNCATE coverage. Missing markers produce false negatives; stale positive markers can admit incomplete plans or choose a representative the reference path would reject. Canonical-profile identity changes and equivalence changes require both old and new dependency groups to be handled. These are reasons to harden lifecycle contracts before relying on markers, not reasons to remove validation.

Transactionally maintained verified searchable memberships are feasible: bind them to immutable version/content and explicit chunking/embedding profile, publish them atomically with readiness/current pointer, and invalidate/rebuild them under the same locking discipline on every dependent write. Updates/reindexing must never expose partially ready replacements; deletion/current-version visibility must remain dynamic. A new persisted membership relation or trigger changes require separately approved migration/lifecycle work. A marker table alone does not solve the index's bounded post-filter semantics or guarantee ordered eligible top-K. The measured existing readiness improvement is only 6–8% for lexical retrieval and does not justify a substantial redesign here.

## E. Performance and implemented improvement

Only one measured low-risk improvement is implemented: combine the two HNSW transaction-local setting SELECTs. One request with one ANN attempt now sends one settings SELECT plus one retrieval SELECT. Candidate SQL, HNSW values/algorithm, retries, isolation, rollback and all evidence semantics are identical.

Five paired warm actual semantic-repository measurements (same two cached vectors; one warm-up excluded):

| HNSW complete repository median | Before ms | Batched ms | Change |
| --- | ---: | ---: | ---: |
| Wisigard | 509.8 | 486.0 | −4.7% |
| Princess Alexandra | 489.8 | 423.0 | −13.7% |

Both variants returned identical complete Evidence payloads on every repetition. Settings SELECT count drops from two to one. Samples include server/network variability (including a baseline outlier); these are focused measurements, not a broad percentile/SLA claim. The tiny change does **not** resolve the 6.8–7.0-second BM25 bottleneck. Hybrid results are reported in section C without attributing all cross-sample variation to this one saved round trip.

An AST comparison also confirms the implemented repository matches the measured batching prototype and that all 16 module-level SQL builders, eligibility helpers and evidence converters are unchanged from the frozen baseline (`.cache/retrieval-architecture/change-verification.json`).

## F. Correctness and validation

- Both requested real questions: full top-20 lexical payload equality for all tested variants across every warm repetition; hybrid variants compare both complete channel payloads, channel ranks/scores, fused ordering and selected payloads. Production introduces no approximate BM25 path.
- All-corpus current readiness/reference equality: 57,899 representatives and zero symmetric difference, including canonical embedding and source occurrence keys.
- Disposable candidate-prefix failure: high-ranking private/incomplete results occupy the prefix; exact eligible retrieval returns the one public occurrence with its own provenance, while a capped prefix returns zero (**0% eligible top-K recall**). This also exercises insufficient eligible results rather than filling K with unauthorized hits.
- Disposable no-match failure: reference BM25 returns the eligible zero-score result; ordered capped index candidates return zero. Thus an empty index prefix is not proof of no eligible baseline evidence.
- Disposable readiness equivalence-mutation failure: one stale marker remains; reference rejects and readiness admits. The issue is documented, not patched through an unapproved migration.
- Baseline relevant integration group: **120 passed**, covering BM25, semantic/hybrid, profile/authorization/provenance, readiness/concurrent mutations and atomic ingestion. After batching, focused semantic/settings checks: **39 passed**, including one configuration statement, exact ef/iterative values, bounded retries, and pooled-connection reset after commit and forced rollback.
- Relevant network-blocked application/repository/tracing unit checks: **264 passed**. Provider implementations in integration tests are synthetic.

After the implementation, BM25/hybrid integration checks: **30 passed**. Together with the 39 semantic/settings checks, the final changed retrieval paths have 69 passing integration cases. Ruff lint/format checks pass on changed source/tests and the new experiment scripts (frozen baseline copies are preserved rather than reformatted). `git diff --check` passes. JUnit artifacts: `integration.xml` (initial 120), `settings-integration.xml` (39), `hybrid-final-integration.xml` (30), and `unit.xml` (264), under `.cache/retrieval-architecture/`.

## G. Recommended architecture

Retain the combined one-snapshot hybrid statement and current reference eligibility semantics. Keep HNSW candidate discovery independent of BM25, application RRF unchanged, narrow ranked keys before evidence hydration, and the existing context/citation behavior. Keep the small settings batching change; do not present it as a BM25 fix.

Do not enable a fixed lexical prefix, concurrent split queries or readiness as a shortcut based on these two successful data examples. The large candidate-first gain is real but its generally approximate behavior is demonstrated. The most relevant next focused prototype is exact, snapshot-consistent adaptive lexical discovery with an explicit score/tie frontier certificate and mandatory reference fallback; it must handle ineligible leaders, zero-score tails, exhaustion and extension-version behavior before adoption. This investigation does not claim that prototype is implemented or fully proved.

Before making authoritative verified memberships a serving dependency, separately approve and test configuration immutability or complete old/new equivalence invalidation, maintenance-operation coverage and lifecycle/backfill contracts. Do not add a new membership table merely to reduce SQL line count. Existing readiness already removes much reconstruction but leaves the dominant full scoring cost, so a schema redesign needs its own measured justification.

## H. Changes, artifacts and reproduction

Tracked changes:

1. `src/investigation_engine/infrastructure/persistence/postgresql_evidence_repository.py`: one SELECT applies both existing transaction-local HNSW settings; no SQL-builder or lexical changes.
2. `tests/integration/infrastructure/persistence/test_semantic_retrieval.py`: update existing statement assertions, cover strict/off iterative settings and prove real pooled-connection reset on success/failure.
3. This report.

Temporary reproducible scripts, frozen baseline/batched modules, exact compiled SQL, full EXPLAIN JSON, complete sample/payload JSON and JUnit results are under `.cache/retrieval-architecture/` (ignored; no credentials copied). Cached query vectors are read from the existing local traces; a missing cache fails instead of calling an AI provider. Performance scripts assert the staging database name and read-only state. The adversarial script never imports application database settings and creates/removes a distinct disposable PostgreSQL database/container on a nonapplication port.

```powershell
uv run python .cache/retrieval-architecture/benchmark.py --phase sql --samples 5
uv run python .cache/retrieval-architecture/benchmark.py --phase application --samples 5
uv run python .cache/retrieval-architecture/settings_benchmark.py
uv run python .cache/retrieval-architecture/planner_controls.py
uv run python .cache/retrieval-architecture/frontiers.py
uv run python .cache/retrieval-architecture/audit_memberships.py
uv run python .cache/retrieval-architecture/adversarial.py
```

Run performance phases serially, with local payload tracing disabled and no competing experiment. `frontiers.py` is a diagnostic, not a production fallback implementation. The mutation experiment requires the existing `investigation-postgres-test:local` image. The investigation did not install/update staging extensions or execute migrations against staging. Existing migrations were applied only to disposable test databases. No application settings or API response shapes changed. Trigger/schema redesign, production rollout and broader evaluation remain outside this implementation and require separate authorization where applicable.
