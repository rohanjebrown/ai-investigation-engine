# Structured evaluation and benchmarking

Use `scripts/evaluate.py` or `python -m investigation_engine.evaluation.cli` from the repository root in the existing environment. No new dependencies, corpus, indexing or downloads are introduced. Input must be the pinned local `data/raw/2wiki/dev.parquet` (12,576 dev questions).

## Defaults and safety

Default: semantic HNSW, indexed BM25 and application RRF; exact cached question vectors; shared-only scope; persisted serving profiles; final limit 20; hybrid channel limits 20/20; HNSW candidates/ef=400, iterative off; RRF k=60; one warmup, three repetitions, concurrency one. Other concurrency values are rejected. Connections enforce read-only transactions and a session-local 30-second statement timeout. Preflight verifies the target database, profiles, retrieval backends, selected gold availability and at least 100,000 current shared pinned 2Wiki occurrences. A question-specific miniature corpus cannot silently replace the existing corpus.

Voyage is never constructed. No reranker is supplied, irrespective of process reranker settings. Generation requires an answer mode, `--allow-generation` and a positive `--max-generation-requests`. The cap includes warmups and all selected answer modes; adapter/SDK retries are disabled. Attempted adapter calls and actual adapter-reported provider RPC counts are recorded separately. Query/document embeddings are never generated, including when generation is enabled. Missing vectors, including adaptive Investigation followups, produce failed rows without inference fallback.

Every run requires a fresh directory. Default output is ignored `reports/evaluation/structured/<run_id>/`. Aggregates contain no passages, credentials, SQL or bind values. Full evidence text is written only with `--local-diagnostics`. The evaluator does not enable application full-payload JSONL tracing or auto-discover old traces; an independently enabled debug trace follows the application's existing opt-in policy.

## Cached vectors

Cache JSON contains `version: 1`, an existing `EmbeddingConfig` dictionary under `configuration`, and `vectors`, an exact-question-to-768-numeric-values dictionary. Existing `storage_vector` validates all vectors. Never relabel a vector for another question. Explicit trace imports read only `retrieval.search` and `retrieval.query_vector`, validating declared embedding profiles. Flat legacy dictionaries need `--embedding-profile profile.json` or a preceding profiled cache. Conflicting profiles/vectors fail.

The initial task prepared ignored `.cache/evaluation/query-embeddings.json` with six already cached questions: five regressions and the previous Polish-Russian War question. The subsequent 40-question benchmark added 40 exact query vectors after the user confirmed Free tier and disabled billing; all six originals were preserved. Its preparation receipt is `.cache/evaluation-40/embedding-audit-ready.json`. Preparation is separate from the evaluator, which remains cache-only. Two explicit traced vectors were selected into `.cache/evaluation/regression-trace-vectors.json`, with source trace hashes retained. To reproduce the original six-vector cache at a fresh path:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py cache --source .cache/hnsw-validation/query-vectors.seed.json --source .cache/evaluation/regression-trace-vectors.json --source .cache/lexical-review/new-query-vectors.json --output .cache/evaluation/query-embeddings-copy.json
```

For another local trace, explicitly supply `--source .debug/traces/ACTUAL_TRACE.jsonl` with a profiled cache or profile file. These local inputs are not committed; another checkout must provide compatible vectors. Lexical-only evaluation can omit a cache by supplying `--embedding-profile`; stored embedding compatibility still determines eligibility.

## Commands

PowerShell, from the project root; `uv run --no-sync python` can replace `.\.venv\Scripts\python.exe`. Existing `.env` supplies database access and serving chunking ID. Optional `--expected-database` and `--chunking-id` bind explicit targets. Always choose fresh output paths.

Three-question retrieval smoke:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py run --set smoke --modes semantic lexical hybrid --warmups 1 --repetitions 1 --output reports/evaluation/structured/my-smoke
```

Five maintained regressions, repeated paired modes, then comparison:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py run --set regression --modes semantic lexical hybrid --warmups 1 --repetitions 3 --output reports/evaluation/structured/my-regression
.\.venv\Scripts\python.exe scripts/evaluate.py compare reports/evaluation/structured/my-regression reports/evaluation/structured/my-regression --left-mode lexical --right-mode hybrid --output reports/evaluation/structured/my-regression/lexical-vs-hybrid.json
```

Persist a seeded type-stratified selection, then reuse exactly its IDs and an already supplied cache:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py select --sample-size 40 --seed 20261106 --output .cache/evaluation/sample-40.json
.\.venv\Scripts\python.exe scripts/evaluate.py run --selection .cache/evaluation/sample-40.json --modes semantic lexical hybrid --cache .cache/evaluation/sample-40-vectors.json --repetitions 3 --output reports/evaluation/structured/sample-40
```

Alternatively `--set sample --sample-size 40 --seed 20261106` selects and persists the sample during the run. Types are shuffled deterministically and sampled round-robin, balancing types rather than preserving natural prevalence. More than 100 questions, including a persisted selection, requires `--large-run`:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py run --set sample --sample-size 1000 --seed 20261106 --large-run --cache .cache/evaluation/sample-1000-vectors.json --output reports/evaluation/structured/sample-1000
```

`--set legacy` preserves the existing 40-case suite: derived factual questions, insufficient-evidence and conflict controls. It queries the same full corpus and reports absent controlled evidence rather than inserting it. Exact question vectors are still required. Historical evaluation scripts and suites remain unchanged.

Optional Quick Answer, only after deciding to spend generation requests:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py run --set regression --modes quick_answer --answer-retrieval-mode hybrid --warmups 0 --repetitions 1 --allow-generation --max-generation-requests 5 --output reports/evaluation/structured/my-quick-answer
```

Optional Investigation, same question IDs; unknown adaptive query vectors fail without an embedding request:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py run --set regression --modes investigation --answer-retrieval-mode hybrid --warmups 0 --repetitions 1 --allow-generation --max-generation-requests 30 --output reports/evaluation/structured/my-investigation
```

Both answer modes can share one run/cap. Application evidence/context/turn/search/request budgets come from existing settings and are recorded. `--final-limit` controls retrieval-only output, not answer-use-case budgets. `--generation-model` overrides the configured model; `--timeout` bounds a whole request. Neither answer command was executed in this task.

Compare saved runs without database/provider access:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py compare reports/evaluation/structured/run-a reports/evaluation/structured/run-b --left-mode hybrid --right-mode hybrid --output reports/evaluation/structured/comparison-a-b.json
```

`--local-diagnostics` deliberately adds `grounding_review.jsonl`: question/reference/generated answer, selected passages and sources, citation IDs, and pending correctness/support review fields. Citation IDs identify the cited passages within this local file. Text remains absent from aggregate JSON/Markdown.

## Gold mapping and metrics

The inspected Parquet has `_id`, `type`, `question`, `answer`, and JSON-encoded `context`, `supporting_facts`, `evidences`. The maintained selection stores real IDs; capitalization-only overrides for Princess Alexandra and James FitzGerald retain the established spellings and exact cached vectors. Original dataset questions remain recorded.

The same ingestion `extract_2wiki` adapter reconstructs canonical passage text and sentence offsets. A supporting title must identify exactly one context occurrence and its sentence index must be valid. Gold records contain exact canonical SHA-256, full half-open sentence bounds and source occurrence. Ambiguous/missing titles and invalid indices remain unresolved facts. Duplicate gold labels count once, with duplicates recorded. No title-only credit is given.

Primary canonical coverage requires the returned chunk to have the same content hash and fully contain the gold sentence interval. A different authorized occurrence of identical content can match. Secondary strict-occurrence recall reuses existing `covered`: source identity/revision/locator, content hash and sentence span/locator must match. Preflight uses actual shared current contents and existing candidate-scoped eligibility to distinguish absence, ineligibility/no wholly covering eligible chunk and eligible evidence.

| Metric | Definition |
| --- | --- |
| Recall@5/@20 | Fraction of unique gold facts fully contained in the first K final chunks; absent/unresolved facts remain in the denominator. |
| Complete support@5/@20 | All annotated facts covered in the first K; false for empty gold. |
| MRR@20 | Reciprocal rank of the first chunk covering any gold fact, or zero. |
| Exact canonical duplicates | Excess repeated `(content_sha256,start,end)` keys / final hit count; zero when empty. Overlapping different ranges remain distinct. |
| Distinct source documents | Number of final `document_id` values; canonical-content count is also retained. |
| Channel counts | Actual eligible repository hits before fusion/selection, distinct from internal discovery prefix limits. Multiple Investigation searches are retained and counted. |
| Similar-passage diagnostic | Distinct canonical ranges with lowercase Unicode word-token 5-shingle Jaccard similarity at least 0.85; approximate diagnostic only, not semantic similarity or relevance. |
| Answer EM/token F1 | Original 2Wiki text normalization and multiset token overlap, including its yes/no/noanswer rules, applied to the whole returned answer. |

Facts are classified as unresolved, absent, present-but-ineligible, present-not-retrieved, candidate-only/excluded-from-final, or retrieved-final. After a failed response, candidate facts are marked final-selection-unavailable rather than claiming deliberate exclusion.

Answer scoring follows the [original official 2Wiki evaluator](https://github.com/Alab-NII/2wikimultihop/blob/main/2wikimultihop_evaluate.py): lowercase, remove ASCII punctuation/articles, collapse whitespace. The pinned text-only snapshot has no answer-ID/alias map, so newer alias-aware scoring is not claimed. No phrase extraction, scorer inflation or paid evaluator is added. Explanations/inline citations may lower EM/F1 despite factual correctness; these are text metrics, not automatic incorrectness judgments. Answered, abstained and failed/incomplete counts are separate. Citation identifier validity is structural and proves neither factual support nor correct relationship interpretation.

Aggregates include all measured attempts, including errors and absent/unresolved gold. Undefined quality values contribute zero to conservative operational means; annotated/reference counts and fact states are explicit. Per-question no-gold metrics stay null. Warmups are saved but excluded from aggregates. Repetitions do not add independent questions. With a final limit below 20, Recall@20 describes only the retained output, not a hidden 20-result pool.

## Timing, artifacts and comparisons

Existing `stage_timing` logs are observed per request. SQLAlchemy events record setup/driver SQL latency without retaining SQL/parameters. An evaluation-only repository decorator records repository duration and actual channels. Application RRF is reused. Investigation's existing admission/registry operation has an `evidence_selection` timer. Quick Answer exposes its existing preparation through `AnswerQuestion.prepare_evidence`; `execute` calls that same preparation before generation. The selector can report its existing admission decisions without changing their order or budgets.

Each retrieval-only attempt measures candidate retrieval at `--final-limit` (normally 20), then separately probes actual Quick Answer preparation at `--serving-max-evidence` (default 5) and `--serving-max-context-chars` (default 12000). The latter repeats the real serving search, decomposition, canonical deduplication and serialized-JSON context admission, without constructing a generator. BM25 uses an adapter to the same preparation port. Candidate latency and serving-preparation latency are independent observations: never add them as a production latency. Context characters include the complete serialized request, not just passage text. The selector takes the first five positions before context checks and does not refill a rejected position.

Rows retain admitted evidence, support coverage, full context size, rejection observations, individual hybrid-channel metrics, and gold-fact pipeline states. The actual five-result search may differ from the first five of the independent twenty-result search. A ranking-prefix identity flag exposes that difference; upstream count-limit attribution stays unknown when the prefix differs. Reported loss from ranked output therefore need not mean the context selector rejected that fact. `analysis.json` and the Markdown report provide per-question comparisons and examples. `--embedding-audit` attaches a whitelisted preparation receipt and its hash without making embedding calls.

Attempted retrieval actions and actual repository calls are separate: a missing cached vector still counts as an attempted search, while issuing no database retrieval. Investigation result search records additionally include rejected/repeated tool requests when the application returns them.

Collected boundaries: query-cache lookup, connection acquisition, HNSW transaction-local setup, driver SQL, repository work, RRF, answer evidence admission, total retrieval, individual generation adapter/API stages and total request. Null stages mean no separate observation/not applicable, e.g. lexical has no query embedding/RRF and SQL-limited retrieval has no separate context admission. Provider embedding network is always excluded; attempted answer generation network is included. Failures retain duration and sanitized categories.

Timings are inclusive and overlap: never sum them to reconstruct total time. Cursor timings include driver/server/transfer behavior; repository roundtrip timers also encompass fetch/decoding. `postgres_explain_execution_ms` stays explicitly null. This runner never issues EXPLAIN automatically or substitutes application latency for server-only execution time. Earlier separate BM25 EXPLAIN artifacts retain their own measurement scope.

Statistics: minimum, ordinary median, arithmetic mean, nearest-rank p90/p95, maximum and count. Both all-attempt and successful-request distributions are saved. Empty distributions have null values/count zero.

Each run saves `manifest.json`, `selection.json`, `questions.jsonl` (references), `retrieval_results.jsonl`, `answer_results.jsonl`, `summary.json`, `analysis.json`, `report.md` and optional `grounding_review.jsonl`. Per-question rows retain configuration, canonical/occurrence IDs, ranks/scores/channels, matches, selection, timings, status/errors and answer/citations. Manifests record commit/dirty flag/source hashes, input revision/hash, IDs/selection hash, corpus counts/preflight snapshot, profiles, backend/candidate/RRF/reranker settings, generation budgets/model, seed, repetitions, cache coverage/hash and software/extension versions. JSON reports use atomic replacement. A run remains `finalizing` until summaries and Markdown are successfully written; a partial run must not be treated as complete.

The corpus is not frozen across requests. Preflight attests counts/selected gold at its snapshot, not every corpus byte for the whole run. Comparisons flag differing manifests/unmatched IDs, metric/latency differences, newly complete/incomplete questions, evidence/rank/score changes and answer EM/F1 changes. They do not assert statistical significance or causal attribution. HNSW remains approximate; BM25 exact completion and ranking are unchanged.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/evaluation tests/unit/application -q
.\.venv\Scripts\python.exe -m pytest tests/integration/infrastructure/persistence/test_bm25_retrieval.py -k structured_evaluation -q
.\.venv\Scripts\python.exe -m pytest tests/integration/infrastructure/persistence/test_answer_quality.py tests/integration/infrastructure/persistence/test_retrieval_evaluation.py -q
.\.venv\Scripts\ruff.exe check src/investigation_engine/evaluation src/investigation_engine/application/investigations.py scripts/evaluate.py tests/unit/evaluation/test_structured_evaluation.py tests/integration/infrastructure/persistence/test_bm25_retrieval.py
```

Unit providers are deterministic fakes; integration tests use the existing disposable Docker PostgreSQL image. No paid calls occur in these tests. See [initial results](structured-evaluation-2026-11-06.md) and the [40-question serving evaluation](40-question-serving-evaluation-2026-11-06.md) for measurements and remaining coverage.
