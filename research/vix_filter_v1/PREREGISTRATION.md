# VIX filter v1 — pre-registration (2026-10-12)

Written before any arm below other than the C0 control was simulated.

## Seen before writing

- The `trend_robustness_v2` results: no arm passes; C0 13.37% CAGR / 49.6%
  max DD (2x costs, 2000-03-06 → 2026-09-29), 2017+ 30.7%, block-20
  bootstrap median 7.40% against SPY's 8.68%, 46.5% of paths beat SPY.
- The `leverage_frontier_v1` results, including Amendment A1 (calibrated
  financing: C0 12.81%; C0's block-20 P(DD > 70%) 12.4% base / 13.3%
  calibrated) and its risk-off arms (BILLS/BOND10/GOLD all fail).
- The `signal_ensemble_v1` results, including its episode table for C0
  (2000–02 −41.4% / DD 43.4%; 2020-02/03 −15.7% / DD 27.6%; 2022 −20.2% /
  DD 21.3%) and its ^GSPC 1957–1999 G8 (C0 rule 9.70% / 39.1%).
- The one-line conclusions of `gold_riskoff_oos_v1` and `timesfm_v1` in
  `docs/research/INDEX.md`.
- General knowledge of VIX history: spikes in 1998 (LTCM), 2001–02, 2008
  (close peak ≈ 80 in Nov 2008), 2010 and 2011-08, 2015-08, 2018-02 and Q4,
  2020-03 (close peak ≈ 83), 2022 (mostly 20–35, no extreme spike), 2025-04;
  long stretches above 30 in 2002 and 2008–09; the term structure inverts in
  sell-offs. General market history 1990–1999 (1990 bear market, 1997-10,
  1998-08).
- The data validation (`VALIDATION.json`) and coverage below. No signal,
  return or drawdown of any arm other than C0 was computed.
- The only simulation run before this file was committed is the C0 harness
  check (C0 only): it reproduces `trend_robustness_v2`'s C0 historical (m = 1
  and m = 2) and block-20 bootstrap numbers with difference 0.0000 pp.

## Question

VT35's known weakness is that the 200-session SMA reacts slowly to fast
crashes (2020-02/03, 2022, a 1987-style day). Does an implied-volatility
(fear index) filter layered on VT35 cut crash losses enough to raise
long-run profit?

## Data

- **Primary:** the committed `data/historical/stress_robustness_v1_daily.csv`
  and `stress_robustness_v1_dtb3.csv` (QQQ/SPY, DTB3).
- **Fear indices (Yahoo chart API, closes only):**
  `data/historical/vix_filter_v1_vix.csv` (^VIX 1990-01-02 → 2026-09-29),
  `vix_filter_v1_vxn.csv` (^VXN 2001-01-23 →), `vix_filter_v1_vix3m.csv`
  (^VIX3M 2006-07-17 →; Yahoo carries CBOE's back-filled history, earlier
  than the series' 2007-12 launch).
- **G8:** `vix_filter_v1_gspc.csv` (^GSPC 1988-01-04 → 1999-12-31, price
  only).
- **Cross-check:** `vix_filter_v1_fred.csv` (FRED VIXCLS, VXNCLS).
- URLs, retrieval time and SHA-256 in `DATA_MANIFEST.json`; download script
  `fetch_data.py` (run once; `screen.py` is offline).
- **Validation (`VALIDATION.json`):**
  - Every QQQ session from each index's first date has a same-date close (no
    forward fill needed); no non-positive values.
  - ^VIX vs VIXCLS: 99.87% of 9,252 common dates agree to 0.01; 3 dates
    differ by more than 0.5 (largest 2.61 on 2026-02-06).
  - **^VXN vs VXNCLS: they disagree systematically in 2001–2003** (646
    dates, Yahoo ≈ 7–9 points higher; e.g. 2001-02-02 63.75 vs 54.89) and
    agree from 2004 on (4 later dates differ). The two sources appear to carry
    different methodologies for the early years. **X4 uses Yahoo ^VXN** (the
    specified source); a report-only sensitivity re-runs X4 (base, m = 2) with
    FRED VXNCLS where available. It cannot change any gate.
  - ^GSPC: 1,957 of the 2,468 G8-window opens are placeholders (equal to the
    previous close to 0.01 points).

## Rules

All signals use the completed close at t; trades fill at the next open.
`vt(v) = min(1, 0.35 / v)`, `vol20` = annualized sample volatility of the 20
most recent daily returns of the held synthetic instrument (QLD\*). `trend` =
QQQ price close > its 200-session SMA (VT35, computed exactly as
`trend_robustness_v2`). Target weight is capped at 0.98.

## Arms (five, no others)

- **C0:** VT35. QLD\* at `trend · vt(vol20)`.
- **X1 level:** `C0 · [VIX close ≤ 30]`. Cash while VIX > 30; re-entry when
  VIX ≤ 30 and the trend is on.
- **X2 spike:** `C0 · [VIX / SMA60(VIX) ≤ 1.5]` (SMA60 of the 60 VIX closes
  ending at t, inclusive). Cash while the ratio is > 1.5.
- **X3 term structure:** `C0 · [VIX ≤ VIX3M]`. Cash while VIX > VIX3M
  (backwardation).
- **X4 implied-vol sizing:** `trend · vt(max(vol20, 2 × VXN / 100))`.

## Instruments and financing

- **QLD\*:** daily-reset 2x on QQQ total return, 0.95%/yr expense + DTB3
  financing (`trend_robustness_v2.synthetic_2x`). **Base** model.
- **Calibrated** model (`leverage_frontier_v1` A1): financing at DTB3 +
  0.75%/yr. Every arm and C0 run under both; comparisons are within a model.
- **G8 instrument:** daily-reset 2x on ^GSPC price, 0.95% + DTB3 + 0.75%
  (calibrated), m = 2.

## Mechanics (unchanged from `trend_robustness_v2`)

Signal on the completed close, trade at the next open, 0.5 pp band, cost
`4 bps × m` per unit traded, idle cash earns zero. Engine, SMA, vol, synthetic
2x and metrics are imported from `research/trend_robustness_v2/screen.py`
(its `main()` is never run).

## Windows (each arm against C0 on the same window)

- **Main (C0, X1, X2):** 2000-03-06 → 2026-09-29, `trend_robustness_v2`'s
  common window. VIX exists from 1990, so X1/X2 need no extra warm-up.
- **X3:** 2006-07-18 → 2026-09-29 (first decision close with VIX3M). C0 is
  re-run on this window. **Disclosed as the short-window arm.**
- **X4:** 2001-01-24 → 2026-09-29 (first decision close with VXN). C0 is
  re-run on this window. It starts after the 2000 peak.

## Bootstrap

- Stationary block bootstrap, mean block 20, 5,000 paths of 250 warm-up +
  2,520 measured sessions, seed 20261011, m = 2, base and calibrated. Same
  generator, draw order and 500-path chunks as `trend_robustness_v2`.
- Whole sessions are resampled jointly: QQQ/SPY overnight and intraday
  returns, dividends, DTB3, **and the VIX, VXN and VIX3M closes of the same
  session**. SMA200, vol20 and the VIX SMA60 are recomputed on each
  bootstrapped path. **Levels jump at block joins** (a VIX of 15 can follow a
  VIX of 40), so a spike rule sees artificial spikes and drops at joins; this
  is disclosed and affects X2/X3 most.
- **Pools:** main arms (C0, X1, X2) use every session (identical draws to
  `trend_robustness_v2`). X3 draws only from sessions with VIX3M (2006-07-17
  →) and X4 only from sessions with VXN (2001-01-23 →); C0 is re-run on each
  of those pools for its comparison.
- Reported: median and 5th-percentile CAGR, P(CAGR < 0), median max DD,
  P(DD > 50%), P(DD > 70%), share of paths beating SPY.

## Decision rule (fixed now)

At **2x costs (m = 2)**, on each arm's own window and pool against C0 on the
same window and pool, each of G1–G7 must hold under **both** the base and the
calibrated model:

- **G1** CAGR ≥ C0 CAGR − 0.5 pp.
- **G2** max DD ≤ C0 max DD.
- **G3** CAGR > C0 CAGR, **or** Calmar ≥ 1.10 × C0 Calmar.
- **G4** 2017-01-03 → end CAGR ≥ C0's − 2.0 pp.
- **G5** block-20 bootstrap median CAGR ≥ C0's **and** share of paths beating
  SPY ≥ C0's.
- **G6** G1–G3 also hold at 1x costs (m = 1).
- **G7** annual turnover ≤ 2 × C0's.
- **G8 (out of sample):** ^GSPC with ^VIX. ^GSPC data from 1988 gives the
  SMA200 its warm-up; ^VIX starts 1990-01-02, and the first measured session
  is the first with full warm-up of every input (200 closes, 20 synthetic
  returns, 60 VIX closes): **1990-03-28 → 1999-12-31**. Placeholder or
  missing opens (missing, ≤ 0, or within 0.01 points of the previous close)
  are set to the previous close, identically for both arms. Pass iff the
  arm's rule on ^GSPC has CAGR ≥ the C0 rule's − 0.5 pp **and** max DD ≤ the
  C0 rule's + 3 pp.
  - **X3 and X4 have no pre-2000 VIX3M/VXN data, so G8 is "not available"
    for them and they cannot pass. Only X1 and X2 can be selected.**

**Selection:** among passing arms, the highest full-window CAGR at m = 2,
base model (tie: higher Calmar). If none passes, **VT35 stays unchanged** and
the campaign closes. **No promotion in this campaign**: a passing arm would
need a separate pre-registered production step (implementation, Stage B
replay against VT35, parity tests).

## Report only

- **Crash episodes** (base, m = 2, each arm on its own window; from the last
  close ≤ the first date to the last close ≤ the second):
  1998-08 (1998-06-30 → 1998-10-30, G8 ^GSPC run, C0/X1/X2 only);
  2000–02 (1999-12-31 → 2002-12-31; starts at the window start);
  2008 (2007-12-31 → 2008-12-31); 2011-08 (2011-06-30 → 2011-10-31);
  2015-08 (2015-06-30 → 2016-02-29); 2018-Q4 (2018-09-28 → 2018-12-31);
  2020-02/03 (2020-01-31 → 2020-03-31); 2022 (2021-12-31 → 2022-12-30);
  2025-04 (2025-01-31 → 2025-04-30). For each: total return, max DD within
  the episode, sessions out of market (target 0), and the **rebound**: from
  the index's (QQQ, or ^GSPC for 1998) lowest close in the episode to 63
  sessions later, the arm's and C0's return and sessions out; the return
  missed is C0's minus the arm's.
- **VIX-triggered exits** (X1–X3): a decision close where the arm's target
  is 0, its previous target was > 0, and C0's target is > 0 (the override,
  not the trend, caused the exit). Exits per year; the share that **avoided
  a further loss**, i.e. QLD\* at the re-entry fill (the open after the next
  decision close with a positive arm target; last close if none) is below
  QLD\* at the exit fill; mean QLD\* return while out; median sessions out.
  Also on ^GSPC for X1/X2.
- X4 VXN-source sensitivity (Yahoo vs FRED VXNCLS), time in market, Sharpe,
  SPY total-return hold on each window.

## Caveats known now

- All arms use the same 2000–2026 history, which the bootstrap resamples; G8
  (1990–1999, price only, mostly previous-close fills) is the only
  out-of-sample evidence, and only for X1 and X2. It contains one bear market
  (1990) and the 1997/1998 shocks; it is short.
- X3's window starts 2006-07 and X4's 2001-01, so their comparisons with C0
  cover different histories from X1/X2's; X4's window misses the 2000 peak.
- VIX/VIX3M are S&P 500 implied vols, applied to a Nasdaq-100 position. VXN
  history in 2001–2003 differs between sources (above).
- Index levels jump at bootstrap block joins (above).
- No parameter in this file is tuned after results are seen; no arm is added.
