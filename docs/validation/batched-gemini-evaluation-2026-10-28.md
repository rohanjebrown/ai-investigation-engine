# Task 15: Gemini batching and persistent evaluation corpus

## Result

The primary acceptance objective is complete. The full fixed evaluation corpus
is registered, whole-passage planned, indexed with real `gemini-embedding-2`
vectors, activated, and searchable in a dedicated persistent local PostgreSQL
18.4/pgvector database. The database container was removed and recreated while
retaining its named volume; PostgreSQL migrations, all 197 vectors, all active
versions, and both semantic and hybrid searches remained available.

The indexed source snapshot contains 218 source occurrences and 197 unique exact
Contents. It retains original document identities, source locators, provenance,
sentence spans, and distractor passages. Questions, expected answers, supporting
fact labels, and reasoning annotations were not registered. The corpus and suite
fingerprints are `93c5ccb10f7fea8dcfbebdf52adc6e3c493f2df71eddb9452383982b4e78cc4e`
and `3506da0cccb341fbde307fad8a57b8ae0d97cb42495998681759a96fe132bda0`.

## Batching implementation and experiment

The provider now embeds multiple independent passages by passing one
`google.genai.types.Content` per input. The SDK serializes this shape as
`batchEmbedContents`; a list of parts in one Content would aggregate inputs into
one vector. Inputs are counted individually with `countTokens`, checked against
the 8,192-token per-input limit, and packed in order under configurable limits.
Defaults are 25 inputs and 32,768 counted tokens per synchronous request. The
aggregate limit is a local safety budget, not a Google-published request cap.
Malformed counts, response cardinality, dimensions, non-finite values, zero
vectors, and reported truncation are rejected. The existing text prefixes,
768-dimensional configuration identity, and query format are unchanged.

The real controlled experiment used distinct pinned 2Wiki passages and completed
every requested size without error:

| Inputs | Vectors returned | Token count calls | Embedding calls | Counted tokens | Elapsed seconds |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1 | 1 | 1 | 73 | 1.472 |
| 2 | 2 | 2 | 1 | 131 | 0.874 |
| 5 | 5 | 5 | 1 | 270 | 5.365 |
| 10 | 10 | 10 | 1 | 948 | 2.549 |
| 25 | 25 | 25 | 1 | 2,080 | 4.626 |

Totals: **43 passage inputs, 43 token-count calls, five embedding calls, and
3,502 counted tokens**. All returned vectors were 768-dimensional, in order.
No HTTP errors, provider quota errors, or retry attempts occurred. The largest
observed successful synchronous batch was 25; this does not establish Google's
maximum. Google documents separate Content inputs and the per-input token limit
in its [embedding guide](https://ai.google.dev/gemini-api/docs/embeddings); its
[rate-limit guide](https://ai.google.dev/gemini-api/docs/rate-limits) describes
RPM, input TPM, and RPD per project. The API response does not expose the
project's remaining quota or establish whether every quota dimension charges
per embedded item versus per HTTP batch.

The user-reported Free tier and disabled billing from prior evaluation records
were relied on; account billing and live remaining quota were not independently
readable through the configured API credential. No billing was enabled and no
paid or asynchronous Batch API was used.

## Persistent database and resumability

The local evaluation service is isolated from application settings and Cloud SQL:

- Database: `ai_investigation_evaluation`
- PostgreSQL: 18.4; pgvector installed
- Host/port: `127.0.0.1:55433`
- Role: `evaluation_engine`
- Volume: `ai-investigation-evaluation_evaluation-postgres-data`
- Migrations: actual repository chain through `0002_embedding_storage`

The ignored `.env.evaluation` file contains a locally generated password. It is
not the application's `.env`; indexing and migration scripts build an explicit
localhost URL and reject the wrong database/version. The application continues to
use its existing Cloud SQL proxy configuration and was not started.

The 10-vector capped run left all versions inactive. After PostgreSQL restart,
the next capped run reported the same 218 registrations, 218 plans, and 10
persisted vectors, then reused those vectors and persisted one more. The final
run resumed from persisted progress and generated the remaining 186 unique
vectors. PostgreSQL directly reported 197 vectors and 218 active/searchable
source occurrences; incomplete planned versions and missing vectors were zero.
Container removal/recreation then preserved all of these counts, and a real
Gemini query returned ten results through both semantic and hybrid retrieval.

Final PostgreSQL measurements after container recreation:

| Table/state | Count |
| --- | ---: |
| Documents/source occurrences | 218 |
| Document versions | 218 |
| Unique Contents | 197 |
| Whole-passage plans | 218 |
| Chunk memberships | 218 |
| Persisted vectors (768 dimensions) | 197 |
| Active versions | 218 |
| Searchable source occurrences | 218 |
| Incomplete planned versions | 0 |
| Database size | 10,196,671 bytes |

Reproduce the local service and migrations:

```powershell
Copy-Item .env.evaluation.example .env.evaluation
# Set a random EVALUATION_DB_PASSWORD in the ignored .env.evaluation file.
docker compose --env-file .env.evaluation -f compose.evaluation.yaml up -d
uv run --no-sync python scripts/migrate_evaluation_database.py
```

Index or resume, then evaluate retrieval:

```powershell
uv run --no-sync python scripts/index_evaluation_corpus.py --batch-size 25 --batch-token-limit 32768
uv run --no-sync python scripts/evaluate_persistent_retrieval.py
```

Stop PostgreSQL while keeping data with `docker compose --env-file .env.evaluation
-f compose.evaluation.yaml stop`. Start it again with the `up -d` command above.
Do not add `down -v`; the named volume is the persistent evaluation corpus.

The actual persisted report is
[`reports/evaluation/task15-2026-10-28/retrieval.json`](../../reports/evaluation/task15-2026-10-28/retrieval.json)
with a compact Markdown summary beside it. A post-container-recreation search
check is in `restart-search.json`. The reproducible live experiment ledger is
[`reports/gemini-embedding-batch-experiment-2026-10-28.json`](../../reports/gemini-embedding-batch-experiment-2026-10-28.json).

## Retrieval evaluation

Both retrieval modes used the same persisted corpus, pinned suite, whole-passage
plans, Gemini document vectors, and 40 real Gemini query vectors. Search latency
below includes application/SQL retrieval with cached query vectors; it excludes
Gemini query-embedding latency. There were 34 supporting-fact-scored cases; the
six unanswerable cases have no gold facts and are excluded from recall/MRR
denominators. All 40 cases completed in both modes.

| Mode | Recall@5 | Recall@10 | Recall@20 | MRR@20 | All facts @20 | Mean retrieval latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Semantic | 0.8750 | 0.9265 | 0.9412 | 0.9853 | 0.8235 | 55.57 ms |
| Hybrid (RRF k=60; 20 candidates/channel) | 0.8750 | 0.9265 | 0.9412 | 0.9853 | 0.8235 | 64.34 ms |

By category at K=20:

| Category | Scored cases | Semantic recall | Semantic all facts | Hybrid recall | Hybrid all facts |
| --- | ---: | ---: | ---: | ---: | ---: |
| Simple factual | 8 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| Two-hop | 12 | 0.9167 | 0.8333 | 0.9167 | 0.8333 |
| Bridge comparison (three-plus-hop) | 8 | 0.8750 | 0.5000 | 0.8750 | 0.5000 |
| Contradictory | 6 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| Unanswerable | 0 | N/A | N/A | N/A | N/A |

For this snapshot, hybrid fusion did not change the aggregate or per-category
relevance metrics and added about 8.8 ms mean retrieval latency. This is a
descriptive result for one 40-question suite, not evidence of general parity.
Bridge comparisons remain the weakest evidence-coverage category.

## Answer-quality evaluation and request accounting

No generation request was made in this task. A representative paired
AnswerQuestion/InvestigateQuestion sample was deferred because the configured
Free-tier project quota could not be read through the API key, while prior
same-day generation use had a reported upper bound near the 20-RPD allowance.
The old sample is documented separately in
[`answer-quality-evaluation.md`](answer-quality-evaluation.md); it is not a
result on this full persisted corpus. No answer correctness, citation quality,
abstention, contradiction, or generation-latency claims are made here.

Confirmed calls in this task were 57 synchronous embedding requests: five
experiment batches, ten corpus-indexing batches across the resumable runs, 40
query embeddings for retrieval evaluation, and two one-query restart checks (the
first completed retrieval but hit a local report-serialization error; the fixed
report was then saved on one rerun). At least 282 countTokens calls were made,
one for each confirmed input; a process interruption lost one run's stdout, so
an extra partial preflight count may have occurred. The interrupted run
persisted 125 vectors in five batches; the resumed run reused them. Reported
token counts total **10,304** across saved ledgers. The 125-input interrupted
run's token sum was not captured, and no extra count calls were repeated to
reconstruct it. No provider error or retry occurred.

The measured indexing segments took 58.9 seconds (10-input, 1-input, and
61-input runs). The unlogged 125-vector process plus planned PostgreSQL restart
made the full indexing wall time about three minutes; this wall time includes
the restart and local orchestration. The final 61-input run took 29.7 seconds.

## Tests and remaining work

- Python unit suite: **467 passed**.
- PostgreSQL integration suite: **161 passed, 4 skipped**; the focused indexing
  integration file passed all 34 cases.
- JavaScript suite: **26 passed**.
- Focused batching/provider/indexing/config/bootstrap unit subset: **107 passed**.
- A direct SQL comparison verified all **218** persisted source occurrences
  against manifest source identity, title, text, content hash, metadata,
  structure and source spans.
- Upstream Google Gen AI SDK emitted its existing Python typing deprecation warning.
- Four external live integration tests remain opt-in and were not run. Real
  Gemini embedding experiments, corpus indexing, query evaluation, and
  PostgreSQL restart/search verification were run explicitly for this task.

The full 125,760-occurrence corpus remains unindexed. The next useful step is
reviewing the bridge-comparison misses and choosing an answer-quality sample
after checking current generation quota in AI Studio; no generation evaluation
should be inferred from the retrieval results.
