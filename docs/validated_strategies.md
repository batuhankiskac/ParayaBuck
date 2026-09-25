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
> validation and approval before it may run on the PAPER product.

## Equity — `equity_residual_momentum_v1` (REMOVED 2026-09-25)

Historical record only. Market-residual momentum, 1d signal timeframe,
monthly decisions. The class is no longer registered or constructible; it is
recoverable from git history (`5d61c8c` on `profit-max-v1`, `ec4e2df` on
`main`).

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

| `max_crypto_exposure_pct` | 0.10 |
| `max_crypto_position_pct` | 0.05 |
| `max_crypto_positions` | 2 |
| `max_gross_exposure_pct` | 0.98 |
| `cash_buffer_pct` | 0.02 |

## Runtime posture (frozen)

- `runtime.mode: paper`
- equity enabled: YES, crypto enabled: YES
- `live.enabled: false` — LIVE is never enabled by this mission.

## Historical cadence selections (v2, promoted 2026-08-18)

The cadence-validated v2 identities were the prior PAPER selections: same
alpha as the frozen v1 baselines, new cadence.

| Identity | Alpha (identical to v1) | Cadence | Status |
|---|---|---|---|
| `equity_residual_momentum_v2` | same parameters as v1 | weekly (`rebalance_frequency: weekly`) | removed 2026-09-25; historical record only |
| `crypto_low_variance_v2` | same parameters as v1 | 96h (`rebalance_hours: 96`) | frozen named baseline |

The crypto v2 classes carry their cadence in the strategy parameters so the
configuration digest and persisted decision identity are distinct from v1.
Parameter changes are separate named identities; `config/settings.yaml` is the
authoritative source for the currently selected product values.

## Current PAPER equity identity

`equity_leveraged_trend_v1` (**Leveraged Index Trend v1**), implemented by
`src/parayabuck/portfolio/equity_leveraged_trend.py`, is the current PAPER
equity identity. It holds QLD (the 2x Nasdaq-100 ETF) while QQQ's completed
daily close is above its 200-session SMA. In an uptrend the position is
`min(1, 0.35 / 20-session annualized realized QLD volatility)` of the equity
allocation and the rest is cash; the target is also cash when history is
insufficient or QLD is unavailable.

| Parameter | Validated value |
|---|---|
| `signal_symbol` | `QQQ` |
| `hold_symbol` | `QLD` |
| `sma_bars` | 200 |
| `vol_target` | 0.35 |
| `vol_window` | 20 |
| `rebalance_frequency` | `hours:24` (cadence `daily`) |
| `maximum_allocation` | 0.93 |

QQQ and QLD are `universe.equity.reference_symbols` with
`universe.equity.trade_reference_symbols: true`; `risk.max_position_pct` is
0.93 so the per-asset cap admits the whole equity allocation in QLD. The
0.20 daily-loss and 0.75 drawdown latches are catastrophe brakes, not routine
entry gates — a daily-reset 2x index ETF moves more than any tighter latch
would allow — and a drawdown halt persists until an operator risk-epoch reset.
Evidence: `docs/research/profit_max_v1.md` (shared-ledger replay
2017-01-03 → 2026-07-30: 29.34% CAGR, Sharpe 1.078, max drawdown 29.2%, 2022
−22.0% for this arm, against VOO buy-and-hold at 14.54% and 32.0%).

Removed 2026-09-25: `equity_residual_momentum_v1/v2/v3`,
`equity_residual_momentum_v3_dividend_sleeve_v1`, the static-sleeve
composition in the risk overlay, and `risk.slot_exempt_equity_symbols`. They
are recoverable from git history; rollback restores `config/settings.yaml` and
the removed modules from `ec4e2df`. This does not imply LIVE readiness.

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
