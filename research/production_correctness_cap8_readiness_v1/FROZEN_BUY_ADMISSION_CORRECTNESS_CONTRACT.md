# Frozen BUY admission correctness contract

Frozen before production BUY-admission edits on 2026-09-24, from corrected local main `96254b7de6e045212002cfa807abd448bc6e5d0d`.

For every new **risk-increasing portfolio BUY batch**, use a fresh authoritative broker account snapshot and exhaustive broker OPEN orders, plus durable account-scoped nonterminal portfolio BUY rows. Current marked absolute broker positions consume realized gross. Already committed BUYs consume only their **remaining** quantity/notional; a partially filled portion is already in broker positions. Deduplicate broker and local representations by client order ID, retaining the conservative larger remaining-notional estimate. Local `submitting`/ambiguous rows remain committed until authoritative terminal resolution. Broker working manual BUYs also consume shared capacity. An unpriceable working BUY makes available BUY headroom zero; failure fetching account or OPEN orders blocks BUY admission. A deferred SELL-dependent BUY is a proposal, not committed capacity: it is re-planned and gated after the SELL becomes authoritative.

Candidate batch incremental notional, based on repository-supported decision/reference price and broker quantity increments, must satisfy both:

`realized_gross_notional + committed_buy_notional + candidate_buy_notional <= maximum_gross * equity`

`committed_buy_notional + candidate_buy_notional <= max(0, broker_cash - minimum_cash * equity)`

The second check preserves the configured cash-buffer target at admission without forcing liquidation after passive mark/cash drift. It never substitutes margin buying power for cash. Existing LIVE hard dollar cap, name/sleeve caps, broker minima and independent global slot claims still apply. If available capacity is positive, scale using the existing increment/minimum-order semantics; if zero, submit no BUY and emit a stable operator-visible reason such as `gross_headroom_exhausted` or `cash_headroom_exhausted`. Risk-reducing SELLs remain available even if gross/cash targets are already breached. Passive mark drift above 0.60 alone does not trigger forced liquidation or a new tolerance. External/manual positions count in gross and slots but are not forcibly sold.

The physical production engine serializes portfolio decisions, trade-update rebalance advances and startup catch-up under its account-wide portfolio lock. After a successful submission, the durable row counts as committed even if the broker account snapshot has not yet reflected it. The admission gate must run before persisting candidate BUY rows so a suppressed candidate does not become a phantom commitment. No strategy target, count cap, gross ratio, cash buffer, crypto cap, rebalance band or alpha parameter changes are authorized.
