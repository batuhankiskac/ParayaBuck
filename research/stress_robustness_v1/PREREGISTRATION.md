# VT35 stress / robustness v1 — pre-registration (2026-09-29)

Written before any result below was computed. Seen before writing: only the
shape of the downloaded Yahoo chart JSON (fields, first/last dates) and the
fact that FRED DTB3 is reachable. No returns, drawdowns or signals were
computed.

## Question

The PAPER identity VT35 (`equity_leveraged_trend_v1`: QLD while QQQ's completed
close > its 200-session SMA, weight `min(1, 0.35 / 20-session realized QLD
vol)`, else cash; daily) was validated only on 2016–2026. Does it survive
2000–2002 and 2007–2009, random start dates and resampled histories?

## Data (`download_data.py`, `DATA_MANIFEST.json`)

- QQQ, SPY, QLD, VOO daily OHLC (split-adjusted, not dividend-adjusted) and
  cash dividends from the Yahoo Finance chart API; FRED DTB3 (3-month T-bill,
  secondary market, percent). Source URL, download date, row counts and
  SHA-256 are recorded in the manifest. Raw CSVs under `data/historical/`.
- Total return: overnight leg `(open_t + div_t) / close_{t-1}`, intraday leg
  `close_t / open_t` (same as profit_max_v1 Stage A).

## Synthetic QLD

Daily reset 2x on QQQ total return, costs only as documented:
`close_t = close_{t-1} * (1 + 2 r_cc − (0.0095 + y_t) / 252)`,
`open_t = close_{t-1} * (1 + 2 r_on)`, where `r_cc`/`r_on` are the QQQ
close-to-close / overnight total returns and `y_t` is the last DTB3 (financing
on 1x borrowed notional). No other calibration. Validated against real QLD
total return from its first bar (2006-06) to the latest bar: annualized
tracking error of daily return differences and CAGR difference.

## Simulation (`stress.py`)

Stage A2 mechanics unchanged: signal on the completed close, trade at the next
open, rebalance to target when any weight drifts > 0.5 pp, cost 4 bps × m per
traded notional, **m = 2** (the profit_max_v1 Stage A/A2 gating costs).
Equity sleeve 0.93 of NAV; the other 0.07 (crypto 0.05 + buffer 0.02) is
modelled as zero-return cash (crypto not modelled). Window: first session with
200 prior QQQ closes (≈ 2000-01) → latest bar. Arms:

- **VT35** at 0.93 on synthetic QLD (primary); VT35 at 1.0 for reference.
- **SPY** buy-and-hold total return at 1.0 (benchmark; SPY covers the whole
  window, VOO starts 2010 and is only used as a sanity check).
- **2X** unfiltered synthetic QLD hold at 0.93 (control).
- **VT35-real**: VT35 on real QLD once 21 real QLD closes exist (cross-check).
- Open prices: used as given; if Yahoo opens are missing/degenerate for early
  years the affected share is reported.

Reported: CAGR, max DD, worst calendar year, 2000-01-01..2002-12-31 and
2007-01-01..2009-12-31 total return and max DD, 2017-01-01..end CAGR, longest
underwater period and the date the max-DD peak was regained.

## Idle-cash overlay

VT35-B: un-invested equity-sleeve cash (0.93 − QLD weight) earns
`(DTB3 − 0.10%) / 252` per session (SGOV/BIL proxy; may be negative when
bills < 0.1%); BIL turnover is charged the same 8 bps. Incremental CAGR over
the full window and 2017-01-01..end.

## Random start dates

Every first session of a month such that the whole window fits: 5-year (1260
sessions) and 10-year (2520 sessions) forward CAGR and max DD for VT35 (0.93)
and SPY, both sliced from the single full-history run. Percentiles
5/25/50/75/95 and share of windows where VT35 CAGR > SPY CAGR. Windows
overlap; the share is descriptive, not independent evidence.

## Monte Carlo

Stationary block bootstrap (Politis–Romano, geometric block length, mean 20
sessions) of whole historical sessions (QQQ overnight/intraday price returns,
dividend yield, DTB3; SPY returns of the same session carried along) from the
full history, 5,000 paths, seed 20260929. Each path: 200-session warm-up + 2,520
measured sessions (10 years). VT35 rule applied per path exactly as above.
Report percentiles 5/25/50/75/95 of CAGR, max DD, worst 252-session "year"
return, and P(max DD > 50%); same stats for SPY hold on the same paths.

## Decision rule (fixed now)

- **VT35 is acceptable for eventual small LIVE** iff its 2000+ max DD ≤ 1.3 ×
  SPY buy-and-hold's max DD **and** its full-period CAGR > SPY's (primary arm:
  VT35 at 0.93, 2x costs, no T-bill yield).
- **SGOV/BIL overlay is adopted** iff VT35-B's full-period CAGR > VT35's and
  VT35-B's max DD ≤ VT35's.

All other outputs are reported, not gated. No parameter of VT35 is changed.
