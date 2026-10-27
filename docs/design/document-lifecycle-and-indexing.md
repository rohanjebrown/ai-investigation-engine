# Document lifecycle, exact reuse, index retirement and recovery

**Status:** Agreed behavior; transaction/index mechanism not yet chosen.  
**Applies to:** Document, DocumentVersion, Content, provenance mappings, derived chunks and vectors.

**Later approval reconciliation (2026-10-25):** [U07](../decisions/register.md#u07) implements the physical schema foundation. [U10](../decisions/register.md#u10) approves nullable owner references, soft deletion retaining the active-version pointer/history/Content, and eligibility exclusions. [U08](../decisions/register.md#u08) specifies authorized deleted-registration retries without restoration. [E16/U14](../decisions/register.md#u14) now implements async PostgreSQL registration with required trusted ownership, repository-owned READ COMMITTED transactions, full rollback/bounded conflict resolution, and cancellation cleanup. Historical suggestions below do not replace these approvals. Authentication, deletion endpoints, activation, cross-revision updates, and indexing/lifecycle recovery remain unimplemented; their mechanisms remain open.

## 1. Governing principle

**The authoritative data store answers which documents and versions exist and are eligible.** Search indexes only speed up discovery of evidence. Identical canonical text may be physically shared; independent document identity and citation provenance may not be discarded.

`Document.current_version_id` identifies the active version. Do not simultaneously maintain a conflicting independently mutable `is_current` flag on versions.

## 2. Create

Input: a previously unseen upload or source record.

1. Register a stable `Document` identity and original source identity.
2. Extract deterministic complete canonical text plus version-specific structural/source spans.
3. Insert/reuse immutable `Content` by *exact* text equality (hash-assisted lookup).
4. Register `DocumentVersion` number `1` with its own source reference, spans and content reference.
5. Build or reuse chunks and search representations where permitted by strategy/model configuration.
6. Mark the document version current only once required indexing is ready (confirmed A09; activation workflow unimplemented).

**Implemented retry policy:** U08 resolves the same source occurrence/revision through kind, source_id, revision and record_locator, returning the stored registration or conflicting on changed text/spans. Independent uploads require distinct stable source IDs. No additional request-id field is selected.

## 3. Upload another independent document with equal text

For `report_a.txt` and `report_b.txt` having equal canonical text:

```text
Document A -> Version A1 --+
                           +--> Content X -> text-only chunks -> embeddings
Document B -> Version B1 --+
```

- Create separate `Document` and first-version identities.
- Reuse `Content X`; preserve separate filename/original-file identity and native location spans.
- Never imply A1 and B1 are versions of the same logical document.
- Reuse chunks and embeddings **only** when input and relevant processing configurations match; otherwise create distinct derived items.

## 4. Update one document

Example: A1 and B1 reference X; A2 changes to Y.

```text
A -> A1 (historical) -> X <- B1 (current) <- B
 \-> A2 (current)    -> Y
```

1. Leave A1 immutable and store new A2 referencing Y (which may itself already exist).
2. Preserve B1 and its source associations.
3. Generate/reuse A2 indexing; keep A1 as current if required work fails.
4. On success, switch A's `current_version_id` to A2 in the authoritative store.
5. After switch, normal retrieval must reject A1; background cleanup retires unnecessary physical index entries.
6. X is still needed because B1 is current, so X's searchable content remains eligible through B1.

### Why switch only after indexing?

It avoids advertising a newly current version before its searchable representation is available. A09 confirms this rule; indexing-readiness, activation and recovery mechanisms remain open under U14.

## 5. Delete a document

- Mark the logical document deleted or otherwise make it immediately ineligible for ordinary retrieval.
- Historical versions may remain according to a separately chosen retention/privacy policy.
- Do not delete shared `Content` or vectors while another eligible document requires them.
- When no current eligible document needs an index entry, retire that entry; underlying historical content may still be retained.
- Physical garbage collection can be eventual; **query/citation eligibility cannot be eventual** if it risks returning deleted or unauthorized sources.

## 6. Retire old vector entries

We have **not** decided on one vector per document version. A vector/index entry could be shared by many current versions for identical text-only chunks. Therefore the safe criterion is **current eligible references**, not simply "is any referencing version old?"

Proposed retirement algorithm (conceptual):

```text
For each derived index entry:
    Find current, nondeleted, authorized/document-eligible source associations.
    If none exist for active retrieval, mark for index removal.
    If some exist, retain and resolve only those eligible sources.
```

Avoid relying only on a cached reference count when concurrent updates/deletions are possible unless it is transactionally maintained. A join/query-derived count is a credible simpler initial option.

## 7. Retrieval-time enforcement and citations

Every candidate result must be resolved to one or more *current, nondeleted, authorized* document versions **before** exposure to the LLM or the user. Historical results are not valid ordinary citations merely because a stale vector exists.

Shared vector points introduce a retrieval-quality and security challenge: filtering after top-K may leave too few authorized results. Consider prefiltering, tenant-scoped vector representations, or retrieving an expanded candidate pool, and test this behavior. **Do not treat post-filtering as equivalent to secure efficient retrieval for every configuration.**

Deduplicating/combining retrieved evidence is a downstream ranking operation and must not silently conflate distinct claims or sources.

## 8. Failure/retry cases

| Failure | Required handling |
| --- | --- |
| Extraction fails | The 2Wiki adapter raises without persisting a failure record (U09); other format policies remain open. Do not present failed extraction as indexed |
| Content stored, index operation fails | Retry safely; current version remains consistent with indexing policy |
| Index write succeeds, database pointer change fails | Treat new derived records as pending/orphan candidates; reconcile; do not expose them as current |
| Old vector lingers after version retirement | Resolver rejects old version; cleanup later |
| Shared content referenced by two documents | Deleting/retiring one preserves the other |
| Concurrent uploads of identical text | Unique hash-assisted transaction and equality check prevent duplicate content rows |
| Source deleted after an investigation was stored | Historical citation availability must follow an explicitly chosen retention policy |

A transaction outbox/index job queue is a **candidate** reliability mechanism, not an agreed dependency or implemented feature.

## 9. Historical evaluation reproducibility

Version rows alone are not enough to reproduce old evaluations. Store/retain, as policy permits, a corpus snapshot or manifest specifying which document versions participated, dataset revision, extraction and chunking configs, model versions, ranking parameters, prompts and evaluation questions/labels. The full original bytes might be required to rerun extraction; determine original-file retention separately.

## 10. Remaining decisions

- Derived storage and indexing transaction design; whether to use background queue/outbox. PostgreSQL authority, authoritative schema and registration transactions are already approved/implemented.
- Index readiness states and retry policy.
- Reference tracking/cleanup policy and historical retention boundaries.
- Authentication and cross-tenant content-sharing safety.
- Format-specific document replacement, failed/empty extraction and unchanged-file reupload semantics.
