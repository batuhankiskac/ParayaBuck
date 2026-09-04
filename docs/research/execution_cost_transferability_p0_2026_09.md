# Execution-cost transferability P0 — measurement pipeline

**Status:** planned technical measurement pipeline
**Economic verdict:** `UNKNOWN_COST_TRANSFERABILITY`
**Promotion:** none

## Implementation receipt

The focused offline gate passed 149 tests; repository-wide Ruff and strict
mypy passed.  The full suite reached `1670 passed, 11 skipped, 13 failed, 1
error`.  The failures are outside this work's focused paths: loopback socket
binds are denied by the sandbox, existing M3 manifest tests require a clean
worktree, and an existing web/backtest test escaped its isolation and changed
the ignored runtime SQLite file.  No attempt was made to overwrite or restore
that runtime state.  See the compact
[technical receipt](../../data/research_receipts/execution_cost_transferability_p0_2026_09.json).

## Purpose and non-goals

This record establishes an offline, read-only measurement path for comparing
PAPER execution records with the existing backtest execution schedule.  It
does not change a cost assumption, fit or select a model, place an order, or
make PAPER or LIVE eligible for promotion.

## Resolved schedule interpretation

The original brief said that every residual tranche must use one 20-bar
liquidity estimate containing only observations known at the original
decision.  Repository code instead creates a residual order at each partial
fill and uses positive-volume bars known at that residual order's
`created_at`.  The fill bar itself remains excluded.  This is the
authoritative benchmark for P0-B: mirror the current dynamic
residual-capacity behavior exactly.  **Execution-model change: none.**  The
frozen-decision-liquidity alternative is out of scope and would require a
separate predeclared candidate research record.

## Phase A design and invariants

`portfolio_execution_events` is an additive, append-only raw broker-event
journal.  Its source-scoped immutable identity makes a same-source replay
idempotent.  It retains broker cumulative quantity, cumulative VWAP,
timestamps, and nullable fee evidence before any ownership conversion.

- `portfolio_orders.cumulative_filled_quantity` remains authoritative net
  ownership bookkeeping, including the crypto fee reserve.
- Raw gross execution is never overwritten by a net ownership value.
- Incremental fill notional is `Q_t * VWAP_t - Q_previous * VWAP_previous`;
  invalid, duplicate, missing-price, decreasing, or out-of-order sequences
  are retained with an explicit quality state and never create negative fill.
- Fee absence is unknown, not zero.  An observed zero is distinct from an
  unknown fee.  A matching reconciled activity takes precedence over stream
  fee evidence and is not double counted.
- Persisted order time, submit-call start/return, broker event, local receipt,
  first/last fill, and terminal time retain their distinct meanings.

## Phase B benchmark

The comparator opens the runtime DB in SQLite read-only mode and requires a
local executable-bar input.  For each eligible order it reproduces the
engine's next-eligible-open schedule, dynamic residual capacity, 3-bps
adverse price adjustment, crypto taker fee, effective-dated equity fees, and
SELL-before-BUY block ordering.  It reports price alignment separately from
known-fee total cost; unknown fees leave total-cost transferability unknown.
Equity and crypto never share a primary verdict.

Golden fixtures prove initial decision-time capacity, residual-boundary
capacity refresh, exclusion of current fill-bar volume, decision
supersession, restart equivalence, and per-tranche parity with
`PortfolioBacktestEngine`.

## Evidence boundary

An accepted implementation result validates only this measurement pipeline.
It does not validate the configured 3-bps assumption or establish PAPER
transferability without a future predeclared sample that satisfies its own
coverage and measurement-quality rules.
