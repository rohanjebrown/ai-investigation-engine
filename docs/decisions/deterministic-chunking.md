# ADR: Deterministic Content ranges and version-specific chunk plans

**E21 update (2026-10-27):** The [embedding storage ADR](embedding-storage.md) now governs the approved and implemented five-table storage mappings/migration and experimental no-title Gemini 768/cosine baseline. Statements below deferring physical derived schema describe the earlier milestone. Persistence workflows, generation, retrieval, production provider and remaining U14 lifecycle stay unresolved; no cloud migration has run.

**Date:** 2026-10-25  
**Status:** CONFIRMED for the scoped chunking foundation; production embedding choices remain UNRESOLVED.  
**Approval:** [E17](register.md#e17), with explicit refinements [E18](register.md#e18).  
**Decision owners:** [U11](register.md#u11), [N07](register.md#n07)-[N09](register.md#n09).

## Context

The assignment requires comparing at least two chunking strategies. The approved pinned 2Wiki development corpus contains 125,760 source occurrences, 56,680 exact Contents and 400,892 original sentence spans. Content and source identity remain different: versions sharing Content may have different sentence structure. Chunking must preserve those distinctions without adding persistent entities or coupling registration to retrieval.

The preceding read-only investigation measured short passages (reference-token median 72, p95 360, maximum 2,591). Its eight-question Gemini Embedding 2/768 pilot found no consistent advantage from title context or smaller chunks; whole passages retained complete supporting sentences with fewer retrieval units. These exploratory results justify a baseline and comparison, not a retrieval-quality claim or production embedding selection.

## Approved strategies

| Strategy | Initial configuration | Behavior and trade-off |
| --- | --- | --- |
| Whole passage | Caller supplies the content-token ceiling; 32,000 is used for the reference corpus audit only | Keep an entire passage when it fits. Otherwise use deterministic fixed-token splits with zero overlap. Do not combine independent passages. Minimizes splitting for this short corpus. |
| Fixed tokens | Experimental maximum 128, target overlap 16 | Advance using actual token offset mappings, then re-encode the exact original slice to enforce its budget. Can split sentences and repeat characters. |
| Sentence packing | Experimental maximum 128, overlap 0 | Greedily pack supplied consecutive sentence units from the specific version. Keep full sentences when they fit; split oversized units deterministically with zero overlap. Capacity can be unused. |

These experimental parameters are not a final default for every document type. The preceding investigation's A128/B128 labels meant fixed/sentence respectively; this ADR uses descriptive names to avoid confusing those historical labels with the approval's whole/fixed/sentence lettering.

Sentence separators join the preceding packing unit, leading whitespace joins the first, and trailing whitespace joins the last. No source characters are trimmed, normalized, inserted or dropped. Sentence and source spans themselves are unchanged. If sentence structure is absent or leaves non-whitespace gaps, use fixed splitting with zero overlap. Invalid ordering, overlaps or out-of-bounds sentence ranges raise ValueError; no silent repair or new sentence detection.

Empty Content produces no chunks. Nonempty whitespace-only Content is retained. This does not decide extraction acceptance, indexing readiness, or activation.

## Minimal application representations

- `Chunk` is an immutable application value containing an existing immutable Content reference and start/end. Its conceptual identity is `(content.sha256, start, end)` and its text property is always `content.text[start:end]`. It has no UUID, title, source provenance, tokenizer fields or copied text.
- `ChunkingConfig` records strategy, exact tokenizer identity, content-token budget, overlap and algorithm implementation version. Policies fixed by the implementation version are documented here rather than exposed as unused configurable switches. A change to boundary handling, oversized splitting or fallback behavior requires a new algorithm version.
- `TokenizerIdentity` records name, repository revision, artifact SHA-256 and runtime version. It identifies the actual encoding artifact without selecting an embedding service.
- `ChunkPlan` records a DocumentVersion ID, one configuration, and ordered chunks. This is a transient membership result, not a database table, registry, indexing job or new authoritative entity.

The one `TextTokenizer` application port supplies identity and original-character token ranges. Pure selection functions depend on this port and existing domain dataclasses. Infrastructure contains the concrete artifact loader. No SQLAlchemy, HTTP client, provider SDK or local embedding model enters the application/domain chunking code.

Chunk plans use DocumentVersion structure, not Content alone. Identical ranges can be present in multiple plans. The algorithm performs no global caching, persistence, registration, Content reuse lookup, or metadata updates.

## Tokenizer and Unicode boundary behavior

[N07](register.md#n07) approves **tokenizers==0.23.3** and a small file-loading adapter as an experimental reference. It does not approve a production tokenizer/provider.

Reference artifact:

- Repository: `MongoDB/voyage-4`.
- Revision: `44f3b2ae4ddf33403ed4dd66bec3fa48ff7dbbf9`.
- [Explicit public artifact](https://huggingface.co/MongoDB/voyage-4/resolve/44f3b2ae4ddf33403ed4dd66bec3fa48ff7dbbf9/tokenizer.json); file SHA-256: `c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539`.
- Runtime: `tokenizers==0.23.3`, installed and locked through uv.

The adapter reads an explicitly supplied local file and checks its SHA-256. It never downloads implicitly. Missing files or mismatched artifacts fail; there is no automatic fallback tokenizer. It disables padding, truncation and added special tokens and returns the Python binding's character offsets. The configuration identity must equal the actual adapter identity.

The pinned artifact contains an **NFC normalizer** inside token encoding. Canonical Content remains original: decomposed accents are not rewritten and do not acquire a normalized Content hash. Offset mappings refer to the original supplied Python string, and every chunk is sliced from that string. Source indices are not replaced with token indices, normalized-string positions or UTF-8 byte positions. Token IDs are never decoded to construct text.

Several byte tokens can map to the same Unicode character. Fixed splitting therefore selects distinct original-character token starts, re-encodes a candidate exact slice and retreats to an earlier boundary if necessary. Overlap targets the last configured number of tokens in the emitted slice; rounding to a whole original character can increase the represented overlap. When no advancing overlap start exists, advance to the prior end. Never emit an empty chunk or loop without progress. An impossibly small budget that cannot fit an indivisible character/legal token boundary raises a descriptive ValueError.

These content-token budgets do not include future titles or task prefixes. The eventual embedding adapter must validate its complete effective input and reject/split oversized inputs without silent truncation. Voyage counts are not exact Gemini counts. See [Voyage tokenization](https://docs.voyageai.com/docs/tokenization) and the [versioned tokenizers API](https://huggingface.co/docs/tokenizers/v0.20.3/en/api/encoding); the installed pinned runtime and actual artifact, not the changing latest documentation, determine verification behavior.

Example explicit construction (no download or inference):

```python
from pathlib import Path
from investigation_engine.application.chunking import (
    ChunkingConfig,
    ChunkingStrategy,
    chunk_document,
)
from investigation_engine.infrastructure.tokenization.huggingface_tokenizer import (
    HuggingFaceTokenizer,
    VOYAGE_REFERENCE_NAME,
    VOYAGE_REFERENCE_REVISION,
    VOYAGE_REFERENCE_SHA256,
)

tokenizer = HuggingFaceTokenizer(
    Path("/explicit/path/to/tokenizer.json"),
    name=VOYAGE_REFERENCE_NAME,
    revision=VOYAGE_REFERENCE_REVISION,
    expected_sha256=VOYAGE_REFERENCE_SHA256,
)
config = ChunkingConfig(
    strategy=ChunkingStrategy.FIXED_TOKENS,
    tokenizer=tokenizer.identity,
    max_tokens=128,
    overlap=16,
)
plan = chunk_document(version, config=config, tokenizer=tokenizer)
```

The caller supplies an existing version; registration and its async transaction contract are unchanged. Chunk generation is synchronous CPU work and performs no I/O through the registration repository.

## Provenance and future embeddings

Authoritative provenance stays on DocumentVersion. A future eligible-source resolver will combine a range with a version that produced it, that version's SourceReference and its original source spans. The covered intersection is `[max(chunk.start, span.start), min(chunk.end, span.end))` when nonempty. A partial sentence retains the original sentence locator and partial character interval; it is not a whole-sentence claim. No citation resolver, eligibility query or source-span mutation is implemented here.

Range identity does not determine embedding identity. Future reuse requires equivalent complete effective input, provider/model/revision, document/query encoding, formatting, dimensions, normalization and other output-affecting settings. A different title may prevent reuse over the same range; different strategies may share an embedding when actual input/configuration is equivalent. This approval adds no embedding cache or physical schema.

Hosted inference is preferred. Gemini Embedding 2, 768 dimensions and cosine remain experimental. Title-aware versus body-only input formatting and a final provider remain open. No API calls or embedding SDK are part of this implementation.

## Verification and measurements

**Executed on 2026-10-25:** Temporary verification outside the repository passed focused Unicode/whitespace/empty/oversized-input and configuration checks, then read the complete unchanged pinned dev file. It reproduced dataset revision `612bc5039a457880d9e7d84c3b0a4cf154b70e4f` and file SHA-256 `c0d8b60b9026b728fb07ad74c5252a0f188f6942e8ba5c02df4dfa369502ea8d`: 125,760 occurrences, 56,680 Contents, 56,702 Content/sentence layouts, and 400,892 original sentence spans. Gold questions, answers and supporting facts were not input to chunking.

Checks covered exact slices, remeasured per-chunk token ceilings, complete ordered character coverage, nonempty/progressing ranges, target overlap, source-span intersections, original sentence indices, unchanged shared Content/provenance, oversized whole passages/sentences, fallback equal to fixed 0-overlap, invalid configuration/structure/artifact identity, and distinguishable immutable configurations. Sixty seeded mixed-Unicode examples and real TXT extraction covered NFC/NFD accents, emoji/ZWJ/CJK, tabs, nonbreaking spaces, BOM, CRLF/CR/LF and whitespace-only input. Determinism was repeated for focused cases, the first 30 unique layouts per configuration, and all shared-Content/different-layout cases; the audit did not rerun every plan twice. An impossibly small byte-token budget failed descriptively instead of truncating or looping.

The audit generated each distinct layout plan and weighted its memberships by actual occurrence frequency. For structure-independent whole/fixed plans, it reused computed ranges over identical Content within the temporary audit only; production code has no cache. It verified every original sentence mapping and every emitted plan's character coverage and measured chunk budgets. Fixed/whole plans were also verified on multiple versions. No whole-corpus registration, PostgreSQL operation or embedding request was performed.

### Corpus results

The following distributions weight all original source occurrences. A membership means one chunk within one occurrence/configuration plan, not a globally distinct range or independent source record.

| Configuration | Total memberships | Distinct Content ranges | Mean / median tokens | Token p90 / p95 / p99 / max | Occurrences split | Crosses multiple sentences |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Whole, experimental audit ceiling 32,000 | 125,760 | 56,680 | 93.35 / 58 | 196 / 317 / 599 / 2,591 | 0% | 64.15% |
| Fixed 128/16 | 167,949 | 83,473 | 73.92 / 67 | 128 / 128 / 128 / 128 | 18.03% | 70.31% |
| Sentence 128/0 | 169,760 | 84,637 | 69.43 / 67 | 119 / 124 / 128 / 128 | 18.03% | 66.57% |

| Configuration | Mean / median characters | Character p90 / p95 / p99 / max | Distinct range-plus-title inputs (future estimate only) |
| --- | ---: | ---: | ---: |
| Whole | 355.16 / 203 | 757 / 1,229 / 2,531 / 9,764 | 56,687 |
| Fixed 128/16 | 282.43 / 247 | 530 / 574 / 635 / 850 | 83,481 |
| Sentence 128/0 | 263.10 / 245 | 475 / 518 / 579 / 760 | 84,645 |

The body-only input estimate is the distinct range count, assuming an otherwise identical future encoding profile. Title-specific figures distinguish exact titles; they do not select title-aware encoding, embedding identity/storage, or a provider. Equivalent text across different ranges could permit additional reuse, but a global substring cache is not implemented. Counts are not embeddings actually generated.

The fixed plan repeats 2,769,262 source characters over the occurrence-weighted original total of 44,664,485: **6.20% character duplication**. Whole/sentence plans repeat zero characters. This is distinct from the preceding investigation's **6.71% tokenization overhead** over unique layouts: character overlap, re-tokenization at boundaries and population weighting have different denominators.

Across unique Content/sentence layouts, fixed and sentence strategies differ on **14,705 / 56,702 (25.93%)**, representing **22,669 / 125,760 occurrences (18.03%)**. Unique-layout memberships are 56,702 / 83,516 / 84,678 for whole/fixed/sentence. Their mean/median token lengths are 112.78/72, 81.71/81 and 75.87/79. Their chunk-crossing rates are 71.06%, 76.86% and 72.03%, and inside-sentence cut rates are 0%, 48.61% and 0.51%. Occurrence-weighted and unique-layout distributions must not be presented as interchangeable.

Some oversized-unit splits can emit a nonempty separator-only range. It remains an exact Content slice and must not acquire an invented source locator; future citation resolution uses only real nonempty source intersections.

### Actual shared-Content example

All **22 Contents with multiple supplied sentence layouts** were checked. One produces different sentence-packed plans at 128 tokens: **Carlos F. Borcosque**, Content SHA-256 `c860f288a7ec50391a78092a0cfc2b7605ca0b7d59415cfca249ee1247f0a18c`.

| Source coordinates in pinned dev | Original sentence ranges | Sentence-packed chunk ranges |
| --- | --- | --- |
| example `c289098a0bdc11eba7f7acde48001122`, context 1 | [0,209), [210,463), [464,498), [499,658) | [0,499), [499,658) |
| example `abaa3fca08e611ebbda5ac1f6bf848b6`, context 4 | [0,209), [210,463), [464,658) | [0,464), [464,658) |

Both versions retain the same immutable Content reference and different source references. The original sentence/source spans and indices remain unchanged. The audit checked clipped source intervals, not rendered citations, authorization or retrieval eligibility.

### Comparison and limits

The emitted distinct range counts **exactly match** the preliminary investigation's whole/fixed128/sentence128 counts: 56,680 / 83,473 / 84,637. No tokenizer change or range-count discrepancy occurred. This implementation additionally records actual occurrence memberships and weighted distributions. It explicitly documents the artifact's internal NFC normalization; no canonical normalization or semantic merging occurs.

Static syntax and offline uv lock checks passed. Only tokenizers 0.23.3 was added; no existing dependency version changed. Pre/post hashes confirm unchanged domain, extraction/registration/repository implementations, ORM models, authoritative schema and migrations. No permanent automated test files were added. This is scoped temporary verification, not a production load test or evidence of retrieval improvement.

An additional async in-memory registration smoke check passed: two independent TXT uploads kept separate Document IDs while sharing the same immutable Content; an identical retry preserved stored IDs; chunking consumed each returned DocumentVersion without activating it. PostgreSQL was not rerun for this chunking-only change; its implementation and mappings/migrations were hash-verified unchanged.

Temporary raw measurement files were retained outside the repository at `C:/Users/merli/AppData/Local/Temp/aie-chunk-implementation-3mg7r5j1/`; the portable results needed for review are recorded above. Temporary paths are not a deployment dependency, and the adapter always requires an explicit tokenizer file.

## Scope still deferred

Production model/tokenizer, final chunking default/budgets, effective embedding formatting/title policy, derived PostgreSQL schema and memberships, vector/full-text indexes, retrieval/RRF, production parameters, workers, activation/recovery, LLMs, API/authentication and deployment remain their separate decisions. U07's authoritative schema, registration invariants, exact Content reuse and canonical/source models are unchanged.

The final U17 evaluation still needs at least 40 questions and full-corpus retrieval/answer/citation assessment. Chunk counts and preserved boundaries do not establish better retrieval.
