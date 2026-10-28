# Single-step RAG and shared-corpus HTTP API

**Status:** CONFIRMED for the scoped implementation authorized by [E28](register.md#e28).
Generation-model selection, live behavior and multi-step investigation remain open.
[E29](register.md#e29) subsequently adds the shared-corpus browser interface below.
[E30 live verification](../validation/live-rag-2026-10-28.md) now verifies one
authorized two-source question through real Gemini, local PostgreSQL and this UI.
E38 subsequently implements and smoke-verifies a bounded multi-step investigation
extension; see the [E38 verification record](../validation/multi-step-investigation-2026-10-28.md).
Historical notes below describe the original E28/E29 single-step scope; general
quality and production model/profile selection remain open.

## Responsibilities and boundaries

AnswerQuestion composes the existing SearchEvidence with one AnswerGenerator call.
There is no second retrieval path, new schema, indexer change, agent framework,
memory, hybrid search, RRF or tool loop. Application values use standard frozen
dataclasses. Gemini SDK types stay in infrastructure; Pydantic validates the HTTP
boundary, not domain models or configuration.

GenerationRequest carries the exact question and immutable (evidence_id, text)
values. Only server-controlled E1... identifiers and retrieved text go to the
model. Real document/version IDs, source metadata and locators stay in the
application's mapping. GenerationResult contains status, answer and citation IDs.
ResolvedCitation pairs an identifier with defensively copied retrieved Evidence.

## Context, grounding and citations

Questions contain non-whitespace, valid UTF-8 text and at most 4,000 Python
characters. Defaults select at most five retrieved items, capped at ten, and at
most 12,000 characters of serialized question/evidence JSON, capped at 64,000.
This is a character budget, not a model-token estimate. Fixed system instructions,
schema and provider framing add bounded overhead outside that user-context budget.
Output is bounded separately by provider tokens and 8,000 answer characters.

Selection follows retrieval order, skips whole items that exceed the remaining
budget, and assigns IDs to accepted items. It never clips text, changes offsets,
reranks or optimizes context. If evidence exists but no intact item fits,
EvidenceContextLimitError is distinct from insufficient evidence.

System instructions require evidence-only conclusions, supporting IDs, uncertainty
and abstention, and classify retrieved text as untrusted data. Question/evidence
JSON is a separate user message. The provider has no tools, source-fetch operation,
database handle or application secrets in its prompt. Prompt boundaries do not
guarantee resistance to injection or factual correctness.

Gemini structured JSON is strictly parsed: exact fields, known status, nonblank
bounded answer and nonempty citations for answered results; null answer and empty
citations for abstention. Duplicate JSON keys, duplicate/malformed citation IDs,
unknown fields and IDs outside the selected context are errors, without repair
or regeneration. Inline [E...] references must occur in the validated citation
list. Application validation is repeated even for constructed provider results.
No model-generated metadata becomes trusted source information.

Citations retain independent document/version identities, Content hashes,
Unicode half-open ranges, exact excerpts and original intersecting source spans.
Free-form answer text remains model-authored: valid references do not prove that
a claim follows from a source. Semantic support needs U17 evaluation. There is
no rendered-citation or original-file download endpoint.

## Model and request policy

No generation model was approved in earlier records. GEMINI_GENERATION_MODEL
therefore has **no implicit runtime default**; HTTP startup requires an explicit
operator selection. Proposed development default: **gemini-3.6-flash**, documented
as stable with structured output by [Google](https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash).
This is a proposal, not production selection or proof of free-tier availability.
Account access, quota, costs and live compatibility were not probed. Generation
settings are separate from gemini-embedding-2/768 serving settings.

The existing google-genai dependency calls async generate_content with separate
system instructions, application/json schema, one candidate and a default
1,024-output-token cap (configurable 128-4,096). Per-attempt timeout defaults to
30 seconds (1-120); total attempts default to two (1-3). SDK automatic retries
are disabled; the adapter retries only explicit transient API statuses, transport
failures, timeouts and upstream 429. Delay is bounded at 0.25/0.5 seconds. Auth,
input, malformed output, safety-blocked and truncated responses are not retried.
Retries can repeat inference; bounded attempts do not imply exactly-once cost.
Cancellation propagates. Raw SDK errors/payloads are not returned to clients.

## FastAPI composition and responses

GET /health is liveness only, with no DB/Gemini probe. POST /questions accepts
only {"question": "..."}; extra fields, including owner/model/limit, are rejected.
No owner identifier or header becomes trusted authorization. The endpoint always
supplies owner_id=None to restrict retrieval to shared sources. This intentionally
permits unauthenticated shared-corpus access; private documents remain excluded.
Public authentication and deployment are not implemented.

Answered and insufficient_evidence are HTTP 200 outcomes. No retrieval evidence
skips generation. Model abstention returns a server-authored insufficient-evidence
message, null answer and no citations. Errors never become abstention:

| HTTP | Meaning |
| --- | --- |
| 422 | Invalid question or forbidden fields; request values are not echoed |
| 502 | Invalid embedding/generation response or rejected retrieval input |
| 503 | Retrieval/generation unavailable, upstream quota, or context-budget failure |
| 504 | Generation timeout or fixed 90-second overall question deadline |
| 500 | Unexpected application/response-validation failure |

The factory creates no engine, external client or settings load at import time.
FastAPI [lifespan](https://fastapi.tiangolo.com/advanced/events/) owns resources
via AsyncExitStack. Startup lazily loads existing flat dataclass settings, requires
credentials/model/profile ID, and reads the selected persisted chunking profile,
closing that session before serving. No migration, schema or profile creation
occurs. Repositories retain their short operation transactions; none spans
embedding or generation. Shutdown and partial-startup failure close clients and
dispose the engine. No pool sizes, isolation changes or worker counts are selected.
Windows launch explicitly uses asyncio.SelectorEventLoop for psycopg.

## Files and verification

New source files: application/answers.py; ports/answer_generator.py and
generation_errors.py; use_cases/answer_question.py; infrastructure/generation/
gemini_answer_generator.py and __init__.py; infrastructure/api/app.py, bootstrap.py
and __init__.py.

Existing infrastructure/config.py and .env.example add only RAG settings;
the real .env is untouched. pyproject.toml/uv.lock add FastAPI and Uvicorn and
explicitly declare already-installed Pydantic for HTTP imports. Locked versions:
FastAPI 0.162.0 and Uvicorn 0.54.0; transitive requirements are uv-managed.

New tests: unit/application/test_answer_question.py; unit/infrastructure/
test_gemini_answer_generator.py, test_rag_api.py and test_rag_bootstrap.py;
integration/infrastructure/persistence/test_rag_workflow.py. Existing
unit/infrastructure/test_config.py adds typed-setting checks. README, documentation
index, register, testing strategy, system overview and this ADR record the scope.

**Verification:** 311 unit + 125 real PostgreSQL integration = **436 passing tests**.
The [testing checkpoint](testing-strategy.md#e28-single-step-rag-checkpoint) records
details. No Cloud SQL/Gemini requests, schema changes, infrastructure changes,
staging, commits or pushes occurred.

Remaining work: authorized live model/quota checks, semantic answer/citation and
prompt-injection evaluation, admission/rate controls, authenticated access, profile
publication/model pinning, keyword/hybrid retrieval, contradiction detection,
multi-step structured tool investigation, UI and deployment.

## Browser interface (E29)

On 2026-10-28, E29 adds static index.html, styles.css and app.js within
infrastructure/api/static. GET / serves the page and /static serves assets using
the existing FastAPI/Starlette facilities. The page is excluded from OpenAPI;
health/questions contracts, application ports/use cases, startup ownership,
configuration, source visibility and database schema remain unchanged. No
dependencies or build configuration changes were needed; the offline-built
wheel was inspected and includes all three assets.

The page sends only {"question": exact_input} to same-origin POST /questions.
It performs presentation-level validation against the existing 4,000-character
limit, counting Unicode code points rather than UTF-16 units. The loading state
disables repeat submissions and announces progress. Editing or invalid input
preserves previous results; a new valid request clears them and identifies the
question associated with the eventual result. No automatic retry is performed.
The browser aborts after 100 seconds, allowing time for the backend's 90-second
deadline; abort does not guarantee cancellation of server/provider work.

Answer text remains plain text. A dedicated citation navigation list is built
from the server's citation entries, with labels such as [E1] Source title. Answer
markers are never parsed into source mappings. Buttons open native details cards,
move keyboard focus to their summaries and reveal exact excerpts. Document and
version IDs remain distinct. Available source identity/revision, canonical range,
Content digest, record locator, descriptive metadata and original spans/indices
are shown. Missing titles receive a generic label; absent locations are stated,
never invented. URLs inside metadata remain literal text, not links or downloads.

Every untrusted answer/title/excerpt/metadata/locator uses textContent. No HTML,
Markdown, dynamic script, untrusted URL attribute or API-selected DOM ID is used.
The root document adds a same-origin CSP, no-referrer and nosniff headers, without
altering API/docs responses or adding CORS. The form has an explicit POST target
so missing JavaScript cannot send questions through a default GET query string;
normal submission remains JSON through the script. No credentials enter assets.

Insufficient evidence has its own non-error result. Invalid input, malformed
responses, network failures, client/server deadlines, upstream/provider and
backend failures show fixed safe messages. Raw backend/provider error text is
not displayed. Answered labels say "Answer generated", not "verified"; source
cards explicitly distinguish citation mapping from semantic correctness.

Layouts adapt at 600px and include wrapping for long text/identifiers, accessible
labels, live status/alert feedback, focus styles, native keyboard disclosures,
skip navigation and reduced-motion handling. There is no chat history, upload,
document manager, authentication, multi-step workflow or new business logic.

### E29 files and verification

- New: infrastructure/api/static/index.html, styles.css and app.js;
  tests/unit/infrastructure/test_web_ui.py; tests/frontend/test_web.cjs.
- Modified: infrastructure/api/app.py for root/static routes;
  tests/integration/infrastructure/persistence/test_rag_workflow.py for page/asset
  assertions through real composition; README, documentation index, register,
  testing strategy, system overview and this ADR for the approved scope/results.
- **316 unit + 125 PostgreSQL integration = 441 passing Python tests**;
  **24 JavaScript tests pass**. One existing upstream SDK warning remains.
- JavaScript tests use Node's built-in runner/VM with fake fetch and a small
  DOM/event test double, without npm packages. They execute the actual production
  script; they are not a browser/layout emulator. Node is a test-only option,
  not an application runtime/build requirement. Existing Python CI is unchanged;
  this separate check runs with node --test tests/frontend/test_web.cjs.
- Actual in-app-browser checks at 1100x900 and 390x844 used the real page/API with
  temporary synthetic retrieval/generation providers: submission/loading, answer,
  multiple citations, keyboard expansion, invalid input, abstention, safe provider
  failure and literal injection-shaped text. No injected img/script nodes or
  horizontal mobile overflow were found. Desktop/mobile screenshots were saved;
  the temporary server/tab were stopped/closed after verification.
- The existing real PostgreSQL-to-HTTP workflow also loads the page/assets and
  preserves shared/private isolation. No Gemini/Cloud SQL access, migrations,
  infrastructure changes or Git staging/commits/pushes occurred.

Limits: application startup still requires the approved DB/model/profile settings
and existing indexed corpus. No live-service or full cross-browser/accessibility
audit was performed. Source access remains excerpt/provenance inspection; original
file downloads, management, auth, semantic support evaluation and deployment remain
future work. E38 adds a bounded multi-step investigation workflow; planning quality,
broader evaluation and operational job recovery remain incomplete.
