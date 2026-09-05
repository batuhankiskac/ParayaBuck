"""Research-only seven-arm crypto portfolio screen.

The module is intentionally outside the product registry.  It reuses the
native product universe, PortfolioBacktestEngine, execution policy, and risk
limits.  It will not run until a caller supplies a manifest whose source,
input, protocol, and settings hashes pass validation.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
for path in (REPO, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine  # noqa: E402
from parayabuck.backtest.product import build_product_universe, load_csv_bars  # noqa: E402
from parayabuck.backtest.universe import hash_point_in_time_universe  # noqa: E402
from parayabuck.config import load_config  # noqa: E402
from parayabuck.core.models import Bar, Market  # noqa: E402
from parayabuck.data.aggregator import aggregate_historical_bars  # noqa: E402
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config  # noqa: E402
from parayabuck.portfolio import rebalance_policy_for  # noqa: E402
from parayabuck.portfolio.base import PortfolioContext  # noqa: E402
from parayabuck.portfolio.crypto_low_variance_hysteresis import CryptoLowVarianceHysteresis1dV1  # noqa: E402
from scripts.research.crypto_max_return_novelty_2026_09 import CryptoMaxReturnHysteresisResearch  # noqa: E402
from scripts.research.crypto_signed_risk_novelty_2026_09 import CryptoSignedRiskHysteresisResearch  # noqa: E402

IDENTITY = "portfolio_expansion_crypto_screen_2026_09"
WINDOWS = {
    "full": (datetime(2022, 1, 1, tzinfo=UTC), datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)),
    "recent": (datetime(2024, 1, 1, tzinfo=UTC), datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)),
}
COST_MULTIPLIERS = (1.0, 2.0, 3.0)
ARMS = ("low_variance22", "low_max22", "signed_risk22", "downside22", "reversal70", "high_proximity364", "low_disagreement5_45")
PROTOCOL_PATH = "docs/research/portfolio_expansion_crypto_screen_protocol_2026_09_05.md"
SETTINGS_PATH = "config/settings.yaml"
SOURCE_PINS = (
    "scripts/research/portfolio_expansion_crypto_screen_2026_09.py",
    "scripts/research/crypto_max_return_novelty_2026_09.py",
    "scripts/research/crypto_signed_risk_novelty_2026_09.py",
    "src/parayabuck/portfolio/crypto_low_variance_hysteresis.py",
    "src/parayabuck/backtest/portfolio_engine.py",
    "src/parayabuck/backtest/product.py",
    "src/parayabuck/backtest/universe.py",
    "src/parayabuck/execution/portfolio.py",
    "src/parayabuck/data/aggregator.py",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_manifest_digest(manifest: Mapping[str, Any]) -> str:
    body = dict(manifest)
    body.pop("manifest_sha256", None)
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_cell_ids() -> tuple[str, ...]:
    """Stable full matrix order used by manifests and deterministic shards."""
    return tuple(f"{window}/{arm}/{cost:g}x" for window in WINDOWS for arm in ARMS for cost in COST_MULTIPLIERS)


def assigned_cell_ids(shard_index: int = 0, shard_count: int = 1) -> tuple[str, ...]:
    if shard_count < 1 or shard_index < 0 or shard_index >= shard_count:
        raise ValueError("invalid shard index/count")
    return canonical_cell_ids()[shard_index::shard_count]


def _daily_window(context: PortfolioContext, symbol: str, prices: int) -> tuple[Any, ...] | None:
    bars = tuple(bar for bar in context.history.get(symbol, ()) if bar.timestamp <= context.decision_timestamp)
    if len(bars) < prices:
        return None
    window = bars[-prices:]
    if any(window[i].timestamp - window[i - 1].timestamp != timedelta(days=1) for i in range(1, len(window))):
        return None
    if not all(math.isfinite(bar.close) and bar.close > 0 for bar in window):
        return None
    return window


class _ScoreAdapter(CryptoLowVarianceHysteresis1dV1):
    """Common frozen HYST4/top2/96h policy with only score replaced."""

    default_rebalance_hours = 96
    rebalance_policy_declared = "hours:96"
    cadence_label = "96h"

    def _validate_parameters(self) -> None:
        if self.parameters.get("top_n", 2) != 2 or self.parameters.get("rebalance_hours", 96) != 96:
            raise ValueError("portfolio screen freezes top_n=2 and rebalance_hours=96")
        self.parameters["top_n"] = 2
        self.parameters["rebalance_hours"] = 96
        super()._validate_parameters()


class Downside22Adapter(_ScoreAdapter):
    name = "screen_downside22"
    version = "1.0.0-research"

    def _score_candidates(self, context: PortfolioContext):
        scored = []
        for symbol in sorted(context.history):
            if not context.in_universe(symbol):
                continue
            window = _daily_window(context, symbol, 23)
            if window is None:
                continue
            returns = [b.close / a.close - 1.0 for a, b in zip(window[:-1], window[1:], strict=True)]
            score = math.sqrt(statistics.fmean(min(value, 0.0) ** 2 for value in returns))
            if math.isfinite(score):
                scored.append((score, symbol))
        return tuple(sorted(scored, key=lambda item: (item[0], item[1])))


class Reversal70Adapter(_ScoreAdapter):
    name = "screen_reversal70"
    version = "1.0.0-research"

    def _score_candidates(self, context: PortfolioContext):
        scored = []
        for symbol in sorted(context.history):
            if not context.in_universe(symbol):
                continue
            window = _daily_window(context, symbol, 71)
            if window is None:
                continue
            score = window[-1].close / window[0].close - 1.0
            if math.isfinite(score) and score < 0.0:
                scored.append((score, symbol))
        return tuple(sorted(scored, key=lambda item: (item[0], item[1])))


class HighProximity364Adapter(_ScoreAdapter):
    name = "screen_high_proximity364"
    version = "1.0.0-research"

    def _score_candidates(self, context: PortfolioContext):
        scored = []
        for symbol in sorted(context.history):
            if not context.in_universe(symbol):
                continue
            window = _daily_window(context, symbol, 364)
            if window is None:
                continue
            score = window[-1].close / max(bar.close for bar in window)
            if math.isfinite(score):
                scored.append((score, symbol))
        return tuple(sorted(scored, key=lambda item: (-item[0], item[1])))


class LowDisagreementAdapter(_ScoreAdapter):
    name = "screen_low_disagreement5_45"
    version = "1.0.0-research"

    def _score_candidates(self, context: PortfolioContext):
        scored = []
        for symbol in sorted(context.history):
            if not context.in_universe(symbol):
                continue
            window = _daily_window(context, symbol, 50)
            if window is None:
                continue
            recent = [bar.volume for bar in window[-5:] if bar.volume > 0 and math.isfinite(bar.volume)]
            prior = [bar.volume for bar in window[:45] if bar.volume > 0 and math.isfinite(bar.volume)]
            if len(recent) != 5 or len(prior) != 45:
                continue
            recent_mean = statistics.fmean(recent)
            prior_mean = statistics.fmean(prior)
            if prior_mean <= 0 or not math.isfinite(recent_mean / prior_mean):
                continue
            scored.append((recent_mean / prior_mean, symbol))
        return tuple(sorted(scored, key=lambda item: (item[0], item[1])))


class TraceMixin:
    def __init__(self, parameters: dict[str, object] | None = None):
        super().__init__(parameters)
        self.decision_traces: list[dict[str, object]] = []

    def decide(self, context: PortfolioContext):
        portfolio = super().decide(context)
        self.decision_traces.append({
            "decision_timestamp": context.decision_timestamp.isoformat(),
            "target_symbols": sorted(weight.symbol for weight in portfolio.weights),
            "retained": sorted(portfolio.metadata.get("retained", ())),
        })
        return portfolio


class RecordingEngine(PortfolioBacktestEngine):
    def __init__(self, *args: object, **kwargs: object):
        super().__init__(*args, **kwargs)
        self.cash_ledger: list[dict[str, object]] = []

    def _record_holdings_snapshot(self, timestamp: datetime) -> None:
        super()._record_holdings_snapshot(timestamp)
        positions = {symbol: {"qty": p.qty, "mark": self._last_price.get(symbol, p.avg_price), "value": p.qty * self._last_price.get(symbol, p.avg_price)} for symbol, p in self._positions.items()}
        self.cash_ledger.append({"timestamp": timestamp.isoformat(), "cash": self._cash, "equity": self._equity(), "positions": positions, "fees_to_date": sum(f.fee for f in self._fills), "slippage_to_date": sum(f.slippage_cost for f in self._fills), "spread_to_date": sum(f.spread_cost for f in self._fills)})


@dataclass(frozen=True)
class ScreenCase:
    arm: str
    window: str
    cost_multiplier: float
    result: Any
    strategy: Any
    engine: RecordingEngine
    universe_hash: str


def validate_manifest(manifest_path: Path, *, require_canonical: bool = False) -> dict[str, Any]:
    if not manifest_path.is_file():
        raise RuntimeError("BLOCKED_MANIFEST_MISSING")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "FROZEN_SCREEN_PROTOCOL":
        raise RuntimeError("BLOCKED_MANIFEST_STATUS")
    source = manifest.get("source", {}).get("file_sha256", {})
    for path in SOURCE_PINS:
        expected = source.get(path)
        if not expected or sha256(REPO / path) != expected:
            raise RuntimeError(f"BLOCKED_SOURCE_HASH:{path}")
    inputs = manifest.get("inputs", {})
    for key in ("bars_1h",):
        row = inputs.get(key, {})
        if not row.get("path") or sha256(REPO / row["path"]) != row.get("sha256"):
            raise RuntimeError(f"BLOCKED_INPUT_HASH:{key}")
    protocol = manifest.get("protocol", {})
    if protocol.get("path") != PROTOCOL_PATH or sha256(REPO / PROTOCOL_PATH) != protocol.get("sha256"):
        raise RuntimeError("BLOCKED_PROTOCOL_HASH")
    settings = manifest.get("settings", {})
    if settings.get("path") != SETTINGS_PATH or sha256(REPO / SETTINGS_PATH) != settings.get("sha256"):
        raise RuntimeError("BLOCKED_SETTINGS_HASH")
    matrix = manifest.get("matrix", {})
    if tuple(matrix.get("arms", ())) != ARMS or tuple(matrix.get("windows", ())) != tuple(WINDOWS) or tuple(float(x) for x in matrix.get("cost_multipliers", ())) != COST_MULTIPLIERS or matrix.get("required_unique_cells") != 42 or tuple(matrix.get("canonical_cell_ids", ())) != canonical_cell_ids():
        raise RuntimeError("BLOCKED_MATRIX_CONTRACT")
    if require_canonical:
        declared_digest = manifest.get("manifest_sha256")
        if not isinstance(declared_digest, str) or declared_digest in {"", "DRAFT_COMPUTE_AFTER_FREEZE"}:
            raise RuntimeError("BLOCKED_MANIFEST_DIGEST_PLACEHOLDER")
        if declared_digest != canonical_manifest_digest(manifest):
            raise RuntimeError("BLOCKED_MANIFEST_DIGEST_MISMATCH")
    return manifest


def build_strategy(arm: str):
    classes = {
        "low_variance22": CryptoLowVarianceHysteresis1dV1,
        "low_max22": CryptoMaxReturnHysteresisResearch,
        "signed_risk22": CryptoSignedRiskHysteresisResearch,
        "downside22": Downside22Adapter,
        "reversal70": Reversal70Adapter,
        "high_proximity364": HighProximity364Adapter,
        "low_disagreement5_45": LowDisagreementAdapter,
    }
    try:
        return classes[arm]({"top_n": 2, "rebalance_hours": 96, **({"variance_lookback_days": 22} if arm == "low_variance22" else {})})
    except KeyError as exc:
        raise ValueError(f"Unknown screen arm: {arm}") from exc


def run_case(config: Any, bars: list[Any], *, arm: str, window: str, cost_multiplier: float, initial_cash: float = 100_000.0) -> ScreenCase:
    start, end = WINDOWS[window]
    strategy = build_strategy(arm)
    daily_bars = aggregate_historical_bars(bars, "1d", require_complete=True)
    available = [bar for bar in bars if bar.market == Market.CRYPTO and bar.timeframe == "1h" and start <= bar.timestamp <= end]
    warmup = [bar for bar in bars if bar.market == Market.CRYPTO and bar.timeframe == "1h" and bar.timestamp < start]
    if not available:
        raise RuntimeError("BLOCKED_EMPTY_WINDOW")
    universe = build_product_universe(config, bars, market=Market.CRYPTO, end=end, timeframe="1h")
    engine = RecordingEngine(strategy, rebalance_policy_for(strategy), starting_equity=initial_cash, slippage_bps=config.backtest.slippage_bps * cost_multiplier, spread_bps=config.backtest.spread_bps * cost_multiplier, commission=config.backtest.commission, crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps * cost_multiplier, benchmark_symbol="BTC/USD", universe=universe, universe_version=hash_point_in_time_universe(universe), risk_limits=portfolio_risk_limits_from_config(config.risk), maximum_allocation=config.strategy.crypto.maximum_allocation, execution_policy=PortfolioExecutionPolicy(rebalance_band_pct=config.execution.rebalance_band_pct, minimum_order_notional=config.execution.minimum_order_notional), state_path=None, cash_rate_annualized=0.0)
    signal_bars = [Bar(**{**bar.__dict__, "timestamp": bar.timestamp + timedelta(days=1)}) for bar in daily_bars if bar.timestamp <= end]
    warmup.extend(bar for bar in signal_bars if bar.timestamp < start)
    warmup.sort(key=lambda bar: (bar.timestamp, bar.symbol, bar.timeframe))
    result = engine.run(available, warmup=warmup, signal_bars=signal_bars, liquidate_at_end=False)
    return ScreenCase(arm, window, cost_multiplier, result, strategy, engine, hash_point_in_time_universe(universe))


def case_payload(case: ScreenCase) -> dict[str, object]:
    result = case.result
    metrics = {key: getattr(result, key) for key in ("total_return", "cagr", "sharpe", "max_drawdown", "turnover")}
    if any(not math.isfinite(float(value)) for value in metrics.values()):
        raise RuntimeError("BLOCKED_NONFINITE_METRIC")
    return {"arm": case.arm, "window": case.window, "cost_multiplier": case.cost_multiplier, "metrics": metrics, "universe_hash": case.universe_hash, "equity_curve": list(result.equity_curve), "equity_timestamps": [stamp.isoformat() for stamp in result.curve_timestamps], "cash_ledger": case.engine.cash_ledger, "fills": [getattr(fill, "__dict__", str(fill)) for fill in result.fills], "fees": {"fee_total": result.fee_total, "slippage_cost_total": result.slippage_cost_total, "spread_cost_total": result.spread_cost_total}, "terminal_policy": "final_mark_no_forced_sale", "forced_liquidation": False}


def run_matrix(manifest_path: Path, output_path: Path, *, shard_index: int = 0, shard_count: int = 1, checkpoint_dir: Path | None = None, invocation_receipt: Path | None = None) -> dict[str, object]:
    manifest = validate_manifest(manifest_path, require_canonical=True)
    if output_path.exists():
        raise RuntimeError("BLOCKED_OUTPUT_EXISTS")
    assigned = assigned_cell_ids(shard_index, shard_count)
    if invocation_receipt is not None:
        invocation_receipt.parent.mkdir(parents=True, exist_ok=True)
        invocation_receipt.write_text(json.dumps({"identity": IDENTITY, "manifest_sha256": manifest.get("manifest_sha256"), "shard_index": shard_index, "shard_count": shard_count, "assigned_cell_ids": assigned, "status": "STARTED"}, sort_keys=True) + "\n", encoding="utf-8")
    config = load_config(str(REPO / SETTINGS_PATH))
    bars = load_csv_bars(REPO / str(manifest["inputs"]["bars_1h"]["path"]), default_market=Market.CRYPTO)
    cells = []
    for cell_id in assigned:
        window, arm, cost_text = cell_id.split("/")
        cost = float(cost_text.removesuffix("x"))
        print(f"SCREEN CELL START arm={arm} window={window} cost={cost:g}x", file=sys.stderr, flush=True)
        payload_cell = case_payload(run_case(config, bars, arm=arm, window=window, cost_multiplier=cost))
        cells.append(payload_cell)
        if checkpoint_dir is not None:
            checkpoint_dir.mkdir(parents=True, exist_ok=True)
            checkpoint = checkpoint_dir / f"{shard_index:02d}_{cell_id.replace('/', '_')}.json"
            tmp = checkpoint.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload_cell, sort_keys=True, allow_nan=False, default=str) + "\n", encoding="utf-8")
            tmp.replace(checkpoint)
        print(f"SCREEN CELL END arm={arm} window={window} cost={cost:g}x", file=sys.stderr, flush=True)
    status = "SCREEN_RESULTS_READY_FOR_REVIEW" if shard_count == 1 else "SCREEN_SHARD_RESULTS_READY_FOR_REVIEW"
    payload = {"status": status, "manifest_sha256": manifest.get("manifest_sha256"), "shard_index": shard_index, "shard_count": shard_count, "assigned_cell_ids": assigned, "cell_count": len(cells), "cells": cells}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n", encoding="utf-8")
    if invocation_receipt is not None:
        invocation_receipt.write_text(json.dumps({"identity": IDENTITY, "manifest_sha256": manifest.get("manifest_sha256"), "shard_index": shard_index, "shard_count": shard_count, "assigned_cell_ids": assigned, "completed_cell_count": len(cells), "status": status, "output": str(output_path)}, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--validate-inputs", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--checkpoint-dir", type=Path)
    parser.add_argument("--invocation-receipt", type=Path)
    args = parser.parse_args(argv)
    if args.run == args.validate_inputs:
        parser.error("choose exactly one of --validate-inputs or --run")
    try:
        manifest = validate_manifest(args.manifest)
        if args.validate_inputs:
            print(json.dumps({"status": "INPUTS_VERIFIED_NO_RUN", "manifest_sha256": manifest.get("manifest_sha256"), "arms": ARMS, "cells": 42}, sort_keys=True))
        else:
            if args.output is None:
                parser.error("--run requires --output")
            result = run_matrix(args.manifest, args.output, shard_index=args.shard_index, shard_count=args.shard_count, checkpoint_dir=args.checkpoint_dir, invocation_receipt=args.invocation_receipt)
            print(json.dumps({"status": result["status"], "output": str(args.output)}, sort_keys=True))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


__all__ = ["ARMS", "COST_MULTIPLIERS", "Downside22Adapter", "HighProximity364Adapter", "LowDisagreementAdapter", "Reversal70Adapter", "ScreenCase", "WINDOWS", "assigned_cell_ids", "build_strategy", "canonical_cell_ids", "case_payload", "main", "run_case", "run_matrix", "validate_manifest"]

if __name__ == "__main__":
    raise SystemExit(main())
