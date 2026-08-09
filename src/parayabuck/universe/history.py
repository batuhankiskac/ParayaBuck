from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def append_snapshot_history(active_path: str | Path, payload: dict[str, Any]) -> Path:
    """Append an immutable audit record next to the latest active snapshot."""
    source = Path(active_path)
    history = source.with_suffix(source.suffix + ".history.jsonl")
    history.parent.mkdir(parents=True, exist_ok=True)
    with history.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
    return history


__all__ = ["append_snapshot_history"]
