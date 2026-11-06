# Optional Voyage reranking

Voyage `rerank-3` is an optional ordering step, disabled by default. The adapter uses the existing `httpx` dependency and the [Voyage REST endpoint](https://docs.voyageai.com/reference/reranker-api), `POST https://api.voyageai.com/v1/rerank`. Model availability and the all-candidates behavior are described in the [Voyage reranker guide](https://docs.voyageai.com/docs/reranker). No SDK, local model, new dependency, schema, migration or indexing change is required.

## Integration and candidate limits

The shared `SearchEvidence.execute` hybrid path is:

```text
Eligible semantic + lexical candidates
  → existing occurrence deduplication and unchanged RRF
  → optional Voyage ordering
  → requested evidence limit
  → existing workflow context admission and citations
```

The existing repositories keep their visibility/owner, active-version, complete-plan/profile, exact-content diversity and provenance checks. HNSW and BM25 scoring/SQL are unchanged. Per-channel `SEMANTIC_CANDIDATE_LIMIT` and `KEYWORD_CANDIDATE_LIMIT` still bound retrieval. With reranking enabled, RRF receives `limit=None`, exposing the entire deduplicated union (at most 200 candidates for the current 100-per-channel settings bounds), rather than discarding everything after the requested five. RRF contributions, formulas and deterministic ties are unchanged. This is the only hybrid truncation change; no SQL change was needed.

In semantic-only mode, enabled reranking requests `max(requested_limit, SEMANTIC_CANDIDATE_LIMIT)` from the unchanged semantic repository. Disabled mode retains the original requested limit. The explicit lexical-only `execute_keywords` diagnostic route retains its existing behavior.

`AnswerQuestion` (Quick Answer) and `InvestigateQuestion` already call this shared search. Both application lifespan factories in `infrastructure/api/bootstrap.py` inject the optional adapter; `application_service` shares one search/adapter/request budget between both workflows. Each Investigation search passes its active query. The accumulated evidence registry is never passed to the reranker or reordered; admitted citation IDs and evidence remain under the existing orchestration. A passage retrieved again as part of a later search remains subject to the existing registry reuse rules. Prompts, orchestration, context/count limits and citation validation are unchanged.

`EvidenceReranker.rerank(query, candidates)` returns `RerankedEvidence` wrappers with the original candidate objects and separate relevance scores. Voyage sees only query text and document title plus passage text, with `model=rerank-3` and `truncation=false`. The API credential travels solely in the required Authorization header. IDs, hashes, vectors, source metadata, offsets and citation identifiers are not in the request body. Provider indexes map back onto the original objects. `Evidence.score`, `rank`, channel scores/ranks, text, offsets and provenance are never overwritten; a first-position reranked result can still have original RRF `rank=9`.

## Configuration and free usage

Use the existing settings/.env conventions; see `.env.example`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `RERANKER_BACKEND` | `none` | Explicitly select `voyage` to enable |
| `RERANKER_MODEL` | `rerank-3` | Only the requested model is accepted |
| `VOYAGE_API_KEY` | empty | Single credential setting, excluded from settings repr |
| `RERANKER_TIMEOUT_SECONDS` | `10` | Total outbound request deadline and HTTP timeout; 0.1–60 seconds |
| `RERANKER_MAX_REQUESTS` | `10` | Adapter lifespan request ceiling, 0–100; zero prevents requests |

Before enabling live requests, **verify the provider account's billing configuration and remaining free allowance**. Application counters cannot guarantee zero billing. The ceiling counts attempts, including failures and cancellations that may already have reached Voyage. It resets at process/lifespan restart, is separate for each worker/replica, and does not coordinate other tools using the same account. No provider token-count call or exact tokenizer-based spending estimate is introduced. Returned `usage.total_tokens` is recorded, rather than estimated. Keep the backend `none` until those account checks are complete; set the ceiling to `1` for the first controlled test.

Identical query and ordered title/passage inputs share cached index/score results within the adapter lifespan, including concurrent calls. Only input digests, indexes, scores and safe error categories are cached; original evidence/provenance is remapped for the current caller. Cache size cannot exceed the request ceiling. Failed or cancelled inputs are also remembered, preventing automatic repeats of uncertain charges. Cache replays generate no extra usage event. There are no retries or redirects. Distinct inputs share an async lock and the same budget.

## Failure and fallback

Disabled configuration creates no Voyage adapter/client. Empty pools generate no request. Missing credentials, exhausted budget, HTTP errors (including 401/429/5xx), timeouts, transport errors, malformed JSON, unexpected object/model/usage structures, empty/incomplete results, invalid/duplicate/out-of-range indexes and nonnumeric/nonfinite/out-of-range relevance scores raise a safe `RerankingError`. The shared search catches that error and selects from the original RRF order (original semantic order for semantic-only retrieval). It never partially accepts a malformed ranking. External cancellation propagates to preserve the Investigation/request deadline; it is not converted into a successful fallback response.

Safe logs distinguish `rerank.fallback` reasons and `rerank.provider_completed` model, duration and returned numeric `total_tokens`. No exception bodies, provider response text, queries, credentials or evidence payloads enter production logging. Oversized provider inputs are rejected with fallback rather than silently truncated or altering passage content.

## Local tracing

The existing `APP_ENV=local`, `DEBUG_TRACE_ENABLED=true` gate and Cloud Run exclusions remain in force. JSONL files stay under `.debug/traces/`, with existing redaction and console restrictions. See [local tracing](local-development-tracing.md).

- `rerank.candidates`: active query, candidate identities, original semantic/keyword ranks and scores, RRF ranks/scores, whole passages/provenance.
- `rerank.configuration` / `rerank.cache`: Voyage model, remaining request budget and cache use.
- `voyage.request` / `voyage.response`: text-only request, model, request duration, actual usage and safe errors. Headers and raw response bodies are never recorded.
- `rerank.ranking`: identity, original position, reranked position and separate relevance score.
- `rerank.selection` / `rerank.fallback`: final selected passages, whether fallback was used and its reason, including disabled/empty cases.
- Existing `answer.context` and `investigation.evidence_state`: final workflow context admission and citation identities after existing context limits.

## Deterministic verification

```powershell
uv run pytest tests/unit/application/test_reranking.py tests/unit/infrastructure/test_voyage_reranker.py tests/unit/infrastructure/test_rag_bootstrap.py tests/unit/infrastructure/test_config.py -q
```

Unit tests block outbound connections and mock the Voyage HTTP transport. Coverage includes ordering and original-object/provenance preservation, full candidate union including 200 candidates, top-five selection, channel duplicates and distinct source occurrences, empty/disabled paths, malformed responses/indexes/scores, timeout and transport/HTTP failures, fallback, credentials/budget, concurrent duplicate suppression, cancellation, lifecycle cleanup, Quick Answer and multi-search Investigation citations, and local trace rank/usage mappings.

The Princess Alexandra regression uses a controlled synthetic channel arrangement and the Caroline/Rainier III passage from the example: semantic rank four, RRF rank nine with score `1/64`. The mock promotes it to first position and checks that it survives final top-five selection. This verifies candidate availability and plumbing; it does **not** predict whether the real Voyage model will promote that passage.

Final implementation validation (6 November 2026): **131 targeted tests passed, one external smoke test skipped** without opt-in; **733 full-suite unit tests passed, one unrelated existing failure** in `test_cloud_build_blocks_publishing_until_all_quality_and_runtime_gates_pass`, which expects `ValidateCommit` in the unchanged `cloudbuild.yaml`. JUnit results: `.cache/reranking-targeted.xml` and `.cache/reranking-unit.xml`. The changed Python files pass Ruff lint/format checks. No comprehensive benchmarks, database changes, live Gemini/Voyage requests, commits or deployments were performed.

## Files changed

Paths below are relative to the repository root:

| Path | Change |
| --- | --- |
| `src/investigation_engine/application/ports/evidence_reranker.py` | Minimal reranker protocol, original-evidence wrapper and safe failure type |
| `src/investigation_engine/infrastructure/reranking/__init__.py` | Adapter package |
| `src/investigation_engine/infrastructure/reranking/voyage.py` | Async HTTP adapter, validation, index mapping, timeout, budget, duplicate suppression, usage diagnostics |
| `src/investigation_engine/application/use_cases/search_evidence.py` | Shared rerank-before-limit integration, fallback and local ranking/selection tracing |
| `src/investigation_engine/application/rrf.py` | Optional full-union output limit; scoring unchanged |
| `src/investigation_engine/infrastructure/api/bootstrap.py` | Optional adapter lifecycle/composition in both service factories |
| `src/investigation_engine/infrastructure/config.py` | Disabled defaults and validated settings, one hidden Voyage credential |
| `src/investigation_engine/infrastructure/structured_logging.py` | Numeric `total_tokens` allowlist entry |
| `.env.example` | Disabled configuration and request-budget/billing notes |
| `tests/unit/application/test_reranking.py` | Shared workflow, Princess Alexandra, citation, full-union, fallback and trace regressions |
| `tests/unit/infrastructure/test_voyage_reranker.py` | Mocked HTTP contract, malformed responses, failures, timeout, budget/cache and cancellation tests |
| `tests/unit/infrastructure/test_rag_bootstrap.py` | Disabled construction and enabled shared-resource/lifecycle tests |
| `tests/unit/infrastructure/test_config.py` | Defaults, credential repr exclusion and invalid settings tests |
| `tests/external/reranking/test_voyage_reranker.py` | Separately gated, one-request synthetic live smoke test; not run live during implementation |
| `docs/design/local-development-tracing.md` | Reranking events and production-safe logging boundaries |
| `docs/design/voyage-reranking.md` | This implementation/configuration/validation report and controlled smoke instructions |

## One controlled live test, after implementation

After verifying billing settings/free allowance, set `VOYAGE_API_KEY` securely in the current process environment. The smoke test does not load `.env` or application/database settings. In a fresh PowerShell session in the repository:

```powershell
$env:RERANKER_BACKEND = "voyage"
# VOYAGE_API_KEY must already be set securely; do not paste credentials into tracked files.
uv run pytest tests/external/reranking/test_voyage_reranker.py --run-external -q --log-cli-level=INFO
Remove-Item Env:RERANKER_BACKEND
```

This invokes only Voyage, once, using two tiny synthetic passages and `rerank-3`, with a hardcoded one-request budget, ten-second timeout and no retries. It checks complete original-object mapping and score ordering without assuming a particular real-model result. An absent key/backend or omitted `--run-external` skips the test without network access. `rerank.provider_completed` logs returned usage. Optional local tracing can be enabled with the usual flags before running. Do not start the HTTP application for this smoke test; that would also initialize the Gemini workflows. Review provider usage afterward and keep the application backend disabled until deliberately enabling it.
