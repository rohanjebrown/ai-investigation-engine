# Retrieval optimization: 29 October 2026

The pre-existing logging changes in the working tree were retained. This task changes `postgresql_evidence_repository.py` and adds an authorization regression in `test_hybrid_retrieval.py`. No database schema, profile, embedding, Cloud SQL configuration or migration was changed.

## Implementation

- Canonical eligible occurrences are shared within each statement. Semantic scoring materializes narrow `(embedding configuration, content digest, start, end, distance)` rows for configuration IDs actually used by eligible candidates, then joins those scores to the eligible representatives before ordering and LIMIT. Vector payloads are not included in the materialized result.
- Lexical scoring materializes matching canonical embedding ranges and their existing PostgreSQL English `ts_rank` scores, then joins the authorized, active, complete representatives before ordering and LIMIT. Its matching and ranking expressions are unchanged; this is not BM25. Both channels reuse the same eligible relation in hybrid queries.
- Shared completeness checks retain plan counts, exact contiguous/overlap rules, embedding compatibility, nonblank checks and current-version/visibility/deletion filtering. The validated `ck_chunk_memberships_range` database constraint already requires nonnegative start and end greater than start. Consequently two identical conditions in the coverage predicate and the derived single-chunk `end > 0` condition were removed. The named check was independently verified as validated on Cloud SQL; the existing local migration and schema tests also enforce it.
- Neither score CTE applies a result limit. The final ranked candidates are joined to eligible occurrences first, and hydration still uses the selected occurrence's source reference and original spans. The result ordering and similarity/rank calculations are unchanged.

The score CTEs can process stored vectors for a selected compatible physical profile that have no currently eligible occurrence. They cannot appear in results, but a future database with many inaccessible, inactive or orphaned embeddings could see extra work. Measure selective scopes and lifecycle churn before treating these plans as a general performance guarantee.

## Production read-only comparison

Six sequential `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` SELECTs used the same already-stored 768-dimensional vector, fixed-token 512/32 configuration, profile, question, public scope and limits in a read-only session with a 30-second statement timeout. They are single paired observations, not p50/p95 or production SLAs. `EXPLAIN` roundtrip includes plan transfer and differs from normal application execution.

| Mode | Baseline server | Optimized server | Baseline EXPLAIN roundtrip | Optimized EXPLAIN roundtrip |
|---|---:|---:|---:|---:|
| Semantic | 2,345.5 ms | 2,189.9 ms | 2,607.3 ms | 2,325.3 ms |
| Lexical | 6,399.0 ms | 3,441.9 ms | 6,509.9 ms | 3,623.4 ms |
| Hybrid | 6,932.4 ms | 3,689.7 ms | 7,068.1 ms | 3,860.4 ms |

One prior paired Cloud SQL observation found 2,358.5 → 2,181.8 ms semantic and 6,404.2 → 3,452.8 ms lexical; those observations predated the removal of the redundant shared predicates. On the final plan, the expensive shared `membership_values` still processes 127,893 rows in about 1.42 s inclusive, and the representative selection still processes 127,893 memberships to obtain 57,899 ranges with an external sort. The lexical baseline makes 57,899 content index probes with `to_tsvector` filtering; the new lexical match CTE changes the join shape and returned one matching canonical range for the tested query. The semantic score CTE processed 57,901 stored embeddings in this snapshot. These node timings are inclusive, not additive.

The rewrite changes memory and temporary I/O: semantic root temp blocks written increased from 5,635 to 6,851; lexical from 5,569 to 6,385; hybrid from 6,385 to 6,851 (8 KiB blocks). Lower elapsed time does not imply lower resource use. More representative data, differing owner scopes and the deployed application should be evaluated before relying on these numbers.

The optimized semantic plan used one sequential scan of `chunk_embeddings` for 57,901 score rows, rather than 57,899 candidate-driven embedding index probes. The optimized lexical plan used a parallel hash join between a `contents` sequential scan and an index-only scan of embedding keys (three reported loops), rather than 57,899 candidate-driven content index probes. The filters still construct lexical vectors dynamically. The optimized score CTEs reference the eligible relation, which PostgreSQL evaluates once in the observed hybrid plan.

In a separate read-only repeatable-read transaction on Cloud SQL, baseline and optimized raw result rows compared equal for the same semantic (5 rows), lexical (1 row) and hybrid (6 channel rows) statements. Comparison included all hydrated columns, including source, spans, offsets, scores and rank inputs. Source text and vector values were not logged. This is one question/vector/scope, not a complete production quality evaluation.

## Local verification and limitations

The existing benchmark runs both implementations on identical synthetic rows and checks identities, exact content, source references, source spans, scores and channel rankings. The synthetic fixture contains one chunk per unique record; it does not reproduce the actual duplicate and multichunk distribution. PostgreSQL integration tests exercise ownership, active/deleted/superseded versions, missing vectors, malformed/incomplete plans, duplicate occurrences and chunk/profile variants. A new regression verifies that high-ranking private and inactive lexical matches cannot occupy the authorized top-K before filtering.

The full unit suite passed 605 tests. The focused PostgreSQL schema and retrieval suites passed 70 tests before the new authorization regression was added. The full disposable PostgreSQL integration suite subsequently passed 169 tests, with four opt-in tests skipped; the new authorization regression also passed independently. Ruff lint and formatting passed. The configured WPS check reports 3,189 findings, compared with 3,190 in the pre-existing working tree. Existing WPS debt remains; no broad suppression or cleanup was performed.

The final paired local run used 50,000 synthetic rows, PostgreSQL 18.4, pgvector 0.8.5, `work_mem=4MB`, and `EXTERNAL` vector storage, matching the Cloud SQL column's observed storage setting. Five warm calls per variant were made in sequence with a disposable `NullPool` connection per call. P95 is nearest-rank and therefore the maximum of five samples; it is descriptive, not a reliable tail estimate. Each EXPLAIN execution measurement is a separate single observation after the warm calls. Stage timings include network/driver materialization and are not server-only.

| Mode | Baseline p50 / p95 | Optimized p50 / p95 | SQL roundtrip p50 / p95, baseline → optimized | Baseline / optimized EXPLAIN server |
|---|---:|---:|---:|---:|
| Semantic | 1,427.6 / 1,771.5 ms | 974.8 / 1,021.2 ms | 1,365.9 / 1,611.2 → 941.2 / 987.2 ms | 1,518.3 / 1,035.1 ms |
| Lexical | 2,876.7 / 2,923.2 ms | 2,115.6 / 2,636.4 ms | 2,843.8 / 2,882.6 → 1,759.9 / 2,179.3 ms | 3,180.1 / 1,662.5 ms |
| Hybrid | 3,723.8 / 3,991.1 ms | 2,039.1 / 2,445.3 ms | 3,676.8 / 3,734.6 → 1,932.4 / 2,280.5 ms | 3,696.3 / 2,014.6 ms |

These medians are independent statistics and cannot be subtracted to derive precise overhead. The run using `PLAIN` inline-vector storage also found a lexical median improvement (3,372.3 → 1,510.2 ms) and hybrid improvement (3,343.2 → 1,871.0 ms), but its semantic median regressed (861.9 → 1,041.4 ms), with a much noisier optimized p95. This storage-sensitive regression and the small samples limit the conclusion: the semantic change was retained based on the verified Cloud SQL storage policy, two modestly faster Cloud SQL plan observations and the `EXTERNAL` fixture, not an assumption of universal improvement. The previous 10-sample run overlapped local test activity and is not a clean tail comparison.

Raw synthetic samples, staged times and plans are in ignored `.cache/deadline-retrieval/external/` and `.cache/deadline-retrieval/final/`; these contain synthetic data only. Sanitized Cloud SQL plan-node metrics and paired summaries are in `.cache/deadline-retrieval/cloud/`. SQL text, query vectors and source text from Cloud SQL are not included in the saved plan summaries.

The principal remaining cost is the corpus-wide eligible-plan/materialization/window work. Lexical retrieval still parses chunk text dynamically without a persisted lexical index, and exact vector ranking still scans stored vectors. Neither mode reliably meets a sub-second Cloud SQL target. `pg_textsearch` BM25 and an indexed canonical chunk-text representation remain the key lexical decision, with extension/schema/backfill approval required; eligibility materialization and ANN remain separate decisions. No production write, index creation, extension installation, deployment or live embedding request was performed.
