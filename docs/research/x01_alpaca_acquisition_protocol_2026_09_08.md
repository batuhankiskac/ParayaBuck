# X01 Alpaca/SIP acquisition protocol — 2026-09-08

Status: frozen before any return inspection. This protocol covers only data
acquisition and normalization for SHY, IEF, and TLT. It authorizes no
economic run, product edit, configuration change, PAPER/LIVE action, or
secret persistence.

## Source and query contract

Use the repository's existing Alpaca historical path and documented endpoint
`GET https://data.alpaca.markets/v2/stocks/bars`. Query exactly the three
symbols `SHY,IEF,TLT`, timeframe `1Day`, `start=2016-01-01`,
`end=2026-08-01` (exclusive), `feed=sip`, `sort=asc`, `limit=10000`, once with
`adjustment=raw` and once with `adjustment=split`. Follow every returned
`next_page_token` until empty. Authentication is read only from the existing
`ALPACA_API_KEY` and `ALPACA_SECRET_KEY` environment variables; neither
header, value, cookie, nor credential-bearing URL is written.

Raw page responses contain only provider data and are retained under the
ignored task path `data/research/external/crossasset_public_2026_09_08/x01_alpaca/`.
The sanitized normalized outputs contain timestamps, symbols, adjustment
basis, OHLCV, and source page identity. Receipts contain endpoint, query
coverage, HTTP status, pagination count, row counts, date bounds, and SHA-256
hashes only.

The declared FRED input
`data/research/external/crossasset_public_2026_09_07/x01_treasury/fred_curve_panel.csv`
is split before any X01 input use at the inclusive cutoff `2026-07-31`.
In-cutoff rows are written to `x01_fred_curve_in_cutoff.csv`; every later row
is written to `x01_fred_curve_quarantine.csv` and cannot reach X01 outcomes.
This explicit split is required because the source currently contains 24
nonempty observations after the requested cutoff.

Treasury year files use an exact frozen header sequence for each vintage:
2016–2017 common maturities; 2018–2021 add `2 Mo`; 2022–2024 add `2 Mo`
and `4 Mo`; and 2025–2026 add `1.5 Month`, `2 Mo`, and `4 Mo`. The common
maturity cells must be present and finite; optional maturities may be blank
before their publication start and are finite whenever populated. Dates must
be unique, in the requested year, and strictly increasing in the admitted
file. The 2016–2026 Treasury bundle is admitted only when every year passes
these gates and has a nonempty SHA-256 receipt; any failed year sets the
bundle status to `FAILED`.

## Validation gates

The main output must contain only 2016-01-01 through 2026-07-31 dates, be
sorted by symbol/timestamp, have unique symbol/timestamp keys, finite positive
prices, nonnegative volume, and equal raw/split row keys. The split/raw price
ratio is diagnostic evidence only and must not be applied again. Distributions
must come from a separately paginated official corporate-action endpoint or
an existing hash-pinned corporate-action source; OHLCV does not imply
distribution cash. Current IEF duration/holdings are never backfilled into
historical dates.

If the canonical endpoint is unavailable, try the same official endpoint with
one-symbol pagination and record the exact status/error. An IEX response may
be used only as an access probe and is never admitted as SIP evidence. HTTP
429, challenge HTML, or undocumented data is quarantined and excluded.

## Rule freeze after data gate

If historical ETF bars, distributions, and PIT duration/holdings vintages are
complete, freeze a monthly next-session-open ETF rule using only curve and
ETF information available at each decision. If duration/holdings vintages
remain absent, freeze a clearly labeled maturity-bucket approximation and
report its limitations; do not present it as historical ETF carry. No return
series is inspected before the source and protocol receipts pass review.
