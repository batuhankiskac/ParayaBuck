# Signal ensemble v1 (2026-10-12)

**No arm passes the pre-registered gate, so VT35 stays unchanged.**

Combining trend signals of different types (200-session SMA, 252-session
time-series momentum over T-bills, 126-session Donchian midline) helps
historically, but no arm survives every gate:

- **E2 (2-of-3 majority) is the closest miss.** It adds 1.4 pp CAGR (14.82%
  against 13.39%) with the same 49.6% max DD. It passes G1–G4, G6, G7 under
  both financing models and the ^GSPC out-of-sample check G8 (10.26% against
  9.70%, max DD 32.6% against 39.1%). It fails only G5, the block-20
  bootstrap: median 7.35% against C0's 7.40%, and 46.4% of paths beat SPY
  against 46.5%. Its historical gain comes from a handful of whipsaw years
  (2004–05, 2012, 2015–16, 2019, 2025). In short-trend bootstrap worlds the gain
  disappears.
- **EP (proportional) has the best tail.** It halves P(DD > 70%) in the
  bootstrap (6.3% against 12.4%) and cuts max DD to 46.9%. It fails G4
  (2017+ CAGR 27.8% against 30.7%) and misses G8 by 0.007 pp (9.197% against
  a 9.204% bar).
- **T1 (TSM alone)** has the highest bootstrap median (8.01%) and the
  lowest max DD (44.9%). It fails G4 (25.7% since 2017) and fails out of
  sample badly (5.66% against 9.70% on ^GSPC 1957–1999, max DD 54.3%).
- **D1 (Donchian alone)** is worse than C0 almost everywhere.

No arm lifts the block-20 bootstrap median above SPY's 8.68%. The short-trend
weakness is unchanged.

Evidence: `research/signal_ensemble_v1/` on branch `research/signal-ensemble-v1`.

- `PREREGISTRATION.md`, `screen.py`, the ^GSPC data and `DATA_MANIFEST.json`
  were committed in `44d6823` before any arm other than C0 was simulated.
- The outputs are `RESULTS.json` and `SUMMARY.txt`.
- The run is offline and deterministic and takes about 20 s. Two runs gave
  byte-identical `RESULTS.json`.

## Method

- **Mechanics:** the same as `trend_robustness_v2`:
  - Decide on QQQ's completed close; trade at the next open.
  - 0.5 pp band; total weight capped at 0.98; idle cash earns zero.
  - Cost 4 bps × m per unit traded, with m = 2 (gated) and m = 1.
  - The engine, SMA, vol and metric functions are imported from
    `research/trend_robustness_v2/screen.py`.
- **Signals:**
  - **SMA:** QQQ price close > 200-session SMA. This is the precedent's
    convention, so that C0 is VT35.
  - **TSM:** QQQ 252-session total return > cumulative DTB3 over the same
    sessions.
  - **DON:** QQQ total-return level > midpoint of its 126-session max/min.
- **Arms:**
  - **C0:** VT35, `SMA · vt`.
  - **E2:** `[SMA + TSM + DON ≥ 2] · vt`.
  - **EP:** `(SMA + TSM + DON)/3 · vt`.
  - **T1:** `TSM · vt`.
  - **D1:** `DON · vt`.
  - In every arm `vt = min(1, 0.35/vol20)`.
- **Instrument:** synthetic QLD\*, daily-reset 2x on QQQ total return with
  0.95% expense. Two financing models:
  - *Base:* DTB3.
  - *Calibrated:* DTB3 + 0.75% (`leverage_frontier_v1` A1).
  - G1–G7 must hold under both models.
- **Window:** 2000-03-09 → 2026-09-29. TSM's 252-session warm-up moves the
  start 3 sessions after `trend_robustness_v2`'s 2000-03-06.
- **Harness check:** C0 on the precedent's window reproduces
  `trend_robustness_v2`'s C0 with 0.0000 pp difference. The check covers
  historical m = 1 and m = 2 (13.37% / 49.56%) and the block-20 bootstrap
  (median 7.40%, 46.48% beat SPY).
- **Bootstrap:**
  - Path draws identical to the precedent: seed 20261011, 5,000 ten-year
    paths.
  - Each path is extended backward by 3 sessions, with no extra random draw,
    for TSM's warm-up.
  - Block 20 is gated; blocks 60 and 250 are report only.
- **G8 (out of sample):**
  - ^GSPC from Yahoo, price only, 1957-01-02 → 1999-12-31. Warm-up data
    starts at 1954-01-04, the first DTB3 date.
  - Instrument: synthetic 2x with 0.95% expense + DTB3 + 0.75%, at m = 2.
  - Yahoo's old ^GSPC opens are placeholders: O = H = L = C before 1962,
    and afterwards the open usually equals the previous close. So every G8
    fill is at the previous close, for every arm.

## Results (2x costs)

Bootstrap columns use block 20 with 10-year paths.

| Arm | Model | CAGR | Max DD | Calmar | 2017+ CAGR | Boot. median | Boot. > SPY | P(DD>70%) | Turnover/yr |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **C0 VT35** | base | **13.39%** | **49.6%** | **0.27** | **30.7%** | **7.40%** | **46.5%** | **12.4%** | **6.5** |
| E2 majority | base | 14.82% | 49.6% | 0.30 | 31.3% | 7.35% | 46.4% | 12.2% | 6.2 |
| EP proportional | base | 13.61% | 46.9% | 0.29 | 27.8% | 7.85% | 48.9% | 6.3% | 6.5 |
| T1 TSM | base | 14.22% | 44.9% | 0.32 | 25.7% | 8.01% | 49.7% | 10.6% | 6.2 |
| D1 Donchian | base | 12.19% | 57.7% | 0.21 | 26.2% | 6.88% | 44.0% | 12.2% | 7.5 |
| **C0 VT35** | calibrated | **12.83%** | **49.6%** | **0.26** | **30.0%** | **6.96%** | **44.4%** | **13.3%** | **6.5** |
| E2 majority | calibrated | 14.25% | 49.6% | 0.29 | 30.6% | 6.90% | 44.2% | 13.0% | 6.2 |
| EP proportional | calibrated | 13.05% | 47.0% | 0.28 | 27.1% | 7.41% | 46.6% | 6.8% | 6.5 |
| T1 TSM | calibrated | 13.64% | 45.6% | 0.30 | 25.0% | 7.57% | 47.8% | 11.4% | 6.2 |
| D1 Donchian | calibrated | 11.64% | 57.8% | 0.20 | 25.5% | 6.44% | 41.8% | 12.9% | 7.5 |
| SPY hold | — | 8.60% | 55.2% | 0.16 | — | 8.68% | — | — | 0 |

At 1x costs (base) the ordering is unchanged: C0 13.68%, E2 15.11%, EP 13.91%,
T1 14.50%, D1 12.53%.

The block-60 and block-250 medians (report only) are:

| Arm | Block 60, base | Block 60, calibrated | Block 250, base | Block 250, calibrated |
|---|---:|---:|---:|---:|
| C0 | 10.74% | 10.23% | 13.39% | 12.83% |
| E2 | 10.95% | 10.44% | 14.20% | 13.65% |
| EP | 11.02% | 10.51% | 13.81% | 13.27% |
| T1 | 10.72% | 10.23% | 14.33% | 13.86% |
| D1 | 10.42% | 9.93% | 12.65% | 12.10% |

### Gates

Each G1–G7 cell gives the base result / the calibrated result.

| Gate | E2 | EP | T1 | D1 |
|---|---|---|---|---|
| G1 CAGR ≥ C0 − 0.5 pp | pass / pass | pass / pass | pass / pass | fail / fail |
| G2 DD ≤ C0 | pass / pass (equal) | pass / pass | pass / pass | fail / fail |
| G3 CAGR > C0 or Calmar ≥ 1.1 × C0 | pass / pass | pass / pass | pass / pass | fail / fail |
| G4 2017+ ≥ C0 − 2 pp | pass / pass | **fail** (27.8 vs 28.7) / fail | **fail** (25.7) / fail | fail / fail |
| G5 bootstrap median and share ≥ C0 | **fail** (7.35 vs 7.40; 46.4 vs 46.5) / fail | pass / pass | pass / pass | fail / fail |
| G6 G1–G3 at 1x | pass / pass | pass / pass | pass / pass | fail / fail |
| G7 turnover ≤ 2 × C0 | pass / pass | pass / pass | pass / pass | pass / pass |
| G8 ^GSPC out of sample | pass | **fail** (−0.51 pp) | **fail** | pass |
| **Result** | **fail (G5)** | **fail (G4, G8)** | **fail (G4, G8)** | **fail** |

### Out of sample: ^GSPC 1957-01-02 → 1999-12-31 (G8)

The test uses calibrated financing at m = 2, with every fill at the previous
close. C0's rule on this data gives the bars: CAGR must be at least 9.20% and
max DD at most 42.1%.

| Arm | CAGR | Max DD | Turnover/yr | Time in market | Round trips/yr | G8 |
|---|---:|---:|---:|---:|---:|---|
| C0 rule | 9.70% | 39.1% | 5.5 | 69.4% | 2.70 | — |
| E2 | 10.26% | 32.6% | 5.7 | 68.7% | 2.77 | pass |
| EP | 9.197% | 38.3% | 5.5 | 76.0% | 1.77 | fail (bar 9.204%) |
| T1 | 5.66% | 54.3% | 4.4 | 60.4% | 2.12 | fail |
| D1 | 11.50% | 40.6% | 6.6 | 67.4% | 3.26 | pass |
| ^GSPC price hold | 8.35% | 48.2% | 0 | 100% | — | — |

## Whipsaw and episodes (base, 2x costs, 2000–2026)

| Arm | Round trips/yr | Losing share | Avg. losing trip | Time in market | 2000–02 return / DD | 2007–09 return / DD | 2020-02/03 return / DD | 2022 return / DD | Longest underwater |
|---|---:|---:|---:|---:|---|---|---|---|---|
| C0 | 3.24 | 75.6% | −3.07% | 73.0% | −41.4% / 43.4% | +53.5% / 35.0% | −15.7% / 27.6% | −20.2% / 21.3% | 7.5 y |
| E2 | 3.09 | 69.5% | −3.26% | 74.3% | −41.4% / 43.4% | +53.5% / 35.0% | −15.7% / 27.6% | −20.8% / 21.9% | 7.2 y |
| EP | 2.15 | 61.4% | −2.49% | 84.6% | −41.5% / 43.6% | +23.7% / 37.0% | −16.9% / 30.0% | −22.7% / 23.8% | 7.5 y |
| T1 | 2.75 | 53.4% | −3.62% | 76.5% | −31.7% / 34.2% | −11.8% / 40.9% | −13.0% / 29.4% | −29.7% / 30.6% | 5.0 y |
| D1 | 4.07 | 71.3% | −3.68% | 74.2% | −51.8% / 53.5% | +36.4% / 42.9% | −22.0% / 33.0% | −19.0% / 20.2% | 10.0 y |
| SPY | — | — | — | 100% | −33.0% / 47.5% | −16.1% / 55.2% | −19.4% / 33.7% | −18.2% / 24.5% | — |

Signal agreement at the decision closes:

- SMA–TSM 86.7%, SMA–DON 92.2%, TSM–DON 81.7%; all three agree 80.3% of the
  time.
- Each signal is on 73.0% (SMA), 76.5% (TSM) and 74.2% (DON) of the time.
- Flips per year: SMA 6.5, TSM 5.5, DON 8.1.

## Findings

- **Mixing signal types reduces whipsaw counts; the money gained is real but
  fragile.**
  - E2 has 4 fewer round trips than C0 and a lower losing share (69.5%
    against 75.6%).
  - Its +1.4 pp CAGR is concentrated in choppy years: 2004 +8.5 pp, 2005
    +7.3, 2015 +3.1, 2016 +10.9, 2019 +9.6, 2025 +6.5.
  - It gives some back in 2011 (−4.1) and 2023 (−11.6).
  - In 2000–02 and 2007–09 it is identical to C0, because the signals agree
    in long trends.
  - The bootstrap resamples 1-month blocks. In those worlds E2's edge is
    −0.05 pp, so the historical gain looks like a few specific episodes and
    not a structural improvement.
- **The proportional score lowers the tail most.**
  - EP's bootstrap P(DD > 70%) is half of C0's (6.3% against 12.4%), and its
    P(CAGR < 0) is 20.6% against 23.0%.
  - Partial exposure through disagreements costs strong recoveries: 2007
    +13.4% against +26.6%, 2018 −2.2% against +4.8%.
  - That is why it misses the 2017+ gate.
- **TSM avoids SMA's whipsaws and adds its own failure modes.**
  - TSM is slower to exit: 2022 −29.7% against −20.2%, and 2008 −27.2%.
  - It fails badly on 1957–1999 ^GSPC (5.7% against 9.7%). Part of that is
    the price-only series: without dividends, TSM's T-bill hurdle is about
    3–4 pp/yr harder in that era, a caveat stated in the pre-registration.
  - The 2000–2026 TSM advantage does not carry over to the older era.
- **The Donchian midline flips most** (8.1 per year) and loses most in
  2000–02 (−51.8%). Its 1957–1999 result (11.5%) is the reverse of its
  2000–2026 result. Single-signal choices are regime-dependent.
- **Short-trend worlds still favour SPY.** No arm's block-20 median reaches
  SPY's 8.68% (best: T1 8.01%). With 250-session blocks, every arm except D1
  beats C0's 13.4% (E2 14.2%, T1 14.3%). This is the same pattern as
  `stress_robustness_v1`, `trend_robustness_v2` and `leverage_frontier_v1`:
  the edge comes from long trends, and signal design does not change that.

## Caveats (from the pre-registration)

- **In-sample history.** All arms use the same 2000–2026 history, and the
  bootstrap resamples it. G8 is the only out-of-sample evidence.
- **G8 data.** The ^GSPC series is price only, and its old opens are
  placeholders. Every G8 fill is at the decision close, identically for
  every arm.
- **Mixed SMA series.** The SMA uses QQQ's price close, as VT35 and the
  precedent do. TSM and DON use total return.
- **Seen before writing.** The pre-registration discloses the
  `trend_robustness_v2` and `leverage_frontier_v1` results, the `timesfm_v1`
  index line, and general S&P 500 history 1957–1999.

## Decision

VT35 stays the PAPER identity with no parameter change. No arm qualifies, and
a passing arm would not have been promoted by this campaign anyway. E2's
near-miss (G5 by 0.05 pp) is report-only. Revisiting it would need a new,
separately pre-registered campaign. This campaign is closed. No code,
configuration or runtime changed.
