"""Trace the frozen high_proximity364 full/1x path without changing decisions."""
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from parayabuck.backtest.product import build_product_universe, load_csv_bars  # noqa: E402
from parayabuck.backtest.universe import hash_point_in_time_universe  # noqa: E402
from parayabuck.config import load_config  # noqa: E402
from parayabuck.core.models import Bar, Market  # noqa: E402
from parayabuck.data.aggregator import aggregate_historical_bars  # noqa: E402
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config  # noqa: E402
from parayabuck.portfolio import rebalance_policy_for  # noqa: E402
from parayabuck.portfolio.base import PortfolioContext  # noqa: E402
from scripts.research import portfolio_expansion_crypto_screen_2026_09 as frozen  # noqa: E402

MISSION = "high_proximity364_trace_2026_09_05"
MANIFEST = REPO / "data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_attempt03_frozen.json"
DATA = REPO / "data/historical/crypto_expanded_1h.csv"
OUTPUT = REPO / "data/research/portfolio_expansion_crypto_screen_2026_09/attempt_01_trace/high_proximity364_full_1x_trace.json"
TRACE_MANIFEST = REPO / "data/research_manifests/high_proximity364_trace_2026_09_05_frozen.json"
INITIAL_CASH = 100_000.0
TRADE_SYMBOLS = tuple(
    "BTC/USD ETH/USD SOL/USD XRP/USD ADA/USD DOGE/USD AVAX/USD LINK/USD LTC/USD BCH/USD DOT/USD UNI/USD AAVE/USD ARB/USD FIL/USD RENDER/USD ONDO/USD HYPE/USD SHIB/USD GRT/USD CRV/USD PEPE/USD".split()
)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def validate_trace_manifest() -> dict[str, object]:
    manifest = json.loads(TRACE_MANIFEST.read_text())
    declared = manifest.get("manifest_sha256")
    canonical = dict(manifest)
    canonical.pop("manifest_sha256", None)
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if manifest.get("status") != "FROZEN_TRACE_CONTRACT" or declared != digest:
        raise RuntimeError("BLOCKED_TRACE_MANIFEST_DIGEST")
    pins = manifest["source_pins"]
    checks = {"trace_source_sha256": Path(__file__), "native_runner_sha256": REPO / "scripts/research/portfolio_expansion_crypto_screen_2026_09.py", "engine_sha256": REPO / "src/parayabuck/backtest/portfolio_engine.py", "product_sha256": REPO / "src/parayabuck/backtest/product.py", "hysteresis_sha256": REPO / "src/parayabuck/portfolio/crypto_low_variance_hysteresis.py", "execution_sha256": REPO / "src/parayabuck/execution/portfolio.py", "dataset_sha256": DATA, "frozen_manifest_raw_sha256": MANIFEST}
    for key, path in checks.items():
        if sha(path) != pins.get(key):
            raise RuntimeError(f"BLOCKED_TRACE_SOURCE_PIN:{key}")
    return manifest


def _snapshot(universe: object, timestamp: datetime) -> object | None:
    snapshots = getattr(universe, "snapshots", ())
    eligible = [s for s in snapshots if s.effective_at <= timestamp]
    return eligible[-1] if eligible else None


def _bar_diag(bars: tuple[Any, ...]) -> dict[str, object]:
    stamps = [bar.timestamp for bar in bars]
    gaps = sum(1 for left, right in zip(stamps, stamps[1:]) if right - left != timedelta(days=1))
    return {"count": len(bars), "first": stamps[0].isoformat() if stamps else None, "last": stamps[-1].isoformat() if stamps else None, "daily_gap_count": gaps}


class TraceHighProximity364(frozen.HighProximity364Adapter):
    """Pure forwarding observer; inherited scoring and portfolio mechanics remain authoritative."""

    def __init__(self, parameters: dict[str, object] | None = None) -> None:
        super().__init__(parameters)
        self.score_observations: list[dict[str, object]] = []
        self.decision_observations: list[dict[str, object]] = []
        self._last_score_observation: dict[str, object] | None = None

    def _score_candidates(self, context: PortfolioContext):  # type: ignore[override]
        ranked = super()._score_candidates(context)
        score_by = {symbol: float(score) for score, symbol in ranked}
        rank_by = {symbol: index for index, (_score, symbol) in enumerate(ranked, start=1)}
        diagnostics: dict[str, dict[str, object]] = {}
        for symbol in sorted(context.history):
            in_universe = context.in_universe(symbol)
            bars = tuple(context.history[symbol])
            if not in_universe:
                reason = "not_in_pit_universe"
            elif len(bars) < 364:
                reason = "insufficient_history"
            elif any(right.timestamp - left.timestamp != timedelta(days=1) for left, right in zip(bars[-364:], bars[-363:])):
                reason = "nonconsecutive_364d_window"
            elif not all(math.isfinite(bar.close) and bar.close > 0 for bar in bars[-364:]):
                reason = "invalid_close"
            else:
                reason = "eligible_and_complete"
            diagnostics[symbol] = {"in_universe": in_universe, "history": _bar_diag(bars), "reason": reason, "score": score_by.get(symbol), "rank": rank_by.get(symbol)}
        row = {"decision_timestamp": context.decision_timestamp.isoformat(), "provided_history": diagnostics, "ranked": [{"symbol": symbol, "score": score, "rank": rank_by[symbol]} for score, symbol in ranked]}
        self.score_observations.append(row)
        self._last_score_observation = row
        return ranked

    def decide(self, context: PortfolioContext):  # type: ignore[override]
        before = len(self.score_observations)
        target = super().decide(context)
        score = self._last_score_observation if len(self.score_observations) > before else None
        self.decision_observations.append({"timestamp": context.decision_timestamp.isoformat(), "held": sorted(symbol for symbol, qty in context.holdings.items() if qty > 0), "target": sorted(weight.symbol for weight in target.weights if weight.weight > 0), "target_weights": {weight.symbol: weight.weight for weight in target.weights}, "cash_weight": target.cash_weight, "rebalance_reason": str(target.rebalance_reason), "retained": sorted(target.metadata.get("retained", ())), "score_observation_index": len(self.score_observations) - 1 if score else None})
        return target


class TraceRecordingEngine(frozen.RecordingEngine):
    """Forwarding engine observer for native gate/decision classification."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.context_observations: list[dict[str, object]] = []
        self.cycle_observations: list[dict[str, object]] = []
        self.reason_counts: Counter[str] = Counter()

    def _build_context(self, timestamp: datetime):  # type: ignore[override]
        context = super()._build_context(timestamp)
        snapshot = _snapshot(context.universe, timestamp)
        self.context_observations.append({"timestamp": timestamp.isoformat(), "timeframe": context.timeframe, "strategy_declared_minimum_history_bars": self.strategy.minimum_history_bars, "engine_native_nonbenchmark_history_cap": 274, "benchmark_symbol": context.benchmark_symbol, "benchmark_exempt_from_native_cap": context.benchmark_symbol in context.history and len(context.history.get(context.benchmark_symbol, ())) >= self.strategy.minimum_history_bars, "provided_history_symbols": sorted(context.history), "provided_history": {symbol: _bar_diag(tuple(bars)) for symbol, bars in sorted(context.history.items())}, "held": sorted(symbol for symbol, qty in context.holdings.items() if qty > 0), "universe_version": context.universe_version, "scanner_snapshot": {"effective_at": snapshot.effective_at.isoformat(), "symbols": sorted(snapshot.symbols), "observation_set": list(snapshot.observation_set), "candidate_count": snapshot.candidate_count, "eliminated": snapshot.eliminated, "score_breakdown": snapshot.score_breakdown} if snapshot is not None else None})
        return context

    def _decide(self, timestamp: datetime) -> None:  # type: ignore[override]
        previous = self._previous_timestamp[self.strategy.market]
        scheduled = self.rebalance_policy.is_period_start(previous, timestamp)
        before_decisions = len(getattr(self.strategy, "decision_observations", ()))
        before_rebalances = len(self._rebalances)
        before_contexts = len(self.context_observations)
        super()._decide(timestamp)
        context = self.context_observations[-1] if len(self.context_observations) > before_contexts else None
        decisions = getattr(self.strategy, "decision_observations", ())
        called = len(decisions) > before_decisions
        if not scheduled and not called:
            reason = "no_rebalance_due"
        elif called:
            reason = "returned_zero_target" if not decisions[-1]["target"] else "returned_target"
        elif context is None:
            reason = "preflight_skip_before_context"
        else:
            expected = (context.get("scanner_snapshot") or {}).get("observation_set", [])
            provided = context.get("provided_history_symbols", [])
            reason = "preflight_skip_incomplete_observation" if any(symbol not in provided for symbol in expected) else "preflight_skip_or_support_false"
        self.reason_counts[reason] += 1
        if scheduled or reason != "no_rebalance_due":
            self.cycle_observations.append({"timestamp": timestamp.isoformat(), "scheduled": scheduled, "reason": reason, "context_built": context is not None, "strategy_called": called, "rebalance_count_delta": len(self._rebalances) - before_rebalances})


def _trace_case(config: Any, bars: list[Any]) -> tuple[dict[str, object], TraceRecordingEngine, TraceHighProximity364]:
    start, end = frozen.WINDOWS["full"]
    strategy = TraceHighProximity364({"top_n": 2, "rebalance_hours": 96})
    daily_bars = aggregate_historical_bars(bars, "1d", require_complete=True)
    available = [bar for bar in bars if bar.market == Market.CRYPTO and bar.timeframe == "1h" and start <= bar.timestamp <= end]
    warmup = [bar for bar in bars if bar.market == Market.CRYPTO and bar.timeframe == "1h" and bar.timestamp < start]
    universe = build_product_universe(config, bars, market=Market.CRYPTO, end=end, timeframe="1h")
    engine = TraceRecordingEngine(strategy, rebalance_policy_for(strategy), starting_equity=INITIAL_CASH, slippage_bps=config.backtest.slippage_bps, spread_bps=config.backtest.spread_bps, commission=config.backtest.commission, crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps, benchmark_symbol="BTC/USD", universe=universe, universe_version=hash_point_in_time_universe(universe), risk_limits=portfolio_risk_limits_from_config(config.risk), maximum_allocation=config.strategy.crypto.maximum_allocation, execution_policy=PortfolioExecutionPolicy(rebalance_band_pct=config.execution.rebalance_band_pct, minimum_order_notional=config.execution.minimum_order_notional), state_path=None, cash_rate_annualized=0.0)
    signal_bars = [Bar(**{**bar.__dict__, "timestamp": bar.timestamp + timedelta(days=1)}) for bar in daily_bars if bar.timestamp <= end]
    warmup.extend(bar for bar in signal_bars if bar.timestamp < start)
    warmup.sort(key=lambda bar: (bar.timestamp, bar.symbol, bar.timeframe))
    result = engine.run(available, warmup=warmup, signal_bars=signal_bars, liquidate_at_end=False)
    payload = {"metrics": {key: getattr(result, key) for key in ("total_return", "cagr", "sharpe", "max_drawdown", "turnover")}, "equity_curve": list(result.equity_curve), "equity_timestamps": [stamp.isoformat() for stamp in result.curve_timestamps], "fills": [getattr(fill, "__dict__", str(fill)) for fill in result.fills], "fees": {"fee_total": result.fee_total, "slippage_cost_total": result.slippage_cost_total, "spread_cost_total": result.spread_cost_total}, "cash_ledger": engine.cash_ledger, "terminal_policy": "final_mark_no_forced_sale", "forced_liquidation": False}
    return payload, engine, strategy


def tiny_check() -> dict[str, object]:
    start = datetime(2021, 1, 1, tzinfo=UTC)
    bars = tuple(Bar(timestamp=start + timedelta(days=i), symbol="BTC/USD", timeframe="1d", open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=1.0, market=Market.CRYPTO) for i in range(364))
    context = PortfolioContext(start + timedelta(days=363), Market.CRYPTO, "1d", {"BTC/USD": bars}, None, "tiny", INITIAL_CASH, {}, {"BTC/USD": 463.0}, "BTC/USD")
    strategy = TraceHighProximity364({"top_n": 2, "rebalance_hours": 96})
    ranked = strategy._score_candidates(context)
    if len(ranked) != 1 or strategy.score_observations[0]["provided_history"]["BTC/USD"]["reason"] != "eligible_and_complete":
        raise RuntimeError("TINY_TRACE_CHECK_FAILED")
    return {"status": "TINY_TRACE_CHECK_PASS", "ranked": ranked, "reason": strategy.score_observations[0]["provided_history"]["BTC/USD"]["reason"]}


def frozen_original() -> dict[str, object]:
    for path in sorted((REPO / "data/research/portfolio_expansion_crypto_screen_2026_09/attempt_03_canonical").glob("shard_*/economic_results.json")):
        payload = json.loads(path.read_text())
        for cell in payload.get("cells", []):
            if cell.get("arm") == "high_proximity364" and cell.get("window") == "full" and float(cell.get("cost_multiplier")) == 1.0:
                return cell
    raise RuntimeError("BLOCKED_ORIGINAL_PARITY_CELL_MISSING")


def parity_and_compact(payload: dict[str, object]) -> dict[str, object]:
    original = frozen_original()
    fields = ("equity_curve", "equity_timestamps", "fills", "cash_ledger", "fees", "metrics")
    def serialized(value: object) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    parity = {field: serialized(payload[field]) == serialized(original[field]) for field in fields}
    if not all(parity.values()):
        raise RuntimeError("TRACE_PARITY_INVALID:" + ",".join(field for field, passed in parity.items() if not passed))
    def digest(value: object) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    ledger = payload["cash_ledger"]
    payload["parity"] = {"status": "PARITY_PASS", "fields": parity, "original_cell": "full/high_proximity364/1x", "fills": len(payload["fills"]), "fee_total": payload["fees"]["fee_total"], "nav_observations": len(payload["equity_curve"]), "cash_ledger_observations": len(ledger), "equity_curve_sha256": digest(payload["equity_curve"]), "equity_timestamps_sha256": digest(payload["equity_timestamps"]), "cash_ledger_sha256": digest(ledger), "first_active_mark": next((row["timestamp"] for row in ledger if row.get("positions")), None), "last_active_mark": next((row["timestamp"] for row in reversed(ledger) if row.get("positions")), None), "terminal_cash": ledger[-1]["cash"], "terminal_positions": ledger[-1]["positions"]}
    payload.pop("equity_curve")
    payload.pop("equity_timestamps")
    payload.pop("cash_ledger")
    return payload


def main() -> None:
    manifest = validate_trace_manifest()
    print(json.dumps(tiny_check(), default=str), flush=True)
    config = load_config(str(REPO / "config/settings.yaml"))
    bars = load_csv_bars(DATA, default_market=Market.CRYPTO)
    payload, engine, strategy = _trace_case(config, bars)
    payload["trace"] = {"cycle_observations": engine.cycle_observations, "context_observations": engine.context_observations, "score_observations": strategy.score_observations, "decision_observations": strategy.decision_observations, "reason_counts": dict(engine.reason_counts)}
    payload["provenance"] = {"trace_manifest_sha256": manifest["manifest_sha256"], "manifest_sha256": sha(MANIFEST), "dataset_sha256": sha(DATA), "frozen_runner_sha256": sha(REPO / "scripts/research/portfolio_expansion_crypto_screen_2026_09.py"), "trace_source_sha256": sha(Path(__file__))}
    payload = parity_and_compact(payload)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n")
    print(json.dumps({"status": "TRACE_OUTPUT_READY_FOR_REVIEW", "output": str(OUTPUT), "sha256": sha(OUTPUT), "fills": len(payload["fills"]), "fees": payload["fees"], "scheduled_rows": sum(1 for row in engine.cycle_observations if row["scheduled"]), "reason_counts": payload["trace"]["reason_counts"]}), flush=True)


if __name__ == "__main__":
    main()
