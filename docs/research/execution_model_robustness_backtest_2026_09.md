# Execution-model robustness backtest — 2026-09

## Status

Predeclared; no scenario result has been generated. This document and its
manifest are the pre-result commitment for an offline execution-robustness
study, not a PAPER or LIVE calibration.

## Frozen scope

The study tests the product-selected `equity_residual_momentum_v3` (0.55,
weekly) and `crypto_low_variance_hysteresis_v1` (0.05, 22-day low variance,
top two, 96-hour cadence, retention rank four) against the configuration and
hash-pinned data recorded in
`data/research_manifests/execution_model_robustness_backtest_2026_09.json`.

It preserves the shared gross 0.60 / cash-buffer 0.40 risk policy and makes
no change to product strategies, settings, data, universes, scanner policy,
or production execution code. The only permitted adapter is research-only and
must exactly reproduce C0 when disabled.

## Predeclared execution scenarios

`C0`, `COST3`, `COST5`, `CAP5`, `CAP2`, `CAP1`, `LAT1`, `LAT2`,
`FROZEN_DECISION_LIQUIDITY`, `COMBO_MEDIUM`, and `COMBO_SEVERE` are fixed in
the manifest. No scenario can be added, removed, or retuned after observing a
result. Capacity scenarios retain the 20 known-positive-volume lookback,
dynamic residual semantics, and the prohibition on current-fill-bar volume.

The previous equity 1x/2x/3x/5x and crypto 1x/2x/3x cost runs are evidence
references only. This mission's added evidence is structural execution stress.

## Gates and interpretation

C0 must match a same-source product control with exact component hashes before
any stress result can be interpreted. Every scenario reports each sleeve and,
where the existing shared-account simulator can prove it, the combined
portfolio. The frozen medium-stress red flags and classification vocabulary
are in the manifest. `COMBO_SEVERE` is a tail report, not an automatic
invalidation rule.

Allowed conclusions are `BACKTEST_EXECUTION_ROBUST`,
`BACKTEST_EXECUTION_FRAGILE`, `MIXED_BY_SLEEVE`, and `INCONCLUSIVE`.
Regardless of the result, PAPER/LIVE transferability remains
`UNKNOWN_COST_TRANSFERABILITY`.
