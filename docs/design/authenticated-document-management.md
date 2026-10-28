# Authenticated document management

The FastAPI browser application supports an optional single administrator configured with `ADMIN_API_TOKEN` and the server-owned `ADMIN_PRINCIPAL_ID`. Privileged document operations are disabled when no token is configured. Tokens use bearer authorization, are compared with `hmac.compare_digest`, and are held by the browser JavaScript only in memory. HTTPS enforcement is controlled by `ADMIN_REQUIRE_HTTPS`; deployments must terminate HTTPS at the trusted application boundary.

## Routes

| Route | Behavior |
|---|---|
| `POST /documents` | Multipart single-file registration; requires `Idempotency-Key` UUID |
| `GET /documents` | Owned and shared listing, bounded `offset`/`limit` |
| `GET /documents/{id}` | Details, versions, and indexing progress |
| `PUT /documents/{id}` | Replacement upload; requires `Idempotency-Key` and active-version `If-Match` |
| `POST /documents/{id}/index` | One explicitly requested bounded indexing batch |
| `DELETE /documents/{id}` | Idempotent soft delete |
| `POST /questions` | Public requests search shared documents; valid admin bearer scope includes shared plus admin-owned documents |

The upload key is the stable registration operation identity. Initial uploads use the UUID key as the source's opaque server-controlled ID. Replacement requests reuse the active source ID and locator and set the explicit revision to the key. Retrying a request with the same key permits repository idempotency; changed extracted text/spans conflict. A new operation must use a new key. Only one pending replacement can exist at once, per the existing lifecycle.

## Upload behavior

TXT, PDF, and DOCX are accepted by extension and independently validated by content. The request stream has a fixed 50 MiB transport ceiling (the maximum permitted configured file cap plus multipart overhead); the file byte count, filename, extracted character count, PDF page count, and DOCX ZIP expansion are additionally bounded by settings. DOCX ZIP entries are inspected in memory; extracted documents are written only to a generated temporary directory and removed after parsing. The original binary upload is not retained and no download endpoint exists. PDF extraction does not perform OCR. Client MIME type is not trusted.

Registration does not index or activate. `POST /documents/{id}/index` processes at most `MAX_INDEX_EMBEDDINGS_PER_REQUEST` newly generated embeddings (default 2), performs no retry loop, and can be explicitly called again to resume. Existing repository calls commit short transactions; provider work is outside those transactions. Activation remains governed by the existing completeness check. Progress states are derived from persisted chunk plans, embeddings, and active version; provider failures are returned as errors, not stored as content states.

## Visibility and limitations

Public `/questions` remains shared-only. A valid administrator bearer token adds only the configured principal's documents to the trusted retrieval scope; caller-provided owner IDs are ignored/rejected by the existing request model. Administrators can read shared documents, while mutation and indexing remain restricted to their private documents. Private document existence is concealed from unauthorized management callers. Soft deletion preserves history and content while immediately excluding the document from retrieval. The browser uses text nodes for dynamic filenames, metadata, extracted details, and errors. Refreshing the page clears administrator authentication.

The current implementation is a one-administrator bearer-token boundary, not user authentication or account management. The API must be deployed over HTTPS. Token revocation and rotation occur through server environment configuration. Indexing is synchronous and request-bounded; there are no workers. Pending replacement cancellation remains unsupported. No original files are retained, and no OCR is provided.
