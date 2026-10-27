# slow_vol_blend_v1: a slower volatility estimate in K's vol target versus K after Turkish tax (audit item 51)

Branch `research/slow_vol_blend_v1`. Pre-registration `research/slow_vol_blend_v1/PREREGISTRATION.md`
(committed ab27ae8a, before any non-control arm was simulated). Driver `screen.py`, signal code `slowvol.py`,
manifest `DATA_MANIFEST.json`. Outputs `RESULTS.json` (sha256 `20ce934685f109224e4a28eb91098b36cad93579d0c24aa2b1ce2280d12e97e2`)
and `SUMMARY.txt`. Two runs gave byte-identical RESULTS.json. Wall clock of one run: 5.2 s and 5.8 s.

## Verdict

**No slow-vol arm beats K under the pre-registered gates. No arm passes; K stays in production.**

1. **S3 is the only arm that beats K after tax in all three windows** (+0.58 / +1.08 / +2.19 pp), and it sells 37 to 59 %
   less often in those windows (sells/yr 6.7 / 5.4 / 3.9 against K's 10.7 / 10.6 / 9.5). That is the effect the hypothesis
   predicted. It is not robust: it fails G2 (max DD +3.7 pp against a +1 pp limit), the pre-tax bootstrap G5
   (median −0.77 pp, above K on 34 % of paths), both after-tax bootstraps (A2, A3) and the pre-2020 after-tax test (A4,
   −0.18 pp).
2. **S1 and S2 do not beat K after tax.** Both lower max DD (43.8 % and 44.0 % against K's 46.7 %) and trade much less
   (sells/yr 7.9 and 6.7 against 10.2), but they pay 0.7 to 0.9 pp of pre-tax CAGR. After tax they lose in the 2000
   window (−0.23 and −0.27 pp), and their bootstraps are clearly negative (B1 −0.24 / −0.45 pp, pre-tax −0.66 / −1.10 pp).
3. **The trim-count hypothesis holds, the return hypothesis does not.** Smoothing sigma cuts pre-tax sells per year by
   22 to 40 % (S1 to S3). S3's after-tax gain is concentrated in the post-2020 half (+2.31 pp; pre-2020 -0.18 pp), and the
   bootstrap draws, which do not share the main window's history, do not reproduce it.
4. **Risk was not similar.** S1 and S2 reduce drawdown, but S3, the level-matched arm, raises max DD by 3.7 pp to 50.4 %.
   Its full-sample exposure is also above K's (see caveats).

Not a production change. The study is a rejection of the slow-sigma idea as a candidate, not a promotion of any arm.

## Arms (every parameter fixed before any non-control arm was simulated)

Weight = factor x min(1, T / sigma), cap 0.98, with K's vote and factor and K's fill rule (A1u band, H2 deferral,
0.5 pp band). Warm-up (t < 252 sessions): K's own weight.

- **Fast leg:** EW-sigma, span 35 (lambda = 34/36), bias-corrected EWMA of squared simple returns, annualised. Defined
  from session 1.
- **Slow leg:** mean of the same daily sigma series over the trailing 2,520 sessions, expanding until 2,520 exist. The
  main window starts at QQQ's first session (1999-03-10), so there is no pre-QQQ splice.
- **S1:** sigma = 0.7 EW + 0.3 LR, T = 0.35 (replaces vol20).
- **S2:** sigma = 0.5 EW + 0.5 LR, T = 0.35 (50/50 blend).
- **S3:** S1's sigma, T = c3 = 0.42017. c3 makes the mean QQQ-equivalent exposure on vote-on days equal to K's over
  2000-03-09 to 2009-12-31 (K 1.6025, 1,491 days). Bisection on pre-2010 data only. Fixed, not re-tuned.
- **Controls:** K (production) and R2 (reference, C0 fill rule).

## Results (QQQ main window 2000-03-09 to 2026-09-29, m = 2; after tax = $100k liquidated, progressive)

| arm | pre CAGR base / cal | max DD base | sells/yr pre-tax | after tax 2000 / 2010 / 2017 | Delta vs K (2000 / 2010 / 2017, pp) | B1 median Delta (share > 0) | B2 median Delta (share > 0) | pre-tax bootstrap median Delta (share > 0) | DSR pre / after (N = 372) |
|---|---|---|---|---|---|---|---|---|---|
| K (control) | 15.76 / 15.18 % | 46.7 % | 10.2 | 10.75 / 15.24 / 23.97 % | 0 | 0 | 0 | 0 | 0.709 / 0.422 |
| R2 (reference) | 15.48 / 14.90 % | 46.8 % | 29.6 | 10.16 / 14.71 / 22.89 % | -0.59 / -0.53 / -1.09 | -0.29 (25 %) | -0.13 (36 %) | +0.01 (51 %) | 0.699 / 0.385 |
| S1 | 15.04 / 14.49 % | 43.8 % | 7.9 | 10.52 / 15.39 / 24.22 % | -0.23 / +0.15 / +0.25 | -0.24 (42 %) | -0.40 (35 %) | -0.66 (28 %) | 0.686 / 0.421 |
| S2 | 14.88 / 14.33 % | 44.0 % | 6.7 | 10.49 / 15.65 / 25.01 % | -0.27 / +0.42 / +1.04 | -0.45 (37 %) | -0.69 (30 %) | -1.10 (23 %) | 0.686 / 0.429 |
| S3 | 15.87 / 15.27 % | 50.4 % | 6.1 | 11.34 / 16.31 / 26.16 % | **+0.58 / +1.08 / +2.19** | -0.43 (40 %) | -0.63 (37 %) | -0.77 (34 %) | 0.675 / 0.439 |

Sells per year in the after-tax windows (2000 / 2010 / 2017): K 10.7 / 10.6 / 9.5; R2 30.7 / 31.4 / 35.6; S1 8.4 / 8.1 / 6.4;
S2 7.1 / 7.2 / 5.3; S3 6.7 / 5.4 / 3.9. Max DD after tax, 2000 window: K 46.7 %, S1 45.7 %, S2 45.3 %, S3 50.4 %.

Sub-period pre-tax CAGR (base, m = 2; P1 2000-2008 / P2 2009-2016 / P3 2017-end): K 0.3 / 14.3 / 33.2 %; S1 -0.4 / 13.8 / 32.3 %;
S2 -0.9 / 12.9 / 33.2 %; S3 -0.7 / 14.7 / 34.3 %. Pre-tax CAGR with m = 1 (base): K 16.02 %, S1 15.27 %, S2 15.08 %, S3 16.11 %.

**Pre- and post-2020.** After-tax deltas (A4): S1 -0.43 / +0.18 pp; S2 -0.75 / +0.98 pp; S3 -0.18 / +2.31 pp. Pre-tax CAGR
deltas, not gated: S1 -0.52 / -1.24 pp; S2 -0.94 / -0.65 pp; S3 -0.09 / +0.92 pp.

**Exposure on vote-on days** (mean QQQ-equivalent, 2 x QLD weight): all days after the start, K / S1 / S2 / S3 = 1.75 / 1.70 / 1.65 / 1.82.
Pre-2010 (the calibration window): K 1.60, S1 1.40, S2 1.22, S3 1.60 (matched by construction). Averaged over all days: K 1.35, S1 1.31, S2 1.26, S3 1.39.

## Gates

| arm | G1 | G2 | G3 | G4 | G5 | G6 | G7 | G8 (reduced) | G9 | A1 | A2 | A3 | A4 | candidate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S1 | fail | pass | pass | fail | fail | fail | pass | pass | pass | fail | fail | fail | fail | no |
| S2 | fail | pass | pass | fail | fail | fail | pass | pass | pass | fail | fail | fail | fail | no |
| S3 | pass | **fail** | pass | pass | **fail** | pass | pass | pass | pass | pass | fail | fail | fail | no |

G1 = CAGR or Calmar gate (both financings); G2 = max DD <= K + 1 pp; G3 = 2017-end CAGR >= K - 1 pp; G4 = above K in >= 2 of 3
sub-periods (base); G5 = pre-tax bootstrap median >= 0 (both financings); G6 = G1 at m = 1; G7 = six OOS windows; G8 = pooled
OOS bootstrap (reduced); G9 = turnover <= 2 x K. A1 = after tax above K in all three windows; A2 = B1 median >= 0 and share >= 55 %;
A3 = B2 likewise; A4 = pre- and post-2020 after-tax deltas both positive.

**S3's failures in detail.** G2: max DD 50.4 % against K's 46.7 % (+3.7 pp). G5: pre-tax bootstrap median -0.77 pp (base) and -0.81 pp
(calibrated). A2 and A3: B1 median -0.43 pp (40 % share) and B2 median -0.63 pp (37 %). A4: pre-2020 after-tax -0.18 pp.
S3 fails only on these; G1, G3, G4, G6, G7, G8, G9 and A1 pass.

**S1 and S2 fail the return gates.** G1 fails for both (CAGR 15.04 and 14.88 % against K's 15.76 %; the Calmar route needs
1.1 x K's Calmar, which is 37.2 and neither reaches it). G4 fails (0 of 3 sub-periods above K for either arm). G5 and A1 through A4 fail.

**OOS (G7, calibrated, m = 2, Delta CAGR pp; GSPC 1957-1999 / M1 / M2 / M3 / M4 / M5):**
S1 +0.18 / +0.77 / +1.04 / +0.43 / -0.18 / +0.22 (median +0.32); S2 +0.27 / +1.31 / +1.23 / +0.64 / -0.10 / +0.20 (median +0.46);
S3 +0.32 / +1.70 / +1.15 / +0.73 / +0.21 / +0.18 (median +0.53). The OOS windows favour S3 and both S arms (G7 passes for all
three). The OOS drawdown changes are mixed: S3 is +6.8 pp worse in M4 and 3.5 to 3.9 pp better in M2 and M3.

**G8 (reduced pooled OOS bootstrap, median annualised Delta):** S1 +0.42, S2 +0.64, S3 +1.00 pp. R2 -0.11 pp. The reduction is
pre-registered in the vol_regime study and labelled as such.

## Deflated Sharpe (N = 372 = 369 + 3 arms)

Pre-tax daily base series: K 0.709, R2 0.699, S1 0.686, S2 0.686, S3 0.675. After tax (progressive daily NAV): K 0.422, R2 0.385,
S1 0.421, S2 0.429, S3 0.439. The bar is 0.95; no arm reaches it. The K sanity check at N = 365 reproduces gates_v1 (0.711).

## Deviations and amendments

- **G8 is reduced** (block-20 circular bootstrap of the M1-M5 daily Delta, 2,000 replications, seed 20261011), exactly as
  pre-registered in vol_regime_leverage_v1. The pooled ten-year path bootstrap is not run.
- **Interpretation fixed in the pre-registration:** the "daily sigma" the 10-year term averages is the EW-sigma series itself.
  The absolute-return sigma reading is not tested.
- No amendments. No gate or arm was changed after results.

## Caveats

- **S3's full-sample exposure is not matched.** c3 matches exposure on pre-2010 vote-on days only, and that is exact (1.6025). On
  the full sample S3 averages 1.82 against K's 1.75 on vote-on days (1.39 against 1.35 over all days), so its extra return and its
  +3.7 pp max DD partly reflect extra leverage in the later years. I did not decompose the drawdown by period.
- **The slow leg has a short history in the main window.** QQQ data starts 1999-03-10, so the 10-year average is about one year
  long at 2000-03 and reaches 2,520 sessions only around 2009 (no pre-QQQ splice, as pre-registered). Bootstrap paths start their
  slow leg at their own first session. Read the 2000-2009 results as a short-history slow leg.
- **One path.** The after-tax windows overlap heavily (2000 and 2010 share history). The bootstraps and OOS windows are the
  robustness checks, and both are negative for S3 on the pre-tax and after-tax bootstraps.
- **S1 and S2 change average leverage** as well as smoothness (their pre-2010 vote-on exposure is 1.40 and 1.22 against K's 1.60).
  S3 is the level-matched test.
- The fast engine is used throughout: `research.fast_engine.parity --quick` passes and K and R2 reproduce band_h2_combo_v1's pre-tax
  and after-tax anchors to 0.01 pp (harness PASS on 2026-10-27, before the arms ran).
