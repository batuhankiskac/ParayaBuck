#!/usr/bin/env python3
"""Frozen, research-only equity V3 profit-protection experiment.

Candidate decisions consume completed closes and are submitted through
PortfolioBacktestEngine, whose orders fill at the next tradable open. Nothing
in this module is imported by production.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.core.models import Market
from parayabuck.portfolio.equity_residual_momentum_v3 import EquityResidualMomentum1dV3
from parayabuck.portfolio.models import RebalanceReason, TargetPortfolio, TargetWeight

REPO = Path(__file__).resolve().parents[2]
MISSION = "equity_profit_protection_2026_09"
MANIFEST = REPO / "data/research_manifests" / f"{MISSION}.json"
OUT = REPO / "data/research" / MISSION
PARAMS = {
    "formation_bars": 252,
    "skip_bars": 21,
    "beta_window_bars": 273,
    "residual_vol_window": 63,
    "top_n": 8,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
    "rebalance_frequency": "weekly",
}
CANDIDATES = ("E0_CONTROL", "E1_TP5_CLOSE", "E2_PROFIT_LOCK_50", "E3_PROFIT_ALPHA_CONFIRMED")


@dataclass
class EpisodeState:
    quantity: float
    basis: float
    armed: bool = False
    high_close: float | None = None
    breakdown_count: int = 0
    pending: bool = False
    activation_time: str | None = None

    def apply_buy(self, qty: float, price: float) -> None:
        """Use an authoritative fill and reset protection on every net increase."""
        total = self.quantity + qty
        self.basis = (self.quantity * self.basis + qty * price) / total
        self.quantity = total
        self.armed = False
        self.high_close = None
        self.breakdown_count = 0
        self.pending = False
        self.activation_time = None

    def apply_sell(self, qty: float) -> None:
        self.quantity = max(0.0, self.quantity - qty)
        if self.quantity == 0:
            self.armed = False
            self.high_close = None
            self.breakdown_count = 0
            self.pending = False
            self.activation_time = None

    def observe(
        self, close: float, candidate: str, *, qualified: bool = True, rank: int = 1, timestamp: str = ""
    ) -> bool:
        if self.pending or self.quantity <= 0:
            return False
        gain = close / self.basis - 1
        if candidate == "E1_TP5_CLOSE":
            self.pending = gain >= 0.05
            return self.pending
        if not self.armed:
            if gain >= 0.05:
                self.armed = True
                self.high_close = close
                self.activation_time = timestamp
            return False
        self.high_close = max(float(self.high_close), close)
        if candidate == "E2_PROFIT_LOCK_50":
            floor = max(self.basis, self.basis + 0.5 * (self.high_close - self.basis))
            self.pending = close <= floor
        elif candidate == "E3_PROFIT_ALPHA_CONFIRMED":
            self.breakdown_count = self.breakdown_count + 1 if (not qualified or rank > 12) else 0
            self.pending = self.breakdown_count >= 2
        return self.pending

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def restore(cls, value: dict[str, Any]) -> EpisodeState:
        return cls(**value)


class ProfitProtectionStrategy(EquityResidualMomentum1dV3):
    def __init__(self, candidate: str):
        super().__init__(dict(PARAMS))
        self.candidate = candidate
        self.states: dict[str, EpisodeState] = {}
        self._triggers: set[str] = set()

    def sync_authoritative_positions(self, positions: dict[str, tuple[float, float]]) -> None:
        """Synchronize from engine quantities/bases; external quantities never enter."""
        for symbol in list(self.states):
            if symbol not in positions:
                self.states.pop(symbol)
        for symbol, (qty, basis) in positions.items():
            old = self.states.get(symbol)
            if old is None:
                self.states[symbol] = EpisodeState(qty, basis)
            elif qty > old.quantity + 1e-9:
                old.quantity = qty
                old.basis = basis
                old.armed = False
                old.high_close = None
                old.breakdown_count = 0
                old.pending = False
            else:
                old.quantity = qty
                old.basis = basis

    def requires_intermediate_risk_rebalance(self, context) -> bool:  # type: ignore[override]
        if self.candidate == "E0_CONTROL":
            return False
        ranked, _ = self._score_candidates(context)  # type: ignore[attr-defined]
        ranks = {s: i + 1 for i, (_, s) in enumerate(ranked)}
        triggers = set()
        for sym, state in self.states.items():
            close = context.prices.get(sym)
            if close and state.observe(
                float(close),
                self.candidate,
                qualified=sym in ranks,
                rank=ranks.get(sym, 999),
                timestamp=context.decision_timestamp.isoformat(),
            ):
                triggers.add(sym)
        self._triggers = triggers
        return bool(triggers)

    def decide(self, context):  # type: ignore[override]
        if not self._triggers:
            return super().decide(context)
        triggers = set(self._triggers)
        self._triggers.clear()
        weights = []
        for sym, qty in context.holdings.items():
            if sym in triggers:
                continue
            price = context.prices.get(sym, 0.0)
            weight = qty * price / max(context.equity, 1.0)
            if weight > 0:
                weights.append(TargetWeight(sym, weight, "profit_protection_hold"))
        return TargetPortfolio(
            self.name,
            self.version,
            context.decision_timestamp,
            context.decision_timestamp,
            tuple(weights),
            max(0.0, 1 - sum(w.weight for w in weights)),
            RebalanceReason.SCHEDULED,
            context.universe_version,
            context.universe_version,
            self.parameter_tuple(),
            {"research_candidate": self.candidate, "triggered": sorted(triggers)},
        )


class ResearchEngine(PortfolioBacktestEngine):
    def _decide(self, timestamp):
        self.strategy.sync_authoritative_positions({s: (p.qty, p.avg_price) for s, p in self._positions.items()})
        super()._decide(timestamp)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize(r) -> dict[str, Any]:
    return {
        k: getattr(r, k)
        for k in (
            "total_return",
            "cagr",
            "annualized_volatility",
            "sharpe",
            "sortino",
            "max_drawdown",
            "max_drawdown_duration_days",
            "calmar",
            "worst_month_return",
            "worst_year_return",
            "average_cash_ratio",
            "turnover",
            "fee_total",
            "spread_cost_total",
            "slippage_cost_total",
            "rebalance_count",
        )
    } | {"average_exposure": 1 - r.average_cash_ratio, "fill_count": len(r.fills)}


def run_stage_one() -> dict[str, Any]:
    # Reuse the audited canonical loader/construction; only the strategy/engine are replaced.
    from scripts.research_v3_exit_latency import (
        EVAL_END,
        EVAL_START,
        RECENT_START,
        build_product_universe,
        load_config,
        load_csv_bars,
        load_csv_corporate_actions,
        make_engine,
    )

    cfg = load_config(str(REPO / "config/settings.yaml"))
    bars = load_csv_bars(REPO / "data/historical/equity_1200_2016_2026.csv", default_market=Market.EQUITY)
    actions = load_csv_corporate_actions(REPO / "data/historical/equity_1200_dividends.csv")
    universe = build_product_universe(bars, Market.EQUITY)
    output = {
        "mission": MISSION,
        "implementation_commit": __import__("subprocess").check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "windows": {},
    }
    for label, start in (("full", EVAL_START), ("recent", RECENT_START)):
        output["windows"][label] = {}
        for cid in CANDIDATES:
            strategy = ProfitProtectionStrategy(cid)
            base = make_engine(cfg, strategy, universe)
            engine = ResearchEngine(
                strategy,
                base.rebalance_policy,
                starting_equity=base.starting_equity,
                slippage_bps=base.slippage_bps,
                spread_bps=base.spread_bps,
                commission=base.commission,
                crypto_taker_fee_bps=base.crypto_taker_fee_bps,
                equity_taf_per_share=base.equity_taf_per_share,
                equity_taf_cap=base.equity_taf_cap,
                equity_cat_per_share=base.equity_cat_per_share,
                equity_sec_fee_rate=base.equity_sec_fee_rate,
                equity_use_effective_dated_fees=base.equity_use_effective_dated_fees,
                benchmark_symbol="SPY",
                universe=universe,
                universe_version=base.universe_version,
                risk_limits=base.risk_overlay.limits,
                maximum_allocation=0.55,
                execution_policy=base.execution_policy,
                enforce_drawdown_halt=False,
            )
            warm = [
                b for b in bars if b.timestamp < start and b.timestamp >= datetime(start.year - 2, 1, 1, tzinfo=UTC)
            ]
            active = [b for b in bars if start <= b.timestamp <= EVAL_END]
            result = engine.run(tuple(warm + active), corporate_actions=actions, evaluation_start=start)
            output["windows"][label][cid] = summarize(result)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "stage_one.json"
    path.write_text(json.dumps(output, indent=2, default=str) + "\n")
    (OUT / "artifact_hashes.json").write_text(json.dumps({"stage_one.json": sha(path)}, indent=2) + "\n")
    return output


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--stage-one", action="store_true")
    a = p.parse_args()
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["no_results_yet"] is True
    if a.stage_one:
        print(json.dumps(run_stage_one(), indent=2, default=str))


if __name__ == "__main__":
    main()
