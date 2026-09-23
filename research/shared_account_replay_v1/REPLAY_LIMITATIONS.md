# Replay limitations

1. **Broker order lifecycle:** production submits asynchronously, receives
   partial/terminal updates, allows a grace period for ambiguous submissions,
   and can refresh account state between sell and buy phases. The historical
   replay makes deterministic next-bar fills, records partial fills from its
   participation model, and blocks BUY fills while any account SELL remains
   working. It cannot reproduce broker acknowledgement latency, reject timing,
   queue priority, or buying-power refresh.
2. **Combined daily-risk session:** one New York calendar day is used for the
   shared combined account. Production runtime risk latches, operator reset
   epochs, and process/restart timing are not reconstructed from historical
   bars. Single-market controls retain the canonical per-market session rule.
3. **Historical crypto provenance:** the supplied hourly data has no original
   provider download receipt or historical broker tradability metadata. PIT
   eligibility is limited to the existing configured pool and supplied
   trailing-bar availability.
4. **Equity history:** the frozen equity bars and entity-lifecycle artifacts
   are used as supplied. Corporate actions are applied once to managed
   quantities/cash; the equity bars already include split adjustments.
5. **Mark-to-market limits:** realized gross can move between target decisions
   as prices change. The replay records this drift and does not invent a
   corrective order. The risk overlay constrains new targets using the latest
   causal account snapshot.
6. **Execution costs:** canonical offline slippage, spread, commission,
   regulatory fee, and crypto taker-fee settings are retained. They are not
   evidence of historical executable liquidity or live fill quality.

7. **Daily-bar availability clock:** equity daily source timestamps are
   New York midnight session-date labels (04:00 or 05:00 UTC), while crypto
   daily timestamps are UTC midnight. The canonical replay treats each full
   daily bar as available at its label to preserve isolated parity. Same-date
   cross-market decisions therefore follow this label ordering rather than
   actual intraday close availability. This can affect same-day slot
   reservations and must be reviewed before using the replay for cap-migration
   economics.

These limitations do not make this a broker execution simulator. They define
which shared-account mechanics a later study may consume. A migration economics
run must first resolve or bound whether sell deferral, combined risk-session
choices, daily-bar availability labels, and broker buying-power refresh can
change the cap comparison. No cap comparison is run here.
