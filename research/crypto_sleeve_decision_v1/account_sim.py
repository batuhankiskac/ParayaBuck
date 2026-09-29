"""Close-to-close account-level test of the 0.05 crypto sleeve (offline, research only).

Inputs are read directly from immutable git tags (no download, no network):

* equity bars / dividends: tag archive/research/profit-max-v1-2026-09
  data/historical/profit_max_v1_daily_split_sip.csv  (Alpaca SIP, split-adjusted)
  data/historical/profit_max_v1_corporate_actions.csv (dividends restated to post-split shares)
* crypto sleeve: tag archive/maintenance/crypto-canonical-causal-execution-v1-2026-09
  maintenance/crypto_canonical_causal_execution_v1/CORRECTED_REPLAY_A.json.gz
  (authoritative causal replay of crypto_low_variance_hysteresis_v1, 5% sleeve on $100k,
  costs included; its ``daily_bars`` supply BTC/USD daily closes for the BTC references).

Model (calendar-day loop, UTC days):
* VT35 equity: QLD weight = cap * min(1, 0.35 / sample-vol20 of QLD simple returns * sqrt(252))
  while QQQ close > SMA200, else 0. Decided on the session close, traded at the NEXT session
  open (engine semantics; ``next_close`` kept as a conservative calibration), 0.005 NAV band,
  3 bps per traded notional (2 bps slippage + half of 2 bps spread). QLD dividends credited on ex-date.
* Strategy crypto sleeve: additive contribution account_nav * r_replay where r_replay is the
  replay's daily NAV return (the replay keeps ~5% of its own NAV in crypto, 95% idle cash).
* BTC references: 5% target of NAV (buy-and-hold, or while BTC daily close > SMA200 of daily
  closes), decided on the UTC daily close, traded at the next UTC daily close, band 0.005,
  28 bps per traded notional (25 bps taker + 2 bps slippage + 1 bp half spread).
* Idle cash earns 0 (PAPER/model reality) or, as a sensitivity, an approximate 3-month T-bill
  yield (annual averages, stated in TBILL_APPROX; not downloaded, approximate).
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "RESULTS.json"
EQ_TAG = "archive/research/profit-max-v1-2026-09"
CR_TAG = "archive/maintenance/crypto-canonical-causal-execution-v1-2026-09"
BARS = "data/historical/profit_max_v1_daily_split_sip.csv"
ACTIONS = "data/historical/profit_max_v1_corporate_actions.csv"
REPLAY = "maintenance/crypto_canonical_causal_execution_v1/CORRECTED_REPLAY_A.json.gz"

EQ_COST = 3e-4
CR_COST = 28e-4
BAND = 0.005
# Approximate annual-average 3-month T-bill yields (sensitivity only, not downloaded).
TBILL_APPROX = {2017: 0.0093, 2018: 0.0194, 2019: 0.0206, 2020: 0.0036, 2021: 0.0004,
                2022: 0.0202, 2023: 0.0507, 2024: 0.0497, 2025: 0.0410, 2026: 0.0365}


def git_blob(tag: str, path: str) -> bytes:
    return subprocess.run(["git", "-C", str(REPO), "show", f"{tag}:{path}"],
                          check=True, capture_output=True).stdout


def sha(tag: str, path: str) -> str:
    import hashlib
    return hashlib.sha256(git_blob(tag, path)).hexdigest()


def load_equity():
    closes: dict[str, dict[date, float]] = defaultdict(dict)
    for row in csv.DictReader(io.StringIO(git_blob(EQ_TAG, BARS).decode())):
        if row["symbol"] in ("QQQ", "QLD"):
            d = datetime.fromisoformat(row["timestamp"]).date()
            closes[row["symbol"]][d] = float(row["close"])
            if row["symbol"] == "QLD":
                closes["QLD_OPEN"][d] = float(row["open"])
    divs: dict[date, float] = {}
    for row in csv.DictReader(io.StringIO(git_blob(EQ_TAG, ACTIONS).decode())):
        if row["action_type"] == "cash_dividends" and row["symbol"] == "QLD":
            divs[date.fromisoformat(row["ex_date"])] = float(row["rate"])
    return closes, divs


def load_crypto():
    rep = json.loads(gzip.decompress(git_blob(CR_TAG, REPLAY)))
    nav, expo = {}, {}
    for snap in rep["snapshots"]:
        d = datetime.fromisoformat(snap["label_time"]).date()
        nav[d] = snap["nav"]
        expo[d] = sum(p["qty"] * p["mark"] for p in snap["positions"].values()) / snap["nav"]
    btc = {}
    for bar in rep["daily_bars"]:
        if bar["symbol"] == "BTC/USD":
            btc[datetime.fromisoformat(bar["timestamp"]).date()] = bar["close"]
    return rep, nav, expo, btc


def vt35_weights(closes):
    """Signal weight per session (decided on that session's close), cap excluded."""
    qqq, qld = closes["QQQ"], closes["QLD"]
    days = sorted(set(qqq) & set(qld))
    out = {}
    for i, d in enumerate(days):
        if i < 200:
            continue
        sma = sum(qqq[x] for x in days[i - 199:i + 1]) / 200
        rets = [qld[days[j]] / qld[days[j - 1]] - 1 for j in range(i - 19, i + 1)]
        vol = statistics.stdev(rets) * math.sqrt(252)
        out[d] = min(1.0, 0.35 / vol) if qqq[d] > sma else 0.0
    return days, out


def metrics(series: list[tuple[date, float]]):
    d0, v0 = series[0]
    d1, v1 = series[-1]
    years = (d1 - d0).days / 365.25
    peak, mdd = v0, 0.0
    for _, v in series:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    month_end = {}
    for d, v in series:
        month_end[(d.year, d.month)] = v
    mv = [v0] + [month_end[k] for k in sorted(month_end)]
    mr = [mv[i] / mv[i - 1] - 1 for i in range(1, len(mv))]
    sharpe = statistics.mean(mr) / statistics.stdev(mr) * math.sqrt(12)
    cagr = (v1 / v0) ** (1 / years) - 1
    return {"start": d0.isoformat(), "end": d1.isoformat(), "total_return": v1 / v0 - 1,
            "cagr": cagr, "max_drawdown": mdd, "sharpe_monthly_rf0": sharpe,
            "vol_monthly_ann": statistics.stdev(mr) * math.sqrt(12),
            "calmar": cagr / mdd if mdd else None}


def simulate(start: date, end: date, closes, divs, weights, eq_days, cr_nav, cr_expo, btc,
             eq_cap: float, crypto: str, cash_yield: bool, btc_sma: dict | None = None,
             fill: str = "next_open"):
    qld = closes["QLD"]
    qld_open = closes["QLD_OPEN"]
    session = set(eq_days)
    prev_session = {eq_days[i]: eq_days[i - 1] for i in range(1, len(eq_days))}
    nav = 1.0
    qld_val = 0.0
    btc_val = 0.0
    pending_eq = None  # target decided at previous session close
    pending_cr = None
    costs = 0.0
    series = []
    d = start
    while d <= end:
        rf = TBILL_APPROX[d.year] / 365 if cash_yield else 0.0
        # 1) returns over the day
        pnl = 0.0
        if d in session and d in prev_session:
            p0 = prev_session[d]
            if pending_eq is not None and fill == "next_open":
                # overnight under old holdings, rebalance at the open, intraday under new
                r_on = (qld_open[d] + divs.get(d, 0.0)) / qld[p0] - 1
                pnl += qld_val * r_on
                qld_val *= 1 + r_on
                nav_open = nav + pnl
                target = pending_eq * nav_open
                if abs(target - qld_val) > BAND * nav_open:
                    c = abs(target - qld_val) * EQ_COST
                    costs += c
                    pnl -= c
                    qld_val = target
                pending_eq = None
                r_id = qld[d] / qld_open[d] - 1
                pnl += qld_val * r_id
                qld_val *= 1 + r_id
            else:
                r = (qld[d] + divs.get(d, 0.0)) / qld[p0] - 1
                pnl += qld_val * r
                qld_val *= 1 + r
        crypto_expo = 0.0
        if crypto == "strategy":
            prev = d - timedelta(days=1)
            if d in cr_nav and prev in cr_nav:
                pnl += nav * (cr_nav[d] / cr_nav[prev] - 1)
            crypto_expo = cr_expo.get(prev, 0.0) * nav
        elif crypto in ("btc_bh", "btc_trend"):
            prev = d - timedelta(days=1)
            if d in btc and prev in btc:
                r = btc[d] / btc[prev] - 1
                pnl += btc_val * r
                btc_val *= 1 + r
            crypto_expo = btc_val
        idle = nav - qld_val - crypto_expo
        pnl += max(idle, 0.0) * rf
        nav += pnl
        # 2) execute pending targets at this close
        if fill == "next_close" and d in session and pending_eq is not None:
            target = pending_eq * nav
            if abs(target - qld_val) > BAND * nav:
                c = abs(target - qld_val) * EQ_COST
                costs += c
                nav -= c
                qld_val = target
            pending_eq = None
        if crypto == "btc_bh":
            pending_cr = 0.05
        elif crypto == "btc_trend" and d in btc_sma:
            pending_cr = 0.05 if btc_sma[d] else 0.0
        if crypto in ("btc_bh", "btc_trend") and pending_cr is not None:
            target = pending_cr * nav
            if abs(target - btc_val) > BAND * nav or (pending_cr == 0 and btc_val > 0):
                c = abs(target - btc_val) * CR_COST
                costs += c
                nav -= c
                btc_val = target
            pending_cr = None
        # 3) decide on this close
        if d in session and d in weights:
            pending_eq = eq_cap * weights[d]
        series.append((d, nav))
        d += timedelta(days=1)
    m = metrics(series)
    m["cost_drag_total_frac_of_start"] = costs
    return m, series


def main() -> int:
    closes, divs = load_equity()
    rep, cr_nav, cr_expo, btc = load_crypto()
    eq_days, weights = vt35_weights(closes)
    bdays = sorted(btc)
    btc_sma = {}
    for i, d in enumerate(bdays):
        if i >= 199:
            btc_sma[d] = btc[d] > sum(btc[x] for x in bdays[i - 199:i + 1]) / 200
    start = min(cr_nav)  # 2021-04-01 label (replay first NAV point)
    end = date(2026, 7, 30)  # last common equity session with a crypto NAV
    results: dict = {
        "inputs": {
            "equity_bars": {"tag": EQ_TAG, "path": BARS, "sha256": sha(EQ_TAG, BARS)},
            "equity_actions": {"tag": EQ_TAG, "path": ACTIONS, "sha256": sha(EQ_TAG, ACTIONS)},
            "crypto_replay": {"tag": CR_TAG, "path": REPLAY, "sha256": sha(CR_TAG, REPLAY),
                              "replay_metrics": rep["metrics"]},
            "btc_daily_source": "CORRECTED_REPLAY_A daily_bars BTC/USD (pinned hourly input "
                                "aggregated to complete UTC days)",
            "btc_first_day": bdays[0].isoformat(),
            "btc_sma200_first_day": min(btc_sma).isoformat(),
            "tbill_approx_annual": TBILL_APPROX,
        },
    }
    # Calibration: VT35 alone at 0.93 over the Stage B window (engine: 28.98% CAGR, 29.16% DD)
    for fill in ("next_open", "next_close"):
        cal, _ = simulate(date(2017, 1, 3), date(2026, 7, 30), closes, divs, weights, eq_days,
                          cr_nav, cr_expo, btc, 0.93, "none", False, fill=fill)
        results[f"calibration_vt35_0.93_2017_2026_no_crypto_{fill}"] = cal
        cal, _ = simulate(start, date(2026, 7, 30), closes, divs, weights, eq_days,
                          cr_nav, cr_expo, btc, 0.93, "strategy", False, fill=fill)
        results[f"calibration_A_overlap_{fill}"] = cal
    variants = {
        "A_eq0.93_strategy_crypto0.05": (0.93, "strategy"),
        "B_eq0.93_crypto_removed_cash": (0.93, "none"),
        "C_eq0.98_crypto_removed": (0.98, "none"),
        "R1_eq0.93_btc_buyhold0.05": (0.93, "btc_bh"),
        "R2_eq0.93_btc_sma200_0.05": (0.93, "btc_trend"),
    }
    windows = {"full_2021-04-01_2026-07-30": (start, end),
               "sub_2021-07-20_2026-07-30": (date(2021, 7, 20), end)}
    series_store = {}
    for cy in (False, True):
        for wname, (s, e) in windows.items():
            key = f"{wname}|cash_{'tbill_approx' if cy else 'zero'}"
            results[key] = {}
            for name, (cap, cr) in variants.items():
                m, ser = simulate(s, e, closes, divs, weights, eq_days, cr_nav, cr_expo, btc,
                                  cap, cr, cy, btc_sma)
                results[key][name] = m
                if not cy and wname.startswith("full"):
                    series_store[name] = dict(ser)
    # Sleeve-level crypto statistics
    days = sorted(cr_nav)
    rr = {days[i]: cr_nav[days[i]] / cr_nav[days[i - 1]] - 1 for i in range(1, len(days))}
    sleeve = [(days[0], 1.0)]
    v = 1.0
    for d in days[1:]:
        v *= 1 + rr[d] / 0.05
        sleeve.append((d, v))
    invested_days = sum(1 for d in days if cr_expo[d] > 0.001)
    results["crypto_sleeve_standalone_per_0.05_allocated_capital"] = metrics(sleeve)
    results["crypto_sleeve_invested_day_share"] = invested_days / len(days)
    results["crypto_sleeve_mean_exposure_of_nav"] = statistics.mean(cr_expo.values())
    results["crypto_replay_as_isolated_account"] = metrics([(d, cr_nav[d]) for d in days])
    # Correlations: crypto sleeve vs VT35 sleeve vs QQQ vs BTC (weekly Fri->Fri and monthly)
    eq_only = series_store["B_eq0.93_crypto_removed_cash"]
    qqq = closes["QQQ"]

    def resample(getter, keys):
        out = []
        for k in keys:
            val = getter(k)
            if val is not None:
                out.append(val)
        return [out[i] / out[i - 1] - 1 for i in range(1, len(out))]

    def last_on_or_before(mapping, d):
        for back in range(7):
            x = d - timedelta(days=back)
            if x in mapping:
                return mapping[x]
        return None

    fridays = [d for d in sorted(eq_only) if d.weekday() == 4]
    month_ends = sorted({date(d.year, d.month, 1) for d in eq_only})[1:]
    month_ends = [m - timedelta(days=1) for m in month_ends] + [end]
    corr = {}
    for label, keys in (("weekly", fridays), ("monthly", month_ends)):
        s_cr = resample(lambda d: last_on_or_before(cr_nav, d), keys)
        s_eq = resample(lambda d: last_on_or_before(eq_only, d), keys)
        s_qqq = resample(lambda d: last_on_or_before(qqq, d), keys)
        s_btc = resample(lambda d: last_on_or_before(btc, d), keys)
        corr[label] = {
            "crypto_sleeve_vs_vt35_account": statistics.correlation(s_cr, s_eq),
            "crypto_sleeve_vs_qqq": statistics.correlation(s_cr, s_qqq),
            "btc_vs_vt35_account": statistics.correlation(s_btc, s_eq),
            "btc_vs_qqq": statistics.correlation(s_btc, s_qqq),
            "crypto_sleeve_vs_btc": statistics.correlation(s_cr, s_btc),
            "n": len(s_cr),
        }
    results["correlations"] = corr
    # Calendar-year contribution of the crypto sleeve (account pct points, zero-cash basis)
    a = series_store["A_eq0.93_strategy_crypto0.05"]
    b = series_store["B_eq0.93_crypto_removed_cash"]
    c = series_store["C_eq0.98_crypto_removed"]
    years = {}
    for y in range(2021, 2027):
        ks = [d for d in a if d.year == y]
        prev = [d for d in a if d.year == y - 1]
        k0 = max(prev) if prev else min(ks)
        k1 = max(ks)
        years[str(y)] = {n: s[k1] / s[k0] - 1 for n, s in (("A", a), ("B", b), ("C", c))}
        years[str(y)]["crypto_replay"] = cr_nav[k1] / cr_nav[k0] - 1 if k0 in cr_nav else \
            cr_nav[k1] / cr_nav[min(cr_nav)] - 1
    results["calendar_year_returns"] = years
    # Is the sleeve's contribution distinguishable from zero? monthly A-B and A-C differences

    def monthly(ser):
        me = {}
        for d, v in sorted(ser.items()):
            me[(d.year, d.month)] = v
        vals = [ser[min(ser)]] + [me[k] for k in sorted(me)]
        return [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals))]

    ma, mb, mc = monthly(a), monthly(b), monthly(c)
    tstats = {}
    for label, other in (("A_minus_B", mb), ("A_minus_C", mc)):
        diff = [x - y for x, y in zip(ma, other)]
        se = statistics.stdev(diff) / math.sqrt(len(diff))
        tstats[label] = {"mean_monthly": statistics.mean(diff),
                         "t_stat": statistics.mean(diff) / se, "n_months": len(diff)}
    results["contribution_t_stats"] = tstats
    OUT.write_text(json.dumps(results, indent=2, sort_keys=True, default=str) + "\n")
    print(json.dumps(results, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
