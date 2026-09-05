# Crypto MAX economic protocol — 2026-09-05

**Status:** pre-results, immutable protocol record; this is not a frozen run.

The study compares `crypto_max_22d_hyst4`, the incumbent
`crypto_low_variance_hysteresis_v1`, and a fixed liquidity comparator over 3
arms × 2 fresh-reset windows × 3 cost tiers = 18 unique cells. Full window is
`2021-04-01..2026-07-31`; recent is `2024-01-01..2026-07-31`. All metrics are
account-level with 5% crypto allocation and 95% idle native cash at zero yield.
The MAX candidate remains the frozen 22 completed single-day-return maximum,
ascending rank, 96h cadence, top-2 equal weights and HYST4 retention.

Technical gate: every one of the 18 cells must be present exactly once, with
finite required metrics, matching manifest/data/source hashes, causal as-of
inputs, and an actual fee/terminal ledger. Any failure is `BLOCKED`; no
fabricated zero, fee, terminal or return value is permitted. Native terminal
mark/trace is recorded; forced crypto liquidation is not substituted.

Economic gates are exact binary conditions, evaluated after technical validity:

- **R1:** MAX net return > 0 in both full and recent at 3x cost.
- **R2:** full-window 1x: `Sharpe_MAX > Sharpe_INC` OR (`DD_MAX <= 0.90 * DD_INC` AND `CAGR_MAX > 0` AND `CAGR_MAX >= max(0, 0.90 * CAGR_INC)`).
- **R3:** MAX drawdown <= `1.5 * incumbent drawdown` in both full and recent at 3x.
- **R4:** MAX net return >= 0 in both full and recent at 2x.
- **R5:** MAX net return >= liquidity-control net return in both full and recent at 3x.

Any failed R1–R5 is `REJECT`; all five passing is
`RETAIN_FOR_RESEARCH`. The liquidity comparator is secondary and fixed: the
prior 20 completed D1 bars ranked by median `close * volume`, descending with
symbol tie-break. It does not replace the incumbent baseline. Its feed volume
unit and the configured scanner/bar-availability pool are not venue-PIT
proofs, so no result supports PAPER/LIVE. Fold and BTC-regime metrics are
optional descriptive outputs only, never gates. No old G1–G13 pass claim is
made.
