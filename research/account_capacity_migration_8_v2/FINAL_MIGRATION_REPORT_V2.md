# Final migration decision

`ACCOUNT_ARCHITECTURE_MIGRATION_8_INCONCLUSIVE`

# Starting corrected main SHA

`8e48b301413a679e6d2b097c28e8711c50ab1a8b`.
The local `main`, `origin/main`, and live `git ls-remote origin refs/heads/main`
all resolved to this commit. The worktree was clean before branch creation.

# Migration-v1 provenance

The archive tag
`archive/research/account-capacity-migration-8-v1-inconclusive-2026-09`
points to stopped commit `ede3b3dfadfd5f3f0bdef040a14d20365c8df469`. V1 stopped
before either arm and produced no outcomes. Its frozen specification is
provenance only; no V1 protocol or result was reused as V2 authority.

# Open-order correctness repair receipt

Corrected main includes exhaustive broker open-order pagination. The focused
regression suite passed: `18 passed` from
`.venv/bin/pytest -q tests/execution/test_alpaca_order_pagination.py`.

# FILL-pagination relevance decision

`FILL_PAGINATION_IRRELEVANT_TO_MIGRATION_V2`, conditional on initializing the
offline replay flat, all-cash, with no pending orders, and never consulting
Alpaca FILL activities, production ledger state, or broker startup state.
The separate live reconciliation dependency does not enter that offline
contract.

# V2 freeze commit SHA

None. The migration was stopped before freezing because one shared-account
historical replay could not be demonstrated. No outcomes were generated.

# Control and treatment

- Control: `maximum_positions = 6`.
- Treatment: `maximum_positions = 8`.
- No other cap or configuration was evaluated.

# Single-variable integrity

The intended proposition remains exactly six versus eight. It was not frozen
or executed, so there is no claim that an outcome comparison passed integrity
gates.

# Shared-account replay fidelity

Failed to demonstrate. The production runtime coordinates a single account
snapshot, processes simultaneous crypto decisions before equity decisions, and
passes position-slot reservations for actual earlier BUY orders. The standard
historical product runner accepts one market and creates one engine with its
own cash, positions, pending orders, and synthetic fills. Existing cross-market
tests cover overlay arithmetic and stubbed ordering, not an evolving shared
historical cash/fill ledger. Combining separate NAV curves would violate the
study contract.

# Data period and hashes

No V2 evaluation period was frozen. The current canonical inputs were
byte-hash-checked for readiness:

- Equity bars: `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139`.
- Entity lifetimes: `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596`.
- Corporate actions: `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23`.
- Crypto hourly bars: `73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e`.
- `config/settings.yaml`: `414692bd0827240496253bdba67d9e1fca9748d7e29744be823fdc370fe8a353`.

These identify inputs only; they do not constitute a V2 freeze or establish
the crypto universe's completeness. Canonical baseline documents record the
crypto provider/tradability limitation.

## Corrected-main settings inspected

- Equity: `equity_residual_momentum_v3` v3.0.0, weekly, 55% allocation;
  formation 252, skip 21, beta window 273, residual volatility window 63,
  top 8, 10% maximum name weight, IVOL reject quantile 0.70, SPY excluded.
- Crypto: `crypto_low_variance_hysteresis_v1` v1.0.0, 5% allocation;
  22-day variance lookback, top 2, 96-hour cadence, with the incumbent
  hysteresis retention rank rule.
- Account: 6 global positions, 2 crypto positions, 10% equity name cap, 5%
  crypto name cap, 10% crypto exposure cap, 60% gross cap, 40% cash buffer,
  0.5% daily-loss limit, and 5% drawdown halt.
- Modeled costs: 2 bps slippage, 2 bps spread, zero fixed commission, 25 bps
  crypto taker fee, and effective-dated equity regulatory fees.

These are inspected corrected-main settings, not frozen V2 study settings.

# Structural capacity comparison

Not run. No decision counts, slot denials, days-at-cap, sleeve deployability,
idle-capacity attribution, or crypto/equity crowd-out counts were generated.

# Exposure and concentration invariants

Not compared. The proposed arms and configured nominal limits remain
unexecuted. Production limits and settings were not edited.

# Operational and order-load comparison

Not run. No new order-load, API-capacity, reconciliation-workload, or retry
measurement was created. The corrected open-order pagination regression
coverage passed independently.

# Economic comparison

Not run. No return, risk, turnover, cost, or ending-NAV result artifacts were
created. No empty result files were fabricated.

# Independent verification

Read-only replay audit found the same blocker: current product replay is
single-market, while the required comparison needs one shared account ledger.
No independent outcome verification applies because there are no outcomes.

# Validation

- Full suite: `1156 passed, 1 skipped, 2 warnings` in 103.62 seconds. This
  permission-enabled run passed the localhost socket tests that were blocked
  by the sandbox in the first run.
- Focused dual-market, combined-account-risk, and open-order pagination suite:
  `41 passed`.
- Open-order pagination regression suite: `18 passed`.
- Ruff check, Ruff format check, and strict `mypy src`: passed.
- All four audit JSON files parsed successfully; credential-pattern scan found
  no hits in this packet, and `git diff --cached --check` passed.
- No migration replay tests or deterministic A/B repetitions were run because
  the required shared-account replay does not exist.

# Why the decision follows from architecture rather than performance chasing

The question is whether eight removes real structural blocking while preserving
the rest of the architecture. Without a replay that shares slots, cash, pending
orders, and fills across both incumbent sleeves, that structural effect cannot
be measured. Historical economics would not resolve the missing account-state
semantics.

# Implications for previous portfolio-rejected alphas

No reruns and no inference. Any future governance review of prior rejected
families must remain a separate task.

# Production state

- Production `maximum_positions` remains 6.
- Equity V3 configuration is unchanged.
- Crypto configuration is unchanged.
- PAPER/LIVE configuration is unchanged.

# Exact next action

`RESOLVE_SHARED_ACCOUNT_REPLAY_LIMITATION`

# Files

- `CORRECTED_MAIN_RECEIPT.json`
- `MIGRATION_V1_PROVENANCE_RECEIPT.json`
- `FILL_PAGINATION_RELEVANCE_AUDIT.md`
- `SHARED_ACCOUNT_REPLAY_SPEC_V2.md` (requirements only; not frozen)
- `REPLAY_PARITY_AUDIT_V2.md`
- `INDEPENDENT_MIGRATION_VERIFICATION_V2.json`
- `MIGRATION_DECISION_V2.json`
- `FINAL_MIGRATION_REPORT_V2.md`

No `FROZEN_MIGRATION_SPEC_V2`, pre-outcome freeze receipt, arm result, or
economic comparison artifact exists.

# Final branch SHA

Reported in the delivery message; a commit cannot contain its own resulting
SHA.
