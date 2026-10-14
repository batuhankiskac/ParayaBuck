# range_vol_v2 — pre-registration (2026-10-14)

Committed with `screen.py` and `DATA_MANIFEST.json` before any non-control arm is
simulated on real data. Only `screen.py --harness` (C0 reproduction) ran before
this commit; the full run path was not modified beyond the v1 harness it imports.

## Question

Is range_vol_v1's result real, or is it (a) a basis confound, (b) weak OOS, or
(c) post-hoc selection? Test C0q and independent ETF OOS.

## Data

- QQQ main: stress_robustness_v1_daily.csv; rates stress_robustness_v1_dtb3.csv.
- OOS ETFs: data/historical/range_vol_v2_*.csv (Yahoo chart API, adjclose + OHLC).
- Financing rates for ETFs: shared DTB3/TB3MS.
- Harness reference: research/ewma_band_v1/E2_INTEGRATION_DEVIATION.json.

## Controls

- C0: production E2, vol20 on synthetic QLD* (basis A).
- C0q: production E2, 2 × QQQ close-to-close vol20, same 20-session window and
  annualization as range estimators (basis B, no range info).

## Candidates

- G1x: Yang-Zhang 20-session vol ×2.
- G3x: Parkinson 20-session vol × expanding pre-date ratio of close-to-close to
  Parkinson vol (exactly as range_vol_v1).

## Plateau

Yang-Zhang and Parkinson-scaled at windows 10, 15, 30, 40 (report and plateau
gate). A candidate passes the plateau gate only if both its 15- and 30-session
neighbours beat C0 on main-window base m=2 CAGR.

## OOS data-quality rule (fixed before download)

A market/window passes only if at most 5% of sessions have `high==low` or an
invalid open (`open` missing, nonpositive, or outside `[low, high]`). Plain
`open == prior close` is not treated as a placeholder when the open field is
present, because it occurs routinely in genuine data. This corrects the v1
single-index `oil open == prior close` false-positive rule.

## Mechanics

Unchanged from v1: synthetic daily-reset 2x, decide at close, fill next open,
0.98 cap, 4 bps × m per unit, 0.5 pp resize band, idle cash zero. Main window
2000-03-09 to 2026-09-29. Signal production E2 on own adjusted close.

## Gates

- v1 G1–G6 and G9 vs BOTH C0 and C0q (must pass against both controls).
- G7': independent ETF OOS (quality-passing instruments): median Δ CAGR
  (candidate − C0, calibrated, m=2) ≥ 0; share Δ > 0 ≥ 60%; Δ max DD ≤ +3 pp
  in ≥ 80%. Repeat vs C0q.
- G8': pooled block-20 bootstrap over the new instruments (2,000 paths each,
  seed 20261011) median Δ ≥ 0. Repeat vs C0q.
- Plateau gate as above.

## Selection

Among candidates passing all gates, pick higher G8' median Δ vs C0; tie within
0.02 pp prefer G1x (no fitted scaling). If none pass, production E2 unchanged.
