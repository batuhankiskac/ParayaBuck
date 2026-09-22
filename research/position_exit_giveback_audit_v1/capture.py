"""Read-only observational hooks around the unchanged canonical engine."""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from unittest.mock import patch

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine


def plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return plain(asdict(value))
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def write_json(path: Path, value: Any) -> None:
    data = json.dumps(plain(value), sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
    if path.suffix == ".gz":
        path.write_bytes(gzip.compress(data, mtime=0))
    else:
        path.write_bytes(data + b"\n")


def read_json(path: Path) -> Any:
    return json.loads(gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes())


def capture_run(run: Callable[[], Any]) -> tuple[Any, dict[str, Any]]:
    events: list[dict[str, Any]] = []
    cls = PortfolioBacktestEngine
    original_fill = cls._fill_side
    original_action = cls._apply_corporate_action
    original_close = cls._close_position
    original_mark = cls._record_holdings_snapshot
    original_target = cls._apply_target

    def fill(self: Any, bar: Any, side: Any) -> None:
        before = len(self._fills)
        original_fill(self, bar, side)
        for row in self._fills[before:]:
            events.append({"type": "fill", **plain(row)})

    def action(self: Any, row: Any) -> None:
        pos = self._positions.get(row.symbol)
        qty = pos.qty if pos else 0.0
        original_action(self, row)
        if qty:
            events.append({"type": "action", **plain(row), "quantity_before": qty})

    def close(self: Any, pos: Any, bar: Any) -> None:
        original_close(self, pos, bar)
        # Keep reference: terminal policy is updated by caller after this returns.
        events.append({"type": "terminal", "event": self._liquidation_events[-1]})

    def mark(self: Any, timestamp: Any) -> None:
        original_mark(self, timestamp)
        events.append(
            {
                "type": "mark",
                "timestamp": timestamp.isoformat(),
                "nav": self._equity(),
                "cash": self._cash,
                "positions": {
                    s: {
                        "qty": p.qty,
                        "avg_price": p.avg_price,
                        "entry_fee": p.entry_fee,
                        "mark": self._last_price.get(s, p.avg_price),
                    }
                    for s, p in sorted(self._positions.items())
                },
            }
        )

    def target(self: Any, row: Any) -> None:
        events.append({"type": "target", **plain(row)})
        original_target(self, row)

    with (
        patch.object(cls, "_fill_side", fill),
        patch.object(cls, "_apply_corporate_action", action),
        patch.object(cls, "_close_position", close),
        patch.object(cls, "_record_holdings_snapshot", mark),
        patch.object(cls, "_apply_target", target),
    ):
        product = run()
    result = product.result
    raw = {
        "schema": "position-exit-raw-v1",
        "events": plain(events),
        "starting_equity": result.starting_equity,
        "ending_equity": result.ending_equity,
        "cash_yield": result.cash_yield,
        "dividend_total": result.dividend_total,
        "realized_pnl_events": plain(result.realized_pnl_events),
        "closed_position_events": plain(result.closed_position_events),
        "summary": plain(product.report.summary),
        "terminal_events": plain(result.terminal_events),
    }
    return product, raw
