# Validated Strategy Specifications (FROZEN)

Status: **FROZEN** · Product contract.

These are the ONLY economically validated strategy specifications for the
PAPER product. The mission freezes them; `config/settings.yaml` is the
authoritative runtime source of the same values and `tests/test_strategy_spec_freeze.py`
guards them against drift.

Documentation only. Nothing at runtime reads this file.

## Product rule

> **A strategy name identifies a validated economic specification.**
> Same strategy name + different economic parameters ≠ validated strategy.
> Any change to the parameters below — however small, however well it
> backtests — is a new, unvalidated strategy and requires a separate
> separate validation and approval before it may run on the PAPER product.

## Equity — `equity_residual_momentum_v1`

Market-residual momentum, 1d signal timeframe, monthly decisions.

| Parameter | Validated value |
|---|---|
| `formation_bars` | 252 |
| `skip_bars` | 21 |
| `beta_window_bars` | 273 |
| `residual_vol_window` | 63 |
| `top_n` | 8 |
| `max_single_weight` | 0.10 |
| `ivol_reject_quantile` | 0.70 |
| `exclude_symbols` | `[SPY]` |
| `maximum_allocation` | 0.50 |

## Crypto — `crypto_low_variance_v1`

Low realized variance (AHXZ monthly construction on 1d bars), weekly decisions.

| Parameter | Validated value |
|---|---|
| `variance_lookback_days` | 22 |
| `top_n` | 2 |
| `rebalance_hours` | 168 |
| `maximum_allocation` | 0.10 |

## Risk limits (shared, frozen)

| Limit | Value |
|---|---|
| `max_crypto_exposure_pct` | 0.10 |
| `max_crypto_position_pct` | 0.05 |
| `max_crypto_positions` | 2 |
| `max_gross_exposure_pct` | 0.60 |
| `cash_buffer_pct` | 0.40 |

## Runtime posture (frozen)

- `runtime.mode: paper`
- equity enabled: YES, crypto enabled: YES
- `live.enabled: false` — LIVE is never enabled by this mission.

## Historical cadence selections (v2, promoted 2026-08-18)

The cadence-validated v2 identities were the prior PAPER selections: same
alpha as the frozen v1 baselines, new cadence. v1 and v2 remain frozen and
reproducible; the current equity selection is recorded below.

| Identity | Alpha (identical to v1) | Cadence | Status |
|---|---|---|---|
| `equity_residual_momentum_v2` | same parameters as v1 | weekly (`rebalance_frequency: weekly`) | frozen named baseline |
| `crypto_low_variance_v2` | same parameters as v1 | 96h (`rebalance_hours: 96`) | frozen named baseline |

The v2 classes carry their cadence in the strategy parameters so the
configuration digest and persisted decision identity are distinct from v1.
Parameter changes are separate named identities; `config/settings.yaml` is the
authoritative source for the currently selected product values.

## Current PAPER equity identity

`equity_residual_momentum_v3` is the current PAPER equity identity. It keeps
the production eligibility, positive 12-1 gate, risk overlay, inverse-vol
sizing, and weekly cadence while replacing only the cross-sectional score with
the frozen raw 6-1/9-1/12-1 percentile-rank consensus. This does not imply LIVE
readiness.

## Current PAPER crypto identity

`crypto_low_variance_hysteresis_v1` is the current PAPER crypto identity. It
keeps the validated low-variance alpha (22d realized variance, 1d signal, 96h
rebalance, top2 equal) and adds hysteresis retention: at 96h a held symbol
stays if scanner-eligible and rank ≤ 4, exits if ineligible or rank > 4;
empty slots are filled from rank 1 upward. Allocation is 0.05 with risk caps
of 0.10 exposure, 0.05 per position, and 2 positions. The v1/v2 low-variance
classes remain frozen named baselines.

## What is NOT frozen here
`docs/paper_operations.md`, `docs/dashboard.md`.
