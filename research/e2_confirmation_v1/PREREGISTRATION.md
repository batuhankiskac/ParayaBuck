# E2 confirmation v1 — pre-registration (2026-10-12)

Written, and committed with `screen.py` and the data, before any arm (C0 or
E2) was simulated on any market below. The only computations on these data
before this commit are the data validation (`validate_data.py` →
`VALIDATION.json`: gaps, stale runs, |daily return| > 25%, open quality) and
the window printout (`screen.py --windows`: dates and counts only). `screen.py`
was smoke-tested on randomly generated synthetic prices only.

## Seen before writing

- **`signal_ensemble_v1`** (tag `archive/research/signal-ensemble-v1-2026-10`):
  no arm passed. E2 (2-of-3 majority of SMA200 / TSM252 / DON126) was the
  closest miss: +1.4 pp CAGR on QQQ 2000-03-09 → 2026-09-29 (calibrated,
  m = 2: 14.25% against C0's 12.83%) at the same 49.6% max DD; out of sample
  on ^GSPC price 1957-01-02 → 1999-12-31: 10.26% against 9.70%, max DD 32.6%
  against 39.1%. It failed only the block-20 bootstrap gate (median 7.35%
  against 7.40%). Its gain came from a handful of whipsaw years (2004–05,
  2012, 2015–16, 2019, 2025), and it gave some back in 2011 and 2023.
- **`gold_riskoff_oos_v1`** (tag `archive/research/gold-riskoff-oos-v1-2026-10`):
  its CASH arm *is* the C0 rule on ^IXIC price, 1975-01-02 → 1998-12-30,
  synthetic 2x with calibrated financing, m = 2: **17.37% CAGR / 44.3% max
  DD**, worst drawdown 1983-06-24 → 1984-12-27, trend exit 1987-10-16. So C0
  on most of M1's window was already seen; E2 on ^IXIC was not.
- **General market history** of all five indices is common knowledge: the
  1929–32 crash and 1937–38 bear market, WWII, the post-war rally (S&P 500);
  the 1973–74 bear market, the 1987 crash and the late-1990s Nasdaq boom;
  **Japan's 1989 peak and the multi-decade post-1989 bear market** (Nikkei
  below its 1989 high until 2024); the 2000–03 and 2007–09 bear markets in
  Europe; the 2020 Covid crash; the FTSE 100's flat 1999–2016 decade.
- The coverage and validation of the downloaded files (below). No signal,
  return or drawdown of any arm on them was computed.

## Question

Does E2 beat C0 (the VT35 rule) on markets and periods this project has never
evaluated E2 on? Pass means "E2 confirmed": the next step would be a separate
production campaign (implementation, Stage B replay, parity tests). Fail means
E2 is closed and VT35 stays unchanged. Nothing here changes production.

## Data

Downloaded by `download_data.py` (Yahoo chart API, FRED fredgraph CSV only).
URLs, retrieval times, coverage, rows and SHA-256 are in `DATA_MANIFEST.json`.

- `data/historical/e2_confirmation_v1_index.csv`: Yahoo daily OHLC of ^IXIC
  (→ 1999-12-31), ^GSPC (→ 1956-12-31), ^N225, ^GDAXI, ^FTSE (→ 2026-09-29).
  **Price indices: no dividends.** Every signal, TSM included, uses the price
  return, so TSM's T-bill hurdle is harder than on total return by about the
  dividend yield (roughly 1–5%/yr depending on market and era).
- `data/historical/e2_confirmation_v1_tb3ms.csv`: FRED TB3MS monthly,
  1934-01 → 1956-12.
- `data/historical/stress_robustness_v1_dtb3.csv` (committed, reused): DTB3
  daily from 1954-01-04.

### Markets and windows

| Key | Index | Data used | First measured session | Last | Measured sessions |
|---|---|---|---|---|---:|
| M1 | ^IXIC | 1971-02-05 → | 1975-01-02 | 1999-12-31 | 6,319 |
| M2 | ^GSPC | 1934-01-02 → | 1935-01-08 | 1956-12-31 | 5,502 |
| M3 | ^N225 | 1965-01-05 → | 1966-01-19 | 2026-09-29 | 14,922 |
| M4 | ^GDAXI | 1987-12-30 → | 1989-01-03 | 2026-09-29 | 9,546 |
| M5 | ^FTSE | 1984-01-03 → | 1985-01-02 | 2026-09-29 | 10,544 |

M2–M5 start at the first session with a full warm-up: the decision close
before the first measured session has 252 prior returns (TSM's lookback; SMA
200, DON 126 and vol 20 need fewer). M2's data start at 1934-01-02 because
the T-bill series (TB3MS) starts in 1934-01. M1 starts at 1975-01-02 with
warm-up data from 1971.

### T-bill rate

`y[d]` = last DTB3 on or before `d` (forward-filled). Before 1954-01-04
(M2 only, 1934-01-02 → 1953-12-31, 5,000 sessions) `y[d]` = TB3MS of the
latest month whose observation date (the 1st) is ≤ `d`, forward-filled daily.
TB3MS is a monthly average, so within a month the value includes later days
of that month (a small look-ahead in the TSM hurdle and the financing; rates
were 0.01%–3.21% and slow-moving then). Session k accrues `y[k−1]/252`.

For M3–M5, **USD T-bills are only a proxy for the local short rate** (JPY,
DEM/EUR, GBP rates differed, sometimes by several points: UK rates were far
above, Japanese rates far below, US rates for long stretches). The proxy enters
both the instrument's financing (both arms, while invested) and E2's TSM
hurdle (E2 only). Time in market is similar across arms, so the financing
effect largely cancels; the hurdle effect does not and is a caveat.

### Opens

An open is a placeholder if it is null, non-positive, O = H = L = C, or outside
[low, high]; a placeholder open is replaced by the previous close, for both
arms. Fills then happen at the decision close. Counts in the measured windows:
M1 2,474 (1975–1984: ^IXIC has no intraday data), M2 all 5,502 (no intraday
data), M3 5,481 (1966–1988), M4 1,237 (1989–1993), M5 0. Yahoo's ^FTSE open
equals the previous close on most sessions from 2000 (it is kept: the fill is
then at the previous close anyway).

### Validation (`VALIDATION.json`, reported, nothing dropped)

- **|daily close return| > 25%:** none on any index. All moves > 10% are
  known events (1929, 1931–33, 1939-09-05, 1987-10, 1989-10-16, 1990-10-02,
  2008-10, 2011-03-15, 2020-03, 2024-08-05/06).
- **Calendar gaps ≥ 6 days:** none on ^IXIC, ^GSPC, ^FTSE. ^N225 has 64
  (New-Year and Golden-Week closures, the 2019 ten-day holiday, Silver Week
  2009/2015/2026). ^GDAXI has 10 (Christmas closures). All are exchange
  holidays.
- **Stale closes (≥ 3 identical consecutive closes):** ^IXIC 1 (1987-11-30 →
  12-02); ^GSPC 6 (1940–43, 1956, 3–4 bars each); ^N225 12, of which 11 are
  3-bar year-end runs and **one is broken: 21 identical closes 1972-03-31 →
  1972-04-28**. It is kept as served: both arms see zero returns for a month
  and the whole move on 1972-05-01 (|return| < 10%).
- **Weekend dates:** none. ^GSPC 1934–1952 and ^N225 before 1989 had Saturday
  sessions in earlier decades that Yahoo omits; their moves are folded into the next session.
- **Null closes** were dropped at download (holiday placeholder rows):
  ^N225 625, ^GDAXI 168, ^FTSE 183, others 0.

## Instrument (per market)

Synthetic daily-reset 2x of the index's daily price return, minus 0.95%/yr
expense, minus `y` financing, minus the calibrated extra 0.75%/yr
(`leverage_frontier_v1` Amendment A1): `trend_robustness_v2.synthetic_2x`
with zero dividends and rate `y + 0.0075`. There was no 2x product in most of
these windows.

## Arms (two, no others)

Signals on each index's own completed price close at t:

- **SMA:** close > 200-session SMA.
- **TSM:** 252-session price return > cumulative T-bill return over the same
  252 sessions.
- **DON:** close > midpoint of the max and min of the 126 closes ending at t.

`vt = min(1, 0.35 / vol20)`, vol20 = annualized sample vol of the 20 most
recent daily returns of the synthetic instrument; total weight capped at 0.98.

- **C0:** `SMA · vt` (the VT35 rule on that index).
- **E2:** `[SMA + TSM + DON ≥ 2] · vt`.

Signal functions are copied verbatim from `signal_ensemble_v1/screen.py`.

## Mechanics

Unchanged from `trend_robustness_v2`/`signal_ensemble_v1` (engine imported
from `research/trend_robustness_v2/screen.py`): decide on the close, trade at
the next open, 0.5 pp band, idle cash earns zero, cost `4 bps × m` per unit
traded. **m = 2 is primary; m = 1 is reported and gated through K4.**

## Bootstrap (K5)

Stationary block bootstrap, mean block 20, per market 2,000 paths of
252 warm-up + 2,520 measured sessions (10 years). One generator,
`numpy.random.default_rng(20261011)`, markets in order M1..M5, 500-path
chunks. Each market resamples its own measured-window sessions; each drawn
session carries its overnight factor (open / previous close), intraday factor
(close / open) and the T-bill rate of its previous close jointly. Signals run
on the resampled price level, the instrument on the resampled factors, both
arms on the same path at m = 2. Statistic: E2 CAGR − C0 CAGR per path, pooled
over the 10,000 paths.

## Gates (all must pass, m = 2 unless stated)

- **K1:** E2 CAGR ≥ C0 CAGR in at least 4 of the 5 markets.
- **K2:** median across the 5 markets of (E2 CAGR − C0 CAGR) > 0.
- **K3:** E2 max DD ≤ C0 max DD + 3 pp in at least 4 of 5 markets, and never
  more than + 8 pp.
- **K4:** K1 and K2 also hold at m = 1.
- **K5:** pooled bootstrap median of (E2 CAGR − C0 CAGR) ≥ 0.

Pass all → **"E2 confirmed"** (no production change by this campaign). Any
fail → **E2 is closed**; VT35 stays unchanged.

## Report only

Per market and arm: CAGR, max DD, Calmar, time in market, round trips per
year, losing share (round-trip definition of `signal_ensemble_v1`), turnover,
the three worst drawdown episodes, calendar-year returns, signal agreement;
per-market bootstrap medians; index price buy-and-hold; and the combined view
with `signal_ensemble_v1`'s two windows (7 windows in all; those two were seen
before writing).

## Caveats known now

- Price-only indices (TSM hurdle harder by the dividend yield).
- USD T-bills as a proxy for local short rates on M3–M5; TB3MS monthly average
  for M2 before 1954.
- Many fills at the previous close (placeholder opens), identically for both
  arms.
- C0 on ^IXIC 1975–1998 was seen (`gold_riskoff_oos_v1`).
- No parameter here is tuned after results are seen; no arm or market is
  added or removed.
