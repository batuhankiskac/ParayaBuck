#!/usr/bin/env python3
"""Read-only per-session comparison of live VT35 decisions/fills with the backtest model.

For every persisted equity ``equity_leveraged_trend_v1`` decision the script
recomputes what the Stage B replay would have decided from official daily
bars (SIP, split-adjusted, labeled 00:00 ET) for the same New York session,
using the production strategy class, and the fill it would have modeled
(next session open). It then reports the live inputs, target weight and
fills next to it. See docs/research/execution_model_audit_v1.md.

    python scripts/backtest_live_comparison.py --db data/runtime/parayabuck.sqlite3 \
        --bars path/to/daily_sip.csv [--config config/settings.yaml] [--json-output out.json]

The database is opened ``mode=ro``; nothing is downloaded or written except
the optional JSON output.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from parayabuck.core.models import AccountSnapshot, Bar, Market  # noqa: E402
from parayabuck.execution.portfolio import (  # noqa: E402
    PortfolioRiskLimits,
    PortfolioRiskOverlay,
    portfolio_risk_limits_from_config,
)
from parayabuck.markets.nyse_calendar import nyse_sessions  # noqa: E402
from parayabuck.markets.timeframes import NEW_YORK  # noqa: E402
from parayabuck.portfolio.base import PortfolioContext  # noqa: E402
from parayabuck.portfolio.equity_leveraged_trend import EquityLeveragedTrendV1  # noqa: E402

STRATEGY = EquityLeveragedTrendV1.name

# Reference sizing is computed for a cold, empty account: the overlay then
# reduces to min(maximum_allocation, maximum_equity, maximum_gross,
# 1 - minimum_cash), the same cap production applies on a flat account.
COLD_ACCOUNT = AccountSnapshot(cash=1.0, equity=1.0, peak_equity=1.0, daily_pnl=0.0)


def reference_target_weight(
    overlay: PortfolioRiskOverlay,
    target: Any,
    hold_symbol: str,
    allocation: float,
) -> float:
    """Strategy weight after the production risk overlay: a cap, never a multiplier."""
    decision = overlay.apply(
        target,
        COLD_ACCOUNT,
        market=Market.EQUITY,
        approved_symbols=(hold_symbol,),
        maximum_allocation=allocation,
        mode="paper",
        enforce_drawdown_halt=False,
    )
    return sum(weight.weight for weight in decision.target.weights if weight.symbol == hold_symbol)


def session_of(timestamp: datetime) -> date:
    return timestamp.astimezone(NEW_YORK).date()


def session_bars(bars: dict[str, list[Bar]]) -> dict[str, list[Bar]]:
    """Only the bars dated on an NYSE session.

    A provider or CSV bar on a holiday is not a session: kept, it would be
    taken as a reference decision close, a fill session, or a missed session.
    """
    days = {session_of(bar.timestamp) for series in bars.values() for bar in series}
    if not days:
        return bars
    sessions = {day for day, _, _ in nyse_sessions(min(days), max(days))}
    return {symbol: [bar for bar in series if session_of(bar.timestamp) in sessions] for symbol, series in bars.items()}


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_live(db: str | Path) -> list[dict[str, Any]]:
    """Equity VT35 decisions with their orders and fills, oldest first."""
    con = sqlite3.connect(f"file:{Path(db).resolve()}?mode=ro", uri=True)
    try:
        decisions = con.execute(
            "SELECT decision_id, decision_timestamp, target_json FROM portfolio_decisions "
            "WHERE market = 'equity' AND strategy_name = ? ORDER BY decision_timestamp",
            (STRATEGY,),
        ).fetchall()
        rows = []
        for decision_id, timestamp, target_json in decisions:
            fills = con.execute(
                "SELECT o.symbol, o.side, o.quantity, o.reference_price, f.occurred_at, f.quantity, f.price "
                "FROM portfolio_orders o JOIN portfolio_fills f ON f.client_order_id = o.client_order_id "
                "WHERE o.decision_id = ? ORDER BY f.occurred_at",
                (decision_id,),
            ).fetchall()
            rows.append(
                {
                    "decision_id": decision_id,
                    "decision_timestamp": _parse(timestamp),
                    "target": json.loads(target_json),
                    "fills": [
                        {"symbol": f[0], "side": f[1], "occurred_at": _parse(f[4]), "quantity": f[5], "price": f[6]}
                        for f in fills
                    ],
                }
            )
        return rows
    finally:
        con.close()


def reference_decision(
    strategy: EquityLeveragedTrendV1,
    bars: dict[str, list[Bar]],
    session: date,
    overlay: PortfolioRiskOverlay,
    allocation: float,
) -> dict[str, Any]:
    """What the backtest decides on ``session``'s completed official close.

    ``bars`` must already be restricted to NYSE sessions (:func:`session_bars`;
    :func:`compare` does it), so a holiday bar is never the trailing session.
    """
    history = {
        symbol: tuple(bar for bar in series if session_of(bar.timestamp) <= session) for symbol, series in bars.items()
    }
    if not all(series and session_of(series[-1].timestamp) == session for series in history.values()):
        return {"status": "missing_reference_bar"}
    context = PortfolioContext(
        decision_timestamp=history[strategy.signal_symbol][-1].timestamp,
        market=Market.EQUITY,
        timeframe="1d",
        history=history,
        universe=None,
        universe_version="reference",
        equity=1.0,
        holdings={},
        prices={symbol: series[-1].close for symbol, series in history.items()},
        benchmark_symbol="SPY",
    )
    target = strategy.decide(context)
    later = [bar for bar in bars[strategy.hold_symbol] if session_of(bar.timestamp) > session]
    return {
        "status": "ok",
        "trend_state": target.metadata["trend_state"],
        "signal_close": target.metadata["signal_close"],
        "signal_sma": target.metadata["signal_sma"],
        "realized_volatility": target.metadata["realized_volatility"],
        "strategy_weight": sum(weight.weight for weight in target.weights),
        "target_weight": reference_target_weight(overlay, target, strategy.hold_symbol, allocation),
        "fill_session": session_of(later[0].timestamp).isoformat() if later else None,
        "fill_open": later[0].open if later else None,
    }


def compare(
    live: list[dict[str, Any]],
    bars: dict[str, list[Bar]],
    strategy: EquityLeveragedTrendV1,
    risk_limits: PortfolioRiskLimits,
    allocation: float,
    band: float,
) -> dict[str, Any]:
    overlay = PortfolioRiskOverlay(risk_limits)
    bars = session_bars(bars)
    rows = []
    for decision in live:
        session = session_of(decision["decision_timestamp"])
        ref = reference_decision(strategy, bars, session, overlay, allocation)
        meta = decision["target"].get("metadata", {})
        live_weight = sum(
            float(w["weight"]) for w in decision["target"].get("weights", ()) if w["symbol"] == strategy.hold_symbol
        )
        row: dict[str, Any] = {
            "session": session.isoformat(),
            "decision_id": decision["decision_id"],
            "decision_origin": meta.get("decision_origin", "scheduled"),
            "entry_risk_latch": meta.get("entry_risk_latch"),
            "live": {
                "trend_state": meta.get("trend_state"),
                "signal_close": meta.get("signal_close"),
                "signal_sma": meta.get("signal_sma"),
                "realized_volatility": meta.get("realized_volatility"),
                "target_weight": live_weight,
            },
            "reference": ref,
            "flags": [],
        }
        flags = row["flags"]
        if row["entry_risk_latch"]:
            flags.append("entry_latched")
        if ref["status"] != "ok":
            flags.append(ref["status"])
        else:
            expected = ref["target_weight"]
            row["weight_gap"] = live_weight - expected
            if meta.get("trend_state") != ref["trend_state"]:
                flags.append("trend_state_mismatch")
            if abs(live_weight - expected) >= band:
                flags.append("target_weight_gap")
            if isinstance(meta.get("signal_close"), (int, float)) and ref["signal_close"]:
                row["signal_close_gap_bps"] = (meta["signal_close"] / ref["signal_close"] - 1) * 1e4
        fills = [f for f in decision["fills"] if f["symbol"] == strategy.hold_symbol]
        if fills:
            quantity = sum(f["quantity"] for f in fills)
            vwap = sum(f["quantity"] * f["price"] for f in fills) / quantity
            first = session_of(fills[0]["occurred_at"])
            row["fill"] = {"side": fills[0]["side"], "quantity": quantity, "vwap": vwap, "session": first.isoformat()}
            if ref.get("fill_open"):
                sign = 1 if fills[0]["side"] == "buy" else -1
                row["fill"]["cost_vs_open_bps"] = sign * (vwap / ref["fill_open"] - 1) * 1e4
                if first.isoformat() != ref["fill_session"]:
                    flags.append("fill_not_next_session")
        rows.append(row)
    decided = {row["session"] for row in rows}
    series = bars[strategy.signal_symbol]
    missed = (
        sorted(
            s.isoformat()
            for s in {session_of(bar.timestamp) for bar in series}
            if rows and rows[0]["session"] <= s.isoformat() <= rows[-1]["session"] and s.isoformat() not in decided
        )
        if rows
        else []
    )
    counts: dict[str, int] = {}
    for row in rows:
        for flag in row["flags"]:
            counts[flag] = counts.get(flag, 0) + 1
    return {"sessions": rows, "sessions_without_live_decision": missed, "flag_counts": counts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--bars", required=True, help="official daily bars CSV (load_csv_bars format)")
    parser.add_argument("--config", default=str(ROOT / "config" / "settings.yaml"))
    parser.add_argument("--json-output", default=None)
    args = parser.parse_args()
    from parayabuck.backtest.product import load_csv_bars
    from parayabuck.config import load_config

    config = load_config(args.config)
    selection = config.strategy.equity
    if selection.name != STRATEGY:
        raise SystemExit(f"configured equity strategy is {selection.name}, not {STRATEGY}")
    strategy = EquityLeveragedTrendV1(dict(selection.parameters))
    wanted = {strategy.signal_symbol, strategy.hold_symbol}
    bars: dict[str, list[Bar]] = {symbol: [] for symbol in wanted}
    for bar in sorted(load_csv_bars(args.bars, default_market=Market.EQUITY), key=lambda item: item.timestamp):
        if bar.symbol in wanted and bar.timeframe == "1d":
            bars[bar.symbol].append(bar)
    report = compare(
        load_live(args.db),
        bars,
        strategy,
        portfolio_risk_limits_from_config(config.risk),
        selection.maximum_allocation,
        config.execution.rebalance_band_pct,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True, default=str) + "\n"
    if args.json_output:
        Path(args.json_output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
