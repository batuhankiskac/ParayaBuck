# Alpaca bar semantics — retrieved 2026-09-23

- [Alpaca-py Bar model](https://alpaca.markets/sdks/python/api_reference/data/models.html): `timestamp` is the **opening** timestamp of a bar; OHLCV describes its interval.
- [Alpaca market-data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq): timestamps are the left side of a bar interval, and hourly bars aggregate minute bars in that hour.
- [Alpaca crypto historical bars](https://docs.alpaca.markets/us/reference/cryptobars-1): `1Hour` and `1Day` are supported aggregations. This is documentation only; this campaign makes no API request and substitutes no modern data.
- [Alpaca real-time crypto data](https://docs.alpaca.markets/us/docs/real-time-crypto-pricing-data): a prior minute bar can be updated after the minute boundary when a late trade arrives. The repository's finalized-bar pipeline must therefore be treated as the live visibility boundary, rather than assuming publication exactly at the label.

The pinned `data/historical/crypto_expanded_1h.csv` SHA-256 is `73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e`. The original provider-download receipt is unavailable, as noted by `docs/research/canonical_crypto_baseline.md`; the authority here is the pinned bytes under the documented opening-label convention. A complete source hour labeled 23:00 cannot be consumed before 00:00, and a 24-component UTC daily aggregate is first complete at the maximum component completion time.
