# Final architecture decision

`POSITION_CAP_ARCHITECTURE_CHANGE_DESERVES_FREEZE`

The present six-position account cap is a real cardinality and operational guard, but its **number** has no documented independent calibration. With the configured two-name crypto sleeve, it cannot express the configured equity sleeve even at the maximum permitted equity name weight. This warrants a separately frozen architecture migration study, not an immediate production change or a claim that more names improve results.

# Starting repository state

At audit start, local `main` and verified remote `main` were both `df9bf797a428c384fb26adc7f9f99abacf79224b`; the worktree was clean. This branch, `research/account-capacity-architecture-audit-v1`, was created directly from that commit. Exact verified settings and remote are recorded in `STARTING_STATE.json`. Active YAML, rather than the differing dataclass defaults, sets six global positions and two crypto positions (`config/settings.yaml:175-184`; `src/parayabuck/config.py:210-237`).

# Origin of the six-position cap

`ORIGIN_UNRESOLVED`. Tracked settings moved from 15 in July 2026 to five, then three. Commit `84c4fbc5f97ff66ed38f572072310ebc261b62fa` first set six on 2026-08-09, replacing three, but did not explain the threshold. Account-wide cross-market counting was implemented later in `fa6276410d3f6bf98df5ff1d25e511c424daf54a` on 2026-09-10. `RiskLimits` still has a generic default of five, while active YAML explicitly loads six. See `POSITION_CAP_ORIGIN_AUDIT.md` for the documented lineage. No broker, assignment, risk-model, or minimum-position-size derivation of six was found.

# Exact enforcement semantics

`PortfolioRiskOverlay.apply` is invoked separately per market but counts distinct non-flat `(market, canonical_symbol)` positions across the account plus fixed external positions and open-order/reservation claims (`src/parayabuck/execution/portfolio.py:263-348`). Tiny nonzero and fractional holdings consume a slot. Current-market managed exits can be replaced after terminal SELL and a fresh snapshot; manual/external inventory cannot be sold simply to free a slot. Local nonterminal orders and broker-visible manual working orders remain claims. The engine orders simultaneous decisions crypto first and passes planned crypto BUY reservations to equity; V3's strategy-local `top_n: 8` is trimmed by the account overlay according to V3 rank. Reference/hedge-only symbols with no position or order consume no slot. One symbol is canonicalized into one market-symbol key; duplicate broker rows are rejected. See `POSITION_CAP_ENFORCEMENT_AUDIT.md` for pending, partial-fill, cancellation, failed-exit, restart, and stale-state paths. Shared locking, persisted idempotency, and fresh BUY replanning reduce known races; they do not prove broker-state atomicity.

# Broker/API constraints

`NO_BROKER_REQUIREMENT_FOR_SIX_FOUND`. The current official Alpaca materials reviewed state buying-power, asset increment/minimum, order-type, and request-rate constraints, but no general six-position ceiling. Fractional shares and crypto can still be constrained by asset and order rules. The published Trading API 200-requests/minute support limit is not a six-name limit or a measured budget for this account; Broker API rate documentation is distinct. The repo's six limit is internal policy. Sources and qualifications are in `BROKER_LIMIT_AUDIT.md`.

# Existing independent risk controls

The active account also enforces 0.60 gross, 0.40 minimum target cash, 0.55 managed equity sleeve, 0.05 managed crypto sleeve, 0.10 equity name and 0.05 crypto name limits, two crypto names, eligibility/liquidity gates, daily loss and drawdown entry latches, order minimum/increment and rebalance-band checks, and fresh BUY cash/gross checks. Raising count alone would not relax these controls (`EXISTING_RISK_CONTROLS.md`). Count uniquely bounds *number of distinct account position/order lines*; it does not bound dollars, correlation, liquidity, or turnover on its own. The 0.40 cash setting is a target-construction **minimum**, not a fixed cash target. The configured gross 0.60 gives the same target cash floor in this setup. The active strategy sleeves are tighter than generic overlay market ceilings.

# Static capacity geometry

These are only upper bounds under sufficient eligible names and no other blocking inventory/orders. Equity names = `min(8, global_cap − crypto_names)`; equity target ≤ `min(0.55, 0.10 × equity_names)`. Crypto target ≤ 0.05 when one or two crypto names are present. V3's actual inverse-vol sizing may yield less. The CSV gives every 0/1/2 crypto case for all required caps, cash, equal-weight HHI illustrations, and count-only idle equity capacity.

| Global cap | 0 crypto: max equities | 1 crypto: max equities | 2 crypto: max equities | Max equity target with 2 crypto | Gross upper bound with 2 crypto | Count-only idle equity sleeve with 2 crypto |
|---:|---:|---:|---:|---:|---:|---:|
| 4 | 4 | 3 | 2 | 0.20 | 0.25 | 0.35 |
| 5 | 5 | 4 | 3 | 0.30 | 0.35 | 0.25 |
| 6 | 6 | 5 | 4 | 0.40 | 0.45 | 0.15 |
| 7 | 7 | 6 | 5 | 0.50 | 0.55 | 0.05 |
| 8 | 8 | 7 | 6 | 0.55 | 0.60 | 0 |
| 10 | 8 | 8 | 8 | 0.55 | 0.60 | 0 |
| 12 | 8 | 8 | 8 | 0.55 | 0.60 | 0 |

At zero crypto, gross tops out at 0.55 even with enough equity slots because the crypto sleeve is absent; that 0.05 gap is not count-caused. At one crypto, six slots allow five equities and gross at most 0.55. At two crypto, eight is the smallest listed integer global cap that permits six equities, the minimum name count needed to *possibly* express 0.55 under a 0.10 equity per-name ceiling. Count eligibility alone does not guarantee full deployment.

# Current six-slot analysis

Two crypto names leave four equity slots and at most 0.40 equity, so the 0.55 equity sleeve cannot be fully expressed. The combined gross upper bound is 0.45 and target cash is at least 0.55 in this idealized geometry. With one crypto name, five equities can express at most 0.50 equity; with none, six equities can potentially express 0.55. V3 ranks up to eight without first debiting crypto occupancy; the risk overlay performs that debit later. Crypto-first sequencing can therefore alter which equity targets survive. The source leaves unallocated capacity as cash, but no documented policy explains this particular count-driven shortfall as intentional. Position count and exposure limits are complementary risk dimensions but conflicting as a full-sleeve expression contract (`CURRENT_SIX_SLOT_ANALYSIS.md`).

# Concentration implications

A higher count does not require weaker or stronger single-name concentration: the permitted equity name maximum remains 0.10. Under the hypothetical *maximal expressible* targets with two crypto names and equal weights within each sleeve, six total slots give four 0.10 equities plus two 0.025 crypto weights (account HHI 0.04125; invested-capital-normalized HHI 0.20370). Eight slots permit six equities sharing 0.55 plus two 0.025 crypto (account HHI 0.05167; normalized HHI 0.14352). Ten slots permit eight equities sharing 0.55 plus two crypto (account HHI 0.03906; normalized HHI 0.10851). Lower gross at six explains its lower unnormalized HHI; it is not evidence of superior diversification. Actual V3 weights are inverse-volatility sized, not equal-weighted. A higher count only lowers the *minimum possible* concentration when more names are selected; it does not force selection or prevent factor overlap (`CONCENTRATION_ANALYSIS.md`).

# Capital fragmentation

The 0.005 rebalance band, $10 policy order floor, broker quantity increment/minimum, and fractional support govern order deltas; no code policy imposes a minimum *target position weight*. Tiny nonzero residual holdings still consume one slot. A larger cap might permit smaller feasible targets and more order suppression or rounding, but no account-size envelope, fee/notional budget, or source-derived threshold selects six (`CAPITAL_FRAGMENTATION_AUDIT.md`).

# Order/API operational burden

A simple full replacement of C held names with C new names entails up to `2C` portfolio SELL/BUY orders: 12, 16, or 20 for C of 6, 8, or 10, before retries, protection, broker metadata, or separate decision times. The runtime sells first, waits for terminal state, refreshes the account, replans BUYs, and checks cash and gross. Working-order suppression, idempotent IDs, shared locking, and reconciliation are present. No measured throughput or broker rule demonstrates an operational knee exactly at six. A future study must freeze an order/request and recovery budget before adoption (`ORDER_LOAD_AUDIT.md`).

# Equity-vs-crypto slot equivalence

Both markets share broker capital and account gross, so some shared constraint is coherent. Yet the 0.05 crypto sleeve's two equally weighted 0.025 targets each cost one slot just as a potential 0.10 equity name does. Count is a reasonable shared measure of operational object count, but a poor cross-asset exposure unit. The crypto-first same-cycle priority is implemented; an independent policy rationale for that priority or six-slot total was not found (`CROSS_STRATEGY_SLOT_AUDIT.md`).

# Core-satellite research policy audit

The frozen 5+1 method defines six V3 **equity** slots, with five core equity slots and one cash/satellite reservation in its controlled comparison. It partitions the 0.55 equity sleeve and keeps unused budgets as cash. It does not specify two simultaneous crypto positions claiming two of the six global slots. That prior methodology remains valid for its frozen conditional equity experiment. It cannot alone establish deployability in the enabled total account (`CORE_SATELLITE_POLICY_AUDIT.md`). No past candidate result was consulted.

# Research-production slot parity

`RESEARCH_PRODUCTION_SLOT_PARITY_MISMATCH` for any claim that the six-equity-slot 5+1 experiment is immediately deployable in the simultaneously enabled two-crypto six-total account. This is conditional capacity arithmetic, not a claim that crypto was historically held or that any old research result is invalid. Research that claims total-account fit must model the same account cap, crypto occupancy/claims, sequencing, and risk overlay (`RESEARCH_PRODUCTION_PARITY.md`).

# External risk-practice review

Official FINRA concentration and asset-allocation guidance recognizes name, sector, asset-class, and correlated exposures; Alpaca applies buying-power and asset/order mechanics. The limited review supports treating position count as one potential operating or diversification control alongside exposure, liquidity, and per-name limits, not as a universal magic count. It gives no external authority for six or eight for this system (`EXTERNAL_RISK_PRACTICE_REVIEW.md`).

# Red-team findings

Against change: more order and monitoring lines, capital fragmentation, possible incremental low-quality or correlated names, and stale/order-state recovery burden. Against retention: the explicit 0.15 equity-sleeve capacity conflict with two crypto claims, unequal slot cost across sleeves, undocumented threshold, and conditional research/production parity. Those objections and the alternative global, sleeve-specific, reservation, and no-count structures are assessed in `ARCHITECTURE_RED_TEAM.md` and `SLOT_PARTITION_ALTERNATIVES.md`.

# Structural problems with current design, if any

The six-count/two-crypto/0.55-equity/0.10-per-equity combination cannot simultaneously express all configured sleeve capacities. The generic `RiskLimits` default of five also differs from active six YAML, and the canonical six-equity research contract must not be read as six simultaneous production equity slots when crypto claims two. None of these facts proves a performance problem.

# Structural problems with higher caps, if any

Eight increases possible concurrent positions and complete-replacement orders, with no explicit minimum position-weight or measured operational budget. Ten has no additional sleeve-deployability benefit over eight with two crypto names; it merely permits two more equity selections under current `top_n: 8`. A larger count cannot repair other safety or reconciliation failures. No higher cap should be promoted before separate frozen, deterministic migration and operating-capacity tests.

# Recommended architecture

`PROPOSED_ARCHITECTURE_FOR_MIGRATION_STUDY: Global account cap 8, retaining max two crypto names and every present gross, cash, sleeve, per-name, eligibility, ownership, and execution safety constraint.`

This is a single, minimal **study design** based on 6 equity slots needed for possible 0.55 equity expression plus 2 crypto claims. It preserves a simple global cardinality backstop. The next campaign should prospectively freeze: cross-market simultaneous occupancy, manual/external and open-order claims, sell/partial-fill/cancel/restart behavior, minimum meaningful target/order size and account-size envelope, static full-replacement/API request budget, and exact total-account research/PAPER parity gates. It should test these without relaxing live safeguards. An outcome experiment, if later authorized, would be a separate stage with predeclared hypotheses and no automatic promotion.

# Future alpha-research implication

Until any migration is separately approved, the current production contract is **six total positions**. Future research claiming deployability under the enabled two-crypto case must demonstrate **four-equity fit** and the associated 0.40 equity upper bound, or explicitly model a different crypto occupancy as a conditional scenario. A six-equity 5+1 experiment remains a valid equity-only structural diagnostic but must not be labeled total-account parity proof. No previously rejected candidate is rescued or reinterpreted here.

# No-outcome-peek statement

No historical holdings distribution, realized count series, strategy/candidate performance, cap-alternative backtest, or prior combination economics informed this decision. Only source/config/history, tests for architecture semantics, static arithmetic, official documents, and the 5+1 *methodology* were used. A necessary configuration excerpt included adjacent legacy outcome comments; they were not used as evidence or copied into analysis. See `NO_OUTCOME_PEEK_RECEIPT.json`.

# Production state

Configuration, V3, crypto strategy, PAPER/LIVE code and settings, and `max_positions` are unchanged. This branch contains research artifacts only. No production promotion or broker order was made.

# Validation

All 19 requested files are present. Three JSON files parse; the CSV has the required 21 distinct cap/crypto rows and recomputed equity, gross, idle-capacity, and HHI values. `ruff check .`, `ruff format --check .`, and `.venv/bin/mypy src` pass. The full `.venv/bin/pytest -q` suite passes with local loopback access: **1,138 passed, 1 skipped, 2 deprecation warnings**. Its initial sandboxed run had six socket-bind permission failures; rerunning the unchanged suite with loopback access resolved them. `git diff --cached --check` passes. A targeted credential-pattern scan found no secrets in the artifacts; the staged paths are limited to this research directory. The no-outcome receipt documents the input boundary.

# Files

All 19 requested artifacts are under `research/account_capacity_architecture_audit_v1/`: `STARTING_STATE.json`, `POSITION_CAP_ORIGIN_AUDIT.md`, `POSITION_CAP_ENFORCEMENT_AUDIT.md`, `BROKER_LIMIT_AUDIT.md`, `STATIC_CAPACITY_GEOMETRY.csv`, `CURRENT_SIX_SLOT_ANALYSIS.md`, `EXISTING_RISK_CONTROLS.md`, `CONCENTRATION_ANALYSIS.md`, `SLOT_PARTITION_ALTERNATIVES.md`, `CAPITAL_FRAGMENTATION_AUDIT.md`, `ORDER_LOAD_AUDIT.md`, `CROSS_STRATEGY_SLOT_AUDIT.md`, `CORE_SATELLITE_POLICY_AUDIT.md`, `RESEARCH_PRODUCTION_PARITY.md`, `EXTERNAL_RISK_PRACTICE_REVIEW.md`, `ARCHITECTURE_RED_TEAM.md`, `NO_OUTCOME_PEEK_RECEIPT.json`, `ARCHITECTURE_DECISION.json`, and this report. No calculator script was needed; the CSV was generated from configuration constants only.

# Final branch SHA

The containing commit's SHA is reported in the final delivery response after commit and push. A Git commit cannot contain its own hash in this file without changing that hash.

# Exact next action

`PROCEED_TO_FROZEN_ACCOUNT_ARCHITECTURE_MIGRATION_STUDY`
