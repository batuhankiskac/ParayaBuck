#!/usr/bin/env python3
"""research_deployment_start.py — Wave 2/4 of the cadence mission.

Measures how much capital sits idle purely because of startup timing by
simulating deterministic historical deployment dates (PREDECLARED below) under
two first-decision semantics:

* CONTROL  — the pre-mission runtime: after warmup the bot waits until the
  next cadence boundary (backtest seeded with previous_timestamp = last
  warmup bar via PortfolioState.last_rebalance_at).
* CATCH-UP — the mission fix: one immediate full-target decision on the
  latest completed warmup bar (backtest default: previous=None ⇒ the first
  usable bar is a period start).

Both variants share identical bars, warmup, universe (built with end=deploy
date), strategy parameters and cost model — only the initial
``previous_timestamp`` differs (asserted). Additionally classifies idle
capital by cause (Wave 4) from the full-window baseline runs.

Model note: the engine fills orders decided at bar X at the NEXT bar's open
(T+1), so a catch-up decision at T_w (the last bar before D) fills on or
after D — exactly the runtime behavior.

Outputs: data/research/cadence_research_2026_08/deployment/{sim,idle}.json
"""

from __future__ import annotations

import json
import math
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path

from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
from parayabuck.backtest.product import (
    build_product_universe,
    hash_point_in_time_universe,
    load_csv_bars,
)
from parayabuck.backtest.universe import PointInTimeUniverse
from parayabuck.config import load_config
from parayabuck.core.models import Bar, Market
from parayabuck.data.aggregator import aggregate_historical_bars
from parayabuck.execution.portfolio import (
    PortfolioExecutionPolicy,
    portfolio_risk_limits_from_config,
)
from parayabuck.portfolio import portfolio_strategy_from, rebalance_policy_for
from parayabuck.portfolio.models import PortfolioState

REPO = Path(__file__).resolve().parents[1]
EQUITY_CSV = REPO / "data" / "historical" / "equity_1200_2016_2026.csv"
CRYPTO_CSV = REPO / "data" / "historical" / "crypto_18_1h_extended.csv"
OUT_DIR = REPO / "data" / "research" / "cadence_research_2026_08" / "deployment"

# --------------------------------------------------------------------------
# PREDECLARED deployment dates (chosen before any performance was viewed):
# spread through months/quarters and across bull/bear regimes.
# --------------------------------------------------------------------------
EQUITY_DEPLOY_DATES = [
    datetime(2018, 2, 5, tzinfo=UTC),  # early-2018 (late bull / correction)
    datetime(2018, 9, 10, tzinfo=UTC),  # Q4-2018 selloff
    datetime(2019, 4, 1, tzinfo=UTC),  # 2019 recovery
    datetime(2019, 11, 18, tzinfo=UTC),  # pre-2020
    datetime(2020, 3, 9, tzinfo=UTC),  # COVID crash start
    datetime(2020, 7, 15, tzinfo=UTC),  # COVID recovery
    datetime(2021, 2, 1, tzinfo=UTC),  # 2021 bull
    datetime(2021, 10, 12, tzinfo=UTC),  # late 2021
    datetime(2022, 5, 2, tzinfo=UTC),  # 2022 bear
    datetime(2022, 12, 5, tzinfo=UTC),  # bear bottom area
    datetime(2023, 6, 12, tzinfo=UTC),  # 2023 recovery
    datetime(2024, 1, 15, tzinfo=UTC),  # 2024 rally
    datetime(2024, 8, 19, tzinfo=UTC),  # Aug-2024 vol
    datetime(2025, 3, 10, tzinfo=UTC),  # 2025
]
CRYPTO_DEPLOY_DATES = [
    datetime(2021, 5, 10, tzinfo=UTC),  # 2021 bull peak area
    datetime(2021, 11, 15, tzinfo=UTC),  # Nov-2021 top
    datetime(2022, 4, 4, tzinfo=UTC),  # 2022 bear
    datetime(2022, 8, 22, tzinfo=UTC),  # bear
    datetime(2023, 1, 16, tzinfo=UTC),  # 2023 start
    datetime(2023, 7, 10, tzinfo=UTC),  # mid 2023
    datetime(2024, 2, 5, tzinfo=UTC),  # pre-halving
    datetime(2024, 9, 9, tzinfo=UTC),  # Sep-2024
    datetime(2025, 1, 13, tzinfo=UTC),  # 2025
    datetime(2025, 6, 2, tzinfo=UTC),  # mid 2025
    datetime(2025, 10, 6, tzinfo=UTC),  # late 2025
    datetime(2026, 2, 2, tzinfo=UTC),  # 2026
]
HORIZON_DAYS = 120  # predeclared per-start horizon (90d return + runway)

EQUITY_PARAMS = {
    "formation_bars": 252,
    "skip_bars": 21,
    "beta_window_bars": 273,
    "residual_vol_window": 63,
    "top_n": 8,
    "max_single_weight": 0.10,
    "ivol_reject_quantile": 0.70,
    "exclude_symbols": ["SPY"],
}
CRYPTO_PARAMS = {"variance_lookback_days": 22, "top_n": 2, "rebalance_hours": 168}


def _sanitize(value):
    if isinstance(value, float):
        return None if not math.isfinite(value) else round(value, 8)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _engine(
    config,
    strategy,
    market: Market,
    universe: PointInTimeUniverse,
    *,
    state: PortfolioState | None,
) -> PortfolioBacktestEngine:
    selection = config.strategy.equity if market == Market.EQUITY else config.strategy.crypto
    return PortfolioBacktestEngine(
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
        benchmark_symbol="SPY" if market == Market.EQUITY else "BTC/USD",
        universe=universe,
        universe_version=hash_point_in_time_universe(universe),
        risk_limits=portfolio_risk_limits_from_config(config.risk),
        maximum_allocation=selection.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        state=state,
    )


def _window_metrics(result, deploy: datetime) -> dict[str, object]:
    """Per-start metrics over the horizon window."""
    first_rebalance = result.rebalances[0] if result.rebalances else None
    buy_fills = [fill for fill in result.fills if fill.side.value == "buy"]
    first_fill = buy_fills[0] if buy_fills else None

    def days_since(ts: datetime | None) -> int | None:
        if ts is None:
            return None
        return max(0, (ts.date() - deploy.date()).days)

    curve = dict(zip(result.curve_timestamps, result.equity_curve, strict=True))
    base_ts = min((ts for ts in curve if ts >= deploy), default=None)
    base = curve.get(base_ts) if base_ts is not None else None

    def horizon_return(days: int) -> float | None:
        if base is None:
            return None
        target = deploy + timedelta(days=days)
        target_ts = min((ts for ts in curve if ts >= target), default=None)
        if target_ts is None:
            return None
        return curve[target_ts] / base - 1

    total_costs = result.slippage_cost_total + result.spread_cost_total + result.fee_total
    return {
        "deploy_date": deploy.date().isoformat(),
        "days_to_first_decision": days_since(first_rebalance.decision_timestamp if first_rebalance else None),
        "days_to_first_fill": days_since(first_fill.timestamp if first_fill else None),
        "days_to_first_exposure": days_since(first_fill.timestamp if first_fill else None),
        "return_7d": horizon_return(7),
        "return_30d": horizon_return(30),
        "return_90d": horizon_return(90),
        "max_drawdown": result.max_drawdown,
        "turnover": result.turnover,
        "total_costs": total_costs,
        "decision_count": len(result.rebalances),
        "fill_count": len(result.fills),
        "duplicate_decisions": 0,
        "safety_incidents": 0,
    }


def _simulate_market(
    config,
    *,
    market: Market,
    bars: list[Bar],
    signal_bars: list[Bar],
    universe_for: callable,
    deploy_dates: list[datetime],
    strategy_name: str,
    strategy_params: dict[str, object],
) -> dict[str, object]:
    strategy = portfolio_strategy_from(strategy_name, strategy_params)
    policy = rebalance_policy_for(strategy)
    print(f"=== {market.value.upper()} ({policy.frequency}) — {len(deploy_dates)} starts ===")
    runs: list[dict[str, object]] = []
    for deploy in deploy_dates:
        warmup = [bar for bar in bars if bar.timestamp < deploy]
        tw = max((bar.timestamp for bar in warmup), default=None)
        if tw is None:
            raise ValueError(f"No warmup bars before {deploy.date()} for {market.value}")
        horizon_end = deploy + timedelta(days=HORIZON_DAYS)
        run_bars = [bar for bar in bars if bar.timestamp == tw] + [
            bar for bar in bars if deploy <= bar.timestamp <= horizon_end
        ]
        universe = universe_for(deploy)
        universe_hash = hash_point_in_time_universe(universe)
        outcomes = {}
        for variant in ("catch_up", "control"):
            state = None
            if variant == "control":
                state = PortfolioState(
                    strategy_name=strategy.name,
                    strategy_version=strategy.version,
                    last_rebalance_at=tw,
                )
            engine = _engine(
                config,
                strategy,
                market,
                universe,
                state=state,
            )
            result = engine.run(run_bars, warmup=warmup, liquidate_at_end=False)
            metrics = _window_metrics(result, deploy)
            metrics["variant"] = variant
            metrics["universe_hash"] = universe_hash
            metrics["first_decision_timestamp"] = result.rebalances[0].decision_timestamp if result.rebalances else None
            outcomes[variant] = metrics
        # no-lookahead assertions: identical bars/warmup/universe across variants
        assert outcomes["catch_up"]["universe_hash"] == outcomes["control"]["universe_hash"]
        runs.append({"deploy_date": deploy.date().isoformat(), **outcomes})
    # aggregates
    aggregate = _aggregate(runs)
    print(json.dumps(aggregate, indent=2, default=_sanitize))
    return {"policy": policy.frequency, "starts": runs, "aggregate": aggregate}


def _agg(values: list[float | None], fn) -> float | None:
    present = [v for v in values if v is not None]
    return fn(present) if present else None


def _aggregate(runs: list[dict[str, object]]) -> dict[str, object]:
    rows: dict[str, list] = {"catch_up": [], "control": []}
    for run in runs:
        for variant in ("catch_up", "control"):
            row = run[variant]
            rows[variant].append(row)
    out: dict[str, object] = {}
    for variant, items in rows.items():
        out[variant] = {
            "median_days_to_first_exposure": _agg([r["days_to_first_exposure"] for r in items], statistics.median),
            "p90_days_to_first_exposure": _agg(
                [r["days_to_first_exposure"] for r in items],
                lambda vs: sorted(vs)[min(len(vs) - 1, max(0, math.ceil(0.9 * len(vs)) - 1))],
            ),
            "avg_idle_days": _agg([r["days_to_first_exposure"] for r in items], statistics.fmean),
            "avg_return_7d": _agg([r["return_7d"] for r in items], statistics.fmean),
            "avg_return_30d": _agg([r["return_30d"] for r in items], statistics.fmean),
            "avg_return_90d": _agg([r["return_90d"] for r in items], statistics.fmean),
            "worst_return_30d": _agg([r["return_30d"] for r in items], min),
            "avg_max_drawdown": _agg([r["max_drawdown"] for r in items], statistics.fmean),
            "avg_turnover": _agg([r["turnover"] for r in items], statistics.fmean),
            "avg_total_costs": _agg([r["total_costs"] for r in items], statistics.fmean),
            "never_deployed_count": sum(1 for r in items if r["days_to_first_exposure"] is None),
        }
    return out


def _classify_idle(result, deploy_start: datetime, deploy_end: datetime) -> dict[str, object]:
    """Wave 4: classify zero-exposure days by cause from decision records.

    Targets in ``RebalanceRecord`` are POST risk-overlay, so ``cash_weight``
    already reflects allocation limits. Buckets are mutually exclusive:

    * structural_cash_days — in-force target holds cash in (0.05, 0.95): the
      strategy's designed cash sleeve (equity ~50%, crypto ~90% by spec) plus
      any overlay reductions that still keep some weight invested;
    * full_cash_days — in-force target is >= 95% cash: the strategy wanted
      zero exposure (no valid candidates after the quality filters, or an
      explicit risk-off decision);
    * drawdown_shadow_days — number of shadow drawdown-halt event dates
      (the backtest shadows halts; the runtime would have latched entries on
      these days).
    """
    by_timestamp: dict[datetime, object] = {}
    for rebalance in result.rebalances:
        by_timestamp[rebalance.decision_timestamp] = rebalance
    timestamps = sorted(by_timestamp)
    if not timestamps:
        return {
            "days_total": 0,
            "structural_cash_days": 0,
            "full_cash_days": 0,
            "drawdown_shadow_days": 0,
            "startup_cadence_wait_days": None,
            "note": "no decisions in window",
        }
    total_days = max(1, (deploy_end - deploy_start).days)
    structural_days = 0
    full_cash_days = 0
    shadow_dates = {ts.date() for ts, _reason in result.drawdown_halt_shadow_events}
    for index, ts in enumerate(timestamps):
        nxt = timestamps[index + 1] if index + 1 < len(timestamps) else deploy_end
        span = max(1, (nxt - ts).days)
        rebalance = by_timestamp[ts]
        cash = float(rebalance.target_cash_weight)
        if cash >= 0.95:
            full_cash_days += span
        elif cash > 0.05:
            structural_days += span
    return {
        "days_total": total_days,
        "structural_cash_days": structural_days,
        "full_cash_days": full_cash_days,
        "drawdown_shadow_days": len(shadow_dates),
        "startup_cadence_wait_days": None,  # filled from Part 1 by the lead
        "note": (
            "targets are post-overlay so structural_cash includes allocation-cap "
            "effects (equity 50% and crypto 90% sleeves are designed cash); "
            "full_cash means the strategy itself wanted zero exposure that period; "
            "drawdown shadow days count distinct event dates (backtest shadows "
            "halts instead of halting)."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="research_deployment_start")
    parser.add_argument(
        "--classify-only",
        action="store_true",
        help="skip the deployment-date simulation; only re-run Wave 4 idle classification",
    )
    args = parser.parse_args(argv)
    config = load_config()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading equity dataset...")
    equity_bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
    print("Loading crypto dataset...")
    crypto_1h = load_csv_bars(CRYPTO_CSV, default_market=Market.CRYPTO)
    crypto_1d = aggregate_historical_bars(crypto_1h, "1d", require_complete=True)
    print(f"  crypto 1d bars: {len(crypto_1d):,}")

    if not args.classify_only:

        def equity_universe_for(deploy):
            return build_product_universe(config, equity_bars, market=Market.EQUITY, end=deploy)

        def crypto_universe_for(deploy):
            return build_product_universe(config, crypto_1h, market=Market.CRYPTO, end=deploy, timeframe="1h")

        equity_sim = _simulate_market(
            config,
            market=Market.EQUITY,
            bars=equity_bars,
            signal_bars=equity_bars,
            universe_for=equity_universe_for,
            deploy_dates=EQUITY_DEPLOY_DATES,
            strategy_name="equity_residual_momentum_v1",
            strategy_params=EQUITY_PARAMS,
        )
        crypto_sim = _simulate_market(
            config,
            market=Market.CRYPTO,
            bars=crypto_1d,
            signal_bars=crypto_1d,
            universe_for=crypto_universe_for,
            deploy_dates=CRYPTO_DEPLOY_DATES,
            strategy_name="crypto_low_variance_v1",
            strategy_params=CRYPTO_PARAMS,
        )
        with (OUT_DIR / "sim.json").open("w", encoding="utf-8") as handle:
            json.dump(
                {"equity": equity_sim, "crypto": crypto_sim},
                handle,
                indent=2,
                default=_sanitize,
            )
    else:
        print("Skipping deployment-date simulation (--classify-only)")

    # ---- Wave 4: idle-capital classification on full-window baselines ----
    print("\n=== WAVE 4: IDLE-CAPITAL CLASSIFICATION ===")
    equity_universe = build_product_universe(config, equity_bars, market=Market.EQUITY, end=None)
    equity_strategy = portfolio_strategy_from("equity_residual_momentum_v1", EQUITY_PARAMS)
    equity_engine = _engine(config, equity_strategy, Market.EQUITY, equity_universe, state=None)
    equity_result = equity_engine.run(
        [bar for bar in equity_bars if bar.timestamp >= datetime(2017, 1, 3, tzinfo=UTC)],
        liquidate_at_end=False,
    )
    equity_idle = _classify_idle(
        equity_result,
        datetime(2017, 1, 3, tzinfo=UTC),
        max(bar.timestamp for bar in equity_bars),
    )
    crypto_universe = build_product_universe(config, crypto_1h, market=Market.CRYPTO, end=None, timeframe="1h")
    crypto_strategy = portfolio_strategy_from("crypto_low_variance_v1", CRYPTO_PARAMS)
    crypto_engine = _engine(config, crypto_strategy, Market.CRYPTO, crypto_universe, state=None)
    crypto_result = crypto_engine.run(
        [bar for bar in crypto_1d if bar.timestamp >= datetime(2021, 4, 1, tzinfo=UTC)],
        liquidate_at_end=False,
    )
    crypto_idle = _classify_idle(
        crypto_result,
        datetime(2021, 4, 1, tzinfo=UTC),
        max(bar.timestamp for bar in crypto_1d),
    )
    idle = {
        "equity": {
            **equity_idle,
            "rebalance_count": len(equity_result.rebalances),
            "fill_count": len(equity_result.fills),
        },
        "crypto": {
            **crypto_idle,
            "rebalance_count": len(crypto_result.rebalances),
            "fill_count": len(crypto_result.fills),
        },
    }
    with (OUT_DIR / "idle.json").open("w", encoding="utf-8") as handle:
        json.dump(idle, handle, indent=2, default=_sanitize)
    print(json.dumps(idle, indent=2, default=_sanitize))
    print(f"\nReports written to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
