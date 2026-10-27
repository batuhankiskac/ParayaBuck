# Round 4 — independent verification of medium/high bug findings

## Portfolio / execution
- C-2 CONFIRMED — mixed SELL+BUY re-plan drops SELLs (runtime_service.py:2541-2575); not covered by tests.
- C-3 CONFIRMED (narrow) — only when an earlier decision's rebalance resolves during a recovering period-start catch-up; _session_decided blocks the same-session case.
- C-4 CONFIRMED — one exhausted leg closes the rebalance; other legs' residual SELLs never sent.
- D-1 CONFIRMED — P&L booked 0.0 for the late SELL (P&L only; quantities fine).
- D-5 CONFIRMED (comment wrong) — Alpaca docs: done_for_day isn't resumed; terminal classification is right, but the non-regression exemption lets a late working-status event pull a done_for_day row back into open_orders. Fix: remove the exemption + comment.
- G-2 CONFIRMED — reconcile would catch a hidden owned symbol, but on its own 60 s cadence, not before decisions.
- E-1 CONFIRMED — peak sidecar save never retried until a new high.
- E-3 PARTIAL — weight 1.0 at zero vol equals the normal cap at vol ≤ target; needs 21 flat live closes; edge case.
- G-4 CONFIRMED (single snapshot) — null last_equity disables only the daily-loss gate for that snapshot; drawdown gate unaffected.

## Runtime / config / web
- B-1 CONFIRMED — batch dropped under account_fetch_failures; reset only retries "blocked:" catch-ups; no alert on the drop. Silent loss of a session's decision.
- M-2 PARTIAL — journal/snapshot shared PAPER↔LIVE; observability only (decision DB is separated).
- M-1 CONFIRMED (low) — dry run creates file/dir, WAL, DDL; ALTER/UPDATE only on legacy schemas; ownership rows untouched.
- K-3 CONFIRMED — Trading line says ACTIVE; `ops status` also prints Runtime CRITICAL + watchdog line, so misleading not silent.
- L-1 CONFIRMED (low) — LIVE excluded from trading_expected; nothing in systemd/container consumes /readyz except the dead-man timer. Design question.
- I-3 CONFIRMED — hung runtime.start() keeps the watchdog pinger alive (WatchdogSec=300 in unit).
- I-1 PARTIAL — only for a missing settings file; start still needs the live arm.
- I-2 PARTIAL — narrow race (stop between transient raise and lock); then stuck until process restart.

## Monitoring / tax / perf
- K-1 PARTIAL — real ordering defect, but needs ~3 h worker stall (max_per_hour 20, queue 64); the drop itself is journaled.
- K-2 PARTIAL — integrity_check catches corruption; residual false OK only on lock/busy or query error.
- K-4 PARTIAL — no live settings-change path; sink swapped at engine construction/web startup; alert is journaled before the e-mail copy.
- DK-10 CONFIRMED — the fill-side skip+alert was removed with the FILL-activity mirror in d88e6c91 (2026-10-06); current check is order-level fail-closed. docs/operations.md:117, docs/operator_runbook.md:177-181 and a test docstring still describe it as live → stale docs + dead vocabulary.
- N-1 CONFIRMED (medium) — rates-fetch replaces existing Yi-UFE months and relabels every row's source.
- N-3 PARTIAL — dashboard get_tax_ytd_payload wraps in except Exception → "unavailable"; failure uncached (re-runs every poll); docstring false.
- P-B1 PARTIAL — ~0.1 ms per call, ~2 ms/min at 30 symbols. Not a production issue.
- P-I2 CONFIRMED — worse than claimed: account() = 2 REST calls × 15 s timeout (+SDK retries), so one failure can hold the lock ~30 s+.
- P-F5 CONFIRMED, retry safe — _submit_with_recovery looks up by client_order_id after any exception; cost is up to ~9 s sleeps + timeouts.
- P-H1 CONFIRMED — cache must be keyed by closes content, not index (history is a sliding 397-bar window).
