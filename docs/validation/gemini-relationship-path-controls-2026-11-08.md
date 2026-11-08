# Relationship-path diagnostics L/M — 2026-11-08

The existing `scripts/diagnose_gemini_investigation.py` now has two diagnostic
cases for the Jolliffe question's meaning. Both preserve native function calling,
the original search schema, production final-response schema and generation
settings. Their only instruction change is one shared addendum to v5.

| Case | Supplied native function response | Direct control |
| --- | --- | --- |
| L | Original full E1 and E2, without summaries | I: only the system instruction differs |
| M | Original full E1 alone | J: only the system instruction differs |

L/M's model-facing inputs differ only in their supplied evidence and derived ID
lists. Both first requests contain only the original question, with no evidence
or evidence-sufficiency verdict. After a valid initial search, L supplies the
unchanged 677-character E1 and 1,296-character E2; M supplies the unchanged
677-character E1. The source is the saved `jolliffe-v5` benchmark.

## Exact instruction change

The production v5 text remains byte-for-byte unchanged. Only L/M append:

```text
Diagnostic question meaning: paternal grandfather means the father of the
subject's father. The required path is subject -> father -> father, containing
exactly two father relationships. Search only if a relationship required by
that path remains unsupported. Once the complete path is supported, answer
using the evidence. Do not investigate ancestors beyond the requested relationship.
```

This addendum contains no answer, named linking person, reference answer,
expected query, evidence IDs or verdict about the provided evidence. The answer
name appears only where it naturally occurs in supplied source text. The saved
reference remains outside SDK request arguments for post-response assessment.
The addendum is active on both requests, including the initial search decision.

The original `search_evidence` declaration is used, with only its existing
`query` argument; K's extra gap fields are not used. Native calling remains
VALIDATED rather than forced. Final answers, searches and other production
structured decisions remain possible. Model, candidate count, output limit,
timeout, response schema and SDK no-retry/tool-execution controls are unchanged.

## Implementation and logging

Changes are confined to the existing diagnostic, its unit tests and documentation:

- `RELATIONSHIP_PATH_ADDENDUM`/`RELATIONSHIP_PATH_INSTRUCTION` define the shared
  diagnostic prompt, whose hash is saved in the plan.
- `NATIVE_CASES` adds L/M. `CONTROLLED_NATIVE_CASES` shares the existing subset,
  provenance and protocol logging path with I/J/K.
- `supplied_refs()` selects E1/E2 for L and E1 for M.
- `make_request()` changes only L/M's system instruction relative to I/J.
- Existing native history, parser, per-request counter, credential sanitization,
  response recording and manual-review mechanisms are reused.

A valid first search response is kept as an actual SDK `Content` object and
deep-copied without changing its native parts, function-call ID or thought
signature. The matching function response uses the original ID/name and roles
remain `user, model, user`. No signed history is recreated from log JSON.
Controlled-subset metadata identifies the saved query, actual requested query,
exact-match flag and lack of live retrieval, exactly as in I/J. It does not label
the subset complete or incomplete for the model.

Each case writes two separate step records, a plan, comparison and review
template. A second-step preview is conditional: its original native ID/signature
cannot exist before the first response, so none is invented. A first final
decision/error/unsupported call stops the case. A second search is recorded but
not executed; there is no third generation request.

Full sanitized SDK requests/responses, native parts, source/citation IDs, supplied
provenance, usage, timing and parsing/protocol errors remain available. SDK object
representations are not described as raw HTTP payloads. Factual support and
missing-relationship relevance remain separate manual judgments; registered IDs
and reference agreement alone do not establish that a relationship is supported.

## Exact PowerShell commands

Run from `C:\Users\merli\upgaming_task\ai-investigation-engine`.

Provider-free preview of L/M:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases L M
```

Run L alone, maximum two generation requests:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases L --execute --max-generation-requests 2
```

Run M alone, maximum two generation requests:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases M --execute --max-generation-requests 2
```

The corresponding controls, if fresh comparison runs are desired:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases I --execute --max-generation-requests 2
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases J --execute --max-generation-requests 2
```

Choose one execution command at a time. Each reserves two generation requests
and can finish after one. L+M together requires a cap of four per trial; three
trials of either case require six. The default 60-second inter-call pause, no
automatic retries and stop-on-429 behavior are retained. No actual searches,
embeddings or database queries occur. Use a fresh output directory; the default
creates one. None of these execution commands was run during implementation.

## Offline validation

The actual saved passages produced the provider-free preview at
`reports/generated/gemini-investigation-lm-preview/`: four maximum planned
requests and zero executed. Its second steps remain conditional.

The preservation audit is saved at
`reports/analysis/gemini-relationship-path-controls-2026-11-08/preservation-audit.json`.
It verifies all 124 production Python files are byte-for-byte unchanged; A–K
initial request arguments and all earlier native follow-ups remain unchanged;
L/I and M/J differ only by their system instruction on both turns; and L/M differ
only in supplied excerpts/derived ID lists. These offline comparisons use one
explicitly synthetic SDK fixture, never submitted to a provider or placed in
executable preview history. The production prompt SHA-256 remains
`45cada8e1a9c9f2455a4966bedc4e86bcad985cc13086e9879ed97bd7638a510`.

Tests exercise those controls, exact excerpts, native IDs/signatures/roles,
original query-only schema, early finalization, two-request limits even with a
larger cap, final-answer availability, preview safety and poisoned retrieval.
The installed SDK is also exercised through an in-memory HTTP transport for L/M
without contacting Gemini. No model-decision improvement is inferred from fakes.

Final checks: **131 tests passed** (97 diagnostic tests plus 34 existing
model/protocol/v5 tests), with one existing SDK/Python deprecation warning. Ruff
lint and formatting checks passed. The JUnit receipt is
`reports/analysis/gemini-relationship-path-controls-2026-11-08/tests.xml`.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/evaluation/test_gemini_decision_diagnostic.py tests/unit/infrastructure/test_gemini_investigation_model.py tests/unit/infrastructure/test_gemini_investigation_protocol.py tests/unit/infrastructure/test_investigation_stopping_prompt.py -q
.\.venv\Scripts\python.exe -m ruff check scripts/diagnose_gemini_investigation.py tests/unit/evaluation/test_gemini_decision_diagnostic.py
.\.venv\Scripts\python.exe -m ruff format --check scripts/diagnose_gemini_investigation.py tests/unit/evaluation/test_gemini_decision_diagnostic.py
```

## Interpretation and limits

Compare L with I and M with J. L answering with E1/E2 while M searches for the
unresolved link would be consistent with a useful goal-path instruction in this
question. It would not prove a general solution or establish causality from one
stochastic trial. A correct M guess can still be unsupported by its supplied E1.

This is deliberately question-specific: the investigator explicitly supplies the
semantic decomposition into two father links. It does not demonstrate that the
model can derive arbitrary relationship paths unaided. It should not be added
generically to other question types or deployed as a production improvement on
the basis of these diagnostics.

Because the addendum also affects the first request, independent L/I or M/J runs
can generate different initial queries and signed history. Model sampling,
provider conditions, prompt-token overhead and query/replay mismatch remain
confounders. The saved controlled result is not a live search for that query.
Review relevance, identities, direction, citation support and any premature answer
rather than relying only on search counts. The existing v5 identity/conflict
safeguards remain present; the path instruction is not a factual-sufficiency verdict.

No production code, retrieval, schemas or budgets changed. No live requests,
commits, pushes or deployment occurred in this task.
