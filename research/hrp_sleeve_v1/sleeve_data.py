"""hrp_sleeve_v1 -- daily total-return series for the diversifier sleeve (IEF*, GLD*, BIL*).

Pre-registered splices (see PREREGISTRATION.md section 3). Real series are used whenever both the
session and the prior session exist in the real file; otherwise the proxy is used.

  IEF*  before 2002-07-30: duration-7.5 synthetic from FRED GS10 (monthly step yield), daily
        return = y_prev/252 - D*dy - 0.15%/252 (the expense ratio). From 2002-07-31: IEF adjclose.
  GLD*  2000-08-31 .. 2004-11-17: continuous gold futures (riskoff_rotation_v2_gcf adjclose) minus
        0.40%/yr. From 2004-11-19: GLD adjclose. Before 2000-08-31: NaN (asset not in the universe).
  BIL*  all sessions: DTB3 accrual (previous session's rate / 252) minus 0.1354%/yr.
"""
from __future__ import annotations

import csv
import math

import numpy as np

from research.fast_engine import fe_config as C

GS10_CSV = C.HIST / "riskoff_rotation_v2_gs10.csv"
IEF_CSV = C.HIST / "batch_screen_v1_ief.csv"
GLD_CSV = C.HIST / "batch_screen_v1_gld.csv"
GCF_CSV = C.HIST / "riskoff_rotation_v2_gcf.csv"
D_IEF = 7.5
ER_IEF, ER_GLD, ER_BIL = 0.0015, 0.0040, 0.001354
NAMES = ("IEF", "GLD", "BIL")


def _read_close(path, col):
    out = {}
    with path.open() as fh:
        for r in csv.DictReader(fh):
            v = r[col]
            if v not in ("", "."):
                out[r["date"] if "date" in r else r["observation_date"]] = float(v)
    return out


def _ffill_map(days, src):
    """value of the latest observation dated on or before each session (NaN if none)."""
    keys = sorted(src)
    out = np.full(len(days), np.nan)
    j, last = 0, None
    for i, d in enumerate(days):
        while j < len(keys) and keys[j] <= d:
            last = src[keys[j]]
            j += 1
        if last is not None:
            out[i] = last
    return out


def _real_returns(days, closes):
    """daily return on the session when both it and the prior session are real; NaN otherwise."""
    px = _ffill_map(days, closes)
    have = np.array([d in closes for d in days])
    r = np.full(len(days), np.nan)
    for i in range(1, len(days)):
        if have[i] and have[i - 1]:
            r[i] = px[i] / px[i - 1] - 1.0
    return r, have


def sleeve_returns(days):
    """(IEF*, GLD*, BIL*) daily returns aligned to `days` (row 0 is NaN); `days` are 'YYYY-MM-DD'."""
    T = len(days)
    gs10 = _read_close(GS10_CSV, "GS10")
    gs10 = {k: v for k, v in gs10.items()}
    y = _ffill_map(days, gs10)  # percent
    ief_c = _read_close(IEF_CSV, "adjclose")
    gld_c = _read_close(GLD_CSV, "adjclose")
    gcf_c = _read_close(GCF_CSV, "adjclose")
    dtb3 = _read_close(C.DTB3_CSV, "DTB3")
    rate = _ffill_map(days, dtb3) / 100.0

    # IEF*: real where available, duration-7.5 GS10 synthetic before
    ief_real, ief_have = _real_returns(days, ief_c)
    ief = np.full(T, np.nan)
    for i in range(1, T):
        if not math.isnan(ief_real[i]):
            ief[i] = ief_real[i]
        elif not ief_have[i] or not ief_have[i - 1]:
            if not (math.isnan(y[i]) or math.isnan(y[i - 1])):
                ief[i] = (y[i - 1] / 100.0) / 252.0 - D_IEF * (y[i] - y[i - 1]) / 100.0 - ER_IEF / 252.0

    # GLD*: real from GLD inception, gold futures before (2000-08-31 on), NaN before that
    gld_real, gld_have = _real_returns(days, gld_c)
    gcf_real, gcf_have = _real_returns(days, gcf_c)
    gld = np.full(T, np.nan)
    for i in range(1, T):
        if not math.isnan(gld_real[i]):
            gld[i] = gld_real[i]
        elif not gld_have[i] or not gld_have[i - 1]:
            if not math.isnan(gcf_real[i]):
                gld[i] = gcf_real[i] - ER_GLD / 252.0

    # BIL*: DTB3 accrual of the prior session less the expense ratio
    bil = np.full(T, np.nan)
    for i in range(1, T):
        if not math.isnan(rate[i - 1]):
            bil[i] = rate[i - 1] / 252.0 - ER_BIL / 252.0
    return ief, gld, bil


def prices_from_returns(r):
    """cumulative index: NaN before the series starts; rebased to 1 at its first return."""
    p = np.full(r.shape[0], np.nan)
    for i in range(r.shape[0]):
        if math.isnan(r[i]):
            p[i] = np.nan
        elif i == 0 or math.isnan(p[i - 1]):
            p[i] = 1.0 + r[i]
        else:
            p[i] = p[i - 1] * (1.0 + r[i])
    return p
