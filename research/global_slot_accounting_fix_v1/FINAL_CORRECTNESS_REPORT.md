# Global slot-accounting correctness report

## Correctness decision

`GLOBAL_SLOT_ACCOUNTING_REPAIR_ACCEPTED`.

## Starting main and replay V1 closure

Starting local and remote main was
`7b0a1063ba5a8ca8ed6bf93cf06a0b684da94b3a`, one docs-only commit beyond
the requested production baseline `8e48b301413a679e6d2b097c28e8711c50ab1a8b`.
The V1 replay evidence branch tip was
`1c1c21ef1631fd426b4e90188ac446db4eaa0fd2`; it had not been pushed.
The annotated tag `archive/research/shared-account-replay-v1-blocked-2026-09`
was pushed and its remote peeled target verified at that exact SHA. Main
closure commit `1692526a32c0b61f907faef1eb79c14dd92f7a45` records the
blocked research status. The local replay branch was deleted, with none of
its implementation merged into production. V1's equity and crypto isolated
parity passed, combined replay was deterministic, and 21 timestamps had
seven slot claims under cap six. No cap-eight arm or migration economics ran.
V1 alone did not prove a production root cause.

## Intended semantics and production path

`maximum_positions = 6` is the configured account-wide risk cap. Nonzero
equity and crypto broker positions count; pending opening orders claim slots;
adds to held symbols deduplicate; partial/pending exits remain occupied until
a fresh broker snapshot is flat. The detailed authority audit and exact
PAPER/LIVE path are in `SLOT_POLICY_AUDIT.md` and `PRODUCTION_CALL_GRAPH.md`.
The crypto strategy still uses top two, retention rank four, 96-hour decisions
and equal targets; the shared execution band remains 0.005. Its exact
order-suppression equation and minimum-trade guards are in
`CRYPTO_REBALANCE_BAND_AUDIT.md`.

## Minimal reproduction and June structural trace

A synthetic production overlay/planner state with four equities, BCH and a
small LTC position started at six slots. Target BCH/BTC created BCH and BTC
BUYs; LTC's 0.001 weight was below the 0.005 band, so no SELL existed. The
BTC opening would claim slot seven. The archived 2024-06-14 trace has the
same holdings/target/order structure. No historical return or strategy
performance was consulted. `MINIMAL_BUG_REPRODUCTION.md` records the inputs.

## Root cause, responsibility and repair

`PRODUCTION_REBALANCE_INTERACTION_BUG_CONFIRMED`: the overlay allowed an
omitted managed current-market holding to be replaced in the desired target,
while the order planner could suppress its actual SELL. With no SELL leg,
the existing two-phase protocol submitted replacement BUYs immediately.
The research replay exposed the same production interaction; its code was not
the sole cause.

`reserve_opening_order_slots()` in the shared execution/risk module now
counts nonzero broker positions, persisted/same-cycle reservations and broker
OPEN symbols, then admits new-symbol BUYs sequentially in existing planner
order. Immediate batches are filtered before persistence/submission. Deferred
BUYs are checked after SELL completion and a fresh account snapshot. The
strategy, rebalance band and SELL protocol are unchanged. No real broker
order was created or canceled. The invariant and same-bug-class review are
in `SLOT_RESERVATION_INVARIANT.md` and `SAME_BUG_CLASS_AUDIT.md`.

## Regression and replay validation

Synthetic tests cover six occupied names, one remaining slot, held-name
adds, working buys/sells, partial and confirmed exits, cancellation, mixed
market contention, band-suppressed and band-eligible LTC exits, deterministic
priority, and table-driven combinations. A PAPER `process_closed_bars()`
test proves that the real production decision boundary submits no seventh
opening. `REGRESSION_TEST_MATRIX.md` lists the cases.

An isolated copy of the archived replay used the small
`ARCHIVED_REPLAY_VALIDATION.patch` against corrected production source.
Its 12 synthetic tests passed. Combined cap-six smoke and repeat passed all
22 required checks: **`over_cap_timestamp_count = 0`**, maximum actual
positions 6, maximum slot claims 6, cash/gross/sleeve checks passed, and
both event hashes matched at
`81cc6daf9e978d1a0d23f77c4d52504f057d78e35af9cdef146e477976383e54`.
Equity isolated parity passed seven checks; crypto isolated parity passed
twelve checks. `CAP6_SHARED_REPLAY_REVALIDATION.json` is the compact receipt.

## Full validation and production state

Final full pytest: **1170 passed, 1 skipped**. Focused slot and open-order
pagination suites passed. `ruff check .`, `ruff format --check .`, `mypy src`
(strict, 133 source files), strict mypy on the two isolated replay modules,
JSON parsing, `git diff --check`, and a changed-file credential-pattern scan
passed. The initial sandboxed full pytest had six loopback-bind permission
errors; the complete rerun with localhost binding allowed passed.

`maximum_positions` remains 6. Equity V3, crypto strategy parameters,
rebalance-band parameter, gross/sleeve limits, and PAPER/LIVE modes remain
unchanged. No cap-eight test or migration research was run.

## Next action and files

`REBUILD_AND_VALIDATE_SHARED_ACCOUNT_REPLAY_V2_FROM_CORRECTED_MAIN`.
The code changes are `src/parayabuck/execution/portfolio.py` and
`src/parayabuck/portfolio/runtime_service.py`; tests are
`tests/test_global_slot_accounting.py` and
`tests/test_portfolio_runtime.py`. This directory contains the closure,
policy, call graph, crypto band, reproduction, root-cause, invariant,
regression, same-class, replay, and decision evidence.
