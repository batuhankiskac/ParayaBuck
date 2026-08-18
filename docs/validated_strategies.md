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

## Cadence research variants (2026-08-18 cadence mission)

RESEARCH-validated identities, NOT the production selection. v1 above
remains the frozen PAPER specification; nothing in this section changes v1.

| Identity | Alpha (identical to v1) | Cadence | Evidence (net, canonical window) |
|---|---|---|---|
| `equity_residual_momentum_v2` | same parameters as v1 | weekly (`rebalance_frequency: weekly`) | 2017-01-03..2026-07-30: ret +84.6% (v1 +59.2%), CAGR 6.61% (4.98%), Sharpe 0.62 (0.47), maxDD 19.7% (25.4%); better at 1x/2x/3x costs; WF 9/16 folds |
| `crypto_low_variance_v2` | same parameters as v1 | 96h (`rebalance_hours: 96`) | 2021-04-01..2026-07-31: ret +6.56% (v1 +4.61%), CAGR 1.20% (0.85%), Sharpe 0.23 (0.17), maxDD 13.3% (13.2%); better at 1x/2x, WF 7/10 (v1 8/10) |

The v2 classes carry their cadence in the strategy parameters so the
configuration digest and persisted decision identity are distinct from v1.
Promoting a v2 (config name + cadence parameter) is an operator decision
after this research; v1 stays reproducible. `crypto_low_variance_v2` with
`rebalance_hours: 72` was evaluated and REJECTED (strictly dominated).
Full evidence: `data/research/cadence_research_2026_08/` and
`docs/capital_utilization_cadence_verdict.md` (includes the equity weekly
ASX-dependence and recent-window caveats).

## What is NOT frozen here

Strategy parameters are frozen; everything around them (robustness evidence,
execution telemetry, observability, dashboard, operations) is product work
and intentionally does not touch these values. See `docs/strategy_robustness.md`,
`docs/paper_operations.md`, `docs/dashboard.md`.
