#!/usr/bin/env python3
"""Execution-quality report over the runtime ledger.

Prints the cost-calibration report (configured slippage/spread/fees, the
observed next-open-vs-decision-close gap, and observed vs modeled fees) plus
the expected-vs-actual summary of the latest decisions. REPORT ONLY: this
script never adjusts a cost assumption, never places an order, and never
writes to the runtime database.

The gap distribution is measured against ``portfolio_orders.reference_price``,
the DECISION close, while market orders fill at the next session open: it is
an overnight move, not execution slippage, so this report deliberately prints
no ``above_model``/``below_model`` verdict. The backtest-aligned comparison
(next-open fills against the next open, with modeled adverse cost and fees)
is ``scripts/execution_shadow_report.py``.

Usage:
    .venv/bin/python scripts/execution_quality_report.py [--db PATH] [--config PATH] [--account-id ID]

Execution and forward metrics are scoped to ``--account-id``, defaulting to
the account of the newest runtime session; a ledger with no session keeps the
whole-database view.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
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
from parayabuck.monitoring.forensics import current_account_id  # noqa: E402

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


def resolve_account_id(db_path: Path, explicit: str | None) -> str | None:
    """The account the metrics are scoped to: the explicit one, else the
    newest runtime session's account; None (whole database) when unknown."""
    if explicit:
        return explicit
    if not db_path.is_file():
        return None
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return current_account_id(conn)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None, help="runtime database path (default: config)")
    parser.add_argument("--config", default=str(_CONFIG_PATH), help="settings.yaml path")
    parser.add_argument(
        "--account-id",
        default=None,
        help="scope execution/forward metrics to this account (default: newest runtime session's account)",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    db_path = Path(args.db or config.runtime.decision_db_path)
    print("ParayaBuck execution-quality report")
    print(f"runtime database: {db_path}")
    print(f"runtime.mode: {config.runtime.mode.value}")

    calibration = cost_calibration(db_path, config)
    if not calibration.get("available", False):
        print(f"\nNo runtime database at {db_path}; nothing to report (empty state, exit 0).")
        return 0

    account_id = resolve_account_id(db_path, args.account_id)
    print(f"account: {account_id or 'all (no runtime session recorded)'}")

    configured = calibration["configured"]
    print("\nConfigured cost assumptions (backtest model, REPORT ONLY):")
    if configured.get("available", False):
        print(
            f"  slippage_bps={_fmt(configured.get('slippage_bps'))} "
            f"spread_bps={_fmt(configured.get('spread_bps'))} "
            f"commission={_fmt(configured.get('commission'))}"
        )
        print(f"  equity_use_effective_dated_fees={configured.get('equity_use_effective_dated_fees')}")
        print(f"  model: {configured.get('model')}")
    else:
        print("  (no configuration available)")

    observed = calibration["observed"]
    _print_summary(
        f"Observed {observed.get('slippage_bps_basis', 'decision close')} gap (bps)", observed["slippage_bps"]
    )
    print(f"  basis: {observed.get('slippage_bps_note', 'next open vs decision close')}")
    _print_summary("Observed spread estimates (bps)", observed["spread_estimate_bps"])
    _print_summary("Observed fees", observed["fees"])
    print(f"  total observed fees: {_fmt(observed.get('total_fees'), 6)}")
    modeled = observed.get("modeled_fees")
    if isinstance(modeled, dict) and modeled.get("available"):
        _print_summary("Modeled fees (same fills, backtest cost model)", modeled)
        print(f"  basis: {configured.get('modeled_fee_basis')}")
    else:
        print(f"  modeled fees: {(modeled or {}).get('note', 'not computed')}")

    comparison = calibration["comparison"]
    print("\nDecision-close diagnostic (NOT a backtest cost-model verdict):")
    print(f"  observed median bps: {_fmt(comparison.get('observed_median_bps'))}")
    print(f"  observed p75 bps:    {_fmt(comparison.get('observed_p75_bps'))}")
    print(f"  observed p95 bps:    {_fmt(comparison.get('observed_p95_bps'))}")
    print(f"  model verdict:       {comparison.get('model_verdict')}")
    print(f"  model verdict note:  {comparison.get('model_verdict_note')}")
    print(f"  reference basis: {comparison.get('reference_basis', 'decision_close')}")
    print(f"  alignment status: {comparison.get('alignment_status', 'not_backtest_aligned')}")
    print(f"  backtest-aligned metric: {comparison.get('backtest_aligned_metric')}")
    print(f"  policy: {calibration.get('policy')}")

    metrics = execution_metrics(db_path, account_id=account_id)
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

    forward = forward_metrics(db_path, account_id=account_id)
    if forward.get("available", False):
        strategy = forward["strategy"]
        print("\nForward metrics:")
        print(
            f"  decisions: {strategy['decisions']['count']} by_market={json.dumps(strategy['decisions']['by_market'])}"
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
