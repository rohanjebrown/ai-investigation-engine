# 2WikiMultihopQA development corpus — measured ingestion audit

**Status:** U03 corpus selection and counting methodology CONFIRMED by the user on 25 October 2026; see [register U03](../decisions/register.md#u03) and [approval E10](../decisions/register.md#e10). Sections 1-8 preserve the original 25 October audit and its historical limitations; section 9 records the later adapter-based measurements and approved policy.  
**Dataset:** `xanhho/2WikiMultihopQA`  
**Pinned revision:** `612bc5039a457880d9e7d84c3b0a4cf154b70e4f`  
**File:** `data/raw/2wiki/dev.parquet`  
**Size:** 30,056,098 bytes (verified locally on 25 October 2026)  
**File SHA-256:** `c0d8b60b9026b728fb07ad74c5252a0f188f6942e8ba5c02df4dfa369502ea8d`  
**Historical audit script:** `src/investigation_engine/ingestion/audit_contexts.py`  
**Historical run:** `uv run python -m investigation_engine.ingestion.audit_contexts`

That script measures serialized sentence lists and exploratory normalized text; it does not reproduce the later extracted-text baseline. The section 9 measurements came from a transient read-only scan using the actual adapter. No new audit runner or automated test file was created.

## 1. Actual dataset structure inspected

- **12,576** dataset rows, which are benchmark **question-answer examples**, not source documents.
- Seven Parquet columns: `_id`, `type`, `question`, `context`, `supporting_facts`, `evidences`, `answer`.
- `context`, `supporting_facts`, `evidences` are JSON **strings** that must be deserialized.
- Each `context` includes article-title/sentence-sequence pairs, with possible repeated titles/content across questions.
- `supporting_facts` uses `(article_title, zero_based_sentence_index)` relative to the context representation for a particular question.
- `evidences` contains gold relationship triples; keep as **evaluation annotations**, not as material the answer generator may see.

## 2. Observed counts — exact sentence-sequence comparison

The original audit serialized the **ordered list of sentences** using `json.dumps(sentences, ensure_ascii=False, separators=(',', ':'))` and hashed that serialization. Thus its "exact content" identity includes sentence-list boundaries and differs from our later agreed canonical-text-only reuse policy.

| Metric | Measured value |
| --- | ---: |
| Question rows | **12,576** |
| Passage occurrences across questions | **125,760** |
| Distinct article-title strings | **54,957** |
| Distinct title + serialized-sentence-list pairs | **56,709** |
| Distinct serialized sentence lists, regardless of title | **56,702** |
| Repeated occurrences of identical title + list | **69,051** |
| Titles associated with more than one serialized list | **1,745** |

The difference between unique title/content pairs and unique content lists means that some identical sentence sequences appear under different titles. This does **not** justify collapsing distinct source identities.

## 3. Exploratory whitespace normalization audit — not an adopted dedup policy

The exploratory function was:

```python
def normalize_text(sentences: list[str]) -> str:
    return " ".join(" ".join(sentences).split())
```

| Metric | Original exact sentence-list audit | Exploratory normalized-text audit |
| --- | ---: | ---: |
| Unique content representations | 56,702 | 56,654 |
| Unique title-content combinations | 56,709 | 56,661 |
| Titles with multiple contents | 1,745 | 1,699 |

Whitespace normalization eliminated only 48 distinct representations by this metric. **We chose not to use this normalization as a deduplication policy.** These figures are retained as historical measurements, not projected post-ingestion counts.

## 4. Actual variants inspected

- **`24 Hour Psycho`:** space differences around quotation marks.
- **`Carlos F. Borcosque`:** one sentence split into two in one representation.
- **`3096 Days`:** sentence split mid-title; hyphen spacing and punctuation formatting.
- **`9 Month Stretch`:** double spacing in one variant.
- **`George Albert I, Count of Erbach-Schönberg`:** spacing around hyphens and parentheses.
- **`Johann Adolf I, Duke of Saxe-Weissenfels`:** different sentence split and punctuation spacing.

The inspected samples look predominantly like extraction/formatting differences. **Do not extrapolate this claim to all 1,699 remaining title variants.** They are not demonstrated historical Wikipedia document revisions.

## 5. Effect of later agreed Option A (historical analysis)

The project subsequently chose **canonical-text-only exact reuse**, preserving original structure and provenance on each document version. Therefore:

- The old 56,702 number measures unique **serialized sentence lists**, not guaranteed final unique `Content.text` rows.
- The 56,654 number measures **whitespace-collapsed concatenated text**, which we explicitly decided *not* to use for deduplication.
- At this stage, a deterministic, non-normalizing conversion from source sentences into canonical text still needed to be specified and audited again. U02 subsequently approved single-ASCII-space assembly; section 9 now records the actual extracted-text count.
- A difference in sentence boundaries may map to the same canonical text and therefore share `Content` only if the resulting strings match exactly; original sentence indexes remain in version-specific structural and source mappings.

## 6. Important limitations at the original audit date

The next two paragraphs preserve the original unresolved state. The later extraction audit and U03 approval resolve the counting-policy and structural-audit gaps as described in section 9; semantic independence and end-to-end retrieval/citation quality remain unproven.

**The 50,000 requirement is NOT yet verified.** The dev split has over 50,000 distinct titles and content-list representations, but article-title strings, question occurrences, and hash-unique extractions are not automatic proof of 50,000 *independent* source documents/text records. A repeatable corpus identity and independence policy needs approval; audit the full selected corpus and report raw, exact-content, independent-record and indexed-chunk counts separately.

**The supporting-facts structural audit has not been run/confirmed in the visible discussion.** We have not yet measured missing referenced titles, invalid sentence indices, duplicate titles within one context, or cross-occurrence evidence mapping behavior.

**No trained/evaluated retrieval system or real-format citation tests have been demonstrated by this audit.**

## 7. Original follow-up checks (historical checklist)

Items 1-6 below describe work that was open at the original audit date, not current blockers in full. U01/U02/U04/N06 now settle occurrence identity, assembly, and provenance; section 9 records the dev audit, source-resolution checks, and approved counting policy. Evaluation/leakage remains U17. Item 7's manifest design is still a proposal, not an implemented or approved interface.

1. Specify exact source-to-canonical text assembly (join separators, line endings, representation of empty fragments), with no extra equality normalization.
2. Recount unique canonical `Content.text` and exact reuse candidates under that policy.
3. Validate every gold supporting-fact title/index against the *specific question context* and log failures/ambiguities.
4. Decide and document mapping of `(dataset revision, split, question ID, context position)` to logical `Document` identities and source occurrences.
5. Audit all selected splits while keeping benchmark gold answers/evidence separate from searchable content.
6. Establish and justify the independent-record count to satisfy the assignment's >=50,000 criterion.
7. Preserve dataset revision, source hashes, configurations, and audit outputs in an ingest manifest.

## 8. Implication for schema

The original audit supported separating **(a) logical source/document identity, (b) identical text storage identity, and (c) original sentence-position annotations**. Measurements alone did not settle identity policy or assembly rules; those were subsequently approved in U01/U02/U04/N06. Physical persistence design remains unresolved under U05-U07.

## 9. Approved U03 corpus and counting policy

### Selected corpus and assignment basis

Use the **complete development split** of `xanhho/2WikiMultihopQA` at the pinned revision above. Preserve all **125,760 passage occurrences**, their separate document/version registrations, and their individual provenance under U01. Reuse identical Content under A07; do not merge logical documents merely because their text matches.

Page 5 of the [original English assignment](C:/Users/merli/Downloads/Applied_AI_Investigation_Engine_Task_English.pdf) requires processing at least 50,000 documents or independent text records, preserving sources, and enabling repeatable evaluation. It permits reproducible, justified subsets and lists 2Wiki as an eligible dataset. The [assignment summary](../decisions/register.md#assignment-requirements) distinguishes these requirements from project decisions.

The approved conservative baseline is **56,680 unique exact-content passages**, exceeding 50,000 by **6,680**. Do not add splits or create arbitrary subsets merely to satisfy this numerical threshold. The complete existing split avoids another selection rule and retains each question's source contexts. This is a corpus/counting approval, not a claim that durable ingestion or all assignment requirements are implemented.

### Counting definitions and measurement procedure

| Measure | Definition |
| --- | --- |
| Passage occurrence | Every context passage extracted from a dataset example, in original position. |
| Unique exact-content passage | A distinct SHA-256 of the original extracted UTF-8 text, irrespective of title or occurrence. This is the approved threshold baseline. |
| Duplicate occurrence beyond the first | Total occurrences minus distinct exact-content passages; the first copy of each text is not a duplicate for this metric. |
| Unique source-coordinate record | A distinct original source, revision, split, example ID, and context position. This is a dataset occurrence, not proof of an independent original article. |
| Chunk | A subdivision of an extracted document; excluded from the independent-record count. No chunks were created by this audit. |

The 25 October full scan used PyArrow batches of 256 question rows and the existing [extract_2wiki adapter](../../src/investigation_engine/infrastructure/ingestion/twowiki_adapter.py):

1. Decode each row's context JSON and pass the parsed record, split `dev`, and actual pinned revision to the adapter.
2. Use the returned text exactly: original sentences in order, joined by one ASCII space. Do not trim, normalize whitespace, change punctuation/case, or include titles or benchmark labels.
3. Compute `SHA-256(text.encode("utf-8"))` through the existing Content implementation. Compare text equality within matching hashes; no hash collisions were observed.
4. Count hash frequencies, distinct titles, distinct title/hash pairs, and occurrence coordinates separately. Confirm example IDs and complete source coordinates are unique across this snapshot.
5. Calculate duplicate occurrences as `125760 - 56680 = 69080`. Frequency-bucket totals must reconcile to both the distinct-text and occurrence totals.

Source identity and content identity remain separate. source_id identifies `xanhho/2WikiMultihopQA`; revision identifies its snapshot; record_locator holds split, example_id, and context_position. Repeated text can occur under different questions, positions, or titles while all references remain individually identifiable. Coordinates are not encoded into source_id. The file hash above identifies the input artifact; passage hashes identify extracted text.

### Measured results

| Metric | Count |
| --- | ---: |
| Question-example rows | 12,576 |
| Passage occurrences | **125,760** |
| Unique exact-content passages | **56,680** |
| Duplicate occurrences beyond the first | **69,080** |
| Unique source-coordinate records | **125,760** |
| Distinct passage titles | 54,957 |
| Distinct title-and-extracted-content combinations | 56,687 |
| Texts appearing exactly once | 44,886 |
| Texts appearing more than once | 11,794 |
| Occurrences in repeated-text groups, including each first copy | 80,874 |
| Distinct texts appearing across multiple questions | 11,483 |
| Questions with duplicate text inside their own context lists | 484 |
| Duplicate occurrences beyond the first within each question | 548 |
| Distinct texts appearing under multiple titles | 5 |
| Titles associated with multiple exact extracted texts | 1,725 |
| Chunks | Excluded; not generated or counted |

The difference between 1,725 titles with multiple extracted texts and the historical 1,745 titles with multiple sentence lists reflects different comparison units; it does not introduce normalization. The approved 56,680 total likewise differs from both the historical 56,702 sentence-list total and 56,654 normalized-text total.

| Occurrences per identical text | Distinct texts | Total occurrences |
| --- | ---: | ---: |
| 1 | 44,886 | 44,886 |
| 2 | 7,097 | 14,194 |
| 3-5 | 3,369 | 11,948 |
| 6-10 | 775 | 5,698 |
| 11-20 | 203 | 2,847 |
| 21-50 | 127 | 4,173 |
| 51+ | 223 | 42,014 |
| **Total** | **56,680** | **125,760** |

Duplicate occurrences beyond the first account for **54.93%** of occurrences. All occurrences in repeated-text groups, including first copies, account for **64.31%**; these percentages describe different metrics.

Concrete examples from the scan:

- **Ian Barry (director):** the identical extracted passage occurs 1,880 times across 1,880 questions. This contributes one unique exact-content passage, while all 1,880 coordinates are preserved.
- **Ultraman Ginga Theater Special / Ultraman X The Movie:** question `ddd251a6089411ebbd75ac1f6bf848b6`, context positions 0 and 6, contains the identical text `The main catchphrase in this movie is.` under two different titles. This contributes one text and two source-coordinate records.

### Limitations, validation scope, and remaining decisions

**Exact-content uniqueness is not proof of semantic independence.** Different strings can describe the same source or subject, and some passages are incomplete. Titles and source coordinates do not resolve that limitation. The approved method provides a transparent, conservative exact-content baseline, not semantic deduplication or an external certification of assignment acceptance.

The actual adapter's separate full scan verified **400,892 original sentence slices** and all **30,687 supporting-fact references** against the appropriate source occurrence. This resolves the historical structural-audit gap in section 6. It does not establish rendered citations, retrieval quality, semantic independence, or evaluation independence.

Keep question and answer labels outside the retrieval corpus. Gold supporting facts and evidences likewise remain evaluation annotations rather than searchable source text. Preserve revision, split, example ID, and context position. Detailed evaluation methodology and potential leakage remain **U17**; corpus selection does not approve evaluation splits or establish absence of content overlap.

These measurements came from transient read-only checks; no new persistent audit runner or automated test suite was added. No additional splits were audited or selected because the existing dev split exceeds the approved numerical baseline. Persistence (U05-U07), retry/reimport behavior (U08), recovery (U14), and evaluation/test sequencing (U17) remain unresolved. The next recommended discussion is U08 before a bulk ingestion runner can safely define what repeated processing means.
