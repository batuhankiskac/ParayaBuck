# riskoff_rotation_v3 — low-turnover risk-off rotation

Verdict: PASS — L2 (L1 + monthly defensive asset re-evaluation) passes all gates G1–G9
(base m=2 CAGR 15.49% vs C0 14.67%; calibrated 14.91% vs 14.10%; pooled OOS
bootstrap median Δ +0.00048 pp, G9 cost stress passes). L1 and L3 fail G9 (and L3 fails G1/G4).

## Method
Low-turnover variants of v2 R4 on audited v2 code. Defensive leg resizes only on >10pp
drift (L1), plus monthly choice of bond/gold/cash (L2), plus fixed 0.5 weight (L3).
Gates G1–G8 as v2; G9 = doubled-cost stress.

## Main window (base financing, m=2)
| arm | CAGR | max DD | 2017+ CAGR | turn/yr | trades/yr | boot median Δ |
|---|---|---|---|---|---|---|
| C0 | 14.67% | 49.56% | 32.44% | 5.97 | 58.7 | 0 |
| L1 | 15.11% | 41.91% | 32.74% | 12.54 | 63.3 | +0.598 |
| L2 | 15.49% | 44.22% | 33.32% | 11.43 | 62.4 | +0.780 |
| L3 | 14.53% | 44.58% | 32.83% | 10.19 | 60.9 | +0.340 |

## Gates
| arm | pass_all | failed |
|---|---|---|
| L1 | False | G9 (doubled cost) |
| L2 | True | none |
| L3 | False | G1, G4, G9 |

Selection by pooled OOS bootstrap median Δ among passing: L2 (+0.00048 pp;
L1 +0.00044 but fails G9; L3 +0.00051 but fails gates).

## Turnover / sells
L2 turnover 11.43/yr (C0 5.97) and ~62 sells/yr vs C0 ~58.7/yr. Monthly
re-evaluation and 10pp resize band reduce turnover relative to R4's 12.95/yr while
preserving edge under doubled cost.
