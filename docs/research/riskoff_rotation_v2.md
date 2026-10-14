# riskoff_rotation_v2 — confirmation + audit of riskoff_rotation_v1

**Verdict:** FAIL. R4 passes G1–G8 and the original turnover gate, but fails the
pre-registered cost-stress replacement G9 (8 bps for R4 only) because under doubled
costs R4 CAGR falls below C0; therefore `pass_all` is false.

## Audit findings
- (a) R1 and R2 turnover nearly identical (~12.46/yr) is expected: both deploy a single
  unlevered defensive leg at 0.98 whenever E2 is off; trades differ (1578 vs 1607).
- (b) Bootstrap RNG consumption reordered to chunk-outer; C0 median now 7.16% base / 6.73%
  calibrated, bit-identical to ewma_band_v1.
- (c) No look-ahead: trend/selection/vol use closes <= decision close; defensive fills at next
  session (open, else previous close); costs on every switch.
- (d) BOND10* vs IEF 2002–2024: corr ~0.96, CAGR ~0.68 pp lower. GC=F proxy has no suspicious
  roll gaps (18 market-move days only).

## Main window (m=2)
| financing | arm | CAGR | max DD | 2017+ CAGR | turn/yr | boot median Δ |
|---|---|---|---|---|---|---|
| base | C0 | 14.67% | 49.56% | 32.44% | 5.97 | 0.00 pp |
| base | R4 | 14.99% | 41.99% | 32.67% | 12.95 | +0.542 pp |
| calibrated | C0 | 14.10% | 49.65% | 31.69% | 5.97 | 0.00 pp |
| calibrated | R4 | 14.42% | 42.21% | 31.93% | 12.95 | +0.540 pp |

## OOS
- ^GSPC 1957-1999: C0 9.98% vs R4 10.94%;
  DD 38.99% vs 34.53%.
- ^IXIC 1975-1999: C0 20.68% vs R4 20.81%;
  DD 38.62% vs 37.24%.
- G7 detail median CAGR Δ +0.543 pp; pooled OOS bootstrap
  median Δ +0.491 pp; G7/G8 PASS.

## Gates (R4)
- G1_base: PASS
- G1_calibrated: PASS
- G2_base: PASS
- G2_calibrated: PASS
- G3_base: PASS
- G3_calibrated: PASS
- G4: PASS
- G5_base: PASS
- G5_calibrated: PASS
- G6_base: PASS
- G6_calibrated: PASS
- G7: PASS
- G8: PASS
- G9_cost_stress: FAIL
- pass_all: FAIL

G9-cost (doubled): base R4 13.81% vs C0
14.13%; calibrated R4
13.24% vs C0
13.56%. FAIL.
