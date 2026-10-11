"""Immutable PAPER decision-receipt (V2) writer.

Ongoing PAPER decisions emit immutable V2 snapshots with explicit intent labels;
the portfolio SQLite ledger owns later execution truth. Historical V1 files
(without a ``decision_id``) are still matched by the forensics reader's
legacy substring probe; nothing writes V1 anymore.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def default_receipt_dir() -> Path:
    """Default PAPER receipt directory (data/runtime/paper_receipts)."""
    return Path("data/runtime/paper_receipts")


def write_decision_receipt_v2(payload: dict[str, Any], directory: str | Path) -> Path:
    """Publish an immutable decision snapshot; SQLite owns later order/fill truth."""
    if payload.get("schema_version") != 2 or not payload.get("decision_id"):
        raise ValueError("Decision receipt v2 requires schema_version=2 and decision_id")
    market = str(payload["market"])
    origin = str(payload["origin"])
    if market != "equity" or origin not in {"scheduled", "startup_catch_up"}:
        raise ValueError("Invalid decision receipt market/origin")
    stamp = str(payload["decision_timestamp"]).replace(":", "").replace("-", "")
    decision_id = str(payload["decision_id"])
    if not decision_id.isalnum():
        raise ValueError("Invalid decision receipt ID")
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{stamp}_{market}_{origin}_{decision_id}.v2.json"
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    descriptor, temp_name = tempfile.mkstemp(prefix=".receipt-v2-", suffix=".tmp", dir=root)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp_name, path)
        except FileExistsError:
            existing = json.loads(path.read_text(encoding="utf-8"))
            identity = ("schema_version", "receipt_id", "decision_id", "market", "origin", "configuration_digest")
            if any(existing.get(key) != payload.get(key) for key in identity):
                raise ValueError(f"Decision receipt conflict for {decision_id}") from None
    finally:
        Path(temp_name).unlink(missing_ok=True)
    return path


def prune_receipts(
    directory: str | Path,
    *,
    retention_days: float,
    now: object = None,
) -> list[Path]:
    """Delete V2 decision receipts older than ``retention_days`` (oldest first).

    Retention never deletes a receipt younger than the PAPER soak/audit
    horizon: ``scripts/audit_paper_soak.py`` replays any window from these
    files, so the default floor keeps two full years plus margin. Pass an
    explicit smaller window only when the operator accepts losing audit
    coverage for it. Returns the removed paths.
    """
    from datetime import UTC, datetime

    if not isinstance(retention_days, (int, float)) or not retention_days > 0:
        raise ValueError("retention_days must be a positive number")
    if retention_days < _MIN_RECEIPT_RETENTION_DAYS:
        raise ValueError(
            f"retention_days must keep at least the soak/audit horizon ({_MIN_RECEIPT_RETENTION_DAYS:g} days)"
        )
    reference = now.timestamp() if isinstance(now, datetime) else datetime.now(UTC).timestamp()
    cutoff = reference - float(retention_days) * 86_400
    root = Path(directory)
    if not root.is_dir():
        return []
    candidates = sorted(
        (path for path in root.glob("*.v2.json") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
    )
    removed: list[Path] = []
    for path in candidates:
        try:
            old = path.stat().st_mtime < cutoff
        except OSError:
            continue
        if old:
            path.unlink()
            removed.append(path)
    return removed


# Floor for prune_receipts: two trading years plus margin, so the soak auditor
# can always replay its window from the receipt directory.
_MIN_RECEIPT_RETENTION_DAYS = 800.0


__all__ = ["default_receipt_dir", "prune_receipts", "write_decision_receipt_v2"]
