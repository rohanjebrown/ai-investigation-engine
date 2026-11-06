# Forty-question retrieval and serving-evidence benchmark — 6 November 2026

**Semantic HNSW supplied the strongest evidence under the actual five-position serving constraints in this sample:** 74.375% average admitted supporting-fact recall and complete support on 19/40 questions, compared with hybrid's 70% and 16/40, and BM25's 59.375% and 14/40. BM25 had the lowest median latency. Hybrid's additional latency did not improve aggregate coverage over semantic here. These are descriptive measurements on forty selected questions, not a statistically established architecture choice.

This extends the existing structured evaluator with observations at the actual Quick Answer evidence-preparation boundary. Candidate-ranking quality and evidence admitted to the generation request are measured separately. No answers were generated; admitted evidence means the payload that would be supplied to Gemini.

## Complete run and retrieval comparison

Run `20261106T171037-fb13fba8` completed with **480/480 successful observations**: 120 warmups and 360 measured attempts. Each mode has forty independent questions and 120 measured executions. All actual serving-preparation probes also succeeded. The independent audit recomputed sentence coverage and latency aggregates from saved rows, checked every source hash, selection/cache identity and admission budget, and found no ranking or admitted-evidence variation across measured repetitions. Standalone channel identities matched the corresponding hybrid channels for all forty questions.

Quality percentages below are macro averages over question-level supporting-fact recall, not percentages of all facts pooled together. Equal repetitions leave these quality means unchanged.

| Metric | Semantic | BM25 | Hybrid |
| --- | ---: | ---: | ---: |
| Candidate Recall@5 | 74.375% | 59.375% | 70.000% |
| Candidate Recall@20 | 83.125% | 71.875% | 81.875% |
| Complete support@5 | 47.5% (19/40) | 35.0% (14/40) | 40.0% (16/40) |
| Complete support@20 | 62.5% (25/40) | 42.5% (17/40) | 60.0% (24/40) |
| MRR@20 | 0.958 | 0.816 | 0.894 |
| Actual admitted recall | 74.375% | 59.375% | 70.000% |
| Actual admitted complete support | 47.5% (19/40) | 35.0% (14/40) | 40.0% (16/40) |
| Average admitted passages | 5.00 | 5.00 | 5.00 |
| Average full context characters | 3069.100 | 2691.725 | 2737.375 |
| Maximum full context characters | 6181 | 5839 | 5602 |
| Mean gold facts lost between ranked twenty and admitted context | 0.20 | 0.35 | 0.25 |

Within the hybrid observation, semantic-channel Recall@5/@20 was 74.375%/83.125% and lexical-channel recall was 59.375%/71.875%; fused recall was 70%/81.875%. Thus fusion reduced average top-five recall by 4.375 percentage points and top-twenty recall by 1.25 points relative to its semantic channel. There were zero lexical-only annotated facts in the combined twenty-per-channel pool. That finding is specific to this sample and the strict canonical sentence definition; it does not establish that BM25 never adds useful evidence.

All modes admitted five passages on every attempt. There were **zero context-budget exclusions**, zero exact canonical-range duplicates in ranked output and zero canonical duplicate rejections during serving preparation. The maximum serialized context was comfortably below 12,000 characters, so the measured coverage reduction concerns ranking and the five-position cutoff, not context truncation. The actual serving retrieval already returned five positions; selector-local count-limit rejections were zero. The independently observed ranked tail accounts for the upstream count-limit loss where prefix identity matches.

Two lexical questions had different serving/ranked-prefix identities (six repeated observations): `8bc6a73a0bb011ebab90acde48001122` and `3835d02c0bde11eba7f7acde48001122`. The benchmark does not assign unobserved tail exclusions for those attempts. Their coverage did not change, so aggregate admitted recall equals candidate Recall@5 despite those identity differences. No causal explanation for the differences is inferred.

## Latency comparison

Milliseconds; each row contains 120 measured observations, excluding warmups. Candidate retrieval and serving preparation are independent phases.

| Phase / mode | Minimum | Median | Mean | p90 | p95 | Maximum | n |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Candidate twenty / semantic | 372.24 | 523.16 | 541.40 | 632.07 | 678.37 | 1377.82 | 120 |
| Candidate twenty / BM25 | 339.37 | 473.25 | 507.06 | 585.61 | 667.67 | 1972.27 | 120 |
| Candidate twenty / hybrid | 516.06 | 712.24 | 767.20 | 890.14 | 957.51 | 2615.52 | 120 |
| Actual serving preparation / semantic | 354.52 | 481.89 | 488.15 | 572.93 | 612.53 | 786.91 | 120 |
| Actual serving preparation / BM25 | 314.97 | 439.35 | 486.21 | 558.36 | 719.52 | 1743.49 | 120 |
| Actual serving preparation / hybrid | 493.61 | 688.39 | 750.81 | 837.88 | 915.36 | 3285.94 | 120 |

BM25's median candidate retrieval was 49.91 ms below semantic. Hybrid's candidate median was 189.08 ms (36.1%) above semantic and 238.99 ms (50.5%) above BM25. Its serving-preparation median was 206.50 ms (42.9%) above semantic and 249.04 ms (56.7%) above BM25. These are differences between distribution medians, not medians of paired differences. Hybrid improved admitted recall over BM25 by 10.625 percentage points, but fell 4.375 points below semantic. This run therefore shows a coverage benefit for hybrid over lexical alone, with additional latency; it shows no aggregate coverage benefit over semantic alone. Network variability and outliers limit latency generalization.

## Question-type results

Each type has **ten independent questions per mode**, with thirty measured executions. Cells show candidate Recall@20 / admitted recall / admitted complete-support rate.

| Type | Semantic | BM25 | Hybrid |
| --- | ---: | ---: | ---: |
| Inference | 90% / 70% / 40% | 65% / 50% / 20% | 90% / 55% / 20% |
| Compositional | 85% / 75% / 50% | 70% / 60% / 30% | 80% / 70% / 40% |
| Comparison | 100% / 100% / 100% | 100% / 95% / 90% | 100% / 100% / 100% |
| Bridge comparison | 57.5% / 52.5% / 0% | 52.5% / 32.5% / 0% | 57.5% / 55% / 0% |

Semantic led admitted coverage for inference and compositional questions. Semantic and hybrid tied on comparison. Hybrid slightly led bridge-comparison recall, but **none of the ten bridge-comparison questions had complete annotated support even at twenty candidates**, for any mode. The all-facts criterion exposes a gap that a high partial recall would conceal. The selected sample is type-balanced rather than representative of natural type prevalence.

## Concrete recovered and lost evidence

Semantic had higher admitted recall than hybrid on four questions; hybrid was higher on one; the other thirty-five tied. These examples use the first successful measured observation; evidence identities were stable across repetitions.

| Question ID and question | Observed supporting-fact movement | Consequence |
| --- | --- | --- |
| `32efd6180bb011ebab90acde48001122` — Who is the uncle of John Of Brienne? | John of Brienne sentence 1 was semantic rank 1, absent from lexical twenty, fused rank 18. | Admitted recall semantic 0.5, hybrid 0.0. The other required fact was also below five. |
| `5aa6b82e0bde11eba7f7acde48001122` — Where did Shahrzad Mojab's husband graduate from? | Amir Hassanpour sentence 2 moved from semantic rank 3 to fused rank 6. | Semantic admitted both facts; hybrid admitted one. |
| `a4f3d22e0baf11ebab90acde48001122` — Who is the paternal grandmother of Eric I Of Denmark? | Sweyn II of Denmark sentence 1 moved from semantic rank 4 to fused rank 8. | Admitted recall 1.0 versus 0.5. |
| `00fc38720bb011ebab90acde48001122` — Who is Maquiztzin's sibling-in-law? | Tlacaelel sentence 1 moved from semantic rank 4 to fused rank 7. | Admitted recall 1.0 versus 0.5. |
| `9eb76ea408ba11ebbd88ac1f6bf848b6` — Which film has the director who died earlier, Deuces Wild or Cavalcade Of The West? | A shared fact at semantic rank 7 and lexical rank 8 became fused rank 3. | Hybrid admitted 3/4 facts versus semantic 2/4. This is useful lexical ranking reinforcement, not a fact absent from semantic's twenty. |
| `39befe640bde11eba7f7acde48001122` — Where was the place of death of Constance Of York, Countess Of Gloucester's father? | Edmund of Langley sentence 1 was semantic rank 19 and absent from lexical twenty; fusion excluded it from the final twenty. | Semantic Recall@20 1.0, hybrid 0.5. |

For `1543dc18088111ebbd6dac1f6bf848b6` (Miss Sloane versus Time Changer directors), John Madden sentence 0 and Rich Christiano sentence 0 were present and eligible in the corpus but absent from both twenty-result channels. These are retrieval misses, not missing corpus records. Preflight found all 100 unique selected supporting-fact annotations eligible. On one observation per question, semantic admitted 70/100 annotated facts, BM25 54/100 and hybrid 67/100; these pooled counts differ from the macro recall percentages above. Relative to each ranked twenty, serving lost 8, 14 and 10 facts respectively. Fusion separately excluded one semantic-channel fact from twenty. No fact is attributed to a context-budget loss.

Highly similar passages sometimes occupied serving positions despite zero exact canonical-range duplicates. For the Miss Sloane question, semantic ranks 4 and 5 were IDs `01a1857a-58e5-759b-a742-9700f7ddd52c:0:182` and `01a1856d-6af8-7168-9140-82b3a6e3595e:0:182`, with word-token 5-shingle Jaccard 1.0. Both were admitted. For `726edd860bdb11eba7f7acde48001122`, lexical ranks 4 and 5 had similarity 0.993789, with IDs `01a184c2-4966-7730-bc42-a2991ce14f3b:0:1996` and `01a1858e-19c6-73c5-bf5c-2297e58b9260:0:2013`; both were admitted. The diagnostic uses lowercase Unicode word tokens, shingle sets, threshold 0.85 and distinct canonical ranges. This is a reproducible heuristic example, not a reliable semantic duplicate rate or proof that replacing a passage would recover a particular fact.

Detailed per-question matches, full channel/fused ranks, similarity pairs and exclusion states are saved in `retrieval_results.jsonl` and `analysis.json`. The generated report's parenthetical `comparison` label on cross-mode finding bullets denotes a cross-mode comparison, not the dataset question type; actual types are in `analysis.json` and the table above.

## Implementation and behavior

| File | Change and purpose |
| --- | --- |
| `application/use_cases/answer_question.py` | Extracted existing preparation into `AnswerQuestion.prepare_evidence`, reused by `execute`; added optional selector decision observations. Retrieval/decomposition, canonical deduplication, admission order, serialized-context budget and generation payload stay the same. |
| `evaluation/serving.py` | Adapts lexical retrieval to that preparation port; records actual admitted IDs, serialized context size, coverage, exclusions, hybrid-channel/fused ranks and an explicitly approximate passage-similarity diagnostic. |
| `evaluation/runner.py` | Runs a separately timed real serving-preparation probe after candidate retrieval; persists channel/admission/pipeline metrics and preserves errors and warmups. |
| `evaluation/cli.py` | Adds serving budgets and an optional embedding-preparation receipt; creates generation-free preparers for the three retrieval modes and saves the requested selection before execution. |
| `evaluation/reporting.py` | Adds serving/type/stage comparisons and per-question analysis; writes JSON atomically and marks a run complete only after final reports are saved. |
| `tests/unit/evaluation/test_serving_evaluation.py` | Focused deterministic coverage for admission, loss attribution, duplicates, failures, aggregation, comparisons, behavior parity and interrupted report writes. |
| `tests/integration/infrastructure/persistence/test_bm25_retrieval.py` | Exercises actual repository retrieval and Quick Answer preparation without generation, alongside existing preflight/SQL observations. |

Paths in this table are under `src/investigation_engine/` unless they start with `tests/`. The existing CLI, dataset loader, canonical gold mapper, query cache, retrieval services, timers and comparison utilities are reused. The earlier structured-evaluation implementation remains in the same uncommitted workspace; this report describes its serving-evaluation extension.

For each question/mode/repetition, the evaluator first retrieves up to twenty ranked candidates. It then independently runs the real Quick Answer preparation using five evidence positions and a 12,000-character serialized context budget. This second observation repeats serving retrieval; it is not a simulation that simply truncates the twenty-result observation. The existing selector considers the first five positions before checking context size and does not refill a context-rejected position. Budget size includes the full serialized request, including metadata and question text.

The twenty-result ranking and independent serving search can have different identities or order. Rows expose whether serving retrieval matches the ranked prefix. A fact found in the twenty-result observation but missing from admitted context is reported as lost; an upstream count-limit cause is only assigned where the observed prefix justifies it. Otherwise the difference remains explicit. Context rejection and count decisions inside the selector are directly observed. Lexical-only serving is an evaluation counterfactual through the same admission code.

No BM25 SQL, HNSW algorithm, RRF scoring, ingestion, schema, prompts, Investigation orchestration, document embeddings or reranking behavior changed. Byte-for-byte checks against saved pre-task sources cover the repository, RRF, search service, embedding adapter, both generation adapters and Investigation orchestration. An offline before/after comparison verified 100 preparation/execution cases, including identical search calls, outputs/errors and generation request payloads.

## Dataset, corpus and protocol

| Setting | Value |
| --- | --- |
| Dataset | `xanhho/2WikiMultihopQA`, `dev`, 12,576 questions |
| Revision | `612bc5039a457880d9e7d84c3b0a4cf154b70e4f` |
| Local input | `data/raw/2wiki/dev.parquet` |
| Input SHA-256 | `c0d8b60b9026b728fb07ad74c5252a0f188f6942e8ba5c02df4dfa369502ea8d` |
| Database/scope | Existing Cloud SQL `investigation_stage`, shared current indexed corpus, 125,760 occurrences |
| Selection | Exact IDs from `.cache/evaluation/next-sample-40.json`; ten of each of four types; no resampling |
| Selection canonical SHA-256 | `27f2e141dffeec5881e279f40e86fa263583791767316cf933535bbe4e691518` |
| Modes | Semantic HNSW, indexed BM25 with existing exact completion, hybrid application RRF |
| HNSW | Candidate limit 400, `ef_search=400`, iterative scan off |
| Hybrid | Semantic 20, lexical 20, final 20, RRF k=60, reranker none |
| Admission | Maximum 5 evidence positions; full serialized context maximum 12,000 characters |
| Sampling protocol | One warmup and three measured repetitions for every question/mode; concurrency one |
| Independent quality sample | 40 questions; ten per type. Repetitions are repeated observations, not additional independent questions. |
| Embedding | Gemini `gemini-embedding-2`, 768 dimensions; existing persisted compatible profiles |
| Query formatting | `gemini_search_query_v1`: `task: search result \| query: {exact_question}`; no API task type; normalization none |
| Document format | Existing `gemini_no_title_v1`; no document generation or indexing |

Gold matching requires the same canonical content SHA-256 and complete containment of the annotated sentence interval. Source-occurrence information and secondary strict-occurrence metrics remain recorded. Missing or unresolvable facts stay in the denominator. This measures annotated supporting-fact coverage, not answer correctness or semantic equivalence.

## Query embedding preparation and cost boundary

The original cache held six compatible vectors, none for these forty exact questions. Explicit cache/trace inspection found no reusable vectors for the selected forty. Preparation added only the forty missing queries; afterward the cache held 46 entries and all six originals were unchanged. Cache SHA-256 is `d02a3bdd363b2f55fadc3ff023c987292b16a0f03ae325f0bc37e6011e5436f5`.

The user supplied the configured API-key project's AI Studio limits (RPM 160/100, TPM 15.64K/30K, RPD 874/1,000) and explicitly confirmed **Free tier; billing disabled**. This is user-confirmed project status, not an independently queried billing API. Google's [pricing page](https://ai.google.dev/gemini-api/docs/pricing) documented the free-tier embedding offering; [rate-limit documentation](https://ai.google.dev/gemini-api/docs/rate-limits) identifies AI Studio as the place to inspect active project limits.

The helper reserved an explicit maximum of 80 provider RPCs, including token counting, within the reported 126 remaining daily requests. It waited 60 seconds initially because RPM was already exceeded, spaced RPCs by at least three seconds, disabled automatic retries and persisted successful vectors. Actual preparation used **40 `count_tokens` + 40 `embed_content` requests**, with forty successes and no errors. The conservative daily arithmetic leaves 46 requests if no other usage occurred; this is not a subsequent quota reading. There were **zero generation, Voyage or document-embedding calls**. The benchmark and its storage retry used the completed cache and made zero provider requests.

Preparation receipts, call ledger and console output remain local in `.cache/evaluation-40/`. The benchmark manifest includes the receipt hash and distinguishes preparation requests from benchmark requests. Do not rerun the preparation helper: its work is complete and the vectors are cached.

## Timing interpretation

Candidate latency measures the independent twenty-result retrieval. Serving-preparation latency measures the real five-position retrieval plus decomposition, balancing and admission. They are separate observations and must not be added as one production latency. Their component timers can overlap; summing connection/setup/repository/SQL timers also produces an invalid total.

The local script connected to Cloud SQL, so durations include network and client/driver overhead. SQL observations are application-observed driver timings, not PostgreSQL server execution time. No EXPLAIN was issued; `postgres_explain_execution_ms` remains unavailable. Cached query-vector lookup is included where the application performs it; embedding API latency and the prior preparation requests are outside benchmark timers. Warmups are excluded from every measured distribution. Percentiles use the evaluator's nearest-rank convention.

## Validation and storage recovery

The relevant unit/application suite passed **381 tests**. Four integration tests passed using disposable Docker PostgreSQL: shared-corpus read-only preflight and SQL observations, actual serving preparation without generation, paired answer-quality use-case behavior with fake AI, and the existing fixed forty-question retrieval suite. Integration runtime was 171.4 seconds. The offline before/after behavior check passed 100 cases with zero provider requests. Final Ruff checks passed and all 32 checked Python files passed the formatter check; `git diff --check` passed. One existing Google SDK deprecation warning remains. The expensive integration suite preceded the final metadata-only atomic-write/finalization fixes; the 381-test unit/application run included those fixes and their regressions.

The first fully cached attempt stopped during warmups when C: ran out of storage. Its partial directory `reports/evaluation/structured/2026-11-06-40-complete/` is preserved and excluded from final statistics. The earlier BM25-only fallback `2026-11-06-40-serving/` is also preserved as historical output, with semantic/hybrid explicitly unavailable. After the user freed storage, the complete protocol was restarted in a fresh `2026-11-06-40-final/` directory. Two regression tests verify that a failed atomic replacement preserves the old manifest and that failed report finalization cannot claim a complete run.

## Reproduction and artifacts

From `C:\Users\merli\upgaming_task\ai-investigation-engine`, use a fresh output directory and the existing compatible cache:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py run --selection .cache/evaluation/next-sample-40.json --cache .cache/evaluation/query-embeddings.json --embedding-audit .cache/evaluation-40/embedding-audit-ready.json --expected-database investigation_stage --modes semantic lexical hybrid --semantic-backend hnsw --hnsw-candidates 400 --hnsw-ef-search 400 --semantic-candidates 20 --lexical-candidates 20 --final-limit 20 --rrf-k 60 --serving-max-evidence 5 --serving-max-context-chars 12000 --warmups 1 --repetitions 3 --concurrency 1 --output reports/evaluation/structured/NEW-40-question-run
```

The local detailed artifacts are ignored by Git. The manifest records dataset/profile/corpus identity, git commit and dirty state, code hashes, selection, cache hash/coverage, quotas/preparation receipt, request counts and timing environment. Another checkout needs the same private local cache and database access. No commit, push or deployment was performed.

All paths below are relative to the exact repository root `C:\Users\merli\upgaming_task\ai-investigation-engine`:

| Artifact | Exact local path below repository root |
| --- | --- |
| Detailed maintained report | `docs/validation/40-question-serving-evaluation-2026-11-06.md` |
| Generated measured report | `reports/evaluation/structured/2026-11-06-40-final/report.md` |
| Manifest and source fingerprints | `reports/evaluation/structured/2026-11-06-40-final/manifest.json` |
| Selection and references | `reports/evaluation/structured/2026-11-06-40-final/selection.json`, `questions.jsonl` |
| All per-question results, matches and timing observations | `reports/evaluation/structured/2026-11-06-40-final/retrieval_results.jsonl` |
| Aggregate quality, type and full latency distributions | `reports/evaluation/structured/2026-11-06-40-final/summary.json` |
| Full per-question findings | `reports/evaluation/structured/2026-11-06-40-final/analysis.json` |
| Independent saved-result audit | `reports/evaluation/structured/2026-11-06-40-final/audit.json` |
| Saved paired comparisons (JSON and Markdown) | `reports/evaluation/structured/2026-11-06-40-final/semantic-vs-hybrid.json`, `semantic-vs-hybrid.md`, `lexical-vs-hybrid.json`, `lexical-vs-hybrid.md` |
| Provider preparation receipt | `.cache/evaluation-40/embedding-audit-ready.json` |
| Cached vectors | `.cache/evaluation/query-embeddings.json` |
| Benchmark console | `.cache/evaluation-40/final-console.log` |
| Unit/integration results and behavior parity | `.cache/evaluation-40/unit.xml`, `integration.xml`, `admission-parity.json` |

The `answer_results.jsonl` file is empty as expected for retrieval-only execution. All detailed diagnostic artifacts stay local; no credential or passage-text dump is included in this maintained report.

## Remaining limits

Forty type-balanced questions and ten per type support descriptive comparisons only. Strict sentence containment does not credit semantically equivalent unannotated passages or establish factual answer quality. No Gemini answer/citation evaluation, multi-step Investigation evaluation or Voyage evaluation occurred. HNSW is approximate; stable repetitions here do not prove deterministic results in general. Preflight attests the existing corpus and selected gold availability at its snapshot; the database was not frozen across requests. Warm caches, serial execution, independent paired probes and local-to-Cloud-SQL network conditions constrain performance interpretation. Similarity is an approximate diagnostic and no exact near-duplicate rate is claimed. PostgreSQL server-only execution time is unmeasured. These results complete this benchmark without selecting or modifying a retrieval architecture.

## Full component timing distributions

All values are milliseconds. Component boundaries overlap; do not sum them. Each component distribution uses the per-request recorded total for that component. A count of zero means no observation, not a measured zero. Lexical setup is recorded as zero because the existing lexical path has no separate timed setup block; this does not imply no transaction overhead. The candidate phase has no admission step.

### Candidate phase

| Mode / timer | Minimum | Median | Mean | p90 | p95 | Maximum | n |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| semantic / query_embedding_ms | 0.003 | 0.004 | 0.005 | 0.006 | 0.008 | 0.037 | 120 |
| semantic / connection_acquisition_ms | 0.156 | 0.331 | 0.594 | 0.573 | 2.097 | 6.229 | 120 |
| semantic / database_setup_ms | 123.524 | 215.204 | 231.347 | 285.511 | 317.622 | 902.770 | 120 |
| semantic / sql_observed_ms | 142.934 | 209.932 | 213.464 | 260.250 | 272.575 | 358.486 | 120 |
| semantic / repository_ms | 371.765 | 522.323 | 539.590 | 631.179 | 677.571 | 1376.896 | 120 |
| semantic / rrf_ms | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | 0 |
| semantic / evidence_selection_ms | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | 0 |
| semantic / total_retrieval_ms | 372.130 | 523.024 | 541.154 | 631.899 | 678.204 | 1377.626 | 120 |
| lexical / query_embedding_ms | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | 0 |
| lexical / connection_acquisition_ms | 0.156 | 0.306 | 0.408 | 0.635 | 1.142 | 1.816 | 120 |
| lexical / database_setup_ms | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 120 |
| lexical / sql_observed_ms | 235.489 | 344.568 | 362.940 | 434.902 | 478.641 | 1217.444 | 120 |
| lexical / repository_ms | 339.253 | 473.038 | 506.800 | 585.414 | 667.449 | 1972.033 | 120 |
| lexical / rrf_ms | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | 0 |
| lexical / evidence_selection_ms | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | 0 |
| lexical / total_retrieval_ms | 339.268 | 473.061 | 506.826 | 585.436 | 667.474 | 1972.059 | 120 |
| hybrid / query_embedding_ms | 0.002 | 0.004 | 0.004 | 0.006 | 0.007 | 0.011 | 120 |
| hybrid / connection_acquisition_ms | 0.157 | 0.313 | 0.545 | 0.686 | 1.611 | 7.445 | 120 |
| hybrid / database_setup_ms | 119.227 | 219.787 | 229.710 | 296.528 | 330.236 | 777.976 | 120 |
| hybrid / sql_observed_ms | 236.128 | 327.683 | 339.755 | 402.987 | 444.228 | 886.911 | 120 |
| hybrid / repository_ms | 513.743 | 710.514 | 763.961 | 888.243 | 955.889 | 2613.391 | 120 |
| hybrid / rrf_ms | 0.416 | 0.966 | 2.006 | 1.655 | 2.596 | 104.341 | 120 |
| hybrid / evidence_selection_ms | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | 0 |
| hybrid / total_retrieval_ms | 515.920 | 712.097 | 767.009 | 889.972 | 957.365 | 2615.306 | 120 |

### Serving phase

| Mode / timer | Minimum | Median | Mean | p90 | p95 | Maximum | n |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| semantic / query_embedding_ms | 0.002 | 0.004 | 0.004 | 0.005 | 0.005 | 0.007 | 120 |
| semantic / connection_acquisition_ms | 0.141 | 0.303 | 0.350 | 0.495 | 0.654 | 1.754 | 120 |
| semantic / database_setup_ms | 113.619 | 208.567 | 217.529 | 274.840 | 322.441 | 513.123 | 120 |
| semantic / sql_observed_ms | 118.252 | 171.027 | 178.003 | 223.277 | 236.568 | 261.336 | 120 |
| semantic / repository_ms | 352.724 | 480.681 | 486.317 | 571.106 | 609.885 | 773.734 | 120 |
| semantic / rrf_ms | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | 0 |
| semantic / evidence_selection_ms | 0.160 | 0.412 | 0.426 | 0.627 | 0.682 | 1.307 | 120 |
| semantic / total_retrieval_ms | 353.395 | 481.067 | 487.151 | 571.693 | 611.448 | 785.970 | 120 |
| lexical / query_embedding_ms | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | 0 |
| lexical / connection_acquisition_ms | 0.151 | 0.311 | 0.374 | 0.468 | 0.624 | 2.468 | 120 |
| lexical / database_setup_ms | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 120 |
| lexical / sql_observed_ms | 205.697 | 319.670 | 346.225 | 408.517 | 515.232 | 1064.225 | 120 |
| lexical / repository_ms | 314.484 | 438.484 | 485.107 | 557.374 | 718.189 | 1742.360 | 120 |
| lexical / rrf_ms | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | 0 |
| lexical / evidence_selection_ms | 0.158 | 0.413 | 0.436 | 0.613 | 0.741 | 1.333 | 120 |
| lexical / total_retrieval_ms | 314.499 | 438.502 | 485.125 | 557.396 | 718.209 | 1742.381 | 120 |
| hybrid / query_embedding_ms | 0.002 | 0.004 | 0.004 | 0.005 | 0.005 | 0.008 | 120 |
| hybrid / connection_acquisition_ms | 0.143 | 0.304 | 0.420 | 0.480 | 0.698 | 5.456 | 120 |
| hybrid / database_setup_ms | 129.649 | 206.272 | 227.180 | 275.255 | 380.861 | 899.202 | 120 |
| hybrid / sql_observed_ms | 226.864 | 326.967 | 335.617 | 395.963 | 409.382 | 780.859 | 120 |
| hybrid / repository_ms | 491.997 | 685.393 | 746.609 | 836.534 | 913.027 | 3283.374 | 120 |
| hybrid / rrf_ms | 0.214 | 0.499 | 2.041 | 0.876 | 1.241 | 175.074 | 120 |
| hybrid / evidence_selection_ms | 0.173 | 0.443 | 0.590 | 0.833 | 0.968 | 12.274 | 120 |
| hybrid / total_retrieval_ms | 493.065 | 687.489 | 749.523 | 837.341 | 914.320 | 3284.851 | 120 |

