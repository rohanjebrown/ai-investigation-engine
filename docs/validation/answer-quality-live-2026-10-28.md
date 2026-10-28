# Answer-quality evaluation

Suite: `retrieval-v1` / `answer-quality-v1`
Corpus: `2wiki-dev-controlled-v1` (668b1a12b649a0ef2143f9b8ed22de0050a651c7e42fb9bae627b44273fb312b)
Cases: 2

| Mode | Cases | Determinate answer accuracy | Strict evidence recall | Strict citation recall | Correct abstentions | Correct conflicts | Incomplete | Mean latency ms | Mean searches | Mean generation requests |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| single_step | 2 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 0 | 6641.4858 | 1 | 1 |
| multi_step | 2 | unavailable | unavailable | unavailable | 0 | 0 | 0 | unavailable | unavailable | unavailable |

Execution: `partial_live_real_gemini_sample`; generation model `gemini-3.6-flash`.
Generation requests: 2/7; embedding text inputs: 17/22.
Embedding API operations: `{"count_tokens": 17, "embed_content": 17}`.
Database: disposable local PostgreSQL/pgvector only; Cloud SQL contacted: False.


## By category

See JSON for per-category aggregates and each question's source occurrences, cited/retrieved evidence, searches, usage, error classes, paired deltas and unavailable metrics.

Gold matching is not semantic claim verification. Answers without a deterministic accepted value require human review.

## Errors and review

### single-step-real-gemini
- `contradictory:0` (contradictory): expected `report_conflict`; actual `provider_failure` / `provider_error`; strict supporting facts retrieved unobserved/2; cited unobserved/2; classification `provider_failure`; observation: Execution raised GenerationProviderError; no completed result exposed its evidence or citations. Hypothesis: The sanitized exception type does not identify the failing workflow stage or underlying provider cause.

### multi-step-real-gemini
- `2wiki:8813f87c0bdd11eba7f7acde48001122` (two_hop): expected `None`; actual `not_run` / `not_run`; strict supporting facts retrieved unavailable/unavailable; cited unavailable/unavailable; classification `unavailable`; observation: unavailable Hypothesis: not assigned
- `contradictory:0` (contradictory): expected `None`; actual `not_run` / `not_run`; strict supporting facts retrieved unavailable/unavailable; cited unavailable/unavailable; classification `unavailable`; observation: unavailable Hypothesis: not assigned

Semantic claim support requires human review; unsupported-claim counts are not inferred from gold-source matching.
Execution failures without a completed application result have unobserved evidence metrics, not zero-recall scores.
The report contains expected answers and labels for audit, but these values are excluded from application/provider inputs.
