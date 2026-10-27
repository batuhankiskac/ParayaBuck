# vol_regime_leverage_v1: volatility-regime exposure versus K after Turkish tax (audit item 50)

Branch `research/vol_regime_leverage_v1`. Pre-registration `research/vol_regime_leverage_v1/PREREGISTRATION.md`
(committed 6abe8f51 before any arm was simulated), driver `research/vol_regime_leverage_v1/screen.py`,
percentile kernel and arm weights `regime.py`, outputs `RESULTS.json` and `SUMMARY.txt`. Two runs gave
byte-identical RESULTS.json (sha256 `81dfd9e1cf06d703140741e9decdc4d102fef66646d13cc3e936314b9c01701c`).
Wall clock of one run: 8.2 s.

## Verdict

**No regime arm beats K after tax. No arm passes the gates; K stays in production.**

1. **All four arms lose to K after tax in the 2000 and 2010 windows and in the 2017 window.** The best arm, V3
   (K's weight times a regime multiplier), is -1.2 / -2.1 / -3.4 pp below K after tax in the three windows. Its
   bootstrap and drawdown results are the only ones that are close to K (G2, G5, G9 and A3 pass).
2. **The regime variable reduces exposure at the wrong times.** Q is high when vol is high, and vol is high
   when the trend recovers, so the arms cut QLD when K keeps it. The arms with a replacement target (V1, V2, V4)
   also run a lower average exposure, which is a second, separate cost.
3. **The drawdown gain is real but small relative to the return loss.** V2 cuts max DD to 32.7 % (K 46.7 %), but
   it pays 6 pp of pre-tax CAGR and 4.5 pp after tax (2000 window). No arm reaches a DSR of 0.95; the best
   pre-tax DSR is V3 at 0.73 (K 0.71), and after tax the best is K at 0.42.
4. **Consistent with vol_shape_v1.** Changing the vol-target shape (p = 1.5 in-sample win that failed OOS) and now a
   regime multiplier both fail to add value on top of K's 0.35 / vol20 target. The K target already captures the
   useful part of the vol signal.

Not a production change. The study is a rejection, not a candidate.

## Arms (fixed before running)

- Control **K** (E2 + R2 early leg, A1u band, H2 deferral, 0.5 pp band) and reference **R2** (C0 band).
- Regime arms, all with K's execution layer (fill rule `K`). Q = percentile of QQQ vol20 within its history
  (own-window percentile in [0, 1]); L = 2 - 1.5 Q (QQQ-equivalent exposure); QLD weight = factor x L / 2, capped 0.98.
  - **V1:** trailing 252-session Q, replaces K's vol target.
  - **V2:** expanding Q (history from ^IXIC vol20 before QQQ's first session, 1999-03-10, then QQQ vol20), replaces K's vol target.
  - **V3:** K's weight x min(1.2, L / 1.25), trailing 252-session Q. The multiplier sits on top of K's target.
  - **V4:** V1 with Q updated only at month-end closes (held through the month).
- Warm-up (Q undefined) uses K's weight in every arm.

## Results (QQQ main window 2000-03-09 to 2026-09-29, m = 2; after tax = $100k liquidated, progressive)

| arm | pre CAGR base / cal | max DD base | sells / yr | after tax 2000 / 2010 / 2017 | Delta vs K (2000 / 2010 / 2017, pp) | B1 median Delta (share > 0) | B2 median Delta (share > 0) | pre / post-2020 Delta (pp) | DSR pre / after tax (N = 369) |
|---|---|---|---|---|---|---|---|---|---|
| K (control) | 15.76 / 15.18 % | 46.7 % | 10.2 | 10.75 / 15.24 / 23.97 % | 0 | 0 | 0 | 0 / 0 | 0.71 / 0.42 |
| R2 (reference) | 15.48 / 14.90 % | 46.8 % | 29.6 | 10.16 / 14.71 / 22.89 % | -0.59 / -0.53 / -1.09 | -0.29 (0.25) | -0.13 (0.36) | -0.32 / -1.35 | 0.70 / 0.39 |
| V1 | 11.56 / 11.10 % | 58.0 % | 28.6 | 7.05 / 10.72 / 16.49 % | -3.71 / -4.52 / -7.48 | -0.84 (0.37) | -0.60 (0.40) | -2.86 / -6.18 | 0.57 / 0.23 |
| V2 | 9.53 / 9.19 % | 32.7 % | 17.8 | 6.28 / 9.37 / 14.19 % | -4.47 / -5.86 / -9.78 | -0.51 (0.41) | -0.42 (0.43) | -2.92 / -9.23 | 0.64 / 0.30 |
| V3 | 14.97 / 14.43 % | 47.4 % | 19.4 | 9.56 / 13.12 / 20.60 % | -1.20 / -2.11 / -3.37 | +0.11 (0.52) | +0.43 (0.57) | -0.63 / -2.82 | 0.73 / 0.36 |
| V4 | 11.39 / 10.95 % | 58.0 % | 9.0 | 7.81 / 12.06 / 19.50 % | -2.95 / -3.17 / -4.47 | -1.25 (0.32) | -1.34 (0.32) | -2.78 / -3.31 | 0.54 / 0.28 |

Pre-tax sub-period CAGR (base, m = 2): K P1 0.3 %, P2 14.3 %, P3 33.2 %; V3 0.3 %, 14.1 %, 30.8 %.
Bootstrap pre-tax median Delta (base, 5,000 ten-year paths, block 20): V3 +0.53 pp, V1 -0.99, V2 -0.76, V4 -2.12.
K's bootstrap median after-tax CAGR (B1 / B2): 3.86 % / 4.04 %.

Exposure, averaged over the days when K's vote is on (QQQ-equivalent = 2 x QLD weight): K 1.74, V1 1.34, V2 1.06,
V3 1.60, V4 1.35. Averaged over all days: K 1.30, V1 1.00, V2 0.79, V3 1.19, V4 1.01.

## Gates

| arm | G1 | G2 | G3 | G4 | G5 | G6 | G7 | G8 (reduced) | G9 | A1 | A2 | A3 | A4 | candidate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| V1 | fail | fail | fail | fail | fail | fail | fail | fail | pass | fail | fail | fail | fail | no |
| V2 | fail | pass | fail | fail | fail | fail | fail | fail | pass | fail | fail | fail | fail | no |
| V3 | fail | pass | fail | fail | pass | fail | fail | fail | pass | fail | fail | **pass** | fail | no |
| V4 | fail | fail | fail | fail | fail | fail | fail | fail | pass | fail | fail | fail | fail | no |

G1 = CAGR or Calmar gate (both financings); G2 = max DD; G3 = 2017-end CAGR; G4 = sub-periods (base); G5 = pre-tax
bootstrap; G6 = G1 at m = 1; G7 = six OOS windows (calibrated); G8 = pooled OOS bootstrap (reduced, see below); G9 = turnover;
A1 = after tax above K in all three windows; A2 = B1 median >= 0 and share >= 55 %; A3 = B2 likewise; A4 = pre- and post-2020 deltas both positive.

**The gate that fails for V3 in the main window is G1**: its CAGR is 0.8 pp below K and its Calmar (0.316) is below
1.1 x K's (0.338). **A1 fails** because the after-tax gap is negative in all three windows.

OOS (G7, calibrated, m = 2, six windows, Delta CAGR pp for V3): GSPC 1957-99 -1.70, M1 -2.51, M2 -4.85, M3 -1.38,
M4 -1.72, M5 -0.61. Every regime arm loses in the OOS windows, so G7 fails. Table: `RESULTS.json`, key `oos_calibrated_m2`.

## Deflated Sharpe (N = 369 = 365 + 4 arms)

DSR of the pre-tax base daily series: K 0.71, V3 0.73, V2 0.64, R2 0.70, V1 0.57, V4 0.54. The bar is 0.95 (gates_v1); no arm
reaches it. The sanity check reproduces gates_v1's K value: DSR at N = 365 = 0.7111 (gates_v1: 0.711).
After-tax DSR (progressive daily NAV) is lower for all arms (K 0.42, V3 0.36).

## Deviations and amendments

- **G8 is reduced.** The protocol's pooled ten-year path bootstrap over M1 to M5 is replaced by a block-20 circular
  bootstrap of each window's daily Delta (arm - K), 2,000 replications, statistic = mean over M1-M5 of 252 x mean daily
  Delta. This is pre-registered in `PREREGISTRATION.md` before any arm ran. No other gate or parameter changed.
- No amendments to the pre-registration.

## Caveats

- **The exposure levels differ across arms.** V1, V2 and V4 carry materially less average QLD exposure than K, so their
  losses mix a shape effect with a leverage effect. V3 is the shape test, but its clip at 1.2 and the 0.98 cap pull its
  average multiplier below 1 (V3 averages 1.60 against K's 1.74 on vote-on days).
- **V2 depends on the pre-QQQ prefix.** The expanding Q averages 0.69 over the main window, against 0.47 for the trailing Q, so
  V2 spends more time at high Q and de-levers more than V1. I did not decompose that gap into prefix and QQQ-era effects.
  Read V2 as "expanding percentile against a 1971-1999 ^IXIC proxy", not as a clean test.
- **Bootstrap paths have no pre-history.** Q252 and Qexp are computed inside each synthetic ten-year path. Qexp is
  therefore warm-up-limited for the first ~270 sessions of each path, and the warm-up fallback applies there.
- **One path.** The after-tax windows overlap heavily (2000 and 2010 are the same history). The bootstrap and OOS windows
  are the robustness checks; they are negative for V1, V2 and V4 in most windows and for V3 in every window.
- **Verification.** The control harness reproduces K and R2 pre-tax and after-tax anchors to 0.01 pp, the K weights equal
  `fe_boot.chunk_legs` on the same draw, the numba rank kernel equals a brute-force reference, and
  `research.fast_engine.parity --quick` passes. The engine's parity check needed the band_h2 instrument CSVs
  (`range_vol_*`), restored from `archive/research/band-h2-combo-v1-2026-10`.
