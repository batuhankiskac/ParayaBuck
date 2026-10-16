"""fe_data -- market data loaders for the research engine (verbatim semantics of the studies).

These are the loaders the study screens copy from each other: `trend_robustness_v2.load` (the
synthetic daily-reset 2x QLD*, `stress_robustness_v1_daily.csv` + DTB3), `band_h2_combo_v1`'s
`main_inputs`, `fx_at`, `usd_market`, `load_gspc`, `load_market`, `load_instrument`, `quality_ok`
and `rate_on`. Nothing here is re-derived: the parsing, date handling and fallbacks are the
published ones, so a study that swaps `screen.py` for this package gets the same arrays bit for
bit (parity.py asserts it).
"""

from __future__ import annotations

import bisect
import csv
from collections import defaultdict

import numpy as np

from . import fe_signals, fe_tax
from .fe_config import (
    BARS,
    DTB3_CSV,
    DTB3_FIRST,
    EXP_Q,
    FX_CSV,
    GSPC_CSV,
    HIST,
    INDEX_CSV,
    INSTRUMENT_FILES,
    MARKETS,
    OOS_GSPC_FIRST,
    OOS_GSPC_LAST,
    QUALITY_MAX_BAD_FRAC,
    START,
    TB3MS_CSV,
    UFE_CSV,
)


def load():
    """`trend_robustness_v2.load`: QQQ/SPY/QLD (open, close, dividend) on the QQQ & SPY days."""
    raw = defaultdict(dict)
    with BARS.open() as fh:
        for r in csv.DictReader(fh):
            raw[r["symbol"]][r["date"]] = tuple(float(r[k]) for k in ("open", "close", "dividend"))
    days = sorted(set(raw["QQQ"]) & set(raw["SPY"]))
    tb, last = {}, None
    with DTB3_CSV.open() as fh:
        rows = {r["observation_date"]: r["DTB3"] for r in csv.DictReader(fh)}
    for d in sorted(set(rows) | set(days)):
        v = rows.get(d, ".")
        if v not in (".", ""):
            last = float(v) / 100
        tb[d] = last
    arr = {}
    for s in ("QQQ", "SPY", "QLD"):
        a = np.full((3, len(days)), np.nan)
        for i, d in enumerate(days):
            if d in raw[s]:
                a[:, i] = raw[s][d]
        arr[s] = a
    y = np.array([tb[d] for d in days])
    return days, arr, y


_FX_CACHE = {}


def fx_at(dates):
    """`screen.fx_at`: last CBRT USD/TRY buying rate at or before each date."""
    if not _FX_CACHE:
        fd, fv = [], []
        with FX_CSV.open() as fh:
            for r in csv.DictReader(fh):
                fd.append(r["date"])
                fv.append(float(r["usdtry_forex_buying"]))
        _FX_CACHE["dates"], _FX_CACHE["fx"] = fd, fv
    fd, fv = _FX_CACHE["dates"], _FX_CACHE["fx"]
    return np.array([fv[bisect.bisect_right(fd, d) - 1] for d in dates], dtype=float)


def load_ufe():
    """The YI-UFE month->index dict."""
    return fe_tax.load_ufe(UFE_CSV)


def usd_market(days):
    """`screen.usd_market`: fx = 1 and a flat YI-UFE (no indexation, scale = 1)."""
    days = list(days)
    years = sorted({int(d[:4]) for d in days})
    ufe = {
        f"{y}-{mo:02d}": 1.0 for y in range(min(years[0], 2025) - 1, max(years[-1], 2026) + 2) for mo in range(1, 13)
    }
    return np.ones(len(days)), ufe


def main_inputs(spread):
    """`screen.main_inputs`: (days, synthetic-2x open/close, E2 wants, R2 wants, E2 vote)."""
    days, arr, y = load()
    q = arr["QQQ"]
    o, c = fe_signals.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
    price, e2, vol_instr = q[1], fe_signals.e2_up(q[1])[0], fe_signals.vol_n(c)[0]
    comps = fe_signals.early_components(price)
    w_e2 = fe_signals.arm_signals(price, e2, vol_instr, "E2", comps)[0][0]
    w_r2 = fe_signals.arm_signals(price, e2, vol_instr, "R2", comps)[0][0]
    return days, o, c, w_e2, w_r2, e2


def load_rates():
    """`screen.load_rates`: (DTB3, TB3MS) as (date, rate) pairs, missing values dropped."""
    d3, tb = [], []
    with DTB3_CSV.open() as fh:
        for r in csv.DictReader(fh):
            if r["DTB3"] not in (".", ""):
                d3.append((r["observation_date"], float(r["DTB3"]) / 100))
    with TB3MS_CSV.open() as fh:
        for r in csv.DictReader(fh):
            if r["TB3MS"] not in (".", ""):
                tb.append((r["observation_date"], float(r["TB3MS"]) / 100))
    return d3, tb


def rate_on(dates, d3, tb):
    """`screen.rate_on`: DTB3 (forward-filled) from 1954-01-04 on, TB3MS before that."""
    out = []
    i3 = it = 0
    last3 = lastt = None
    for d in dates:
        while i3 < len(d3) and d3[i3][0] <= d:
            last3 = d3[i3][1]
            i3 += 1
        while it < len(tb) and tb[it][0] <= d:
            lastt = tb[it][1]
            it += 1
        if d >= DTB3_FIRST:
            out.append(last3)
        else:
            if lastt is None:
                raise RuntimeError(f"no T-bill rate on {d}")
            out.append(lastt)
    return np.array(out, dtype=float)


def load_gspc():
    """`screen.load_gspc`: ^GSPC from the first DTB3 date to 1999-12-31, opens := previous close."""
    rows = []
    with GSPC_CSV.open() as fh:
        for r in csv.DictReader(fh):
            rows.append((r["date"], float(r["close"])))
    tb = {}
    with DTB3_CSV.open() as fh:
        for r in csv.DictReader(fh):
            tb[r["observation_date"]] = r["DTB3"]
    first_tb = min(tb)
    rows = [r for r in rows if first_tb <= r[0] <= OOS_GSPC_LAST]
    days = [r[0] for r in rows]
    c = np.array([r[1] for r in rows])
    o = np.concatenate([[c[0]], c[:-1]])
    y, last = [], None
    dset = set(days)
    for d in sorted(set(tb) | set(days)):
        v = tb.get(d, ".")
        if v not in (".", ""):
            last = float(v) / 100
        if d in dset:
            y.append(last)
    start = next(i for i, d in enumerate(days) if d >= OOS_GSPC_FIRST)
    assert start >= START
    return days, o, c, np.array(y), start


def placeholder_open(o, h, lo, c):
    """`screen.placeholder_open`: the pre-registered open rule."""
    return (
        o is None
        or h is None
        or lo is None
        or o <= 0
        or (o == h == lo == c)
        or o > h * (1 + 1e-9)
        or o < lo * (1 - 1e-9)
    )


def load_market(key):
    """`screen.load_market`: one OOS index window (symbol, first, measured-from, last)."""
    sym, first, meas_from, last = MARKETS[key]
    rows = []
    with INDEX_CSV.open() as fh:
        for r in csv.DictReader(fh):
            if r["symbol"] == sym and first <= r["date"] <= last:

                def _f(x):
                    return None if x == "" else float(x)

                rows.append((r["date"], _f(r["open"]), _f(r["high"]), _f(r["low"]), float(r["close"])))
    days = [r[0] for r in rows]
    c = np.array([r[4] for r in rows])
    o = np.empty_like(c)
    for k, (d, op, h, lo, cl) in enumerate(rows):
        o[k] = cl if k == 0 else (c[k - 1] if placeholder_open(op, h, lo, cl) else op)
    d3, tb = load_rates()
    y = rate_on(days, d3, tb)
    start = START if meas_from is None else next(i for i, d in enumerate(days) if d >= meas_from)
    assert start >= START
    return days, o, c, y, start


def load_instrument(key):
    """`screen.load_instrument`: adjusted OHLC (close := adjclose, open/high/low scaled)."""
    rows = []
    with (HIST / INSTRUMENT_FILES[key]).open() as fh:
        for r in csv.DictReader(fh):
            rows.append(
                (
                    r["date"],
                    float(r["open"]),
                    float(r["high"]),
                    float(r["low"]),
                    float(r["close"]),
                    float(r["adjclose"]),
                )
            )
    dates = [r[0] for r in rows]
    o = np.empty(len(rows))
    h = np.empty(len(rows))
    lo = np.empty(len(rows))
    c = np.empty(len(rows))
    for k, (d, op, hi, l, cl, adj) in enumerate(rows):
        f = adj / cl
        o[k] = op * f
        h[k] = hi * f
        lo[k] = l * f
        c[k] = adj
    d3, tb = load_rates()
    y = rate_on(dates, d3, tb)
    return dates, o, h, lo, c, y


def quality_ok(high, low, opn, close):
    """`screen.quality_ok`: > 5 % of bars with high == low or an invalid open."""
    bad = 0
    for hi, lo, op, cl in zip(high, low, opn, close):
        if hi is None or lo is None or hi == lo:
            bad += 1
            continue
        if op is None or op <= 0 or op < lo * (1 - 1e-9) or op > hi * (1 + 1e-9):
            bad += 1
    frac = bad / max(1, len(high))
    return frac, bool(frac <= QUALITY_MAX_BAD_FRAC)
