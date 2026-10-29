"""Explicit container-build command for the existing pinned runtime tokenizer."""

import argparse
from hashlib import sha256
from pathlib import Path
from urllib.request import urlopen

from investigation_engine.infrastructure.tokenization.huggingface_tokenizer import (
    VOYAGE_REFERENCE_NAME,
    VOYAGE_REFERENCE_REVISION,
    VOYAGE_REFERENCE_SHA256,
)


def fetch(destination: Path) -> None:
    url = f"https://huggingface.co/{VOYAGE_REFERENCE_NAME}/resolve/{VOYAGE_REFERENCE_REVISION}/tokenizer.json"
    with urlopen(url, timeout=60) as response:
        artifact = response.read(16 * 1024 * 1024 + 1)
    if len(artifact) > 16 * 1024 * 1024 or sha256(artifact).hexdigest() != VOYAGE_REFERENCE_SHA256:
        raise ValueError("Runtime tokenizer does not match the pinned SHA-256/size")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(artifact)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    fetch(parser.parse_args().destination)


if __name__ == "__main__":
    main()
