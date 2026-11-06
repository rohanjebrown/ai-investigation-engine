# Lexical retrieval investigation — 6 November 2026

Read-only investigation of `investigation_stage`; no retrieval/schema/configuration changes. Five warm application/SQL round-trip measurements per variant after one warm-up; one EXPLAIN ANALYZE BUFFERS per query variant. Settings unchanged, nested loops enabled normally. Exact compiled SQL and full JSON plans are in `.cache/lexical-review/`. Core synchronous SQL measurements use the actual repository SQL builders and evidence converter; the SQLAlchemy Core result key `metadata` is mapped to the ORM evidence key `source_metadata` by the benchmark only. HNSW settings statements are combined into one round trip in this SQL microbenchmark; the separate actual-async-repository check uses the implementation unchanged. No generation calls; two requested vectors reused from local traces, three missing queries embedded once (three count_tokens plus three embed_content calls), then cached for all repetitions. Shared-only scope (`owner_id=None`), chunk configuration 1, compatible Gemini embedding profile, HNSW ef=400/candidates=400/iterative off, top20, hybrid channel limits20+20, RRF k60. The zero-th measurement is excluded. Measurements use separate read-only transactions, not one exported MVCC snapshot; no data was changed by this investigation.

## Findings and recommendation

**The configured lexical implementation already is genuine BM25. Do not implement a second BM25 engine.** The source default is a separate PostgreSQL FTS fallback; local `.env` explicitly selects BM25. Installed pg_textsearch version is 1.3.1, pgvector 0.8.5, PostgreSQL 18.6. The canonical BM25 index main relation is 14,557,184 bytes (13.88 MiB); this is not total heap/WAL/cache storage. There is no GIN index covering the fallback's computed tsvector expression.

BM25 combines corpus-aware inverse document frequency, term-frequency saturation and document-length normalization. The extension's `<@>` operator returns the **negative** relevance score, sorted ascending (more negative is better). This is not ts_rank renamed as BM25. The index explicitly specifies English text configuration; no custom k1/b override appears in its definition. The built-in fallback uses default ts_rank weighting/normalization, not corpus IDF BM25; plainto_tsquery requires all surviving lexemes. For example, Wisigard becomes `'wisigard' & 'father-in-law' & 'father' & 'law'`; Muthu becomes `'m' & 'k' & 'muthu' & 'father' & 'die'`. Full questions often contain relational terms absent from the supporting passage. All five fallback queries return zero rows. Complete measured query expressions are in `.cache/lexical-review/raw-index.json`.

Measured first-query plan: complete BM25 execution 6,801.499 ms, HNSW 54.289 ms, hybrid 6,874.692 ms. The BM25 query does NOT use the ordered BM25 index scan: it reconstructs full-corpus eligible memberships, performs primary-key lookups into searchable_chunks, scores 57,899 ranges and sorts to top20. A subsequent join to the materialized representative set rejects 1,157,960 comparisons (20 × 57,899 minus matches). Corpus inputs include 125,760 documents/plans, and the compatible-key aggregation scans 57,901 embedding rows. Expensive inclusive nodes: representative CTE scan ~1,454 ms and score/lookup nested loop ~6,621 ms (the latter includes prior work, so these times must NOT be summed). Root buffers: 1,505,045 shared hits, zero shared reads, 25,697 temporary blocks read and 9,345 written. This is a warm-cache CPU/relational/scoring/temp-I/O query, not cold-disk vector search.

The HNSW query uses `ix_chunk_embeddings_embedding_hnsw`, has 28,523 shared hits and zero temporary blocks in the first-question plan. Hybrid retains that HNSW index but also incurs the full lexical path: 1,533,568 shared hits and the same temporary I/O as BM25. Eligibility reconstruction and compatible embedding aggregation remain necessary for integrity, but their scope/physical execution deserves investigation. No validation was removed here.

A separate **unfiltered** control query using the existing BM25 index has five warm EXPLAIN times 0.273, 0.256, 0.250, 0.259, 0.275 ms (median 0.259 ms) and uses `Index Scan searchable_chunks_bm25_idx`, returning20. This is only evidence that the installed ordered index works; it omits authorization/completeness and is NOT a correct replacement or fair end-to-end performance comparison. Full control plan: `.cache/lexical-review/raw-index-plan.json`.

### Actual async repository confirmation

Unchanged PostgreSQLEvidenceRepository, Wisigard question, warm pool, five warm samples after one warm-up, read-only connections, same vector/profile/scope/settings:

|Mode|Total p50 ms|Connection acquisition p50|Repository SQL stage p50|Buffered fetch p50|Evidence construction p50|
|---|---:|---:|---:|---:|---:|
|lexical|6851.797|0.271|6739.348|0.066|2.337|
|hnsw|540.368|0.241|202.352|0.064|1.932|
|hybrid|7191.676|0.228|6847.712|0.096|4.019|

The repository SQL stage is inclusive of buffered fetch and Python/driver work, not server-only EXPLAIN time. RRF in the SQL microbenchmark costs roughly 1 ms. Query construction costs 12–25 ms for the BM25 SQL builder (one measurement per question); warm pool acquisition is sub-ms. The independent Core harness measures Python transaction-object setup around 0.007–0.009 ms, not server-side BEGIN; server transaction/network work can occur in driver execute. HNSW and hybrid execute two separate transaction-local settings statements in the actual repository and then the retrieval statement, followed by transaction completion. Lexical executes one retrieval statement then transaction completion. These setup/teardown round trips explain part of the SQL-stage versus whole-operation gap. They are dwarfed by the ~6.8 s lexical SQL. Full phase samples are preserved; no inclusive times or separately measured medians are added to claim an exact additive decomposition. Client/server BEGIN, transfer and buffered decoding cannot be completely separated by these existing timers without lower-level instrumentation. No new database operations were inserted into repository code.

### Quality and hybrid behavior

Gold coverage totals across ten annotated supporting sentences: BM25 6/10@5 and 7/10@20; HNSW 8/10@5 and 9/10@20; hybrid 7/10@5 and 9/10@20; FTS fallback 0/10 at both. This five-question hand-selected set is not sufficient to certify a global default. No semantic exact-vs-ANN recall claim is made.

Alexandra: exact Caroline supporting passage is absent from BM25 top20, HNSW rank5, hybrid rank11. Muthu: exact M. Karunanidhi support absent in BM25 top20, rank2 in both HNSW/hybrid. Paschen/Bessey: both facts are top2 in BM25/HNSW/hybrid. FitzGerald: exact Thomas passage is BM25 rank11, HNSW rank14, hybrid rank12; title-related candidates nearer the top do not substitute for that exact passage. Wisigard: the exact annotated Wisigard passage is rank1 for all three; the separately annotated Theudebert passage is absent, even though Wisigard itself contains enough facts to answer. Each question's full rankings, scores, canonical IDs and missing gold facts follow below.

Hybrid uses one SQL statement with UNION ALL of independent semantic and lexical candidates (20 each), followed by hydration and Python RRF. This is neither two sequential application retrieval requests nor asyncio-concurrent channel calls. The plans use ordinary Append, not parallel application execution. RRF contributes 1/(60+channel_rank) once per source-version/range identity per channel, removes within-channel duplicates, and sorts by fused score descending then deterministic document/version/range keys. Semantic and keyword magnitudes are not combined or compared. Canonical content/range representatives are already selected in SQL; RRF identity is source-version/start/end. Distinct contents can have identical titles. The same-identity payload must agree across channels. Quick Answer further balances canonical content/range identities, admits at most five whole evidence items within its context budget; Investigation similarly retrieves at most five per tool search and keeps its stable evidence registry. Therefore hybrid's Caroline rank11 is a practical top-five selection loss, even though hybrid top20 coverage looks good. Initial independent-question decomposition in Quick Answer can trigger additional searches; Investigation's later queries are model-directed. No generation or answer-quality evaluation was performed here.

### Alternatives and operational assessment

1. **Keep existing pg_textsearch BM25; investigate its query plan next.** Benchmark an ordered lexical candidate-driven plan that still filters authorization/current versions/complete compatible plans before final topK and preserves deterministic provenance. Also inspect the top20-to-memberships join. Merely changing an algorithm name will not remove the observed full-corpus work. Do not constrain lexical retrieval to HNSW candidates: that would remove its independent recall benefit. Candidate exhaustion/filter-selectivity and exact filtered topK must be tested before implementation.
2. **Retain the FTS fallback as an explicit fallback, not as BM25.** A stored/matching expression index could accelerate its search predicate but would not repair all-term-AND misses or add corpus-aware BM25 ranking. No such index is proposed without a separately measured query/quality design.
3. **Relational custom BM25:** possible with maintained document lengths, per-term frequencies, corpus document frequency and average length. Calculating these aggregates during each request is expensive; maintaining extra tables/triggers and consistent updates/rollback/deletion is more complexity than reusing the existing extension. It must preserve canonical identity and owner filtering; no accuracy advantage is established here.
4. **Other extensions (e.g. ParadeDB pg_search) or external engines:** pg_search is absent from this database's pg_available_extensions result. Do not assume an arbitrary C extension can be installed on Cloud SQL. A separate service introduces duplicate indexing/update/delete synchronization, tenant filtering, provenance hydration, operational cost and consistency failure modes. No measured requirement justifies it now.

Cloud SQL officially documents native pg_textsearch for PostgreSQL17+, enabled via its cloudsql.enable_pg_textsearch flag then extension installation. This verifies platform support; no Cloud SQL flag or extension was changed. Check the actual target's installed version/support policy before any rollout. See [Google Cloud SQL pg_textsearch](https://docs.cloud.google.com/sql/docs/postgres/pg-textsearch). The extension's documented negative-score ordering and standalone scoring using index corpus statistics explain why the current query can use genuine BM25 while failing to use an ordered index scan: [pg_textsearch upstream](https://github.com/timescale/pg_textsearch). PostgreSQL explains its separate FTS ranking and query parsing: [PostgreSQL text-search controls](https://www.postgresql.org/docs/17/textsearch-controls.html).

No replacement implementation is justified. An isolated read-only lexical SQL experiment and a larger blinded quality set are justified. Expected benefit is avoiding tens of thousands of standalone scores/lookups and repeated joins; its actual safe latency gain remains unmeasured. Scope-aware BM25 filtering must never bypass completeness/authorization to obtain a faster plan. Global canonical index statistics include contents outside an individual eligible scope; scores can change with corpus composition even though returned evidence stays authorized. Title-aware indexing would require careful treatment of multiple source occurrences with differing titles and is not a drop-in canonical text change.

Only this report and ignored `.cache/lexical-review` artifacts/benchmark scripts were written in this task. No production/application code, retrieval behavior, schema, persisted database settings, documents, or indexes changed; nothing was committed or deployed. All DB work was read-only. Three real query embeddings were generated (six token-count/embedding requests); **zero Gemini generation calls**. New vectors are cached for reuse. Existing local tracing supplied the first two vectors; tracing instrumentation supplied actual-repository timing events. No additional full-payload tracing was enabled in the database or application.


## Implementation

Local configuration selects `bm25`, not the source default `postgres_fts`. Both Quick Answer and Investigation share the same repository and SearchEvidence wiring. This report does not change either selection. BM25 is already implemented using pg_textsearch; replacing a supposed ts_rank-only system with BM25 is therefore not the current task.

`_bm25_statement` ranks exact persisted `searchable_chunks.text` using `text <@> to_bm25query(:question, :index)`, ascending negative BM25 scores. It joins the authorized complete canonical membership set before LIMIT, then resolves deterministic occurrence/provenance. Titles are hydrated after ranking and are NOT searched. Exact canonical chunk ranges, not document IDs, are lexical search units. BM25 statistics are over the canonical search index, not recomputed per authorized owner scope.

The retained FTS fallback constructs `to_tsvector(english, substr(contents.text, start+1, end-start)) @@ plainto_tsquery(english, :question)`, scores with `ts_rank(vector,query)`, and sorts descending rank. It uses neither ts_rank_cd nor BM25. English stemming/stop words apply; plainto_tsquery joins surviving terms with AND. No application synonym expansion, named-entity normalization, title weighting or question decomposition is done inside either SQL lexical path. The BM25 adapter passes original query text to the extension with english index tokenization; it is not the AND-filtered FTS route.

Eligibility uses active current versions, not-deleted documents, shared/owner scope, equivalent chunk configuration, a unique matching serving plan, exact blankness validation, complete chunk geometry/count/order/coverage and embeddings for every required nonblank range under a compatible embedding profile. Minimum equivalent embedding configuration is selected consistently. Ordinary lexical calls use full reference completeness reconstruction; they do not get HNSW candidate-content restriction. No global topK is taken before authorization/completeness.

## Database and indexes

{'db': 'investigation_stage', 'version': 'PostgreSQL 18.6 on x86_64-pc-linux-gnu, compiled by Debian clang version 12.0.1, 64-bit'}

Installed/available extensions: `[{"name": "vector", "default_version": "0.8.5", "installed_version": "0.8.5"}, {"name": "pg_textsearch", "default_version": "1.3.1", "installed_version": "1.3.1"}]`

| Table | Index | Bytes | Definition |
|---|---|---:|---|
|chunk_embeddings|pk_chunk_embeddings|3948544|`CREATE UNIQUE INDEX pk_chunk_embeddings ON public.chunk_embeddings USING btree (embedding_configuration_id, content_sha256, start, "end")`|
|chunk_memberships|pk_chunk_memberships|5218304|`CREATE UNIQUE INDEX pk_chunk_memberships ON public.chunk_memberships USING btree (document_version_id, chunking_configuration_id, ordinal)`|
|contents|pk_contents|3366912|`CREATE UNIQUE INDEX pk_contents ON public.contents USING btree (sha256)`|
|document_versions|pk_document_versions|3989504|`CREATE UNIQUE INDEX pk_document_versions ON public.document_versions USING btree (id)`|
|searchable_chunks|pk_searchable_chunks|3948544|`CREATE UNIQUE INDEX pk_searchable_chunks ON public.searchable_chunks USING btree (content_sha256, start, "end")`|
|chunk_memberships|uq_chunk_memberships_range|6365184|`CREATE UNIQUE INDEX uq_chunk_memberships_range ON public.chunk_memberships USING btree (document_version_id, chunking_configuration_id, start, "end")`|
|document_versions|uq_document_versions_number|5128192|`CREATE UNIQUE INDEX uq_document_versions_number ON public.document_versions USING btree (document_id, version_number)`|
|document_versions|uq_document_versions_owned_id|6258688|`CREATE UNIQUE INDEX uq_document_versions_owned_id ON public.document_versions USING btree (document_id, id)`|
|document_versions|uq_document_versions_source_occurrence|28827648|`CREATE UNIQUE INDEX uq_document_versions_source_occurrence ON public.document_versions USING btree (kind, source_id, revision, record_locator) NULLS NOT DISTINCT`|
|document_versions|ix_document_versions_content_sha256|3973120|`CREATE INDEX ix_document_versions_content_sha256 ON public.document_versions USING btree (content_sha256)`|
|searchable_chunks|searchable_chunks_bm25_idx|14557184|`CREATE INDEX searchable_chunks_bm25_idx ON public.searchable_chunks USING bm25 (text) WITH (text_config=english)`|
|chunk_embeddings|ix_chunk_embeddings_embedding_hnsw|237166592|`CREATE INDEX ix_chunk_embeddings_embedding_hnsw ON public.chunk_embeddings USING hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64')`|

## Measured performance

Values are milliseconds. SQL EXPLAIN is a separate execution, not a median or a component of the application median. Do not add inclusive plan-node times or medians. Transaction Python measures begin-object setup only: server BEGIN/transfer can occur inside driver execution. Fetch measures buffered `.mappings().all()`; transfer/decoding may already be in SQL execute. Total includes settings and transaction teardown; HNSW excludes embedding-provider latency.

|Question|Variant|Preparation (one build)|Acquisition p50|Python begin p50|Settings p50|SQL round trip p50|Fetch p50|Evidence p50|RRF p50|Total p50|EXPLAIN SQL|
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
|1|bm25|25.224|0.052|0.008|0.002|6764.444|1.540|3.824|0.000|6860.946|6801.499|
|1|postgres_fts|15.264|0.056|0.009|0.002|2990.937|0.035|0.005|0.000|3067.891|2991.747|
|1|hnsw|18.232|0.054|0.008|148.774|178.029|1.376|3.416|0.000|416.779|54.288|
|1|hybrid|30.360|0.059|0.008|155.236|6850.630|1.783|5.939|0.861|7144.370|6874.687|
|2|bm25|17.159|0.043|0.007|0.002|6968.036|0.647|1.985|0.000|7050.823|7115.721|
|2|postgres_fts|14.589|0.044|0.007|0.001|2956.339|0.045|0.004|0.000|3035.617|2991.034|
|2|hnsw|23.377|0.049|0.008|151.894|145.501|0.713|2.493|0.000|374.867|56.862|
|2|hybrid|24.553|0.045|0.007|149.444|6986.367|1.448|5.106|1.398|7264.574|7123.069|
|3|bm25|11.983|0.046|0.007|0.002|6710.232|0.760|2.259|0.000|6785.309|6763.281|
|3|postgres_fts|10.400|0.058|0.009|0.002|2949.448|0.033|0.004|0.000|3035.521|3075.101|
|3|hnsw|24.757|0.049|0.007|179.779|252.515|0.787|2.315|0.000|848.454|54.856|
|3|hybrid|34.518|0.052|0.007|154.311|6714.686|1.528|5.418|0.911|7044.477|6862.150|
|4|bm25|17.756|0.046|0.007|0.002|6825.865|0.932|2.391|0.000|6912.727|6871.394|
|4|postgres_fts|17.421|0.059|0.009|0.002|2958.624|0.032|0.003|0.000|3033.966|3004.293|
|4|hnsw|23.944|0.046|0.007|153.083|177.246|0.804|2.311|0.000|414.871|69.736|
|4|hybrid|19.505|0.072|0.007|150.013|6814.599|1.610|5.107|0.943|7051.401|6945.095|
|5|bm25|20.092|0.047|0.007|0.002|7010.467|0.740|2.241|0.000|7142.439|7113.369|
|5|postgres_fts|23.680|0.049|0.008|0.002|2967.416|0.025|0.003|0.000|3068.113|2976.871|
|5|hnsw|14.246|0.050|0.008|142.758|152.521|0.785|2.576|0.000|367.513|49.108|
|5|hybrid|24.174|0.051|0.008|159.543|7001.652|1.514|6.138|1.207|7273.174|7140.170|

## Exact gold supporting-fact coverage

Coverage requires canonical reference-content SHA256 AND a returned range covering the annotated sentence; title-only matches do not count. Gold annotations are not an exhaustive list of all passages that could answer the question. In particular Wisigard E1 independently names the spouse and spouse’s father even when the annotated Theudebert passage is absent.

|Question|Variant|Gold facts @5|Gold facts @20|
|---|---|---:|---:|
|1|bm25|1/2|1/2|
|1|postgres_fts|0/2|0/2|
|1|hnsw|1/2|1/2|
|1|hybrid|1/2|1/2|
|2|bm25|1/2|1/2|
|2|postgres_fts|0/2|0/2|
|2|hnsw|2/2|2/2|
|2|hybrid|1/2|2/2|
|3|bm25|1/2|1/2|
|3|postgres_fts|0/2|0/2|
|3|hnsw|2/2|2/2|
|3|hybrid|2/2|2/2|
|4|bm25|2/2|2/2|
|4|postgres_fts|0/2|0/2|
|4|hnsw|2/2|2/2|
|4|hybrid|2/2|2/2|
|5|bm25|1/2|2/2|
|5|postgres_fts|0/2|0/2|
|5|hnsw|1/2|2/2|
|5|hybrid|1/2|2/2|

## Question 1: Who is Wisigard's father-in-law?

Reference answer: Theuderic I


### bm25

Gold details: `[{"title": "Wisigard", "sentence": 3, "text": "After an abnormally long term of engagement of seven years, Wisigard married Theudebert I, Merovingian king of Austrasia.", "ranks": [1], "title_only_ranks": [1]}, {"title": "Theudebert I", "sentence": 1, "text": "He was the son of Theuderic I and the father of Theudebald.", "ranks": [], "title_only_ranks": []}]`

Ranks 1–5 are the top-five list; all rows below form top20. Scores across methods are not comparable. Duplicate titles can represent different exact content/ranges.

|Rank|Title|Score|Document ID|Version ID|Canonical identity (SHA/start/end)|
|---:|---|---:|---|---|---|
|1|Wisigard|-18.8897202|01a184af-375a-7378-8b32-4286ca6fae4f|01a184af-375a-7378-8b32-4287da1f728f|d514357cdef7509b4732c007caf1d5342127c315022b29bd3d91d651408b7de1/0/730|
|2|James Armour (Master mason)|-18.7275882|01a184a3-27c2-70f7-978e-3a90fba03d19|01a184a3-27c2-70f7-978e-3a9146a9ce11|8af46e2e67a55b9fe5dc5ef67fd8b8dcc45f6383f762e1b90fd9d07657aa38bb/0/154|
|3|Simpson Harris Morgan|-17.4124372|01a184b2-bd69-75cc-9df4-508c419058a4|01a184b2-bd69-75cc-9df4-508dc323256d|d95ccedd10ca219f7c990866cccd76818a3f0622cd0083d0886575ca6a32c49a/0/200|
|4|William Roper|-16.6526144|01a18595-3ecd-716d-961d-e8a698565819|01a18595-3ecd-716d-961d-e8a7ee589e08|474af2e473bfa6ef2f168abf67d35913473d39438625d026097826ded83a33a0/0/229|
|5|Gnaeus Domitius Corbulo|-16.1878741|01a18575-244e-7337-add3-f39ec30eeca4|01a18575-244e-7337-add3-f39f2d7b3660|ad455b76e5dbb0ff5d78ac1c706aff05da011b6732dd7d1f8e38d3220668a4f0/0/348|
|6|Marie Thérèse of France|-16.1550109|01a18526-520d-73ac-a9fa-c11a19eab5ca|01a18526-520d-73ac-a9fa-c11bb103152a|bc5895a77e678ed990edcf329c6764117a59783a52c271031d5f0634d92f9a70/0/788|
|7|Martha of Armagnac|-15.6295772|01a1859d-9691-70f4-ad6a-343e05aba91e|01a1859d-9691-70f4-ad6a-343fee180554|5ed11b8722882ed743b742ac71431daf6004adfd3cab7485b866327b1c77da5e/0/298|
|8|Matthew Spender|-15.4540017|01a1857d-e27a-778f-8bfe-2e34df3d79e8|01a1857d-e27a-778f-8bfe-2e3504bfd10d|f01055fba88e7b19a10871ddd3cc4dd13b2355e9cd21ac14ab8140a4eca8aa13/0/374|
|9|Lara Trump|-14.4462552|01a18506-4421-7424-9e65-b43360bff340|01a18506-4421-7424-9e65-b4345a8f4c7d|54724f106b1c8477c80b7f638d4190c0dc4d41f5386a003838f4ab0e3b3303ff/0/384|
|10|Ælfgar, Earl of Mercia|-13.9962211|01a184ac-6306-705a-ba63-27e9ad947103|01a184ac-6306-705a-ba63-27ea453fdaf3|2597bdbbddb655f484d8e96fe576e874a75117f33cab477b1b79b08fe64ad230/0/486|
|11|Deuteria|-13.9083166|01a184e4-0beb-72cc-ad4b-b686b4e902d4|01a184e4-0beb-72cc-ad4b-b687807bdb05|c9261a519faa2b66a4b23d496300c44a9c27d03b9c3e0c9114cd4d49dca5e2ea/0/1253|
|12|Julia Agricola|-13.5706542|01a18524-9ce6-7285-968c-5b964535a4ec|01a18524-9ce7-71cb-b18a-9591a9ddbc96|c4deb1584b9880c9c96ce4ed438a03619812a553c5c770fb9fede284327b0a8c/0/545|
|13|Shuja-ud-Din Muhammad Khan|-13.1972899|01a1856e-d6ae-7669-97d1-a5425f9c9d14|01a1856e-d6ae-7669-97d1-a543d7c670ef|da147535701c74fe20f9563380e674b6b430f7f8a94d7c8fa8acaebc06c70a4f/0/356|
|14|Grimoald the Younger|-13.0684719|01a184bd-da35-7218-88c7-a4f7a4a1757d|01a184bd-da35-7218-88c7-a4f84e58b651|0d1f7a463e698a26b573c3eaee47c4db711ca78177e890ba5904e41fcba93638/0/770|
|15|Chey Chettha III|-12.5681686|01a18576-b982-7347-a0eb-4ffe2bea5d81|01a18576-b982-7347-a0eb-4fff57293807|b8a434df63b9eb0591532a7947e2f86cb6312cca04827c4035adf436a6f511ea/0/660|
|16|James Stuart, 3rd Earl of Moray|-11.784754|01a1857d-d8f6-771c-a2cc-781cf5087b3c|01a1857d-d8f6-771c-a2cc-781d1a5d2d1b|d577e90c683eceeda475c19786072722abd9a2497d5507865d979719e664eab9/0/663|
|17|John Ramon III, Count of Cardona|-11.7723796|01a184b1-10be-733a-a6d8-aecc63c3b022|01a184b1-10be-733a-a6d8-aecde7236270|f0a3aceed4d0ded0920e4f341cde599863dc55c39d05fef6be30cb9bd8e8b622/1685/2284|
|18|Aénor de Châtellerault|-11.7723796|01a1858b-2728-752a-b146-c9db5748077a|01a1858b-2728-752a-b146-c9dccbcca7ec|8bf7fb422ec52ef1057702b28ef992973ba664cc549144ebdb64b75067a09426/0/630|
|19|John, Duke of Touraine|-11.5202515|01a18505-716f-700a-8a4c-9d1de0a589ae|01a18505-716f-700a-8a4c-9d1edf899e2f|0cf462c1d1799dd64deecccbac3a9fdbaf353d179b569714920746d821b391db/0/1161|
|20|Confidentially Connie|-11.4200866|01a185a0-532e-779a-b077-31ed524e7107|01a185a0-532e-779a-b077-31ee227d34ae|d21b154d8496aba6a7d7238ee6eedd603a497cbb951e525c194282256c34b1d8/0/673|

Plan buffers: `{"Shared Hit Blocks": 1505045, "Shared Read Blocks": 0, "Temp Read Blocks": 25697, "Temp Written Blocks": 9345}`

Full plan: `.cache/lexical-review/0-bm25-plan.json`.

### postgres_fts

Gold details: `[{"title": "Wisigard", "sentence": 3, "text": "After an abnormally long term of engagement of seven years, Wisigard married Theudebert I, Merovingian king of Austrasia.", "ranks": [], "title_only_ranks": []}, {"title": "Theudebert I", "sentence": 1, "text": "He was the son of Theuderic I and the father of Theudebald.", "ranks": [], "title_only_ranks": []}]`

Ranks 1–5 are the top-five list; all rows below form top20. Scores across methods are not comparable. Duplicate titles can represent different exact content/ranges.

|Rank|Title|Score|Document ID|Version ID|Canonical identity (SHA/start/end)|
|---:|---|---:|---|---|---|

Plan buffers: `{"Shared Hit Blocks": 642629, "Shared Read Blocks": 0, "Temp Read Blocks": 10697, "Temp Written Blocks": 9344}`

Full plan: `.cache/lexical-review/0-postgres_fts-plan.json`.

### hnsw

Gold details: `[{"title": "Wisigard", "sentence": 3, "text": "After an abnormally long term of engagement of seven years, Wisigard married Theudebert I, Merovingian king of Austrasia.", "ranks": [1], "title_only_ranks": [1]}, {"title": "Theudebert I", "sentence": 1, "text": "He was the son of Theuderic I and the father of Theudebald.", "ranks": [], "title_only_ranks": []}]`

Ranks 1–5 are the top-five list; all rows below form top20. Scores across methods are not comparable. Duplicate titles can represent different exact content/ranges.

|Rank|Title|Score|Document ID|Version ID|Canonical identity (SHA/start/end)|
|---:|---|---:|---|---|---|
|1|Wisigard|0.807536866|01a184af-375a-7378-8b32-4286ca6fae4f|01a184af-375a-7378-8b32-4287da1f728f|d514357cdef7509b4732c007caf1d5342127c315022b29bd3d91d651408b7de1/0/730|
|2|Luitgard (Frankish queen)|0.736586379|01a1856d-73b7-76f4-b1e4-9d47c3ee773a|01a1856d-73b7-76f4-b1e4-9d48b9902d7d|6c69d0c3e1369afae7090f3e19af80817f2cfd853aa7db70ad24fa61934a329d/0/480|
|3|Gundeberga|0.726073335|01a1851e-a399-707a-8397-1ee294fb154a|01a1851e-a399-707a-8397-1ee3cf079b05|e4fdf908f7dd576e968e2448a37115a57cf7c228d3293301d11b071058131833/0/394|
|4|Gisela of France|0.722035034|01a1850a-8e26-70a7-ab83-254de50e7730|01a1850a-8e26-70a7-ab83-254efaca25eb|3e8abc2b0ad148e6b95dc890f6b1c3a265247f9f87447297ef5d4e394b72170f/0/730|
|5|Sichilde|0.721461196|01a18582-300e-7144-95f9-25324d1e344e|01a18582-300e-7144-95f9-25333df326d4|b019a41c56c18c9348ce9e4884f89c60d5d752c6232b0abe8f0cdba33a8a11ed/0/473|
|6|Thiadsvind|0.720658047|01a1858f-0a4a-77d5-99ac-c079f86f8e11|01a1858f-0a4a-77d5-99ac-c07a52c3c705|01b0ef609412724e6beb6e7359f2f80c8cba63eff99ae0fc92fb331c851160e2/0/313|
|7|Amalaric|0.719211776|01a184d5-03b3-7448-8be3-38bea89fe02c|01a184d5-03b3-7448-8be3-38bf777671cf|d4bc8f009d588b8ceda0e5fbebde21b96859fa50f5608e8b217005706745d084/0/2005|
|8|Sigramnus, Count of Hesbaye|0.718718879|01a184a3-2b5a-73ed-b5b7-e86f1bb946d8|01a184a3-2b5a-73ed-b5b7-e870d1b4e781|25a4fe35ac94ed9e92d6ab3149c404218b9da9b24ac3b3d7604b90b23dac2002/0/877|
|9|Ealhswith|0.718614528|01a18501-4b89-7295-b574-064efe9c5fec|01a18501-4b89-7295-b574-064f276286e9|3c8461765bffd6cd7dbd2458d9653c7c297a73785aaff6801e4cc6f4d38a74a6/0/441|
|10|Audofleda|0.717812611|01a184a4-005e-76b6-830e-4b98d12be229|01a184a4-005e-76b6-830e-4b993ebf28b6|e3a349c971c096390e05b34534197a630acb961e6cac06570fda777bf575d57c/0/675|
|11|Burchard II, Duke of Swabia|0.717321005|01a1857d-f2be-731a-99b7-d6e6d51cf81f|01a1857d-f2be-731a-99b7-d6e7fa82b590|af46f17c11faaaa0a9bff445bb96ee712b61e64a30a3f2d375b02149c046a17b/0/1954|
|12|Gisela, Margravine of Meissen|0.715821527|01a1856f-c49a-7689-bcd9-bd9db42a3528|01a1856f-c49a-7689-bcd9-bd9ebab9a846|ca1903295ce8914d4873a9a5f9fe49c94cc723d2ec3b18328b2e12daa34bfc19/0/1190|
|13|Ogawa Mataji|0.713561662|01a184a3-278f-7466-b975-6ec36fe761a2|01a184a3-278f-7466-b975-6ec4800dae26|f3d3e07e0ebe237249a1fe7d6a434ba5c741a3e43051be3b383b3830570a8dd5/0/122|
|14|Godfrey, Count Palatine of Lotharingia|0.712168327|01a185aa-8239-7342-9105-50f9e88bc51a|01a185aa-8239-7342-9105-50fa37ea752b|d54f5e209d734ea5deff19c2baadbebd58081c0bd1fdc3914ae8b20217eac4cc/0/1805|
|15|Ermengarde of Tours|0.711608026|01a184ec-aa8b-76ad-bc62-85b2dc16d51f|01a184ec-aa8b-76ad-bc62-85b327011d1b|6f1a4d1af8b24ed03be249151034662efd4223a4735cf4b67be46b0226d9e8cb/0/366|
|16|Gundobad|0.711495747|01a185ac-6d2e-7791-812a-d8db5fbca43d|01a185ac-6d2e-7791-812a-d8dc22dec34b|db0502442dd5f5d7c2783d729b323b26468baee2910849bd229bdb8e00ce1c1a/0/447|
|17|Desiderata of the Lombards|0.711473277|01a1851e-a2e2-7299-903c-613c38f1a0ed|01a1851e-a2e2-7299-903c-613d20bd13ae|662d0d0e8e18fa6623e8a49b652d424ac56a0c0d48a3de39a5e54a696ad75aa8/0/416|
|18|Hedwig of Nordgau|0.709339107|01a185af-9e06-7708-9084-824d72f5050d|01a185af-9e06-7708-9084-824e4e93e849|237579bf05fe1a9649fd74b918ad84da975ae39e54587cf3d1de965aef1bea41/0/473|
|19|Tassilo II of Bavaria|0.709206231|01a184dc-f7bf-725a-b5ee-24fc502339ad|01a184dc-f7bf-725a-b5ee-24fd46b80a09|c209961820361c7118b89521df4186e10601c10b7959bb4706737d0e9041c401/0/1537|
|20|Gisela of Swabia|0.708783796|01a184b9-979f-7171-ace0-6e1d21d76162|01a184b9-979f-7171-ace0-6e1e96907172|a5afd1ff0418df686f2bcb6690fc8eec4cc269a5989467c30146d9e2522f93df/0/364|

Plan buffers: `{"Shared Hit Blocks": 28523, "Shared Read Blocks": 0, "Temp Read Blocks": 0, "Temp Written Blocks": 0}`

Full plan: `.cache/lexical-review/0-hnsw-plan.json`.

### hybrid

Gold details: `[{"title": "Wisigard", "sentence": 3, "text": "After an abnormally long term of engagement of seven years, Wisigard married Theudebert I, Merovingian king of Austrasia.", "ranks": [1], "title_only_ranks": [1]}, {"title": "Theudebert I", "sentence": 1, "text": "He was the son of Theuderic I and the father of Theudebald.", "ranks": [], "title_only_ranks": []}]`

Ranks 1–5 are the top-five list; all rows below form top20. Scores across methods are not comparable. Duplicate titles can represent different exact content/ranges.

|Rank|Title|Score|Document ID|Version ID|Canonical identity (SHA/start/end)|
|---:|---|---:|---|---|---|
|1|Wisigard|0.0327868852|01a184af-375a-7378-8b32-4286ca6fae4f|01a184af-375a-7378-8b32-4287da1f728f|d514357cdef7509b4732c007caf1d5342127c315022b29bd3d91d651408b7de1/0/730|
|2|James Armour (Master mason)|0.0161290323|01a184a3-27c2-70f7-978e-3a90fba03d19|01a184a3-27c2-70f7-978e-3a9146a9ce11|8af46e2e67a55b9fe5dc5ef67fd8b8dcc45f6383f762e1b90fd9d07657aa38bb/0/154|
|3|Luitgard (Frankish queen)|0.0161290323|01a1856d-73b7-76f4-b1e4-9d47c3ee773a|01a1856d-73b7-76f4-b1e4-9d48b9902d7d|6c69d0c3e1369afae7090f3e19af80817f2cfd853aa7db70ad24fa61934a329d/0/480|
|4|Simpson Harris Morgan|0.0158730159|01a184b2-bd69-75cc-9df4-508c419058a4|01a184b2-bd69-75cc-9df4-508dc323256d|d95ccedd10ca219f7c990866cccd76818a3f0622cd0083d0886575ca6a32c49a/0/200|
|5|Gundeberga|0.0158730159|01a1851e-a399-707a-8397-1ee294fb154a|01a1851e-a399-707a-8397-1ee3cf079b05|e4fdf908f7dd576e968e2448a37115a57cf7c228d3293301d11b071058131833/0/394|
|6|Gisela of France|0.015625|01a1850a-8e26-70a7-ab83-254de50e7730|01a1850a-8e26-70a7-ab83-254efaca25eb|3e8abc2b0ad148e6b95dc890f6b1c3a265247f9f87447297ef5d4e394b72170f/0/730|
|7|William Roper|0.015625|01a18595-3ecd-716d-961d-e8a698565819|01a18595-3ecd-716d-961d-e8a7ee589e08|474af2e473bfa6ef2f168abf67d35913473d39438625d026097826ded83a33a0/0/229|
|8|Gnaeus Domitius Corbulo|0.0153846154|01a18575-244e-7337-add3-f39ec30eeca4|01a18575-244e-7337-add3-f39f2d7b3660|ad455b76e5dbb0ff5d78ac1c706aff05da011b6732dd7d1f8e38d3220668a4f0/0/348|
|9|Sichilde|0.0153846154|01a18582-300e-7144-95f9-25324d1e344e|01a18582-300e-7144-95f9-25333df326d4|b019a41c56c18c9348ce9e4884f89c60d5d752c6232b0abe8f0cdba33a8a11ed/0/473|
|10|Marie Thérèse of France|0.0151515152|01a18526-520d-73ac-a9fa-c11a19eab5ca|01a18526-520d-73ac-a9fa-c11bb103152a|bc5895a77e678ed990edcf329c6764117a59783a52c271031d5f0634d92f9a70/0/788|
|11|Thiadsvind|0.0151515152|01a1858f-0a4a-77d5-99ac-c079f86f8e11|01a1858f-0a4a-77d5-99ac-c07a52c3c705|01b0ef609412724e6beb6e7359f2f80c8cba63eff99ae0fc92fb331c851160e2/0/313|
|12|Amalaric|0.0149253731|01a184d5-03b3-7448-8be3-38bea89fe02c|01a184d5-03b3-7448-8be3-38bf777671cf|d4bc8f009d588b8ceda0e5fbebde21b96859fa50f5608e8b217005706745d084/0/2005|
|13|Martha of Armagnac|0.0149253731|01a1859d-9691-70f4-ad6a-343e05aba91e|01a1859d-9691-70f4-ad6a-343fee180554|5ed11b8722882ed743b742ac71431daf6004adfd3cab7485b866327b1c77da5e/0/298|
|14|Sigramnus, Count of Hesbaye|0.0147058824|01a184a3-2b5a-73ed-b5b7-e86f1bb946d8|01a184a3-2b5a-73ed-b5b7-e870d1b4e781|25a4fe35ac94ed9e92d6ab3149c404218b9da9b24ac3b3d7604b90b23dac2002/0/877|
|15|Matthew Spender|0.0147058824|01a1857d-e27a-778f-8bfe-2e34df3d79e8|01a1857d-e27a-778f-8bfe-2e3504bfd10d|f01055fba88e7b19a10871ddd3cc4dd13b2355e9cd21ac14ab8140a4eca8aa13/0/374|
|16|Ealhswith|0.0144927536|01a18501-4b89-7295-b574-064efe9c5fec|01a18501-4b89-7295-b574-064f276286e9|3c8461765bffd6cd7dbd2458d9653c7c297a73785aaff6801e4cc6f4d38a74a6/0/441|
|17|Lara Trump|0.0144927536|01a18506-4421-7424-9e65-b43360bff340|01a18506-4421-7424-9e65-b4345a8f4c7d|54724f106b1c8477c80b7f638d4190c0dc4d41f5386a003838f4ab0e3b3303ff/0/384|
|18|Audofleda|0.0142857143|01a184a4-005e-76b6-830e-4b98d12be229|01a184a4-005e-76b6-830e-4b993ebf28b6|e3a349c971c096390e05b34534197a630acb961e6cac06570fda777bf575d57c/0/675|
|19|Ælfgar, Earl of Mercia|0.0142857143|01a184ac-6306-705a-ba63-27e9ad947103|01a184ac-6306-705a-ba63-27ea453fdaf3|2597bdbbddb655f484d8e96fe576e874a75117f33cab477b1b79b08fe64ad230/0/486|
|20|Deuteria|0.014084507|01a184e4-0beb-72cc-ad4b-b686b4e902d4|01a184e4-0beb-72cc-ad4b-b687807bdb05|c9261a519faa2b66a4b23d496300c44a9c27d03b9c3e0c9114cd4d49dca5e2ea/0/1253|

Plan buffers: `{"Shared Hit Blocks": 1533568, "Shared Read Blocks": 0, "Temp Read Blocks": 25697, "Temp Written Blocks": 9345}`

Full plan: `.cache/lexical-review/0-hybrid-plan.json`.

## Question 2: Who is the maternal grandfather of Princess Alexandra of Hanover (born 1999)?

Reference answer: Rainier III, Prince of Monaco


### bm25

Gold details: `[{"title": "Princess Alexandra of Hanover (born 1999)", "sentence": 1, "text": "(Alexandra Charlotte Ulrike Maryam Virginia; born 20 July 1999) is the only child of Princess Caroline of Monaco and Prince Ernst August of the defunct Kingdom of Hanover.", "ranks": [2], "title_only_ranks": [1, 2]}, {"title": "Caroline, Princess of Hanover", "sentence": 0, "text": "Caroline, Princess of Hanover (Caroline Louise Marguerite Grimaldi; born 23 January 1957), is the eldest child of Rainier III, Prince of Monaco, and the American actress Grace Kelly.", "ranks": [], "title_only_ranks": []}]`

Ranks 1–5 are the top-five list; all rows below form top20. Scores across methods are not comparable. Duplicate titles can represent different exact content/ranges.

|Rank|Title|Score|Document ID|Version ID|Canonical identity (SHA/start/end)|
|---:|---|---:|---|---|---|
|1|Princess Alexandra of Hanover (born 1999)|-32.7588781|01a184b7-0597-71b3-b019-eb4c0c51aac8|01a184b7-0597-71b3-b019-eb4d026fff0b|6cf5c82cea6f296baeec365a371769f180196a9fb3fca9aeeaf1762463d2c957/0/201|
|2|Princess Alexandra of Hanover (born 1999)|-32.7588781|01a185a7-e861-720e-a518-60d89b262c39|01a185a7-e861-720e-a518-60d94bb493ed|762c82d5b51f497d251774d0eea6ef25a79be2052136516e59968871d2170e6f/0/201|
|3|Alexandra, Princess of Leiningen|-26.5580099|01a184fe-dc04-76ff-bd25-bc9fff25c748|01a184fe-dc04-76ff-bd25-bca024b7d414|e63eb6f8c43b7932cb257ac1d8eec9230a6055767c99fb57bed73f3dab07df6f/0/524|
|4|Princess Alexandra of Hanover (1882–1963)|-19.7418575|01a185a7-e860-7294-b286-0f02e61a4312|01a185a7-e860-7294-b286-0f03a1c605e5|bed5da256aa6ab0462de4b330f972f2e7659ff840ad4b1b4dca475b215271ba5/0/304|
|5|Princess Irene of Greece and Denmark|-19.6907794|01a184b7-1516-77bb-98b9-1c1f45ce2120|01a184b7-1516-77bb-98b9-1c208d1c553c|049fd9370675cfcd1364d00a3a1b60514afe1b85cd46af9bb62de24560f4e76b/0/304|
|6|Thyra von Westernhagen|-18.5845369|01a184fe-dc66-71f1-bae1-af6cf966f0a3|01a184fe-dc66-71f1-bae1-af6d565f0a83|bcd28f8836322f4e4dfee3c57a2a786c7fe06060782a94fcff5039e582c19578/0/207|
|7|Lord Herbert Montagu Douglas Scott|-18.406733|01a184c8-e1a1-715f-a7b2-52a6d5244bef|01a184c8-e1a1-715f-a7b2-52a7409f7c9c|9483a65fd97e411ce4c98274a7c20a01058bb98403ea644f767ee147ce60b3cc/0/677|
|8|Ekaterina Malysheva|-18.3472168|01a184fe-dbc0-737c-b81c-4a937bf61dd4|01a184fe-dbc0-737c-b81c-4a9422ff94fc|8bbb2596847f810cc2d39db43e5ae667f385312a3727b71f46417171f8b12511/0/244|
|9|Lord Herbert Montagu Douglas Scott|-18.0701911|01a1856f-ba1b-76ce-8ba6-2e88734c0087|01a1856f-ba1b-76ce-8ba6-2e89db4b8a6a|04da4c989dc1b4105f047c0dffa5440f98518060447fa08c132760d34b4e4010/0/673|
|10|Princess Thyra of Denmark|-16.9925778|01a185b1-63ee-71bf-b8ea-e3f36fbb476c|01a185b1-63ee-71bf-b8ea-e3f44cb2c016|5b52f22201b20a2032d48e19c95b13a7cd34deafb786ab73146848e0049fcb3b/0/588|
|11|Chantal Hochuli|-16.7766498|01a184fe-db01-7181-8711-fe9cf065fbb4|01a184fe-db01-7181-8711-fe9d0a9a0161|f3209f11f44d408f38dac7421fa331653ba1152d650f00efdf3dc7aaddd1d32a/0/238|
|12|Chantal Hochuli|-16.7766498|01a1850d-e1b8-7303-854b-f822abeda50a|01a1850d-e1b8-7303-854b-f823887b4d46|58ff86d5409a4456b47256b12271be662052dd9548f962904b9872ae75157a08/0/238|
|13|Princess Friederike of Hanover|-16.691568|01a1858c-b1cb-75a8-8487-6ba760de098d|01a1858c-b1cb-75a8-8487-6ba8940e6167|78552c667ed6fc497e564e843a8e8321c09e0f39196c9bedad226da037de153a/0/186|
|14|Princess Thyra of Denmark|-16.5263438|01a1858c-b1d5-7300-beb0-34fe068723b5|01a1858c-b1d5-7300-beb0-34ff8b12f80a|21c4a7ab6c02837c3043710f3a4c1908c0254a0f242f682aad2ae2120f252bfb/0/588|
|15|Princess Alexandra of Sayn-Wittgenstein-Berleburg|-16.4700032|01a1850f-91fb-7033-a509-f29cf30ebc82|01a1850f-91fb-7033-a509-f29db2e4fa2f|8f9f6a7d14d966ed2272c36cb23cd0711b8f5b02c071b783807bdb27aecfaf6d/0/601|
|16|William Montagu Douglas Scott, 6th Duke of Buccleuch|-16.2547581|01a184fc-3613-7664-9eb7-1eeb96ab2907|01a184fc-3613-7664-9eb7-1eec248398d3|8d8ded4ef92e12dafc3d93e1030394b4f8a1ac7303f41b26936cd7029baddcfe/0/429|
|17|William Montagu Douglas Scott, 6th Duke of Buccleuch|-16.026345|01a18579-a67f-7031-9c39-74876db80af8|01a18579-a67f-7031-9c39-74887c4efe96|472caa0294db93d8ea493ef3d4eea4b0407fd66529ae9485bf880bf6c0ca4d2b/0/426|
|18|Princess Marie of Hanover|-15.8950295|01a184a1-4591-7416-9cf3-a652e64e669b|01a184a1-4591-7416-9cf3-a653f3226065|f498d4fa7a31aa45ecede48692e2731c14bd255a2ce538c99daf870c019bccd6/0/161|
|19|Prince Ernst August of Hanover (born 1954)|-15.7727194|01a184fe-da8c-73ec-9b6b-70135c86d3e9|01a184fe-da8c-73ec-9b6b-7014f26bf269|9f2074c9b71cd7055ac84fb6174e42f318ae91d6e760824ed0b1433ce9f80437/0/361|
|20|Princess Marie Alexandra of Baden|-15.7477031|01a1856e-1331-76d7-82f8-f12eb3aee7e8|01a1856e-1331-76d7-82f8-f12f0647baf1|131d17828a3739493cebb30384688e15737235471fd18501221c9f1d930a11f1/0/157|

Plan buffers: `{"Shared Hit Blocks": 1505165, "Shared Read Blocks": 0, "Temp Read Blocks": 25697, "Temp Written Blocks": 9345}`

Full plan: `.cache/lexical-review/1-bm25-plan.json`.

### postgres_fts

Gold details: `[{"title": "Princess Alexandra of Hanover (born 1999)", "sentence": 1, "text": "(Alexandra Charlotte Ulrike Maryam Virginia; born 20 July 1999) is the only child of Princess Caroline of Monaco and Prince Ernst August of the defunct Kingdom of Hanover.", "ranks": [], "title_only_ranks": []}, {"title": "Caroline, Princess of Hanover", "sentence": 0, "text": "Caroline, Princess of Hanover (Caroline Louise Marguerite Grimaldi; born 23 January 1957), is the eldest child of Rainier III, Prince of Monaco, and the American actress Grace Kelly.", "ranks": [], "title_only_ranks": []}]`

Ranks 1–5 are the top-five list; all rows below form top20. Scores across methods are not comparable. Duplicate titles can represent different exact content/ranges.

|Rank|Title|Score|Document ID|Version ID|Canonical identity (SHA/start/end)|
|---:|---|---:|---|---|---|

Plan buffers: `{"Shared Hit Blocks": 642629, "Shared Read Blocks": 0, "Temp Read Blocks": 10697, "Temp Written Blocks": 9344}`

Full plan: `.cache/lexical-review/1-postgres_fts-plan.json`.

### hnsw

Gold details: `[{"title": "Princess Alexandra of Hanover (born 1999)", "sentence": 1, "text": "(Alexandra Charlotte Ulrike Maryam Virginia; born 20 July 1999) is the only child of Princess Caroline of Monaco and Prince Ernst August of the defunct Kingdom of Hanover.", "ranks": [1], "title_only_ranks": [1, 2]}, {"title": "Caroline, Princess of Hanover", "sentence": 0, "text": "Caroline, Princess of Hanover (Caroline Louise Marguerite Grimaldi; born 23 January 1957), is the eldest child of Rainier III, Prince of Monaco, and the American actress Grace Kelly.", "ranks": [5], "title_only_ranks": [4, 5]}]`

Ranks 1–5 are the top-five list; all rows below form top20. Scores across methods are not comparable. Duplicate titles can represent different exact content/ranges.

|Rank|Title|Score|Document ID|Version ID|Canonical identity (SHA/start/end)|
|---:|---|---:|---|---|---|
|1|Princess Alexandra of Hanover (born 1999)|0.843423266|01a185a7-e861-720e-a518-60d89b262c39|01a185a7-e861-720e-a518-60d94bb493ed|762c82d5b51f497d251774d0eea6ef25a79be2052136516e59968871d2170e6f/0/201|
|2|Princess Alexandra of Hanover (born 1999)|0.842367078|01a184b7-0597-71b3-b019-eb4c0c51aac8|01a184b7-0597-71b3-b019-eb4d026fff0b|6cf5c82cea6f296baeec365a371769f180196a9fb3fca9aeeaf1762463d2c957/0/201|
|3|Alexandra, Princess of Leiningen|0.792015532|01a184fe-dc04-76ff-bd25-bc9fff25c748|01a184fe-dc04-76ff-bd25-bca024b7d414|e63eb6f8c43b7932cb257ac1d8eec9230a6055767c99fb57bed73f3dab07df6f/0/524|
|4|Caroline, Princess of Hanover|0.786226179|01a1856d-72cd-76ab-b797-0193747157cf|01a1856d-72cd-76ab-b797-019494085ea4|60339eaa413e5c06e4de1e99f0f24912d7aa3a609f267ad791700d1240340d34/0/669|
|5|Caroline, Princess of Hanover|0.777697308|01a185a7-e85b-77a0-8652-ad6f3fce50f4|01a185a7-e85b-77a0-8652-ad70dfd423be|fbb74c1dfce62f623ca6b7fa6d9f30dc1418b63c3280d00b4418a4cd435f6064/0/669|
|6|Prince Ernst August of Hanover (born 1954)|0.766630687|01a184fe-da8c-73ec-9b6b-70135c86d3e9|01a184fe-da8c-73ec-9b6b-7014f26bf269|9f2074c9b71cd7055ac84fb6174e42f318ae91d6e760824ed0b1433ce9f80437/0/361|
|7|Prince Ernst August of Hanover (born 1983)|0.739012894|01a184fe-dc25-775e-8c22-bf3ff5cb9962|01a184fe-dc25-775e-8c22-bf402a43fcbe|f5186766e39cd62d0f99cd7fc5eea51de3c08439d8191034605fe8db8a8e4461/0/231|
|8|Pauline Ducruet|0.735781911|01a184f3-d22b-70ca-9ad6-3808516962e4|01a184f3-d22b-70ca-9ad6-3809679154c6|40062cfee7308c278d2a35afa8f71cfcfb049befb97b26af60e990a7ec964eda/0/312|
|9|Prince Ernst August of Hanover (born 1983)|0.731228059|01a1858f-2935-752e-b2d2-fb04d36e4045|01a1858f-2935-752e-b2d2-fb05c0d156ab|76853190b4d2d9bf411bd8d1c2eb1db88118f1c1238ec2488cabd7104613bd45/0/231|
|10|Prince Christian of Hanover (born 1985)|0.7310017|01a184fe-dbc8-7125-842c-98aa288e4380|01a184fe-dbc8-7125-842c-98abd884c40d|d9461adbb19d16632520aa489adf4c53fdd0f0476425afcc112796847e5f77fd/0/496|
|11|Princess Marie Louise of Hanover|0.724929068|01a185a0-54ca-710f-b52e-3b2f1d445d63|01a185a0-54ca-710f-b52e-3b30b755150c|5c7f268b9cbaea8c9b43c833c468c7cee54be42578c85d6797f229019b4b515c/0/485|
|12|Princess Marie Louise of Hanover|0.722819348|01a18592-e8f3-771e-a76d-717e5f6e7f83|01a18592-e8f3-771e-a76d-717fff93b322|76083d3d3a157d88f73c032ace0388b1e23b15bb438968278855275c3c9bb567/0/481|
|13|Prince Christian of Hanover (1885–1901)|0.720101983|01a185b1-644a-7667-84da-5820efb6b9de|01a185b1-644a-7667-84da-582158ed7eaf|2622b07705cbee51093e224e8f12981d20624238ed431f880636e478eb9468fd/0/505|
|14|Ekaterina Malysheva|0.719560194|01a184fe-dbc0-737c-b81c-4a937bf61dd4|01a184fe-dbc0-737c-b81c-4a9422ff94fc|8bbb2596847f810cc2d39db43e5ae667f385312a3727b71f46417171f8b12511/0/244|
|15|Princess Stéphanie of Monaco|0.718769005|01a18571-bf2a-76b9-8347-38f5595d6281|01a18571-bf2a-76b9-8347-38f6c56ff855|c82d98694fe0d81ee5a116274b2a357d88743dee36eccb58270d1739b54e8175/0/423|
|16|Prince George William of Hanover (1880–1912)|0.71862201|01a185b1-6415-75f4-9465-ac1ca80e3719|01a185b1-6415-75f4-9465-ac1d05e551fe|246b4c9ce8773e508e980f86e921c85413727c8e0eb0ec1aaed7fa15e92f2f05/0/549|
|17|Chantal Hochuli|0.717959522|01a184fe-db01-7181-8711-fe9cf065fbb4|01a184fe-db01-7181-8711-fe9d0a9a0161|f3209f11f44d408f38dac7421fa331653ba1152d650f00efdf3dc7aaddd1d32a/0/238|
|18|Princess Alexandra of Hanover (1882–1963)|0.717856808|01a185a7-e860-7294-b286-0f02e61a4312|01a185a7-e860-7294-b286-0f03a1c605e5|bed5da256aa6ab0462de4b330f972f2e7659ff840ad4b1b4dca475b215271ba5/0/304|
|19|Albert II, Prince of Monaco|0.717007347|01a1856d-72da-7098-88ff-3a71f1053b50|01a1856d-72da-7098-88ff-3a725682b5ae|5d924dd03be5c3f931d46357f485952f3bc92cbd9fcb4d6c53c728a2bc365757/0/783|
|20|Chantal Hochuli|0.716832519|01a1850d-e1b8-7303-854b-f822abeda50a|01a1850d-e1b8-7303-854b-f823887b4d46|58ff86d5409a4456b47256b12271be662052dd9548f962904b9872ae75157a08/0/238|

Plan buffers: `{"Shared Hit Blocks": 31769, "Shared Read Blocks": 0, "Temp Read Blocks": 0, "Temp Written Blocks": 0}`

Full plan: `.cache/lexical-review/1-hnsw-plan.json`.

### hybrid

Gold details: `[{"title": "Princess Alexandra of Hanover (born 1999)", "sentence": 1, "text": "(Alexandra Charlotte Ulrike Maryam Virginia; born 20 July 1999) is the only child of Princess Caroline of Monaco and Prince Ernst August of the defunct Kingdom of Hanover.", "ranks": [2], "title_only_ranks": [1, 2]}, {"title": "Caroline, Princess of Hanover", "sentence": 0, "text": "Caroline, Princess of Hanover (Caroline Louise Marguerite Grimaldi; born 23 January 1957), is the eldest child of Rainier III, Prince of Monaco, and the American actress Grace Kelly.", "ranks": [11], "title_only_ranks": [9, 11]}]`

Ranks 1–5 are the top-five list; all rows below form top20. Scores across methods are not comparable. Duplicate titles can represent different exact content/ranges.

|Rank|Title|Score|Document ID|Version ID|Canonical identity (SHA/start/end)|
|---:|---|---:|---|---|---|
|1|Princess Alexandra of Hanover (born 1999)|0.0325224749|01a184b7-0597-71b3-b019-eb4c0c51aac8|01a184b7-0597-71b3-b019-eb4d026fff0b|6cf5c82cea6f296baeec365a371769f180196a9fb3fca9aeeaf1762463d2c957/0/201|
|2|Princess Alexandra of Hanover (born 1999)|0.0325224749|01a185a7-e861-720e-a518-60d89b262c39|01a185a7-e861-720e-a518-60d94bb493ed|762c82d5b51f497d251774d0eea6ef25a79be2052136516e59968871d2170e6f/0/201|
|3|Alexandra, Princess of Leiningen|0.0317460317|01a184fe-dc04-76ff-bd25-bc9fff25c748|01a184fe-dc04-76ff-bd25-bca024b7d414|e63eb6f8c43b7932cb257ac1d8eec9230a6055767c99fb57bed73f3dab07df6f/0/524|
|4|Princess Alexandra of Hanover (1882–1963)|0.0284455128|01a185a7-e860-7294-b286-0f02e61a4312|01a185a7-e860-7294-b286-0f03a1c605e5|bed5da256aa6ab0462de4b330f972f2e7659ff840ad4b1b4dca475b215271ba5/0/304|
|5|Ekaterina Malysheva|0.0282193959|01a184fe-dbc0-737c-b81c-4a937bf61dd4|01a184fe-dbc0-737c-b81c-4a9422ff94fc|8bbb2596847f810cc2d39db43e5ae667f385312a3727b71f46417171f8b12511/0/244|
|6|Prince Ernst August of Hanover (born 1954)|0.027809743|01a184fe-da8c-73ec-9b6b-70135c86d3e9|01a184fe-da8c-73ec-9b6b-7014f26bf269|9f2074c9b71cd7055ac84fb6174e42f318ae91d6e760824ed0b1433ce9f80437/0/361|
|7|Chantal Hochuli|0.02707152|01a184fe-db01-7181-8711-fe9cf065fbb4|01a184fe-db01-7181-8711-fe9d0a9a0161|f3209f11f44d408f38dac7421fa331653ba1152d650f00efdf3dc7aaddd1d32a/0/238|
|8|Chantal Hochuli|0.0263888889|01a1850d-e1b8-7303-854b-f822abeda50a|01a1850d-e1b8-7303-854b-f823887b4d46|58ff86d5409a4456b47256b12271be662052dd9548f962904b9872ae75157a08/0/238|
|9|Caroline, Princess of Hanover|0.015625|01a1856d-72cd-76ab-b797-0193747157cf|01a1856d-72cd-76ab-b797-019494085ea4|60339eaa413e5c06e4de1e99f0f24912d7aa3a609f267ad791700d1240340d34/0/669|
|10|Princess Irene of Greece and Denmark|0.0153846154|01a184b7-1516-77bb-98b9-1c1f45ce2120|01a184b7-1516-77bb-98b9-1c208d1c553c|049fd9370675cfcd1364d00a3a1b60514afe1b85cd46af9bb62de24560f4e76b/0/304|
|11|Caroline, Princess of Hanover|0.0153846154|01a185a7-e85b-77a0-8652-ad6f3fce50f4|01a185a7-e85b-77a0-8652-ad70dfd423be|fbb74c1dfce62f623ca6b7fa6d9f30dc1418b63c3280d00b4418a4cd435f6064/0/669|
|12|Thyra von Westernhagen|0.0151515152|01a184fe-dc66-71f1-bae1-af6cf966f0a3|01a184fe-dc66-71f1-bae1-af6d565f0a83|bcd28f8836322f4e4dfee3c57a2a786c7fe06060782a94fcff5039e582c19578/0/207|
|13|Lord Herbert Montagu Douglas Scott|0.0149253731|01a184c8-e1a1-715f-a7b2-52a6d5244bef|01a184c8-e1a1-715f-a7b2-52a7409f7c9c|9483a65fd97e411ce4c98274a7c20a01058bb98403ea644f767ee147ce60b3cc/0/677|
|14|Prince Ernst August of Hanover (born 1983)|0.0149253731|01a184fe-dc25-775e-8c22-bf3ff5cb9962|01a184fe-dc25-775e-8c22-bf402a43fcbe|f5186766e39cd62d0f99cd7fc5eea51de3c08439d8191034605fe8db8a8e4461/0/231|
|15|Pauline Ducruet|0.0147058824|01a184f3-d22b-70ca-9ad6-3808516962e4|01a184f3-d22b-70ca-9ad6-3809679154c6|40062cfee7308c278d2a35afa8f71cfcfb049befb97b26af60e990a7ec964eda/0/312|
|16|Lord Herbert Montagu Douglas Scott|0.0144927536|01a1856f-ba1b-76ce-8ba6-2e88734c0087|01a1856f-ba1b-76ce-8ba6-2e89db4b8a6a|04da4c989dc1b4105f047c0dffa5440f98518060447fa08c132760d34b4e4010/0/673|
|17|Prince Ernst August of Hanover (born 1983)|0.0144927536|01a1858f-2935-752e-b2d2-fb04d36e4045|01a1858f-2935-752e-b2d2-fb05c0d156ab|76853190b4d2d9bf411bd8d1c2eb1db88118f1c1238ec2488cabd7104613bd45/0/231|
|18|Prince Christian of Hanover (born 1985)|0.0142857143|01a184fe-dbc8-7125-842c-98aa288e4380|01a184fe-dbc8-7125-842c-98abd884c40d|d9461adbb19d16632520aa489adf4c53fdd0f0476425afcc112796847e5f77fd/0/496|
|19|Princess Thyra of Denmark|0.0142857143|01a185b1-63ee-71bf-b8ea-e3f36fbb476c|01a185b1-63ee-71bf-b8ea-e3f44cb2c016|5b52f22201b20a2032d48e19c95b13a7cd34deafb786ab73146848e0049fcb3b/0/588|
|20|Princess Marie Louise of Hanover|0.014084507|01a185a0-54ca-710f-b52e-3b2f1d445d63|01a185a0-54ca-710f-b52e-3b30b755150c|5c7f268b9cbaea8c9b43c833c468c7cee54be42578c85d6797f229019b4b515c/0/485|

Plan buffers: `{"Shared Hit Blocks": 1536934, "Shared Read Blocks": 0, "Temp Read Blocks": 25697, "Temp Written Blocks": 9345}`

Full plan: `.cache/lexical-review/1-hybrid-plan.json`.

## Question 3: Where did M. K. Muthu's father die?

Reference answer: kauvery hospital


### bm25

Gold details: `[{"title": "M. K. Muthu", "sentence": 1, "text": "Muthu is the eldest son of late former Tamil Nadu chief minister M. Karunanidhi.", "ranks": [1], "title_only_ranks": [1]}, {"title": "M. Karunanidhi", "sentence": 6, "text": "Karunanidhi died on 7 August 2018 at Kauvery Hospital in Chennai after prolonged, age-related illness.", "ranks": [], "title_only_ranks": []}]`

Ranks 1–5 are the top-five list; all rows below form top20. Scores across methods are not comparable. Duplicate titles can represent different exact content/ranges.

|Rank|Title|Score|Document ID|Version ID|Canonical identity (SHA/start/end)|
|---:|---|---:|---|---|---|
|1|M. K. Muthu|-26.5703959|01a184cd-116e-7499-aed0-e813bd9becbf|01a184cd-116e-7499-aed0-e814cccfcfaa|21722e62c004775189bc2345d948e050666e76d94536818c7bde0ffddb6513ff/0/390|
|2|Ann McQuaid|-14.4719498|01a1859d-81a7-76c1-ac0a-cf580ce1f7e2|01a1859d-81a7-76c1-ac0a-cf591e66cefc|f36023c741b1485b2f302e5b1dd6dccbc3fa1b9df5df3633c9dc64f58ac90681/0/220|
|3|Richard M.K. Saunders|-14.1063957|01a184f2-fba8-71f1-b4d6-315b8044af94|01a184f2-fba8-71f1-b4d6-315cfd8f3b4a|3032fb9b5c8d3cd5fbd18d69e9b1a2787ea1771d1099e87dee3df0d3a4c99607/0/49|
|4|Myint Tayzar Phone|-14.0247207|01a184ba-8489-716c-8225-e41c04e94d7d|01a184ba-8489-716c-8225-e41de5b48e2a|3816e28926a5c03c0af44d5577d5e465c4cba0fb70967c0ef3d1e80ee935cc3b/0/240|
|5|Pier Duilio Puccetti|-14.0247207|01a185ac-64cd-756e-9cc1-4f206f33f3fd|01a185ac-64cd-756e-9cc1-4f21fc670152|d91da73b657d889f087865e2436cffab0d99553cfad4d8a395cc58e622a0e782/0/245|
|6|Vetri Vinayagar|-13.4621067|01a18592-a88d-74fb-9b73-bfcb1cbb8964|01a18592-a88d-74fb-9b73-bfcc9c90501f|c7708ff0e4924c3d052fcfe3c035aa4a5669918a866b14f42394c751edaf8dd2/0/393|
|7|Nalla Idathu Sammandham|-13.3214197|01a1856e-da58-729b-905e-659396d9f043|01a1856e-da58-729b-905e-6594b46e3740|7b4eace88278fd642c53b26e6e74b22796bee1f891e9356016a11a44b6c5e548/0/431|
|8|Banashankari (film)|-13.1401534|01a18572-e8e1-768b-a212-3f836e5847ac|01a18572-e8e1-768b-a212-3f8445a16e5d|77945ab7136d1db373bfc68cc29a0791850454ac7b0a00d8141464ef79f36ca6/0/267|
|9|Nagamadathu Thampuratti|-12.9594669|01a1856d-6379-722e-8f58-7e6778f31715|01a1856d-6379-722e-8f58-7e68c4ee018f|2fef8177c7e1defe55c0af757c049b000eb92a9cf68c1e7053ebb77b3d2e5b24/0/286|
|10|Attahaasam|-12.8669076|01a184c4-bb1a-7040-bfe1-f27175e0cd44|01a184c4-bb1a-7040-bfe1-f2726a52c7b7|4cf4e8681218ff2a3b15a0fdf6413a6f84dc42f87bd4af3d5a22640626bca347/0/128|
|11|A. K. M. Nurul Islam|-12.7073989|01a184ce-ba08-702e-a2a0-b08060df13ac|01a184ce-ba08-702e-a2a0-b0814240872e|79ba74f15e578dc76beb7df3f20762f9be43bbb4a64cd37719968ea5e80754c8/0/119|
|12|Andrzej Gajewski|-12.6410828|01a18503-ef74-74e1-99cc-09f1c3be670e|01a18503-ef74-74e1-99cc-09f2d21bbcfe|8a1890ce17216898b541775307275dfff83e5333b394c6e0232e8fc6c02c243d/0/348|
|13|Collector Malathy|-12.5896392|01a1857d-34ed-7701-8b72-c68987f56be9|01a1857d-34ed-7701-8b72-c68a83b8d4aa|ff5f06013da83b8de5e365ef7625b5f4553283790fbf47117970af4c474afa7a/0/234|
|14|Edavazhiyile Poocha Minda Poocha|-12.5584645|01a1850c-3db6-77f0-adaa-57f149f3df88|01a1850c-3db7-7581-8633-b458cda39632|ae9d122edfb68fbdcf8d9d4f8c66da2555ebf1624ebf25a7fb2aefd2396c38db/0/267|
|15|Avasara Police 100|-12.3999586|01a185a0-d8a0-751f-8317-ad7f58747140|01a185a0-d8a0-751f-8317-ad807a6d2a3b|d5a48c3e0bc7b704a05f1c5db44e461cb7bbdccf181606c6e019cc873e110f8f/0/136|
|16|Poonthenaruvi|-12.373035|01a184cd-efb8-77a3-8b70-8bf1612bbb6f|01a184cd-efb8-77a3-8b70-8bf2a7c0f17c|86563e984a41552f20eadea95231450e3790d3858a48866a8abfc8cf5de43c57/0/223|
|17|Bhajantrilu|-12.1070428|01a1858c-c47b-7142-b9ad-6acee1cfefc5|01a1858c-c47b-7142-b9ad-6acf8a30a4fe|8da0c65740e904ff8b5f205688add88bbd6c92265dfad1a56b5ec41e4a85bc0b/0/195|
|18|Muddula Priyudu|-11.9068952|01a1857f-4f78-7517-8b1d-7ab6e5676c3b|01a1857f-4f78-7517-8b1d-7ab7f21bf36d|12100ac95b62d29a1c94f5bb9970e2128ade522ee93fa391538793aca8ab375c/0/511|
|19|Uzhaikkum Karangal|-11.8276463|01a184b1-e77b-708e-8d2d-b570cb5f3599|01a184b1-e77b-708e-8d2d-b5713a7dea75|5aad89ed3b5a60af936a7636ebb51da99c3375f5748538d12f44ba9c3b18042f/0/166|
|20|M. K. Arjunan|-11.6927295|01a1857d-39de-75c1-9b91-c80d2ec962a0|01a1857d-39de-75c1-9b91-c80e804af34c|444435e7f32c8b57ffba2c21667189dab62323b5e1df3c52317380fc21fff2de/0/188|

Plan buffers: `{"Shared Hit Blocks": 1505075, "Shared Read Blocks": 0, "Temp Read Blocks": 25697, "Temp Written Blocks": 9345}`

Full plan: `.cache/lexical-review/2-bm25-plan.json`.

### postgres_fts

Gold details: `[{"title": "M. K. Muthu", "sentence": 1, "text": "Muthu is the eldest son of late former Tamil Nadu chief minister M. Karunanidhi.", "ranks": [], "title_only_ranks": []}, {"title": "M. Karunanidhi", "sentence": 6, "text": "Karunanidhi died on 7 August 2018 at Kauvery Hospital in Chennai after prolonged, age-related illness.", "ranks": [], "title_only_ranks": []}]`

Ranks 1–5 are the top-five list; all rows below form top20. Scores across methods are not comparable. Duplicate titles can represent different exact content/ranges.

|Rank|Title|Score|Document ID|Version ID|Canonical identity (SHA/start/end)|
|---:|---|---:|---|---|---|

Plan buffers: `{"Shared Hit Blocks": 642629, "Shared Read Blocks": 0, "Temp Read Blocks": 10697, "Temp Written Blocks": 9344}`

Full plan: `.cache/lexical-review/2-postgres_fts-plan.json`.

### hnsw

Gold details: `[{"title": "M. K. Muthu", "sentence": 1, "text": "Muthu is the eldest son of late former Tamil Nadu chief minister M. Karunanidhi.", "ranks": [1], "title_only_ranks": [1]}, {"title": "M. Karunanidhi", "sentence": 6, "text": "Karunanidhi died on 7 August 2018 at Kauvery Hospital in Chennai after prolonged, age-related illness.", "ranks": [2], "title_only_ranks": [2]}]`

Ranks 1–5 are the top-five list; all rows below form top20. Scores across methods are not comparable. Duplicate titles can represent different exact content/ranges.

|Rank|Title|Score|Document ID|Version ID|Canonical identity (SHA/start/end)|
|---:|---|---:|---|---|---|
|1|M. K. Muthu|0.755841226|01a184cd-116e-7499-aed0-e813bd9becbf|01a184cd-116e-7499-aed0-e814cccfcfaa|21722e62c004775189bc2345d948e050666e76d94536818c7bde0ffddb6513ff/0/390|
|2|M. Karunanidhi|0.724472051|01a184cd-1176-74eb-a29d-c97dece1ff89|01a184cd-1176-74eb-a29d-c97ebee75e75|4860bc1d6336278765f13c0512ba4194adae02c0d202b340470989a2d3ba14e6/0/877|
|3|Genshin Takano (governor of Hiroshima)|0.713506269|01a184c4-c58c-759b-8357-dd0546a0e418|01a184c4-c58c-759b-8357-dd061421df14|6dd9e1be283b4d000c88ddaa769d97efec0b7edeb6ad262fd2e58961b323dcee/0/21|
|4|Muthukkal Moondru|0.681197704|01a184e8-71f5-714c-9b7b-0c355afcd85c|01a184e8-71f5-714c-9b7b-0c36de70a09c|d0140b6d76859f2db1ccde2faf57800f8e92924afa61113440e296c0bed837f8/0/198|
|5|N. S. Krishnan|0.678139784|01a1857b-311d-734c-b71f-000bfb79d912|01a1857b-311d-734c-b71f-000cae0fcab9|06f06918d1d745e380b16d8cffdaba1f3a40ea48f1bea68f5c86c9f7e5b27fa7/0/554|
|6|Nandamuri Harikrishna|0.672475856|01a1857b-d9e3-72b4-94c0-ac20acf5657b|01a1857b-d9e3-72b4-94c0-ac214a7a589f|2e1741bc3730343584c0c0712c4bbbb1983139886c996ee033d5756940242133/0/1380|
|7|Ichirō Satsuki|0.671193959|01a18582-3408-7314-915e-91c9b9771f99|01a18582-3409-70ed-b671-4f17d91bb3ab|75641644df26962bb5cc5077008245b6c5503a744716da0c1d26d70e3f2866f2/0/25|
|8|M. B. Sreenivasan|0.669504185|01a184d9-4ef3-77a3-b0a3-e1390abdfbdc|01a184d9-4ef3-77a3-b0a3-e13a5d549f36|c45626036b3853343cb2b59c0e8850155c8a27cdba107a9f84f4ba7f7735dede/0/1625|
|9|M. S. Narayana|0.667102178|01a1858c-c473-748b-886d-a901fc610566|01a1858c-c473-748b-886d-a902e8a7e4d7|2e87172d6c04a57ffc0fc5c5d6c72cd46871c233ca35072a01a4c2a2fed5e8cb/0/180|
|10|Marakka Mudiyumaa?|0.660871009|01a185a1-95ae-7330-99bd-a950baa57f3b|01a185a1-95ae-7330-99bd-a951b6f59c9c|96b39e2efd051983bedcc4016213193b81fa9cf842220580916c2e50fd800b0c/0/561|
|11|Sanjay Gandhi|0.659280221|01a18585-c807-73d3-a34d-7fa98a5a1e4a|01a18585-c807-73d3-a34d-7faa0a80fc2a|2c84790afe0ddde3d4c179ee701ef5af66fd07179ce92df3ca195177d5a3b631/0/528|
|12|H. Nagappa|0.658963302|01a18595-1c6d-7523-8bd5-4ce1f2825cdb|01a18595-1c6d-7523-8bd5-4ce21919106d|21c7835e90f989627041b99a6eef537c2eb21a600b733d38265b7e180e219d8d/0/432|
|13|Rajesh Khanna|0.656521757|01a1859f-1361-724d-8ee2-8c30b2c5c388|01a1859f-1361-724d-8ee2-8c31ba2f0e4e|d231515decdb162e374160c776cb3dc4ca3ebddd6686bdfd3c4763b58606a264/1723/2191|
|14|Avan Pithana?|0.655208624|01a184e5-d2bc-7768-8bab-15da3db372c5|01a184e5-d2bc-7768-8bab-15db7b81c6d1|6f89430ee61bb4ca8d1b4c2243916633e64c26affc55544aed8b75452390da2a/0/319|
|15|C. M. Stephen|0.654575569|01a18574-f5a7-71c8-96b8-3eeed076efee|01a18574-f5a7-71c8-96b8-3eefad05f680|f10bb855a2da83b0ec896eb7678e4966c3f8c77d7c1355231afd4fa0ffd8cb88/1854/2144|
|16|Kasthuri Raja|0.653417169|01a184b0-d65e-73ed-b547-b839a15659f5|01a184b0-d65e-73ed-b547-b83ac796815f|158c85580189249082f2b50b72b1f5393c0e4c38750ba33ba97c4ce5991f6689/0/486|
|17|Kodardas Kalidas Shah|0.651949664|01a1857c-90b3-7307-b4c6-c0baf5744614|01a1857c-90b3-7307-b4c6-c0bbe3906e3a|316ccf31633ce2f86ebf1dd7fcd957b586cfb166b2a4b46936dffbe782a21d6a/0/459|
|18|Man Singh (dacoit)|0.651899876|01a185b4-0932-76b3-85c3-134a8eeb1111|01a185b4-0932-76b3-85c3-134b3360f9bf|43db0e158380c8255cd66dc68416cf957554dc764d2b44c30e02d220b67f68a4/0/146|
|19|Yakov Dzhugashvili|0.650261585|01a184c5-a232-77ef-bafa-49b018bdbe93|01a184c5-a232-77ef-bafa-49b1961036e9|d89688b466a35bbf311112fc8959931a8fcbd13a1b9040d4cb93524c813b6288/0/1160|
|20|Nusrat Bhutto|0.649365883|01a185bb-4425-70f1-8420-00d938dbc081|01a185bb-4425-70f1-8420-00dab5a79988|d71129f76744b2e05e040561c118dadfec50737f4994d8bd040705d88dc7eb61/1921/2683|

Plan buffers: `{"Shared Hit Blocks": 23365, "Shared Read Blocks": 0, "Temp Read Blocks": 0, "Temp Written Blocks": 0}`

Full plan: `.cache/lexical-review/2-hnsw-plan.json`.

### hybrid

Gold details: `[{"title": "M. K. Muthu", "sentence": 1, "text": "Muthu is the eldest son of late former Tamil Nadu chief minister M. Karunanidhi.", "ranks": [1], "title_only_ranks": [1]}, {"title": "M. Karunanidhi", "sentence": 6, "text": "Karunanidhi died on 7 August 2018 at Kauvery Hospital in Chennai after prolonged, age-related illness.", "ranks": [2], "title_only_ranks": [2]}]`

Ranks 1–5 are the top-five list; all rows below form top20. Scores across methods are not comparable. Duplicate titles can represent different exact content/ranges.

|Rank|Title|Score|Document ID|Version ID|Canonical identity (SHA/start/end)|
|---:|---|---:|---|---|---|
|1|M. K. Muthu|0.0327868852|01a184cd-116e-7499-aed0-e813bd9becbf|01a184cd-116e-7499-aed0-e814cccfcfaa|21722e62c004775189bc2345d948e050666e76d94536818c7bde0ffddb6513ff/0/390|
|2|M. Karunanidhi|0.0161290323|01a184cd-1176-74eb-a29d-c97dece1ff89|01a184cd-1176-74eb-a29d-c97ebee75e75|4860bc1d6336278765f13c0512ba4194adae02c0d202b340470989a2d3ba14e6/0/877|
|3|Ann McQuaid|0.0161290323|01a1859d-81a7-76c1-ac0a-cf580ce1f7e2|01a1859d-81a7-76c1-ac0a-cf591e66cefc|f36023c741b1485b2f302e5b1dd6dccbc3fa1b9df5df3633c9dc64f58ac90681/0/220|
|4|Genshin Takano (governor of Hiroshima)|0.0158730159|01a184c4-c58c-759b-8357-dd0546a0e418|01a184c4-c58c-759b-8357-dd061421df14|6dd9e1be283b4d000c88ddaa769d97efec0b7edeb6ad262fd2e58961b323dcee/0/21|
|5|Richard M.K. Saunders|0.0158730159|01a184f2-fba8-71f1-b4d6-315b8044af94|01a184f2-fba8-71f1-b4d6-315cfd8f3b4a|3032fb9b5c8d3cd5fbd18d69e9b1a2787ea1771d1099e87dee3df0d3a4c99607/0/49|
|6|Myint Tayzar Phone|0.015625|01a184ba-8489-716c-8225-e41c04e94d7d|01a184ba-8489-716c-8225-e41de5b48e2a|3816e28926a5c03c0af44d5577d5e465c4cba0fb70967c0ef3d1e80ee935cc3b/0/240|
|7|Muthukkal Moondru|0.015625|01a184e8-71f5-714c-9b7b-0c355afcd85c|01a184e8-71f5-714c-9b7b-0c36de70a09c|d0140b6d76859f2db1ccde2faf57800f8e92924afa61113440e296c0bed837f8/0/198|
|8|N. S. Krishnan|0.0153846154|01a1857b-311d-734c-b71f-000bfb79d912|01a1857b-311d-734c-b71f-000cae0fcab9|06f06918d1d745e380b16d8cffdaba1f3a40ea48f1bea68f5c86c9f7e5b27fa7/0/554|
|9|Pier Duilio Puccetti|0.0153846154|01a185ac-64cd-756e-9cc1-4f206f33f3fd|01a185ac-64cd-756e-9cc1-4f21fc670152|d91da73b657d889f087865e2436cffab0d99553cfad4d8a395cc58e622a0e782/0/245|
|10|Nandamuri Harikrishna|0.0151515152|01a1857b-d9e3-72b4-94c0-ac20acf5657b|01a1857b-d9e3-72b4-94c0-ac214a7a589f|2e1741bc3730343584c0c0712c4bbbb1983139886c996ee033d5756940242133/0/1380|
|11|Vetri Vinayagar|0.0151515152|01a18592-a88d-74fb-9b73-bfcb1cbb8964|01a18592-a88d-74fb-9b73-bfcc9c90501f|c7708ff0e4924c3d052fcfe3c035aa4a5669918a866b14f42394c751edaf8dd2/0/393|
|12|Nalla Idathu Sammandham|0.0149253731|01a1856e-da58-729b-905e-659396d9f043|01a1856e-da58-729b-905e-6594b46e3740|7b4eace88278fd642c53b26e6e74b22796bee1f891e9356016a11a44b6c5e548/0/431|
|13|Ichirō Satsuki|0.0149253731|01a18582-3408-7314-915e-91c9b9771f99|01a18582-3409-70ed-b671-4f17d91bb3ab|75641644df26962bb5cc5077008245b6c5503a744716da0c1d26d70e3f2866f2/0/25|
|14|M. B. Sreenivasan|0.0147058824|01a184d9-4ef3-77a3-b0a3-e1390abdfbdc|01a184d9-4ef3-77a3-b0a3-e13a5d549f36|c45626036b3853343cb2b59c0e8850155c8a27cdba107a9f84f4ba7f7735dede/0/1625|
|15|Banashankari (film)|0.0147058824|01a18572-e8e1-768b-a212-3f836e5847ac|01a18572-e8e1-768b-a212-3f8445a16e5d|77945ab7136d1db373bfc68cc29a0791850454ac7b0a00d8141464ef79f36ca6/0/267|
|16|Nagamadathu Thampuratti|0.0144927536|01a1856d-6379-722e-8f58-7e6778f31715|01a1856d-6379-722e-8f58-7e68c4ee018f|2fef8177c7e1defe55c0af757c049b000eb92a9cf68c1e7053ebb77b3d2e5b24/0/286|
|17|M. S. Narayana|0.0144927536|01a1858c-c473-748b-886d-a901fc610566|01a1858c-c473-748b-886d-a902e8a7e4d7|2e87172d6c04a57ffc0fc5c5d6c72cd46871c233ca35072a01a4c2a2fed5e8cb/0/180|
|18|Attahaasam|0.0142857143|01a184c4-bb1a-7040-bfe1-f27175e0cd44|01a184c4-bb1a-7040-bfe1-f2726a52c7b7|4cf4e8681218ff2a3b15a0fdf6413a6f84dc42f87bd4af3d5a22640626bca347/0/128|
|19|Marakka Mudiyumaa?|0.0142857143|01a185a1-95ae-7330-99bd-a950baa57f3b|01a185a1-95ae-7330-99bd-a951b6f59c9c|96b39e2efd051983bedcc4016213193b81fa9cf842220580916c2e50fd800b0c/0/561|
|20|A. K. M. Nurul Islam|0.014084507|01a184ce-ba08-702e-a2a0-b08060df13ac|01a184ce-ba08-702e-a2a0-b0814240872e|79ba74f15e578dc76beb7df3f20762f9be43bbb4a64cd37719968ea5e80754c8/0/119|

Plan buffers: `{"Shared Hit Blocks": 1528440, "Shared Read Blocks": 0, "Temp Read Blocks": 25697, "Temp Written Blocks": 9345}`

Full plan: `.cache/lexical-review/2-hybrid-plan.json`.

## Question 4: Who lived longer, Friedrich Paschen or Ernst Bessey?

Reference answer: Friedrich Paschen


### bm25

Gold details: `[{"title": "Friedrich Paschen", "sentence": 0, "text": "Louis Carl Heinrich Friedrich Paschen( 22 January 1865- 25 February 1947), was a German physicist, known for his work on electrical discharges.", "ranks": [1], "title_only_ranks": [1]}, {"title": "Ernst Bessey", "sentence": 0, "text": "Ernst Athearn Bessey( February 20, 1877 – July 17, 1957) was an American mycologist, botanist and plant pathologist who served Professor of Mycology and Botany and Dean of the Graduate School at Michigan State University.", "ranks": [2], "title_only_ranks": [2]}]`

Ranks 1–5 are the top-five list; all rows below form top20. Scores across methods are not comparable. Duplicate titles can represent different exact content/ranges.

|Rank|Title|Score|Document ID|Version ID|Canonical identity (SHA/start/end)|
|---:|---|---:|---|---|---|
|1|Friedrich Paschen|-20.8423204|01a1856f-7902-7716-9af5-7f67ffafb5c0|01a1856f-7902-7716-9af5-7f68f21f8d3a|c3c5f6ec37cb9ad4f7d0335a5f115ef100a490e920fa9a270c7f77388c8557d2/0/634|
|2|Ernst Bessey|-20.3730259|01a1856f-7909-763e-947a-e7b5f9103f48|01a1856f-7909-763e-947a-e7b6da38980d|a6956a99c9fed27fbf26d9f4db21302b028b6a2a89ccb32103db42c93969a6b6/0/1441|
|3|Ernst I, Duke of Saxe-Altenburg|-15.7045183|01a184ff-a20d-71bd-816d-4b6b33bdb4ab|01a184ff-a20d-71bd-816d-4b6ce6eb5ac3|b148a18cfb2dcd3a2c171a548df6245037c88c193fc8b2c71717e700c3250867/0/593|
|4|Prince Waldemar of Prussia (1868–1879)|-15.3535733|01a1856e-1a68-77b5-b090-8a828805ceac|01a1856e-1a68-77b5-b090-8a83d7ea11d4|89fd2ee553368c5f25da44ac11afdf9777d49bcd2dae06ef1c2559079ab9f646/0/252|
|5|Prince Waldemar of Prussia (1868–1879)|-15.3535733|01a1857e-a267-77c9-ae90-8748265c5c1b|01a1857e-a267-77c9-ae90-87491efd2f51|1371b04375781de225fe156c8b74039163f842e9b7d2a6234a442719f1833131/0/252|
|6|Ernest, Count of Lippe-Biesterfeld|-15.0287919|01a1858a-4254-7771-8252-01d77f3c041b|01a1858a-4254-7771-8252-01d806be7fb2|dfda0e735c525a9462d1f505440b05e888a2595f1315512e238bbfc1dec307c1/0/250|
|7|Ernest Friedrich Gilg|-14.6924443|01a184cc-fc03-7533-8225-d21f59f7405c|01a184cc-fc03-7533-8225-d2208b650559|f77ebceb5451880623fa335498861a9073e9797eebf7dde203843174a99ef8b6/0/134|
|8|Sumurun|-14.6924443|01a184f9-8e6f-7196-a000-3e9ba05791f8|01a184f9-8e6f-7196-a000-3e9ca0ed31e6|fb2bcd61e57eb00e3031a70507182bb16b0958f8881f441fed51ee845e611043/0/134|
|9|Friedrich Josias, Prince of Saxe-Coburg and Gotha|-14.1940341|01a184f9-bbd1-7642-bf05-1f0b941e64d1|01a184f9-bbd1-7642-bf05-1f0c51b87431|1cef1a6a53495c02bce27b103aad3f6d77cd223ecca5bbcc57e1316e015e559b/0/266|
|10|Ernst zu Münster|-13.5325031|01a184cc-fc03-7533-8225-d21dc5312aa9|01a184cc-fc03-7533-8225-d21e6adab158|85f62d6d9bd1e6eed012789a5ecbf987a147428761fa09c3f3345bd3ec452fc9/0/185|
|11|Prince Christian of Hanover (born 1985)|-13.4025693|01a184fe-dbc8-7125-842c-98aa288e4380|01a184fe-dbc8-7125-842c-98abd884c40d|d9461adbb19d16632520aa489adf4c53fdd0f0476425afcc112796847e5f77fd/0/496|
|12|Friedrich Graf von Wrangel|-13.3815813|01a184d5-100d-77e9-b67c-ba43a36e242f|01a184d5-100d-77e9-b67c-ba44974da558|5b5a7366c70f6ce9a4fd6f247d661c9ee34d47d420266fdd9628aa654ee2b17f/0/217|
|13|Maximilian, Margrave of Baden|-13.089221|01a18589-7b08-7119-8edc-ec95b10c1f7f|01a18589-7b08-7119-8edc-ec96f4790cbd|9b5ca1b32949f7831cdd220b73822643478fd921ef2c88a2f33231dfa63fd531/0/518|
|14|Friedrich Gottlob Uhlemann|-12.8097157|01a184f0-3997-7381-a5ab-7401aefc52e7|01a184f0-3997-7381-a5ab-74025272402c|5af71173878da2037244a745d0a4f6325681d682dde920532b767dcb5bb7e741/0/759|
|15|Prince Moritz of Saxe-Altenburg|-12.2854686|01a184ff-a010-7587-a6e9-4cab3c731a5d|01a184ff-a011-768c-9ce4-764bedf51512|5615368fddf1c197b4a59b80b71bf51ad3fa3600d241c5fbb5a0c14a206fb6f8/0/268|
|16|Ernst II, Prince of Hohenlohe-Langenburg|-12.0389357|01a184ec-ea6c-73d4-84f2-f652a93c1b57|01a184ec-ea6c-73d4-84f2-f65350a83112|e5d9d413f791ef99159261354bf12da5ec5775e9dc4db823ff7be6573fea036c/0/323|
|17|Gustav Albrecht, 5th Prince of Sayn-Wittgenstein-Berleburg|-11.9193425|01a184d7-adb9-77f0-8049-d287f624263c|01a184d7-adb9-77f0-8049-d288d700fc99|4c554ec81c1f07ec3545603c7e8ac68413eb57ded6573f4c8f579672b22511b1/0/293|
|18|Ernst Ludwig, Duke of Pomerania|-11.8021021|01a18579-bbcd-7625-b529-652c1f43e022|01a18579-bbcd-7625-b529-652d73151c31|36f872d66f13d3323ef7d8c19e052d539f16aa6b9f82b64e0de597faaa56c338/0/338|
|19|Carol Victor, Hereditary Prince of Albania|-11.5744066|01a18507-2d54-7707-b516-ab3b288eca05|01a18507-2d54-7707-b516-ab3c2943c5e3|10125d98c7f7fb68c8f39b2505891f32fed920909bfa06b0bb88613dba5a9e64/0/346|
|20|Carol Victor, Hereditary Prince of Albania|-11.5744066|01a18514-5a5f-705c-9e91-96436c016e44|01a18514-5a5f-705c-9e91-964493bef2a5|2b64e9da54dc0e30227c0597d2588d7553a47f5174fa4b5d363b72baefdff3f7/0/346|

Plan buffers: `{"Shared Hit Blocks": 1505121, "Shared Read Blocks": 0, "Temp Read Blocks": 25697, "Temp Written Blocks": 9345}`

Full plan: `.cache/lexical-review/3-bm25-plan.json`.

### postgres_fts

Gold details: `[{"title": "Friedrich Paschen", "sentence": 0, "text": "Louis Carl Heinrich Friedrich Paschen( 22 January 1865- 25 February 1947), was a German physicist, known for his work on electrical discharges.", "ranks": [], "title_only_ranks": []}, {"title": "Ernst Bessey", "sentence": 0, "text": "Ernst Athearn Bessey( February 20, 1877 – July 17, 1957) was an American mycologist, botanist and plant pathologist who served Professor of Mycology and Botany and Dean of the Graduate School at Michigan State University.", "ranks": [], "title_only_ranks": []}]`

Ranks 1–5 are the top-five list; all rows below form top20. Scores across methods are not comparable. Duplicate titles can represent different exact content/ranges.

|Rank|Title|Score|Document ID|Version ID|Canonical identity (SHA/start/end)|
|---:|---|---:|---|---|---|

Plan buffers: `{"Shared Hit Blocks": 642629, "Shared Read Blocks": 0, "Temp Read Blocks": 10697, "Temp Written Blocks": 9344}`

Full plan: `.cache/lexical-review/3-postgres_fts-plan.json`.

### hnsw

Gold details: `[{"title": "Friedrich Paschen", "sentence": 0, "text": "Louis Carl Heinrich Friedrich Paschen( 22 January 1865- 25 February 1947), was a German physicist, known for his work on electrical discharges.", "ranks": [2], "title_only_ranks": [2]}, {"title": "Ernst Bessey", "sentence": 0, "text": "Ernst Athearn Bessey( February 20, 1877 – July 17, 1957) was an American mycologist, botanist and plant pathologist who served Professor of Mycology and Botany and Dean of the Graduate School at Michigan State University.", "ranks": [1], "title_only_ranks": [1]}]`

Ranks 1–5 are the top-five list; all rows below form top20. Scores across methods are not comparable. Duplicate titles can represent different exact content/ranges.

|Rank|Title|Score|Document ID|Version ID|Canonical identity (SHA/start/end)|
|---:|---|---:|---|---|---|
|1|Ernst Bessey|0.785580954|01a1856f-7909-763e-947a-e7b5f9103f48|01a1856f-7909-763e-947a-e7b6da38980d|a6956a99c9fed27fbf26d9f4db21302b028b6a2a89ccb32103db42c93969a6b6/0/1441|
|2|Friedrich Paschen|0.751859926|01a1856f-7902-7716-9af5-7f67ffafb5c0|01a1856f-7902-7716-9af5-7f68f21f8d3a|c3c5f6ec37cb9ad4f7d0335a5f115ef100a490e920fa9a270c7f77388c8557d2/0/634|
|3|Ernst Pringsheim Jr.|0.687837996|01a184cc-fbe6-76a1-adfb-14dd7e7099c0|01a184cc-fbe6-76a1-adfb-14de695354bf|dbe42d35fc95869d24ad480d8a46a7c8784f89e1f42ad2558b5ef24a5dc217aa/0/387|
|4|Ernest William Goodpasture|0.683650309|01a184b4-9545-76cf-a0c0-1e99029590c3|01a184b4-9545-76cf-a0c0-1e9a56ec89a3|35cf236098a0a8d848fad710b23e5363092ff595e5876f8a89c7a09a6d08919d/0/616|
|5|Ernest Friedrich Gilg|0.671763043|01a184cc-fc03-7533-8225-d21f59f7405c|01a184cc-fc03-7533-8225-d2208b650559|f77ebceb5451880623fa335498861a9073e9797eebf7dde203843174a99ef8b6/0/134|
|6|Ernst Ziehm|0.66429945|01a185bd-5e43-7392-8b9a-ab49ddb65fb7|01a185bd-5e44-7137-b569-04364f288370|b98ec20de9eab92bddc6d3968850c2f0fb8b430b511d902e55eb0e533b869bee/0/665|
|7|Heinrich Ernst Beyrich|0.66240567|01a184d5-115a-767a-862d-93ee147f1f9a|01a184d5-115a-767a-862d-93efeeaa6a82|fa2dd9847f4c75161f82198b0ace47dd100443773bd75ece3dddb0413fed57a8/0/83|
|8|Friedrich Rosen|0.661337336|01a185a6-10c3-754b-9be9-13398f078e50|01a185a6-10c3-754b-9be9-133a655c73d1|0cde22a66d3dc520903118e97563cf9e5a142e3c8009fa6f8f098fd3bf151535/0/202|
|9|Ernest de Bunsen|0.659328879|01a18512-e650-73eb-908d-9ecd231de77d|01a18512-e650-73eb-908d-9ece5655022b|e6272600c66eb3a38af33ae0529e5d6bd8e8cbbb837621b8b3b0b343d686d4ea/0/298|
|10|Prince Ernst of Hohenberg|0.656052478|01a18572-d81f-7359-9345-405f9b73c680|01a18572-d81f-7359-9345-4060f2c74547|78c1573c516b3cc6ab7f64f19e377e228c1d8e40e48465f351469253df5a7bac/0/270|
|11|Ernest, Count of Lippe-Biesterfeld|0.654599034|01a1858a-4254-7771-8252-01d77f3c041b|01a1858a-4254-7771-8252-01d806be7fb2|dfda0e735c525a9462d1f505440b05e888a2595f1315512e238bbfc1dec307c1/0/250|
|12|Herman Frasch|0.652943398|01a184f7-161c-74ff-b204-c70f66f635b3|01a184f7-161c-74ff-b204-c710cbaee60e|2ad0ae51e2b1ad621253580b55cfb3cab925c6f7196aeff76940cc93cf5ffc4b/0/205|
|13|Friedrich Graf von Wrangel|0.650961696|01a184d5-100d-77e9-b67c-ba43a36e242f|01a184d5-100d-77e9-b67c-ba44974da558|5b5a7366c70f6ce9a4fd6f247d661c9ee34d47d420266fdd9628aa654ee2b17f/0/217|
|14|Herman Frasch|0.649678066|01a185a0-e7d8-7647-9edf-84899bf51768|01a185a0-e7d8-7647-9edf-848a2b4a55b9|c9d1d6b2b33591ad617b914fbeb89da3e911353129ba1df6436ff19bd77e9ffd/0/204|
|15|Carl Friedrich von Weizsäcker|0.6495713|01a18575-10b6-751d-a916-6a355143fa3e|01a18575-10b6-751d-a916-6a360571863c|6b75e0090d266d58aa9ac6b3b4667a5515d9cdc9a71c4c70518e5afdcb0455aa/0/1141|
|16|Erich Haenisch|0.649245584|01a185b1-1e79-71af-91a0-a93f260eebdc|01a185b1-1e79-71af-91a0-a940bd5978bc|21dbabea0b298bee41673f88b893d0aaf86c30d1c3f1c8ebc96530dd5b418168/0/597|
|17|George Lessey|0.647604672|01a1857e-ad68-73d8-9588-30c777d59787|01a1857e-ad68-73d8-9588-30c82b4ddede|d67d617cb77a64303fd736b2f215aa6cbd4be7eeaa05c73f2076b385ead51397/0/1027|
|18|Friedrich Erismann|0.646879119|01a185b7-c5ff-726a-a767-9974459da059|01a185b7-c600-7269-85e6-cc28f3436b46|1148bfd843f46b0f63a76df334679c1597d595b12b7fbf63e6fba4d9671db130/0/180|
|19|George Lessey|0.646744351|01a184b7-280c-75e7-85e0-b8107b296a00|01a184b7-280c-75e7-85e0-b81134d684b0|7e45a779fda3936887a97e76e8c08424374d2be4413acd371ad4c5071bbfe33a/0/1027|
|20|Ernst Heinrich Karl von Dechen|0.646460856|01a184f5-8389-76e9-8be0-90666387b9d0|01a184f5-8389-76e9-8be0-9067818d31d8|0c5cda33ce824cd650724dddacfca3061f36037294e50513c57d2f1a7db91900/0/1231|

Plan buffers: `{"Shared Hit Blocks": 46160, "Shared Read Blocks": 0, "Temp Read Blocks": 0, "Temp Written Blocks": 0}`

Full plan: `.cache/lexical-review/3-hnsw-plan.json`.

### hybrid

Gold details: `[{"title": "Friedrich Paschen", "sentence": 0, "text": "Louis Carl Heinrich Friedrich Paschen( 22 January 1865- 25 February 1947), was a German physicist, known for his work on electrical discharges.", "ranks": [1], "title_only_ranks": [1]}, {"title": "Ernst Bessey", "sentence": 0, "text": "Ernst Athearn Bessey( February 20, 1877 – July 17, 1957) was an American mycologist, botanist and plant pathologist who served Professor of Mycology and Botany and Dean of the Graduate School at Michigan State University.", "ranks": [2], "title_only_ranks": [2]}]`

Ranks 1–5 are the top-five list; all rows below form top20. Scores across methods are not comparable. Duplicate titles can represent different exact content/ranges.

|Rank|Title|Score|Document ID|Version ID|Canonical identity (SHA/start/end)|
|---:|---|---:|---|---|---|
|1|Friedrich Paschen|0.0325224749|01a1856f-7902-7716-9af5-7f67ffafb5c0|01a1856f-7902-7716-9af5-7f68f21f8d3a|c3c5f6ec37cb9ad4f7d0335a5f115ef100a490e920fa9a270c7f77388c8557d2/0/634|
|2|Ernst Bessey|0.0325224749|01a1856f-7909-763e-947a-e7b5f9103f48|01a1856f-7909-763e-947a-e7b6da38980d|a6956a99c9fed27fbf26d9f4db21302b028b6a2a89ccb32103db42c93969a6b6/0/1441|
|3|Ernest Friedrich Gilg|0.0303099885|01a184cc-fc03-7533-8225-d21f59f7405c|01a184cc-fc03-7533-8225-d2208b650559|f77ebceb5451880623fa335498861a9073e9797eebf7dde203843174a99ef8b6/0/134|
|4|Ernest, Count of Lippe-Biesterfeld|0.0292360222|01a1858a-4254-7771-8252-01d77f3c041b|01a1858a-4254-7771-8252-01d806be7fb2|dfda0e735c525a9462d1f505440b05e888a2595f1315512e238bbfc1dec307c1/0/250|
|5|Friedrich Graf von Wrangel|0.027587519|01a184d5-100d-77e9-b67c-ba43a36e242f|01a184d5-100d-77e9-b67c-ba44974da558|5b5a7366c70f6ce9a4fd6f247d661c9ee34d47d420266fdd9628aa654ee2b17f/0/217|
|6|Ernst Pringsheim Jr.|0.0158730159|01a184cc-fbe6-76a1-adfb-14dd7e7099c0|01a184cc-fbe6-76a1-adfb-14de695354bf|dbe42d35fc95869d24ad480d8a46a7c8784f89e1f42ad2558b5ef24a5dc217aa/0/387|
|7|Ernst I, Duke of Saxe-Altenburg|0.0158730159|01a184ff-a20d-71bd-816d-4b6b33bdb4ab|01a184ff-a20d-71bd-816d-4b6ce6eb5ac3|b148a18cfb2dcd3a2c171a548df6245037c88c193fc8b2c71717e700c3250867/0/593|
|8|Ernest William Goodpasture|0.015625|01a184b4-9545-76cf-a0c0-1e99029590c3|01a184b4-9545-76cf-a0c0-1e9a56ec89a3|35cf236098a0a8d848fad710b23e5363092ff595e5876f8a89c7a09a6d08919d/0/616|
|9|Prince Waldemar of Prussia (1868–1879)|0.015625|01a1856e-1a68-77b5-b090-8a828805ceac|01a1856e-1a68-77b5-b090-8a83d7ea11d4|89fd2ee553368c5f25da44ac11afdf9777d49bcd2dae06ef1c2559079ab9f646/0/252|
|10|Prince Waldemar of Prussia (1868–1879)|0.0153846154|01a1857e-a267-77c9-ae90-8748265c5c1b|01a1857e-a267-77c9-ae90-87491efd2f51|1371b04375781de225fe156c8b74039163f842e9b7d2a6234a442719f1833131/0/252|
|11|Ernst Ziehm|0.0151515152|01a185bd-5e43-7392-8b9a-ab49ddb65fb7|01a185bd-5e44-7137-b569-04364f288370|b98ec20de9eab92bddc6d3968850c2f0fb8b430b511d902e55eb0e533b869bee/0/665|
|12|Heinrich Ernst Beyrich|0.0149253731|01a184d5-115a-767a-862d-93ee147f1f9a|01a184d5-115a-767a-862d-93efeeaa6a82|fa2dd9847f4c75161f82198b0ace47dd100443773bd75ece3dddb0413fed57a8/0/83|
|13|Sumurun|0.0147058824|01a184f9-8e6f-7196-a000-3e9ba05791f8|01a184f9-8e6f-7196-a000-3e9ca0ed31e6|fb2bcd61e57eb00e3031a70507182bb16b0958f8881f441fed51ee845e611043/0/134|
|14|Friedrich Rosen|0.0147058824|01a185a6-10c3-754b-9be9-13398f078e50|01a185a6-10c3-754b-9be9-133a655c73d1|0cde22a66d3dc520903118e97563cf9e5a142e3c8009fa6f8f098fd3bf151535/0/202|
|15|Friedrich Josias, Prince of Saxe-Coburg and Gotha|0.0144927536|01a184f9-bbd1-7642-bf05-1f0b941e64d1|01a184f9-bbd1-7642-bf05-1f0c51b87431|1cef1a6a53495c02bce27b103aad3f6d77cd223ecca5bbcc57e1316e015e559b/0/266|
|16|Ernest de Bunsen|0.0144927536|01a18512-e650-73eb-908d-9ecd231de77d|01a18512-e650-73eb-908d-9ece5655022b|e6272600c66eb3a38af33ae0529e5d6bd8e8cbbb837621b8b3b0b343d686d4ea/0/298|
|17|Ernst zu Münster|0.0142857143|01a184cc-fc03-7533-8225-d21dc5312aa9|01a184cc-fc03-7533-8225-d21e6adab158|85f62d6d9bd1e6eed012789a5ecbf987a147428761fa09c3f3345bd3ec452fc9/0/185|
|18|Prince Ernst of Hohenberg|0.0142857143|01a18572-d81f-7359-9345-405f9b73c680|01a18572-d81f-7359-9345-4060f2c74547|78c1573c516b3cc6ab7f64f19e377e228c1d8e40e48465f351469253df5a7bac/0/270|
|19|Prince Christian of Hanover (born 1985)|0.014084507|01a184fe-dbc8-7125-842c-98aa288e4380|01a184fe-dbc8-7125-842c-98abd884c40d|d9461adbb19d16632520aa489adf4c53fdd0f0476425afcc112796847e5f77fd/0/496|
|20|Herman Frasch|0.0138888889|01a184f7-161c-74ff-b204-c70f66f635b3|01a184f7-161c-74ff-b204-c710cbaee60e|2ad0ae51e2b1ad621253580b55cfb3cab925c6f7196aeff76940cc93cf5ffc4b/0/205|

Plan buffers: `{"Shared Hit Blocks": 1551281, "Shared Read Blocks": 0, "Temp Read Blocks": 25697, "Temp Written Blocks": 9345}`

Full plan: `.cache/lexical-review/3-hybrid-plan.json`.

## Question 5: Who is the paternal grandfather of James FitzGerald, 8th Earl of Desmond?

Reference answer: James FitzGerald, 6th Earl of Desmond


### bm25

Gold details: `[{"title": "James FitzGerald, 8th Earl of Desmond", "sentence": 0, "text": "James FitzGerald, 8th Earl of Desmond (1459–1487) was the son of Thomas FitzGerald, 7th Earl of Desmond and his wife, Ellice de Barry, daughter of William Barry, 8th Baron Barry, and Ellen de la Roche.", "ranks": [4], "title_only_ranks": [3, 4]}, {"title": "Thomas FitzGerald, 7th Earl of Desmond", "sentence": 0, "text": "Thomas FitzJames FitzGerald, 7th Earl of Desmond (died 1467/68), called 'Thomas of Drogheda', and also known as the Great Earl, was the son of James FitzGerald, 6th Earl of Desmond and Mary de Burgh.", "ranks": [11], "title_only_ranks": [11, 12]}]`

Ranks 1–5 are the top-five list; all rows below form top20. Scores across methods are not comparable. Duplicate titles can represent different exact content/ranges.

|Rank|Title|Score|Document ID|Version ID|Canonical identity (SHA/start/end)|
|---:|---|---:|---|---|---|
|1|Maurice FitzGerald, 9th Earl of Desmond|-39.9915886|01a184f3-e74d-73c8-b9fd-4f08812a80f6|01a184f3-e74d-73c8-b9fd-4f09b20e0929|305c1b4a540fb93dcd2a09414bff054f0dba3d57ea654c168d16692779b71795/0/109|
|2|John FitzGerald, de facto 12th Earl of Desmond|-37.9591794|01a184f3-e700-73e2-940d-9d80bc4d8865|01a184f3-e700-73e2-940d-9d818ce54e5b|58b7ef85cb6b0e0c5efa598ea28ad8f3b60ce02db94ff2feec5f59253fb693f0/0/874|
|3|James FitzGerald, 8th Earl of Desmond|-37.2580743|01a1856d-69a0-74cb-bb3f-539cbc5abf97|01a1856d-69a0-74cb-bb3f-539df87e7ce8|3437a065547186585d173d4d6aef2bf01e669de4cc775c5354f2164da063c073/0/203|
|4|James FitzGerald, 8th Earl of Desmond|-37.2580743|01a1859c-de26-76af-8ce2-7773eaa59585|01a1859c-de26-76af-8ce2-7774ea89ce7d|1e3b78203fb46be16dfa519d2d6ec98b73579b1d121bcd6ffe49965c330a731c/0/201|
|5|James FitzGerald, 6th Earl of Desmond|-31.6720276|01a184f1-6c78-73d8-a4a4-c059c9147e76|01a184f1-6c78-73d8-a4a4-c05adc125c61|00bc0797b630515e451e3495009d09b542ebc135033f8ed143409d348c9111c2/0/198|
|6|James FitzGerald, 6th Earl of Desmond|-31.6720276|01a1856d-69b3-70b8-bdeb-a4f793980e6c|01a1856d-69b3-70b8-bdeb-a4f8fa6be699|2f8913fce53d3ac69210e2612a296b92707df5a4482b4599cbcd9892954cbf8d/0/198|
|7|Thomas FitzGerald, 5th Earl of Desmond|-28.3887799|01a184f3-e739-7745-81e2-43ec61f8541e|01a184f3-e739-7745-81e2-43ed7ab3c0db|7ca4d22c7a8bf1dc97367afab94fafbfdaaa8d8a7673cc4944b5bb7f3c09c90b/0/1004|
|8|James FitzGerald, 1st Earl of Desmond|-28.1494348|01a184f3-ea0e-7346-9236-879fe0feefff|01a184f3-ea0e-7346-9236-87a0734e4a94|343304c3fc7ecaca3b059f25de2776fb0a3789cd4f6ce7789ff238500060e861/0/519|
|9|John FitzGerald, 4th Earl of Desmond|-27.9991865|01a184f3-e8b9-767e-ab3e-e2c058f959d5|01a184f3-e8b9-767e-ab3e-e2c1c365c7a2|3071cb529b5f82dfad57460a7988662977154c5eacdaa3d2fa7a0f0a89adaf55/0/431|
|10|John FitzGerald, 4th Earl of Desmond|-27.9991865|01a1856d-69aa-70ff-95d0-9918dae19946|01a1856d-69aa-70ff-95d0-99199b2ff24d|fe403f914fc261341c81d8a68014e6fb4d3dcb6f9e1990b1ea4b98ae76f34874/0/431|
|11|Thomas FitzGerald, 7th Earl of Desmond|-26.9897168|01a1859c-de2b-717f-88f2-0009a5391a26|01a1859c-de2b-717f-88f2-000a38037f68|cf18dbe3ec7bac19c206b54aa600ffd4d05eeaa55b7ea6247be0466ffba5e748/0/407|
|12|Thomas FitzGerald, 7th Earl of Desmond|-26.6494989|01a1856d-69aa-70ff-95d0-991ac2cf6623|01a1856d-69aa-70ff-95d0-991bf4620ae4|39eb720c3a4cbfd37a0fe5246ac44424dc9515ed8f790c4a7a426abf4f359636/0/408|
|13|Maurice FitzGerald, 2nd Earl of Desmond|-26.3003235|01a184f3-e913-726e-a0d6-c863fa7bcef3|01a184f3-e913-726e-a0d6-c864f6e07d72|7c30968b0952cfefc2d38368c4750f3f992abb54a146933dd241c6f1a5d3eb9c/0/571|
|14|Gerald FitzGerald, 3rd Earl of Desmond|-26.2228308|01a184f3-e715-74ab-baff-0cf2d258c198|01a184f3-e715-74ab-baff-0cf335d40c88|1b0a0df0dd626564553a833fba023186143bb4bd23473fe0ec1ef6b2298ea637/0/740|
|15|Gerald FitzGerald, 3rd Earl of Desmond|-26.2228308|01a185a9-4f6a-70ff-a8a8-83fcd51436c3|01a185a9-4f6a-70ff-a8a8-83fd3f4c14da|8f09f777b41d7faf5a23e664718822180b36ca079fbb84e1f9dc1740f20c4b70/0/744|
|16|James FitzGerald, 14th Earl of Desmond|-26.1592438|01a184f3-e9f8-715a-b19e-ff2dc5b52c34|01a184f3-e9f8-715a-b19e-ff2e14427e77|dbe411075eb0b990dc5b4288a1c4d99b2045fbab43326eaaf23510c50d8cfbf0/0/542|
|17|Thomas Butler, 10th Earl of Ormond|-26.1502848|01a184bd-c8d4-7717-9ad3-3e8ccb8cd2e5|01a184bd-c8d4-7717-9ad3-3e8deee5f0a8|dbcbfe89cd90756dc7b39d711efb8a4e1359e760a80db7667b0731cc4759ae45/0/372|
|18|Thomas Butler, 10th Earl of Ormond|-26.1502848|01a18574-3c8a-708b-8393-e7a73bac0d6e|01a18574-3c8a-708b-8393-e7a879a04ecc|7c83fc97450a572ea37c15ce64561238e42e14ec1ecf5fbe1de829c7e2a40f51/0/371|
|19|John FitzGerald, 1st Baron Desmond|-22.9022689|01a18588-6453-7671-b4d1-df84725233fb|01a18588-6453-7671-b4d1-df853877c073|99e6b15db7cad59d521ed2c7d04976895ffcc83c212f33a0b0b573a7709f2f48/0/2051|
|20|Joan Fitzgerald, Countess of Ormond|-22.395462|01a184f8-141f-77fc-8297-4804c7b9084a|01a184f8-141f-77fc-8297-4805749e5166|ca57709312a5c8dc11740b27dde3c05a1c27ce4d95339ab01f03e11e87f28c99/0/981|

Plan buffers: `{"Shared Hit Blocks": 1505156, "Shared Read Blocks": 0, "Temp Read Blocks": 25697, "Temp Written Blocks": 9345}`

Full plan: `.cache/lexical-review/4-bm25-plan.json`.

### postgres_fts

Gold details: `[{"title": "James FitzGerald, 8th Earl of Desmond", "sentence": 0, "text": "James FitzGerald, 8th Earl of Desmond (1459–1487) was the son of Thomas FitzGerald, 7th Earl of Desmond and his wife, Ellice de Barry, daughter of William Barry, 8th Baron Barry, and Ellen de la Roche.", "ranks": [], "title_only_ranks": []}, {"title": "Thomas FitzGerald, 7th Earl of Desmond", "sentence": 0, "text": "Thomas FitzJames FitzGerald, 7th Earl of Desmond (died 1467/68), called 'Thomas of Drogheda', and also known as the Great Earl, was the son of James FitzGerald, 6th Earl of Desmond and Mary de Burgh.", "ranks": [], "title_only_ranks": []}]`

Ranks 1–5 are the top-five list; all rows below form top20. Scores across methods are not comparable. Duplicate titles can represent different exact content/ranges.

|Rank|Title|Score|Document ID|Version ID|Canonical identity (SHA/start/end)|
|---:|---|---:|---|---|---|

Plan buffers: `{"Shared Hit Blocks": 642629, "Shared Read Blocks": 0, "Temp Read Blocks": 10697, "Temp Written Blocks": 9344}`

Full plan: `.cache/lexical-review/4-postgres_fts-plan.json`.

### hnsw

Gold details: `[{"title": "James FitzGerald, 8th Earl of Desmond", "sentence": 0, "text": "James FitzGerald, 8th Earl of Desmond (1459–1487) was the son of Thomas FitzGerald, 7th Earl of Desmond and his wife, Ellice de Barry, daughter of William Barry, 8th Baron Barry, and Ellen de la Roche.", "ranks": [1], "title_only_ranks": [1, 2]}, {"title": "Thomas FitzGerald, 7th Earl of Desmond", "sentence": 0, "text": "Thomas FitzJames FitzGerald, 7th Earl of Desmond (died 1467/68), called 'Thomas of Drogheda', and also known as the Great Earl, was the son of James FitzGerald, 6th Earl of Desmond and Mary de Burgh.", "ranks": [14], "title_only_ranks": [14, 15]}]`

Ranks 1–5 are the top-five list; all rows below form top20. Scores across methods are not comparable. Duplicate titles can represent different exact content/ranges.

|Rank|Title|Score|Document ID|Version ID|Canonical identity (SHA/start/end)|
|---:|---|---:|---|---|---|
|1|James FitzGerald, 8th Earl of Desmond|0.821520731|01a1859c-de26-76af-8ce2-7773eaa59585|01a1859c-de26-76af-8ce2-7774ea89ce7d|1e3b78203fb46be16dfa519d2d6ec98b73579b1d121bcd6ffe49965c330a731c/0/201|
|2|James FitzGerald, 8th Earl of Desmond|0.820182048|01a1856d-69a0-74cb-bb3f-539cbc5abf97|01a1856d-69a0-74cb-bb3f-539df87e7ce8|3437a065547186585d173d4d6aef2bf01e669de4cc775c5354f2164da063c073/0/203|
|3|John FitzGerald, de facto 12th Earl of Desmond|0.781494102|01a184f3-e700-73e2-940d-9d80bc4d8865|01a184f3-e700-73e2-940d-9d818ce54e5b|58b7ef85cb6b0e0c5efa598ea28ad8f3b60ce02db94ff2feec5f59253fb693f0/0/874|
|4|James FitzGerald, 6th Earl of Desmond|0.779471563|01a1856d-69b3-70b8-bdeb-a4f793980e6c|01a1856d-69b3-70b8-bdeb-a4f8fa6be699|2f8913fce53d3ac69210e2612a296b92707df5a4482b4599cbcd9892954cbf8d/0/198|
|5|James FitzGerald, 6th Earl of Desmond|0.776465647|01a184f1-6c78-73d8-a4a4-c059c9147e76|01a184f1-6c78-73d8-a4a4-c05adc125c61|00bc0797b630515e451e3495009d09b542ebc135033f8ed143409d348c9111c2/0/198|
|6|Maurice FitzGerald, 9th Earl of Desmond|0.77264944|01a184f3-e74d-73c8-b9fd-4f08812a80f6|01a184f3-e74d-73c8-b9fd-4f09b20e0929|305c1b4a540fb93dcd2a09414bff054f0dba3d57ea654c168d16692779b71795/0/109|
|7|Gerald FitzGerald, 3rd Earl of Desmond|0.772232226|01a185a9-4f6a-70ff-a8a8-83fcd51436c3|01a185a9-4f6a-70ff-a8a8-83fd3f4c14da|8f09f777b41d7faf5a23e664718822180b36ca079fbb84e1f9dc1740f20c4b70/0/744|
|8|Gerald FitzGerald, 3rd Earl of Desmond|0.769182169|01a184f3-e715-74ab-baff-0cf2d258c198|01a184f3-e715-74ab-baff-0cf335d40c88|1b0a0df0dd626564553a833fba023186143bb4bd23473fe0ec1ef6b2298ea637/0/740|
|9|James FitzGerald, 14th Earl of Desmond|0.765019915|01a184f3-e9f8-715a-b19e-ff2dc5b52c34|01a184f3-e9f8-715a-b19e-ff2e14427e77|dbe411075eb0b990dc5b4288a1c4d99b2045fbab43326eaaf23510c50d8cfbf0/0/542|
|10|John FitzGerald, 1st Baron Desmond|0.76301908|01a18588-6453-7671-b4d1-df84725233fb|01a18588-6453-7671-b4d1-df853877c073|99e6b15db7cad59d521ed2c7d04976895ffcc83c212f33a0b0b573a7709f2f48/0/2051|
|11|Maurice FitzGerald, 2nd Earl of Desmond|0.751955972|01a184f3-e913-726e-a0d6-c863fa7bcef3|01a184f3-e913-726e-a0d6-c864f6e07d72|7c30968b0952cfefc2d38368c4750f3f992abb54a146933dd241c6f1a5d3eb9c/0/571|
|12|Thomas FitzMaurice FitzGerald|0.750083938|01a18588-6443-7534-b482-c3283d3559d4|01a18588-6443-7534-b482-c329e53e2910|d5257e5585d3243680e2a685b1c146004a0e62ab04a51a83b0092bc30099c870/0/640|
|13|John FitzGerald, 1st Baron Desmond|0.749850228|01a18588-6453-7671-b4d1-df84725233fb|01a18588-6453-7671-b4d1-df853877c073|99e6b15db7cad59d521ed2c7d04976895ffcc83c212f33a0b0b573a7709f2f48/1935/2069|
|14|Thomas FitzGerald, 7th Earl of Desmond|0.74850823|01a1859c-de2b-717f-88f2-0009a5391a26|01a1859c-de2b-717f-88f2-000a38037f68|cf18dbe3ec7bac19c206b54aa600ffd4d05eeaa55b7ea6247be0466ffba5e748/0/407|
|15|Thomas FitzGerald, 7th Earl of Desmond|0.746881469|01a1856d-69aa-70ff-95d0-991ac2cf6623|01a1856d-69aa-70ff-95d0-991bf4620ae4|39eb720c3a4cbfd37a0fe5246ac44424dc9515ed8f790c4a7a426abf4f359636/0/408|
|16|Thomas FitzGerald, 5th Earl of Desmond|0.743501607|01a184f3-e739-7745-81e2-43ec61f8541e|01a184f3-e739-7745-81e2-43ed7ab3c0db|7ca4d22c7a8bf1dc97367afab94fafbfdaaa8d8a7673cc4944b5bb7f3c09c90b/0/1004|
|17|John FitzGerald, 4th Earl of Desmond|0.739918289|01a184f3-e8b9-767e-ab3e-e2c058f959d5|01a184f3-e8b9-767e-ab3e-e2c1c365c7a2|3071cb529b5f82dfad57460a7988662977154c5eacdaa3d2fa7a0f0a89adaf55/0/431|
|18|John FitzGerald, 4th Earl of Desmond|0.736788759|01a1856d-69aa-70ff-95d0-9918dae19946|01a1856d-69aa-70ff-95d0-99199b2ff24d|fe403f914fc261341c81d8a68014e6fb4d3dcb6f9e1990b1ea4b98ae76f34874/0/431|
|19|James FitzGerald, 1st Earl of Desmond|0.734611635|01a184f3-ea0e-7346-9236-879fe0feefff|01a184f3-ea0e-7346-9236-87a0734e4a94|343304c3fc7ecaca3b059f25de2776fb0a3789cd4f6ce7789ff238500060e861/0/519|
|20|Joan Fitzgerald, Countess of Ormond|0.731583185|01a184f8-141f-77fc-8297-4804c7b9084a|01a184f8-141f-77fc-8297-4805749e5166|ca57709312a5c8dc11740b27dde3c05a1c27ce4d95339ab01f03e11e87f28c99/0/981|

Plan buffers: `{"Shared Hit Blocks": 24017, "Shared Read Blocks": 0, "Temp Read Blocks": 0, "Temp Written Blocks": 0}`

Full plan: `.cache/lexical-review/4-hnsw-plan.json`.

### hybrid

Gold details: `[{"title": "James FitzGerald, 8th Earl of Desmond", "sentence": 0, "text": "James FitzGerald, 8th Earl of Desmond (1459–1487) was the son of Thomas FitzGerald, 7th Earl of Desmond and his wife, Ellice de Barry, daughter of William Barry, 8th Baron Barry, and Ellen de la Roche.", "ranks": [1], "title_only_ranks": [1, 3]}, {"title": "Thomas FitzGerald, 7th Earl of Desmond", "sentence": 0, "text": "Thomas FitzJames FitzGerald, 7th Earl of Desmond (died 1467/68), called 'Thomas of Drogheda', and also known as the Great Earl, was the son of James FitzGerald, 6th Earl of Desmond and Mary de Burgh.", "ranks": [12], "title_only_ranks": [12, 15]}]`

Ranks 1–5 are the top-five list; all rows below form top20. Scores across methods are not comparable. Duplicate titles can represent different exact content/ranges.

|Rank|Title|Score|Document ID|Version ID|Canonical identity (SHA/start/end)|
|---:|---|---:|---|---|---|
|1|James FitzGerald, 8th Earl of Desmond|0.0320184426|01a1859c-de26-76af-8ce2-7773eaa59585|01a1859c-de26-76af-8ce2-7774ea89ce7d|1e3b78203fb46be16dfa519d2d6ec98b73579b1d121bcd6ffe49965c330a731c/0/201|
|2|John FitzGerald, de facto 12th Earl of Desmond|0.0320020481|01a184f3-e700-73e2-940d-9d80bc4d8865|01a184f3-e700-73e2-940d-9d818ce54e5b|58b7ef85cb6b0e0c5efa598ea28ad8f3b60ce02db94ff2feec5f59253fb693f0/0/874|
|3|James FitzGerald, 8th Earl of Desmond|0.0320020481|01a1856d-69a0-74cb-bb3f-539cbc5abf97|01a1856d-69a0-74cb-bb3f-539df87e7ce8|3437a065547186585d173d4d6aef2bf01e669de4cc775c5354f2164da063c073/0/203|
|4|Maurice FitzGerald, 9th Earl of Desmond|0.0315449578|01a184f3-e74d-73c8-b9fd-4f08812a80f6|01a184f3-e74d-73c8-b9fd-4f09b20e0929|305c1b4a540fb93dcd2a09414bff054f0dba3d57ea654c168d16692779b71795/0/109|
|5|James FitzGerald, 6th Earl of Desmond|0.0307765152|01a1856d-69b3-70b8-bdeb-a4f793980e6c|01a1856d-69b3-70b8-bdeb-a4f8fa6be699|2f8913fce53d3ac69210e2612a296b92707df5a4482b4599cbcd9892954cbf8d/0/198|
|6|James FitzGerald, 6th Earl of Desmond|0.0307692308|01a184f1-6c78-73d8-a4a4-c059c9147e76|01a184f1-6c78-73d8-a4a4-c05adc125c61|00bc0797b630515e451e3495009d09b542ebc135033f8ed143409d348c9111c2/0/198|
|7|Gerald FitzGerald, 3rd Earl of Desmond|0.0282587065|01a185a9-4f6a-70ff-a8a8-83fcd51436c3|01a185a9-4f6a-70ff-a8a8-83fd3f4c14da|8f09f777b41d7faf5a23e664718822180b36ca079fbb84e1f9dc1740f20c4b70/0/744|
|8|Gerald FitzGerald, 3rd Earl of Desmond|0.0282193959|01a184f3-e715-74ab-baff-0cf2d258c198|01a184f3-e715-74ab-baff-0cf335d40c88|1b0a0df0dd626564553a833fba023186143bb4bd23473fe0ec1ef6b2298ea637/0/740|
|9|Thomas FitzGerald, 5th Earl of Desmond|0.0280832679|01a184f3-e739-7745-81e2-43ec61f8541e|01a184f3-e739-7745-81e2-43ed7ab3c0db|7ca4d22c7a8bf1dc97367afab94fafbfdaaa8d8a7673cc4944b5bb7f3c09c90b/0/1004|
|10|Maurice FitzGerald, 2nd Earl of Desmond|0.0277831372|01a184f3-e913-726e-a0d6-c863fa7bcef3|01a184f3-e913-726e-a0d6-c864f6e07d72|7c30968b0952cfefc2d38368c4750f3f992abb54a146933dd241c6f1a5d3eb9c/0/571|
|11|James FitzGerald, 14th Earl of Desmond|0.0276506484|01a184f3-e9f8-715a-b19e-ff2dc5b52c34|01a184f3-e9f8-715a-b19e-ff2e14427e77|dbe411075eb0b990dc5b4288a1c4d99b2045fbab43326eaaf23510c50d8cfbf0/0/542|
|12|Thomas FitzGerald, 7th Earl of Desmond|0.0275980206|01a1859c-de2b-717f-88f2-0009a5391a26|01a1859c-de2b-717f-88f2-000a38037f68|cf18dbe3ec7bac19c206b54aa600ffd4d05eeaa55b7ea6247be0466ffba5e748/0/407|
|13|John FitzGerald, 4th Earl of Desmond|0.0274797666|01a184f3-e8b9-767e-ab3e-e2c058f959d5|01a184f3-e8b9-767e-ab3e-e2c1c365c7a2|3071cb529b5f82dfad57460a7988662977154c5eacdaa3d2fa7a0f0a89adaf55/0/431|
|14|James FitzGerald, 1st Earl of Desmond|0.0273641102|01a184f3-ea0e-7346-9236-879fe0feefff|01a184f3-ea0e-7346-9236-87a0734e4a94|343304c3fc7ecaca3b059f25de2776fb0a3789cd4f6ce7789ff238500060e861/0/519|
|15|Thomas FitzGerald, 7th Earl of Desmond|0.0272222222|01a1856d-69aa-70ff-95d0-991ac2cf6623|01a1856d-69aa-70ff-95d0-991bf4620ae4|39eb720c3a4cbfd37a0fe5246ac44424dc9515ed8f790c4a7a426abf4f359636/0/408|
|16|John FitzGerald, 4th Earl of Desmond|0.0271062271|01a1856d-69aa-70ff-95d0-9918dae19946|01a1856d-69aa-70ff-95d0-99199b2ff24d|fe403f914fc261341c81d8a68014e6fb4d3dcb6f9e1990b1ea4b98ae76f34874/0/431|
|17|John FitzGerald, 1st Baron Desmond|0.0269439421|01a18588-6453-7671-b4d1-df84725233fb|01a18588-6453-7671-b4d1-df853877c073|99e6b15db7cad59d521ed2c7d04976895ffcc83c212f33a0b0b573a7709f2f48/0/2051|
|18|Joan Fitzgerald, Countess of Ormond|0.025|01a184f8-141f-77fc-8297-4804c7b9084a|01a184f8-141f-77fc-8297-4805749e5166|ca57709312a5c8dc11740b27dde3c05a1c27ce4d95339ab01f03e11e87f28c99/0/981|
|19|Thomas FitzMaurice FitzGerald|0.0138888889|01a18588-6443-7534-b482-c3283d3559d4|01a18588-6443-7534-b482-c329e53e2910|d5257e5585d3243680e2a685b1c146004a0e62ab04a51a83b0092bc30099c870/0/640|
|20|John FitzGerald, 1st Baron Desmond|0.0136986301|01a18588-6453-7671-b4d1-df84725233fb|01a18588-6453-7671-b4d1-df853877c073|99e6b15db7cad59d521ed2c7d04976895ffcc83c212f33a0b0b573a7709f2f48/1935/2069|

Plan buffers: `{"Shared Hit Blocks": 1529173, "Shared Read Blocks": 0, "Temp Read Blocks": 25697, "Temp Written Blocks": 9345}`

Full plan: `.cache/lexical-review/4-hybrid-plan.json`.