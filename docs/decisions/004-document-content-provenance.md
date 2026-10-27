# ADR-004 — Document identity, versions, reusable content and provenance

**Date:** 2026-10-26  
**Status:** CONFIRMED for the baseline principles in [A06](register.md#a06)-[A09](register.md#a09); detailed representations and mechanisms remain UNRESOLVED.  
**Scope:** Ingestion domain representation, exact duplicate reuse, document lifecycle and evidence lineage.

## Approval reconciliation (2026-10-26)

The user explicitly ratified document/version separation, exact-text reuse, version-specific spans, and activation after indexing: [A06](register.md#a06), [A07](register.md#a07), [A08](register.md#a08), [A09](register.md#a09), approval [E01](register.md#e01). The historical "Accepted at conceptual level" label does not approve every candidate field or implementation detail.

Physical keys/tables and constraints remain [U07](register.md#u07); dataset identity and assembly are [U01](register.md#u01)/[U02](register.md#u02); source representations and extraction policies are [U04](register.md#u04)/[U09](register.md#u09). Retention, access, concurrency, retries, and cleanup require [U10](register.md#u10)/[U14](register.md#u14). No separate occurrence table, hierarchical span field, or search interface is selected by this ADR's examples.

Alternatives below retain their historical reasoning. An alternative described as rejected or not selected is not automatically a previously approved decision eligible for SUPERSEDED status. The registration/content reuse implementation exists; update/delete/index recovery and citation scenarios remain unimplemented or unverified. See the register's [observed evidence](register.md#observed-evidence).

## Context

Upgaming requires source-attributed multi-document investigations, 3+ source formats, duplicate handling, updates/deletes, multiple chunking strategies and repeatable evaluation. Our initial 2WikiMultihopQA audit found repeated contexts and distinct sentence segmentations of similar article texts. We must preserve source-specific location references while preventing avoidable duplicate embedding/index work.

The earlier simplistic model `Document(id, source, title, text, segments, segment_kind)` is insufficient: it mixes source identity, content, structure and lifecycle; it cannot robustly model version history, reused content or multi-location citations.

## Decision

### Independent logical documents and explicit revisions

- **`Document`** is an independently managed logical document with a stable identifier, lifecycle and pointer to the current revision.
- **`DocumentVersion`** records an immutable revision, including which reusable content it references and the source-specific extraction/provenance associated with *that revision*.
- Document B is *not* a version of Document A even if their text is byte-for-byte identical.
- The document's current-version pointer is authoritative; revision history need not be searchable.

### Reusable immutable canonical text

- **`Content`** stores one complete extracted-text representation and a SHA-256 fingerprint of **the exact canonical text bytes encoded in UTF-8**.
- Two versions may reference the same content when the canonical strings are exactly equal.
- **No extra deduplication normalization:** no whitespace collapsing, case folding, semantic similarity or approximate matching for this identity comparison.
- Canonical text still has to be *constructed* deterministically by adapters (e.g. a documented separator between source sentences). That is extraction policy, **not** a secondary normalization pass intended to force near-matches to merge.
- A hash match is a lookup candidate; guard against accidental equality claims by verifying the canonical text, particularly before reusing a content record.
- Source format, sentence boundaries and page locations **do not** enter content identity. They live in version-specific mappings.

### Canonical text + version-specific structure and locations

- Each `DocumentVersion` references **exactly one** complete `Content` representation (for the initial text-centric system).
- `StructuralSpan`: structural type plus offsets into that text (sentence, paragraph, heading, etc.); optional hierarchy when available.
- `SourceSpan`: offsets into the same text associated with typed original locators (page, paragraph, line, benchmark sentence position, etc.).
- Structure and source-location mappings are **version-specific**; two versions sharing text may have different segmentations or page numbers.
- Offsets are **zero-based, half-open `[start,end)` Python string positions** into the immutable canonical text; code-point units, not UTF-8 bytes. Convert units if a provider uses another convention.
- Preserve unknown/unavailable locators as unavailable; never fabricate them.

### Chunking/indexing remain derived concerns

**Later scoped approval (2026-10-25):** E17/E18 now approve and implement contiguous exact Content ranges and transient version/configuration chunk plans; see the [chunking ADR](deterministic-chunking.md). Future augmented embedding input stays separate from chunk text. Historical disjoint-range examples below do not authorize disjoint chunks, new identifiers or physical tables.

- Chunks are created **after** extraction/storage from the canonical text, using one of multiple chunking strategies.
- Chunks retain a source mapping as one or more canonical-text spans; don't assert one continuous span when chunk text is augmented, reordered or disjoint.
- Text-only chunks may be shared across content; structure-aware chunks can be version-specific even with identical content. Embedding reuse also requires the same effective embedding input and model/configuration.
- The **vector DB is a rebuildable index**, not the sole authority for document identity, history, permissions or provenance.

### Retiring old versions / deleting documents

- Updates create new versions; do not overwrite old immutable data.
- Prepare a new index representation before making it current; then atomically switch the authoritative document pointer where the selected database permits.
- Historical versions are not eligible for normal search. A shared vector remains if it still serves another *active, authorized* source.
- Deleting one document removes its active retrieval eligibility without deleting shared content that other active versions use. Historical content/source retention follows a separate policy.
- Validate retrieved points against authoritative current-version and authorization state to prevent stale citations; eventual background index cleanup is not the security boundary.

### Semantic redundancy

- No automatic semantic duplicate removal before indexing.
- Consider retrieval-time grouping/diversification of redundant evidence later, with explicit evaluation and without losing independent citations or contradictory details.

## Conceptual cardinalities

| Relationship | Cardinality / note |
| --- | --- |
| Document → DocumentVersion | 1 to many |
| Document → current DocumentVersion | 0 or 1 pointer; must belong to that document |
| DocumentVersion → Content | many-to-one; exactly one content per version initially |
| DocumentVersion → StructuralSpan | one-to-many, possibly none |
| DocumentVersion → SourceSpan | one-to-many, possibly none when not extractable |
| DocumentVersion → SourceReference | one version-specific provenance record; may include occurrence-specific coordinates |
| Content → derived chunks | one-to-many *when text-only chunking*; may also require version-specific derivations |
| Chunk → embedding/index representation | derived and configuration-dependent; not assumed universal 1:1 |

`SourceReference`, `StructuralSpan` and `SourceSpan` are conceptual value objects; **no decision yet** to create a separate physical table for each. `ContentOccurrence` is not an initial standalone entity: version-specific source mappings handle the current use cases, subject to dataset-occurrence validation.

## Alternatives considered

| Alternative | Why not selected now |
| --- | --- |
| Single `Document` with text and version integer | Does not preserve independent history and encourages conflating content, provenance and identity |
| Full version-history table but separate text per version | Correct but needlessly duplicates exact text across independent documents |
| Exact equality includes sentence/page structure | Preserves structure but prevents safe reuse of identical canonical text; version-specific spans already preserve those differences |
| Normalized/fuzzy dedup during ingestion | May merge text that is not exactly equal; explicitly deferred |
| Semantic merging based only on embedding distance | Can suppress contradictory facts, dates and independently attributable evidence |
| One `Content` row per page/paragraph | Adds arbitrary subdivisions before chunking; not justified by current formats/scale |
| Vector DB as authoritative source store | Makes version history, deletion, multi-source citation and rebuilds unnecessarily fragile |

## Consequences and engineering risks

**Benefits:** Reuse identical extracted text and potentially embeddings; preserve independent users' documents, original sentence positions and PDF/DOCX citations; straightforward re-chunking; recoverable index rebuild; explicit historical revisions.

**Costs:** Version/content joins, garbage-collection rules, citation resolution across shared text, and index consistency need careful implementation. A simple shared vector record is **not automatically safe for per-user access control**: authorization and current-version eligibility must be enforced during retrieval, possibly requiring tenant-scoped indexes or oversampling before filtering.

**Important edge case:** If chunking uses a document version's structure, identical text may yield different chunk boundaries; sharing a content row does not entitle us to share that chunk or vector. If benchmark evidence is expressed as `(title, sentence_index)`, store the dataset-context occurrence too so the sentence index is interpreted correctly.

## Acceptance criteria

Some domain/registration/chunking cases now have temporary verification recorded in the register. End-to-end citation, lifecycle and multi-format criteria remain incomplete; this list is not a claim of a passing test suite.

1. Two independently uploaded files with equal canonical text retain different `Document` IDs and source locators while referencing one `Content` record.
2. A version update preserves history; when the new version is active, stale version-specific results cannot be cited as current.
3. Deleting one document does not remove searchable evidence that another authorized current document still supplies.
4. Different source sentence boundaries over the same text preserve their original indexes and citations.
5. Cross-page chunks resolve all relevant original page locations.
6. Different chunking strategies can be rebuilt from the same `Content` without rewriting versions.
7. Search/index failure leaves a recoverable state; no partial revision is silently presented as current.
8. Source-aware authorization is enforced even if text/vector is shared (if user accounts are implemented).

## Open implementation decisions

- Database(s), physical table and JSON layout, ORM vs domain dataclasses, file retention and security policy.
- Chosen text construction algorithm and extraction version tagging per format.
- Precise location locator variants, structural nesting and empty/failed extraction behavior.
- Dataset logical-document identity vs per-question occurrence identity.
- Index entry identity, eligibility filtering and transactional retry strategy.
- Historical retention/garbage collection policy and corpus snapshots for reproducibility.

## Industry references

- [W3C PROV-O](https://www.w3.org/TR/prov-o/) supports the principle of source/derived-entity lineage.
- [Azure Document Intelligence layout](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout) and [Google Document AI `TextAnchor`](https://docs.cloud.google.com/document-ai/docs/reference/rest/v1/Document) illustrate text-plus-spans representations.
- [Azure AI Search index projections](https://learn.microsoft.com/en-us/azure/search/search-how-to-define-index-projections) documents parent/child chunk indexing patterns.
- [LlamaIndex ingestion pipeline implementation](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/ingestion/pipeline.py) illustrates ID/hash-driven ingest and upsert strategies; **not** an assertion that it uses this exact architecture.
