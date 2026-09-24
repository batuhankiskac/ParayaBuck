# Submission path audit

| Path | Submission boundary | Result |
| --- | --- | --- |
| Pure immediate BUY | `process_closed_bars` calls gross/cash sizing then `reserve_opening_order_slots` before persistence | `FIXED_BY_SHARED_ADMISSION` |
| SELL then deferred BUY | `_submit_deferred_buys` takes fresh account, replans, sizes, then calls the same slot helper before submission | `FIXED_BY_SHARED_ADMISSION` |
| Restart resumed deferred BUY | `advance_rebalance` restores persisted phase and reaches `_submit_deferred_buys` | `FIXED_BY_SHARED_ADMISSION` |
| Startup catch-up | `startup_catch_up` reaches `process_closed_bars` or `advance_rebalance` | `FIXED_BY_SHARED_ADMISSION` |
| Trade update advancement | engine runtime calls `advance_rebalance` after trade updates | `FIXED_BY_SHARED_ADMISSION` |
| Same-cycle cross-market decisions | reserved keys join broker positions and durable local nonterminal order keys before final admission | `FIXED_BY_SHARED_ADMISSION` |
| Broker position aliases and working order aliases | canonical `(market, symbol)` keys deduplicate representations | `SAFE` |

Risk overlay and planner do not constitute the final physical admission boundary. `_size_buys_to_safe_capacity` checks gross/cash/name economics, while the shared slot helper now checks both account and explicit market counts. No strategy or risk parameter changed.
