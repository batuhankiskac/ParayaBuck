#!/usr/bin/env python3
"""Production-policy simulation — equity hardening mission 2026-08, Phase 5.

The research (alpha) backtest records portfolio-drawdown breaches as SHADOW
events so the curve keeps trading. Production behaves differently: the
monotonic drawdown peak latches entries off until an operator starts a new
risk epoch. This script quantifies what historical operation under the REAL
5% policy would have looked like, under PREDECLARED operator reset policies
(see data/research/equity_hardening_2026_08/predeclared.json):

    P0 never reset            P1 reset after 10 healthy sessions
    P2 next rebalance after   P3 fixed 20-trading-day delay
       drawdown recovered

Daily-loss latch semantics are already enforced in both curves by the shared
overlay; only the drawdown latch differs between alpha and production runs.
No future information is used: each policy acts only on state observable at
the decision timestamp.

Usage:
    python scripts/research_production_policy.py [--smoke]
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime
from pathlib import Path

from research_helpers import run_identity

from parayabuck.backtest.portfolio_engine import (
    PortfolioBacktestEngine,
    PortfolioBacktestResult,
)
from parayabuck.backtest.product import (
    build_product_universe,
    load_csv_bars,
    load_csv_corporate_actions,
)
from parayabuck.backtest.universe import hash_point_in_time_universe
from parayabuck.config import load_config
from parayabuck.core.models import Market
from parayabuck.execution.portfolio import (
    PortfolioExecutionPolicy,
    PortfolioRiskLimits,
    portfolio_risk_limits_from_config,
)
from parayabuck.portfolio import rebalance_policy_for
from parayabuck.portfolio.equity_residual_momentum import EquityResidualMomentum1dV2

REPO = Path(__file__).resolve().parents[1]
EQUITY_CSV = REPO / "data/historical/equity_1200_2016_2026.csv"
DIVIDENDS_CSV = REPO / "data/historical/equity_1200_dividends.csv"
OUT_DIR = REPO / "data/research/equity_hardening_2026_08"

_FROZEN: dict[str, object] = {
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


class ProductionPolicyEngine(PortfolioBacktestEngine):
    """Alpha engine + enforced drawdown halt + a predeclared operator-reset
    policy. Mirrors runtime semantics:

    * breach (drawdown >= limit) sets the latch; while latched the overlay
      caps every target at current weight (SELL-only);
    * ``reset_peak_equity()`` is the research mirror of the operator's
      verified risk-epoch reset (rebase peak to current equity);
    * the daily-loss limit needs no modeling here beyond the overlay: it is
      session-scoped and already enforced on decision days in both curves.
    """

    def __init__(
        self,
        *args: object,
        policy_id: str,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.policy_id = policy_id
        self.open_halt_episode_cycles = 0
        self._latched = False
        self._halt_started_at: datetime | None = None
        self._healthy_streak = 0
        self._cycles_since_halt = 0
        self._policy_previous_equity: float | None = None
        self.halts: list[dict[str, object]] = []
        self.resets: list[dict[str, object]] = []

    # -- policy hooks -------------------------------------------------------

    def _is_breached(self) -> bool:
        limits = self.risk_overlay.limits if self.risk_overlay is not None else PortfolioRiskLimits()
        peak = self._peak_equity
        return peak > 0 and (peak - self._equity()) / peak >= limits.drawdown_halt

    def _risk_epoch_policy(self, timestamp: datetime) -> None:
        breached = self._is_breached()
        equity = self._equity()
        flat_or_up = self._policy_previous_equity is None or equity >= self._policy_previous_equity
        self._policy_previous_equity = equity
        if breached and not self._latched:
            self._latched = True
            self._halt_started_at = timestamp
            self._healthy_streak = 0
            self._cycles_since_halt = 0
            self.halts.append({"at": timestamp.isoformat()})
        if self._latched:
            self._cycles_since_halt += 1
            self.open_halt_episode_cycles = self._cycles_since_halt
        else:
            return
        if self.policy_id == "P0":
            return
        if self.policy_id == "P1":
            # Healthy session = up-or-flat close while latched; any down
            # session restarts the count. Ten consecutive healthy sessions
            # (~two weeks) justify an operator restart.
            if flat_or_up:
                self._healthy_streak += 1
                if self._healthy_streak >= 10:
                    self._operator_reset(timestamp, "P1_10_healthy_sessions")
            else:
                self._healthy_streak = 0
        elif self.policy_id == "P2":
            previous = self._previous_timestamp[self.strategy.market]
            boundary = self.rebalance_policy.is_period_start(previous, timestamp)
            if boundary and not self._is_breached():
                self._operator_reset(timestamp, "P2_recovered_at_rebalance")
        elif self.policy_id == "P3":
            if self._cycles_since_halt >= 20:  # ~20 trading days on 1d bars
                self._operator_reset(timestamp, "P3_fixed_20d_delay")

    def _operator_reset(self, timestamp: datetime, reason: str) -> None:
        started = self._halt_started_at
        baseline = self.reset_peak_equity()
        duration_cycles = self._cycles_since_halt
        self.resets.append(
            {
                "at": timestamp.isoformat(),
                "reason": reason,
                "baseline_equity": round(baseline, 2),
                "latched_cycles": duration_cycles,
                "halt_started_at": started.isoformat() if started else None,
            }
        )
        self._latched = False
        self._halt_started_at = None
        self._healthy_streak = 0
        self._cycles_since_halt = 0
        self.open_halt_episode_cycles = 0


def _policy_metrics(result: PortfolioBacktestResult, engine: ProductionPolicyEngine) -> dict[str, object]:
    latched_cycles = sum(int(reset.get("latched_cycles", 0)) for reset in engine.resets)
    durations = [int(reset["latched_cycles"]) for reset in engine.resets]
    open_cycles = int(getattr(engine, "open_halt_episode_cycles", 0) or 0)
    if open_cycles:
        # The still-open final episode (e.g. P0 never resets) IS part of the
        # operational cost: count it and report it explicitly.
        durations.append(open_cycles)
        latched_cycles += open_cycles
    median_duration = statistics.median(durations) if durations else 0.0
    return {
        "open_halt_episode": open_cycles > 0,
        "open_halt_episode_cycles": open_cycles,
        "return": round(result.total_return, 6),
        "cagr": round(result.cagr, 6),
        "sharpe": round(result.sharpe, 6),
        "max_drawdown": round(result.max_drawdown, 6),
        "calmar": round(result.calmar, 6),
        "turnover": round(result.turnover, 6),
        "fees_total": round(result.slippage_cost_total + result.spread_cost_total + result.fee_total, 2),
        "avg_exposure": round(1.0 - result.average_cash_ratio, 4),
        "avg_cash_ratio": round(result.average_cash_ratio, 4),
        "avg_holdings": round(result.average_holdings_count, 3),
        "halt_count": len(engine.halts),
        "reset_count": len(engine.resets),
        "total_latched_cycles_estimate": latched_cycles,
        "median_halt_duration_cycles": median_duration,
        "halts": engine.halts[:50],
        "resets": engine.resets[:50],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true")
    arguments = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config()
    print("loading dataset...", flush=True)
    bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
    actions = load_csv_corporate_actions(DIVIDENDS_CSV)
    universe = build_product_universe(config, bars, market=Market.EQUITY)
    strategy = EquityResidualMomentum1dV2(dict(_FROZEN))
    limits = portfolio_risk_limits_from_config(config.risk)

    available = [bar for bar in bars if bar.market == Market.EQUITY and bar.timeframe == "1d"]
    warmup = []  # full window from inception; warmup handled via history length

    def build(policy_id: str) -> ProductionPolicyEngine:
        return ProductionPolicyEngine(
            strategy,
            rebalance_policy_for(strategy),
            starting_equity=config.backtest.initial_cash,
            slippage_bps=config.backtest.slippage_bps,
            spread_bps=config.backtest.spread_bps,
            commission=config.backtest.commission,
            crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps,
            equity_taf_per_share=config.backtest.equity_taf_per_share,
            equity_taf_cap=config.backtest.equity_taf_cap,
            equity_cat_per_share=config.backtest.equity_cat_per_share,
            equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
            equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
            benchmark_symbol="SPY",
            universe=universe,
            universe_version=hash_point_in_time_universe(universe),
            risk_limits=limits,
            maximum_allocation=config.strategy.equity.maximum_allocation,
            execution_policy=PortfolioExecutionPolicy(
                rebalance_band_pct=config.execution.rebalance_band_pct,
                minimum_order_notional=config.execution.minimum_order_notional,
            ),
            enforce_drawdown_halt=True,
            policy_id=policy_id,
        )

    # Alpha reference: identical run with the SHADOW halt default.
    print("alpha reference run...", flush=True)
    alpha_engine = build("P0")
    alpha_engine.enforce_drawdown_halt = False
    alpha = alpha_engine.run(available, warmup=warmup, corporate_actions=actions)

    suffix = "_smoke" if arguments.smoke else ""
    artifacts: dict[str, object] = {"alpha_shadow": _policy_metrics(alpha, alpha_engine)}
    for policy_id in ("P0", "P1", "P2", "P3"):
        print(f"production-policy {policy_id}...", flush=True)
        engine = build(policy_id)
        result = engine.run(available, warmup=warmup, corporate_actions=actions)
        artifacts[policy_id] = _policy_metrics(result, engine)

    alpha_metrics = artifacts["alpha_shadow"]
    deltas: dict[str, object] = {}
    for policy_id in ("P0", "P1", "P2", "P3"):
        production = artifacts[policy_id]
        deltas[policy_id] = {
            "return_delta_pp": round((production["return"] - alpha_metrics["return"]) * 100, 2),
            "cagr_delta_pp": round((production["cagr"] - alpha_metrics["cagr"]) * 100, 2),
            "maxdd_delta_pp": round((production["max_drawdown"] - alpha_metrics["max_drawdown"]) * 100, 2),
            "exposure_delta": round(production["avg_exposure"] - alpha_metrics["avg_exposure"], 4),
            "halt_count": production["halt_count"],
            "median_halt_duration_cycles": production["median_halt_duration_cycles"],
            "total_latched_cycles_estimate": production["total_latched_cycles_estimate"],
            "turnover_delta": round(production["turnover"] - alpha_metrics["turnover"], 4),
        }
    artifacts["deltas_vs_alpha"] = deltas
    artifacts["identity"] = run_identity(config)
    path = OUT_DIR / f"production_policy{suffix}.json"
    path.write_text(json.dumps(artifacts, indent=1))
    print(f"wrote {path}")
    for policy_id in ("P0", "P1", "P2", "P3"):
        delta = deltas[policy_id]
        print(
            f"{policy_id}: return_delta={delta['return_delta_pp']:+.1f}pp "
            f"maxDD={production_label(artifacts[policy_id])} halts={delta['halt_count']}"
        )
    return 0


def production_label(metrics: object) -> str:
    value = metrics["max_drawdown"]  # type: ignore[index]
    return f"{value:.4f}"


if __name__ == "__main__":
    raise SystemExit(main())
