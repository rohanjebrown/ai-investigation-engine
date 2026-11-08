# Response-constraint controls P/Q — 2026-11-08

`scripts/diagnose_gemini_investigation.py` now supports two native-history controls
that unset only two API response settings on the second generation request.
Production code/configuration and the existing A–O request constructions remain
unchanged. No live Gemini requests were made during implementation.

| Case | Second-turn evidence | Direct control | Only configuration differences |
| --- | --- | --- | --- |
| P | Original full E1/E2 | I | `response_mime_type=None`, `response_json_schema=None` |
| Q | Original full E1 | J | Same two fields unset |

Use `gemini-3.5-flash-lite` explicitly for these comparisons. The new `--model`
option selects the model for both requests of every selected diagnostic case,
without changing project settings, environment variables or production config.
Omitting it preserves the existing configured-model behavior. The selected
model and override are recorded in the plan and actual SDK arguments; protocol
signature checks follow the model actually requested.

## Strict controls

Both first requests remain identical to I/J at the selected model: original
question, exact production v5 system instruction, original query-only
`search_evidence` declaration, VALIDATED calling mode, existing JSON response
MIME/schema and generation options. Only a supported first search response
permits the second request; an early final decision or failure is recorded and
stops the case.

The second request preserves every other field, including `response_schema`
(already unset in the original configuration). It adds no function, prompt text,
relationship path, thinking setting or alternative calling mode. The v5 prompt
still requests structured final decisions; the experiment removes API response
constraints without changing that instruction. Automatic function calling stays
disabled. Output limits, timeout, candidate count and unset generation options
are unchanged.

Original native content, call IDs, arguments and thought signatures are retained
through the same SDK object/deep-copy history path as I/J. The matching function
response replays full original source excerpts: E1 is 677 characters and E2 is
1,296 characters. P receives both; Q only E1. No text is summarized or rewritten.
P/Q have identical instructions/configuration; only excerpts and their derived
ID lists differ.

Controlled replay metadata continues to distinguish the actual requested query
from the saved-result query, record exact matches/mismatches and state that this
is a saved subset rather than live retrieval. No reference answer, expected query
or explicit sufficiency verdict is added to model-facing inputs.

## Response handling

Only P/Q's second-turn handling changes. The full sanitized SDK response and all
candidate parts remain recorded, without using `response.text` or altering them.

- Native search calls retain every argument/ID/signature. Valid query-only calls
  are recognized even with accompanying prose. Multiple/malformed/unknown calls
  remain visible with ambiguity/protocol observations; none is executed.
- Visible text parts are concatenated without inserting separators, while the
  individual original parts are preserved. `final_text` and `observed_final_text`
  retain the complete text.
- Whole JSON objects, entire JSON code fences and JSON split across text parts
  are tested against the existing final-decision contract. Validation reuses
  production final value classes; it does not create fake SDK messages/history.
- Unstructured prose or nonconforming JSON stays in its original form.
  `structured_final_recognized=false` and `structured_final_parse_error` describe
  format recognition; they do **not** make ordinary completed text a protocol
  error. `production_parse_error`/comparison `parsing_error` remain null for it.
- Actual completion anomalies, empty responses or invalid native calls are
  reported independently. Partial text is retained for a non-STOP finish.

The existing comparison fields/files remain, with additional P/Q fields such as
`response_kind`, `observed_final_text`, citation observations, structured-format
recognition and ambiguity. For unstructured completed text, `final_answer` shows
that actual text, explicitly labeled by `response_kind=unstructured_text`; it is
not represented as a schema-valid final decision. Valid JSON continues to expose
its parsed answer/status/citations.

Citation extraction records declared IDs from recognized final JSON, bracketed
IDs from prose (including groups such as `[E1, E2]`), and separate raw observations
of declared IDs/evidence-ID mentions. Unrecognized styles remain in the full text
for review. Missing markers are unavailable checks, not proof of no factual
support. Unknown IDs are flagged independently of output-format validity.

Reference agreement, answer extraction and citation-ID checks are separate from
attributed manual factual/support judgments. A Q answer matching the reference
can still be unsupported by E1. No confidence score or automatic factual-correctness
claim is invented when a schema or citation check passes.

## Exact PowerShell commands

Run from `C:\Users\merli\upgaming_task\ai-investigation-engine`.

Provider-free baseline-model preview:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases P Q --model gemini-3.5-flash-lite
```

Execute P alone, maximum two generation requests:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases P --model gemini-3.5-flash-lite --execute --max-generation-requests 2
```

Execute Q alone, maximum two generation requests:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases Q --model gemini-3.5-flash-lite --execute --max-generation-requests 2
```

If new control runs are needed, select the same explicit model:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases I --model gemini-3.5-flash-lite --execute --max-generation-requests 2
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases J --model gemini-3.5-flash-lite --execute --max-generation-requests 2
```

Choose one execution command at a time. Each case reserves at most two requests;
P+Q requires four per trial. The existing 60-second inter-call gap, attempts=1,
stop-on-429 behavior and fresh-output-directory rules remain. There are no
retries, embeddings, database queries, actual searches or third generation.
None of the execution commands was run during implementation.

## Offline validation and limits

The provider-free preview at `reports/generated/gemini-investigation-pq-preview/`
uses `gemini-3.5-flash-lite` explicitly and records four maximum planned requests,
zero executed. Second-step configuration is exact while native history remains
conditional until a first response exists; no signatures are invented.

The audit at
`reports/analysis/gemini-response-constraint-controls-2026-11-08/preservation-audit.json`
verifies all 124 production Python source files are unchanged, all A–O initial
requests/native follow-ups match the saved baseline, P/I and Q/J first requests
match, second requests differ only in the two unset fields, and P/Q differ only
in original evidence. Native comparisons use an explicitly synthetic in-memory
SDK fixture, never submitted to a provider or inserted into executable previews.

Tests cover all those controls, ordinary/fenced/split/malformed text, full native
parts, citations, mixed prose/search calls, multiple calls, partial completions,
model override without settings mutation, request caps and prohibited retrieval.
SDK tests use an in-memory HTTP transport to verify the actual serialized second
request omits only the MIME/response-schema generation fields while keeping the
same tools and VALIDATED mode. No external requests occur in these tests.

Final validation: **188 tests passed** (154 diagnostic tests plus 34 existing
model/protocol/v5 tests), with one existing SDK/Python deprecation warning. Ruff
lint and formatting checks passed. JUnit results:
`reports/analysis/gemini-response-constraint-controls-2026-11-08/tests.xml`.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/evaluation/test_gemini_decision_diagnostic.py tests/unit/infrastructure/test_gemini_investigation_model.py tests/unit/infrastructure/test_gemini_investigation_protocol.py tests/unit/infrastructure/test_investigation_stopping_prompt.py -q
.\.venv\Scripts\python.exe -m ruff check scripts/diagnose_gemini_investigation.py tests/unit/evaluation/test_gemini_decision_diagnostic.py
.\.venv\Scripts\python.exe -m ruff format --check scripts/diagnose_gemini_investigation.py tests/unit/evaluation/test_gemini_decision_diagnostic.py
```

The experiment changes MIME and JSON-schema constraints together; it cannot
identify which independently caused a difference. It also leaves v5's JSON-style
instruction in place. Independent first queries/history, provider conditions,
unseeded sampling, saved-result relevance and the replay notice are confounders.
Removing a format constraint can change output form and extraction coverage;
compare factual support and citation support, not just parse success or a lucky
reference match. One Jolliffe trial is not evidence of a general improvement.
No live outcome is claimed from offline simulations.

No commits, pushes, deployment or production configuration changes were made.
