# Automated answer-quality evaluation (E39)

The E39 evaluator extends the E32 retrieval suite without changing production
retrieval. It calls the existing `AnswerQuestion` and `InvestigateQuestion`
services and reports their outcomes against the same source-only corpus. Gold
questions, expected answers, and supporting-fact labels stay outside document
text and provider inputs.

## Suite review and scoring

The fixed `answer-quality-v1` snapshot retains 40 questions: 8 single-fact
projections, 12 two-relation questions, 8 branching bridge comparisons, 6
unanswerable controls, and 6 controlled contradictions. The bridge cases are
comparisons across two subject-to-person-to-attribute chains, not linear
four-hop paths. The six contradiction cases gained evaluation-only labels tying
each asserted value to its exact supporting source occurrence and sentence.
Dataset revision, source coordinates, original expected answers, and occurrence
provenance are validated when loading the suite.

Answer scoring is deliberately conservative. Unicode NFC, case folding, word
and punctuation boundaries are used for explicit expected/accepted values;
negative mentions do not receive positive credit. A short direct value that
does not match is incorrect. Longer answers without an explicitly accepted
literal value require human review. There is no LLM judge and no inferred
semantic answer score.

Citation scoring is separate from answer scoring. Strict support requires the
same source occurrence and Content hash, the complete annotated sentence range,
and its original sentence locator. Partial overlap is reported separately and
does not count as strict support. Repeated evidence identities count once.
These measures establish gold-occurrence agreement, not whether every generated
claim logically follows from its excerpt. Semantic claim support remains a
human-review measure; unsupported-claim counts are not fabricated.

Outcome counts distinguish correct/incorrect answers, correct/incorrect
abstention, unsupported answers, correct/missed/false conflicts, incomplete
investigations, provider failures, and cases not run. A run that fails before
returning evidence marks retrieval/citation observations unavailable rather
than assigning zero recall. Error analysis records observations separately from
hypotheses. Search/generation counts, latency, usage, retrieval/citation
coverage, per-category metrics, and paired per-question deltas are recorded.
Paired comparisons require matching corpus, chunking, embedding, retrieval, and
generation configurations; descriptive deltas do not claim statistical
significance or multi-step superiority.

The stable machine-readable report contains suite/corpus fingerprints, question
IDs, expected labels, actual results, resolved trusted evidence and source
coordinates, search records, model versions, usage, failures, and explicit null
metrics. Expected labels appear only in the report after the application call.
Console summaries use ASCII-safe JSON for Windows terminals; full exports use
UTF-8 JSON and Markdown.

## Offline verification

```powershell
uv run --no-sync pytest tests/unit -q
uv run --no-sync pytest tests/integration -q
node --test tests/frontend/test_web.cjs
```

The unit suite exercises hand-checked answer values, citations, abstention,
contradiction matching, failures, stop-on-provider-error, paired comparisons,
JSON/Markdown export stability, and gold-label isolation. A separate disposable
PostgreSQL integration test indexes a real manifest source, activates it,
retrieves through the existing repository, and compares single-step and
multi-step use cases with deterministic fake generation and embeddings. It also
checks that no database connection remains open while fake generation runs.
These offline tests do not call Gemini or Cloud SQL.

Executed on 2026-10-28: **445 unit + 159 PostgreSQL integration tests passed**;
4 opt-in tests were skipped in the ordinary integration run. **26 JavaScript
tests passed.** The upstream Google Gen AI SDK emitted one typing deprecation
warning. The separate opt-in live sample below used disposable local PostgreSQL.

## Partial live sample

The [JSON result](answer-quality-live-2026-10-28.json) and
[Markdown summary](answer-quality-live-2026-10-28.md) preserve the complete
captured sample. It paired a 2Wiki two-hop question and one controlled
contradiction across the same 12 source occurrences and 12 exact Contents,
using the pinned dev revision, Gemini Embedding 2 (768 dimensions), and
Gemini 3.6 Flash. No questions, answers, or gold labels were indexed. The model
selection was the previously exercised 3.6 alias; the user reported that 3.8
Flash has the same limits, but it was not substituted.

The single-step two-hop run answered **“The mother of the director of
Polish-Russian War (Xawery Żuławski) is actress Małgorzata Braunek.”** The answer
matches the benchmark's expected value. It retrieved five passages; both gold
supporting facts were strictly retrieved (2/2) and both were cited (2/2). Strict
retrieval precision was 0.40 because three additional passages were retrieved;
citation precision was 1.00. The factual claim was manually checked against the
film passage naming Xawery Żuławski as director and his biographical passage
identifying Małgorzata Braunek as his mother. This is one checked answer, not a
general grounding guarantee.

The next single-step generation attempt failed with sanitized
`GenerationProviderError`. The provider adapter did not retain its HTTP status,
so the exact cause cannot be distinguished as quota, availability, or another
rejection. The evaluator stopped the paired run immediately: no multi-step
generation was sent, and the contradiction was not scored. No retry was made.
Because that failed use-case returned no result object, its retrieval and
citation metrics are unavailable rather than zeros. Thus **there are no paired
case comparisons** and no live contradiction or multi-step quality result.

The run made 17 document/query embedding inputs: **17 `count_tokens` and 17
`embed_content` API operations**. It attempted **2 generation requests** (one
successful, one provider failure) against a hard cap of 7; embedding inputs
were capped at 22. Provider retries were disabled. The successful answer
reported 633 prompt, 44 candidate, and 263 thinking tokens (940 total). Usage
for the failed request was unavailable.

The user reported Free tier with billing disabled and project limits of 100 RPM,
30,000 TPM, 1,000 RPD for Gemini Embedding 2 and 5 RPM, 250,000 TPM, 20 RPD for
Gemini 3.6/3.8 Flash. These account limits and billing status were not
independently queried. Prior live tests had an upper bound of 12 generation
requests; with this run's 2 attempts the known test total is at most 14, but
untracked account/project usage was unavailable. The sample made no Cloud SQL
connection, used only the disposable local PostgreSQL/pgvector fixture, and
made no paid-only request.

The original 40-question suite remains the benchmark. This live evaluation is a
partial two-case sample with one successful single-step answer. Remaining work
for a real-model quality evaluation includes the multi-step and contradiction
cases, abstention behavior, a broader sample across all categories, and a
complete paired run if free-tier quota permits. The benchmark is derived from
the development split, not a held-out test set; semantic citation support and
paraphrases still require human review. No prompt tuning was performed.

## Task 16D: answer values and grounding are independent

The evaluation-only scorer `answer-values-v2-nationality-aliases` adds one
reviewed rule: a question of the form **“What nationality is/was …?”** with the
gold value **“United States”** may accept the direct answer **“American”**.
Case, surrounding punctuation, and citation markers are ignored for this
direct-value comparison. The whole value must match; `South American`,
`Native American`, `American Samoa`, negations, and multiple alternatives do
not qualify. The rule does not infer citizenship, residence, origin, or other
country/demonym equivalences. Longer paraphrases retain the existing review
policy. Gold answers and their original acceptable-answer lists are preserved;
reports record aliases, matched rule, and scorer version separately.

Reports now show independent answer correctness, citation identifier validity,
annotated supporting-fact overlap, semantic evidence support, and cross-document
entity identity. Even complete gold coverage defaults to `requires_review` for
semantic support and identity. Explicit source reviews can record supported,
unsupported, or unresolved support and are bound to a fingerprint of the actual
saved question, answer, citations, and evidence. A changed answer or evidence
invalidates that review. These are attributed review judgments, not automatic
semantic scores or an LLM judge.

Use `scripts/rescore_answer_quality.py` to rescore saved results into a **fresh**
directory. It verifies unchanged suite/corpus fingerprints, preserves previous
score labels, and records the source report SHA-256; it has no provider or
database execution path. Historical reports are not rewritten.

Task 16D also adds source-independent investigation guidance to retain identifying
context, corroborate ambiguous cross-source identity, and abstain when a necessary
identity link cannot be established. Prompt guidance is not a deterministic
entity resolver and has not been verified by a new live run. The recorded
`gemini-3.5-flash-lite` response predates this change; the earlier
`gemini-3.6-flash` failure remains separately documented. See
[the Task 16D report](../../reports/evaluation/task16d-2026-10-28/analysis.md).
