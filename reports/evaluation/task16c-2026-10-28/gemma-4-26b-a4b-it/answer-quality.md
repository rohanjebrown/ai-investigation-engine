# Answer-quality evaluation

Suite: `retrieval-v1` / `answer-quality-v1`
Corpus: `2wiki-dev-controlled-v1` (93c5ccb10f7fea8dcfbebdf52adc6e3c493f2df71eddb9452383982b4e78cc4e)
Cases: 1

| Mode | Cases | Determinate answer accuracy | Strict evidence recall | Strict citation recall | Correct abstentions | Correct conflicts | Incomplete | Mean latency ms | Mean searches | Mean generation requests |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| multi_step | 1 | unavailable | 0.0000 | 0.0000 | 0 | 0 | 0 | 1904.6024 | 0 | 1 |

Execution: `persistent_local_frozen_sample`; generation model `gemma-4-26b-a4b-it`.
Generation requests: 1/19; embedding text inputs: 0/15.
Embedding API operations: `{"count_tokens": 0, "embed_content": 0}`.
Database: persistent local PostgreSQL 18.4/pgvector evaluation database; Cloud SQL contacted: False.


## By category

See JSON for per-category aggregates and each question's source occurrences, cited/retrieved evidence, searches, usage, error classes, paired deltas and unavailable metrics.

Gold matching is not semantic claim verification. Answers without a deterministic accepted value require human review.

## Errors and review

### investigation-real-gemini
- `2wiki:2207e9060bda11eba7f7acde48001122` (two_hop): expected `answer`; actual `incorrect_abstention` / `insufficient_evidence`; strict supporting facts retrieved 0/2; cited 0/2; classification `search_planning_or_retrieval_failure`; observation: Strictly retrieved 0 of 2 required facts. Hypothesis: Search logs identify query order and returned evidence, but do not establish whether planning or ranking caused the gap.

Semantic claim support requires human review; unsupported-claim counts are not inferred from gold-source matching.
Execution failures without a completed application result have unobserved evidence metrics, not zero-recall scores.
The report contains expected answers and labels for audit, but these values are excluded from application/provider inputs.
