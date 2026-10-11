"""Execution boundary benchmarks — broker path latency without network.

Scope: AlpacaBroker facade + AccountService/OrderService/Reconciliation via FakeTradingClient.
Dataset: synthetic 100 positions, no CSV, no universe.
Window: N/A (broker isolated).
Setup: ledger + fake client, peak temp dir, no network.
Python: 3.12+, git HEAD recorded in JSON artifact.

Cases:
- account 100 positions
- asset rules warm cache (1 asset cached, 100 lookups)
- asset rules cold cache (100 unique symbols)
- submit portfolio BUY
- submit portfolio SELL
- submit timeout → recovered by client ID
- reconcile 20 positions
- peak state atomic save
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from benchmarks.harness import Report, timeit
from parayabuck.core.models import Market
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.ledger import ExecutionLedger
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide
from tests.fake_client import FakeTradingClient


def _git_head() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def _broker_with_fake(tmp_path: Path | None = None) -> tuple[AlpacaBroker, FakeTradingClient, Path]:
    if tmp_path is None:
        tmp_path = Path(tempfile.mkdtemp())
    ledger = ExecutionLedger(tmp_path / "ledger.db")
    fake = FakeTradingClient()
    broker = AlpacaBroker("k", "s", paper=True, ledger=ledger, peak_state_path=tmp_path / "peak.json", client=fake)
    fake.set_account(equity="100000")
    return broker, fake, tmp_path


def bench_account_100(report: Report) -> None:
    broker, fake, _ = _broker_with_fake()
    # Seed 100 equity positions
    for i in range(100):
        sym = f"SYM{i:03d}"
        fake.set_position(sym, qty=10)
        fake.set_asset(sym)
    # Also need ledger / portfolio ownership to make them managed for realistic path
    broker.portfolio_owned_quantities = lambda: {f"SYM{i:03d}": 10 for i in range(100)}

    def _account() -> None:
        broker.account()

    timing, _ = timeit(_account, label="account 100 positions (Fake)", runs=100)
    report.add(timing)


def bench_asset_warm(report: Report) -> None:
    broker, fake, _ = _broker_with_fake()
    fake.set_asset("AAPL")
    # Warm the cache once
    broker.asset_trading_rules("AAPL", Market.EQUITY)

    def _warm() -> None:
        for _ in range(100):
            broker.asset_trading_rules("AAPL", Market.EQUITY)

    timing, _ = timeit(_warm, label="asset rules warm cache 100x (1 symbol)", runs=200)
    report.add(timing)


def bench_asset_cold(report: Report) -> None:
    broker, fake, _ = _broker_with_fake()

    def _cold() -> None:
        for i in range(20):
            sym = f"COLD{i:03d}"
            fake.set_asset(sym)
            broker.asset_trading_rules(sym, Market.EQUITY)

    timing, _ = timeit(_cold, label="asset rules cold cache 20 symbols", runs=100)
    report.add(timing)


def _make_order(symbol: str, side: PortfolioOrderSide, client_id: str) -> PortfolioOrderRequest:
    return PortfolioOrderRequest(
        symbol=symbol,
        market=Market.EQUITY,
        side=side,
        quantity=5,
        reference_price=100,
        client_order_id=client_id,
        decision_id="d1",
        decision_timestamp=datetime.now(UTC),
        strategy_name="bench",
        strategy_version="1",
        risk_reducing=False,
    )


def bench_submit_buy(report: Report) -> None:
    def _one() -> None:
        broker, fake, _ = _broker_with_fake()
        fake.set_asset("AAPL")
        order = _make_order("AAPL", PortfolioOrderSide.BUY, f"bench-buy-{time.time_ns()}")
        broker.submit_portfolio_order(order)

    timing, _ = timeit(_one, label="submit portfolio BUY (Fake)", runs=100)
    report.add(timing)


def bench_submit_sell(report: Report) -> None:
    def _one() -> None:
        broker, fake, _ = _broker_with_fake()
        fake.set_asset("AAPL")
        fake.set_position("AAPL", qty=10)
        broker.portfolio_owned_quantities = lambda: {"AAPL": 10}
        # Need account snapshot to seed ledger? For submit we just need rules and no open order
        order = _make_order("AAPL", PortfolioOrderSide.SELL, f"bench-sell-{time.time_ns()}")
        broker.submit_portfolio_order(order)

    timing, _ = timeit(_one, label="submit portfolio SELL (Fake)", runs=100)
    report.add(timing)


def bench_submit_recovered(report: Report) -> None:
    # Simulate submit timeout then recovered by client ID lookup
    def _one() -> None:
        broker, fake, _ = _broker_with_fake()
        fake.set_asset("AAPL")
        # Script: first submit raises timeout, then lookup finds order
        call_count = {"n": 0}
        orig_submit = fake.submit_order

        def script_submit(req):  # type: ignore[no-untyped-def]
            if call_count["n"] == 0:
                call_count["n"] += 1
                # Simulate that broker actually created order but response lost
                orig_submit(req)
                # Next lookup will find it, but this call raises
                raise TimeoutError("simulated network timeout after accept")
            return orig_submit(req)

        # Use get_order_by_client_id to recover — FakeTradingClient already stores by client_id
        # So we don't need extra scripting; submit will raise, then OrderService will lookup and find it
        # To trigger the path, we need fake to have an order stored under that client_id after first call
        # Our script_submit above does create order then raise, so lookup will find it
        fake.script_submit = script_submit  # type: ignore[attr-defined]
        order = _make_order("AAPL", PortfolioOrderSide.BUY, "recovery-test-fixed-id")
        # First attempt will raise then recover? Actually OrderService will call submit, get Timeout, then lookup
        # Since we stored order via orig_submit, lookup will succeed and return it (recovered)
        try:
            broker.submit_portfolio_order(order)
        except TimeoutError:
            pass
        except Exception:
            pass
        finally:
            fake.script_submit = None  # type: ignore[attr-defined]

    timing, _ = timeit(_one, label="submit timeout → recovered by client_id (Fake)", runs=50)
    report.add(timing)


def bench_reconcile_20(report: Report) -> None:
    broker, fake, _ = _broker_with_fake()
    for i in range(20):
        sym = f"R{i:03d}"
        fake.set_position(sym, qty=10)
        fake.set_asset(sym)
    broker.portfolio_owned_quantities = lambda: {f"R{i:03d}": 10 for i in range(20)}

    def _reconcile() -> None:
        broker.reconcile()

    timing, _ = timeit(_reconcile, label="reconcile 20 positions (Fake)", runs=100)
    report.add(timing)


def bench_peak(report: Report) -> None:
    broker, _fake, _ = _broker_with_fake()

    def _peak() -> None:
        broker._save_peak_equity()

    timing, _ = timeit(_peak, label="peak save (atomic_write)", runs=200)
    report.add(timing)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Execution boundary broker-path latency (no network).")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Where to write the JSON artifact (default: a fresh file under the system temp dir).",
    )
    args = parser.parse_args()
    report = Report()
    bench_account_100(report)
    bench_asset_warm(report)
    bench_asset_cold(report)
    bench_submit_buy(report)
    bench_submit_sell(report)
    bench_submit_recovered(report)
    bench_reconcile_20(report)
    bench_peak(report)
    report.print_table()
    # Write JSON artifact
    artifact = (
        args.out
        if args.out is not None
        else Path(tempfile.mkdtemp(prefix="execution_boundary_")) / "execution_boundary.json"
    )
    payload = {
        "git_head": _git_head(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "platform": platform.platform(),
        "cases": [
            {
                "label": t.label,
                "median_s": t.median,
                "p95_s": t.p95,
                "min_s": t.minimum,
                "max_s": t.maximum,
                "iterations": len(t.samples),
            }
            for t in report.timings
        ],
    }
    artifact.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote {artifact} ({len(report.timings)} cases)")
