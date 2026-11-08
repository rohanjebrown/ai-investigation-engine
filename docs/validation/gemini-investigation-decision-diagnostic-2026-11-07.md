# Standalone Gemini Investigation decision diagnostic

`scripts/diagnose_gemini_investigation.py` tests isolated decisions and one
bounded native two-turn interactions. Its default mode previews nineteen cases
(at most thirty-one generation requests) with **zero provider,
embedding or database requests**. No production source, prompts or orchestration
were changed for this diagnostic.

## Cases and interpretation

| Case | Evidence supplied | Search availability | Instructions | Purpose |
| --- | --- | --- | --- | --- |
| A | All five passages from the first saved v5 Jolliffe search | No tool declaration | Production v5 plus its existing final-only suffix | Forced final decision: factual reasoning with sufficient evidence. Abstention remains a possible structured response. |
| B | Same five passages | Production `search_evidence`, VALIDATED mode | Exact production v5 | Free choice between searching and a final decision. |
| C | Identical to B | Identical to B | Production v5 plus an explicitly recorded relationship-check paragraph | Change only the instruction; compare the answer/search decision. |
| D | E1 alone, identifying William's father | Production `search_evidence`, VALIDATED mode | Exact production v5 | Check whether a search targets the missing father-of-the-father relationship. |
| E | Identical to B | No tool declaration or tool configuration | Exact production v5, no final-only suffix, `final_only=false` | Isolate tool availability without A's forced finalization. The prompt still mentions searching. |
| F | First call: only the original question. Conditional second call: E1–E5 through a native function response | Production tool stays available on both calls | Exact production v5 | Observe native call/result/decision history, capped at two requests. |
| G | E1 only | No native tools | Diagnostic JSON decision guidance/schema | Propose a search for a missing link without guessing. |
| H | E1–E5 | Same as G | Identical to G | Answer when all required links are supported. Only evidence differs from G. |
| I | First question, then full original E1/E2 | Production native tools | Exact production v5 | Remove E3–E5 without shortening the two required passages. |
| J | Identical to I except E1 alone | Same as I | Exact production v5 | Missing-link control; no reference answer or explicit missing relationship is sent. |
| K | Same E1/E2 subset as I | Native search with three required diagnostic arguments | V5 plus a minimal argument-description addendum | Report observable missing-fact/evidence-ID claims while retaining the final-answer option. |
| L | Original full E1/E2, through native history | Original production search schema | V5 plus a diagnostic two-father relationship path | Compare directly with I; only the instruction changes. |
| M | Original full E1, through native history | Same as L | Identical to L | Compare directly with J; L/M differ only in supplied evidence. |
| N | Original full E1/E2 after a production initial search | Second request: search and native final functions under ANY | V5 plus a minimal two-function interface explanation | Test the combined alternative calling mode/final representation. |
| O | Original full E1 only | Identical to N | Identical to N | Incomplete-evidence control; only evidence differs from N. |
| P | Original full E1/E2 | Original native VALIDATED search | Exact production v5 | I control with only second-request response MIME/JSON-schema settings unset. |
| Q | Original full E1 | Identical to P | Identical to P | J control with the same two second-request settings unset. |
| R | Original full E1/E2 | Original native VALIDATED search and JSON response schema | Exact production v5 | I control with only second-request thinking_level=MEDIUM. |
| S | Original full E1 | Identical to R | Identical to R | J control with the same second-request MEDIUM level. |

The [native controls report](gemini-native-evidence-controls-2026-11-07.md)
documents I/J/K. The [relationship-path controls report](gemini-relationship-path-controls-2026-11-08.md)
documents L/M, their execution commands, question-specific limitations and latest
validation. The [native final-decision controls N/O](gemini-native-final-decision-controls-2026-11-08.md)
document the two-function alternative and latest checks. Earlier validation
figures below are historical.
The [response-constraint controls P/Q](gemini-response-constraint-controls-2026-11-08.md)
document the baseline-model override, two-setting isolation and unconstrained
text handling while retaining native function calling.
The [thinking-level controls R/S](gemini-thinking-level-controls-2026-11-08.md)
record explicit second-turn MEDIUM requests and provider-reported thinking usage
without inferring reasoning from zero or missing counts.

The default source is `reports/evaluation/structured/jolliffe-v5/`, preserved run
`20261107T084545-dace22b8`. The script reads the original first admitted exchange
and retains its E1–E5 IDs, exact excerpts and source context through the existing
`InvestigationTurn.search_responses()` presentation. Full saved provenance and
source-file SHA-256 hashes are recorded in `plan.json`. It does not construct
evidence from the reference answer or gold annotations. The saved reference is
used only for post-response comparison and manual review, never added to requests.

E1 identifies William George Hervey Jolliffe, 4th Baron Hylton, as the son of
Hylton Jolliffe, 3rd Baron Hylton. E2 identifies the latter as the son of Hedworth
Jolliffe, 2nd Baron Hylton. Case D therefore lacks the second link; mentioning
other relatives in E1 does not establish that link.

Each selected case/trial starts a fresh independent conversation. A–E supply
saved evidence as data in one user message. **A–E are not reconstructed post-tool histories:** the saved benchmark traces
do not contain the original opaque native call signatures, and the diagnostic
does not fabricate signed model turns or pretend a search just happened.
Consequently, the experiment isolates decisions on supplied evidence but cannot
by itself explain production behavior after a native function response. This
presentation limitation is common to B, C and E. F instead retains a newly
returned native SDK model response in memory and appends it without rebuilding
signed parts from JSON.
In particular, v5 refers to evidence returned by tools: supplying that evidence
as saved data in a user message may itself influence search or citation choices.
A/B/E results therefore do not fully separate factual reasoning from native
tool-history effects. No claim that this reproduces the production search loop
is warranted.

## Configuration reuse and controls

The script imports the production `SYSTEM_INSTRUCTION`, `TOOL_DECLARATION`,
`FINAL_SCHEMA`, guidance version, final-response parser and citation helper.
The complete evidence payload uses the existing application formatter. The
configured model, `generation_timeout_seconds` and
`investigation_max_output_tokens` come from the existing project settings,
matching the production API bootstrap's Investigation adapter construction.
Loading settings reads local configuration; it does not open a database.

For B/C/D/F/I/J the response format and function-calling configuration match production:
JSON response MIME/schema, candidate count one, production declaration and
VALIDATED calling mode with `search_evidence` allowed. Temperature, top-p, top-k,
thinking and seed remain unset, as in production. Actual provider defaults are
not guessed. A and E remove tool declarations/configuration. A uses the existing
final-only suffix; E leaves the v5 prompt and `final_only=false` unchanged.
A forces final-decision mode, not a correct answer. E's prompt still mentions
an unavailable tool: abstention/failure cannot be attributed to factual reasoning
alone. Inspect final status, errors and visible output; any attribution to the
unavailable-tool instructions belongs in the manual review explanation.

The sole common diagnostic SDK addition is explicit
`automatic_function_calling.disable = true`. Only the function schema is supplied;
no Python search callable or database/embedding dependency is installed.
No returned function call is executed. The complete SDK response is retained even
when the production parser rejects its shape or finish reason.

Every client uses production's base URL/API version/timeout and
`HttpRetryOptions(attempts=1)`. Each A–E/G/H case/trial invokes
`client.aio.models.generate_content()` once; native-history cases invoke it at most twice.
There are no countTokens calls.
A failed invocation still consumes one request from the cap. Execution requires
both `--execute` and an explicit positive `--max-generation-requests` at least as
large as the complete selected worst-case plan, reserving two requests per native-history
trial even if its first call might finalize. The counter is checked again before
each submission. No streaming or automatic tool execution is performed.
Only the native-history cases have bounded follow-ups; none makes a third request.

Repeated trials are separate independent conversations, not retries. The default
pause between calls is 60 seconds. A 429 is saved as a failed call and stops the
remaining plan; it is never retried automatically. Wait at least a minute before
an explicit new invocation and check whether the quota permits it. Failed runs
return a nonzero process status; preview returns success without submitting calls.

### F: native history and simulated results

F's first request is the production initial question message, with no supplied
evidence. If it produces a final decision, provider error, malformed/unsupported
call, invalid role or missing required Gemini 3 signature, F records that outcome
and stops. No function call is invented.

For a valid search request, the second request has roles `user, model, user`:
the initial question, a native deep copy of the original SDK model `Content`
(all parts/IDs/thought signatures preserved), and a matching SDK
`FunctionResponse` using the original call name/ID. Both calls use the same v5
prompt, tools and generation configuration. Stateless clients are created per
request; explicit conversation contents carry the native protocol state.
Signed parts are never reconstructed from their logged JSON.

The simulated response uses production's search-result representation and E1–E5,
plus an explicit model-facing `replay` metadata object containing:

- `requested_query`: Gemini's actual query.
- `saved_result_query`: the query that originally returned the saved passages.
- `query_matches_saved_exactly`: exact string comparison, not semantic equivalence.
- `live_retrieval_performed: false`, source run ID and a simulated-replay notice.

The ordinary response `query` identifies the function request being answered;
the replay metadata states the evidence's actual origin. It never represents
the saved passages as fresh retrieval for an arbitrary query. Even an exact
match is a saved snapshot. With a mismatch, relevance to the new query is
unverified. This metadata is a deliberate diagnostic addition to the production
body for transparency and can itself affect model behavior.

The second response is retained whether it answers, abstains, fails or requests
another search. That search is not executed, and there is no third generation.
Assess E1/E2 jointly for the supported grandfather chain. Whether a search for
Hedworth's parents is unnecessary is a visible-output review against that chain,
not an inferred account of Gemini's hidden rationale.

## Exact PowerShell commands

Run from `C:\Users\merli\upgaming_task\ai-investigation-engine` using the existing
configured model/key. Do not pass the API key on the command line.

Preview all nineteen cases without requesting anything from Gemini:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py
```

Execute **only A**, one request:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases A --execute --max-generation-requests 1
```

Execute **only B**, one request:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases B --execute --max-generation-requests 1
```

Execute **only C**, one request:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases C --execute --max-generation-requests 1
```

Execute **only D**, one request:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases D --execute --max-generation-requests 1
```

Preview only E/F, without API requests:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases E F
```

Execute **only E**, maximum one generation request:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases E --execute --max-generation-requests 1
```

Execute **only F**, maximum two generation requests, with a 60-second gap if a
second request is needed:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases F --execute --max-generation-requests 2
```

E+F together requires a cap of at least three per trial; F with `--trials 3`
requires six. All default cases now require thirty-one. A larger cap never creates extra
steps. Embedding, database and actual retrieval operations remain zero.

Optional independent repeated trials of B and C, six requests total, in
B1/C1/B2/C2/B3/C3 order:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases B C --trials 3 --execute --max-generation-requests 6 --pause-seconds 60
```

Omitting `--execute` from any command previews that selection. `--source-run`
can point to another compatible single-question saved Jolliffe run. `--output`
must name a fresh directory; existing directories are never overwritten.
By default, each invocation gets a fresh UTC-stamped directory under the
already Git-ignored `reports/generated/gemini-investigation-decisions/` tree.
The actual directory and attempted request count are printed as a small JSON
receipt. None of the execution commands above was run during implementation.

## Outputs and assessment

Each invocation writes:

- `plan.json`: selected cases/trials, caps, source provenance/hashes, reference
  used only for review, production/alternative instruction hashes, SDK version
  and controls.
- `call-A-001.json`, etc.: exact sanitized SDK model/contents/config arguments,
  full system instruction, declared tools, generation settings and explicit
  client options, recorded before submission. The completed record adds actual
  SDK `model_dump(mode="json", exclude_none=False)` response representation,
  all native candidate content/parts, thought signatures if returned, function
  calls, non-thought final text, finish reasons/messages, response metadata,
  token usage, model version, production parse result, errors and duration.
  F uses `call-F-001-step1.json` and `call-F-001-step2.json`. Step two is a
  conditional template in preview and records an explicit skipped reason when
  the first response does not warrant a valid continuation. No placeholder
  native IDs/signatures become SDK request objects. Execution records actual
  second-call arguments, native function response and replay/query-match data.
  Per-call durations include client initialization but exclude the inter-call pause.
- `comparison.json`: one comparison row per saved call, forced/free-choice
  labels, actual search requests/queries, answer, reference-agreement results,
  structural citation checks and manual judgments. Planned but unattempted
  IDs are listed explicitly.
- `review-template.json`: result-fingerprinted review entries with initially
  unassigned judgments.

**SDK representations are not raw HTTP wire payloads.** Authentication is not
part of the recorded client options. Credential/auth/cookie fields, configured
key/password/token values, recognizable Google API keys, Bearer strings and key
URL parameters are redacted recursively. Provider exception strings/bodies are
not exported. Native thought signatures are retained as opaque SDK protocol
fields because this diagnostic explicitly captures complete response parts;
generated records stay local under the ignored output tree by default.

The comparison distinguishes these observations:

| Requested report | How it is assessed |
| --- | --- |
| Returned the correct answer | Existing reference-free answer extraction plus official/reference-agreement signals are shown as text agreement only. Final factual judgment requires attributed manual review. A match is not an automatic claim of factual support. |
| Requested another search | Directly observed from all native candidate parts with a `search_evidence` function call, including outputs rejected by the production parser. No call is executed. |
| Identified the specific missing relationship | Show the returned search queries/final text against the expected missing father-of-the-father link in D; review the visible output. Do not infer a hidden rationale from a query or assert an absent gap in B/C. |
| Produced unsupported claims | Manual review against the exact supplied excerpts. In D, a correct name from model knowledge can still be unsupported by the supplied evidence. |
| Used citations correctly | Automatic membership/format/inline-reference checks are separate from manual claim-to-source support. For the grandfather answer, jointly cited E1/E2 can establish the chain. |

To review, copy the generated template to a separate file, fill its judgment
fields with `yes`, `no`, `unverifiable`, `not_applicable` or leave null, and supply
`reviewer` plus optional `explanation`. Keep call IDs/fingerprints unchanged.
For example, given a completed directory `reports/generated/my-diagnostic`:

```powershell
Copy-Item reports/generated/my-diagnostic/review-template.json reports/generated/my-diagnostic/reviews.json
# Edit reviews.json, then incorporate it without any API/database requests:
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --summarize reports/generated/my-diagnostic --reviews reports/generated/my-diagnostic/reviews.json
```

Saved-record tampering, stale/unknown/duplicate reviews and unattributed completed
judgments are rejected. Preview/failed-call entries cannot receive completed
manual judgments. Null is unreviewed; it is never silently counted as correct or
incorrect. No paid evaluator or invented confidence score is introduced.

### G/H structured decision controls

G/H use the same diagnostic-only five-field schema: `action` (answer, search,
insufficient_evidence or conflicting_evidence), nullable `answer`, `citations`,
nullable `missing_fact` and nullable `search_query`. They declare no native tools
and have no native `final_only` flag. Only their supplied excerpts/derived ID
lists differ. The diagnostic instructions reuse/adapt v5 identity and source
safeguards, remove executable-tool assumptions, require all relationships to be
supported, prohibit background-knowledge answers and unrelated searches, and
prefer an investigable JSON search over premature abstention.

Action/field contracts are validated locally. Answer requires nonempty answer
and citations with null missing_fact/query. Search requires null answer and a
nonempty missing_fact/query (query at most 500 characters); supporting citations
are optional. Insufficient evidence requires a gap description, null answer/query.
Conflicting evidence requires null answer/query, at least two distinct citations
and descriptions of incompatible scoped claims in missing_fact. Unknown fields,
duplicate JSON keys/IDs and invalid combinations are reported without repairing
the response. Complete SDK parts remain available even for invalid outputs.

The comparison records actual action, answer, missing_fact, proposed query,
citations, prompt/output tokens, duration and `expected_action_matches` as an
automatic action-only signal. `matches_expected_evidence_sufficiency_behavior`
is a separate manual judgment; matching an action alone cannot establish a
relevant query, correct relationship or supported answer. G's expected answer
and specific missing relationship are never included in its model request.

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases D --execute --max-generation-requests 1
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases G --execute --max-generation-requests 1
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases H --execute --max-generation-requests 1
```

Choose one command for one experiment. Omitting `--execute` produces a provider-free
preview. A saved G/H preview exists at
`reports/generated/gemini-investigation-gh-preview/`, with zero executed requests.

## Validation and current status

The real saved Jolliffe passages were originally used to generate four A–D
provider-free preview files under
`reports/generated/gemini-investigation-decisions-preview/`. The receipt records
four planned calls and **zero generation requests**. All four original preview
cases were `not_executed`. The user subsequently executed A/B separately: A
answered correctly; B searched for Hedworth's parents. Those historical artifacts
are retained. No new live calls were made while implementing E/F.

The initial targeted suite passed **46 deterministic tests**, including twelve
new diagnostic tests, with one existing Google GenAI/Python deprecation warning.
Tests prove B/C request equality except the instruction, B configuration parity
with the production adapter apart from explicit SDK automatic-tool disabling,
independent repeated calls/clients, cap enforcement before client creation,
complete signed native-part capture, response/usage preservation, parser failures,
reference-match limits, citation checks, sanitization and result-bound reviews.
A real SDK using an offline HTTP transport receives a simulated 429 exactly once
and neither retries nor continues to another case. No external service is used.
JUnit output is `reports/analysis/gemini-decision-diagnostic-2026-11-07/tests.xml`.

### E/F extension validation and interpretation

Implementation changes are confined to this script, its existing test module,
this guide and its documentation index entry. `make_request()` adds the E
tool-removal control and F's question-only initial contents. `native_followup()`
uses the original live SDK model content and production evidence formatter.
`submit_request()` centralizes one-request logging, sanitization and budget
charging. The plan/comparison now count F's conditional second call explicitly.
Existing A–D call IDs/review fields remain compatible, and historical A/B outputs
are not rewritten.

The six-case offline preview is saved under
`reports/generated/gemini-investigation-ef-preview/`: six cases, seven maximum
requests, zero executed. F's second request remains conditional; its exact signed
history can only exist after a future authorized first generation response.
The saved B request is compared with the E preview after removing only the two
tool fields, including model, messages, instructions and generation/client settings.
This check passed against the actual B call at
`reports/generated/gemini-investigation-decisions/20261107T164817-50100a00/call-B-001.json`.
The v5 prompt SHA-256 remains
`45cada8e1a9c9f2455a4966bedc4e86bcad985cc13086e9879ed97bd7638a510`.
The offline receipt is
`reports/analysis/gemini-decision-ef-2026-11-07/preview-audit.json`.

Final validation: **66 deterministic tests passed**, comprising 32 diagnostic
tests and 34 existing model/protocol/v5 prompt tests, with one existing SDK/Python
deprecation warning. Ruff lint and formatting checks passed. Coverage includes
E/B tool-only equality; native content, binary signatures, ID/name and role
preservation; honest exact-query match/mismatch reporting; initial final answers;
second search requests with a larger run-wide cap; unsupported/unsigned initial
calls; errors on either request; mixed/repeated-trial accounting; and a poisoned
retrieval entry point that must never execute. An actual SDK with an in-memory
HTTP transport also preserves the native message through two serialized requests.
No external requests are made by these tests. Latest JUnit output:
`reports/analysis/gemini-decision-ef-2026-11-07/tests.xml`.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/evaluation/test_gemini_decision_diagnostic.py tests/unit/infrastructure/test_gemini_investigation_model.py tests/unit/infrastructure/test_gemini_investigation_protocol.py tests/unit/infrastructure/test_investigation_stopping_prompt.py -q
.\.venv\Scripts\python.exe -m ruff check scripts/diagnose_gemini_investigation.py tests/unit/evaluation/test_gemini_decision_diagnostic.py
.\.venv\Scripts\python.exe -m ruff format --check scripts/diagnose_gemini_investigation.py tests/unit/evaluation/test_gemini_decision_diagnostic.py
```

Interpret observations conservatively:

- If E answers while B searches, tool availability is a candidate explanation
  under these instructions. A single stochastic trial does not establish a general cause.
- If E abstains/fails, distinguish the unchanged prompt's unavailable-tool
  references, provider/configuration errors and visible factual assertions.
  Do not invent a cause from an HTTP code or a reference match.
- If F's second call answers while B searches, native evidence presentation/history
  may matter. F also includes an actual preceding model decision and explicit
  replay metadata, so this is not a single-variable B/F comparison.
- If F still searches for Hedworth's parents, the tendency persists with native
  function history and sufficient replayed evidence in this trial. This does not
  prove that tools alone, the v5 wording or any particular hidden rationale caused it.
- A query mismatch limits claims about retrieval relevance. All F results use a
  simulated saved snapshot, not actual retrieval for Gemini's new query.

E/F factual support and citation support remain reviewable, unassigned judgments
until attributed manual review. Final structured status, including
`insufficient_evidence`, and actual requested queries are recorded directly.
Unsupported/incomplete outputs and errors remain visible and are not converted
into optimistic correctness labels.

No production Investigation behavior or evaluation metric was changed. No live
API calls, database queries, embeddings, commits, pushes or deployment were made
for this task. Prior authorized live runs remain separate historical artifacts.
