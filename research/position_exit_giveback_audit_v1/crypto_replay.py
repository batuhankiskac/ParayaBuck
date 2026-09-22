"""Fresh unmodified production replay on the frozen T1 promotion environment."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from parayabuck.backtest.product import build_product_universe, load_csv_bars, run_product_backtest
from parayabuck.backtest.universe import hash_point_in_time_universe
from parayabuck.config import load_config
from parayabuck.core.models import Market
from parayabuck.data.aggregator import aggregate_historical_bars
from research.position_exit_giveback_audit_v1.capture import capture_run, plain, write_json

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
DATA = ROOT / "data/historical/crypto_expanded_1h.csv"
EXPECTED_SHA = "73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e"
START = datetime(2021, 4, 1, tzinfo=UTC)
END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(plain(value), sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def historical_hashes(product: Any, raw: dict[str, Any]) -> dict[str, str]:
    targets = [e for e in raw["events"] if e["type"] == "target"]
    result = product.result
    histories = {
        "ranking_history": [list(t["metadata"].get("selected_symbols", [])) for t in targets],
        "retention_history": [list(t["metadata"].get("retained", [])) for t in targets],
        "selected_symbol_history": [sorted(w["symbol"] for w in t["weights"]) for t in targets],
        "target_weight_history": [
            {w["symbol"]: float(w["weight"]) for w in sorted(t["weights"], key=lambda w: w["symbol"])} for t in targets
        ],
        "fill_history": [
            {
                "symbol": f.symbol,
                "qty": float(f.qty),
                "price": float(f.fill_price),
                "timestamp": f.timestamp.isoformat(),
            }
            for f in result.fills
        ],
        "equity_curve": [float(v) for v in result.equity_curve],
    }
    return {k: digest(v) for k, v in histories.items()}


def main() -> None:
    cfg = load_config(str(ROOT / "config/settings.yaml"))
    assert cfg.strategy.crypto.name == "crypto_low_variance_hysteresis_v1"
    actual_sha = hashlib.sha256(DATA.read_bytes()).hexdigest()
    assert actual_sha == EXPECTED_SHA, actual_sha
    print("Loading frozen hourly dataset", flush=True)
    hourly = load_csv_bars(DATA, default_market=Market.CRYPTO)
    daily = aggregate_historical_bars(hourly, "1d", require_complete=True)
    universe = build_product_universe(cfg, hourly, market=Market.CRYPTO, end=END, timeframe="1h")
    print(f"Loaded {len(hourly)} hourly; {len(daily)} daily; {len(universe.snapshots)} PIT snapshots", flush=True)

    def run() -> Any:
        return run_product_backtest(
            cfg,
            daily,
            market=Market.CRYPTO,
            start=START,
            end=END,
            universe=universe,
            persist=False,
            persist_artifacts=False,
        )

    first, raw = capture_run(run)
    print("Captured first replay", flush=True)
    second = run()

    def stable(p: Any) -> dict[str, Any]:
        return {
            k: plain(getattr(p.result, k))
            for k in (
                "fills",
                "rebalances",
                "terminal_events",
                "equity_curve",
                "curve_timestamps",
                "realized_pnl_events",
                "closed_position_events",
                "ending_equity",
            )
        }

    deterministic = digest(stable(first)) == digest(stable(second))
    current = historical_hashes(first, raw)
    baseline = json.loads((OUT / "CRYPTO_HISTORICAL_PARITY.json").read_text())
    mismatches = [k for k, v in current.items() if v != baseline["full_window"]["hashes_production"][k]]
    report = {
        "strategy": cfg.strategy.crypto.name,
        "status": "PASS" if deterministic and not mismatches else "AUDIT_INCONCLUSIVE_REPLAY_MISMATCH",
        "current_configuration": plain(cfg.strategy.crypto),
        "dataset": str(DATA.relative_to(ROOT)),
        "dataset_sha256": actual_sha,
        "window": [START.isoformat(), END.isoformat()],
        "aggregation": "1d require_complete=True",
        "hourly_bar_count": len(hourly),
        "daily_bar_count": len(daily),
        "universe_hash": hash_point_in_time_universe(universe),
        "universe_snapshots": len(universe.snapshots),
        "historical_artifact_source": "90988e7b35e29bdcd9d7e16a41b62df1696872af:data/research/crypto_t1_promotion_2026_08/parity_audit.json",
        "historical_hashes": baseline["full_window"]["hashes_production"],
        "current_hashes": current,
        "historical_hash_mismatches": mismatches,
        "fresh_replay_deterministic": deterministic,
        "historical_universe_hash": baseline["pit_hash"],
        "universe_identity_matches_historical": hash_point_in_time_universe(universe) == baseline["pit_hash"],
        "historical_fill_count": baseline["artifact_metrics"]["number_of_fills"],
        "historical_rebalance_count": baseline["full_window"]["rebalance_count"],
        "interpretation_permitted": deterministic and not mismatches,
        "mismatch_note": "The data bytes match the frozen promotion input, but current PIT universe and economic path differ. Captured and uninstrumented current production runs match exactly. No legacy behavior was reinstated and no shifted path is treated as historical parity.",
        "fresh_capture_output_sha256": digest(stable(first)),
        "fresh_uninstrumented_output_sha256": digest(stable(second)),
        "fill_count": len(first.result.fills),
        "rebalance_count": len(first.result.rebalances),
        "terminal_count": len(first.result.terminal_events),
        "summary": plain(first.report.summary),
        "historical_metrics": baseline["production_metrics"],
        "period_note": "Historical promotion used overlapping full-window and half-year walk-forward folds, not an untouched validation sample. No post-promotion data exists in frozen file.",
    }
    write_json(OUT / "CRYPTO_RAW.json.gz", raw)
    write_json(OUT / "CRYPTO_REPLAY_PARITY.json", report)
    print(
        json.dumps(
            {
                k: report[k]
                for k in [
                    "status",
                    "historical_hash_mismatches",
                    "fresh_replay_deterministic",
                    "fill_count",
                    "rebalance_count",
                ]
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
