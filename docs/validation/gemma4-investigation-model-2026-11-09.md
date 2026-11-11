# Gemma 4 26B investigation model

## Recommendation

Keep Gemini as the default investigation provider for now. Gemma 4 26B is integrated behind `INVESTIGATION_MODEL_PROVIDER=gemma4`, and one saved-evidence case finalized correctly with valid citations. The sample is far too small to establish reliability. The reviewed-slice runs were interrupted by provider HTTP 500 errors for Gemma and a daily quota 429 for the Gemini 3.5 baseline, so there is no completed head-to-head comparison.

## Architecture

The investigation use case remains shared. It still owns search execution, query and step limits, evidence admission, evidence IDs, provenance, matching exchanges, citation validation, and application result semantics. The Google GenAI transport and signed native-history lifecycle are shared between the providers.

Gemini retains its existing `VALIDATED` function-calling mode and JSON response schema. Gemma uses `gemma-4-26b-a4b-it` with the native `search_evidence` declaration and provider-default tool choice; its requests omit `tool_config`, `response_mime_type`, `response_json_schema`, and automatic function execution. This follows Google's Gemma 4 API function-calling example, which supplies tools and lets the model return either a function call or text ([Gemma 4 API guide](https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api), [function calling](https://ai.google.dev/gemma/docs/capabilities/text/function-calling-gemma4)).

Gemma's final text is adapted through a strict status envelope. Answered results require cited answer text; conflicting results require at least two separately cited claims; insufficient evidence has no answer or citations. A grouped citation such as `[E1, E2]` is expanded into the existing inline form without creating IDs. The shared investigation service then validates every citation against the evidence registry. JSON finals remain accepted for compatibility. Missing markers, missing citations, invalid shapes, and unknown evidence IDs are rejected rather than converted to abstention or an answer.

At the application's enforced resource cap, Gemma receives no search tool and a final-only instruction. The normal decision turn always includes the native search tool. The original call content, including provider signatures, is retained in bounded in-memory state and replayed with a function response carrying the original provider call ID. Tool names and argument shapes are validated before the application can execute search.

Quick Answer generation remains configured by `GEMINI_GENERATION_MODEL`. Select the investigation provider with:

```dotenv
INVESTIGATION_MODEL_PROVIDER=gemini   # existing default
# or
INVESTIGATION_MODEL_PROVIDER=gemma4  # gemma-4-26b-a4b-it
```

Both use `GEMINI_API_KEY`; no retrieval or ingestion setting changes.

## Evaluation

All evaluation used pinned local evidence fixtures; the diagnostic harness did not execute retrieval or send reference answers to either model.

| Run | Completed observations | Result |
|---|---:|---|
| Gemma 4 26B, Polish-Russian War two-hop, complete evidence | 1 decision | Correct answer, 2/2 expected evidence IDs, 0 invalid citations; 2 generation requests total. Initial and decision latencies were 3.47s and 3.39s; total usage was 2,269 prompt and 70 candidate tokens. |
| Same case, incomplete evidence | 0 decisions | Follow-up returned HTTP 500 before a search decision. |
| Gemma complete-evidence review slice | 0/3 cases | The first initial request returned HTTP 500. A separate six-case preview was bounded to 12 requests but its first request also returned HTTP 500. |
| Gemini 3.5 matched complete-evidence slice | 0/3 cases | The first initial request returned HTTP 429 because the configured free-tier daily request quota was exhausted. |

The successful Gemma answer was “The mother of the director of the film Polish-Russian War (Film), Xawery Żuławski, is the actress Małgorzata Braunek” with evidence IDs E1 and E2. The diagnostic artifacts are under `reports/generated/gemma4-26b-complete-format-normalized-live-20261109/`, `gemma4-26b-onecase-live-20261109/`, `gemma4-26b-reviewed-three-complete-live-20261109/`, and `gemini-35-reviewed-three-complete-live-20261109/`.

This does not establish a necessary-search rate, unnecessary-search rate, semantic query relevance, unsupported-answer rate, or model-to-model improvement. Historical Gemini baseline results remain the only broad comparison: on its separate 40-question diagnostic, the post-hoc reviewed baseline finalized 20/30 sufficient decisions (66.7%) and searched on 38/38 insufficient cases; that suite had only 20 independent target facts and differs from the new 31-target suite. The new matched live comparison is incomplete. No production-default change is supported.

## Verification and limitations

- The focused provider/configuration/evaluation tests passed: **164 passed**.
- The full unit suite reported **1,135 passed and one unrelated failure**: `test_cloud_build_blocks_publishing_until_all_quality_and_runtime_gates_pass` expects a `ValidateCommit` step absent from the current `cloudbuild.yaml`.
- Ruff check and format check passed for every changed integration and evaluation file. Repository-wide Ruff still reports an unused import in `postgresql_document_repository.py` and import ordering in the existing manual diagnostic script; neither is part of the integration.
- The evaluation is a one-case success plus provider failures, not evidence that Gemma is broadly reliable. It does not justify making Gemma the default.
- The smallest useful follow-up is a matched three-question run (one simple, one two-hop, one three-plus-hop), each with complete and incomplete fixtures: 9 requests per model. Run it after both provider quotas recover; if it completes, expand to the 31 independent targets and repeat trials.
- No deployment, database mutation, ingestion, retrieval execution, commit, or push was performed.
