# Retrieval and investigation review

## Baseline, before behavior changes

Reviewed `main` at `b72a1c7` (WPS merge), on 2026-10-29. The worktree was clean and there were no stashes. This review uses the merged, split modules rather than earlier patches.

- Unit tests: **576 passed**, one upstream google-genai deprecation warning.
- Ruff lint and format: clean (`src tests scripts`, 171 formatted files).
- PostgreSQL integration baseline: **163 passed, 4 skipped** (137.95 seconds).
- WPS is advisory existing debt; see `reports/wps-lint-baseline.md` (2,798 historical findings in its broader scope).

Confirmed remaining issues:

1. Investigation returns INCOMPLETE immediately on evidence overflow, before appending its last tool response. Exactly-full evidence does not disable further searching.
2. Evidence identity and SQL candidate limits use source occurrences, so identical canonical ranges can consume multiple slots.
3. Quick Answer embeds the whole compound question once. Lexical retrieval uses `plainto_tsquery` (all terms), which can exclude separate passages answering independent clauses. There is no branch coverage selection.
4. Lexical scoring is PostgreSQL `ts_rank`, not BM25. Text vectors are computed at query time.
5. Serving eligibility performs corpus-wide completeness windows/grouping before ranking; plans must be measured before changing this invariant.
6. Search records time total searches, but there is no reliable complete breakdown of query embedding, acquisition, SQL roundtrip, fusion, generation and validation.

Existing safeguards to preserve: current-version/owner/deletion filters, complete plans and vectors, native signed Gemini tool history, strict admitted-ID citations, bounded calls/context, short repository transactions, exception chaining and idempotent replay. API pooling already enables pre-ping and 1,800-second recycling. The current checkout's corpus-processing report retains exception type/locator but lacks the earlier proposed driver diagnostics. The earlier import failure has no recoverable original driver exception; stale pooling remains unproven.

The full production corpus is not reproduced by synthetic local benchmarks. No quality, Cloud SQL latency or live model behavior is inferred from fake providers. No production writes, paid requests, model changes, reingestion, migration, commit or deployment are authorized by this review.

## Results

### Correctness changes

- **Finalization:** reaching the evidence, executed-search or embedding budget changes the next model turn to `final_only`. Exactly-full and overflowing searches retain admitted E IDs and their complete native function response. Gemini uses function-calling mode `NONE`; application orchestration also refuses search actions. One decision opportunity remains only within the existing turn/generation/context limits. Budget exhaustion without a decision is INCOMPLETE, not a corpus-quality abstention. Unknown/dropped IDs still fail citation validation.
- **Fact coverage:** the investigation guidance now explicitly requires coverage of every original subquestion, evidence-led dependencies, searches for unresolved facts and avoidance of incidental entities. No new planner state or model call is added. Deterministic tests exercise independent and genuinely dependent search transcripts. These tests establish protocol behavior, not reliable live model reasoning.
- **Content diversity:** each SQL channel selects one eligible representative per exact `(content_sha256, start, end)` **before** top-K. It chooses the lowest document ID, then version ID, among visible, nondeleted, current, completely indexed candidates. SQL source hydration uses that exact representative's version and spans. Other occurrences remain stored and independently manageable. Quick Answer and the investigation registry apply the same exact-range diversity defensively. Source-version `Evidence.identity` and occurrence-based RRF remain unchanged; both SQL channels choose the same representative. Similar text, distinct hashes and different ranges are not merged.
- **Quick Answer:** two explicit independent interrogative clauses joined by `and` use at most two existing retrieval calls; results are interleaved before intact-item context selection, with exact-range duplicates removed. A simple question still uses one retrieval. Common dependent pronouns leave the whole question intact. The Maheen Khan/Alice Washburn pattern now supplies both passages to one generation request. Counters report both query embeddings. This conservative English heuristic is not general question planning; other phrasings and real model support accuracy remain evaluation concerns. Generation instructions require abstention when any requested fact is missing.
- **Explicit lexical path:** `SearchEvidence.execute_keywords()` skips query embedding and semantic ranking while retaining the selected stored-profile completeness and authorization rules. It is an operator/application method, not new HTTP auto-routing or changed semantic/hybrid defaults. It still uses English stemming/stop words, not literal substring matching.

### Timing and SQL changes

`application/timing.py` emits names, elapsed milliseconds, success flags and a per-operation correlation ID, with no question, document text, SQL, URL, credentials or parameters. Nested calls share the same trace. Stages cover query embedding, acquisition, semantic/lexical/combined-hybrid SQL roundtrips, Python evidence hydration, fusion, selection, generation, citation validation, total RAG/retrieval/investigation, each model turn and each investigation search. HTTP middleware also times question/investigation processing, including validation and response construction, with the same nested trace. Failed operations are timed without changing their exceptions. The entrypoint enables INFO output for this timing logger alone, without verbose SDK or SQL logging.

SQL roundtrip timings include driver transfer/decoding and server work. Acquisition includes SQLAlchemy connection initialization. They are **not** pure server execution measurements. Hybrid remains one SQL statement/snapshot; separate channel server costs must be examined in its EXPLAIN plan rather than fabricated from application timers. SQL hydration is included in the statement, while the hydration timer measures Python reconstruction. Independent stages are not necessarily additive; totals include validation, SQL construction, commit/close and application overhead. HTTP timing ends when response headers are available; network transit and delivery of a streaming body are outside that server-processing total.

Measured plans showed text being carried through completeness sorts (estimated width about 544 bytes; a 27,616 KB disk sort), corpus-wide windows/aggregation, cardinality underestimates and many vector/content primary-key probes. Lexical queries additionally tokenize every candidate at request time. There is no persisted chunk lexical index.

The revised SQL materializes only text-dependent scalars before coverage windows, keeping text out of those sorts. Plans with one declared membership use a separate exact validation branch: one matching plan, ordinal zero, start zero, end equal to actual content length, nonblank text, matching vector and **no extra ordinal**. Multi-membership plans retain full coverage, count, ordinal, geometry and vector checks. This works for every selected strategy; it does not assume `whole_passage` always produces one chunk. Exact diversity adds an ordered representative-selection cost. No filter or completeness condition is bypassed, no DB parameter is changed, and no migration is introduced.

### Local performance evidence

Disposable PostgreSQL 18.4 / pgvector 0.8.5, `work_mem=4MB`, cached pinned image, 50,000 unique synthetic texts/ranges and dense 768-dimensional tied vectors. PLAIN vector storage was applied **only to the disposable fixture**, equally for both variants, consistent with the previous inline-storage experiment. This is not a production storage change. Approximately 402 characters per passage; 20 candidates/channel and hybrid final limit 10. Both variants run on identical rows, with one warm-up followed by ten samples; P95 uses nearest rank. Baseline module SHA-256: `13c2ac4724784ee418c574defea218ab791f3abf1c696fd76d961dc75d004b30`.

Exploratory paired measurement (milliseconds, p50 / p95):

| Channel | Before | After | Server execution before / after |
|---|---:|---:|---:|
| Semantic | 930.8 / 987.5 | 872.7 / 960.6 | 875.3 / 885.6 |
| Lexical | 3283.3 / 3533.6 | 2714.6 / 3077.8 | 3081.9 / 2696.0 |
| Hybrid + RRF | 3147.3 / 3233.5 | 3028.2 / 3301.3 | 3185.6 / 3039.0 |

This run overlapped some disposable-test work, so a follow-up nonoverlapping run is required for the final performance conclusion. Even here, hybrid P95 regressed and semantic server execution did not improve: no across-the-board latency claim is justified. Raw measurements and plans are in ignored `.cache/retrieval-review/final/`. All compared ordered result/provenance/score signatures were identical on this unique-content fixture; duplicate-heavy behavior is separately verified by regression tests. The first narrowing-only attempt also gave mixed semantic results, so narrowing alone was not declared a success.

Remaining large costs are full exact vector scanning, complete-plan validation, joins/probes and request-time lexical parsing. These changes do **not** achieve fast indexed full-corpus retrieval. An appropriate next acceptance experiment after an approved lexical index is a warm local exact-term p95 below 200 ms with at least 100 representative samples, and a separately measured exact semantic baseline. This is a proposed objective, not a measured result or production SLA. Do not replace exact semantics with ANN or trust an activation pointer alone to meet a target.

### BM25 decision boundary — no migration executed

Research checked on 2026-10-29:

- [Cloud SQL supported extensions](https://docs.cloud.google.com/sql/docs/postgres/extensions) list `pg_textsearch` for PostgreSQL 17+. They do not list ParadeDB `pg_search`. Managed compatibility must be checked against the actual instance's version and available extensions; local PostgreSQL 18.4 does not establish Cloud SQL's version.
- [Cloud SQL pg_textsearch instructions](https://docs.cloud.google.com/sql/docs/postgres/pg-textsearch) require enabling `cloudsql.enable_pg_textsearch`, then installing the extension and building a BM25 index. No such changes were made.
- [pg_textsearch upstream](https://github.com/timescale/pg_textsearch) documents BM25 scoring, expression indexes limited to immutable expressions, filtering behavior, memory/maintenance requirements and corpus-statistics effects under row security. Actual installed extension versions must be tested rather than assuming upstream main features exist in Cloud SQL.
- [PostgreSQL ranking documentation](https://www.postgresql.org/docs/current/textsearch-controls.html#TEXTSEARCH-RANKING) describes `ts_rank`; the existing implementation remains labeled `keyword_rank`, not BM25.

| Option | Correctness / indexing | Operations, synchronization and cost | Recommendation |
|---|---|---|---|
| `pg_textsearch` on derived unique canonical chunk rows in existing PostgreSQL | True indexed BM25 at the same range granularity as semantic retrieval. Join authorized complete memberships before final top-K; hydrate an eligible occurrence. | Needs extension flag/installation approval plus a derived chunk-text store, deterministic backfill and maintenance transaction policy. Adds local storage, WAL, index build CPU and extension maintenance; no second service or vector regeneration. | Preferred design to review next. |
| BM25 index on `contents.text` alone | Valid **document-level** BM25, but scores/limits whole content while current retrieval ranks arbitrary chunk ranges. A cross-table substring cannot be used as an immutable index expression. | Smaller DDL, but silently changes ranking granularity or incorrectly applies document statistics to chunk scoring. | Do not substitute it for chunk BM25 without approving a document-level retrieval change. |
| Project-owned term-frequency/postings/statistics tables in PostgreSQL | Can implement genuine BM25 with tests for IDF, saturation and length normalization. | Larger migration, scoring/maintenance code, backfill and update/delete synchronization responsibilities; index CPU/storage. | More maintenance than using a supported extension. |
| External BM25 service, e.g. Lucene-based engine | Genuine indexed BM25 with mature retrieval. PostgreSQL must remain authoritative for authorization, completeness and citations. | Additional persistent service/billing, replication and deletion/activation synchronization; stale candidates need eligibility handling **before** final limits. | Unnecessary initial infrastructure unless local extension design fails. |
| On-the-fly BM25 corpus statistics | Formula can be correct, but requires corpus scans and an explicit statistics population. | No new service, but repeats expensive parsing/statistics per query and misses indexed-latency objective. | Do not add an unmeasured full-scan replacement. |

Approval needed: extension enablement/availability verification, the derived text/index layout and backfill/maintenance design, and the BM25 statistics population (unique canonical ranges vs occurrences; shared/private/active corpus). Global statistics can affect scores through private rows even if no private text is returned; do not claim result filtering alone prevents that side channel. Keep result eligibility before top-K and existing RRF. No BM25 scoring or index was implemented in this task because those decisions exceed the approved schema-preserving work.

### Connection resilience and diagnostics

The earlier import's exact cause cannot be recovered from exception type/timing alone. API composition already has pre-ping, bounded acquisition, a statement timeout and connection recycling. Repository calls own short transactions and retain SQLAlchemy/psycopg exceptions through chaining. They do not hold transactions during model calls or broadly replay unknown commits. The corpus validator has separate pool settings; API pooling findings must not be projected onto it or onto a missing earlier Vertex-specific operator script.

New real-local-backend tests demonstrate recovery from a killed idle pooled connection using existing pre-ping, and mid-transaction disconnection with invalidation, preserved driver cause, complete rollback, and explicit idempotent replay. No pooling/retry/concurrency/transaction behavior was changed.

The current `process_corpus()` report now records `register_document`, `build_chunk_plan` or `save_chunk_plan`, an underlying exception class, validated SQLSTATE, connection-invalidation flag, a static allowlisted explanation, and the existing failed source locator. Raw exception messages, diagnostics, SQL and parameter objects are never serialized. Error re-raising and checkpoint/replay behavior remain unchanged. This improves the code present in this checkout; it does not recreate absent historical bulk scripts or identify the old failure retroactively.

### Evaluation scope and deployment implications

The existing fixed 40-question evaluator now accepts an explicit `keyword` run using `execute_keywords()`, alongside unchanged semantic/hybrid modes. Its actual PostgreSQL integration test runs all three with deterministic fake vectors over the fixed 218-occurrence corpus. This validates pipeline/metric execution, not Gemini quality. Occurrence-level gold matching is deliberately unchanged: a different occurrence of identical canonical text can still lose strict source-occurrence Recall@K credit. That limitation must accompany future diversity comparisons; do not quietly relax gold source identity to improve scores.

No safely running local copy of the completed production corpus and matching full query-vector cache was established. The persistent evaluation script also generates live query embeddings. Therefore full-corpus Recall@5, Recall@10 and MRR, real compound-answer accuracy, and actual model search-drift improvements remain **unmeasured** here. No paid model requests or Cloud SQL workload was run.

No migration, dependency, cloud setting, model/profile, ingestion deduplication, document ID, schema, commit, push or deployment changes. A future deployment needs normal application rollout authorization only for these schema-preserving changes. BM25 needs separate architecture, extension and migration authorization.

### Manual verification after a separately authorized rollout

1. Check health and configured serving profile. Exercise Quick Answer with each Maheen/Alice clause separately, then the original compound question. Inspect both excerpts, version IDs and original source locations; record embedding/generation counts and stage timings.
2. Run the compound investigation with bounded budgets. Confirm searches target unresolved facts; at E15/search exhaustion, confirm tools are disabled and a final decision is attempted only within budget. Verify insufficiency and INCOMPLETE remain distinguishable.
3. Check a genuine dependent question and an unanswerable question. Inspect evidence for the required identity link rather than accepting citation syntax as semantic proof.
4. Exercise repeated content, shared sources, two authorized principals, deletion and inactive/incomplete versions. Confirm distinct content can fill top-K and representative citations never disclose ineligible occurrences. No production mutation is part of this verification task.
5. Collect cold-start and at least 100 warm request timings under an approved call budget; separate model/network time from SQL. Use cached query vectors and a read-only, attested corpus snapshot for paired 40-question retrieval metrics before enabling a new lexical ranker.

### Files changed

Application: `application/investigation_records.py`, `application/investigations.py`, `application/ports/evidence_repository.py`, `application/use_cases/answer_question.py`, `application/use_cases/search_evidence.py`, new `application/timing.py`.

Infrastructure: `infrastructure/api/app.py`, `infrastructure/api/server.py`, `infrastructure/generation/gemini_answer_generator.py`, `infrastructure/generation/gemini_investigation_model.py`, `infrastructure/persistence/postgresql_evidence_repository.py`, new `infrastructure/persistence/diagnostics.py`.

Supporting code: `ingestion/corpus_processing.py`, `evaluation/retrieval.py` (all paths above under `src/investigation_engine/`), `scripts/benchmark_retrieval.py`.

Tests: `tests/unit/application/test_answer_question.py`, `test_investigations.py`, `test_search_evidence.py`, new `test_timing.py`; `tests/unit/infrastructure/test_gemini_investigation_protocol.py`, new `test_repository_diagnostics.py`, `test_container_runtime.py`, `test_rag_api.py`; `tests/unit/ingestion/test_corpus_processing.py`; `tests/integration/infrastructure/persistence/test_hybrid_retrieval.py`, `test_semantic_retrieval.py`, `test_retrieval_evaluation.py`, new `test_connection_resilience.py`.

Documentation: this report, its companion measurements JSON, and `docs/README.md`.

### Final paired measurement and stage checks

The repeat used the same frozen baseline and final SQL against identical 50k rows, without concurrent database tests. Ten warm samples per variant/channel; nearest-rank P95. Each invocation uses `NullPool`, so these totals include a fresh connection rather than a production pool checkout. No OS/shared-buffer cache flush was attempted. "First" below is the first invocation of each variant in that run, not a Cloud Run cold start or an independent cold-storage experiment.

| Channel | First before / after | Warm p50 before / after | Warm p95 before / after | EXPLAIN server execution before / after |
|---|---:|---:|---:|---:|
| Semantic | 1072.8 / 965.4 | 949.3 / 862.5 | 1050.4 / 910.9 | 943.0 / 774.5 |
| Lexical | 3465.0 / 2797.8 | 3114.3 / 2671.4 | 3240.1 / 2890.2 | 2824.7 / 2797.7 |
| Hybrid + RRF | 3006.0 / 3092.1 | 3275.3 / 3127.1 | 3456.6 / 3214.7 | 3211.3 / 2982.4 |

Warm median improvements in this run were approximately 9.1%, 14.2% and 4.5% respectively. Hybrid's first call regressed. Semantic temporary blocks written fell from 8,691 to 2,261 and hybrid from 8,690 to 2,908, while representative selection retained exact expected results. No universal cold/tail improvement is claimed.

A separate ten-sample run verified the timing collector after discovering that Alembic's logging configuration disables existing application loggers. The benchmark now temporarily enables and restores its own collector. Selected measured stages (p50 / p95 ms):

| Mode | Acquisition | SQL roundtrip | Python hydration | RRF |
|---|---:|---:|---:|---:|
| Semantic | 33.64 / 63.44 | 1318.81 / 1397.30 | 1.18 / 1.69 | — |
| Lexical | 30.14 / 89.77 | 3078.43 / 3257.80 | 1.29 / 1.62 | — |
| Hybrid | 20.15 / 36.64 | 3088.10 / 3533.43 | 1.92 / 4.00 | 0.58 / 0.87 |

This separate run was slower for semantic (total p50 1377.5 ms) and lexical (3121.7 ms); hybrid p50 was 3135.6 ms. This variability limits extrapolation from a small local benchmark. Do not splice the separate stage and paired distributions into a single claimed request. In both, SQL work dominates small reconstruction/fusion costs; neither establishes fast indexed retrieval. Embedding/generation stages were verified with offline providers and HTTP trace tests, not measured against Gemini.

The [sanitized measurement artifact](retrieval-correctness-2026-10-29-measurements.json) includes environment, code hashes, raw samples, stage sample counts, first-call values, EXPLAIN totals and output signature hashes. Full synthetic plans remain in ignored `.cache/retrieval-review/verified/` and `.cache/retrieval-review/stages/`.

Reproduce with the cached image and a frozen baseline module from the recorded revision:

```powershell
uv --cache-dir .cache/uv run --no-sync python scripts/benchmark_retrieval.py --sizes 50000 --samples 10 --baseline .cache/retrieval-review/baseline_repository.py --embedding-storage plain --output .cache/retrieval-review/repeat
```

No API keys or application database settings are used by this benchmark. The baseline module is an ignored local copy, not a runtime dependency.

Final unit verification: **603 passed** (12.64 seconds). Final disposable PostgreSQL integration verification: **168 passed, 4 skipped** (153.87 seconds). Total: **771 passing tests**, 32 more than the baseline. Ruff lint, formatting (176 files) and `git diff --check` pass. The configured WPS check remains red: **3,171 findings**, compared with **3,055** from the clean commit using the same `src scripts tests` scope and per-file configuration. This includes new feature/test complexity and naming findings as well as existing debt; it is not a clean WPS result. No blanket ignores or unrelated style refactor were introduced. The upstream google-genai deprecation warning remains.

Additional SQL regression: the entire compound Maheen/Alice question has no all-terms lexical match in either disjoint passage, while both targeted clauses do. Quick Answer's balanced retrieval then resolves both real PostgreSQL evidence rows through one fake generation call. This isolates a concrete retrieval weakness without claiming that a fake generator proves live answer correctness.
