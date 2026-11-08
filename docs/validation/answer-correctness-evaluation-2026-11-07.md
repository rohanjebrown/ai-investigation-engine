# Answer-focused scoring and manual correctness review — 7 November 2026

The evaluator now separates **official whole-response text scores**, **conservatively extracted answer-value text scores**, and **explicit manual factual judgments**. The saved run `20261107T054241-2f5866da` was reprocessed entirely from local artifacts. Its official scores and original artifacts are unchanged. No database queries or external AI requests were made.

## Existing limitations and structured result inspection

`GenerationResult`, `AnswerResult`, `InvestigationDecision` and `InvestigationResult` have an `answer` string, with citations and status stored separately. None provides a second, explicitly concise answer-value field. The answer string can contain a complete sentence or explanation, as the saved Jolliffe result demonstrates. The evaluator therefore reads that existing field, then applies an evaluation-only extraction step when the surface form is supported. Production result representations and generation behavior are unchanged.

The official scorer in `evaluation/metrics.py` is unchanged: lowercase, remove ASCII punctuation and articles, collapse whitespace, exact normalized comparison, and multiset token F1 with the existing yes/no/noanswer special case. It continues to score the **entire original answer string**. Explanations and inline citations can reduce those scores even when the asserted value agrees with the reference.

The older suite-specific `answer_quality.answer_correctness` helper can label an answer using positive reference-phrase/alias matching. It is not used for the new structured evaluator's factual judgment or extraction: finding a reference name in text is insufficient to establish the asserted relationship. The new workflow reuses the official scorer, existing JSONL/report utilities, fingerprint utility, and `grounding.review_fingerprint` pattern. Existing evidence-support/entity-identity reviews remain separate from answer correctness. Valid citations and complete annotated-fact coverage do not establish factual correctness.

## Files changed in this extension

Source paths are under `src/investigation_engine/evaluation/` unless stated otherwise. Earlier Investigation work already existed in this uncommitted workspace and is preserved.

| File | Change |
| --- | --- |
| `answer_focus.py` (new) | Versioned, reference-free extraction; separate official/focused assessment fields; explicit aggregation denominators and pending/manual judgment counts. |
| `answer_reviews.py` (new) | Self-contained, result-bound review packets, strict review import, offline saved-run rescoring, immutable-source checks and a per-attempt comparison report. |
| `runner.py` | Adds answer assessment after the actual service call and existing official scoring; persists it in answer rows and Investigation traces. No generation/retrieval behavior changes. |
| `reporting.py` | Labels official scores explicitly; reports conditional focused scores, extraction availability, manual accuracy and review counts. Existing official numeric fields remain unchanged. |
| `cli.py` | Adds the offline `rescore SOURCE --output FRESH_OUTPUT [--reviews REVIEWS.jsonl]` command. It does not enter database/provider setup. |
| `tests/unit/evaluation/test_answer_focus.py` (new) | 63 deterministic cases covering extraction safety, relationship errors, manual judgments/binding, denominators, immutable artifacts, CLI isolation and future-run integration. |
| `docs/validation/structured-evaluation.md`, `docs/README.md` | Document and link the new workflow. |
| This report | Definitions, measured saved-run results, commands, tests and limitations. |

`metrics.py`, production answer/Investigation contracts, prompts, orchestration, retrieval SQL/HNSW/BM25/RRF, Voyage, embeddings, ingestion and schema were not modified in this extension. No dependencies, migrations, deployment, commits or pushes were introduced.

## Extraction approach and safety boundary

`extract_answer(question, answer, status=...)` has no reference-answer, gold-evidence or annotation argument. Tests verify that changing the reference cannot change its output. Extraction happens before text comparison. No substring is selected because it matches the reference, and no reference-specific aliases are added.

The version is `answer-focus-v1`. It normalizes display whitespace and curly apostrophes and removes only `[E<number>]` citation markers for the separate extraction path. It supports:

- A single complete assertion of the exact predicate from a “Who/What is/was …?” question, followed by a supported value. For Jolliffe, it matches the whole subject and relationship, then takes the asserted value after “was”.
- Limited explicit location/date forms, including “Where/When was X born/buried?” and simple copular location/date statements.
- Anchored “The answer is …” or “Answer: …” labels with a single supported value.
- Standalone proper-name/title/place values, selected numerical/unit/date formats, and single Boolean yes/no/noanswer responses to Boolean questions.
- A limited explicit comparative form (“Who lived longer …?”, or “was born earlier/later”) when the generated assertion identifies one exact question option. A standalone option can also be extracted as a direct value.

This is a small surface grammar, not a general language parser or entity recognizer. The extractor does **not** shorten subject names, repair relationship direction, equate father with father-in-law, expand name aliases, select the first named entity, remove negation, or use an annotated answer to guide extraction. The entire generated field must fit a supported form; an initial matching sentence is not accepted while trailing assertions are discarded.

Negation/exclusion, uncertainty, conjunctions/alternatives, multiple assertions and uncertain sentence boundaries are rejected as ambiguous. Unsupported or mismatched predicates and unclear answer roles are unavailable. Bare “No” is preserved for an appropriate Boolean question; a longer qualified/contradictory yes/no response is not reduced to its first word. Abstention, conflicting-evidence and incomplete outcomes are not applicable; failed/no-output cases are unavailable. Status and reason are retained. Extraction failures produce **null focused scores**, never an automatic incorrect factual judgment.

“Available” means a supported surface-form value was isolated. It is not a confidence estimate, an entailment result, or proof that the claim is true. A wrong but unambiguous value can be available and receive focused EM zero while its factual judgment remains unreviewed.

## Metrics and exact denominators

Existing `answer_metrics.exact_match` and `.f1` retain the original official scores. Additional fields live under `answer_assessment`:

| Field | Meaning |
| --- | --- |
| `official_whole_response.exact_match` / `.f1` | The unchanged official scorer applied to the entire generated `answer`. These duplicate the original scores with an explicit label. |
| `extraction.answer` | Isolated answer value, or null. |
| `extraction.status` | `available`, `ambiguous`, `unavailable`, or `not_applicable`; version, method and reason are also stored. |
| `answer_focused.exact_match` / `.f1` | The same official normalization/scorer applied to the extracted value, only when extraction is available and a reference exists. Otherwise both are null. These are answer-value text-agreement metrics. |
| `manual_factual_judgment` | Null until an explicit imported review; then `correct`, `incorrect`, `partially_correct`, or `unverifiable`. |
| `reviewer`, `review_explanation` | Explicit attribution and optional explanation for the saved judgment. |
| `factual_correctness_basis` | States whether the result is unreviewed or has an explicit reviewer judgment. |

Official operational means continue to include all measured attempts, including failed/no-answer attempts, using the existing zero-contribution rule for missing scores. Focused means include **only attempts with available extracted values and a reference**, with `answer_focused_denominator` displayed alongside total attempts. A focused score of 1 on a small extractable subset is not equivalent to perfect benchmark accuracy. Extraction status counts and ambiguous/unreviewed counts are always available.

**Reviewed-answer accuracy = correct / (correct + incorrect + partially_correct).** Partial judgments receive no full-correct credit and have their own count. Unverifiable and unreviewed cases are excluded from that denominator and reported separately. If the denominator is zero, accuracy is null/unavailable, not zero or one. No fractional credit or automatic confidence score is invented. Warmups are excluded from all measured aggregates. Counts refer to measured attempts; the saved-run rescore also records independent question counts so repetitions cannot be mistaken for additional questions.

## Actual saved-run result: William Jolliffe

Source: `reports/evaluation/structured/20261107T054241-2f5866da/`, one measured hybrid Investigation answer.

Question: **Who is William Jolliffe, 4th Baron Hylton's paternal grandfather?**

Reference: **Hedworth Jolliffe, 2nd Baron Hylton**

Generated: **William Jolliffe, 4th Baron Hylton's paternal grandfather was Hedworth Jolliffe, 2nd Baron Hylton.**

| Measure | Original result | Updated assessment |
| --- | ---: | ---: |
| Official whole-response EM | 0 | **0, unchanged** |
| Official whole-response F1 | 0.5555555555555556 | **0.5555555555555556, unchanged** |
| Extracted value | Not recorded | Hedworth Jolliffe, 2nd Baron Hylton |
| Extraction status / method | Not recorded | Available / exact question predicate |
| Separate answer-focused EM | Not available | **1** |
| Separate answer-focused F1 | Not available | **1** |
| Manual factual judgment | No imported review | **Unreviewed** |
| Reviewed-answer accuracy | Not available | **Unavailable; denominator 0** |
| Unreviewed / ambiguous measured attempts | Not reported on these axes | **1 / 0** |

This establishes a full-sentence formatting difference in deterministic text comparison. It does not automatically establish factual correctness. The pending packet includes citations E1/E2, full saved final-context excerpts, canonical passage identities and annotated supporting-fact metadata so an actual reviewer can judge the claimed relationship. No completed review was invented for this saved result.

The final output is `reports/evaluation/structured/20261107T054241-2f5866da-answer-evaluation-final/`. An offline audit verified that **every file in the original run directory retained its SHA-256**, all original answer-row fields were preserved exactly, the original official scores matched recomputation, and the new judgment stayed pending. Historical provider counts/timings in the copied summary describe the original execution; the rescore manifest separately states zero provider requests and zero database queries during rescoring.

## Incorrect relationship regressions

| Case | Expected behavior verified |
| --- | --- |
| Wisigard father-in-law question; “Wisigard's father was Wacho.” | Predicate mismatch; extraction unavailable, focused metrics null, factual judgment pending. Explicit incorrect review imports successfully and yields reviewed accuracy zero. A valid citation does not override the judgment. |
| Wisigard father-in-law question with reversed subject/object | No direction repair or extraction of a reference name from the wrong assertion. |
| Jolliffe question; short-subject sentence naming the 1st Baron | No subject alias/relationship repair; extraction unavailable. Explicit incorrect review stays incorrect. |
| Exact full Jolliffe subject naming the 1st Baron | Wrong asserted value is extracted as stated; focused EM is zero. No substitution of the expected 2nd Baron. |
| Wrong assertion followed by the expected name in a reference sentence | No reference-occurrence credit; multiple assertions are ambiguous and focused scores are null. |
| Negations, “X or Y”, slash/comma alternatives, uncertainty and contradictory assertions | No selection of a matching fragment or first entity. Unclear cases stay ambiguous/unavailable. |
| Abstentions, conflicts, incomplete and failed executions | No invented answer extraction or automatic factual label. |

Tests import explicit synthetic reviewer judgments to verify the mechanism; those test fixtures are not human reviews of the live dataset result.

## Manual-review workflow

Each `review_packets.jsonl` record is a self-contained local review packet containing:

- Source run/attempt identity: question ID, configuration, repetition and warmup flag.
- Question, reference, original generated answer/status and extracted answer/status/method.
- Both sets of text scores, citation IDs, cited evidence and available supporting evidence/excerpts.
- Evidence scope: actual final-decision context when observed, otherwise explicitly accumulated/returned evidence with final-context membership unverified.
- Annotated supporting-fact metadata for reviewer context, separate from extraction.
- Stable result ID, immutable-context fingerprint and the existing answer/evidence fingerprint.
- Editable `judgment`, `reviewer` and optional `explanation`.

Copy the generated packet file to a local review file. Inspect the complete assertion and relationship, reference, citations and actual excerpts. Do not equate token overlap, a valid citation or a matched supporting fact with factual correctness. Edit only the three review fields. Leave `judgment: null` pending, or choose `correct`, `incorrect`, `partially_correct`, or `unverifiable`. A completed judgment requires a nonblank reviewer attribution; explanation is optional. Use unverifiable when the provided material does not support a trustworthy judgment.

Import validates both the displayed fingerprint and the immutable packet body against the current saved answer/reference/extraction/evidence. Unknown attempt IDs, duplicates/conflicting reviews, stale/edited context, unknown judgment labels and unattributed completed reviews are rejected. There is no last-wins merge. A reference/extractor change can invalidate a previous review and require deliberate re-review. Grounding-review files have a different purpose/schema and cannot silently become factual reviews.

Attribution is a local reviewer attestation, not authenticated identity or a guarantee of review quality. Evidence unavailable in the original saved run is not reconstructed with database or model calls. The report identifies absent text and uncertain evidence scope; unverifiable is an appropriate explicit outcome. The pending template does not become a human review merely by being generated or imported unchanged.

## Commands for existing and future saved runs

PowerShell from `C:\Users\merli\upgaming_task\ai-investigation-engine`. All commands below use local files only. Output directories must be fresh; original run files are never overwritten.

Reproduce the offline pass using a new output path:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py rescore reports/evaluation/structured/20261107T054241-2f5866da --output reports/evaluation/structured/NEW-answer-evaluation
```

Create an editable local copy of the actual pending packets:

```powershell
Copy-Item -LiteralPath reports/evaluation/structured/20261107T054241-2f5866da-answer-evaluation-final/review_packets.jsonl -Destination .cache/answer-reviews.jsonl
```

After a reviewer edits only `judgment`, `reviewer` and optional `explanation`, incorporate those reviews without repeating inference:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate.py rescore reports/evaluation/structured/20261107T054241-2f5866da --reviews .cache/answer-reviews.jsonl --output reports/evaluation/structured/NEW-reviewed-answer-evaluation
```

Multiple disjoint review files can be supplied with repeated `--reviews`. An unchanged packet file is also valid and remains unreviewed. A derived output can be rescored again: source run identity is retained and saved grounding excerpts are carried forward. Supplying reviews is explicit on each rescore; a prior output's labels are not silently carried forward as fresh reviews.

Future Quick Answer and Investigation executions attach `answer_assessment` automatically after service execution. Their official metrics, prompts and outputs are unchanged. Future or older saved structured runs use the same `rescore` command; it verifies that stored official scores agree with the unchanged scorer before exporting new fields. An inconsistent source score/reference fails closed instead of silently rewriting history. No live-generation or live-embedding authorization flags are needed or accepted by the rescore subcommand.

## Validation and artifacts

**446 unit/application tests passed**, including **63 new focused regression cases**. Ruff, formatter and whitespace checks pass. One existing Google SDK deprecation warning remains. The offline CLI test forbids both Gemini client creation and database-engine creation. A deterministic future-run test verifies that the runner adds focused metrics while retaining official values and an unreviewed factual judgment. Review round-trip, warmup exclusion, denominators, context tampering and source preservation are covered.

No PostgreSQL integration run was performed for this change: the task requires saved-result reprocessing without database access, and the new functionality is local scoring/review I/O. The existing application/unit tests exercise service composition with fake providers. No Gemini, Voyage, external evaluator or other AI request occurred.

| Artifact | Path below the repository root |
| --- | --- |
| This report | `docs/validation/answer-correctness-evaluation-2026-11-07.md` |
| Original saved run (unchanged) | `reports/evaluation/structured/20261107T054241-2f5866da/` |
| Final human-readable result | `reports/evaluation/structured/20261107T054241-2f5866da-answer-evaluation-final/report.md` |
| Original row fields plus new assessment | `reports/evaluation/structured/20261107T054241-2f5866da-answer-evaluation-final/answer_results.jsonl` |
| Editable, pending factual review packets | `reports/evaluation/structured/20261107T054241-2f5866da-answer-evaluation-final/review_packets.jsonl` |
| Aggregate definitions and results | `reports/evaluation/structured/20261107T054241-2f5866da-answer-evaluation-final/summary.json` |
| Source/code hashes, review imports and no-call receipt | `reports/evaluation/structured/20261107T054241-2f5866da-answer-evaluation-final/manifest.json` |
| Independent original-directory and score audit | `reports/evaluation/structured/20261107T054241-2f5866da-answer-evaluation-final/audit.json` |
| Saved test receipt | `.cache/answer-correctness-tests.xml` |

The first exploratory offline output, `20261107T054241-2f5866da-answer-evaluation/`, is preserved separately. The final directory reflects the final implementation; neither pass repeated model or database work. Detailed excerpts remain local ignored artifacts. No completed factual review is fabricated in either output.

## Remaining limitations

Extraction deliberately trades coverage for precision. Unsupported grammar, lowercase/multiword answer forms, richer comparisons, abbreviations, conjunction-bearing names and complex dates can remain unavailable even when a person can easily interpret them. No semantic aliases, calendar equivalence, entity resolution, general coreference, or entailment inference is performed. Selected values can still be factually wrong, so focused EM/F1 remain text metrics. Scores conditional on extraction availability must be read with their denominators; they are not substitutes for official dataset metrics or reviewed accuracy.

Manual review depends on reviewer competence, evidence completeness and careful interpretation. Fingerprints prevent accidental application to changed results, not malicious forgery by someone editing local files. Unreviewed factual correctness stays unverified; zero completed reviews means no reviewed accuracy. The saved-run demonstration is one answer, not evidence of broad benchmark accuracy. Historical suite-specific heuristic labels are preserved for compatibility and are not used as the new factual-review assessment.
