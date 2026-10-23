"""PPUT sanity check: can a Black-Scholes pricer on SPX+VIX reproduce Cboe's PPUT?

Pre-registered pass criteria (PREREGISTRATION.md section 5): at the fixed skew slope
SKEW_S = 2.0 the synthetic index's annualised return must be within 1.5 pp/yr of the published
PPUT in BOTH the 1990-1999 and 2001-2026 windows, and the monthly-return correlation must be at
least 0.95 in both.  The +/-50 % skew rows are reported, not selected on.

The replication is the published methodology approximated at the close: hold one SPX unit, buy a
5 % OTM put on the third Friday (preceding session if a holiday), strike = 0.95 x spot, hold to
the next third Friday, settle at intrinsic, credit the S&P 500 dividend.  The published index is
a total-return index; ^SP500TR supplies the dividend.  The published roll uses the SOQ at the
open and a VWAP near noon, so the close-to-close approximation itself contributes tracking error
that is not attributable to the pricer.
"""

from __future__ import annotations

import bisect
import csv
import math

import numpy as np

from research.fast_engine import fe_data
from research.collar_v1 import config as C


def _load_close(path):
    rows = []
    with open(path) as fh:
        for r in csv.DictReader(fh):
            rows.append((r["date"], float(r["close"])))
    rows.sort()
    return rows


def _series(dates, rows):
    ds = [r[0] for r in rows]
    out = np.empty(len(dates))
    j = 0
    last = np.nan
    while j < len(ds) and ds[j] <= dates[0]:
        last = rows[j][1]
        j += 1
    for k, d in enumerate(dates):
        while j < len(ds) and ds[j] <= d:
            last = rows[j][1]
            j += 1
        out[k] = last
    return out


def third_fridays(days, start, end):
    """Roll sessions: the third Friday of each month, or the preceding trading session."""
    import datetime as dt

    out = []
    y, m = int(start[:4]), int(start[5:7])
    y1, m1 = int(end[:4]), int(end[5:7])
    while (y, m) <= (y1, m1):
        d = 1
        fridays = []
        while True:
            try:
                if dt.date(y, m, d).weekday() == 4:
                    fridays.append(d)
            except ValueError:
                break
            d += 1
        iso = f"{y}-{m:02d}-{fridays[2]:02d}"
        k = bisect.bisect_right(days, iso) - 1
        if k >= 0 and start <= days[k] <= end:
            out.append(k)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return sorted(set(out))


def bs_put(S, K, T, r, q, sig):
    from math import erf, exp, log, sqrt

    if T <= 0:
        return max(K - S, 0.0)
    if sig <= 1e-8:
        return max(K * exp(-r * T) - S * exp(-q * T), 0.0)
    sq = sig * sqrt(T)
    d1 = (log(S / K) + (r - q + 0.5 * sig * sig) * T) / sq
    d2 = d1 - sq
    ncdf = lambda x: 0.5 * (1.0 + erf(x / sqrt(2.0)))
    return K * exp(-r * T) * ncdf(-d2) - S * exp(-q * T) * ncdf(-d1)


def replicate(days, S, TR, vix, rate, s, q_level=0.02, cost=0.0, start=None, end=None):
    """Self-financing one-SPX-unit + 5 % OTM 1-month put portfolio (daily level)."""
    i0 = 0 if start is None else bisect.bisect_left(days, start)
    i1 = len(days) - 1 if end is None else bisect.bisect_right(days, end) - 1
    rolls = [k for k in third_fridays(days, days[i0], days[i1]) if i0 <= k <= i1]
    roll_set = set(rolls)
    div = np.zeros(len(days))
    for k in range(1, len(days)):
        if S[k - 1] > 0 and TR[k - 1] > 0:
            div[k] = S[k - 1] * (TR[k] / TR[k - 1] - S[k] / S[k - 1])
    cash = 0.0
    put = 0.0
    K = None
    expiry = None
    nav = np.zeros(i1 - i0 + 1)
    premium_paid = 0.0
    payoff_recv = 0.0
    notional_days = 0.0
    for k in range(i0, i1 + 1):
        cash += div[k]
        if k in roll_set:
            if K is not None:
                pay = max(K - S[k], 0.0)
                cash += pay
                payoff_recv += pay
            K = 0.95 * S[k]
            j = rolls.index(k)
            nxt = rolls[j + 1] if j + 1 < len(rolls) else i1
            expiry = nxt
            T = (np.datetime64(days[nxt]) - np.datetime64(days[k])).astype(int) / 365.25
            sig = vix[k] * (1.0 + s * math.log(S[k] / K))
            premium = bs_put(S[k], K, T, rate[k], q_level, sig) * (1.0 + cost)
            cash -= premium
            premium_paid += premium
            put = premium
        if K is not None and k != rolls[-1]:
            T = max((np.datetime64(days[expiry]) - np.datetime64(days[k])).astype(int) / 365.25, 0.0)
            sig = vix[k] * (1.0 + s * math.log(S[k] / K))
            put = bs_put(S[k], K, T, rate[k], q_level, sig)
        nav[k - i0] = S[k] + put + cash
        notional_days += S[k]
    yrs = (np.datetime64(days[i1]) - np.datetime64(days[i0])).astype(int) / 365.25
    return {
        "nav": nav,
        "cagr": float((nav[-1] / nav[0]) ** (1 / yrs) - 1),
        "years": float(yrs),
        "premium_pp_yr": float(premium_paid / (notional_days / len(nav)) / yrs * 100),
        "payoff_pp_yr": float(payoff_recv / (notional_days / len(nav)) / yrs * 100),
        "rolls": len(rolls),
    }


def _monthly_returns(nav, days, i0):
    out = []
    base = nav[0]
    key0 = days[i0][:7]
    for k in range(1, len(nav)):
        if days[i0 + k][:7] != key0:
            out.append(nav[k] / base - 1)
            base = nav[k]
            key0 = days[i0 + k][:7]
    return np.array(out)


def _one(days, S, TR, VIX, rate, PP, s, a, b):
    i0 = bisect.bisect_left(days, a)
    i1 = bisect.bisect_right(days, b) - 1
    r = replicate(days, S, TR, VIX, rate, s, start=a, end=b)
    nav = r["nav"]
    pp = PP[i0 : i1 + 1]
    m = min(len(nav), len(pp))
    nav, pp = nav[:m], pp[:m]
    yrs = (np.datetime64(days[i1]) - np.datetime64(days[i0])).astype(int) / 365.25
    pp_cagr = float((pp[-1] / pp[0]) ** (1 / yrs) - 1)
    mon_n = _monthly_returns(nav, days, i0)[: m - 1]
    mon_p = _monthly_returns(pp, days, i0)[: m - 1]
    n = min(len(mon_n), len(mon_p))
    mon_n, mon_p = mon_n[:n], mon_p[:n]
    return {
        "window": [days[i0], days[i1]],
        "synth_cagr": float(r["cagr"]),
        "pput_cagr": pp_cagr,
        "diff_pp": float((r["cagr"] - pp_cagr) * 100),
        "premium_pp_yr": float(r["premium_pp_yr"]),
        "payoff_pp_yr": float(r["payoff_pp_yr"]),
        "rolls": int(r["rolls"]),
        "monthly_corr": float(np.corrcoef(mon_n, mon_p)[0, 1]) if n > 2 else None,
        "monthly_rmse_pp": float(np.sqrt(np.mean((mon_n - mon_p) ** 2)) * 100),
    }


def run_sanity():
    spx = _load_close(C.SPX_CSV)
    tr = _load_close(C.SPXTR_CSV)
    vix = _load_close(C.VIX_CSV)
    pp = _load_close(C.PPUT_CSV)
    days = [d for d, _ in spx]
    S = np.array([v for _, v in spx])
    TR = _series(days, tr)
    VIX = _series(days, vix) / 100.0
    PP = _series(days, pp)
    d3, tb = fe_data.load_rates()
    rate = fe_data.rate_on(days, d3, tb)
    windows = (("1990_1999", "1990-01-02", "1999-12-31"), ("2001_2026", "2001-01-23", "2026-09-29"))
    base = {name: _one(days, S, TR, VIX, rate, PP, C.SKEW_S, a, b) for name, a, b in windows}
    sens = {}
    for label, s in (("s_minus_50", C.SKEW_S * 0.5), ("s_plus_50", C.SKEW_S * 1.5)):
        sens[label] = {"skew_s": s, **{name: _one(days, S, TR, VIX, rate, PP, s, a, b) for name, a, b in windows}}
    ok = all(abs(base[n]["diff_pp"]) <= 1.5 and base[n]["monthly_corr"] >= 0.95 for n, _, _ in windows)
    return {
        "skew_s": C.SKEW_S,
        "windows": base,
        "sensitivity": sens,
        "criteria": {"max_abs_annual_diff_pp": 1.5, "min_monthly_corr": 0.95},
        "pass": bool(ok),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run_sanity(), indent=1))
