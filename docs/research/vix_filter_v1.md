# VIX filter v1 (2026-10-12)

**No arm passes the pre-registered gate, so VT35 stays unchanged.** A fear
index filter layered on VT35 does not raise long-run profit. Every arm loses
CAGR against C0 on its own window (−0.7 to −3.2 pp) and has a lower block-20
bootstrap median.

- **X1 (cash while VIX > 30)** is worst. It loses 3.2 pp CAGR, and its max
  DD gets *worse* (56.2% against 49.6%). It sells near panic lows, misses
  rebounds (2020: +6.1% against C0's +18.3% in the 63 sessions after the
  trough) and stays underwater 10.8 years against 7.5. It also fails
  out of sample (^GSPC 1990–99: 14.85% against 17.22%).
- **X2 (cash while VIX / SMA60 > 1.5)** is the best single crash dodge.
  In 2020-02/03 it made +3.2% against −15.7%, and its bootstrap P(DD > 70%)
  is 9.8% against 12.4%. It loses everywhere else: −0.7 pp CAGR, 2018-Q4
  −24.3% against −18.9%, bootstrap median 5.68% against 7.40%. Out of sample
  it misses the G8 CAGR bar by 0.06 pp (16.66% against 16.72%).
- **X3 (cash while VIX > VIX3M)** cuts max DD most (31.5% against 41.6% on
  2006-07 →) and raises Calmar 25%. It trades 4.2 extra exits a year,
  doubles turnover and gives up 1.1 pp CAGR and 3.8 pp/yr since 2017. Only
  31% of its exits avoided a further loss. It has no pre-2000 data, so it
  could not pass (G8 not available).
- **X4 (vol estimate = max(vol20, 2 × VXN))** lowers every crash a little
  and cuts bootstrap P(DD > 70%) from 9.6% to 2.7%. It gives up 1.9 pp CAGR
  and 4.3 pp/yr since 2017, because it under-sizes calm bull markets. It also
  cannot pass (G8 not available).

Evidence: `research/vix_filter_v1/` and `data/historical/vix_filter_v1_*.csv`,
removed from main on 2026-10-12 and preserved in tag
`archive/research/vix-filter-v1-2026-10`.

- `PREREGISTRATION.md`, `screen.py`, `fetch_data.py`, the data,
  `DATA_MANIFEST.json` and `VALIDATION.json` were committed in `e8f0424`
  before any arm other than the C0 harness check was simulated.
- The outputs are `RESULTS.json` and `SUMMARY.txt`. The run is offline and
  deterministic and takes about 35 s; two runs gave byte-identical
  `RESULTS.json`.
- **Post-run bug fix (disclosed):** in the first run, the VIX SMA60 was a
  plain cumulative sum. On the ^GSPC calendar, which starts in 1988 before
  ^VIX (1990-01-02), the leading NaNs made every G8 SMA60 NaN. X2 was then
  identical to C0 on ^GSPC and passed G8 trivially. The fix masks missing
  values and implements the pre-registered rule; no rule or parameter
  changed. Only G8 X2 changed: from a trivial pass (17.22% / 25.4%) to a fail
  (16.66% / 27.9%). The main-window and bootstrap numbers are bit-identical
  (VIX has no gaps there). The decision is the same either way, because X2
  already failed G1, G3, G5 and G6.

## Method

- **Mechanics** as `trend_robustness_v2`. Decide on the completed close,
  trade at the next open, 0.5 pp band, cap 0.98, cost 4 bps × m, idle cash
  earns zero. Synthetic QLD\* (2x QQQ total return, 0.95% expense). Engine
  imported from `research/trend_robustness_v2/screen.py`.
- **Financing:** base (DTB3) and calibrated (DTB3 + 0.75%,
  `leverage_frontier_v1` A1). G1–G7 must hold under both.
- **Arms** (all keep SMA200 trend and VT35 sizing):
  - **C0:** VT35.
  - **X1:** cash while VIX close > 30.
  - **X2:** cash while VIX / SMA60(VIX) > 1.5.
  - **X3:** cash while VIX > VIX3M.
  - **X4:** `vt(max(vol20, 2 × VXN/100))`.
- **Windows** (each arm against C0 on the same window):
  - Main (C0/X1/X2): 2000-03-06 → 2026-09-29.
  - X3: 2006-07-18 →. This is the short-window arm.
  - X4: 2001-01-24 →.
- **Harness check:** C0 on the main window reproduces `trend_robustness_v2`'s
  C0 (historical m = 1/2 and block-20 bootstrap) to 0.00005 pp.
- **Bootstrap:**
  - Block 20, 5,000 ten-year paths, seed 20261011.
  - Whole sessions are resampled jointly, including the VIX/VXN/VIX3M closes
    of the same session. SMA200, vol20 and SMA60 are recomputed on each path.
  - Levels jump at block joins, which the pre-registration disclosed.
  - X3 and X4 draw only from sessions where their index exists, and C0 is
    re-run on each of those pools.
- **G8:** ^GSPC with ^VIX, 1990-03-28 → 1999-12-31.
  - Synthetic 2x, price only, calibrated financing, m = 2.
  - 1,957 of 2,468 opens are placeholders and fill at the previous close,
    identically for both arms.

## Data coverage

| Series | Source | Coverage | Check |
|---|---|---|---|
| ^VIX | Yahoo | 1990-01-02 → 2026-09-29, 9,255 rows | Every QQQ session has a same-date close. 99.87% match FRED VIXCLS to 0.01; 3 dates differ > 0.5. |
| ^VXN | Yahoo | 2001-01-23 → 2026-09-29, 6,459 rows | Matches FRED VXNCLS from 2004. **2001–2003 differ systematically** (646 dates, Yahoo ≈ 7–9 points higher). X4 with FRED instead: 13.49% / 39.7% against 13.44% / 39.7% (report only). |
| ^VIX3M | Yahoo | 2006-07-17 → 2026-09-29, 5,083 rows | CBOE back-fill before its 2007-12 launch. |
| ^GSPC | Yahoo | 1988-01-04 → 1999-12-31, 3,033 rows | Price only; old opens are placeholders. |
| QQQ/SPY/DTB3 | committed `stress_robustness_v1_*` | 1999-03-10 → 2026-09-29 | Unchanged. |

## Results (2x costs)

Bootstrap columns use block 20 with 10-year paths, on each arm's pool.

| Window | Arm | Model | CAGR | Max DD | Calmar | 2017+ CAGR | Turnover/yr | Boot. median | Boot. > SPY | P(DD>70%) |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| main | **C0** | base | **13.37%** | **49.6%** | 0.27 | 30.7% | 6.5 | **7.40%** | 46.5% | **12.4%** |
| main | X1 | base | 10.21% | 56.2% | 0.18 | 24.7% | 7.7 | 4.61% | 31.9% | 13.9% |
| main | X2 | base | 12.63% | 49.6% | 0.25 | 29.4% | 8.1 | 5.68% | 38.2% | 9.8% |
| main | **C0** | calibrated | **12.81%** | **49.6%** | 0.26 | 30.0% | 6.5 | **6.96%** | 44.4% | **13.3%** |
| main | X1 | calibrated | 9.68% | 56.3% | 0.17 | 24.0% | 7.7 | 4.23% | 29.8% | 14.8% |
| main | X2 | calibrated | 12.09% | 49.6% | 0.24 | 28.7% | 8.1 | 5.30% | 36.0% | 10.6% |
| 2006-07 → | **C0** | base | **20.11%** | **41.6%** | 0.48 | 30.7% | 6.4 | **16.54%** | 75.2% | **4.2%** |
| 2006-07 → | X3 | base | 18.97% | 31.5% | 0.60 | 27.0% | 12.7 | 15.78% | 71.6% | 1.8% |
| 2006-07 → | **C0** | calibrated | **19.45%** | **41.8%** | 0.47 | 30.0% | 6.4 | **15.96%** | 72.5% | **4.4%** |
| 2006-07 → | X3 | calibrated | 18.35% | 31.6% | 0.58 | 26.3% | 12.7 | 15.24% | 69.0% | 1.9% |
| 2001-01 → | **C0** | base | **15.36%** | **41.6%** | 0.37 | 30.7% | 6.5 | **8.38%** | 48.8% | **9.6%** |
| 2001-01 → | X4 | base | 13.44% | 39.7% | 0.34 | 26.5% | 7.7 | 8.23% | 47.1% | 2.7% |
| 2001-01 → | **C0** | calibrated | **14.78%** | **41.8%** | 0.35 | 30.0% | 6.5 | **7.91%** | 45.9% | **10.1%** |
| 2001-01 → | X4 | calibrated | 12.92% | 40.0% | 0.32 | 25.8% | 7.7 | 7.82% | 44.7% | 3.0% |

SPY total-return hold: main 8.47% (bootstrap 8.68%), 2006-07 → 11.47%
(11.68%), 2001-01 → 8.89% (9.17%). At 1x costs (base): C0 13.66%, X1
10.55%, X2 13.00%; C0 20.42% vs X3 19.58%; C0 15.66% vs X4 13.79%.

### Gates

Each cell gives base / calibrated.

| Gate | X1 | X2 | X3 | X4 |
|---|---|---|---|---|
| G1 CAGR ≥ C0 − 0.5 pp | fail / fail | **fail** (−0.74 pp) / fail | **fail** (−1.14) / fail | **fail** (−1.92) / fail |
| G2 DD ≤ C0 | fail / fail | pass (equal) / pass | pass / pass | pass / pass |
| G3 CAGR > C0 or Calmar ≥ 1.1 × C0 | fail / fail | fail / fail | pass (Calmar +25%) / pass | fail / fail |
| G4 2017+ ≥ C0 − 2 pp | fail / fail | pass / pass | **fail** (27.0 vs 30.7) / fail | **fail** (26.5 vs 30.7) / fail |
| G5 bootstrap median and share ≥ C0 | fail / fail | fail / fail | fail / fail | fail / fail |
| G6 G1–G3 at 1x | fail / fail | fail / fail | fail / fail | fail / fail |
| G7 turnover ≤ 2 × C0 | pass / pass | pass / pass | pass (12.7 vs 12.7 bar) / pass | pass / pass |
| G8 ^GSPC out of sample | **fail** | **fail** | not available | not available |
| **Result** | **fail** | **fail** | **fail** | **fail** |

### Out of sample: ^GSPC + ^VIX 1990-03-28 → 1999-12-31 (G8)

Calibrated financing, m = 2. The C0 rule sets the bars: CAGR ≥ 16.72%, max
DD ≤ 28.40%.

| Arm | CAGR | Max DD | Turnover/yr | Time in market | VIX exits | G8 |
|---|---:|---:|---:|---:|---:|---|
| C0 rule | 17.22% | 25.4% | 5.6 | 85.6% | — | — |
| X1 | 14.85% | 25.8% | 6.8 | 84.0% | 11 (9% avoided a further loss) | fail |
| X2 | 16.66% | 27.9% | 5.8 | 85.5% | 2 (0%) | fail (−0.06 pp) |
| X3, X4 | — | — | — | — | — | not available |
| ^GSPC price hold | 16.12% | 19.9% | 0 | 100% | — | — |

## Crash episodes (base, 2x costs)

Each cell is episode return / max DD within the episode / sessions out of
market. The last column is the return over the 63 sessions after the
index's lowest close in the episode, for C0 → arm. C0 is shown on the main
window; on the X3/X4 windows C0's episode numbers are identical, except
2000–02, which starts 2001-01 for X4.

| Episode | C0 | X1 | X2 | X3 | X4 | Rebound C0 → X1 / X2 / X3 / X4 |
|---|---|---|---|---|---|---|
| 1998-08 (^GSPC) | −17.2% / 25.4% / 41 | −17.5% / 25.6% / 52 | −20.0% / 27.9% / 42 | n/a | n/a | 8.1% → 12.4% / 8.1% / — / — |
| 2000–02 | −41.6% / 43.4% / 574 | −47.0% / 48.6% / 577 | −41.6% / 43.4% / 574 | n/a | from 2001-01: −14.8% / 15.1% (C0 −18.3% / 18.6%) | −4.1% → −4.1% / −4.1% / — / −3.1% |
| 2008 | −18.3% / 20.4% / 209 | same | same | same | −15.0% / 16.9% / 209 | 0.0% → 0.0% for all |
| 2011-08 | −10.5% / 19.7% / 44 | −12.3% / 19.2% / 56 | same as C0 | −1.1% / 10.0% / 52 | −7.7% / 16.0% / 44 | −7.6% → −9.6% / −7.6% / −9.6% / −7.1% |
| 2015-08 | −26.1% / 34.5% / 72 | same | same | −13.1% / 19.8% / 76 | −24.1% / 32.4% / 72 | −5.8% → same, X4 −5.8% |
| 2018-Q4 | −18.9% / 19.2% / 44 | same | −24.3% / 24.6% / 47 | −18.6% / 19.0% / 50 | −18.3% / 18.6% / 44 | −2.1% → same for all |
| 2020-02/03 | −15.7% / 27.6% / 15 | −13.4% / 25.6% / 23 | **+3.2% / 12.7%** / 26 | −1.6% / 12.7% / 27 | −15.4% / 26.2% / 15 | 18.3% → **6.1%** / 18.3% / 18.7% / 15.5% |
| 2022 | −20.2% / 21.3% / 234 | same | same | same | −19.1% / 20.3% / 234 | 5.3% → same, X4 3.5% |
| 2025-04 | −13.5% / 18.2% / 38 | same | same | −11.1% / 16.0% / 41 | −11.1% / 15.9% / 38 | 16.0% → same, X4 13.9% |

"Same" means identical to C0: the trend rule was already in cash, or VIX
never crossed the threshold while the trend was on.

**VIX-triggered exits** (the override, not the trend, forced the exit):

| Arm | Exits | Per year | Avoided a further loss | Mean QLD\* return while out | Median sessions out |
|---|---:|---:|---:|---:|---:|
| X1 | 33 | 1.24 | 18% | +4.4% | 2 |
| X2 | 32 | 1.20 | 34% | +0.8% | 1 |
| X3 | 85 | 4.21 | 31% | +0.9% | 2 |

Most exits are one- or two-day spells. The index usually bounces before the
re-entry fill, so the filter sells low and buys back higher.

## Findings

- **The trend rule already handles slow bears.** In 2000–02, 2008 and 2022
  VT35 was in cash before VIX became extreme. In 2008 and 2022 no arm
  changes anything, except X4 slightly. The filter can only act in fast
  crashes that begin while the trend is on: 2011-08, 2015-08, 2018-Q4,
  2020-02/03 and 2025-04.
- **In those fast crashes the gain is real but small, and the cost lands
  elsewhere.**
  - X2 and X3 cut the 2020 drawdown from 27.6% to 12.7%, and X3 halves
    2011-08 and 2015-08.
  - The same signals fire on many shocks that recover within days. X3's 85
    exits, 69% of them followed by a higher re-entry price, cost 1.1 pp/yr.
  - X2 took a worse 2018-Q4. It exited on the 2018-10-10 spike and bought
    back 4 sessions later with QLD\* 8.2% higher, before the trend exit.
- **An absolute VIX level is the wrong trigger.** VIX > 30 occurs mostly
  after the market has already fallen, and it stays high during the
  rebound.
  - X1 exited on 2020-02-27 and stayed out for 50 sessions, while QLD\* rose
    16.6%.
  - It made six more exits in 2020-05 → 10 and four in 2011-10/11.
  - That turns a crash filter into a drawdown amplifier: 56.2% max DD and
    10.8 years underwater.
- **Implied-vol sizing (X4) is a pure de-risking.** It lowers almost every
  episode by 1–3 pp, and the bootstrap tail by a lot (P(DD > 70%) 2.7%
  against 9.6%). It also under-sizes bull runs: 2 × VXN exceeds QLD\*'s
  realised vol20 on 86% of trend-on days, and it gives up 4.3 pp/yr since
  2017. That is the
  same return-for-tail trade as a lower vol target (`leverage_frontier_v1`:
  VT30), and VT35 already sits at the chosen point on that curve.
- **Short-trend worlds still favour SPY.** No arm lifts the block-20 median
  above C0's on its own pool; X1 and X2 fall further, to 4.6% and 5.7%. In
  bootstrap paths, block joins create artificial VIX spikes and drops. The
  pre-registration disclosed this; it hurts X2 and X3 most.

## Caveats

- **In-sample history.** All arms use the same 2000–2026 history, and the
  bootstrap resamples it. G8 (1990–99, short, price only, mostly
  previous-close fills) is the only out-of-sample evidence, and only for X1
  and X2.
- **Short windows.** X3's window starts 2006-07 and X4's 2001-01, so they
  are not comparable with X1/X2's numbers. They are comparable only with C0
  on their own windows.
- **Index mismatch.** VIX and VIX3M are S&P 500 implied vols applied to a
  Nasdaq-100 position. The two VXN sources disagree in 2001–2003; the
  sensitivity check shows this changes nothing material.
- **G8 X2 bug fix after the first run** (above). No rule or parameter
  changed.
- **Seen before writing.** The pre-registration discloses earlier campaign
  results and general VIX history (the 2008 and 2020 spikes, etc.).

## Decision

VT35 stays the PAPER identity with no parameter change. No arm qualifies,
and a passing arm would not have been promoted by this campaign anyway. The
VIX-filter idea is closed in these four forms. Revisiting it (for example
with a VIX-based re-entry rule) would need a new, separately pre-registered
campaign. No code, configuration or runtime changed.
