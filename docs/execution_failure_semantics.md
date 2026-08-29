# Failure Semantics — Execution Boundary (Mission 7)

This table is the contract for `src/parayabuck/execution/alpaca.py` failure handling. It is referenced by `docs/alpaca_broker_truth_matrix.md` and `docs/mission7_execution_boundary_report.md`.

## Core Rule

When broker truth is uncertain: **DO NOT GUESS**. Prefer `AmbiguousOrderState` / `OwnershipDriftError` / `RiskStatePersistenceError` / `BLOCKED` reconciliation → entry halt / manual intervention over silent retry.

## Table

| Condition | Exception / State | Retry? | Entry halt? | Manual intervention? | Test |
|---|---|---|---|---|---|
| `submit_order` timeout before broker accept (no order created) | `TimeoutError` → `AmbiguousOrderState` after lookup fails | No | Yes (block) | Maybe (ops timeline) | `test_submit_ambiguous_when_lookup_fails` |
| `submit_order` timeout after broker accept (order exists, lookup finds) | Recovered `broker_id`, `persist/link` | No (recovered) | No | No | `test_submit_proven_accepted_via_lookup` |
| `submit_order` 429/500/400 with no order | `RuntimeError` / broker `rejected` | According to existing semantics (no duplicate if ambiguous) | Depends | No | `test_submit_proven_rejected_when_broker_proves_absence` |
| `submit_order` response malformed / missing ID | `RuntimeError` fail-closed, no ID fabricated | No | Yes | No | characterization |
| `get_account` / `get_positions` / `get_orders` failure | `RuntimeError` / `RiskState`? No partial truth presented as complete | No | Yes | No | `test_query_faults` |
| `cancel_order_by_id` 404 / filled during cancel | Verify terminal state, not proven canceled → `AmbiguousOrderState` | No | Yes | Yes | `test_cancel_unconfirmed_is_ambiguous` |
| `cancel_order_by_id` timeout / 500 | `AmbiguousOrderState` | No | Yes | Yes | `test_cancel_*` |
| Order status unknown future SDK value | `UNKNOWN` / `ambiguous`, never `filled`/`canceled` | No | Yes | No | `test_unknown_broker_status` |
| Dual ownership: ledger>0 AND repo>0 for same symbol | `OwnershipDriftError` (fail-closed) | No | Yes | Yes | `test_dual_ownership_lineage` |
| Broker shortfall: repo qty > broker qty, no sufficient SELL | `OwnershipDriftError` | No | Yes | Yes | `test_broker_shortfall_*` |
| Broker shortfall with verified working SELL sufficient | Exempted, no error | No | No | No | `test_working_managed_sell_explains_shortfall` |
| Manual BUY/SELL or unrelated open order | Must NOT suppress drift | No | Yes | Yes | `test_manual_does_not_explain_shortfall` |
| Protection missing / underprotected | `ProtectionReconciliationError` / `re-protect` remainder | No | No (re-protect) | No | `test_protection_*` |
| Protection overprotected | Cancel/replace to required qty | No | No | No | `test_overprotection` |
| HELD nested bracket stop (open-orders shows take-profit, stop HELD) | Must detect nested `parent_order_id` → do NOT submit duplicate | No | No | No | `test_held_bracket_stop` |
| Crypto `cancel_order_by_id` returned but not terminal | Poll 5s/0.25s, verify `canceled`/`filled` else `ambiguous` | No | Yes | Yes | `test_crypto_cancel_is_not_confirmation` |
| Crypto partial fill + cancel remainder timeout | Verify, protect remainder, no duplicate exit, no unprotected | No | Maybe | Maybe | `test_partial_crypto_exit` |
| Crypto exit ambiguous / emergency exit | `AmbiguousOrderState` / `manual intervention` | No | Yes | Yes | `test_emergency_exit` |
| Risk sidecar malformed / non-finite / negative / invalid epoch / unreadable | `RiskStatePersistenceError` fail-closed | No | Yes | Yes | `test_peak_malformed_*` |
| Risk sidecar missing | Bootstrap 0.0, distinguishable from corrupt | No | No | No | `test_peak_missing_bootstrap` |
| Account identity change after peak bound | `RuntimeError` fail-closed | No | Yes | Yes | `test_peak_account_scoped_*` |

## Retryability

* **Transient network** (`TimeoutError`, `HTTP 429/500`, `get_account` failure): retry only where broker proof of absence exists; otherwise `AmbiguousOrderState`.
* **Permanent broker rejection** (`HTTP 400`, `rejected` status): no retry, surfaced.
* **Execution ambiguity** (`AmbiguousOrderState`): block/reconcile, never blind resubmit.
* **Ownership/protection drift**: entry halt, manual reconcile.
* **Risk-state persistence**: entry halt, operator fixes file.

## Correlation

Every high-value execution event includes `client_order_id` / `broker_order_id` / `symbol` / `market` where available, never credentials, isolated from journal failures.
