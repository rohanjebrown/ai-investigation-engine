# Index-driven BM25 retrieval — 6 November 2026

## Outcome and scope

Implemented bounded, index-first BM25 discovery followed by the existing exact eligibility logic. The initial pool is 400 canonical ranges. A strict score frontier either certifies the eligible top-K or triggers exact completion over the remaining contents. Fixed-prefix approximation is never returned.

The new path serves `lexical_backend="bm25"` with the default `eligibility_backend="reference"`, in lexical-only and hybrid reads. The optional readiness backend keeps its previous query. HNSW discovery, retries, transaction settings, semantic ranking, RRF, hydration, prompts, Investigation orchestration, selection and citation validation are unchanged from the working tree at the start of this task. Earlier uncommitted HNSW settings batching and its tests are preserved.

No schema/migration changes, persistent PostgreSQL settings, staging data writes, Gemini/Voyage calls, commits, pushes or deployment. The local ignored `.env` had Voyage enabled; its `RERANKER_BACKEND` was changed to `none` as requested. No other environment values were changed.

## 1. Baseline execution plan and bottleneck

The original `_bm25_statement` reconstructs all eligible representatives, joins each to `searchable_chunks`, scores them, selects the score/hash/range top-K, then rejoins for representative identity and provenance. On the selected staging profile it processes 127,893 occurrence memberships and scores 57,899 eligible canonical ranges. The final top-20 rejoin rejects 1,157,960 comparisons. It uses primary-key probes rather than an ordered `searchable_chunks_bm25_idx` scan.

The baseline full-corpus scoring and eligibility work dominates. Flattening the final rejoin alone did not recover index-driven retrieval in the preceding controlled experiments. The current task therefore changes candidate discovery and scopes validation instead of rewriting the ranker or eligibility rules.

## 2. Optimized SQL strategy

All of the following are CTEs inside **one retrieval statement**, including final hydration:

1. `bm25_index_candidates AS MATERIALIZED`: order canonical ranges by `text <@> to_bm25query(...)`, limit 400. The server-owned literal limit remains visible to the extension planner.
2. `bm25_first_contents`: collect the distinct content hashes from that prefix.
3. Reuse `_eligible_memberships` with those hashes to resolve associated versions, visible current documents and complete compatible serving plans. Materialize all eligible canonical ranges of those contents, including nonmatching sibling ranges needed for completion.
4. Join this cached eligibility relation to the prefix, choose the score/hash/start/end top-K and compute a score frontier.
5. Accept only when K eligible hits exist, their worst score is finite and negative, the prefix boundary is finite, and the worst selected score is **strictly better** than the prefix boundary.
6. Otherwise expand once to every remaining searchable content hash. Validate only those disjoint contents, union their eligible representatives with the first materialized cache, and invoke the unchanged exact `_bm25_statement`.

This is a bounded adaptive strategy: 400 candidates followed, when needed, by exact remaining-content completion. Repeatedly rerunning ever larger prefixes would redo the extension scan and eligibility work. Earlier experiments with 2,000 hidden leaders required multiple such queries and were slower than the baseline. The implemented one-step completion bounds the number of validation phases and SQL round trips while retaining exact behavior.

The remainder is excluded with `content_sha256 NOT IN (first contents)`. Hashes are nonnull. Each content's associated plans are validated in one partition only. Materialized first-content representatives are reused by completion; they are not reconstructed. Final payload hydration stays after selection, with no N+1 requests.

## 3. Reuse of HNSW eligibility

The shared `_eligible_memberships` gains one keyword option, `candidate_ranges_only=False`. Its default preserves HNSW's existing range restriction and compiled SQL. BM25 uses content-only candidate keys and retains every eligible range of those contents for exact completion.

Both partitions reuse the existing candidate-content/version lookup, shared-or-owner visibility, nondeleted/current-version predicates, selected chunking configuration equivalence, membership count/ordinal/coverage/blankness checks, compatible embeddings for every nonblank sibling, hashed complete-plan membership test, and lowest authorized complete document/version representative per canonical range. Provenance is hydrated from that representative's own version.

A local AST/compilation audit confirms that `_hnsw_candidates`, `_semantic_statement`, `_search_statement`, HNSW retry/read methods, `_bm25_statement`, `_hydrate` and evidence conversion are unchanged. Standalone HNSW SQL **and bound parameters** match the frozen pre-task repository for shared and owner scope.

## 4. Exactness and deterministic ordering

The ordered prefix establishes a global score boundary. If K eligible hits have scores strictly better than that boundary, no unseen range can displace them. Canonical hash/start/end breaks equal-score ties before the limit, matching the baseline's selected identities. The unchanged outer hydration orders by score/document/version/range, preserving exposed ranks and provenance.

Equality with the prefix boundary is deliberately uncertain: completion handles ties that cross the prefix, regardless of the index's ordering among equal scores. An empty index result, zero-score tail, nonfinite boundary, fewer than K eligible results, or too many ineligible leaders also triggers completion. This preserves the original behavior of returning eligible zero-score ranges for unmatched questions.

In completion, the first and remaining hash sets partition all searchable contents. Their cached and newly validated representatives therefore cover the same eligible canonical ranges as the reference query, with the same representative and score rules. The unchanged ranker then selects the exact result. A content's nonprefix siblings must stay in the first cache; otherwise excluding that content from the remainder would lose zero-score results. A dedicated regression covers this case.

All discovery, frontier checks, eligibility partitions, ranking and hydration share one statement snapshot. Existing HNSW/hybrid REPEATABLE READ retries retain their existing behavior. No state survives between searches.

## 5. Paired warm benchmark

Environment: existing `investigation_stage`, PostgreSQL 18.6, pg_textsearch 1.3.1, pgvector 0.8.5; shared scope; persisted serving chunking/embedding profiles; K=20; HNSW 400 candidates/ef=400/iterative off; RRF k=60. Cached vectors were used, with no embedding-provider fallback. Both variants used the same async repository/session configuration and a single pooled connection. Transactions were explicitly read-only, with a session-local 30-second statement timeout.

For each question and mode: one warm-up per variant, then five paired warm samples with alternating order. Complete retrieval includes pool/session work, SQL execution/transfer, hydration and Evidence conversion; hybrid additionally includes existing Python RRF. Query embedding, model generation and reranking are excluded. Exact equality was asserted for every Evidence field, both hybrid channels and fused results on every run.

| Question | Mode | Baseline retrieval median, ms | Optimized retrieval median, ms | Speedup | Separate baseline EXPLAIN, ms | Separate optimized EXPLAIN, ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Who is Wisigard's father-in-law? | Lexical | 6,841.30 | 503.10 | 13.60× | 6,805.775 | 128.373 |
| Same | Hybrid | 7,029.99 | 639.65 | 10.99× | 6,948.235 | 179.309 |
| Who is the maternal grandfather of Princess Alexandra of Hanover (born 1999)? | Lexical | 7,064.16 | 371.16 | 19.03× | 7,028.309 | 104.847 |
| Same | Hybrid | 7,313.23 | 608.71 | 12.01× | 7,241.024 | 169.563 |
| Where did M. K. Muthu's father die? | Lexical | 7,688.21 | 529.27 | 14.53× | 6,992.875 | 81.703 |
| Same | Hybrid | 7,746.50 | 682.68 | 11.35× | 6,872.887 | 131.993 |
| Who lived longer, Friedrich Paschen or Ernst Bessey? | Lexical | 6,842.35 | 385.04 | 17.77× | 6,855.355 | 78.497 |
| Same | Hybrid | 7,176.10 | 656.41 | 10.93× | 6,898.613 | 146.916 |
| Who is the paternal grandfather of James FitzGerald, 8th Earl of Desmond? | Lexical | 7,089.03 | 377.76 | 18.77× | 7,075.838 | 92.593 |
| Same | Hybrid | 7,252.31 | 584.40 | 12.41× | 7,186.612 | 143.001 |

Pooling the 25 measured samples per mode gives lexical medians **7,039.19 → 401.93 ms** (17.51×) and hybrid medians **7,273.79 → 624.14 ms** (11.65×). Every one of the 120 warm-up/measured retrieval calls asserted complete payload equality with the reference result. Lexical calls issued one explicit SQL command; hybrid calls issued two (the existing batched transaction settings and one retrieval statement).

`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` runs were collected separately from timed retrieval calls. Their server execution times are separate observations, not subtracted from complete retrieval medians or presented as components of the same execution. These are sequential local-to-staging measurements, not a concurrency/load test.

## 6. Index and work verification

| Question | First-content membership rows validated | Baseline membership rows | Remaining validation loops | BM25 ordered index |
| --- | ---: | ---: | ---: | --- |
| Wisigard | 2,464 | 127,893 | 0 | Yes |
| Princess Alexandra | 1,834 | 127,893 | 0 | Yes |
| M. K. Muthu | 791 | 127,893 | 0 | Yes |
| Paschen / Bessey | 965 | 127,893 | 0 | Yes |
| James FitzGerald | 770 | 127,893 | 0 | Yes |

Counts match between lexical and hybrid plans. Each first-content validation producer executes once. Every remaining-content validation producer is unexecuted on these five questions; this is plan evidence that the new path avoids rebuilding the full eligible corpus in the common case. Every optimized hybrid plan also retains `ix_chunk_embeddings_embedding_hnsw`.

For Wisigard, 400 discovered ranges map to 398 contents, 2,405 associated versions and 2,464 visible occurrence memberships. Validation yields 425 eligible canonical ranges, including siblings retained in the cache; only prefix hits participate in fast selection. No corpus-wide vector/text data is loaded into the application.

The staging plans confirm the intended BM25 index. A three-row disposable fixture may correctly choose a sequential scan; the integration EXPLAIN smoke test does not force an index for that tiny relation.

Additional read-only fallback diagnostics used one warm-up and one timed call per variant, plus a separate EXPLAIN. The excluded-leader case adds a **test-only SQL exclusion** for the contents found in the first 2,000 index rows; it does not change stored ownership or application authorization. These observations are not five-pair benchmark medians:

| Diagnostic | Baseline retrieval, ms | Optimized retrieval, ms | Separate baseline / optimized EXPLAIN, ms | First / remaining membership rows |
| --- | ---: | ---: | ---: | ---: |
| Excluded leading contents | 6,729.72 | 7,643.84 | 6,487.801 / 7,383.170 | 2,464 / 125,429 |
| Unmatched lexeme / all-zero ranking | 6,627.58 | 7,132.81 | 6,474.895 / 7,171.336 | 0 / 127,893 |

Both returned the same complete 20-result Evidence payload as the reference. Both optimized diagnostics used the BM25 index before exact completion. In the excluded-leader case the two validation producer row counts sum to the baseline's 127,893, each with one execution. The unmatched case confirms that an empty indexed match stream still returns the original exact zero-score top-20. These cases demonstrate the fallback overhead as well as its correctness.

## 7. Correctness and integration validation

- **73 passed**: BM25, hybrid and semantic integration suites against the existing disposable PostgreSQL test image (pg_textsearch 1.5.1, pgvector 0.8.5). No staging writes were used to create adversarial fixtures.
- **1 passed**: rerun of the actual optimized-query EXPLAIN integration test after switching its builder from the legacy helper.
- **1 passed**: focused sibling-completeness regression extended to add an incompatible-model embedding after removing the selected embedding.
- **265 passed**: application, BM25 SQL-builder, repository diagnostics and debug-trace unit tests. One existing Google SDK deprecation warning.
- Ruff lint/format on all modified Python files and `git diff --check`: passed.

The regressions compare complete Evidence values against the unchanged exact ranker. They cover 410 private high-score leaders before 25 public results; authorized owner scope with more than 400 tied leaders; deleted and superseded documents; wrong chunking configuration; malformed/incomplete plans; missing and incompatible sibling embeddings; duplicate canonical occurrences and canonical provenance; positive and zero-score ties; empty eligible pool; fewer than 20 results; and nonprefix zero-score siblings of cached contents. Existing hybrid/semantic tests exercise snapshot boundaries, authorization, completeness and HNSW behavior.

## 8. Files and reproducibility

Changed for this task:

- `src/investigation_engine/infrastructure/persistence/postgresql_evidence_repository.py`: shared eligibility option, indexed BM25 helper, lexical/hybrid wiring for reference eligibility.
- `tests/integration/infrastructure/persistence/test_bm25_retrieval.py`: exact completion, tie/provenance/sibling/embedding cases and actual-query EXPLAIN.
- `tests/unit/infrastructure/persistence/test_bm25_retrieval.py`: bounded prefix, strict frontier, disjoint completion and shared authorization SQL assertions.
- This report.
- Ignored local `.env`: only `RERANKER_BACKEND=none`.

Pre-existing modifications to HNSW settings batching, `test_semantic_retrieval.py`, and the earlier architecture report were retained.

Local ignored artifacts in `.cache/bm25-400/` include the frozen pre-task repository, read-only `verify.py`/`benchmark.py`, raw paired samples and complete evidence signatures in `paired-results.json`, separate SQL/JSON plans for every question/mode/variant, test XML, and `code-audit.json`. The frozen reference includes the earlier HNSW settings batching, so measured improvements isolate the BM25 change. The source hash recorded by the run is compared with the final implementation. These local artifacts contain no connection credentials.

Final repository source SHA-256: `7d840bb8034c50335033d259eea7d189358b4807cb0c74e2ddcb884eeea6add7`. `run.json` records the full persisted serving profiles. `fallback.py` and `fallback-results.json` retain the separate read-only completion diagnostics; `summary.json` verifies all paired sample counts, payload equivalence flags, SQL command counts, index plans, skipped remainder producers and the source hash.

## 9. Remaining limitations

The fallback is exact but can still perform full-corpus eligibility and scoring when authorization removes the leaders, the score boundary remains tied, or matching results are too few. It also pays for the initial 400-candidate scan; this optimization does not guarantee subsecond retrieval for every query/tenant. Initial and remaining contents are disjoint, so that fallback does not validate the first contents twice.

The optional readiness backend is intentionally unchanged. BM25 lexical ranking remains independent of approximate HNSW candidates. No index hint or global planner configuration was added; actual ordered index usage is demonstrated on the installed staging version rather than assumed for every database size/version.

Correctness relies on the installed BM25 index fulfilling its SQL ordering contract. The strict frontier avoids accepting an unresolved boundary tie; the prior five-question 2,000-row-per-query index audit found no score inversions. This is empirical extension validation, not a proof of the extension's internal implementation. Staging performance was measured on pg_textsearch 1.3.1; mutation regressions ran on the existing disposable 1.5.1 image.
