"""Machine-readable PAPER cycle receipt per weekly equity decision."""

from __future__ import annotations

import json
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
        market = "equity" if "equity" in self.strategy_name else "crypto"
        filename = f"{date}_{market}_{self.strategy_name}.json"
        path = directory / filename
        # Atomic write via temp file
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True))
        tmp.replace(path)
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
        warnings=warnings or [],
        incidents=incidents or [],
    )


__all__ = ["PaperReceipt", "build_receipt"]
