# Document lifecycle, exact reuse, index retirement and recovery

**Status:** Core application lifecycle implemented under E35; background orchestration, authentication and operational recovery remain open.
**Applies to:** Document, DocumentVersion, Content, provenance mappings, derived chunks and vectors.

**Approval reconciliation:** [U07](../decisions/register.md#u07) implements the physical schema foundation. [U10](../decisions/register.md#u10) approves nullable owner references, soft deletion retaining the active-version pointer/history/Content, and eligibility exclusions. [U08](../decisions/register.md#u08) specifies authorized deleted-registration retries without restoration. [E16/U14](../decisions/register.md#u14) implements async PostgreSQL registration; [E35](../decisions/register.md#e35) adds scoped application updates, deletion and listing. Authentication, public management endpoints, background orchestration and recovery beyond same-revision retries remain unimplemented. Historical proposals below are design context; the implemented behavior is summarized in section 11.

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

- Durable background orchestration, worker claims/scheduling, and whether to use an outbox. PostgreSQL authority, schema, registration, foreground indexing and activation transactions are already implemented.
- Persisted readiness/job states, cancellation/discard and automated recovery for an unindexable pending version. Current foreground indexing is resumable for a selected version/profile.
- Reference tracking/cleanup policy and historical retention boundaries.
- Authentication/principal sourcing and cross-tenant content-sharing policy.
- Upload lifecycle details: source identity generation, unchanged-file reupload, retention of original bytes, and format-specific replacement/failure behavior beyond the implemented extraction contracts.

## 11. Implemented application lifecycle (E35)

### Update and retry contract

`UpdateDocument` receives a `DocumentActor` from a trusted caller, the target document, an `ExtractedDocument`, and the expected current version ID. The actor principal must match the document owner; a shared (`owner_id IS NULL`) document is system-managed and cannot be updated by a user actor. The expected ID provides optimistic stale-write protection.

An update must preserve the current source `kind`, `source_id`, and `record_locator`, and must supply a new nonblank explicit `revision`. This keeps a logical source occurrence attached to one logical Document while making revision identity deliberate. A retry with the same source identity/revision returns the existing version only when canonical text, structural spans, and source spans match; otherwise it conflicts. Descriptive metadata is not rewritten. A different revision creates a new immutable version even when text is identical, allowing exact Content reuse without collapsing history.

The PostgreSQL repository takes a short READ COMMITTED transaction and locks the Document row. It checks the actor/deleted state, resolves a source-revision retry, compares the expected active version, rejects a second distinct pending version, allocates the next version number, inserts/reuses exact Content, and commits the inactive version. Extraction, chunking, embedding calls, and indexing happen outside this transaction. The in-memory implementation mirrors behavior sequentially but has no durability or cross-process concurrency guarantee.

### Pending version and activation policy

Only one version newer than the active pointer may be pending. Repeating the same source revision is allowed and returns its stored version; a distinct update is rejected until indexing/activation succeeds. Version numbering is protected by the document-row lock. If no version is active, updates are rejected until initial indexing activates version 1.

Activation still verifies selected chunk-plan/vector completeness and locks the same Document row. It rejects deleted documents and any target that is not the newest stored version. Thus deletion and activation serialize, and an older indexing operation cannot reactivate a superseded version. Failed indexing leaves the prior active pointer unchanged.

There is no durable pending-job state, cancellation/discard operation, automatic retry scheduler, or automatic deletion of failed pending versions. If a version remains unindexable, another update to that Document is blocked; callers can retry its indexing, while a future recovery policy must decide how to abandon it safely.

### Soft deletion and access

`DeleteDocument` is idempotent for an owned private document. It locks the Document row, sets `deleted_at` once, and preserves versions, current pointer, provenance, Content, chunks, and embeddings. Existing semantic and hybrid retrieval filters exclude the deleted Document immediately; shared content remains eligible through other active documents. Activation checks deleted state after acquiring the same row lock and fails if deletion won the race.

`DocumentActor` is an application trust-context value, not authentication. The caller must construct it only from trusted identity context. Reads permit shared documents and exact-owner private documents; updates and deletes require exact ownership and reject shared documents. Missing and unauthorized documents have distinct internal errors; an eventual HTTP layer must map them without leaking private-document existence.

`ListDocuments` validates offset and a limit from 1 through 100. PostgreSQL applies `(owner_id IS NULL OR owner_id = actor)` and deletion predicates in its query, orders by creation time and UUID for stable pagination, and returns simple application read models rather than ORM rows. Deleted entries are omitted by default; an authorized caller may request them explicitly. `GetDocument` returns the document state and version history, including deletion state, only to an authorized reader.

The public API and browser do not yet expose these operations. Authentication/principal sourcing, shared-document administrative operations, HTTP error policy, private citation retention, and pending-version cancellation/recovery remain separate decisions.
