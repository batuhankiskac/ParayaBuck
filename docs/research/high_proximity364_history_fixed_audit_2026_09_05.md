# Corrected high-proximity364 independent audit — 2026-09-05

## Status

`AUDIT_COMPLETE`, research-only and diagnostic. The corrected result contains exactly six cells with manifest digest `2e391f735055d43af7903536e3f6ef2636b9080c343b36e3bea045d2e7784ed0`. Candidate input SHA is `73783d27c6b8ace01a7571844ef56817f771b0751060fdc45ad9d702684359f3`. Machine output: `data/research/high_proximity364_history_fixed_2026_09_05/audit_2026_09_05.json`.

Every candidate and reused incumbent NAV starts at the `INITIAL_CASH` sentinel of 100000. The audit preserves the first native point and its first-day return. All six candidate cells reconcile cash plus marked positions to equity with maximum error 0 and reconcile terminal ledger equity to terminal NAV with error 0; all values are finite. Fees and fills are read from each cell and summarized independently.

## Corrected candidate readout

| Window | Cost | Candidate terminal NAV | Total return | Hourly DD | Hourly Sharpe | Daily correlation to incumbent | Fills | Mean gross | Active fraction |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| full | 1x | 106868.58 | 6.8686% | 2.7979% | 0.6933 | 0.9383 | 1104 | 2.9813% | 60.502% |
| full | 2x | 106680.01 | 6.6800% | 2.8202% | 0.6751 | 0.9382 | 1104 | 2.9815% | 60.502% |
| full | 3x | 106491.74 | 6.4917% | 2.8426% | 0.6569 | 0.9380 | 1103 | 2.9816% | 60.499% |
| recent | 1x | 101630.61 | 1.6306% | 2.8015% | 0.3221 | 0.9279 | 995 | 2.3858% | 61.433% |
| recent | 2x | 101491.08 | 1.4911% | 2.8237% | 0.2956 | 0.9278 | 994 | 2.3857% | 61.433% |
| recent | 3x | 101351.69 | 1.3517% | 2.8461% | 0.2691 | 0.9277 | 993 | 2.3856% | 61.433% |

Hourly drawdown and Sharpe use the full hourly NAV curve; hourly Sharpe annualizes 8,760 observations. Daily correlation is correlation of synchronized daily net-PnL returns and is not the native hourly Sharpe identity.

## Blend readout

Each blend is an initial-capital 50/50 curve average with the incumbent. The half-cash comparison is an analytical half-incumbent/half-constant-100000 curve; neither is a shared live ledger or order competition simulation.

| Window | Cost | Blend return delta vs incumbent | Blend DD delta vs incumbent | Blend return delta vs half-cash | Daily corr |
|---|---:|---:|---:|---:|---:|
| full | 1x | +1.5210 pp | -2.2330 pp | +3.4343 pp | 0.9383 |
| full | 2x | +1.8753 pp | -2.1780 pp | +3.3400 pp | 0.9382 |
| full | 3x | +2.2267 pp | -2.1232 pp | +3.2459 pp | 0.9380 |
| recent | 1x | -0.2443 pp | -0.5370 pp | +0.8153 pp | 0.9279 |
| recent | 2x | -0.0001 pp | -0.6293 pp | +0.7455 pp | 0.9278 |
| recent | 3x | +0.2427 pp | -0.7217 pp | +0.6758 pp | 0.9277 |

## Interpretation and limitations

The corrected candidate is economically active and the corrected accounting is internally consistent. Full-window blends improve return and drawdown versus the incumbent in this descriptive curve comparison; recent-window return deltas range from slightly negative to slightly positive. The high daily correlation means the result remains largely shared crypto exposure. These figures do not establish independent alpha or a complementary shared-capital implementation.

The original `high_proximity364` cells are retained only as an `INVALID_ALGORITHM_BASELINE_HISTORY_TRIM`: their inherited 23-bar declaration allowed the engine’s non-BTC 274-bar trim to defeat a 364-bar score window. They are not a valid corrected candidate comparison. The incumbent cells are reused as frozen controls.

The corrected run log contains 129 `Historical strategy batch incomplete` warnings. This audit does not infer trades or causes from those warnings; the warnings are missing decision-level evidence. A future trace should classify scheduled skips, eligibility, scores/ranks, observed membership, cash reasons and terminal marks directly. No optimization, new strategy, production integration, or promotion follows from this audit.
