#!/usr/bin/env python3
"""Execution-quality report over the runtime ledger.

Prints the cost-calibration report (configured slippage/spread/fees vs the
observed median/p75/p95 slippage and observed fees) plus the expected-vs-
actual summary of the latest decisions. REPORT ONLY: this script never
adjusts a cost assumption, never places an order, and never writes to the
runtime database.

Usage:
    .venv/bin/python scripts/execution_quality_report.py [--db PATH] [--config PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from parayabuck.config import load_config  # noqa: E402
from parayabuck.execution.telemetry import (  # noqa: E402
    cost_calibration,
    decision_expected_vs_actual,
    execution_metrics,
    forward_metrics,
    latest_decision_ids,
)

_CONFIG_PATH = ROOT / "config" / "settings.yaml"


def _fmt(value: object, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _print_summary(title: str, summary: dict[str, object]) -> None:
    print(f"\n{title}")
    print(
        f"  count={_fmt(summary.get('count'))} mean={_fmt(summary.get('mean'))} "
        f"median={_fmt(summary.get('median'))} p75={_fmt(summary.get('p75'))} "
        f"p95={_fmt(summary.get('p95'))}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None, help="runtime database path (default: config)")
    parser.add_argument("--config", default=str(_CONFIG_PATH), help="settings.yaml path")
    args = parser.parse_args()

    config = load_config(args.config)
    db_path = Path(args.db or config.runtime.decision_db_path)
    print("ParayaBuck execution-quality report")
    print(f"runtime database: {db_path}")
    print(f"runtime.mode: {config.runtime.mode.value}")

    calibration = cost_calibration(db_path, config)
    if not calibration.get("available", False):
        print(f"\nNo runtime database at {db_path}; nothing to report (empty state, exit 0).")
        return 0

    configured = calibration["configured"]
    print("\nConfigured cost assumptions (backtest model, REPORT ONLY):")
    if configured.get("available", False):
        print(
            f"  slippage_bps={_fmt(configured.get('slippage_bps'))} "
            f"spread_bps={_fmt(configured.get('spread_bps'))} "
            f"commission={_fmt(configured.get('commission'))} "
            f"crypto_taker_fee_bps={_fmt(configured.get('crypto_taker_fee_bps'))}"
        )
        print(
            f"  equity_use_effective_dated_fees={configured.get('equity_use_effective_dated_fees')}"
        )
        print(f"  model: {configured.get('model')}")
    else:
        print("  (no configuration available)")

    observed = calibration["observed"]
    _print_summary("Observed slippage (bps, positive = adverse)", observed["slippage_bps"])
    _print_summary("Observed spread estimates (bps)", observed["spread_estimate_bps"])
    _print_summary("Observed fees", observed["fees"])
    print(f"  total observed fees: {_fmt(observed.get('total_fees'), 6)}")

    comparison = calibration["comparison"]
    print("\nCost calibration (configured vs observed):")
    modeled = comparison.get("modeled_adverse_bps")
    print(f"  modeled adverse bps (slippage + spread/2): {_fmt(modeled)}")
    print(f"  observed median bps: {_fmt(comparison.get('observed_median_bps'))}")
    print(f"  observed p75 bps:    {_fmt(comparison.get('observed_p75_bps'))}")
    print(f"  observed p95 bps:    {_fmt(comparison.get('observed_p95_bps'))}")
    print(f"  verdict: {comparison.get('verdict')}")
    print(f"  policy: {calibration.get('policy')}")

    metrics = execution_metrics(db_path)
    if metrics.get("available", False):
        orders = metrics["orders"]
        print("\nExecution metrics:")
        print(f"  orders total={orders['total']} open={orders['open']}")
        print(f"  rejects: {metrics['rejects']}")
        _print_summary("  fill latency (s)", metrics["fill_latency"])
        _print_summary("  slippage (bps)", metrics["slippage"])
        print(
            f"  fees: total={_fmt(metrics['fees'].get('total'), 6)} "
            f"by_market={json.dumps(metrics['fees'].get('by_market'))}"
        )

    forward = forward_metrics(db_path)
    if forward.get("available", False):
        strategy = forward["strategy"]
        print("\nForward metrics:")
        print(
            f"  decisions: {strategy['decisions']['count']} "
            f"by_market={json.dumps(strategy['decisions']['by_market'])}"
        )
        _print_summary("  target turnover (one-way weight)", strategy["target_turnover"])
        print(f"  observation-set churn events: {strategy['observation_set_churn']['total']}")
        _print_summary("  candidate pool size", strategy["candidate_pool_size"])
        portfolio = forward["portfolio"]
        print(f"  portfolio pnl: {json.dumps(portfolio['pnl'])}")

    # Expected-vs-actual summary for the latest decisions.
    latest_by_market = latest_decision_ids(db_path)
    if latest_by_market:
        print("\nExpected-vs-actual (latest decision per market):")
        for market, decision_id in sorted(latest_by_market.items()):
            comparison_row = decision_expected_vs_actual(db_path, decision_id)
            print(f"  market={market} decision={decision_id}")
            for order in comparison_row.get("orders", []):
                print(
                    f"    {order['side']:4s} {order['symbol']:12s} "
                    f"expected_qty={_fmt(order['expected_quantity'])} "
                    f"fill_ratio={_fmt(order['fill_ratio'])} "
                    f"price_delta_bps={_fmt(order['fill_price_delta_bps'])} "
                    f"slippage_bps={_fmt(order['slippage_bps'])} "
                    f"fees={_fmt(order['fees'], 6)} "
                    f"delay_s={_fmt(order['fill_delay_seconds'])} "
                    f"status={order['status']}"
                )

    print("\nReport complete (report only; no assumptions were changed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
