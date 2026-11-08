# Thinking-level controls R/S — 2026-11-08

`scripts/diagnose_gemini_investigation.py` now supports a second-turn thinking-level
comparison with native search still available. Production code/configuration,
the v5 prompt and existing A–Q model-facing request constructions remain unchanged.
No live Gemini calls were made during implementation.

| Case | Saved function-response evidence | Direct control | Only SDK configuration difference |
| --- | --- | --- | --- |
| R | Original complete E1/E2 | I | Second request: `thinking_config.thinking_level=MEDIUM` |
| S | Original E1 only | J | Same second-request setting |

R/S use `gemini-3.5-flash-lite` through the existing diagnostic-only `--model`
option. Both requests use that selected model. The setting is local to the
diagnostic, without modifying production settings or environment files.

## Exact construction and controls

The first request is identical to I/J: original question, exact production v5
system text, original query-only `search_evidence` declaration, VALIDATED mode,
JSON response MIME/schema, existing generation options and **unset/default
thinking**. No provider default level is inferred or replaced on this first turn.

After a supported native search, the copied second config sets exactly:

```python
thinking_config = types.ThinkingConfig(
    thinking_level=types.ThinkingLevel.MEDIUM,
)
```

Neither `include_thoughts` nor `thinking_budget` is set. The second request retains
the original JSON response constraints, prompt, native function declaration and
calling mode; it does not apply P/Q's schema removal, N/O's two-function mechanism,
K's gap arguments or L/M's path instruction. Output limits, timeout, candidate
count, retries and other generation options are unchanged. The explanatory
`unset_settings` log text accurately notes that thinking is now explicit; it is
not a model-facing instruction or SDK argument.

The original full passages are reused verbatim: E1 is 677 characters, E2 is 1,296
characters. R gets both, S only E1. R/S differ only in those excerpts and derived
evidence-ID lists. No answer/reference, expected query, explicit missing
relationship or sufficiency verdict is inserted into model-facing inputs.

Native roles, first model content, function-call IDs and thought signatures are
preserved through the existing SDK deep-copy path. Saved-result subset/query
metadata is unchanged from I/J and explicitly identifies the replay as simulated
saved evidence, not live retrieval for an arbitrary query. No original native
parts are reconstructed from logged JSON.

## Recording and outcomes

The existing per-call JSON files, plan, comparison and attributed review workflow
are retained. Complete sanitized SDK arguments/responses, native parts, finish
reasons, usage, durations and provider/parsing/protocol errors remain available.
SDK object representations are not raw HTTP wire payloads.

R/S comparison rows additionally record:

- `requested_model`: actual SDK model argument, and `reported_model_version`:
  the provider's returned version when available. A requested alias is not
  silently substituted for a missing reported version.
- `requested_thinking_level`: the level in an instantiated SDK request; null
  means no explicit level. A deferred second-step preview instead has
  `planned_thinking_level=MEDIUM` and no attempted generation.
- `generation_request_attempted`: distinguishes previews/skipped steps from
  budgeted SDK invocations.
- `selected_action`: the one observed native function name or parsed production
  final status; multiple calls are labeled ambiguous, unclassified outputs stay null.
- Existing final answer/query/citation fields, prompt/input tokens, candidate/output
  tokens and duration, alongside `reported_thinking_tokens` from the actual SDK
  `thoughts_token_count` field.
- `provider_error`, existing parsing/protocol errors and a thinking-usage note.

**Zero reported thinking tokens does not prove that no reasoning occurred.**
Missing usage remains null and is not coerced to zero in the reported-thinking
column. An explicit MEDIUM request also does not by itself establish how much
reasoning the provider performed. No hidden rationale or thought process is
inferred from these fields. Reference agreement/citation structure remain separate
from unassigned manual factual-support judgments.

An initial final decision/error/unsupported call ends the case after one request.
The second response is recorded whether it answers, searches, fails or is malformed.
A returned search is never executed, and there is no third generation. A provider
error is retained without retry, fallback to another level or additional request.

## Exact PowerShell commands

Run from `C:\Users\merli\upgaming_task\ai-investigation-engine`.

Provider-free baseline-model preview:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases R S --model gemini-3.5-flash-lite
```

Execute R alone, maximum two generation requests:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases R --model gemini-3.5-flash-lite --execute --max-generation-requests 2
```

Execute S alone, maximum two generation requests:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases S --model gemini-3.5-flash-lite --execute --max-generation-requests 2
```

For fresh I/J comparisons, select the same model:

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases I --model gemini-3.5-flash-lite --execute --max-generation-requests 2
.\.venv\Scripts\python.exe scripts/diagnose_gemini_investigation.py --cases J --model gemini-3.5-flash-lite --execute --max-generation-requests 2
```

Choose one execution command at a time. R+S together reserve four requests per
trial; three trials of either require six. The existing 60-second gap, attempts=1,
stop-on-429 and fresh-directory controls remain. No automatic tool execution,
embeddings, database queries, live retrieval or third request occurs. None of
these execution commands was run during implementation.

## Offline validation and limitations

The provider-free preview is saved at
`reports/generated/gemini-investigation-rs-preview/`: four maximum planned
requests, zero executed, selected model `gemini-3.5-flash-lite`. The second request
is conditional until a real first response supplies original native protocol state.

The preservation receipt is
`reports/analysis/gemini-thinking-level-controls-2026-11-08/preservation-audit.json`.
It verifies 124 production Python files are unchanged, A–Q initial and native
follow-up requests are preserved, R/I and S/J first requests match, second SDK
arguments change only the thinking configuration, and R/S differ only in original
evidence. The production v5 hash remains
`45cada8e1a9c9f2455a4966bedc4e86bcad985cc13086e9879ed97bd7638a510`.
Native comparisons use an explicitly synthetic in-memory fixture that is never
submitted or inserted into executable preview history.

Tests cover native parts/IDs/signatures, full excerpts, configuration isolation,
request limits and early exits, forbidden retrieval, actual model/version fields,
reported thinking counts of null/zero/nonzero, and provider errors without fallback.
An installed-SDK test with in-memory HTTP transport verifies only the second
serialized request adds the MEDIUM config, while tools/schema/mode/prompt stay
unchanged. The SDK uses the nested field name `thinking_level`; no manual wire
rewriting or dependency change is introduced. Client serialization is tested,
not live server acceptance or actual model behavior.

Final checks: **204 tests passed** (170 diagnostic tests plus 34 existing
model/protocol/v5 tests), with one existing SDK/Python deprecation warning. Ruff
lint and formatting checks passed. JUnit results:
`reports/analysis/gemini-thinking-level-controls-2026-11-08/tests.xml`.
The initial SDK test's camelCase assumption was corrected to the installed SDK's
observed nested field name; the requested ThinkingConfig itself was unchanged.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/evaluation/test_gemini_decision_diagnostic.py tests/unit/infrastructure/test_gemini_investigation_model.py tests/unit/infrastructure/test_gemini_investigation_protocol.py tests/unit/infrastructure/test_investigation_stopping_prompt.py -q
.\.venv\Scripts\python.exe -m ruff check scripts/diagnose_gemini_investigation.py tests/unit/evaluation/test_gemini_decision_diagnostic.py
.\.venv\Scripts\python.exe -m ruff format --check scripts/diagnose_gemini_investigation.py tests/unit/evaluation/test_gemini_decision_diagnostic.py
```

This compares an explicit MEDIUM request with an unspecified provider-default
level; it does not establish the baseline's actual internal thinking level.
Provider support/behavior can vary, and any rejection is recorded rather than
silently changing the experiment. More thinking may affect cost, latency,
completion/visible output within the unchanged output limit, or timeouts; those
effects are not measured by fake providers.

One stochastic Jolliffe trial cannot demonstrate a general sufficiency improvement.
Independent initial queries/history, provider conditions, replay relevance and
the saved-result notice are potential confounders. S must still be checked for
unsupported guesses, even when its answer matches the reference. No live outcome,
causal improvement or reasoning quantity is claimed from offline tests.

No commits, pushes, deployment, production configuration or budget changes occurred.
