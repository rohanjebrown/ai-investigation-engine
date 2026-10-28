# Corpus-scale processing and indexing feasibility (E40)

This checkpoint uses real context passages from the approved complete 2Wiki
development split. Corpus processing and semantic eligibility are separate
measurements. The validation databases are disposable; their records are not
installed in Cloud SQL or the running application.

## Reproducible source manifest

The [manifest](../../data/samples/2wiki-dev-corpus-manifest.json) selects the entire
`xanhho/2WikiMultihopQA` development split at revision
`612bc5039a457880d9e7d84c3b0a4cf154b70e4f`, with pinned Parquet SHA-256
`c0d8b60b9026b728fb07ad74c5252a0f188f6942e8ba5c02df4dfa369502ea8d`.
An independent current audit reproduced:

| Measurement | Result |
|---|---:|
| Dataset question rows read for their context columns | 12,576 |
| Passage/source-coordinate occurrences | 125,760 |
| Unique exact UTF-8 Contents | 56,680 |
| Duplicate occurrences beyond the first | 69,080 |
| Excluded records | 0 |
| Total occurrence text bytes | 44,839,228 |
| Unique Content text bytes | 24,513,423 |
| Original sentence spans | 400,892 |
| Distinct Content/sentence layouts | 56,702 |

Exact-text uniqueness remains the approved conservative counting baseline; it
does not prove semantic independence. Chunks do not count as independent records.
The source stream loads only `_id` and `context` columns and uses the existing
adapter. JSON parsing is the only input conversion: no trimming, whitespace
normalization, semantic merging, or invented source coordinates occurs.
Questions, answers, supporting facts, reasoning triples, and evaluation labels
are not read by this processing path.

`records.jsonl` in the ignored audit directory contains each original locator,
exact-text SHA-256, text lengths, sentence-layout digest, and three chunk counts.
Its ordered SHA-256 is
`af1e97c0c787782c8946eb9a951c2c7f40fff2b001d29a4b2974fd382da17a64`.
The compact committed manifest and pinned input reproduce that selection without
committing the roughly 42 MB per-occurrence ledger. The audit took 262.46 seconds
on this host; manifest counts and fingerprints exclude runtime timings.

## Bulk processing and recovery

The operator utility reuses `extract_2wiki`, `RegisterDocument`, `chunk_document`,
and `PostgreSQLChunkPlanRepository.save`. It does not insert authoritative rows
through benchmark SQL. Parquet reading, in-flight operations, and database
connections are bounded. The validation composition explicitly assigns shared
ownership (`owner_id=None`) for this system-managed corpus.

One source occurrence is registered and its complete plan committed before it
can contribute to a completed batch checkpoint. If a process stops between
registration and plan persistence, replay resolves the existing registration
and saves its missing plan. If it stops after database commit but before progress
is recorded, the unfinished batch is replayed using the existing registration
and plan conflict checks. There is no automatic transaction or provider retry.

Checkpoints bind the input digest, revision, chunking/tokenizer configuration,
explicit ownership, source-range start, and actual PostgreSQL cluster/database
identity plus migration revision. They are trusted operator files, not an
authorization mechanism. Each foreground range has one owner and its own
checkpoint. The coordinator advances the completed prefix only after every
disjoint range completes. There is no durable queue, distributed lease, or
background job system.

Windows repeatedly denied replacement of a progress file, including after a
bounded local retry. Progress therefore uses immutable small batch records in
`<checkpoint>.d/`, with fsync and atomic installation of each new record. Resume
ignores unfinished temporary files and reads the newest complete record; it also
accepts this task's earlier single-file checkpoints. Database/API work is never
repeated merely to retry a local file replacement. A restored/edited database
that no longer matches a previously completed prefix requires integrity review
and an explicit fresh replay; checkpoints do not prove that external mutations
have not occurred. No power-loss or distributed exactly-once guarantee is made.

Reproduce the audit and structural comparison using existing local artifacts:

```powershell
uv run --no-sync python -m investigation_engine.ingestion.corpus_processing
uv run --no-sync python -m investigation_engine.evaluation.chunking_comparison
```

Run the full disposable-database validation with the already cached image:

```powershell
uv run --no-sync python scripts/validate_corpus_scale.py --processes 4 --concurrency 8 --batch-size 128 --output .cache/corpus-scale/validation
```

The runner starts a loopback-only PostgreSQL container, applies the actual
Alembic migrations there, measures milestones, deliberately terminates a worker
after committed registrations, resumes, compares stored data to the original
adapter output, restarts PostgreSQL, and checks stable counts and IDs. It also
performs an actual repository replay over a 1,000-occurrence prefix and a
completed-checkpoint no-op over the full selection. These are distinguished from
a forced full-repository replay. The worker mode can resume against that same
explicit local database while it exists; application `DB_*`, Cloud SQL ports,
and normal application settings are not used. A fresh container requires a fresh
output/checkpoint directory. The 512 MiB host disk guard stops new batches.

## Executed complete-corpus database validation

The [complete captured result](corpus-processing-results-2026-10-28.json) passed
on disposable PostgreSQL **18.4 / pgvector 0.8.5**, using the actual migrations
through `0002_embedding_storage`. All 125,760 stored occurrences were compared
with the pinned source via the production adapter, including exact text,
SHA-256, original titles, metadata, structure and source-span/locator arrays.
There were zero invalid content hashes or incomplete/invalid whole-passage plans.

| Final database measurement | Result |
|---|---:|
| Logical input source occurrences / successful registrations | 125,760 |
| Documents created | 125,760 |
| DocumentVersions created | 125,760 |
| Unique exact Contents persisted | 56,680 |
| Occurrences reusing an existing Content | 69,080 |
| Complete version chunk plans | 125,760 |
| Chunk memberships persisted | 125,760 |
| Embeddings generated / persisted in this complete-corpus run | 0 / 0 |
| Fully indexed / activated versions | 0 / 0 |
| Semantically searchable source occurrences | 0 |
| Database size (`pg_database_size`) | 253,613,759 bytes |
| Full validation elapsed, including integrity/restart/replay | 1,262.41 seconds (21.04 minutes) |

This meets U03's approved **at least 50,000 unique exact-text baseline for
processing**. It does not establish semantic independence or full-corpus
searchability. The source audit read 12,576 question rows for their context
columns; they expanded into the 125,760 selected passage records. Foreground
range workers reread source prefixes to preserve original coordinates, so those
logical input counts are not a claim of only one physical Parquet scan.

The deliberate process exit left 1,008 committed documents but only 1,000 plans,
while the completed checkpoint remained at 1,000. Resume reused committed
registrations and filled the gap. The final counts were exact. Restarting the
actual PostgreSQL container preserved counts and the complete document/version
ID-set fingerprint. A completed checkpoint caused zero registration operations;
an additional fresh-checkpoint replay of the first 1,000 source occurrences
preserved every full-database count and ID fingerprint. These are process and
database-restart checks, not a simulated success or an in-memory substitute.

### Observed processing performance

Milestones are cumulative corpus sizes; elapsed and throughput describe the
increment since the previous milestone. The first two phases used one process
with eight connections; later phases used four processes with eight connections
each. Embedding generation is excluded from these timings.

| Corpus milestone | Increment processed | Elapsed seconds | Increment records/second | Database MB |
|---|---:|---:|---:|---:|
| 100 | 100 | 3.85 | 26.0 | 8.89 |
| 1,000 | 900 | 19.95 | 45.1 | 11.05 |
| 10,000 | 9,000 | 164.87 | 54.6 | 30.32 |
| 125,760 | 115,760 | 987.06 | 117.3 | 253.61 |

For the final increment, average observed per-task registration wait was about
112 ms, chunk generation about 0.94 ms, and plan-persistence wait about 148 ms.
The JSON preserves summed stage elapsed times across concurrent tasks; they
overlap and must not be summed as wall time or interpreted as isolated stage
throughput. Chunk generation stays in the existing pure chunker; registration
and plan transactions retain their existing boundaries.

The largest worker's observed peak working set was 197,058,560 bytes; the sum of
worker peaks was 780,652,544 bytes, an upper bound rather than a simultaneously
measured physical-memory peak. The controller peaked at 152,166,400 bytes.
Database size excludes cluster WAL and Docker/host filesystem overhead.

An earlier single-process 9,000-record increment took 307.29 seconds, versus
164.87 seconds for the four-process increment. Host/container loads differed;
this is descriptive local evidence, not a controlled scaling law. The final
increment ran faster after the slower duplicate benchmark was stopped. Earlier
Windows checkpoint/ledger failures were retained as harness failures and led
to immutable progress records; the final selected run had no failed record or
phase. Throughput varied materially, so linear estimates from 100 records would
have been misleading. The final validation container was removed; the unrelated
pre-existing local database was untouched.

## Chunking comparison

The full-source structural audit used the same pinned tokenizer and existing
implementations. These configurations are experiments, not a new serving default.
The whole-passage reference ceiling of 32,000 is a chunking parameter; it does
not override Gemini's 8,192-token formatted-input limit.

| Configuration | Occurrence memberships | Distinct Content ranges | Median / P95 reference tokens | Original sentences wholly contained in one chunk |
|---|---:|---:|---:|---:|
| Whole passage, 32,000 ceiling | 125,760 | 56,680 | 58 / 317 | 400,892 / 400,892 |
| Fixed 128, overlap 16 | 167,949 | 83,473 | 67 / 128 | 380,458 / 400,892 |
| Supplied-sentence packing 128, overlap 0 | 169,760 | 84,637 | 67 / 124 | 400,558 / 400,892 |

The matched [218-source / 40-question comparison](corpus-chunking-comparison-2026-10-28.json)
produced 218/293/298 memberships and 197/270/275 distinct ranges. Whole/fixed/
sentence packing wholly contained 76/75/76 annotated supporting facts. Fixed
overlap repeated 4,872 characters in this sample. Fragment text and native
sentence locators remain preserved even when strict whole-sentence coverage is
lost. These are structural measurements with all chunks available, not ranking
quality. Unsupported full-suite semantic metrics remain null.

## Bounded Gemini retrieval pilot

The user selected Gemini Embedding 2 and Gemini Flash 3.6 or 3.8. This task kept
the existing 768-dimensional Gemini embedding profile and used no generation
requests. Flash 3.6 remains the selected integration model if generation is
needed; no production setting or `.env` value was changed.

The [captured real-Gemini report](corpus-gemini-pilot-2026-10-28.json) registered
four shared pinned passages, one genuine independent occurrence with identical
Content owned by a test principal, and one inactive real passage. It persisted
six genuine vectors reused by both whole-passage and fixed-128/16 plans. There
were 13 memberships, five active versions in administrator scope, four public
searchable source occurrences, and one inactive version. Query vectors came
from the same model and formatting, then were cached for matched comparisons.
Reported search timings therefore exclude a fresh external query-embedding call.

Both selected questions achieved strict Recall@5=1 and MRR=1 for semantic and
hybrid retrieval under both chunking configurations. Whole returned four public
ranges; fixed returned five. Keyword search was separately exercised with an
actual matching term. Source hashes, exact canonical slices and original
provenance were checked. A database restart preserved ordered retrieval results.
Private duplicate provenance remained separate, shared vectors were reused,
public retrieval excluded that owner, and the inactive source was excluded.
This source selection deliberately contains the required facts plus two
distractors, so it is a persistence/citation correctness pilot, not an unbiased
corpus-scale quality benchmark or evidence that either chunker is superior.

The first pilot attempt stopped on a local ledger-file replacement failure after
four count-token and four embedding calls; its disposable data was removed. The
reduced recovery pilot used different source passages and made eight more of
each call, including two query inputs. **Total Task 14 API usage was 12 token
counts, 12 embedding operations, zero generation requests, zero provider retries.**
The recovery run counted 659 formatted tokens, spent 5.74 seconds inside the
embedding adapter (including counting/ledger overhead), and took 19.78 seconds
including database work, pacing, and restart. This is a small measurement; it
does not establish 100/1,000/10,000-record embedding throughput. The run stayed
within its aggregate 18-input/36-operation ceiling. The earlier project usage
upper bound plus this task is at most 106 known embedding-related operations;
untracked project usage and billing were not independently queried.

## Embedding feasibility and next indexing strategy

The user reported billing-disabled Free limits of 100 RPM, 30,000 TPM and 1,000
RPD for Gemini Embedding 2. Google says active limits vary by project and must be
checked in [AI Studio](https://ai.google.dev/gemini-api/docs/rate-limits).
The standard text endpoint is [free of charge](https://ai.google.dev/gemini-api/docs/pricing#gemini-embedding-2),
while the asynchronous Batch service has no Free-tier price. No billing or paid
Batch operation was enabled. Synchronous `batchEmbedContents` and asynchronous
Batch jobs are distinct [API methods](https://ai.google.dev/api/embeddings).

The current adapter counts and embeds each missing exact range separately.
Whole/fixed/sentence configurations would require 56,680/83,473/84,637 distinct
document embeddings if indexed separately, plus the same number of token-count
requests and later query embeddings. At 1,000 embedding requests/day those are
**at least 57/84/85 inference-quota days**, before reserving quota for questions,
failures and unknown existing usage. Token counting is tracked separately; no
claim is made that it doubles inference quota. The current one-input adapter
does not implement multi-input synchronous batching. Google's [embedding guide](https://ai.google.dev/gemini-api/docs/embeddings)
requires separate `Content` inputs to avoid aggregating multiple texts into one
vector. Independent batching and its project quota accounting need a bounded
wire-level verification before promising a shorter free-tier schedule.

For whole passages, raw float32 vector values alone total 174,120,960 bytes,
excluding pgvector headers, row/index/TOAST overhead, provenance and plans. Exact
input checks still run against Gemini's actual tokenizer; the reference
tokenizer is not proof that every full-corpus input fits the provider limit.
Resume reuses compatible stored range/profile vectors, and query embedding must
retain that same profile. No Gemini/local vector mixing is permitted.

Local candidates were researched but not installed or selected, consistent
with the user's Gemini instruction:

| Candidate | Dimensions / context | License / main float32 weights | Operational trade-off |
|---|---|---|---|
| [all-mpnet-base-v2](https://huggingface.co/sentence-transformers/all-mpnet-base-v2) | 768 / 384 wordpieces by default | Apache-2.0 / about [438 MB](https://huggingface.co/sentence-transformers/all-mpnet-base-v2/tree/main) | Established CPU sentence encoder; oversized passages must be explicitly rejected/rechunked rather than silently truncated. |
| [e5-base-v2](https://huggingface.co/intfloat/e5-base-v2) | 768 / 512 tokens | MIT / about [438 MB](https://huggingface.co/intfloat/e5-base-v2/tree/main) | Retrieval-oriented; requires distinct `query:` and `passage:` prefixes and its own normalization/pooling contract. |
| [nomic-embed-text-v1.5](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5) | 768 / longer context | Apache-2.0 / about [547 MB](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5/tree/main) | Uses search task prefixes; backend/model-code support and exact preprocessing must be pinned and reviewed. |

The host has a 10-core/16-thread i7-12650H and about 16.87 GB physical RAM, but
initial free disk was only about 2 GB. These weight sizes exclude dependencies,
tokenizers, activations and serving overhead. CPU inference is plausible at
small batches, but no local throughput or memory result was measured. A local
runtime would also have to fit the eventual deployment host and be available
for live query encoding; generating offline vectors alone is insufficient.

The existing provider port already accepts document sequences and queries, so
it needs no new generic interface. However, both `EmbeddingConfig` and the
database CHECK currently permit only `gemini_no_title_v1`. A separate local
format identity would require explicit contract/schema approval, exact model
and tokenizer revisions, token limits and normalization, plus the same provider
at query time. No such migration or local-model dependency was introduced.

The recommended path under the selected Gemini policy is to preserve the full
processed corpus, publish the actual searchable count separately, and approve a
bounded synchronous batching/quota experiment before choosing a full embedding
schedule. Without verified batching capacity or enough days, claiming a fully
searchable 50,000-record corpus would be incorrect. Production migration,
hosting, cost and deployment remain separate decisions.

## Failure diagnostics

Future Gemini failures now carry allowlisted operation, model identifier, HTTP
status when observed, category, retryability, rate/quota flag, and cumulative
operation request count for that client instance. SDK messages, credentials,
headers and document bodies are never copied. Evaluation JSON and Markdown
retain these safe fields. A 429 does not distinguish RPM from daily quota
without further safe evidence; counters from separate diagnostics must not be
summed as independent request totals. Offline tests exercise authentication,
permission, availability, rate/quota, transport, malformed-response and safe
redaction behavior. The old Task 13 provider cause remains unknown; it was not
retried for diagnosis.

## Implementation map and test results

| Files | Responsibility |
|---|---|
| `src/investigation_engine/ingestion/corpus_processing.py` | Incremental source-only reader, deterministic manifest, database/configuration-bound progress, bounded existing-service processing. |
| `scripts/validate_corpus_scale.py` | Disposable local migrations, 100/1k/10k/full measurements, disjoint foreground processes, deliberate interruption, full source integrity, restart and replay checks. |
| `src/investigation_engine/evaluation/chunking_comparison.py` | Matched structural comparison through the existing chunkers and fixed evaluation labels. |
| `scripts/validate_gemini_corpus_pilot.py` | Offline preflight by default; explicit `--run-live`, fresh output directory, bounded Gemini inputs, immutable request ledger, real indexing/retrieval/restart verification. |
| `application/ports/provider_diagnostics.py`, Gemini adapters, `evaluation/answer_quality.py` | Safe diagnostic values and JSON/Markdown export, preserving existing exception/retry/public API behavior. |
| Corpus/diagnostic/comparison unit tests and PostgreSQL corpus-processing test | Determinism, exact reuse, failed batch replay, cancellation after registration commit, configuration/target binding, disk guard, Windows file locks, disjoint source ranges, diagnostic redaction and error categories. |

Commands executed after implementation:

```powershell
uv --cache-dir .cache/uv run --no-sync pytest tests -q
node --test tests/frontend/test_web.cjs
```

**620 Python tests passed (460 unit, 160 PostgreSQL integration), 7 opt-in tests
skipped; 26 JavaScript tests passed.** The ordinary run made no external AI or
Cloud SQL requests. One existing Google SDK typing deprecation warning remains.
The separate real-Gemini script passed its actual indexing, source, owner,
configuration, retrieval and restart assertions. The full processing script
passed against real PostgreSQL with no synthetic vector seeding.

No domain models, dependencies, migrations, retrieval ranking, RRF, embedding
storage policy, cloud settings or application defaults were changed by Task 14.
The complete local validation data and Gemini pilot data were removed with their
disposable containers; their captured reports remain. Nothing was staged,
committed or pushed. Full-corpus Gemini indexing, a verified accelerated batching
design, deployment serving profiles and public hosting remain unfinished.
