"""Execution boundary benchmarks — broker path latency without network."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from benchmarks.harness import Report, timeit
from parayabuck.execution.alpaca import AlpacaBroker
from parayabuck.execution.fake_client import FakeTradingClient
from parayabuck.execution.ledger import ExecutionLedger


def _broker_with_fake(tmp_path: Path | None = None) -> tuple[AlpacaBroker, FakeTradingClient, Path]:
    if tmp_path is None:
        tmp_path = Path(tempfile.mkdtemp())
    ledger = ExecutionLedger(tmp_path / "ledger.db")
    fake = FakeTradingClient()
    broker = AlpacaBroker("k", "s", paper=True, ledger=ledger, peak_state_path=tmp_path / "peak.json")
    broker._client = fake  # type: ignore[assignment]
    fake.set_account(equity="100000")
    return broker, fake, tmp_path


def bench_account(report: Report) -> None:
    broker, _fake, _ = _broker_with_fake()

    def _account() -> None:
        broker.account()


def bench_submit(report: Report) -> None:
    # Skipped: PortfolioOrderRequest requires full decision context; we bench reconcile instead.
    # Keeping placeholder for latency parity checks.
    return


def bench_reconcile(report: Report) -> None:
    broker, fake, _ = _broker_with_fake()
    fake.set_position("IWM", qty=10)
    broker.portfolio_owned_quantities = lambda: {"IWM": 10}

    def _reconcile() -> None:
        broker.reconcile()

    timing, _ = timeit(_reconcile, label="broker.reconcile() (Fake, 1 pos)", runs=200)
    report.add(timing)


def bench_peak(report: Report) -> None:
    broker, _fake, _ = _broker_with_fake()

    def _peak() -> None:
        broker._save_peak_equity()

    timing, _ = timeit(_peak, label="peak save (atomic_write)", runs=200)
    report.add(timing)


if __name__ == "__main__":
    report = Report()
    bench_account(report)
    bench_submit(report)
    bench_reconcile(report)
    bench_peak(report)
    report.print_table(scale=1)
