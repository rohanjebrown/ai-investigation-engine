# TXT, PDF and DOCX extraction

E34 implements file-format adapters behind the existing ExtractedDocument contract.
This is local synchronous extraction, not an upload endpoint or new ingestion
orchestrator. The trusted caller assigns a stable source_id and later calls
RegisterDocument.execute(extracted, owner_id=...). A different independent upload
needs a different source ID; filenames and content hashes do not establish source
identity. Reprocessing changed text/spans under the same occurrence/revision still
conflicts under U08. No automatic source-revision update is introduced.

```python
from pathlib import Path
from investigation_engine.infrastructure.ingestion.pdf_adapter import extract_pdf
from investigation_engine.infrastructure.ingestion.docx_adapter import extract_docx

pdf = extract_pdf(Path("source.pdf"), source_id="trusted-upload-id-1")
word = extract_docx(Path("source.docx"), source_id="trusted-upload-id-2")
# Then use the existing async registration, chunking and indexing interfaces.
```

## Dependencies and boundaries

The uv environment locks pypdf 6.21.3 (BSD-3-Clause) and python-docx 1.2.0 (MIT),
with lxml 6.1.3 as python-docx's compiled dependency. pypdf is pure Python; neither
adapter needs Office, LibreOffice, Poppler or OCR at runtime. Python 3.14 operation
is verified by this project's tests on Windows, not asserted for every deployment
platform. Other platforms need compatible lxml wheels or build prerequisites.
Official metadata: [pypdf](https://pypi.org/project/pypdf/),
[python-docx](https://pypi.org/project/python-docx/).

Parser imports remain in infrastructure. The only domain change adds table, row
and cell values to the existing LocatorKind enum. Existing JSONB span storage
already persists these strings; no ORM or migration change is needed. No new
document model, registration logic, chunking strategy, retrieval branch or RRF
behavior is introduced. TXT extraction and its exact newline behavior are unchanged.

## Canonical text and provenance

All offsets use Python Unicode character positions, half-open [start,end), not
byte offsets. No whitespace collapsing, Unicode normalization, semantic merging
or title insertion is performed. Equality/deduplication remains exact canonical
text SHA-256 through the existing registration mechanism. Parser updates can change
extracted text; record the locked parser version and treat resulting U08 conflicts
explicitly rather than silently overwriting earlier versions.

Both new adapters preserve the caller's source ID, UPLOAD kind, filename as
display_name, default absent revision and empty record_locator. Structured source
metadata carries format, filename, parser name/version and source properties.
Parser objects are not returned. Empty/missing titles fall back to filename.
No original bytes, local absolute file path, invented URL or ownership metadata
is added to provenance. Original-file retention/access remains separate U10 work.

### PDF

`extract_pdf(path, source_id=...)` uses strict PdfReader parsing and plain text
extraction in original page order. It preserves each readable page's parser text,
including emitted trailing newlines, and inserts exactly two LF characters between
retained pages. These separators are outside source spans. Page locators are
**one-based original page numbers**; blank/whitespace-only pages receive no text
span and are listed in metadata. A blank page does not renumber later pages.

The fixture has readable pages 1 and 3 and a blank page 2. Its spans are [0,82)
and [84,157), with [82,84) reserved for the inserted separator. Page 1's own
trailing newline is retained, so the visible boundary can contain three LFs.
The fixture was generated with ReportLab's embedded Vera font and rendered using
bundled Poppler; readable pages were visually checked for Unicode glyphs/clipping.
ReportLab/Poppler are fixture-authoring tools, not project dependencies.

Metadata preserves textual title, author, subject, keywords, creator, producer,
and raw PDF creation/modification date strings where present. page_count and
empty_pages are structured values; nontext binary metadata/XMP is not serialized
as arbitrary object representations. There is no heading/sentence inference.

PDF text order is the library's content-stream interpretation. Columns, ligatures,
font encodings, rotated/positioned text, tables and unusual PDFs can produce
imperfect text/order. Page provenance does not establish layout or OCR fidelity.
Images, annotations, attachments and rendered bounding boxes are not extracted.

### DOCX

`extract_docx(path, source_id=...)` reads main-body paragraphs and tables through
iter_inner_content in XML order. It retains paragraph text (including Unicode,
tabs and line breaks), adds LF between blocks/rows and TAB between grid cells,
and recursively handles tables inside cells. Blank paragraphs retain their index
and separators, with no invalid zero-length span. Nonempty paragraphs, including
whitespace paragraphs within a readable document, retain their original text.

Paragraph indices are zero-based within their body/cell container, counting empty
paragraphs. Table indices are zero-based within that container; row/cell indices
use the original table grid, including leading omitted cells in locator numbering.
Repeated grid references to the same merged cell emit text once at its first
anchor; later references are empty slots, not duplicated source assertions.
Omitted/merged layout is not claimed to be a lossless visual table representation.

Examples:

- Body paragraph: (paragraph,2).
- Table-cell paragraph: (table,0),(row,1),(cell,0),(paragraph,0).
- Nested table: append another table/row/cell/paragraph path inside its parent cell.

Source spans describe the exact emitted paragraph text, excluding inserted
separators. Existing StructuralSpan values mark paragraphs and containing tables,
with parent tables ordered before their child paragraphs. No DOCX page numbers
are fabricated, and style names are not guessed into semantic headings.

Core properties preserve title, subject, author, keywords, comments, category,
language, last editor and integer revision; present date values become ISO strings.
If the core-properties part is absent, metadata stays empty rather than accepting
python-docx's synthesized defaults. Headers, footers, footnotes/endnotes, text boxes,
comments, content controls/tracked-revision text omitted by the library, images and embedded objects
are outside this main-body extraction contract. No image OCR is attempted.

## Failure policy

No partially extracted document is returned after a parser failure. Malformed
PDFs/packages/XML raise DocumentExtractionError (a ValueError). Unsupported encrypted
PDFs raise UnsupportedDocumentError. No password handling or repair fallback is
added. Files with no non-whitespace extractable text raise InsufficientTextError,
including scanned/image-only PDFs without a text layer and empty DOCX bodies.
This outcome does not identify whether a file was scanned; it states that the
adapter cannot extract readable text. File access errors such as FileNotFoundError
remain file access errors. Source IDs are validated before opening the file.

Streams close on success/failure; returned dataclasses contain no live readers,
archives or XML nodes. Failed extractions are not registered or persisted.
Batch retries, sandbox/process isolation, upload limits, archive/resource limits,
MIME verification and authentication belong to the future untrusted upload boundary.
These synchronous parsers should not be run directly on an async HTTP event loop.

## Verification

Committed fixtures live in data/samples/formats. Unit tests cover exact PDF page
offsets/Unicode/empty pages, metadata and absent metadata, encrypted/empty/malformed
files, DOCX body/table/nested/merged order, native coordinates, malformed XML,
stream cleanup after a later page failure, retries, independent source Content
reuse and fixed-token chunk reconstruction. Real disposable PostgreSQL tests
register each format, reuse identical Content, reconstruct exact text/metadata/
structure/spans, persist/reload chunk plans, index with fake vectors, activate and
retrieve source-correct exact slices. No live Gemini or Cloud SQL is used.

Full verification: **385 unit + 149 PostgreSQL integration = 534 Python tests passed**, plus **24 JavaScript tests passed**. Twenty new unit cases and two PostgreSQL cases were added. One existing upstream Google SDK typing deprecation warning remains. PDF readable fixture pages were visually checked.

HTTP upload validation, authenticated ownership, original-byte storage/access,
format dispatch, lifecycle operations and document management remain unimplemented.
