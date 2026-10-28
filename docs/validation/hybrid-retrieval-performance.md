# Hybrid retrieval: local performance checkpoint, 2026-10-28

This measures the E31 implementation, not real-corpus quality, Cloud SQL capacity
or a production latency guarantee. No external AI requests occurred.

## Method

Disposable local PostgreSQL 18 / pgvector 0.8.5 used the existing pinned Docker
image and unchanged Alembic migrations. No new database settings or indexes were
introduced. Each synthetic record has one shared active DocumentVersion, one
whole-passage membership, unique exact Content and one distinct persisted VECTOR(768)
row. All vectors contain the same seeded dense nonzero values: realistic storage
width, deliberately tied similarity, no claim about semantic quality. Text is
approximately 400 characters, with a rare two-term keyword match in about 1% of
records. This is not the actual 125,760-occurrence 2Wiki corpus, its duplicate
distribution, mixed chunk lengths, or multiple serving profiles.

The ignored local `.cache/benchmark_hybrid.py` seeds 1k, 10k and 50k records,
ANALYZEs the tables, and runs three sequential calls per mode. Keyword and semantic
limits are 20; hybrid uses 20 candidates per channel and RRF final limit 10.
Timings include local async repository/session/result mapping; hybrid also includes
Python RRF. Separate EXPLAIN ANALYZE BUFFERS JSON executions measure server time.
The saved sanitized plans and raw measurements remain in ignored `.cache` artifacts.
Each operation executes one retrieval statement. No embedding/generation latency
is included. Other local tests ran during parts of this benchmark; these small
samples do not establish percentiles, resource isolation or concurrency capacity.

## Results

All times below are milliseconds; repository times are medians of three calls.

| Records / memberships / embedding rows | Keyword | Semantic | Hybrid | EXPLAIN keyword / semantic / hybrid |
| --- | ---: | ---: | ---: | --- |
| 1,000 / 1,000 / 1,000 | 188.8 | 145.9 | 224.1 | 152.9 / 89.1 / 161.2 |
| 10,000 / 10,000 / 10,000 | 1,418.1 | 1,019.8 | 1,972.7 | 1,336.1 / 920.3 / 1,288.8 |
| 50,000 / 50,000 / 50,000 | 5,475.8 | 4,217.0 | 6,546.7 | 5,575.1 / 4,188.9 / 6,717.7 |

At 50k, sample ranges were keyword 5,434.8-5,761.4, semantic 4,114.2-4,366.7,
and hybrid 6,482.8-6,696.6 ms. Database size was approximately 299.7 MB decimal.
Keyword returns 9 hits at 1k and 20 at larger sizes; semantic returns 20 and fused
hybrid 10. A previous run without semantic timing measured 6,845 / 8,321 ms for
keyword/hybrid at 50k, illustrating local variation, not a code optimization.

## Query-plan findings

All plans enumerate 50k serving memberships and inspect complete selected-profile
coverage before limiting. Full scans, hash joins, window/aggregate work and Content
PK lookups remain necessary with the current SQL. Membership CTE materialization
took about 2.93-3.01 seconds in the 50k plans (inclusive node times; do not sum
ancestor and descendant times). Sorting spilled to disk: one external merge sort
used 27,616 kB. Root temporary blocks written were 8,688 for the single channels
and 8,691 for hybrid. Top-N ranking heaps were small (about 44-45 kB).

Keyword matching executed a Content PK scan 50,000 times with a filter containing
`to_tsvector(english, substr(...)) @@ query`, about 0.044 ms per lookup/filter loop.
The rare keyword does not yield a lexical index scan. Derived vectors are rebuilt
for matching, with ranking expression work for qualifying rows. Hybrid shares the
membership/completeness CTEs, then performs separate exact-vector and lexical
branches; Python receives at most 40 rows. No all-vector Python scan occurs.

Thus runtime text-vector construction is costly, but it is not the only bottleneck:
semantic-only eligibility/scoring already takes seconds. A lexical index alone
cannot be claimed to solve end-to-end latency. No planner settings were changed.

## Recommendation requiring separate approval

The unindexed baseline is functionally suitable for controlled automated quality
evaluation, but these results do not support interactive 50k-scale readiness.
Design a reusable exact-range lexical representation with explicit FTS configuration
and a GIN index, plus maintenance/backfill behavior, before relying on full-corpus
interactive search. Keep joining matching ranges to independently eligible version
memberships; preserve provenance and authorization before top-K. Measure eligibility
query improvements independently and repeat realistic-corpus/concurrency benchmarks.

An ordinary index on full Content is not a correctness-preserving prefilter for
all chunk strategies. For example `microelectronics` split into `micro` and later
fragments has a searchable chunk lexeme absent from the full-text lexeme. A
cross-table substring cannot simply become an expression index on memberships.
Persisting lexemes entails a derived-storage/maintenance decision and migration,
which is recommended for review and **not implemented or approved here**. Adding
an ANN index or weakening completeness checks is not silently substituted.

Latency targets, final language/query parameters, actual-corpus and concurrent
measurements remain open. No additional database service is proposed.

## E33 schema-preserving optimization, 2026-10-28

The earlier tables above are historical measurements. E33 reproduces the same
synthetic corpus and compares the frozen pre-change repository with the optimized
repository on the **same database and same rows** at each scale. No schema,
migration, index, database setting, model, visibility or ranking policy changes.
The reproducible runner is `scripts/benchmark_retrieval.py`; raw results/plans
are saved to ignored `.cache/retrieval-optimization/`.

### Changes and preserved behavior

1. Blankness now uses `btrim(chunk_text, PYTHON_WHITESPACE) != ''` instead of
   removing every whitespace character with `translate`. Only the boolean
   presence of a non-whitespace character matters. Boundary trimming is equivalent
   for this predicate and avoids scanning the interior of ordinary nonblank text.
   The exact Python whitespace set is retained, including control and Unicode
   characters; no text or stored span is changed. A real-PostgreSQL regression
   compares both predicates against Python for every whitespace character and
   mixed/non-whitespace examples.
2. Candidate ranking projects narrow identity/range/score rows. Document title,
   source JSON, native spans and final excerpt are fetched after channel LIMIT,
   still inside the same SQL statement/snapshot. This is deferred payload
   reconstruction, not post-limit eligibility filtering. All existing pre-limit
   owner/deleted/current-version/profile/manifest/geometry/vector checks remain.

The public repository/use-case interfaces, exact cosine, English FTS, query
construction, tie ordering, limits, RRF and occurrence identity are unchanged.
Source reconstruction in Python remains bounded to returned rows. No completeness
cache or assumption that every active version fits every serving profile was added.

Verification: **365 unit + 147 PostgreSQL integration = 512 Python tests passed**,
plus **24 JavaScript tests passed**. No assertions were weakened. Existing
dependency/lock files, schema mappings, migrations, indexing and activation code
were unchanged; no live Gemini or Cloud SQL calls occurred.

### Benchmark method

The historical module was frozen before editing at
`.cache/retrieval_before_optimization.py`, SHA-256
`2660ea7e0c55bfa98b4a7a18d3bc9d0fe74d8ab4759a7f81d4c3fac1adfeef2b`.
It is a local comparison artifact, not a second production implementation.

```powershell
uv run --no-sync python scripts/benchmark_retrieval.py --baseline .cache/retrieval_before_optimization.py --samples 20
```

The baseline argument is optional for future optimized-only runs. To recreate
a before/after experiment, save the pre-change module before editing and supply
that trusted file; a fresh clone does not contain the ignored historical module.
`--sizes 1000` supports a focused repeat. The script requires existing cached
PostgreSQL/pgvector and Ryuk images and checks their availability before starting.
It creates only a disposable loopback database, applies existing migrations there,
and checks that the mapped port is neither 5432 nor 15432. No process settings,
external clients or production database connections are used.

Every size has one membership and distinct embedding row per record. As before,
the synthetic 768-vectors are dense and identical, with 400-character passages and
a rare two-term match. Both variants use NullPool, 20 candidates per channel,
final fused limit 10, one unmeasured warm-up and 20 measured calls per channel.
P95 is the nearest-rank 19th ordered sample; it is descriptive, not a reliable
production tail estimate. Variants run in fixed baseline-then-optimized order,
with warm caches; no randomized or concurrent load experiment is claimed.
The first 1k stage overlapped the focused correctness suite; large scales ran
after that suite ended. Runtime outliers are retained, not silently discarded.

Warm-up result signatures compare ordered occurrence identities, document IDs,
hashes, exact text, source references/spans, raw and fused scores and channel
ranks before/after. A difference aborts the benchmark. Timed calls issue exactly
one retrieval statement. CPU is container CPU-seconds across each 20-call batch,
not CPU percentage or per-function profiling. EXPLAIN times, root buffer/temp
counters, database size and all individual timings are saved separately. Node
times include descendants and must not be summed as independent costs.

### Measured results

Local service times in milliseconds; each cell is median / P95 over 20 measured calls. These new paired baselines, rather than the historical three-sample figures, establish the before/after comparison.

| Records | Channel | Before median / P95 | After median / P95 | Server execution before / after |
| --- | --- | ---: | ---: | ---: |
| 1,000 | semantic | 102.4 / 125.1 | 64.6 / 91.6 | 69.8 / 17.6 |
| 1,000 | keyword | 145.6 / 369.7 | 96.7 / 141.0 | 102.2 / 59.2 |
| 1,000 | hybrid | 166.7 / 213.0 | 112.0 / 220.4 | 115.9 / 61.9 |
| 10,000 | semantic | 746.7 / 1134.2 | 239.8 / 256.9 | 706.0 / 198.2 |
| 10,000 | keyword | 1079.1 / 1456.0 | 610.7 / 692.2 | 1058.2 / 550.9 |
| 10,000 | hybrid | 1229.3 / 1306.0 | 672.3 / 824.3 | 1155.6 / 641.4 |
| 50,000 | semantic | 4954.2 / 5111.3 | 1477.3 / 1866.8 | 5139.8 / 1244.4 |
| 50,000 | keyword | 6513.8 / 6869.0 | 3563.1 / 3868.8 | 6738.2 / 3672.8 |
| 50,000 | hybrid | 6424.3 / 6907.6 | 3426.6 / 3787.6 | 6277.6 / 3369.8 |

At 50k, median semantic latency fell about 70%, keyword 45%, and hybrid 47%. The sub-second median aspiration is **not met**. A single hybrid EXPLAIN can be faster than a keyword EXPLAIN due to normal plan/cache/runtime variation; these are separate executions, not additive component timings.

| 50k channel | Container CPU seconds, 20 calls before / after | Shared hits before / after | Shared reads before / after | Temp written blocks before / after |
| --- | ---: | ---: | ---: | ---: |
| semantic | 101.70 / 32.39 | 1,028,455 / 479,844 | 32,841 / 31,664 | 8,691 / 8,691 |
| keyword | 133.66 / 74.70 | 561,288 / 211,508 | 0 / 0 | 8,691 / 8,691 |
| hybrid | 131.19 / 70.35 | 1,579,040 / 679,700 | 32,248 / 32,028 | 8,692 / 8,691 |

All 50k variants reported database size 299,710,143 bytes (about 299.7 MB decimal). No persistent storage was added. Temp I/O remains essentially unchanged. Shared-read counters are PostgreSQL buffer misses, not proof of physical device reads. Per-function cosine CPU and Python source-copy cost are not isolated by EXPLAIN; their inclusion in wider nodes and end-to-end timings must not be presented as independent exact timings.

At 50k, the semantic membership WindowAgg inclusive time fell from 3,643.9 to 587.7 ms. Both versions still enumerate all 50k memberships, perform 50k embedding lookups and spill membership sorts (one 27,616 kB external merge). Deferred document/version lookups fell from 50k each to 20 each. The optimized semantic server execution was 1,244.4 ms; the remainder after the membership stage includes completeness joins, vector lookup/distance, ranking and final hydration, not cosine alone. Keyword server execution remained 3,672.8 ms with runtime exact-slice FTS across candidates. Thus both eligibility and lexical work remain material.

The saved before/after signatures matched at all three scales for semantic, keyword and fused hybrid results. Result counts were semantic 20, keyword 9 at 1k / 20 otherwise, and fused hybrid 10 (up to 40 raw channel candidates). No ranking change was found.

The focused 1k repeat, without concurrent database tests, again preserved every result signature. Its before/after semantic median was 117.7/51.8 ms (P95 153.3/113.2); hybrid median 155.3/105.9 ms (P95 184.1/129.3). Small-scale P95 remains noisy, and the main-run hybrid 1k P95 slightly regressed despite its median improvement. No across-the-board tail-latency claim is made. The repeat recorded PostgreSQL 18.4, pgvector 0.8.5 and unchanged work_mem=4MB; Docker reported no explicit per-container memory/CPU limit, which does not establish unlimited host resources.

### Additional plan evidence

At 10k, the serving-membership WindowAgg inclusive time fell from about 540 to
104 ms for semantic search. Top-K payload lookups into documents/versions fell
from 10,000 per relation to 20 (40 for combined channels), while the initial
visibility scan still examines the full eligible corpus. Semantic ranking's
top-N heap narrowed from about 44 to 30 kB. Membership sorting still spills:
the two observed external-merge sorts used about 5,520 and 5,008 kB; root temp
blocks written remained 1,318. This change reduces computation and payload
joins but does not claim to remove the remaining full-scan/sort bottleneck.

### Proposed follow-up requiring approval

No migration is necessary for the implemented changes. The next lexical design
candidate remains a **new derived exact-range table**, provisionally named
`chunk_lexical_ranges`, not a full-Content index:

| Proposed column / key | Responsibility |
| --- | --- |
| content_sha256 BYTEA NOT NULL, FK contents(sha256) | Reusable immutable canonical Content |
| start INTEGER NOT NULL, end INTEGER NOT NULL | Exact half-open canonical character range; CHECK start >= 0 AND end > start |
| text_search_configuration TEXT NOT NULL | Initial allowlisted pg_catalog.english; configuration changes require rebuild/identity review |
| lexemes TSVECTOR NOT NULL | Derived from the exact substring using that configuration; no copied canonical text |
| PRIMARY KEY(content_sha256,start,end,text_search_configuration) | One lexical artifact per reusable range/configuration |
| GIN(lexemes) | Indexed lexical candidate matching; version memberships remain the authority for occurrence provenance/eligibility |

This is a migration **proposal, not approved DDL**. Full bounds/digest/derivation
checks belong at the persistence boundary; no cross-table CHECK or speculative
trigger is implied. Source owners and spans are not duplicated into this table.
An implementation would need idempotent artifact writes/backfill, validation of
the exact range and trusted configuration, safe rebuilds after dictionary/server
changes, and a missing-artifact strategy that preserves current eligibility.
Silently excluding versions without lexical rows would change semantics and is
not acceptable. Runtime fallback or a fully verified backfill before switching
must be selected explicitly.

Expected benefit is avoiding repeated per-range parsing and using GIN to locate
rare matches; it cannot eliminate the independent completeness/cosine costs.
Storage is one tsvector plus PK/GIN/heap overhead per unique range, not per source
occurrence. A read-only 1k diagnostic measured an average 587.23 bytes per derived tsvector for these synthetic whole passages. At 50k equally sized unique ranges that suggests about 29.4 MB of vector payload alone, before heap, primary-key and GIN overhead. This extrapolation is not actual index-size or real-corpus evidence. GIN build/write costs and total storage must still be measured before capacity claims. New ingestion work is one tokenization/upsert per missing range,
plus GIN maintenance and a reproducible backfill. Alternatives are the current
runtime path (no derived-state burden), further narrow eligibility SQL, or a
materialized readiness design (additional lifecycle/invalidation decisions).
No ANN/HNSW index is proposed: exact-search changes already yield measurable
improvement, and approximate filtering/recall trade-offs have not been evaluated.

## E37 inline embedding storage comparison, 2026-10-28

This is a disposable-database experiment for PostgreSQL column storage policy,
not a production schema decision. The referenced separate performance handoff was
not present in the supplied files or repository. Its reported 4.8x isolated-query
result is treated as context, not as evidence for the complete application.

### Method

The comparison used two independent disposable loopback containers from the same
image digest, each created from the current Alembic head and seeded with the same
deterministic logical workload: 50,000 active public documents, one version, plan,
membership and distinct 768-dimensional embedding per record. Texts were about 402
characters; the dense synthetic vectors were identical. The current optimized
`PostgreSQLEvidenceRepository` served all queries. No Cloud SQL settings were read,
no Gemini calls were made, and only existing migrations were applied inside each
throwaway container.

The column policy was set after migrations and before inserting the corpus, so both
physical layouts were confirmed without relying on a rewrite: PostgreSQL reported
`attstorage = e` for EXTERNAL and `attstorage = p` for PLAIN. Each mode had one
unmeasured warm-up and 20 timed queries for keyword, semantic, and hybrid retrieval.
The fixed order was keyword, semantic, hybrid; EXTERNAL ran before PLAIN. P95 is
nearest-rank `ceil(0.95*N)`. The run was single-client, warm-cache, and had no
explicit Docker CPU or memory limit. These are local synthetic results, not a
production-load or real-corpus prediction.

Across separate containers, normalized ordered signatures were identical for all
three modes. The signature covers source occurrence coordinates, exact Content
hash/text, offsets, SourceReference, source spans, channel and fused scores, and
ranks; generated per-run UUIDs are excluded. Hashes were keyword
`2db0c4d68f527a06d0b7c8bd3c748f9d5c1db8e11ced9d62663d686fe78f4f6e`, semantic
`5fb4bfff08e66b146a09c81858ceddb79ddda4de7926020d8ede548bd57f06bc`, and hybrid
`b479c503832b8cab0023184a38ff12f625991bca1ab1283059026c2c7369485c` in both
copies. This confirms equal results for the tested synthetic corpus and queries;
the separate integration tests remain the evidence for broader eligibility and
authorization cases. The existing optimized-blankness, semantic retrieval, and
hybrid retrieval PostgreSQL integration suites passed: **41 passed**. No test
files were added.

### Results

The environment was PostgreSQL 18.4, pgvector 0.8.5, `work_mem=4MB`, image
`pgvector/pgvector@sha256:9d2e61c7352b9e9f4798df5fd9a498f043f4cda1cdacc707de3d198650f4321e`.
Times are milliseconds, median / P95 over 20 calls. PostgreSQL execution time is
from one captured `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` after the timed run.

| Retrieval | EXTERNAL median / P95 | PLAIN median / P95 | Median change | Server execution EXTERNAL / PLAIN |
| --- | ---: | ---: | ---: | ---: |
| Keyword | 2,778.2 / 2,935.5 | 2,806.8 / 3,142.9 | +1.0% | 2,785.5 / 2,822.3 |
| Semantic | 1,127.0 / 1,225.9 | 905.5 / 980.5 | -19.6% | 1,156.0 / 967.5 |
| Hybrid + RRF | 3,296.0 / 3,404.4 | 3,056.2 / 3,173.2 | -7.3% | 3,334.8 / 3,052.4 |

PLAIN improved measured exact semantic retrieval by about 221 ms median and
reduced hybrid median by about 240 ms. Keyword retrieval did not improve. Relative
to the historical optimized benchmark, the EXTERNAL numbers here are lower for
keyword/hybrid and similar for semantic; the paired figures above, not comparisons
across environments or runs, are the relevant evidence.

### Physical storage and plans

| 50k database measurement | EXTERNAL | PLAIN |
| --- | ---: | ---: |
| Database size | 300,299,967 bytes | 297,547,455 bytes |
| `chunk_embeddings` main heap | 5,464,064 bytes | 204,800,000 bytes |
| Embedding TOAST relation | 204,800,000 bytes | 3,833,856 bytes |
| Main-table indexes | 4,341,760 bytes | 4,341,760 bytes |
| `chunk_embeddings` total including TOAST/indexes | 216,981,504 bytes | 214,228,992 bytes |

PLAIN moved about 199.3 MB into the main heap and reduced the embedding TOAST
relation by about 201.0 MB. Total database size was 2.75 MB (0.9%) smaller in this
run, so the larger heap did not produce a net storage increase here. This synthetic
result does not predict a hosted quota or a differently sized corpus.

Across the root plan counters, PLAIN semantic search used 180,943 shared hits and
30,593 shared reads versus 479,274 hits and 32,403 reads with EXTERNAL. Hybrid
used 380,656 hits / 31,097 reads versus 679,732 / 32,111. Keyword counters were
similar (211,536 hits / zero reads versus 211,677 / zero). PostgreSQL buffer reads
do not establish physical device I/O. Temporary writes were effectively unchanged:
about 8,689 blocks for each mode and query. Both plans still process all 50,000
memberships and perform 50,000 embedding lookups for semantic ranking; the
membership sort still spills to disk. PLAIN reduces buffer work around exact
vector retrieval, but does not remove the dominant eligibility/completeness and
sorting work or runtime full-text construction. It is not a fix for hybrid's
remaining multi-second latency.

### Write and operational trade-offs

This experiment did **not** isolate `PostgreSQLEmbeddingRepository.save`, compatible
embedding lookup, incremental indexing, commit/WAL cost, or concurrent writes. The
corpus seed used direct bulk SQL as in the existing benchmark convention. The
single observed bulk-seed wall times were 40.69 s (EXTERNAL) and 41.53 s (PLAIN),
about 2.1% longer for PLAIN. This includes Content, documents, versions, plans,
memberships, embedding rows, current-version updates, commit, and `ANALYZE`; it is
not a repeated or isolated embedding-write measurement and cannot establish the
indexing write cost. A write-path measurement remains required before claiming no
indexing regression.

PostgreSQL documents that `ALTER TABLE ... ALTER COLUMN ... SET STORAGE PLAIN`
changes the policy for future tuple writes and does not rewrite existing rows.
Consequently, adding only that DDL to Alembic would leave existing embeddings in
their old layout. Rewriting the table is a separate operational action. PostgreSQL
documents `VACUUM FULL`/table rewrites as requiring `ACCESS EXCLUSIVE` and temporary
space for another copy of the table and indexes; the production downtime, free
space headroom, and rollback rewrite cost were not measured here. Reverting the
column policy also does not by itself move already-inline values back to TOAST.
See the official [ALTER TABLE storage-policy documentation](https://www.postgresql.org/docs/current/sql-altertable.html)
and [routine vacuuming documentation](https://www.postgresql.org/docs/17/routine-vacuuming.html).

If later approved, the minimum policy DDL would be
`ALTER TABLE chunk_embeddings ALTER COLUMN embedding SET STORAGE PLAIN;` in an
Alembic revision. It would not perform the required existing-row rewrite, which
must be explicitly planned and authorized as a separate operational step; a
downgrade changing the policy to EXTERNAL likewise would not relocate rows by
itself. No migration or model change is included in this experiment.

### Recommendation

The results make PLAIN a strong candidate: exact semantic and hybrid retrieval
improved without a result-signature change, keyword was essentially unchanged,
and total measured database size was slightly lower. They do not yet establish
that production adoption has acceptable write and maintenance costs. Recommend
one more isolated rehearsal against a production-sized copy to measure the actual
embedding save/indexing path and table-rewrite duration/peak disk headroom, then
review a minimal storage-policy migration plus a separately scheduled rewrite.
This is a recommendation for further validation, not an approval or migration.
No decision status is changed.

Reproduce each independent copy with the existing pinned image and current
migrations:

```powershell
uv --cache-dir .cache/uv run --no-sync python scripts/benchmark_retrieval.py --sizes 50000 --samples 20 --embedding-storage external --output .cache/embedding-storage/external
uv --cache-dir .cache/uv run --no-sync python scripts/benchmark_retrieval.py --sizes 50000 --samples 20 --embedding-storage plain --output .cache/embedding-storage/plain
```

The runner writes measurements and JSON plans under ignored `.cache/`. It never
reads application database settings. The benchmark used the current optimized
repository hash `4e0c5620fef377dd2a1494e9ffc98b4df61f42f74c84f8d973bec430bc089969`.
