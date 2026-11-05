# Pre-deployment review — 5 November 2026

**Decision: NOT READY to deploy.** Repository cleanup is prepared, but live configuration and final container validation remain blocked. No commit, staging, push, deployment, Gemini request, production migration or cloud-resource modification was performed. Current branch: `staging`; reviewed base: `6723f3a`.

## A. Changes to keep

The exact **60-file proposed commit allowlist** is in [release-files.txt](release-files.txt). It contains every proposed path, including this report and the allowlist itself. Do not use `git add .`.

| Files/group | Purpose and justification |
| --- | --- |
| `migrations/env.py`, revisions `0006_validate_chunk_plan_readiness.py`, `0007_membership_owned_embeddings.py`, `0008_chunk_embeddings_hnsw.py` | Enforce readiness invariants, add non-cascading membership-owned vector relationships without removing legacy data, and build/reuse a full-table cosine HNSW index concurrently. Session advisory locking survives Alembic autocommit boundaries and serializes migration runners. |
| `application/ports/indexed_document_repository.py`, `use_cases/ingest_document.py`, `infrastructure/persistence/postgresql_indexed_document_repository.py` | Prepare vectors before a short transaction that registers, persists, validates and activates atomically. Preserve old active versions on failure, exact Content deduplication, embedding reuse and provenance. |
| `application/ports/embedding_errors.py`, `embedding_repository.py`, `use_cases/index_document_version.py` | Pre-registration cache reuse and bounded retry-aware indexing; corrected upper timeout bound includes full accepted Retry-After waits. |
| `infrastructure/persistence/models.py`, `_embedding_mapping.py`, `postgresql_embedding_repository.py`, `postgresql_document_activation_repository.py` | ORM/schema parity, compatible vector reuse, precise lock ordering, activation/readiness checks and bounded readiness backfill. |
| `infrastructure/persistence/postgresql_evidence_repository.py` | Keep exact and opt-in HNSW, candidate-first eligibility, content-scoped compatible keys including noncandidate sibling ranges, MIN compatible configuration selection, hashed complete-key filtering, authorization/current-version/visibility, deterministic deduplication/ranking/provenance and lexical/hybrid behavior. Keep supported membership-owned retrieval. |
| `infrastructure/api/app.py`, `bootstrap.py`, `config.py` | Wire approved atomic uploads/replacements and shared Quick Answer/Investigation HNSW selection. Exact remains the source default. Smoke mode now avoids import-time mandatory DB configuration. |
| Gemini embedding/generation providers, `structured_logging.py` | Keep bounded retries, pooled async clients and low-volume numeric diagnostics. Fine-grained timings remain DEBUG; usage events contain allowlisted model/token/attempt values. No prompts, private excerpts, responses, keys or native thought signatures are logged by these changes. |
| `postgresql_document_repository.py` | Fix the documented shared-plus-owned listing contract while retaining strict private read/mutation authorization. |
| Three operator scripts: `audit_chunk_plan_readiness.py`, `backfill_chunk_plan_readiness_fast.py`, `backfill_membership_chunk_embeddings.py`; existing `backfill_chunk_plan_readiness.py` | Preserve read-only audits, guarded resumable migration tooling and bounded per-document streaming backfill. These are operator tools, not startup jobs. Fix the membership backfill CLI's root import path. Existing generic evaluation/benchmark tools remain; no extra one-off benchmark runner is required. |
| Listed unit/integration tests and fixtures | Keep ingestion rollback/idempotency/concurrency, geometry/blankness/completeness, membership FK/configuration isolation, migrations/recovery/downgrade, exact/HNSW eligibility/provenance, lexical/RRF and diagnostic privacy coverage. Remove only duplicate assertions for the retired experimental variant. |
| `Dockerfile`, `.dockerignore`, `.github/workflows/tests.yml`, `cloudbuild.yaml`, `deploy/runtime.env.example` | Isolate real configuration from builds, supply test-only collection placeholders, retain a non-root runtime and pinned tokenizer, and require branch/preflight/tests/smoke checks before publishing/deploying an immutable digest. Reuse the existing fail-closed deployment helper. No new cloud resource/service is provisioned. |
| README, deployment guide, example configuration, this report/allowlist | Remove contradictory startup/migration claims and document exact defaults, shared opt-in scope, rollout risks and the proposed release. |

Application paths in the table are under `src/investigation_engine/`; exact test/operator/deployment paths are listed in the allowlist.

## B. Removed or simplified

- Retired the failed `membership_owned_canonical` content-plan experiment: removed its duplicated SQL builder and configuration branch. It was not promoted, did not improve latency and was not selected locally or in production. Keep `legacy_shared`, `membership_owned`, exact/HNSW, readiness and full integrity validation. Existing membership-owned safety tests remain; duplicate experimental assertions were removed and chunk-profile coverage now targets the supported backend.
- Archived **22 one-off investigation/benchmark scripts and 15 research reports/results** to ignored `.cache/release-review/archived/`. No information was deleted. `archived-manifest.json` records original locations and SHA-256 values; `before.patch` preserves the tracked pre-review diff. In particular the unvalidated first-search prefetch prototype and temporary driver/SDK timing hooks are outside the release.
- Corrected `maximum_retry_budget_seconds`: exponential-jitter estimates did not cover a valid provider Retry-After at the cap on each retry. The upper bound now uses all attempt timeouts plus the maximum permitted waits. Added regression coverage.
- Replaced raw unexpected Investigation traceback printing with a sanitized error event; added privacy regression coverage. Responses/status remain unchanged by this cleanup.
- Fixed smoke import/lifespan configuration dependency. A credential-free container smoke must not import mandatory production settings solely to decide whether tracing is enabled. Added a fresh-subprocess/no-DB-environment regression.
- Removed redundant persisted-row reads in the indexed repository's idempotent path; inputs/outputs and transaction checks are unchanged.
- Fixed listing to agree with both the existing read authorization policy and documented GET listing contract. Updated the schema test's strict allowed-index set to include the intentionally added indexes, including the new membership table; the metadata/schema comparison remains intact.
- Fixed two confirmed lint findings and required formatting issues, including formatting-only changes to existing diagnostic/tracing helpers. No dependencies or domain redesign were introduced.

Six retained semantic/hybrid combinations were compiled before/after cleanup: legacy exact, legacy HNSW and membership-owned exact. **SQL text and parameters are identical** for all six. A fresh read-only cached-vector staging query returned 20 evidence items and EXPLAIN confirmed both `ix_chunk_embeddings_embedding_hnsw` and the **hashed completeness SubPlan** remain active. This was a release regression check, not another optimization experiment.

Atomic upload/replacement response status `indexed` is the intentional approved ingestion behavior already present in this working tree; cleanup preserves it and the existing endpoint/request shapes. The deprecated experimental environment value is the only removed retrieval selection. Generation model, thinking/output settings, provider request construction and Investigation orchestration remain unchanged.

## C. Do not commit

- Ignored local `.env`, credentials, downloaded tokenizer/data, caches, logs, local DB dumps and generated benchmark results. Local `.env` still opts into HNSW; it does not reach the source defaults or image. `.env` is verified ignored.
- All archived experiments and their raw `.cache` artifacts; no prefetch code is included in the application.
- Unrelated untracked root SQL/JSONL/plan files, `compare_ready_plan_materialization.zip` and its directory, `retrieval_sql_stages/`, `staging-restore.toc`. They were **not moved or deleted**. Their exact names remain recorded in the ignored initial inventory/classification.
- Existing operator/test dummy values are not live credentials. Runtime examples contain no real secrets. No production secret values were printed in this review.

All 82 initial untracked paths were classified: 12 production/operational/test paths are kept, 37 research files are preserved outside the release, and the remaining 33 local/user artifact paths are excluded. The staged set was empty at the start and remains empty. The initial 32 tracked modifications and the additional justified cleanup/configuration/test changes are accounted for in the final allowlist.

## D. Validation results

| Check | Actual result |
| --- | --- |
| Ruff lint and format | PASS across `src tests scripts deploy migrations` (211 Python files in the final formatting check) |
| Complete unit suite, corrected tree | **638 passed** |
| Frontend | **26 passed** using `node --test tests/frontend/test_web.cjs` |
| First complete unit/PostgreSQL run | 865 passed, 2 failed, 4 skipped; identified listing bug and stale schema-index expectation, both corrected |
| Final full PostgreSQL rerun | **Blocked:** all 231 integration setups failed when the Docker engine became unavailable; 4 explicit dataset/live checks skipped. Unit portion still passed. No test was weakened to suppress this environment failure. |
| Migration chain/recovery/concurrency | Passed in the first complete run; no migration implementation changed afterward. Final full rerun remains required. |
| SQL preservation | Six retained query/parameter combinations identical before/after cleanup |
| Actual HNSW plan | Read-only staging confirms HNSW index, hashed complete-key subplan and 20 returned evidence items |
| Secret signatures | No matches in 333 initially reviewed tracked/candidate files; final allowlist rechecked separately. `.env` ignored. This is a scoped scan, not a claim about all Git history or live secret storage. |
| Runtime Docker build | First build succeeded; smoke exposed import-time DB-setting defect, corrected and covered by isolated subprocess tests. **Corrected rebuild failed with Docker RPC EOF; final image/smoke are not validated.** |
| Docker test-stage build | Initial build succeeded. Updated test-only environment and final test image still require validation after Docker recovery. |
| Recovery attempt | Docker engine returned 500/EOF. Non-destructive start reported already running; one bounded Desktop restart timed out. No forced process/container deletion or repeated recovery loop was attempted. |
| Cloud deployment preflight | Read-only validation rejects current live settings: `DB_PASSWORD must be an existing runtime Secret Manager reference` |
| External calls | Zero Gemini API requests; cumulative ledger remains 158/200 |

The smoke regression and deployment guard tests pass offline, but they do not replace a successful corrected container smoke or integration run. Recover Docker, rerun `pytest tests/integration`, rebuild both Docker targets, run `deploy/smoke_image.sh`, and exercise the test image before approving the release. JUnit results, SQL parity, fresh HNSW plan, archive manifest and sanitized scan output are under `.cache/release-review/`.

## E. Deployment readiness and blockers

**NOT READY.** Do not deploy this candidate yet.

1. **Live secrets:** read-only inspection found DB_PASSWORD, GEMINI_API_KEY and ADMIN_API_TOKEN as plain environment values rather than Secret Manager references. Existing invocation IAM is private. The existing deployment guard correctly rejects the secret configuration. Remediate only under a reviewed operator action; no values are included here and no live setting was changed.
2. **Traffic:** the snapshot showed traffic pinned 100% to revision `ai-investigation-engine-00029-992`, while latest ready was `ai-investigation-engine-00030-z9z`. The guard intentionally rejects pinned/split traffic. Review the rollback/serving intent before changing it; this release does not silently route traffic to latest.
3. **Migration/startup budget:** container entrypoint still runs `alembic upgrade head` before Uvicorn. This approved behavior is preserved. Live startup probe permits approximately 240 seconds; 0008's concurrent index build, lock waits or recovery may exceed it. Review production schema/index state and pre-apply reviewed long-running migrations with the intended identity before rollout. No production migration/backfill was executed. Existing valid equivalent full-table cosine indexes are reused; unowned invalid builds require operator review; downgrade removes only demonstrably migration-owned indexes. Avoid simultaneous old writers/large ingestion during a concurrent build when planning the migration window.
4. **Container validation:** restore Docker and finish the corrected integration/image/smoke gates. The new Cloud Build definition was reviewed and unit-tested but has not been executed in Cloud Build; do not call it operationally proven.

Expected runtime behavior: retain the existing production `RETRIEVAL_MODE=hybrid`, `RETRIEVAL_EMBEDDING_BACKEND=legacy_shared`, and **exact** semantic backend unless HNSW is separately selected and quality-validated. The observed service does not explicitly set SEMANTIC_SEARCH_BACKEND, so its default remains exact. Both Quick Answer and Investigation share the backend; lexical/RRF remain unchanged. For an approved HNSW opt-in use ef_search 400, candidate limit 400, iterative scanning false. Keep the configured Gemini model (`gemini-3.5-flash-lite`), serving chunk configuration 1 and existing private authorization scope. No prefetch optimization is present.

The live service snapshot used a 300-second request timeout, concurrency 10 and 512 MiB memory. Multi-batch atomic ingestion can have a computed retry budget beyond 300 seconds; large-upload/timeouts and resource limits need operator/load review. Atomic database rollback preserves the old active version on failure, but external provider work is not transactionally reversible. This review does not introduce queues, new services or a resource increase.

Readiness still proves legacy embedding completeness; membership-owned retrieval additionally checks its own required vectors. No readiness migration extending that promise was added. HNSW's broader quality certification is incomplete (only two compatible cached queries were found), so no default ANN change is justified.

## F. Proposed commit and automation

Suggested message:

```text
feat: finalize atomic ingestion and guarded opt-in HNSW retrieval
```

After the report and remaining validation are approved, the exact proposed staging command is:

```text
git add --pathspec-from-file=docs/validation/release-files.txt
```

This command was **not run**. Review the allowlist and final diff before staging, then inspect the staged diff and secret scan before committing. The production paths and migrations are interdependent; do not omit the new port/repository/use-case or new migrations while staging their callers/tests.

Read-only Cloud Build inspection confirmed the existing trigger `61c02136-b7c3-4edb-8dc2-7d7c5802d395` is enabled, watches `^main$`, uses repository `cloudbuild.yaml`, has no inline build and does **not** require manual approval. A push to `main` automatically starts the deployment pipeline. A push to the current `staging` branch runs GitHub Actions but does not match that Cloud Build deployment trigger.

The proposed repository pipeline validates the main/full-SHA identity and existing live-service policy first, then runs Python formatting/lint/unit/integration checks, frontend checks, runtime build/smoke, publication and immutable-digest deployment through the existing protected-config helper. With today's live settings it is expected to **stop at preflight**, not bypass the blockers. It reuses existing Cloud Build, Artifact Registry, Cloud Run and Cloud SQL resources. There is no hidden default change, schema-only side job, new paid service, commit, push or deployment.
