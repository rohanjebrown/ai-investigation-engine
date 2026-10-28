# Answer-quality evaluation

Suite: `retrieval-v1` / `answer-quality-v1`
Corpus: `2wiki-dev-controlled-v1` (93c5ccb10f7fea8dcfbebdf52adc6e3c493f2df71eddb9452383982b4e78cc4e)
Cases: 5

| Mode | Cases | Determinate answer accuracy | Strict evidence recall | Strict citation recall | Correct abstentions | Correct conflicts | Incomplete | Mean latency ms | Mean searches | Mean generation requests |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| single_step | 5 | unavailable | 0.6667 | 0.0000 | 1 | 0 | 0 | 10320.4616 | 1 | 1 |
| multi_step | 5 | unavailable | 0.5000 | 0.0000 | 0 | 0 | 3 | 27199.5060 | 2 | 2 |

Execution: `persistent_task16_live_paired_sample`; generation model `gemini-3.6-flash`.
Generation requests: 15/15; embedding text inputs: 8/15.
Embedding API operations: `{"count_tokens": 8, "embed_content": 8}`.
Database: persistent local PostgreSQL 18.4/pgvector evaluation database; Cloud SQL contacted: False.


## By category

See JSON for per-category aggregates and each question's source occurrences, cited/retrieved evidence, searches, usage, error classes, paired deltas and unavailable metrics.

Gold matching is not semantic claim verification. Answers without a deterministic accepted value require human review.

## Errors and review

### quick-answer-real-gemini
- `simple:8813f87c0bdd11eba7f7acde48001122` (simple_factual): expected `answer`; actual `execution_failure` / `failed`; strict supporting facts retrieved unobserved/1; cited unobserved/1; classification `result_persistence_failure`; observation: The application calls completed, but a local report aggregator raised before their result rows were written. Hypothesis: Exact answers and evidence are unavailable. Calls were not repeated to avoid regenerating successful responses.
- `2wiki:2207e9060bda11eba7f7acde48001122` (two_hop): expected `answer`; actual `incorrect_abstention` / `insufficient_evidence`; strict supporting facts retrieved 1/2; cited 0/2; classification `search_planning_or_retrieval_failure`; observation: Strictly retrieved 1 of 2 required facts. Hypothesis: Search logs identify query order and returned evidence, but do not establish whether planning or ranking caused the gap.
- `2wiki:298f23b8088a11ebbd6eac1f6bf848b6` (three_plus_hop): expected `answer`; actual `incorrect_abstention` / `insufficient_evidence`; strict supporting facts retrieved 2/4; cited 0/4; classification `search_planning_or_retrieval_failure`; observation: Strictly retrieved 2 of 4 required facts. Hypothesis: Search logs identify query order and returned evidence, but do not establish whether planning or ranking caused the gap.
- `contradictory:0` (contradictory): expected `report_conflict`; actual `missed_contradiction` / `insufficient_evidence`; strict supporting facts retrieved 2/2; cited 0/2; classification `reasoning_or_synthesis_failure`; observation: Observed outcome: missed_contradiction. Hypothesis: Gold matching and output status expose the failure class but do not prove its semantic cause.

### investigation-real-gemini
- `simple:8813f87c0bdd11eba7f7acde48001122` (simple_factual): expected `answer`; actual `execution_failure` / `failed`; strict supporting facts retrieved unobserved/1; cited unobserved/1; classification `result_persistence_failure`; observation: The application calls completed, but a local report aggregator raised before their result rows were written. Hypothesis: Exact answers and evidence are unavailable. Calls were not repeated to avoid regenerating successful responses.
- `2wiki:2207e9060bda11eba7f7acde48001122` (two_hop): expected `answer`; actual `incomplete` / `incomplete`; strict supporting facts retrieved 1/2; cited 0/2; classification `budget_exhaustion`; observation: The workflow returned incomplete or recorded a configured resource limit. Hypothesis: The current per-run budgets ended the investigation before a final decision.
- `2wiki:298f23b8088a11ebbd6eac1f6bf848b6` (three_plus_hop): expected `answer`; actual `incomplete` / `incomplete`; strict supporting facts retrieved 2/4; cited 0/4; classification `budget_exhaustion`; observation: The workflow returned incomplete or recorded a configured resource limit. Hypothesis: The current per-run budgets ended the investigation before a final decision.
- `unanswerable:0` (unanswerable): expected `abstain`; actual `incomplete` / `incomplete`; strict supporting facts retrieved 0/0; cited 0/0; classification `budget_exhaustion`; observation: The workflow returned incomplete or recorded a configured resource limit. Hypothesis: The current per-run budgets ended the investigation before a final decision.
- `contradictory:0` (contradictory): expected `report_conflict`; actual `provider_failure` / `provider_error`; strict supporting facts retrieved unobserved/2; cited unobserved/2; classification `provider_failure`; observation: Execution raised GenerationProviderError; no completed result exposed its evidence or citations. Hypothesis: The sanitized exception type does not identify the failing workflow stage or underlying provider/database cause. Provider diagnostic: `generate_content` / HTTP `503` / `availability`; model `gemini-3.6-flash`; request count 8; retryable True.

Semantic claim support requires human review; unsupported-claim counts are not inferred from gold-source matching.
Execution failures without a completed application result have unobserved evidence metrics, not zero-recall scores.
The report contains expected answers and labels for audit, but these values are excluded from application/provider inputs.
