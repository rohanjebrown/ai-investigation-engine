from pathlib import Path

from huggingface_hub import hf_hub_download


DATASET_ID = "xanhho/2WikiMultihopQA"
DATASET_REVISION = "612bc5039a457880d9e7d84c3b0a4cf154b70e4f"
DATA_DIRECTORY = Path("data/raw/2wiki")


def main() -> None:
    file_path = hf_hub_download(
        repo_id=DATASET_ID,
        repo_type="dataset",
        filename="dev.parquet",
        revision=DATASET_REVISION,
        local_dir=DATA_DIRECTORY,
    )

    print(f"Dataset downloaded: {file_path}")


if __name__ == "__main__":
    main()