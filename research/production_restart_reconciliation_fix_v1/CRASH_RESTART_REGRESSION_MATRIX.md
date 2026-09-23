# Crash and restart regression matrix

All tests use synthetic broker state and isolated SQLite/temp state; no Alpaca request or order submission occurs.

| Window | Broker truth at restart | Recovery / evidence |
| --- | --- | --- |
| C2 submit sent, ack not stored | local submitting, broker absent or accepted | Existing `test_restart_crash_after_submitting_never_reached_broker_recovers` and submission lookup tests preserve deterministic client ID. |
| C3 accepted working | OPEN order with original client ID | `test_cancel_reject_and_working_order_states`, `test_restart_with_open_order_gates_submission`; reservation remains. |
| C4 partial BUY | OPEN partially filled | `test_partial_buy_fill_while_offline_retains_order`; cumulative 0.4 and nonterminal reservation. |
| C5 full BUY | terminal lookup, OPEN absent | `test_full_buy_fill_while_offline_reconciles_before_drift`; ownership updates before drift. |
| C6 cancel sent | pending cancel or terminal cancel | `test_cancel_reject_and_working_order_states`; pending state stays working. |
| C7 rejection | terminal rejected | `test_cancel_reject_and_working_order_states`; no inferred fill. |
| C8 partial SELL | OPEN partially filled | `test_sell_partial_and_full_fill_while_offline`; remaining ownership 0.6. |
| C9 full SELL | terminal lookup, OPEN absent | `test_sell_partial_and_full_fill_while_offline`; ownership reaches zero. |

The existing `test_restart_chaos.py`, `test_two_phase_rebalance.py`, slot, risk, and startup suites cover durable reservations, two-phase restart, duplicate update idempotency, persistent incident restore, first-decision gating, and next-intent behavior. The new tests also cover lookup uncertainty, identity mismatch, malformed OPEN identities, provider failure, nonfinite fill, repeated restart, unexplained drift, FILL pagination boundaries, overlap deduplication, later-page failure, and 3+4+3 partial fills.
