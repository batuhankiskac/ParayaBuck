# vol_window_v1 — pre-registration (2026-10-14)

Written and committed with `screen.py`, `DATA_MANIFEST.json` before any non-control arm is simulated on real data.
Only `screen.py --harness` (C0 = production E2) has run on real data before this commit; the full run path was smoke-tested on synthetic random prices.

## Seen before writing

- Production E2 (`e2_integration_v1`): 14.67% CAGR / 49.6% max DD (base m=2), 14.10% calibrated, 14.95% (m=1). Screen 2000-03-09 -> 2026-09-29.
- `ewma_band_v1`: RiskMetrics EWMA vol on E2 failed G3/G5.
- `stress_robustness_v2` / `trend_robustness_v2`: vol20 on QLD* with vt = min(1, 0.35/vol20).
- Production sizes by vol20 (20-session realized vol on the instrument, QLD*).

## Question

On top of production E2 (signal and engine unchanged), does changing the volatility window/estimator improve long-run return versus S&P 500 buy-and-hold without materially worse drawdown?

## Data

- Main: `data/historical/stress_robustness_v1_{daily,dtb3}.csv`.
- OOS: `data/historical/ewma_band_v1_{gspc,index,tb3ms}.csv`.
- Harness reference: `research/vol_window_v1/E2_INTEGRATION_DEVIATION.json`.
- SHA-256 in `DATA_MANIFEST.json`.

## Mechanics (unchanged)

Same as production E2: synthetic QLD* = daily-reset 2x on QQQ total return; base = DTB3, calibrated = DTB3 + 0.75%; decide at close, fill next open; cap 0.98; cost 4 bps x m per unit traded (m=2 primary, m=1 gated via G6); idle cash zero. Main window 2000-03-09 -> 2026-09-29. Resize band always 0.5 pp (production `execution.rebalance_band_pct`).

## Signal (all arms)

Production E2 1.1.0 on QQQ split-adjusted price close: at least 2 of SMA200, 252-session price return > 0, close > 126-session max/min midpoint.

## Volatility (the only thing that changes)

All estimators use the instrument's (QLD*) daily simple returns `r_t = close_t/close_{t-1} - 1`, annualized by sqrt(252), with NaN for the first n sessions. `vt(v) = min(1, 0.35/v)`.

- **vol20 (C0):** annualized sample std, ddof=1, over 20 returns (production basis).
- **vol10 (V10):** same, n = 10.
- **vol40 (V40):** same, n = 40.
- **vol60 (V60):** same, n = 60.
- **volmax (VMAX):** `max(vol20, vol60)` elementwise; fast to cut, slow to re-lever.
- **semidev20 (SEMI):** downside semi-deviation over 20 returns scaled by sqrt(2): `sqrt(2 * 252 / 20 * sum_{i in window} min(r_i, 0)^2)`. Only negative returns count; positives contribute zero.

## Arms

| Arm | Vol estimator | Band |
|---|---|---|
| C0 | vol20 | 0.5 pp (production) |
| V10 | vol10 | 0.5 pp |
| V40 | vol40 | 0.5 pp |
| V60 | vol60 | 0.5 pp |
| VMAX | volmax | 0.5 pp |
| SEMI | semidev20 | 0.5 pp |

Target = E2 * vt(vol), capped at 0.98.

## Gates

Reuse ewma_band_v1 G1-G8 unchanged; G9 turnover <= 2 x C0 (all arms keep the 0.5 pp band, so vol-only changes may still drop it).

- G1 (m=2, both financings): CAGR >= C0 or (Calmar >= 1.1 x C0 and CAGR >= C0 - 0.5 pp).
- G2 (m=2, both): max DD <= C0 + 1 pp.
- G3 (m=2, both): 2017-01-03 -> end CAGR >= C0 - 1 pp.
- G4 (m=2, base): CAGR > C0 in >= 2 of 3 sub-periods (P1 2000-03-09 -> 2008-12-31, P2 2009 -> 2016, P3 2017 -> end).
- G5 (both): block-20 bootstrap median (arm - C0) CAGR >= 0.
- G6 (m=1, both): G1 at 1x costs.
- G7 (OOS, six windows, calibrated, m=2): median (arm - C0) CAGR >= 0 and >= -0.5 pp in >= 5/6 and max DD <= C0 + 3 pp in >= 5/6, never > C0 + 8 pp.
- G8 (OOS pooled bootstrap, M1-M5): median (arm - C0) CAGR >= 0.
- G9 (turnover, m=2, base): turnover/yr <= 2 x C0's.

All six OOS windows usable (GSPC 1957-1999, M1-M5).

Selection: among candidates passing all gates, highest base m=2 CAGR (tie Calmar); else C0 unchanged.
PY
echo written