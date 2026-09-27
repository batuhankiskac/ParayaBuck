"""Historical V1 equity receipt reader and immutable two-market V2 writer.

V1 has projected post-order fields and remains available for historical files.
Ongoing PAPER decisions emit immutable V2 snapshots with explicit intent labels;
the portfolio SQLite ledger owns later execution truth.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PaperReceipt:
    decision_timestamp: str
    config_digest: str
    strategy_version: str
    strategy_name: str
    universe_hash: str
    universe_size: int
    observation_set_symbols: tuple[str, ...]
    selected_symbols: tuple[str, ...]
    target_weights: dict[str, float]
    risk_adjusted_weights: dict[str, float]
    broker_positions_before: dict[str, float]
    intended_orders: list[dict[str, Any]]
    submitted_order_ids: list[str]
    fills: list[dict[str, Any]]
    positions_after: dict[str, float]
    actual_gross_exposure: float
    cash_ratio: float
    # T1 hysteresis promotion: crypto receipts include per-symbol rank/retained
    # and observation strategy provenance. Kept optional with defaults so
    # existing equity receipts and old tests remain valid.
    ranks: dict[str, int] = field(default_factory=dict)
    retained: dict[str, bool] = field(default_factory=dict)
    previous_holdings: dict[str, float] = field(default_factory=dict)
    universe_version: str = ""
    observation_strategy_digest: str = ""
    warnings: list[str] = field(default_factory=list)
    incidents: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write(self, directory: str | Path) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        # Filename: 2026-08-26_equity_v3.json  (date + market + strategy)
        date = self.decision_timestamp[:10]
        # Market is a strategy-name convention: PaperReceipt carries no market field
        market = "equity" if "equity" in self.strategy_name else "crypto"
        filename = f"{date}_{market}_{self.strategy_name}.json"
        path = directory / filename
        # Atomic write via temp file in same dir — avoid with_suffix collision
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True))
        try:
            tmp.replace(path)
        except Exception:
            # Cross-mount replace can fail; ensure cleanup
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise
        try:
            from parayabuck.monitoring import alerts as _alerts
            from parayabuck.monitoring import metrics as _metrics

            _metrics.record_event("paper_receipt_written")
            _metrics.record_paper_cycle(
                {
                    "decision_timestamp": self.decision_timestamp,
                    "strategy_name": self.strategy_name,
                    "strategy_version": self.strategy_version,
                    "universe_hash": self.universe_hash,
                    "universe_size": self.universe_size,
                    "selected_symbols": list(self.selected_symbols),
                    "target_weights": dict(self.target_weights),
                    "risk_adjusted_weights": dict(self.risk_adjusted_weights),
                    "intended_order_count": len(self.intended_orders),
                    "submitted_order_ids": list(self.submitted_order_ids),
                    "fill_count": len(self.fills),
                    "positions_after": dict(self.positions_after),
                    "actual_gross_exposure": self.actual_gross_exposure,
                    "cash_ratio": self.cash_ratio,
                    "receipt_path": str(path),
                }
            )
            _alerts.emit(
                "paper_receipt",
                message=f"PAPER receipt written for {self.strategy_name} @ {self.decision_timestamp}",
                receipt_path=str(path),
                universe_hash=self.universe_hash,
                strategy=self.strategy_name,
            )
        except Exception:
            pass
        return path


def build_receipt(
    *,
    decision_timestamp: datetime,
    config_digest: str,
    strategy_name: str,
    strategy_version: str,
    universe_hash: str,
    universe_size: int,
    observation_set_symbols: tuple[str, ...],
    selected_symbols: tuple[str, ...],
    target_weights: dict[str, float],
    risk_adjusted_weights: dict[str, float],
    broker_positions_before: dict[str, float],
    intended_orders: list[dict[str, Any]],
    submitted_order_ids: list[str],
    fills: list[dict[str, Any]],
    positions_after: dict[str, float],
    actual_gross_exposure: float,
    cash_ratio: float,
    ranks: dict[str, int] | None = None,
    retained: dict[str, bool] | None = None,
    previous_holdings: dict[str, float] | None = None,
    universe_version: str = "",
    observation_strategy_digest: str = "",
    warnings: list[str] | None = None,
    incidents: list[dict[str, Any]] | None = None,
) -> PaperReceipt:
    return PaperReceipt(
        decision_timestamp=decision_timestamp.astimezone(UTC).isoformat(),
        config_digest=config_digest,
        strategy_version=strategy_version,
        strategy_name=strategy_name,
        universe_hash=universe_hash,
        universe_size=universe_size,
        observation_set_symbols=observation_set_symbols,
        selected_symbols=selected_symbols,
        target_weights=target_weights,
        risk_adjusted_weights=risk_adjusted_weights,
        broker_positions_before=broker_positions_before,
        intended_orders=intended_orders,
        submitted_order_ids=submitted_order_ids,
        fills=fills,
        positions_after=positions_after,
        actual_gross_exposure=actual_gross_exposure,
        cash_ratio=cash_ratio,
        ranks=ranks or {},
        retained=retained or {},
        previous_holdings=previous_holdings or dict(broker_positions_before),
        universe_version=universe_version or universe_hash,
        observation_strategy_digest=observation_strategy_digest,
        warnings=warnings or [],
        incidents=incidents or [],
    )


def default_receipt_dir() -> Path:
    """Default PAPER receipt directory (data/runtime/paper_receipts)."""
    return Path("data/runtime/paper_receipts")


def write_decision_receipt_v2(payload: dict[str, Any], directory: str | Path) -> Path:
    """Publish an immutable decision snapshot; SQLite owns later order/fill truth."""
    if payload.get("schema_version") != 2 or not payload.get("decision_id"):
        raise ValueError("Decision receipt v2 requires schema_version=2 and decision_id")
    market = str(payload["market"])
    origin = str(payload["origin"])
    if market not in {"equity", "crypto"} or origin not in {"scheduled", "startup_catch_up"}:
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


__all__ = ["PaperReceipt", "build_receipt", "default_receipt_dir", "write_decision_receipt_v2"]
