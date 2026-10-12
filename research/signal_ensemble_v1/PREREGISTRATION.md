# Signal ensemble v1 — pre-registration (2026-10-12)

Written before any arm below other than the C0 control was simulated.

## Seen before writing

- The `trend_robustness_v2` results: no arm passes; the SMA 100–250 ensemble
  (E1) gives up 1.3 pp CAGR, loses more in 2000–02 and has a longer
  underwater spell; C0 13.37% CAGR / 49.6% max DD (2x costs), block-20
  bootstrap median 7.40% against SPY's 8.68%, 46.5% of paths beat SPY.
- The `leverage_frontier_v1` results: no leverage / vol-target cell or
  risk-off arm passes; the calibrated financing model (+0.75%/yr × (L − 1))
  lowers C0 to 12.81% CAGR; block-250 bootstrap C0 ≈ 13.4% against SPY ≈ 9.0%.
- The one-line `timesfm_v1` conclusion in `docs/research/INDEX.md` (no
  TimesFM arm passes; trend-replacement arms lose 5.5–6.1 pp CAGR).
- General S&P 500 history 1957–1999 (bear markets of 1962, 1966, 1969–70,
  1973–74, the 1987 crash, the 1990s bull market) is common knowledge.
- The coverage of the downloaded `^GSPC` file (below), including that Yahoo's
  old opens are placeholders. No signal, return or drawdown of any arm on it
  was computed.
- The only simulation run before this file was committed is the C0 harness
  check (C0 only): it reproduces `trend_robustness_v2`'s C0 historical (m = 1
  and m = 2) and block-20 bootstrap numbers with difference 0.0000 pp.

## Question

VT35 holds QLD while QQQ's completed close is above its 200-session SMA. Its
known weakness is short-trend, whipsaw regimes, where it loses to buy and hold
(block-20 bootstrap median 7.4% against SPY 8.7%). An ensemble of SMA lengths
did not help (`trend_robustness_v2`). Does combining trend signals of
**different types** (moving average, time-series momentum, channel midline)
reduce whipsaw losses and raise long-run profit?

## Data

- **Primary:** the committed `data/historical/stress_robustness_v1_daily.csv`
  and `stress_robustness_v1_dtb3.csv` (SHA-256 in
  `research/stress_robustness_v1/DATA_MANIFEST.json`).
- **Out of sample (G8 only):** `data/historical/signal_ensemble_v1_gspc.csv`,
  Yahoo chart API `^GSPC` daily, 1950-01-03 → 1999-12-31, 12,582 rows,
  price only (no dividends). URL, retrieval time and SHA-256 in
  `research/signal_ensemble_v1/DATA_MANIFEST.json`. Only rows from the first
  DTB3 date (1954-01-04) are used. Yahoo's opens are placeholders in old years
  (O = H = L = C on every bar 1950–1961; from 1962 the open usually equals the
  previous close), so **every G8 open is set to the previous close for every
  arm**: all G8 fills happen at the decision close, identically for every arm.

## Signals

All causal, evaluated on QQQ's completed close at t:

- **SMA:** close > 200-session SMA of closes (the current rule). Computed
  exactly as `trend_robustness_v2` (QQQ price close historically) so that C0
  is VT35 and reproduces the precedent.
- **TSM:** 252-session total return of QQQ (closes plus dividends) >
  cumulative DTB3 return over the same 252 sessions, where session k accrues
  `DTB3[k−1]/252` (the same convention as the synthetic instruments' T-bill
  financing).
- **DON:** QQQ total-return level > midpoint of the max and min of the 126
  total-return levels ending at t (t included).

In the bootstrap every signal runs on the resampled total-return level (as
`trend_robustness_v2` does for the SMA). On `^GSPC` (G8) every signal runs on
the price close and the TSM hurdle uses DTB3 as above.

## Arms (five, no others)

`vt(vol) = min(1, 0.35 / vol)`, vol = annualized sample volatility of the 20
most recent daily returns of the held synthetic instrument. Total weight is
capped at 0.98.

- **C0:** VT35. QLD\* at `SMA · vt`.
- **E2:** binary majority. QLD\* at `[SMA + TSM + DON ≥ 2] · vt`.
- **EP:** proportional. QLD\* at `(SMA + TSM + DON) / 3 · vt`.
- **T1:** TSM alone. QLD\* at `TSM · vt`.
- **D1:** DON alone. QLD\* at `DON · vt`.

## Instruments and financing

- **QLD\*:** daily-reset 2x on QQQ total return, 0.95%/yr expense + DTB3
  financing (`trend_robustness_v2.synthetic_2x`). This is the **base** model.
- **Calibrated** model (`leverage_frontier_v1` Amendment A1): financing at
  DTB3 + 0.75%/yr (× (L − 1) = 1). Every arm, C0 included, is run under both
  models, and each comparison is made within one model. The TSM hurdle always
  uses plain DTB3.
- **G8 instrument:** daily-reset 2x on `^GSPC` price, 0.95% expense + DTB3 +
  0.75% (calibrated only).

## Mechanics (unchanged from `trend_robustness_v2`)

Signal on the completed close, trade at the next open, 0.5 pp band, cost
`4 bps × m` per unit traded, idle cash earns zero. The engine, SMA, vol and
metric functions are imported from `research/trend_robustness_v2/screen.py`
(its `main()` is never run).

**Common window: 2000-03-09 → 2026-09-29.** TSM needs 252 total returns at the
first decision close, which forces the start 3 sessions later than
`trend_robustness_v2`'s 2000-03-06. All gated historical numbers use this
window. C0 on the precedent's window is the harness check.

## Bootstrap

Stationary block bootstrap, 5,000 ten-year paths, seed 20261011; path draws
identical to `trend_robustness_v2` (same generator, draw order and 500-path
chunks; 250 warm-up + 2,520 measured sessions). To give TSM its 252-session
warm-up without changing any draw, each path is extended backward by 3
sessions: the 3 pool sessions preceding its first session, cyclically. The
measured sessions are the precedent's; C0's numbers are unchanged by the
extension (verified by the harness check). Blocks 20 (gated), 60 and 250
(report only), all at m = 2, under both financing models. Reported per arm:
median and 5th-percentile CAGR, P(CAGR < 0), median max DD, P(DD > 50%),
P(DD > 70%), share of paths beating SPY.

## Decision rule (fixed now)

G1–G7 are exactly `trend_robustness_v2`'s, at **2x costs (m = 2)** on the
common window, and each must hold under **both** the base and the calibrated
model (arm vs C0 within the same model):

- **G1** CAGR ≥ C0 CAGR − 0.5 pp.
- **G2** max DD ≤ C0 max DD.
- **G3** CAGR > C0 CAGR, **or** Calmar ≥ 1.10 × C0 Calmar.
- **G4** 2017-01-03 → end CAGR ≥ C0's − 2.0 pp.
- **G5** block-20 bootstrap median CAGR ≥ C0's **and** share of paths beating
  SPY ≥ C0's.
- **G6** G1–G3 also hold at 1x costs (m = 1).
- **G7** annual turnover ≤ 2 × C0's.
- **G8 (out of sample):** on `^GSPC`, first measured session 1957-01-02
  (warm-up complete) through 1999-12-31, each arm's rule applied to `^GSPC`
  with the G8 instrument at m = 2: arm CAGR ≥ C0-rule CAGR − 0.5 pp **and** arm
  max DD ≤ C0-rule max DD + 3 pp.

An arm passes iff G1–G7 hold under both models and G8 holds. Selection: among
passing arms, the highest full-window CAGR at 2x costs, base model (tie:
higher Calmar). If no arm passes, **VT35 stays unchanged** and the campaign
closes. A passing arm is **not** promoted by this campaign; that needs a
separate production step (implementation, Stage B replay against VT35, parity
tests).

## Report only

- Whipsaw statistics: an in-market episode is a run of sessions with target
  weight > 0; a round trip runs from the close before its first held session
  to the close of the first unheld session (exit fill and cost included).
  Round trips per year, share of losing round trips, average return of losing
  round trips. Open episodes at the end are not counted. For EP an episode is
  any non-zero weight; its partial changes are not round trips.
- Time in market (share of measured sessions with target > 0) and average
  target exposure.
- 2000–02, 2007–09, 2020-02/03 and 2022 total return and max DD.
- Signal agreement rates (pairwise, all three, share on, flips per year) at
  the decision closes of the common window.
- Bootstrap block-60 and block-250 medians.

## Caveats known now

- The same 2000–2026 history is used for every arm and resampled by the
  bootstrap; the only out-of-sample evidence is G8.
- G8 is price only (no dividends), so TSM's hurdle there is harder than on a
  total-return series (by roughly the dividend yield, 3–4%/yr in that era),
  and all G8 fills are at the decision close.
- No parameter in this file is tuned after results are seen; no arm is added.
