"""Frozen six-cell research runner for corrected high-proximity history.

This runner intentionally leaves the original 42-cell screen immutable.  It
reuses its native engine, universe preparation, signal-bar shift, recording
engine and payload schema, changing only the candidate strategy class.
"""

from __future__ import annotations

import json
import math
from hashlib import sha256
from pathlib import Path
import sys
from datetime import timedelta
from typing import Any, Sequence

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from parayabuck.backtest.product import build_product_universe, load_csv_bars  # noqa: E402
from parayabuck.backtest.universe import hash_point_in_time_universe  # noqa: E402
from parayabuck.config import load_config  # noqa: E402
from parayabuck.core.models import Bar, Market  # noqa: E402
from parayabuck.data.aggregator import aggregate_historical_bars  # noqa: E402
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config  # noqa: E402
from parayabuck.portfolio import rebalance_policy_for  # noqa: E402
from scripts.research.high_proximity364_history_fixed_2026_09_05 import (  # noqa: E402
    HighProximity364HistoryFixedAdapter,
)
from scripts.research.portfolio_expansion_crypto_screen_2026_09 import (  # noqa: E402
    RecordingEngine,
    ScreenCase,
    WINDOWS,
    case_payload,
)


IDENTITY = "high_proximity364_history_fixed_2026_09"
ARM = "high_proximity364_history_fixed"
COST_MULTIPLIERS = (1.0, 2.0, 3.0)
SETTINGS_PATH = "config/settings.yaml"
INPUT_PATH = "data/historical/crypto_expanded_1h.csv"
PROTOCOL_PATH = "docs/research/high_proximity364_deep_assessment_protocol_2026_09_05.md"
MATRIX = tuple(f"{window}/{ARM}/{cost:g}x" for window in WINDOWS for cost in COST_MULTIPLIERS)


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def validate_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError("BLOCKED_MANIFEST_MISSING")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("status") != "FROZEN_CORRECTED_HIGH_PROXIMITY_PROTOCOL":
        raise RuntimeError("BLOCKED_MANIFEST_STATUS")
    inputs = manifest.get("inputs", {})
    row = inputs.get("bars_1h", {})
    if row.get("path") != INPUT_PATH or _sha(REPO / INPUT_PATH) != row.get("sha256"):
        raise RuntimeError("BLOCKED_INPUT_HASH")
    for key, expected_path in (("settings", SETTINGS_PATH), ("protocol", PROTOCOL_PATH)):
        item = manifest.get(key, {})
        if item.get("path") != expected_path or _sha(REPO / expected_path) != item.get("sha256"):
            raise RuntimeError(f"BLOCKED_{key.upper()}_HASH")
    sources = manifest.get("source", {})
    for source_path in (
        "scripts/research/high_proximity364_history_fixed_2026_09_05.py",
        "scripts/research/high_proximity364_history_fixed_runner_2026_09_05.py",
        "scripts/research/portfolio_expansion_crypto_screen_2026_09.py",
    ):
        if _sha(REPO / source_path) != sources.get(source_path):
            raise RuntimeError(f"BLOCKED_SOURCE_HASH:{source_path}")
    matrix = manifest.get("matrix", {})
    if tuple(matrix.get("cells", ())) != MATRIX or matrix.get("cell_count") != 6:
        raise RuntimeError("BLOCKED_MATRIX_CONTRACT")
    if manifest.get("manifest_sha256") in (None, "", "DRAFT_COMPUTE_AFTER_FREEZE"):
        raise RuntimeError("BLOCKED_MANIFEST_DIGEST_PLACEHOLDER")
    digest_payload = dict(manifest)
    digest_payload.pop("manifest_sha256", None)
    canonical = sha256(json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if manifest.get("manifest_sha256") != canonical:
        raise RuntimeError("BLOCKED_MANIFEST_DIGEST_MISMATCH")
    return manifest


def run_case(config: Any, bars: list[Any], *, window: str, cost_multiplier: float) -> ScreenCase:
    start, end = WINDOWS[window]
    strategy = HighProximity364HistoryFixedAdapter({"top_n": 2, "rebalance_hours": 96})
    daily_bars = aggregate_historical_bars(bars, "1d", require_complete=True)
    available = [bar for bar in bars if bar.market == Market.CRYPTO and bar.timeframe == "1h" and start <= bar.timestamp <= end]
    warmup = [bar for bar in bars if bar.market == Market.CRYPTO and bar.timeframe == "1h" and bar.timestamp < start]
    if not available:
        raise RuntimeError("BLOCKED_EMPTY_WINDOW")
    universe = build_product_universe(config, bars, market=Market.CRYPTO, end=end, timeframe="1h")
    engine = RecordingEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=100_000.0,
        slippage_bps=config.backtest.slippage_bps * cost_multiplier,
        spread_bps=config.backtest.spread_bps * cost_multiplier,
        commission=config.backtest.commission,
        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps * cost_multiplier,
        benchmark_symbol="BTC/USD",
        universe=universe,
        universe_version=hash_point_in_time_universe(universe),
        risk_limits=portfolio_risk_limits_from_config(config.risk),
        maximum_allocation=config.strategy.crypto.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        state_path=None,
        cash_rate_annualized=0.0,
    )
    signal_bars = [Bar(**{**bar.__dict__, "timestamp": bar.timestamp + timedelta(days=1)}) for bar in daily_bars if bar.timestamp <= end]
    warmup.extend(bar for bar in signal_bars if bar.timestamp < start)
    warmup.sort(key=lambda bar: (bar.timestamp, bar.symbol, bar.timeframe))
    result = engine.run(available, warmup=warmup, signal_bars=signal_bars, liquidate_at_end=False)
    return ScreenCase(ARM, window, cost_multiplier, result, strategy, engine, hash_point_in_time_universe(universe))


def run_matrix(manifest_path: Path, output_path: Path) -> dict[str, object]:
    manifest = validate_manifest(manifest_path)
    if output_path.exists():
        raise RuntimeError("BLOCKED_OUTPUT_EXISTS")
    config = load_config(str(REPO / SETTINGS_PATH))
    bars = load_csv_bars(REPO / INPUT_PATH, default_market=Market.CRYPTO)
    cells: list[dict[str, object]] = []
    for cell_id in MATRIX:
        window, _arm, cost_text = cell_id.split("/")
        cost = float(cost_text.removesuffix("x"))
        payload = case_payload(run_case(config, bars, window=window, cost_multiplier=cost))
        cells.append(payload)
    result = {
        "status": "CORRECTED_HIGH_PROXIMITY_RESULTS_READY_FOR_REVIEW",
        "identity": IDENTITY,
        "manifest_sha256": manifest["manifest_sha256"],
        "cell_count": len(cells),
        "cells": cells,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n", encoding="utf-8")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--validate-inputs", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.validate_inputs:
        validate_manifest(args.manifest)
        print("CORRECTED_HIGH_PROXIMITY_INPUTS_VALID")
        return 0
    if args.run:
        if args.output is None:
            parser.error("--run requires --output")
        run_matrix(args.manifest, args.output)
        return 0
    parser.error("select --validate-inputs or --run")


if __name__ == "__main__":
    raise SystemExit(main())
