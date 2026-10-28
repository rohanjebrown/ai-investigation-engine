# Task 16C: Gemini sequential tool-call protocol

## Finding and confidence

The Task 16B HTTP 400 occurred on the recovery request, not on the original uninterrupted two-search conversation. The recovery process executed the saved third query and then called `generateContent` using reconstructed history. Its model messages were synthesized from query strings with IDs such as `task16b-resume-1`. Those messages contained no original Gemini response parts or thought signatures; their function responses also omitted the corresponding IDs.

This is a confirmed request-construction defect. Google's [GenerateContent thought-signature documentation](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures) requires the original signed function-call parts from every sequential step in the current turn. Missing required signatures produce HTTP 400. The installed SDK does not invent or restore those signatures when given reconstructed history. An offline test reproduces that exact unsigned serialization.

The historical provider diagnostic retained only HTTP 400 / `request_rejected`, not Google's original status string or reason. Therefore the exact original server explanation cannot be recovered. Missing required signatures is the strongest documented explanation, and missing response IDs is an additional confirmed defect; this report does not claim the original provider message was observed. Earlier successful requests carried the full native content through `provider_state`, explaining why the uninterrupted path differed from recovery.

The [function-calling guide](https://ai.google.dev/gemini-api/docs/function-calling) and [Gemini 3 guide](https://ai.google.dev/gemini-api/docs/gemini-3) were also reviewed. Current general guides emphasize Interactions, while the signature page explicitly documents the GenerateContent protocol already used by this application. No API or SDK migration was needed.

## Focused correction

- The Gemini adapter retains deep copies of original SDK `Content` objects inside infrastructure. Every part, role, function-call ID, and signature remains attached as returned by Gemini.
- Application actions and exchanges carry an opaque call handle. The old `provider_state` fields were removed from application values; signatures and native messages do not enter domain/application state or evaluation reports.
- Tool responses use the original function name and provider ID. Sequential request history is ordered as user question, original model call, user function response, next original model call, next function response.
- The adapter verifies the original question and call-prefix order before sending history. Missing, reordered, mismatched, or evicted state is rejected locally. There is no reconstruction fallback, synthetic production signature, or validation bypass.
- Gemini 3 tool responses missing a signature are rejected before application tool execution. The existing single-call-per-model-step contract remains; a parallel-call response is rejected in full rather than partially executed.
- Native state is bounded to 1,024 entries of at most 64,000 serialized characters each, released on final decision or client close. Interleaved investigations use distinct handles.

`InvestigateQuestion`, evidence accumulation, search limits, citation validation, retrieval, authorization, and the single-step answer use case retain their behavior.

## Checkpoint limitations

The Task 16B checkpoints contain completed application results and retrieved evidence. They do not contain resumable native Gemini history. Evidence state cannot recreate the provider's signed conversation state.

The unsafe `--continue-pending-id` path is now rejected before reading the database or opening a provider. An interrupted/resource-limited investigation cannot continue after process/client loss from these checkpoints; a new investigation is necessary. Completed mode results remain durable and are skipped on ordinary evaluation resume. Durable native conversation storage was not added.

The evaluator now supports a single frozen case and a single answering mode, requires explicit prior request counts, and requires the full configured generation allowance before starting that mode. This prepares one new Investigation without rerunning Quick Answer or starting an evaluation campaign. New default output is this Task 16C directory; historical Task 16/16B reports were preserved.

## Safe diagnostics

Future failures include the operation, model, HTTP status, allowlisted provider status, allowlisted reason, retryability, investigation step, and cumulative attempted generation count. Known categories distinguish missing signatures, function-response mismatch, invalid history, unsupported configuration, generic invalid arguments, and quota/rate rejection. Unknown/raw reason text is discarded. Keys, prompts, document excerpts, raw SDK messages, and opaque signatures are not logged or exported.

For example, a mocked third-step HTTP 400 returns `INVALID_ARGUMENT` / `missing_thought_signature`; a mocked 429 returns `RESOURCE_EXHAUSTED` / `quota_or_rate_limit`. Tests verify that secret sentinels in raw provider messages cannot reach the diagnostic or exception chain. Retryability is metadata; the investigation adapter still sends exactly one request per model turn and never retries automatically.

## Installed SDK and protocol regression evidence

The installed package is `google-genai==2.33.0`, using `generativelanguage.googleapis.com/v1beta/...:generateContent`. It supports native signature bytes, full `Content` objects, function-call IDs, and `FunctionResponse.id`. No dependency upgrade was made.

Eleven parametrized protocol cases use the real SDK and `httpx.MockTransport`, not a mocked `models.generate_content` method. They exercise SDK response parsing and the actual outgoing JSON body:

1. Initial request, first signed tool call/response, second dependent signed tool call/response, and a parsed final answer with validated application citations.
2. Exact native part and signature preservation in every later request; stable tools and output configuration.
3. Missing signature rejection before tool execution.
4. Incorrect history order, changed question, and mismatched tool query rejection before network access.
5. Evidence-only recovery on a new client refused without another request.
6. Full rejection of unsupported parallel calls.
7. Historical unsigned reconstruction serialized by the installed SDK without automatic repair.
8. Sanitized HTTP 400 reason, step, count, and no retry.
9. Distinct 429 quota and 503 availability diagnostics.
10. Both user-authorized Gemma 4 model IDs preserve their native unsigned content without fabricated signatures. Gemini 3 still requires its own signatures.

Signatures in these fixtures are synthetic offline test data and never leave the mocked transport. Two additional runner tests verify zero external work for unsafe checkpoint continuation and the consumed daily request allowance.

## Verification

Full Python and JavaScript results are recorded in `verification.json`. The combined run passed **643 tests with 4 opt-in live tests skipped** (482 unit and 161 PostgreSQL integration tests). After the user's Gemma authorization, the final unit suite passed **484 tests**, including two additional Gemma protocol cases; production PostgreSQL code was unchanged. JavaScript passed **26 tests**. There is one upstream Google GenAI deprecation warning. The Python command explicitly includes both `tests/unit` and `tests/integration`; plain `pytest` uses the repository's unit-only default and is insufficient to claim PostgreSQL verification. All PostgreSQL integration tests use disposable Testcontainers databases; no application/evaluation schema is changed.

## Live verification and quota

The prior reports account for 20 `gemini-3.6-flash` generation attempts in the current Pacific daily window (6 single-step and 14 investigation attempts). The previously reported allowance is 20 RPD, and active project quota remains independently unverified. No new generation request was sent to that model. The configuration uses an API key without a separately configured project ID, so this task cannot identify or read the active project's limit from local settings.

Google documents [project/model limits and midnight Pacific RPD reset](https://ai.google.dev/gemini-api/docs/rate-limits). The next reset after this work is October 29, 2026 at 07:00 UTC (08:00 Budapest). After the user authorized Gemma 4 26B or 31B alternatives, official [Gemma API documentation](https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api) confirmed native function calling for both model IDs, and the [pricing table](https://ai.google.dev/gemini-api/docs/pricing#gemma-4) confirmed free input/output. Two read-only model-metadata requests confirmed both models were available through the configured key, version `001`, with `generateContent` support, 262,144 input tokens and 32,768 output tokens. Metadata does not expose active remaining quota.

The authorized alternatives used the same frozen two-hop question and persistent database, each with a maximum of 5 searches, 6 turns/generation attempts, 5 results per search, 25 accumulated evidence items, 64,000 context characters, 5 query embeddings, 4,096 output tokens, 12.5-second spacing, and zero retries. Larger evidence/context caps were bounded to accommodate all five allowed searches; production defaults were unchanged. The local daily ceiling of 20 used for Gemma was a conservative execution setting, not a claim about Gemma's actual provider quota. Selected-model prior usage known to this task was zero; the 20 prior Gemini attempts remained separately accounted for.

| Live model | Outcome | Searches | Generation attempts | Usage |
| --- | --- | ---: | ---: | --- |
| `gemma-4-31b-it` | HTTP 500 `INTERNAL` at step 1; provider availability failure | 0 | 1 | Token usage unavailable |
| `gemma-4-26b-a4b-it` | Valid `insufficient_evidence` final decision; evaluator classifies incorrect abstention | 0 | 1 | 314 prompt, 24 output, 0 thinking tokens |
| `gemini-3.5-flash-lite` (subsequently authorized) | Answered `American` with E1/E6 after three sequential searches | 3 | 4 | 18,485 prompt, 117 output, 0 thinking tokens |

The 31B failure occurred before any search; it was not retried. The user-authorized 26B alternative then returned an empty-evidence abstention without using the search tool. Its completed response was not regenerated, and the prompt was not tuned to force a successful benchmark outcome. Both independent reports/checkpoints are saved in the model-named subdirectories. Neither Gemma model exercised live sequential tool use.

The user then authorized `gemini-3.5-flash-lite`. A model-metadata request confirmed availability, and Google's [model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite) documents function calling and structured outputs. One controlled investigation used the same bounds and completed three signed sequential tool calls followed by a final answer. No provider errors or retries occurred. Its queries were `World And Time Enough film director nationality` → `"Eric Mueller" director nationality` → `"Eric Mueller" director "World and Time Enough" american`; later queries used the director name found by the initial search. All original native parts were retained in infrastructure, and each Gemini 3 call passed the adapter's required-signature check.

The answer was `American`, citing E1 and E6. Citation identifiers were valid and strict annotated retrieval/citation coverage was 1.0. The automatic answer scorer retained `incorrect_answer` because the fixed acceptable list contains `United States` alone. This is a demonym/country wording mismatch, but it is not sufficient to declare the answer supported: E1 names the film director, while E6 describes Olympic rower Eric C. Mueller without establishing that he is the same person. The model's answer has an unresolved entity bridge despite complete annotated coverage. No gold labels, synonyms, prompts, or responses were changed. The detailed [Flash-Lite review](gemini-3.5-flash-lite/review.md) preserves this distinction.

Total new Task 16C usage is **3 model-metadata requests, 6 generation attempts, 3 query embeddings, and 3 query token-count requests**, with zero retries and zero document embeddings regenerated. Known daily generation attempts are now Gemini 3.6 Flash 20, Gemma 31B 1, Gemma 26B 1, and Gemini 3.5 Flash-Lite 4. The successful run took 39.18 seconds, including request spacing.

The historical Task 16B sequence remains separately saved and unchanged. The successful Task 16C Flash-Lite sequence, all three result sets, final answer, and usage are in `gemini-3.5-flash-lite/answer-quality.json`. The two Gemma sequences are empty.

The protocol defect is corrected and verified offline and with the user-authorized real Gemini 3.5 Flash-Lite model. **Task 16C's sequential-tool protocol acceptance is met.** Gemini 3.6 Flash itself was not rerun, the exact historical 400 message remains unavailable, and the successful run does not establish answer correctness or general model superiority. No deployment, reindexing, document embedding regeneration, retrieval/storage change, commit, or push occurred.

## Files changed

- `src/investigation_engine/application/investigations.py`
- `src/investigation_engine/application/ports/provider_diagnostics.py`
- `src/investigation_engine/infrastructure/generation/gemini_investigation_model.py`
- `src/investigation_engine/infrastructure/generation/gemini_diagnostics.py`
- `scripts/evaluate_persistent_answer_quality.py`
- `tests/unit/infrastructure/test_gemini_investigation_model.py`
- `tests/unit/infrastructure/test_gemini_investigation_protocol.py`
- `tests/unit/evaluation/test_persistent_answer_runner.py`
