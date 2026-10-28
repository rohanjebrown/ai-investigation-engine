# Automated retrieval evaluation

Implemented under E32 on 2026-10-28. Evaluation tooling lives in
`investigation_engine.evaluation`, outside production request handling. It reuses
SearchEvidence; it does not implement another search algorithm, generation,
database schema, indexing optimization or service bootstrap.

## Fixed suite and source-only corpus

Committed files are `data/samples/evaluation/suite.json` (questions/gold) and
`corpus.json` (searchable source documents). Suite retrieval-v1 has exactly:

| Category | Cases | Basis |
| --- | ---: | --- |
| Simple factual | 8 | Derived single-relation questions from compositional 2Wiki annotations; answer object occurs in exactly one annotated supporting sentence. |
| Two-hop | 12 | Original compositional questions with two annotated relations. |
| Three-plus-hop | 8 | Original bridge-comparison questions: two entity-to-person-to-attribute chains and a comparison. These are branching reasoning cases, not linear four-edge paths. |
| Unanswerable | 6 | Fictional station dates deliberately absent from the controlled corpus. |
| Contradictory | 6 | Two same-time fictional vault-code claims disagree; neither source has priority. |

No category is inferred solely from supporting sentence count. The simple questions
are explicitly derived, not claimed to be original benchmark questions. Category
basis and original annotation triples are preserved for review. The original dev
questions themselves are multihop; the simple projections test factual retrieval.
This initial small selection is not representative or a held-out production benchmark.

Verification: **365 unit + 146 disposable PostgreSQL integration = 511 Python
tests passed**, plus **24 JavaScript tests passed**. The final combined run passed
507 tests; four additional paired-comparison/manifest tests then passed within the
complete 365-test unit rerun. One existing upstream SDK typing deprecation warning
remains. Snapshot rebuilding was checked for exact byte-for-byte reproducibility.

The revision is `612bc5039a457880d9e7d84c3b0a4cf154b70e4f`, dataset
`xanhho/2WikiMultihopQA`, split dev. Selection takes the first eligible records in
existing pinned Parquet order. The 20 selected original records contribute all
200 context occurrences, including distractors, plus 18 controlled documents:
**218 source records and 197 unique exact UTF-8 Contents**. All 40 questions in
both retrieval modes search this same collection, not per-question gold-only pools.
This evaluation subset does not replace U03's complete development ingestion corpus.

Each case identifies corpus, category, question ID/text, expected answer or null,
expected behavior, revision/split and exact required supporting sentences. The
manifest enumerates source references, exact text/SHA-256, original sentence spans,
record IDs, counts, selection policy and input file digest. Titles remain separate.
Only these document texts go through indexing; questions, expected answers,
supporting labels and annotation triples never become searchable text. A source
may naturally contain an answer; that is evidence, not injected ground truth.

Rebuild using the already downloaded file, without network access:

```powershell
uv run --no-sync python -m investigation_engine.evaluation.build_suite
```

The builder uses the existing 2Wiki adapter, rejects ambiguous title-to-context
mapping and validates the inspected Parquet digest before assigning its pinned
revision. It neither downloads nor indexes data. Committed snapshots let ordinary
metric and PostgreSQL tests run without the raw dataset or tokenizer artifact.

## Relevance and metrics

A gold item is one supporting sentence at exact source coordinates:
source kind, dataset/source ID, revision, split, example ID, context position and
zero-based sentence index, plus canonical half-open bounds and Content hash.
Titles are used only once to resolve benchmark labels within their original record;
ambiguous matches fail. Runtime metric matching never uses title or text similarity.

A hit receives credit only when it has the same occurrence and Content hash,
fully contains the sentence's canonical range, and retains the matching original
sentence locator and bounds. An intersecting partial sentence is insufficient.
One chunk can cover multiple gold items. Several partial chunks whose union covers
a sentence do not receive full-sentence credit in v1; this conservative rule may
penalize smaller chunking and is deliberately recorded for future comparisons.
Another occurrence with identical text receives no credit for this occurrence's label.
This is strict annotation-occurrence recall, not an exhaustive alternate-evidence judge.

Duplicate retrieved `(version ID,start,end)` identities are removed, preserving
the first list position and compacting unique ranks. Each gold sentence counts
once even if multiple overlapping hits cover it. Production rank fields are not
used in place of returned order. Duplicate gold labels are rejected.

- **Recall@5/10/20:** unique required sentences covered by unique top-K hits divided
  by required sentences. Each question has equal weight in macro aggregates.
- **Evidence fraction@K:** the same sentence-level fraction, explicitly named for
  multihop coverage. **All-required@K:** whether every required sentence is covered;
  its mean is the fraction of cases with complete evidence.
- **MRR:** reciprocal unique-result position of the first fully relevant hit,
  truncated at the largest requested K (20 by default). No hit scores zero.
- **Latency:** monotonic elapsed SearchEvidence.execute time, including query
  embedding lookup, repository transaction/mapping and RRF where applicable.
  Metric/export overhead is excluded; whole-run elapsed time is recorded separately.
  This does not separate SQL time from provider latency or establish an SLA.

No-gold questions have null recall/MRR/coverage and remain in case results with
their retrieved hits and latency. They never earn success for empty retrieval.
Answerability is a later generation decision. Missing gold annotations or source
records are unavailable cases, not zeros or successes. Retrieval exceptions are
failed cases with safe error class only. Aggregates expose denominators/status
counts and category metrics. Paired deltas use only mutually successful scored
cases, preventing unequal failures from masquerading as a quality improvement.
Delta direction describes MRR: positive/negative, or numerically negligible within
1e-9; this is not a statistical significance threshold. Per-K deltas are also exported.

## Runner and offline use

`evaluate(suite, corpus, [(RetrievalRun, SearchEvidence), ...], ks=(5,10,20),
indexed_corpus_fingerprint=..., owner_id=None)` accepts existing configured services.
It does not import process settings, open a database, construct Gemini clients or
perform indexing. A trusted caller supplies matching repository/provider/configuration
objects and verifies the indexed snapshot. The required fingerprint attestation guards
accidental manifest substitution; it is not an automatic database audit or authorization
mechanism. The PostgreSQL test actually checks stored source coordinates/hashes,
record counts and active pointers before attesting. Avoid using production credentials.

For existing real embeddings, `CachedQueryEmbeddings(embedding_configuration,
{exact_question: vector})` can supply SearchEvidence with previously obtained query
vectors. It validates finite nonzero 768-dimensional storage vectors, copies them,
and rejects misses/document embedding calls without external fallback. No real query
cache for this complete suite exists yet. Supplying fake vectors must always be
reported as synthetic. Do not label them as a model's outputs.

Run descriptors include full chunking/tokenizer and embedding configuration, mode,
candidate limits and RRF k. Construct descriptors from the same values passed to
SearchEvidence (as in the integration test), not guessed settings. Paired comparisons
require identical chunking/embedding profiles; future chunking experiments can run
separately with the same source corpus and compare their exported reports. This
does not approve multi-step execution or change retrieval behavior.

`export(report, directory)` writes JSON and Markdown. JSON includes suite/corpus
fingerprints, source manifest, index/retrieval configurations, UTC timestamp, K,
scope, per-question hits with real version IDs/native provenance, metrics, latency,
failures, aggregates, category metrics and paired deltas. Markdown is a compact
summary linking the interpretation to JSON. Answer correctness, citation accuracy,
abstention and contradiction detection remain null: no generation evaluation ran.
The case format's expected answer/behavior supports later evaluation without an
LLM judge or passing gold labels into production generation.

## Executed controlled comparison

```powershell
uv run --no-sync pytest tests/unit/evaluation -q
uv run --no-sync pytest tests/integration/infrastructure/persistence/test_retrieval_evaluation.py -q -s
```

The integration test registers, chunks, persists synthetic vectors, activates and
queries all 218 occurrences in disposable PostgreSQL. The same tied 768-vector
is used for every document and query. Both modes run all 40 questions (80 query
lookups); no live AI, Cloud SQL or production settings are used. Reports are saved
under ignored `.cache/evaluation-controlled/` and a test temporary directory.

First completed run (34 scored cases; six no-gold cases retained, zero failures):

| Mode | Recall@5 | Recall@10 | Recall@20 | MRR@20 | All-required@20 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Synthetic semantic | 0.0147 | 0.0588 | 0.1176 | 0.0129 | 0.1176 |
| Synthetic hybrid | 0.1912 | 0.2353 | 0.2941 | 0.1012 | 0.2941 |

Paired MRR delta was +0.0882; mean service latency was 70.5 / 84.8 ms.
These values exercise actual SQL and actual metric calculations, not mocked
metric outputs. They show lexical contribution against deliberately tied fake
semantic rankings. **They do not establish real-model quality or hybrid superiority.**
Tie IDs are generated during registration; stable relative insertion order determines
semantic ties in this fixture. Runtime timestamps, IDs and latency vary across runs.

## Requirements for the first real-quality experiment

Obtain separate authorization/budget for missing real document and exact-question
vectors, or an approved existing compatible local cache. Verify a complete indexed
snapshot, selected profile, source revisions/active versions and query-encoding
compatibility. Record the corpus and query-cache digests. Freeze cases/parameters
before comparison and report overlap with development/tuning data. Keep gold labels
outside index and model requests. The original dev benchmark was used to construct
this suite; no train/test independence is asserted. Strict occurrence labels and
derived simple questions need review before broad conclusions. Evaluate answer,
citations, abstention and contradiction separately later. Full-corpus ingestion,
new API requests, performance optimization and multi-step investigation are outside
this milestone; existing full-scale search latency limitations still apply.

## Answer-quality extension (E39)

The preceding statement that generated-answer metrics remain unavailable described
the E32 retrieval-only checkpoint. E39 adds deterministic answer correctness,
strict and partial cited-sentence coverage, answer/abstention/conflict outcomes,
investigation behavior and usage, and paired `AnswerQuestion` versus
`InvestigateQuestion` reporting. See the separate [answer-quality methodology,
offline verification and partial live sample](answer-quality-evaluation.md).
The fixed 40-case retrieval suite remains the same; answer labels are versioned
evaluation-only annotations. A two-case live attempt completed one successful
single-step 2Wiki answer before a provider error caused the paired multi-step
cases to be stopped. It is not a full answer-quality benchmark.
