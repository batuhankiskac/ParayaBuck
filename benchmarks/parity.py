"""Deterministic parity reference for canonical backtest outputs.

Runs the equity leveraged trend v1 backtest (holding QQQ, 2019-2021 window)
on the local historical CSV and hashes the authoritative outputs (equity curve,
fills, targets, universe history, summary economics). The reference hash is
expected to remain stable when benchmarked code changes.

No reference fixture is committed. The checked-in ``parity_guard.json`` was a
frozen fixture of a superseded unified-equity pipeline (different dataset and
no ``combined_hash`` key), so ``--check`` against it was a false assurance; it
was removed on 2026-10-01. Generate the reference from the same checkout, the
same dataset and the same HEAD you intend to guard, and keep the result with
the change it guards. ``--check`` refuses to run against a missing, unreadable
or key-less reference instead of passing silently.

The dataset (``data/historical/equity_1200_2016_2026.csv``) is gitignored and
not part of the checkout; the tool exits with an explicit message when it is
absent. Nothing here reaches the network.

Usage:
    python benchmarks/parity.py                # print hashes
    python benchmarks/parity.py --out <file>   # write reference JSON
    python benchmarks/parity.py --check <file> # compare against reference
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parayabuck.backtest.product import load_csv_bars, run_product_backtest
from parayabuck.config import load_config
from parayabuck.core.models import Market

ROOT = Path(__file__).resolve().parents[1]
EQUITY_CSV = ROOT / "data" / "historical" / "equity_1200_2016_2026.csv"
CONFIG = ROOT / "config" / "settings.yaml"

EQUITY_START = "2019-01-01"
EQUITY_END = "2021-12-31"


def _require_dataset() -> None:
    if not EQUITY_CSV.exists():
        raise SystemExit(
            f"parity dataset missing: {EQUITY_CSV} is gitignored and not committed. "
            "Restore it (Alpaca SIP historical download) before running the parity reference."
        )


def load_reference(path: Path) -> dict[str, object]:
    """Read a reference fixture, refusing missing or superseded ones."""
    if not path.exists():
        raise SystemExit(
            f"parity reference missing: {path}. Generate one with "
            "`python benchmarks/parity.py --out <file>` on the checkout and dataset you guard."
        )
    try:
        reference = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"parity reference unreadable: {path} is not valid JSON ({exc}).") from exc
    missing = [key for key in ("combined_hash", "equity") if key not in reference]
    if missing:
        raise SystemExit(
            f"parity reference {path} is stale: missing {', '.join(missing)}. "
            "Regenerate it with `python benchmarks/parity.py --out <file>`; a reference from a "
            "different pipeline is not evidence for this one."
        )
    return reference


def _sha(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _run_equity(config) -> dict[str, object]:
    bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
    run = run_product_backtest(
        config,
        bars,
        market=Market.EQUITY,
        strategy_name="equity_leveraged_trend_v1",
        # The dataset has no QLD; hold QQQ so the run trades.
        parameters={"hold_symbol": "QQQ", "vol_target": 0.35},
        start=datetime.fromisoformat(EQUITY_START).replace(tzinfo=UTC),
        end=datetime.fromisoformat(EQUITY_END).replace(tzinfo=UTC),
        output_dir=str(ROOT / "data" / "smoke" / "parity_equity"),
        persist_artifacts=False,
    )
    result = run.result
    summary = run.report.summary
    return {
        "strategy": "equity_leveraged_trend_v1",
        "equity_curve_hash": _sha(list(result.equity_curve)),
        "curve_points": len(result.equity_curve),
        "fills_hash": _sha(
            [
                {
                    "id": fill.client_order_id,
                    "symbol": fill.symbol,
                    "qty": fill.qty,
                    "fill_price": fill.fill_price,
                    "side": fill.side.value,
                    "timestamp": fill.timestamp.isoformat(),
                    "fee": fill.fee,
                    "decision_timestamp": fill.decision_timestamp.isoformat(),
                }
                for fill in result.fills
            ]
        ),
        "fill_count": len(result.fills),
        "weight_history_hash": _sha([(timestamp.isoformat(), weights) for timestamp, weights in result.weight_history]),
        "universe_history_hash": _sha(
            [(timestamp.isoformat(), sorted(symbols)) for timestamp, symbols in result.universe_history]
        ),
        "economics": {
            "net_return": summary["net_return"],
            "max_drawdown": summary["max_drawdown"],
            "turnover": summary["turnover"],
            "fee_total": summary["fee_total"],
            "rebalance_count": summary["rebalance_count"],
            "average_holdings_count": summary["average_holdings_count"],
        },
    }


def collect() -> dict[str, object]:
    config = load_config(str(CONFIG))
    equity = _run_equity(config)
    return {
        "head": _head_sha(),
        "equity": equity,
        "combined_hash": _sha([equity]),
    }


def _head_sha() -> str:
    import subprocess

    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out")
    parser.add_argument("--check")
    args = parser.parse_args()
    if args.check:
        reference = load_reference(Path(args.check))
        _require_dataset()
        current = collect()
        match = current["combined_hash"] == reference["combined_hash"]
        print(f"combined_hash: {current['combined_hash']}")
        print(f"reference:     {reference['combined_hash']}")
        print("PARITY MATCH" if match else "PARITY MISMATCH")
        if not match:
            for key in ("equity_curve_hash", "fills_hash", "weight_history_hash"):
                before = reference["equity"][key]
                after = current["equity"][key]
                if before != after:
                    print(f"  equity.{key} changed: {before} -> {after}")
        return 0 if match else 1
    _require_dataset()
    payload = collect()
    print(json.dumps(payload, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
