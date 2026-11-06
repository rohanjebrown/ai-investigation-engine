# Wisigard Investigation correctness investigation — 6 November 2026

## Confirmed execution and causes

Inspected local trace `0bcaff6a7c6a4dac99c2c5159af148e4`, the corresponding Quick Answer trace, application state transitions, and actual Gemini request construction. No provider calls or database data changes were made.

The original question was **Who is Wisigard's father-in-law?** E1 says Wisigard married Theudebert I and Theuderich I was Theudebert I's father. This establishes the requested relationship. The second search returned additional corroborating evidence. Nevertheless, the model selected `Wacho children wives` on its third turn. Wacho appears in E1 as Wisigard's own father; the query moves to an incidental relationship rather than the requested spouse's father. The trace contains no explanation for this choice, so it cannot establish the model's internal cause or prove exactly which relationship it confused.

The traced system instruction exactly matches the current 3,021-character instruction. It already says not to pivot to incidental people and to finalize when all required facts are supported. The tool declares only search, but function-calling mode is **VALIDATED**, not forced ANY; structured final decisions remain available on every ordinary turn. The final schema permits answered/insufficient/conflicting decisions with citations. Final-only mode switches tools to NONE and asks for a grounded final decision. Original question, native model calls, function IDs, and actual tool results are replayed and checked against their original sequence. No missing evidence, forced third search, or unavailable final-answer action was found. The phrase “enough searches” could be evaluated later as wording, but there is no evidence that changing it alone would correct this case; no prompt changes were made.

There was no application-level semantic sufficiency detector; search choices were model-directed. The confirmed application defect was **admission before validation**: append all evidence and its repeated transcript payload, then check context length. At 24,064 characters, the application immediately returned incomplete/context_limit before offering the existing final-only path. It did not exceed a provider limit or receive a provider rejection: the next Gemini request was never made.

## Exact original accounting

`InvestigationTurn.context_json()` accounts a compact provider-neutral transcript, not raw passage length, model tokens, or the entire SDK/wire request. Fourteen unique passages appeared **15 times** across the three exchanges because one result was reused.

| Component | Characters |
| --- | ---: |
| Unique raw passage text | 8,298 |
| Repeated passage text | 730 |
| JSON string escaping/quotes for excerpts | 48 |
| Evidence ID values | 65 |
| Document/version IDs and content hashes | 2,130 |
| Chunk location objects | 317 |
| Source objects | 3,847 |
| Source-span/locator arrays | 6,567 |
| Evidence object keys and punctuation | 1,815 |
| Outer question/query/search transcript structure | 247 |
| **Total** | **24,064** |

The first two actual submitted function-response payloads measured 6,494 and 10,625 characters when compactly serialized. Their native conversation wrappers, system instructions, declarations, and native model parts are separate from the application counter. The over-limit third response was stored internally but never submitted to Gemini. The configured character budget continues to mean this provider-neutral transcript; it is not renamed or presented as a complete provider-token budget.

In this snapshot source metadata is empty; record locators contain only dataset split/example/context-position coordinates. UUIDs, hashes, offsets and sentence indices support internal citation/provenance resolution, not the relationship inference. Entire excerpts, titles and human-readable source kind/display name/revision are retained for the model. Nonempty arbitrary source metadata is retained because other corpora may use it for relevant qualifiers.

## Minimal corrections

- `investigation_records.py`: use a compact, shared model-facing evidence serializer for both accounting and actual Gemini tool responses. Remove document/version UUIDs, hashes, chunk offsets, dataset record locators and source-span arrays from that representation. Keep every field on internal Evidence and public citation/evidence serialization. Add the actual document title; retain whole excerpts and source qualifiers. No text normalization/truncation or new summarization call.
- `investigations.py`: prospectively account the complete next transcript for each whole passage, before mutating the evidence registry. Admit only passages that fit; smaller later hits may still fit. Prior admitted evidence and IDs remain stable. Record the raw result count and a context_limit disposition when capacity excludes results.
- Capacity exhaustion disables subsequent searching via the existing final-only mode. The next turn uses existing generation/turn budgets, with unchanged citation validation. It may answer, abstain, or report conflict. A further tool call is rejected as before; no extra/unbounded generation loop is added. If generation or turn budget is already exhausted, return incomplete rather than pretend to have finalized.
- Reserve the empty tool-response envelope when assessing results. In the extreme case where even that envelope cannot fit, retain the prior transcript, record the executed search internally as context-limited, and finalize from the previously admitted transcript. Do not send an oversized/unpaired tool exchange. This rare branch is regression-tested.

The shared serializer reduces the recorded three-search transcript to **11,862 characters**, a reduction of 12,202 (50.7%). This is an offline serialization measurement, not a live quality or latency result. Repeated excerpts are still repeated in history; no separate transcript-deduplication mechanism was introduced.

## Validation

Deterministic tests distinguish scripted model decisions from application guarantees:

- One search then a valid final decision; two searches then a valid final decision, using the real Gemini adapter and mocked SDK responses.
- An unnecessary third Wacho search with oversized results; intact earlier evidence, empty correctly paired tool response, final-only NONE configuration, and successful cited finalization.
- Whole-passage admission, skipping an oversized result while admitting a smaller later result, exact budget enforcement, preservation of provenance and IDs.
- Model abstention remains abstention even when evidence was admitted; empty/insufficient evidence and conflicting-source cases remain covered.
- Citation of an excluded passage is rejected, and final-only tool requests cannot execute another search.
- Exhausted generation budget does not create an extra call; empty-envelope overflow preserves prior history.
- Existing Quick Answer application and HTTP RAG regressions remain unchanged.

Offline replay of the **actual cached evidence for all three recorded searches**, through the real adapter with mocked SDK search/final responses: status answered, answer **Theuderich I [E1]**, 14 admitted passages, 3 searches, 4 mocked SDK calls, original E1 Evidence/provenance identical. No Gemini service was contacted. This demonstrates completion with scripted responses; a live model could still choose another irrelevant search or abstain.

Initial focused suite: **98 passed**. Existing disposable-PostgreSQL HTTP RAG tests: **4 passed**. Final broader application/provider/API/tracing suite: **253 passed**. Ruff lint/format checks pass for the four changed Python files. No staging/production configuration, database data, retrieval algorithm, context-limit setting, Quick Answer code, system prompt, tool declaration, or final decision schema was changed.

## Remaining limits

The application validates citation membership and structure; that does **not** prove semantic entailment of an arbitrary model-written answer. It cannot determine factual sufficiency or diagnose hidden model reasoning without a separate reviewed mechanism. No relationship-specific answer rule was added. Live evaluation is still needed for early stopping, relationship accuracy, conflicting/ambiguous identities and the slimmer tool representation across corpora. Those evaluations were not run because this task authorizes no Gemini API requests.

Files changed in this task: the two application files above; `tests/unit/application/test_investigations.py`; `tests/unit/infrastructure/test_gemini_investigation_model.py`; and this report. Previously uncommitted tracing and other user changes remain intact. No commit or deployment was performed.
