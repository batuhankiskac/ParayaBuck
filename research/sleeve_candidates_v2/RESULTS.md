# Sleeve candidates v2 — results (2026-09-25)

Verdict: **NO CANDIDATE PASSES — no production change.** Every candidate fails
at least one frozen Stage A or Stage B gate. Method and gates:
`PREREGISTRATION.md`. Machine-readable outputs: `RESULTS_STAGE_A.json`,
`stage_b/RESULTS_STAGE_B.json`.

## Stage A — summed sleeve screen (1x costs)

Control A (shipped): CAGR 6.71%, Sharpe 0.657, max DD 19.61%. Cash control B:
CAGR 5.10%, Sharpe 0.663.

| Candidate | CAGR | Sharpe | Max DD | Holm p | Stage A |
|---|---:|---:|---:|---:|---|
| passive_us_core_v1 | 7.76% | 0.781 | 19.40% | 0.088 | FAIL (G6: 2022 −15.83% vs −15.47%) |
| defensive_factor_v1 | 6.78% | 0.734 | 17.87% | 0.194 | pass |
| risk_parity_v1 | 6.39% | 0.764 | 17.53% | 0.009 | pass |
| gtaa5_sma_v1 | 6.13% | 0.724 | 16.97% | 0.194 | FAIL (G3) |
| defensive_sectors_v1 | 6.73% | 0.749 | 15.89% | 0.194 | pass |
| global_equity_v1 | 6.82% | 0.721 | 19.46% | 0.194 | pass |
| tbill_sweep_v1 (X) | 7.19% | 0.699 | 19.57% | 0.000 | pass |

## Stage B — shared ledger (1x costs)

The fresh arm A reproduced the v1 archive's decision and fill hashes exactly,
so the archived A/B controls are reused. A: CAGR 6.69%, Sharpe 0.675, max DD
19.41%, validation Sharpe 0.475, 2022 −15.27%. B: CAGR 5.15%, Sharpe 0.677.

| Candidate | CAGR | Sharpe | Max DD | Val Sharpe | 2022 | Ending NAV 1x/2x/3x | Failed gates |
|---|---:|---:|---:|---:|---:|---|---|
| risk_parity_v1 | 5.84% | 0.727 | 18.77% | 0.467 | −14.4% | 172.2k / 158.3k / 151.2k | G3, G4, G9a |
| defensive_sectors_v1 | 6.17% | 0.714 | 18.41% | 0.379 | −14.4% | 177.4k / 164.9k / 156.1k | S1, G2, G3, G4, G9a, G9c |
| defensive_factor_v1 † | 6.36% | 0.709 | 19.03% | 0.481 | −14.6% | 180.5k / 169.5k / 160.5k | G2, G9a |
| global_equity_v1 † | 6.83% | 0.738 | 20.07% | 0.584 | −15.1% | 188.2k / 178.1k / 165.8k | G2 (max DD above A) |
| tbill_sweep_v1 (X) | 6.77% | 0.682 | 19.11% | 0.469 | −15.1% | 187.1k / 183.0k / 171.3k | XG2 (+0.08 pp vs +0.25 required), XG5 |

† Declared post-hoc extension: after `defensive_sectors_v1` failed S1, the next
Stage A passers by Sharpe were added to Stage B.

`global_equity_v1` came closest: higher Sharpe (0.738 vs 0.675), higher
validation Sharpe and equal CAGR, but its drawdown (20.07%) exceeds A's
(19.41%), so G2 fails. T-bill ETFs earned about $6.5k per $100k over the
window, yet the account-level uplift (+0.08 pp CAGR) is below the frozen
+0.25 pp threshold and inside single-path noise.

## Harness corrections (disclosed)

1. **Cash-equivalent slot double offset.** The first X-mode runs counted held
   T-bill positions twice when freeing slots, giving crypto an effective cap of
   10 (nine `account_position_count_limit` cancellations). Fixed so only
   reserved sleeve orders are offset; invalid runs kept in
   `stage_b/runs/invalid_double_offset/`.
2. **Pin leak into V3 scoring.** Pinning sleeve ETFs into the equity stream
   made them visible in V3's `context.history`; V3 then scored them as
   candidates, which moved its idiosyncratic-volatility threshold and changed
   picks (first divergence 2017-06-12: CBOE instead of ELV) although V3 never
   targeted a sleeve ETF. The composite now hides pin-only sleeve symbols from
   V3. After the fix every invariance arm except `defensive_sectors_v1`
   (whose ETFs are in V3's own fallback universe) reproduces arm A exactly
   (ending NAV 185,805.52, identical hashes). Pre-fix runs are kept in
   `stage_b/runs/pre_hide/`.

## Caveats

- One historical path: shared-ledger outcomes move by roughly ±$1–4k on a
  ~$186k ending NAV through daily-loss-latch and gross-ceiling path
  dependence, comparable to several gate margins.
- The 2017–2026 window was used by earlier campaigns; nothing here is fresh
  out-of-sample evidence.
- Broker interest on idle cash is modeled as zero everywhere; whether the
  account earns cash interest would change the T-bill comparison.
