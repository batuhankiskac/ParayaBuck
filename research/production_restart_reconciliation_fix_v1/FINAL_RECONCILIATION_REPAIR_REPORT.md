# Production restart reconciliation repair

**Correctness decision: `PRODUCTION_RECONCILIATION_REPAIR_ACCEPTED`**

Starting durable production main: `af7225e1952c20a81f7d22d4f8d98126f02e27b3`. Audit provenance: branch `audit/production-restart-reconciliation-v1` at `3551322b9ac609ed2ee52c0343a14f2dcd408f37`, with local annotated tag `archive/audit/production-restart-reconciliation-v1-2026-09`. The audit changed **no production files** and **no account-capacity files**. This fix branch was created from main, never from the audit or parallel research branch.

## RR-01: pre-fix proof and repair

The audit and new red test reproduced a `submitted` local BUY that filled while offline. The terminal broker order disappeared from OPEN; broker position showed the fill; the old reconciliation classified stale local ownership and halted before the later order recovery path. Two other new RR-01 tests also failed before code changes. Reconciliation now binds account identity, fetches exhaustive OPEN orders, matches all local nonterminal rows, and performs targeted client-ID/broker-ID lookup for rows absent from OPEN. It validates identity, contract, status and cumulative fill, then applies the durable update before `account()` derives ownership and drift. A failed lookup, malformed order, unreadable local provider, or unidentified broker OPEN order cannot be treated as empty. Known fills reconcile; unexplained position or order drift remains fatal.

## RR-02: pre-fix proof and repair

The audit and new red test reproduced a repeated FILL page being treated as successful. The fixed path requests only FILL with `direction=desc`, `page_size=100`, and the final activity ID as `page_token`; it requires an explicit empty page. It validates every activity, detects nonadvancing/repeated cursors and no-progress pages, deduplicates by activity ID, and stages all pages before applying. Later-page failure leaves activity application and watermark untouched. Startup raises on incomplete FILL history. The 3+4+3 partial-fill test verifies incremental `qty` sums to 10 rather than summing cumulative quantities. See the official-source contract in `ALPACA_RECONCILIATION_API_CONTRACT.md`.

## Authority, barrier, and restart behavior

Broker state supplies account cash/equity, positions, OPEN and targeted order status. Durable state supplies strategy ownership, order intent, reservations, decisions, risk and strategy checkpoints. The existing EngineRuntime reconciliation gate runs before portfolio runtime construction and startup decisions. The repair reorders the broker/local comparison inside that gate. A second restart does not add a second fill or replacement economic intent. C2–C9, slot reservation, cancel/fill, risk incident and two-phase strategy recovery evidence is catalogued in `CRASH_RESTART_REGRESSION_MATRIX.md`. The direct synthetic filled-BUY A/B comparison passes; broader cash/risk/next-intent dimensions are covered by separate offline suites, as delimited in `RESTART_EQUIVALENCE_RECEIPT.json`.

Actual nonzero broker positions, working opening orders and partial exits retain the existing slot treatment; manual/unmanaged holdings retain their existing risk role. Peak equity, daily loss, drawdown/operator reset, V3 and crypto cadence/hysteresis, target construction and rebalance band were not changed. No strategy, allocation, maximum-position, risk-threshold, migration, replay or cap-8 file changed. Therefore this repair does **not** invalidate ongoing 6-vs-8 historical research. A later production-parity migration review should use the then-current main.

## Validation and scope

Offline full pytest: **1,197 passed, 4 skipped**. Focused reconciliation and crash/restart suites passed. Ruff check and Ruff format check passed for the repository. Strict mypy passed for the changed reconciliation module and new regression file. A broader strict probe across seven touched production files reports 18 pre-existing issues on unchanged lines in four modules; this repair did not expand scope to refactor them. JSON artifacts validated, `git diff --check` passed, and a credential-pattern scan found only an existing literal placeholder in a test fixture, with no new credential. No real Alpaca calls, broker orders, PAPER/LIVE mutations, or network-dependent production tests were performed; the socket fixtures used local loopback only.

Main was **not merged** because the repair was performed in an isolated branch while parallel account-capacity work exists. Main remains `af7225e1952c20a81f7d22d4f8d98126f02e27b3`. Audit archive tag and fix branch are local-only; publishing either ref requires operator approval. Exact next action: **`CLOSE_AND_ARCHIVE_RECONCILIATION_REPAIR`**. Review/publish the isolated fix branch before any controlled merge; do not force an update over parallel work.
