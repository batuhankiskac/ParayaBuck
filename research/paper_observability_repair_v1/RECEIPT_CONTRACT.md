# PAPER decision receipt V2

One immutable JSON receipt is written for each persisted equity or crypto decision, scheduled or startup catch-up. Identity is decision ID plus timestamp, market and origin. `target_state` and `planned_orders` are explicitly intent. `submission_snapshot` is a read at receipt-write time, not final fill truth. SQLite `portfolio_decisions`, `portfolio_orders`, `portfolio_fills` and `portfolio_execution_events` are authoritative for lifecycle. `portfolio_buy_admission_observations` records the final direct or deferred BUY gate, linked by client order ID. No original receipt is rewritten after execution.

V2 is emitted only in PAPER mode. A failed receipt write emits `paper_incident` while trading remains on its existing safety path. The auditor then fails S10; the affected forward observation is invalid. No receipt may claim a fill, post-fill position or resulting account balance by projecting target weights or intended orders.
