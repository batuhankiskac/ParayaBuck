# tax_asym_v1 — is A2's after-tax improvement robust, and how much drawdown risk does it carry?

## Question
A2 (downward-only vol resizes) beat C0 after tax in tax_frontier_v1. Confirm that it is
a robust after-tax improvement, and quantify its extra drawdown risk, over a threshold
plateau, a partial-trim variant, an after-tax bootstrap, and an 18-ETF pre-tax
robustness test.

## Control
C0 = production E2 (`equity_leveraged_trend_v1` 1.1.0), as in ewma_band_v1.

## Arms
- C0: production E2, 0.5 pp resize band both directions.
- A2_k (k in {33%,40%,50%,60%,67%,75%}): vol target 0.35; upward resizes as production,
  downward resizes only on trend exit or when target < k x held weight; resize to target.
- A2r (k=50%): same, but on a threshold trim resizes down only to
  target + 0.5 x (held - target) (partial trim).
Default name "A2" = A2_50.

## Data
QQQ/SPY synthetic QLD*, CBRT USD/TRY, Yi-UFE, DTB3/TB3MS, 18 ETFs range_vol_v2.

## Gates
Pre-tax G1-G8 (ewma_band_v1 harness) for A2 vs C0: G1 m=2 return both financings,
G2 DD <= C0+1pp, G3 2017+ >= C0-1pp, G4 >=2/3 sub-periods, G5 bootstrap median delta >=0,
G6 G1 at m=1, G7 six OOS windows, G8 pooled OOS bootstrap.
G9 turnover <= 2 x C0.
Plateau: A2_40 and A2_60 must also beat C0 after tax in all 3 windows at $100k.
T1 (after-tax bootstrap): median after-tax CAGR delta >= +0.5 pp and share >0 >= 65%.
T2 (pre-tax risk): median delta max DD <= +3 pp across 18 ETFs and 6 OOS windows,
no instrument worse than +10 pp.

## Verdict
CONFIRMED if A2 or A2r passes T1, T2 and the plateau gate.
