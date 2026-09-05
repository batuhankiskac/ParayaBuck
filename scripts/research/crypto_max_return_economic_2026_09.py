"""Research-only economic adapter for the frozen C3 crypto comparison.

This module deliberately bypasses the product registry while reusing the native
PortfolioBacktestEngine, scanner universe, execution policy, and risk limits.
It does not run unless an explicitly supplied frozen manifest and receipt pass.
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

IDENTITY = "crypto_max_return_economic_2026_09"
WINDOWS = {
    "full": (datetime(2021, 4, 1, tzinfo=UTC), datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)),
    "recent": (datetime(2024, 1, 1, tzinfo=UTC), datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)),
}
COST_MULTIPLIERS = (1.0, 2.0, 3.0)
ARMS = ("max", "low_variance", "dollar_volume")
REQUIRED_SOURCE_PIN_PATHS = (
    "scripts/research/crypto_max_return_economic_2026_09.py",
    "scripts/research/crypto_max_return_novelty_2026_09.py",
    "scripts/research/crypto_max_return_novelty_runner_2026_09.py",
    "src/parayabuck/portfolio/crypto_low_variance_hysteresis.py",
    "src/parayabuck/backtest/portfolio_engine.py",
    "src/parayabuck/backtest/product.py",
    "src/parayabuck/backtest/universe.py",
    "src/parayabuck/execution/portfolio.py",
    "src/parayabuck/data/aggregator.py",
)


class CryptoLaggedDollarVolumeHysteresis(CryptoLowVarianceHysteresis1dV1):
    """Comparator: 20 completed daily median(close*volume), descending."""

    name = "crypto_lagged_dollar_volume_hyst4_research"
    version = "0.1.0-research"

    def _score_candidates(self, context: PortfolioContext):
        scored: list[tuple[float, str]] = []
        for symbol in sorted(context.history):
            if not context.in_universe(symbol):
                continue
            bars = tuple(context.history[symbol])[-20:]
            values = [bar.close * bar.volume for bar in bars if bar.close > 0 and bar.volume >= 0]
            if len(values) != 20 or not all(math.isfinite(value) for value in values):
                continue
            scored.append((float(statistics.median(values)), symbol))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(scored)


class _TraceHysteresisMixin:
    """Recorder only; inherited decide remains the portfolio authority."""

    def __init__(self, parameters: dict[str, object] | None = None) -> None:
        super().__init__(parameters)
        self.decision_traces: list[dict[str, object]] = []

    def decide(self, context: PortfolioContext):
        previous = tuple(sorted(symbol for symbol, weight in context.holdings.items() if weight > 1e-12))
        scores = self._score_candidates(context)
        portfolio = super().decide(context)
        rank_map = {symbol: index for index, (_score, symbol) in enumerate(scores, start=1)}
        targets = tuple(sorted(weight.symbol for weight in portfolio.weights))
        retained = tuple(sorted(symbol for symbol in previous if symbol in targets and rank_map.get(symbol, 999) <= self.RETENTION_RANK and context.in_universe(symbol)))
        self.decision_traces.append({
            "decision_timestamp": context.decision_timestamp,
            "scores": {symbol: score for score, symbol in scores},
            "ranks": rank_map,
            "eligible_symbols": sorted(symbol for symbol in context.history if context.in_universe(symbol)),
            "observed_symbols": sorted(context.history),
            "previous_held": previous,
            "retained": retained,
            "retention_rank": self.RETENTION_RANK,
            "target_symbols": targets,
        })
        return portfolio


class CryptoLowVarianceHysteresisTrace(_TraceHysteresisMixin, CryptoLowVarianceHysteresis1dV1):
    """Incumbent with an observation-only decision recorder."""

    name = CryptoLowVarianceHysteresis1dV1.name
    version = "1.0.0-research-trace"


class CryptoLaggedDollarVolumeHysteresisTrace(_TraceHysteresisMixin, CryptoLaggedDollarVolumeHysteresis):
    """Liquidity comparator with the same observation-only recorder."""

    version = "0.1.0-research-trace"


class RecordingPortfolioBacktestEngine(PortfolioBacktestEngine):
    """Research-only cash/mark recorder around native accounting."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.cash_ledger: list[dict[str, object]] = []

    def _record_holdings_snapshot(self, timestamp: datetime) -> None:
        super()._record_holdings_snapshot(timestamp)
        positions = {
            symbol: {"qty": position.qty, "mark": self._last_price.get(symbol, position.avg_price), "value": position.qty * self._last_price.get(symbol, position.avg_price)}
            for symbol, position in self._positions.items()
        }
        self.cash_ledger.append({"timestamp": timestamp.isoformat(), "cash": self._cash, "equity": self._equity(), "positions": positions, "fees_to_date": sum(fill.fee for fill in self._fills), "slippage_to_date": sum(fill.slippage_cost for fill in self._fills), "spread_to_date": sum(fill.spread_cost for fill in self._fills)})


@dataclass(frozen=True)
class EconomicCase:
    arm: str
    window: str
    cost_multiplier: float
    result: Any
    strategy: Any
    engine: RecordingPortfolioBacktestEngine
    universe_hash: str


def verify_frozen_inputs(manifest_path: Path, receipt_path: Path) -> dict[str, Any]:
    if not manifest_path.is_file() or not receipt_path.is_file():
        raise RuntimeError("BLOCKED_SELECTED_INPUT_MISSING")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "FROZEN_NOVELTY":
        raise RuntimeError("BLOCKED_MANIFEST_NOT_FROZEN")
    for relative, expected in manifest.get("source", {}).get("file_sha256", {}).items():
        actual = hashlib.sha256((REPO / str(relative)).read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"BLOCKED_SOURCE_HASH:{relative}")
    source_pins = manifest.get("source", {}).get("file_sha256", {})
    missing = [path for path in REQUIRED_SOURCE_PIN_PATHS if path not in source_pins]
    if missing:
        raise RuntimeError("BLOCKED_SOURCE_PIN_MISSING:" + ",".join(missing))
    if receipt.get("manifest_sha256") != manifest.get("manifest_sha256"):
        raise RuntimeError("BLOCKED_RECEIPT_MANIFEST_MISMATCH")
    for key, value in manifest.get("inputs", {}).items():
        if isinstance(value, Mapping) and value.get("path") and value.get("sha256"):
            actual = hashlib.sha256((REPO / str(value["path"])).read_bytes()).hexdigest()
            if actual != value["sha256"] or receipt.get("observed_sha256", {}).get(key) != actual:
                raise RuntimeError(f"BLOCKED_INPUT_HASH:{key}")
    pins = manifest.get("economic_provenance")
    if not isinstance(pins, Mapping):
        raise RuntimeError("BLOCKED_ECONOMIC_PROVENANCE_PINS_MISSING")
    for field in ("protocol_path", "protocol_sha256", "settings_path", "settings_sha256"):
        if not pins.get(field):
            raise RuntimeError(f"BLOCKED_ECONOMIC_PROVENANCE_PIN:{field}")
    for path_key, hash_key in (("protocol_path", "protocol_sha256"), ("settings_path", "settings_sha256")):
        actual = hashlib.sha256((REPO / str(pins[path_key])).read_bytes()).hexdigest()
        if actual != pins[hash_key]:
            raise RuntimeError(f"BLOCKED_ECONOMIC_PROVENANCE_HASH:{path_key}")
    return manifest


def evaluate_results(cells: Sequence[Mapping[str, Any]]) -> dict[str, object]:
    """Apply the frozen R1-R5 gates; missing evidence blocks, never passes."""
    keys = {(str(cell.get("arm")), str(cell.get("window")), float(cell.get("cost_multiplier", -1))) for cell in cells}
    expected = {(arm, window, cost) for arm in ARMS for window in WINDOWS for cost in COST_MULTIPLIERS}
    if keys != expected or len(cells) != 18:
        return {"status": "BLOCKED", "reason": "incomplete_or_duplicate_18_cell_matrix"}
    by = {(str(c["arm"]), str(c["window"]), float(c["cost_multiplier"])): c for c in cells}
    for cell in cells:
        try:
            validate_case_evidence(cell)
        except (TypeError, ValueError, RuntimeError) as exc:
            return {"status": "BLOCKED", "reason": str(exc)}
        metrics = cell.get("metrics")
        ledger = cell.get("cash_ledger")
        if not isinstance(metrics, Mapping) or not isinstance(ledger, list) or not ledger:
            return {"status": "BLOCKED", "reason": "missing_metrics_or_cash_ledger"}
        required = ("total_return", "cagr", "sharpe", "max_drawdown", "turnover")
        if any(key not in metrics or not math.isfinite(float(metrics[key])) for key in required):
            return {"status": "BLOCKED", "reason": "nonfinite_or_missing_required_metrics"}
        if any(not isinstance(row, Mapping) or any(not math.isfinite(float(row.get(key))) for key in ("cash", "equity")) for row in ledger):
            return {"status": "BLOCKED", "reason": "nonfinite_cash_ledger"}
    max_full_3 = by[("max", "full", 3.0)]["metrics"]; max_recent_3 = by[("max", "recent", 3.0)]["metrics"]
    inc_full_1 = by[("low_variance", "full", 1.0)]["metrics"]
    max_full_1 = by[("max", "full", 1.0)]["metrics"]
    max_full_2 = by[("max", "full", 2.0)]["metrics"]; max_recent_2 = by[("max", "recent", 2.0)]["metrics"]
    inc_full_3 = by[("low_variance", "full", 3.0)]["metrics"]; inc_recent_3 = by[("low_variance", "recent", 3.0)]["metrics"]
    liq_full_3 = by[("dollar_volume", "full", 3.0)]["metrics"]; liq_recent_3 = by[("dollar_volume", "recent", 3.0)]["metrics"]
    r1 = float(max_full_3["total_return"]) > 0 and float(max_recent_3["total_return"]) > 0
    r2 = float(max_full_1["sharpe"]) > float(inc_full_1["sharpe"]) or (float(max_full_1["max_drawdown"]) <= .9 * float(inc_full_1["max_drawdown"]) and float(max_full_1["cagr"]) > 0 and float(max_full_1["cagr"]) >= max(0, .9 * float(inc_full_1["cagr"])))
    r3 = float(max_full_3["max_drawdown"]) <= 1.5 * float(inc_full_3["max_drawdown"]) and float(max_recent_3["max_drawdown"]) <= 1.5 * float(inc_recent_3["max_drawdown"])
    r4 = float(max_full_2["total_return"]) >= 0 and float(max_recent_2["total_return"]) >= 0
    r5 = float(max_full_3["total_return"]) >= float(liq_full_3["total_return"]) and float(max_recent_3["total_return"]) >= float(liq_recent_3["total_return"])
    gates = {"R1": r1, "R2": r2, "R3": r3, "R4": r4, "R5": r5}
    return {"status": "RETAIN_FOR_RESEARCH" if all(gates.values()) else "REJECT", "gates": gates}


def validate_case_evidence(cell: Mapping[str, Any]) -> None:
    """Validate accounting evidence without inferring or repairing values."""
    curve = cell.get("equity_curve"); timestamps = cell.get("equity_timestamps")
    ledger = cell.get("cash_ledger"); fills = cell.get("fills"); fees = cell.get("fees")
    if not isinstance(curve, list) or not isinstance(timestamps, list) or not curve or len(curve) != len(timestamps):
        raise RuntimeError("BLOCKED_EVIDENCE_EQUITY_CURVE_ALIGNMENT")
    if any(not math.isfinite(float(value)) for value in curve):
        raise RuntimeError("BLOCKED_EVIDENCE_NONFINITE_EQUITY")
    if any(str(left) >= str(right) for left, right in zip(timestamps, timestamps[1:])):
        raise RuntimeError("BLOCKED_EVIDENCE_UNORDERED_EQUITY_TIMESTAMPS")
    if not isinstance(ledger, list) or not ledger:
        raise RuntimeError("BLOCKED_EVIDENCE_CASH_LEDGER_MISSING")
    for row in ledger:
        if not isinstance(row, Mapping):
            raise RuntimeError("BLOCKED_EVIDENCE_LEDGER_ROW")
        for key in ("cash", "equity", "fees_to_date", "slippage_to_date", "spread_to_date"):
            if key not in row or not math.isfinite(float(row[key])):
                raise RuntimeError(f"BLOCKED_EVIDENCE_NONFINITE_LEDGER:{key}")
        positions = row.get("positions")
        if not isinstance(positions, Mapping):
            raise RuntimeError("BLOCKED_EVIDENCE_POSITIONS_MISSING")
        marked = 0.0
        for position in positions.values():
            if not isinstance(position, Mapping):
                raise RuntimeError("BLOCKED_EVIDENCE_POSITION_ROW")
            for key in ("qty", "mark", "value"):
                if key not in position or not math.isfinite(float(position[key])) or float(position[key]) < 0:
                    raise RuntimeError(f"BLOCKED_EVIDENCE_NONFINITE_POSITION:{key}")
            marked += float(position["value"])
        if not math.isclose(float(row["cash"]) + marked, float(row["equity"]), rel_tol=1e-8, abs_tol=1e-5):
            raise RuntimeError("BLOCKED_EVIDENCE_CASH_POSITION_RECONCILIATION")
    if not isinstance(fills, list) or not isinstance(fees, Mapping):
        raise RuntimeError("BLOCKED_EVIDENCE_FILLS_OR_FEES_MISSING")
    fill_fee = 0.0
    for fill in fills:
        if not isinstance(fill, Mapping):
            raise RuntimeError("BLOCKED_EVIDENCE_FILL_ROW")
        for key in ("fee", "slippage_cost", "spread_cost", "qty", "fill_price"):
            if key not in fill or not math.isfinite(float(fill[key])):
                raise RuntimeError(f"BLOCKED_EVIDENCE_NONFINITE_FILL:{key}")
        fill_fee += float(fill["fee"])
    if "fee_total" not in fees or not math.isfinite(float(fees["fee_total"])) or not math.isclose(fill_fee, float(fees["fee_total"]), rel_tol=1e-8, abs_tol=1e-5):
        raise RuntimeError("BLOCKED_EVIDENCE_FEE_RECONCILIATION")
    if cell.get("terminal_policy") != "final_mark_no_forced_sale" or cell.get("forced_liquidation") is not False:
        raise RuntimeError("BLOCKED_EVIDENCE_TERMINAL_MARK_POLICY")
    if not math.isclose(float(curve[-1]), float(ledger[-1]["equity"]), rel_tol=1e-8, abs_tol=1e-5):
        raise RuntimeError("BLOCKED_EVIDENCE_FINAL_EQUITY_MISMATCH")


def build_strategy(arm: str):
    if arm == "max":
        return CryptoMaxReturnHysteresisResearch({"top_n": 2, "rebalance_hours": 96})
    if arm == "low_variance":
        return CryptoLowVarianceHysteresisTrace({"variance_lookback_days": 22, "top_n": 2, "rebalance_hours": 96})
    if arm == "dollar_volume":
        return CryptoLaggedDollarVolumeHysteresisTrace({"top_n": 2, "rebalance_hours": 96})
    raise ValueError(f"Unknown crypto economic arm: {arm}")


def run_case(config: Any, bars: list[Any], *, arm: str, window: str, cost_multiplier: float, initial_cash: float = 100_000.0) -> EconomicCase:
    start, end = WINDOWS[window]
    strategy = build_strategy(arm)
    daily_bars = aggregate_historical_bars(bars, "1d", require_complete=True)
    available = [bar for bar in bars if bar.market == Market.CRYPTO and bar.timeframe == "1h" and start <= bar.timestamp <= end]
    warmup = [bar for bar in bars if bar.market == Market.CRYPTO and bar.timeframe == "1h" and bar.timestamp < start]
    if not available:
        raise RuntimeError("BLOCKED_EMPTY_WINDOW")
    # Scanner consumes the raw 1h feed; the strategy consumes the separately
    # aggregated/shifted 1d signal series below. Passing 1d here would filter
    # every raw bar and yield an empty PIT universe.
    universe = build_product_universe(config, bars, market=Market.CRYPTO, end=end, timeframe="1h")
    engine = RecordingPortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=initial_cash,
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
    # End-of-window positions remain marked at the final available bar. The
    # protocol does not force a synthetic sell solely to close the report.
    # Daily labels are bucket starts. Shift only the signal series to bucket
    # end so the native event loop cannot ingest a day's close at its start;
    # execution remains on the unshifted hourly feed and next tradable bar.
    signal_bars = [
        Bar(**{**bar.__dict__, "timestamp": bar.timestamp + timedelta(days=1)})
        for bar in daily_bars
        if bar.timestamp <= end
    ]
    # ``PortfolioBacktestEngine.run`` ingests ``warmup`` into strategy history
    # before the first execution cycle. Include shifted completed D1 signals
    # there so the first due timestamp has its causal pre-start lookback; these
    # remain a separate 1d history key and do not replace hourly execution.
    warmup.extend(bar for bar in signal_bars if bar.timestamp < start)
    warmup.sort(key=lambda bar: (bar.timestamp, bar.symbol, bar.timeframe))
    result = engine.run(available, warmup=warmup, signal_bars=signal_bars, liquidate_at_end=False)
    return EconomicCase(arm, window, cost_multiplier, result, strategy, engine, hash_point_in_time_universe(universe))


def case_payload(case: EconomicCase) -> dict[str, object]:
    result = case.result
    required = {"total_return": result.total_return, "cagr": result.cagr, "sharpe": result.sharpe, "max_drawdown": result.max_drawdown}
    if any(not math.isfinite(float(value)) for value in required.values()):
        raise RuntimeError("BLOCKED_NONFINITE_METRIC")
    raw_traces = getattr(case.strategy, "decision_traces", [])
    traces = []
    for raw in raw_traces:
        trace = dict(raw)
        trace["scores"] = trace.get("scores", trace.get("candidate_scores", {}))
        traces.append(trace)
    raw_traces = getattr(case.strategy, "decision_traces", [])
    traces = []
    for raw in raw_traces:
        trace = dict(raw)
        trace["scores"] = trace.get("scores", trace.get("candidate_scores", {}))
        traces.append(trace)
    return {
        "arm": case.arm,
        "window": case.window,
        "cost_multiplier": case.cost_multiplier,
        "metrics": required,
        "universe_hash": case.universe_hash,
        "cash_accrual": "zero",
        "terminal_policy": "final_mark_no_forced_sale",
        "forced_liquidation": False,
        "virtual_holdings_only": False,
        "fills": [getattr(fill, "__dict__", str(fill)) for fill in result.fills],
        "equity_curve": list(result.equity_curve),
        "equity_timestamps": [timestamp.isoformat() for timestamp in result.curve_timestamps],
        "cash_ledger": case.engine.cash_ledger,
        "fees": {"fee_total": result.fee_total, "slippage_cost_total": result.slippage_cost_total, "spread_cost_total": result.spread_cost_total},
        "turnover": result.turnover,
        "strategy_traces": traces,
    }


def run_matrix(manifest_path: Path, receipt_path: Path, output_path: Path) -> dict[str, object]:
    manifest = verify_frozen_inputs(manifest_path, receipt_path)
    config = load_config(str(REPO / "config/settings.yaml"))
    record = manifest["inputs"]["bars_1h"]
    bars = load_csv_bars(REPO / str(record["path"]), default_market=Market.CRYPTO)
    results = []
    for window in WINDOWS:
        for arm in ("max", "low_variance", "dollar_volume"):
            for cost in COST_MULTIPLIERS:
                results.append(case_payload(run_case(config, bars, arm=arm, window=window, cost_multiplier=cost)))
    payload = {"status": "ECONOMIC_RESULTS_READY_FOR_REVIEW", "manifest_sha256": manifest["manifest_sha256"], "cell_count": len(results), "cells": results, "gate_evaluation": evaluate_results(results)}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str, allow_nan=False) + "\n", encoding="utf-8")
    return payload


__all__ = ["ARMS", "COST_MULTIPLIERS", "CryptoLaggedDollarVolumeHysteresis", "CryptoLaggedDollarVolumeHysteresisTrace", "CryptoLowVarianceHysteresisTrace", "EconomicCase", "RecordingPortfolioBacktestEngine", "WINDOWS", "build_strategy", "case_payload", "evaluate_results", "run_case", "run_matrix", "verify_frozen_inputs"]


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--validate-inputs", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not args.run and not args.validate_inputs:
        parser.error("one of --run or --validate-inputs is required")
    try:
        manifest = verify_frozen_inputs(args.manifest, args.receipt)
        if args.run:
            if args.output is None:
                parser.error("--run requires --output")
            result = run_matrix(args.manifest, args.receipt, args.output)
            print(json.dumps({"status": result["status"], "output": str(args.output)}, sort_keys=True))
        else:
            print(json.dumps({"status": "INPUTS_VERIFIED_NO_RUN", "manifest_sha256": manifest["manifest_sha256"]}, sort_keys=True))
        return 0
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
