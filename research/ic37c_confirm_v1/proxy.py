"""ic37c_confirm_v1.proxy -- the two extended series, registered into the framework's extra-data
registry **in memory** (`research/batch_screen/` is never edited).

The committed IC37 plugin is imported **unchanged**: its spec declares `leg_b = "ief"` and reads
`data.extra("dgs10")` / `data.extra("t10yie")`. The two series behind those keys are what this
study extends, so the arm's code is literally the committed one:

    mode="proxy" (default)     ief    -> IC37C_IEF_PROXY   (synthetic IEF total return, 1969-07-01 ->)
                              t10yie -> IC37C_BE_PROXY     (T10YIE where published, else the
                                                            point-in-time CPI 12-month change)
    mode="real"                the committed IEF / T10YIE series (the bank_v3 data)

`use()` mutates the cached registry document in place; every `MarketData.extra` call, `leg_b_usable`
check and `resolve_arm` validation then resolves through it. Nothing is written.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from research.batch_screen import extra as _extra

from . import data as D

ROOT = Path(__file__).resolve().parents[2]

IEF_PROXY = "IC37C_IEF_PROXY"
BE_PROXY = "IC37C_BE_PROXY"
IEF_PROXY_FILE = "data/historical/ic37c_confirm_v1_ief_proxy.csv"
BE_PROXY_FILE = "data/historical/ic37c_confirm_v1_be_proxy.csv"

#: the keys the study re-points, and what each one means in each mode
KEYS = {"ief": ("IEF", IEF_PROXY), "t10yie": ("T10YIE", BE_PROXY)}

MODE = "proxy"


def _entry(rel: str, why: str) -> dict:
    p = ROOT / rel
    if not p.exists():
        raise FileNotFoundError(f"{p} is missing -- run `python -m research.ic37c_confirm_v1.data "
                                f"--fetch` first")
    d, v = D.read_two_col(rel)
    ok = np.isfinite(v)
    return {"file": rel, "column": "value", "price": "close",
            "url": "derived in-repo (ic37c_confirm_v1.data)", "source": why,
            "range": [str(d[ok][0]), str(d[ok][-1])], "rows": int(ok.sum()),
            "bytes": p.stat().st_size, "sha256": D.sha256(p)}


def use(mode: str = "proxy") -> dict:
    """Point the extra-data registry at the proxy series (`mode="proxy"`) or back at the committed
    ones (`mode="real"`). Idempotent; returns the key map in force."""
    global MODE
    if mode not in ("proxy", "real"):
        raise ValueError(f"mode must be 'proxy' or 'real', got {mode!r}")
    reg = _extra.registry()          # the framework's committed registry, cached in place
    reg["series"][IEF_PROXY] = _entry(IEF_PROXY_FILE, "synthetic IEF total-return proxy")
    reg["series"][BE_PROXY] = _entry(BE_PROXY_FILE, "breakeven proxy (T10YIE else point-in-time CPI)")
    for key, (real, proxy) in KEYS.items():
        reg["keys"][key] = proxy if mode == "proxy" else real
    MODE = mode
    return {k: reg["keys"][k] for k in KEYS}


# ----------------------------------------------------------------------------- raw series (E1/E5)
def series(rel: str):
    """A `date,value` series file as `(dates, values)`."""
    return D.read_two_col(rel)


def ief_proxy():
    """`(dates, values)` of the synthetic IEF total-return index."""
    return series(IEF_PROXY_FILE)


def real_ief():
    """`(dates, adjclose)` of the real IEF ETF (the Yahoo total-return-comparable series)."""
    d, cols = D.read_wide("data/historical/ic37c_confirm_v1_ief.csv")
    return d, cols["adjclose"]


def be_proxy():
    """`(dates, values)` of the breakeven proxy."""
    return series(BE_PROXY_FILE)


def real_t10yie():
    """`(dates, values)` of the committed 10-year breakeven (FRED T10YIE)."""
    return series("data/historical/ic37c_confirm_v1_t10yie.csv")


def cpi_vintages():
    """`(vintage_date, obs_date, value)` rows of the ALFRED CPI vintages the breakeven uses."""
    rows = []
    with (ROOT / "data/historical/ic37c_confirm_v1_cpi_vintages.csv").open() as fh:
        import csv

        rd = csv.reader(fh)
        next(rd)
        for r in rd:
            if r:
                rows.append((r[0], r[1], float(r[2])))
    return rows


def real_yield_series():
    """The real-yield gate input `DGS10 - BE` the study's `real10y` is a statement about:
    `(dates, real_yield_pp)` of the proxy. Report-only."""
    dg = D.read_two_col("data/historical/ic37c_confirm_v1_dgs10.csv")
    bc, bv = be_proxy()
    pos = np.searchsorted(dg[0], bc, side="right") - 1
    return bc, dg[1][np.clip(pos, 0, None)] - bv


__all__ = ["BE_PROXY", "IEF_PROXY", "KEYS", "MODE", "be_proxy", "cpi_vintages", "ief_proxy",
           "real_ief", "real_t10yie", "real_yield_series", "series", "use"]
