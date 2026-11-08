# Native final-decision controls N/O — 2026-11-08

The existing diagnostic now compares two native decision functions after a
controlled evidence replay. No production Investigation code, prompts, tool
schemas, retrieval, database operations or generation budgets were changed.
The existing A–M diagnostic request behavior is preserved.

| Case | Replayed evidence | Intended second decision |
| --- | --- | --- |
| N | Original full E1/E2 | `submit_final_answer` identifying Hedworth Jolliffe, 2nd Baron Hylton, citing E1/E2. |
| O | Original full E1 only | `search_evidence` for the missing link, without guessing from background knowledge. |

These are expected behaviors for later review, not observed live results.

## First request and controlled native history

N's first SDK request is identical to I's; O's is identical to J's. It contains
the original question, unmodified production v5 system instruction, original
query-only search declaration, VALIDATED mode, production JSON final-response
schema and existing generation settings. No diagnostic path or expected answer
is supplied. An initial final text decision is recorded and stops the case; it
does not test the second-request interface. An unsupported initial function,
malformed call, invalid native role/signature or provider error also stops it.

Only a valid production `search_evidence` request permits the second generation.
The actual SDK model `Content` is deep-copied without changing parts, arguments,
function-call ID or thought signature. The matching native function response
uses the original ID/name. The original full excerpts are replayed unchanged:
E1 is 677 characters and E2 is 1,296 characters. O receives E1 alone.

Role ordering remains `user, model, user`. Controlled-subset metadata explicitly
identifies the saved query/run, actual requested query, exact-string match result
and lack of live retrieval. A query mismatch is not represented as a real search
result for that arbitrary query. No signed messages are fabricated from logs.

## Second-request configuration

N/O have identical prompts/configuration and differ only in the supplied
excerpts and derived evidence-ID lists. Both offer exactly:

1. `search_evidence`: unchanged production declaration with its `query` argument.
2. `submit_final_answer`: diagnostic-only declaration whose
   `parameters_json_schema` is a deep copy of production `FINAL_SCHEMA`.

The second function requires `status`, nullable `answer`, `citations` and
`conflicts`; conflict entries require a claim and its citation IDs. Statuses
remain answered, insufficient_evidence and conflicting_evidence. There is no
Python callback or external operation for this function.

The second request uses `function_calling_config.mode = ANY`, allowing exactly
those two names. It has no text-response MIME type, `response_json_schema` or
`response_schema`. SDK automatic function execution remains disabled. Model,
timeout, output limit, candidate count and unset generation options are unchanged.

The second request appends only this interface explanation to v5:

```text
Diagnostic two-function interface for this decision: use search_evidence when a
fact required by the ORIGINAL question remains unsupported and a relevant search
can resolve it. Use submit_final_answer when the evidence supports an answer,
establishes a material conflict, or cannot support a further useful investigation.
Do not search for unrelated relationships or answer using unsupported facts.
Return final decisions as submit_final_answer arguments, not JSON text.
Choosing submit_final_answer terminates the investigation without an external operation.
```

There are no question-specific relationship paths, named linking people,
reference answers, expected queries or evidence-sufficiency verdicts in this
addendum. Answer names naturally present in source evidence are not removed.

**This changes calling mode and final-answer representation together**, including
the necessary function declaration/interface instruction. It tests the combined
alternative decision mechanism, not the independent effect of ANY versus native
finalization. It is not an isolated test of either change.

## Parsing, logging and interpretation

Only one normally completed candidate with exactly one native decision call and
no non-thought final text can be accepted. Search arguments use the existing
production parser. Final function arguments are validated through the existing
`InvestigationDecision`, `ConflictClaim` and status values, preserving production
status/answer/conflict/citation semantics without rewriting SDK parts or creating
fake text messages. Extra/missing fields, malformed arguments and unknown
functions are recorded as parsing errors.

Native finalization is terminal: arguments are captured and the case ends without
a function execution, acknowledgement request or third generation. A second
search is also recorded without executing it. Multiple returned calls, including
simultaneous search/final calls, are retained in full, marked ambiguous and never
counted as successful finalization. Text-only JSON is not converted into a native
function call.

Each step record includes exact sanitized SDK arguments and response object
representation, all returned native parts/calls/arguments/IDs/signatures, source
provenance, token usage, duration and provider/parsing/protocol errors. The
comparison additionally records:

- `selected_native_function`, or null for multiple calls.
- `returned_function_calls` and all `native_final_arguments`, including malformed
  or ambiguous outputs.
- Parsed final answer/status/citations or proposed search query.
- `ambiguous_native_decision`, `native_terminal_decision_valid` and validation errors.

`native_terminal_decision_valid` means argument, citation-structure and native
protocol validity only. It is **not a factual correctness judgment**. An O answer
that happens to match the reference and cites E1 can still lack the required
second link. Known citation IDs do not establish support. Actual function choice,
reference agreement and structural observations remain separate from the existing
attributed manual factual/support judgments, which begin null.

Unknown IDs, unlisted inline citations and unavailable conflict citations cannot
count as structurally valid terminal decisions. Ambiguous calls do not promote a
final answer into the parsed-success fields, but their original arguments remain
available. Error observations retain successful earlier steps and the second
request history when applicable. Credentials/auth headers are sanitized with the
existing mechanism. SDK representations are **not raw HTTP wire payloads**.

## Exact PowerShell commands and request caps

Run from `C:\Users\merli\upgaming_task\ai-investigation-engine`.

Preview N/O without provider requests:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases N O
```

Execute N alone, maximum two generation requests:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases N --execute --max-generation-requests 2
```

Execute O alone, maximum two generation requests:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases O --execute --max-generation-requests 2
```

Each case reserves two requests even if it ends after one. N+O together require
four per trial; repeating either case three times requires six. The existing
default 60-second gap, attempts=1, stop-on-429 and fresh-output-directory rules
remain. No retries, third request, actual searches, embeddings or database
queries occur. No execution command was run during implementation.

## Offline validation and limits

The provider-free preview is at `reports/generated/gemini-investigation-no-preview/`:
four maximum requests, zero executed. Its second-step templates contain the
correct ANY/two-function configuration while native history remains deferred until
a real first response exists. No native ID or thought signature is invented.

The preservation receipt at
`reports/analysis/gemini-native-final-controls-2026-11-08/preservation-audit.json`
checks unchanged production source bytes, all A–M initial SDK arguments and prior
native follow-ups, first-request equality with I/J, full excerpts, second-request
configuration equality and evidence-only N/O differences. Native comparisons use
an explicitly synthetic in-memory test fixture, never sent to a provider or
inserted into executable preview history.

Offline tests cover both functions under ANY, absent text-response schema, final
semantics, malformed/unavailable citations, ambiguous calls, raw-part preservation,
provider-free preview, early exits, no execution/acknowledgement and request caps.
An actual SDK using an in-memory HTTP transport confirms the serialized second
request lacks response JSON/MIME fields. No external request is made by these tests.

Final validation: **165 tests passed** (131 diagnostic tests and 34 existing
model/protocol/v5-prompt tests), with one existing Google GenAI/Python deprecation
warning. Ruff lint and formatting checks passed. JUnit output:
`reports/analysis/gemini-native-final-controls-2026-11-08/tests.xml`.
The preservation audit confirms all 124 production Python files are unchanged,
all A–M initial requests/prior native follow-ups are unchanged, and N/O differ
only in their original full evidence subsets after the initial controls.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/evaluation/test_gemini_decision_diagnostic.py tests/unit/infrastructure/test_gemini_investigation_model.py tests/unit/infrastructure/test_gemini_investigation_protocol.py tests/unit/infrastructure/test_investigation_stopping_prompt.py -q
.\.venv\Scripts\python.exe -m ruff check scripts/diagnose_gemini_investigation.py tests/unit/evaluation/test_gemini_decision_diagnostic.py
.\.venv\Scripts\python.exe -m ruff format --check scripts/diagnose_gemini_investigation.py tests/unit/evaluation/test_gemini_decision_diagnostic.py
```

If N finalizes and O searches, that is consistent with a useful combined native
decision mechanism on this question. One stochastic trial does not establish a
general improvement or identify whether mode, representation, schema exposure or
interface wording caused the difference. Independent initial queries/history,
saved-result relevance, provider conditions and the replay notice are additional
confounders. An early first-request final decision leaves the second interface
untested. No live Gemini behavior or accuracy improvement is claimed here.

Production code, previous diagnostic behavior and source evidence are preserved.
No live calls, commits, pushes or deployment occurred during implementation.
