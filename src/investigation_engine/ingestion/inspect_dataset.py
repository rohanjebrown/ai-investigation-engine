"""Inspect remote dataset metadata; running main() requires network access."""

from huggingface_hub import HfApi

DATASET_ID = "xanhho/2WikiMultihopQA"


def main() -> None:
    api = HfApi()
    dataset = api.dataset_info(DATASET_ID, files_metadata=True)

    print(f"Dataset: {dataset.id}")
    print(f"Revision: {dataset.sha}")

    for file in dataset.siblings or []:
        if file.rfilename.endswith(".parquet"):
            print(f"{file.rfilename}: {file.size} bytes")


if __name__ == "__main__":
    main()
