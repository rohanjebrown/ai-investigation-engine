# Gemini native evidence controls I/J/K — 2026-11-07

The existing `scripts/diagnose_gemini_investigation.py` now supports three more
native function-calling experiments. No production source files, prompts,
retrieval, database schemas, metrics or generation budgets changed. Existing A–F
requests and F's second-call representation are unchanged. G/H remain available
as the previously requested separate JSON-decision controls.

## Experiments and protocol

| Case | Supplied function-response evidence | Prompt and tools | Intended observation |
| --- | --- | --- | --- |
| I | Original full E1/E2 excerpts | Production v5 and original search declaration/configuration | Does minimal complete evidence yield the grandfather answer citing E1/E2? |
| J | Original full E1 alone | Identical to I | Does the model request the father's father rather than guessing? |
| K | Same full E1/E2 as I | V5 plus an argument addendum; extended diagnostic search schema | What gap does a further search explicitly claim, and is it relevant/genuine? |

All three start with only the original question in the production initial user
message. No expected answer, specific missing relationship or evidence-completeness
label is supplied to the model. Native tools stay enabled with production
VALIDATED calling mode, so a final answer remains a permitted alternative.
Model, response JSON schema, output limit, timeout, candidate count and unset
generation options are inherited from the existing diagnostic/production settings.
As in F, SDK automatic tool execution is disabled and HTTP attempts are one.

A first final decision ends the case after one generation. A valid supported
search permits exactly one second generation. The original native SDK model
`Content` is deep-copied directly, preserving every part, call ID, argument and
thought signature. A matching native function response is appended in role order
`user, model, user`. Signed parts are never rebuilt from logged JSON, and missing
signatures/unsupported calls are not repaired. A search requested on turn two
is recorded but never executed. There is no third generation request.

The replay uses production's evidence formatter. E1 is **677 characters** and E2
is **1,296 characters**, copied verbatim from
`reports/evaluation/structured/jolliffe-v5/`; neither is shortened or summarized.
Their IDs, title/source context and canonical provenance are retained.

Function-response `replay` metadata explicitly states `controlled_subset: true`
and `live_retrieval_performed: false`, identifies the saved run/query, records
the actual requested query and its exact-string match result, and says these
are controlled saved results rather than retrieval for the requested query.
I/J have identical metadata/instructions/configuration except their excerpt and
derived evidence-ID lists. Query differences from independent stochastic first
responses are recorded, not normalized or hidden.

## K's exact diagnostic changes

The original production declaration is copied, not mutated. The tool name,
description, allowed-function configuration and final-response schema stay the
same. Its argument JSON schema retains the existing query field and requires:

| Field | Type and validation |
| --- | --- |
| `query` | Existing nonblank 1–500 character query. |
| `missing_fact` | Nonblank string describing the claimed information gap. |
| `evidence_ids_considered` | Array of unique E-number IDs; empty before evidence is supplied. |

Additional arguments are rejected. Availability checks on claimed IDs are
reported separately from semantic assessment. An ID being available does not
prove it was understood or used to evaluate sufficiency.

Only this addendum is appended to the unchanged production v5 prompt:

```text
Diagnostic search arguments: when calling search_evidence, provide query,
missing_fact (the specific information you believe is still required to answer
the ORIGINAL question), and evidence_ids_considered (IDs of previously supplied
evidence considered when identifying that gap). Before any evidence is supplied,
use an empty evidence_ids_considered array. These fields are observable claims,
not hidden reasoning. Final structured decisions remain unchanged: you are free
to answer instead of calling the search tool.
```

K uses diagnostic validation for the extended call arguments and the existing
production parser for final decisions. Validation does not strip/rewrite native
arguments or modify the signed message. Malformed arguments are retained with
their parsing error; they are never filled from expected values.

## Implementation, records and judgment

Changes are confined to the standalone script, its existing tests and documentation:

- `NATIVE_CASES`/request accounting cap F/I/J/K at two requests per trial.
- `supplied_refs()` selects the original E1/E2 or E1 objects, preserving full text.
- `make_request()` reuses production configuration for I/J and the copied
  declaration/addendum for K.
- `native_followup()` reuses F's native SDK history path and adds explicit
  controlled-subset metadata only for I/J/K.
- `parse_native_action()` handles K's required search fields without altering
  production code or reconstructing signed parts.
- Comparison records add supplied evidence/source IDs, canonical/document/version
  provenance, tokens, durations, protocol/parsing errors and K's observable claims.

Each call has its own `call-I-001-step1.json`/`step2.json` (and equivalent J/K)
containing sanitized SDK arguments, all native response parts, finish reasons,
metadata, usage and timing. Before a first response exists, step two is explicitly
a conditional template without fabricated native IDs or signatures. Source
provenance and input hashes remain in `plan.json`; records never send that plan
or its reference answer to the model.

`comparison.json` reports actual final answers, search queries and K gap claims.
The existing reference-agreement and citation-structure observations remain
separate from manual factual support. K's review template additionally supports:

- `requested_unnecessary_search`
- `identified_genuinely_missing_fact`
- `invented_missing_relationship`
- `investigated_unrelated_relationship`

These start null and use the existing attributed, result-fingerprinted offline
review import. Correct-answer judgment remains a separate existing field. No
keyword heuristic, background-knowledge check or hidden-rationale inference
assigns these judgments automatically.

SDK object dumps are sanitized representations, **not raw HTTP wire payloads**.
The existing credential/auth/header sanitization is reused. Opaque native
signatures are retained locally as requested; generated records use the existing
Git-ignored output tree. No search callbacks, embeddings or database dependencies
are executed.

## Exact PowerShell commands and maximum usage

From `C:\Users\merli\upgaming_task\ai-investigation-engine`, preview all three:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases I J K
```

Execute one selected case using the configured model/key:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases I --execute --max-generation-requests 2
```

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases J --execute --max-generation-requests 2
```

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases K --execute --max-generation-requests 2
```

Each case reserves at most **two generation requests**, even if it ends after
one. All three together reserve six per trial. No embeddings, actual searches
or database queries occur. Repeated trials require the corresponding worst-case
cap (e.g. K with three trials needs six). A larger cap does not create a third
turn. Requests are paced by the existing default 60-second gap; a 429 stops the
plan without retrying. Use a fresh output directory; the default generates one.
Execution still requires `--execute`; no execution command was run in this task.

## Offline validation and limitations

The preview at `reports/generated/gemini-investigation-ijk-preview/` contains
six maximum planned requests and **zero executed requests**. The preservation
audit at `reports/analysis/gemini-decision-controls-2026-11-07/preservation-audit.json`
verifies all 124 production Python source files byte-for-byte, the original v5
prompt hash, unchanged A–F SDK arguments and F follow-up, I/J evidence-only
differences, and the full excerpt lengths. Native request comparisons use a
clearly synthetic in-memory test fixture that is never sent to a provider or
inserted into executable preview history.

Offline tests cover configuration equality, full excerpts, native parts/IDs and
binary signatures, K required arguments and malformed payloads, free final answers,
early exits, two-request caps despite a larger run cap, prohibited retrieval and
complete logs. An actual SDK using an in-memory HTTP transport verifies manual
function-call serialization for F/I/J/K, with no external network requests.

Final validation: **118 tests passed** (84 diagnostic tests and 34 existing
production model/protocol/v5-prompt tests), with one existing Google GenAI/Python
deprecation warning. Ruff lint and formatting checks passed. JUnit results are
saved at `reports/analysis/gemini-decision-controls-2026-11-07/tests.xml`.
The same suite also finishes validation of the pending G/H controls while
retaining all A–F regression coverage.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/evaluation/test_gemini_decision_diagnostic.py tests/unit/infrastructure/test_gemini_investigation_model.py tests/unit/infrastructure/test_gemini_investigation_protocol.py tests/unit/infrastructure/test_investigation_stopping_prompt.py -q
.\.venv\Scripts\python.exe -m ruff check scripts/diagnose_gemini_investigation.py tests/unit/evaluation/test_gemini_decision_diagnostic.py
.\.venv\Scripts\python.exe -m ruff format --check scripts/diagnose_gemini_investigation.py tests/unit/evaluation/test_gemini_decision_diagnostic.py
```

If I answers while F searches, removing distractor passages is a candidate
explanation; one unpaired stochastic trial is not a causal demonstration. J tests
premature completion: a lucky reference match from prior knowledge can still be
unsupported by E1. K exposes claims about a gap, not the model's internal process
or a guarantee that the cited evidence was considered. Its schema and instruction
changes cannot independently identify which change affected the decision.

All results use controlled saved evidence. A first query mismatch and the replay
notice itself can influence behavior; nothing here measures retrieval quality or
demonstrates that the supplied subset would be returned by that new query. No live
outcome or improvement is claimed from the offline tests.
