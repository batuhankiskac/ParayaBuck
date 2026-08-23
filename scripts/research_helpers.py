#!/usr/bin/env python3
"""Shared validated-research helpers (walk-forward windows, fold stats, cost
stress, profit factor). Extracted from the retired bake-off runner
(scripts/validate_strategy_candidates.py, removed 2026-08-18) because the
cadence-mission research scripts reuse them; the rejected-candidate halves of
that runner are archived in docs/rejected_strategies.md.

Research-only utilities: never imported by src/ or tests/.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import platform
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from parayabuck.backtest.product import run_product_backtest
from parayabuck.core.models import Market

REPO_ROOT = Path(__file__).resolve().parents[1]


def _sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO_ROOT
    ).stdout.strip()


def dataset_identity() -> dict[str, object]:
    """Hashes of the historical datasets every equity research run consumes."""
    bars = REPO_ROOT / "data/historical/equity_1200_2016_2026.csv"
    dividends = REPO_ROOT / "data/historical/equity_1200_dividends.csv"
    return {
        "bars_csv": str(bars.relative_to(REPO_ROOT)),
        "bars_sha256": _sha256_file(bars),
        "dividends_csv": str(dividends.relative_to(REPO_ROOT)),
        "dividends_sha256": _sha256_file(dividends),
    }


def config_digest(config) -> str:  # noqa: ANN001
    """Stable digest of everything economic in the loaded configuration."""
    payload = {
        "equity_strategy": {
            "name": config.strategy.equity.name,
            "parameters": dict(config.strategy.equity.parameters),
            "maximum_allocation": config.strategy.equity.maximum_allocation,
        },
        "risk": {
            key: getattr(config.risk, key)
            for key in (
                "max_position_pct",
                "max_positions",
                "max_gross_exposure_pct",
                "cash_buffer_pct",
                "daily_loss_limit_pct",
                "max_drawdown_pct",
                "max_crypto_exposure_pct",
                "max_crypto_position_pct",
                "max_crypto_positions",
            )
        },
        "backtest": {
            key: getattr(config.backtest, key)
            for key in (
                "initial_cash",
                "slippage_bps",
                "spread_bps",
                "commission",
                "crypto_taker_fee_bps",
                "equity_taf_per_share",
                "equity_taf_cap",
                "equity_cat_per_share",
                "equity_sec_fee_rate",
                "equity_use_effective_dated_fees",
                "scanner_rebalance",
            )
        },
        "execution": {
            "rebalance_band_pct": config.execution.rebalance_band_pct,
            "minimum_order_notional": config.execution.minimum_order_notional,
        },
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def run_identity(config) -> dict[str, object]:  # noqa: ANN001
    return {
        "head_commit": git_head(),
        "python_version": platform.python_version(),
        "dataset": dataset_identity(),
        "config_digest_sha256": config_digest(config),
        "generated_at": datetime.now(UTC).isoformat(),
    }


def sha256_of_file(path: str | Path) -> str:
    return _sha256_file(path)


def main() -> None:
    raise SystemExit("library only")


if __name__ == "__main__":
    main()


def _fold_window(
    start: datetime, end: datetime, step_months: int, warmup_months: int
) -> list[tuple[datetime, datetime]]:
    """Anchored walk-forward test windows (train = everything before fold start)."""
    folds: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        fold_end = min(cursor + timedelta(days=30 * step_months), end)
        if fold_end - cursor >= timedelta(days=20):
            folds.append((cursor, fold_end))
        cursor += timedelta(days=30 * step_months)
    return folds


def _calendar_fold_window(
    start: datetime, end: datetime, step_months: int
) -> list[tuple[datetime, datetime]]:
    """Anchored walk-forward windows aligned to whole calendar months.

    ``_fold_window`` steps by 30-day blocks; the frozen reversal walk-forward
    is specified as 6-month folds from 2022-01-01 to CRYPTO_END (10 folds,
    Jan/Jul starts), which requires calendar-month stepping: 2022-01-01,
    2022-07-01, ..., 2026-07-01. Folds tile the window end-to-end.
    """
    folds: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        month_index = cursor.year * 12 + (cursor.month - 1) + step_months
        year, month0 = divmod(month_index, 12)
        month = month0 + 1
        day = min(cursor.day, calendar.monthrange(year, month)[1])
        fold_end = min(datetime(year, month, day, tzinfo=cursor.tzinfo), end)
        if fold_end - cursor >= timedelta(days=20):
            folds.append((cursor, fold_end))
        cursor = fold_end
    return folds


def _fold_stats(result) -> dict[str, object]:
    return {
        "start": result.start_date,
        "end": result.end_date,
        "return": round(result.total_return, 6),
        "cagr": round(result.cagr, 6),
        "sharpe": round(result.sharpe, 6),
        "max_drawdown": round(result.max_drawdown, 6),
        "turnover": round(result.turnover, 6),
        "trade_count": len(result.closed_position_events),
        "avg_holdings": round(result.average_holdings_count, 3),
        "avg_exposure": round(1.0 - result.average_cash_ratio, 4),
    }


def _concentration(result) -> dict[str, object]:
    """PnL concentration: largest single-symbol realized contribution shares."""
    contributions = sorted(
        (
            (symbol, pnl)
            for symbol, pnl in ((item.symbol, item.realized_pnl) for item in result.per_symbol)
        ),
        key=lambda item: (-item[1], item[0]),
    )
    positive_total = sum(pnl for _symbol, pnl in contributions if pnl > 0)
    top = contributions[:3]
    return {
        "top_1": {"symbol": top[0][0], "realized_pnl": round(top[0][1], 2)} if top else None,
        "top_2": (
            {"symbol": top[1][0], "realized_pnl": round(top[1][1], 2)} if len(top) > 1 else None
        ),
        "top_3": (
            {"symbol": top[2][0], "realized_pnl": round(top[2][1], 2)} if len(top) > 2 else None
        ),
        "top1_share_of_positive_pnl": (
            round(top[0][1] / positive_total, 4) if positive_total > 0 else 0.0
        ),
        "top2_share_of_positive_pnl": (
            round(sum(item[1] for item in top[:2]) / positive_total, 4)
            if positive_total > 0
            else 0.0
        ),
        "top3_share_of_positive_pnl": (
            round(sum(item[1] for item in top[:3]) / positive_total, 4)
            if positive_total > 0
            else 0.0
        ),
        "max_single_weight": round(result.maximum_concentration, 4),
        "symbol_count": len(contributions),
    }


def _avg_equity(result) -> float:
    curve = result.equity_curve
    return sum(curve) / len(curve) if curve else result.starting_equity


def _profit_factor(result) -> float:
    gross_profit = sum(pnl for pnl in result.realized_pnls if pnl > 0)
    gross_loss = abs(sum(pnl for pnl in result.realized_pnls if pnl < 0))
    if gross_loss > 0:
        return gross_profit / gross_loss
    return float("inf") if gross_profit > 0 else 0.0


def _sector_coverage(result) -> dict[str, object]:
    """Best-effort sector mix of traded symbols using the static sector map."""
    from parayabuck.markets.sectors import sector_of

    traded = {item.symbol for item in result.per_symbol}
    counts: dict[str, int] = {}
    known = 0
    for symbol in sorted(traded):
        sector = sector_of(symbol)
        if sector != "other":
            known += 1
        counts[sector] = counts.get(sector, 0) + 1
    return {
        "traded_symbols": len(traded),
        "sector_known": known,
        "sector_distribution": dict(sorted(counts.items(), key=lambda item: -item[1])),
    }


def _cost_stress(
    config,
    bars,
    *,
    market: Market,
    strategy_name: str,
    parameters: dict[str, object],
    strategy_timeframe: str | None,
    start: datetime,
    end: datetime,
    universe=None,
) -> dict[str, object]:
    runs = {}
    for multiplier in (1.0, 2.0, 3.0):
        run = run_product_backtest(
            config,
            bars,
            market=market,
            strategy_name=strategy_name,
            parameters=parameters,
            strategy_timeframe=strategy_timeframe,
            start=start,
            end=end,
            cost_multiplier=multiplier,
            persist=False,
            persist_artifacts=False,
            universe=universe,
        )
        result = run.result
        gross = result.gross_return
        total_cost = result.slippage_cost_total + result.spread_cost_total + result.fee_total
        avg_equity = _avg_equity(result)
        gross_pnl = result.starting_equity * gross
        runs[f"{multiplier:g}x"] = {
            "return": round(result.total_return, 6),
            "gross_return": round(gross, 6),
            "cagr": round(result.cagr, 6),
            "sharpe": round(result.sharpe, 6),
            "sortino": round(result.sortino, 6),
            "calmar": round(result.calmar, 6),
            "max_drawdown": round(result.max_drawdown, 6),
            "profit_factor": round(_profit_factor(result), 4),
            "turnover": round(result.turnover, 6),
            "fees_total": round(total_cost, 2),
            "fees_pct_of_avg_equity": round(total_cost / avg_equity, 6) if avg_equity > 0 else 0.0,
            "fees_pct_of_gross_pnl": (
                round(total_cost / abs(gross_pnl), 6) if abs(gross_pnl) > 1e-9 else None
            ),
            "avg_holdings": round(result.average_holdings_count, 3),
            "avg_cash_ratio": round(result.average_cash_ratio, 4),
            "rebalance_count": result.rebalance_count,
            "trade_count": len(result.closed_position_events),
            "concentration": _concentration(result),
            "sector_coverage": _sector_coverage(result),
        }
    return runs
