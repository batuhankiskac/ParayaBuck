"""Golden parity reference: deterministic hash of canonical backtest outputs.

Runs equity v2 (2019-2021 window) and crypto v2 (full) backtests on the local
historical CSVs and hashes the authoritative outputs (equity curve, fills,
targets, universe history, summary economics). The optimization tournament
requires this hash to be UNCHANGED after Tier-1 optimizations.

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
CRYPTO_CSV = ROOT / "data" / "historical" / "crypto_18_1d_extended.csv"
CONFIG = ROOT / "config" / "settings.yaml"

EQUITY_START = "2019-01-01"
EQUITY_END = "2021-12-31"


def _sha(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _run_equity(config) -> dict[str, object]:
    bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
    run = run_product_backtest(
        config,
        bars,
        market=Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        start=datetime.fromisoformat(EQUITY_START).replace(tzinfo=UTC),
        end=datetime.fromisoformat(EQUITY_END).replace(tzinfo=UTC),
        output_dir=str(ROOT / "data" / "smoke" / "parity_equity"),
        persist_artifacts=False,
    )
    result = run.result
    summary = run.report.summary
    return {
        "strategy": "equity_residual_momentum_v2",
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
        "weight_history_hash": _sha(
            [
                (timestamp.isoformat(), weights)
                for timestamp, weights in result.weight_history
            ]
        ),
        "universe_history_hash": _sha(
            [
                (timestamp.isoformat(), sorted(symbols))
                for timestamp, symbols in result.universe_history
            ]
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


def _run_crypto(config) -> dict[str, object]:
    bars = load_csv_bars(CRYPTO_CSV, default_market=Market.CRYPTO)
    run = run_product_backtest(
        config,
        bars,
        market=Market.CRYPTO,
        strategy_name="crypto_low_variance_v2",
        output_dir=str(ROOT / "data" / "smoke" / "parity_crypto"),
        persist_artifacts=False,
    )
    result = run.result
    summary = run.report.summary
    return {
        "strategy": "crypto_low_variance_v2",
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
        "weight_history_hash": _sha(
            [
                (timestamp.isoformat(), weights)
                for timestamp, weights in result.weight_history
            ]
        ),
        "universe_history_hash": _sha(
            [
                (timestamp.isoformat(), sorted(symbols))
                for timestamp, symbols in result.universe_history
            ]
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
    crypto = _run_crypto(config)
    return {
        "head": _head_sha(),
        "equity": equity,
        "crypto": crypto,
        "combined_hash": _sha([equity, crypto]),
    }


def _head_sha() -> str:
    import subprocess

    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out")
    parser.add_argument("--check")
    args = parser.parse_args()
    if args.check:
        reference = json.loads(Path(args.check).read_text())
        current = collect()
        match = current["combined_hash"] == reference["combined_hash"]
        print(f"combined_hash: {current['combined_hash']}")
        print(f"reference:     {reference['combined_hash']}")
        print("PARITY MATCH" if match else "PARITY MISMATCH")
        if not match:
            for market in ("equity", "crypto"):
                for key in ("equity_curve_hash", "fills_hash", "weight_history_hash"):
                    before = reference[market][key]
                    after = current[market][key]
                    if before != after:
                        print(f"  {market}.{key} changed: {before} -> {after}")
        return 0 if match else 1
    payload = collect()
    print(json.dumps(payload, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
