# Validated strategy (FROZEN)

The only strategy registered and allowed on the PAPER product.
`config/settings.yaml` is the runtime source of these values;
`tests/test_strategy_spec_freeze.py` guards them. Nothing at runtime reads this
file.

> A strategy name identifies a validated economic specification. Any parameter
> change, however small, is a new unvalidated strategy that needs separate
> validation and approval before it runs on PAPER.

## VT35 with the E2 trend vote — `equity_leveraged_trend_v1` 1.1.0 (Leveraged Index Trend v1)

Module `src/parayabuck/portfolio/equity_leveraged_trend.py`. PAPER identity
since 2026-09-25; E2 trend vote since 2026-10-12
(`docs/research/e2_integration_v1.md`).

Hold QLD (2x Nasdaq-100) while QQQ is in trend, at weight `min(1, 0.35 /
annualized 20-session realized QLD volatility)` of the equity allocation; the
rest is cash. QQQ is in trend when at least two of three votes hold on its
completed split-adjusted daily closes:

- **SMA:** close > 200-session SMA (VT35's original rule);
- **TSM:** 252-session price return > 0;
- **DON:** close > midpoint of the max/min of the last 126 closes.

The target is all cash out of trend, with insufficient history for any vote
(253 sessions are needed), or when QLD is unavailable. Decided every session
on completed bars after the NYSE close (13:00 ET on early-close days); orders
fill at the next open. `trend_rule: sma` without `tsm_bars`/`donchian_bars`
restores plain VT35 (strategy version 1.0.0) exactly.

| Parameter | Value |
|---|---|
| `signal_symbol` | `QQQ` |
| `hold_symbol` | `QLD` |
| `sma_bars` | 200 |
| `vol_target` | 0.35 |
| `vol_window` | 20 |
| `trend_rule` | `majority_sma_tsm_don` (`sma` = plain VT35) |
| `tsm_bars` | 252 |
| `donchian_bars` | 126 |
| `rebalance_frequency` | `hours:24` (daily; the only accepted value) |
| `strategy.equity.maximum_allocation` | 0.98 |
| `universe.equity.symbols` | `[SPY, QQQ, QLD]` (SPY = benchmark) |

## Risk and execution limits

| Key | Value |
|---|---|
| `risk.max_position_pct` | 0.98 |
| `risk.max_gross_exposure_pct` | 0.98 |
| `risk.cash_buffer_pct` | 0.02 |
| `risk.max_positions` | 8 |
| `risk.daily_loss_limit_pct` | 0.20 (catastrophe brake; SELL-only latch) |
| `risk.max_drawdown_pct` | 0.75 (catastrophe brake; halt until operator risk-epoch reset) |
| `execution.rebalance_band_pct` | 0.005 |
| `execution.minimum_order_notional` | 10 |

The loss latches are deliberately wide: a daily-reset 2x index ETF routinely
moves more than tighter latches would allow.

## Runtime posture

`runtime.mode: paper`, equity only, `live.enabled: false`,
`live.hard_dollar_cap: 0`.

## Evidence and history

| Date | Change | Evidence |
|---|---|---|
| 2026-09-25 | VT35 adopted at 0.93 | `docs/research/profit_max_v1.md` (Stage B 2017–2026: 28.98% CAGR, 29.2% max DD vs VOO 14.5% / 32.0%) |
| 2026-09-29 | Allocation and per-asset cap 0.93 → 0.98; crypto sleeve disabled | `docs/research/paper_config_v2.md` (29.35% CAGR, 27.7% max DD) |
| 2026-09-30 | Crypto code removed; VT35 results unchanged | `docs/research/crypto_removal_v1.md` |
| 2026-10-12 | E2 2-of-3 trend vote (owner-approved exception to the failed signal_ensemble_v1 bootstrap gate) | `docs/research/e2_integration_v1.md` (Stage B 2017–2026: 31.05% CAGR, 32.2% max DD vs VT35 29.35% / 27.7%) |

Earlier equity identities and the crypto strategies are removed and not
selectable; they are recoverable only from git history (see
`docs/research/INDEX.md` and `docs/OPERATIONS_LOG.md`). This spec does not imply
LIVE readiness.
