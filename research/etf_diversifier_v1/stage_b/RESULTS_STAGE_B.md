# ETF diversifier v1 — Stage B results (shared ledger)

Verdict: **STAGE_B_FAIL — neither frozen candidate passes every gate. No
production-integration design is authorized; the shipped configuration stays.**
Structural gates all hold (the result is conclusive, not a harness failure).

Pre-registration: `PREREGISTRATION_STAGE_B.md` (commit `0c79045`, before any
Stage B outcome). Machine-readable: `RESULTS_STAGE_B.json`; run outputs:
`runs/*.json.gz` (`run_stage_b.py`, evaluated by `evaluate_stage_b.py`).

## Structural gates

| Gate | Result |
|---|---|
| S1 invariance: I1 (composite, V3 0.55, ETF disabled) decision, equity-decision and fill hashes = arm A | pass (identical, ending NAV 185,805.52 both) |
| S2 accounting (cash conserved, NAV = ledger, quantities reconciled, no duplicate/unowned fills, no future data) | pass, all 12 runs |
| S3 non-ETF slot claims ≤ 8 | pass (max 8; the harness logs ~48k "violations" in C arms only because it counts additive ETF holdings as slots) |
| S4 fills strictly after order creation | pass (asserted inside the harness) |

Arm A reproduces the accepted CAP8 shared-ledger study (total 85.8%).

## Combined-account metrics (1x costs, 2017-01-03 → 2026-07-30)

| Arm | Total | CAGR | Sharpe | Max DD | Calmar | Dev Sharpe | Val Sharpe | Val DD | 2022 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A shipped: V3 0.55 + crypto | 85.8% | 6.69% | 0.675 | 19.41% | 0.345 | 0.856 | 0.475 | 18.82% | −15.27% |
| B cash control: V3 0.40 + cash + crypto | 61.8% | 5.15% | 0.677 | 17.28% | 0.298 | 0.889 | 0.446 | 15.25% | −13.05% |
| C1 static: V3 0.40 + ETF 0.15 + crypto | 73.3% | 5.91% | 0.724 | 19.14% | 0.309 | 0.984 | 0.444 | 17.45% | −14.92% |
| C2 trend: V3 0.40 + ETF 0.15 + crypto | 74.6% | 6.00% | 0.742 | 17.32% | 0.346 | 0.911 | 0.557 | 15.77% | −13.30% |

Full Sharpe at 2x / 3x: A 0.616 / 0.552, B 0.601 / 0.526, C1 0.684 / 0.610,
C2 0.676 / 0.569.

## Economic gates

| Gate | C1 | C2 |
|---|---|---|
| G2 full Sharpe ≥ A + 0.05 and DD ≤ A | **fail** (+0.049) | pass |
| G3 CAGR ≥ A − 0.50 pp | **fail** (−0.78) | **fail** (−0.69) |
| G4 validation Sharpe ≥ A, DD ≤ A + 1 pp | **fail** (0.444 < 0.475) | pass |
| G5 development Sharpe ≥ A − 0.05 | pass | pass |
| G6 2022 return ≥ A | pass | pass |
| G7 3x-cost Sharpe ≥ A at 3x | pass | pass |
| G8 largest ETF share of |P&L| ≤ 60% | pass (SPY 46%) | pass (SPY 31%) |
| G9a full Sharpe ≥ B + 0.05 | **fail** (+0.046) | pass (+0.064) |
| G9b Calmar ≥ B | pass | pass |
| G9c validation Sharpe ≥ B | **fail** | pass |

## Interpretation

- C2 fails only the CAGR guard: it gives up about 0.7 pp/year of compound
  return versus the shipped account for +0.07 Sharpe and ~2 pp lower max
  drawdown. The pre-registered rule treats that trade as not worth it; it is
  not relaxed after the fact.
- Against the matched cash control, both sleeves do earn their capital (ETF
  P&L ≈ $15.0k C1 / $13.5k C2 on $100k, positive in every cost case), so the
  ETFs are a real diversifier, just a lower-returning one than V3.
- The shared ledger costs C1 more than Stage A implied (Stage A CAGR gap −0.39
  pp, here −0.78). Non-ETF P&L is $58.3k in C1 versus $61.8k in B at identical
  V3/crypto allocations ($61.2k in C2): path dependence through the
  account-wide daily-loss latch (64 vs 63 latched decisions, 15 blocked ETF
  increases) and the shared gross ceiling. Not decomposed further; it does not
  change any verdict.
- Stage B reused the Stage A data window, so it is a confirmation under
  realistic account mechanics, not new out-of-sample evidence. Re-tuning the
  sleeve on this same window to clear G3 would be data mining; any successor
  needs a genuinely different hypothesis or new data.
