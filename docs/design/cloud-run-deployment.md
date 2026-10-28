# Production container and Cloud Build deployment

Task 17, inspected 2026-10-28. Cloud Build configuration is prepared; no Git
commit/push, image publication, trigger update, or Cloud Run deployment has been
performed by this task. A real main push and successful production verification
are still required before calling CI/CD operational.

## Existing infrastructure and activation blockers

| Resource | Inspected value |
| --- | --- |
| Project / number | `ai-investigation-engine-512912` / `415474464012` |
| Region | `europe-west3` |
| Cloud Run | `ai-investigation-engine`, container `placeholder-1` |
| Current image / ready revision | `gcr.io/cloudrun/placeholder` / `ai-investigation-engine-00002-sd8` |
| Runtime identity | `415474464012-compute@developer.gserviceaccount.com` |
| CPU / memory / concurrency / timeout | `1000m` / `512Mi` / `10` / `300s` |
| Maximum instances / traffic | `1` / 100% latest revision |
| Artifact Registry | `europe-west3-docker.pkg.dev/ai-investigation-engine-512912/cloud-run-source-deploy` |
| Cloud SQL | `ai-investigation-engine-512912:europe-west3:investigation-postgres`, PostgreSQL 18 |
| GitHub | `rohanjebrown/ai-investigation-engine`, `^main$` |
| Existing global trigger | `61c02136-b7c3-4edb-8dc2-7d7c5802d395` |

The service has the Cloud SQL attachment, but **no application environment
variables or secret references**. Secret Manager listing returned no secrets.
Its current image is Google's placeholder, not this application. The existing
trigger contains inline build/push/deploy steps without the required tests.
Neither the trigger nor runtime has been changed. The deployment preflight
deliberately rejects this incomplete runtime rather than deploying a broken app.

Ingress is `all`, but service IAM has no public invoker binding. Keep IAM
authentication enabled. The existing HTTPS address is
`https://ai-investigation-engine-415474464012.europe-west3.run.app`; it is not
evidence of an application deployment or anonymous public availability.

The local Auth Proxy was unavailable during inspection. Historical validation
reported PostgreSQL 18.6 / pgvector 0.8.5, but current cloud schema, extensions,
serving profile and indexed-record counts have not been verified. Do not copy
the local evaluation database into Cloud SQL or regenerate its embeddings.

## Image design and local checks

`Dockerfile` pins official Python 3.14.9 slim Bookworm and uv 0.11.17 by digest.
It installs the existing lockfile without dev dependencies, then installs the
src package non-editably. The runtime receives only that environment and the
existing checksum-pinned Voyage reference tokenizer, downloaded explicitly at
build time from its pinned public revision. It contains no model weights or
runtime Hugging Face download dependency. No credentials are build inputs.
The separate `test` target adds disposable-test dependencies and fixtures.

The final image runs UID/GID 10001, one Uvicorn process, no shell/reload, binds
`0.0.0.0:$PORT`, and logs to stdout/stderr. The direct Python entry point handles
SIGTERM with an eight-second graceful request deadline. Cloud Run permits ten
seconds before SIGKILL; long foreground investigations can be interrupted and
are not durable jobs. Database/provider resources are closed by lifespan.

`.dockerignore` excludes Git, local virtualenvs, secret files, `.env*` overrides,
caches, raw datasets, model artifacts, logs, reports, backups and IDE state.
The non-secret `.env.example` and tests/scripts/sample fixtures are available
only for the test stage. The runtime copies none of them. `.gcloudignore` adds
similar protections for an eventual explicit local source upload.

From the repository root (Bash; Git Bash on Windows):

```bash
docker build --target runtime -t ai-investigation-engine:local .
bash deploy/smoke_image.sh ai-investigation-engine:local
docker image inspect ai-investigation-engine:local --format '{{.Size}}'
```

The smoke script uses an explicit `--smoke` entry-point argument, **not production
credentials**. It starts with `PORT=9091`, networking disabled, read-only root
filesystem and a 32 MiB writable `/tmp`. It checks health, all browser assets,
non-root UID, ephemeral upload writes, no dev dependencies or local source/data,
no secret environment variables, disabled provider/management routes and exit 0
on SIGTERM. Production is the default; do not add `--smoke` to Cloud Run.

To inspect interactively without provider or database connections:

```bash
docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=33554432 \
  -e PORT=9091 -p 127.0.0.1:9091:9091 ai-investigation-engine:local --smoke
```

`/health` is liveness only. Production lifespan must finish validating the
configured database serving profile and tokenizer before the socket accepts
requests. It does not run migrations, create records or call Gemini at startup.
Production startup necessarily queries the intended database; only explicit
smoke mode isolates all external dependencies.

## Runtime settings, secrets and database

Use `deploy/runtime.env.example` as a **reference list**, filling the existing
database name/user and operator-selected generation model/profile. It is dotenv
syntax, not a `gcloud --env-vars-file` YAML file. Do not apply empty model/profile
values. Keep production model selection explicit; evaluation models are not
production defaults. Preserve the existing app settings and limits in
`.env.example`; investigation remains disabled unless explicitly enabled.

Cloud Run requires `DB_CONNECTION_MODE=cloudsql_socket`,
`DB_HOST=/cloudsql/PROJECT:REGION:INSTANCE`, `DB_PORT=5432`, existing `DB_NAME`
and `DB_USER`. Psycopg appends the PostgreSQL socket suffix. Local development
retains the Auth Proxy at `127.0.0.1:15432`. `K_SERVICE` rejects this development
mode. `sslmode=disable` applies only to local IPC with the managed proxy, whose
connection to Cloud SQL is encrypted; this is not an unencrypted public-IP
database connection. No instance IP/TLS settings are modified.

Defaults bound one instance's SQLAlchemy pool to three connections, zero
overflow, five-second checkout/connect deadlines, 30-second statement deadline,
pre-ping and 30-minute recycle. Corresponding `DB_POOL_*`,
`DB_CONNECT_TIMEOUT_SECONDS`, `DB_MAX_OVERFLOW` and `DB_STATEMENT_TIMEOUT_MS`
are bounded settings. Ordinary deployments retain documents, versions and
embeddings in PostgreSQL; `/tmp` holds bounded temporary uploads only.

Before activation, an operator must verify existing database credentials and
the non-destructive schema state, `vector` extension/version, Alembic revision
and serving configuration. Use a read-only transaction through the Auth Proxy
to record database/version identity and document/embedding counts. If migration
is needed, review `alembic upgrade --sql` and compatibility/backups first, then
run a controlled operator migration with the intended database configuration.
Never migrate from container startup or ordinary Cloud Build deployment. The
pipeline has no production DB credentials and tests cannot use its database.

Create/reuse the intended Secret Manager resources through the operator's
approved secret-entry process. Map `DB_PASSWORD`, `GEMINI_API_KEY`, and
`ADMIN_API_TOKEN` to **numeric secret versions** in Cloud Run; do not put their
values in files, build substitutions, command lines, image layers or logs.
The admin token requires at least 32 ASCII characters. Grant the existing
runtime identity `roles/secretmanager.secretAccessor` only on those secrets and
`roles/cloudsql.client` for the existing project/instance connection. The build
identity receives no secret access. No secret was created, rotated or read here.

Set `FORWARDED_ALLOW_IPS=*` explicitly behind Cloud Run's managed ingress so the
existing HTTPS-only administrator authentication sees the forwarded scheme.
Do not reuse that trust setting on an exposed direct Uvicorn listener. Keep
`ADMIN_REQUIRE_HTTPS=true`, admin-only indexing, visibility filters and all
generation/search/embedding/time budgets. Existing Cloud Run IAM restricts
invocation; per-request budgets and one maximum instance do **not** provide an
aggregate public quota budget. Anonymous public access requires a separate
aggregate rate/quota protection design. Deployment rejects public IAM bindings
and invoker-check bypass. Project-level IAM was also inspected without public
invoker grants; future inherited-policy changes require review.

512 MiB, one CPU and concurrency 10 are preserved, not load-tested guarantees.
Large PDF/DOCX extraction and concurrent investigations need memory/latency
measurement before increasing access. Quick Answer has its existing 90-second
HTTP timeout, investigation defaults to 120 seconds (bounded at 180), within
the retained 300-second Cloud Run timeout. Errors remain sanitized by the API.

## Cloud Build and the existing trigger

`cloudbuild.yaml` executes sequentially, with no allowed-failure steps:

1. Require branch `main` and full 40-character commit SHA.
2. Build the isolated `test` target with BuildKit enabled.
3. Run all Python unit and PostgreSQL integration tests.
4. Run all JavaScript tests with Node 24.
5. Build the production image, tagged with the Git commit SHA; smoke-test it.
6. Verify the existing service's runtime/secret/socket/private-access settings.
7. Push the commit image, resolve its registry digest, then update only the
   existing container's image to `.../ai-investigation-engine@sha256:DIGEST`.
8. Require a ready revision, exact intended image and unchanged protected
   environment, secrets, identity, probes, resources, traffic and annotations.

No `latest` tag, migrations, live AI tests, production test databases or new
service creation occur. Failure before push/deploy stops the pipeline; failed
rollout fails the build. Deployment prints the prior ready revision for rollback
and a configuration fingerprint without logging configuration/secret values.

The integration runner mounts the worker Docker daemon socket, uses host
networking and `TESTCONTAINERS_HOST_OVERRIDE=127.0.0.1`. Tests start their own
pinned PostgreSQL 18/pgvector 0.8.5 container on a random port, run migrations
there, and remove only that disposable database. No local production DB mounts
or live-service flags are passed. This deliberately uses the worker daemon,
rather than assuming nested automatic networking works. The runner requires a
Linux Docker worker supporting socket mounting/host networking. A real Cloud
Build run is still required to establish compatibility with this project's
worker. Failure is fatal, never silently skipped. If policy forbids this setup,
retain the gate and validate an explicit disposable PG service runner before
activating deployment. Existing GitHub Actions tests remain independent PR
checks; no Actions deployment was added.

`deploy/configure_trigger.sh` previews a one-time update. After runtime setup,
local checks, review and an authorized commit containing these files, run:

```bash
bash deploy/configure_trigger.sh
bash deploy/configure_trigger.sh --apply
```

The script verifies/reuses the existing global GitHub trigger and its repository,
sets `cloudbuild.yaml`, watches `^main$`, and uses the proposed keyless
`ai-investigation-build@ai-investigation-engine-512912.iam.gserviceaccount.com`.
It grants Artifact Registry writer **on the existing repository**, Run developer
**on the existing service**, Service Account User **on the runtime identity**,
and Logs Writer on the project with Cloud Logging-only build logs. No new
trigger or long-lived JSON key is created. The operator needs trigger-update,
IAM-policy and service-account creation permissions, and actAs on old/new build
identities. Source-bucket read access may also be needed if the connected GitHub
integration stages source into a bucket; grant Object Viewer on that specific
bucket only if the first build reports that requirement. Do not enable APIs or
change billing as a troubleshooting shortcut.

The current shared compute identity has Editor, Run Admin and other broad roles.
Those pre-existing grants are preserved, not recommended. The proposed dedicated
build identity separates future CI from runtime. Tightening the runtime's old
grants requires an independently reviewed inventory of other workloads.

In Console, edit the identified trigger under Cloud Build → Triggers → global;
verify the existing GitHub connection, repository, push branch regex, config
path and dedicated identity. Do not create a duplicate trigger. Protect main and
review pipeline edits because repository writers can exercise deployment rights.

## First real deployment and rollback

After explicit Git authorization, commit/review and push main normally. Confirm
Build History records that exact GitHub commit and all gates. Inspect the new
ready revision/image digest, preserved service settings, startup logs and
authenticated HTTPS `/health`, `/`, and static assets. Use a permitted invoker
identity (for local inspection, `gcloud run services proxy` is convenient).
Do not disable IAM to make verification easier. This task has not verified an
anonymous public application URL.

Compare read-only document/version/embedding counts and selected-profile IDs
before/after deployment and after a revision restart. Check admin rejection,
authorized document listing and semantic/hybrid retrieval without reindexing.
With operator-selected model and enabled investigation, perform one bounded
Quick Answer and one investigation, check citations and usage, then stop.
These production data/AI checks remain pending; offline smoke is not proof of
Cloud SQL, persisted-data survival or answer quality.

Identify a genuinely successful application revision (the current placeholder
is not one) and inspect only safe fields:

```bash
gcloud run revisions list --service=ai-investigation-engine \
  --project=ai-investigation-engine-512912 --region=europe-west3 \
  --format='table(metadata.name,status.conditions[0].status,spec.containers[0].image)'
gcloud run services update-traffic ai-investigation-engine \
  --project=ai-investigation-engine-512912 --region=europe-west3 \
  --to-revisions=LAST_GOOD_REVISION=100
```

Use the previous ready revision from the build logs, verify health/logs, and
retain previous revisions and commit-tagged images. The commit tag identifies
its immutable digest in Artifact Registry. Diagnose startup via Cloud Run
revision logs (avoid printing environment/secret values); missing secret access,
SQL attachment/permissions/schema/profile or memory exhaustion must be fixed
before retry. A rollback changes traffic to a named revision; this pipeline
deliberately blocks subsequent deployments while such pinned/split traffic is
present. Explicitly restore 100% latest only after recovery review. Image
rollback does not undo a database migration; use backward-compatible schema
changes and a separately reviewed database recovery plan.

## Cost and verification evidence

Billing was already enabled; no billing settings, APIs or paid services were
added. Existing Cloud SQL is `db-perf-optimized-N-8`, zonal: do not assume it is
free or covered by trial credits. Cloud Build minutes, registry storage/network,
Cloud Run execution, Cloud SQL, Secret Manager, logging and Gemini usage can
incur charges. Check current project credits/budgets and official pricing;
free-tier eligibility and credits were not verified. Keep the maximum instance
limit, private invocation and bounded controlled AI verification.

### Task 17 verification record

- **535 Python unit tests passed**, including 27 new configuration, container
  entry-point/smoke-service, tokenizer-integrity and deployment-safety cases.
- **26 JavaScript tests passed**. Cloud Build YAML parsed successfully: ten
  sequential required steps, no allowed failures. Both Bash scripts passed
  syntax checks; trigger preview ran without modifying cloud resources.
- Runtime dependencies installed and the pinned tokenizer downloaded/verified
  in the Docker builder. The first build failed copying into the runtime stage
  when C: filled (`metadata_v2.db: input/output error`). After space was freed,
  Docker remained unavailable. The user explicitly requested finishing without
  Docker. **No final image exists from this verification; image size, successful
  container startup, port/health/assets/non-root/filesystem/SIGTERM checks and
  the containerized PostgreSQL integration suite remain unverified.** Passing
  in-process smoke-service tests do not replace those container checks.
- No PostgreSQL integration tests were run for Task 17 because Docker was
  unavailable; prior milestones' results are not reported as current results.
  No production database test or live Gemini request was made.
- Cloud runtime/trigger/IAM inspection was read-only. Setup scripts are prepared
  but not applied. No image was pushed, no revision deployed, and persisted
  cloud data/retrieval/application behavior across revisions is unverified.
- No files were staged, committed or pushed. Local evaluation data/volumes and
  existing cloud resources were preserved. Actual GitHub-triggered CI/CD is
  **not yet operationally verified**.

Remaining order: complete local Docker and disposable PostgreSQL checks; verify
cloud schema/data/profile and operator runtime selections; configure runtime
secret references and scoped IAM; review/authorize the Git commit; update the
existing trigger; push main with authorization; verify the resulting build,
revision, protected runtime configuration, data persistence and bounded AI calls.

References: [uv Docker guidance](https://docs.astral.sh/uv/guides/integration/docker/),
[Cloud Run container contract](https://docs.cloud.google.com/run/docs/container-contract),
[Cloud SQL PostgreSQL connection](https://docs.cloud.google.com/sql/docs/postgres/connect-run),
[Cloud Build network/schema](https://docs.cloud.google.com/build/docs/build-config-file-schema),
[user-managed build identities](https://docs.cloud.google.com/build/docs/securing-builds/configure-user-specified-service-accounts),
[image-only service update](https://docs.cloud.google.com/sdk/gcloud/reference/run/services/update).
