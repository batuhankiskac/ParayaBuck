#!/usr/bin/env python3
"""Predeclared residual-momentum research — equity hardening mission 2026-08.

Candidates are DECLARED in data/research/equity_hardening_2026_08/predeclared.json
BEFORE any result is inspected; this harness only executes that declaration:

* CONTROL   production v2 parameters under the corrected semantics
            (alpha-rank position retention + nearest-rank ivol quantile);
* A1/A2/A3  standardized residual score ablations;
* B504/B756 longer beta estimation windows (formation/skip untouched);
* C_TR      total-return SIGNAL series (dividend-reinvested, built offline);
            executable bars and explicit dividend cash flows unchanged — the
            account accounting never changes, only what the strategy ranks on;
* TOPN6/TOPN9  re-run of the position-count question AFTER the phase-2 fix.

Every candidate runs: full window, anchored walk-forward folds, cost stress
(1x/2x/3x full window), jackknife (drop one of CONTROL's top-3 realized-PnL
symbols), concentration and calendar-year decomposition. No promotion is
decided here; artifacts land in data/research/equity_hardening_2026_08/.

Usage:
    python scripts/research_equity_hardening.py \
        --variants control,a1,a2,a3 [--skip-jackknife] [--smoke]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import platform
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path

from research_helpers import (
    dataset_identity,
    git_head,
    run_identity,
    sha256_of_file,
)

from parayabuck.backtest.portfolio_engine import (
    PortfolioBacktestEngine,
    PortfolioBacktestResult,
)
from parayabuck.backtest.product import (
    build_product_universe,
    load_csv_bars,
    load_csv_corporate_actions,
)
from parayabuck.backtest.universe import (
    PointInTimeUniverse,
    UniverseSnapshot,
    hash_point_in_time_universe,
)
from parayabuck.config import Config, load_config
from parayabuck.core.models import Bar, Market
from parayabuck.data.market_dataset import CorporateAction
from parayabuck.execution.portfolio import (
    PortfolioExecutionPolicy,
    portfolio_risk_limits_from_config,
)
from parayabuck.portfolio import rebalance_policy_for
from parayabuck.portfolio.equity_residual_momentum import (
    EquityResidualMomentum1dV2,
    _compounded_return,
    _volatility,
)

REPO = Path(__file__).resolve().parents[1]
EQUITY_CSV = REPO / "data/historical/equity_1200_2016_2026.csv"
DIVIDENDS_CSV = REPO / "data/historical/equity_1200_dividends.csv"
OUT_DIR = REPO / "data/research/equity_hardening_2026_08"
# COMMITTED outside the gitignored data/research/ tree: the predeclaration is
# a reproducibility contract, not a disposable artifact.
MANIFEST_PATH = REPO / "data/research_manifests/equity_hardening_2026_08.json"

EVAL_START = datetime(2017, 1, 3, tzinfo=UTC)
EVAL_END = datetime(2026, 7, 30, tzinfo=UTC)
FOLD_STEP_MONTHS = 6

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


# --------------------------------------------------------------------------- #
# Research strategy identities (each a DISTINCT name; production untouched)
# --------------------------------------------------------------------------- #


def _variant_class(name: str, base: type[EquityResidualMomentum1dV2]) -> type:
    return type(
        name,
        (base,),
        {"name": name.lower(), "version": "1.0.0", "display_name": name},
    )


class HardeningControl(EquityResidualMomentum1dV2):
    """CONTROL identity under corrected semantics (production math)."""


class HardeningStandardized(HardeningControl):
    """A-family score: compounded residual return standardized by the
    formation-window residual stdev (Blitz-Huij-Martens style)."""

    def _candidate_score(self, residuals: list[float], *, formation: int, skip: int) -> float:
        window = residuals[-formation - skip : -skip]
        compounded = _compounded_return(window)
        stdev = _volatility(window)
        if stdev <= 0 or compounded != compounded or stdev != stdev:
            return float("-inf")
        return compounded / stdev


class HardeningStandardizedNoIvol(HardeningStandardized):
    """A2: standardization already penalizes volatile residuals."""

    def _apply_ivol_filter(self, scored, residual_vols, quantile):  # type: ignore[no-untyped-def]
        return scored


class HardeningStandardizedEqualSized(HardeningStandardized):
    """A3: equal weights isolate selection from inverse-vol sizing."""

    def _position_weights(self, selected, residual_vols):  # type: ignore[no-untyped-def]
        cap = self.param_float("max_single_weight", 0.10)
        weight = min(1.0 / max(1, len(selected)), cap)
        return {symbol: weight for symbol in selected}


VARIANTS: dict[str, tuple[type[EquityResidualMomentum1dV2], dict[str, object]]] = {
    "control": (HardeningControl, {}),
    "a1": (_variant_class("HardeningStdA1", HardeningStandardized), {}),
    "a2": (
        _variant_class("HardeningStdA2NoIvol", HardeningStandardizedNoIvol),
        {},
    ),
    "a3": (
        _variant_class("HardeningStdA3Equal", HardeningStandardizedEqualSized),
        {},
    ),
    "b504": (HardeningControl, {"beta_window_bars": 504}),
    "b756": (HardeningControl, {"beta_window_bars": 756}),
    "c_tr": (HardeningControl, {}),  # signal series swapped at run time
    "topn6": (HardeningControl, {"top_n": 6}),
    "topn9": (HardeningControl, {"top_n": 9}),
}


# --------------------------------------------------------------------------- #
# Total-return signal series (Candidate C) — offline, deterministic
# --------------------------------------------------------------------------- #


def build_total_return_signal(bars_by_symbol: dict[str, list[Bar]], actions: list[CorporateAction]) -> list[Bar]:
    """Dividend-reinvested closes on top of the split-adjusted price series.

    TR(t) = TR(t-1) * [P(t)/P(t-1)] * [1 + D(t)/P(t-1)] where D(t) is the
    per-share dividend whose ex date falls on bar t (daily bars: intraday
    timing is not observable, the previous close is the declared basis).
    Splits are already inside P; dividends enter ONLY here — the account
    keeps booking them as explicit cash flows, so nothing double-counts.
    """
    # Dividend rows are stamped 00:00:00Z (ex-date) while equity daily bars
    # sit at the session close; match on CALENDAR DATE, not exact timestamp.
    by_symbol_actions: dict[str, dict[object, float]] = {}
    for action in actions:
        by_symbol_actions.setdefault(action.symbol, {})[action.timestamp.date()] = action.value
    out: list[Bar] = []
    for symbol, bars in bars_by_symbol.items():
        dividends = by_symbol_actions.get(symbol, {})
        if not dividends:
            out.extend(bars)
            continue
        factor = 1.0
        previous_close = 0.0
        for bar in bars:
            close = bar.close
            dividend = dividends.get(bar.timestamp.date())
            if dividend is not None and previous_close > 0:
                factor *= 1.0 + dividend / previous_close
            scaled = close * factor
            out.append(
                Bar(
                    bar.timestamp,
                    bar.symbol,
                    bar.timeframe,
                    bar.open * factor,
                    bar.high * factor,
                    bar.low * factor,
                    scaled,
                    bar.volume,
                    bar.market,
                )
            )
            previous_close = close
    return out


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def fold_windows() -> list[tuple[datetime, datetime]]:
    folds: list[tuple[datetime, datetime]] = []
    cursor = EVAL_START
    while cursor < EVAL_END:
        fold_end = min(cursor + timedelta(days=30 * FOLD_STEP_MONTHS), EVAL_END)
        if fold_end - cursor >= timedelta(days=20):
            folds.append((cursor, fold_end))
        cursor = fold_end
    return folds


def make_engine(
    config: Config,
    strategy: EquityResidualMomentum1dV2,
    universe: PointInTimeUniverse,
    *,
    cost_multiplier: float = 1.0,
) -> PortfolioBacktestEngine:
    return PortfolioBacktestEngine(
        strategy,
        rebalance_policy_for(strategy),
        starting_equity=config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps * cost_multiplier,
        spread_bps=config.backtest.spread_bps * cost_multiplier,
        commission=config.backtest.commission,
        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps * cost_multiplier,
        equity_taf_per_share=config.backtest.equity_taf_per_share,
        equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share,
        equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
        benchmark_symbol="SPY",
        universe=universe,
        universe_version=hash_point_in_time_universe(universe),
        risk_limits=portfolio_risk_limits_from_config(config.risk),
        maximum_allocation=config.strategy.equity.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
    )


def run_window(
    config: Config,
    strategy: EquityResidualMomentum1dV2,
    universe: PointInTimeUniverse,
    bars: list[Bar],
    *,
    start: datetime | None,
    end: datetime | None,
    cost_multiplier: float = 1.0,
    signal_bars: list[Bar] | None = None,
    actions: list[CorporateAction] | None = None,
) -> PortfolioBacktestResult:
    available = [bar for bar in bars if bar.market == Market.EQUITY and bar.timeframe == strategy.timeframe]
    if end is not None:
        available = [bar for bar in available if bar.timestamp <= end]
    filtered = available if start is None else [bar for bar in available if bar.timestamp >= start]
    warmup = [] if start is None else [bar for bar in available if bar.timestamp < start]
    engine = make_engine(config, strategy, universe, cost_multiplier=cost_multiplier)
    return engine.run(filtered, warmup=warmup, signal_bars=signal_bars, corporate_actions=actions)


def full_stats(result: PortfolioBacktestResult) -> dict[str, object]:
    gross = result.gross_return
    total_cost = result.slippage_cost_total + result.spread_cost_total + result.fee_total
    avg_equity = sum(result.equity_curve) / len(result.equity_curve) if result.equity_curve else 0.0
    gross_pnl = result.starting_equity * gross
    return {
        "return": round(result.total_return, 6),
        "cagr": round(result.cagr, 6),
        "sharpe": round(result.sharpe, 6),
        "sortino": round(result.sortino, 6),
        "calmar": round(result.calmar, 6),
        "max_drawdown": round(result.max_drawdown, 6),
        "volatility": round(result.annualized_volatility, 6),
        "turnover": round(result.turnover, 6),
        "fees_total": round(total_cost, 2),
        "fees_pct_avg_equity": round(total_cost / avg_equity, 6) if avg_equity > 0 else 0.0,
        "fees_pct_gross_pnl": (round(total_cost / abs(gross_pnl), 6) if abs(gross_pnl) > 1e-9 else None),
        "avg_exposure": round(1.0 - result.average_cash_ratio, 4),
        "avg_holdings": round(result.average_holdings_count, 3),
        "max_concentration_weight": round(result.maximum_concentration, 4),
        "rebalance_count": result.rebalance_count,
        "closed_positions": result.closed_positions,
        "dividend_total": round(result.dividend_total, 2),
        "shadow_drawdown_halts": len(result.drawdown_halt_shadow_events),
        "worst_year": round(result.worst_year_return, 6),
        "worst_month": round(result.worst_month_return, 6),
        "positive_month_ratio": round(result.positive_month_ratio, 4),
    }


def yearly_returns(result: PortfolioBacktestResult) -> dict[str, float]:
    buckets: dict[int, tuple[float, float]] = {}
    for equity, timestamp in zip(result.equity_curve, result.curve_timestamps):
        first, last = buckets.get(timestamp.year, (equity, equity))
        buckets[timestamp.year] = (first, equity)
    out: dict[str, float] = {}
    previous = result.starting_equity
    for year in sorted(buckets):
        _first, last = buckets[year]
        out[str(year)] = round(last / previous - 1, 6) if previous > 0 else 0.0
        previous = last
    return out


def concentration(result: PortfolioBacktestResult) -> dict[str, object]:
    contributions = sorted(
        ((item.symbol, item.realized_pnl) for item in result.per_symbol),
        key=lambda item: (-item[1], item[0]),
    )
    positive_total = sum(pnl for _symbol, pnl in contributions if pnl > 0)
    top = contributions[:3]
    return {
        "top_symbols": [symbol for symbol, _pnl in top],
        "top1_share_of_positive_pnl": (round(top[0][1] / positive_total, 4) if top and positive_total > 0 else 0.0),
        "top3_share_of_positive_pnl": (
            round(sum(item[1] for item in top) / positive_total, 4) if positive_total > 0 else 0.0
        ),
    }


def universe_without(universe: PointInTimeUniverse, banned: frozenset[str]) -> PointInTimeUniverse:
    snapshots = []
    for snapshot in universe.snapshots:
        snapshots.append(
            UniverseSnapshot(
                effective_at=snapshot.effective_at,
                symbols=frozenset(snapshot.symbols - banned),
                scanner_version=snapshot.scanner_version,
                config_digest=snapshot.config_digest,
                candidate_count=snapshot.candidate_count,
                score_breakdown=snapshot.score_breakdown,
                eliminated=snapshot.eliminated,
                sector_metadata_coverage=snapshot.sector_metadata_coverage,
                observation_set=tuple(symbol for symbol in snapshot.observation_set if symbol not in banned),
                maximum_stream_symbols=snapshot.maximum_stream_symbols,
                pinned_symbols=tuple(symbol for symbol in snapshot.pinned_symbols if symbol not in banned),
                observation_strategy_name=snapshot.observation_strategy_name,
                observation_strategy_version=snapshot.observation_strategy_version,
            )
        )
    return PointInTimeUniverse(market=universe.market, snapshots=tuple(snapshots), methodology=universe.methodology)


def summarize_folds(results: list[tuple[datetime, datetime, PortfolioBacktestResult]]) -> dict:
    stats = [
        {
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
            "return": round(result.total_return, 6),
            "sharpe": round(result.sharpe, 6),
            "max_drawdown": round(result.max_drawdown, 6),
            "turnover": round(result.turnover, 6),
        }
        for start, end, result in results
    ]
    returns = [item["return"] for item in stats]
    sharpes = [item["sharpe"] for item in stats]
    median_return = statistics.median(returns) if returns else 0.0
    return {
        "folds": stats,
        "fold_count": len(stats),
        "median_fold_return": median_return,
        "worst_fold_return": min(returns) if returns else None,
        "best_fold_return": max(returns) if returns else None,
        "positive_fold_ratio": (round(sum(1 for value in returns if value > 0) / len(returns), 4) if returns else 0.0),
        "median_fold_sharpe": statistics.median(sharpes) if sharpes else 0.0,
    }


def run_variant(
    variant_id: str,
    config: Config,
    bars: list[Bar],
    universe: PointInTimeUniverse,
    actions: list[CorporateAction],
    tr_signal: list[Bar] | None,
    control_top_symbols: list[str] | None,
    *,
    smoke: bool,
) -> dict[str, object]:
    cls, overrides = VARIANTS[variant_id]
    parameters = dict(_FROZEN)
    parameters.update(overrides)
    strategy = cls(parameters)

    eval_end = EVAL_END
    eval_start = EVAL_START
    folds = fold_windows()
    if smoke:
        eval_start = datetime(2024, 1, 2, tzinfo=UTC)
        folds = folds[-4:]

    # Full window.
    full = run_window(
        config,
        strategy,
        universe,
        bars,
        start=None,
        end=None,
        signal_bars=tr_signal if variant_id == "c_tr" else None,
        actions=actions,
    )

    # Walk-forward folds (anchored: everything before each fold is warmup).
    fold_results: list[tuple[datetime, datetime, PortfolioBacktestResult]] = []
    for fold_start, fold_end in folds:
        if fold_start < eval_start or fold_end > eval_end:
            continue
        result = run_window(
            config,
            strategy,
            universe,
            bars,
            start=fold_start,
            end=fold_end,
            signal_bars=tr_signal if variant_id == "c_tr" else None,
            actions=actions,
        )
        fold_results.append((fold_start, fold_end, result))

    # Cost stress on the full window.
    stress: dict[str, object] = {}
    if not smoke:
        for multiplier in (2.0, 3.0):
            stressed = run_window(
                config,
                strategy,
                universe,
                bars,
                start=None,
                end=None,
                cost_multiplier=multiplier,
                signal_bars=tr_signal if variant_id == "c_tr" else None,
                actions=actions,
            )
            stress[f"{multiplier:g}x"] = {
                "return": round(stressed.total_return, 6),
                "sharpe": round(stressed.sharpe, 6),
                "max_drawdown": round(stressed.max_drawdown, 6),
                "turnover": round(stressed.turnover, 6),
                "fees_pct_gross_pnl": full_stats(stressed)["fees_pct_gross_pnl"],
            }

    # Jackknife: drop one of CONTROL's top-3 realized-PnL symbols.
    jackknife: dict[str, object] = {}
    if not smoke and control_top_symbols:
        for symbol in control_top_symbols:
            reduced = universe_without(universe, frozenset({symbol}))
            result = run_window(
                config,
                strategy,
                reduced,
                bars,
                start=None,
                end=None,
                signal_bars=tr_signal if variant_id == "c_tr" else None,
                actions=actions,
            )
            jackknife[f"drop_{symbol}"] = {
                "return": round(result.total_return, 6),
                "sharpe": round(result.sharpe, 6),
                "max_drawdown": round(result.max_drawdown, 6),
            }

    artifact = {
        "variant": variant_id,
        "strategy_name": strategy.name,
        "parameters": {
            key: str(value) if isinstance(value, list) else value for key, value in strategy.parameter_tuple()
        },
        "full_period": full_stats(full),
        "concentration": concentration(full),
        "yearly_returns": yearly_returns(full),
        "walk_forward": summarize_folds(fold_results),
        "cost_stress_extra": stress,
        "jackknife": jackknife,
    }
    return artifact


def load_and_validate_manifest() -> dict:
    """The committed predeclaration is authoritative: the runner refuses to
    execute when its built-in VARIANTS drift from the declared contract."""
    if not MANIFEST_PATH.exists():
        raise SystemExit(
            f"predeclaration manifest missing: {MANIFEST_PATH} (commit it; "
            "research without a predeclared contract is not runnable here)"
        )
    manifest = json.loads(MANIFEST_PATH.read_text())
    declared = manifest.get("variants", {})
    built_in = {
        variant_id: {
            "base": cls.__name__,
            "overrides": {key: str(value) if isinstance(value, list) else value for key, value in overrides.items()},
        }
        for variant_id, (cls, overrides) in VARIANTS.items()
    }
    normalized = {
        variant_id: {
            "base": spec["base"],
            "overrides": {
                key: str(value) if isinstance(value, list) else value for key, value in spec["overrides"].items()
            },
        }
        for variant_id, spec in declared.items()
    }
    if normalized != built_in:
        drift = {key for key in set(normalized) | set(built_in) if normalized.get(key) != built_in.get(key)}
        raise SystemExit(
            "VARIANTS drifted from the committed predeclaration: "
            f"{sorted(drift)}. Update the code AND the manifest together."
        )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variants",
        default="control,a1,a2,a3,b504,b756,c_tr,topn6,topn9",
        help="comma-separated subset of: " + ",".join(VARIANTS),
    )
    parser.add_argument("--skip-jackknife", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument(
        "--refresh-manifest-hashes",
        action="store_true",
        help="after running, write sha256 of each generated artifact into the "
        "committed predeclaration manifest (results section)",
    )
    arguments = parser.parse_args()
    manifest = load_and_validate_manifest()
    requested = [item.strip() for item in arguments.variants.split(",") if item.strip()]
    unknown_check = [item for item in requested if item not in manifest.get("variants", {})]
    if unknown_check:
        raise SystemExit(f"variants absent from the predeclaration: {unknown_check}")
    unknown = [item for item in requested if item not in VARIANTS]
    if unknown:
        raise SystemExit(f"unknown variants: {unknown}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    config = load_config()
    print("loading dataset...", flush=True)
    bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
    actions = load_csv_corporate_actions(DIVIDENDS_CSV)
    print(f"{len(bars)} bars, {len(actions)} dividend actions", flush=True)

    bars_by_symbol: dict[str, list[Bar]] = {}
    for bar in bars:
        if bar.market == Market.EQUITY and bar.timeframe == "1d":
            bars_by_symbol.setdefault(bar.symbol, []).append(bar)
    tr_signal: list[Bar] | None = None
    if "c_tr" in requested:
        print("building total-return signal series...", flush=True)
        tr_signal = build_total_return_signal(bars_by_symbol, actions)
        print(f"{len(tr_signal)} total-return bars", flush=True)

    print("building PIT universe...", flush=True)
    universe = build_product_universe(config, bars, market=Market.EQUITY, end=EVAL_END)
    del bars_by_symbol

    # CONTROL's top contributors define the shared jackknife perturbation.
    control_top: list[str] | None = None
    control_artifact_path = OUT_DIR / ("control.json" if not arguments.smoke else "control_smoke.json")
    if arguments.skip_jackknife:
        control_top = None
    elif control_artifact_path.exists():
        payload = json.loads(control_artifact_path.read_text())
        control_top = list(payload["concentration"]["top_symbols"])
    elif "control" not in requested:
        raise SystemExit("control artifact missing; run --variants control first (or --skip-jackknife)")

    for variant_id in requested:
        started = datetime.now(UTC)
        print(f"=== {variant_id} ===", flush=True)
        if control_top is None and not arguments.skip_jackknife and variant_id == "control":
            # Control defines the set itself from its own full run.
            probe = run_window(
                config,
                HardeningControl(dict(_FROZEN)),
                universe,
                bars,
                start=None,
                end=None,
                actions=actions,
            )
            control_top = list(concentration(probe)["top_symbols"])
        artifact = run_variant(
            variant_id,
            config,
            bars,
            universe,
            actions,
            tr_signal,
            control_top,
            smoke=arguments.smoke,
        )
        artifact["identity"] = {
            **run_identity(config),
            "dataset": dataset_identity(),
            "python_version": platform.python_version(),
            "research_commit": git_head(),
            "eval_start": EVAL_START.date().isoformat(),
            "eval_end": EVAL_END.date().isoformat(),
            "fold_step_months": FOLD_STEP_MONTHS,
            "maximum_allocation": config.strategy.equity.maximum_allocation,
            "risk": dataclasses.asdict(config.risk),
        }
        suffix = "_smoke" if arguments.smoke else ""
        path = OUT_DIR / f"{variant_id}{suffix}.json"
        path.write_text(json.dumps(artifact, indent=1))
        summary = artifact["full_period"]
        print(
            f"{variant_id}: return={summary['return']:+.4f} sharpe={summary['sharpe']:.3f} "
            f"maxDD={summary['max_drawdown']:.4f} "
            f"medianFold={artifact['walk_forward']['median_fold_return']:+.4f} "
            f"({(datetime.now(UTC) - started).total_seconds():.0f}s) -> {path.name}",
            flush=True,
        )
    if arguments.refresh_manifest_hashes and not arguments.smoke:
        manifest = json.loads(MANIFEST_PATH.read_text())
        results: dict[str, str] = {}
        for variant_id in VARIANTS:
            path = OUT_DIR / f"{variant_id}.json"
            if path.exists():
                results[f"{variant_id}.json"] = sha256_of_file(path)
        manifest["results_sha256"] = results
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=1) + "\n")
        print(f"manifest hashes refreshed ({len(results)} artifacts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
