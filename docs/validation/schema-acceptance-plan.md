# Document schema and provenance — validation plan

**Status:** Broader acceptance backlog; E22 adds a partial permanent suite for implemented behavior. See [current execution evidence](../decisions/testing-strategy.md).
**Purpose:** Convert agreed conceptual architecture into falsifiable implementation requirements.

Temporary authoritative-schema/registration and chunking checks have since been executed; scoped results are in the decision register and focused ADRs. E22 now adds permanent unit, integration and opt-in corpus checks. This broader plan still includes unimplemented formats, retrieval, citations and lifecycle behavior, and is not a completed acceptance gate. Historical disjoint/augmented-chunk scenarios below do not authorize them: E17/E18 select contiguous exact Content ranges, with future embedding augmentation kept separate.

**Cleanup review finding — resolved under E24:** Direct in-memory registration previously accepted a new version other than 1 or a non-null current_version_id. Both repositories now require inactive version 1 for new occurrences, consistent with RegisterDocument and delayed activation. Identical authorized retries are resolved before that guard and retain the stored active pointer. The two former expected failures are ordinary passing tests, with additional state-preservation and active-retry coverage. The [testing ADR](../decisions/testing-strategy.md) records actual results and the remaining PostgreSQL integration limitation; no broader acceptance gate is implied.

## 1. Functional contract tests

| Test | Given / action | Expected proof |
| --- | --- | --- |
| F01 — Independent exact uploads | Upload two separate files producing the same canonical text | Two distinct `Document`s, each with version 1 and separate provenance; one shared `Content`; no collision of title/filename identity |
| F02 — Cross-format reuse | PDF and DOCX extract *identical* canonical strings | One shared `Content`; PDF page and DOCX paragraph original source locators remain distinct |
| F03 — Source segmentation | `['Alpha.', 'Beta.']` vs `['Alpha. Beta.']` yield identical canonical text | Shared content if strings truly equal; version A retains two original sentence ranges, B one; benchmark refs resolve correctly |
| F04 — Text offset correctness | Unicode string with non-ASCII / emoji | Every `[start,end)` slices expected Python string; imported UTF-16/byte offsets converted correctly |
| F05 — Multi-page evidence | One retrieved chunk overlaps two pages | Citation resolver can return pages from both original source spans |
| F06 — Update | A1 current; create and index A2 | A1 history retained; current pointer switches only when new index is ready; no A1 citations in normal search |
| F07 — Shared vector retirement | A1 and B1 share X; A updates to Y | X/index remains usable through B1; stale association through A1 is excluded |
| F08 — Delete | A and B share X; delete A | A ineligible immediately; B remains eligible; source and vector cleanup not premature |
| F09 — Unused vector cleanup | No active documents use a derived entry | Entry can be retired safely while historical content retention follows policy |
| F10 — Chunk strategies | Two chunk strategies run on same original content | Independently identifiable chunk sets; source/version mappings intact; no mutation of canonical content |
| F11 — Non-contiguous chunk | Chunk text includes heading injection or two disjoint spans | Its source spans correctly represent origins; never fake a single continuous source range |
| F12 — No source location | Adapter cannot determine exact page/sentence | Citation falls back to available document/version information and clearly indicates limited precision |
| F13 — Ambiguous title | Two unrelated documents share title | IDs and source references stay distinct; no overwritten/merged document |
| F14 — Hash verification | Hash lookup suggests an existing record | System checks exact canonical string equality before reuse; mismatches do not merge |
| F15 — Retry/concurrency | Duplicate ingest and partially failed index operation | Idempotent/recoverable state, no duplicate authoritative content records, no broken current pointer |
| F16 — Authorization | Two tenants share exact text, have different source documents | Neither can retrieve or cite the other's source; measure results after security filtering |
| F17 — Citation round trip | Index hit -> source reference -> extracted range | Returned citation matches exact input document version and original source location |
| F18 — Retrieval-time grouping | Similar evidence with differing dates or negation | No automatic semantic deletion; conflicting evidence remains retrievable and independently attributable |

## 2. Real source-format matrix

| Format | Extracted content requirement | Expected native locator(s) | Main validation risk |
| --- | --- | --- | --- |
| 2WikiMultihopQA | Ordered source sentences into deterministic canonical text | Question ID, context position, article title, original 0-based sentence index | Sentence splits differ; gold refs must resolve per occurrence |
| PDF | Document reading order; preserve multi-page mapping | Source filename/asset, 1-based page, optional region | Two-column text, tables, page transitions, low-quality scans |
| DOCX | Paragraph/heading/table reading order | Paragraph/section structural identifiers | Word document pagination is not reliable natively; tables/runs affect ordering |
| TXT | Raw text and line-break policy | Original filename; 1-based line numbers and/or char ranges | Line endings, encodings, missing metadata |

These locators are conditional on information actually exposed by the chosen extractor; never claim a precision that it did not provide.

## 3. Benchmark integrity and 50k threshold

- **Dataset audit:** Re-run on exactly selected data/splits at pinned revision; publish metrics for question rows, passage occurrences, raw unique representations, unique canonical strings, unique source records and active index items.
- **Independent records:** State a defensible definition and audit it; *do not use question count or chunk count as a substitute*.
- **Gold evidence:** Verify all `(title, sentence_index)` references against their specific context occurrence and report ambiguous or invalid mappings.
- **Leakage check:** Assert benchmark `question`, `answer`, `supporting_facts`, `evidences` are not indexed as part of the searchable corpus. Inference must not have gold reasoning/answer fields.
- **Evaluation:** >=40 cases spanning simple, multi-hop, abstention and contradictions; metrics include Recall@K, MRR, answer correctness and citation accuracy; compare at least two chunking/retrieval configurations and analyze failures.
- **Reproducibility:** Each run records corpus snapshot/manifest, dataset revision, source fingerprints, extraction/chunking/model/retrieval configs and test split.

## 4. Reliability, resource and deployment tests

1. Index consistency under simulated failure at each create/update/delete stage.
2. Recovery/reconciliation from orphan pending index entries.
3. Active-version gating when stale vectors are intentionally left in the index.
4. Persistent authoritative records through application restart/redeploy on the chosen free host.
5. Memory, storage, indexing time and query latency at a realistically populated >=50k-record corpus.
6. Security boundary tests if source ownership/authorization exists; tenant-filter selectivity when vectors are shared.
7. Serialization/reconstruction tests for models and locators, including Unicode, noncontiguous spans, long pages, empty extraction and unusual document metadata.

## 5. Explicit milestones / release gates

### Gate 1 — Data contract accepted

- Implemented models agree with the documented contract.
- F01–F05, F10–F14 and F17 pass on synthetic fixtures and real source examples.
- 2Wiki sentence-location mapping explicitly verified.

### Gate 2 — Safe lifecycle and index

- F06–F09 and F15 pass; no stale/deleted evidence exposed during failed indexing.
- Security assumptions documented and, where applicable, F16 passes.

### Gate 3 — Assignment scale and quality

- Independent 50k-record count justified and reproducible; persistence demonstrated; evaluation >=40 questions run; compare chunking and retrieval settings; report measured failure cases.

**No gate is currently marked passed.** Prior "PASS" statements in conversational scenario walkthroughs were *logical feasibility checks*, not automated implementation results.

## 6. Implementation order recommendation

1. Freeze only the minimal contract after checking real examples for PDF, DOCX, TXT and dataset occurrence mapping.
2. Implement pure Python domain/value models and validation/unit tests; do not hard-wire ORM/vector DB types into adapters.
3. Implement one adapter and simple persistent content store, prove F01/F03/F17 first.
4. Implement update/version retirement and index reconciliation tests before scale-up.
5. Build a small end-to-end RAG slice, then add hybrid retrieval/evaluation and test at actual target scale.

This order is a recommended development plan, not a claim those stages are complete.
