# Structured logging increment

The Cloud Run entrypoint sends the existing application timing logger through the existing allowlisted JSON formatter. `request_id` is generated locally and propagated with a context variable; it is also the correlation identifier for an investigation executed within that request. It is not a Cloud Trace ID or a separately generated investigation ID. Events include `severity`, `event`, `operation`, `request_id`, and `succeeded`; `duration_ms` is present only when measured. Only explicitly allowlisted fields are serialized. Do not include user text, documents, tokens, SQL, vectors or exception messages in event fields.

| Event | Severity | Purpose |
|---|---|---|
| `operation.completed` | INFO for totals and HTTP; DEBUG for individual stages | Wall-clock elapsed time in the named scope. |
| `operation.failed` | WARNING | A measured scope raised; error category identifies repository, provider, invalid response or internal failure; no exception message is serialized. |
| `retrieval.completed` | INFO | Selected mode and channel/final result counts; elapsed time starts after validation. |
| `answer.evidence_deduplicated` | INFO | Number of exact-range duplicates removed across retrieved branches. |
| `answer.evidence_selected` | INFO | Selected count and serialized generation-context size/budget. |
| `answer.abstained` | INFO | No evidence was returned by retrieval. |
| `answer.context_exhausted` | INFO | No intact evidence fits the context budget. |
| `investigation.started` | INFO | Start of the bounded investigation. |
| `investigation.turn_started` | INFO | Turn number, final-only flag, evidence and serialized context size. |
| `investigation.limit_reached` | INFO | Evidence, search or embedding budget requires a final-only turn. |
| `investigation.turn_completed` | INFO | Provider turn elapsed time and reported token usage. |
| `investigation.search_completed` | INFO | Search duration, result/new/reused evidence counts, accumulated evidence and context size, disposition. |
| `investigation.completed` | INFO | Answered, abstained, conflicting or incomplete result, with request counts and, for model decisions, provider token usage. Incomplete results include an allowlisted reason. |
| `investigation.citation_rejected` | WARNING | Citation validation rejected a model decision; no response text is logged. |

Example: `{"severity":"INFO","event":"investigation.search_completed","request_id":"...","operation":"investigation.search_completed","duration_ms":123.4,"succeeded":true,"search_number":1,"result_count":5,"new_evidence_count":4,"reused_evidence_count":1,"evidence_count":4,"context_chars":1200,"context_limit":24000,"status":"succeeded"}`.

To trace a request, filter on `request_id`. Set `ENGINE_LOG_LEVEL=DEBUG` for detailed stage timings; INFO is the default. Timed SQL roundtrips include database execution, network transfer and driver materialization. Hybrid runs both channels in one statement; independent application-side semantic and lexical SQL durations cannot be inferred from that statement. The benchmark explicitly enables DEBUG to capture its stage samples. Previous real Cloud SQL and local synthetic measurements are documented in `logging-cloud-review-2026-10-29.md` and `retrieval-correctness-2026-10-29.md`; this increment made no live measurements.

## Known gaps

This is not yet the complete requested cross-application logging strategy. Existing answer, provider and API loggers remain outside the allowlisted JSON logger. Failures have broad categories but nested scopes may produce duplicate warnings. Unexpected internal stack traces have not been redesigned for safe collection. Separate investigation IDs, actual SQL-side duplicate/exclusion counts, HTTP status codes and authoritative provider request counts beyond the existing investigation result are not recorded. INFO per-turn and search events should be assessed against production log volume before rollout. Do not infer actual zero values for omitted metrics.

The current representative selection runs inside SQL before channel limits, so its removed duplicate count cannot be measured from returned rows without changing the query or executing an additional query. This increment deliberately does neither. Similarly, candidate counts in `retrieval.completed` are counts returned from each channel after eligibility and channel limits, not all candidates examined by PostgreSQL.

The feature changes no domain model, database schema or production resources. The formatter and Cloud Run logging configuration change require a separately authorized application deployment.

## Verification

The full unit suite passed 605 tests after the final changes. The disposable PostgreSQL suite passed 168 tests with four opt-in skips after the investigation event changes; the final changes to answer logging and error categorization were subsequently checked by focused unit tests. Ruff lint and formatting pass. WPS reports 3,190 findings versus 3,178 at the start of this iteration. The increase includes the new event handling and expanded control flow; existing violations were not suppressed or broadly refactored. This remains a regression against the requested no-new-findings goal and needs follow-up.
