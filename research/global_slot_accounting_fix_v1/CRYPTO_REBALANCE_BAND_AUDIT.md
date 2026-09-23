# Crypto rebalance and band semantics

Current config selects `crypto_low_variance_hysteresis_v1`, 22-day variance,
top two, 96-hour decisions, maximum allocation 0.05 and two crypto slots.
The strategy retains a held eligible symbol only when its current variance
rank is at most four, then fills vacancies from lowest variance rank upward.
Its selected names receive equal strategy weights before account risk caps
them (often 0.025 each). These parameters remain unchanged.

The shared execution policy defaults are `rebalance_band_pct = 0.005` and
`minimum_order_notional = 10.0` (`execution/portfolio.py:69-84`). For every
approved current or targeted symbol, the production planner computes
`current_weight = qty * price / account_equity` and skips **both** BUY and SELL
when `abs(target_weight - current_weight) < 0.005`. Otherwise it computes
`delta = target_weight * equity / price - current_qty`, limits SELLs to
managed and available units, floors quantity to the broker increment, and
skips if below broker minimum size or `max(10, broker_min_notional)`.
An omitted target has `target_weight = 0`; a held residual below 0.005 is
therefore retained without a SELL. This is order-existence suppression, not
a change to strategy selection. `PortfolioBacktestEngine._apply_target`
applies the same band and notional order-existence policy in offline tests.

If the residual's SELL is suppressed, prior production code still admitted
new replacement BUYs because the overlay had treated the omitted holding as
replaceable. The correction keeps the band intact and counts the residual
until a fresh zero-position snapshot exists.
