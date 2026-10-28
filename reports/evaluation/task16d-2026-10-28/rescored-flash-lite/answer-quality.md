# Answer-quality evaluation

Suite: `retrieval-v1` / `answer-quality-v1`
Corpus: `2wiki-dev-controlled-v1` (93c5ccb10f7fea8dcfbebdf52adc6e3c493f2df71eddb9452383982b4e78cc4e)
Cases: 1

| Mode | Cases | Determinate answer accuracy | Strict evidence recall | Strict citation recall | Correct abstentions | Correct conflicts | Incomplete | Mean latency ms | Mean searches | Mean generation requests |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| multi_step | 1 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 0 | 39182.2548 | 3 | 4 |

Execution: `persistent_local_frozen_sample`; generation model `gemini-3.5-flash-lite`.
Generation requests: 4/19; embedding text inputs: 3/15.
Embedding API operations: `{"count_tokens": 3, "embed_content": 3}`.
Database: persistent local PostgreSQL 18.4/pgvector evaluation database; Cloud SQL contacted: False.


## By category

See JSON for per-category aggregates and each question's source occurrences, cited/retrieved evidence, searches, usage, error classes, paired deltas and unavailable metrics.

Gold matching is not semantic claim verification. Answers without a deterministic accepted value require human review.

## Independent answer and grounding assessments

Correctness concerns the benchmark answer value. Citation IDs and gold-fact overlap do not prove semantic support or entity identity.

| Mode / Case | Answer correctness | Citation IDs | Semantic evidence support | Entity identity |
|---|---|---|---|---|
| multi_step / `2wiki:2207e9060bda11eba7f7acde48001122` | correct | valid | unsupported | unresolved |

Explicit source reviews, where present, are attributed and bound to the exact saved answer and evidence fingerprint.

## Errors and review

### investigation-real-gemini
- `2wiki:2207e9060bda11eba7f7acde48001122` (two_hop): expected `answer`; actual `correct_answer` / `answered`; strict supporting facts retrieved 2/2; cited 2/2; classification `unavailable`; observation: unavailable Hypothesis: not assigned Source review (Codex source audit, Task 16D; explicit reviewable judgment, not an automatic semantic score): unsupported; E1 establishes the director name. E6 establishes a nationality-related fact about an Olympic rower with a shared name, but no cited passage links that rower to the film director. Occupational differences warrant corroboration and do not alone prove different people. The cited excerpts fail to establish the necessary identity bridge.

Semantic claim support requires human review; unsupported-claim counts are not inferred from gold-source matching.
Execution failures without a completed application result have unobserved evidence metrics, not zero-recall scores.
The report contains expected answers and labels for audit, but these values are excluded from application/provider inputs.
