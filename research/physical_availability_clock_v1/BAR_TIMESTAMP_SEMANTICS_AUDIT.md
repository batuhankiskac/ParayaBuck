# Bar timestamp semantics

- Equity v5 SIP daily CSV: `2016-01-04T05:00:00+00:00` labels the New York session date at local midnight. The row contains the full session OHLCV, so its label is neither its completion nor an executable open. `load_csv_bars` retains this timestamp. The v5 manifest identifies the split-adjusted SIP input and its SHA-256 `bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139`.
- Crypto hourly CSV: source bars are Alpaca hourly interval-opening labels; its first row is `2021-01-01T06:00:00Z`. Alpaca's [Bar model](https://alpaca.markets/sdks/python/api_reference/data/models.html) explicitly calls `timestamp` the opening timestamp of the bar. `load_csv_bars` retains the source value and defaults this market to `1h`.
- Crypto daily: `aggregate_historical_bars(..., "1d", require_complete=True)` groups 24 contiguous hourly components into a UTC day. `_combine_bars` assigns the UTC bucket start to the aggregate. Its close and full volume require the last component to finish.

All canonical input bytes and labels remain unchanged. Replay metadata carries physical availability separately.

The full equity CSV scan found 11,686,832 rows at 2,659 distinct physical
timestamps, all aligned to New York local midnight and all valid XNYS
sessions. The raw timestamp strings have two equivalent ISO encodings (`Z`
and `+00:00`); the count above normalizes them as datetimes. The full crypto
hourly CSV scan found 705,541 rows, all aligned to UTC hour boundaries.
