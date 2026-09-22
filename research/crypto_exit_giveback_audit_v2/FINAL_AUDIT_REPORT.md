# Stage A — canonical baseline consolidation

## Old baseline status

`SUPERSEDED_HISTORICAL_CONTROL`. The old control predates the causal observation-visibility fix. First target divergence was 2023-05-11, first fill/NAV divergence 2023-05-12. Its old results are evidence of an old implementation, not a defect in today's replay.

## New canonical identity

Strategy `crypto_low_variance_hysteresis_v1`; production source `754f4060b1eb38593201b2a269e9593d083eb534`. Isolated $100, 000 account, hourly-scanner canonical adapter, completed UTC daily strategy bars; window 2021-04-01 through 2026-07-31. Exactly **455 decisions / 388 fills**. See [baseline contract](../../docs/research/canonical_crypto_baseline.md) for frozen data, 22-day volatility, top-two/rank-four retention, 96-hour cadence, 5% allocation and costs.

- decision_evidence_hash: `bebbbd30d0029561f3a44f54cf4e62a144378832e3f00f0151addbb82b089577`
- fills_hash: `c8bf27f1d21846bf754a53a72e5da29416c79dd817162606cecc6ce19fe1e07b`
- final_state_hash: `e09c993fd46b81bfdf9878feb0e13079bc84e18aabbaa1b0bf7e533f980c7964`
- payload_hash: `016bc06a7381d19434e6d21b4bafc40f1890abee57b7ead71a9b684ab40fb8dd`
- target_hash: `ab52ec2e09586462504dbe631dec51585aff18603c03254c0a5102197dc325d4`

## Main merge

Main was verified at the expected source SHA before consolidation. All 36 changed maintenance files were classified. Only `docs/research/canonical_crypto_baseline.md` and the baseline-maintenance entry in `docs/research/research_register.md` were committed and pushed. No runtime change. `POST_BASELINE_MAIN_SHA = b81e9d9fef3744723449f4c019f2a5dd75f2e2cb`.

## Archive tag

Annotated and pushed `archive/maintenance/crypto-replay-parity-v1-2026-09` → `4b7eb4020896453a599c876092ff550216183d07`. Maintenance local/remote branches were deleted only after archive verification. Evidence remains accessible through pinned links. See STAGE_A_RECEIPT.json and STAGE_A_FILE_CLASSIFICATION.json.

# Stage B — crypto giveback audit V2

## Canonical parity proof

Fresh replay matched every archived canonical target, full decision/hysteresis, order, fill, terminal, final-state, metric, input/config and whole-payload identity. The gate passed before diagnostics. The fresh independent replay verifier also passed. This is a new run against the consolidated authority, not reuse of the V1 crypto result.

## Episode count

57 continuous-inventory episodes: 55 natural closures and2 horizon-censored liquidations. Adds and partial sales never split an episode. Natural P&L $6, 009.561372 plus censored -$528.042836 equals full-account $5, 481.518536. Final residual is <1e-8 USD; maximum daily NAV residual <1e-8 USD. See EPISODE_DEFINITION.md for fixed ex-post all-purchase-outlay normalization and terminal quote/horizon timestamp distinction.

## Holding-duration distribution

Natural median36 days; P25/P75 12/78.5; P10/P90 8/210.2; mean70.96. Full distribution is in CRYPTO_GIVEBACK_REPORT.md and SUMMARY.json.

## MFE / MAE

Completed-bar-only natural median MFE8.85% ($320.45), median MAE-7.17% (-$191.47). Time-to-MFE median8 days. Actual exit fills are excluded from extrema; retention is not clipped.

## Profit retention

50 natural episodes have positive MFE. Median retention37.27%; P25/P75 -170.83%/83.07%. Tiny positive peaks make the mean -239.82% unstable. Bins: 17 retain>=75%, 4 retain50–75%, 7 retain25–50%, 2 retain0–25%, 20 retain<=0%.

## Giveback distribution

Among positive-MFE natural episodes, median dollar giveback$236.69; P25/P75 $76.57/$610.61; mean$460.28. This is hindsight loss relative to a marked peak, not demonstrated recoverable execution profit.

## +5/+10/+20/+30/+40% groups

55 natural episodes; nested groups based on completed-bar normalized episode returns ever reaching each level. These are descriptive groups, not hypothetical sales. Terminal liquidations are excluded from exit-behavior estimates.

| Ever reached | Count | Mean final | Median final | Profitable exits | Retention P25 | Retention median | Retention P75 | Exits <=0 | Median crossing-to-MFE days | Median MFE-to-exit days |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 5% | 36 | 15.47% | 13.37% | 75.00% | 11.26% | 60.37% | 89.61% | 25.00% | 7.00 | 8.50 |
| 10% | 27 | 24.93% | 22.48% | 88.89% | 47.52% | 75.27% | 93.53% | 11.11% | 15.00 | 5.00 |
| 20% | 19 | 33.51% | 30.53% | 94.74% | 63.00% | 85.35% | 95.34% | 5.26% | 12.00 | 5.00 |
| 30% | 15 | 39.28% | 32.72% | 100.00% | 63.00% | 87.15% | 95.34% | 0.00% | 9.00 | 7.00 |
| 40% | 9 | 46.52% | 42.07% | 100.00% | 50.87% | 87.15% | 95.18% | 0.00% | 12.00 | 13.00 |
| 50% | 6 | 52.26% | 48.43% | 100.00% | 59.14% | 85.55% | 93.18% | 0.00% | 15.00 | 41.50 |

The +50% sample has only six episodes. The groups overlap and are not independent experiments. MFE is hindsight and threshold-to-peak progression does not establish an executable signal. No exit was simulated.

## Large-winner episodes

All15 natural episodes reaching>=30% exited profitably; median retention87.15%, 11 retained>=75%, 3 below50%, 1 below25%. The >=40% group has9 episodes; all profitable. All15 are individually listed with verified reasons and observed hysteresis in LARGE_WINNER_EPISODES.md. Neither censored episode reached30%. Litecoin-like partial giveback exists, but typical large-winner collapse is not established.

## Right-tail dependence

Top1/3/5/10/10% winners contribute47.52%/106.37%/145.86%/230.00%/165.29% of full-account net profit. Top10% means6 episodes. They contribute13.68%/30.62%/41.99%/66.21%/47.58% of positive P&L. Attribution only; see RIGHT_TAIL_ATTRIBUTION.md for identities and natural-net denominator.

## Winner-to-loser round trips

20/50 positive-MFE natural episodes end negative (40%); two finish near flat, both positive. Nonpositive exits among >=10%/20%/30% MFE groups: 3/27, 1/19, 0/15. See full identities in CRYPTO_GIVEBACK_REPORT.md.

## Hysteresis contribution

96 hours is an epoch decision cadence, not an entry-age lock/expiry. Held eligible rank<=4 is retained subject to a two-name cap.25 natural episodes have at least one strictly post-peak rank3/4 retained decision; their median giveback is$581.65, retention-49.09%, peak-to-exit34 days, versus$93.02, 75.27%, 4 days for the other30. This is a substantial association, but confounded by duration/rank trajectories and not causal avoidable loss. Large-MFE episodes also remain top-two ranked across59 post-peak decisions and rank3/4 across22. Rank at MFE is explicitly last-observed with age. See HYSTERESIS_GIVEBACK_AUDIT.md.

## Exit reasons

38 failed retention rank, 8 lost qualified history, 5 scanner-ineligible, 4 displaced by retained selection capacity; 2 separate terminal-censored settlements. No guessed “hysteresis expiry” or risk-overlay closure. EXIT_REASON_AUDIT.md contains per-category economics and every actual zero-target/final-fill delay, including four changed-source decisions during prolonged liquidation.

## MFE-to-exit delay

Natural median9days; P25/P75 3.5/38; P90 102; mean36.36. Median below-peak occupancy8days and still-positive post-peak occupancy4 days. Conditional first25/50/75% peak erosion medians1/2/2days (35/31/25 episodes observed). MFE is never a strategy signal.

## Period stability

No canonical independent development/validation split exists; those fields are not applicable. Annual exit-year descriptive cohorts vary markedly, with0–4 >=30% episodes per year and no such episode in 2026. Large gains were preserved in several years, but small samples cannot establish stable validation. See PERIOD_STABILITY.md; overlapping historical folds are not relabeled as untouched validation.

## Current PAPER/LTC case

`CURRENT_PAPER_CRYPTO_HISTORY_NOT_AUDITED`. Historical LTC episodes are not the user's current PAPER position. No current case was invented.

# Independent verification

PASS: 57 episodes, 4056 active completed marks, 1946 NAV snapshots, 388 fills+2 terminal settlements, 455 decisions. Independent accounting/ranking checks every episode's entries/exits, resizes, path, MFE/MAE, final P&L, retention, threshold/timing, hysteresis and final reasons; all/natural/censored aggregates independently pass. See INDEPENDENT_VERIFICATION.json and CANONICAL_INDEPENDENT_VERIFICATION.json. Focused tests: **10 passed**. Full normal suite: **1148 passed, 1 skipped, 2 existing deprecation warnings** in 101 seconds. Ruff check and format passed (260 files); mypy passed (140 source files); strict JSON validation passed. Details are recorded in VALIDATION.json.

# Crypto conclusion

`MIXED_PROFIT_RETENTION_BEHAVIOR`

Profit round trips and rank-retention association are real. Large-MFE episodes generally preserve substantial gains, rather than repeatedly contributing little final profit. Both observations belong in the conclusion.

# Exit-research decision

`CRYPTO_EXIT_RESEARCH_NOT_JUSTIFIED`

Under the requested “only genuinely yes” gate, these data do not justify opening a new exit campaign. The strongest contrary evidence—the poorer post-peak retention cohort—is explicitly retained, but is not enough to override large-winner preservation, right-tail dependence and absent independent validation. This is a present-evidence governance decision, not proof no exit can improve. No future exit families are proposed.

# What this does NOT prove

MFE is hindsight; peak profit was not necessarily tradable; no alternative exit was tested. No take-profit, trailing stop, tighter hysteresis, counterfactual P&L or leave-out rerun occurred. Normalized returns are ex-post capital-outlay returns, not coin price changes. Provider-download provenance and historical broker tradability are unavailable; bar availability is the frozen proxy. The hourly observation adapter's fallback ordering is not runtime daily-warmup observation parity. Small samples, overlapping exposure and in-sample descriptive grouping do not establish predictive causality or independent validation.

# Production state

Production source, configuration, strategy, allocation, risk, persistence and PAPER/LIVE behavior are unchanged. PAPER identities remain equity_residual_momentum_v3 and crypto_low_variance_hysteresis_v1; LIVE remains disabled. No broker action. V1 branch/SHA4444147ae8e53a2b33b0648077ee2589f4920e5d is immutable: equity conclusion remains MIXED_PROFIT_RETENTION_BEHAVIOR; crypto status is SUPERSEDED_BY_CRYPTO_GIVEBACK_AUDIT_V2_DUE_TO_STALE_BASELINE. Its old crypto conclusion was inconclusive, not wrong economics.

# Files

All evidence/code in `research/crypto_exit_giveback_audit_v2/`; focused synthetic fixtures in the three `tests/test_crypto_giveback_v2*.py` files. The directory includes the required 13 artifacts plus raw replay, summary, independent interpretive review, archived authority, StageA receipts and reproducible verifier/builder code. Production main contains only the two durable documentation changes.

# Final main SHA

`b81e9d9fef3744723449f4c019f2a5dd75f2e2cb` (pushed).

# Final research branch SHA

Branch `research/crypto-exit-giveback-audit-v2`. The final committed evidence SHA is recorded in the completion message; resolve with `git rev-parse research/crypto-exit-giveback-audit-v2`. A commit cannot embed its own SHA without changing that SHA.
