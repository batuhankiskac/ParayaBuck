# Reconciliation repair verification

The accepted fix branch at `5229a061b1d5921fc4dffef8c9bb54393b3dd833` was diffed against clean production main `af7225e1952c20a81f7d22d4f8d98126f02e27b3`. Production edits are confined to Alpaca reconciliation, the durable portfolio order DTO/repository, the recovery callback and the working-status submit guard. No strategy, allocation, risk threshold, cap, migration or replay production code changed.

Three new RR-01/RR-02 probes run against old main failed as expected: terminal offline BUY drift, partial offline BUY ownership, and repeated FILL page falsely succeeding. The corrected fix branch passed 64 focused reconciliation/restart/two-phase tests. After local main integration, 70 focused reconciliation, pagination, slot, startup and risk tests passed.

Code inspection confirms account identity is bound before account-scoped durable reads; OPEN orders are fetched exhaustively; locally nonterminal rows are matched against OPEN or targeted client/broker ID lookup; missing OPEN alone is not terminal evidence; broker status/cumulative fills are durably applied before position ownership/drift classification. FILL pages are staged, validated, deduplicated by activity ID and exhausted before application/watermark. Invalid pages or later failures make startup reconciliation raise. `EngineRuntime._start_impl()` still rejects drift or exceptions before portfolio runtimes and first decisions. Unknown positions/orders remain drift. The repository uses cumulative updates and deterministic client IDs for idempotency. Existing risk incident restore, current account cash and slot tests were included in the focused gate.

Internal decision: `RECONCILIATION_REPAIR_VERIFIED`.
