#!/usr/bin/env python3
"""Strategy robustness pack for the two FROZEN validated strategies.

NON-OPTIMIZING evidence only. Production parameters are read from
config/settings.yaml and NEVER modified — no matter what the diagnostics show,
the frozen values stay the production values (formation 252 / skip 21 / beta
273 / residual vol 63 / top_n 8 / max weight 0.10 / ivol quantile 0.70 for
equity_residual_momentum_v1; variance_lookback_days 22 / top_n 2 /
rebalance_hours 168 for crypto_low_variance_v1).

Sections per market:
  a. LOCAL PARAMETER NEIGHBORHOOD — predeclared, tiny, one-at-a-time around the
     frozen point (deliberately NOT a grid and NOT an optimizer).
  b. ROLLING PERFORMANCE — trailing-window metrics on the full-period run's
     realized daily curve (equivalent to the realized path the strategy
     actually experienced; window-restricted fresh runs would start flat and
     answer a different question).
  c. REGIME ANALYSIS — ex-post diagnostic only; NO regime filters are added to
     production. Equity: bull/bear (SPY vs 200d SMA) and high/low vol (trailing
     63d realized vol vs full-sample median). Crypto: BTC-up / BTC-flat-down
     (BTC vs 30d SMA) and high/low vol (trailing 22d vs full-sample median).
  d. SYMBOL JACKKNIFE — rerun dropping top contributor(s), BTC, ETH. Diagnostic
     only; results are NOT used to exclude symbols from production.
  e. COST ROBUSTNESS — 1x/2x/3x costs plus a break-even cost multiplier estimate
     (validated against the 2x/3x runs; flagged when nonlinear portfolio
     effects make it unreliable).
  f. BLOCK BOOTSTRAP — serial-dependence-aware (NOT iid): 63d blocks (equity) /
     22d blocks (crypto) on the strategy daily return series. Descriptive only.

Offline-only: reads data/historical/*.csv through the production backtest
harness (parayabuck.backtest.product). No broker calls, no paper/live orders.

Outputs: reports/robustness/equity.json, reports/robustness/crypto.json,
reports/robustness/summary.md
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import statistics
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from parayabuck.backtest.product import build_product_universe, load_csv_bars, run_product_backtest
from parayabuck.config import load_config
from parayabuck.core.models import Bar, Market
from parayabuck.data.aggregator import aggregate_historical_bars

REPO = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO / "reports" / "robustness"
EQUITY_CSV = REPO / "data" / "historical" / "equity_1200_2016_2026.csv"
CRYPTO_CSV = REPO / "data" / "historical" / "crypto_18_1h_2024_2026.csv"

EQUITY_START = datetime(2017, 1, 3, tzinfo=UTC)
EQUITY_END = datetime(2026, 7, 30, 23, 59, 59, tzinfo=UTC)
CRYPTO_EVAL_START = datetime(2024, 12, 1, tzinfo=UTC)
CRYPTO_END = datetime(2026, 7, 31, 23, 59, 59, tzinfo=UTC)

# Predeclared tiny local neighborhoods. One-at-a-time variation around the
# frozen point (frozen value is always in the set and is the base run).
EQUITY_NEIGHBORHOOD = {
    "formation_bars": [240, 252, 264],
    "top_n": [7, 8, 9],
    "ivol_reject_quantile": [0.65, 0.70, 0.75],
}
CRYPTO_NEIGHBORHOOD = {
    "variance_lookback_days": [20, 22, 24, 26],
    "top_n": [2, 3],  # 3 is diagnostic only; 2 stays production
}
CRYPTO_REBALANCE_HOURS = 168  # always stays production

# Rolling window configuration (trading-day windows / step in trading days).
EQUITY_ROLLING = {"window": 252, "step": 126}
CRYPTO_ROLLING = {"window": 90, "step": 30}

# Block bootstrap: serial-dependence-aware block lengths and resample count.
BLOCK_BOOTSTRAP = {"equity_blocks": 63, "crypto_blocks": 22, "resamples": 2000, "seed": 20260812}

# Break-even cost multiplier: linearity is validated against the 2x/3x runs;
# when the relative error exceeds this, the estimate is marked unreliable.
BREAK_EVEN_MAX_RELATIVE_ERROR = 0.25

# Verdict thresholds for the neighborhood section (diagnostic classification,
# not a gate): max CAGR deviation from base in percentage points and max
# Sharpe deviation, plus sign flips.
VERDICT_CAGR_STABLE_PP = 2.0
VERDICT_CAGR_KNIFE_PP = 5.0
VERDICT_SHARPE_STABLE = 0.20
VERDICT_SHARPE_KNIFE = 0.50

# Fork-based multiprocessing for the expensive equity full-window runs. The
# loaded bars and the point-in-time universe are inherited by each child via
# copy-on-write (macOS default is spawn, which would reload them per worker).
POOL_WORKERS = 4


# --------------------------------------------------------------------------- #
# JSON / number helpers
# --------------------------------------------------------------------------- #


def _sanitize(obj):
    """Recursively make a structure JSON-serializable (NaN/Inf -> None)."""
    if isinstance(obj, float):
        return None if not math.isfinite(obj) else round(obj, 12)
    if isinstance(obj, (int, str, bool)) or obj is None:
        return obj
    if isinstance(obj, dict):
        return {str(key): _sanitize(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(value) for value in obj]
    if isinstance(obj, np.generic):
        return _sanitize(obj.item())
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_sanitize(payload), indent=2, sort_keys=True), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Metric helpers (from daily curves/returns)
# --------------------------------------------------------------------------- #


def _window_metrics(curve: list[float], timestamps: list[datetime]) -> dict[str, object]:
    """Return/Sortino/Sharpe/maxDD over a curve slice (daily series)."""
    returns = [
        curve[i] / curve[i - 1] - 1 for i in range(1, len(curve)) if curve[i - 1] > 0
    ]
    total_return = curve[-1] / curve[0] - 1 if curve and curve[0] > 0 else 0.0
    days = (timestamps[-1] - timestamps[0]).total_seconds() / 86_400 if len(timestamps) > 1 else 0.0
    years = days / 365.25
    cagr = (
        (curve[-1] / curve[0]) ** (1 / years) - 1
        if years > 0 and curve and curve[0] > 0 and curve[-1] > 0
        else None
    )
    mean = statistics.fmean(returns) if returns else 0.0
    std = statistics.stdev(returns) if len(returns) > 1 else 0.0
    downside = [value for value in returns if value < 0]
    downside_std = statistics.stdev(downside) if len(downside) > 1 else 0.0
    annualizer = math.sqrt(252.0)
    peak = curve[0]
    max_drawdown = 0.0
    for value in curve:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak)
    return {
        "return": round(total_return, 6),
        "cagr": round(cagr, 6) if cagr is not None else None,
        "sharpe": round(mean / std * annualizer, 4) if std > 0 else 0.0,
        "sortino": round(mean / downside_std * annualizer, 4) if downside_std > 0 else 0.0,
        "max_drawdown": round(max_drawdown, 6),
        "days": round(days, 1),
    }


def _annualized_from_daily(returns: list[float], periods_per_year: float = 252.0) -> dict:
    if not returns:
        return {"days": 0, "annualized_return": None, "sharpe": None, "win_rate": None}
    mean = statistics.fmean(returns)
    std = statistics.stdev(returns) if len(returns) > 1 else 0.0
    annualized = (1.0 + mean) ** periods_per_year - 1.0
    sharpe = mean / std * math.sqrt(periods_per_year) if std > 0 else 0.0
    wins = sum(1 for value in returns if value > 0)
    return {
        "days": len(returns),
        "annualized_return": round(annualized, 6),
        "sharpe": round(sharpe, 4),
        "win_rate": round(wins / len(returns), 4),
    }


def _rolling_windows(
    curve: list[float],
    timestamps: list[datetime],
    rebalances,
    window: int,
    step: int,
) -> list[dict[str, object]]:
    """Trailing-window metrics on the realized daily curve.

    ``rebalances`` supplies per-decision target exposure and expected turnover
    so each window reports average target exposure and summed decision
    turnover from the decisions made inside the window.
    """
    windows: list[dict[str, object]] = []
    for start in range(0, len(curve) - window + 1, step):
        sl = curve[start : start + window]
        ts = timestamps[start : start + window]
        metrics = _window_metrics(sl, ts)
        start_date = ts[0].date()
        end_date = ts[-1].date()
        decisions = [
            record
            for record in rebalances
            if start_date <= record.decision_timestamp.date() <= end_date
        ]
        exposures = [
            sum(weight for _symbol, weight in record.target_weights) for record in decisions
        ]
        turnover = sum(record.expected_turnover for record in decisions)
        windows.append(
            {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                **metrics,
                "avg_target_exposure": round(statistics.fmean(exposures), 4) if exposures else 0.0,
                "decisions_in_window": len(decisions),
                "turnover": round(turnover, 6),
            }
        )
    return windows


def _rolling_summary(windows: list[dict[str, object]]) -> dict[str, object]:
    cagrs = [float(item["cagr"]) for item in windows if item.get("cagr") is not None]
    sharpes = [float(item["sharpe"]) for item in windows]
    returns = [float(item["return"]) for item in windows]
    return {
        "window_count": len(windows),
        "mean_window_cagr": round(statistics.fmean(cagrs), 6) if cagrs else None,
        "min_window_cagr": round(min(cagrs), 6) if cagrs else None,
        "max_window_cagr": round(max(cagrs), 6) if cagrs else None,
        "negative_window_ratio": (
            round(sum(1 for value in returns if value < 0) / len(returns), 4) if returns else 0.0
        ),
        "mean_window_sharpe": round(statistics.fmean(sharpes), 4) if sharpes else None,
        "sharpe_std": round(statistics.stdev(sharpes), 4) if len(sharpes) > 1 else None,
        "min_window_sharpe": round(min(sharpes), 4) if sharpes else None,
        "max_window_sharpe": round(max(sharpes), 4) if sharpes else None,
    }


# --------------------------------------------------------------------------- #
# Regime classification (ex-post diagnostic only)
# --------------------------------------------------------------------------- #


def _regime_table(
    return_dates: list[date],
    returns: list[float],
    closes: dict[date, float],
    *,
    trend_sma: int,
    vol_window: int,
    label_up: str,
    label_down: str,
) -> dict[str, object]:
    """Per-regime strategy performance.

    Trend split is causal (close vs trailing SMA of its own series); the
    volatility split uses the FULL-SAMPLE median of the trailing realized-vol
    series (ex-post classification, documented — this is a diagnostic only and
    no regime filter is added to production).
    """
    series = pd.Series({day: closes[day] for day in sorted(closes)})
    sma = series.rolling(trend_sma, min_periods=trend_sma).mean()
    daily_rets = series.pct_change()
    trailing_vol = daily_rets.rolling(vol_window, min_periods=max(10, vol_window // 2)).std()
    vol_median = float(trailing_vol.median())
    trend: dict[date, str] = {}
    vol: dict[date, str] = {}
    for day in sorted(closes):
        if pd.notna(sma.get(day)) and series[day] > sma[day]:
            trend[day] = label_up
        else:
            trend[day] = label_down
        v = trailing_vol.get(day)
        if pd.notna(v) and v >= vol_median:
            vol[day] = "high_vol"
        else:
            vol[day] = "low_vol"

    buckets: dict[str, list[float]] = {}
    quadrants: dict[tuple[str, str], list[float]] = {}
    for day, ret in zip(return_dates, returns):
        if day not in trend or day not in vol:
            continue
        buckets.setdefault(trend[day], []).append(ret)
        buckets.setdefault(vol[day], []).append(ret)
        quadrants.setdefault((trend[day], vol[day]), []).append(ret)

    def _section(values: list[float]) -> dict[str, object]:
        return _annualized_from_daily(values)

    return {
        "methodology": (
            f"ex-post diagnostic ONLY (no production regime filters): trend = close vs "
            f"{trend_sma}d SMA of the benchmark; vol = trailing {vol_window}d realized vol "
            f"vs the full-sample median of that series"
        ),
        "vol_median_annualized": round(vol_median * math.sqrt(252), 4),
        "trend": {
            key: _section(values)
            for key, values in sorted(buckets.items())
            if key in {label_up, label_down}
        },
        "volatility": {
            key: _section(values)
            for key, values in sorted(buckets.items())
            if key in {"high_vol", "low_vol"}
        },
        "quadrants": {
            f"{t}+{v}": _section(values) for (t, v), values in sorted(quadrants.items())
        },
    }


# --------------------------------------------------------------------------- #
# Block bootstrap (serial-dependence-aware, NOT iid)
# --------------------------------------------------------------------------- #


def _block_bootstrap(
    returns: list[float], block: int, resamples: int, seed: int
) -> dict[str, object]:
    arr = np.asarray(returns, dtype=float)
    n = len(arr)
    rng = np.random.default_rng(seed)
    n_blocks = int(math.ceil(n / block))
    max_start = n - block
    annualized = np.empty(resamples, dtype=float)
    sharpes = np.empty(resamples, dtype=float)
    for index in range(resamples):
        starts = rng.integers(0, max_start + 1, size=n_blocks)
        sample = np.concatenate([arr[s : s + block] for s in starts])[:n]
        mean = float(sample.mean())
        std = float(sample.std(ddof=1)) if len(sample) > 1 else 0.0
        annualized[index] = (1.0 + mean) ** 252 - 1.0
        sharpes[index] = mean / std * math.sqrt(252.0) if std > 0 else 0.0

    def _pct(values: np.ndarray) -> dict[str, object]:
        return {
            "p5": round(float(np.percentile(values, 5)), 6),
            "p50": round(float(np.percentile(values, 50)), 6),
            "p95": round(float(np.percentile(values, 95)), 6),
        }

    observed_mean = float(arr.mean())
    observed_std = float(arr.std(ddof=1)) if n > 1 else 0.0
    return {
        "methodology": (
            f"overlapping-block bootstrap (block length {block} days, {resamples} resamples, "
            f"seed {seed}) on the strategy's daily NET return series; approximates the "
            f"uncertainty around the observed mean annualized return and Sharpe while "
            f"preserving serial dependence. Descriptive only, not a gate."
        ),
        "block_days": block,
        "resamples": resamples,
        "seed": seed,
        "observed": {
            "mean_annualized_return": round((1.0 + observed_mean) ** 252 - 1.0, 6),
            "sharpe": (
                round(observed_mean / observed_std * math.sqrt(252.0), 4)
                if observed_std > 0
                else 0.0
            ),
        },
        "mean_annualized_return_percentiles": _pct(annualized),
        "sharpe_percentiles": _pct(sharpes),
    }


# --------------------------------------------------------------------------- #
# Backtest run helpers
# --------------------------------------------------------------------------- #


def _run_result(config, bars, *, market, parameters, timeframe, start, end, multiplier, universe):
    run = run_product_backtest(
        config,
        bars,
        market=market,
        parameters=parameters,
        strategy_timeframe=timeframe,
        start=start,
        end=end,
        cost_multiplier=multiplier,
        persist=False,
        persist_artifacts=False,
        universe=universe,
    )
    return run.result


def _summary_stats(result) -> dict[str, object]:
    total_cost = result.slippage_cost_total + result.spread_cost_total + result.fee_total
    avg_equity = statistics.fmean(result.equity_curve) if result.equity_curve else 0.0
    gross_pnl = result.starting_equity * result.gross_return
    return {
        "return": round(result.total_return, 6),
        "gross_return": round(result.gross_return, 6),
        "cagr": round(result.cagr, 6),
        "sharpe": round(result.sharpe, 6),
        "sortino": round(result.sortino, 6),
        "calmar": round(result.calmar, 6),
        "max_drawdown": round(result.max_drawdown, 6),
        "turnover": round(result.turnover, 6),
        "fees_total": round(total_cost, 2),
        "fees_pct_of_avg_equity": round(total_cost / avg_equity, 6) if avg_equity > 0 else 0.0,
        "fees_pct_of_gross_pnl": (
            round(total_cost / abs(gross_pnl), 6) if abs(gross_pnl) > 1e-9 else None
        ),
        "avg_cash_ratio": round(result.average_cash_ratio, 4),
        "avg_exposure": round(1.0 - result.average_cash_ratio, 4),
        "rebalance_count": result.rebalance_count,
        "trade_count": len(result.closed_position_events),
        "avg_holdings": round(result.average_holdings_count, 3),
    }


def _benchmark_bars(bars: list[Bar], symbol: str, market: Market) -> list[Bar]:
    return sorted(
        (bar for bar in bars if bar.symbol == symbol and bar.market == market),
        key=lambda bar: bar.timestamp,
    )


def _benchmark_stats(bars: list[Bar], start: datetime) -> dict[str, object]:
    window = [bar for bar in bars if bar.timestamp >= start]
    if len(window) < 2:
        return {"return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0}
    returns = [window[i].close / window[i - 1].close - 1 for i in range(1, len(window))]
    mean = statistics.fmean(returns)
    std = statistics.stdev(returns) if len(returns) > 1 else 0.0
    peak = 0.0
    max_dd = 0.0
    for bar in window:
        peak = max(peak, bar.close)
        if peak > 0:
            max_dd = max(max_dd, (peak - bar.close) / peak)
    return {
        "return": round(window[-1].close / window[0].close - 1.0, 6),
        "sharpe": round(mean / std * math.sqrt(252.0), 4) if std > 0 else 0.0,
        "max_drawdown": round(max_dd, 6),
    }


# --------------------------------------------------------------------------- #
# Fork worker for the expensive equity runs
# --------------------------------------------------------------------------- #

_WORKER: dict[str, object] = {}


def _equity_worker(task: tuple[dict[str, object], float, datetime, datetime]):
    """Run one equity full-window backtest in a forked child.

    ``_WORKER`` (config/bars/universe) is set in the parent BEFORE the pool is
    created; fork copy-on-write makes it visible to every child. Returns
    (\"ok\", stats) or (\"error\", reason) so the strategy's own parameter
    validation rejections surface as data instead of killing the pool.
    """
    parameters, multiplier, start, end = task
    try:
        result = _run_result(
            _WORKER["config"],
            _WORKER["bars"],
            market=Market.EQUITY,
            parameters=parameters,
            timeframe="1d",
            start=start,
            end=end,
            multiplier=multiplier,
            universe=_WORKER["universe"],
        )
        return ("ok", _summary_stats(result))
    except ValueError as error:
        return ("error", str(error))


# --------------------------------------------------------------------------- #
# Equity pack
# --------------------------------------------------------------------------- #


def _neighborhood_verdict(base: dict, runs: dict[str, dict]) -> dict[str, object]:
    feasible = {key: run for key, run in runs.items() if not run.get("infeasible")}
    if not feasible:
        return {
            "verdict": "no feasible neighborhood runs",
            "max_cagr_deviation_pp": 0.0,
            "max_sharpe_deviation": 0.0,
            "sign_flips": 0,
            "rationale": (
                "every predeclared neighborhood point was rejected by parameter "
                "validation"
            ),
            "note": "See runs for the per-point rejection reasons.",
        }
    base_cagr = base["cagr"] if base["cagr"] is not None else 0.0
    base_sharpe = base["sharpe"]
    deviations_cagr_pp = []
    deviations_sharpe = []
    sign_flips = 0
    for run in feasible.values():
        run_cagr = run["cagr"] if run["cagr"] is not None else 0.0
        deviations_cagr_pp.append((run_cagr - base_cagr) * 100.0)
        deviations_sharpe.append(run["sharpe"] - base_sharpe)
        if (run["return"] > 0) != (base["return"] > 0):
            sign_flips += 1
    max_cagr_pp = max(abs(value) for value in deviations_cagr_pp)
    max_sharpe = max(abs(value) for value in deviations_sharpe)
    if sign_flips > 0 or max_cagr_pp > VERDICT_CAGR_KNIFE_PP or max_sharpe > VERDICT_SHARPE_KNIFE:
        verdict = "knife-edge"
    elif max_cagr_pp <= VERDICT_CAGR_STABLE_PP and max_sharpe <= VERDICT_SHARPE_STABLE:
        verdict = "stable plateau"
    else:
        verdict = "moderately sensitive"
    rationale = (
        f"max CAGR deviation {max_cagr_pp:.2f}pp, max Sharpe deviation {max_sharpe:.2f}, "
        f"{sign_flips} sign flip(s) across {len(feasible)} feasible neighborhood run(s)"
    )
    if len(feasible) < len(runs):
        rationale += (
            f" ({len(runs) - len(feasible)} predeclared point(s) rejected by the strategy's "
            "own parameter validation; see runs)"
        )
    return {
        "verdict": verdict,
        "max_cagr_deviation_pp": round(max_cagr_pp, 4),
        "max_sharpe_deviation": round(max_sharpe, 4),
        "sign_flips": sign_flips,
        "rationale": rationale,
        "note": (
            "Diagnostic classification only (NOT a gate): knife-edge = any sign flip or "
            f"CAGR deviation > {VERDICT_CAGR_KNIFE_PP}pp or Sharpe deviation > "
            f"{VERDICT_SHARPE_KNIFE}; stable plateau = CAGR deviation <= "
            f"{VERDICT_CAGR_STABLE_PP}pp and Sharpe deviation <= {VERDICT_SHARPE_STABLE}; "
            "otherwise moderately sensitive. Production keeps the frozen parameters "
            "regardless of the verdict."
        ),
    }


def run_equity_pack(config) -> dict[str, object]:
    print("=== EQUITY ROBUSTNESS PACK — equity_residual_momentum_v1 ===")
    t0 = datetime.now(UTC)
    bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
    print(f"  loaded {len(bars):,} 1d bars")
    universe = build_product_universe(config, bars, market=Market.EQUITY, end=EQUITY_END)
    print(f"  PIT universe: {len(universe.snapshots)} snapshots")

    base_params = dict(config.strategy.equity.parameters)
    print("  full-period 1x base run...")
    base = _run_result(
        config, bars, market=Market.EQUITY, parameters=base_params, timeframe="1d",
        start=EQUITY_START, end=EQUITY_END, multiplier=1.0, universe=universe,
    )
    base_stats = _summary_stats(base)
    print(
        f"  base: ret={base_stats['return']:.2%} cagr={base_stats['cagr']:.2%} "
        f"sharpe={base_stats['sharpe']:.2f} mdd={base_stats['max_drawdown']:.2%}"
    )

    top_contributors = sorted(
        base.per_symbol, key=lambda item: -item.realized_pnl
    )
    top_symbols = [item.symbol for item in top_contributors if item.realized_pnl > 0][:6]
    print(f"  top contributors: {top_symbols}")

    # ---- neighborhood + cost stress + jackknife, one forked pool ----
    # All remaining full-window runs share the same bars/universe and are
    # independent, so they run concurrently in forked children (copy-on-write
    # inheritance; nothing is pickled across the pipe).
    pool_tasks: list[tuple[str, tuple[dict, float, datetime, datetime]]] = []

    for parameter, values in EQUITY_NEIGHBORHOOD.items():
        for value in values:
            if float(value) == float(base_params[parameter]):
                continue  # base run covers the frozen point
            params = {**base_params, parameter: value}
            label = f"neigh_{parameter}={value}"
            pool_tasks.append((label, (params, 1.0, EQUITY_START, EQUITY_END)))

    for multiplier in (2.0, 3.0):
        label = f"cost_{multiplier:g}x"
        pool_tasks.append((label, (base_params, multiplier, EQUITY_START, EQUITY_END)))

    if len(top_symbols) >= 1:
        dropped = [*base_params["exclude_symbols"], top_symbols[0]]
        params = base_params | {"exclude_symbols": dropped}
        pool_tasks.append(("drop_top1", (params, 1.0, EQUITY_START, EQUITY_END)))
    if len(top_symbols) >= 2:
        dropped2 = [*base_params["exclude_symbols"], top_symbols[0], top_symbols[1]]
        params = base_params | {"exclude_symbols": dropped2}
        pool_tasks.append(("drop_top1_top2", (params, 1.0, EQUITY_START, EQUITY_END)))
    for rank in (2, 3):  # top-3, top-4 excluded one at a time
        if len(top_symbols) > rank:
            dropped = [*base_params["exclude_symbols"], top_symbols[rank]]
            params = base_params | {"exclude_symbols": dropped}
            label = f"drop_top{rank + 1}"
            pool_tasks.append((label, (params, 1.0, EQUITY_START, EQUITY_END)))

    print(
        f"  running {len(pool_tasks)} full-window runs in a forked pool "
        f"({POOL_WORKERS} workers)..."
    )
    _WORKER["config"] = config
    _WORKER["bars"] = bars
    _WORKER["universe"] = universe
    ctx = multiprocessing.get_context("fork")
    with ctx.Pool(processes=POOL_WORKERS) as pool:
        results = pool.map(_equity_worker, [task[1] for task in pool_tasks])

    neighborhood_runs: dict[str, dict] = {}
    cost_runs: dict[str, dict] = {"1x": base_stats}
    jackknife: dict[str, dict] = {}
    for (key, _task), outcome in zip(pool_tasks, results):
        if key.startswith("neigh_"):
            label = key[len("neigh_") :]
            if outcome[0] == "ok":
                neighborhood_runs[label] = outcome[1]
                print(f"  neighborhood {label}: ret={outcome[1]['return']:.2%}")
            else:
                # The strategy's own parameter validation rejects the point
                # (e.g. formation 264 would need beta_window >= 285 while the
                # frozen beta window is 273). Record it instead of hiding it:
                # the constraint boundary is itself robustness evidence.
                neighborhood_runs[label] = {"infeasible": True, "reason": outcome[1]}
                print(f"  neighborhood {label}: INFEASIBLE ({outcome[1]})")
        elif key.startswith("cost_"):
            cost_runs[key[len("cost_") :]] = outcome[1]
            print(f"  cost stress {key[len('cost_'):]}: ret={outcome[1]['return']:.2%}")
        else:
            jackknife[key] = outcome[1]
            print(f"  jackknife {key}: ret={outcome[1]['return']:.2%}")

    # ---- rolling windows on the base run's realized curve ----
    rebalances = base.rebalances
    rolling = _rolling_windows(
        list(base.equity_curve),
        list(base.curve_timestamps),
        rebalances,
        EQUITY_ROLLING["window"],
        EQUITY_ROLLING["step"],
    )
    rolling_summary = _rolling_summary(rolling)

    # ---- regime analysis (SPY-based, ex-post diagnostic) ----
    spy = _benchmark_bars(bars, "SPY", Market.EQUITY)
    spy_closes = {bar.timestamp.date(): bar.close for bar in spy}
    return_dates = [ts.date() for ts in base.curve_timestamps]
    curve_returns = [
        base.equity_curve[i] / base.equity_curve[i - 1] - 1
        for i in range(1, len(base.equity_curve))
        if base.equity_curve[i - 1] > 0
    ]
    return_dates = return_dates[1:]
    regimes = _regime_table(
        return_dates,
        curve_returns,
        spy_closes,
        trend_sma=200,
        vol_window=63,
        label_up="bull",
        label_down="bear",
    )

    # ---- break-even cost multiplier ----
    break_even = _break_even(cost_runs)
    print(
        f"  break-even cost multiplier: {break_even['multiplier']} "
        f"(reliable={break_even['reliable']})"
    )

    # ---- block bootstrap ----
    bootstrap = _block_bootstrap(
        curve_returns,
        BLOCK_BOOTSTRAP["equity_blocks"],
        BLOCK_BOOTSTRAP["resamples"],
        BLOCK_BOOTSTRAP["seed"],
    )

    report: dict[str, object] = {
        "strategy": "equity_residual_momentum_v1",
        "market": "equity",
        "generated_at": datetime.now(UTC).isoformat(),
        "frozen_parameters": base_params,
        "parameters_source": "config/settings.yaml (read-only; NEVER modified by this script)",
        "dataset": EQUITY_CSV.name,
        "evaluation_window": [EQUITY_START.isoformat(), EQUITY_END.isoformat()],
        "warmup": "2016 full year (formation + beta window + scanner lookback)",
        "benchmarks": {"SPY": _benchmark_stats(spy, EQUITY_START)},
        "base_run": base_stats,
        "neighborhood": {
            "methodology": (
                "one-at-a-time around the frozen point: each run changes exactly one "
                "parameter to one predeclared neighbor; NOT a grid, NOT an optimizer. "
                "Frozen values stay production regardless of outcomes."
            ),
            "base": base_stats,
            "runs": neighborhood_runs,
            "verdict": _neighborhood_verdict(base_stats, neighborhood_runs),
        },
        "rolling": {
            "methodology": (
                f"{EQUITY_ROLLING['window']}d trailing windows, {EQUITY_ROLLING['step']}d step, "
                "computed on the full-period run's realized daily curve (the path the "
                "strategy actually experienced)"
            ),
            "windows": rolling,
            "summary": rolling_summary,
        },
        "regimes": regimes,
        "jackknife": {
            "methodology": (
                "full-period rerun with the listed symbol removed from BOTH the PIT universe "
                "build and the strategy data. Diagnostic only; NO symbol is excluded from "
                "production based on these results."
            ),
            "base": base_stats,
            "top_contributors": [
                {"symbol": item.symbol, "realized_pnl": round(item.realized_pnl, 2)}
                for item in top_contributors[:6]
            ],
            "runs": jackknife,
        },
        "cost_stress": {
            "methodology": (
                "slippage_bps 2, spread_bps 2 and equity regulatory fees at the configured "
                "effective-dated schedule, scaled by 1x/2x/3x (crypto taker fee does not apply "
                "to the equity sleeve)."
            ),
            "runs": cost_runs,
            "break_even": break_even,
        },
        "bootstrap": bootstrap,
    }
    print(f"  elapsed {datetime.now(UTC) - t0}")
    return report


# --------------------------------------------------------------------------- #
# Crypto pack
# --------------------------------------------------------------------------- #


def run_crypto_pack(config) -> dict[str, object]:
    print("=== CRYPTO ROBUSTNESS PACK — crypto_low_variance_v1 ===")
    t0 = datetime.now(UTC)
    bars_1h = load_csv_bars(CRYPTO_CSV, default_market=Market.CRYPTO)
    bars_1d = aggregate_historical_bars(bars_1h, "1d", require_complete=True)
    print(f"  loaded {len(bars_1h):,} 1h bars -> {len(bars_1d):,} 1d bars")
    universe = build_product_universe(
        config, bars_1h, market=Market.CRYPTO, end=CRYPTO_END, timeframe="1h"
    )
    print(f"  PIT universe (1h scanner): {len(universe.snapshots)} snapshots")

    base_params = dict(config.strategy.crypto.parameters)
    base_params["rebalance_hours"] = CRYPTO_REBALANCE_HOURS  # always production
    print("  full-period 1x base run...")
    base = _run_result(
        config, bars_1d, market=Market.CRYPTO, parameters=base_params, timeframe="1d",
        start=CRYPTO_EVAL_START, end=CRYPTO_END, multiplier=1.0, universe=universe,
    )
    base_stats = _summary_stats(base)
    print(
        f"  base: ret={base_stats['return']:.2%} cagr={base_stats['cagr']:.2%} "
        f"sharpe={base_stats['sharpe']:.2f} mdd={base_stats['max_drawdown']:.2%}"
    )

    # ---- neighborhood ----
    neighborhood_runs: dict[str, dict] = {}
    for parameter, values in CRYPTO_NEIGHBORHOOD.items():
        for value in values:
            if float(value) == float(base_params[parameter]):
                continue
            params = {**base_params, parameter: value}
            print(f"  neighborhood {parameter}={value}...")
            result = _run_result(
                config, bars_1d, market=Market.CRYPTO, parameters=params, timeframe="1d",
                start=CRYPTO_EVAL_START, end=CRYPTO_END, multiplier=1.0, universe=universe,
            )
            neighborhood_runs[f"{parameter}={value}"] = _summary_stats(result)

    # ---- cost stress ----
    cost_runs: dict[str, dict] = {"1x": base_stats}
    for multiplier in (2.0, 3.0):
        print(f"  cost stress {multiplier:g}x...")
        result = _run_result(
            config, bars_1d, market=Market.CRYPTO, parameters=base_params, timeframe="1d",
            start=CRYPTO_EVAL_START, end=CRYPTO_END, multiplier=multiplier, universe=universe,
        )
        cost_runs[f"{multiplier:g}x"] = _summary_stats(result)

    # ---- jackknife ----
    top_contributors = sorted(base.per_symbol, key=lambda item: -item.realized_pnl)
    top_symbols = [item.symbol for item in top_contributors if item.realized_pnl > 0]
    drop_symbols: list[str] = []
    if top_symbols:
        drop_symbols.append(top_symbols[0])
    for required in ("BTC/USD", "ETH/USD"):
        if required not in drop_symbols:
            drop_symbols.append(required)
    jackknife: dict[str, dict] = {}
    for symbol in drop_symbols:
        key = f"drop_{symbol.replace('/', '_')}"
        print(f"  jackknife {key}...")
        bars_1h_excl = [bar for bar in bars_1h if bar.symbol != symbol]
        bars_1d_excl = [bar for bar in bars_1d if bar.symbol != symbol]
        universe_excl = build_product_universe(
            config, bars_1h_excl, market=Market.CRYPTO, end=CRYPTO_END, timeframe="1h"
        )
        result = _run_result(
            config, bars_1d_excl, market=Market.CRYPTO, parameters=base_params, timeframe="1d",
            start=CRYPTO_EVAL_START, end=CRYPTO_END, multiplier=1.0, universe=universe_excl,
        )
        jackknife[key] = _summary_stats(result)
        print(f"  jackknife {key}: ret={jackknife[key]['return']:.2%}")

    # ---- rolling windows ----
    rolling = _rolling_windows(
        list(base.equity_curve),
        list(base.curve_timestamps),
        base.rebalances,
        CRYPTO_ROLLING["window"],
        CRYPTO_ROLLING["step"],
    )
    rolling_summary = _rolling_summary(rolling)

    # ---- regime analysis (BTC-based, ex-post diagnostic) ----
    btc = _benchmark_bars(bars_1d, "BTC/USD", Market.CRYPTO)
    btc_closes = {bar.timestamp.date(): bar.close for bar in btc}
    return_dates = [ts.date() for ts in base.curve_timestamps]
    curve_returns = [
        base.equity_curve[i] / base.equity_curve[i - 1] - 1
        for i in range(1, len(base.equity_curve))
        if base.equity_curve[i - 1] > 0
    ]
    return_dates = return_dates[1:]
    regimes = _regime_table(
        return_dates,
        curve_returns,
        btc_closes,
        trend_sma=30,
        vol_window=22,
        label_up="btc_up",
        label_down="btc_flat_down",
    )

    break_even = _break_even(cost_runs)
    be_mult = break_even["multiplier"]
    be_text = f"{be_mult}" if be_mult is not None else "n/a (negative gross at 1x costs)"
    print(f"  break-even cost multiplier: {be_text} (reliable={break_even['reliable']})")

    bootstrap = _block_bootstrap(
        curve_returns,
        BLOCK_BOOTSTRAP["crypto_blocks"],
        BLOCK_BOOTSTRAP["resamples"],
        BLOCK_BOOTSTRAP["seed"],
    )

    eth = _benchmark_bars(bars_1d, "ETH/USD", Market.CRYPTO)
    report: dict[str, object] = {
        "strategy": "crypto_low_variance_v1",
        "market": "crypto",
        "generated_at": datetime.now(UTC).isoformat(),
        "frozen_parameters": base_params,
        "parameters_source": "config/settings.yaml (read-only; NEVER modified by this script)",
        "dataset": CRYPTO_CSV.name,
        "evaluation_window": [CRYPTO_EVAL_START.isoformat(), CRYPTO_END.isoformat()],
        "warmup": "2024-08-01 .. 2024-11-30 (variance lookback + scanner lookback)",
        "benchmarks": {
            "BTC": _benchmark_stats(btc, CRYPTO_EVAL_START),
            "ETH": _benchmark_stats(eth, CRYPTO_EVAL_START),
        },
        "base_run": base_stats,
        "neighborhood": {
            "methodology": (
                "one-at-a-time around the frozen point; rebalance_hours stays 168 in every "
                "run. top_n=3 is a diagnostic only (3 positions at ~3.33% each after the "
                "0.10 sleeve cap); 2 stays production. NOT a grid, NOT an optimizer."
            ),
            "base": base_stats,
            "runs": neighborhood_runs,
            "verdict": _neighborhood_verdict(base_stats, neighborhood_runs),
        },
        "rolling": {
            "methodology": (
                f"{CRYPTO_ROLLING['window']}d trailing windows, {CRYPTO_ROLLING['step']}d step, "
                "computed on the full-period run's realized daily curve"
            ),
            "windows": rolling,
            "summary": rolling_summary,
        },
        "regimes": regimes,
        "jackknife": {
            "methodology": (
                "rerun with the listed symbol removed from BOTH the PIT universe build (1h) "
                "and the strategy data (1d). Diagnostic only; NO symbol is excluded from "
                "production based on these results."
            ),
            "base": base_stats,
            "top_contributors": [
                {"symbol": item.symbol, "realized_pnl": round(item.realized_pnl, 2)}
                for item in top_contributors[:6]
            ],
            "runs": jackknife,
        },
        "cost_stress": {
            "methodology": (
                "slippage_bps 2, spread_bps 2 and crypto taker fee 25 bps at the configured "
                "rates, scaled by 1x/2x/3x."
            ),
            "runs": cost_runs,
            "break_even": break_even,
        },
        "bootstrap": bootstrap,
    }
    print(f"  elapsed {datetime.now(UTC) - t0}")
    return report


# --------------------------------------------------------------------------- #
# Break-even cost multiplier
# --------------------------------------------------------------------------- #


def _break_even(cost_runs: dict[str, dict]) -> dict[str, object]:
    """Estimate the cost multiplier at which net return crosses zero.

    Model: net(m) = (1 + gross) * (1 - m * r1) - 1, where gross is the
    cost-adjusted gross return from the 1x run and r1 = total 1x costs / avg
    1x equity. Costs scale linearly with the multiplier; avg equity moves
    slightly as returns compound differently, which is the nonlinearity the
    2x/3x runs measure.
    """
    one = cost_runs["1x"]
    gross = one["gross_return"]
    r1 = one["fees_pct_of_avg_equity"]
    if r1 is None or r1 <= 0 or gross is None:
        return {
            "multiplier": None,
            "reliable": False,
            "reason": "no positive 1x cost ratio or gross return available",
        }
    if gross <= 0:
        return {
            "multiplier": None,
            "reliable": False,
            "reason": (
                "gross return is not positive at 1x costs — the strategy already loses "
                "before the cost stress, so break-even sits below the current cost level"
            ),
        }
    multiplier = gross / ((1.0 + gross) * r1)
    # Validate the linearity assumption against the actual 2x/3x runs.
    errors: dict[str, object] = {}
    worst = 0.0
    for key in ("2x", "3x"):
        if key not in cost_runs:
            continue
        actual = cost_runs[key]["return"]
        m = float(key[0])
        predicted = (1.0 + gross) * (1.0 - m * r1) - 1.0
        denom = max(abs(actual), 1e-6)
        relative = abs(predicted - actual) / denom
        errors[key] = {
            "predicted_net_return": round(predicted, 6),
            "actual_net_return": round(actual, 6),
            "relative_error": round(relative, 4),
        }
        worst = max(worst, relative)
    reliable = worst <= BREAK_EVEN_MAX_RELATIVE_ERROR
    note = (
        f"linearity check against 2x/3x runs: worst relative error {worst:.1%} "
        f"(threshold {BREAK_EVEN_MAX_RELATIVE_ERROR:.0%})"
    )
    if not reliable:
        note += (
            "; nonlinear portfolio effects (changing avg equity, position sizing drift) "
            "make the break-even estimate unreliable"
        )
    if multiplier > 100:
        note += "; multiplier beyond tested range — costs are a negligible drag"
    return {
        "multiplier": round(multiplier, 3),
        "reliable": reliable,
        "linearity_check": errors,
        "note": note,
    }


# --------------------------------------------------------------------------- #
# Markdown summary
# --------------------------------------------------------------------------- #


def _fmt_pct(value) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "n/a"
    return f"{value * 100:.2f}%"


def _fmt_num(value, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "n/a"
    return f"{value:.{digits}f}"


def _market_markdown(report: dict) -> list[str]:
    lines: list[str] = []
    title = (
        f"## {report['strategy']} ({report['market']})"
    )
    lines.append(title)
    lines.append("")
    base = report["base_run"]
    lines.append(
        f"- Base 1x run: return {_fmt_pct(base['return'])}, CAGR {_fmt_pct(base['cagr'])}, "
        f"Sharpe {_fmt_num(base['sharpe'])}, Sortino {_fmt_num(base['sortino'])}, "
        f"maxDD {_fmt_pct(base['max_drawdown'])}, turnover {_fmt_num(base['turnover'], 4)}"
    )
    neigh = report["neighborhood"]
    lines.append(
        f"- Neighborhood verdict: **{neigh['verdict']['verdict']}** — "
        f"{neigh['verdict']['rationale']}."
    )
    runs = neigh["runs"]
    for key, stats in sorted(runs.items()):
        if stats.get("infeasible"):
            lines.append(f"  - `{key}`: INFEASIBLE — {stats['reason']}")
            continue
        lines.append(
            f"  - `{key}`: return {_fmt_pct(stats['return'])}, "
            f"CAGR {_fmt_pct(stats['cagr'])}, "
            f"Sharpe {_fmt_num(stats['sharpe'])}, maxDD {_fmt_pct(stats['max_drawdown'])}"
        )
    roll = report["rolling"]["summary"]
    lines.append(
        f"- Rolling ({report['rolling']['methodology']}): {roll['window_count']} windows, "
        f"mean window CAGR {_fmt_pct(roll['mean_window_cagr'])}, "
        f"min {_fmt_pct(roll['min_window_cagr'])}, max {_fmt_pct(roll['max_window_cagr'])}, "
        f"negative windows {_fmt_pct(roll['negative_window_ratio'])}, "
        f"mean window Sharpe {_fmt_num(roll['mean_window_sharpe'])}"
    )
    regimes = report["regimes"]
    for bucket, label in (("trend", "trend"), ("volatility", "volatility")):
        parts = []
        for key, stats in sorted(regimes[bucket].items()):
            parts.append(
                f"{key}: ann {_fmt_pct(stats['annualized_return'])} "
                f"Sharpe {_fmt_num(stats['sharpe'])} ({stats['days']}d)"
            )
        lines.append(f"- Regime ({label}, ex-post diagnostic): " + "; ".join(parts))
    jk = report["jackknife"]
    parts = [f"base {_fmt_pct(jk['base']['return'])}"]
    for key, stats in sorted(jk["runs"].items()):
        parts.append(f"{key} {_fmt_pct(stats['return'])}")
    lines.append("- Jackknife (diagnostic only): " + "; ".join(parts))
    cs = report["cost_stress"]
    parts = []
    for key in ("1x", "2x", "3x"):
        stats = cs["runs"][key]
        parts.append(f"{key} {_fmt_pct(stats['return'])} (Sharpe {_fmt_num(stats['sharpe'])})")
    be = cs["break_even"]
    if be.get("reliable"):
        be_str = f"break-even multiplier {_fmt_num(be['multiplier'])} (reliable)"
    else:
        detail = be.get("reason") or be.get("note") or ""
        be_str = f"break-even multiplier {_fmt_num(be['multiplier'])} UNRELIABLE: {detail}"
    lines.append("- Cost stress: " + "; ".join(parts) + f"; {be_str}")
    bt = report["bootstrap"]
    obs = bt["observed"]
    pct = bt["mean_annualized_return_percentiles"]
    sp = bt["sharpe_percentiles"]
    lines.append(
        f"- Block bootstrap: block length {bt['block_days']}d on daily net returns; "
        f"observed ann mean {_fmt_pct(obs['mean_annualized_return'])}, "
        f"p5/p50/p95 {_fmt_pct(pct['p5'])}/{_fmt_pct(pct['p50'])}/{_fmt_pct(pct['p95'])}; "
        f"Sharpe p5/p50/p95 {_fmt_num(sp['p5'])}/{_fmt_num(sp['p50'])}/{_fmt_num(sp['p95'])}"
    )
    lines.append("")
    return lines


def _write_summary_file(equity: dict | None, crypto: dict | None) -> None:
    lines: list[str] = []
    if equity:
        lines += _market_markdown(equity)
    if crypto:
        lines += _market_markdown(crypto)
    header = [
        "# Strategy robustness pack — summary",
        "",
        "Non-optimizing robustness evidence for the two FROZEN validated strategies. "
        "Production parameters were NOT changed (read from config/settings.yaml, never "
        "modified). Offline backtests only — no paper/live orders were placed.",
        "",
        "Generated by `scripts/strategy_robustness.py`; the combined-portfolio section is "
        "appended by `scripts/combined_portfolio_validation.py`.",
        "",
        *lines,
    ]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "summary.md").write_text("\n".join(header) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="strategy_robustness")
    parser.add_argument("--market", choices=("equity", "crypto", "both"), default="both")
    parser.add_argument("--pool-workers", type=int, default=POOL_WORKERS)
    args = parser.parse_args(argv)
    config = load_config()

    reports: dict[str, dict] = {}
    if args.market in ("equity", "both"):
        reports["equity"] = run_equity_pack(config)
        _write_json(OUTPUT_DIR / "equity.json", reports["equity"])
    if args.market in ("crypto", "both"):
        reports["crypto"] = run_crypto_pack(config)
        _write_json(OUTPUT_DIR / "crypto.json", reports["crypto"])

    _write_summary_file(reports.get("equity"), reports.get("crypto"))
    print("\nDone. Reports written to", OUTPUT_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
