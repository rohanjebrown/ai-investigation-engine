import json
from pathlib import Path

import pyarrow.parquet as pq

DATASET_PATH = Path("data/raw/2wiki/dev.parquet")


def main() -> None:
    with pq.ParquetFile(DATASET_PATH) as parquet_file:
        print("DATASET INFORMATION")
        print(f"Rows: {parquet_file.metadata.num_rows}")
        print(f"Columns: {parquet_file.metadata.num_columns}")
        print(f"Row groups: {parquet_file.metadata.num_row_groups}")

        print("\nSCHEMA")

        for field in parquet_file.schema_arrow:
            print(f"{field.name}: {field.type}")

        print("\nFIRST RECORD")

        batch = next(parquet_file.iter_batches(batch_size=1))
        record = batch.to_pylist()[0]

        for name, value in record.items():
            print(f"\n{name}:")
            print(f"Python type: {type(value).__name__}")
            print(f"Value: {repr(value)[:300]}")

        context = json.loads(record["context"])
        supporting_facts = json.loads(record["supporting_facts"])

        print("\nCONTEXT INFORMATION")
        print(f"Number of documents: {len(context)}")

        for title, sentences in context[:3]:
            print(f"\nDocument: {title}")
            print(f"Number of sentences: {len(sentences)}")
            print(f"First sentence: {sentences[0] if sentences else 'Empty'}")

        print("\nSUPPORTING FACTS")

        for title, sentence_index in supporting_facts:
            print(f"Document: {title}")
            print(f"Sentence index: {sentence_index}")


if __name__ == "__main__":
    main()
