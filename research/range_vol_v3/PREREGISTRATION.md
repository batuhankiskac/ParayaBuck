# range_vol_v3 — pre-registration (2026-10-14)

INDEPENDENT REPLICATION + production-feasibility study. Rebuilt from
specification; no code copied from range_vol_v1/v2 screen.py.

## Question

Does range-based volatility sizing (Parkinson, Yang-Zhang, production-feasible
variants of the expanding-scaled Parkinson estimator) beat production E2 on
QQQ, and are the gains robust across 18 independent instruments and bootstrap
resampling?

## Arms (all parameters fixed)

- C0: production E2, vol20 on synthetic QLD* (control).
- YZ20: production E2, standard Yang-Zhang 20-session vol x2 (k = 0.34/(1.34+(n+1)/(n-1))).
- G3x: Parkinson 20-session vol x expanding ratio R (rebuild of claimed winner).
- F1: G3x-style with R rolling over last 252 sessions (production-feasible).
- F2: G3x-style with R rolling over last 504 sessions.
- F3: G3x-style with R fixed to R* estimated on QQQ 1999-03..2009-12 (frozen).
- F4: plain Parkinson 20-session vol x2 (R=1).

Target weight min(1, 0.35/vol), cap 0.98, 0.5 pp resize band, cost 4 bps x m,
m=2 primary, m=1 gated; calibrated financing adds 0.75% spread.

## Data

- QQQ main: stress_robustness_v1_{daily,dtb3}.csv.
- OOS ETFs: range_vol_v2_*.csv (18 quality-passing instruments; EWC excluded by data-quality rule).
- Shared rates: ewma_band_v1_{gspc,index,tb3ms}, stress_robustness_v1_dtb3.
- Harness ref: research/ewma_band_v1/E2_INTEGRATION_DEVIATION.json.

## Mechanics

Unchanged. Main window 2000-03-09 to 2026-09-29. Signal production E2 on QQQ QQQ price close.

## Gates (F1-F3 only)

- Main window: v1 G1-G6 and G9 vs C0.
- 18-instrument ETF OOS: G7' (median Δ CAGR >=0, >=60% positive, DD Δ <= +3 pp in >=80%),
  G8' pooled block-20 bootstrap median Δ >=0.
- Selection: passing F-arm with highest 18-ETF pooled bootstrap median Δ; prefer
  simpler (F3 > F1 > F2) if within 0.03 pp.

## Selection rule

Among passing F-arms, pick highest G8' median Δ; tie within 0.03 pp prefer simpler.

## Amendments

(none yet)
