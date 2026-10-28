# Source-only extraction fixtures

- multipage.pdf: three pages, readable Unicode on pages 1/3 and blank page 2;
  title/author/subject metadata, embedded Vera font. Locally generated using
  bundled ReportLab; readable pages rendered/visually checked using Poppler.
- paragraphs-and-table.docx: Unicode body text, an empty paragraph, two-by-two
  table and a body paragraph after the table; title/author/core properties.
  Locally generated with python-docx 1.2.0.

All text is controlled regression content, not dataset/gold annotations or user
documents. Binary files are committed so ordinary tests need no authoring/render
dependency. Temporary malformed, encrypted, blank, missing-metadata and nested/
merged variants are constructed by tests. See docs/design/file-extraction.md
for canonical separators, exact offsets, locators, metadata and limitations.
