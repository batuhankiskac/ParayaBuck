#!/usr/bin/env python3
# ruff: noqa: E501  # research script: long report/print lines
"""Crypto cadence bake-off research: 168h (control) vs 96h vs 72h.

Frozen alpha `crypto_low_variance_v1` (variance_lookback_days=22, top_n=2 —
NOT touched); the ONLY change is the rebalance cadence override
(`rebalance_hours`). Production harness only (run_product_backtest, same PIT
universe built ONCE from the 1h scanner bars, same risk overlay / execution
band / cost model). Research-only script: never edits src/ or tests/.

Candidates (predeclared, do not add 24h/48h/120h):
  168h baseline = FROZEN_CRYPTO
  96h          = FROZEN_CRYPTO | {"rebalance_hours": 96}
  72h          = FROZEN_CRYPTO | {"rebalance_hours": 72}

Evaluation window: REVERSAL_EVAL_START 2021-04-01 .. CRYPTO_END
2026-07-31 23:59:59 UTC (warmup 2021-01-01..03-31). Walk-forward: anchored
calendar 6-month folds from 2022-01-01 (10 folds), 1x cost, same universe.

Outputs: data/research/cadence_research_2026_08/crypto/*.json + printed
markdown tables.

Usage:
  .venv/bin/python scripts/research_crypto_cadence.py
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from parayabuck.backtest.product import load_csv_bars, run_product_backtest  # noqa: E402
from parayabuck.backtest.universe import hash_point_in_time_universe  # noqa: E402
from parayabuck.config import load_config  # noqa: E402
from parayabuck.core.models import Bar, Market  # noqa: E402
from parayabuck.data.aggregator import aggregate_historical_bars  # noqa: E402
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for  # noqa: E402
from parayabuck.portfolio.base import PortfolioContext  # noqa: E402

# Reuse the validated-research helpers instead of reinventing them.
from scripts.research_helpers import (  # noqa: E402
    _calendar_fold_window,
    _cost_stress,
    _fold_stats,
    _profit_factor,
)

OUTPUT_DIR = REPO / "data" / "research" / "cadence_research_2026_08" / "crypto"
CRYPTO_CSV = REPO / "data" / "historical" / "crypto_18_1h_extended.csv"
DATASET_SHA256 = "aa88c99a561e29c84174cb2a3c7d81cb3571d17ac32d7af0e588aceec39f0ebb"

CRYPTO_END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)
REVERSAL_EVAL_START = datetime(2021, 4, 1, tzinfo=UTC)
FROZEN_CRYPTO = {"variance_lookback_days": 22, "top_n": 2, "rebalance_hours": 168}
CANDIDATES = (("168h", 168), ("96h", 96), ("72h", 72))
STRATEGY_NAME = "crypto_low_variance_v1"
SLEEVE = 0.10  # config strategy.crypto.maximum_allocation
NEAR_MAX_EXPOSURE = 0.9 * SLEEVE  # >= 0.09 of the 0.10 sleeve
REBUY_WINDOW_DAYS = 7  # sell -> rebuy same symbol round-trip window

# ---------------------------------------------------------------------------
# Markdown table helper
# ---------------------------------------------------------------------------


def _md_table(headers: list[str], rows: list[list[object]]) -> str:
    out = ["| " + " | ".join(str(h) for h in headers) + " |"]
    out.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Per-candidate metrics
# ---------------------------------------------------------------------------


def _per_year_breakdown(result) -> dict[str, dict[str, int]]:
    """decisions / orders / fills / trades per calendar year.

    decisions = rebalance records (period starts, band NOT applied).
    orders    = distinct client_order_ids across fills (proxy for orders
                created; a crypto order fills at the next bar, so un-filled
                created orders are negligible; an order is attributed to the
                year of its fill).
    fills     = decision-driven fills (liquidation at window end is cost-only,
                never a synthetic fill).
    trades    = closed position events (full position lifetimes).
    """
    years: dict[int, dict[str, int]] = {}
    for rebalance in result.rebalances:
        year = rebalance.decision_timestamp.year
        years.setdefault(year, {"decisions": 0, "orders": 0, "fills": 0, "trades": 0})["decisions"] += 1
    seen_orders: set[str] = set()
    for fill in result.fills:
        year = fill.timestamp.year
        years.setdefault(year, {"decisions": 0, "orders": 0, "fills": 0, "trades": 0})["fills"] += 1
        if fill.client_order_id not in seen_orders:
            seen_orders.add(fill.client_order_id)
            years[year]["orders"] += 1
    for timestamp, _symbol, _pnl in result.closed_position_events:
        year = timestamp.year
        years.setdefault(year, {"decisions": 0, "orders": 0, "fills": 0, "trades": 0})["trades"] += 1
    return {str(year): years[year] for year in sorted(years)}


def _daily_exposures(result, bars_1d: list[Bar]) -> list[tuple[str, float]]:
    """Per-day portfolio exposure reconstructed from fills + bar closes.

    Mirrors the engine's per-bar accounting: fills at day ``ts`` execute at
    that bar's open, positions are then marked at the day close, and the
    equity curve value for ``ts`` is the post-fill close-marked equity, so
    exposure(ts) = position_value(close) / equity_curve(ts). Identical
    timestamp set (the last duplicate liquidation timestamp is dropped).
    """
    close_at: dict[str, dict[datetime, float]] = {}
    for bar in bars_1d:
        close_at.setdefault(bar.symbol, {})[bar.timestamp] = bar.close
    fills_by_ts: dict[datetime, list] = {}
    for fill in result.fills:
        fills_by_ts.setdefault(fill.timestamp, []).append(fill)
    positions: dict[str, float] = {}
    exposures: list[tuple[str, float]] = []
    seen_ts: set[datetime] = set()
    for ts, equity in zip(result.curve_timestamps, result.equity_curve, strict=False):
        if ts in seen_ts:
            continue  # skip the end-of-window liquidation duplicate
        seen_ts.add(ts)
        for fill in fills_by_ts.get(ts, ()):
            if fill.side.value == "buy":
                positions[fill.symbol] = positions.get(fill.symbol, 0.0) + fill.qty
            else:
                positions[fill.symbol] = max(0.0, positions.get(fill.symbol, 0.0) - fill.qty)
                if positions[fill.symbol] <= 1e-12:
                    positions.pop(fill.symbol, None)
        value = sum(quantity * close_at.get(symbol, {}).get(ts, 0.0) for symbol, quantity in positions.items())
        exposure = value / equity if equity > 0 else 0.0
        exposures.append((ts.isoformat(), exposure))
    return exposures


def _capital_utilization(result, bars_1d: list[Bar], start: datetime) -> dict[str, object]:
    days = _daily_exposures(result, bars_1d)
    values = [exposure for _ts, exposure in days]
    deployed = next((ts for ts, exposure in days if exposure > 1e-9), None)
    return {
        "days": len(values),
        "avg_exposure": round(statistics.fmean(values), 4),
        "median_exposure": round(statistics.median(values), 4),
        "pct_days_positive_exposure": round(sum(1 for v in values if v > 1e-9) / len(values), 4),
        "pct_days_near_max": round(sum(1 for v in values if v >= NEAR_MAX_EXPOSURE) / len(values), 4),
        "pct_days_idle": round(sum(1 for v in values if v < 1e-9) / len(values), 4),
        "time_to_first_deployment_days": (
            round((datetime.fromisoformat(deployed) - start).total_seconds() / 86_400, 2) if deployed else None
        ),
        "near_max_threshold": NEAR_MAX_EXPOSURE,
        "note": (
            "daily exposure = sum(position qty x day close) / equity curve, reconstructed "
            "from fills + 1d closes (engine parity); near-max = exposure >= 0.09 of the "
            "0.10 sleeve; idle = fully in cash"
        ),
    }


def _position_intervals(result) -> list[tuple[str, datetime, datetime]]:
    """(symbol, open_ts, close_ts) per completed position from the fill stream."""
    fills = sorted(result.fills, key=lambda fill: (fill.timestamp, fill.symbol))
    quantities: dict[str, float] = {}
    opened_at: dict[str, datetime] = {}
    intervals: list[tuple[str, datetime, datetime]] = []
    for fill in fills:
        symbol = fill.symbol
        if fill.side.value == "buy":
            if quantities.get(symbol, 0.0) <= 1e-12:
                opened_at[symbol] = fill.timestamp
            quantities[symbol] = quantities.get(symbol, 0.0) + fill.qty
        else:
            before = quantities.get(symbol, 0.0)
            quantities[symbol] = max(0.0, before - fill.qty)
            if before > 1e-12 and quantities[symbol] <= 1e-12:
                intervals.append((symbol, opened_at.get(symbol, fill.timestamp), fill.timestamp))
    return intervals


def _churn_diagnostics(result) -> dict[str, object]:
    """Churn from fills: sell->rebuy round-trips, A->B->A flips, reversal costs.

    Two round-trip measures:
      * fill-level (spec): any SELL fill of symbol S followed by a BUY fill of
        S within 7 days — captures band/partial churn, but the engine's 10%
        participation cap drip-fills orders over ~7 days, so one position
        reversal can contribute several fill-level pairs;
      * position-level: a completed position of S closing and a NEW position
        of S opening within 7 days — the economically meaningful reversal.

    ab_a_flip: the held-name state sequence (compressed, single-symbol swap)
    S -> T -> S, i.e. A sold and B bought, then B sold and A re-bought, with
    no other name taking the slot in between. Reversal/flip costs include
    slippage+spread+fee of the involved fills.
    """
    fills = sorted(result.fills, key=lambda fill: (fill.timestamp, fill.symbol))
    sells = [fill for fill in fills if fill.side.value == "sell"]
    buys = [fill for fill in fills if fill.side.value == "buy"]
    round_trips_fill: list[tuple] = []
    for sell in sells:
        rebuy = next(
            (
                buy
                for buy in buys
                if buy.symbol == sell.symbol
                and sell.timestamp < buy.timestamp
                and buy.timestamp <= sell.timestamp + timedelta(days=REBUY_WINDOW_DAYS)
            ),
            None,
        )
        if rebuy is not None:
            round_trips_fill.append((sell, rebuy))

    intervals = _position_intervals(result)
    by_symbol: dict[str, list[tuple[str, datetime, datetime]]] = {}
    for interval in intervals:
        by_symbol.setdefault(interval[0], []).append(interval)
    for symbol in by_symbol:
        by_symbol[symbol].sort(key=lambda item: (item[1], item[2]))
    # Position-level round trips: interval closed, same symbol re-opened <= 7d later.
    round_trips_pos: list[tuple[tuple[str, datetime, datetime], tuple[str, datetime, datetime]]] = []
    for symbol, series in by_symbol.items():
        for index in range(len(series) - 1):
            current, following = series[index], series[index + 1]
            if following[1] <= current[2] + timedelta(days=REBUY_WINDOW_DAYS):
                round_trips_pos.append((current, following))

    # A->B->A flips from the DECISION target sequence (post-risk-overlay
    # pairs), compressed over consecutive identical pairs: {A,X} -> {B,X} ->
    # {A,X} with a single-symbol swap. Decision-level, so the engine's
    # participation-cap drip transients in the fill timeline cannot create
    # spurious three-name overlap states.
    pair_states: list[tuple[datetime, frozenset]] = []
    for rebalance in result.rebalances:
        symbols = frozenset(symbol for symbol, _weight in rebalance.target_weights)
        if not pair_states or pair_states[-1][1] != symbols:
            pair_states.append((rebalance.decision_timestamp, symbols))
    flips: list[tuple[str, str, datetime, datetime]] = []
    for idx in range(len(pair_states) - 2):
        t0, s1 = pair_states[idx]
        t1, s2 = pair_states[idx + 1]
        t2, s3 = pair_states[idx + 2]
        if s1 != s3 or len(s1) != 2 or len(s2) != 2:
            continue
        left = s1 - s2
        entered = s2 - s1
        if len(left) == 1 and len(entered) == 1:
            flips.append((next(iter(left)), next(iter(entered)), t1, t2))
    flip_pairs: dict[tuple[str, str], int] = {}
    for a_symbol, b_symbol, _start, _end in flips:
        flip_pairs[(a_symbol, b_symbol)] = flip_pairs.get((a_symbol, b_symbol), 0) + 1
    top_flip_pairs = [
        {"from": a_symbol, "to": b_symbol, "count": count}
        for (a_symbol, b_symbol), count in sorted(flip_pairs.items(), key=lambda item: -item[1])[:5]
    ]
    flip_cost_fills: set[int] = set()
    for a_symbol, b_symbol, win_start, win_end in flips:
        for fill_index, fill in enumerate(fills):
            if fill.symbol not in (a_symbol, b_symbol):
                continue
            if win_start <= fill.timestamp <= win_end + timedelta(days=REBUY_WINDOW_DAYS):
                flip_cost_fills.add(fill_index)
    # Reversal cost: fills of the symbol in the sell exit / rebuy entry windows
    # (covers the participation-cap drip execution of both legs).
    reversal_cost_fills: set[int] = set()
    for current, following in round_trips_pos:
        symbol = current[0]
        for fill_index, fill in enumerate(fills):
            if fill.symbol != symbol:
                continue
            if (
                current[2] - timedelta(days=REBUY_WINDOW_DAYS)
                <= fill.timestamp
                <= following[1] + timedelta(days=REBUY_WINDOW_DAYS)
            ):
                reversal_cost_fills.add(fill_index)

    def _fill_cost(fill) -> float:
        return fill.slippage_cost + fill.spread_cost + fill.fee

    reversal_cost = sum(_fill_cost(fills[index]) for index in reversal_cost_fills)
    flip_cost = sum(_fill_cost(fills[index]) for index in flip_cost_fills)
    total_cost = result.slippage_cost_total + result.spread_cost_total + result.fee_total
    return {
        "sell_fill_count": len(sells),
        "buy_fill_count": len(buys),
        "round_trip_count_fill_level": len(round_trips_fill),
        "round_trips_per_sell_fill": round(len(round_trips_fill) / len(sells), 4) if sells else 0.0,
        "round_trip_count_position_level": len(round_trips_pos),
        "round_trips_per_trade": (
            round(len(round_trips_pos) / len(result.closed_position_events), 4)
            if result.closed_position_events
            else 0.0
        ),
        "reversal_cost": round(reversal_cost, 2),
        "reversal_cost_pct_of_total_cost": round(reversal_cost / total_cost, 4) if total_cost > 0 else 0.0,
        "ab_a_flip_count": len(flips),
        "flip_cost": round(flip_cost, 2),
        "top_flip_pairs": top_flip_pairs,
        "methodology": (
            f"fill-level round trip = any SELL fill of S followed by a BUY fill of S within "
            f"{REBUY_WINDOW_DAYS} days (spec definition; contaminated by the 10% participation-"
            "cap drip fills); position-level round trip = completed position of S closed and a "
            "new position of S opened within 7 days (economic reversal); A->B->A flip = "
            "compressed decision-target pair sequence {A,X}->{B,X}->{A,X} with a single-symbol "
            "swap (band may suppress the order for a flip; decision-level avoids "
            "participation-cap drip transients); reversal cost = fills of S in the exit/rebuy "
            "windows; flip cost = unique A/B fills between the two swap decisions + 7 days; "
            "costs include slippage+spread+fee"
        ),
    }


def _baseline_avoidance(fills_fast, baseline_fills) -> dict[str, object]:
    """Fraction of faster-cadence trades the 168h baseline would not have made.

    Approximation (stated): a faster-cadence fill of (symbol, side) at time t
    is "not avoided" iff the 168h baseline has a fill of the SAME (symbol,
    side) whose timestamp lies in the same 168h epoch block as t (the
    baseline decides once per block and fills next bar, so the block key
    captures symbol+timing overlap). Everything else would not have happened
    at the 168h cadence. Also reported on position-OPEN events (trades).
    """
    block_seconds = 168 * 3600
    baseline_keys = {
        (int(fill.timestamp.timestamp()) // block_seconds, fill.symbol, fill.side.value) for fill in baseline_fills
    }
    totals = {"fills": len(fills_fast), "trades": 0}
    avoided = {"fills": 0, "trades": 0}
    quantities: dict[str, float] = {}
    for fill in sorted(fills_fast, key=lambda item: (item.timestamp, item.symbol)):
        key = (int(fill.timestamp.timestamp()) // block_seconds, fill.symbol, fill.side.value)
        if key not in baseline_keys:
            avoided["fills"] += 1
        was_open = quantities.get(fill.symbol, 0.0) > 1e-12
        if fill.side.value == "buy":
            if not was_open:
                totals["trades"] += 1
                if key not in baseline_keys:
                    avoided["trades"] += 1
            quantities[fill.symbol] = quantities.get(fill.symbol, 0.0) + fill.qty
        else:
            quantities[fill.symbol] = max(0.0, quantities.get(fill.symbol, 0.0) - fill.qty)
    return {
        "pct_fills_avoided_by_168h": round(avoided["fills"] / totals["fills"], 4) if totals["fills"] else 0.0,
        "pct_trades_avoided_by_168h": round(avoided["trades"] / totals["trades"], 4) if totals["trades"] else 0.0,
        "fills_total": totals["fills"],
        "fills_avoided": avoided["fills"],
        "trades_total": totals["trades"],
        "trades_avoided": avoided["trades"],
        "approximation": (
            "same (symbol, side) fill in the same 168h epoch block as a 168h-baseline fill "
            "= overlap (would also occur at 168h); all other faster-cadence fills/trades "
            "counted as avoided by the 168h baseline"
        ),
    }


def _band_analysis(result) -> dict[str, object]:
    order_ids = {fill.client_order_id for fill in result.fills}
    total_cost = result.slippage_cost_total + result.spread_cost_total + result.fee_total
    return {
        "decisions": result.rebalance_count,
        "orders": len(order_ids),
        "fills": len(result.fills),
        "trades": len(result.closed_position_events),
        "order_ratio": round(len(order_ids) / result.rebalance_count, 4) if result.rebalance_count else 0.0,
        "fill_ratio": round(len(result.fills) / result.rebalance_count, 4) if result.rebalance_count else 0.0,
        "avg_cost_per_fill": round(total_cost / len(result.fills), 2) if result.fills else 0.0,
        "note": (
            "decisions = period starts (band NOT applied); orders = distinct client_order_ids "
            "in fills (every crypto order fills next bar, so == orders created); fills = "
            "decision-driven fills (liquidation is cost-only, no synthetic fill); trades = "
            "closed position events"
        ),
    }


def _concentration(result, fills) -> dict[str, object]:
    contributions = sorted(result.per_symbol, key=lambda item: -item.realized_pnl)
    total_pnl = sum(item.realized_pnl for item in result.per_symbol)
    positive_pnl = sum(item.realized_pnl for item in result.per_symbol if item.realized_pnl > 0)
    top = contributions[:2]
    btc_contrib = next((item for item in result.per_symbol if item.symbol == "BTC/USD"), None)
    eth_contrib = next((item for item in result.per_symbol if item.symbol == "ETH/USD"), None)
    btc_fills = sum(1 for fill in fills if fill.symbol == "BTC/USD")
    eth_fills = sum(1 for fill in fills if fill.symbol == "ETH/USD")
    trades = len(result.closed_position_events)
    btc_trades = sum(1 for _ts, symbol, _pnl in result.closed_position_events if symbol == "BTC/USD")
    eth_trades = sum(1 for _ts, symbol, _pnl in result.closed_position_events if symbol == "ETH/USD")
    return {
        "top_1": {"symbol": top[0].symbol, "realized_pnl": round(top[0].realized_pnl, 2)} if top else None,
        "top_2": {"symbol": top[1].symbol, "realized_pnl": round(top[1].realized_pnl, 2)} if len(top) > 1 else None,
        "top1_share_of_positive_pnl": round(top[0].realized_pnl / positive_pnl, 4) if positive_pnl > 0 else 0.0,
        "top2_share_of_positive_pnl": (
            round(sum(item.realized_pnl for item in top[:2]) / positive_pnl, 4) if positive_pnl > 0 else 0.0
        ),
        "top1_share_of_total_pnl": round(top[0].realized_pnl / total_pnl, 4) if total_pnl != 0 else 0.0,
        "BTC": {
            "realized_pnl": round(btc_contrib.realized_pnl, 2) if btc_contrib else None,
            "share_of_total_pnl": round(btc_contrib.realized_pnl / total_pnl, 4)
            if btc_contrib and total_pnl != 0
            else 0.0,
            "share_of_fills": round(btc_fills / len(fills), 4) if fills else 0.0,
            "share_of_trades": round(btc_trades / trades, 4) if trades else 0.0,
        },
        "ETH": {
            "realized_pnl": round(eth_contrib.realized_pnl, 2) if eth_contrib else None,
            "share_of_total_pnl": round(eth_contrib.realized_pnl / total_pnl, 4)
            if eth_contrib and total_pnl != 0
            else 0.0,
            "share_of_fills": round(eth_fills / len(fills), 4) if fills else 0.0,
            "share_of_trades": round(eth_trades / trades, 4) if trades else 0.0,
        },
        "jackknife_note": (
            "symbol jackknife is DEGENERATE here: BTC/USD and ETH/USD are PINNED pool "
            "members (config universe.crypto.pinned_symbols) and are the structural "
            "low-variance parking spots, so dropping them changes the pinned observation "
            "set itself and needs a rebuilt universe — contrary to the mission's "
            "build-universe-once requirement. Baseline 168h jackknife already exists in "
            "strategy_validation_v4/candidate_B.json (drop-XRP +4.06%, drop-ETH +2.68%, "
            "drop-BTC -1.96%). Not re-run per cadence."
        ),
    }


def _detailed_metrics(result, bars_1d: list[Bar]) -> dict[str, object]:
    run_days = max(1, result.duration_days)
    total_cost = result.slippage_cost_total + result.spread_cost_total + result.fee_total
    avg_equity = sum(result.equity_curve) / len(result.equity_curve) if result.equity_curve else 0.0
    gross_pnl = result.starting_equity * result.gross_return
    net_pnl = result.ending_equity - result.starting_equity
    return {
        "return": round(result.total_return, 6),
        "gross_return": round(result.gross_return, 6),
        "cagr": round(result.cagr, 6),
        "annualized_volatility": round(result.annualized_volatility, 6),
        "sharpe": round(result.sharpe, 6),
        "sortino": round(result.sortino, 6),
        "max_drawdown": round(result.max_drawdown, 6),
        "calmar": round(result.calmar, 6),
        "profit_factor": round(_profit_factor(result), 4),
        "duration_days": result.duration_days,
        "decisions": result.rebalance_count,
        "orders": len({fill.client_order_id for fill in result.fills}),
        "fills": len(result.fills),
        "trades": len(result.closed_position_events),
        "avg_holdings_count": round(result.average_holdings_count, 3),
        "average_holding_days": (
            round(run_days * result.average_holdings_count / len(result.closed_position_events), 2)
            if result.closed_position_events
            else None
        ),
        "trades_per_year": round(len(result.closed_position_events) / (run_days / 365.25), 2),
        "turnover": round(result.turnover, 6),
        "annualized_turnover": round(result.turnover / (run_days / 365.25), 4),
        "fees_total": round(total_cost, 2),
        "fees_pct_of_avg_equity": round(total_cost / avg_equity, 6) if avg_equity > 0 else 0.0,
        "fees_pct_of_gross_pnl": (round(total_cost / abs(gross_pnl), 6) if abs(gross_pnl) > 1e-9 else None),
        "net_pnl": round(net_pnl, 2),
        "net_per_turnover": round(net_pnl / result.turnover, 2) if result.turnover > 0 else 0.0,
        "fees_per_turnover": round(total_cost / result.turnover, 2) if result.turnover > 0 else 0.0,
        "avg_cash_ratio": round(result.average_cash_ratio, 4),
        "per_year": _per_year_breakdown(result),
        "capital_utilization": _capital_utilization(result, bars_1d, REVERSAL_EVAL_START),
        "churn": _churn_diagnostics(result),
        "band_analysis": _band_analysis(result),
        "concentration": _concentration(result, result.fills),
        "decisions_per_year_actual": round(result.rebalance_count / (run_days / 365.25), 2),
    }


def _cadence_semantics_check(params: dict[str, object], hours: int, result) -> dict[str, object]:
    strategy = portfolio_strategy_from(STRATEGY_NAME, params, timeframe="1d")
    policy = rebalance_policy_for(strategy, params)
    assert policy.hours == hours, f"policy hours {policy.hours} != expected {hours}"
    run_days = max(1, result.duration_days)
    actual_per_year = result.rebalance_count / (run_days / 365.25)
    expected_per_year = 365.25 * 24 / hours
    # Every decision timestamp must sit on an epoch-hour block boundary
    # (RebalancePolicy.is_period_start uses int(ts.timestamp()) // (hours*3600)
    # on the first bar of each block; crypto 1d bars are midnight UTC).
    boundaries = sum(
        1 for rebalance in result.rebalances if int(rebalance.decision_timestamp.timestamp()) % (hours * 3600) == 0
    )
    return {
        "asserted_policy_hours": policy.hours,
        "expected_decisions_per_year": round(expected_per_year, 2),
        "actual_decisions_per_year": round(actual_per_year, 2),
        "decisions_total": result.rebalance_count,
        "pct_decisions_on_epoch_block_boundary": round(boundaries / len(result.rebalances), 4)
        if result.rebalances
        else 0.0,
        "gap_vs_expected_pct": round((actual_per_year / expected_per_year - 1.0) * 100, 2),
        "note": (
            "epoch-hour anchoring: 168h/96h/72h blocks are exact multiples of 24h, so on "
            "midnight-UTC 1d bars every block boundary is a decision bar; the residual gap "
            "vs theory comes from the LIVE-parity completeness gate (a streamed observation "
            "symbol missing a closed bar at the decision timestamp skips that decision)"
        ),
    }


# ---------------------------------------------------------------------------
# Staleness diagnostic (cadence-independent: ranking at 168h blocks)
# ---------------------------------------------------------------------------


def _rank_vectors(ranked: tuple[str, ...]) -> dict[str, int]:
    return {symbol: index for index, symbol in enumerate(ranked)}


def _spearman(a: tuple[str, ...], b: tuple[str, ...]) -> float | None:
    """Spearman rank correlation on the symbol intersection (average ranks)."""
    a_rank = _rank_vectors(a)
    b_rank = _rank_vectors(b)
    common = sorted(set(a_rank) & set(b_rank))
    if len(common) < 2:
        return None

    def _average_ranks(order: tuple[str, ...]) -> dict[str, float]:
        ranks: dict[str, float] = {}
        for index, symbol in enumerate(order):
            ranks[symbol] = float(index)
        return ranks

    a_r = _average_ranks(a)
    b_r = _average_ranks(b)
    xs = [a_r[symbol] for symbol in common]
    ys = [b_r[symbol] for symbol in common]
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    return cov / math.sqrt(var_x * var_y)


def _staleness_diagnostic(bars_1d: list[Bar], universe, strategy, start: datetime, end: datetime) -> dict[str, object]:
    """Weekly ranking over the full window (NO trades — pure signal audit).

    For every 168h epoch block decision date (RebalancePolicy parity) build a
    point-in-time context and run the strategy's OWN rank_candidates. Reports
    top-2 persistence, consecutive-block ranking churn and how long a symbol
    stays selected — the low-variance ranking is the same signal for every
    cadence, so this measures the signal's inherent staleness (BTC/ETH pinned
    parking).
    """
    block_seconds = 168 * 3600
    by_symbol: dict[str, list[Bar]] = {}
    for bar in bars_1d:
        by_symbol.setdefault(bar.symbol, []).append(bar)
    for series in by_symbol.values():
        series.sort(key=lambda bar: bar.timestamp)
    timestamps = sorted({bar.timestamp for bar in bars_1d})

    decision_dates: list[datetime] = []
    last_key: int | None = None
    for ts in timestamps:
        if ts < start or ts > end:
            continue
        key = int(ts.timestamp()) // block_seconds
        if key != last_key:
            decision_dates.append(ts)
            last_key = key

    rankings: list[tuple[datetime, tuple[str, ...]]] = []
    top2_history: list[tuple[datetime, tuple[str, ...]]] = []
    for decision in decision_dates:
        history: dict[str, tuple[Bar, ...]] = {}
        for symbol, series in by_symbol.items():
            eligible = [bar for bar in series if bar.timestamp <= decision]
            if eligible:
                history[symbol] = tuple(eligible)
        context = PortfolioContext(
            decision_timestamp=decision,
            market=Market.CRYPTO,
            timeframe="1d",
            history=history,
            universe=universe,
            universe_version="",
            equity=0.0,
            holdings={},
            prices={},
            benchmark_symbol="BTC/USD",
        )
        ranked = tuple(strategy.rank_candidates(context))
        rankings.append((decision, ranked))
        top2_history.append((decision, ranked[:2]))

    # Top-2 persistence: fraction of consecutive blocks with the SAME pair.
    same_pair = 0
    for index in range(1, len(top2_history)):
        if set(top2_history[index - 1][1]) == set(top2_history[index][1]) and len(top2_history[index][1]) == 2:
            same_pair += 1
    # Ranking churn: Spearman between consecutive full rankings.
    churns = [
        value
        for value in (_spearman(rankings[index - 1][1], rankings[index][1]) for index in range(1, len(rankings)))
        if value is not None
    ]
    # Per-symbol top-2 streaks (consecutive weeks selected).
    streaks: dict[str, list[int]] = {}
    selected: dict[str, int] = {}
    for _decision, pair in top2_history:
        current = set(pair)
        previous = set(selected)
        for symbol in current & previous:
            selected[symbol] += 1
        for symbol in current - previous:
            selected[symbol] = 1
        for symbol in previous - current:
            streaks.setdefault(symbol, []).append(selected[symbol])
            del selected[symbol]
    for symbol, count in selected.items():
        streaks.setdefault(symbol, []).append(count)
    symbol_streaks = {
        symbol: {
            "weeks_in_top2": sum(streaks[symbol]),
            "n_streaks": len(streaks[symbol]),
            "mean_streak_weeks": round(statistics.fmean(streaks[symbol]), 2),
            "max_streak_weeks": max(streaks[symbol]),
        }
        for symbol in sorted(streaks)
    }
    all_streaks = [streak for symbol in streaks for streak in streaks[symbol]]
    symbol_means = [stats["mean_streak_weeks"] for stats in symbol_streaks.values()]
    return {
        "blocks": len(decision_dates),
        "window": [start.isoformat(), end.isoformat()],
        "top2_persistence": {
            "same_pair_consecutive_blocks": same_pair,
            "total_consecutive_pairs": max(0, len(top2_history) - 1),
            "pct_consecutive_blocks_same_pair": round(same_pair / max(1, len(top2_history) - 1), 4),
        },
        "ranking_churn": {
            "consecutive_pairs": len(churns),
            "mean_spearman": round(statistics.fmean(churns), 4) if churns else None,
            "median_spearman": round(statistics.median(churns), 4) if churns else None,
            "min_spearman": round(min(churns), 4) if churns else None,
        },
        "top2_streaks": symbol_streaks,
        "overall": {
            "mean_streak_weeks_all": round(statistics.fmean(all_streaks), 2) if all_streaks else None,
            "mean_of_symbol_mean_streaks": round(statistics.fmean(symbol_means), 2) if symbol_means else None,
            "avg_time_selected_symbol_remains_top2_weeks": (
                round(statistics.fmean(all_streaks), 2) if all_streaks else None
            ),
        },
        "methodology": (
            "no trades: rank_candidates at every 168h epoch block decision date over "
            "2021-04-01..2026-07-31 (PIT pool, >=23 consecutive completed 1d bars, "
            "ascending realized vol of 22 daily returns); persistence = identical top-2 "
            "SET across consecutive blocks; churn = Spearman rank correlation of the full "
            "ranking on the symbol intersection; streak = consecutive blocks a symbol is "
            "in the top-2 (weeks)"
        ),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config(str(REPO / "config" / "settings.yaml"))

    print("Loading extended crypto dataset (1h)...")
    bars_1h = load_csv_bars(CRYPTO_CSV, default_market=Market.CRYPTO)
    print(f"  {len(bars_1h):,} 1h bars")
    bars_1d = aggregate_historical_bars(bars_1h, "1d", require_complete=True)
    print(f"  aggregated {len(bars_1d):,} 1d bars (require_complete=True)")

    print("Building point-in-time crypto universe ONCE from 1h scanner bars...")
    from parayabuck.backtest.product import build_product_universe

    universe = build_product_universe(config, bars_1h, market=Market.CRYPTO, end=CRYPTO_END, timeframe="1h")
    universe_hash = hash_point_in_time_universe(universe)
    snapshots = universe.snapshots
    candidate_counts = [snapshot.candidate_count for snapshot in snapshots]
    print(
        f"  snapshots={len(snapshots)} candidates avg={statistics.fmean(candidate_counts):.1f} "
        f"hash={universe_hash[:16]}..."
    )

    shared_context = {
        "dataset": CRYPTO_CSV.name,
        "dataset_sha256": DATASET_SHA256,
        "window": [REVERSAL_EVAL_START.isoformat(), CRYPTO_END.isoformat()],
        "warmup": "2021-01-01 .. 2021-03-31 (harness-wide)",
        "universe": {
            "hash": universe_hash,
            "snapshots": len(snapshots),
            "methodology": universe.methodology,
            "scanner_timeframe": "1h",
            "avg_candidate_pool": (round(statistics.fmean(candidate_counts), 1) if candidate_counts else 0.0),
            "built_once": True,
        },
    }

    # Staleness diagnostic — cadence-independent, run once over the full window.
    print("Staleness diagnostic (weekly ranking, no trades)...")
    strategy = portfolio_strategy_from(STRATEGY_NAME, FROZEN_CRYPTO, timeframe="1d")
    staleness = _staleness_diagnostic(bars_1d, universe, strategy, REVERSAL_EVAL_START, CRYPTO_END)
    (OUTPUT_DIR / "crypto_cadence_staleness.json").write_text(
        json.dumps(
            {"generated_at": datetime.now(UTC).isoformat(), **shared_context, **staleness},
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    print(
        "  top-2 persistence: "
        f"{staleness['top2_persistence']['pct_consecutive_blocks_same_pair']:.1%} "
        "| mean ranking Spearman: "
        f"{staleness['ranking_churn']['mean_spearman']} "
        f"| avg top-2 stay: {staleness['overall']['avg_time_selected_symbol_remains_top2_weeks']}w"
    )

    # Baseline fills (168h) are the reference for faster-cadence avoidance.
    baseline_fills: list | None = None
    reports: dict[str, dict[str, object]] = {}
    fills_by_label: dict[str, list] = {}
    for label, hours in CANDIDATES:
        print(f"\n=== CANDIDATE {label} (rebalance_hours={hours}) ===")
        parameters = dict(FROZEN_CRYPTO)
        parameters["rebalance_hours"] = hours
        policy = rebalance_policy_for(portfolio_strategy_from(STRATEGY_NAME, parameters, timeframe="1d"), parameters)
        assert policy.hours == hours, f"cadence assert failed: {policy.hours} != {hours}"
        print(f"  policy hours={policy.hours} (asserted)")

        print("  full-period 1x run...")
        one_x = run_product_backtest(
            config,
            bars_1d,
            market=Market.CRYPTO,
            strategy_name=STRATEGY_NAME,
            parameters=parameters,
            strategy_timeframe="1d",
            start=REVERSAL_EVAL_START,
            end=CRYPTO_END,
            cost_multiplier=1.0,
            persist=False,
            persist_artifacts=False,
            universe=universe,
        )
        result = one_x.result
        fills_by_label[label] = list(result.fills)
        if baseline_fills is None:
            baseline_fills = fills_by_label[label]
        detailed = _detailed_metrics(result, bars_1d)
        semantics = _cadence_semantics_check(parameters, hours, result)
        print(
            f"  return={detailed['return']:.2%} sharpe={detailed['sharpe']:.2f} "
            f"mdd={detailed['max_drawdown']:.2%} pf={detailed['profit_factor']:.2f} "
            f"decisions={detailed['decisions']} orders={detailed['orders']} "
            f"fills={detailed['fills']} trades={detailed['trades']}"
        )

        print("  cost stress 1x/2x/3x...")
        cost_stress = _cost_stress(
            config,
            bars_1d,
            market=Market.CRYPTO,
            strategy_name=STRATEGY_NAME,
            parameters=parameters,
            strategy_timeframe="1d",
            start=REVERSAL_EVAL_START,
            end=CRYPTO_END,
            universe=universe,
        )

        print("  anchored walk-forward (10 calendar 6-month folds from 2022-01-01)...")
        folds = _calendar_fold_window(datetime(2022, 1, 1, tzinfo=UTC), CRYPTO_END, 6)
        fold_results = []
        for index, (fold_start, fold_end) in enumerate(folds, start=1):
            fold_run = run_product_backtest(
                config,
                bars_1d,
                market=Market.CRYPTO,
                strategy_name=STRATEGY_NAME,
                parameters=parameters,
                strategy_timeframe="1d",
                start=fold_start,
                end=fold_end,
                cost_multiplier=1.0,
                persist=False,
                persist_artifacts=False,
                universe=universe,
            )
            stats = _fold_stats(fold_run.result)
            fold_results.append(stats)
            print(
                f"    fold {index:02d} {fold_start.date()}->{fold_end.date()} "
                f"return={stats['return']:.2%} sharpe={stats['sharpe']:.2f} "
                f"mdd={stats['max_drawdown']:.2%}"
            )
        positive = sum(1 for fold in fold_results if fold["return"] > 0)
        sorted_folds = sorted(fold_results, key=lambda item: item["return"])
        walk_forward = {
            "folds": fold_results,
            "positive_fold_count": positive,
            "negative_fold_count": len(fold_results) - positive,
            "total_fold_count": len(fold_results),
            "positive_fold_ratio": round(positive / len(fold_results), 4),
            "mean_fold_return": round(statistics.fmean([f["return"] for f in fold_results]), 6),
            "median_fold_return": round(statistics.median([f["return"] for f in fold_results]), 6),
            "worst_fold": (
                {
                    "start": sorted_folds[0]["start"],
                    "end": sorted_folds[0]["end"],
                    "return": sorted_folds[0]["return"],
                }
                if sorted_folds
                else None
            ),
            "best_fold": (
                {
                    "start": sorted_folds[-1]["start"],
                    "end": sorted_folds[-1]["end"],
                    "return": sorted_folds[-1]["return"],
                }
                if sorted_folds
                else None
            ),
            "methodology": (
                "anchored calendar 6-month folds from 2022-01-01 (10 folds), 1x cost, same "
                "PIT universe, warmup = all bars before the fold"
            ),
        }

        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "candidate": label,
            "rebalance_hours": hours,
            "strategy": STRATEGY_NAME,
            "parameters": parameters,
            "cadence_semantics": semantics,
            "full_period_1x": detailed,
            "cost_stress": cost_stress,
            "walk_forward": walk_forward,
            **shared_context,
        }
        reports[label] = report
        (OUTPUT_DIR / f"crypto_cadence_{label}.json").write_text(
            json.dumps(report, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8"
        )

    # Baseline-avoidance for the faster cadences (needs the 168h fills).
    assert baseline_fills is not None
    for label in ("96h", "72h"):
        reports[label]["full_period_1x"]["baseline_avoidance"] = _baseline_avoidance(
            fills_by_label[label], baseline_fills
        )
        (OUTPUT_DIR / f"crypto_cadence_{label}.json").write_text(
            json.dumps(reports[label], indent=2, sort_keys=True, allow_nan=False),
            encoding="utf-8",
        )

    # Baseline validation vs the frozen bakeoff artifact (candidate B).
    m168 = reports["168h"]["full_period_1x"]
    baseline_validation = {
        "reference": "docs/crypto_bakeoff_validation.md candidate B (validated bit-identically at commit 68331da9430b)",
        "bit_identical_metrics": {
            "return": {"reference": 0.046073, "this_run": m168["return"]},
            "cagr": {"reference": 0.008484, "this_run": m168["cagr"]},
            "sharpe": {"reference": 0.171791, "this_run": m168["sharpe"]},
            "sortino": {"reference": 0.230506, "this_run": m168["sortino"]},
            "max_drawdown": {"reference": 0.131654, "this_run": m168["max_drawdown"]},
            "profit_factor": {"reference": 1.109, "this_run": m168["profit_factor"]},
            "turnover": {"reference": 13.284977, "this_run": m168["turnover"]},
            "rebalance_count": {"reference": 263, "this_run": m168["decisions"]},
            "trade_count": {"reference": 124, "this_run": m168["trades"]},
        },
        "drift": {
            "fees_total": {
                "reference": 3757.39,
                "this_run": m168["fees_total"],
                "diff": round(m168["fees_total"] - 3757.39, 2),
                "pct": round((m168["fees_total"] / 3757.39 - 1.0) * 100, 2),
                "cause": (
                    "current HEAD (commit bb5e2eb 'codebase deslop') folds the end-of-window "
                    "liquidation costs (final BTC/ETH position closes) into the reported "
                    "slippage/spread/fee totals; the bakeoff commit 68331da did not. Net "
                    "equity is unaffected — every headline metric is bit-identical, only the "
                    "fee-total accounting line moved (+$41.44 = ~1.1%)"
                ),
            }
        },
    }
    reports["168h"]["baseline_validation"] = baseline_validation
    (OUTPUT_DIR / "crypto_cadence_168h.json").write_text(
        json.dumps(reports["168h"], indent=2, sort_keys=True, allow_nan=False), encoding="utf-8"
    )

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mission": "crypto cadence bake-off: 168h control vs 96h vs 72h, frozen alpha",
        "strategy": STRATEGY_NAME,
        "frozen_parameters": FROZEN_CRYPTO,
        "candidates": {label: reports[label] for label, _hours in CANDIDATES},
        "baseline_validation": baseline_validation,
        **shared_context,
    }
    (OUTPUT_DIR / "crypto_cadence_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8"
    )

    # ------------------------------------------------------------------
    # Printed markdown tables
    # ------------------------------------------------------------------
    one_x_rows = []
    for label, _hours in CANDIDATES:
        m = reports[label]["full_period_1x"]
        one_x_rows.append(
            [
                label,
                f"{m['return']:.2%}",
                f"{m['cagr']:.2%}",
                f"{m['annualized_volatility']:.1%}",
                f"{m['sharpe']:.2f}",
                f"{m['sortino']:.2f}",
                f"{m['max_drawdown']:.2%}",
                f"{m['calmar']:.2f}",
                f"{m['profit_factor']:.2f}",
                f"{m['decisions']}",
                f"{m['orders']}",
                f"{m['fills']}",
                f"{m['trades']}",
                f"{m['average_holding_days']}",
                f"{m['annualized_turnover']:.2f}x",
                f"${m['fees_total']:,.0f}",
                f"{m['fees_pct_of_gross_pnl']:.1%}",
                f"{m['net_per_turnover']:.0f}",
            ]
        )
    print("\n## Full-period metrics (1x, net of costs)")
    print(
        _md_table(
            [
                "cadence",
                "return",
                "CAGR",
                "vol",
                "Sharpe",
                "Sortino",
                "maxDD",
                "Calmar",
                "PF",
                "decisions",
                "orders",
                "fills",
                "trades",
                "avg hold (d)",
                "ann. turnover",
                "fees $",
                "fees/gross",
                "net/turnover",
            ],
            one_x_rows,
        )
    )

    cost_rows = []
    for label, _hours in CANDIDATES:
        for multiplier in ("1x", "2x", "3x"):
            run = reports[label]["cost_stress"][multiplier]
            cost_rows.append(
                [
                    f"{label} {multiplier}",
                    f"{run['return']:.2%}",
                    f"{run['cagr']:.2%}",
                    f"{run['sharpe']:.2f}",
                    f"{run['sortino']:.2f}",
                    f"{run['max_drawdown']:.2%}",
                    f"{run['profit_factor']:.2f}",
                    f"{run['turnover']:.2f}x",
                    f"${run['fees_total']:,.0f}",
                ]
            )
    print("\n## Baseline validation vs bakeoff doc (candidate B, 168h)")
    bv_rows = []
    for metric, pair in baseline_validation["bit_identical_metrics"].items():
        status = "bit-identical" if round(pair["reference"], 6) == round(pair["this_run"], 6) else "DRIFT"
        bv_rows.append([metric, pair["reference"], pair["this_run"], status])
    bv_rows.append(
        [
            "fees_total",
            baseline_validation["drift"]["fees_total"]["reference"],
            baseline_validation["drift"]["fees_total"]["this_run"],
            f"+{baseline_validation['drift']['fees_total']['pct']}% (liquidation costs now in fee totals)",
        ]
    )
    print(_md_table(["metric", "reference", "this run", "status"], bv_rows))

    print("\n## Cost stress (1x / 2x / 3x)")
    print(
        _md_table(
            ["cadence", "return", "CAGR", "Sharpe", "Sortino", "maxDD", "PF", "turnover", "fees $"],
            cost_rows,
        )
    )

    fold_rows = []
    for fold_index in range(10):
        row = [f"fold {fold_index + 1}"]
        for label, _hours in CANDIDATES:
            fold = reports[label]["walk_forward"]["folds"][fold_index]
            row.append(f"{fold['return']:.2%} / {fold['sharpe']:.2f} / {fold['max_drawdown']:.2%}")
        fold_rows.append(row)
    print("\n## Walk-forward (10 anchored calendar 6-month folds from 2022-01-01, 1x)")
    print(
        _md_table(
            ["fold", "168h (ret/Sharpe/mDD)", "96h (ret/Sharpe/mDD)", "72h (ret/Sharpe/mDD)"],
            fold_rows,
        )
    )
    wf_summary_rows = []
    for label, _hours in CANDIDATES:
        wf = reports[label]["walk_forward"]
        wf_summary_rows.append(
            [
                label,
                f"{wf['positive_fold_count']}/{wf['total_fold_count']}",
                f"{wf['median_fold_return']:.2%}",
                f"{wf['mean_fold_return']:.2%}",
                f"{wf['worst_fold']['return']:.2%}",
                f"{wf['best_fold']['return']:.2%}",
            ]
        )
    print(
        _md_table(
            ["cadence", "positive", "median", "mean", "worst", "best"],
            wf_summary_rows,
        )
    )

    util_rows = []
    for label, _hours in CANDIDATES:
        u = reports[label]["full_period_1x"]["capital_utilization"]
        util_rows.append(
            [
                label,
                f"{u['avg_exposure']:.2%}",
                f"{u['median_exposure']:.2%}",
                f"{u['pct_days_positive_exposure']:.1%}",
                f"{u['pct_days_near_max']:.1%}",
                f"{u['pct_days_idle']:.1%}",
                f"{u['time_to_first_deployment_days']}",
            ]
        )
    print("\n## Capital utilization (daily exposure, 0.10 sleeve)")
    print(
        _md_table(
            [
                "cadence",
                "avg exp",
                "median exp",
                "% days >0 exp",
                "% days >=9% exp",
                "% days idle",
                "time to first deploy (d)",
            ],
            util_rows,
        )
    )

    churn_rows = []
    for label, _hours in CANDIDATES:
        c = reports[label]["full_period_1x"]["churn"]
        b = reports[label]["full_period_1x"].get("baseline_avoidance", {})
        avoided = (
            f"{b['pct_fills_avoided_by_168h']:.1%} fills / {b['pct_trades_avoided_by_168h']:.1%} trades" if b else "—"
        )
        churn_rows.append(
            [
                label,
                c["round_trip_count_fill_level"],
                c["round_trip_count_position_level"],
                f"${c['reversal_cost']:,.0f}",
                c["ab_a_flip_count"],
                f"${c['flip_cost']:,.0f}",
                avoided,
            ]
        )
    print("\n## Churn diagnostics (7d sell->rebuy; A->B->A flips)")
    print(
        _md_table(
            [
                "cadence",
                "round trips (fill)",
                "round trips (position)",
                "reversal cost",
                "A->B->A flips",
                "flip cost",
                "avoided by 168h (fills/trades)",
            ],
            churn_rows,
        )
    )

    band_rows = []
    for label, _hours in CANDIDATES:
        b = reports[label]["full_period_1x"]["band_analysis"]
        band_rows.append(
            [
                label,
                b["decisions"],
                b["orders"],
                b["fills"],
                b["trades"],
                f"{b['order_ratio']:.1%}",
                f"{b['fill_ratio']:.1%}",
                f"${b['avg_cost_per_fill']:.2f}",
            ]
        )
    print("\n## Band analysis (decisions vs orders vs fills vs trades)")
    print(
        _md_table(
            [
                "cadence",
                "decisions",
                "orders",
                "fills",
                "trades",
                "orders/decisions",
                "fills/decisions",
                "avg cost/fill",
            ],
            band_rows,
        )
    )

    sem_rows = []
    for label, _hours in CANDIDATES:
        s = reports[label]["cadence_semantics"]
        sem_rows.append(
            [
                label,
                f"{s['expected_decisions_per_year']:.0f}",
                f"{s['actual_decisions_per_year']:.0f}",
                f"{s['gap_vs_expected_pct']:+.1f}%",
                f"{s['pct_decisions_on_epoch_block_boundary']:.1%}",
            ]
        )
    print("\n## Cadence semantic check (epoch-hour blocks, decisions/year)")
    print(
        _md_table(
            ["cadence", "expected/yr", "actual/yr", "gap", "% decisions on block boundary"],
            sem_rows,
        )
    )

    print("\n## Staleness diagnostic (168h-block ranking, no trades)")
    print(
        _md_table(
            ["metric", "value"],
            [
                ["blocks", staleness["blocks"]],
                [
                    "top-2 persistence (same pair, consecutive blocks)",
                    f"{staleness['top2_persistence']['pct_consecutive_blocks_same_pair']:.1%}",
                ],
                [
                    "mean ranking Spearman (consecutive blocks)",
                    staleness["ranking_churn"]["mean_spearman"],
                ],
                ["median ranking Spearman", staleness["ranking_churn"]["median_spearman"]],
                ["min ranking Spearman", staleness["ranking_churn"]["min_spearman"]],
                [
                    "avg time a selected symbol remains top-2 (weeks)",
                    staleness["overall"]["avg_time_selected_symbol_remains_top2_weeks"],
                ],
            ],
        )
    )
    print("\nTop-2 streaks by symbol (weeks):")
    streak_rows = [
        [
            symbol,
            stats["weeks_in_top2"],
            stats["n_streaks"],
            stats["mean_streak_weeks"],
            stats["max_streak_weeks"],
        ]
        for symbol, stats in staleness["top2_streaks"].items()
    ]
    print(
        _md_table(
            ["symbol", "weeks in top-2", "n streaks", "mean streak (w)", "max streak (w)"],
            streak_rows,
        )
    )

    conc_rows = []
    for label, _hours in CANDIDATES:
        c = reports[label]["full_period_1x"]["concentration"]
        conc_rows.append(
            [
                label,
                f"{c['top_1']['symbol']} {c['top_1']['realized_pnl']:+,.0f}",
                f"{c['top_2']['symbol']} {c['top_2']['realized_pnl']:+,.0f}",
                f"{c['BTC']['share_of_trades']:.0%} / {c['BTC']['share_of_total_pnl']:+.0%}",
                f"{c['ETH']['share_of_trades']:.0%} / {c['ETH']['share_of_total_pnl']:+.0%}",
            ]
        )
    print("\n## Concentration (top-1/top-2 realized PnL; BTC/ETH share of trades/PnL)")
    print(
        _md_table(
            ["cadence", "top-1", "top-2", "BTC trades/PnL", "ETH trades/PnL"],
            conc_rows,
        )
    )

    print(f"\nJSON written to {OUTPUT_DIR}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
