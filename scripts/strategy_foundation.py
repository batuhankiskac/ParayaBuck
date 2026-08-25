#!/usr/bin/env python3
"""Strategy Foundation Test — does active trading add value over a static
diversified ETF portfolio?

Four systems, evaluated on identical data, cost model and execution rules:

    B0  diversified equal-weight ETF portfolio (static, no timing)
    B1  inverse-volatility weighted ETF portfolio (static, no timing)
    C1  B1 + 12-month own-price trend gate (binary per-asset filter)
    C2  B1 + 10-month SMA (200d) trend filter (binary per-asset filter)

Only C1/C2 are "active". B0/B1 are the control group; B1 is the primary
benchmark for the trend-increment question.

Data
----
Total-return daily bars (Alpaca ``adjustment=all``: splits AND dividends
applied to prices), hybrid feed: SIP 2016-01-04..2025-12-31 + IEX
2026-01-01.. (same broker, same adjustment policy; Dec-2025 overlap
reconciled). Dividends are inside the price series — no separate
corporate-action cash flows are added, so there is NO double counting.

Method
------
- Monthly rebalance at month-end close (decisions on completed bars only).
- B1/C1/C2 use trailing 252-trading-day realized vol / returns (2016 is the
  estimation warmup; evaluation starts 2017-01-03, no lookahead).
- C1 gate: 12m own-price total return > 0. C2 gate: close > 200d SMA.
  Filtered assets sit in cash (0% yield, conservative and identical across
  systems). No other indicators, no parameter fitting.
- Costs mirror config.backtest: adverse slippage + half spread per side,
  TAF/CAT/SEC regulatory fees (fixed 2026 rates), stress at 1x/2x/3x.
- Research policy: drawdown halts are SHADOW events (nothing is blocked),
  per the frozen production drawdown policy (commit f3c394d).

Research-only tooling: no runtime dependency, no orders, no PAPER/LIVE
writes. Policy version of every portfolio-return number is recorded.

Usage
-----
  python scripts/strategy_foundation.py download
  python scripts/strategy_foundation.py evaluate
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]
DATASET_ID = "equity_etf_foundation_1d_v1"
DATASET_DIR = REPO / "data" / "research" / DATASET_ID
OUTPUT_DIR = REPO / "data" / "research" / "strategy_foundation_v1"

# Economically distinct, liquid, Alpaca-covered from 2016-01-04 (SIP).
UNIVERSE = ["SPY", "IWM", "EFA", "EEM", "TLT", "LQD", "GLD", "XLRE", "DBC"]
ASSET_LABELS = {
    "SPY": "US large-cap equity",
    "IWM": "US small-cap equity",
    "EFA": "International developed",
    "EEM": "Emerging markets",
    "TLT": "US long Treasury",
    "LQD": "IG corporate credit",
    "GLD": "Gold",
    "XLRE": "US REITs",
    "DBC": "Commodities (broad)",
}
SYSTEMS = ("B0", "B1", "C1", "C2")
SIP_END = datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)
IEX_START = datetime(2025, 12, 1, tzinfo=UTC)  # overlap month for reconciliation
EVAL_START = datetime(2017, 1, 3, tzinfo=UTC)
VOL_LOOKBACK = 252  # trading days (~12 months)
SMA_LOOKBACK = 200  # trading days (10 months)
C1_RETURN_LOOKBACK = 252  # trading days (~12 months)


# --------------------------------------------------------------------------- #
# Download (hybrid SIP + IEX, adjustment=all)
# --------------------------------------------------------------------------- #


def _canonical_csv_rows(symbol: str, df: pd.DataFrame) -> list[str]:
    rows: list[str] = []
    for _, bar in df.iterrows():
        rows.append(
            f"{bar['timestamp'].isoformat()},{symbol},"
            f"{bar['open']:.8f},{bar['high']:.8f},{bar['low']:.8f},{bar['close']:.8f},"
            f"{bar['volume']:.1f},equity"
        )
    return rows


def cmd_download(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="strategy_foundation download")
    parser.add_argument("--start", default="2016-01-01", help="SIP window start")
    parser.add_argument("--end", default=None, help="IEX window end (default: now)")
    args = parser.parse_args(argv)

    load_dotenv(REPO / ".env")
    from parayabuck.data.alpaca import AlpacaHistoricalData

    def _make_client() -> AlpacaHistoricalData:
        return AlpacaHistoricalData(
            os.environ["ALPACA_API_KEY"],
            os.environ["ALPACA_SECRET_KEY"],
            stock_feed="sip",
        )

    end = datetime.now(UTC) if args.end is None else datetime.fromisoformat(args.end.replace("Z", "+00:00"))
    start = datetime.fromisoformat(args.start.replace("Z", "+00:00")).replace(tzinfo=UTC)

    # Phase 1: SIP for the pre-2026 window on a fresh client. The account has
    # no recent-SIP entitlement; a "recent" request degrades the client to
    # IEX for the WHOLE process, so all SIP fetches must complete before any
    # recent (IEX) request is made - otherwise later symbols silently lose
    # their 2016-2025 history.
    client = _make_client()
    sip_bars: dict[str, list] = {}
    for symbol in UNIVERSE:
        sip_bars[symbol] = client.get_bars(symbol, "1d", start, SIP_END, adjustment="all")
        print(f"SIP {symbol:5s} {len(sip_bars[symbol]):5d} bars")
    client.close()
    # Phase 2: fresh client for the recent window (degrades to IEX internally).
    client = _make_client()
    iex_bars: dict[str, list] = {}
    for symbol in UNIVERSE:
        iex_bars[symbol] = client.get_bars(symbol, "1d", IEX_START, end, adjustment="all")
        print(f"IEX {symbol:5s} {len(iex_bars[symbol]):5d} bars")
    source_feed = client.feed_status()["effective"]
    client.close()

    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    bars_per_symbol: dict[str, int] = {}
    duplicate_bars: dict[str, int] = {}
    conflicting_duplicate_bars: dict[str, int] = {}
    overlap_checks: dict[str, dict[str, object]] = {}
    feed_notes: list[str] = []
    digest = hashlib.sha256()
    merged_last: pd.DataFrame | None = None

    for symbol in UNIVERSE:
        sip_df = pd.DataFrame(
            [
                {
                    "timestamp": b.timestamp,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                }
                for b in sip_bars[symbol]
            ]
        )
        iex_df = pd.DataFrame(
            [
                {
                    "timestamp": b.timestamp,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                }
                for b in iex_bars[symbol]
            ]
        )
        if sip_df.empty and iex_df.empty:
            print(f"ERROR: no bars for {symbol}")
            return 1
        if sip_df.empty:
            feed_notes.append(f"{symbol}: no SIP segment; IEX only")
            merged = iex_df
        else:
            # SIP is authoritative on the overlap month; the two feeds' all-
            # adjusted series may drift by <2bps at the seam (same broker,
            # slightly different adjustment rounding) - record, do not fail.
            overlap = sip_df.merge(iex_df, on="timestamp", suffixes=("_sip", "_iex"))
            rel_delta = (overlap["close_sip"] - overlap["close_iex"]).abs() / overlap["close_sip"]
            bad = int(rel_delta.gt(5e-3).sum())
            conflicting_duplicate_bars[symbol] = bad
            overlap_checks[symbol] = {
                "overlap_dates": int(len(overlap)),
                "close_conflicts_gt_50bps": bad,
                "max_close_delta_bps": (float(rel_delta.max() * 1e4) if len(overlap) else None),
            }
            merged = (
                pd.concat([sip_df, iex_df[~iex_df["timestamp"].isin(sip_df["timestamp"])]])
                .sort_values("timestamp")
                .reset_index(drop=True)
            )
        if merged.empty:
            print(f"ERROR: empty merged bars for {symbol}")
            return 1
        dupes = int(merged["timestamp"].duplicated().sum())
        duplicate_bars[symbol] = dupes
        bars_per_symbol[symbol] = int(len(merged))
        rows = _canonical_csv_rows(symbol, merged)
        for row in rows:
            digest.update(row.encode("utf-8"))
            digest.update(b"\n")
        path = DATASET_DIR / f"{symbol}_1d.csv.gz"
        with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
            handle.write("timestamp,symbol,open,high,low,close,volume,market\n")
            handle.write("\n".join(rows))
            handle.write("\n")
        first = merged["timestamp"].min().date()
        last = merged["timestamp"].max().date()
        seam = overlap_checks[symbol]["max_close_delta_bps"]
        print(f"{symbol:5s} {len(merged):5d} bars {first} -> {last} seam_max_delta_bps={seam}")
        merged_last = merged

    assert merged_last is not None
    manifest = {
        "dataset_id": DATASET_ID,
        "format": "csv_gz",
        "timeframe": "1d",
        "adjustment": "all",
        "source_feed": "sip+iex_hybrid",
        "start": str(merged_last["timestamp"].min()),
        "end": str(merged_last["timestamp"].max()),
        "symbols": list(UNIVERSE),
        "bars_per_symbol": bars_per_symbol,
        "duplicate_bars": duplicate_bars,
        "conflicting_duplicate_bars": conflicting_duplicate_bars,
        "input_duplicate_bars": {},
        "overlap_reconciliation": overlap_checks,
        "data_hash": digest.hexdigest(),
        "universe_version": 1,
        "downloaded_at": datetime.now(UTC).isoformat(),
        "notes": (
            f"{DATASET_ID}: total-return (adjustment=all) hybrid SIP+IEX; "
            f"{len(UNIVERSE)}-asset permanent universe (US large/small, intl dev, "
            f"emerging, long Treasury, IG credit, gold, REIT, commodities); "
            "SIP through 2025-12-31, IEX 2026-01-01.. (same broker, same "
            "dividend+split adjustment policy); Dec-2025 seam reconciled with "
            "SIP authoritative (feed-side all-adjustment drift <2bps recorded "
            "in overlap_reconciliation). "
            "Dividends are inside the price series; do NOT add separate "
            "corporate-action cash flows to these bars (double counting)."
        ),
        "provider_metadata": {
            "provider_name": "alpaca",
            "adjustment_method": "all",
            "currency": "USD",
            "exchange_calendar": "NYSE",
            "downloaded_at": datetime.now(UTC).isoformat(),
            "source_feed": source_feed,
        },
    }
    (DATASET_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(f"manifest written: {DATASET_DIR / 'manifest.json'}")
    if feed_notes:
        print("feed notes:", "; ".join(feed_notes))
    return 0


def _load_panel() -> tuple[pd.DataFrame, dict]:
    manifest = json.loads((DATASET_DIR / "manifest.json").read_text(encoding="utf-8"))
    closes: dict[str, pd.Series] = {}
    for symbol in UNIVERSE:
        path = DATASET_DIR / f"{symbol}_1d.csv.gz"
        df = pd.read_csv(
            path,
            parse_dates=["timestamp"],
            index_col="timestamp",
        )
        closes[symbol] = df["close"].astype(float)
    panel = pd.DataFrame(closes).sort_index()
    panel = panel.ffill()  # carry last close across halts; 9 liquid ETFs, rare
    return panel, manifest


def _month_end_indices(dates: pd.DatetimeIndex) -> pd.Index:
    years = pd.Series(dates).dt.year
    months = pd.Series(dates).dt.month
    is_last = (years != years.shift(-1)) | (months != months.shift(-1))
    return pd.Index(dates[is_last.to_numpy()])  # last trading day per month


def _trailing_vol(closes: pd.DataFrame, idx: int, lookback: int) -> pd.Series:
    window = closes.iloc[max(0, idx - lookback) : idx + 1]
    rets = window.pct_change().dropna()
    if len(rets) < 30:
        return pd.Series(np.nan, index=closes.columns)
    return rets.std(ddof=1)


def _inverse_vol_weights(vol: pd.Series) -> pd.Series:
    inv = 1.0 / vol.replace(0.0, np.nan)
    return inv / inv.sum()


def _trend_gate_c1(closes: pd.DataFrame, idx: int, lookback: int) -> pd.Series:
    past = closes.iloc[max(0, idx - lookback)]
    return (closes.iloc[idx] / past - 1.0) > 0.0


def _trend_gate_c2(closes: pd.DataFrame, idx: int, lookback: int) -> pd.Series:
    sma = closes.iloc[max(0, idx - lookback + 1) : idx + 1].mean()
    return closes.iloc[idx] > sma


def _system_weights(system: str, closes: pd.DataFrame, idx: int) -> tuple[pd.Series, float]:
    """Return target weights (cash fraction = 1 - weight sum) at row idx."""
    n = len(closes.columns)
    if system == "B0":
        w = pd.Series(1.0 / n, index=closes.columns)
    elif system in ("B1", "C1", "C2"):
        vol = _trailing_vol(closes, idx, VOL_LOOKBACK)
        if vol.isna().any():
            w = pd.Series(1.0 / n, index=closes.columns)  # warmup fallback
        else:
            w = _inverse_vol_weights(vol)
        if system == "C1":
            gate = _trend_gate_c1(closes, idx, C1_RETURN_LOOKBACK)
            w = w.where(gate, 0.0)
        elif system == "C2":
            gate = _trend_gate_c2(closes, idx, SMA_LOOKBACK)
            w = w.where(gate, 0.0)
        total = w.sum()
        if total > 0:
            w = w / total  # re-normalize the surviving set
    else:
        raise ValueError(system)
    cash_frac = float(1.0 - w.sum())
    return w, cash_frac


def _run_system(
    system: str,
    panel: pd.DataFrame,
    eval_mask: pd.Series,
    rebalance_dates: list,
    cost_multiplier: float,
    cfg,
    cash_yield: float = 0.0,
) -> dict:
    closes = panel
    idx_pos = {ts: i for i, ts in enumerate(closes.index)}
    nav = 1.0
    nav_series: list[tuple] = []
    weights_history: list[dict] = []
    turnover_total = 0.0
    fee_total = 0.0
    current_w = pd.Series(0.0, index=closes.columns)
    cash_frac = 1.0
    daily_cash = cash_yield / 252.0

    adverse_bps = (cfg.backtest.slippage_bps + cfg.backtest.spread_bps / 2.0) * cost_multiplier
    exposure_sum = 0.0
    eval_days = 0

    for ts in closes.index:
        i = idx_pos[ts]
        # 1) Day return with the weights held at the START of the session
        #    (rebalance decisions at close become effective the next session).
        if i > 0:
            prev = closes.iloc[i - 1]
            curr = closes.iloc[i]
            price_ret = curr / prev - 1.0
            cash_gain = cash_frac * daily_cash
            port_ret = float((current_w * price_ret).sum()) + cash_gain
            nav *= 1.0 + port_ret
            factor = 1.0 + port_ret
            current_w = (current_w * (1.0 + price_ret)) / factor
            cash_frac = cash_frac * (1.0 + daily_cash) / factor
        # 2) Rebalance at the close: target weights are effective next session.
        if ts in rebalance_dates and eval_mask.loc[ts]:
            target, _cash_frac = _system_weights(system, closes, i)
            cash_frac = 1.0 - float(target.sum())
            delta = target - current_w
            traded = delta.abs().sum()
            turnover_total += traded / 2.0
            if traded > 0:
                # Per-share regulatory fees on the traded notional: buys pay
                # CAT only; sells pay CAT + TAF (capped) + SEC Section 31.
                # Fixed rates (config.backtest) — NOT scaled by the cost
                # multiplier, mirroring the product backtest engine.
                reg_fee = 0.0
                for sym, d in delta.items():
                    if abs(d) < 1e-12:
                        continue
                    qty = abs(d) * nav / closes.loc[ts, sym]
                    price = closes.loc[ts, sym]
                    if d > 0:  # buy
                        reg_fee += qty * cfg.backtest.equity_cat_per_share
                    else:  # sell
                        taf = min(
                            qty * cfg.backtest.equity_taf_per_share,
                            cfg.backtest.equity_taf_cap,
                        )
                        reg_fee += (
                            qty * cfg.backtest.equity_cat_per_share
                            + taf
                            + qty * price * cfg.backtest.equity_sec_fee_rate
                        )
                fee_total += reg_fee
                nav -= reg_fee
                nav -= traded * adverse_bps / 1e4 * nav
            current_w = target
            weights_history.append(
                {
                    "date": ts.isoformat(),
                    **{s: round(float(current_w.get(s, 0.0)), 6) for s in closes.columns},
                    "cash": round(float(1.0 - current_w.sum()), 6),
                }
            )
        if eval_mask.loc[ts]:
            exposure_sum += float(current_w.sum())
            eval_days += 1
        nav_series.append((ts, nav))

    nav_df = pd.DataFrame(nav_series, columns=["timestamp", "nav"]).set_index("timestamp")
    return {
        "nav": nav_df,
        "weights": weights_history,
        "turnover": turnover_total,
        "fees": fee_total,
        "avg_exposure": exposure_sum / eval_days if eval_days else 0.0,
    }


def _metrics(nav: pd.Series) -> dict:
    rets = nav.pct_change().dropna()
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    total_return = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    cagr = float((nav.iloc[-1] / nav.iloc[0]) ** (1.0 / years) - 1.0) if years > 0 else math.nan
    vol = float(rets.std(ddof=1) * math.sqrt(252)) if len(rets) > 1 else math.nan
    sharpe = float(rets.mean() / rets.std(ddof=1) * math.sqrt(252)) if rets.std(ddof=1) > 0 else math.nan
    downside = rets[rets < 0].std(ddof=1)
    sortino = float(rets.mean() / downside * math.sqrt(252)) if downside > 0 else math.nan
    peak = nav.cummax()
    dd = nav / peak - 1.0
    max_dd = float(dd.min())
    calmar = float(cagr / abs(max_dd)) if max_dd < 0 else math.nan
    monthly = nav.resample("ME").last().pct_change().dropna()
    months_positive = float((monthly > 0).mean()) if len(monthly) else math.nan
    return {
        "total_return": total_return,
        "cagr": cagr,
        "ann_vol": vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "avg_monthly_return": float(monthly.mean()) if len(monthly) else math.nan,
        "monthly_win_rate": months_positive,
        "n_months": int(len(monthly)),
    }


def _evaluate(cfg, cost_multiplier: float, panel: pd.DataFrame, cash_yield: float = 0.0) -> dict:
    eval_mask = pd.Series(panel.index >= EVAL_START, index=panel.index)
    rebalance_dates = set(_month_end_indices(panel.index))
    results: dict[str, dict] = {}
    for system in SYSTEMS:
        run = _run_system(
            system,
            panel,
            eval_mask,
            sorted(rebalance_dates),
            cost_multiplier,
            cfg,
            cash_yield=cash_yield,
        )
        nav = run["nav"]["nav"]
        m = _metrics(nav[nav.index >= EVAL_START])
        m.update(
            {
                "turnover": run["turnover"] / ((nav.index[-1] - nav.index[0]).days / 365.25),
                "total_fees": run["fees"],
                "n_rebalances": len(run["weights"]),
                "avg_exposure": run["avg_exposure"],
            }
        )
        results[system] = {"metrics": m, "nav": nav, "run": run}
    benchmark = panel["SPY"] / panel["SPY"].iloc[0]
    benchmark_metrics = _metrics(benchmark[benchmark.index >= EVAL_START])
    benchmark_metrics.update(
        {
            "turnover": 0.0,
            "total_fees": 0.0,
            "n_rebalances": 0,
            "avg_exposure": 1.0,
        }
    )
    results["SPY"] = {"metrics": benchmark_metrics, "nav": benchmark, "run": None}
    return results


def _fold_returns(nav: pd.Series) -> pd.Series:
    yearly = nav.resample("YE").last()
    return yearly.pct_change().dropna()


def cmd_evaluate(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="strategy_foundation evaluate")
    parser.add_argument("--cost-multipliers", default="1,2,3")
    args = parser.parse_args(argv)

    from parayabuck.config import load_config

    cfg = load_config()
    panel, manifest = _load_panel()
    eval_mask = panel.index >= EVAL_START
    first = panel.index[0].date()
    last = panel.index[-1].date()
    print(f"panel: {len(panel)} days {first} -> {last}; assets: {list(panel.columns)}")
    print(f"evaluation window: {EVAL_START.date()} -> {last} ({int(eval_mask.sum())} days)")
    print(f"data_hash: {manifest['data_hash'][:16]}…")

    multipliers = [float(x) for x in args.cost_multipliers.split(",")]
    stress: dict[str, dict[str, dict]] = {}
    for mult in multipliers:
        stress[str(mult)] = _evaluate(cfg, mult, panel)

    # Cash-yield sensitivity for the trend systems' idle cash (base case 0%).
    cash_sensitivity: dict[str, dict[str, dict]] = {}
    for yield_pct in (0.0, 2.0, 4.0):
        cash_sensitivity[f"{yield_pct:g}%"] = {
            name: {k: data["metrics"][k] for k in ("cagr", "sharpe", "sortino", "max_drawdown", "avg_exposure")}
            for name, data in _evaluate(cfg, 1.0, panel, cash_yield=yield_pct / 100.0).items()
        }

    # Build report
    report: dict[str, object] = {
        "title": "Strategy Foundation Test — B0/B1/C1/C2",
        "generated_at": datetime.now(UTC).isoformat(),
        "policy_version": {
            "research": "drawdown halt = SHADOW event; entries never blocked",
            "production_commit": "f3c394d (2026-08-12): drawdown operational incident + verified risk-epoch reset",
            "note": "portfolio-return comparisons are valid only within this policy "
            "version; V1/V2 ETF runs (pre-f3c394d) enforced the drawdown latch on "
            "the return path and are NOT directly comparable.",
        },
        "universe": {s: ASSET_LABELS[s] for s in UNIVERSE},
        "data": {
            "dataset_id": manifest["dataset_id"],
            "adjustment": manifest["adjustment"],
            "source_feed": manifest["source_feed"],
            "data_hash": manifest["data_hash"],
            "start": first.isoformat(),
            "end": last.isoformat(),
            "dividend_model": "total-return adjusted prices (splits+dividends in "
            "series); no separate cash flows; no double counting",
        },
        "method": {
            "rebalance": "monthly, month-end close",
            "warmup": "2016 (252d trailing vol / 12m returns / 200d SMA); evaluation from 2017-01-03",
            "cost_model": {
                "adverse_bps_per_side": "slippage + half spread (config.backtest)",
                "regulatory_fees": "TAF/CAT/SEC fixed 2026 rates (config.backtest)",
                "cash_yield": "0% base case; 2%/4% sensitivity reported",
            },
            "systems": {
                "B0": "static equal weight",
                "B1": "static inverse-vol (252d trailing realized vol)",
                "C1": "B1 weights gated by 12m own-price total return > 0",
                "C2": "B1 weights gated by close > 200d SMA (10-month)",
            },
        },
    }

    # Full-period comparison at 1x costs
    full: dict[str, dict] = {}
    for name, data in stress["1.0"].items():
        m = dict(data["metrics"])
        nav = data["nav"]
        m["end_nav"] = float(nav.iloc[-1])
        m["yearly_returns"] = {str(ts.year): float(v) for ts, v in _fold_returns(nav).items()}
        full[name] = m
    report["full_period_1x"] = full

    # Per-year folds at 1x (calendar-year returns and max DD); 2016 is the
    # estimation warmup (B1/C1/C2 run on equal-weight fallback there), so the
    # folds start at the evaluation window.
    folds: dict[str, dict] = {}
    for name, data in stress["1.0"].items():
        nav = data["nav"]
        yearly: dict[str, dict] = {}
        for year, group in nav.groupby(nav.index.year):
            if year < EVAL_START.year:
                continue
            yearly[str(year)] = _metrics(group)
        folds[name] = yearly
    report["annual_folds_1x"] = folds

    # Rolling folds (3y and 5y) at 1x: Sharpe and maxDD per window, plus the
    # share of windows where each active system beats B1's Sharpe.
    rolling: dict[str, dict] = {}
    ref_nav = stress["1.0"]["B0"]["nav"]
    for window in (3, 5):
        n_days = window * 252
        per_window: dict[int, dict[str, dict]] = {}
        start_i = 0
        while start_i + n_days <= len(ref_nav):
            per_window[start_i] = {
                name: _metrics(data["nav"].iloc[start_i : start_i + n_days]) for name, data in stress["1.0"].items()
            }
            start_i += 252
        windows: dict[str, dict] = {}
        for name in [*SYSTEMS, "SPY"]:
            sharpe_series = [m[name]["sharpe"] for m in per_window.values() if not math.isnan(m[name]["sharpe"])]
            dd_series = [
                m[name]["max_drawdown"] for m in per_window.values() if not math.isnan(m[name]["max_drawdown"])
            ]
            b1_win_share = (
                sum(
                    1
                    for m in per_window.values()
                    if not math.isnan(m[name]["sharpe"])
                    and not math.isnan(m["B1"]["sharpe"])
                    and m[name]["sharpe"] > m["B1"]["sharpe"]
                )
                / len(per_window)
                if per_window
                else None
            )
            windows[name] = {
                "n_windows": len(sharpe_series),
                "sharpe_mean": float(np.mean(sharpe_series)) if sharpe_series else None,
                "sharpe_median": float(np.median(sharpe_series)) if sharpe_series else None,
                "sharpe_min": float(np.min(sharpe_series)) if sharpe_series else None,
                "sharpe_win_vs_b1": b1_win_share,
                "maxdd_mean": float(np.mean(dd_series)) if dd_series else None,
                "maxdd_min": float(np.min(dd_series)) if dd_series else None,
            }
        rolling[f"{window}y"] = windows
    report["rolling_folds_1x"] = rolling

    # Cost stress: full-period metrics per multiplier
    stress_report: dict[str, dict[str, dict]] = {}
    for mult in multipliers:
        key = f"{mult:g}x"
        stress_report[key] = {
            name: {
                k: v
                for k, v in stress[f"{mult}"][name]["metrics"].items()
                if k
                in (
                    "total_return",
                    "cagr",
                    "ann_vol",
                    "sharpe",
                    "sortino",
                    "max_drawdown",
                    "calmar",
                    "turnover",
                )
            }
            for name in SYSTEMS
        }
    report["cost_stress"] = stress_report
    report["cash_yield_sensitivity"] = cash_sensitivity

    # Trend-increment summary: C1/C2 vs the B1 control (1x costs), the
    # decision-relevant table per the mission brief.
    trend_increment: dict[str, dict] = {}
    b1 = full["B1"]
    for name in ("C1", "C2"):
        m = full[name]
        trend_increment[name] = {
            "delta_cagr_pp": (m["cagr"] - b1["cagr"]) * 100.0,
            "delta_sharpe": m["sharpe"] - b1["sharpe"],
            "delta_sortino": m["sortino"] - b1["sortino"],
            "delta_maxdd_pp": (m["max_drawdown"] - b1["max_drawdown"]) * 100.0,
            "delta_calmar": m["calmar"] - b1["calmar"],
            "sharpe_win_rate_3y": rolling["3y"][name]["sharpe_win_vs_b1"],
            "sharpe_win_rate_5y": rolling["5y"][name]["sharpe_win_vs_b1"],
            "sharpe_at_3x_costs": stress_report["3x"][name]["sharpe"],
        }
    report["trend_increment_vs_b1_1x"] = trend_increment

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "report.json").write_text(
        json.dumps(_sanitize(report), indent=2, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    # Monthly returns CSVs
    for name, data in stress["1.0"].items():
        nav = data["nav"]
        monthly = nav.resample("ME").last().pct_change()
        monthly.to_csv(OUTPUT_DIR / f"monthly_returns_{name}.csv", header=["return"])
    _write_markdown(report, OUTPUT_DIR / "report.md")
    _print_console(report)
    return 0


def _sanitize(obj):
    """Recursively replace NaN floats with None for JSON serialization."""
    if isinstance(obj, float) and math.isnan(obj):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


def _fmt_pct(v) -> str:
    return "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v * 100:.2f}%"


def _fmt_num(v, digits: int = 2) -> str:
    return "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.{digits}f}"


def _print_console(report: dict) -> None:
    print("\n=== STRATEGY FOUNDATION TEST — FULL PERIOD (1x costs) ===")
    header = (
        f"{'system':6s} {'CAGR':>8s} {'vol':>7s} {'Sharpe':>7s} {'Sortino':>8s} "
        f"{'maxDD':>7s} {'Calmar':>7s} {'turn':>6s} {'exp':>6s}"
    )
    print(header)
    for name in [*SYSTEMS, "SPY"]:
        m = report["full_period_1x"][name]
        print(
            f"{name:6s} {_fmt_pct(m['cagr']):>8s} {_fmt_pct(m['ann_vol']):>7s} "
            f"{_fmt_num(m['sharpe']):>7s} {_fmt_num(m['sortino']):>8s} "
            f"{_fmt_pct(m['max_drawdown']):>7s} {_fmt_num(m['calmar']):>7s} "
            f"{_fmt_num(m['turnover'], 1):>6s} {_fmt_pct(m['avg_exposure']):>6s}"
        )
    print("\n=== YEARLY RETURNS (1x costs) ===")
    years = sorted({y for name in [*SYSTEMS, "SPY"] for y in report["full_period_1x"][name]["yearly_returns"]})
    print(f"{'year':6s} " + " ".join(f"{name:>8s}" for name in [*SYSTEMS, "SPY"]))
    for y in years:
        row = []
        for name in [*SYSTEMS, "SPY"]:
            v = report["full_period_1x"][name]["yearly_returns"].get(y)
            row.append(_fmt_pct(v))
        print(f"{y:6s} " + " ".join(f"{v:>8s}" for v in row))
    print("\n=== COST STRESS (full period, Sharpe / maxDD) ===")
    for mult in report["cost_stress"]:
        row = []
        for name in SYSTEMS:
            m = report["cost_stress"][mult][name]
            row.append(f"{name}:S={_fmt_num(m['sharpe'])}/DD={_fmt_pct(m['max_drawdown'])}")
        print(f"{mult:4s} " + "  ".join(row))
    print(f"\nreport: {OUTPUT_DIR / 'report.json'}")


def _write_markdown(report: dict, path: Path) -> None:
    lines = [
        f"# {report['title']}",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Policy version",
        "",
        f"- Research: {report['policy_version']['research']}",
        f"- Production: {report['policy_version']['production_commit']}",
        f"- {report['policy_version']['note']}",
        "",
        "## Universe (9 economically distinct assets)",
        "",
    ]
    for sym, label in report["universe"].items():
        lines.append(f"- {sym} — {label}")
    lines += [
        "",
        "## Data",
        "",
        f"- dataset: `{report['data']['dataset_id']}` (hash `{report['data']['data_hash'][:16]}…`)",
        f"- adjustment: {report['data']['adjustment']} — dividends inside the price series, "
        "no separate cash flows, no double counting",
        f"- span: {report['data']['start']} → {report['data']['end']}, feed {report['data']['source_feed']}",
        "",
        "## Method",
        "",
        "- Monthly rebalance at month-end close; decisions on completed bars only.",
        "- 2016 = estimation warmup (252d vol / 12m returns / 200d SMA); evaluation 2017-01-03 → end.",
        "- C1 gate: 12m own-price total return > 0. C2 gate: close > 200d SMA. Filtered assets → cash (0%).",
        "- Costs mirror `config.backtest` (slippage + half spread per side, TAF/CAT/SEC fixed rates), ×1/×2/×3.",
        "- Drawdown halt is a shadow event (research policy); nothing is blocked.",
        "",
        "## Full period (2017-01-03 → end, 1x costs)",
        "",
        "| system | CAGR | vol | Sharpe | Sortino | maxDD | Calmar | turnover/yr | in-market |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name in [*SYSTEMS, "SPY"]:
        m = report["full_period_1x"][name]
        lines.append(
            f"| {name} | {_fmt_pct(m['cagr'])} | {_fmt_pct(m['ann_vol'])} | "
            f"{_fmt_num(m['sharpe'])} | {_fmt_num(m['sortino'])} | {_fmt_pct(m['max_drawdown'])} | "
            f"{_fmt_num(m['calmar'])} | {_fmt_num(m['turnover'], 1)} | "
            f"{_fmt_pct(m['avg_exposure'])} |"
        )
    lines += [
        "",
        "## Yearly returns (1x costs)",
        "",
        "| year | " + " | ".join([*SYSTEMS, "SPY"]) + " |",
        "|---|---" + "---|" * (len(SYSTEMS) + 1),
    ]
    years = sorted({y for name in [*SYSTEMS, "SPY"] for y in report["full_period_1x"][name]["yearly_returns"]})
    for y in years:
        cells = []
        for name in [*SYSTEMS, "SPY"]:
            v = report["full_period_1x"][name]["yearly_returns"].get(y)
            cells.append(_fmt_pct(v))
        lines.append(f"| {y} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Cost stress (full period)",
        "",
        "| costs | " + " | ".join(SYSTEMS) + " |",
        "|---|---" + "---|" * len(SYSTEMS),
    ]
    for mult, table in report["cost_stress"].items():
        cells = []
        for name in SYSTEMS:
            m = table[name]
            cells.append(f"S {_fmt_num(m['sharpe'])} / DD {_fmt_pct(m['max_drawdown'])}")
        lines.append(f"| {mult} | " + " | ".join(cells) + " |")
    lines += ["", "## Trend increment vs B1 (decision table, 1x costs)", ""]
    lines += [
        "| system | dCAGR pp | dSharpe | dSortino | dmaxDD pp | dCalmar | Sharpe win 3y | Sharpe win 5y |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, ti in report["trend_increment_vs_b1_1x"].items():
        lines.append(
            f"| {name} | {ti['delta_cagr_pp']:+.2f} | {ti['delta_sharpe']:+.2f} | "
            f"{ti['delta_sortino']:+.2f} | {ti['delta_maxdd_pp']:+.2f} | "
            f"{ti['delta_calmar']:+.2f} | {ti['sharpe_win_rate_3y']:.0%} | "
            f"{ti['sharpe_win_rate_5y']:.0%} |"
        )
    lines += ["", "## Rolling folds (1x costs)", ""]
    for window, table in report["rolling_folds_1x"].items():
        lines.append(f"### {window} rolling windows")
        lines.append("")
        lines.append("| system | n | Sharpe mean | Sharpe med | Sharpe min | maxDD mean | maxDD min |")
        lines.append("|---|---|---|---|---|---|---|")
        for name, m in table.items():
            lines.append(
                f"| {name} | {m['n_windows']} | {_fmt_num(m['sharpe_mean'])} | "
                f"{_fmt_num(m['sharpe_median'])} | {_fmt_num(m['sharpe_min'])} | "
                f"{_fmt_pct(m['maxdd_mean'])} | {_fmt_pct(m['maxdd_min'])} |"
            )
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="strategy_foundation")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("download")
    sub.add_parser("evaluate")
    args = parser.parse_args(argv)
    if args.command == "download":
        return cmd_download(argv[1:])
    if args.command == "evaluate":
        return cmd_evaluate(argv[1:])
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
