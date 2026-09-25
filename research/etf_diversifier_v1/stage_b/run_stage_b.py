#!/usr/bin/env python3
"""Run one ETF diversifier Stage B arm on the shared-ledger physical replay.

    .venv/bin/python research/etf_diversifier_v1/stage_b/run_stage_b.py --arm A --cost 1.0 \
        --out research/etf_diversifier_v1/stage_b/runs/A_1.0.json.gz

Arms: A (shipped V3 0.55 + crypto), B (V3 0.40, ETF disabled), C1/C2 (V3 0.40 +
ETF 0.15), I1 (composite V3 0.55, ETF disabled; invariance check against A).
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research/etf_diversifier_v1"))

from etf_strategies import EtfStaticDefensiveV1, EtfTrend12mV1  # noqa: E402

from parayabuck.backtest.lifetimes import load_entity_lifetimes  # noqa: E402
from parayabuck.backtest.numeric_history import build_numeric_history  # noqa: E402
from parayabuck.backtest.product import (  # noqa: E402
    _prepared_window,
    build_product_universe,
    load_csv_bars,
    load_csv_corporate_actions,
)
from parayabuck.backtest.universe import PointInTimeUniverse, hash_point_in_time_universe  # noqa: E402
from parayabuck.config import Config, load_config  # noqa: E402
from parayabuck.core.models import Market  # noqa: E402
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config  # noqa: E402
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for  # noqa: E402
from research.etf_diversifier_v1.stage_b.composite import (  # noqa: E402
    ETF_SLEEVE_SYMBOLS,
    CompositeEquity,
    CompositeOverlay,
    EtfExcludedOverlay,
    admit_excluding_etf,
    is_etf_key,
)
from research.physical_availability_clock_v2 import run_historical as rh  # noqa: E402
from research.physical_availability_clock_v2 import shared_replay as shared  # noqa: E402
from research.physical_availability_clock_v2.timing import (  # noqa: E402
    equity_action_effective_open,
    equity_daily_available_at,
    equity_session_open,
)

ETF_BARS = ROOT / "data/historical/etf_diversifier_v1_daily_split_sip.csv"
ETF_ACTIONS = ROOT / "data/historical/etf_diversifier_v1_corporate_actions.csv"
# SPY and BIL come from the canonical v5 inputs (byte-identical closes and dividends were verified).
ETF_EXTRA_SYMBOLS = ("TLT", "IEF", "GLD", "DBC")
ARMS = {
    "A": {"composite": False, "v3": 0.55, "etf": None},
    "I1": {"composite": True, "v3": 0.55, "etf": None},
    "B": {"composite": True, "v3": 0.40, "etf": None},
    "C1": {"composite": True, "v3": 0.40, "etf": EtfStaticDefensiveV1},
    "C2": {"composite": True, "v3": 0.40, "etf": EtfTrend12mV1},
}
ETF_ALLOCATION = 0.15


def _engine_factory(multiplier: float):  # type: ignore[no-untyped-def]
    def build(
        replay,
        config: Config,
        market: Market,
        universe: Any,
        universe_version: str,
        *,
        strategy=None,
        maximum_allocation=None,
        terminal_lifecycles=None,
    ):  # type: ignore[no-untyped-def]
        selection = config.strategy.equity if market == Market.EQUITY else config.strategy.crypto
        if strategy is None:
            strategy = portfolio_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
        return shared._SharedEngine(
            replay.account,
            strategy,
            rebalance_policy_for(strategy),
            starting_equity=config.backtest.initial_cash,
            slippage_bps=config.backtest.slippage_bps * multiplier,
            spread_bps=config.backtest.spread_bps * multiplier,
            commission=config.backtest.commission,
            crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps * multiplier,
            equity_taf_per_share=config.backtest.equity_taf_per_share,
            equity_taf_cap=config.backtest.equity_taf_cap,
            equity_cat_per_share=config.backtest.equity_cat_per_share,
            equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
            equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
            equity_regulatory_fee_multiplier=multiplier,
            benchmark_symbol="SPY" if market == Market.EQUITY else "BTC/USD",
            universe=universe,
            universe_version=universe_version,
            risk_limits=portfolio_risk_limits_from_config(config.risk),
            maximum_allocation=maximum_allocation if maximum_allocation is not None else selection.maximum_allocation,
            ordinary_alpha_excluded_symbols=(
                config.universe.equity.ordinary_alpha_excluded_symbols if market == Market.EQUITY else ()
            ),
            execution_policy=PortfolioExecutionPolicy(
                rebalance_band_pct=config.execution.rebalance_band_pct,
                minimum_order_notional=config.execution.minimum_order_notional,
            ),
            enforce_drawdown_halt=False,
            terminal_lifecycles=terminal_lifecycles,
        )

    return build


def _pinned_universe(universe: PointInTimeUniverse) -> PointInTimeUniverse:
    """Pin ETF-sleeve symbols into every observation stream, growing capacity to match."""
    snapshots = []
    for snapshot in universe.snapshots:
        added = tuple(sorted(ETF_SLEEVE_SYMBOLS - set(snapshot.pinned_symbols)))
        snapshots.append(
            replace(
                snapshot,
                pinned_symbols=(*snapshot.pinned_symbols, *added),
                maximum_stream_symbols=(
                    snapshot.maximum_stream_symbols + len(added) if snapshot.maximum_stream_symbols else 0
                ),
            )
        )
    return PointInTimeUniverse(universe.market, tuple(snapshots), universe.methodology + " + etf_diversifier_v1 pins")


def _equity_input(config: Config, replay, build, arm: dict[str, Any]):  # type: ignore[no-untyped-def]
    bars = load_csv_bars(rh.EQUITY_BARS, default_market=Market.EQUITY)
    lifetimes = load_entity_lifetimes(rh.EQUITY_LIFETIMES)
    actions = load_csv_corporate_actions(rh.EQUITY_ACTIONS)
    selection = config.strategy.equity
    v3 = portfolio_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
    universe = build_product_universe(
        config, bars, market=Market.EQUITY, end=rh.EQUITY_END, timeframe=v3.timeframe, observation_strategy=v3
    )
    strategy = None
    allocation = None
    if arm["composite"]:
        extra = [
            bar for bar in load_csv_bars(ETF_BARS, default_market=Market.EQUITY) if bar.symbol in ETF_EXTRA_SYMBOLS
        ]
        bars = bars + extra
        actions = actions + [
            action for action in load_csv_corporate_actions(ETF_ACTIONS) if action.symbol in ETF_EXTRA_SYMBOLS
        ]
        universe = _pinned_universe(universe)
        etf_class = arm["etf"]
        strategy = CompositeEquity(
            v3,
            etf_class() if etf_class is not None else None,
            v3_allocation=arm["v3"],
            etf_allocation=ETF_ALLOCATION,
        )
        allocation = arm["v3"] + (ETF_ALLOCATION if etf_class is not None else 0.0)
    filtered, warmup, (ordered, decision_bars) = _prepared_window(
        bars, Market.EQUITY, v3.timeframe, rh.EQUITY_START, rh.EQUITY_END
    )
    engine = build(
        replay,
        config,
        Market.EQUITY,
        universe,
        hash_point_in_time_universe(universe),
        strategy=strategy,
        maximum_allocation=allocation,
        terminal_lifecycles=lifetimes.engine_terminal_lifecycles(),
    )
    engine._numeric_history = build_numeric_history(warmup + ordered, market=Market.EQUITY, timeframe=v3.timeframe)
    del filtered
    labels = {bar.timestamp for bar in ordered}
    replay_input = shared.MarketReplayInput(
        engine,
        ordered,
        warmup,
        decision_bars,
        actions,
        {label: equity_daily_available_at(label.date()) for label in labels},
        {action.timestamp: equity_action_effective_open(action.timestamp.date()) for action in actions},
        final_close_at=max(equity_daily_available_at(label.date()) for label in labels),
        execution_schedule={label: equity_session_open(label.date()) for label in labels},
    )
    closes = sorted({equity_daily_available_at(label.date()) for label in labels})
    return replay_input, strategy, closes


def _session_nav(result: shared.SharedReplayResult, closes: list[datetime]) -> list[list[Any]]:
    """NAV at each XNYS close: the last replay NAV at or before that close."""
    rows: list[list[Any]] = []
    index = 0
    stamps, navs = result.timestamps, result.nav_path
    for close in closes:
        while index + 1 < len(stamps) and stamps[index + 1] <= close:
            index += 1
        if stamps[index] <= close:
            rows.append([close.isoformat(), navs[index]])
    return rows


def _etf_pnl(result: shared.SharedReplayResult) -> dict[str, float]:
    pnl: dict[str, float] = defaultdict(float)
    for event in result.events:
        symbol = event.get("symbol")
        if event.get("market") != "equity" or symbol not in ETF_SLEEVE_SYMBOLS:
            continue
        if event["event"] == "fill":
            signed = 1.0 if event["side"] == "sell" else -1.0
            pnl[symbol] += signed * float(event["qty"]) * float(event["fill_price"]) - float(event["fee"])
        elif event["event"] in {"final_liquidation", "terminal_liquidation"}:
            pnl[symbol] += float(event["proceeds"])
        elif event["event"] == "corporate_action":
            pnl[symbol] += float(event["cash_delta"])
    return dict(pnl)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", required=True, choices=sorted(ARMS))
    parser.add_argument("--cost", required=True, type=float, choices=(1.0, 2.0, 3.0))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    arm = ARMS[args.arm]
    started = time.monotonic()

    config = load_config(str(rh.CONFIG_PATH))
    if config.risk.max_positions != 8:
        raise ValueError("Stage B freezes the shipped cap of 8")
    build = _engine_factory(args.cost)
    replay = shared.SharedAccountReplay(
        starting_nav=config.backtest.initial_cash,
        maximum_positions=config.risk.max_positions,
        allow_test_strategies=arm["composite"],
    )
    with patch.object(rh, "_engine", build):
        crypto_input, _crypto_identity = rh._crypto_input(config, replay)
    equity_input, composite, closes = _equity_input(config, replay, build, arm)
    replay.add_market(crypto_input)
    replay.add_market(equity_input)

    patches = []
    composite_overlay = None
    if arm["composite"]:
        equity_engine = equity_input.engine
        composite_overlay = CompositeOverlay(equity_engine._shared_overlay, composite)
        equity_engine._shared_overlay = composite_overlay
        equity_engine.risk_overlay = composite_overlay
        crypto_engine = crypto_input.engine
        crypto_overlay = EtfExcludedOverlay(crypto_engine._shared_overlay._overlay, replay.account)
        crypto_engine._shared_overlay = crypto_overlay
        crypto_engine.risk_overlay = crypto_overlay
        patches.append(
            patch.object(shared, "_admit_synthetic_orders", admit_excluding_etf(shared._admit_synthetic_orders))
        )
    for item in patches:
        item.start()
    try:
        result = replay.run(liquidate_at_end=True)
    finally:
        for item in patches:
            item.stop()

    violations = [event for event in result.events if event["event"] == "slot_claim_violation"]
    max_non_etf_claims = 0
    for event in violations:
        claims = {(row[0], row[1]) for row in event["positions"]} | {
            (row[0], row[1]) for row in event["pending_orders"]
        }
        non_etf = {key for key in claims if not is_etf_key((Market(key[0]), key[1]))}
        max_non_etf_claims = max(max_non_etf_claims, len(non_etf))
    fills = [event for event in result.events if event["event"] == "fill"]
    decisions = [event for event in result.events if event["event"] == "decision"]
    payload = {
        "arm": args.arm,
        "cost_multiplier": args.cost,
        "config_sha256": rh._sha256_file(rh.CONFIG_PATH),
        "elapsed_seconds": time.monotonic() - started,
        "start_nav": result.start_nav,
        "ending_nav": result.ending_nav,
        "session_nav": _session_nav(result, closes),
        "invariants": rh._plain(result.invariants),
        "structural": {
            "slot_claim_violation_events": len(violations),
            "max_non_etf_slot_claims_in_violations": max_non_etf_claims,
            "fills_after_creation": True,  # enforced by an AssertionError inside the harness
            "etf_decision_count": composite.etf_decision_count if composite is not None else 0,
            "halt_counts": composite_overlay.halt_counts if composite_overlay is not None else {},
        },
        "hashes": {
            "decisions": rh._digest(decisions),
            "fills_without_client_ids": rh._digest(
                [{key: value for key, value in event.items() if key != "client_order_id"} for event in fills]
            ),
            "equity_decisions": rh._digest([event for event in decisions if event["market"] == "equity"]),
        },
        "counts": {"decisions": len(decisions), "fills": len(fills)},
        "etf_pnl": _etf_pnl(result),
        "costs": {
            "fees": sum(float(event["fee"]) for event in fills),
            "slippage": sum(float(event["slippage_cost"]) for event in fills),
            "spread": sum(float(event["spread_cost"]) for event in fills),
        },
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.out, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
    print(
        f"{args.arm} {args.cost}x: ending_nav={result.ending_nav:.2f} fills={len(fills)} "
        f"violations={len(violations)} elapsed={payload['elapsed_seconds']:.0f}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
