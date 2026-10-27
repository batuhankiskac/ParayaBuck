# PREREGISTRATION: slow_vol_blend_v1 (audit item 51)

Worker `slow_vol_blend_v1`, branch `research/slow_vol_blend_v1`, dated 2026-10-27.
Code: `research/slow_vol_blend_v1/screen.py` (driver) and `slowvol.py` (EW-sigma kernel, slow leg, arm weights,
S3 calibration). Engine: `research/fast_engine` (every simulation through `fe_sim.simulate`; bootstrap through
`fe_boot._boot_chunk`). Gate and DSR code: `research/gates_v1/stats.py`. Harness origin: `research/vol_regime_leverage_v1`.

## Question

Does a slower volatility estimate in K's vol target beat K after Turkish tax, at similar risk?

Hypothesis: about 91 % of R2's realised Turkish taxable gain came from vol-target trims. A slower sigma moves less
from day to day, so it should trim less often while keeping the same average risk. The test is the after-tax
result, not the trim count.

## Control and reference (unchanged)

- **K** (control) = production rule: E2 vote + R2 early re-entry (`fe_signals.r2_state`), sized as
  `on x factor x min(1, 0.35 / vol20)`, cap 0.98, where vol20 = `fe_signals.vol_n` of the QLD synthetic close, with
  the K fill rule (A1u gain band, H2 December deferral, 0.5 pp band).
- **R2** (reference row) = same weights, C0 fill rule.
- Mechanics as in `vol_regime_leverage_v1`: decide at the close, fill at the next open, cost m = 2 (base) and
  the calibrated financing spread (+0.75 %). Idle cash earns zero. Main window 2000-03-09 to 2026-09-29.

## Slow volatility estimate (definitions, fixed)

Let c_t be the synthetic QLD close (base or calibrated financing, as in K) and r_t = c_t / c_{t-1} - 1 (the same
return definition as `vol_n`).

- **Fast leg, EW-sigma (span 35).** lambda = 1 - 2/(35 + 1) = 34/36. Bias-corrected EWMA of squared returns:
  v_t = sum_{i=0}^{t-1} lambda^i r_{t-i}^2 / sum_{i=0}^{t-1} lambda^i, sigma^EW_t = sqrt(252 v_t). Defined from
  session 1 on.
- **Daily sigma series.** sigma_d := sigma^EW_d, one value per session. The slow leg is therefore the 10-year average
  level of this same estimator. (This is the reading of "10-year average of daily sigma" fixed here.)
- **Slow leg.** sigma^LR_t = mean of sigma_d over the trailing 2,520 sessions d in [max(1, t-2519), t]. Expanding:
  until 2,520 sessions exist, the mean is over all sessions since the series starts (first QQQ session 1999-03-10
  in the main window; path start in each bootstrap path; window start in each OOS window). No pre-QQQ data is used.
- **Warm-up.** For t < 252 (fewer than 252 sessions of history), every S arm uses K's own weight (vol20 sizing).

## Arms (every parameter fixed here)

Weight = factor_t x min(1, T / sigma_t), cap min(0.98, .). `factor` in {1, 0.5, 0} is `r2_state`'s factor
(E2 on = 1, early re-entry = 0.5, off = 0), i.e. K's vote. Only the target sigma and T change; the K fill rule is
used for every S arm.

| arm | sigma_t | T |
|---|---|---|
| **S1** | 0.7 x sigma^EW + 0.3 x sigma^LR (replaces vol20) | 0.35 |
| **S2** | 0.5 x sigma^EW + 0.5 x sigma^LR (50/50 blend) | 0.35 |
| **S3** | S1's sigma (re-levelled) | c3 = 0.420173 (see the calibration below) |

Three new arms (S1-S3), within the limit of four, plus the controls K and R2.

**S3 calibration (fixed by a rule computed on data before 2010).** c3 is the value of T for which the mean
QQQ-equivalent exposure of S3 (2 x QLD weight) on vote-on days (factor > 0, i.e. K's vote on) equals K's mean over the
same days, using sessions 2000-03-09 to 2009-12-31 only. Bisection on [0.001, 10], 200 iterations; deterministic.
The inputs (vote, factor, K's weight, S1's sigma) are causal, so no later data enters the calibration. The harness
computed it on 2026-10-27, before any non-control arm was simulated:

- K mean exposure on vote-on pre-2010 days: 1.602524 (1,491 days). S3 at c3: the same.
- **c3 = 0.42017314752592927**, fixed. The run recomputes it and must reproduce this value (RESULTS.json `meta.s3_calibration`).

No other arm used any exposure target.

## Data

Inputs (restored from tag `archive/research/vol-regime-leverage-v1-2026-10`, SHA-256 in `DATA_MANIFEST.json`):
`data/historical/stress_robustness_v1_daily.csv` (QQQ/SPY/QLD bars, 1999-03-10 onward),
`ewma_band_v1_index.csv` (^IXIC and the M1-M5 OOS windows), `ewma_band_v1_gspc.csv` (^GSPC 1957-1999 OOS),
`stress_robustness_v1_dtb3.csv` and `ewma_band_v1_tb3ms.csv` (rates), and the USD-TRY and YI-UFE series.
No new market data is downloaded. Turkish tax by the progressive engine (2026 tariff, YI-UFE indexation,
instalments), $100k liquidated.

## Gates (pre-registered; all must pass for an arm to be a candidate)

Same definitions as `vol_regime_leverage_v1` (which uses ewma_band_v1's G1-G9 with K as C0).

- **G1 (both financings):** CAGR >= K, or Calmar >= 1.1 x K's Calmar with CAGR >= K - 0.5 pp.
- **G2 (both):** max DD <= K + 1 pp.
- **G3 (both):** 2017-end CAGR >= K - 1 pp.
- **G4 (base):** CAGR above K in >= 2 of 3 sub-periods (P1 2000-2008, P2 2009-2016, P3 2017-end).
- **G5 (both):** pre-tax bootstrap median (arm - K) >= 0, block 20, seed 20261011, 5,000 ten-year paths (`fe_boot` draws, identical across arms).
- **G6 (both):** G1 at m = 1.
- **G7 (calibrated, m = 2):** six OOS windows (^GSPC 1957-1999 and M1-M5). Median Delta CAGR >= 0; Delta CAGR >= -0.5 pp
  in >= 5 of 6; Delta DD <= +3 pp in >= 5 of 6 and never > +8 pp.
- **G8 (reduced, as pre-registered in vol_regime_leverage_v1):** each of M1-M5's daily Delta (arm - K, pre-tax,
  calibrated, m = 2) is resampled in circular blocks of 20 sessions, 2,000 replications, seed 20261011; statistic per
  replication = mean over M1-M5 of 252 x mean daily Delta. Pass iff its median >= 0. Labelled as a deviation from the
  pooled ten-year path bootstrap.
- **G9:** annual turnover <= 2 x K's (base, m = 2).

After tax (progressive, $100k liquidated, base financing):

- **A1:** after-tax CAGR above K in all three windows (2000-03-09 to 2026-09-29; 2010-01-01 to end; 2017-01-03 to end).
- **A2 (B1-recent):** progressive-tax bootstrap on the same draws, first 1,000 paths, 2015-09 to 2026-09 calendar:
  median Delta >= 0 and share Delta > 0 >= 55 %.
- **A3 (B2, flat 20 % on the annual net USD gain):** same paths: median Delta >= 0 and share >= 55 %.
- **A4:** pre-2020 (2000-03-09 to 2019-12-31) and post-2020 (2020-01-01 to end) after-tax Delta vs K both > 0. Each half is its own $100k account.

Reported, not gated: sells per year, mean QQQ-equivalent exposure on vote-on days (all days, and pre-2010), pre-tax
pre-2020 / post-2020 CAGR deltas, DSR, the calibrated pre-tax CAGR and the OOS table.

**DSR.** Bailey and Lopez de Prado deflated Sharpe (`research/gates_v1/stats.py`) on the pre-tax daily base m = 2
returns of each arm, and the after-tax (progressive) daily series, with **N = 369 + 3 = 372** trials (369 already
counts all earlier trials; this study adds its three arms). Bar 0.95, reported only. Sanity check: K at N = 365
reproduces gates_v1's 0.711 (the harness checks this).

**Selection.** An arm replaces K only if every G and A gate passes. Otherwise K stays and the report says so.
No production configuration changes in any case. The study is a candidate test, not a promotion.

## Controls and anchors (harness, run before any non-control arm)

`python -m research.slow_vol_blend_v1.screen --harness` must reproduce band_h2_combo_v1's K and R2 numbers to 0.01 pp
(pre-tax 15.765 % base / 15.18 % calibrated, max DD 46.67 %; after tax 10.75 / 15.24 / 23.97 %; R2 15.48 %,
after tax 10.16 / 14.71 / 22.89 %). It also checks: K's weights equal `fe_boot.chunk_legs` on the same draw; the
sizing helper with vol20 and 0.35 reproduces K's sizing; the numba EWMA kernel equals the explicit normalised-weight
definition and the recursion reference; the 2,520-session slow leg equals an explicit trailing-window mean; K's DSR at
N = 365 is 0.711; `research.fast_engine.parity --quick` passes. Result on 2026-10-27: HARNESS PASS.

## Amendments

None. Any bug fix found after results is recorded here with the reason; the gates and arms stay as written.

## Known limits (stated before running)

- **The 10-year leg is expanding in the main window.** QQQ data starts 1999-03-10, so the slow leg averages about one
  year of history at 2000-03 and reaches 2,520 sessions only around 2009. The pre-QQQ ^IXIC history is not spliced in
  (unlike vol_regime's V2). Read 2000-2009 as "slow leg from a short history", not as a full 10-year average.
- **Bootstrap paths have no pre-history.** Each 2,773-session path starts its slow leg at its own first session, so
  within a path the slow leg is an expanding mean. The warm-up fallback applies to the first 252 sessions.
- **The sigma interpretation.** The 10-year term averages the EW-sigma series itself (one vol value per session). An
  alternative reading (absolute-return sigma) is not tested.
- **S1 and S2 change average leverage as well as smoothness.** S3 is the level-matched test (same mean exposure on
  pre-2010 vote-on days). Read S1/S2 with that confound in mind.
- **One path.** The after-tax windows overlap heavily (2000 and 2010 are the same history). The bootstraps and OOS
  windows are the robustness checks.
