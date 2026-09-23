# Separate repair campaign handoff

Audit branch: \`audit/production-restart-reconciliation-v1\` based on \`main@af7225e1952c20a81f7d22d4f8d98126f02e27b3\`. **Do not implement in this audit branch.** Keep account-capacity work isolated.

## RR-01 — terminal broker order versus local nonterminal row

Root cause: \`ReconciliationService.reconcile\` compares repository nonterminal IDs only to broker **OPEN** IDs (\`src/parayabuck/execution/alpaca_reconciliation.py:597-617\`) and \`EngineRuntime._start_impl\` halts on the resulting missing-order bucket (\`runtime.py:910-947\`) before \`PortfolioRuntime._resolve_stuck_submission_rows\` can query terminal status (\`portfolio/runtime_service.py:1311-1348\`). The latter only handles \`submitting\`, not every previously \`submitted\` row whose broker order became terminal while down.

Minimal reproduction: start with a durable portfolio BUY row at \`submitted\`, broker accepted then fully filled while stream/process was offline, broker now holds the position and returns no OPEN order, local fill/ownership row remains stale. The audit test \`test_filled_buy_without_trade_update_halts_before_phase_recovery\` reproduces broker-stale plus missing-order drift. Actual startup turns those nonempty buckets into a halt.

Required invariant: before declaring a local nonterminal order missing, resolve its **specific client ID** against broker terminal/partial state, book authoritative cumulative fill once at broker average, update ownership/order status durably, then compare broker positions and all remaining OPEN orders. A failed/ambiguous lookup must block new entries; never infer cancellation merely from absence in OPEN list.

Regression tests: submitted BUY full fill while offline; submitted SELL full/partial fill; cancel/late-fill race; rejected/expired terminal order; broker lookup timeout; repeated restart and replay idempotency; mismatching/manual order remains fail closed.

## RR-02 — incomplete FILL page treated as success

Root cause: \`_reconcile_fill_activity_fees\` (\`alpaca_reconciliation.py:142-168\`) converts non-list payloads to empty pages and breaks on a repeated/missing cursor, then processes a partial activity set, advances the watermark, and returns \`True\`. The returned success permits stale ledger closure path (\`alpaca_reconciliation.py:365-380\`). The two audit tests characterize both paths.

Required invariant: any invalid page, nonadvancing cursor, missing ID on a full page, or pagination loop must be an explicit incomplete result with no success watermark. Preserve already durable deduplication, avoid processing partial scans as a complete recovery, and keep startup/ownership policy fail closed for required fill evidence.

Regression tests: 100+ FILL pages with exact cursor sequence; repeated full page; missing last ID; non-list page; later-page exception; partial/full activity dedup; fee/realized-P&L recovery; restart after long outage; no broker network in unit tests.

Both issues are **MEDIUM** on demonstrated paths: standard downtime can block automatic recovery, and incomplete activity evidence can be marked complete. No demonstrated duplicate exposure or risk-latch bypass justifies HIGH/CRITICAL.
