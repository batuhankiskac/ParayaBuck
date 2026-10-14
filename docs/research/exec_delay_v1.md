# exec_delay_v1 (2026-10-14)

Verdict: **FAIL** (no candidate passes all gates). Later fills (D1-D4) raise
main-window CAGR/DD on the 2000-2026 sample but lose in OOS real-open windows
and pooled OOS bootstrap.

## Method
Reuses ewma_band_v1 harness (C0 = production E2). Arms fix fill timing only:
C0 open t+1 control; D1 close t+1; D2 open t+2; D3 half at open t+1 / half at
close t+1; D4 exits and downward resizes at open t+1, entries and upward
resizes at close t+1; D4r report-only mirror. Real-open windows corrected
against open != prior close frequency: IXIC 1990, GDAXI 1997, N225 2017,
FTSE 1991-98 only (FTSE opens are stale after 2000).

Main window (2000-03-09 -> 2026-09-29, m=2):

| Arm | CAGR base/cal | Max DD | 2017+ CAGR | Boot median Δ |
|---|---|---|---|---|
| C0 | 14.67 / 14.10 | 49.56 / 49.65 | 32.44 / 31.69 | +0.000 pp |
| D1 | 15.05 / 14.48 | 42.44 / 42.53 | 31.62 / 30.88 | +0.688 pp |
| D2 | 13.94 / 13.38 | 41.35 / 41.60 | 28.89 / 28.17 | +1.239 pp |
| D3 | 14.87 / 14.29 | 46.33 / 46.42 | 32.03 / 31.29 | +0.385 pp |
| D4 | 14.92 / 14.36 | 45.02 / 45.10 | 32.44 / 31.70 | +0.435 pp |
| D4r | 14.77 / 14.18 | 47.70 / 47.80 | 31.62 / 30.88 | +0.221 pp |

OOS real-open (calibrated, m=2):

| Window | C0 CAGR | D1 | D2 | D3 | D4 |
|---|---|---|---|---|---|
| IXIC 1990-1999 | 30.16 | 30.58 | 29.27 | 30.37 | 29.38 |
| GDAXI 1997-2026 | 8.79 | 6.44 | 7.71 | 7.63 | 8.46 |
| N225 2017-2026 | 6.78 | 5.19 | 4.77 | 6.04 | 7.25 |
| FTSE 1991-1998 | 11.68 | 10.91 | 11.64 | 11.30 | 11.87 |

Pooled OOS bootstrap median (arm - C0): D1 -0.265 pp, D2 -0.074 pp,
D3 -0.094 pp, D4 -0.031 pp.

Gates: all candidates pass G1/G2/G3/G5/G6/G9 and G4, but fail G7 and G8.

Close-only robustness (report-only): D1 vs fill-at-close-t across six windows
(^GSPC 1957-99, M1-M5) is mixed; D1 is worse in most windows (e.g. GSPC
7.36 vs 7.94, M1 14.09 vs 15.36, M5 0.95 vs 2.11), so the "wait one day"
effect is not robustly positive when opens are ignored.

## Interpretation
The main-window gain comes from avoiding part of the overnight/entry drift on
specific subsets, but it does not survive OOS splits dominated by GDAXI/N225
recent decades. D4 (asymmetric timing) has the best OOS showing but still
fails pooled bootstrap slightly. Production next-open fills remain preferred.

## Audit
moc_execution_v1 X3 implementation reviewed: no look-ahead; vol/weights through
decision close; correct cost; resize threshold charged once. The moc FTSE
window was not actually real-open after 2000 (corrected here).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
