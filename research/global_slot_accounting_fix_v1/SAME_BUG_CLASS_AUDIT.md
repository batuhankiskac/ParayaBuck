# Same bug class audit

| Path | Classification | Basis |
|---|---|---|
| Equity replacement | SAFE after patch | Uses the same overlay, planner and runtime admission helper. A band/minimum-suppressed SELL leaves its holding counted. |
| Hedge replacement | NOT_APPLICABLE | No separate account-order hedge replacement planner was found; held hedge/reference symbols flow through the same account snapshot and key logic. |
| Startup catch-up | SAFE after patch | Delegates to `process_closed_bars()` with same-cycle reservations. |
| Pending-order restoration | SAFE after patch | `_submit_deferred_buys()` uses a refreshed broker account, nonterminal local rows, broker OPEN symbols and the admission helper. |
| Rejected/canceled opening | SAFE after patch | Terminal repository rows cease reserving; broker OPEN symbols remain claims until no longer reported. Nonzero partial fills remain broker positions. |

The audit is scoped to optimistic exit release and duplicate final-slot use.
It does not assert broker snapshot atomicity beyond the existing lease,
idempotency and reconciliation controls.
