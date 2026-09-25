# ETF diversifier v1 — Stage A results

Verdict: **STAGE_A_PASS — both frozen candidates pass all eight pre-registered
gates; preferred by the frozen tie-break: `etf_static_defensive_v1` (C1).**
A post-hoc matched cash control (below) narrows what the result means.
Stage A authorizes a shared-ledger Stage B only; no production change.

Pre-registration: `PREREGISTRATION.md` (commit `3d33501`, before any outcome).
Machine-readable results: `RESULTS.json`. Inputs: `DATA_MANIFEST.json`,
`V3_CURVES.json.gz` (shipped config: V3 0.55, `max_positions` 8),
`ETF_CURVES.json.gz`, crypto causal replay A (`maintenance/crypto_canonical_causal_execution_v1`).

## Combined-account metrics (evaluation 2017-01-03 → 2026-07-30)

| Arm (1x costs) | Total | CAGR | Sharpe | Max DD | Dev Sharpe | Val Sharpe | Val DD | 2022 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Control A: V3 0.55 + crypto 0.05 | 86.1% | 6.71% | 0.657 | 19.61% | 0.929 | 0.357 | 18.74% | −15.47% |
| C1 static: V3 0.40 + ETF 0.15 + crypto | 79.7% | 6.32% | **0.757** | 17.93% | 1.031 | 0.469 | 16.87% | −13.75% |
| C2 trend: V3 0.40 + ETF 0.15 + crypto | 78.6% | 6.25% | 0.749 | 16.64% | 0.969 | 0.515 | 15.51% | −12.32% |
| *Post-hoc control B: V3 0.40 + cash + crypto* | 61.0% | 5.10% | 0.663 | 15.80% | — | 0.381 | 14.71% | −12.01% |

At 3x costs: control Sharpe 0.565, C1 0.673, C2 0.662. Sensitivity (V3 0.55 +
ETF 0.15, gross 0.75, not used for the decision): C1 Sharpe 0.729 / DD 21.6%,
C2 0.723 / 20.3%.

Daily-return correlation with V3: C1 0.48 (0.53 in 2022), C2 0.44 (0.33 in 2022).

## Gates

| Gate | C1 | C2 |
|---|---|---|
| G1 validity (coverage 100%, fills after decisions) | pass | pass |
| G2 full Sharpe ≥ A + 0.05 and DD ≤ A | pass | pass |
| G3 CAGR ≥ A − 0.50 pp | pass (−0.39) | pass (−0.46) |
| G4 validation Sharpe ≥ A, DD ≤ A + 1 pp | pass | pass |
| G5 development Sharpe ≥ A − 0.05 | pass | pass |
| G6 2022 return ≥ A | pass | pass |
| G7 3x-cost Sharpe ≥ A at 3x | pass | pass |
| G8 largest ETF share of |P&L| ≤ 60% | pass (SPY 48%) | pass (SPY 34%) |

ETF sleeve P&L on $100k (1x): C1 SPY +5,912, GLD +5,397, BIL +872, TLT −44.
C2 SPY +4,076, GLD +3,858, DBC +2,437, BIL +1,011, IEF +438, TLT −244.

## Post-hoc finding: matched cash control (not a pre-registered gate)

The pre-registration omitted the matched reservation control that the
project's core/satellite policy requires (`docs/research/core_satellite_combination_policy_v1.md`).
Against that control B (V3 cut to 0.40, the freed 0.15 left in cash):

- **Real benefit:** the ETF sleeve adds ~+1.2 pp CAGR and ~+0.09 Sharpe over B
  in the full period and raises validation Sharpe (0.469 / 0.515 vs 0.381).
  The combined Sharpe gain over A is diversification, not de-risking alone.
- **Not a drawdown hedge:** B has the *lowest* drawdown (15.8%). Most of the
  drawdown improvement versus A comes from holding less V3. C1's drawdown is
  2.1 pp worse than B, C2's 0.8 pp worse.
- **2022 was not rescued:** B lost −12.0% in 2022; C2 (mostly in BIL that
  year) −12.3%, C1 −13.8% (stocks and long bonds fell together).
- **Regime concentration:** a large share of C1's gain is gold's 2024–2026 rally.

## Known limitations

1. Stage A sums standalone sleeve accounts; it does not model account-wide
   `max_positions` contention (V3 up to 8 + crypto 2 + ETF 4–6 names exceeds
   the shipped cap of 8). Any production path needs per-sleeve slot budgets.
2. Uninvested cash earns 0% in every arm (engine default); BIL's T-bill yield
   is therefore a small (~$0.9–1.0k) advantage for the ETF arms.
3. Crypto is only available at 1x and from 2021-04-01; it is identical in every
   arm and cannot change a between-arm comparison.
4. V3 at 0.40 is the 0.55 replay scaled linearly (exact up to rebalance-band
   and minimum-notional rounding).

## Side observations (outside this campaign's question)

- The shipped V3 configuration (`max_positions` 8) replays to +76.9% (1x,
  standalone) versus the canonical six-position control's +97.6%. This is a
  CAP8 consequence to investigate separately, not a reason to change settings.
- Even the best arm spends ~570 of ~2,400 sessions more than 5% below its
  peak; the runtime's 5% drawdown latch would block new entries on those days.

## Next step

Stage B, pre-registered before outcomes: a shared-ledger multi-sleeve replay
(one cash balance, one position book, per-sleeve slot budgets) carrying
**both** C1 and C2, with the matched cash control B as a mandatory gate.
