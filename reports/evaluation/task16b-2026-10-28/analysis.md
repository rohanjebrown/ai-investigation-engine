# Task 16B — Evaluation correction and result

## Scope and status

Task 16’s reports remain unchanged in `reports/evaluation/task16-2026-10-28/`. This run reused the frozen five-question selection, the existing local PostgreSQL evaluation database, the same 218 searchable occurrences and 197 stored embeddings, Gemini `gemini-3.6-flash`, and semantic retrieval. No document embeddings were regenerated, no corpus was reindexed, and no cloud database or paid infrastructure was used.

The corrected runner supports five searches and compatible final-answer budgets. The controlled live run used one paired two-hop case because the account’s remaining RPD could not be read. It did not produce a final completed Investigation: Gemini’s final continuation request was rejected with HTTP 400. The required answerable multi-step case therefore remains incomplete, and the bridge comparison was not started. The system is **not ready for a public demonstration of completed multi-step answer quality**.

## Root cause and budgets

`InvestigateQuestion` has independent limits for model turns, generation requests, search calls, results per search, accumulated evidence, context size, and query embeddings. The FastAPI defaults were already compatible: 6 turns, 5 searches, 5 results per search, 15 accumulated evidence items, 24,000 context characters, 6 generation requests, and 5 query embeddings. A final decision after five searches needs at least six turns and six generation requests.

The Task 16 evaluation wrapper overrode those defaults with `max_search_calls=1`, `max_turns=2`, `max_generation_requests=2`, and one query embedding. The state machine correctly recorded the model’s next search as `SEARCH_LIMIT` and returned `incomplete`; it did not turn budget exhaustion into abstention.

Task 16B’s runner now records and validates independent budgets. Its normal defaults are 6 turns, 5 searches, 5 results per search, 15 accumulated evidence items, 24,000 context characters, 6 generation requests, and 5 embeddings. Validation requires enough turns and generation requests for all configured searches plus one final decision, and one embedding per search. The first live attempt used a quota-bounded 2-search / 3-turn / 3-generation / 2-embedding budget. Gemini requested a third search, so the runner saved `incomplete`. A continuation reused that already generated query, executed it, then attempted one final generation request.

## Persistence and resume correction

The previous simple-factual pair failed after both application calls returned because local aggregation raised before saving either row. Task 16B now:

- Writes Quick Answer and Investigation checkpoints separately, atomically, in ASCII-escaped JSON so Unicode cannot break serialization.
- Saves a completed result through an evaluator callback before aggregate/report generation.
- Saves usage and sanitized provider diagnostics with the mode result when available.
- Verifies checkpoint question, mode, and configuration before resuming; completed mode requests are skipped.
- Keeps the frozen selection and per-question checkpoints in this Task 16B directory.

Offline tests reproduced an aggregation failure after the callback and confirmed that the result and provider usage survived. A fake-provider continuation test confirmed that saved dependent search exchanges can be rehydrated and that the pending query is not regenerated. During the real continuation, the final provider request was rejected before the search result had been checkpointed. The report records that result as unavailable; the two preceding search exchanges remain preserved. The continuation code now checkpoints search results before making the following generation request.

## Quick Answer diagnostics

Quick Answer uses the original question as its semantic retrieval query, with `max_evidence=5` and a 12,000-character serialized context budget. The saved `retrieved_evidence` entries are the evidence selected for the generation request after the context-fit check. All five retrieved items fit and were supplied in each reviewed case.

| Frozen case | Evidence in Quick Answer context | Model result | Finding |
| --- | ---: | --- | --- |
| Two-hop: nationality of the director of *World And Time Enough* | 1 of 2 required facts, among 5 results | `insufficient_evidence`; no citations | The second supporting fact was absent. This is a retrieval coverage gap, not evidence excluded by the generation context. |
| Bridge comparison: *El Extraño Viaje* vs *Love In Pawn* directors’ birth years | 2 of 4, among 5 results | `insufficient_evidence`; no citations | Top-K evidence was incomplete. The broader Recall@20 result did not establish that Quick Answer received all four facts. |
| Contradiction: access code for `Conflict-0` | Both labeled facts, plus distractors, among 5 results | `insufficient_evidence`; no citations | Both passages reached the model, but it abstained instead of reporting the conflict. Validation did not alter the outcome. |

The Quick Answer schema represents `answered` or `insufficient_evidence`; it has no structured conflict status. A conflict can be described in an answered response and is checked by the evaluator, but the Quick Answer instructions do not clearly require that behavior when the sources conflict. This run provides evidence of model/prompt-contract behavior, not a confirmed citation-validation defect. No production prompt or schema tuning was made.

## Live paired case

The executed pair used the frozen two-hop ID `2wiki:2207e9060bda11eba7f7acde48001122`.

| Measure | Quick Answer | Investigation |
| --- | ---: | ---: |
| Outcome | Incorrect abstention | Incomplete / budget limited |
| Required facts retrieved | 1/2 (strict recall 0.5) | 2/2 before continuation (strict recall 1.0) |
| Required facts cited | 0/2 | 0/2; no final decision was produced |
| Generation requests in scored row | 1 | 3 before continuation |
| Search sequence | Original question, one retrieval | `World And Time Enough director nationality` → `Eric Mueller` → model requested `"Eric Mueller" director OR filmmaker OR American` |
| Latency in scored row | 10.25 s | 48.60 s through the initial incomplete result |

The second query used the director’s name found in the first search. The first two executed searches retrieved both annotated facts (strict evidence recall 1.0); the third query was initially blocked by the 2-search cap. The continuation executed that saved query without repeating the prior three generation calls. Its final Gemini request returned sanitized HTTP 400 (`request_rejected`, not a quota error). No retry was made. That request used a reconstructed continuation transcript because Task 16 did not persist Gemini’s raw continuation payload; the exact provider rejection reason was not available. The final answer and citations are unavailable. The continuation’s third-search result count and evidence could not be recovered from the failed run, so they are not included in the scored row.

The saved paired-comparison metrics cover only the initial Quick Answer and incomplete Investigation rows: one paired case, +0.5 strict retrieval recall, zero citation-recall change, +2 generation calls, and +38.35 seconds. These are descriptive and incomplete; the continuation request/search is separately recorded in the execution ledger. The incomplete outcome is not counted as an incorrect answer.

## Quota, failures, and remaining sample

Task 16 had 15 attempted generation requests. Task 16B attempted 4 for the initial pair and 1 for the continuation, for 20 known attempts under the previously reported 20-RPD ceiling. AI Studio’s project-level remaining quota was unavailable through the API key and the user could not view it; this is an estimate from the prior reported limit, not an independently verified quota reading. There were no automatic retries. The continuation HTTP 400 consumed the final conservative request allowance. Earlier Task 16 embedding usage for the lost simple pair is unknown. The continuation used one query-embedding input (one token-count and one embed-content request); its counted-token total was not persisted and is marked partial.

The frozen bridge case was not attempted in Task 16B because the conservative generation cap was reached. The unanswerable and contradictory cases were not repeated. The lost simple-factual pair is marked unavailable and was not regenerated. Previous Task 16 provider failure and outputs remain in their historical report.

## Verification and limits

- Python: **471 passed** (including unit and PostgreSQL integration tests); one upstream Google GenAI deprecation warning.
- JavaScript: **26 passed**.
- Offline pending-search continuation: fake search and generation providers passed; no network used.
- Real provider failures: one continuation HTTP 400; zero retries.
- No public deployment, Cloud SQL, paid service, commit, or push.

The primary evaluation objective remains outstanding: no real Investigation reached a final decision with valid citations, no bridge pair was attempted, and the final continuation response was rejected. Use the saved search/action checkpoints and wait for a new quota window before any additional generation request. Do not repeat the successful Quick Answer or the three previously successful investigation turns.
