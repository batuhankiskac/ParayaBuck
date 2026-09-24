# Root cause

Production and research both had the same invariant gap. `PortfolioRiskOverlay.apply` intentionally treats a managed position omitted from the target as replaceable through SELL then BUY. That is valid for target selection but does not prove the broker position is flat. On deferred advancement, the runtime restores the target, observes the fresh account, and replans. After a rejected or partially terminal BTC SELL, the replan still contains a BTC SELL, but `_submit_deferred_buys` retained only BUY requests. Gross/cash sizing could allow DOGE, and final `reserve_opening_order_slots` checked only `maximum_positions`; it did not check `maximum_crypto_positions`. DOGE therefore reached submission with BTC and LTC both nonzero.

The generic repair adds an explicit market count gate to the shared final helper and passes the shipped crypto cap from every runtime call. When a deferred replan still needs a SELL and final slot admission denies the opening, the rebalance stays `buy_ready` and returns `rebalance_waiting_for_slot`. A later fresh account observation can release the slot; the rejected SELL does not. The newly needed SELL is not silently treated as a completed exit.

The research replay reused the global-only final helper at deferred activation. Its original failed trace is separate evidence and remains invalid for candidate materiality.
