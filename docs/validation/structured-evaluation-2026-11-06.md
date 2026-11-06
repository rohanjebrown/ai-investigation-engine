# Initial structured evaluation — 6 November 2026

## A. Implementation and interfaces

Implemented the local `scripts/evaluate.py` CLI, also available as `python -m investigation_engine.evaluation.cli`. Commands are `select`, `cache`, `run`, and `compare`. No new dependencies, framework, database schema, migration or corpus were added. Existing retrieval, cached query validation, 2Wiki ingestion/provenance adapter, strict fact matcher, answer result adapter and application timers are reused.

| File | Responsibility |
| --- | --- |
| `src/investigation_engine/evaluation/cli.py`, `scripts/evaluate.py` | Operator configuration, existing service composition, read-only engine, generation guards and manifests. |
| `evaluation/dataset.py` | Pinned Parquet inspection, deterministic type-stratified IDs, real regression IDs, verified gold sentence coordinates and preserved legacy cases. |
| `evaluation/metrics.py` | Canonical/strict sentence coverage, complete support, MRR, duplicates, source counts, official text EM/F1 and descriptive statistics. |
| `evaluation/telemetry.py` | Evaluation-only observation of existing timers, SQL operations, repository channels and bounded generation/RPC counts. |
| `evaluation/postgres.py` | Actual full shared-corpus/profile attestation and gold presence/eligibility, all read-only. |
| `evaluation/runner.py` | Sequential warmups/repetitions through actual services, per-question errors, streaming exports and optional review artifacts. |
| `evaluation/reporting.py` | All-attempt summaries, latency distributions, Markdown and saved-run comparisons. |
| `evaluation/cached_queries.py` | Existing cached provider retained; explicit local cache/trace import, profile checks and typed cache misses added. |
| `data/samples/evaluation/regression-selection.json` | Five maintained questions, real dataset IDs, seed/revision/hash and two capitalization-only overrides. |
| `application/investigations.py` | One timing context around existing evidence admission/registry work. No logic/order changes. |
| `tests/unit/evaluation/test_structured_evaluation.py` | Deterministic scorer, mapping, selection, cache, privacy, error, budget and comparison regressions with fake AI. |
| `tests/integration/infrastructure/persistence/test_bm25_retrieval.py` | Disposable read-only preflight regression for public/private/incomplete evidence and safe SQL observations. |
| `docs/validation/structured-evaluation.md`, this report, `docs/README.md` | Exact commands, definitions, scope, results and limitations. |

Paths beginning `evaluation/` above are under `src/investigation_engine/`; application path is under the same package. Existing evaluation suites/scripts and all additional historical cases remain intact. No commits, pushes, deployments, Gemini/Voyage requests or staging writes were performed.

## B. Commands and artifacts

See the [CLI guide](structured-evaluation.md#commands) for exact smoke, all-mode, seeded sample, larger-run, Quick Answer, Investigation and saved-run comparison commands. The default cache is `.cache/evaluation/query-embeddings.json`. It contains six existing vectors; missing entries never trigger model calls.

Executed after the unit checks:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py run --set smoke --modes semantic lexical hybrid --warmups 1 --repetitions 1 --output reports/evaluation/structured/2026-11-06-smoke
.\.venv\Scripts\python.exe scripts/evaluate.py run --set regression --modes semantic lexical hybrid --warmups 1 --repetitions 3 --output reports/evaluation/structured/2026-11-06-baseline
.\.venv\Scripts\python.exe scripts/evaluate.py compare reports/evaluation/structured/2026-11-06-baseline reports/evaluation/structured/2026-11-06-baseline --left-mode semantic --right-mode hybrid --output reports/evaluation/structured/2026-11-06-baseline/semantic-vs-hybrid.json
.\.venv\Scripts\python.exe scripts/evaluate.py compare reports/evaluation/structured/2026-11-06-baseline reports/evaluation/structured/2026-11-06-baseline --left-mode lexical --right-mode hybrid --output reports/evaluation/structured/2026-11-06-baseline/lexical-vs-hybrid.json
```

Earlier completed regression runs remain at `reports/evaluation/structured/2026-11-06-regression/` and `2026-11-06-regression-final/`. The final baseline repeated the protocol after completing RPC accounting, bounded memory retention and separate attempted-search/repository-call counts, binding measured artifacts to final source hashes. A cache miss counts as an attempted search while making no repository call. The first smoke attempt found a Core-versus-ORM metadata-key mismatch in preflight hydration before running any question; this was fixed and is covered by the new disposable regression.

Final run ID: `20261106T140238-3fc27825`. Its directory contains manifest, persisted selection, reference questions, per-question retrieval/answer JSONL, summary JSON and Markdown, plus both comparison JSON/Markdown pairs. `answer_results.jsonl` is empty because no answer evaluation ran. Diagnostics were disabled, so no passages or grounding review text were retained. Generated run artifacts and caches are ignored local files, not new tracked data fixtures.

## C. Metric definitions

Recall@5/@20 measures the fraction of unique annotated supporting sentences fully contained by the first K final chunks. Complete support requires all facts. MRR@20 uses the first chunk covering any fact. Exact canonical duplicate rate counts excess repeated hash/start/end keys; distinct source-document and channel candidate counts are recorded. Near-duplicate similarity is explicitly unavailable.

All measured attempts stay in operational means, including failures, absent evidence and unresolved mappings; warmups are retained separately. Absent/unresolved facts stay in each gold denominator. No-gold per-question metrics are null, with annotated/reference counts reported and conservative zero contributions to aggregate operational means. Repetitions do not create independent questions.

Optional answer evaluation scores the unchanged returned answer string with official original 2Wiki text normalization/EM/token F1. It separately records answered/abstained/failed/incomplete status, citations, structural validity, retrieval/generation counts, usage and timing. Explanatory answers can score low without being factually incorrect. No heuristic answer extraction or paid judge is used; semantic correctness/support and relationship interpretation require manual review. Full definitions and the primary scorer source are in the [guide](structured-evaluation.md#gold-mapping-and-metrics).

Latency statistics include minimum, median, mean, nearest-rank p90/p95, maximum and count. Existing timers/cursor events expose inclusive, overlapping boundaries. Application-observed SQL is never reported as PostgreSQL EXPLAIN execution time; the latter is null in these runs.

## D. Dataset and supporting-fact mapping

Verified the actual local Parquet SHA-256 `c0d8b60b9026b728fb07ad74c5252a0f188f6942e8ba5c02df4dfa369502ea8d`, dataset `xanhho/2WikiMultihopQA`, dev revision `612bc5039a457880d9e7d84c3b0a4cf154b70e4f`. Supporting titles resolve against each example's actual context and sentence indices, using the same canonical-text/sentence-offset extractor as ingestion. Gold labels/references are separate from searchable content and are never passed to generation.

| Maintained question | Actual dataset ID |
| --- | --- |
| Who is Wisigard's father-in-law? | `ab94845c0baf11ebab90acde48001122` |
| Who is the maternal grandfather of Princess Alexandra of Hanover (born 1999)? | `77f5a3dc0bb011ebab90acde48001122` |
| Where did M. K. Muthu's father die? | `21906d320bde11eba7f7acde48001122` |
| Who lived longer, Friedrich Paschen or Ernst Bessey? | `672e556408de11ebbd9eac1f6bf848b6` |
| Who is the paternal grandfather of James FitzGerald, 8th Earl of Desmond? | `474ea44a0bb011ebab90acde48001122` |

The maintained set has three inference, one compositional and one comparison question. All ten supporting facts resolved and were present in eligible indexed chunks at preflight; none were dropped. Primary coverage requires canonical hash equality plus full sentence containment. Secondary strict-occurrence coverage additionally checks original source identity and sentence locators, so canonical deduplication through another occurrence is explicit rather than mistaken for missing content. Title matches alone never count.

Read-only preflight attested **125,760 shared current occurrences and 56,680 distinct canonical contents** in the existing corpus; all 125,760 shared occurrences belonged to the pinned dev dataset. This is the full existing shared corpus, including distractors, not a miniature supporting-document corpus. PostgreSQL 18.6, pgvector 0.8.5 and pg_textsearch 1.3.1 were used. Full serving profiles, software versions, database snapshot fingerprint, git commit/dirty flag and source hashes are in the manifest.

## E. Initial measured retrieval results

Final protocol: identical five IDs/corpus/profiles, shared scope, cached vectors, final 20, semantic/lexical channel limits 20/20, HNSW ef/candidates 400, iterative off, BM25 exact completion, RRF k=60, no reranker. One warmup per question/mode followed by three measured repetitions, alternating mode order. **60 successful calls: 15 warmups and 45 measured calls**, with no provider network requests. Each mode has 15 measured observations across five independent questions. Selected identities were stable across these repetitions.

| Mode | Recall@5 | Recall@20 | Complete@5 | Complete@20 | MRR@20 | Median ms | p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Semantic HNSW | 0.80 | 0.90 | 0.60 | 0.80 | 1.00 | 465.03 | 604.09 |
| BM25 | 0.60 | 0.70 | 0.20 | 0.40 | 0.75 | 451.07 | 548.85 |
| Hybrid RRF | 0.70 | 0.90 | 0.40 | 0.80 | 0.90 | 644.78 | 755.21 |

| Mode | Minimum ms | Mean ms | p90 ms | Maximum ms | Mean distinct documents | Semantic / lexical candidates |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Semantic HNSW | 407.44 | 487.98 | 603.85 | 604.09 | 19.8 | 20 / 0 |
| BM25 | 384.01 | 451.15 | 507.25 | 548.85 | 20.0 | 0 / 20 |
| Hybrid RRF | 564.58 | 650.76 | 726.49 | 755.21 | 19.8 | 20 / 20 |

Exact canonical duplicate rate was zero for every mode. Near-duplicate rate is unavailable. All 45 measured requests succeeded; no gold mapping was unresolved/absent/ineligible. Semantic/hybrid each covered nine of ten gold facts per repetition; BM25 covered seven. This says nothing about unannotated relevant passages or answer correctness.

| Question | Semantic Recall@20 | BM25 Recall@20 | Hybrid Recall@20 |
| --- | ---: | ---: | ---: |
| Wisigard | 0.5 | 0.5 | 0.5 |
| Princess Alexandra | 1.0 | 0.5 | 1.0 |
| M. K. Muthu | 1.0 | 0.5 | 1.0 |
| Paschen / Bessey | 1.0 | 1.0 | 1.0 |
| James FitzGerald | 1.0 | 1.0 | 1.0 |

Saved BM25→hybrid comparison identifies Princess Alexandra and M. K. Muthu as newly complete at 20, with no newly incomplete question. Wisigard's second fact is eligible but absent from both candidate channels, rather than lost by final selection. Semantic→hybrid has unchanged Recall@20/completeness but lower Recall@5/MRR on this sample. These are descriptive observations on five maintained regressions, not statistical evidence that one mode is generally better. No retrieval tuning followed these observations.

Median observed timing boundaries, milliseconds:

| Mode | Cache lookup | Acquire connection | HNSW setup | Driver SQL | Repository | RRF | Whole retrieval |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Semantic | 0.0041 | 0.347 | 166.32 | 169.02 | 464.12 | n/a | 464.87 |
| BM25 | n/a | 0.335 | 0 | 278.73 | 450.85 | n/a | 450.88 |
| Hybrid | 0.0042 | 0.317 | 176.95 | 268.97 | 642.95 | 1.05 | 644.61 |

These are independent medians of inclusive boundaries; do not add or subtract them. HNSW setup includes the observed roundtrip for its transaction-local settings. Repository time includes additional transaction/driver work. The request median above also includes outer benchmark dispatch. Connection acquisition is a warm pooled observation, not a cold-connect benchmark. Retrieval-only evidence admission is SQL-limited and has no separate context-selection timer. No EXPLAIN was executed by this evaluator; server-only SQL time remains unavailable.

## F. Verification and limitations

- **347 passed:** existing/new evaluation and application unit tests, including metrics, normalization, fact offsets/ambiguity, duplicates, empty/missing gold, cache misses, abstentions, timeout/errors, configuration serialization, comparison, payload isolation, privacy, warmup budgets and provider-counter accounting. One existing Google SDK deprecation warning.
- **1 passed:** new read-only preflight/SQL-observation integration regression, including private/incomplete/absent/unresolved evidence and rejection of miniature-corpus operator runs.
- **2 passed:** existing answer-quality and fixed-corpus retrieval integration tests, using existing disposable PostgreSQL and fake AI.
- Ruff lint/format checks and `git diff --check` passed. Relevant source hashes in the final run match the implementation.
- Local `.cache/evaluation/final-audit.json` verifies zero provider calls, one explicit SQL command per BM25 call and two per semantic/hybrid call, stable identities across repetitions, unchanged retrieval repository/RRF/search/Quick Answer/prompt bytes, and timing-only Investigation changes by AST comparison.

The five-question set is maintained for regressions and lacks bridge-comparison coverage. Live answer quality, generation latency, manual grounding judgments, adaptive uncached followups, large-sample quality, concurrent load, cold connections and automatic EXPLAIN are unmeasured. The corpus is not frozen between application requests; preflight counts/selected gold and its snapshot do not attest every corpus byte throughout the run. HNSW is still approximate. Canonical sentence coverage is not semantic claim support. Original text answer scoring lacks the newer dataset alias/ID map and can penalize explanatory answers.

Adaptive Investigation can run only while its generated queries have compatible cached vectors. Generation authorization alone does not authorize embedding inference. Legacy controlled cases absent from this full corpus are reported explicitly rather than indexed specially. Deliberate local diagnostics are required for passage-level manual review. No live generation run was executed.

## G. Next run

Persisted `.cache/evaluation/next-sample-40.json` using seed **20261106**: ten inference, ten compositional, ten comparison and ten bridge-comparison questions. Current exact cache coverage is **0/40**. Acquire/supply compatible vectors under separate explicit embedding authorization, then run this exact selection across all three retrieval modes with one warmup and three repetitions. Until that cache exists, the evaluator will report misses rather than spend provider requests or silently reduce the sample.

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py run --selection .cache/evaluation/next-sample-40.json --cache .cache/evaluation/sample-40-vectors.json --modes semantic lexical hybrid --warmups 1 --repetitions 3 --output reports/evaluation/structured/next-40
```

Keep the five-question set as a fixed regression gate and compare like-for-like saved runs before changing ranking or generation. Add a separately authorized bounded Quick Answer/Investigation run only when needed query caches and manual review capacity are available. No statistical claim should be made from the initial five-question differences.
