# Final audit decision

**PRODUCTION_RECONCILIATION_FIX_REQUIRED**

## Starting production main SHA

\`af7225e1952c20a81f7d22d4f8d98126f02e27b3\` (local \`main\` and local \`origin/main\` tracking ref). Direct \`git ls-remote\` confirmation failed because GitHub DNS was unavailable; the audit is pinned to the verified local durable main, not the parallel research branch. Initial worktree/branch inventory is in \`STARTING_STATE.json\`.

## Production restart flow

Web engine manager → \`EngineRuntime\` → SQLite ledger/repository and Alpaca broker → broker account/positions/FILL/OPEN reconciliation → drift gate → portfolio runtimes/decision watermarks → incidents/risk state → workers/warmup → fresh broker account and risk persistence → persisted rebalance resume → startup catch-up. Exact files/functions and ordering: \`PRODUCTION_RESTART_CALL_GRAPH.md\`.

## State authority model

Broker supplies quantities, average entry price, cash/equity, and OPEN order existence. Local durable state supplies strategy ownership, decisions, phase/order status, incidents, and peak history. Reconciliation compares rather than blindly overwrites. Broker-only/manual holdings are external/immutable to strategy but count for gross and slots. See \`STATE_AUTHORITY_MATRIX.md\` and \`POSITION_RECONCILIATION_AUDIT.md\`.

## Open-order completeness

**OPEN_ORDER_RECONCILIATION_COMPLETE** for stable Alpaca pages. The 500-order order-ID cursor helper is used by startup and planning, deduplicates, and raises on partial-page errors. [Alpaca order-list specification](https://docs.alpaca.markets/us/v1.4.2/reference/getallorders-1).

## FILL activity pagination

**FILL_ACTIVITY_PAGINATION_INCOMPLETE_CONFIRMED** for an invalid/nonadvancing page. The normal 100-item activity-ID pagination follows [Alpaca's Trading API contract](https://docs.alpaca.markets/us/reference/getaccountactivities-2). A repeated full page or non-list response instead reports success. Material paths are ledger fill/fee/realized-P&L recovery and stale-ownership closure, not direct portfolio order duplication. See \`FILL_ACTIVITY_PAGINATION_AUDIT.md\`.

## Client-order-id / idempotency and crash windows

Portfolio IDs are deterministic by durable decision identity; row persistence precedes broker submission. Timeout recovery and \`submitting\` phase recovery query the same client ID. C2/C3 working orders are not blindly resent. The C5 full fill after prior \`submitted\` status is the recovery gap. See \`IDEMPOTENCY_AUDIT.md\` and \`CRASH_SCENARIO_MATRIX.md\`.

## Partial fills and cancel/fill race

Broker positions and remaining OPEN orders plus nonterminal local rows retain slot reservations. A missed stream fill can cause startup drift and halt. Cancel request is not interpreted as terminal in the slot model; broker \`pending_cancel\` remains working. Terminal status lost during downtime may still wedge startup. See \`PARTIAL_FILL_AUDIT.md\`, \`CANCEL_FILL_RACE_AUDIT.md\`, and [Alpaca statuses](https://docs.alpaca.markets/us/docs/orders-at-alpaca).

## Broker positions, manual holdings, slots, and cash

Unknown/manual positions are externally owned; current config has \`manage_external_positions=false\`. They count toward gross and global slots; broker-only state also creates drift and prevents startup decisions. Cash, equity, and non-marginable power are freshly broker-fetched before startup decisions. No stale local cash planning path was found. Pending exits and partials do not free slots. See \`POSITION_RECONCILIATION_AUDIT.md\`, \`GLOBAL_SLOT_RECONSTRUCTION_AUDIT.md\`.

## Risk and strategy state restart

Peak/epoch and incidents persist; the same-day loss counter and decision watermarks restore. Crypto held-name hysteresis recomputes from current holdings/ranks and 96-hour cadence from durable decision time. Equity cadence and rebalance phase are persisted. A corrupt risk JSON row is ignored, while incident restore remains separate. See \`RISK_STATE_RESTART_AUDIT.md\` and \`STRATEGY_STATE_RESTART_AUDIT.md\`.

## Startup ordering and failure-mode policy

Portfolio decisions follow reconciliation, warmup, a fresh account fetch, and risk persistence. Account/positions/OPEN failures stop startup; FILL request failures are deferred, but malformed/nonadvancing successful responses are incorrectly considered complete. See \`STARTUP_ORDER_AUDIT.md\`, \`FAILURE_POLICY_AUDIT.md\`.

## Restart determinism

Existing restart suites plus four audit-only probes cover representative crash states. No live broker or exhaustive event interleaving was run. C5 differs from uninterrupted execution by halting before local terminal fill recovery; no duplicate order was observed. The precise scenario matrix records C1-C12.

## Confirmed issues

| ID | Issue | Severity | Production path | Reproduction | Fix required? |
|---|---|---|---|---|---|
| RR-01 | Filled/terminal broker order can remain locally nonterminal; startup halts before generic terminal recovery | MEDIUM | \`alpaca_reconciliation.py:597-617\` → \`runtime.py:910-947\` | \`test_filled_buy_without_trade_update_halts_before_phase_recovery\` | Yes, separate campaign |
| RR-02 | Invalid/nonadvancing FILL page returns success and may advance watermark | MEDIUM | \`alpaca_reconciliation.py:142-168,283\` | Two audit tests for repeated full page and non-list response | Yes, separate campaign |

No unintended duplicate exposure or risk-latch bypass was reproduced. Severity is limited accordingly.

## Same-bug-class findings

See \`SAME_BUG_CLASS_AUDIT.md\`: OPEN list and slot paths are safe under tested contracts; FILL false completeness and terminal-local order recovery require repair.

## Production files changed

**NONE**

## Account-capacity / migration files changed

**NONE**

## Validation

Audit-only synthetic probes: **4 passed**. Focused restart/slot/loss suites: **161 passed**. OPEN pagination suite: **18 passed**. Full suite in restricted sandbox: **1165 passed, 4 skipped, 6 failed solely on denied 127.0.0.1 socket bind**. The six socket tests were rerun with local-loopback permission: **6 passed**. Thus all executed test cases passed in an environment supporting their required local socket. Ruff check/format, strict mypy, JSON, diff whitespace, and credential scan receipts are recorded in \`VALIDATION.md\`. No real broker API calls, PAPER/LIVE actions, or production code edits.

## Exact next action

**ISOLATE_PRODUCTION_RECONCILIATION_CORRECTNESS_REPAIR**. Use \`NEXT_TASK_HANDOFF.md\`; fix in a separate branch after resolving parallel-work isolation.

## Final audit branch SHA

See the HEAD commit of \`audit/production-restart-reconciliation-v1\` (recorded in final task response).
