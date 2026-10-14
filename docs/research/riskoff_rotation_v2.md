# riskoff_rotation_v2 — confirmation + audit of riskoff_rotation_v1 R4

> Evidence for this report (`research/riskoff_rotation_v2/` code, data inputs and results) is not on
> `main`; it is preserved in tag `archive/research/riskoff-rotation-v2-2026-10`.

**Verdict:** FAIL. R4 passes G1–G8 (including all five OOS windows and the pooled
OOS bootstrap) but fails the pre-registered replacement G9 (turnover-cost stress at 8 bps
for R4 only).

## Audit findings

(a) R1 and R2 both deploy a single unlevered defensive leg at 0.98 whenever E2 is off, so
rebalance sizes are similar and turnover is near-identical (~12.46/yr); trades differ (1578 vs
1607). Expected, not a bug.

(b) Bootstrap uses chunk-outer consumption with a 20-session mean block, 3-session
back-extension, seed 20261011, 5,000 ten-year paths, matching ewma_band_v1. C0 median now
7.16% base / 6.73% calibrated, byte-identical to ewma_band_v1.

(c) No look-ahead: trend/selection/vol use closes <= decision close; defensive fills at the next
session open (bond intraday open = previous close; GLD/GC open where present, otherwise
previous close); costs charged on every defensive switch.

(d) GC=F proxy: 18 market-move days above 5%; no suspicious roll gaps. BOND10* vs IEF
2002–2024: corr 0.96, CAGR 0.67 pp lower; vs TLT: corr 0.90, CAGR 0.94 pp lower. Accepted as
trend-proxy instrument.

## Main window (2000-03-09 → 2026-09-29, 6679 sessions)

| financing | arm | CAGR | max DD | 2017+ CAGR | turn/yr | boot median Δ |
|---|---|---:|---:|---:|---:|---:|
| base | C0 | 14.67% | 49.56% | 32.44% | 5.97 | 0.00 pp |
| calibrated | C0 | 14.10% | 49.65% | 31.69% | 5.97 | 0.00 pp |
| base | R4 | 14.99% | 41.99% | 32.67% | 12.95 | +0.542 pp |
| calibrated | R4 | 14.42% | 42.21% | 31.93% | 12.95 | +0.540 pp |

## OOS confirmation (5 windows)

| window | C0 CAGR | R4 CAGR | C0 max DD | R4 max DD |
|---|---:|---:|---:|---:|
| ^GSPC 1957–1999 | 9.98% | 10.94% | 38.99% | 34.53% |
| M1 ^IXIC 1975–1999 | 20.68% | 20.81% | 38.62% | 37.24% |
| M3 ^N225 2001–2026 | 3.53% | 4.05% | 60.39% | 57.10% |
| M4 ^GDAXI 2001–2026 | 8.67% | 9.94% | 52.75% | 51.35% |
| M5 ^FTSE 2001–2026 | -3.44% | -1.51% | 80.86% | 72.47% |

Median CAGR Δ +0.958 pp; pooled OOS bootstrap median Δ +0.043 pp.

## Gates (R4)

G1 (both financings, m=2) PASS; G2 PASS; G3 PASS; G4 PASS; G5 PASS; G6 PASS;
G7 PASS; G8 PASS; G9 (cost stress, 8 bps for R4 only) FAIL.

Cost-stress (8 bps): base R4 13.81% vs C0 14.13%; calibrated R4 13.24% vs C0 13.56%.

pass_all = false.

## Caveat

R4's edge disappears once its own higher turnover is charged at 2x cost, so it does not
satisfy the pre-registered replacement gate despite better base returns and drawdowns.