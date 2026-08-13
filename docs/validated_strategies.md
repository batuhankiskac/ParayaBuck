# Validated Strategy Specifications (FROZEN)

Status: **FROZEN** · 2026-08-12 · Productization & PAPER-readiness mission.

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
> research mission before it may run on the PAPER product.

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

## What is NOT frozen here

Strategy parameters are frozen; everything around them (robustness evidence,
execution telemetry, observability, dashboard, operations) is product work
and intentionally does not touch these values. See `docs/strategy_robustness.md`,
`docs/paper_operations.md`, `docs/dashboard.md`.
