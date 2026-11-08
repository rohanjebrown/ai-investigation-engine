# Incremental Investigation evidence presentation — 2026-11-07

Investigation now introduces each admitted canonical passage in full once per
conversation. Later search responses return its existing citation ID, title,
source context and the search where its text was introduced. New passages still
arrive in full. This changes evidence presentation only; the v5 instructions,
reasoning/stopping policy, retrieval and request budgets are unchanged.

## Implementation and protocol

Previously, canonical registry IDs were reused, but both
`InvestigationTurn.context_json()` and the Gemini adapter rendered a full excerpt
for every occurrence in every exchange. The saved v5 Jolliffe investigation
retrieved the same five passages five times. Its final context contained 22 full
excerpt appearances; three final-search copies were excluded by the context cap.

`InvestigationTurn.search_responses()` now provides one shared presentation for
the context-admission calculation and Gemini's native function-response bodies.
It walks the exchanges in order with a local map keyed by
`(content_sha256, start, end)`, the existing canonical registry identity. The
first occurrence uses the unchanged full `evidence_payload()`. Subsequent
occurrences omit only `excerpt`, retain the original ID/title/source fields,
and add `previously_available: true` and `first_seen_search`.

Every search response also contains `new_evidence_ids`,
`previously_available_evidence_ids` and `no_new_evidence`. For example, after E1
has been introduced:

```json
{
  "search_sequence": 2,
  "query": "follow-up search",
  "results": [{
    "evidence_id": "E1",
    "title": "Example source",
    "source": {"kind": "dataset", "revision": "example", "display_name": "Example source"},
    "previously_available": true,
    "first_seen_search": 1
  }],
  "new_evidence_ids": [],
  "previously_available_evidence_ids": ["E1"],
  "no_new_evidence": true
}
```

The source object follows the existing source contract; the values above are
illustrative. Source metadata is retained when present. Empty searches have an
empty result list, empty ID lists and `no_new_evidence: true`. A mixed response
contains full excerpts for new IDs and references for old IDs, in retrieval order.
These flags describe evidence admitted to the conversation, not whether omitted
raw retrieval hits were novel. They do not trigger application stopping.

Different ranges from the same document/hash/title remain distinct, as do
contradictory passages. Canonical copies use the original registry ID and source
context. The full immutable `Evidence` objects, hashes, offsets, source spans,
locators, document/version IDs, scores and source data remain on the logical
exchanges and registry. Raw search observations preserve each returned hit and
its provenance before admission or presentation.

The renderer is **prefix-stable**: rendering a longer conversation reproduces
each earlier response identically. It does not retroactively replace earlier
full excerpts with references. Its seen map is local to one ordered rendering,
so another investigation introduces its own evidence in full.

The Gemini adapter still replays the original native model `Content` objects
using deep copies, including function-call IDs, arguments, thought parts and
required thought signatures. Existing sequence/question/argument validation is
unchanged. Each call retains its matching native function response. Only the
response body supplied for a newly appended exchange uses incremental evidence.
Earlier signed model messages and function responses remain byte-equivalent in
SDK serialization across subsequent requests. Earlier full evidence stays in
history and its IDs remain valid for final answers and conflicts.

The unchanged context-admission code now measures this smaller presentation.
It still applies the existing 24,000-character cap and can admit references that
previously did not fit. This is additional headroom, not an increased budget.
The cap remains a provider-neutral context measure; it does not bound the full
SDK request including native protocol and system/schema overhead.

## Files changed for this task

| File | Change |
| --- | --- |
| `src/investigation_engine/application/investigation_records.py` | Shared incremental response renderer and context serialization. |
| `src/investigation_engine/infrastructure/generation/gemini_investigation_model.py` | Use shared response bodies while preserving native model messages. |
| `src/investigation_engine/evaluation/investigation.py` | Add per-turn `model_facing_search_responses` alongside unchanged raw retrieval and logical exchanges. Full presentation is exported only when evidence-text export is enabled; otherwise the field is null. |
| `tests/unit/application/test_incremental_investigation_evidence.py` | New canonical, context, citation, multihop and conflict regressions. |
| `tests/unit/infrastructure/test_gemini_investigation_protocol.py` | Real SDK serialization through an offline HTTP transport for repeated/mixed results and signed history. |
| `tests/unit/evaluation/test_investigation_evaluation.py` | Verify raw hits/coverage independently of model-facing compaction, with text export enabled and disabled. |
| This report and `docs/README.md` | Explain validation, comparison and live regression command. |

Other pre-existing workspace modifications belong to earlier tasks and were
retained. No prompts, stopping rules, retrieval SQL/BM25/HNSW/RRF, Voyage code,
generation/embedding budgets, database schemas or evaluation metrics changed.
No commits, pushes or deployments were performed.

## Preserved v5 baseline and offline comparison

Baseline: `reports/evaluation/structured/jolliffe-v5/`, run
`20261107T084545-dace22b8`. Its original artifacts are unchanged and an exact
copy is retained in `.cache/incremental-evidence/v5-baseline/`. The pre-change
records/adapter files and hashes are also retained there in the parent directory.
The unchanged v5 instruction SHA-256 is
`45cada8e1a9c9f2455a4966bedc4e86bcad985cc13086e9879ed97bd7638a510`.

The historical live result answered Jolliffe correctly with E1/E2, five searches,
six generation requests, 30,071 reported prompt tokens, 242 candidate tokens and
26.161 seconds elapsed. Both annotated supporting facts were already available
after search one. Those are historical observations, not updated measurements.

Machine-readable offline results:
`reports/analysis/incremental-evidence-2026-11-07/comparison.json`.
Two comparisons keep formatting effects separate from changed admission:

1. **Fixed saved admission:** render exactly the saved logical exchanges,
   including only two passages admitted from search five in the original run.
2. **Real use-case replay:** execute the unchanged `InvestigateQuestion` with
   fake search/model ports, the same five raw result sets and saved model actions.
   This allows normal admission to use the additional space. The real Gemini
   adapter constructs its native request contents, using synthetic offline
   signatures rather than making provider requests.

| Generation turn | Baseline context chars | Incremental, fixed admission | Incremental, normal admission | Baseline SDK contents chars | Incremental SDK contents chars |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1, before search | 113 | 113 | 113 | 123 | 123 |
| 2, after S1 | 5,698 | 5,807 | 5,807 | 6,044 | 6,153 |
| 3, after S2 | 11,276 | 7,246 | 7,246 | 11,949 | 7,919 |
| 4, after S3 | 16,922 | 8,753 | 8,753 | 17,990 | 9,821 |
| 5, after S4 | 22,499 | 10,191 | 10,191 | 23,893 | 11,585 |
| 6, after S5 | 23,811 | 10,856 | 11,629 | 25,533 | 13,351 |

Characters are Python Unicode character counts of compact JSON. SDK columns
serialize `Content.model_dump(mode="json", exclude_none=True)` with synthetic
native IDs/signatures; they are not captured live HTTP byte counts. The small
first-search increase is the explicit new/reused metadata overhead.

| Measure | Baseline | Incremental |
| --- | ---: | ---: |
| Full-text appearances in final history, fixed saved admission | 22 | 5 |
| Compact references, fixed saved admission | 0 | 17 |
| Full-text appearances in final history, normal admission | 22 | 5 |
| Compact references, normal admission | 0 | 20 |
| Total result positions admitted, normal admission | 22 | 25 |
| Unique canonical passages | 5 | 5 |
| Raw returned positions | 25 | 25 |
| Annotated support after first search / at final turn | 2/2 / 2/2 | 2/2 / 2/2 |
| Final citation IDs | E1, E2 | E1, E2 |
| Scripted search / generation counts | 5 / 6 | 5 / 6 |
| Scripted embedding actions | 5 | 5 |
| Maximum searches / turns / generation / embedding actions | 5 / 6 / 6 / 5 | 5 / 6 / 6 / 5 |
| Evidence / context limits | 15 / 24,000 | 15 / 24,000 |
| Final search disposition | context_limit | succeeded |
| Final-only generation turn | yes | yes |

The final turn is still final-only because the search and embedding-action
budgets are exhausted. Full raw-hit and canonical-registry hashes match between
replays, including provenance. Every compact reference points to an earlier full
excerpt with the same source context. Citation IDs pass existing structural
validation. Replaying the saved correct final answer does not independently
establish semantic citation support or improved model accuracy.

Across the same six requests, serialized SDK contents fall from 85,532 to 48,952
characters: **36,580 fewer characters (42.8%)**. The final provider-neutral
context shrinks **51.2%**, despite admitting three additional references.

**Prompt-token saving estimate only:** at four serialized characters per token,
the marginal saving is approximately **9,145 tokens** across these six requests.
Using three to five characters per token gives approximately **7,316–12,193**.
This is a heuristic, not Gemini tokenization or a confidence interval. It excludes
unchanged instruction/tool-schema overhead and uses synthetic native signatures.
Do not subtract it from historical usage to report measured new token usage.
No offline latency, decision-making or stopping improvement is established.

To reproduce offline with a fresh output directory, run from the repository root:

```powershell
.\.venv\Scripts\python.exe .cache/incremental-evidence/compare_saved.py --output reports/analysis/incremental-evidence-replay
```

This local recipe requires the retained pre-change snapshots and saved v5 run.
It makes zero provider requests and zero database queries. It refuses an existing
output directory. Its integrity assertions verify unchanged baseline/prompt,
identical raw retrieval/registry, prior message stability and citation preservation.

## Deterministic regression results

**503 tests passed**, with one existing Google GenAI/Python deprecation warning.
Ruff lint and formatting checks passed for all six changed source/test files.
JUnit output is `reports/analysis/incremental-evidence-2026-11-07/tests.xml`.

Coverage includes first five new passages, identical five, mixed new/reused,
first-source identity for canonical copies, distinct ranges from one document,
empty results, separate conversations, previously introduced citation IDs,
dependent multihop after a repeated-only search, distinct conflicting passages,
compact context admission, overflow/final-only behavior and citation rejection
for excluded evidence. SDK tests retain original signed model content and exact
prior function-response bodies through complete sequential call/response history.
Evaluator tests preserve repeated raw retrieval, gold coverage and the existing
text-export option independently of model-facing presentation.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/application tests/unit/evaluation tests/unit/infrastructure/test_gemini_investigation_model.py tests/unit/infrastructure/test_gemini_investigation_protocol.py tests/unit/infrastructure/test_investigation_stopping_prompt.py -q
```

## Small live regression command

The user subsequently authorized real Gemini calls and requested waiting a minute
on rate limits. One Jolliffe attempt was launched separately from the baseline,
with an initial 60-second cooldown, paced providers and the following run-wide
RPC caps. It completed successfully; no rate-limit wait/retry was needed.

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py run `
  --selection reports/evaluation/structured/jolliffe-v5/selection.json `
  --cache .cache/evaluation/query-embeddings.json `
  --expected-database investigation_stage --modes investigation_hybrid `
  --semantic-backend hnsw --hnsw-candidates 400 --hnsw-ef-search 400 `
  --semantic-candidates 20 --lexical-candidates 20 --final-limit 20 --rrf-k 60 `
  --generation-model gemini-3.5-flash-lite `
  --allow-generation --max-generation-requests 6 `
  --allow-live-embeddings --max-embedding-requests 10 `
  --generation-rpm 12 --generation-tpm 200000 `
  --embedding-rpm 60 --embedding-tpm 24000 --cooldown-seconds 60 `
  --warmups 0 --repetitions 1 --concurrency 1 --timeout 900 `
  --local-diagnostics --output reports/evaluation/structured/jolliffe-incremental-evidence
```

Use a fresh output path for future authorized runs. The maximum ten embedding
RPCs include countTokens and embedContent for five uncached queries; cache hits
can reduce actual use. No extra judge calls are added. This invocation preserves
the production per-investigation budgets. Any rate-limit failure is retained;
waiting does not authorize exceeding the total request caps.

## Authorized live result

Saved run: `reports/evaluation/structured/jolliffe-incremental-evidence/`, run ID
`20261107T090946-0e11b0dc`. Audited summary:
`reports/analysis/incremental-evidence-2026-11-07/live-regression.json`.
This was a real Gemini/embedding request run over the
existing database, separately authorized after the original offline-only task.
No retries, warmups or additional measured attempts were run. The SQL work was
existing read-only retrieval/preflight; no corpus or schema changes were made.

| Observed measure | Historical v5 baseline | New live regression |
| --- | ---: | ---: |
| Final answer | Hedworth Jolliffe, 2nd Baron Hylton, in a complete sentence | Same complete sentence |
| Citation IDs | E1, E2 | E1, E2 |
| Search / generation calls | 5 / 6 | 5 / 6 |
| Prompt tokens, reported by Gemini | 30,071 | 20,381 |
| Candidate tokens, reported by Gemini | 242 | 225 |
| Total Investigation latency | 26.161 s | 26.110 s |
| Unique canonical passages in final registry | 5 | 6 |
| Raw returned passage positions | 25 | 25 |
| Full excerpt appearances in final history | 22 | 6 |
| Compact reference appearances in final history | 0 | 19 |
| Final context characters | 23,811 | 11,650 |
| First-search / final-context annotated evidence recall | 1.0 / 1.0 | 1.0 / 1.0 |

The new live run made **six generation RPCs and six embedding RPCs**: three
countTokens and three embedContent requests for three uncached follow-up queries.
Two other searches used cached vectors. This is within the six-generation and
ten-embedding run caps. There were zero Voyage requests and no provider errors.
All six sequential native conversation requests were accepted by Gemini. The
recorded final citation IDs pass existing structural validation. Inspecting E1
and E2 confirms the father-to-grandfather chain supporting the reported answer.

| Turn | Context chars | Reported prompt tokens | Full excerpts / compact references in history |
| --- | ---: | ---: | ---: |
| 1 | 113 | 717 | 0 / 0 |
| 2 | 5,807 | 2,557 | 5 / 0 |
| 3 | 7,246 | 3,238 | 5 / 5 |
| 4 | 8,685 | 3,917 | 5 / 10 |
| 5 | 10,192 | 4,623 | 6 / 14 |
| 6 | 11,650 | 5,329 | 6 / 19 |

The live path was:

1. `William Jolliffe 4th Baron Hylton paternal grandfather`
2. `"Hedworth Jolliffe, 2nd Baron Hylton" father`
3. `"Hedworth Jolliffe" 2nd Baron Hylton parents`
4. `"William George Hervey Jolliffe" grandfather`
5. `"Hedworth Jolliffe, 2nd Baron Hylton" parents OR son OR children`

Search four introduced a genuinely new sixth passage, which was presented in
full; later copies of it became references. Both required annotated facts were
already available after search one. **Search and generation counts did not
improve.** The observed token total is 9,690 lower (32.2%) than the historical run,
but query paths, retrieved passages, cache state and provider conditions differ.
This is one stochastic smoke test, not a controlled causal estimate. Latency was
essentially unchanged. The controlled offline comparison above isolates
presentation more directly. No general accuracy or stopping claim follows.

## Remaining limitations

References and source metadata still add context; very short passages may not
become smaller. First appearances remain full and can exhaust the context cap.
The canonical key inherits existing content-hash/range semantics. No semantic
deduplication, cross-conversation cache or title-based merging is introduced.
Earlier passages remain in the native transcript and are re-sent with later
requests, so this does not eliminate repeated transmission across requests.
It removes duplicate excerpt copies within each conversation history.

The model may still repeat searches or ignore new/reused indicators. The single
successful live Jolliffe smoke cannot establish general multihop accuracy,
relationship understanding, conflict handling or improved stopping. Offline
coverage and citation-ID validity are structural checks, not factual judgments.
