#!/usr/bin/env python3
"""Combined equity + crypto portfolio validation (the ACTUAL product combo).

Validates the production product combination historically through the same
harness as the strategy bake-off: the equity sleeve (equity_residual_momentum_v1,
maximum allocation 0.50) and the crypto sleeve (crypto_low_variance_v1, maximum
allocation 0.10) run as two independently-sized sleeves of ONE account, exactly
as the paper runtime sizes them. The shared risk layer keeps the combined
account inside gross exposure 0.60 / cash buffer 0.40 / crypto exposure 0.10 /
crypto position 0.05 / 2 crypto positions.

Combination model (matches production sizing):
  combined_value(t) = equity_sleeve_value(t) + crypto_sleeve_value(t) - initial_cash
Both sleeves are sized against the same 100k account and hold the leftover as
the account's single cash pool, so their values add and the cash is counted
once. (If the equity sleeve earns 0.5*r and the crypto sleeve 0.1*r_c on the
shared account, the combined return is exactly 0.5*r + 0.1*r_c.)

NO allocation ratio is optimized. Production parameters and risk limits are
read from config/settings.yaml and NEVER modified.

Offline-only: no broker calls, no paper/live orders.

Outputs: reports/robustness/combined.json + combined section appended to
reports/robustness/summary.md
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

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

# Diversification verdict thresholds (evidence-based diagnostic, not a gate).
CORR_DIVERSIFIED = 0.5
CORR_LIMITED = 0.7


def _sanitize(obj):
    if isinstance(obj, float):
        return None if not math.isfinite(obj) else round(obj, 12)
    if isinstance(obj, (int, str, bool)) or obj is None:
        return obj
    if isinstance(obj, dict):
        return {str(key): _sanitize(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(value) for value in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_sanitize(payload), indent=2, sort_keys=True), encoding="utf-8")


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denom = math.sqrt(sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys))
    return cov / denom if denom > 0 else None


def _metrics_from_daily(dates: list[datetime], returns: list[float]) -> dict[str, object]:
    """CAGR/Sharpe/Sortino/maxDD/Calmar from a daily return series."""
    if len(returns) < 2 or not dates:
        return {}
    days = (dates[-1] - dates[0]).total_seconds() / 86_400
    years = days / 365.25
    total_return = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        total_return *= 1.0 + value
        peak = max(peak, total_return)
        max_drawdown = max(max_drawdown, (peak - total_return) / peak)
    cagr = total_return ** (1 / years) - 1 if years > 0 and total_return > 0 else None
    mean = statistics.fmean(returns)
    std = statistics.stdev(returns) if len(returns) > 1 else 0.0
    downside = [value for value in returns if value < 0]
    downside_std = statistics.stdev(downside) if len(downside) > 1 else 0.0
    annualizer = math.sqrt(252.0)
    sharpe = mean / std * annualizer if std > 0 else 0.0
    sortino = mean / downside_std * annualizer if downside_std > 0 else 0.0
    calmar = cagr / max_drawdown if cagr is not None and max_drawdown > 0 else None
    return {
        "total_return": round(total_return - 1.0, 6),
        "cagr": round(cagr, 6) if cagr is not None else None,
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "max_drawdown": round(max_drawdown, 6),
        "calmar": round(calmar, 6) if calmar is not None else None,
        "days": round(days, 1),
    }


def _benchmark_bars(bars: list[Bar], symbol: str, market: Market) -> list[Bar]:
    return sorted(
        (bar for bar in bars if bar.symbol == symbol and bar.market == market),
        key=lambda bar: bar.timestamp,
    )


def _benchmark_stats(bars: list[Bar], start: datetime) -> dict[str, object]:
    window = [bar for bar in bars if bar.timestamp >= start]
    if len(window) < 2:
        return {"return": 0.0}
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


def _window_sleeve_stats(result, start: datetime, end: datetime) -> dict[str, object]:
    """Cost/turnover/rebalance stats scoped to [start, end] from a full run."""
    window_fills = [fill for fill in result.fills if start <= fill.timestamp <= end]
    window_cost = sum(fill.slippage_cost + fill.spread_cost + fill.fee for fill in window_fills)
    traded_notional = sum(fill.qty * fill.reference_price for fill in window_fills)
    window_rebalances = [record for record in result.rebalances if start <= record.decision_timestamp <= end]
    window_closes = [event for event in result.closed_position_events if start <= event[0] <= end]
    curve_in_window = [
        value
        for value, ts in zip(result.equity_curve, result.curve_timestamps)
        if start.date() <= ts.date() <= end.date()
    ]
    avg_equity = statistics.fmean(curve_in_window) if curve_in_window else 0.0
    return {
        "fees_total": round(window_cost, 2),
        "fees_pct_of_avg_equity": (round(window_cost / avg_equity, 6) if avg_equity > 0 else 0.0),
        "turnover": round(traded_notional / avg_equity, 6) if avg_equity > 0 else 0.0,
        "rebalance_count": len(window_rebalances),
        "trade_count": len(window_closes),
        "avg_equity_in_window": round(avg_equity, 2),
    }


def _daily_exposure(weight_history, dates: list[datetime]) -> list[float]:
    """Forward-filled target exposure per date (proxy for daily exposure:
    target weights at the latest decision at-or-before each date)."""
    decisions = sorted(weight_history, key=lambda item: item[0])
    exposures: list[float] = []
    index = 0
    for day in dates:
        while index + 1 < len(decisions) and decisions[index + 1][0] <= day:
            index += 1
        if index < len(decisions) and decisions[index][0] <= day:
            exposures.append(sum(decisions[index][1].values()))
        else:
            exposures.append(0.0)
    return exposures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="combined_portfolio_validation",
        description=(
            "Historically validate the ACTUAL product combination: equity "
            "residual momentum (allocation 0.50) + crypto low variance "
            "(allocation 0.10) on one shared account with the frozen risk caps."
        ),
    )
    parser.parse_args(argv)
    config = load_config()
    initial_cash = config.backtest.initial_cash
    print("=== COMBINED PORTFOLIO VALIDATION ===")
    print(f"  initial cash: {initial_cash:,.0f}")

    print("Equity sleeve (equity_residual_momentum_v1, allocation 0.50)...")
    eq_bars = load_csv_bars(EQUITY_CSV, default_market=Market.EQUITY)
    eq_universe = build_product_universe(config, eq_bars, market=Market.EQUITY, end=EQUITY_END)
    eq_run = run_product_backtest(
        config,
        eq_bars,
        market=Market.EQUITY,
        parameters=dict(config.strategy.equity.parameters),
        strategy_timeframe="1d",
        start=EQUITY_START,
        end=EQUITY_END,
        cost_multiplier=1.0,
        persist=False,
        persist_artifacts=False,
        universe=eq_universe,
    )
    eq_result = eq_run.result
    print(f"  full-period ret={eq_result.total_return:.2%} (window metrics below are scoped to the common window)")

    print("Crypto sleeve (crypto_low_variance_v1, allocation 0.10)...")
    c_bars_1h = load_csv_bars(CRYPTO_CSV, default_market=Market.CRYPTO)
    c_bars_1d = aggregate_historical_bars(c_bars_1h, "1d", require_complete=True)
    c_universe = build_product_universe(config, c_bars_1h, market=Market.CRYPTO, end=CRYPTO_END, timeframe="1h")
    c_run = run_product_backtest(
        config,
        c_bars_1d,
        market=Market.CRYPTO,
        parameters=dict(config.strategy.crypto.parameters),
        strategy_timeframe="1d",
        start=CRYPTO_EVAL_START,
        end=CRYPTO_END,
        cost_multiplier=1.0,
        persist=False,
        persist_artifacts=False,
        universe=c_universe,
    )
    c_result = c_run.result
    print(f"  crypto sleeve ret={c_result.total_return:.2%}")

    # ---- common daily window (equity trading days intersect crypto days) ----
    eq_by_date = {
        ts.date(): value
        for ts, value in zip(eq_result.curve_timestamps, eq_result.equity_curve)
        if ts.date() >= CRYPTO_EVAL_START.date()
    }
    c_by_date = {ts.date(): value for ts, value in zip(c_result.curve_timestamps, c_result.equity_curve)}
    common_dates = sorted(set(eq_by_date) & set(c_by_date))
    if len(common_dates) < 60:
        raise RuntimeError(
            f"Common window too short: {len(common_dates)} days (equity {len(eq_by_date)}, crypto {len(c_by_date)})"
        )
    window_start = datetime.combine(common_dates[0], datetime.min.time(), tzinfo=UTC)
    window_end = (
        datetime.combine(common_dates[-1], datetime.min.time(), tzinfo=UTC) + timedelta(days=1) - timedelta(seconds=1)
    )
    print(f"  common window: {common_dates[0]} .. {common_dates[-1]} ({len(common_dates)} trading days)")

    eq_window = [eq_by_date[day] for day in common_dates]
    c_window = [c_by_date[day] for day in common_dates]
    combined_window = [eq_value + c_value - initial_cash for eq_value, c_value in zip(eq_window, c_window)]
    dates = [datetime.combine(day, datetime.min.time(), tzinfo=UTC) for day in common_dates]

    def _returns(series: list[float]) -> list[float]:
        return [series[i] / series[i - 1] - 1 for i in range(1, len(series)) if series[i - 1] > 0]

    combined_returns = _returns(combined_window)
    eq_returns = _returns(eq_window)
    c_returns = _returns(c_window)

    combined_metrics = _metrics_from_daily(dates[1:], combined_returns)
    equity_only_metrics = _metrics_from_daily(dates[1:], eq_returns)
    crypto_only_metrics = _metrics_from_daily(dates[1:], c_returns)

    # ---- sleeve window cost/turnover stats ----
    eq_sleeve_stats = _window_sleeve_stats(eq_result, window_start, window_end)
    c_sleeve_stats = _window_sleeve_stats(c_result, window_start, window_end)
    combined_avg_equity = statistics.fmean(combined_window)
    combined_traded_notional = (
        eq_sleeve_stats["turnover"] * eq_sleeve_stats["avg_equity_in_window"]
        + c_sleeve_stats["turnover"] * c_sleeve_stats["avg_equity_in_window"]
    )
    combined_sleeve_stats = {
        "fees_total": round(eq_sleeve_stats["fees_total"] + c_sleeve_stats["fees_total"], 2),
        "fees_pct_of_avg_equity": round(
            (eq_sleeve_stats["fees_total"] + c_sleeve_stats["fees_total"]) / combined_avg_equity,
            6,
        ),
        "turnover": round(combined_traded_notional / combined_avg_equity, 6),
        "rebalance_count": eq_sleeve_stats["rebalance_count"] + c_sleeve_stats["rebalance_count"],
        "trade_count": eq_sleeve_stats["trade_count"] + c_sleeve_stats["trade_count"],
        "avg_equity_in_window": round(combined_avg_equity, 2),
    }

    # ---- exposures (forward-filled target weights) ----
    eq_exposure = _daily_exposure(eq_result.weight_history, dates)
    c_exposure = _daily_exposure(c_result.weight_history, dates)
    total_exposure = [e + c for e, c in zip(eq_exposure, c_exposure)]
    avg_eq_exp = statistics.fmean(eq_exposure)
    avg_c_exp = statistics.fmean(c_exposure)
    avg_total_exp = statistics.fmean(total_exposure)

    # ---- risk-on fractions ----
    both_risk_on = sum(1 for e, c in zip(eq_exposure, c_exposure) if e > 1e-9 and c > 1e-9)
    one_risk_on = sum(1 for e, c in zip(eq_exposure, c_exposure) if (e > 1e-9) != (c > 1e-9))
    both_cash = sum(1 for e, c in zip(eq_exposure, c_exposure) if e <= 1e-9 and c <= 1e-9)
    n_days = len(dates)

    # ---- correlation of sleeve daily PnL ----
    correlation = _pearson(eq_returns, c_returns)

    # ---- benchmarks over the common window ----
    spy = _benchmark_bars(eq_bars, "SPY", Market.EQUITY)
    btc = _benchmark_bars(c_bars_1d, "BTC/USD", Market.CRYPTO)
    benchmarks = {
        "SPY": _benchmark_stats(spy, window_start),
        "BTC": _benchmark_stats(btc, window_start),
    }

    # ---- diversification verdict (evidence-based, no allocation changes) ----
    corr_text = (
        f"correlation {correlation:.2f} < {CORR_DIVERSIFIED}"
        if correlation is not None and correlation < CORR_DIVERSIFIED
        else (
            f"correlation {correlation:.2f} in [{CORR_DIVERSIFIED}, {CORR_LIMITED})"
            if correlation is not None and correlation < CORR_LIMITED
            else f"correlation {correlation:.2f} >= {CORR_LIMITED}"
        )
    )
    diversification = (
        "adds diversification"
        if correlation is not None and correlation < CORR_DIVERSIFIED
        else (
            "limited diversification"
            if correlation is not None and correlation < CORR_LIMITED
            else "no meaningful diversification"
        )
    )
    combined_cagr = combined_metrics.get("cagr")
    equity_cagr = equity_only_metrics.get("cagr")
    combined_mdd = combined_metrics.get("max_drawdown", 0.0)
    equity_mdd = equity_only_metrics.get("max_drawdown", 0.0)
    crypto_cagr = crypto_only_metrics.get("cagr")
    return_part = (
        "dilutes return"
        if (combined_cagr is not None and equity_cagr is not None and combined_cagr < equity_cagr)
        else "does not dilute return"
    )
    risk_part = "reduces max drawdown" if combined_mdd < equity_mdd else "does not reduce max drawdown"
    crypto_cagr_pct = f"{crypto_cagr * 100:.2f}%" if crypto_cagr is not None else "n/a"
    cost_drag = (
        "crypto sleeve is a cost drag relative to its (negative) contribution"
        if crypto_cagr is not None and crypto_cagr <= 0
        else "crypto sleeve costs are small relative to its contribution"
    )
    verdict = (
        f"crypto {diversification} for the combined account ({corr_text} between sleeve daily "
        f"PnLs), {return_part} (combined CAGR {combined_cagr * 100:.2f}% vs equity-only "
        f"{equity_cagr * 100:.2f}%), {risk_part} (combined maxDD {combined_mdd * 100:.2f}% vs "
        f"equity-only {equity_mdd * 100:.2f}%); the crypto sleeve itself returned "
        f"{crypto_cagr_pct} CAGR over the window and {cost_drag}. Evidence-based diagnostic "
        "only — the allocation ratio is NOT optimized and stays frozen."
    )

    report: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "product_combo": {
            "equity_strategy": config.strategy.equity.name,
            "equity_max_allocation": config.strategy.equity.maximum_allocation,
            "crypto_strategy": config.strategy.crypto.name,
            "crypto_max_allocation": config.strategy.crypto.maximum_allocation,
            "risk_limits": {
                "max_gross_exposure_pct": config.risk.max_gross_exposure_pct,
                "cash_buffer_pct": config.risk.cash_buffer_pct,
                "max_crypto_exposure_pct": config.risk.max_crypto_exposure_pct,
                "max_crypto_position_pct": config.risk.max_crypto_position_pct,
                "max_crypto_positions": config.risk.max_crypto_positions,
                "max_position_pct": config.risk.max_position_pct,
                "max_positions": config.risk.max_positions,
            },
            "note": (
                "Sleeves sized independently against the same account (production semantics); "
                "the shared risk layer enforces gross/cash/crypto caps. Allocation ratio "
                "NOT optimized."
            ),
        },
        "common_window": [common_dates[0].isoformat(), common_dates[-1].isoformat()],
        "combination_model": (
            "combined_value(t) = equity_sleeve(t) + crypto_sleeve(t) - initial_cash; both "
            "sleeves sized against the same account, one shared cash pool"
        ),
        "benchmarks": benchmarks,
        "equity_sleeve": {
            "metrics": equity_only_metrics,
            "costs_turnover": eq_sleeve_stats,
            "avg_exposure": round(avg_eq_exp, 4),
        },
        "crypto_sleeve": {
            "metrics": crypto_only_metrics,
            "costs_turnover": c_sleeve_stats,
            "avg_exposure": round(avg_c_exp, 4),
        },
        "combined": {
            "metrics": combined_metrics,
            "costs_turnover": combined_sleeve_stats,
            "avg_equity_exposure": round(avg_eq_exp, 4),
            "avg_crypto_exposure": round(avg_c_exp, 4),
            "avg_total_exposure": round(avg_total_exp, 4),
            "avg_cash_usage": round(1.0 - avg_total_exp, 4),
            "correlation_equity_crypto_daily_pnl": (round(correlation, 4) if correlation is not None else None),
            "risk_on_fractions": {
                "both_risk_on": round(both_risk_on / n_days, 4),
                "one_risk_on": round(one_risk_on / n_days, 4),
                "both_cash_heavy": round(both_cash / n_days, 4),
            },
        },
        "verdict": verdict,
        "verdict_components": {
            "diversification": diversification,
            "correlation_text": corr_text,
            "return": return_part,
            "risk": risk_part,
            "crypto_sleeve_cagr": crypto_cagr,
            "cost_drag": cost_drag,
        },
    }
    _write_json(OUTPUT_DIR / "combined.json", report)

    print("\n=== COMBINED RESULTS ===")
    print(
        f"  equity sleeve: CAGR {equity_only_metrics.get('cagr', 0) * 100:.2f}% "
        f"Sharpe {equity_only_metrics.get('sharpe', 0):.2f} "
        f"maxDD {equity_only_metrics.get('max_drawdown', 0) * 100:.2f}%"
    )
    print(
        f"  crypto sleeve: CAGR {crypto_only_metrics.get('cagr', 0) * 100:.2f}% "
        f"Sharpe {crypto_only_metrics.get('sharpe', 0):.2f} "
        f"maxDD {crypto_only_metrics.get('max_drawdown', 0) * 100:.2f}%"
    )
    print(
        f"  combined:      CAGR {combined_metrics.get('cagr', 0) * 100:.2f}% "
        f"Sharpe {combined_metrics.get('sharpe', 0):.2f} "
        f"maxDD {combined_metrics.get('max_drawdown', 0) * 100:.2f}% "
        f"Calmar {combined_metrics.get('calmar', 0):.2f}"
    )
    print(f"  sleeve correlation: {correlation:.4f}" if correlation is not None else "  sleeve correlation: n/a")
    print(
        f"  avg exposures: equity {avg_eq_exp:.2%} crypto {avg_c_exp:.2%} "
        f"total {avg_total_exp:.2%} cash {1 - avg_total_exp:.2%}"
    )
    print(
        f"  risk-on fractions: both {both_risk_on / n_days:.1%} "
        f"one {one_risk_on / n_days:.1%} cash-heavy {both_cash / n_days:.1%}"
    )
    print(f"  verdict: {verdict}")

    _append_summary_section(report)
    print("\nDone. reports/robustness/combined.json written; summary.md updated.")
    return 0


def _fmt_pct(value) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def _fmt_num(value, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _append_summary_section(report: dict) -> None:
    path = OUTPUT_DIR / "summary.md"
    lines = [
        "",
        "## Combined portfolio validation (equity 0.50 + crypto 0.10)",
        "",
        "Common window: "
        f"{report['common_window'][0]} .. {report['common_window'][1]} "
        "(equity trading days). Sleeves sized independently against one account; "
        "the shared risk layer enforces gross 0.60 / cash 0.40 / crypto caps.",
        "",
        "| metric | equity sleeve | crypto sleeve | combined |",
        "|---|---|---|---|",
    ]
    metrics = {
        "CAGR": ("cagr", "pct"),
        "Sharpe": ("sharpe", "num"),
        "Sortino": ("sortino", "num"),
        "maxDD": ("max_drawdown", "pct"),
        "Calmar": ("calmar", "num"),
        "total return": ("total_return", "pct"),
    }
    for label, (key, kind) in metrics.items():
        fmt = _fmt_pct if kind == "pct" else lambda v: _fmt_num(v, 2)
        lines.append(
            f"| {label} | {fmt(report['equity_sleeve']['metrics'].get(key))} | "
            f"{fmt(report['crypto_sleeve']['metrics'].get(key))} | "
            f"{fmt(report['combined']['metrics'].get(key))} |"
        )
    lines.append(
        f"| avg exposure | {_fmt_pct(report['equity_sleeve']['avg_exposure'])} | "
        f"{_fmt_pct(report['crypto_sleeve']['avg_exposure'])} | "
        f"{_fmt_pct(report['combined']['avg_total_exposure'])} |"
    )
    lines.append(
        f"| fees (% of avg equity) | "
        f"{_fmt_pct(report['equity_sleeve']['costs_turnover']['fees_pct_of_avg_equity'])} | "
        f"{_fmt_pct(report['crypto_sleeve']['costs_turnover']['fees_pct_of_avg_equity'])} | "
        f"{_fmt_pct(report['combined']['costs_turnover']['fees_pct_of_avg_equity'])} |"
    )
    lines.append(
        f"| turnover | {_fmt_num(report['equity_sleeve']['costs_turnover']['turnover'], 3)} | "
        f"{_fmt_num(report['crypto_sleeve']['costs_turnover']['turnover'], 3)} | "
        f"{_fmt_num(report['combined']['costs_turnover']['turnover'], 3)} |"
    )
    combined = report["combined"]
    lines.append("")
    lines.append(f"- Sleeve daily-PnL correlation: {_fmt_num(combined['correlation_equity_crypto_daily_pnl'], 4)}")
    lines.append(
        f"- Risk-on fractions: both sleeves risk-on "
        f"{_fmt_pct(combined['risk_on_fractions']['both_risk_on'])}, "
        f"one sleeve risk-on {_fmt_pct(combined['risk_on_fractions']['one_risk_on'])}, "
        f"both cash-heavy {_fmt_pct(combined['risk_on_fractions']['both_cash_heavy'])}"
    )
    lines.append(
        f"- Benchmarks over the common window: "
        f"SPY {_fmt_pct(report['benchmarks']['SPY']['return'])} "
        f"(Sharpe {_fmt_num(report['benchmarks']['SPY']['sharpe'])}), "
        f"BTC {_fmt_pct(report['benchmarks']['BTC']['return'])} "
        f"(Sharpe {_fmt_num(report['benchmarks']['BTC']['sharpe'])})"
    )
    lines.append("")
    lines.append(f"**Verdict:** {report['verdict']}")
    lines.append("")
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    # Idempotent: replace any previous combined section (the combined script
    # may be re-run independently of the robustness script).
    marker = "## Combined portfolio validation"
    if marker in existing:
        existing = existing.split(marker, 1)[0].rstrip("\n")
    path.write_text(existing.rstrip("\n") + "\n" + "\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
