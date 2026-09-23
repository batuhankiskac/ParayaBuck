# Shared-account replay V2 decision

`SHARED_ACCOUNT_REPLAY_V2_BLOCKED`

The engine's synthetic account and current cap-six slot rules pass isolated controls and focused tests. Physical market-data availability is not established for the merged daily-bar clock. This can affect which incumbent receives a scarce account slot, so V2 cannot be used for a future position-cap comparison.

# Starting corrected main SHA

`3f97c02826bc42eda8197dbb7b605d299915155a`; local and remote main matched and the worktree was clean before branch creation. `CORRECTED_MAIN_RECEIPT.json` records the unchanged config and repair.

# Prior replay V1 provenance

The archived annotated tag `archive/research/shared-account-replay-v1-blocked-2026-09` peels to `1c1c21ef1631fd426b4e90188ac446db4eaa0fd2`. Its coordinator and isolated-control harness were reviewed against corrected current production policy. The old combined outcome was not accepted as V2 evidence. `REPLAY_V1_PROVENANCE_RECEIPT.json` names reused components and frozen control hashes.

# Global slot-accounting repair receipt

Current production `reserve_opening_order_slots` counts nonzero holdings and opening reservations, admits new buys sequentially, and never frees capacity on a requested exit. V2 adapts synthetic orders to this helper after each decision and at deferred-buy activation. The configured cap remains six.

# FILL-activity relevance

`FILL_PAGINATION_IRRELEVANT_TO_SHARED_REPLAY_V2`: flat all-cash start; no pending orders, broker ledger, Alpaca activity history, broker API or live account state; synthetic fills only. See `FILL_ACTIVITY_RELEVANCE_AUDIT.md`.

# Replay implementation architecture

Research-only `replay.py` wraps canonical offline per-market engines around one `SharedAccount` with asset-aware inventory, one cash ledger, one risk snapshot, and global pending-order claims. `run_historical.py` pins inputs and controls, verifies isolated receipts against source hashes, and runs a memory-bounded repeat comparison. Only current configured V3 equity and low-variance hysteresis crypto are accepted for historical runs.

# Starting synthetic account state

NAV and cash each start at 100000. Position inventory and working orders start empty. No broker or strategy runtime state is imported. Each strategy builds its own historical causal state from supplied bars under its canonical isolated convention.

# Cross-market event clock

Sorted union of timezone-aware equity and crypto bar labels. Equity has actual supplied NYSE sessions; crypto has 24/7 complete daily buckets. No missing equity holiday/weekend bar is fabricated. On a crypto-only event the last equity mark persists. **Limitation:** equity full-session data are labeled at NY midnight and full crypto daily data at UTC bucket start. These are earlier than physical completion. See `CLOCK_AVAILABILITY_AUDIT_V2.md`.

# Same-timestamp ordering

Prior sells; deferred/working buys only after sells terminal; marks/risk update; crypto decision then equity decision; current slot filter; account mark. Symbols sort lexically within market. This is deterministic, but physical cross-market action priority is not proved by the source labels.

# Shared cash and ledger

One spendable cash balance receives sale proceeds and equity dividends and pays buys and fees. NAV equals cash plus marked inventory. The canonical offline fill engine enforces available cash; live broker non-marginable buying power and async refresh are outside historical inputs.

# Corrected global slot semantics

Positive holdings, pending openings, held adds, pending/partial/full exits, cancellation and two competing openings are covered by cap-six tests. The production helper is called through a typed adapter. Final position count and effective occupied/reserved claims are separately measured.

# Pending reservations

A new opening is reserved before fill. A fill changes the same asset-aware key from pending to occupied; cancellation releases once. A pending or partial SELL leaves the positive position occupied. Deferred buys are re-filtered when sells become terminal.

# Crypto rebalance-band parity

Current config remains 0.005. Canonical offline planning suppresses small exits; a suppressed LTC-style exit leaves its held slot occupied, and BTC/BCH replacements are denied when all six slots remain claimed. No parameter change was made.

# Equity execution parity

Current V3 weekly decision, canonical PIT universe and full-session bars, next actual bar open, costs, splits/dividends and terminal lifecycles are reused. Isolated control: 495 decisions, 2054 fills, seven parity check groups PASS at 1e-12 metric tolerance. This parity is to the canonical isolated *label-time* convention, not physical availability.

# Crypto execution parity

Current 22-day variance/top-two/96-hour hysteresis, complete hourly-derived daily signals, configured band, canonical offline fill price/cost and synthetic cash are reused. Isolated control: 455 decisions, 388 fills; all twelve check groups PASS, numeric normalization exact.

# Mixed mark-to-market

At each merged label timestamp, new bars update their market only; closed equity retains the last supplied close while crypto trades. Valuation does not invent weekend equity prices.

# Corporate actions

Canonical equity split/dividend/terminal methods run once on equity input only. Dividends enter shared cash. Crypto receives no equity corporate actions.

# Equity isolated parity

`EQUITY_ISOLATED_PARITY_V2.json`: PASS; source and control hashes recorded.

# Crypto isolated parity

`CRYPTO_ISOLATED_PARITY_V2.json`: PASS; source and control hashes recorded.

# Production-semantics mapping

`PRODUCTION_SEMANTICS_MAPPING_V2.md` separates exact synthetic/canonical offline account and slot policy from approximate broker timing, buying power, daily risk epoch and physical data availability. The latter is material for slot priority.

# Combined cap-6 correctness run

Accounting-only receipt `COMBINED_CAP6_INVARIANTS_V2.json`: PASS at current cap six. `over_cap_timestamp_count = 0`; maximum actual nonzero positions = 6; maximum effective occupied plus reserved slot claims = 6. Target gross maximum = 0.550000; realized marked gross maximum = 0.589767; both below configured 0.60. These are mechanical constraint checks, not investment performance.

No combined return, CAGR, Sharpe, Sortino, drawdown, alpha or diversification interpretation was produced.

# Accounting invariants

Cash reconciliation error = 2.91e-11; position quantity error = 0. 2138 reservations created and 2138 released. 2364 fills; zero unowned fills or duplicate fill applications. All receipt checks passed. The `no_future_data_access` field checks only source-label ordering across 4352 decision inspections; it does **not** establish physical bar availability.

# Determinism

`DETERMINISM_RECEIPT_V2.json`: PASS. Two independent event streams each hash to `81cc6daf9e978d1a0d23f77c4d52504f057d78e35af9cdef146e477976383e54` over 11023134 events. NAV path, cash path, final position/cash/NAV state, source identities and final-state hashes match exactly. No combined investment metric is interpreted.

# Known limitations

The unproven physical availability of complete equity and crypto daily bars is the blocking limitation. Broker async/reject/buying-power behavior, combined daily-risk epoch and historical tradability metadata are additional recorded approximations. See `REPLAY_LIMITATIONS_V2.md`.

# Generic correctness findings

No new generic PAPER/LIVE correctness issue was established. The identified clock problem is in historical replay semantics; production code was not modified.

# Migration readiness

`MIGRATION_REPLAY_NOT_READY`

# Durable replay infrastructure decision

V2 is not validated, so no replay implementation is merged into main. Keep the research branch with source, tests, controls and receipts for the clock repair. Do not use the current label-time combined output for migration economics.

# Production state

`maximum_positions = 6`; equity V3, crypto parameters, rebalance band 0.005, gross/sleeves and PAPER/LIVE settings remain unchanged.

# Final main SHA

`3f97c02826bc42eda8197dbb7b605d299915155a` (unchanged).

# Final research branch SHA

Recorded in the final delivery after committing this report; a commit cannot contain its own SHA.

# Exact next action

Build a physical availability/execution clock from completed hourly crypto bars and NYSE session close/next-open times, including DST/half-days/holidays; re-establish isolated controls and rerun cap-six combined invariants and determinism. Keep migration economics closed until that proof passes.
