# Local development traces

Enable explicitly in the local process environment (or its existing `.env`):

```dotenv
APP_ENV=local
DEBUG_TRACE_ENABLED=true
```

Restart the normal local application and send a Quick Answer or Investigation request. No new packages, collector, backend, schema, or API parameters are required. Full tracing defaults to off. `APP_ENV=staging`/`production`, an absent environment, or Cloud Run's `K_SERVICE`, `K_REVISION`, or `CLOUD_RUN_JOB` disables it even if the flag is true. Do not label a deployed environment `local`; the data is private development material, not production telemetry. Existing safe structured logging and OTLP instrumentation are unchanged.

One UTF-8 JSONL file per request is written relative to the working directory under `.debug/traces/<UTC timestamp>-<request ID>.jsonl`. Console lines show only request ID, event name, and duration. Use that ID to correlate existing timing logs. Nothing is added to public response bodies/headers. Both Git and Docker exclude `.debug/`.

Every line has `request_id`, monotonically increasing `sequence`, UTC `timestamp`, elapsed monotonic `elapsed_ms`, `event`, and `data`. Events are flushed incrementally; no full trace accumulates in memory. Example with synthetic data:

```json
{"request_id":"example","sequence":1,"timestamp":"2026-11-06T10:00:00+00:00","elapsed_ms":0.1,"event":"request.start","data":{"request_type":"/investigations"}}
{"request_id":"example","sequence":2,"timestamp":"2026-11-06T10:00:00+00:00","elapsed_ms":0.2,"event":"request.validated","data":{"question":"Which city contains the synthetic museum?","request_type":"investigation"}}
```

Inspect `gemini.request` for the exact adapter kwargs: model, system instructions, conversation parts, tool declarations, generation/thinking/output settings where supplied. `gemini.response` serializes the actual SDK model including all returned candidates, function calls/IDs/arguments, text, finish reasons, and usage. It does not use `.text`. Repeated request events show actual retries; errors record class, SDK code when available, and duration. Existing retry-wait stages capture waits. No provider calls are made to enrich diagnostics.

Inspect `db.execute`/`db.executed`, `db.transaction`, and `db.results` for executed parameterized SQL, all actual bound parameters (including query vectors), execution order, begin/commit/rollback, settings statements, driver times, row counts, and already-fetched retrieval rows. Parameters remain bound normally; no result iterator is consumed for tracing. SQLAlchemy hooks apply only while a local request trace is active. Evidence payloads include text, document/version IDs, offsets, scores, titles and source provenance returned by the existing query.

`retrieval.search`, `retrieval.query_vector`, `retrieval.backend`, `retrieval.hnsw_attempt`, `retrieval.channels`, and `retrieval.selected` explain configuration, searches and admitted rankings. SQL shows lexical expressions and completeness/compatibility filters. RRF contribution events contain the actual rank, smoothing constant, contribution, accumulated score and duplicate occurrence keys. Answer context and decision events show selected evidence and citation validation.

For Investigation inspect, in order:

1. `investigation.turn`: question/history, admitted evidence and remaining budgets.
2. Gemini request/response and `investigation.action`: the actual tool call or model decision.
3. Retrieval events and `investigation.evidence_state`: search results, evidence registry, disposition and new/reused counts.
4. `investigation.completed`: actual application stop reason (search/generation/embedding/context/turn limit, repeated query, or final-only search). `investigation.decision` marks a model-selected conclusion and successful citation validation. A model's `insufficient_evidence` is a decision, not proof that retrieval found nothing. No hidden explanation is invented.

`stage.start`/`stage.end` reuse existing timings for token counting, query embeddings, acquisition, provider calls, payload construction, validation, evidence hydration, selection, RRF and total operations. New `result_fetch` measures `.mappings().all()` on the existing buffered result. Timings are **inclusive/nested**; do not add parent and child durations. Driver execution may include transfer and decoding; it is not PostgreSQL server-only execution. Request-end elapsed time includes diagnostic overhead.

## Deliberate limits and security

- No additional SQL, EXPLAIN, provider, or authentication calls. PostgreSQL-internal ANN candidates, rejected rows, per-predicate rejection explanations, server-only timings, and internal model reasoning are unavailable. Candidate budgets and admitted results are captured, not a fabricated list of all visited graph nodes. Cosine distances can be derived from returned cosine similarity as `1 - similarity`.
- SDK-visible objects only; HTTP wire encoding and transport headers are not captured. Opaque thought signatures are redacted. Tool IDs remain visible.
- Authentication headers/cookies, credential-named fields, known credential environment values, bearer strings and credential-bearing URLs are excluded/redacted. Unknown objects are represented by type, not unsafe `repr`. Avoid putting arbitrary secrets in free text: no generic sanitizer can recognize every possible unlabeled secret. Full payload tracing is unsuitable for sensitive real user material.
- Valid JSON request/response bodies are captured passively. Malformed/split transport JSON fragments record byte counts rather than potentially unredactable raw fragments; the validated question is recorded separately. No unbounded transport-body buffer is introduced.
- Trace I/O/serialization failures do not fail requests; partial/missing files are possible on disk errors. There is no automatic retention/deletion. Restrict local directory access, monitor disk use, and remove local traces when finished. Full vectors/content and synchronous serialization/file flushes increase disk use and latency **only when explicitly tracing locally**. Disable tracing for performance benchmarks.
- With tracing disabled, payload serialization/file I/O/console output is skipped. Passive hooks and context checks remain; provider calls take a direct fast path. The implementation does not change retrieval algorithms, prompts, provider kwargs, Investigation decisions, or API contracts.

## Implementation and validation (6 November 2026)

Changed files:

| Paths (relative to repository root) | Change |
| --- | --- |
| `src/investigation_engine/application/debug_trace.py` | Central local gate, serializer/redactor, incremental request writer, SDK-call tap |
| `src/investigation_engine/infrastructure/observability/debug_trace.py` | Passive ASGI bodies/configuration and SQLAlchemy execution/transaction taps |
| `src/investigation_engine/application/timing.py` | Mirror existing timing/operational events into the active local request |
| `src/investigation_engine/application/investigations.py` | Turn budgets/state, actual actions, evidence registry, model decision and citation outcome |
| `src/investigation_engine/application/rrf.py` | Actual contributions, duplicates and fused ranking |
| `src/investigation_engine/application/use_cases/search_evidence.py`, `answer_question.py` | Search configuration/vector, channel results, selection and generation context/decision |
| `src/investigation_engine/infrastructure/api/app.py` | Register middleware and record validated questions |
| `src/investigation_engine/infrastructure/generation/gemini_answer_generator.py`, `gemini_investigation_model.py` | Wrap the unchanged SDK request kwargs/actual returned models |
| `src/investigation_engine/infrastructure/embeddings/gemini_provider.py` | Same tap for token counting and embedding calls |
| `src/investigation_engine/infrastructure/persistence/postgresql_evidence_repository.py` | Existing fetched rows, buffered result-fetch timing and effective HNSW attempt settings; SQL builders unchanged |
| `tests/unit/infrastructure/test_debug_trace.py` | 14 synthetic tracing, credential, SDK parts, SQL, API, concurrency, failure and termination checks |
| `tests/integration/infrastructure/persistence/test_semantic_retrieval.py` | Real PostgreSQL trace-on/off evidence/provenance parity |
| `.env.example`, `.gitignore`, `.dockerignore` | Disabled configuration example and local artifact exclusions |
| `docs/design/local-development-tracing.md` | This guide and validation record |

Results:

- Tracing-specific tests: **14 passed** on final implementation.
- Relevant unit suite (application, APIs, providers, timing, tracing): **305 passed**.
- Complete unit suite: **651 passed, 1 failed**. The unrelated existing `test_cloud_build_blocks_publishing_until_all_quality_and_runtime_gates_pass` expects `ValidateCommit` in the current `cloudbuild.yaml`; that build definition has no such step. Neither deployment file nor that test was changed.
- PostgreSQL semantic/HNSW, hybrid, HTTP RAG and new tracing integration tests: **65 passed**, 104.32 seconds, disposable Testcontainers only, synthetic embeddings/providers.
- Ruff lint/format passed on all changed Python files. Whole-project checks identify the pre-existing unused `sqlalchemy.or_` import and redundant parentheses/formatting in untouched `postgresql_document_repository.py`; these were left intact to preserve unrelated changes. Diff whitespace checks passed.
- No Gemini API requests, schema/staging data changes, new dependencies, commits, pushes or deployment. Docker Desktop was started normally to run disposable tests.

JUnit artifacts: `.cache/tracing-final-unit.xml`, `.cache/tracing-relevant-unit.xml`, `.cache/tracing-integration.xml`. The SDK deprecation warning and optional local OTLP collector shutdown warning remain unrelated to full payload tracing.
