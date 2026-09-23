# Physical availability audit

The input event timestamps are bar labels, not proof that the complete OHLCV was known then.

- The equity CSV begins with completed daily rows labeled `2016-01-04T05:00:00+00:00`, or midnight New York at the beginning of that trading session. The `close`, `high`, `low`, and full-session `volume` in those rows were unavailable at midnight. Current isolated V3 parity intentionally consumes that canonical label.
- `aggregate_historical_bars` groups complete crypto hourly components and emits `_combine_bars(..., bucket, "1d")` where `bucket` is the UTC day start (`src/parayabuck/data/aggregator.py:144-160`). A daily close/volume derived from the final hourly component cannot be known at the bucket-start timestamp.
- The V2 merged clock sorts these labels directly (`replay.py:run`). Consequently a full crypto daily signal may enter before its final source hour, and an equity full-session signal may enter before the exchange close. The label-only `no_future_data_access` invariant compares history timestamps, so it cannot detect this physical lookahead.

The isolated controls cannot certify physical cross-market order because they inherit the same labels. Equal-label and near-label order can affect which incumbent receives a scarce sixth slot. No bound proves the effect immaterial for a future position-cap comparison. Required repair: build separate *data availability* and *execution* timestamps from completed source bars and exchange sessions, with half-days, DST, weekends and holidays; then establish fresh isolated semantic controls and rerun combined cap-six accounting/determinism. Do not run migration economics first.
