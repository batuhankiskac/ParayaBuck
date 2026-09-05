# Crypto MAX economic protocol draft — 2026-09

**Status:** draft only; no economic run or promotion authorization.

This protocol is for the frozen `crypto_max_22d_hyst4` candidate, the incumbent
`crypto_low_variance_hysteresis_v1`, and one secondary liquidity comparator.
Each arm uses the same configured 22-symbol bar-availability/trailing-scanner
proxy, native 96-hour decisions, top two equal weights, HYST4 retention, 5%
account allocation, and existing crypto risk/execution policy. The comparator
ranks the prior 20 completed daily bars by median `close * volume`, descending
with symbol tie-break; the feed's volume-unit meaning is not independently
venue-verified.

Run exactly 3 arms x 2 fresh-reset windows x 3 cost tiers (18 unique cells):
full `2021-04-01..2026-07-31` and recent descriptive `2024-01-01..2026-07-31`,
with 1x/2x/3x configured costs. The same source, terminal policy, and native
closed-bar/next-tradable-open contract apply to all arms.

## Classification

**BLOCKED** if any technical prerequisite fails: missing or duplicate cells,
source/data/manifest mismatch, missing or non-finite required metrics, broken
terminal/fill ledger, lookahead or paired-as-of failure, or insufficient
coverage/denominators. A configured bar-availability/scanner proxy does not
prove real venue-PIT completeness; therefore even a technically complete run
remains research-only and cannot support PAPER/LIVE.

**REJECT** if technical prerequisites pass but any frozen economic condition
fails. The primary conditions are:

1. MAX full and recent 3x net return are both strictly positive.
2. MAX beats the incumbent on at least one full-window 1x efficiency criterion:
   Sharpe is higher, **or** max drawdown is at most 90% of incumbent max
   drawdown while MAX CAGR is positive and at least `max(0, 0.90 * incumbent
   CAGR)`.
3. MAX full and recent 3x drawdown do not exceed 1.5 times the incumbent's
   corresponding 3x drawdown, and MAX 2x full/recent net return is positive.
4. MAX full and recent 3x net return is at least the liquidity comparator's
   corresponding return. The liquidity arm is a fixed secondary benchmark;
   it never replaces the incumbent as the primary baseline.

**RETAIN_FOR_RESEARCH** only when all four conditions pass, with complete
artifacts and explicit `pit_completeness=UNSUPPORTED_PROXY` metadata. This
classification is not a deployment or PAPER/LIVE recommendation.

Walk-forward fold, BTC-regime, contributor-LOO, turnover, and cost-ledger
statistics are diagnostic unless the native engine emits complete denominators
and comparable fresh-run metrics. Missing diagnostics must be recorded as
unavailable, never treated as passed. The archived tournament's G1-G13 remain
historical reference conditions ([plan] lines 48-60); its direct implementation
only checks Sharpe/CAGR-drawdown/fold-dependence/PIT parity ([source] lines
1536-1549), so this draft does not claim those gates were automatically
satisfied.
