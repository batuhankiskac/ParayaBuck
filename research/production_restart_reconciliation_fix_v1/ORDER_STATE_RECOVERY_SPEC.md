# Durable order recovery

For every account-scoped nonterminal portfolio order: match its deterministic client ID against exhaustive broker OPEN orders. If absent, use the existing targeted lookup by client ID and persisted broker ID. Verify client ID, broker ID, canonical symbol, side, status, cumulative quantity, and fill price. An unresolved 404 does not imply cancellation or fill; final local/broker comparison blocks startup. Transport/API exceptions propagate. Working, partial, terminal fill, cancel, expiry, and rejection states update the durable repository before drift. Repository cumulative fills and terminal checks make repeated recovery idempotent. Working statuses remain reserved and do not create a replacement intent.

Unknown broker positions/orders and identity mismatches remain drift or hard errors. No order is submitted by this recovery path.
