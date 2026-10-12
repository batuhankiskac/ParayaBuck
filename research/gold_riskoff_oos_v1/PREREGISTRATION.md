# Gold risk-off out of sample v1 — pre-registration (2026-10-12)

Written before any arm (CASH, GOLD, BILLS) was simulated on real 1975–1998
data. Every number this campaign gates on comes from
`research/gold_riskoff_oos_v1/screen.py --run`, run once after this file, the
code and the data are committed. Nothing here is tuned after results are seen;
no arm, index or gate is added or dropped.

## Question

`leverage_frontier_v1` (2000-08 → 2026-09) found that holding gold at 0.98 in
VT35's risk-off state, instead of zero-yield cash, added +2.3 pp CAGR and lifted
the block-20 bootstrap median from 6.08% to 9.05%. It failed because max DD
rose by 4.3 pp. That result came from one gold regime, the 2001–2011 bull
market. Does the gold risk-off leg also beat cash on 1975–1998, data this
project has never used?

## Seen before writing

1. **The `leverage_frontier_v1` GOLD result** (`docs/research/leverage_frontier_v1.md`):
   - C0 + GOLD: 16.97% against 14.69% CAGR (base financing; calibrated 16.39% / 14.12%).
   - Max DD 45.9% against 41.6%.
   - Bootstrap median 9.05% against 6.08%.
   - It failed the +2 pp max-DD condition.
   - The doc says much of the gain came from gold's 2001–2011 bull market.
   The rest of that campaign (BILLS, BOND10, the leverage grid) and the
   earlier research docs listed in `docs/research/INDEX.md` are also known.
2. **General market history of 1975–1999 is common knowledge** to the author:
   - The gold bull market to the January 1980 peak (about $850), then a long
     gold bear market to 1999.
   - The 1987 crash (October 19).
   - The early-1980s recessions and high interest rates.
   - The late-1990s Nasdaq boom.
   The author has not computed, and does not know, the 200-session-SMA trend
   state of any index on any date in the window. The author also does not
   know the number or timing of risk-off episodes, or gold's return inside
   them.
3. **Gold source search** (`DATA_MANIFEST.json`, `unavailable_gold_sources`):
   - The Bundesbank's USD London fixing series (`BBEX3.D.XAU.USD.EA.AC.C05`
     to `C08`) return HTTP 404.
   - FRED `GOLDAMGBD228NLBM` and `GOLDPMGBD228NLBM` return 404.
   - The LBMA price JSON is behind a Cloudflare bot block, which was not
     circumvented.
   - Yahoo `GC=F` starts 2000-08-30.
   The only official daily gold price found for 1975–1998 is the Bundesbank's
   Frankfurt Stock Exchange fixing (`BBEX3.D.XAU.DEM.EA.AC.C01`, DM per kg,
   1968-06-18 → 1998-12-30). The coordinator therefore amended the spec:
   - Gold is the Frankfurt fixing converted to USD.
   - Every window ends 1998-12-30.
   - The post-2000 GLD/GC=F cross-check is replaced by a World Bank Pink
     Sheet monthly check (item 4).
4. **Data validation** (`VALIDATION.json`, `screen.py --validate`; no arm simulated):
   - **Gold vs the World Bank Pink Sheet monthly London PM fix**: 288 months,
     1975-01 → 1998-12. The monthly mean of the Frankfurt-USD fixings is
     compared with the Pink Sheet value.
     - Mean absolute difference 0.34% (signed +0.31%; Frankfurt is slightly
       higher). This is under the 2% stop threshold.
     - Correlation of monthly returns 0.983.
     - Total return 1975-01 → 1998-12: +64.6% against +65.5%.
     - The largest difference is 1980-12: 597.32 against 538.26 (+11.0%).
       There the Pink Sheet value sits below both neighbouring months'
       Frankfurt and Pink Sheet values (1981-01 Pink Sheet 557.39). Every
       other year's mean absolute difference is ≤ 0.78%; 1980's is 1.16%.
   - **Gold gaps.**
     - Mapped onto each index calendar, a missing fixing is carried forward
       at most 2 sessions inside the measured windows.
     - No run longer than 3 sessions falls in a window. The only runs over 3
       on the calendars are 1971-08-16 → 08-20 (5 sessions) and
       1973-03-02 → 03-16 (11 sessions), both before the windows and the
       warm-ups that matter. The 11-session run lies before 1975, so the
       10-session limit is not hit in any window.
   - **Indices** (Yahoo, price only):
     - ^IXIC 1971-02-05 →; open = high = low = close for every session before
       1984 and for 197 sessions of 1984.
     - ^NDX 1985-10-01 →.
     - ^GSPC 1970-01-02 →.
     - No calendar gap is longer than 5 days. The largest daily move is
       1987-10-19 on every index.
     - **Raw Yahoo opens equal the previous close on most sessions** for ^NDX
       (e.g. 243 of 253 in 1987, 203 of 251 in 1998) and ^GSPC (82–248 a
       year). So for much of the window, "fill at the next open" is a fill at
       the previous close on every index, not only for ^IXIC before 1985.
   - **DTB3** (committed `stress_robustness_v1_dtb3.csv`, from 1954): carried
     forward at most 1 session; no missing value in any window.
   - **Instrument code identity**: the L = 2 synthetic with spread 0 is
     bit-identical to `trend_robustness_v2.synthetic_2x` on ^IXIC.
5. **Dividends**: no free daily dividend series covers 1975–1998. Yahoo
   `^SP500TR` starts 1988-01-04 and `^XCMP` 2003-09-25. All arms therefore
   run on **price indices** (see Caveats).
6. **Code smoke test**: `--run` was executed once on random synthetic data
   (scratch only), to check that it runs. No real-data arm result was computed.

## Data

- `data/historical/gold_riskoff_oos_v1_index.csv`: ^IXIC, ^NDX, ^GSPC daily
  OHLC from the Yahoo chart API, clipped to ≤ 1998-12-31.
- `data/historical/gold_riskoff_oos_v1_gold.csv`: Bundesbank Frankfurt gold
  fixing (DM per kg) and the Bundesbank Frankfurt USD fixing (DM per USD) on
  the same Frankfurt date.
- `data/historical/gold_riskoff_oos_v1_pinksheet.csv`: World Bank Pink Sheet
  monthly gold. Used for **validation only**, never as an arm input.
- `data/historical/stress_robustness_v1_dtb3.csv`: DTB3 (committed).
- URLs, retrieval times, coverage, row counts and SHA-256 for each file are in
  `DATA_MANIFEST.json`. The run is offline.

## Instruments

- **2x instrument** (per index): daily-reset 2 × the index's daily price
  return, minus 0.95%/yr expense, minus DTB3 financing. Per session:
  `1 + 2·r − (0.0095 + (DTB3[t−1] + s)) / 252`. The open is built from the
  overnight return the same way. This is `leverage_frontier_v1`'s
  `synthetic_L`.
  - **Calibrated (primary)**: s = 0.75%/yr (`leverage_frontier_v1`
    Amendment A1).
  - **Base**: s = 0.
- **^IXIC before 1985-01-01**: open := previous close, because Yahoo has no
  intraday data. This applies to every arm. On those sessions the overnight
  return is zero and the whole return is intraday, so trades fill at the
  previous close.
- **GOLD**: USD per troy oz = Frankfurt DM/kg ÷ DM per USD ÷ 32.1507466.
  - Mapped onto the index calendar by carrying the latest fixing forward
    (≤ 10 sessions; runs > 3 are logged in `VALIDATION.json`).
  - **Alignment (primary)**: the GOLD level of session t is the first
    Frankfurt fixing after session t's close, i.e. the same-date level of
    session t + 1 (the last session keeps its own).
  - Per session: open = previous close (overnight factor 1) and intraday
    factor = level ratio × (1 − 0.40%/252). This is `leverage_frontier_v1`'s
    pre-GLD gold leg with a 0.40%/yr drag.
  - Why this alignment: the Frankfurt fixing is struck around 10:30–13:00
    CET, before the New York open. A risk-off trade decided on the close of
    t − 1 then fills at the fixing struck on the morning of t, after the
    signal. The same-date alignment (the latest fixing dated ≤ t, as
    `leverage_frontier_v1` used for GC=F) would fill at a price struck about
    9–11 hours before the signal. It is **report-only**.
- **BILLS** (report-only): per session `1 + (DTB3[t−1] − 0.10%) / 252`, open
  = previous close.

## Signal, weights and mechanics (VT35; unchanged from `leverage_frontier_v1`)

- `up[t]` = the index's own completed close > its 200-session SMA.
- Equity leg target = `up · min(0.98, min(1, 0.35 / vol20))`. vol20 is the
  annualized sample vol of the last 20 daily returns of that index's 2x
  instrument (vol ≤ 0 or undefined → weight 1).
- Risk-off leg target: CASH = none (zero yield); GOLD or BILLS =
  `(1 − up) · 0.98`, with no vol target. The in-trend remainder stays zero
  cash in every arm.
- Signal on the completed close; trade at the next open. All legs rebalance
  when any leg drifts > 0.5 pp. Cost is 4 bps × m per unit traded on every
  leg, with m = 2 (gated) and m = 1.
- Engine: `trend_robustness_v2.simulate_multi` (imported).

## Windows

- First measured session: the first session with index ≥ 200 (the SMA200
  is defined on the previous close) dated ≥ 1975-01-02. CAGR runs from the
  close before it.
  - **P** = ^IXIC: 1975-01-02 → 1998-12-30 (6,066 sessions).
  - **S2** = ^GSPC: 1975-01-02 → 1998-12-30 (6,066 sessions).
  - **S1** = ^NDX: 1986-07-17 → 1998-12-30 (3,150 sessions).
- Sub-periods (report-only): 1975–1980 (window start → last close ≤
  1980-12-31) and 1981–1998 (→ end). S1 has only the second.

## Bootstrap

- Stationary block bootstrap: mean block 20, 5,000 paths of 250 warm-up +
  2,520 measured sessions, seed 20261011.
- Draw order as `leverage_frontier_v1.boot_paths`.
- Pool: each index's measured-window sessions.
- Each drawn session carries jointly the index overnight and intraday
  returns, the previous DTB3 and the GOLD session factor.
- CASH, GOLD, BILLS and index buy-and-hold all run on the same paths.
- Gated: P at m = 2, calibrated. Report-only: P at m = 2 base and m = 1
  calibrated; S1 and S2 at m = 2 calibrated.

## Gates (all must pass; m = 2 and calibrated financing unless stated)

- **H1**: GOLD CAGR > CASH CAGR on P.
- **H2**: GOLD max DD ≤ CASH max DD + 5 pp on P.
- **H3**: block-20 bootstrap median CAGR GOLD ≥ CASH on P.
- **H4**: GOLD CAGR ≥ CASH CAGR − 0.5 pp on S1 **and** on S2.
- **H5**: H1 also holds at m = 1 (calibrated), under base financing (m = 2),
  and at m = 1 under base financing.

## Decision rule (fixed now)

- **All of H1–H5 pass**: "gold risk-off confirmed out of sample". This
  changes nothing in production. A production step would be a separate
  pre-registered campaign: implementation, a Stage B replay on the
  production engine against VT35, and parity tests.
- **Any gate fails**: gold risk-off is closed and not revisited.

## Report-only

- BILLS against CASH and GOLD.
- Sub-periods 1975–1980 (gold bull) and 1981–1998 (gold bear).
- 1987 calendar-year and October 1987 returns.
- Risk-off episodes per index: count, share of sessions, and gold's,
  the index's and each arm's return in each episode.
- P(10-yr CAGR < 0) and P(DD > 50%) from the bootstrap.
- Index price buy-and-hold.
- The same-date gold alignment.
- A chained CAGR combining P 1975–1998 with `leverage_frontier_v1`'s
  2000-08 → 2026-09 GOLD and CASH figures (different index, dividends
  included there; 1999 → 2000-08 not covered).

## Caveats (known now)

- **Price-only indices.** Dividends (about 3–5%/yr for the S&P 500 in
  1975–1990, less for Nasdaq) are missing from the in-trend leg. That leg is
  identical across CASH, GOLD and BILLS, so the arm comparison is not biased
  by it. Absolute CAGRs are understated.
- **Frankfurt, not London.** The monthly check shows a mean gap of 0.34%.
  Daily timing noise remains.
- **1999 not covered.** The window ends 1998-12-30 because the Frankfurt
  series stops there.
- **Fills at the previous close.** Every risk-off leg, ^IXIC before 1985,
  and most ^NDX / ^GSPC sessions fill at the previous close.
- **Synthetic leverage.** There was no 2x Nasdaq product before 2006. The
  instrument is synthetic, with calibrated financing.
- **Overlapping indices.** S1 and S2 overlap heavily with P (Nasdaq-100 ⊂
  Nasdaq Composite; all US large caps), so they are not independent
  evidence.
- **No taxes.**
