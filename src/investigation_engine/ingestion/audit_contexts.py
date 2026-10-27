"""Historical sentence-list/normalized-text comparison, not the U03 count.

The approved corpus baseline hashes the adapter's exact extracted text.
This exploratory script preserves earlier measurements and does not ingest data.
"""

import hashlib
import json
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq


DATASET_PATH = Path("data/raw/2wiki/dev.parquet")

def sha256(text: str) -> str:
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def normalize_text(sentences: list[str]) -> str:
    return " ".join(" ".join(sentences).split())


def main() -> None:
    total_passages = 0
    total_questions = 0

    title_contents = defaultdict(set)
    unique_contents = set()

    title_normalized_contents = defaultdict(set)
    unique_normalized_contents = set()

    with pq.ParquetFile(DATASET_PATH) as parquet_file:
        for batch in parquet_file.iter_batches(
            batch_size=128,
            columns=["context"],
        ):
            for raw_context in batch.column(0).to_pylist():
                total_questions += 1

                context = json.loads(raw_context)

                for title, sentences in context:
                    total_passages += 1

                    content = json.dumps(
                        sentences,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )

                    content_hash = sha256(content)

                    title_contents[title].add(content_hash)
                    unique_contents.add(content_hash)

                    normalized_text = normalize_text(sentences)
                    normalized_hash = sha256(normalized_text)

                    title_normalized_contents[title].add(normalized_hash)
                    unique_normalized_contents.add(normalized_hash)

    unique_title_content_pairs = sum(
        len(contents)
        for contents in title_contents.values()
    )

    unique_normalized_title_content_pairs = sum(
        len(contents)
        for contents in title_normalized_contents.values()
    )

    titles_with_variants = [
        (title, len(contents))
        for title, contents in title_contents.items()
        if len(contents) > 1
    ]

    normalized_titles_with_variants = sum(
        len(contents) > 1
        for contents in title_normalized_contents.values()
    )

    print("DATASET AUDIT")
    print(f"Questions: {total_questions:,}")
    print(f"Total passage occurrences: {total_passages:,}")
    print(f"Unique titles: {len(title_contents):,}")

    print("\nEXACT CONTENT ANALYSIS")
    print(f"Unique contents: {len(unique_contents):,}")
    print(
        f"Unique title-content combinations: "
        f"{unique_title_content_pairs:,}"
    )
    print(
        f"Content-only duplicate occurrences: "
        f"{total_passages - len(unique_contents):,}"
    )
    print(
        f"Title-content duplicate occurrences: "
        f"{total_passages - unique_title_content_pairs:,}"
    )
    print(
        f"Titles with multiple contents: "
        f"{len(titles_with_variants):,}"
    )

    print("\nNORMALIZED CONTENT ANALYSIS")
    print(
        f"Unique normalized contents: "
        f"{len(unique_normalized_contents):,}"
    )
    print(
        f"Unique normalized title-content combinations: "
        f"{unique_normalized_title_content_pairs:,}"
    )
    print(
        f"Normalized content-only duplicate occurrences: "
        f"{total_passages - len(unique_normalized_contents):,}"
    )
    print(
        f"Titles with multiple normalized contents: "
        f"{normalized_titles_with_variants:,}"
    )

    print("\nNORMALIZATION IMPACT")
    print(
        f"Content variants eliminated: "
        f"{len(unique_contents) - len(unique_normalized_contents):,}"
    )
    print(
        f"Title-content variants eliminated: "
        f"{unique_title_content_pairs - unique_normalized_title_content_pairs:,}"
    )

    print("\nTOP 10 TITLES WITH CONTENT VARIANTS")

    for title, count in sorted(
        titles_with_variants,
        key=lambda item: (-item[1], item[0]),
    )[:10]:
        print(f"{title}: {count} variants")


if __name__ == "__main__":
    main()
