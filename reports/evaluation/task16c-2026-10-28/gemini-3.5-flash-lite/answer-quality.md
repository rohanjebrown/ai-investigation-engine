# Answer-quality evaluation

Suite: `retrieval-v1` / `answer-quality-v1`
Corpus: `2wiki-dev-controlled-v1` (93c5ccb10f7fea8dcfbebdf52adc6e3c493f2df71eddb9452383982b4e78cc4e)
Cases: 1

| Mode | Cases | Determinate answer accuracy | Strict evidence recall | Strict citation recall | Correct abstentions | Correct conflicts | Incomplete | Mean latency ms | Mean searches | Mean generation requests |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| multi_step | 1 | 0.0000 | 1.0000 | 1.0000 | 0 | 0 | 0 | 39182.2548 | 3 | 4 |

Execution: `persistent_local_frozen_sample`; generation model `gemini-3.5-flash-lite`.
Generation requests: 4/19; embedding text inputs: 3/15.
Embedding API operations: `{"count_tokens": 3, "embed_content": 3}`.
Database: persistent local PostgreSQL 18.4/pgvector evaluation database; Cloud SQL contacted: False.


## By category

See JSON for per-category aggregates and each question's source occurrences, cited/retrieved evidence, searches, usage, error classes, paired deltas and unavailable metrics.

Gold matching is not semantic claim verification. Answers without a deterministic accepted value require human review.

## Errors and review

### investigation-real-gemini
- `2wiki:2207e9060bda11eba7f7acde48001122` (two_hop): expected `answer`; actual `incorrect_answer` / `answered`; strict supporting facts retrieved 2/2; cited 2/2; classification `reasoning_or_synthesis_failure`; observation: Observed outcome: incorrect_answer. Hypothesis: Gold matching and output status expose the failure class but do not prove its semantic cause.

Semantic claim support requires human review; unsupported-claim counts are not inferred from gold-source matching.
Execution failures without a completed application result have unobserved evidence metrics, not zero-recall scores.
The report contains expected answers and labels for audit, but these values are excluded from application/provider inputs.
