"""mf_sleeve_v1.build_proxy -- a long-history managed-futures (trend-following) proxy series.

No free daily series of a managed-futures fund reaches back to 2000 (DBMF starts 2019, KMLM
2020), so the study builds one from the repo's committed inputs with parameters fixed in
PREREGISTRATION.md before any arm is screened:

* nine markets: ^GSPC, ^N225, ^GDAXI, ^FTSE (price indices), a synthetic 10-year Treasury
  (FRED DGS10, duration 7.5), WTI spot (FRED DCOILWTICO), gold (GC=F), copper (HG=F) and the
  broad dollar (FRED DTWEXBGS); every series is forward-filled onto the NYSE calendar;
* each market's excess return: price return (indices, commodities, dollar), and for the bond
  carry + duration return minus the T-bill;
* signal = mean of sign(21 / 63 / 252-session excess return), in [-1, 1];
* raw position = signal x 0.10 / vol60 (annualised, lagged); a market trades once it has
  252 + 60 sessions of history;
* the book is scaled to 12 % ex-ante annual vol (60-session covariance of the current
  positions), gross exposure capped at 4;
* positions decided at close t earn the excess return of t + 1; 3 bp per unit of traded
  notional, 0.85 %/yr fund fee, collateral earns DTB3.

Output: `data/historical/mf_sleeve_v1_proxy.csv` (`date,close`), registered as the extra key
`mf_proxy` (main window only) by `register.py`.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
H = ROOT / "data" / "historical"
OUT = H / "mf_sleeve_v1_proxy.csv"

EQ = {"gspc": "batch_screen_v1_gspc.csv", "n225": "batch_screen_v1_n225.csv",
      "gdaxi": "batch_screen_v1_gdaxi.csv", "ftse": "batch_screen_v1_ftse.csv"}
COMM = {"gold": ("plugins_b3_gc_f.csv", "close"), "copper": ("batch_screen_v1_hg_f.csv", "close"),
        "wti": ("plugins_c4_dcoilwtico.csv", "value"), "usd": ("batch_screen_v1_dtwexbgs.csv", "value")}
DURATION = 7.5
LOOKBACKS = (21, 63, 252)
VOL_N = 60
ASSET_RISK = 0.10
BOOK_VOL = 0.12
GROSS_CAP = 4.0
COST = 3e-4
FEE = 0.0085
START = "1990-01-02"


def _series(fname, col):
    df = pd.read_csv(H / fname)
    dcol = df.columns[0]
    s = pd.to_numeric(df[col], errors="coerce")
    s.index = pd.to_datetime(df[dcol])
    s = s[s > 0].dropna()
    return s[~s.index.duplicated()]


def build():
    cal = _series("batch_screen_v1_gspc.csv", "close").index
    cal = cal[cal >= START]
    dtb3 = _series("stress_robustness_v1_dtb3.csv", "DTB3").reindex(cal).ffill() / 100.0
    rf = (dtb3.shift(1) / 252.0).fillna(0.0)

    rets = {}
    for k, f in EQ.items():
        p = _series(f, "close").reindex(cal).ffill()
        rets[k] = p.pct_change()
    for k, (f, c) in COMM.items():
        p = _series(f, c).reindex(cal).ffill()
        rets[k] = p.pct_change()
    y = _series("batch_screen_v1_dgs10.csv", "value").reindex(cal).ffill() / 100.0
    rets["ust10"] = y.shift(1) / 252.0 - DURATION * y.diff() - rf
    R = pd.DataFrame(rets).replace([np.inf, -np.inf], np.nan)
    # a market starts trading once 252 + 60 sessions of its own returns exist
    have = R.notna().cumsum()
    live = have >= (max(LOOKBACKS) + VOL_N)

    logc = np.log1p(R.fillna(0.0)).cumsum()
    sig = sum(np.sign(logc - logc.shift(n)) for n in LOOKBACKS) / len(LOOKBACKS)
    vol = R.rolling(VOL_N).std() * np.sqrt(252)
    raw = (sig * ASSET_RISK / vol).where(live).fillna(0.0)

    Rf = R.fillna(0.0).to_numpy()
    W = raw.to_numpy()
    T, N = W.shape
    pos = np.zeros_like(W)
    for t in range(VOL_N, T):
        w = W[t]
        if not np.any(w):
            continue
        cov = np.cov(Rf[t - VOL_N + 1 : t + 1].T) * 252
        sv = float(np.sqrt(max(w @ cov @ w, 1e-12)))
        k = BOOK_VOL / sv
        g = np.abs(w).sum() * k
        if g > GROSS_CAP:
            k *= GROSS_CAP / g
        pos[t] = w * k
    held = np.vstack([np.zeros((1, N)), pos[:-1]])          # decided at t-1 close, earns t
    traded = np.abs(np.diff(np.vstack([np.zeros((1, N)), pos]), axis=0)).sum(axis=1)
    cost = np.concatenate([[0.0], traded[:-1]]) * COST
    r = rf.to_numpy() + (held * Rf).sum(axis=1) - cost - FEE / 252.0
    lvl = np.cumprod(1.0 + r)
    out = pd.Series(lvl, index=cal)
    gross = pd.Series(np.abs(held).sum(axis=1), index=cal)
    return out, gross, pd.DataFrame(held, index=cal, columns=R.columns)


def main():
    lvl, gross, held = build()
    with OUT.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "close"])
        for d, v in lvl.items():
            w.writerow([d.strftime("%Y-%m-%d"), f"{v:.10f}"])
    yr = lvl.resample("YE").last().pct_change().dropna()
    print("wrote", OUT, len(lvl), "rows", lvl.index[0].date(), "->", lvl.index[-1].date())
    print("annual returns:", " ".join(f"{d.year}:{v:+.1%}" for d, v in yr.items() if d.year >= 1995))
    print("mean gross since 2000:", round(float(gross["2000":].mean()), 2))


if __name__ == "__main__":
    main()
