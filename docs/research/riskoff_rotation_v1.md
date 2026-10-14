# Risk-off rotation v1 (2026-10-14)

> Evidence for this report (`research/riskoff_rotation_v1/` code, data inputs and results) is not on
> `main`; it is preserved in tag `archive/research/riskoff-rotation-v1-2026-10`.

**Verdict: FAIL — no arm passes all gates.** R4 is the closest miss: it clears
return, drawdown, sub-period, m=1 and bootstrap gates, but fails the turnover
gate (12.95 vs limit 11.94 annual) and has no out-of-sample run in this
campaign. R1 wins only on historical CAGR and drawdown but loses the sub-period,
bootstrap-median and turnover gates. Gold risk-off in this sample (R2) lowers
CAGR and raises max DD.

## Method

Production E2 (C0) exactly, per `ewma_band_v1` / `e2_integration_v1`:
close > SMA200; 252-session price return > 0; close > 126-session channel
midpoint; at least 2 of 3. QLD* = daily-reset 2x QQQ total return with base and
calibrated financing (+0.75%/yr × (L−1)), 4 bps × m per unit traded,
m = 2 gated and m = 1 reported, 0.5 pp resize band, cap 0.98.

Defensive assets (unlevered, `leverage_frontier_v1` constructions):
- BOND10 = 10-year par bond from DGS10, −0.15%/yr.
- GOLD = GLD from 2004-11-19, GC=F −0.40% before that.
- BILLS = DTB3 −0.10% for the R3 hurdle only.

Trend gating on each asset's own close: 252-session total return > 0 and
close > 200-session SMA. R3/R4 choose the better of BOND10/GOLD by
252-session return, held only if it beats the T-bill 252-session return.
R4 sizes the chosen asset by min(1, 0.10/vol20) instead of 0.98.
Total weight capped at 0.98.

Window 2000-03-09 → 2026-09-29. Block-20 bootstrap, 5,000 paths, seed
20261011, m = 2.

## Historical results (m = 2)

| Arm | CAGR base | CAGR cal | Max DD base | Max DD cal | 2017+ base | Turn/yr |
|---|---:|---:|---:|---:|---:|---:|
| C0 | 14.67% | 14.10% | 49.56% | 49.65% | 32.44% | 5.97 |
| R1 | 14.83% | 14.26% | 42.22% | 42.44% | 32.00% | 12.46 |
| R2 | 12.48% | 11.92% | 59.14% | 60.37% | 33.61% | 12.46 |
| R3 | 14.26% | 13.69% | 43.19% | 43.32% | 33.09% | 14.23 |
| R4 | 14.99% | 14.42% | 41.99% | 42.21% | 32.67% | 12.95 |

Sub-period CAGR (base, m = 2):

| Arm | 2000–08 | 2009–16 | 2017+ |
|---|---:|---:|---:|
| C0 | −1.10% | 13.26% | 32.44% |
| R1 | 2.42% | 9.93% | 32.00% |
| R2 | −6.61% | 11.93% | 33.61% |
| R3 | −1.67% | 11.96% | 33.09% |
| R4 | 0.49% | 12.08% | 32.67% |

## Bootstrap (block-20, 5,000 paths, m = 2)

| Arm | median CAGR base | median diff vs C0 base | median CAGR cal | median diff vs C0 cal |
|---|---:|---:|---:|---:|
| C0 | 7.19% | +0.00 pp | 7.29% | +0.00 pp |
| R1 | 7.40% | −0.02 pp | 7.41% | +0.00 pp |
| R2 | 8.19% | +0.51 pp | 8.14% | +0.53 pp |
| R3 | 8.21% | +0.52 pp | 8.08% | +0.54 pp |
| R4 | 8.04% | +0.54 pp | 8.01% | +0.58 pp |

## Gates (G1-G9)

| Arm | G1 base/cal | G2 | G3 | G4 | G5 base/cal | G6 | G9 | pass_all |
|---|---|---|---|---|---|---|---|---|
| R1 | P/P | P | P | F (1) | F/P | P | F | F |
| R2 | F/F | F | P | F (1) | P/P | F | F | F |
| R3 | F/F | P | P | F (1) | P/P | F | F | F |
| R4 | P/P | P | P | P (2) | P/P | P | F | F |

G7/G8: not run (no eligible OOS data in this campaign). C0 reproduction harness
PASS (max diff 0.0000 pp). m=1 CAGR: C0 14.95%, R4 15.59%, R1 15.41%,
R3 14.92%, R2 13.04%.

## Findings

- **Bond trend-gating (R1)** is the only bond form with positive historical
  CAGR (+0.16 pp vs C0) and lower max DD (42.2% vs 49.6%), but it wins only
  one of three sub-periods (2000–08) and its bootstrap median is −0.02 pp below
  C0 on base financing, failing G5 and G4.
- **Gold (R2)** remains harmful on this window: −2.19 pp CAGR and +9.58 pp
  max DD, confirming `gold_riskoff_oos_v1`. Its bootstrap median is higher
  (+0.51 pp), so the arm fails return/DD, not the bootstrap.
- **Selection (R3)** lowers return (−0.41 pp) despite higher bootstrap
  median.
- **Vol-sized selection (R4)** is the strongest: +0.32 pp CAGR, 41.99% max DD,
  +0.23 pp 2017+, 2 of 3 sub-periods, bootstrap median +0.54 pp and
  +0.58 pp calibrated. It misses only on turnover (12.95 vs the 2 × 5.97
  = 11.94 limit) and on G7/G8, which were not run.
- Off-state switching is the cost driver: even where the risk-off leg helps,
  entries/exits roughly double turnover versus C0.

## Decision

No arm passes all pre-registered gates. VT35 / production E2 unchanged.
R4 is close and could be revisited only after an explicit exception to G9
and a dedicated out-of-sample run.
