# Execution-cost transferability P0 — measurement pipeline

**Status:** accepted technical measurement record
**Economic verdict:** `UNKNOWN_COST_TRANSFERABILITY`
**Promotion:** none

## Technical acceptance receipt

The focused P0 selection passed **154 tests**. In disposable worktrees using
the same interpreter, the base (`0a47aba`) completed `1662 passed, 15 skipped,
6 failed` and the verified P0-C source (`c3d65d7`) completed `1678 passed, 15
skipped, 6 failed`. The six failures are identical loopback bind attempts, all
denied by this sandbox with `PermissionError: [Errno 1] Operation not
permitted`; there are no added, removed, or non-sandbox failures. The
production-isolation record/verify gate passed in both worktrees. Repository
Ruff, Ruff format, and strict mypy passed at P0-C source.

The committed P0 implementation identities are
`491ffa12e7460fe5353d3dd544a8a33e78ce61b6` (raw evidence) and
`140c3def3267e683e20c449314d8bfab01c2429b` (shadow comparator). The human
design brief predates implementation; that is distinct from the machine
manifest's first commit timestamp (`2026-09-04T16:40:58+03:00`). See the
compact [technical receipt](../../data/research_receipts/execution_cost_transferability_p0_2026_09.json).

The machine manifest was committed before a future PAPER economic sample, but
not before P0's technical result: both are recorded explicitly. The result
recording commit is `c0e167b2df8fc3cb2f5e4e4843a40d480fa56151`; this later
metadata clarification is not self-referential.

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
- Fee absence is unknown, not zero. An observed zero is distinct from an
  unknown fee. Fee evidence is event-local only: cross-source fee activity
  reconciliation and precedence are future work, and unknown fees block an
  actual total-cost comparison.
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

Golden fixtures invoke the real `PortfolioBacktestEngine` event loop and
compare every tranche with the comparator: timestamp, quantity, residual,
reference open, capacity boundary, known average volume, modeled price, and
modeled fee. They additionally reject mixed timeframes and conflicting
duplicates, derive supersession from every later same-market decision
including zero-order decisions, and exclude invalid/decreasing/missing-VWAP
raw-event sequences from price comparison. Only `first_fill` and
`valid_incremental` observations establish a final gross quantity/VWAP;
`status_only` and duplicate zero-delta observations are benign non-fills; all
other quality states are explicit exclusions. Total-cost eligibility remains
false until fee reconciliation exists.

## Evidence boundary

An accepted technical result validates only this measurement pipeline.
It does not validate the configured 3-bps assumption or establish PAPER
transferability without a future predeclared sample that satisfies its own
coverage and measurement-quality rules.
