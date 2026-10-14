# vote_hysteresis_v1 — asymmetric vote-count hysteresis on E2

**Verdict: FAIL.** No arm clears all gates (G1–G9). Production E2 (C0) unchanged.

## Question

Does asymmetric hysteresis on the E2 vote count (enter/exit at different thresholds) cut
whipsaw round trips (taxable sales for the Turkish-resident owner) without materially
worse return/drawdown, relative to C0 symmetric 2-of-3?

## Method

Identical harness to `ewma_band_v1`: synthetic QLD* (daily-reset 2x QQQ total return,
base/calibrated financing), decide at close / fill at next open, cap 0.98, cost 4 bps×m,
0.5 pp resize band, rev FIN volatility sizing, block-20 bootstrap (seed 20261011, 5000
paths), six OOS windows, pooled OOS bootstrap. C0 reproduces production E2 to 0.00 pp.
Arms: C0 (>=2 / <2), Y1 (3 / <=1), Y2 (>=2 / 0), Y3 (3 / <=1 or QQQ < 0.95*SMA200),
Y4 (>=2 / <=1 for 3 consecutive closes). Four candidate arms.

## Main window 2000-03-09 → 2026-09-29, m=2

| arm | CAGR base | CAGR cal | maxDD base | 2017+ base | boot Δ base | rt/yr base | sells/yr base |
|---|---|---|---|---|---|---|---|
| C0 | 14.67% | 14.10% | 49.56% | 32.44% | 0.00 pp | 2.94 | 27.41 |
| Y1 | 12.93% | 12.40% | 41.16% | 32.24% | -0.45 pp | 1.54 | 22.55 |
| Y2 | 14.32% | 13.71% | 57.40% | 26.80% | +2.56 pp | 0.87 | 32.64 |
| Y3 | 12.93% | 12.40% | 41.16% | 32.24% | -0.47 pp | 1.54 | 22.55 |
| Y4 | 14.62% | 14.03% | 45.17% | 30.29% | +1.47 pp | 1.92 | 28.73 |

Y3 is bit-identical to Y1 in this sample (the 5%-below-SMA exit never triggers
differently), so no incremental effect from the SMA break.

## Gate outcomes (14 gates)

| arm | failed gates |
|---|---|
| Y1 | G1 (both financings), G5 (bootstrap both), G6 (m=1), G4, G7, G8 |
| Y2 | G1, G2, G3, G6, G4, G7 |
| Y3 | G1, G5, G6, G4, G7, G8 |
| Y4 | G1, G3, G6, G4, G7 |

All arms pass G9 (turnover <= 2× C0) and G2 where noted. Y1/Y3 cut drawdown (49.6% → 41.2%) and
sells/yr (27.4 → 22.6) but cost -1.74 pp CAGR and fail the bootstrap/OOS. Y2/Y4 retain or
grow bootstrap median but fail return/DD/OOS windows.

## OOS (calibrated, m=2)

Median OOS CAGR vs C0: Y1 -1.3 pp, Y2 ~0 (median +0.1 pp but deepens M2/M4 DD), Y3 -1.3 pp,
Y4 +0.1 pp. Pooled OOS bootstrap median Δ: Y1 -0.86, Y2 +0.35, Y3 -0.86, Y4 +0.17 pp. No
arm shows a consistent OOS gain (Y2/Y4 pass G8 only; fail G7 — 4/6 windows and DD > 3 pp).

## Amendments

- `arm_stats` indexing of `wants` was corrected post pre-registration (time-in-market extra
  statistic only; no metric or gate changed).

## Conclusion

Asymmetric thresholds do not pass. Strict entry (3/3) costs too much CAGR; loose exit (0 votes
or 3-day) cuts drawdown/round-trips in the bootstrap on one or two arms but fails exact return
and OOS windows. Production E2 unchanged.
