"""Crash-resistant, per-mode evaluation checkpoints."""

import json
import os
import tempfile
from pathlib import Path


def save_checkpoint(path: Path, *, question_id: str, mode: str, row: dict, configuration: dict) -> None:
    """Atomically persist one completed mode; ASCII escaping keeps all Unicode safe."""
    if mode not in ("single_step", "multi_step") or row.get("id") != question_id:
        raise ValueError("Checkpoint identity does not match its result row")
    payload = {"question_id": question_id, "mode": mode, "configuration": configuration, "row": row}
    encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="ascii", newline="\n", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as stream:
            temp_name = stream.name
            stream.write(encoded)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name is not None and os.path.exists(temp_name):
            os.unlink(temp_name)


def load_checkpoint(path: Path, *, question_id: str, mode: str, configuration: dict) -> dict | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="ascii"))
    row = payload.get("row")
    if (
        payload.get("question_id") != question_id
        or payload.get("mode") != mode
        or payload.get("configuration") != configuration
        or not isinstance(row, dict)
        or row.get("id") != question_id
    ):
        raise ValueError(f"Checkpoint identity mismatch: {path}")
    return row
