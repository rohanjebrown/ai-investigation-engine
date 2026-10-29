# Completion verification — 29 October 2026

> Correction (29 October 2026): The user explicitly confirmed automatic migrations. Normal container startup runs `alembic upgrade head` before starting the API and exits if migration fails. Explicit `--smoke` skips migrations. Earlier statements below describing removal/manual-only startup are superseded. Restoring this code does not execute a production migration.

## Final evidence

| Check | Result |
| --- | --- |
| Ruff format / lint | Pass for src, tests, scripts, deploy and migrations; CI checks without rewriting |
| Python unit suite | 616 passed on Windows; includes evaluation and investigation regressions |
| Linux container unit suite | 615 passed before addition of the provider-telemetry report regression; current tests/deploy mounted read-only |
| Disposable PostgreSQL integration | 174 passed, 4 explicitly skipped opt-in cases, 292.61 seconds |
| JavaScript | 26 passed |
| Nested Docker/PostgreSQL path | 5 BM25 integration tests passed on Linux with Docker Desktop host gateway |
| Production image | Build succeeded, 156,653,926 bytes (156.65 MB decimal; local Docker size) |
| Isolated runtime smoke | Health, assets, PORT 9091, UID 10001, ephemeral uploads, absent dev dependencies/secrets, networking disabled, read-only root filesystem, SIGTERM exit 0 |
| Cloud trigger | Existing global trigger now reads cloudbuild.yaml; inline build/deploy absent; no regional duplicate found in europe-west3 |
| Actual Cloud Build run / deployment | Not executed; no Git push authorized |
| Config/link/whitespace checks | 12 sequential mandatory Cloud Build gates validated; Bash syntax, submission local links and git diff --check passed |
| Production data | Not modified, queried for evaluation or used by tests |

The final normal Python suites total **790 passing tests** plus four opt-in
integration skips. Repeated Linux/targeted runs are not additional unique tests.
The skips are the dataset test and three live/browser investigation checks;
ordinary tests never enable `--run-external` or `--run-dataset`. The upstream
Google Gen AI typing deprecation warning remains.

Post-evaluation read-only attestation again confirmed 218 documents, 197 vectors,
218 active versions and migration 0002. The frozen report contains 74 observed
outcomes and preserves the original gold/corpus fingerprints.

## Failures found and resolved

- Initial full run: 613 passed, 4 skipped, 171 setup errors and 2 migration-test
  failures. The default pgvector image lacked pg_textsearch, now required by the
  existing BM25 tests; two migration assertions still expected head 0002.
- A test-only image now installs checksum-pinned pg_textsearch 1.5.1 on pinned
  PostgreSQL 18/pgvector 0.8.5. The integration fixture always creates its own
  random-port container. The explicit settings-based external test-database
  override was removed, preventing CI from using application DB credentials.
- Intermediate integration run: 173 passed, 4 skipped, one schema assertion
  failed because the migration-owned BM25 index is outside ORM metadata. The
  test now explicitly validates its definition and excludes exactly that named
  reflected index from the general metadata comparison. No schema was changed.
- Old startup tests expected automatic Alembic upgrade. They were updated to
  assert no subprocess migration occurs and the process execs the API. Pending
  schema changes remain explicit operator work; no production migration ran.
- Linux pytest collection rejected a None event-loop factory mapping; the hook
  now returns an explicit asyncio loop factory. Host-network/127.0.0.1 nested
  Docker failed locally because Docker Desktop host networking is unavailable;
  bridge/host.docker.internal succeeded. The Cloud Build Linux worker path is
  configured but still needs an actual build verification; failures block deploy.
- Report rendering initially failed when a provider-failure row had unavailable
  usage counters. It now sums observed counters without crashing, labels absent
  metadata, and retains the failed row. A deterministic regression covers this.

## CI and production state

The only discovered deployment trigger is global
`61c02136-b7c3-4edb-8dc2-7d7c5802d395` for
`rohanjebrown/ai-investigation-engine`, `^main$`. Previously its inline workflow
built, pushed and deployed without tests. The CLI oneof update failed with
INVALID_ARGUMENT; a narrow REST `build,filename` field-mask update succeeded.
Readback verified `filename=cloudbuild.yaml`, no inline build and unchanged
GitHub/identity. The regional europe-west3 listing had no triggers.

The existing trigger now uses the repository file. The working tree's Ruff and
test-image additions require an authorized commit/push before they are available
to GitHub builds. No source was staged, committed or pushed; no Cloud Build run,
image upload or Cloud Run deployment was started. A failing required step has no
allowFailure/allowExitCodes escape; sequential execution prevents push/deploy.
Actual end-to-end gating remains unverified until a real main push succeeds.

Existing runtime revision `ai-investigation-engine-00029-992` uses commit
`0157442fd57b594580ec88e44335a78463683e11`, generation model
`gemini-3.5-flash-lite`, Cloud SQL socket mode and profile 1. Runtime settings,
database connectivity, secrets and IAM were preserved. Plaintext secret env
values and disabled invoker IAM are current blockers rejected by the prepared
preflight; their values were redacted during inspection and never logged here.
The existing shared compute build/runtime identity remains overprivileged.
Scoped keyless build-IAM setup is prepared, not applied. These production
restrictions need operator review rather than weakening the preflight.

## Reliability and live evaluation

Quick Answer received source-independent identity and scoped-contradiction
guidance. Investigation guidance now checks common entity/time/scope and seeks
missing facts while budget remains. The decision validator rejects identical
claims presented as a conflict. Controlled same-name fixtures verify identity
follow-up context, unchanged provenance, trusted ownership and answered versus
insufficient outcomes. Existing dependent-search, missing-evidence, conflict,
budget, invalid-citation and provider-failure tests remain passing. Fake models
do not establish that Gemini obeys a prompt or that conclusions are entailed.

The existing runner selected all 40 frozen questions and used the same local
database/model/profile/ranking in both modes. The source database was attested
at 218 documents/versions, 197 real 768-dimensional embeddings and 218 active
searchable versions, migration 0002; it was not migrated or re-embedded.

The operator reported 15 RPM / 250k TPM / 500 RPD. Remaining quota and other
project use are not API-readable; zero prior attempts means zero known attempts
in this evaluation process, not independently verified account-wide usage.
Requests were paced at least 4.2 seconds apart with no generation retries,
a 300-attempt hard cap and 240-query-embedding cap. Token totals stayed below
the configured output/context bounds; no paid Batch API or billing changes ran.

The run persisted 37 attempts per mode (74/80 outcomes), with **36 pairs without
provider failure**. At investigation request 100, HTTP 503 UNAVAILABLE stopped
the run; it was availability, not observed quota exhaustion. No rerun followed.
Three questions remain unrun in both modes. Total generation attempts: **137**
(37 Quick Answer, 100 investigation turns). There were **104 query embeddings**,
with 104 countTokens plus 104 embedContent operations. No document embedding
was regenerated. Recorded tokens: Quick Answer 38,794 prompt / 1,717 candidate;
investigation 307,956 prompt / 3,759 candidate; thinking counts reported zero.
Failed requests may omit usage, so those sums are observed metadata only.

See the [consolidated evaluation](submission-evaluation.md) and
[frozen machine-readable results](submission-answer-quality.json) for outcomes,
all category metrics, citation coverage, paired deltas, search traces, latency
and per-case failure analysis. Two answers have explicit result-bound Codex
source audits; the rest are not silently marked semantically supported.
Historical reports and failed-run logs are preserved. No prompt was changed
after seeing the new run's outcomes.

## Documentation and remaining requirements

README now describes the current architecture, formats, lifecycle, setup,
retrieval/investigation trade-offs, exact corpus scopes, tests, Docker and
deployment limitations. AI_USAGE.md distinguishes developer decisions, AI
assistance, executed checks and unverified claims. The deployment guide and
decision register distinguish dated historical records from current evidence.

Remaining before submission can claim completion:

1. Operator review of runtime secrets, public invocation/aggregate quota
   protection and scoped build identity; preserve the existing service/data.
2. Explicit authorization to commit/push, then verify the actual Cloud Build
   worker, all gates, pushed digest, ready revision, preserved settings and
   application behavior/data persistence. No new service/database is needed.
3. Three remaining paired questions require a later explicitly budgeted run;
   preserve the HTTP 503 attempt and do not rerun failures to improve results.
4. Independent human semantic/entity grounding review and held-out controls
   remain necessary for strong reliability claims. Current benchmark gains are
   descriptive workflow results with different context budgets, not a causal
   proof or production guarantee.

Files changed include Cloud Build/GitHub checks, the test-only PG Dockerfile,
trigger update helper, startup entrypoint, two historical migration files
(format/import changes only), the existing evaluation runner, generation
guidance/conflict validation, targeted test fixtures/regressions and submission
documentation/report artifacts. No storage policy, gold labels, embedding
model/dimension, RRF, production records or Git history was changed.
