#!/usr/bin/env python3
"""Replay the frozen ETF sleeve candidates through the production backtest engine.

Each candidate runs in its own standalone account at the pre-registered sleeve
allocation (0.15) with the shipped risk limits, execution policy and cost
model, at 1x/2x/3x costs. Writes daily account curves plus validity and
per-ETF contribution evidence.

    .venv/bin/python research/etf_diversifier_v1/run_etf_sleeves.py \
        --out research/etf_diversifier_v1/ETF_CURVES.json.gz
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from etf_strategies import EtfStaticDefensiveV1, EtfTrend12mV1  # noqa: E402

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine  # noqa: E402
from parayabuck.backtest.product import load_csv_bars, load_csv_corporate_actions  # noqa: E402
from parayabuck.backtest.universe import (  # noqa: E402
    PointInTimeUniverse,
    UniverseSnapshot,
    hash_point_in_time_universe,
)
from parayabuck.config import load_config  # noqa: E402
from parayabuck.core.models import Market  # noqa: E402
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config  # noqa: E402
from parayabuck.portfolio.models import RebalancePolicy  # noqa: E402

BARS_PATH = ROOT / "data/historical/etf_diversifier_v1_daily_split_sip.csv"
ACTIONS_PATH = ROOT / "data/historical/etf_diversifier_v1_corporate_actions.csv"
CONFIG_PATH = ROOT / "config/settings.yaml"
START = datetime(2017, 1, 3, tzinfo=UTC)
END = datetime(2026, 7, 30, 23, 59, 59, 999999, tzinfo=UTC)
SLEEVE_ALLOCATION = 0.15
COST_MULTIPLIERS = (1.0, 2.0, 3.0)
CANDIDATES = (EtfStaticDefensiveV1, EtfTrend12mV1)
SYMBOLS = ("SPY", "TLT", "IEF", "GLD", "DBC", "BIL")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contributions(result, bars_by_symbol, dividends) -> dict[str, float]:
    """Per-ETF P&L from held weights times next-bar price move plus dividends."""
    closes = {symbol: {bar.timestamp: bar.close for bar in bars} for symbol, bars in bars_by_symbol.items()}
    # weight_history is recorded on change only; carry the last snapshot forward.
    weights_at = dict(result.weight_history)
    pnl: dict[str, float] = defaultdict(float)
    timestamps, equity = result.curve_timestamps, result.equity_curve
    held: dict[str, float] = {}
    for index in range(1, len(timestamps)):
        previous, current = timestamps[index - 1], timestamps[index]
        held = weights_at.get(previous, held)
        for symbol, weight in held.items():
            price_prev, price_now = closes[symbol].get(previous), closes[symbol].get(current)
            if not weight or price_prev is None or price_now is None:
                continue
            shares = weight * equity[index - 1] / price_prev
            pnl[symbol] += shares * (price_now - price_prev)
            pnl[symbol] += shares * dividends.get((symbol, current.date()), 0.0)
    return dict(pnl)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    config = load_config(CONFIG_PATH)
    bars = [bar for bar in load_csv_bars(BARS_PATH, default_market=Market.EQUITY) if bar.timestamp <= END]
    actions = load_csv_corporate_actions(ACTIONS_PATH)
    warmup = [bar for bar in bars if bar.timestamp < START]
    window = [bar for bar in bars if bar.timestamp >= START]
    bars_by_symbol: dict[str, list] = defaultdict(list)
    for bar in bars:
        bars_by_symbol[bar.symbol].append(bar)
    dividends = {(action.symbol, action.timestamp.date()): action.value for action in actions}
    session_dates = sorted({bar.timestamp for bar in window})
    coverage = {
        symbol: len([bar for bar in bars_by_symbol[symbol] if bar.timestamp >= START]) / len(session_dates)
        for symbol in SYMBOLS
    }

    universe = PointInTimeUniverse(
        market=Market.EQUITY,
        snapshots=(
            UniverseSnapshot(
                effective_at=datetime(2016, 1, 1, tzinfo=UTC),
                symbols=frozenset(SYMBOLS),
                scanner_version="etf_diversifier_v1_static",
                observation_set=SYMBOLS,
                maximum_stream_symbols=len(SYMBOLS),
            ),
        ),
        methodology="etf_diversifier_v1 fixed ETF universe (pre-registered, static)",
    )
    risk = portfolio_risk_limits_from_config(config.risk)
    runs: dict[str, dict] = {}
    for candidate in CANDIDATES:
        for multiplier in COST_MULTIPLIERS:
            engine = PortfolioBacktestEngine(
                candidate(),
                RebalancePolicy(Market.EQUITY, "monthly"),
                starting_equity=config.backtest.initial_cash,
                slippage_bps=config.backtest.slippage_bps * multiplier,
                spread_bps=config.backtest.spread_bps * multiplier,
                commission=config.backtest.commission,
                equity_taf_per_share=config.backtest.equity_taf_per_share,
                equity_taf_cap=config.backtest.equity_taf_cap,
                equity_cat_per_share=config.backtest.equity_cat_per_share,
                equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
                equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
                equity_regulatory_fee_multiplier=multiplier,
                benchmark_symbol="SPY",
                universe=universe,
                universe_version=hash_point_in_time_universe(universe),
                risk_limits=risk,
                maximum_allocation=SLEEVE_ALLOCATION,
                execution_policy=PortfolioExecutionPolicy(
                    rebalance_band_pct=config.execution.rebalance_band_pct,
                    minimum_order_notional=config.execution.minimum_order_notional,
                ),
            )
            result = engine.run(window, warmup=warmup, corporate_actions=actions)
            key = f"{candidate.name}|{multiplier}"
            runs[key] = {
                "candidate": candidate.name,
                "cost_multiplier": multiplier,
                "curve_timestamps": [ts.isoformat() for ts in result.curve_timestamps],
                "equity_curve": [float(value) for value in result.equity_curve],
                "fill_count": len(result.fills),
                "decision_count": len(result.rebalances),
                "fills_after_decisions": all(fill.timestamp > fill.decision_timestamp for fill in result.fills),
                "fee_total": float(result.fee_total),
                "slippage_cost_total": float(result.slippage_cost_total),
                "spread_cost_total": float(result.spread_cost_total),
                "dividend_total": float(getattr(engine, "_dividend_total", 0.0)),
                "max_gross_weight": max((sum(weights.values()) for _ts, weights in result.weight_history), default=0.0),
                "contributions": _contributions(result, bars_by_symbol, dividends),
                "risk_halts": sum(1 for record in result.rebalances if record.risk_overlay_halted),
            }
            print(
                f"{key}: ending={result.ending_equity:.2f} fills={len(result.fills)} "
                f"decisions={len(result.rebalances)}",
                flush=True,
            )

    payload = {
        "sleeve_allocation": SLEEVE_ALLOCATION,
        "coverage": coverage,
        "session_count": len(session_dates),
        "input_identity": {
            "bars_sha256": _sha256(BARS_PATH),
            "corporate_actions_sha256": _sha256(ACTIONS_PATH),
            "config_sha256": _sha256(CONFIG_PATH),
            "universe_sha256": hash_point_in_time_universe(universe),
        },
        "runs": runs,
    }
    with gzip.open(args.out, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
