# Task 16: Real Gemini answer quality and investigation

## Executive result

This is a **five-question stratified pilot**, not a full-suite accuracy estimate.
The original 40-case suite and source-only corpus were preserved. Three question
pairs have saved, completed application outcomes; the fourth saved pair ended in
a Gemini availability error during investigation; the simple-factual pair's
responses were lost to a local reporting bug and were not regenerated.

No completed run produced an answer. Quick Answer abstained on the answerable
two-hop and bridge questions, correctly abstained on the unanswerable control,
and missed the contradiction. The three saved investigations returned
`incomplete` because the experiment allowed only one search; each requested a
second search, which the application correctly blocked at the configured limit.
The contradictory investigation ended in HTTP 503. This sample therefore does
**not** establish that investigation improves answer correctness, citation
coverage, or contradiction handling. It shows a retrieval-evidence gap and an
investigation budget too small to reach a final decision on the selected
multi-step cases.

## Task 15 retrieval baseline

The complete 40-question retrieval suite scored 34 questions with gold evidence;
the six unanswerable controls have no supporting-fact denominator.

| Mode | Recall@5 | Recall@10 | Recall@20 | MRR@20 | All required facts @20 | Mean retrieval latency |
|---|---:|---:|---:|---:|---:|---:|
| Semantic | 0.8750 | 0.9265 | 0.9412 | 0.9853 | 0.8235 | 55.57 ms |
| Hybrid, RRF k=60 | 0.8750 | 0.9265 | 0.9412 | 0.9853 | 0.8235 | 64.34 ms |

Category results at K=20: simple factual 8/8 complete; two-hop 10/12; bridge
comparison 4/8; contradictory 6/6; unanswerable is unscored. Bridge comparisons
were the weakest category. Semantic and hybrid measurements were identical for
this snapshot; hybrid added about 8.8 ms mean retrieval latency. See the [Task
15 results](../../reports/evaluation/task15-2026-10-28/retrieval.md) and
[validation report](batched-gemini-evaluation-2026-10-28.md).

The measured K=20 coverage is useful for a bounded answer-quality pilot, but
the bridge gap means a missing final answer must be separated into retrieval
coverage and reasoning. The Task 16 answerable two-hop/bridge sample was chosen
from the lowest semantic evidence-coverage cases rather than only from prior
successes.

## Environment and configuration

- Dataset: 2WikiMultihopQA development revision
  `612bc5039a457880d9e7d84c3b0a4cf154b70e4f`.
- Answer suite: `answer-quality-v1`, fingerprint
  `3506da0cccb341fbde307fad8a57b8ae0d97cb42495998681759a96fe132bda0`.
- Source corpus fingerprint:
  `93c5ccb10f7fea8dcfbebdf52adc6e3c493f2df71eddb9452383982b4e78cc4e`.
- Persistent database: local PostgreSQL 18.4 / pgvector, `ai_investigation_evaluation`
  at `127.0.0.1:55433`; **218** manifest-verified source occurrences, **197**
  compatible vectors, **218** active/searchable versions, zero incomplete plans.
- Gold questions/annotations were not inserted into searchable content. The
  database's source text and occurrence provenance were checked against the
  source-only corpus manifest before execution.
- Primary paired configuration: semantic retrieval, Gemini Embedding 2,
  768 dimensions, whole-passage chunking (MongoDB/voyage-4 pinned artifact,
  32,000 tokens), generation model `gemini-3.6-flash` (same model as the prior
  live evaluation). Both answer modes used these same settings.
- Quick Answer budget: five evidence passages, 12,000 context characters.
  Investigation pilot budget: two generation turns, one search, five results,
  ten accumulated evidence items, 24,000 context characters, one query
  embedding.
- Semantic-vs-hybrid was reported separately using the complete Task 15 results;
  this answer-quality pilot did not change retrieval mode between paired runs.

## Frozen sample

The IDs were saved before generation in
[`selection.json`](../../reports/evaluation/task16-2026-10-28/selection.json).

| Category | Selected ID | Selection rationale / Task 15 semantic coverage |
|---|---|---|
| Simple factual | `simple:8813f87c0bdd11eba7f7acde48001122` | First fixed-suite case; recall@5 1.0 |
| Two-hop | `2wiki:2207e9060bda11eba7f7acde48001122` | Lowest K=20 coverage, then K=5; recall@20 0.5 |
| Bridge comparison | `2wiki:298f23b8088a11ebbd6eac1f6bf848b6` | Tied lowest K=20 and K=5 coverage; recall@20 0.75 |
| Unanswerable | `unanswerable:0` | First fixed-suite control |
| Contradictory | `contradictory:0` | First fixed-suite case; recall@20 1.0 |

All five categories were attempted. Three cases have both mode results for
paired analysis. The contradiction's investigation result is a provider failure
and is excluded from completed pairs. The simple-factual responses were lost
after generation and are recorded as a local result-persistence failure, not
scored as correct or incorrect.

The original 40-case `answer-quality-v1` suite remains unchanged. The existing
`evaluate_answers` function evaluates the complete suite when `case_ids` is
omitted; the persistent runner also accepts a frozen selection manifest.

## Answer and citation outcomes

| Measure | Quick Answer | Investigation |
|---|---:|---:|
| Selected cases | 5 | 5 |
| Persisted application outcomes | 4 | 4 |
| Correct generated answers | 0 | 0 |
| Incorrect generated answers | 0 | 0 |
| Incorrect abstentions on answerable cases | 2 | Not applicable; no final answers |
| Correct insufficient-evidence outcomes | 1 | 0 |
| Missed contradictions | 1 | Not observed; one provider failure |
| Incomplete investigations | 0 | 3 |
| Provider failures | 0 | 1 |
| Lost local result pairs | 1 | 1 |

The Quick Answer results for the two-hop and bridge cases were
`insufficient_evidence`, classified as incorrect abstentions because the gold
behavior is to answer. The unanswerable control correctly abstained. On the
contradictory question, Quick Answer retrieved both annotated facts (2/2) but
returned insufficient evidence instead of identifying the conflict.

All saved Quick Answer outcomes contained no citations. On the three completed
investigation outcomes, strict citation recall was also zero because the runs
were incomplete and returned no citations. Citation precision is undefined
when no evidence is cited. No structurally malformed citation was observed.
No citation entries were produced in the saved outcomes, so the observed
structurally valid citation count is zero and the malformed count is zero.
These gold-occurrence metrics do not establish semantic claim grounding.

No unsupported answer was produced. There were no completed factual answers to
score as correct/incorrect or to assess for semantic citation support. Human
semantic review remains necessary for any future answered outcomes.

## Investigation behavior and paired comparison

| Matched case | Quick Answer | Investigation | Retrieved required facts | Strict citation facts |
|---|---|---|---:|---:|
| Two-hop | Incorrect abstention | Incomplete at search limit | 1/2 in both | 0/2 in both |
| Bridge comparison | Incorrect abstention | Incomplete at search limit | 2/4 in both | 0/4 in both |
| Unanswerable | Correct abstention | Incomplete at search limit | No gold facts | N/A |

Across the three completed pairs, outcome correctness did not improve; retrieval
recall delta was **0**, citation-recall delta was **0**, and investigation used
one additional generation request per case. Observed wall-latency delta was
**+14.80 seconds** on average. It includes the conservative 12.5-second
request-spacing delay, so it is not an estimate of intrinsic model overhead.
This comparison is descriptive and too small to support a general claim.

The three incomplete investigations each executed one successful search and
then requested a second search. The recorded second search was blocked by the
one-search evaluation budget (`search_limit`) and did not retrieve evidence.
The two-hop follow-up query was `Eric Mueller`; the bridge follow-up was
`Love In Pawn director`; the unanswerable follow-up was `"Unlisted-0"
"constructed"`. These are observable follow-up plans; the unexecuted searches
cannot be judged for retrieval quality. No follow-up found an additional
supporting fact because none ran.

The contradictory investigation failed on HTTP 503 before a result object was
available. Its retrieval/citation metrics are unobserved, not zero. The
sanitized diagnostic classifies this as `availability`, retryable, and not a
rate/quota error. The request was not retried.

## API usage, quota, and latency

- Generation requests attempted: **15/15 cap** (5 Quick Answer, 10
  investigation turns), including the three attempts from the first pair whose
  result report was lost. No provider retries were enabled. Requests were
  separated by at least 12.5 seconds.
- Gemini embedding query inputs in the resumed run: **8**; eight `countTokens`
  and eight `embedContent` operations; **134 counted tokens**. Embedding-call
  accounting for the lost first process is unavailable.
- Generation token usage recorded: **10,920 prompt**, **181 candidate**, and
  **2,009 thinking** tokens. Usage metadata is unavailable for three requests
  from the lost first pair and the HTTP 503 request.
- Saved Quick Answer mean latency: **10.32 s** across four scored/persisted
  outcomes. Investigation mean latency: **27.20 s** across three incomplete
  outcomes. Total measured resumed-run wall time was **147.84 s**; the lost
  first-pair duration is not included.
- Active project quota could not be read from the Gemini API key or AI Studio
  window. The user-reported 20 RPD limit was used as an assumption, with a
  15-request hard cap and reserved headroom. Google documents that RPD is
  project-scoped and resets at midnight Pacific time in its [rate-limit
  guide](https://ai.google.dev/gemini-api/docs/rate-limits). No billing was
  enabled. A 503 availability response occurred; it was not classified as
  quota exhaustion.

## Reporting incident and failure analysis

The initial simple-factual pair returned from both application calls, but a
local paired-metric aggregator raised `NameError` before the result rows were
written. The evaluation did not rerun those successful provider calls. Three
generation requests are counted, the exact answer/search responses are
unavailable, and the rows are marked `execution_failure`. The aggregator defect
was fixed; provider-error cases are now excluded from completed paired
comparisons. A regression test covers that rule.

Confirmed observations:

- The two-hop retrieval gap is present in both modes: one of two facts found.
- The bridge gap is present in both modes: two of four facts found.
- The contradiction Quick Answer saw both gold facts but did not report a
  conflict; this is a generation/decision failure in that run, not a retrieval
  miss.
- All three investigation outcomes were stopped by the explicit one-search
  budget after the model requested another search. The final decision was never
  reached.
- The contradictory investigation provider call failed with HTTP 503
  availability. No cause beyond the sanitized diagnostic is inferred.

No prompt was tuned to individual examples. The source suite is derived from a
development split, the sample has only one item per category, and no full-suite
answer evaluation was performed. The first pair's generation outcome and token
usage remain unavailable.

## Tests and recommendation

The final verification passed **469 Python unit tests**, **161 PostgreSQL
integration tests**, and **26 JavaScript tests**; four opt-in integration tests
were skipped. The existing upstream Google Gen AI typing deprecation warning
remains. This covers document management, indexing, authentication, retrieval,
Quick Answer, and investigation paths. No embedding regeneration, retrieval ranking/RRF change, database
policy change, Cloud SQL connection, paid infrastructure, or deployment was
performed.

This evidence is **not sufficient for deployment readiness**. Quick Answer did
not produce a scorable answer in this sample; the contradiction control was
missed; and investigation could not complete under the quota-safe budget. A
larger paired sample with enough search/generation turns, current quota
visibility, and saved per-call response usage is needed before making quality
or superiority claims. The 40-question suite remains available for a later
full run.

Machine-readable results and four per-question checkpoints are in
[`reports/evaluation/task16-2026-10-28`](../../reports/evaluation/task16-2026-10-28/answer-quality.json).
