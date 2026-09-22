# Data provenance audit

Exact local bytes: `data/historical/crypto_expanded_1h.csv`; SHA256 `73425011c3e021a10a5d4d7d29c2c169957000f89b23afb9aea9d2354729067e`; 50,157,874 bytes, 705,541 rows, 25 symbols.

Duplicate symbol/timestamp rows:0; out-of-order timestamp/symbol rows:0; nonpositive OHLC rows:0; zero-volume rows:220,499. No stablecoin base pair detected among the actual symbols. Positive prices plus zero volume do not prove executable liquidity.

CSV has only timestamp,symbol,open,high,low,close,volume. Provider/feed, downloader command/version, request bounds, acquisition time and source-response hashes are absent. Adjacent `crypto_18_extended_report.json` describes Alpaca origins for a different18-symbol dataset; it cannot authenticate expanded-file bytes. Treat expanded provenance as UNVERIFIED. Current bytes are reproducibly pinned; historical identity requires a matching historical SHA, not a filename match. No data was downloaded or repaired.

Per-symbol gaps below count missing hourly grid points between each symbol's first/last timestamps; they do not invent prelisting history. Partial days are observed UTC dates with other than24 distinct hourly rows. Full24-row days still may include zero volume. Canonical daily aggregation excludes partial days; scanner/ranking may reject additional histories.

|Symbol|Rows|First UTC|Last UTC|Missing hours|Partial days|Zero-volume rows|
|---|---:|---|---|---:|---:|---:|
|AAVE/USD|44072|2021-07-15T13:00:00+00:00|2026-07-31T00:00:00+00:00|124|73|16847|
|ADA/USD|4020|2026-02-13T12:00:00+00:00|2026-07-31T00:00:00+00:00|1|3|1450|
|ARB/USD|3947|2026-02-16T13:00:00+00:00|2026-07-31T00:00:00+00:00|1|3|3256|
|AVAX/USD|41163|2021-11-18T13:00:00+00:00|2026-07-31T00:00:00+00:00|9|6|13309|
|BCH/USD|48746|2021-01-01T06:00:00+00:00|2026-07-31T00:00:00+00:00|137|83|16246|
|BTC/USD|48869|2021-01-01T06:00:00+00:00|2026-07-31T00:00:00+00:00|14|7|956|
|CRV/USD|25871|2023-08-18T13:00:00+00:00|2026-07-31T23:00:00+00:00|12|7|18133|
|DOGE/USD|48859|2021-01-01T06:00:00+00:00|2026-07-31T00:00:00+00:00|24|10|8608|
|DOT/USD|25848|2023-08-18T13:00:00+00:00|2026-07-31T00:00:00+00:00|12|8|12840|
|ETH/USD|48867|2021-01-01T06:00:00+00:00|2026-07-31T00:00:00+00:00|16|8|3000|
|FIL/USD|3947|2026-02-16T13:00:00+00:00|2026-07-31T00:00:00+00:00|1|3|3156|
|GRT/USD|44175|2021-07-15T13:00:00+00:00|2026-07-31T23:00:00+00:00|44|29|16605|
|HYPE/USD|3923|2026-02-17T13:00:00+00:00|2026-07-31T00:00:00+00:00|1|3|3144|
|LINK/USD|48857|2021-01-01T06:00:00+00:00|2026-07-31T00:00:00+00:00|26|12|12935|
|LTC/USD|48865|2021-01-01T06:00:00+00:00|2026-07-31T00:00:00+00:00|18|8|14353|
|MKR/USD|40641|2021-01-01T06:00:00+00:00|2025-09-05T19:00:00+00:00|365|154|17124|
|NEAR/USD|6298|2022-01-20T01:00:00+00:00|2023-06-23T20:00:00+00:00|6178|11|246|
|ONDO/USD|3923|2026-02-17T13:00:00+00:00|2026-07-31T00:00:00+00:00|1|3|3141|
|PEPE/USD|13156|2025-01-29T09:00:00+00:00|2026-07-31T23:00:00+00:00|11|5|9802|
|RENDER/USD|3878|2026-02-19T09:00:00+00:00|2026-07-31T00:00:00+00:00|2|4|3088|
|SHIB/USD|29634|2023-03-14T17:00:00+00:00|2026-07-31T23:00:00+00:00|13|5|19501|
|SOL/USD|38787|2021-01-01T06:00:00+00:00|2026-07-31T00:00:00+00:00|10096|12|3491|
|TRX/USD|7830|2022-04-01T00:00:00+00:00|2023-04-19T02:00:00+00:00|1365|67|5|
|UNI/USD|48799|2021-01-01T06:00:00+00:00|2026-07-31T00:00:00+00:00|84|47|18310|
|XRP/USD|22566|2024-01-01T06:00:00+00:00|2026-07-31T00:00:00+00:00|37|13|953|

Machine-readable detail is in `DATA_AUDIT.json`. Historical provider comparability remains unknown; no survivorship or causal point-in-time listing-universe guarantee follows from this retrospective file.
