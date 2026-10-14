# tax_asym_v1 — robustness of A2 and its drawdown risk

**Verdict: FAIL — A2 does not satisfy T1.** T2 and the plateau gate pass, but the
after-tax bootstrap median Δ is +0.013 pp (gate +0.50 pp) with only 58% of paths
positive (gate 65%), on 357/1000 finite paths. Pre-tax, A2 uses essentially all of
the +5 pp drawdown allowance on the main window and fails G2/G5.

## Method
Audit of A2's implementation found no look-ahead: the target is decided at the
previous close; at each open `w` is recalculated after overnight growth, so price
drift between trades is handled correctly. A2 is reproduced through the same engine
as ewma_band_v1 for control C0.

## Threshold plateau (after-tax, $100k, base financing)

| k | 2000 window (A2 / C0) | 2010 (A2) | 2017 (A2) | beats C0 all three? |
|---|---:|---:|---:|:---:|
| 33% | 11.22 / 9.53 | 16.70 | 27.24 | yes |
| 40% | 11.22 / 9.53 | 16.70 | 27.24 | yes |
| 50% (A2) | 11.15 / 9.53 | 16.58 | 27.05 | yes |
| 60% | 11.04 / 9.53 | 16.26 | 25.48 | yes |
| 67% | 10.42 / 9.53 | 15.39 | 23.98 | yes |
| 75% | 10.78 / 9.53 | 15.94 | 24.99 | yes |
| A2r (partial trim) | 11.11 / 9.53 | 16.53 | 26.95 | yes |

Plateau gate (40% and 60% neighbours beat C0 after tax in all three windows): PASS.

## After-tax bootstrap T1 (1000 ten-year paths, $100k)
Median Δ after-tax CAGR = +0.013 pp; share >0 = 58.0%; 5th percentile = −0.312 pp;
median Δ max DD = +0.277 pp. Finite paths = 357/1000 (many terminal-liquidation
failures in synthetic paths). **T1 FAIL.**

## Pre-tax full gates G1–G8 (A2 vs C0)
G1 base/calibrated: PASS; G2 base/calibrated (DD ≤ C0+1pp): FAIL (54.52 vs 49.56,
+4.96 pp); G3: PASS; G4 (≥2/3 sub-periods): PASS; G5 bootstrap median ≥0: FAIL
(median −0.356 pp); G6: PASS; G7 six OOS windows: PASS; G8 pooled OOS bootstrap: PASS;
G9 turnover ≤2×: PASS. G2+G5 FAIL.

## 18-ETF test (EWC excluded)
Median pre-tax Δ CAGR = +0.292 pp; share positive = 66.7%; median Δ max DD = +1.48 pp;
worst instrument Δ DD = +8.04 pp (XLB). Sells/yr ratio A2/C0 median = 0.173.
**T2 PASS** (median ≤+3 pp, no instrument >+10 pp).

## Worst drawdown episodes (main window, pre-tax, base)
Both C0 and A2 peak at 2000-03-27 and trough at 2003-03-31; C0 −49.56%,
A2 −54.52%.

## Amendment
After-tax runs use base financing (0.95% + DTB3). Terminal-negative paths in the
after-tax bootstrap (643/1000) indicate the liquidation-tax model is unstable on
extreme synthetic paths; only finite paths entered the T1 distribution.
