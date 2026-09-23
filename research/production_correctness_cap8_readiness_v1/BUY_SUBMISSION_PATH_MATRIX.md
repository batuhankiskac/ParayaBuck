# Production BUY submission paths, before the shared gate

| Path / entry | Broker submit route | Classification before fix | Required treatment |
| --- | --- | --- | --- |
| Pure BUY, new symbol | `process_closed_bars` → `_submit_orders` → `AlpacaBroker.submit_portfolio_order` | `STALE_TARGET_ONLY` | Fresh shared gross/cash/committed BUY gate before persistence/submission. |
| Pure BUY, add to held | Same | `STALE_TARGET_ONLY` | Same gate; marked existing position counts. |
| Equity/crypto normal decision | Same runtime class per market | `STALE_TARGET_ONLY` | One account-wide headroom model, serialized by engine portfolio lock. |
| SELL+BUY replacement | `process_closed_bars` submits SELL; `_submit_deferred_buys` later submits BUY | `CAPACITY_CHECK_PARTIAL` | Existing fresh gross/cash sizing omits working BUY commitments; use shared gate. |
| Restart-resumed deferred BUY | `advance_rebalance` → `_submit_deferred_buys` | `CAPACITY_CHECK_PARTIAL` | Same shared gate after authoritative SELL and fresh account. |
| Trade-update callback advance | `EngineRuntime._advance_portfolio_rebalance` → same deferred path | `CAPACITY_CHECK_PARTIAL` | Same shared gate; engine account-wide lock serializes. |
| Startup catch-up | `PortfolioRuntime.startup_catch_up` → `process_closed_bars` | `STALE_TARGET_ONLY` for pure BUY | Same gate after reconciliation startup barrier. |
| Repeated decision/retry | `_submit_orders` deterministic client ID and durable status | `CAPACITY_CHECK_PARTIAL` | No duplicate economic intent; resumed/ambiguous rows count as committed until resolved. |
| Protective BUY stop for a short position | `AlpacaBroker._submit_stop` | `NOT_RISK_INCREASING` | Short-cover protection; preserve existing risk-reduction path. |
| Emergency short flatten BUY | `AlpacaBroker._flatten` | `NOT_RISK_INCREASING` | Short-cover exit; preserve emergency behavior. |

All portfolio risk-increasing BUYs converge through `PortfolioRuntime` before `_submit_orders`; broker order submission itself remains the last idempotent gateway. The plan/overlay target may be safe while the immediate order is unsafe because retained holdings and committed orders consume actual headroom.
