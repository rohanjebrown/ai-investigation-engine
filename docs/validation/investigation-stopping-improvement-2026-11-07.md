# Minimal Investigation stopping clarification — 7 November 2026

Implemented a **prompt-only clarification** of when Investigation should answer or search again. The existing ambiguous search-count sentence is gone. The original prompt is preserved exactly; the revised prompt adds 41 words (437 → 478) without requesting more reasoning output or adding any model call.

**No live comparison was executed.** Offline tests validate wording, prompt forwarding, protocol, budgets and existing decision paths. They do not establish that Gemini will search less, answer more accurately, or obey the revised wording. A controlled three-question/two-prompt pilot is prepared locally for future explicit authorization.

## Exact prompt changes and rationale

Production source: `src/investigation_engine/infrastructure/generation/gemini_investigation_model.py`. Guidance metadata changed from `grounding-v4-scoped-conflicts` to `grounding-v5-evidence-based-stopping` so the instruction revision can be identified.

The original paragraph portion was:

```text
Do not pivot to incidental people or works unless evidence links them to a required
fact. A name merely appearing in a result is not a reason to investigate it. Finalize
as soon as ALL required facts are supported; do not spend searches on unrelated details.
If a required fact remains unsupported, abstain rather than answer only part of the question.
```

It is now:

```text
Do not investigate an entity's unrelated relationships merely because it appears in a result.
There is no minimum search count; one search may establish multiple required links.
Finalize as soon as admitted evidence supports ALL required relationships and conclusions.
Further searches must target a specific missing fact, unresolved ambiguity, or material
contradiction relevant to the ORIGINAL question. Avoid repeated or rephrased searches
that do not address such a gap. Never invent a relationship to finish early.
If a required fact remains unsupported, search for it while a relevant bounded search
remains; otherwise return insufficient_evidence rather than a partial or guessed answer.
```

The later sentence:

```text
Return a structured final decision when enough searches have been performed. Use
```

became:

```text
Return a structured final decision based on evidence sufficiency, not search count. Use
```

| Clarification | Why it is needed |
| --- | --- |
| Do not investigate unrelated relationships merely because an entity appears | Targets the recorded drift from the original requested relationship into an already identified entity's ancestry. It is generic, not a Jolliffe-specific rule. |
| No minimum search count; one search may supply several links | Makes explicit what orchestration already permits. It neither mandates one search nor lowers any budget. |
| Finish when admitted evidence supports all required relationships/conclusions | Grounds completion in the evidence actually available to the model, including relationship interpretation. Retrieval or a name match alone is insufficient. |
| Further searches need a specific missing fact, ambiguity or material contradiction | Preserves genuinely necessary dependent follow-ups and identity/conflict resolution. |
| Avoid repeated/rephrased searches without such a gap | Addresses unproductive rewording without introducing a hard novelty-based stop rule. |
| Never invent a relationship to finish early | Guards against interpreting early stopping as a requirement to manufacture or guess a conclusion. |
| Search for a missing requirement while a relevant bounded search remains; otherwise abstain | Keeps missing-evidence continuation and appropriate abstention explicit. |
| Replace “enough searches” with evidence sufficiency | Removes identified ambiguity. The analysis did not prove that the old wording caused the observed searches; this remains a hypothesis to evaluate. |

The identity-corroboration paragraph, untrusted-source/citation paragraph, final-only behavior, concise-answer requirements, conflict handling and all instructions after “answered only when …” are unchanged. No reference answers, dataset names, benchmark entities or gold annotations were added to the prompt.

## Preserved original and isolation audit

| Snapshot | Guidance | SHA-256 |
| --- | --- | --- |
| `docs/validation/investigation-stopping-prompt-v4.txt` | Original v4 | `544430c1ce4f48827d9c45079b715eb7f61591ce73db61b748094a34900f87a2` |
| `docs/validation/investigation-stopping-prompt-v5.txt` | Active v5 | `45cada8e1a9c9f2455a4966bedc4e86bcad985cc13086e9879ed97bd7638a510` |

The original snapshot contains the exact prior `SYSTEM_INSTRUCTION`, including its terminal newline. The revised snapshot equals the active constant. An exact unified diff is in `.cache/investigation-stopping-comparison/prompt.diff`.

A before/after source audit confirms that the **only changed Python source file under `src/investigation_engine/` in this task is the model adapter**. Its AST is identical after masking the two instruction/version assignments. All functions, tool definitions, schemas, model configuration and other constants are identical. Every other source file, including all evaluation modules, has the same SHA-256 as at task start. Earlier uncommitted work in the workspace is preserved and is not attributed to this prompt change.

Specifically unchanged: `InvestigateQuestion`, search/generation/turn budgets, context limits, evidence accumulation/admission, canonical citation provenance, citation validation, early-decision execution path, exact-query rejection behavior, native tool-response construction, generation-model settings, HNSW/BM25/RRF, Voyage behavior, database schema and evaluation metrics/framework. No heuristic was added to stop after zero novel passages.

## Files modified or added by this task

| File | Purpose |
| --- | --- |
| `src/investigation_engine/infrastructure/generation/gemini_investigation_model.py` | Only production change: system instruction text and guidance-version label. |
| `docs/validation/investigation-stopping-prompt-v4.txt` | Exact preserved original instructions. |
| `docs/validation/investigation-stopping-prompt-v5.txt` | Exact active instructions for reproducible comparison. |
| `tests/unit/infrastructure/test_investigation_stopping_prompt.py` | Ten prompt-content, safeguard-preservation, snapshot and mocked-adapter forwarding cases. |
| This report and `docs/README.md` | Change rationale, validation, isolated comparison protocol and commands. |
| `.cache/investigation-stopping-comparison/` (local ignored preparation) | Fixed question selection, identical starting caches, comparison plan, launch recipe, pending manual worksheet, source-change audit and offline replay/test receipts. |

No evaluation module or CLI was edited. The comparison launch recipe is a local wrapper around the existing evaluator, not a new Investigation algorithm or replacement evaluation system.

## Offline regression results

**286 relevant application, Investigation adapter/protocol and prompt tests passed**, including the 12 stopping-behavior cases from the prior analysis and ten new prompt checks. Existing fake-model regressions cover:

- **Jolliffe:** one search can establish both required links; an immediate next-turn final answer is accepted before any final-only limit, preserving E1/E2 provenance.
- **Princess Alexandra:** identifying a parent does not end the workflow; a contextual dependent follow-up can obtain the grandparent.
- **Wisigard:** the scripted valid path uses spouse → spouse's father. A separate characterization retains the known limitation that structural citation validation cannot itself detect a wrong father/father-in-law assertion. No semantic correctness guarantee is inferred from passing that validator.
- **Missing evidence:** empty or insufficient first results can lead to a useful later query rather than an invented answer.
- **Contradictions:** a further resolution search is permitted; unresolved conflict can produce an explicit cited conflict outcome rather than an unsupported final answer.
- **Repeated searches:** identical-query termination remains incomplete without another retrieval/embedding, whitespace variants retain the existing exact-string boundary, and a repeated result set can still be followed by a useful alternative while information remains missing.

The new tests check all seven requested instruction requirements, unchanged identity/untrusted-source/citation/conflict safeguards, absence of benchmark-specific text, exact prompt snapshot hashes, and the revised text actually forwarded to the mocked SDK with the same VALIDATED tool mode, schema and output-token configuration.

Saved Jolliffe actions were also replayed through the **actual Gemini adapter and unchanged use case with a mocked SDK** under both snapshots. Complete scripted history: five searches/six model calls under each prompt. Scripted early saved final decision: one search/two model calls under each. For corresponding calls, serialized conversation contents and every configuration field other than system instruction had equal hashes; final answers and citation provenance matched. Synthetic native signatures were used only in offline fixtures, never submitted externally.

This deliberately does not manufacture a prompt benefit: fixed fake responses do not change when instructions change. The replay proves forwarding/isolation and supported mechanics, not that Gemini would now choose the early decision. Token usage and live latency were not measured in these mocks.

Ruff, formatter and whitespace checks pass. One existing Google SDK deprecation warning remains. All validation was local: no provider requests and no database queries.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/application tests/unit/infrastructure/test_gemini_investigation_model.py tests/unit/infrastructure/test_gemini_investigation_protocol.py tests/unit/infrastructure/test_investigation_stopping_prompt.py -q --junitxml=.cache/investigation-stopping-comparison/tests.xml
.\.venv\Scripts\ruff.exe check src/investigation_engine/infrastructure/generation/gemini_investigation_model.py tests/unit/infrastructure/test_investigation_stopping_prompt.py
.\.venv\Scripts\ruff.exe format --check src/investigation_engine/infrastructure/generation/gemini_investigation_model.py tests/unit/infrastructure/test_investigation_stopping_prompt.py
```

## Prepared controlled comparison — not executed or authorized

The same three existing dev question IDs are fixed in both arms:

| Case | ID | Purpose |
| --- | --- | --- |
| William Jolliffe, 4th Baron Hylton | `5d6a128c0bb011ebab90acde48001122` | Evidence can already support the final relationship after one search. |
| Wisigard | `ab94845c0baf11ebab90acde48001122` | Correct relationship interpretation rather than selecting the subject's own father. |
| Princess Alexandra of Hanover, born 1999 | `77f5a3dc0bb011ebab90acde48001122` | Dependent follow-up remains possible when the initial result lacks a grandparent link. |

The existing Princess Alexandra capitalization override is retained. No cases are resampled, downloaded, inserted or indexed. Missing/conflicting/repeated-result controls remain covered offline; these three live factual questions would not by themselves establish general abstention/conflict performance.

Both arms use the existing shared `investigation_stage` corpus, hybrid retrieval, HNSW candidate/ef 400, semantic/lexical channel sizes 20/20, RRF k=60, no reranker, `gemini-3.5-flash-lite`, 1,024 output tokens, 30-second provider timeout, and the existing Investigation limits: six turns/generation calls, five searches/embedding actions, five results per search, fifteen accumulated passages and 24,000 context characters. One measured attempt per question, zero warmups, concurrency one. The process-level request timeout is 900 seconds including pacing. No generation model or production budget setting is changed.

Each arm starts with a separate byte-identical copy of the same compatible query cache. All three original questions have cached vectors; model-generated queries can still be misses. The global cache and frozen seed are unchanged. Vectors learned by the original arm do not warm the revised arm. The corpus is not frozen across requests, and SQL/server caches and API/network conditions remain potential confounders.

The prepared order is original then revised. That fixed order is recorded rather than hidden; six stochastic attempts are only a pilot. If a later authorized larger comparison is warranted, counterbalance order and increase coverage under a new budget. Do not use the historical Jolliffe run's 29.4 seconds as a matched baseline for newly warmed caches or changed provider conditions.

### Exact live commands, for future explicit authorization only

Run from `C:\Users\merli\upgaming_task\ai-investigation-engine`. **Do not execute these commands under this task's no-live-request instruction.** Current project quota/billing status and approval for both arms must be verified first; historical free-tier confirmations are not authorization for a new pilot.

The local launch recipe defaults to plan-only output. These safe commands were executed to validate the prepared paths and bounds and made no calls:

```powershell
.\.venv\Scripts\python.exe .cache/investigation-stopping-comparison/run_arm.py --arm original --output reports/evaluation/structured/stopping-v4-pilot
.\.venv\Scripts\python.exe .cache/investigation-stopping-comparison/run_arm.py --arm revised --output reports/evaluation/structured/stopping-v5-pilot
```

After explicit authorization, the exact paired live commands would be:

```powershell
.\.venv\Scripts\python.exe .cache/investigation-stopping-comparison/run_arm.py --arm original --output reports/evaluation/structured/stopping-v4-pilot --execute-live --allow-generation --max-generation-requests 18 --allow-live-embeddings --max-embedding-requests 30
.\.venv\Scripts\python.exe .cache/investigation-stopping-comparison/run_arm.py --arm revised --output reports/evaluation/structured/stopping-v5-pilot --execute-live --allow-generation --max-generation-requests 18 --allow-live-embeddings --max-embedding-requests 30
```

The wrapper passes the fixed selection, per-arm cache and the above retrieval/serving options to the **unchanged** `scripts/evaluate.py` entry point. In the original arm it temporarily sets the module's instruction/version constants to the preserved v4 snapshot; all execution, formatting, tool and retrieval code remains current and identical. The revised arm uses the preserved active v5 snapshot. Each output would contain `comparison-prompt.json` recording the exact effective prompt hash/version, plan/cache/selection hashes, runtime override and evaluator arguments. This is necessary because a source-code hash alone would still point to the v5 adapter file during the v4 runtime override.

The wrapper rejects changed prompt/selection snapshots, a changed arm cache (for example after warming in an earlier attempt), existing/out-of-scope output directories, missing explicit API flags/caps and drift in the recorded model/Investigation settings. It does not claim to verify project billing or authorization by itself; every new execution requires its own approved budget even when all queries would be cache hits. API retries remain disabled. A 60-second initial cooldown and existing evaluation pacing are passed through; measured latency includes pacing within the request, not that initial cooldown.

### Maximum API usage

| Scope | Investigations | Maximum generation RPCs | Maximum token-count RPCs | Maximum embedding RPCs | Combined embedding-related RPCs |
| --- | ---: | ---: | ---: | ---: | ---: |
| One prompt arm | 3 | 18 | 15 | 15 | 30 |
| Both arms | 6 | **36** | **30** | **30** | **60** |

Maximum paired total is **96 provider RPCs**, comprising 36 generation and 60 embedding-related RPCs. The conservative bound assumes five uncached model-generated searches per attempt, including countTokens; original-question cache coverage does not guarantee dynamic-query hits. Actual use may be lower. There are no warmups, repeated trials, Voyage calls, document embeddings or paid LLM judge. Each arm has a separate cap, and the paired maximum must be approved together; starting another process/run is a new usage decision. No allowance is assumed available or newly verified now.

## Measurement and review protocol

The existing evaluator already records the execution data; no metrics or framework changes are needed. After an authorized run, rescore both saved outputs without further API/database work:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py rescore reports/evaluation/structured/stopping-v4-pilot --output reports/evaluation/structured/stopping-v4-pilot-rescored
.\.venv\Scripts\python.exe scripts/evaluate.py rescore reports/evaluation/structured/stopping-v5-pilot --output reports/evaluation/structured/stopping-v5-pilot-rescored
.\.venv\Scripts\python.exe scripts/evaluate.py compare reports/evaluation/structured/stopping-v4-pilot reports/evaluation/structured/stopping-v5-pilot --left-mode investigation_hybrid --right-mode investigation_hybrid --output reports/evaluation/structured/stopping-v5-pilot/paired-comparison.json
```

Use the existing result-bound factual review packets for answer correctness and the separate prepared `.cache/investigation-stopping-comparison/manual-comparison-review.jsonl` worksheet for citation support and stopping judgments. Bind each pending worksheet entry to the actual source run and result fingerprint before judging it. Do **not** add custom fields to an existing factual review packet: its immutable-context fingerprint would correctly reject them. No manual labels have been assigned in advance.

| Measure | Source and denominator |
| --- | --- |
| Official whole-response EM/F1 | Existing unchanged metrics across all measured attempts; retain for standard comparisons. |
| Answer-focused EM/F1 | Existing offline rescore; show extraction status counts, scorable `n`, total attempts and independent question count. Null/unavailable extraction is not a factual error. |
| Manual factual correctness | Existing explicit reviewer packets. Accuracy=`correct/(correct+incorrect+partially_correct)`; show partial, unverifiable and unreviewed separately and the reviewed denominator. Empty denominator means unavailable. |
| Supporting evidence | Existing first-search, all-search, accumulated and final-generation-context canonical coverage. Show unavailable context separately; annotation matching is not a semantic correctness judge. |
| Searches/generation calls | Actual per-question tool calls/executed searches and actual provider counts; preserve failures and incomplete attempts. Report totals and averages with `n=3` per arm. |
| Latency | Existing total Investigation duration and distributions, including median/p90/p95 and observation count. Do not sum overlapping generation/search/embedding/SQL stages. Network and pacing effects are included. |
| Prompt-token usage | Sum actual reported prompt tokens; separately count calls without usage. At equal call counts the 41 additional prompt words add some input overhead, so fewer searches alone are not a token-saving proof. |
| Citation identifier validity | Existing structural validation, clearly separated from factual support. Registered IDs alone do not establish accuracy. |
| Citation accuracy | Manual claim/citation-group support. Fully supported reviewed cited claims divided by supported + unsupported + partially supported assessable reviewed cited claims. Count partial, unverifiable and pending separately; exclude unknown/pending from the denominator. Assess jointly cited relationship chains as groups, rather than requiring either individual excerpt alone to establish a multihop conclusion. |
| Premature finalization | Manual yes/no/unverifiable on answered attempts: did the final context leave a required relationship, identity issue or material conflict unsupported/unresolved? Rate=yes/(yes+no) among reviewable answered attempts, with unknown/pending and denominator explicit. A lucky reference match can still be unsupported/premature. |
| Incorrect abstention | Manual yes/no/unverifiable on insufficient-evidence attempts: did the admitted context already support a complete citable answer? Rate=yes/(yes+no) among reviewable abstentions, with unknown/pending and denominator explicit. Speculation about unexecuted searches finding unseen evidence remains unverified. |

Inspect query paths and the first supported-answer point per question; the two arms may generate different searches. Fewer searches do not compensate for a wrong relationship, unsupported citation, premature answer or avoidable abstention. A single Jolliffe success cannot establish general improvement, and even this three-question pilot is not a statistically strong accuracy/latency comparison. Differences in extraction coverage or reviewed coverage must be visible, not converted into optimistic scores.

## Context duplication remains separate

The prior analysis found repeated excerpts in tool-response history even when canonical registry IDs are reused. This can consume context and tokens. **Evidence payloads, exchange construction, native signed history and context admission were not changed here.** Both offline replay arms have identical conversation contents and non-instruction configuration hashes. Deduplication/compaction is a separate potential optimization requiring native-history, source-availability and citation/provenance validation; combining it with this pilot would confound the stopping-instruction comparison.

## Limitations and status

The intended effect is fewer searches that do not resolve a required gap while preserving needed multihop reasoning, identity checks, conflicts and abstention. That behavioral effect is **not measured yet**. Scripted fake decisions can demonstrate allowed paths and detect protocol regressions, but cannot prove live instruction adherence. The exact reason for the historical extra searches remains unknown. No accuracy, search-count, latency or token improvement is claimed from these offline results.

Prepared artifacts are under `.cache/investigation-stopping-comparison/`: `comparison-plan.json`, `selection.json`, both arm caches and immutable seed, `run_arm.py`, `prompt.diff`, `change-audit.json`, `offline-replay.json`, `manual-comparison-review.jsonl`, and `tests.xml`. Local preparation and recipe files are ignored artifacts; the prompt snapshots, production change, tests and this report are reviewable workspace files. The live output directories have not been created. No database, API, deployment, commit or push operation was performed.
