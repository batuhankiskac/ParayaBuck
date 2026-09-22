# Shared-account replay protocol status — V2

Status: `NOT_FROZEN_NO_RUN`

No economic migration specification was frozen and neither arm was run. The
study stopped at the shared-account parity gate because corrected main has no
historical replay that combines the equity and crypto incumbents on one evolving
account ledger. This document records the conditions a future replay adapter
must satisfy; it is not authority to generate migration outcomes.

## Required account model

Use one deterministic account initialized with configured cash, no positions,
and no working orders. Keep one cash balance, position inventory, gross
exposure, global position count, and pending-order ledger across both markets.
Keep configured equity and crypto sleeves, per-name limits, crypto position
count, cash buffer, gross cap, and all other risk limits fixed. A reserved
position slot must be attached to an actual planned BUY or a persisted working
order, following current production semantics.

## Production semantics that must be represented

- `EngineRuntime` processes simultaneous portfolio decisions crypto first and
  passes one account snapshot plus earlier actual BUY position keys to later
  markets (`src/parayabuck/runtime.py:3073-3120,3227-3240`). Events at different
  observable times must remain chronological.
- The shared risk overlay counts cross-market positions, prior-cycle
  reservations, broker-open-order symbols, and current-market fixed positions
  against the account-wide cap (`src/parayabuck/execution/portfolio.py:265-337`).
- The account snapshot is shared during a production processing cycle; slot
  reservations carry position keys, not dollar notional
  (`src/parayabuck/runtime.py:3106-3113,3227-3240`).
- Production persists SELL-first rebalance phases. A pure BUY decision submits
  immediately; deferred BUYs follow completed SELLs and are resized against a
  fresh cash/gross snapshot (`src/parayabuck/portfolio/runtime_service.py:1135-1207,1438-1455,1526-1570`).
- Working account-local orders continue to reserve position slots; broker-wide
  open-order symbols prevent duplicate planning
  (`src/parayabuck/portfolio/runtime_service.py:1473-1524`).

## Required replay proof before a freeze

The adapter must show, with deterministic synthetic fixtures, simultaneous and
asynchronous equity/crypto decisions, same-cycle tie ordering, shared slot
reservation, pending BUY and SELL claims, partial and rejected/cancelled orders,
SELL-to-BUY transitions, cash sufficiency, gross and sleeve limits, fees,
realized P&L, mark-to-market, and repeated-run identity. It must compare
independently reconstructed cash, fills, fees, exposure, and positions against
the replay ledger. It must not blend separate single-market NAV curves.

The existing single-market product backtest does not meet these conditions.
Until a research-only adapter passes the parity audit, no A/B outcomes may be
generated.
