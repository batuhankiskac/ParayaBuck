#!/usr/bin/env python3
"""Shared-ledger validation of the PRODUCTION dividend-sleeve integration.

Unlike the sleeve candidates v3 Stage B arm C (a research composite with a
33-symbol stream), this runs the shipped code path: the configured
``equity_residual_momentum_v3_dividend_sleeve_v1`` strategy, the canonical
``PortfolioRiskOverlay`` sleeve composition and slot exemption, and the
production 30-symbol observation stream (sleeve ETFs pinned, 23 ranked V3
slots). The only harness patch passes the configured slot exemption into the
harness's slot admission, as every production caller does.

    .venv/bin/python research/dividend_sleeve_production_v1/run_production.py \
        --config config/settings.yaml --cost 1.0 --out .../P_1.0.json.gz

Arm A reproduction: ``--config research/dividend_sleeve_production_v1/settings_shipped_v3_0.55.yaml``
must reproduce the archived Stage B arm A hashes under the new code.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from parayabuck.backtest.lifetimes import load_entity_lifetimes  # noqa: E402
from parayabuck.backtest.numeric_history import build_numeric_history  # noqa: E402
from parayabuck.backtest.product import (  # noqa: E402
    _prepared_window,
    build_product_universe,
    load_csv_bars,
    load_csv_corporate_actions,
)
from parayabuck.backtest.universe import hash_point_in_time_universe  # noqa: E402
from parayabuck.config import load_config  # noqa: E402
from parayabuck.core.models import Market  # noqa: E402
from parayabuck.execution.portfolio import reserve_opening_order_slots  # noqa: E402
from parayabuck.portfolio import portfolio_strategy_from  # noqa: E402
from research.physical_availability_clock_v2 import run_historical as rh  # noqa: E402
from research.physical_availability_clock_v2 import shared_replay as shared  # noqa: E402
from research.physical_availability_clock_v2.timing import (  # noqa: E402
    equity_action_effective_open,
    equity_daily_available_at,
    equity_session_open,
)
from research.sleeve_candidates_v2.stage_b import composite as sleeve  # noqa: E402
from research.sleeve_candidates_v2.stage_b import run_stage_b as stage_b  # noqa: E402

# Campaign ETF file: split-adjusted bars with dividend rates restated to
# post-split shares (see download_data.py).
SLEEVE_BARS = ROOT / "data/historical/profit_max_v1_daily_split_sip.csv"
SLEEVE_ACTIONS = ROOT / "data/historical/profit_max_v1_corporate_actions.csv"


def _admission(exempt: tuple[str, ...]):  # type: ignore[no-untyped-def]
    def admit(orders, account, maximum_positions, reserved_position_keys):  # type: ignore[no-untyped-def]
        requests = tuple(
            shared.PortfolioOrderRequest(
                client_order_id=order.client_order_id,
                decision_id=order.client_order_id,
                symbol=order.symbol,
                market=order.market,
                side=shared.PortfolioOrderSide(order.side.value),
                quantity=order.qty,
                reference_price=0.0,
                decision_timestamp=order.decision_timestamp,
                strategy_name="historical-replay",
                strategy_version="2",
                risk_reducing=order.side == shared.OrderSide.SELL,
            )
            for order in orders
        )
        admitted = reserve_opening_order_slots(
            requests,
            account,
            maximum_positions=maximum_positions,
            reserved_position_keys=reserved_position_keys,
            slot_exempt_equity_symbols=exempt,
        )
        admitted_ids = {order.client_order_id for order in admitted}
        return tuple(order for order in orders if order.client_order_id in admitted_ids)

    return admit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--cost", required=True, type=float, choices=(1.0, 2.0, 3.0))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    started = time.monotonic()
    config = load_config(args.config)
    if config.risk.max_positions != 8:
        raise ValueError("Validation freezes the shipped cap of 8")
    sleeve_symbols = config.universe.equity.sleeve_trade_symbols
    sleeve.configure(sleeve_symbols, cash_equivalent=False)  # reporting only (is_etf_key)
    build = stage_b._engine_factory(args.cost)
    replay = shared.SharedAccountReplay(
        starting_nav=config.backtest.initial_cash,
        maximum_positions=config.risk.max_positions,
        allow_test_strategies=True,  # product identities outside the frozen migration arms
    )
    with patch.object(rh, "_engine", build):
        crypto_input, _crypto_identity = rh._crypto_input(config, replay)

    bars = load_csv_bars(rh.EQUITY_BARS, default_market=Market.EQUITY)
    lifetimes = load_entity_lifetimes(rh.EQUITY_LIFETIMES)
    actions = load_csv_corporate_actions(rh.EQUITY_ACTIONS)
    needed = set(sleeve_symbols) | set(config.universe.equity.reference_symbols)
    if needed - {bar.symbol for bar in bars}:
        # ETFs absent from canonical v5 (SCHD/GLD/OUNZ, VOO, QLD, TQQQ) come from
        # the campaign file; symbols present in v5 keep their v5 bars.
        missing = needed - {bar.symbol for bar in bars}
        bars = bars + [bar for bar in load_csv_bars(SLEEVE_BARS, default_market=Market.EQUITY) if bar.symbol in missing]
        actions = actions + [
            action for action in load_csv_corporate_actions(SLEEVE_ACTIONS) if action.symbol in missing
        ]
    selection = config.strategy.equity
    strategy = portfolio_strategy_from(selection.name, selection.parameters, timeframe=selection.timeframe)
    universe = build_product_universe(
        config,
        bars,
        market=Market.EQUITY,
        end=rh.EQUITY_END,
        timeframe=strategy.timeframe,
        observation_strategy=strategy,
    )
    filtered, warmup, (ordered, decision_bars) = _prepared_window(
        bars, Market.EQUITY, strategy.timeframe, rh.EQUITY_START, rh.EQUITY_END
    )
    engine = build(
        replay,
        config,
        Market.EQUITY,
        universe,
        hash_point_in_time_universe(universe),
        strategy=strategy,
        terminal_lifecycles=lifetimes.engine_terminal_lifecycles(),
    )
    engine._numeric_history = build_numeric_history(
        warmup + ordered, market=Market.EQUITY, timeframe=strategy.timeframe
    )
    del filtered
    labels = {bar.timestamp for bar in ordered}
    equity_input = shared.MarketReplayInput(
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
    replay.add_market(crypto_input)
    replay.add_market(equity_input)

    admission = patch.object(shared, "_admit_synthetic_orders", _admission(config.risk.slot_exempt_equity_symbols))
    admission.start()
    try:
        result = replay.run(liquidate_at_end=True)
    finally:
        admission.stop()

    stream_sizes = sorted({len(snapshot.observation_set) for snapshot in universe.snapshots})
    pinned_sets = sorted({tuple(snapshot.pinned_symbols) for snapshot in universe.snapshots})
    violations = [event for event in result.events if event["event"] == "slot_claim_violation"]
    max_non_etf_claims = 0
    for event in violations:
        claims = {(row[0], row[1]) for row in event["positions"]} | {
            (row[0], row[1]) for row in event["pending_orders"]
        }
        non_etf = {key for key in claims if not sleeve.is_etf_key((Market(key[0]), key[1]))}
        max_non_etf_claims = max(max_non_etf_claims, len(non_etf))
    fills = [event for event in result.events if event["event"] == "fill"]
    decisions = [event for event in result.events if event["event"] == "decision"]
    sleeve_fills = [event for event in fills if event.get("symbol") in sleeve_symbols]
    payload = {
        "arm": "P" if sleeve_symbols else "A",
        "config": args.config,
        "strategy": [strategy.name, strategy.version],
        "cost_multiplier": args.cost,
        "config_sha256": rh._sha256_file(Path(args.config)),
        "elapsed_seconds": time.monotonic() - started,
        "start_nav": result.start_nav,
        "ending_nav": result.ending_nav,
        "session_nav": stage_b._session_nav(result, closes),
        "invariants": rh._plain(result.invariants),
        "structural": {
            "slot_claim_violation_events": len(violations),
            "max_non_etf_slot_claims_in_violations": max_non_etf_claims,
            "observation_stream_sizes": stream_sizes,
            "pinned_symbol_sets": [list(item) for item in pinned_sets[:3]],
            "sleeve_fill_count": len(sleeve_fills),
            "sleeve_fill_months": len({event["timestamp"][:7] for event in sleeve_fills if "timestamp" in event}),
        },
        "hashes": {
            "decisions": rh._digest(decisions),
            "fills_without_client_ids": rh._digest(
                [{key: value for key, value in event.items() if key != "client_order_id"} for event in fills]
            ),
            "equity_decisions": rh._digest([event for event in decisions if event["market"] == "equity"]),
        },
        "counts": {"decisions": len(decisions), "fills": len(fills)},
        "etf_pnl": stage_b._etf_pnl(result),
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
        f"{payload['arm']} {args.cost}x: ending_nav={result.ending_nav:.2f} fills={len(fills)} "
        f"sleeve_fills={len(sleeve_fills)} violations={len(violations)} streams={stream_sizes} "
        f"elapsed={payload['elapsed_seconds']:.0f}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
