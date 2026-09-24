# Restart audit

The real runtime and durable `PortfolioStateRepository` were exercised with a synthetic broker and temporary database. Rejected, canceled, and partially filled terminal BTC SELLs left BTC and LTC in broker positions. Each `advance_rebalance` returned `rebalance_waiting_for_slot`, made no DOGE submission, and retained a retryable `buy_ready` phase. Repeated advancement and a new `PortfolioRuntime` over the same repository continued to block DOGE. A fully filled BTC SELL followed by a fresh account without BTC admitted DOGE.

Source inspection also found startup catch-up and trade-update callbacks converge on this same advancement method; they do not have an independent BUY submission path. The existing restart/reconciliation test group passed (`80 passed`). No physical broker, PAPER operation, or LIVE operation was used.
