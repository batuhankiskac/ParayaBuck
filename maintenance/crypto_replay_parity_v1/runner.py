"""Capture unchanged crypto engine behavior on a pinned offline environment.

Historical source checkouts are imported only when explicitly named. No broker
or runtime persistence path is opened. Alternate source runs diagnose history;
they are not alternative strategy or exit experiments.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return plain(asdict(value))
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [plain(v) for v in (sorted(value) if isinstance(value, (set, frozenset)) else value)]
    return value


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(plain(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def write(path: Path, value: Any) -> None:
    data = json.dumps(plain(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    path.write_bytes(gzip.compress(data, mtime=0) if path.suffix == ".gz" else data + b"\n")


def read(path: Path) -> Any:
    return json.loads(gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--universe-timeframe", choices=["1h", "1d"], default="1d")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root / "src"))
    from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
    from parayabuck.backtest.product import build_product_universe, load_csv_bars, run_product_backtest
    from parayabuck.backtest.universe import hash_point_in_time_universe
    from parayabuck.config import load_config
    from parayabuck.core.models import Market
    from parayabuck.data.aggregator import aggregate_historical_bars
    from parayabuck.portfolio.crypto_low_variance_hysteresis import CryptoLowVarianceHysteresis1dV1

    cfg_path = args.config or args.source_root / "config/settings.yaml"
    config = load_config(str(cfg_path))
    data_path = ROOT / "data/historical/crypto_expanded_1h.csv"
    start = datetime(2021, 4, 1, tzinfo=UTC)
    end = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)
    print("Loading data", flush=True)
    hourly = load_csv_bars(data_path, default_market=Market.CRYPTO)
    daily = aggregate_historical_bars(hourly, "1d", require_complete=True)
    print("Building universe", args.universe_timeframe, flush=True)
    universe = build_product_universe(
        config,
        hourly if args.universe_timeframe == "1h" else daily,
        market=Market.CRYPTO,
        end=end,
        timeframe=args.universe_timeframe,
    )
    decisions: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    engine_info: dict[str, Any] = {}
    orig_decide = CryptoLowVarianceHysteresis1dV1.decide
    orig_apply = PortfolioBacktestEngine._apply_target
    orig_mark = PortfolioBacktestEngine._record_holdings_snapshot

    def decide(self: Any, ctx: Any) -> Any:
        target = orig_decide(self, ctx)
        decisions.append(
            {
                "timestamp": ctx.decision_timestamp.isoformat(),
                "history": {s: plain(list(bars[-23:])) for s, bars in sorted(ctx.history.items())},
                "eligible_symbols": [s for s in sorted(ctx.history) if ctx.in_universe(s)],
                "holdings": plain(ctx.holdings),
                "prices": plain(ctx.prices),
                "equity": ctx.equity,
                "scores": plain(self._score_candidates(ctx)),
                "alpha_target": plain(target),
            }
        )
        return target

    def apply(self: Any, target: Any) -> None:
        orig_apply(self, target)
        decisions[-1]["target"] = plain(target)
        decisions[-1]["orders"] = plain(self._pending)
        decisions[-1]["account"] = plain(self._account_snapshot())

    def mark(self: Any, ts: Any) -> None:
        orig_mark(self, ts)
        snapshots.append(
            {
                "timestamp": ts.isoformat(),
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
        if not engine_info:
            for key in (
                "enforce_drawdown_halt",
                "max_participation_pct",
                "liquidity_lookback_bars",
                "turnover_band",
                "minimum_order_notional",
                "commission",
                "slippage_bps",
                "spread_bps",
                "crypto_taker_fee_bps",
                "maximum_allocation",
            ):
                engine_info[key] = getattr(self, key)
            engine_info["starting_cash"] = config.backtest.initial_cash
            engine_info["risk_limits"] = plain(self.risk_overlay.limits)
            engine_info["liquidate_at_end"] = True

    with (
        patch.object(CryptoLowVarianceHysteresis1dV1, "decide", decide),
        patch.object(PortfolioBacktestEngine, "_apply_target", apply),
        patch.object(PortfolioBacktestEngine, "_record_holdings_snapshot", mark),
    ):
        product = run_product_backtest(
            config,
            daily,
            market=Market.CRYPTO,
            strategy_name="crypto_low_variance_hysteresis_v1",
            parameters={"variance_lookback_days": 22, "top_n": 2, "rebalance_hours": 96},
            universe=universe,
            start=start,
            end=end,
            persist=False,
            persist_artifacts=False,
        )
    result = product.result
    metrics = {
        k: plain(getattr(result, k))
        for k in (
            "starting_equity",
            "ending_equity",
            "total_return",
            "cagr",
            "sharpe",
            "max_drawdown",
            "slippage_cost_total",
            "spread_cost_total",
            "fee_total",
            "rebalance_count",
        )
    }
    histories = {
        "ranking_history": [d["target"]["metadata"].get("selected_symbols", []) for d in decisions],
        "retention_history": [d["target"]["metadata"].get("retained", []) for d in decisions],
        "selected_symbol_history": [sorted(w["symbol"] for w in d["target"]["weights"]) for d in decisions],
        "target_weight_history": [{w["symbol"]: float(w["weight"]) for w in d["target"]["weights"]} for d in decisions],
        "fill_history": [
            {
                "symbol": f.symbol,
                "qty": float(f.qty),
                "price": float(f.fill_price),
                "timestamp": f.timestamp.isoformat(),
            }
            for f in result.fills
        ],
        "equity_curve": [float(v) for v in result.equity_curve],
    }
    payload = {
        "schema": "crypto-replay-audit-v2",
        "config": plain(config),
        "config_sha256": hashlib.sha256(cfg_path.read_bytes()).hexdigest(),
        "data": {
            "path": str(data_path.relative_to(ROOT)),
            "sha256": hashlib.sha256(data_path.read_bytes()).hexdigest(),
            "hourly_count": len(hourly),
            "daily_count": len(daily),
        },
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "universe_timeframe": args.universe_timeframe,
        "universe": plain(universe.to_dict()),
        "universe_hash": hash_point_in_time_universe(universe),
        "daily_bars": plain(daily),
        "decisions": decisions,
        "snapshots": snapshots,
        "fills": plain(result.fills),
        "terminal_events": plain(getattr(result, "terminal_events", ())),
        "curve_timestamps": plain(result.curve_timestamps),
        "equity_curve": plain(result.equity_curve),
        "final_state": plain(result.state),
        "metrics": metrics,
        "engine": engine_info,
        "historical_hashes": {k: digest(v) for k, v in histories.items()},
    }
    payload["hashes"] = {
        k: digest(payload[k])
        for k in ("daily_bars", "decisions", "fills", "snapshots", "terminal_events", "final_state")
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write(args.out, payload)
    print(
        json.dumps(
            {
                "out": str(args.out),
                "decisions": len(decisions),
                "fills": len(result.fills),
                "metrics": metrics,
                "hashes": payload["hashes"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
