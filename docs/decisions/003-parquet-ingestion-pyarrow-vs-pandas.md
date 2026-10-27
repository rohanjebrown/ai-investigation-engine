# ADR-003 — PyArrow for Parquet Ingestion

**Project:** AI Investigation Engine  
**Status:** CONFIRMED for PyArrow in [A02](register.md#a02); canonical text construction remains UNRESOLVED in [U02](register.md#u02).  
**Decision date:** 2026-10-25  
**Scope:** Reading and validating 2WikiMultihopQA Parquet files; this is not a project-wide prohibition on pandas.

## Approval reconciliation (2026-10-26)

The user explicitly ratified PyArrow for Parquet processing: [A02](register.md#a02), approval [E01](register.md#e01). The historical status was "Accepted for initial dataset inspection and ingestion"; this confirms the reader choice, not every subsequent transformation.

The older references below to "normalize" and "normalized output" are preserved to show the original reasoning. They must not be interpreted as approval for whitespace, punctuation, or semantic deduplication. [A07](register.md#a07) confirms exact canonical-text reuse without normalized or semantic merging. Deterministic sentence assembly is still [U02](register.md#u02); corpus identity and selection remain [U01](register.md#u01)/[U03](register.md#u03). Batch reading does not establish a bounded total memory footprint when audit state is retained.

## Context

The AI Investigation Engine uses 2WikiMultihopQA as its initial dataset. At the pinned Hugging Face dataset revision (`612bc5039a457880d9e7d84c3b0a4cf154b70e4f`), the development Parquet file is **30,056,098 bytes** and the training Parquet file is **343,395,646 bytes**. These are compressed *file sizes*, not measurements of the memory required to decode their contents.

The Upgaming assignment requires ingestion of at least **50,000 independent documents or text records**, source preservation, duplicate handling, and reproducible evaluation. Each 2WikiMultihopQA row is a question-answer example containing context passages, so its row count must not be treated as the number of independent source documents. The first ingestion milestone must inspect schemas, count rows, iterate through records, extract passages, and deduplicate those passages without unnecessarily loading the full dataset into memory.

## Decision

Use **PyArrow** as the Parquet I/O layer for dataset inspection and streaming ingestion. Use `pyarrow.parquet.ParquetFile` for file metadata and schema inspection, and `iter_batches()` for bounded-batch reading. Do not introduce pandas solely to read this dataset. pandas remains a valid option later for analysis, reporting, or tabular transformations if those tasks benefit from DataFrames.

## Rationale

### 1. Direct access to metadata and schema

`ParquetFile.metadata` exposes the row count, column count, and row-group information; `ParquetFile.schema_arrow` exposes the schema. These can be inspected without first materializing all source rows as a pandas DataFrame. This directly supports the initial dataset audit.

### 2. Incremental processing at predictable batch sizes

`ParquetFile.iter_batches(batch_size=..., columns=...)` yields Arrow record batches and permits reading selected columns. Our ingestion pipeline can process a batch, extract its passages, and write normalized output before reading the next batch. This can keep peak memory lower than loading an entire file into one DataFrame, **provided the application does not retain all batches or all decoded records in memory**. Actual peak memory depends on record sizes, selected columns, transformations, and deduplication state, so memory savings are a design goal to measure rather than an established benchmark result.

### 3. Better fit for the task's data flow

The initial workflow is **read → validate → extract passages → normalize → deduplicate → write records**. It does not currently need DataFrame indexing, vectorized statistics, joins, or group-by analysis. Using the lower-level Parquet reader avoids an unnecessary full-DataFrame representation for this particular stage.

### 4. Clear dependency boundary

PyArrow directly implements the Parquet functionality we need. pandas often uses PyArrow as its Parquet engine, so choosing pandas would not necessarily eliminate the PyArrow dependency. Keeping only PyArrow in the ingestion path limits the initial dependency surface and makes the reader's behavior explicit.

### 5. Alignment with assignment constraints

The assignment requires processing a substantial corpus and demonstrating defensible technical decisions. Batch-oriented ingestion is suitable for testing the 50,000-record requirement and for scaling from the smaller development file to the larger training file without redesigning the reader. It also makes it straightforward to record schema, counts, and validation failures for a reproducible audit.

## Alternatives considered

| Option | Strengths | Reason not selected for the initial reader |
|---|---|---|
| **PyArrow (`ParquetFile`)** | Metadata and schema access; explicit column selection; batch iteration | **Selected:** directly matches the first ingestion milestone. |
| **pandas (`read_parquet`)** | Concise API; strong DataFrame transformations and analysis | Its standard API returns a DataFrame; those conveniences are not needed for our current record-by-record pipeline. pandas can still be used later. |
| **PyArrow + pandas for individual batches** | Combines incremental file reading with DataFrame operations | Viable if we later need DataFrame transformations; presently adds conversion without clear benefit. |

The choice is **not** based on a claim that PyArrow is universally faster than pandas. pandas supports column selection and can use PyArrow internally. Comparative performance has not yet been measured on our dataset.

## Risks and validation

- Batch size limits the number of rows returned per batch, **not** an absolute memory ceiling. Large nested text fields can still produce sizeable batches.
- Converting a batch to Python dictionaries (`to_pylist()`) allocates Python objects; we should benchmark this during the full-data audit.
- File metadata counts **question rows**, not independent source documents. Deduplication and source-record counting remain separate tasks.
- Record the dataset revision and verify file contents/checksums as part of the reproducibility manifest.
- Validate the full ingestion process on the training split, including peak memory, processing time, row counts, malformed records, and duplicate rates.
- Reconsider pandas for evaluation reports or data exploration when its DataFrame features improve clarity more than they add cost.

## Consequences

**Positive:** A small and explicit Parquet I/O layer; controlled batch processing; no requirement to load the whole file into a DataFrame; direct access to file metadata.

**Trade-off:** More explicit iteration and normalization code compared with pandas; some analytical operations may be less convenient without a DataFrame.

## Conclusion

**PyArrow was selected for the dataset-ingestion layer because this stage primarily requires schema inspection and incremental record processing, rather than DataFrame analysis.** This choice is consistent with the project's emphasis on efficient, testable, reproducible ingestion and will be revisited if measured results or later requirements justify a different approach.

## References

1. Apache Arrow documentation — `ParquetFile`, metadata, schema, and `iter_batches()`: https://arrow.apache.org/docs/python/generated/pyarrow.parquet.ParquetFile.html
2. Apache Arrow documentation — Reading and writing Parquet files: https://arrow.apache.org/docs/python/parquet/parquet.html
3. pandas documentation — `pandas.read_parquet()`: https://pandas.pydata.org/docs/reference/api/pandas.read_parquet.html
4. Upgaming — *Applied AI: AI Investigation Engine Task*, sections on Document Processing and Dataset (project assignment).
