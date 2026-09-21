# Abnormal-volume readiness V2 — final report

## Frozen authority

- Original immutable design: `4f454f5deb4c77151b574f87c29228c859cfc884`.
- Original immutable readiness: `93e85169b9e1417902d3c13ac91fc1c304dda4f8`.
- Original blocker: `G1.2_FROZEN_SIGNAL_IDENTITY / FROZEN_Q90_CONVENTION_UNSPECIFIED`.
- Updated production main: `980c6b90b7f316bcec6a9466dfc9b0e1756cc748`.
- Frozen repaired design: `a39cf6dfc72a4a4aa92d95b3f760626e2ce216c6`.

## Data and signal identity

The runner strictly validated the canonical bar schema and consumed 11,681,230 rows through 2026-07-30 across 2,658 exchange sessions. It consumed zero rows from the partial 2026-07-31 cross-section. The lifetime loader verified 9,010 identities and the raw corporate-action artifact was hash-bound but never used as an outcome or post-endpoint signal input.

The repaired Q0.90 rule is the prior-49 multiset's one-based `x_(44)`: zero-based integer index `9*(49-1)//10 = 43`, no interpolation and no deduplication. The signal independently requires inclusive-50 dollar-volume average rank at least 46 and formation-return average rank from 16 through 35. Synthetic split transforms preserve dollar volume, ranks, threshold, score, and eligibility.

## Monthly execution and lifecycle

All 115 boundaries use completed month-end close to the first exchange open of the next calendar month. Coverage runs from 2016-12-30 close / 2017-01-03 open through 2026-06-30 close / 2026-07-01 open. The legacy first-new-month-completed-bar then following-session-fill behavior is not used.

The candidate fails closed on incomplete history, zero signal-window volume, ambiguous or unresolved identity, lifecycle invalidity, and unavailable/nonpositive intended first-open rows. Fill-day completed volume is not used. Dropped targets are not replaced and weights are not redistributed. Reserved-role target counts are zero for SPY, SH, PSQ, and DOG.

## Structural coverage and independent verification

The runner reconstructed 1,730 eligible signals and 595 targets. All 595 intended targets had a valid first-open row; one structural terminal event was retained without price or P&L. Full per-target reference sessions, dollar volumes, sorted values, threshold, score, ranks, target order, weight, and fill schedule are in `RUNNER_OUTPUT.json`.

The independent verifier separately streamed the raw bars, loaded lifetimes, reimplemented x44, average ranks, cross-section ordering, weights, schedule, fill checks, and terminal handling, and matched every normalized structural value for all 115 decisions. Runner and verifier comparison hashes are both `8baed348f477f4f12746c1f27bc7c7a2b8214a2c35fe5271260a82a1e6515e33`.

An initial exact comparison failed only because runner ratios used the ambient 28-digit Decimal context while the verifier used 50 digits; target identities, order, weights, fills, and events were unchanged. Precision is now explicitly fixed at 50 digits. Fresh runner and verifier passes match exactly, and an additional runner replay produced a byte-identical output SHA-256 `cfa881156725f471736f7db8de6255b9696a62e0f93977290ea7b9e9fd3cd675`.

## Validation

- Focused readiness and repository contract tests: 72 passed.
- Full suite: 1,132 passed in sandbox; six loopback-only tests were blocked by sandbox permissions and passed 6/6 on the permitted rerun. Effective result: 1,138 passed, 1 skipped, two existing deprecation warnings.
- Ruff: pass.
- Format: 254 files checked.
- mypy: 137 source files checked.
- JSON and diff checks: pass.

No future return, P&L, NAV, CAGR, Sharpe, Sortino, Calmar, drawdown, IC, hit rate, bucket return, economic parameter sweep, or candidate/control outcome comparison was calculated. G2-G9 were not evaluated.

## Decision

`G1_READY_FOR_FROZEN_ECONOMIC_TEST`

This authorizes only one separately controlled frozen economic experiment. It does not authorize PAPER, LIVE, promotion, parameter tuning, or a combination test.
