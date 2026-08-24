# MH_RAW v3 PAPER Promotion Receipt — 2026-08

Status: **PAPER selection applied; operational soak evidence pending**

This receipt covers the frozen equity candidate `equity_residual_momentum_v3`
(MH_RAW). The product registry and PAPER equity selection are now enabled;
the runtime database currently contains no persisted V3 decisions, so this
receipt does not claim that a real PAPER soak has already occurred. LIVE
remains prohibited.

## Scope and allowed change

The intended promotion is:

```text
PAPER equity selection: equity_residual_momentum_v2 -> equity_residual_momentum_v3
crypto selection:       unchanged
risk settings:          unchanged
execution settings:     unchanged
universe settings:      unchanged
runtime mode:           paper
live.enabled:           false
```

The only economic-selection delta is the equity strategy identity. The v3
parameter block, equity sleeve allocation, risk overlay, execution policy,
universe, crypto block, and runtime posture remain unchanged from the
pre-promotion PAPER configuration. The additional runtime changes are limited
to V3 observability, read-only auditing, fail-closed thin-pool handling, and
persisted observation-set strategy identity validation; none participates in
alpha scoring or order decisions.

Why promoted: the frozen production implementation remains parity-locked to
the MH_RAW research identity and reuses the existing eligibility, signal
verification, risk, sizing, and execution paths. The offline evidence is a
promotion input only. It is not unbiased future performance evidence: the
historical universe and any selected-name statistics can contain
survivorship/selection bias and must not be treated as an alpha guarantee.

## Frozen v3 semantics

Identity: `equity_residual_momentum_v3`, version `3.0.0`, equity, 1d signal
bars, weekly rebalance.

The frozen pipeline is:

1. Apply the inherited positive 12-1 residual-momentum gate first.
2. For eligible symbols, compute the equal-weight mean of percentile ranks of
   raw residual momentum over 6-1, 9-1, and 12-1 horizons (126, 189, and 252
   residual bars; skip 21 bars).
3. Keep the inherited idiosyncratic-volatility rejection at `q=0.70`, `top_n=8`,
   inverse-volatility sizing, and `max_single_weight=0.10`.
4. Keep the v2 risk overlay, order-existence policy, fills/reconciliation
   semantics, and point-in-time universe behavior unchanged.

No parameter search, post-hoc tuning, risk-policy change, execution-policy
change, crypto change, or live enablement is part of this receipt. The offline
MH_RAW validation and shadow replay are evidence only; they are not PAPER soak
observations and never submit orders.

## Read-only soak audit

Run from the repository root:

```text
python3 scripts/audit_v3_paper_soak.py --pretty
```

The command reads `portfolio_decisions`, `portfolio_orders`,
`portfolio_fills`, `portfolio_rebalance_executions`, runtime sessions, and
scoped anomaly tables through SQLite read-only mode. It reports, when
persisted:

- v3 identity, version, configuration digest, account/session mode, universe
  version and snapshot identity;
- one row per v3 rebalance with target invested/cash weight, position count,
  maximum symbol weight, risk gross-before/gross-after, one-way target
  turnover, lifecycle status, order/fill status, fees, slippage, and fill
  delay;
- risk halts, non-terminal or ambiguous orders, malformed identity/target
  records, duplicate decision identities, ownership adjustments, and scoped
  safety incidents;
- target exposure and, only when fills exist, ledger net filled quantities.

It does not claim broker mark-to-market exposure when the persisted schema does
not contain a v3-linked broker snapshot. A missing database, missing table, or
zero v3 decisions is reported as **no data**, not as a successful zero result.
The command does not create a database, repository, observation, order, or
output file.

## Validation prerequisites before sign-off

The coordinator must complete and record all of the following before treating
the PAPER promotion as accepted:

- [x] `runtime.mode=paper`, `live.enabled=false`, and
      `PARAYABUCK_ENABLE_LIVE` is not `YES`.
- [x] The config diff is limited to the equity identity switch and contains
      no change to parameters, allocation, risk, execution, universe, crypto,
      or live settings.
- [x] The resolved runtime identity is exactly
      `equity_residual_momentum_v3` / `3.0.0` / equity / 1d / weekly, with the
      frozen semantics above.
- [ ] The v3 configuration digest and universe identity are captured after
      the PAPER restart; stale v2 sessions are not counted as v3 evidence.
- [ ] The audit has at least four real scheduled weekly V3 PAPER rebalances
      over the operator-selected soak window. No-data is not a pass.
- [ ] Every observed rebalance is reviewed for target exposure, risk overlay,
      order/fill linkage, lifecycle completion, and identity continuity.
- [ ] There are no unexplained reconciliation-required orders, open/ambiguous
      rebalance lifecycles, ownership adjustments, active safety incidents, or
      identity/configuration-digest mismatches.
- [ ] The existing 7/30/90-day PAPER reporting windows are recorded as
      reporting evidence; a single short-period PnL result does not authorize
      strategy or parameter changes.
- [x] Offline validation evidence remains linked to the frozen implementation
      and its declared dataset/manifest; it is not replaced by post-hoc PAPER
      tuning.

The runtime V2 shadow calculation was intentionally skipped. V3 is the only
order-producing PAPER strategy; the existing offline shadow replay remains a
strictly non-executing comparison reference.

## Exact rollback: V3 → V2

If rollback is required, the operator performs this exact identity change:

```text
strategy.equity.name = equity_residual_momentum_v2
```

Then restart the PAPER engine and verify the resolved identity is
`equity_residual_momentum_v2` / `2.0.0`. Restore the pre-promotion v2
configuration digest and keep the v2 parameter block, equity allocation, risk,
execution, universe, crypto, `runtime.mode=paper`, and `live.enabled=false`
unchanged. Do not use rollback as permission to alter strategy math or risk.
The coordinator must record the resulting digest and restart evidence.

## LIVE prohibition

This receipt authorizes no LIVE action. LIVE is prohibited:

- `runtime.mode` must remain `paper`;
- `live.enabled` must remain `false`;
- `PARAYABUCK_ENABLE_LIVE` must not be `YES`;
- no live credentials, live arm token, hard-dollar cap, or operator approval
  may be introduced by this promotion;
- a PAPER smoke opt-in, if separately used by an operator, is not a LIVE
  authorization and must not be manufactured by the audit.

## Evidence and completion facts

The committed offline evidence is the MH_RAW validation report and the
historical offline shadow artifact. The artifact's 495 decisions are labelled
offline shadow replay and must not be counted as PAPER soak observations.

| fact | value |
|---|---|
| frozen strategy | `equity_residual_momentum_v3` / `3.0.0` |
| promotion scope | equity PAPER only |
| research validation commits | `2c42341`, `32126af`, `bda7002`, `c1148d4` |
| implementation/provenance commit | `bda700270b4853bcffd47218dfebcef8fc3677b0` |
| promotion/config commit | `[RECORDED AFTER FINAL VALIDATION]` |
| config SHA256 before / after | `c65ab34257d51cb99650b01d90e544cd11dd678954b420a1c20872ea6cd0f3f4` / `c86dc22012cbd77cb8932e816af8a72e67e3f834ea53d94a9bc3215015ed3eb3` |
| frozen manifest SHA256 | `d2b906a836484b102b30610d4b3786923ace8a079df0fc7b811eebb0516b6e96` |
| expected runtime configuration digest | `330c51141df61411fa3440d432deaa66224ffce932bb2ed1ad7fb73ab30a0cd6` |
| expected universe config digest | `d8e06f54cd1a2fe0a201317fb6e3f4265470b2a10fe6e119ad81554128b33392` |
| post-promotion universe snapshot | `not captured: current on-disk snapshot is stale and lacks V3 observation identity; startup validation rejects it for refresh` |
| audit as-of UTC | `2026-08-24T11:45:01.753616+00:00 (0 V3 observations; rerun after PAPER activity)` |
| completed PAPER rebalances / soak window | `0 / no persisted V3 soak window; offline 495-decision shadow excluded` |
| rollback verification | `procedure recorded; deterministic V2->V3 restart/idempotency tested; V3->V2 operator restart not run` |

The unobserved soak items remain an explicit operational follow-up. They do
not authorize LIVE or change the completed PAPER product selection.
