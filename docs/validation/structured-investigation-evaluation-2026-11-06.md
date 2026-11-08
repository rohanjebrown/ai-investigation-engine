# Structured end-to-end Investigation evaluation — 6 November 2026

The structured evaluator now runs the application's actual `InvestigateQuestion` workflow with semantic, BM25 lexical and hybrid retrieval. It observes model-directed follow-up queries, actual retrieval results, evidence accumulation, each model-turn transcript and the final-decision context. Quick Answer and retrieval-only modes remain available. No separate Investigation algorithm was implemented.

The full forty-question, three-configuration live benchmark is **not executed**: its worst-case provider demand exceeds the currently reported free daily allowance. The separately authorized, capped live smoke completed on one member of the existing forty-question selection. Two configurations failed at the generation provider; hybrid returned an answer that disagreed with the reference despite complete annotated evidence. These observations cannot support comparative quality claims across forty questions.

## Files and application boundary

All source paths below are under `src/investigation_engine/evaluation/`.

| File | Purpose |
| --- | --- |
| `cli.py` | Composes ordinary search services and `InvestigateQuestion`; supports named Investigation configurations, explicit query-embedding authorization/caps, rate settings, smoke subsets and a provider-free preflight/usage plan. |
| `runner.py` | Runs the real service sequentially, defaults answer modes to one repetition and zero warmups, preserves partial observations on errors, writes Investigation traces, and finalizes provider/cache accounting. |
| `investigation.py` | Transparent model/search-port observers, exact sentence-coverage stages, distinct evidence snapshots, limit/stop observations, aggregate Investigation metrics and worst-case request estimates. |
| `live_queries.py` | Exact compatible cache reuse; explicitly authorized misses use the ordinary live query adapter and persist successful validated vectors atomically. |
| `live_providers.py` | Evaluation-only caps and rolling RPM/TPM pacing around the existing Gemini adapters. Native payloads are forwarded unchanged, automatic retries remain disabled. |
| `telemetry.py` | Adds per-request search/model/embedding observations, generic budget checks and separately observed Investigation API durations. |
| `reporting.py` | Adds the three-configuration end-to-end comparison, stage coverage, behavior/latency distributions and per-type summaries. |
| `answer_quality.py` | Allows the explicit lexical configuration in the existing answer-result adapter. Scoring conventions are unchanged. |

Focused tests are in `tests/unit/evaluation/test_investigation_evaluation.py`; a new PostgreSQL integration case is in `tests/integration/infrastructure/persistence/test_bm25_retrieval.py`. The evaluation guide and documentation index link this report.

The application use case, prompts, tool definitions, search decisions, stopping rules, native history handling, evidence admission, retrieval SQL, HNSW, BM25, RRF, schema and ingestion are unchanged. Voyage is never constructed. Production application and infrastructure source files have no changes in this task.

The CLI constructs the configured `GeminiInvestigationModel`, the existing `SearchEvidence`, and the existing `InvestigateQuestion` with application settings. An evaluation lexical adapter routes the common search port to `execute_keywords`. Search/model observers pass the original arguments to these ports and return the original results. A deterministic parity test compares wrapped and unwrapped application results, search arguments, actual turns and dispositions, excluding naturally variable elapsed times.

## Dataset and run configuration

The authoritative selection is `.cache/evaluation/next-sample-40.json`: forty IDs, ten each of inference, compositional, comparison and bridge comparison. No resampling is performed. `--question-id` explicitly selects a smoke subset and preserves the complete selection's canonical parent hash in the manifest.

The existing local `data/raw/2wiki/dev.parquet` is verified by the loader: `xanhho/2WikiMultihopQA`, revision `612bc5039a457880d9e7d84c3b0a4cf154b70e4f`, dev split, 12,576 questions, SHA-256 `c0d8b60b9026b728fb07ad74c5252a0f188f6942e8ba5c02df4dfa369502ea8d`. Read-only preflight verified `investigation_stage`, the existing full shared corpus with 125,760 current pinned occurrences, compatible stored profiles and selected gold availability. No download, ingestion, index or database changes occur.

| Setting | Actual configured value |
| --- | --- |
| Generation model | `gemini-3.5-flash-lite` |
| Investigation maximum turns / generation requests | 6 / 6 per question |
| Search calls / embedding actions | 5 / 5 per question |
| Results per search | 5 |
| Maximum accumulated evidence | 15 canonical passages |
| Maximum serialized Investigation context | 24,000 characters |
| Maximum output / provider timeout | 1,024 tokens / 30 seconds per generation call |
| Query embedding | `gemini-embedding-2`, 768 dimensions, normalization none |
| Query text | `task: search result \| query: {exact_query}`, existing `gemini_search_query_v1`, no API task type |
| Stored document format | Existing `gemini_no_title_v1` |
| HNSW | Candidates 400, ef_search 400, iterative scan off |
| Hybrid | Semantic 20, lexical 20, existing RRF k=60, no reranker |
| Measured repetitions / warmups / concurrency | 1 / 0 / 1 by default for answer modes |
| Whole request timeout | 900 seconds for CLI answer runs, including pacing |
| Smoke pacing | Generation 12 RPM and 200,000 reserved tokens/minute; embedding 60 RPC/minute and 24,000 input tokens/minute; initial cooldown 60 seconds |

These are Investigation's existing budgets, not the separate Quick Answer five-passage/12,000-character budgets. Invocation flags can choose the retrieval configuration without changing orchestration. Across configurations, Gemini may choose different queries, counts and stopping decisions; results must not be treated as identical-query retrieval experiments.

## Dynamic embeddings and bounded provider usage

`CachedQueryEmbeddings` remains the offline implementation. The new evaluation read-through adapter validates that the live adapter's full `EmbeddingConfig` equals the cache/persisted profile. The CLI restricts live misses to the existing Gemini model, dimensions, document encoding and normalization; the unchanged adapter supplies the existing query formatting. No model substitution or vector relabeling occurs.

Exact cached queries return validated stored vectors with zero RPCs. Without both `--allow-live-embeddings` and a positive `--max-embedding-requests` of at least two, misses fail explicitly. With authorization, a miss must have capacity for both countTokens and embedContent before it begins. Each actual RPC also consumes the shared cap, including failed calls. The original adapter counts tokens, embeds once, validates the vector, and the read-through adapter writes the successful vector back to the existing profiled cache using atomic replacement. Original compatible vectors and cache metadata are retained. Documents cannot be embedded through this evaluation interface.

Cache hits/misses, exact query, vector fingerprint, duration, counted input tokens when available and actual count/embed deltas are saved per request. The manifest records cache hashes before and after execution. Use one writer for this local cache: run concurrency is one, but atomic replacement is not a cross-process writer lock.

Generation requires `--allow-generation` and a positive shared `--max-generation-requests`. The ordinary Investigation adapter performs one generateContent RPC per turn; SDK retries are disabled. Attempted adapter invocations and actual adapter-reported RPC deltas remain distinct, including local validation failures. A cap applies across all questions, configurations, warmups and repetitions within that invocation. A new CLI invocation resets the cap, so its budget must be separately authorized.

Rolling pacing conservatively includes token-counting calls in embedding RPM. Embedding TPM uses the actual formatted-input count returned by countTokens. Generation uses an ephemeral conservative byte reservation for the actual native history envelopes, instructions/schema and maximum output allowance; this is not a measured token count. No extra generation countTokens requests are issued. Native signatures and hidden reasoning are not exported. Pacing waits are included in end-to-end latency, and provider API durations are separately observed. Usage from the provider is reported separately from reservations. Other clients sharing the project are outside the local limiter's scope. Quota errors are retained without automatic retries.

The user supplied current AI Studio usage/limits and explicitly authorized **smoke only**, confirming Free tier with billing disabled. The reported values were:

| Model | RPM used / limit | TPM used / limit | RPD used / limit | Reported daily remaining |
| --- | ---: | ---: | ---: | ---: |
| Gemini Embedding 2 | 160 / 100 | 15.64K / 30K | 874 / 1,000 | 126 |
| Gemini 3.5 Flash-Lite | 16 / 15 | 43.29K / 250K | 164 / 500 | 336 |

This is user-confirmed project status, not an independently queried billing API. Google's [pricing documentation](https://ai.google.dev/gemini-api/docs/pricing) lists a free tier for both configured models; [rate-limit documentation](https://ai.google.dev/gemini-api/docs/rate-limits) points to active project limits in AI Studio. Because the reported RPM was already exceeded, the smoke waits sixty seconds before measured execution.

## Metrics and trace interpretation

Every answer row retains expected/generated text, status, normalized Exact Match, token F1, citations, structural citation validity, errors, generation calls, usage and model version. `correct_by_normalized_exact_match` is the deterministic text-match result, **not a factual correctness judgment**. The existing original 2Wiki whole-answer scorer is reused without extracting a short answer or stripping citations. A correct explanatory/cited answer can have EM zero and partial F1. Abstention, conflicts, incomplete execution and provider failures remain separate statuses. No LLM judge is added.

Gold matching preserves canonical SHA-256, complete sentence containment and source-occurrence information. Investigation metrics do not truncate accumulated/final-context coverage to twenty passages. The stages are:

1. **First search:** actual passages returned by the first executed search, normally at most five.
2. **All searches:** canonical union of actual returned passages across executed searches; this does not mean all internal hybrid candidates.
3. **Accumulated evidence:** actual returned registry. On an exception, only the last observed model-turn state is available and this limitation is explicit.
4. **Final generation context:** union of references in the exact turn that produced a final decision. No final context is invented for incomplete or provider-failed runs. A final-decision attempt rejected by application citation validation can still have an observed model context; it remains a failed answer.

Each stage records observed/unavailable state, exact matched gold indexes, recall and complete support. `final_generation.complete` states whether all annotated supporting facts were present in that context before the final decision. It does not prove the model used the facts correctly.

`investigation_traces.jsonl` includes each exact model-requested query, retrieval configuration, search duration, actual returned evidence and per-search coverage; admitted exchange references; accumulated state after each search where observable; each actual model-turn transcript; final registry/context; final answer/citations; expected answer; scores; timings and usage. `--local-diagnostics` additionally retains full excerpts locally. Source IDs/hashes/ranges and citation IDs remain distinct. A failed search remains a trace entry. Rejected repeated/limit searches are retained in the ordinary application search records in `answer_results.jsonl`.

Search-tool calls, executed searches, actual database repository calls, retrieved passage count, distinct canonical retrieved count, accumulated count and final-context count are separate. Application `embedding_requests` is a workflow action counter; lexical searches can increment that counter while making **zero embedding RPCs**. Actual provider counts are authoritative for usage. Limit flags distinguish reaching configured thresholds from the separate stop outcome (decision, incomplete or error).

Timing includes total Investigation latency, individual/total search duration, individual/total generation duration, embedding duration, connection/setup/repository/SQL/RRF/admission observations, actual API durations and usage when available. Search includes embeddings and generation includes pacing, so these components must not be added to total Investigation time. Local-to-Cloud-SQL measurements include network overhead; SQL observations are not PostgreSQL server execution time. All distributions retain min/median/mean/p90/p95/max/count. Warmups are excluded from measured quality and latency; their requests still consume caps. Failed or unavailable stages cannot improve all-attempt operational means.

The generated report includes the requested three-configuration comparison: answer EM/F1, complete final context, average search/generation calls, median/p95 latency and failed/incomplete requests. Generic pre-existing Recall@K fields describe returned registry order for Investigation; the explicitly named stage metrics are the appropriate multistep evidence measures.

## Request estimate and full-benchmark blocker

| Scope | Investigations | Maximum generation RPCs | Maximum query misses | Maximum embedding RPCs, including counting |
| --- | ---: | ---: | ---: | ---: |
| One question × three configurations | 3 | 18 | 10 | 20 |
| Forty questions × three configurations | 120 | 720 | 400 | 800 |

The calculation uses min(turn limit, generation limit) generation calls per attempt, at most five searches, two embedding RPCs per semantic/hybrid cache miss and zero lexical embedding calls. It assumes no dynamic-query reuse and no retries. The forty original vectors do not guarantee a cache hit for model-generated first or follow-up searches. Actual use may be much lower, but the full maximum exceeds both reported daily allowances. A low run-wide cap would stop calls but would also leave an incomplete, budget-confounded comparison, so no full live benchmark is claimed.

The approved smoke caps of 18 generation and 20 embedding RPCs fit the reported 336/126 remaining daily requests. The conservative remainder would be 318/106 if both caps were fully consumed and no other project usage occurred; that is arithmetic, not a refreshed quota reading. No additional paid allowance or billing activation is authorized.

## Authorized live smoke results

Execution run `20261106T182339-016cbf26` completed its three recorded attempts: one inference question, `5d6a128c0bb011ebab90acde48001122`, **Who is William Jolliffe, 4Th Baron Hylton's paternal grandfather?** The expected answer is **Hedworth Jolliffe, 2nd Baron Hylton**.

| Metric | Semantic Investigation | BM25 Investigation | Hybrid Investigation |
| --- | ---: | ---: | ---: |
| Independent questions / measured attempts | 1 / 1 | 1 / 1 | 1 / 1 |
| Answer status | Failed: generation provider | Failed: generation provider | Answered |
| Answer Exact Match, all attempts | 0 | 0 | 0 |
| Answer token F1, all attempts | 0 | 0 | 0.212766 |
| Complete final context, operational mean | 0, unobserved | 0, unobserved | 1, observed |
| Final contexts actually observed | 0 | 0 | 1 |
| Search tool calls | 0 | 2 | 5 |
| Actual generation RPCs | 1 | 3 | 6 |
| Actual embedding RPCs including counting | 0 | 0 | 10 |
| First-search annotated recall | Unavailable | 1.0 | 1.0 |
| Accumulated annotated recall | 0, observed empty turn | 1.0, latest observed turn | 1.0, returned registry |
| Distinct retrieved / accumulated passages | 0 / 0 | 5 / 5 | 5 / 5 |
| Total retrieved passage appearances | 0 | 10 | 25 |
| Final-decision context passages / characters | Unavailable | Unavailable | 5 / 23,798 |
| Total latency, median and p95 (ms) | 4,249.06 | 12,469.10 | 42,472.57 |
| Failed / incomplete attempts | 1 / 0 | 1 / 0 | 0 / 0 |

With one observation per configuration, median and p95 equal the single latency and have no distributional significance. Provider failures explain the short failed runs; they do not demonstrate faster retrieval. The manifest's `complete` status means all planned attempts and artifacts were recorded, not that all Investigations answered successfully.

Semantic failed on its first generation RPC and never searched. BM25 generated two searches and accumulated both annotated facts before a generation provider failure. Hybrid completed five model-directed searches followed by a final decision, using six generation RPCs. It reached the existing search/embedding-action and generation thresholds and observed a context-limit disposition; the final turn had `final_only=true`. Its repeated transcript grew through 113, 5,698, 11,287, 16,906, 22,487 and 23,798 serialized characters while the canonical registry remained five passages. This demonstrates why registry size, repeated transcript size, retrieved appearances and final-context evidence need separate measurements.

Hybrid returned **William George Hylton Jolliffe, 1st Baron Hylton**, citing E1/E2/E3. The expected answer names the **2nd Baron**. This disagreement is substantive, rather than just an explanatory-answer formatting difference. Both annotated facts were already present after the first hybrid search and remained in the final context; later queries recovered no additional distinct canonical passages or annotated facts in this smoke. The result shows that complete supporting-fact coverage does not guarantee correct relationship reasoning. It is not an architectural recommendation or an estimate of forty-question answer accuracy.

The exact BM25 queries were:

1. `William Jolliffe 4th Baron Hylton paternal grandfather`
2. `Hedworth Jolliffe 2nd Baron Hylton parents father`

The exact hybrid queries were:

1. `William Jolliffe 4th Baron Hylton paternal grandfather`
2. `"Hedworth Jolliffe, 2nd Baron Hylton" parents OR father`
3. `"Hedworth Jolliffe" son of "William George Hylton Jolliffe" OR "1st Baron Hylton"`
4. `"Hedworth Hylton Jolliffe" "1st Baron Hylton"`
5. `"Hedworth Jolliffe" 2nd Baron Hylton father`

All five hybrid queries were exact cache misses; the original question's cached vector was not relabeled for them. They used five countTokens and five embedContent requests. The complete smoke therefore used **10 generation RPCs + 10 embedding RPCs**, with zero retries, zero Voyage calls and zero document embeddings. The cache grew **46 → 51**; all original forty questions and six earlier regression queries remain available, and the six stored vectors match their source caches in the existing float32 representation. The input cache hash was `d02a3bdd363b2f55fadc3ff023c987292b16a0f03ae325f0bc37e6011e5436f5`; the output hash is `071ee95fe27d2a4d585ec10f184593824f9fc77d30f90c379d240ab6b5abf8b1`. Smoke persistence reserialized existing vectors in that storage representation; the final writer additionally preserves existing raw numeric encoding on later writes. No original query was re-embedded.

Known reported generation usage totaled **32,809 prompt tokens and 368 candidate tokens**, with zero reported thinking tokens on successful calls. The two failed generation calls had unavailable usage, so these are partial known totals, not asserted complete usage. Embedding countTokens reported 127 formatted input tokens across the five new queries. The conservative post-smoke daily arithmetic is 326 generation and 116 embedding requests remaining if no other usage occurred; it is not a refreshed quota reading. The full 720/800 worst-case requirement still exceeds that allowance.

The original failure rows retained `GenerationProviderError` and the actual RPC durations/counts, but not their HTTP status. No transient or quota cause is inferred. After the smoke, the runner was tightened to retain the application's existing allowlisted provider diagnostics for future failures; raw SDK messages/bodies remain excluded. Reporting was also clarified to prioritize Investigation metrics and remove irrelevant Quick Answer tables from Investigation-only runs. These are offline/reporting changes, and **no live attempt was repeated**. Original execution hashes and `report.md` are preserved; `investigation-comparison.md` is derived from the saved measurements, and `audit.json` records the postprocessing source hashes and the three evaluation files changed afterward.

The offline audit independently recomputed observed sentence coverage, checked question/parent-selection identity, per-call/manifest request accounting, caps, context bounds and cache membership/source-vector compatibility. Production retrieval/orchestration/model source hashes match the execution. Deterministic tests separately verify successful discovery of additional evidence, authorized cache reuse/misses, abstentions, limits and failures; the one-question live smoke does not establish that every configuration can complete a multistep answer under provider availability conditions.

## Commands

PowerShell from `C:\Users\merli\upgaming_task\ai-investigation-engine`. Always choose a fresh output directory. Explicit API flags authorize live usage when `--plan-only` is absent; review current project quotas and billing status first.

Provider-free full-corpus preflight and worst-case request plan (already executed; use a new output path to reproduce):

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py run --selection .cache/evaluation/next-sample-40.json --cache .cache/evaluation/query-embeddings.json --expected-database investigation_stage --modes investigation_semantic investigation_lexical investigation_hybrid --allow-generation --max-generation-requests 720 --allow-live-embeddings --max-embedding-requests 800 --plan-only --output reports/evaluation/structured/NEW-investigation-plan
```

The authorized smoke command (one original question, three configurations; do not rerun without another budget authorization):

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py run --selection .cache/evaluation/next-sample-40.json --question-id 5d6a128c0bb011ebab90acde48001122 --cache .cache/evaluation/query-embeddings.json --expected-database investigation_stage --modes investigation_semantic investigation_lexical investigation_hybrid --allow-generation --max-generation-requests 18 --allow-live-embeddings --max-embedding-requests 20 --generation-rpm 12 --generation-tpm 200000 --embedding-rpm 60 --embedding-tpm 24000 --cooldown-seconds 60 --repetitions 1 --warmups 0 --concurrency 1 --timeout 900 --local-diagnostics --output reports/evaluation/structured/2026-11-06-investigation-smoke
```

Full benchmark command, **ready but unexecuted and not authorized under the current smoke-only approval**:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py run --selection .cache/evaluation/next-sample-40.json --cache .cache/evaluation/query-embeddings.json --expected-database investigation_stage --modes investigation_semantic investigation_lexical investigation_hybrid --allow-generation --max-generation-requests 720 --allow-live-embeddings --max-embedding-requests 800 --repetitions 1 --warmups 0 --concurrency 1 --cooldown-seconds 60 --timeout 900 --local-diagnostics --output reports/evaluation/structured/NEW-investigation-40
```

To run only one configuration, use `--modes investigation --answer-retrieval-mode semantic` (or `lexical`/`hybrid`) with a separately approved smaller cap. Cached-only embeddings remain the default when the live embedding flags are omitted. Retrieval-only and Quick Answer commands in the [existing guide](structured-evaluation.md) are preserved.

Saved comparisons require no database or provider access:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py compare reports/evaluation/structured/2026-11-06-investigation-smoke reports/evaluation/structured/2026-11-06-investigation-smoke --left-mode investigation_semantic --right-mode investigation_hybrid --output reports/evaluation/structured/2026-11-06-investigation-smoke/semantic-vs-hybrid.json
```

## Deterministic validation

The final unit/application suite passed **383 tests**, including dynamic cache misses and hits, exact Gemini query formatting, profile mismatch/authorization guards, preservation of existing raw cache encoding, countTokens-inclusive caps, allowlisted provider failures without retries, rolling pacing, dynamic follow-ups through the real use case, canonical deduplication, multiple-search accumulation, context/evidence exclusions, answers/abstentions, incomplete/search/generation/turn limits, partial error traces, deterministic scoring, aggregation, comparisons and parity. One existing Google SDK deprecation warning remains.

Three PostgreSQL integration tests passed in the final run (9.71 seconds): full-corpus preflight/SQL observations in an isolated fixture, existing Quick Answer serving admission, and actual Investigation with a second query derived from first-search evidence and a correct cited final answer. The integration case uses a one-result search budget to make discovery of its second source observable; it does not modify the application's default search budget. Docker was initially stopped; it was started in the background before rerunning the tests. No staging database writes or external AI calls occurred in tests.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/evaluation tests/unit/application -q --junitxml=.cache/investigation-evaluation-unit.xml
.\.venv\Scripts\python.exe -m pytest tests/integration/infrastructure/persistence/test_bm25_retrieval.py -k 'structured_evaluation or actual_serving_preparation or structured_investigation' -q --junitxml=.cache/investigation-evaluation-integration.xml
.\.venv\Scripts\ruff.exe check src/investigation_engine/evaluation tests/unit/evaluation/test_investigation_evaluation.py tests/integration/infrastructure/persistence/test_bm25_retrieval.py
.\.venv\Scripts\ruff.exe format --check src/investigation_engine/evaluation tests/unit/evaluation/test_investigation_evaluation.py tests/integration/infrastructure/persistence/test_bm25_retrieval.py
```

## Reproducibility and limits

Artifacts below are local paths relative to the exact repository root `C:\Users\merli\upgaming_task\ai-investigation-engine`:

| Artifact | Path below repository root |
| --- | --- |
| This maintained report and commands | `docs/validation/structured-investigation-evaluation-2026-11-06.md` |
| Provider-free original full request plan | `reports/evaluation/structured/2026-11-06-investigation-plan/plan.json` |
| Final-code readiness/preflight plan | `reports/evaluation/structured/2026-11-06-investigation-ready-plan/plan.json` |
| Clear saved-result comparison | `reports/evaluation/structured/2026-11-06-investigation-smoke/investigation-comparison.md` |
| Original run manifest / report | `reports/evaluation/structured/2026-11-06-investigation-smoke/manifest.json`, `report.md` |
| IDs / expected answers and sentence annotations | `reports/evaluation/structured/2026-11-06-investigation-smoke/selection.json`, `questions.jsonl` |
| Actual answers, citations, matches and errors | `reports/evaluation/structured/2026-11-06-investigation-smoke/answer_results.jsonl` |
| Per-question dynamic search/model/evidence trace | `reports/evaluation/structured/2026-11-06-investigation-smoke/investigation_traces.jsonl` |
| Aggregate distributions and coverage | `reports/evaluation/structured/2026-11-06-investigation-smoke/summary.json` |
| Independent offline audit | `reports/evaluation/structured/2026-11-06-investigation-smoke/audit.json` |
| Saved paired comparisons | `reports/evaluation/structured/2026-11-06-investigation-smoke/semantic-vs-hybrid.json`, `lexical-vs-hybrid.json` (each with Markdown) |
| Full local excerpts / console | `reports/evaluation/structured/2026-11-06-investigation-smoke/grounding_review.jsonl`, `.cache/investigation-evaluation-smoke.log` |
| Query cache | `.cache/evaluation/query-embeddings.json` |
| Test receipts | `.cache/investigation-evaluation-unit.xml`, `.cache/investigation-evaluation-integration.xml` |

Outputs retain selected IDs/references, full configuration, dataset/corpus/profile identity, git commit and dirty state, source hashes, cache coverage/hashes, global request caps and actual request counts, raw answer rows, Investigation traces, aggregate JSON and Markdown. Provider-free plans are explicitly marked `planned_no_provider_calls`; they are not completed benchmarks. Detailed artifacts are ignored local output, not committed test fixtures. No API key or hidden reasoning is exported.

Gemini nondeterminism can produce different search paths across configurations and later runs. A one-question smoke verifies wiring and accounting, not general multistep reasoning quality. Strict annotated-fact matching is incomplete as a semantic relevance measure; EM/F1 is a text metric, not an answer judge. Citation validation is structural. Failed attempts retain only states actually observed at the ports; unobserved intermediate registry state is not reconstructed by a simplified algorithm. Pacing makes local live end-to-end latency different from unrestricted production latency. The database is not frozen across requests, and no provider-wide allowance or billing API is queried automatically. No deployment, commit or push occurs.
