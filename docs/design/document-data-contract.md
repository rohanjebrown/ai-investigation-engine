# Document and provenance logical data contract

**Status:** Approved logical responsibilities and scoped deterministic chunking foundation; historical candidate fields below do not independently select new domain fields or storage.  
**Depends on:** ADR-004.

**Later approval reconciliation (2026-10-25):** This document preserves historical logical candidates. The [U07 physical schema ADR](../decisions/postgresql-physical-schema.md) governs the approved three-table mappings, binary Content key, version provenance/JSONB spans, global occurrence uniqueness, and ownership/deletion columns. The [register](../decisions/register.md) governs U01/U02/U04/U08/U10 approvals and implemented async registration. The [chunking ADR](../decisions/deterministic-chunking.md) governs E17/E18's implemented range/configuration contracts. Extra candidate fields below are not implicitly selected; domain dataclasses and infrastructure storage have different responsibilities. Authentication, derived persistence, activation and recovery remain future work.

## 1. Separation of concerns

```text
Document (stable logical identity)
  └── DocumentVersion 1..N (immutable revision, source + extraction provenance)
        ├── Content 1 (shared immutable canonical text; N versions -> 1 Content)
        ├── StructuralSpan 0..N (version-specific structure over Content.text)
        └── SourceSpan 0..N (version-specific link from text to origin)

Content / DocumentVersion ──> Chunk representations (derived)
Chunk + embedding config ───> Search index entries (derived)
```

For a simple upload, a `DocumentVersion` represents one revision of a logical file. U01 assigns each 2Wiki context occurrence its own Document and initial DocumentVersion, preserving its source locator while reusing equal Content. Cross-revision document continuity requires a future explicit update operation; it is not inferred from equal source coordinates.

## 2. Candidate contracts

Fields below are *logical* and describe required information, not an instruction to use UUIDs, JSONB, or a particular Python class.

### `Document`

| Field | Required | Meaning / invariant |
| --- | --- | --- |
| `document_id` | Yes | Stable opaque identity of one independently managed logical document |
| `title` / display name | No | Human-readable; never used as unique ID |
| `current_version_id` | Nullable | Active version pointer; if present it must belong to this document |
| `created_at` | Yes | Registration time |
| `deleted_at` or lifecycle state | Yes, logical meaning | Normal retrieval excludes deleted documents; representation TBD |
| `access_scope` | If accounts/tenants exist | Authorization policy is independent of content sharing; exact form open |

### `DocumentVersion`

| Field | Required | Meaning / invariant |
| --- | --- | --- |
| `version_id` | Yes | Immutable revision identity |
| `document_id` | Yes | Owner logical document |
| `version_number` | Yes | Positive, sequential **within a document**, not globally |
| `content_id` | Yes | References exactly one immutable canonical text record |
| `source_reference` | Yes | Version-specific original input provenance |
| `structural_spans` | Collection | Version-specific sentence, heading, paragraph or similar spans; may be empty |
| `source_spans` | Collection | Version-specific extracted-text ranges with original locators; may be empty if unavailable |
| `extractor_id` / configuration revision | Needed for reproducibility | Identifies extraction procedure; fields TBD |
| `created_at` | Yes | Revision registration time |
| Index readiness | Operational | Keep independently mutable from immutable revision content; location (version/job) TBD |

### `Content`

| Field | Required | Meaning / invariant |
| --- | --- | --- |
| `content_id` | Yes | Reusable immutable content identity |
| `text` | Yes | Complete canonical extracted text in deterministic reading order |
| `sha256` | Yes | `SHA256(text.encode("utf-8"))`, used to *find candidate* exact duplicates |
| `created_at` | Likely | Diagnostic and audit use |

**Policy:** Equality requires identical `text` strings. No whitespace-collapsing, case folding, punctuation fixing, NFC conversion or embedding similarity is introduced as a *deduplication* operation. The source adapter must still define separators/reading order to construct `text`, which can differ across extractors. Preserve extractor revision so offsets can be interpreted.

**Zero/empty content:** N09 approves no chunks for empty Content, while preserving nonempty whitespace-only text. This does not settle format-specific extraction acceptance/failure (U09) or make an empty plan successfully indexed (U14). The 2Wiki adapter already rejects empty/whitespace-only sentences.

### `StructuralSpan` (version-specific value object)

| Field | Required | Meaning |
| --- | --- | --- |
| `kind` | Yes | `sentence`, `paragraph`, `heading`, `table`, etc.; validate supported types |
| `start`, `end` | Yes | Half-open indices within `Content.text` |
| `parent` | Optional | Hierarchical relationship; design TBD |
| `source_ordinal` | When applicable | E.g. original 0-based benchmark sentence index, which may differ from computed order |

### `SourceReference` (version-specific value object)

Identity of the original input or dataset occurrence. A **typed source kind** plus relevant source-specific attributes are preferable to an unvalidated catch-all dictionary.

Common semantic attributes: source kind (`upload` / `dataset` / future), original identifier, source revision/checksum when present, original format/media type, optional original filename, optional original asset URI, source acquisition/ingestion timestamp and extractor provenance.

Examples (illustrative, not approved wire format):

- Upload: `kind=upload`, `original_filename=incident.pdf`, `original_file_sha256=...`, `mime_type=application/pdf`.
- Dataset: `kind=dataset`, `dataset=xanhho/2WikiMultihopQA`, `revision=612bc503...`, `split=dev`, `question_id=...`, `context_position=3`, `article_title=...`.

**Important:** Article title is a human-readable source label, not globally unique identity; the question ID and context position identify an *occurrence* within the dataset. A dataset source may not have a direct Wikipedia page ID or historical revision identifier; don't invent one.

### `SourceSpan` (version-specific value object)

| Field | Required | Meaning |
| --- | --- | --- |
| `start`, `end` | Yes | Span in this version's referenced `Content.text` |
| `locators` | When available | One or more validated original-source locators |
| `quality/precision` | Optional | E.g. page-only vs exact native text segment, when extractor offers this signal |

Suggested **locator variants** (not finalized JSON shapes):

- PDF: `page_number` (usually 1-based original page), optionally polygon/region if actually available.
- DOCX: structural paragraph/heading identifier or ordinal, and original file; **do not invent PDF-style page numbers**.
- TXT: line number or line range (specify 1-based vs 0-based convention); underlying canonical char spans remain common.
- 2Wiki: dataset question occurrence + context position + **original sentence index** (0-based).
- Unknown: reference only the document/version and an available excerpt; disclose lack of a precise location.

A source span may map to multiple locators, ranges may overlap, and not all canonical text needs a precise original locator. Better honest coarse citation than invented precision.

### `Chunk` and `ChunkPlan` (derived application values)

E17 replaces the historical candidate chunk UUID/disjoint-range/effective-text representation with a smaller approved contract. `Chunk` holds an immutable Content reference and one nonempty `[start, end)` range. Its text is always exactly `content.text[start:end]`; its conceptual identity is `(content.sha256, start, end)`. No title, source metadata, configuration copy, generated UUID or independently stored text is added to a chunk.

`ChunkPlan` holds a DocumentVersion ID, one immutable ChunkingConfig and ordered chunks. Configuration records strategy, implementation version, tokenizer name/revision/artifact/runtime, content-token budget and overlap. Fixed policy details are versioned with the algorithm rather than made into redundant configurable fields. These are transient values, not an approved physical schema or registry.

- Whole-passage and fixed-token ranges depend on Content and configuration. Sentence packing additionally uses this DocumentVersion's supplied sentence structure.
- The same range may belong to several version/configuration plans. Range equality alone does not establish those memberships or source eligibility.
- N08 defines zero-overlap fixed fallback for absent/incomplete sentence structure; invalid sentence ranges are rejected. N07 supplies the experimental pinned tokenizer; it does not choose a production provider.
- Future embedding input is a separate representation. It may include title/task context without changing Chunk.text or offsets. Reuse requires equivalent effective inputs and provider/model/revision/encoding/format/dimension/normalization settings, across versions or strategies. No embedding cache, storage or inference is implemented.

See the [chunking ADR](../decisions/deterministic-chunking.md) for exact boundary, oversized-unit, fallback and Unicode rules.

## 3. Span invariants

For a nonempty source/structural span:

```python
0 <= start < end <= len(content.text)
content.text[start:end]  # the exact represented substring
```

Use **Python string index units**, not byte positions or automatically provider-specific UTF-16 indices. Offsets must always be associated with the specific immutable content whose text was measured. Never carry offsets from raw PDF bytes or the pre-extraction representation straight into canonical text without mapping.

For a cross-page chunk, future source resolution may return multiple locations. Implemented chunks are contiguous exact Content ranges; combining disjoint spans is not selected. Intersect a chunk with each original source span using `[max(start, span.start), min(end, span.end))` when nonempty; preserve original locators and distinguish partial-sentence coverage.

## 4. Example: same text, different source structure

```text
Canonical text: "Alpha. Beta."   (Content C1)

Document A, Version A1:
  Structure: sentence 0 -> [0,6), sentence 1 -> [7,12)
  Source: 2Wiki row R1/context 3; original sentence positions 0 and 1

Document B, Version B1:
  Structure: sentence 0 -> [0,12)
  Source: 2Wiki row R2/context 6; original sentence position 0

Both -> Content C1; neither loses its original segmentation.
```

This does **not** mean that two differently segmented versions are revisions of a common document: they can belong to two independently managed documents.

## 5. Exact duplicate lookup rule

1. Adapter extracts immutable `text` with documented construction rules and emits original mappings.
2. Compute `SHA256(text.encode('utf-8'))` and search existing `Content` candidates.
3. Confirm exact `text` equality before reusing `content_id`.
4. Create separate Document/DocumentVersion values for a genuinely independent source occurrence. U08 retries of an existing occurrence/revision return the stored registration or conflict on changed text/spans; they do not create duplicate versions or silently update descriptions. Preserve independent SourceReference, StructuralSpan and SourceSpan values.
5. Build/reuse derived chunks/embeddings only when their effective inputs/configurations permit.

Raw file hash is distinct from extracted-content hash: identical extracted text may come from different original files, and identical files may be ingested under different source identities.

## 6. Index and citation contract

A retrieval hit identifying a shared chunk/content is **not itself a complete citation**. Citation resolution must:

1. Determine candidate document versions containing that content and compatible source ranges.
2. Keep only nondeleted, current versions accessible to the querying user.
3. Map chunk source ranges to each eligible version's source spans.
4. Return version-specific source labels and precise native locators where available.
5. Preserve the possibility of citing more than one independent source supporting the same text.

An index payload may cache convenient source metadata for search efficiency, but the authoritative source-to-content relationship and eligibility remain outside the vector store.

## 7. Constraints pending explicit decisions

- The authoritative original-file/asset retention policy and ability to reproduce historical extraction.
- Remaining format-specific locators, location hierarchy and provider offset conversion; existing 2Wiki/TXT locators and U07 persistence mapping are implemented.
- Security/tenant scope and how vector pre-filtering interacts with shared vectors.
- Derived range/membership/vector schema, index strategy and update/delete/activation transaction mechanisms. U07's authoritative schema and U14's registration-specific transactions are already approved and implemented.

These are **open**; do not pretend they have been implemented.
