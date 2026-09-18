# Corrected V3 Baseline Protocol V2

Status: **FROZEN before persisted corrected-baseline economic outputs were
opened for re-evaluation**.

Freeze timestamp: `2026-09-18T19:49:41Z`.

Protocol branch: `research/v3-baseline-protocol-repair-v1`.

Production reference: `main` at
`bdfa55ea58858b5f8eaa8b58b0ab8ae34dda7c53`.

## Purpose and non-goals

This is a protocol repair, not a production change. It repairs the original
G5 methodology after the drawdown-semantics audit classified that gate as
`G5_MISSPECIFIED`.

No production behavior, V3 parameter, allocation, risk limit, runtime
drawdown behavior, crypto behavior, PAPER state, or LIVE state may change.
The persisted corrected replay is eligible for re-evaluation only if its
identity and evidence match this protocol. A fresh full replay is not
required when those invariants are independently verified.

## Frozen identity and data contract

The evaluation must match the prior corrected replay exactly:

* production SHA: `bdfa55ea58858b5f8eaa8b58b0ab8ae34dda7c53`;
* strategy: `equity_residual_momentum_v3`, version `3.0.0`;
* configuration: `config/settings.yaml`, no parameter overrides;
* current production posture: `runtime.mode=paper`, `live.enabled=false`, no
  live authorization;
* role boundary: `SPY` reference-only; `SH`, `PSQ`, and `DOG` hedge-only;
  zero ordinary V3 target eligibility;
* bars SHA-256:
  `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139`;
* entity lifetimes SHA-256:
  `ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596`;
* corporate actions SHA-256:
  `a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23`;
* common endpoint: `2026-07-30T04:00:00+00:00`;
* development: 2017-01-03 through 2021-12-31;
* validation: 2022-01-03 through 2026-07-30;
* full: one continuous path from the development start through the common
  endpoint;
* current production costs, next-open execution, terminal lifecycle handling,
  and the existing 1x/2x/3x cost multipliers.

The old structural and economic artifacts remain immutable evidence on
`research/v3-reserved-role-corrected-baseline-v1`; this branch references
them rather than copying the large replay output.

## Frozen gates

Gates are evaluated only after an independent verifier confirms the identity,
data, role boundary, and evidence provenance. The final decision vocabulary
is exactly one of:

* `CORRECTED_V3_BASELINE_VALID`
* `CORRECTED_V3_BASELINE_FAILED`
* `CORRECTED_V3_BASELINE_INCONCLUSIVE_PROTOCOL_ERROR`

### G1 — validity and parity

Pass requires the three expected dataset hashes, lifecycle counts and
semantics, common endpoint, canonical V3/config identity, no reserved-role
ordinary targets, no lookahead violations, valid next-open execution,
persisted NAV/cash/cost evidence, and independent verifier agreement.

Any missing or unverifiable input is a validity failure, not an economic pass.

### G2 — full 1x return

Full-period 1x net total return and CAGR must both be strictly greater than
zero.

### G3 — validation 1x return

Validation-period 1x net total return and CAGR must both be strictly greater
than zero.

### G4 — full 1x risk-adjusted economics

Full-period 1x Sharpe, Sortino, and Calmar must each be strictly greater than
zero. Calmar is the risk-adjusted drawdown-aware economic gate; it is not a
replacement for reporting maximum drawdown.

### G5 — drawdown semantics and reporting, not a 1x alpha cap

There is **no absolute 1x offline alpha maximum-drawdown threshold** in V2.

Pass requires all of the following:

1. full-period 1x maximum drawdown is present, finite, and independently
   recomputed from the persisted NAV curve;
2. the production 5% value is identified only as the account-level
   operational `portfolio_drawdown_halt` threshold and is not compared as an
   offline alpha acceptance bound;
3. any persisted `portfolio_drawdown_halt` shadow event is traced to the
   offline risk-overlay reason and the event list is consistent with the
   persisted rebalances;
4. offline shadow events do not cause an invented alpha freeze, liquidation,
   operator reset, or NAV truncation; and
5. the source/test semantic audit proves that PAPER/LIVE behavior remains the
   production latch behavior: new entries blocked, risk-reducing sells
   permitted, no automatic liquidation, and verified risk-epoch reset for
   explicit recovery.

The existing 3x cost-stress catastrophic-loss guard below remains a separate
pre-result stress control. It is not a normal 1x alpha max-drawdown bound and
does not repair or replace G5.

### G6 — pre-result cost robustness

Carry forward the cost-stress rule frozen in the original protocol before its
economic outputs were inspected:

* full-period 2x-cost net total return must be strictly greater than zero;
* full-period 3x-cost net total return must be greater than `-25.0%`; and
* full-period 3x-cost maximum drawdown must be less than `50.0%` as a
  catastrophic-stress guard only.

All 1x/2x/3x max drawdowns remain reported. No new stress threshold may be
selected from the persisted result.

### G7 — portfolio and role controls

For every corrected target and every cost multiplier, at most six positions,
target weights at or below 10%, cash at or above 40%, and gross exposure at or
below 60% must hold. The ordinary target count for each of `SPY`, `SH`, `PSQ`,
and `DOG` must be exactly zero. Required observation/reference/hedge role
context must remain present, and the explicitly authorized hedge path must
remain possible. No reserved symbol may be restored to ordinary V3 alpha
targeting for performance.

## Temporal and anti-post-hoc controls

* This file is frozen and hashed before persisted economic output inspection.
* The protocol repair is based on current source semantics and pre-existing
  governance evidence only.
* The verifier must answer `NO` to: “Did the observed 20.4359% result
  influence V2 gate definition?”
* No 1x maximum-drawdown threshold may be added after seeing the result.
* No operator reset dates may be invented for a runtime-latch replay.
* If the persisted evidence cannot prove the V2 gates without re-running the
  complete protocol, the decision is `CORRECTED_V3_BASELINE_INCONCLUSIVE_PROTOCOL_ERROR`.

## Required outputs

The repair branch must contain:

* drawdown semantics audit;
* pre-existing governance evidence;
* `G5_AUDIT.json`;
* this frozen protocol and its SHA-256 receipt;
* independent persisted-result V2 evaluation;
* independent verification;
* final decision; and
* a final protocol-repair report.

No large replay artifact is copied to this branch.
