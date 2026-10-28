# Answer-quality evaluation

Suite: `retrieval-v1` / `answer-quality-v1`
Corpus: `2wiki-dev-controlled-v1` (93c5ccb10f7fea8dcfbebdf52adc6e3c493f2df71eddb9452383982b4e78cc4e)
Cases: 1

| Mode | Cases | Determinate answer accuracy | Strict evidence recall | Strict citation recall | Correct abstentions | Correct conflicts | Incomplete | Mean latency ms | Mean searches | Mean generation requests |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| multi_step | 1 | unavailable | unavailable | unavailable | 0 | 0 | 0 | unavailable | unavailable | unavailable |

Execution: `persistent_local_frozen_sample`; generation model `gemma-4-31b-it`.
Generation requests: 1/19; embedding text inputs: 0/15.
Embedding API operations: `{"count_tokens": 0, "embed_content": 0}`.
Database: persistent local PostgreSQL 18.4/pgvector evaluation database; Cloud SQL contacted: False.


## By category

See JSON for per-category aggregates and each question's source occurrences, cited/retrieved evidence, searches, usage, error classes, paired deltas and unavailable metrics.

Gold matching is not semantic claim verification. Answers without a deterministic accepted value require human review.

## Errors and review

### investigation-real-gemini
- `2wiki:2207e9060bda11eba7f7acde48001122` (two_hop): expected `answer`; actual `provider_failure` / `provider_error`; strict supporting facts retrieved unobserved/2; cited unobserved/2; classification `provider_failure`; observation: Execution raised GenerationProviderError; no completed result exposed its evidence or citations. Hypothesis: The sanitized exception type does not identify the failing workflow stage or underlying provider/database cause. Provider diagnostic: `generate_content` / HTTP `500` / `availability`; model `gemma-4-31b-it`; request count 1; retryable True.

Semantic claim support requires human review; unsupported-claim counts are not inferred from gold-source matching.
Execution failures without a completed application result have unobserved evidence metrics, not zero-recall scores.
The report contains expected answers and labels for audit, but these values are excluded from application/provider inputs.
