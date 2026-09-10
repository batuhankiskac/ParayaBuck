# Equity H01–H16 local diagnostics — 2026-09-09

Status: `LOCAL_DIAGNOSTICS_COMPLETE_NO_ECONOMIC_ADMISSION`. All definitions remain frozen before outcomes. These diagnostics use local receipt-bound files and do not change the authoritative gates, V3, PAPER, LIVE, production, registry, or configuration.

| ID | Local work | Result | Economic admission |
|---|---|---|---|
| H01 | existing SEC facts/price/share structural diagnostic | BLOCKED_PRICE_SHARE_BASIS_UNPROVEN | `false` |
| H02 | existing SEC facts/price/share structural diagnostic | BLOCKED_BREADTH | `false` |
| H03 | existing SEC facts/price/share structural diagnostic | READY_SCHEMA_ONLY | `false` |
| H04 | existing SEC facts/price/share structural diagnostic | BLOCKED_BREADTH | `false` |
| H05 | existing SEC facts/price/share structural diagnostic | BLOCKED_PRICE_SHARE_BASIS_UNPROVEN_OR_BREADTH | `false` |
| H06 | 274 receipt-time Form 4 rows and 63-session event joins | DIAGNOSTIC_ONLY_NO_SCORE_ADMISSION | `false` |
| H07 | none beyond frozen source gate | NO_LOCAL_ECONOMIC_JOIN | `false` |
| H08 | none beyond frozen source gate | NO_LOCAL_ECONOMIC_JOIN | `false` |
| H09 | none beyond frozen source gate | NO_LOCAL_ECONOMIC_JOIN | `false` |
| H10 | 3 exact same-CIK filing pairs and 63-session event joins | SMALL_SAMPLE_METADATA_EVENT_DIAGNOSTIC_ONLY | `false` |
| H11 | none beyond frozen source gate | NO_LOCAL_ECONOMIC_JOIN | `false` |
| H12 | none beyond frozen source gate | NO_LOCAL_ECONOMIC_JOIN | `false` |
| H13 | full local PIT OHLC/action scan plus authorized route reconciliation | BLOCKED_RAW_ADJUSTED_BASIS | `false` |
| H14 | preserved frozen H14 result | EXISTING_FROZEN_DIAGNOSTIC_ONLY | `false` |
| H15 | 1 lifecycle row; announcement timestamp check | LIFECYCLE_METADATA_ONLY_NO_EVENT_RETURN | `false` |
| H16 | none beyond frozen source gate | NO_LOCAL_ECONOMIC_JOIN | `false` |

H06 uses all 274 local candidate transaction rows and computes receipt-time 63-session price joins only as event diagnostics; score/rank admission remains false because market-cap/security-continuity gates are unresolved. Cost multipliers are recorded but no notional cost is invented; the moving-block bootstrap and fixed +7-day placebo are diagnostic, while issuer-label permutation and V3 overlay remain closed without a full label panel and ranked signal.

H10 uses all 3 exact same-CIK version pairs as a small-sample metadata/event diagnostic with the same explicit cost, bootstrap, permutation, and overlay boundaries; breadth and direction evidence remain incomplete.

H14b runs the fixed 95/5 joint NAV on every active H14 date where the strictly prior dated V3 weight history permits spare capacity (31 days across 14 months), at 1x/2x/3x, with full/recent/true-holdout deltas versus V3 for return/CAGR/Sharpe/Sortino/max drawdown/CVaR, correlation, calendar-year regime, active-day counts, cap checks, 10,000-block bootstrap, paired-sign permutation, and a separate POST_REVIEW_DIAGNOSTIC +7 trading-session native schedule placebo. Turnover remains null because no target turnover ledger exists; lowest-ranked substitution remains unrun because the local weight history has no frozen rank field. The existing H14 18-cell result is preserved.

H13 has PIT OHLC rows and identity interval coverage, and the corporate-action table contains 143,446 rows (141,908 cash-dividend, 1,538 split/stock-action rows), but the canonical bar file is split-adjusted only and has no raw/adjusted/split-factor columns. Raw/adjusted invariants therefore cannot be proven from these files; no adjusted/raw substitution is made and no economic result is admitted.

H01–H05 reuse the existing receipt-bound 288-row diagnostic: H03 has schema-ready rows, while price/share basis, breadth, periods, or payout inputs keep admission closed.

No result is selected on forward returns. Small samples are diagnostic only; no candidate is eligible for promotion or integration.
