# Final cap-8 production parity report

## Production parity decision

`CAP8_PRODUCTION_PARITY_BLOCKED`

## Gross policy decision

`REALIZED_GROSS_DRIFT_CONSISTENT_WITH_EXISTING_POLICY`

Gross classification: `MAXIMUM_GROSS_IS_TARGET_LIMIT`.

## Authorities and archive

Starting production main SHA: `af7225e1952c20a81f7d22d4f8d98126f02e27b3`. Frozen migration V3 branch final SHA: `9c5e61382e8cdb71fdd3442d945719024accedd9`; independent verifier `PASS`; research decision `ACCOUNT_ARCHITECTURE_MIGRATION_8_ACCEPTED`. The local annotated tag `archive/research/account-capacity-migration-8-v3-accepted-2026-09` peels to that exact commit. The tag and audit branch are not pushed. The research branch has no `src/` or `config/` diff from its comparison main. Global count denials were 1,112 at cap 6 and 438 at cap 8. This is a structural research result, not deployment authorization.

## Existing risk contract and defect

`docs/research/canonical_v3_baseline.md:35-68` defines target gross and realized marked gross separately. Target/order-caused gross must remain at or below 0.60, cash target at or above 0.40, and each BUY submission/re-plan must use current account headroom. No continuous liquidation of mark drift is required. Gross is marked absolute position value divided by account equity (`src/parayabuck/core/models.py:197-198`). The overlay computes a compliant expected target, but the pure-BUY path submits planned orders without the fresh gross/cash sizing used by deferred BUYs (`src/parayabuck/portfolio/runtime_service.py:1180-1209,1399-1453,1543-1609`). The 0.005 rebalance band can retain small excesses in other names while permitting a larger BUY. This is an actual order-caused policy breach, distinct from accepted passive mark drift.

Five no-network synthetic cases in `tests/test_cap8_production_parity_audit.py` show: at realized gross 0.605, cap 8 admits a $1,900 new-symbol BUY and a $1,500 add-to-held BUY; a SELL submits; a SELL+BUY replacement defers BUY; an external position consumes a slot and gross and cap 6 denies the opening. The two immediate BUYs increase already excessive broker gross despite `risk.gross_after <= 0.60`. The test uses production-like equity allocation 0.55, crypto count 2, gross 0.60 and cash 0.40. The source code and tests do not establish a safe no-headroom BUY branch.

## Frozen excursions and control

The frozen trace analysis in `GROSS_EXCURSION_ANALYSIS.json` defines an episode as consecutive observed marks above 0.60 and lists the first and last timestamp of all 135 cap-8 episodes. Cap 8 had 9,152/50,194 over-limit marks and peak 0.6155074542; median excess was 0.00313036 and p95 excess 0.00985634 of equity. Observed first-to-last episode span was median 46 hours, p95 147.2 hours, maximum 185 hours. The left-held observation-grid integral was 33.010891 gross-fraction-hours over 87,192 observed hours; it is not an estimate of continuous intramark exposure. Seventy-four episodes started on the first mark after an observed fill; 61 began with no fill between adjacent marks. One hundred four had a decision while observed over limit, 31 did not. Each episode's next mark was within limit by construction, but the trace does not prove that the next rebalance caused resolution. There were 128 BUY reservations after the most recent over-limit account mark; because exact account gross at creation and queued-order provenance are not in every event, that is an exposure-screening count, not 128 proven policy breaches.

Sleeve values reconstructed from held-position fills and marks reconcile to account gross on 7,825/9,152 over-limit marks. At those marks mean equity/crypto gross contributions were 0.55616174/0.04763143. The remaining 1,327 marks cannot be sleeve-attributed exactly from this trace alone; the independent V3 verifier also notes missing non-held source marks. No per-episode causal sleeve claim is made for them. These data support fuller cap-8 deployment plus marked movement as a source of drift; they do not independently apportion all effects among price, NAV, cost and execution.

Cap 6 had 0/50,194 over-limit marks, peak realized gross 0.58976717 and peak target gross 0.55, versus 0.60 target peak under cap 8. The five-percentage-point lower target peak created headroom in this frozen control; its lack of breach is an observed result, not proof that cap 6 could never drift above 0.60. Cash/NAV arithmetic gives cap-8 9,152 marks below 40% realized cash, minimum 0.38449255; control had zero, minimum 0.41023283. Cash 0.40 is a target/order constraint, not a continuous bank-balance floor.

## Parameterization, parity and operations

Config still says `max_positions: 6`, `max_crypto_positions: 2`, gross 0.60 and cash buffer 0.40 (`config/settings.yaml:179-185`). The global cap flows through config mapping, overlay, slot helper, runtime and dashboard; no hardcoded six-position production array was found. Cap 8 does not alter crypto cap, per-name caps or sleeve controls. The frozen migration saw 8 actual positions and 11 working orders; they represent different counts. Exhaustive open-order pagination and local order persistence/idempotency have focused tests. The pending separate reconciliation repair for restart sequencing, authoritative order recovery and FILL pagination remains a mandatory prerequisite; this audit did not modify it.

External inventory is counted in gross and account slots and is not sold as managed inventory. The current operator risk panel shows realized gross, cash, sleeve exposures, configured limits, crypto count and halt indicators. It lacks a joined view of effective occupied/reserved global slots, broker working-order count and aligned target versus realized gross. Add that instrumentation and an explicit suppressed-BUY reason before a rollout; see `MONITORING_READINESS.md`.

## Rollout and rollback status

`PAPER_ROLLOUT_PLAN.md` is a design only. It requires a future pinned main SHA with the pure-BUY admission repair and accepted reconciliation repair, clean startup account/order recovery, unchanged strategy and risk parameters except a separately approved cap setting, LIVE disabled, and first-session monitoring. No arbitrary realized-drift threshold is adopted. A later rollback to cap 6 blocks new openings while seven or eight positions remain; an unfilled SELL does not free a slot, and no forced liquidation follows solely from the lower count. Existing strategy exits and authoritative reconciliation must handle inventory and pending orders.

Production files changed: `NONE`. Production `maximum_positions`: `6`. No broker mutation, PAPER/LIVE mode change, cap migration or remote push occurred.

## Validation and exact next action

Seven audit-only synthetic tests passed. The focused gross/slot/open-order pagination set passed (36 tests before the two extra audit cases). The final full normal suite passed with loopback access: 1,181 passed, 1 skipped and two deprecation warnings. Ruff, Ruff format, strict mypy for the trace analyzer, JSON parsing, `git diff --check` and a scoped credential-pattern scan passed. See the final task response for the exact audit branch HEAD; a commit cannot embed its own final SHA in its tracked content. Required next action: repair and verify fresh gross/cash headroom for every pure BUY on production main; accept/merge the separate restart/reconciliation repair; then repeat production parity review before any controlled PAPER cap-8 configuration change.
