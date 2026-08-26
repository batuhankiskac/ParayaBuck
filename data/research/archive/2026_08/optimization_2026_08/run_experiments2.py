#!/usr/bin/env python3
# ruff: noqa: E501
"""Second experiment batch (2026-08): dividends, intermediate risk-rebalance,
top_n=9, band 0.25%, expanded crypto universe, top_n=9 walk-forward.

Commands: eqvar | crband | intr | divrun | crxrun | eqwf9
"""

from __future__ import annotations

import dataclasses
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, "/tmp/pb_opt")
REPO = Path("/Users/batuhankiskac/Desktop/ParayaBuck")
sys.path.insert(0, str(REPO))

from run_experiments import (  # noqa: E402
    CONFIG_PATH,
    CR_END,
    CR_START,
    CRYPTO_CSV,
    EQ_END,
    EQ_START,
    EQUITY_CSV,
    OUT,
    _load,
    _run_and_save,
    _save,
    config_with_alloc,
    config_with_shortlist,
    load_config,
)
from run_experiments import build_product_universe, aggregate_historical_bars, load_csv_bars  # noqa: E402
from run_experiments import Market  # noqa: E402

from parayabuck.backtest.product import load_csv_corporate_actions, run_product_backtest  # noqa: E402
from parayabuck.core.models import Market as M  # noqa: E402
from parayabuck.data.aggregator import aggregate_historical_bars as agg  # noqa: E402
from parayabuck.portfolio.equity_residual_momentum import (  # noqa: E402
    EquityResidualMomentum1dV2,
)
from parayabuck.portfolio.models import (  # noqa: E402
    RebalanceReason,
    TargetPortfolio,
    TargetWeight,
)

assert Market == M
DIV_CSV = REPO / "data" / "historical" / "equity_1200_dividends.csv"
CRYPTO_EXPANDED = REPO / "data" / "historical" / "crypto_expanded_1h.csv"


def config_with_band(band: float):
    cfg = load_config(CONFIG_PATH)
    return dataclasses.replace(
        cfg,
        execution=dataclasses.replace(cfg.execution, rebalance_band_pct=band),
    )


def _equity_bars_universe(end):
    bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
    universe = build_product_universe(load_config(CONFIG_PATH), bars, market=Market.EQUITY, end=end)
    return bars, universe


def _crypto_1d_universe(csv_path: Path, end):
    bars_1h = load_csv_bars(csv_path, default_market=Market.CRYPTO)
    bars_1d = aggregate_historical_bars(bars_1h, "1d", require_complete=True)
    universe = build_product_universe(
        load_config(CONFIG_PATH), bars_1h, market=Market.CRYPTO, end=end, timeframe="1h"
    )
    return bars_1d, universe


def _equity_params(**overrides) -> dict:
    params = dict(load_config(CONFIG_PATH).strategy.equity.parameters)
    params.update(overrides)
    return params


# ---------------------------------------------------------------------------
# eqvar: equity top_n=9 + rebalance band 0.25% (equity)
# ---------------------------------------------------------------------------


def cmd_eqvar() -> None:
    bars, universe = _equity_bars_universe(EQ_END)
    _run_and_save(
        "eq_topn9",
        load_config(CONFIG_PATH),
        bars,
        Market.EQUITY,
        strategy_name="equity_residual_momentum_v2",
        parameters=_equity_params(top_n=9),
        strategy_timeframe="1d",
        start=EQ_START,
        end=EQ_END,
        universe=universe,
    )
    _run_and_save(
        "eq_band0025",
        config_with_band(0.0025),
        bars,
        Market.EQUITY,
        strategy_timeframe="1d",
        start=EQ_START,
        end=EQ_END,
        universe=universe,
    )


def cmd_crband() -> None:
    bars_1d, universe = _crypto_1d_universe(CRYPTO_CSV, CR_END)
    _run_and_save(
        "cr_band0025",
        config_with_band(0.0025),
        bars_1d,
        Market.CRYPTO,
        strategy_timeframe="1d",
        start=CR_START,
        end=CR_END,
        universe=universe,
    )


# ---------------------------------------------------------------------------
# intr: intermediate drawdown-triggered risk reduction (machinery already in
# the engine/runtime; the hook is simply never overridden by production
# strategies — this subclass activates it WITHOUT touching project code).
# ---------------------------------------------------------------------------


def make_intermediate_strategy(threshold: float, scale: float):
    base_params = dict(load_config(CONFIG_PATH).strategy.equity.parameters)

    class IntermediateRisk(EquityResidualMomentum1dV2):
        def __init__(self, parameters):  # noqa: ANN001
            super().__init__(parameters)
            self._dd_threshold = threshold
            self._dd_scale = scale
            self._peak = None
            self._intermediate = False
            self._last_hook_ts = None

        def _update_peak(self, equity: float) -> float:
            self._peak = equity if self._peak is None else max(self._peak, equity)
            return 1.0 - equity / self._peak if self._peak > 0 else 0.0

        def requires_intermediate_risk_rebalance(self, context) -> bool:  # noqa: ANN001
            self._last_hook_ts = context.decision_timestamp
            drawdown = self._update_peak(context.equity)
            self._intermediate = drawdown >= self._dd_threshold
            return self._intermediate

        def decide(self, context):  # noqa: ANN001
            scheduled = context.decision_timestamp != self._last_hook_ts
            self._update_peak(context.equity)
            if not scheduled and self._intermediate:
                equity = max(context.equity, 1.0)
                weights = tuple(
                    TargetWeight(
                        symbol,
                        min(0.999, qty * context.prices.get(symbol, 0.0) / equity * self._dd_scale),
                        "intermediate_dd_derisk",
                    )
                    for symbol, qty in sorted(context.holdings.items())
                    if qty > 1e-12 and context.prices.get(symbol, 0.0) > 0
                )
                return TargetPortfolio(
                    strategy_name=self.name,
                    strategy_version=self.version,
                    decision_timestamp=context.decision_timestamp,
                    weights=weights,
                    cash_weight=max(0.0, 1.0 - sum(weight.weight for weight in weights)),
                    rebalance_reason=RebalanceReason.RISK_OFF,
                    universe_version=context.universe_version,
                    universe_snapshot_id=context.universe_version,
                    parameters=self.parameter_tuple(),
                    metadata={
                        "intermediate_risk_reduction": True,
                        "dd_threshold": self._dd_threshold,
                        "dd_scale": self._dd_scale,
                    },
                )
            return super().decide(context)

    return IntermediateRisk(base_params)


def cmd_intr() -> None:
    bars, universe = _equity_bars_universe(EQ_END)
    for label, threshold in (("eq_intr_dd075", 0.075), ("eq_intr_dd10", 0.10)):
        strategy = make_intermediate_strategy(threshold, 0.5)
        _engine_run(label, strategy, bars, universe)


def _engine_run(label: str, strategy, bars, universe) -> None:
    from parayabuck.backtest.product import ProductBacktestResult  # noqa: F401
    from parayabuck.backtest.universe import hash_point_in_time_universe
    from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
    from parayabuck.execution.portfolio import PortfolioExecutionPolicy
    from parayabuck.portfolio import rebalance_policy_for

    cfg = load_config(CONFIG_PATH)
    risk = __import__(
        "parayabuck.execution.portfolio", fromlist=["portfolio_risk_limits_from_config"]
    ).portfolio_risk_limits_from_config(cfg.risk)
    engine = PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=cfg.backtest.initial_cash,
        slippage_bps=cfg.backtest.slippage_bps,
        spread_bps=cfg.backtest.spread_bps,
        commission=cfg.backtest.commission,
        crypto_taker_fee_bps=cfg.backtest.crypto_taker_fee_bps,
        equity_taf_per_share=cfg.backtest.equity_taf_per_share,
        equity_taf_cap=cfg.backtest.equity_taf_cap,
        equity_cat_per_share=cfg.backtest.equity_cat_per_share,
        equity_sec_fee_rate=cfg.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=cfg.backtest.equity_use_effective_dated_fees,
        benchmark_symbol="SPY",
        universe=universe,
        universe_version=hash_point_in_time_universe(universe),
        risk_limits=risk,
        maximum_allocation=cfg.strategy.equity.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=cfg.execution.rebalance_band_pct,
            minimum_order_notional=cfg.execution.minimum_order_notional,
        ),
    )
    available = [
        bar
        for bar in bars
        if bar.market == Market.EQUITY and bar.timeframe == "1d" and bar.timestamp <= EQ_END
    ]
    filtered = [bar for bar in available if bar.timestamp >= EQ_START]
    warmup = [bar for bar in available if bar.timestamp < EQ_START]
    result = engine.run(filtered, warmup=warmup)
    print(
        f"  [{label}] ret={result.total_return:.2%} cagr={result.cagr:.2%} "
        f"sharpe={result.sharpe:.2f} mdd={result.max_drawdown:.2%} "
        f"rebalances={result.rebalance_count}",
        flush=True,
    )
    payload = {
        "label": label,
        "metrics": {
            "total_return": result.total_return,
            "cagr": result.cagr,
            "sharpe": result.sharpe,
            "sortino": result.sortino,
            "calmar": result.calmar,
            "max_drawdown": result.max_drawdown,
            "max_drawdown_duration_days": result.max_drawdown_duration_days,
            "turnover": result.turnover,
            "fee_total": result.fee_total,
            "average_cash_ratio": result.average_cash_ratio,
            "rebalance_count": result.rebalance_count,
            "shadow_halt_events": len(result.drawdown_halt_shadow_events),
        },
        "equity_curve": list(result.equity_curve),
        "curve_timestamps": [ts.isoformat() for ts in result.curve_timestamps],
    }
    _save(label, payload)


# ---------------------------------------------------------------------------
# divrun: equity baselines WITH the fetched dividend stream
# ---------------------------------------------------------------------------


def cmd_divrun() -> None:
    if not DIV_CSV.exists():
        raise SystemExit(f"missing {DIV_CSV}; run fetch_data.py dividends first")
    actions = load_csv_corporate_actions(DIV_CSV)
    print(f"loaded {len(actions):,} dividend actions", flush=True)
    bars, universe = _equity_bars_universe(EQ_END)
    for label, allocation in (("eq_div_alloc050", 0.50), ("eq_div_alloc060", 0.60)):
        _run_and_save(
            label,
            config_with_alloc(Market.EQUITY, allocation),
            bars,
            Market.EQUITY,
            strategy_timeframe="1d",
            start=EQ_START,
            end=EQ_END,
            universe=universe,
            corporate_actions=actions,
        )


# ---------------------------------------------------------------------------
# crxrun: expanded crypto dataset (backfilled holes + additional pairs)
# ---------------------------------------------------------------------------


def cmd_crxrun() -> None:
    if not CRYPTO_EXPANDED.exists():
        raise SystemExit(f"missing {CRYPTO_EXPANDED}; run fetch_data.py crypto first")
    bars_1d, universe = _crypto_1d_universe(CRYPTO_EXPANDED, CR_END)
    print(
        f"expanded universe: snapshots={len(universe.snapshots)} "
        f"avg_pool={statistics.fmean(s.candidate_count for s in universe.snapshots):.1f}",
        flush=True,
    )
    base = dict(load_config(CONFIG_PATH).strategy.crypto.parameters)
    variants = (
        ("crx_base_topn2", None, 0.10),
        ("crx_topn3", dict(base, top_n=3), 0.10),
    )
    for label, parameters, allocation in variants:
        kwargs = {}
        if parameters is not None:
            kwargs = {"strategy_name": "crypto_low_variance_v2", "parameters": parameters}
        _run_and_save(
            label,
            config_with_alloc(Market.CRYPTO, allocation),
            bars_1d,
            Market.CRYPTO,
            strategy_timeframe="1d",
            start=CR_START,
            end=CR_END,
            universe=universe,
            **kwargs,
        )


# ---------------------------------------------------------------------------
# eqwf9: walk-forward for top_n=9 (16 folds, same helper as batch 1)
# ---------------------------------------------------------------------------


def cmd_eqwf9() -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    from research_helpers import _calendar_fold_window  # noqa: E402

    bars, universe = _equity_bars_universe(EQ_END)
    folds = _calendar_fold_window(datetime(2019, 1, 1, tzinfo=UTC), EQ_END, 6)
    fold_stats = []
    for index, (fold_start, fold_end) in enumerate(folds, start=1):
        product = run_product_backtest(
            load_config(CONFIG_PATH),
            bars,
            market=Market.EQUITY,
            strategy_name="equity_residual_momentum_v2",
            parameters=_equity_params(top_n=9),
            strategy_timeframe="1d",
            start=fold_start,
            end=fold_end,
            universe=universe,
            persist=False,
            persist_artifacts=False,
        )
        fold_stats.append(
            {
                "fold": index,
                "window": [fold_start.date().isoformat(), fold_end.date().isoformat()],
                "total_return": product.result.total_return,
                "sharpe": product.result.sharpe,
                "max_drawdown": product.result.max_drawdown,
            }
        )
        print(f"  fold {index}/{len(folds)} ret={product.result.total_return:.2%}", flush=True)
    _save(
        "wf_eq_topn9",
        {
            "folds": fold_stats,
            "positive_folds": sum(1 for item in fold_stats if item["total_return"] > 0),
            "median_fold_return": statistics.median(item["total_return"] for item in fold_stats),
        },
    )


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    commands = {
        "eqvar": cmd_eqvar,
        "crband": cmd_crband,
        "intr": cmd_intr,
        "divrun": cmd_divrun,
        "crxrun": cmd_crxrun,
        "eqwf9": cmd_eqwf9,
    }
    if command not in commands:
        raise SystemExit(f"unknown command: {command}; known: {sorted(commands)}")
    commands[command]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
