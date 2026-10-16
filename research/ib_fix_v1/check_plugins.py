"""research/ib_fix_v1/check_plugins.py -- unit checks for the IB12 / IB14 bug fixes.

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.ib_fix_v1.check_plugins

No simulation, no NAV, no CAGR, no gate, no registry write: only `targets` / `real_10y` /
`credit_series` are called, on small synthetic fixtures and on the real panels.  The fixtures are
exactly the two shapes the framework's smoke runner does not build:

* a bootstrap chunk whose drawn source session differs from the session the buggy IB12 read
  (`extra_days[extra_index]` vs `extra_days[extra_index - 1]`), and
* a hand-made monthly calendar containing a month whose 1st is not a trading session -- the IB14
  case an exact-date join plus a forward fill cannot see.

Every expectation is re-derived independently (month-ordinal as-of lookups written as explicit
loops over the series' own monthly grid, a CPI year-on-year built from the level grid rather than
from the plugin's cache): a fix that only re-orders the same expression fails here.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

import numpy as np

from research.batch_screen import api, boot as B, extra as _ext, smoke, windows
from research.fast_engine import fe_boot, fe_signals
from research.fast_engine.fe_config import BLOCK, DON_N, SEED, SMA_N, TAX_BOOT_EXT, TAX_BOOT_L

P_BOOT = 3
FAILED = []


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -- ' + detail) if detail else ''}")
    if not ok:
        FAILED.append(name)


def same(a, b):
    return bool(np.allclose(np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64),
                            rtol=0, atol=1e-12, equal_nan=True))


# ----------------------------------------------------------------------------- fixtures
def weekdays(start: str, end: str) -> list:
    """Every weekday (a session list with no holiday calendar) between two dates."""
    d, out = date.fromisoformat(start), []
    while d <= date.fromisoformat(end):
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def synthetic_panel(days, window, P=1):
    """A minimal `MarketData` (its shape and calendar is all these checks read)."""
    n = len(days)
    price = np.tile(np.linspace(100.0, 130.0, n), (P, 1))
    div = np.zeros((P, n))
    rate = np.full((P, n), 0.04)
    return api.panel(days, price=price, div=div, rate=rate, o2=price * 0.998, c2=price, d2=div,
                     window=window)


def month_ord(days):
    """Month ordinals (months since 1970-01) of ISO day strings, shape preserved."""
    return np.asarray(days).astype("datetime64[D]").astype("datetime64[M]").astype(np.int64)


def monthly_grid(name):
    """``(month ordinals, values)`` of a registered monthly series, on its own declared grid."""
    meta = _ext.series_meta(name)
    grid = np.arange(np.datetime64(meta["range"][0], "M"),
                     np.datetime64(meta["range"][1], "M") + np.timedelta64(1, "M"))
    vals = _ext.values_on(name, grid.astype("datetime64[D]").astype(str))
    have = np.isfinite(vals)
    return grid[have].astype(np.int64), vals[have]


def asof_ref(months, vals, m):
    """Independent (loop) as-of: the value of the last month ordinal ``<= m``, ``(P, T)``."""
    shape = np.shape(m)
    m = np.atleast_2d(np.asarray(m))
    out = np.full(m.shape, np.nan)
    for p in range(out.shape[0]):
        for t in range(out.shape[1]):
            sel = months <= int(m[p, t])
            if sel.any():
                out[p, t] = vals[sel][-1]
    return out.reshape(shape)


def cpi_yoy_ref(m):
    """Independent CPI year-on-year one month behind month ordinal ``m``, from the level grid."""
    months, lvl = monthly_grid("CPIAUCSL")
    level = dict(zip(months.tolist(), lvl.tolist()))
    shape = np.shape(m)
    m = np.atleast_2d(np.asarray(m))
    out = np.full(m.shape, np.nan)
    for p in range(out.shape[0]):
        for t in range(out.shape[1]):
            prev, year = level.get(int(m[p, t]) - 1), level.get(int(m[p, t]) - 13)
            if prev is not None and year is not None:
                out[p, t] = prev / year - 1.0
    return out.reshape(shape)


def boot_chunk():
    """One real clean-bootstrap chunk (the drawn source sessions of each column)."""
    win = B.BootWindow("none")
    pos = fe_boot.draw_positions(np.random.default_rng(SEED), BLOCK, P_BOOT, TAX_BOOT_L,
                                 win.npool, TAX_BOOT_EXT)
    return B.boot_panels(win, pos, ("2x",), (0.0,))[("2x", 0.0)]


# ----------------------------------------------------------------------------- IB12
def checks_ib12():
    mod = api.load_idea("IB12")

    # (a) the bootstrap source date: `extra.aligned` reads `pool[extra_index]`, not `[… - 1]`
    p = boot_chunk()
    ei, ed = np.asarray(p.extra_index), np.asarray(p.extra_days)
    src_ok, src_bad = ed[ei], ed[ei - 1]
    m_ok, m_bad = month_ord(src_ok), month_ord(src_bad)
    check("IB12 fixture: the chunk's column t source month differs from extra_index-1 on some cells",
          int((m_ok != m_bad).sum()) > 0,
          f"{int((m_ok != m_bad).sum())} of {m_ok.size} cells")
    dgs = p.extra("dgs10", ffill=True)
    want = dgs - 100.0 * cpi_yoy_ref(m_ok)
    other = dgs - 100.0 * cpi_yoy_ref(m_bad)
    got = mod.real_10y(p)
    check("IB12 real_10y on a chunk = DGS10 - the CPI YoY of pool[extra_index]'s month",
          same(got, want), f"max |diff| {np.nanmax(np.abs(got - want)):.2e}")
    check("IB12 real_10y is NOT the extra_index-1 read (the reported bug)",
          int((~np.isclose(got, other, equal_nan=True)).sum()) > 0,
          f"{int((~np.isclose(got, other, equal_nan=True)).sum())} cells differ from the buggy read")

    # the real windows have no `extra_index`: the read is the panel's own calendar, unchanged
    days, rp, _i0, _i1 = windows.main_panel(0.0)
    want_main = rp.extra("dgs10", ffill=True) - 100.0 * cpi_yoy_ref(month_ord(days))
    check("IB12 real_10y on the main window = DGS10 - the CPI YoY of the panel's own month",
          same(mod.real_10y(rp), want_main))

    # (b) the 4th vote's fallback: 2 of 3 price votes when the real rate is missing
    sp = smoke.synthetic_panel()                       # 3 paths, 800 sessions
    votes = (fe_signals.sma_up(sp.price, SMA_N).astype(int)
             + fe_signals.tsm_up_price(sp.price).astype(int)
             + fe_signals.don_up(sp.price, DON_N).astype(int))
    block = np.zeros((sp.P, sp.T), dtype=bool)
    where = None
    for start in range(300, sp.T - 60):
        cell = (votes[:, start : start + 60] == 2).any() and (votes[:, start : start + 60] == 3).any()
        if cell:
            where = start
            break
    check("IB12 fixture: a 60-session block with 2-of-3 and 3-of-3 price votes exists",
          where is not None, f"block starts at column {where}")
    block[:, where : where + 60] = True
    rr = np.where(block, np.nan, 3.0)                  # above the 2.0 % threshold / missing
    orig = mod.real_10y
    mod.real_10y = lambda data: rr
    try:
        got_c = mod.targets(sp, dict(mod.ARMS["C"]))
    finally:
        mod.real_10y = orig
    comps = fe_signals.early_components(sp.price)
    fixed_vote = np.where(block, votes >= 2, votes >= 3)          # 2 of 3 without the 4th vote
    buggy_vote = np.where(block, votes >= 3, votes >= 3)          # 3 of 3: the reported bug
    fixed = fe_signals.sized_wants(*fe_signals.r2_state(sp.price, fixed_vote, comps), sp.vol)
    buggy = fe_signals.sized_wants(*fe_signals.r2_state(sp.price, buggy_vote, comps), sp.vol)
    check("IB12 fixture: the fixed and buggy thresholds really differ inside the block",
          int((fixed_vote != buggy_vote).sum()) > 0,
          f"{int((fixed_vote != buggy_vote).sum())} sessions")
    check("IB12/C requires 2 of 3 price votes while the real rate is missing",
          same(got_c, fixed), f"max |diff| {np.nanmax(np.abs(got_c - fixed)):.2e}")
    check("IB12/C is NOT the 3-of-3 fallback (the reported bug)",
          int((~np.isclose(got_c, buggy)).sum()) > 0)

    # applicability unchanged
    scoped = [windows.oos_panel(k)[1] for k in ("M3", "M4", "M5")]
    na = 0
    for q in scoped:
        try:
            mod.targets(q, dict(mod.ARMS["A"]))
        except api.NotApplicable:
            na += 1
    check("IB12 still refuses M3/M4/M5 (the declared OOS scope) and runs on the main window",
          na == 3 and isinstance(mod.targets(rp, dict(mod.ARMS["A"])), np.ndarray))


# ----------------------------------------------------------------------------- IB14
def old_alignment(data, mod):
    """HEAD's IB14 alignment, reproduced independently: exact-date extra join + ffill, then the
    1-month lag taken in the *panel's* column space (HEAD's `credit_series` + `month_lag`)."""
    s = data.extra("baa_m", ffill=True) - data.extra("aaa_m", ffill=True)
    d = mod.session_calendar(data).astype("datetime64[M]").astype(np.int64)
    yr, mon = (d // 12) + 1970, (d % 12) + 1
    mk = yr * 12 + mon
    out = np.full_like(s, np.nan)
    for p in range(mk.shape[0]):
        row = mk[p]
        j = np.searchsorted(row, row - 1, side="right") - 1
        ok = j >= 0
        out[p, ok] = s[p, j[ok]]
    return out

def checks_ib14():
    mod = api.load_idea("IB14")
    baa, aaa = _ext.resolve_series("baa_m", "M2"), _ext.resolve_series("aaa_m", "M2")
    months, spread = monthly_grid(baa)[0], monthly_grid(baa)[1] - monthly_grid(aaa)[1]

    # a month whose 1st is not a session: 2021-05-01 is a Saturday
    days = weekdays("2021-04-01", "2021-07-30")
    check("IB14 fixture: the calendar contains a month whose 1st is not a session",
          "2021-05-01" not in days and "2021-06-01" in days and "2021-07-01" in days)
    p = synthetic_panel(days, "M2")
    m = month_ord(days)
    want = asof_ref(months, spread, m - 1)             # the observation of reference month M-1
    got, what = mod.credit_series(p, dict(mod.ARMS["A"]))
    check("IB14 credit_series on a real-calendar fixture = the observation of reference month M-1",
          same(got, want) and what.startswith("monthly"), f"max |diff| {np.nanmax(np.abs(got - want)):.2e}")
    old = old_alignment(p, mod)
    bad = ~np.isclose(old, want, equal_nan=True)
    check("IB14 the old exact-date+ffill alignment is NOT what credit_series returns",
          int(bad.sum()) > 0, f"{int(bad.sum())} of {bad.size} sessions differ (the reported bug)")

    # the real OOS windows: same as-of convention, already before the fix the whole month was off
    for key in ("M1", "M2", "GSPC_1957_1999"):
        _d, q, _i0, _i1 = windows.oos_panel(key)
        mm = month_ord(mod.session_calendar(q))
        exp = asof_ref(months, spread, mm - 1)
        g = mod.credit_series(q, dict(mod.ARMS["A"]))[0]
        check(f"IB14 credit_series({key}) = the as-of observation of reference month M-1",
              same(g, exp), f"max |diff| {np.nanmax(np.abs(g - exp)):.2e}")
        check(f"IB14 the old alignment differs on {key} (the bug was live on a real window)",
              int((~np.isclose(old_alignment(q, mod), exp, equal_nan=True)).sum()) > 0)

    # the daily path (main + ETF windows) is untouched
    main_days, mp, _i0, _i1 = windows.main_panel(0.0)
    dm, what_d = mod.credit_series(mp, dict(mod.ARMS["A"]))
    check("IB14 the daily BAA10Y path on the main window is unchanged",
          what_d.startswith("daily") and same(dm, mp.extra("baa10y", ffill=True)))

    # a bootstrap chunk of a monthly window reads the drawn source sessions' months
    win = B.BootWindow("none")
    pos = fe_boot.draw_positions(np.random.default_rng(SEED), BLOCK, P_BOOT, TAX_BOOT_L,
                                 win.npool, TAX_BOOT_EXT)
    chunk = B.boot_panels(win, pos, ("2x",), (0.0,))[("2x", 0.0)]
    chunk.extra_window = "M2"
    mm = month_ord(np.asarray(chunk.extra_days)[np.asarray(chunk.extra_index)])
    gchunk = mod.credit_series(chunk, dict(mod.ARMS["A"]))[0]
    check("IB14 credit_series in a monthly-window chunk = the as-of months of the drawn sessions",
          same(gchunk, asof_ref(months, spread, mm - 1)),
          f"max |diff| {np.nanmax(np.abs(gchunk - asof_ref(months, spread, mm - 1))):.2e}")

    # applicability unchanged: the US windows + ETF/chunks in, M3-M5 out
    ok = all(isinstance(mod.credit_series(windows.oos_panel(k)[1], dict(mod.ARMS["A"]))[0],
                        np.ndarray) for k in ("M1", "M2", "GSPC_1957_1999"))
    na = 0
    for k in ("M3", "M4", "M5"):
        try:
            mod.credit_series(windows.oos_panel(k)[1], dict(mod.ARMS["A"]))
        except api.NotApplicable:
            na += 1
    check("IB14 still refuses M3/M4/M5 and runs on the three US windows", ok and na == 3)


def main() -> int:
    print("IB12 / IB14 fix checks -- targets/reals only, no simulation, no registry write.")
    checks_ib12()
    checks_ib14()
    print(f"checks {'PASS' if not FAILED else 'FAIL'} ({len(FAILED)} failed)")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
