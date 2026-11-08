# Investigation stopping analysis — 7 November 2026

**The Jolliffe answer was supportable at model turn 2, immediately after search 1.** Both relationship passages were in the actual saved application turn supplied to the model, not merely somewhere in the retrieval pool. Gemini nevertheless requested four more searches. The application already accepts a final decision at that point; it did not require five searches. Its safeguards limit resource use and enforce citation identifiers, but do not independently decide semantic sufficiency or reject a differently worded search that returns the same evidence.

This is an investigation and offline validation record, **not an implemented optimization**. No prompt, orchestration, tool, retrieval, evaluation-framework or metric code was changed. The new code is a characterization-test file. All original saved artifacts and all Python files under `src/investigation_engine/` retained their hashes during analysis. No Gemini, embedding, Voyage, database, deployment, commit or push operation occurred.

## Scope and confirmed saved example

Source run: `reports/evaluation/structured/20261107T054241-2f5866da/`.

Question: **Who is William Jolliffe, 4th Baron Hylton's paternal grandfather?**

Final answer: **William Jolliffe, 4th Baron Hylton's paternal grandfather was Hedworth Jolliffe, 2nd Baron Hylton.** Citations: **E1/E2**.

The run used Gemini `gemini-3.5-flash-lite`, hybrid HNSW/BM25 with RRF, no reranker, five results per search, at most five searches/embedding actions, six model turns/generation requests, fifteen distinct accumulated passages, and a 24,000-character application transcript budget. It recorded five searches, six successful generation RPCs, four token-counting and four embedding RPCs, and **29,386.704 ms** total latency. Search 1 used a cached vector; searches 2–5 were exact cache misses. The recorded generation usage was 29,217 prompt tokens and 232 candidate tokens. No usage was incurred by this analysis.

The saved manifest's hashes of `application/investigations.py` and `infrastructure/generation/gemini_investigation_model.py` match the inspected implementation. The conclusions therefore concern the code that executed this run, rather than an assumed earlier version.

## 1. Current decision-making flow

1. `InvestigateQuestion.execute` validates the question and initializes the evidence registry, search records, tool exchanges and budget counters.
2. Before each model turn, the application checks generation headroom. It sets `final_only` when the evidence-context, distinct-evidence, search-call or embedding-action limit requires finalization. It does **not** compute a semantic “enough evidence” score.
3. The model adapter sends the original question, the preserved native conversation, each admitted tool response, the system instruction, search-tool declaration and final-decision schema. While searching is allowed, function calling is configured as **VALIDATED**, not ANY. During `final_only`, it becomes **NONE** and an explicit finalization instruction is appended.
4. Gemini returns either one `SearchEvidenceCall` or an `InvestigationDecision` (`answered`, `insufficient_evidence`, or `conflicting_evidence`). The adapter parses both forms. “One search per model turn” is a maximum, not a requirement to search on every turn.
5. A decision is accepted immediately after structural/citation validation, **before** another search is considered. An answered decision needs registered citations, but there is no two-search, five-search or budget-exhaustion prerequisite. A zero-evidence answered claim cannot cite a registered source; this legitimate citation requirement is different from a mandatory verification search.
6. For a search action, the application rejects an exhausted search/embedding budget or an identical raw query string. Otherwise it runs the configured search, admits whole hits within the context budget, assigns stable E-identifiers to distinct canonical ranges, and appends a tool exchange. It then asks the model for the next action.
7. A final-only model turn can still answer, abstain or report conflicts. Attempting another search in that state is rejected. A generation/turn limit reached without a final decision produces `incomplete`, not a fabricated answer or abstention.

The model chooses query wording, whether a required fact is missing, whether another search is worthwhile, and whether to return a final decision. The application executes those choices subject to validation and resource guards. It does not implement a hidden planner that requires each relationship hop to have its own query.

Relevant source locations:

| Responsibility | Location |
| --- | --- |
| Turn loop, final-only conditions and immediate decision branch | `application/investigations.py:250` |
| Exact query/search/embedding rejection | `application/investigations.py:140` |
| Search execution and evidence admission | `application/investigations.py:386`, `:462` |
| Citation validation / canonical registry | `application/investigations.py:513`, `:534` |
| Model instructions / final decision wording | `infrastructure/generation/gemini_investigation_model.py:47`, `:78` |
| Function-calling modes and final-only instruction | `infrastructure/generation/gemini_investigation_model.py:205` |
| Native history construction / action parsing | `infrastructure/generation/gemini_investigation_model.py:327`, `:368` |
| Model-facing evidence fields | `application/investigation_records.py:89` |

Paths in this table are below `src/investigation_engine/`.

## 2. Was the required evidence actually visible?

At turn 1 the model had the original question and no tool evidence. Search 1 returned five passages, all of which were admitted to the first tool exchange. **At turn 2**, the saved model-facing evidence included:

- **E1 — William Jolliffe, 4th Baron Hylton:** “Hylton was the son of Hylton Jolliffe, 3rd Baron Hylton, and Lady Alice Adeliza Hervey.” The paragraph lead identifies this Hylton as William George Hervey Jolliffe, 4th Baron Hylton.
- **E2 — Hylton Jolliffe, 3rd Baron Hylton:** “Hylton was the eldest son of Hedworth Jolliffe, 2nd Baron Hylton, and Lady Agnes Mary Byng.” The paragraph lead identifies this Hylton as the 3rd Baron. The same excerpt also names his wife Alice Adeliza Hervey and the son who succeeded him, William George Hervey Jolliffe, corroborating the family link in E1.

Those two local passage assertions establish **4th Baron → father: 3rd Baron → father: Hedworth, 2nd Baron**, preserving the paternal relationship. This assessment follows the excerpt content and identifying context, not just annotated-sentence flags or matching names. The first context contains no recorded contradiction to this chain. Investigating the parents of the already identified grandfather is not another required hop of this question.

The retrospective canonical sentence checks confirm both annotated facts at the exact saved bounds: E1 `[119,206)`, E2 `[129,220)`. These annotations are used **only to audit this saved run**, never as a production stopping input. The passages came from authorized occurrences with the same canonical contents; their actual document-version provenance and citation mapping were preserved in replay.

E3 was a biography of the 1st Baron, E4 a short biography of Hedworth, and E5 a historical Henry Hylton biography. They were additional context, not missing links needed to derive the requested paternal grandfather. Their presence may have offered distractors; the trace does not prove a distractor caused a particular model decision.

## 3. Complete turn-by-turn reconstruction

“Visible IDs” below means the union of evidence references across the admitted tool exchanges in that saved turn. It is distinct from both the most recent retrieval and the number of passage appearances in the growing transcript. Application context characters describe `InvestigationTurn.context_json()`, not raw SDK request bytes or prompt tokens.

| Model turn | Accumulated and visible IDs before action | Passage appearances / context chars | Required relationship supported? | Actual model action | Search result and information gain | Could a final answer be supported then? |
| --- | --- | ---: | --- | --- | --- | --- |
| 1 | None | 0 / 113 | No evidence yet | Search 1: `William Jolliffe 4th Baron Hylton paternal grandfather` | Five new canonical passages, E1–E5; both target links included and admitted | No, before the search. Yes after its result. |
| 2 | E1–E5 | 5 / 5,698 | **Yes, E1/E2** | Search 2: `"Hedworth Jolliffe, 2nd Baron Hylton" parents` | Same five canonical passages reordered; zero new passages or annotated facts | **Yes: earliest supported final-answer turn** |
| 3 | E1–E5 | 10 / 11,277 | Yes | Search 3: `"Hedworth Jolliffe" 2nd Baron Hylton father mother` | Same five canonical passages again; zero new passages or annotated facts | Yes |
| 4 | E1–E5 | 15 / 16,861 | Yes | Search 4: `"Hedworth Hylton Jolliffe" son of William George Hylton Jolliffe` | Reused E4/E1/E2/E3 and added E6, a William Jolliffe (1745–1802) biography; no new target link | Yes |
| 5 | E1–E6 | 20 / 20,627 | Yes | Search 5: `"Hedworth Jolliffe, 2nd Baron Hylton" father` | Returned known E4/E2/E1/E3/E5. Only E4/E2/E1 fit in the new exchange; no new canonical passage or target fact | Yes |
| 6 | E1–E6 | 23 / 23,446 | Yes | Final `answered` decision, citing E1/E2 | Correct saved answer; no search | Yes |

All six unique registry passages were still present somewhere in the last model transcript. E3/E5 excluded from the **last appended exchange** remained in earlier exchanges; they were not removed from the whole context. E1/E2 were visible throughout turns 2–6.

Search 4's one new excerpt states only that William Jolliffe (1745–1802) was a British politician serving in the Commons from 1768 to 1802. It does not establish a previously missing relationship in the requested two-link chain. Therefore it would be inaccurate to say every later search returned literally no new evidence, but accurate to say none improved the target's annotated coverage, and none was required to support the saved answer.

The first five turns had `final_only=false`. At turn 6, search count 5, embedding-action count 5 and the search-5 context-limit disposition all required final-only handling. The unique registry was only 6/15; the distinct-evidence cap was not responsible. The sixth generation request was permitted and produced the answer; reaching the generation threshold did not invalidate that successful result. The application did not force the answer's content, only disable further search and require a final decision type.

## 4. Confirmed causes, enabling mechanics and uncertainty

### Confirmed behavior

- **Gemini selected the extra searches.** The saved actions explicitly request them; they are not hidden retries, automatic retrieval probes or application-generated verification queries.
- **The query target drifted beyond the needed relationship.** After E1/E2 already established the target grandfather, later queries sought that grandfather's parents, father or connection to the 1st Baron. This describes the recorded queries, not an inferred private rationale.
- **Semantic/result repetition is not an application stopping condition.** The exact-string guard did not apply because the five query strings differ. Searches 2 and 3 had no canonical novelty, but remained ordinary successful searches.
- **Canonical registry deduplication does not deduplicate the native tool transcript.** Previously known IDs are reused, but full excerpts are repeated in each function response. The final transcript contains 23 appearances of 6 unique passages, including repeated E1/E2. This consumed context and input tokens, and search 5 hit context admission limits despite the small registry.
- **The application delegates sufficiency to the model.** It does not independently validate whether the original relationship has been completed. Citation validation is structural, not a semantic verifier. A new characterization test demonstrates that a wrong father/father-in-law assertion with a registered citation can still pass application citation validation.
- **The protocol already supports early finalization.** Both real application replay and the adapter with a mocked SDK accept an early final JSON decision while search mode remains VALIDATED. No minimum search count blocks turn-2 completion.

### Prompt and presentation findings

The system instruction already says to identify facts required by the original question, avoid incidental pivots and **“Finalize as soon as ALL required facts are supported”**. It does not require verification of every answer or a fixed number of searches. It also correctly calls for identity corroboration when context conflicts or a required identity link is genuinely unresolved.

Later in the same instruction, the sentence **“Return a structured final decision when enough searches have been performed”** introduces search-count wording where evidence sufficiency would be clearer. This is a plausible source of ambiguity, **not a proven causal explanation** for this sample. “Enough” does not explicitly mean five, and the earlier instruction already requests timely completion.

The tool declaration simply describes a bounded evidence search; it does not require calls. The model sees the original question and all preserved exchanges, with evidence ID, title, complete admitted excerpt and human-readable source fields. It does not receive gold labels, a server-derived required-fact graph, novel-evidence counts or an explicit no-progress indicator. `remaining_searches`/`remaining_generations` in application trace emissions are diagnostic values, not fields sent by `_contents` to Gemini. The function response includes search sequence, but the ordinary tool transcript does not announce remaining numerical budgets.

### What cannot be concluded

The trace records no free-form reason explaining why Gemini chose those searches. It does not establish whether the model misread the genealogy, sought reassurance, was distracted by another biography, or responded to the “enough searches” wording. Zero reported thinking tokens is not evidence that no internal computation occurred. The system must not invent hidden reasoning or claim that Gemini understood E1/E2 merely because those excerpts were available.

The authoritative saved artifacts record model-port turns/actions, not the complete signed native SDK messages. Offline reconstruction restores source display names from saved titles and uses the empty default metadata, reproducing every saved application context length, evidence ID/order, visible text and admission decision. The matching production source hashes and formatter explain how excerpts are forwarded into tool responses. This is strong evidence of content availability, **not a byte-for-byte reconstruction of unrecorded native signatures or a record of model comprehension**.

## 5. Recommended minimal improvements — not implemented

**First candidate: a small prompt-only clarification of the existing finish rule.** Replace the search-count phrasing with evidence-based wording, explicitly state that there is no minimum number of searches, and distinguish unresolved required links from ancestors/attributes beyond the original question. Keep the existing identity, missing-evidence, conflict and abstention safeguards.

Illustrative candidate text for review, not a source-code change:

> After each tool result, decide whether the admitted evidence supports every relationship needed for the original question, including any necessary identity link. If so, return the final cited decision now. There is no minimum number of searches; one search may supply multiple required links. Search again only for a specific missing required fact, unresolved identity issue or relevant contradiction. Do not extend an already completed relationship chain into unrelated attributes or additional ancestors. If evidence remains missing, continue with a relevant search while budget permits, or return the appropriate insufficient/conflicting-evidence decision.

This targets a concrete recorded failure pattern and removes an identified ambiguity. It does not make one search a goal, suppress justified corroboration, use gold annotations, add a classifier, or add a supervisor LLM call. Because most of this intent is already in the prompt, the incremental benefit is uncertain and must be measured rather than assumed.

| Option | Assessment and priority |
| --- | --- |
| Clarify existing finish instructions and remove search-count wording | Smallest first candidate. No orchestration, schema, extra call or evidence changes. Proposed only. |
| Expose progress such as new IDs/reused IDs and a neutral “no new passages” indicator in model-facing results | Possible second candidate if prompt clarification is insufficient. Do not equate zero novelty with sufficient evidence; an alternative query may still be necessary. This changes presentation and needs native-history/protocol regression checks. |
| Compact/deduplicate repeated evidence presentations | Could reduce token growth, but higher risk: signed native call history, function-response correspondence, original provenance and source availability must be preserved. Do not casually rewrite previous exchanges or send IDs without resolvable text. Defer behind the simpler candidate. |
| Normalize or semantically detect repeated queries | Exact duplicates already terminate; these were distinct queries. Any normalization must preserve meaningful qualifiers/operators. Semantic deduplication alone cannot establish sufficiency. Not the first intervention. |
| Gracefully offer final-only answer/abstain/conflict after an exact repeated query, when generation headroom remains | A separate resilience candidate: current behavior is an explicit incomplete result with no second retrieval. It would not explain/fix this run's distinct-query loop and would require deliberate orchestration changes. |
| Force a final answer after one search, two no-gain searches, full gold coverage, or a reference-name match | Reject. Would prematurely stop genuinely dependent or ambiguous investigations and could expose evaluation labels to production behavior. |
| Add a separate sufficiency/answer-construction model call | No strong justification from this trace. The existing next-action turn can already generate the final answer. Adds latency, tokens and another failure point. |
| Reduce all search/turn budgets | Not an evidence-based fix; would constrain valid Princess Alexandra/missing-evidence/conflict cases. Keep budgets for safety, not as completion targets. |

## 6. Expected effects and measured work

An offline scripted counterfactual supplies the **already generated saved final decision**, with unchanged E1/E2 citations, at turn 2 using the exact first-search hits. The unchanged use case accepts it with 1 search and 2 model calls, before final-only mode, and resolves the same source-version provenance. This proves feasibility and the absence of an orchestration barrier. It does **not** show that a revised prompt would cause Gemini to make that decision.

| Axis | Current observation | Conditional effect if correct turn-2 finalization is achieved |
| --- | --- | --- |
| Search count | 5 | 1: four searches removed, 80% fewer for this example |
| Generation calls | 6 | 2: four calls removed, 66.7% fewer for this example |
| Embedding RPCs | 4 countTokens + 4 embedContent; first search cached | The eight observed later-query RPCs would be unnecessary on this path; future cache conditions can differ |
| Accuracy | Correct saved final answer; both facts already visible | Could preserve this answer, but real-model early-decision accuracy is unmeasured. Premature stopping remains the principal risk. |
| Input tokens | 29,217 measured total | The first two observed requests used 3,104; turns 3–6 used 26,113. Same initial/finalization input state would avoid that later transcript growth. This is a retrospective token allocation, not a new measured optimized run. |
| Output tokens | 232 measured total | Fewer tool actions should reduce output overhead; the content/length of a new early final response is unknown. |
| Latency | 29.387 seconds, including pacing/network/provider/embedding/database work | Likely lower with fewer calls, but no exact optimized latency can be established offline. |
| Context pressure | 23,446 characters, 23 appearances, six unique passages | A turn-2 final input uses the saved 5,698-character state with five unique passages. |

Observed searches 2–5 occupied **8,773.018 ms** in their sequential search boundaries. Observed model calls 3–6 occupied **10,870.285 ms**, including pacing. These are distinct call boundaries, not SQL/embedding substage totals; embeddings are already inside search time. Do not add inclusive substages again. Do not simply subtract these figures from 29.387 seconds to advertise a measured faster answer: turn 2 would produce a final response instead of the observed search action, with unknown response latency, token use and possible pacing differences.

No statistically strong accuracy or latency claim follows from this single saved example. A later authorized paired evaluation would be needed to establish whether a prompt clarification improves actual model decisions without harming harder cases. That evaluation is not authorized or performed in this offline task.

## 7. Offline validation and regression gates

The new `tests/unit/application/test_investigation_stopping.py` contains **12 characterization cases**, all passing:

- Jolliffe: one search provides both links; the application accepts the next-turn final decision with `final_only=false` and unchanged citation provenance.
- Distinct redundant queries: the unchanged implementation can repeat known excerpts until the configured search limit, while the canonical registry remains deduplicated.
- Wisigard: a scripted decision uses spouse → spouse's father and does not select the person's own father. A separate characterization explicitly exposes the structural validator's inability to detect a wrong but registered relationship citation.
- Princess Alexandra: the first result identifies her mother; a contextual follow-up discovers the mother's father. No premature one-search finalization is imposed.
- Missing evidence: an empty first search can be followed by a productive contextual query.
- No-new-evidence detour: a repeated result set does not prevent a subsequent useful alternative query while a required link is still missing. A hard “zero novelty means stop” heuristic would fail this case.
- Contradictory evidence: follow-up resolution is permitted; absent a resolution, an explicit conflicting-evidence outcome preserves both citations instead of choosing a premature answer.
- Identical query: second retrieval/embedding is avoided and the result is explicitly incomplete. Whitespace variants characterize the existing exact-string boundary.
- Actual Gemini adapter with mocked SDK: an early JSON final decision is accepted while tool mode remains VALIDATED. No live API request is made.

The broader application plus Gemini adapter/protocol suites passed **276 tests**, including the new cases. Existing tests cover context/evidence overflow, rejected citations, search/generation/turn exhaustion, native signed-history handling and final-only behavior. The SDK protocol suite uses an offline HTTP mock transport, not the provider service. One existing Google SDK deprecation warning remains. Ruff, formatter and whitespace checks pass.

The local saved-trace replay additionally reproduced all six original turns, all five result/admission sequences, six canonical registry IDs, final-only transition, context sizes and correct E1/E2 final citations. The early saved-decision replay used one search/two scripted model calls with the same final answer and cited evidence. Original run artifacts and production/evaluation source hashes were checked unchanged.

**What these tests validate:** the existing application can represent early supported completion and genuinely necessary continued investigation while preserving protocol, budgets and provenance. **What they do not validate:** that Gemini recognizes sufficiency correctly, that it will obey candidate prompt wording, or that a semantic relationship is true merely because a fake model chose it. No candidate prompt or orchestration change has been deployed or claimed effective.

Before implementing/adopting any candidate, preserve these gates and add candidate-specific comparisons: supported final-decision rate, needed follow-up retention, wrong-relation and same-name failure checks, conflict handling, citation/provenance identity, per-turn novelty/context effects and total API/token usage. Prompt behavior ultimately requires an explicitly authorized model comparison; saved traces and scripted tests cannot manufacture that evidence.

## 8. Reproduction, artifacts and remaining uncertainty

All paths below are relative to `C:\Users\merli\upgaming_task\ai-investigation-engine`.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/application tests/unit/infrastructure/test_gemini_investigation_model.py tests/unit/infrastructure/test_gemini_investigation_protocol.py -q --junitxml=reports/analysis/investigation-stopping-2026-11-07/tests.xml
.\.venv\Scripts\ruff.exe check tests/unit/application/test_investigation_stopping.py
.\.venv\Scripts\ruff.exe format --check tests/unit/application/test_investigation_stopping.py
```

| Artifact | Path |
| --- | --- |
| This report | `docs/validation/investigation-stopping-analysis-2026-11-07.md` |
| Original execution (unchanged) | `reports/evaluation/structured/20261107T054241-2f5866da/` |
| Turn/search/provenance reconstruction and source hashes | `reports/analysis/investigation-stopping-2026-11-07/analysis.json` |
| Deterministic test receipt | `reports/analysis/investigation-stopping-2026-11-07/tests.xml` |
| Permanent characterization tests | `tests/unit/application/test_investigation_stopping.py` |
| One-off local replay helper (ignored) | `.cache/analyze_investigation_stopping.py` |

The source artifacts establish content availability and action selection, not the model's comprehension or private rationale. They do not prove which wording or presentation change would alter those actions, nor whether a separate verification search would be worthwhile on other questions. Raw signed native messages are not fully present in these saved artifacts; no hidden reasoning is reconstructed. Canonical novelty is a passage-identity observation, not a semantic sufficiency oracle. A new passage can be irrelevant, and a repeated passage can still contain an unresolved fact. The recommended first intervention remains a reviewable prompt clarification, with all production behavior unchanged pending an explicit implementation decision and validation.
