#!/usr/bin/env python3
"""Offline, predeclared execution-model robustness research.

This module is deliberately outside ``src/``.  It provides the only two
structural perturbations absent from the product engine: initial eligible-bar
latency and a decision-time frozen liquidity reference.  With both disabled,
the adapter delegates to the production mechanics unchanged.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import research_v3_equity_roadmap as equity_roadmap  # noqa: E402

from parayabuck.backtest.portfolio_engine import (  # noqa: E402
    OrderSide,
    PortfolioBacktestEngine,
    PortfolioBacktestResult,
    PortfolioOrder,
)
from parayabuck.backtest.product import build_product_universe, load_csv_bars  # noqa: E402
from parayabuck.backtest.terminal_aware_engine import TerminalAwareResearchEngine  # noqa: E402
from parayabuck.backtest.universe import hash_point_in_time_universe  # noqa: E402
from parayabuck.config import Config, load_config  # noqa: E402
from parayabuck.core.models import Bar, Market  # noqa: E402
from parayabuck.data.aggregator import aggregate_historical_bars  # noqa: E402
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config  # noqa: E402
from parayabuck.portfolio import rebalance_policy_for  # noqa: E402
from parayabuck.portfolio.crypto_low_variance_hysteresis import CryptoLowVarianceHysteresis1dV1  # noqa: E402

MISSION = "execution_model_robustness_backtest_2026_09"
MANIFEST = REPO / "data/research_manifests" / f"{MISSION}.json"
OUTPUT = REPO / "data/research" / MISSION
CRYPTO_DATASET = REPO / "data/historical/crypto_expanded_1h.csv"
CRYPTO_START = datetime(2021, 4, 1, tzinfo=equity_roadmap.UTC)
CRYPTO_END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=equity_roadmap.UTC)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalise(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if dataclasses.is_dataclass(value):
        return _normalise(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _normalise(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    return value


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(_normalise(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class _ExecutionStressMixin:
    """Research-only perturbations layered over the unmodified engine path."""

    def __init__(
        self,
        *args: object,
        additional_eligible_bar_delay: int = 0,
        frozen_decision_liquidity: bool = False,
        regulatory_fee_multiplier: float = 1.0,
        **kwargs: object,
    ) -> None:
        if additional_eligible_bar_delay < 0:
            raise ValueError("additional_eligible_bar_delay must be non-negative")
        super().__init__(*args, **kwargs)  # type: ignore[misc]
        self.additional_eligible_bar_delay = additional_eligible_bar_delay
        self.frozen_decision_liquidity = frozen_decision_liquidity
        self.regulatory_fee_multiplier = regulatory_fee_multiplier
        self._remaining_initial_delay: dict[tuple[str, datetime], int] = {}
        self.superseded_residuals: list[dict[str, object]] = []

    @staticmethod
    def _parent_key(order: PortfolioOrder) -> tuple[str, datetime]:
        return order.client_order_id, order.decision_timestamp

    def _known_average_volume(self, order: PortfolioOrder, timeframe: str) -> float:  # type: ignore[override]
        if not self.frozen_decision_liquidity:
            return super()._known_average_volume(order, timeframe)  # type: ignore[misc]
        history = self._execution_history.get((order.symbol, timeframe), ())
        known = [item.volume for item in history if item.timestamp <= order.decision_timestamp and item.volume > 0]
        window = known[-self.liquidity_lookback_bars :]
        return sum(window) / len(window) if window else 0.0

    def _fill_side(self, bar: Bar, side: OrderSide) -> None:  # type: ignore[override]
        # A zero-volume execution bar still consumes latency: delay is a timing
        # perturbation, not a hidden liquidity rule.  Replacing created_at
        # makes the production ``created_at >= bar.timestamp`` gate skip this
        # bar, then base mechanics handle the next eligible one unchanged.
        if self.additional_eligible_bar_delay:
            adjusted: list[PortfolioOrder] = []
            for order in self._pending:
                if order.symbol == bar.symbol and order.side == side and order.created_at < bar.timestamp:
                    key = self._parent_key(order)
                    remaining = self._remaining_initial_delay.setdefault(key, self.additional_eligible_bar_delay)
                    if remaining:
                        self._remaining_initial_delay[key] = remaining - 1
                        order = dataclasses.replace(order, created_at=bar.timestamp)
                adjusted.append(order)
            self._pending = adjusted
        super()._fill_side(bar, side)  # type: ignore[misc]

    def _apply_target(self, target: object) -> None:  # type: ignore[override]
        decision_at = getattr(target, "decision_timestamp")
        for order in self._pending:
            if order.decision_timestamp < decision_at:
                price = self._last_price.get(order.symbol, 0.0)
                self.superseded_residuals.append(
                    {"client_order_id": order.client_order_id, "symbol": order.symbol, "side": order.side.value,
                     "decision_timestamp": order.decision_timestamp, "superseded_at": decision_at,
                     "remaining_qty": order.qty, "remaining_notional": order.qty * price}
                )
        super()._apply_target(target)  # type: ignore[misc]

    def _equity_fee(self, side: OrderSide, quantity: float, price: float, trade_date: date) -> float:  # type: ignore[override]
        return super()._equity_fee(side, quantity, price, trade_date) * self.regulatory_fee_multiplier  # type: ignore[misc]


class ExecutionStressEngine(_ExecutionStressMixin, PortfolioBacktestEngine):
    """Research adapter for non-terminal sleeves."""


class TerminalExecutionStressEngine(_ExecutionStressMixin, TerminalAwareResearchEngine):
    """Research adapter preserving the canonical equity T1 terminal semantics."""


def _scenario(manifest: Mapping[str, object], name: str) -> Mapping[str, object]:
    scenarios = manifest["scenarios"]
    if not isinstance(scenarios, Mapping) or name not in scenarios or not isinstance(scenarios[name], Mapping):
        raise ValueError(f"undeclared scenario: {name}")
    return scenarios[name]


def load_manifest() -> dict[str, object]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("declared_before_results") is not True or manifest.get("result_status") != "NOT_RUN":
        raise RuntimeError("manifest is not a clean pre-result declaration")
    return manifest


def verify_inputs(manifest: Mapping[str, object]) -> None:
    product = manifest["product_contract"]
    if not isinstance(product, Mapping):
        raise RuntimeError("missing product contract")
    config = load_config(str(REPO / "config/settings.yaml"))
    if config.strategy.equity.name != product["equity"]["strategy"] or config.strategy.crypto.name != product["crypto"]["strategy"]:  # type: ignore[index]
        raise RuntimeError("configured strategies drifted from predeclaration")
    for sleeve in ("equity", "crypto"):
        inputs = manifest["dataset_contract"][sleeve]  # type: ignore[index]
        for record in inputs.values():  # type: ignore[union-attr]
            if isinstance(record, Mapping) and "path" in record and sha256(REPO / str(record["path"])) != record["sha256"]:
                raise RuntimeError(f"{sleeve} input hash mismatch: {record['path']}")


def _equity_engine(config: Config, window: object, terminal_map: Mapping[str, object], scenario: Mapping[str, object], *, adapter: bool):
    strategy = equity_roadmap.build_strategy("control")
    kwargs: dict[str, object] = dict(
        strategy=strategy, rebalance_policy=rebalance_policy_for(strategy),
        terminal_map=dict(terminal_map), policy="T1", starting_equity=config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps * float(scenario["cost_multiplier"]),
        spread_bps=config.backtest.spread_bps * float(scenario["cost_multiplier"]),
        commission=config.backtest.commission * float(scenario["cost_multiplier"]),
        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps * float(scenario["cost_multiplier"]),
        equity_taf_per_share=config.backtest.equity_taf_per_share, equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share, equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees, benchmark_symbol="SPY",
        risk_limits=portfolio_risk_limits_from_config(config.risk), maximum_allocation=config.strategy.equity.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(rebalance_band_pct=config.execution.rebalance_band_pct, minimum_order_notional=config.execution.minimum_order_notional),
        enforce_drawdown_halt=False, universe=window.universe, universe_version=window.universe_version,
        max_participation_pct=float(scenario["max_participation_pct"]),
    )
    if not adapter:
        return TerminalAwareResearchEngine(**kwargs)
    return TerminalExecutionStressEngine(
        **kwargs, additional_eligible_bar_delay=int(scenario["additional_eligible_bar_delay"]),
        frozen_decision_liquidity=scenario["capacity_reference"] == "decision_timestamp_only",
        regulatory_fee_multiplier=float(scenario["cost_multiplier"]),
    )


def _crypto_engine(config: Config, bars_1h: list[Bar], scenario: Mapping[str, object], *, adapter: bool):
    bars_1d = aggregate_historical_bars(bars_1h, "1d", require_complete=True)
    universe = build_product_universe(config, bars_1h, market=Market.CRYPTO, end=CRYPTO_END, timeframe="1h")
    strategy = CryptoLowVarianceHysteresis1dV1(dict(config.strategy.crypto.parameters))
    kwargs: dict[str, object] = dict(
        strategy=strategy, rebalance_policy=rebalance_policy_for(strategy), starting_equity=config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps * float(scenario["cost_multiplier"]),
        spread_bps=config.backtest.spread_bps * float(scenario["cost_multiplier"]),
        commission=config.backtest.commission * float(scenario["cost_multiplier"]),
        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps * float(scenario["cost_multiplier"]),
        equity_taf_per_share=config.backtest.equity_taf_per_share, equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share, equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees, benchmark_symbol="BTC/USD",
        risk_limits=portfolio_risk_limits_from_config(config.risk), maximum_allocation=config.strategy.crypto.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(rebalance_band_pct=config.execution.rebalance_band_pct, minimum_order_notional=config.execution.minimum_order_notional),
        universe=universe, universe_version=hash_point_in_time_universe(universe), max_participation_pct=float(scenario["max_participation_pct"]),
    )
    engine = (ExecutionStressEngine if adapter else PortfolioBacktestEngine)(
        **kwargs,
        **({"additional_eligible_bar_delay": int(scenario["additional_eligible_bar_delay"]), "frozen_decision_liquidity": scenario["capacity_reference"] == "decision_timestamp_only", "regulatory_fee_multiplier": float(scenario["cost_multiplier"])} if adapter else {}),
    )
    filtered = [bar for bar in bars_1d if CRYPTO_START <= bar.timestamp <= CRYPTO_END]
    warmup = [bar for bar in bars_1d if bar.timestamp < CRYPTO_START]
    return engine, filtered, warmup


def result_components(result: PortfolioBacktestResult, engine: object, universe: object) -> dict[str, str]:
    return {"fills": canonical_digest(result.fills), "rebalances": canonical_digest(result.rebalances),
            "curve": canonical_digest({"timestamps": result.curve_timestamps, "equity": result.equity_curve}),
            "terminal_events": canonical_digest(getattr(engine, "terminal_events_fired", ())), "universe": canonical_digest(universe.to_dict())}


def _metrics(result: PortfolioBacktestResult, engine: object) -> dict[str, object]:
    curve = list(result.equity_curve)
    returns = [curve[index] / curve[index - 1] - 1 for index in range(1, len(curve)) if curve[index - 1] > 0]
    peak = max(curve[0], 1.0) if curve else 1.0
    max_dd = 0.0
    for value in curve:
        peak = max(peak, value)
        max_dd = max(max_dd, (peak - value) / peak if peak else 0.0)
    stdev = statistics.stdev(returns) if len(returns) > 1 else 0.0
    sharpe = statistics.fmean(returns) / stdev * math.sqrt(252) if stdev else 0.0
    total_return = curve[-1] / result.starting_equity - 1 if curve else 0.0
    fills = list(result.fills)
    parents: dict[tuple[str, datetime], list[object]] = defaultdict(list)
    for fill in fills:
        parents[(fill.client_order_id, fill.decision_timestamp)].append(fill)
    completion_bars = [len({fill.timestamp for fill in group}) for group in parents.values()]
    superseded = list(getattr(engine, "superseded_residuals", ()))
    intended = sum(fill.qty * fill.reference_price for fill in fills) + sum(float(item["remaining_notional"]) for item in superseded)
    return {"total_return": total_return, "sharpe": sharpe, "max_drawdown": max_dd,
            "average_exposure": statistics.fmean(result.exposures) if result.exposures else 0.0,
            "average_cash": statistics.fmean(result.cash_curve) if result.cash_curve else 0.0,
            "turnover": result.turnover, "modeled_costs": sum(fill.fee + fill.slippage_cost + fill.spread_cost for fill in fills),
            "fill_count": len(fills), "total_order_count": len(parents), "capacity_bound_order_count": sum(value > 1 for value in completion_bars),
            "completion_bars": {"average": statistics.fmean(completion_bars) if completion_bars else 0.0, "p50": statistics.median(completion_bars) if completion_bars else 0.0, "p90": sorted(completion_bars)[max(0, math.ceil(len(completion_bars) * .9) - 1)] if completion_bars else 0, "first_bar_fraction": sum(value == 1 for value in completion_bars) / len(completion_bars) if completion_bars else 0.0, "three_plus_fraction": sum(value >= 3 for value in completion_bars) / len(completion_bars) if completion_bars else 0.0},
            "superseded_parent_fraction": len({(item["client_order_id"], item["decision_timestamp"]) for item in superseded}) / len(parents) if parents else 0.0,
            "superseded_notional_fraction": sum(float(item["remaining_notional"]) for item in superseded) / intended if intended else 0.0}


def c0_parity() -> dict[str, object]:
    """Run an adapter-off-vs-C0 differential parity gate; raises on drift."""
    manifest = load_manifest(); verify_inputs(manifest); config = load_config(str(REPO / "config/settings.yaml")); c0 = _scenario(manifest, "C0")
    equity_bars = equity_roadmap.load_bars(equity_roadmap.DATASET_V5)
    terminal_map, _ = equity_roadmap.load_lifetimes(); actions = equity_roadmap.load_corporate_actions_all()
    window = equity_roadmap.prepare_window(config, equity_bars, key="execution-c0", start=None, end=equity_roadmap.EVAL_END)
    base = _equity_engine(config, window, terminal_map, c0, adapter=False); stressed = _equity_engine(config, window, terminal_map, c0, adapter=True)
    base_result = base.run(window.filtered, warmup=window.warmup, liquidate_at_end=True, corporate_actions=list(actions), prepared_inputs=window.prepared, numeric_history=window.numeric_history)
    stressed_result = stressed.run(window.filtered, warmup=window.warmup, liquidate_at_end=True, corporate_actions=list(actions), prepared_inputs=window.prepared, numeric_history=window.numeric_history)
    equity_equal = result_components(base_result, base, window.universe) == result_components(stressed_result, stressed, window.universe)
    crypto_1h = load_csv_bars(CRYPTO_DATASET, default_market=Market.CRYPTO)
    crypto_base, filtered, warmup = _crypto_engine(config, crypto_1h, c0, adapter=False); crypto_stressed, _, _ = _crypto_engine(config, crypto_1h, c0, adapter=True)
    crypto_base_result = crypto_base.run(filtered, warmup=warmup); crypto_stressed_result = crypto_stressed.run(filtered, warmup=warmup)
    crypto_equal = result_components(crypto_base_result, crypto_base, crypto_base.universe) == result_components(crypto_stressed_result, crypto_stressed, crypto_stressed.universe)
    if not equity_equal or not crypto_equal:
        raise RuntimeError(f"C0 parity failed: equity={equity_equal}, crypto={crypto_equal}")
    return {"equity": result_components(base_result, base, window.universe), "crypto": result_components(crypto_base_result, crypto_base, crypto_base.universe)}


def _red_flags(control: Mapping[str, object], stressed: Mapping[str, object]) -> list[str]:
    flags: list[str] = []
    if float(control["total_return"]) > 0 and float(stressed["total_return"]) < 0:
        flags.append("positive_return_becomes_negative")
    if float(stressed["sharpe"]) < 0:
        flags.append("sharpe_below_zero")
    if float(control["max_drawdown"]) > 0 and float(stressed["max_drawdown"]) > 1.5 * float(control["max_drawdown"]):
        flags.append("max_drawdown_over_1_5x_control")
    if float(control["average_exposure"]) > 0 and float(stressed["average_exposure"]) < 0.8 * float(control["average_exposure"]):
        flags.append("exposure_drop_over_20pct")
    if float(stressed["superseded_parent_fraction"]) > 0.2:
        flags.append("superseded_parent_orders_over_20pct")
    if float(stressed["superseded_notional_fraction"]) > 0.25:
        flags.append("superseded_notional_over_25pct")
    return flags


def run_sleeves(output: Path) -> dict[str, object]:
    """Execute all fixed sleeve scenarios after C0 parity.

    It intentionally does not synthesize a combined curve.  The repository
    lacks a shared-account offline event loop, and independent sleeve curves
    cannot evidence cross-sleeve cash drag or capacity crowding.
    """
    manifest = load_manifest(); verify_inputs(manifest)
    parity = c0_parity()
    config = load_config(str(REPO / "config/settings.yaml"))
    equity_bars = equity_roadmap.load_bars(equity_roadmap.DATASET_V5)
    terminal_map, _ = equity_roadmap.load_lifetimes(); actions = equity_roadmap.load_corporate_actions_all()
    window = equity_roadmap.prepare_window(config, equity_bars, key="execution-stress", start=None, end=equity_roadmap.EVAL_END)
    crypto_1h = load_csv_bars(CRYPTO_DATASET, default_market=Market.CRYPTO)
    sleeves: dict[str, dict[str, dict[str, object]]] = {"equity": {}, "crypto": {}}
    for name in manifest["scenarios"]:  # type: ignore[union-attr]
        scenario = _scenario(manifest, name)
        equity_engine = _equity_engine(config, window, terminal_map, scenario, adapter=True)
        equity_result = equity_engine.run(window.filtered, warmup=window.warmup, liquidate_at_end=True, corporate_actions=list(actions), prepared_inputs=window.prepared, numeric_history=window.numeric_history)
        sleeves["equity"][name] = _metrics(equity_result, equity_engine)
        crypto_engine, filtered, warmup = _crypto_engine(config, crypto_1h, scenario, adapter=True)
        crypto_result = crypto_engine.run(filtered, warmup=warmup)
        sleeves["crypto"][name] = _metrics(crypto_result, crypto_engine)
    flags = {
        sleeve: {name: _red_flags(records["C0"], records[name]) for name in ("CAP2", "LAT1", "COMBO_MEDIUM")}
        for sleeve, records in sleeves.items()
    }
    classifications = {
        sleeve: ("BACKTEST_EXECUTION_FRAGILE" if sum(bool(value) for value in per_scenario.values()) >= 2 else "BACKTEST_EXECUTION_ROBUST")
        for sleeve, per_scenario in flags.items()
    }
    payload: dict[str, object] = {
        "mission": MISSION, "predeclaration_sha256": sha256(MANIFEST), "c0_parity": parity,
        "sleeves": sleeves, "red_flags": flags, "sleeve_classifications": classifications,
        "combined": {"status": "INCONCLUSIVE", "reason": "no research-only shared-account multi-market event loop; independent sleeve curves are not substituted"},
        "overall_classification": "INCONCLUSIVE", "paper_live_transferability": "UNKNOWN_COST_TRANSFERABILITY",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(_normalise(payload), indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-predeclaration", action="store_true")
    parser.add_argument("--c0-parity", action="store_true")
    parser.add_argument("--run-sleeves", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT / "sleeve_results.json")
    args = parser.parse_args()
    manifest = load_manifest(); verify_inputs(manifest)
    if args.run_sleeves:
        print(json.dumps(run_sleeves(args.output), indent=2, sort_keys=True))
    elif args.c0_parity:
        print(json.dumps(c0_parity(), indent=2, sort_keys=True))
    else:
        print(json.dumps({"mission": MISSION, "predeclaration_sha256": sha256(MANIFEST), "status": "READY_FOR_C0_PARITY"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
