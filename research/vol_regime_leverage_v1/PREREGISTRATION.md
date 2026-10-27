# PREREGISTRATION: vol_regime_leverage_v1 (audit item 50)

Worker `vol_regime_leverage_v1`, branch `research/vol_regime_leverage_v1`, dated 2026-10-27.
Code: `research/vol_regime_leverage_v1/screen.py` (driver) and `regime.py` (percentile kernel and arm
weights). Engine: `research/fast_engine` (every simulation through `fe_sim.simulate`; bootstrap through
`fe_boot._boot_chunk`). Harness: `research/gates_v2` (K control), gate and DSR code `research/gates_v1/stats.py`.

## Question

Does scaling QLD exposure by the volatility regime beat K after Turkish tax?

Regime variable: Q_t = percentile of today's QQQ 20-session volatility (vol20, annualised, as in
`fe_signals.vol_n` on the QQQ close) within its trailing history, known at the close of t.
Exposure: L_t = 2 - 1.5 Q_t, the QQQ-equivalent exposure (L = 2 is full 2x, L = 0.5 is a quarter).
QLD weight = L_t / 2 when K's vote is on, so the weight runs from 0.25 (Q = 1) to 0.98 (Q = 0).

## Control and common mechanics (unchanged on every arm)

- **K** = production rule: E2 vote + R2 early re-entry (`fe_signals.r2_state`) with the K fill rule
  (A1u gain band + H2 December deferral + 0.5 pp band). **R2** is the reference row (same weights, C0 rule).
- The K execution layer (fill rule `K`) is applied to every V arm. Only the target weight changes.
- `factor_t` in {1, 0.5, 0} is r2_state's factor (E2 on / early re-entry / off). Decide at the close,
  fill at the next open, cost m = 2 (base) and the calibrated financing spread (+0.75 %), as in K.
- Warm-up: where Q is undefined (fewer than 252 valid vol20 values in the history), every V arm uses K's own
  weight w_K = min(1, 0.35 / vol20_QLD) x factor (the K target).

## Arms (every parameter fixed here)

| arm | target weight (QLD, before the 0.98 cap) | Q history |
|---|---|---|
| **V1** | factor x L/2 with L = 2 - 1.5 Q252 (replaces K's vol target) | trailing 252 sessions, window includes t |
| **V2** | factor x L/2 with L = 2 - 1.5 Qexp (replaces K's vol target) | expanding, all finite history up to t (min. 252 values) |
| **V3** | w_K x min(1.2, L/1.25) with L = 2 - 1.5 Q252 (V1 multiplier on top of K's target) | trailing 252 |
| **V4** | factor x L/2 with L = 2 - 1.5 Qm (replaces K's vol target) | Qm = Q252 at the most recent month-end close <= t |

Cap: min(0.98, raw). Only the weights differ between arms; the fill rule is `K` for all four.
Five arms maximum: this study has four (V1-V4) plus two controls (K, R2).

**Definitions.**
- Q252_t = (number of the 252 finite vol20 values in sessions t-251..t that are <= vol20_t) / 252.
- Qexp_t = (number of finite vol20 values from the start of history through t that are <= vol20_t) / count, if count >= 252.
- Main window history for Qexp: ^IXIC vol20 (the `ewma_band_v1_index.csv` M1 series) for sessions before
  QQQ's first session (1999-03-10), then QQQ vol20. This is the only use of pre-QQQ data (the task asked
  for 1990-era history "where available"; the file starts 1971). ^IXIC is a 1x index, so no scaling is needed at the splice.
- OOS windows (G7/G8): each window's own vol20 history, expanding from that window's first session for Qexp.
- Bootstrap paths (G5, B1, B2): Q252 and Qexp are computed inside each synthetic ten-year path (no pre-history).
  Qexp is therefore warm-up-limited in the path's first ~270 sessions; the fallback applies there.
- Qm uses the calendar months of the main window; the bootstrap paths use the fixed 2015-09..2026-09 calendar
  that `fe_boot.BootWindow` already defines for each path index.

## Data

Inputs (restored from tags, SHA-256 in `DATA_MANIFEST.json`): `data/historical/stress_robustness_v1_daily.csv`
(QQQ/SPY/QLD bars, 1999-03-10 onward), `data/historical/ewma_band_v1_index.csv` (^IXIC from 1971-02-05
for V2's prefix and the M1 OOS window; M2-M5), `ewma_band_v1_gspc.csv` (GSPC 1957-1999 OOS),
`stress_robustness_v1_dtb3.csv` / `ewma_band_v1_tb3ms.csv` (rates), and the USD-TRY and YI-UFE series.
Main window: 2000-03-09 to 2026-09-29, 6,679 daily returns. Turkish tax by the progressive engine
(2026 tariff, YI-UFE indexation, instalments), $100k liquidated.

## Gates (pre-registered; all must pass for an arm to be a candidate)

Pre-tax, QQQ main window, m = 2 unless stated. "Both" = base and calibrated financing. Gates G1-G3, G5-G6 and G9
are ewma_band_v1's G1-G9 as applied in fast_reentry_v1 (unchanged), with K as C0.

- **G1 (both):** CAGR >= K, or Calmar >= 1.1 x K's Calmar with CAGR >= K - 0.5 pp.
- **G2 (both):** max DD <= K + 1 pp.
- **G3 (both):** 2017-end CAGR >= K - 1 pp.
- **G4 (base):** CAGR above K in >= 2 of 3 sub-periods (P1 2000-2008, P2 2009-2016, P3 2017-end).
- **G5 (both):** bootstrap median (arm - K) >= 0, block 20, seed 20261011, 5,000 ten-year paths (`fe_boot` draws, identical across arms).
- **G6 (both):** G1 at m = 1.
- **G7 (calibrated, m = 2):** six OOS windows (^GSPC 1957-1999 and M1-M5). Median Delta CAGR >= 0; Delta CAGR >= -0.5 pp in >= 5 of 6;
  Delta DD <= +3 pp in >= 5 of 6 and never > +8 pp.
- **G8 (reduced, pre-registered here before any arm ran):** the protocol's G8 is a pooled ten-year path bootstrap over M1-M5.
  Here: each window's daily Delta (arm - K) series (pre-tax, calibrated, m = 2) is resampled with circular blocks of
  20 sessions, 2,000 replications, seed 20261011. Statistic per replication = mean over M1-M5 of 252 x mean daily Delta.
  Pass iff its median >= 0. This is a deviation from the full path bootstrap and is labelled as such in the report.
- **G9:** turnover per year <= 2 x K's (base, m = 2).

After tax (progressive, $100k liquidated, base financing):

- **A1:** after-tax CAGR above K in all three windows (2000-03-09 to 2026-09-29; 2010-01-01 to end; 2017-01-03 to end).
- **A2 (B1-recent):** progressive-tax bootstrap, same 5,000-path draws restricted to the first 1,000 paths, 2015-09 to 2026-09 calendar:
  median Delta >= 0 and share Delta > 0 >= 55 %.
- **A3 (B2, flat 20 %):** same paths, flat 20 % tax on the annual net USD gain: median Delta >= 0 and share >= 55 %.
- **A4:** pre-2020 (2000-03-09 to 2019-12-31) and post-2020 (2020-01-01 to end) after-tax Delta vs K both > 0. Each half is its own $100k account.

Reported, not gated: sells per year, DSR, the calibrated pre-tax CAGR and the OOS table.

**DSR.** Bailey and Lopez de Prado deflated Sharpe (`research/gates_v1/stats.py`) on the pre-tax daily base m = 2
returns of each arm, with N = 365 + 4 = 369 trials (the arm count is four), variance floor 1/(T-1). Also the
after-tax (progressive) daily series, same N. Bar 0.95 (gates_v1), reported only. Sanity check: K at N = 365 must
reproduce gates_v1's 0.711 (no correlation adjustment).

**Selection.** An arm replaces K only if every G and A gate passes. Otherwise K stays and the report says so.
No production configuration changes in any case. The study is a candidate test, not a promotion.

## Controls and anchors (harness, before any arm is run)

`python -m research.vol_regime_leverage_v1.screen --harness` must reproduce band_h2_combo_v1's K and R2 numbers
to 0.01 pp (pre-tax 15.765 % base / 15.18 % calibrated, max DD 46.67 %; after tax 10.75 / 15.24 / 23.97 %;
R2 15.48 %, after tax 10.16 / 14.71 / 22.89 %). It also checks: the K weights equal `fe_boot.chunk_legs` on the same draw;
the numba rank kernel equals the brute-force reference; `research.fast_engine.parity --quick` passes.

## Amendments

None yet. Any bug fix after results is recorded here with the reason, and the gates stay as written.

## Known limits (stated before running)

- V1 and V4 change average leverage as well as the shape (mean L = 1.25, against K's average near 1.6-1.75 QQQ-equivalent).
  V3 is the shape-only test: it keeps K's mean and multiplies by L/1.25. Read V1/V2/V4 with that confound in mind.
- V2 uses ^IXIC history before 1999; the pre-QQQ part is a proxy.
- Each arm's after-tax result is one path (2000 and 2010 windows overlap heavily). The bootstrap is the robustness check.
