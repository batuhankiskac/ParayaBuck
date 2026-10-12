# Leverage frontier v1 — pre-registration (2026-10-12)

Written before any arm below was simulated (only C0, which is already
published in `trend_robustness_v2`, was re-run as a harness check). Every
number this campaign gates on comes from
`research/leverage_frontier_v1/screen.py --phase2`, run once after this file is
committed. Nothing in this file is tuned after results are seen; no arm is
added or dropped.

## Seen before writing

Exactly what was computed or read before this file was written:

1. **Data coverage** of the new downloads (`DATA_MANIFEST.json`): TQQQ
   2010-02-11 →, IEF 2002-07-30 →, GLD 2004-11-18 →, GC=F 2000-08-30 → (83
   Yahoo rows with a null open/close dropped), DGS10 1962-01-02 →, all clipped
   to 2026-09-29. The FRED LBMA gold series `GOLDAMGBD228NLBM` and
   `GOLDPMGBD228NLBM` return HTTP 404 (no longer served). No clean daily gold
   series before 2000-08-30 was obtained.
2. **Harness check** (`VALIDATION.json`, `harness_check`): C0 re-run with the
   generalized instrument code reproduces `trend_robustness_v2` C0 on its
   common window (2000-03-06 → 2026-09-29): CAGR 13.3655% / max DD 49.5643%
   at m = 2 and 13.6598% / 49.2471% at m = 1, and its block-20 bootstrap
   (median 7.3958% / p5 −7.5603% / median DD 53.7855% at m = 2; 7.7761% /
   −7.1820% / 53.1090% at m = 1). All differences < 0.0001 pp (the reference
   file is rounded to 6 decimals). These C0 numbers were already published.
   The L = 2 instrument is bit-identical to `synthetic_2x`; L = 1 equals QQQ's
   total return exactly.
3. **Instrument validation** (`VALIDATION.json`, `validation`), data checks
   only:

   | Check | Window | Daily corr | TE (ann.) | CAGR synthetic / real | Annual diff (full years) |
   |---|---|---:|---:|---:|---|
   | L = 2 (0.95%) vs QLD | 2006-06-21 → | 0.9960 | 3.94% | 26.07% / 25.30% (+0.77 pp) | mean +0.64 pp, range −0.09 … +1.59 |
   | L = 3 (0.84%) vs TQQQ | 2010-02-11 → | 0.9990 | 2.93% | 44.52% / 42.88% (+1.64 pp) | mean +1.81 pp, range −0.11 … +4.73 (2020 +4.7, 2023 +4.5) |
   | BOND10 (−0.15%) vs IEF | 2002-07-30 → | 0.9607 | 2.23% | 2.69% / 3.37% (−0.68 pp) | mean −0.65 pp, range −5.18 … +2.43; vol 7.7% vs 6.8%, beta(IEF on BOND10) 0.84 |
   | GC=F proxy (−0.40%) vs GLD | 2004-11-18 → | 0.8866 | 8.74% | 10.38% / 10.36% (+0.02 pp) | mean +0.05 pp, range −0.60 … +0.54 |

   The synthetic leveraged instruments are optimistic: the real funds pay
   more than DTB3 for financing (implied all-in drag 1.64%/yr for QLD and
   2.49%/yr for TQQQ against 0.95% / 0.84% expense). The gold proxy's low
   daily correlation is a timing effect (COMEX settlement vs NYSE close); it
   has no drift.
4. **Prior published results known to the author** (from `docs/research/`):
   `trend_robustness_v2` (C0 = VT35 on synthetic QLD, 2000-03 → 2026-09);
   `stress_robustness_v1` "VT35 + BIL" (all idle cash, including the in-trend
   remainder, parked in T-bills: 13.9% vs 13.8% CAGR, 45.9% vs 49.6% max DD,
   1999-12 → 2026-09; uptrend in 73% of sessions); `rule_variants_v1`
   (2017-01 → 2026-07: T-bill parking F-BIL/F-SGOV narrowly failed; gold
   diversification E-GLD cut DD ~3 pp and cost 3–5 pp CAGR); `profit_max_v1`
   (Stage B 2017-01 → 2026-07: TQQQ trend report-only 42.2% CAGR / 53.8% DD;
   VT25/VT35 chosen on 2016+ data; "growth-optimal leverage ≈ 2x").
5. **Code smoke test**: the phase-2 code path was run once on random synthetic
   data (scratch only) to check that it executes. No real-data result of any
   Stage-1 cell other than C0 or of any Stage-2 arm was computed; no trend
   state at any specific date was inspected.

## Question

VT35's leverage (2x), vol target (35%) and zero-yield risk-off state were never
tested on long history. (A) Is 2x the right leverage? (B) Is 35% the right vol
target? (C) Should the risk-off state hold something other than zero cash?
The owner's goal is maximum long-run profit versus S&P 500 buy-and-hold;
drawdowns are accepted.

## Data

- Committed: `data/historical/stress_robustness_v1_{daily,dtb3}.csv`
  (QQQ/SPY/QLD/VOO bars, DTB3; SHA-256 in
  `research/stress_robustness_v1/DATA_MANIFEST.json`).
- New (`research/leverage_frontier_v1/DATA_MANIFEST.json`, SHA-256 per file):
  `data/historical/leverage_frontier_v1_daily.csv` (TQQQ, IEF, GLD, GC=F:
  symbol, date, open, close, dividend; Yahoo chart API) and
  `data/historical/leverage_frontier_v1_dgs10.csv` (FRED DGS10).
- The phase-2 run is offline and reads only these files.

## Instruments

- **NQ\*(L)**, L ∈ {1.0, 1.5, 2.0, 2.5, 3.0}: daily-reset L × QQQ total
  return − expense − (L − 1) × DTB3 financing, per session
  `1 + L·r − (expense + (L − 1)·DTB3[t−1]) / 252`; open from the overnight
  return the same way (exactly `synthetic_2x` generalized).
  - L = 1.0: real QQQ total return (expense 0; QQQ's own fee is in its price).
  - L = 2.0: 0.95% (QLD); identical to the precedent's QLD\*.
  - L = 3.0: 0.84% (TQQQ, as specified; not re-verified, because network
    access was limited to FRED and the Yahoo chart API, which do not carry
    expense ratios).
  - L = 1.5 and 2.5: 0.95%. **Hypothetical** — no daily-reset 1.5x or 2.5x
    Nasdaq-100 product exists. They could be approximated by a QQQ/QLD or
    QLD/TQQQ mix, but a mix of daily-reset funds rebalanced at the 0.5 pp
    band is not identical to a daily-reset L-x fund (it drifts between
    rebalances and pays two expense ratios).
- **BILLS**: `1 + (DTB3[t−1] − 0.10%) / 252` per session (the
  `stress_robustness_v1` BIL convention).
- **BOND10**: synthetic constant-maturity 10-year Treasury total return from
  DGS10 (forward-filled onto equity sessions), standard par-bond
  approximation per session: `y[t−1]/252 − D·Δy + ½·C·Δy² − 0.15%/252`, where
  D and C are the modified duration and convexity of a 10-year semiannual
  par bond at y[t−1] (D ≈ 8.2, C ≈ 79 at 4%). The 0.15%/yr is an IEF-class
  expense ratio, so BOND10 is net of fees like the other instruments.
- **GOLD**: real GLD open/close on sessions where GLD has bars on t−1 and t
  (from 2004-11-19). Earlier (and on the splice session 2004-11-18): GC=F
  close-to-close × (1 − 0.40%/252), with a GC=F close missing on an equity
  day carried forward. First GOLD return: 2000-08-31.
- BILLS, BOND10 and the pre-GLD gold proxy have no real open: their open is
  the previous close (the whole session return is intraday), so a trade "at
  the next open" fills at the previous close's level for these legs.

## Signal, weights and mechanics (unchanged from `trend_robustness_v2`)

- `up[t]` = QQQ's completed close > its 200-session SMA (all arms).
- `vt(vol) = min(1, VT / vol)`; vol = annualized sample volatility of the 20
  most recent daily returns of the held NQ\*(L); VT = none means weight 1.
  Edge convention as before (vol ≤ 0 or undefined → 1).
- Equity leg target = `up · min(0.98, vt(vol))`.
- Risk-off leg target (Stage 2 only) = `(1 − up) · 0.98`, at full allocation
  with no vol targeting. **Risk-off state only**: the unused remainder in the
  trend state stays zero cash, so in-trend behaviour is identical to the cash
  version. Justification: Stage 2 then isolates question (C); parking the
  in-trend remainder was already screened (`stress_robustness_v1` VT35+BIL,
  `rule_variants_v1` F-BIL); and a vol target on a 7–18%-vol asset at
  VT ≥ 0.30 would be 1 almost always and only add band trades.
- Signal on the completed close, trade at the next open; rebalance all legs to
  target when any leg drifts > 0.5 pp; cost `4 bps × m` per unit of traded
  notional on every leg (risk-off trades included), m ∈ {1, 2}; cash earns
  zero; dividends via the total-return construction.

## Windows

- **Full window**: 2000-03-06 → 2026-09-29 (the `trend_robustness_v2` common
  window; first measured session index 250). All Stage-1 cells, and BILLS and
  BOND10 Stage-2 arms with their cash bases.
- **Gold window**: 2000-08-31 → 2026-09-29 (first session with a GOLD
  return). GOLD arms and their cash bases are both run on it; GOLD results are
  never compared with full-window numbers.
- Sub-periods (CAGR, calendar years / 365.25): P1 window start → last close ≤
  2008-12-31; P2 → last close ≤ 2016-12-31; P3 → end (2017-01-03 → end).

## Statistics

Full-window CAGR, max DD, Calmar, Sharpe (daily, rf = 0), annual turnover,
trades, worst calendar year (≥ 240 sessions), 2000–02, 2007–09 and 2022 total
return and max DD, longest underwater spell, P1/P2/P3 CAGR.

**Block bootstrap**: stationary block bootstrap of whole sessions resampling
jointly QQQ and SPY overnight/intraday returns and dividends, the previous
session's DTB3, and the BOND10 and GOLD session returns; 5,000 paths, seed
20261011, 250 warm-up + 2,520 measured sessions (10 years); draw order as
`trend_robustness_v2` (so the block-20 full-pool paths are identical to it).
Mean block **20 (gated)**, 60 and 250 (report-only). Pool: all sessions
1999-03-11 → 2026-09-29; for GOLD arms and their bases, sessions from
2000-08-31 only. Per arm: median, p5/p25/p75/p95 10-year CAGR,
P(CAGR < 0), median max DD, P(DD > 50%), P(DD > 70%), share of paths beating
SPY. All arms on a given (pool, block) see the same paths. Stage-1 bootstrap
runs at m = 2; Stage-2 bootstraps at m = 2 (blocks 20/60/250) and m = 1
(block 20).

## Stage 1 — leverage × vol target (risk-off = zero cash)

25 cells: L ∈ {1.0, 1.5, 2.0, 2.5, 3.0} × VT ∈ {0.30, 0.35, 0.40, 0.50,
none}. Cell (2.0, 0.35) is **C0** (= VT35). A cell is **eligible** iff all
hold (m = 2 and full window unless stated; bootstrap = block 20, m = 2):

- **G1** CAGR ≥ C0 CAGR + 1.0 pp.
- **G2** bootstrap median CAGR ≥ C0's **and** bootstrap p5 CAGR ≥ C0's − 1.0 pp.
- **G3** max DD ≤ 65% **and** bootstrap P(DD > 70%) ≤ C0's + 5 pp.
- **G4** 2017-01-03 → end CAGR ≥ C0's − 2.0 pp.
- **G5** CAGR ≥ C0's in at least 2 of P1, P2, P3.
- **G6** G1 also holds at m = 1 (against C0 at m = 1).
- **G7** plateau: every grid neighbour (adjacent L at the same VT; adjacent VT
  at the same L, in the order 0.30, 0.35, 0.40, 0.50, none — "none" is
  adjacent to 0.50 only) has full-window m = 2 CAGR ≥ C0's. C0 counts as a
  neighbour like any other cell (it passes this trivially).

**Selection**: among eligible cells, the highest bootstrap (block 20, m = 2)
median CAGR (tie: higher p5). If none is eligible, Stage 1 selects nothing.

*(The original report-only financing sensitivity that stood here is
replaced by Amendment A1 below, which makes it a gate.)*

## Stage 2 — risk-off asset

For C0 and for the Stage-1 selected cell (if any), three arms replace zero
cash in the risk-off state with BILLS, BOND10 or GOLD (≤ 6 arms). **Base** =
the same cell with zero cash, on the arm's own window and bootstrap pool. An
arm **passes** iff all of the following hold at m = 2 **and** again at m = 1
(each m against the base at the same m):

- CAGR ≥ base + 0.3 pp;
- max DD ≤ base + 2.0 pp;
- bootstrap (block 20) median CAGR ≥ base's;
- CAGR ≥ base's in at least 2 of P1, P2, P3.

Among a base's passing arms the best is the one with the largest m = 2
block-20 bootstrap median excess over its base.

## Decision rule (fixed now)

- Candidate = the Stage-1 selected cell, with its best passing risk-off
  asset if any; else C0's best passing risk-off asset; else **none — VT35
  stays unchanged**.
- No candidate is promoted by this campaign. A candidate would need a
  separate pre-registered production step: implementation, a Stage B replay
  on the production engine against VT35, and parity tests. A candidate whose
  L is 1.5 or 2.5 additionally needs a pre-registered, separately tested
  implementation (fund mix) because no such product exists.
- The harness check must pass before any arm result is read; if it fails the
  run stops.

## Report-only

Sharpe, Calmar, worst calendar year, 2000–02 / 2007–09 / 2022 drawdowns,
longest underwater, turnover, trades, P(10-yr CAGR < 0), P(DD > 50% / 70%),
share of bootstrap paths beating SPY, blocks 60 and 250, and each Stage-2
arm under its non-gating financing model.

## Amendment A1 — before any arm result (2026-10-12)

Made after the phase-1 validation and before any Stage-1 cell other than C0
or any Stage-2 arm was simulated. Reason: the synthetic leveraged instruments
are financed at DTB3, but real QLD and TQQQ lag them by about 0.69 and
1.65 pp/yr. That bias grows with L and is the same size as G1's +1.0 pp
margin, so a high-L cell could pass on financing it would not get in
practice.

- **Two financing models.**
  - *Base*: as specified above.
  - *Calibrated*: every NQ\*(L) pays an extra 0.75%/yr × (L − 1) on top of
    DTB3 financing: `1 + L·r − (expense + (L − 1)·(DTB3[t−1] + 0.75%)) / 252`.
    This applies to every cell including C0 (L = 2: +0.75%/yr; L = 3:
    +1.50%/yr; L = 1: unchanged).
- **Stage 1.** G1–G7 are evaluated separately within each model, like for
  like: each cell is compared with C0 under the same model, and G7's
  neighbours are evaluated under the same model. A cell is **eligible only if
  it is eligible under both models**. The block-20, m = 2 bootstrap (and the
  report-only blocks 60 and 250) is run for all 25 cells under both models.
- **Selection.** Among cells eligible under both models, pick the highest
  **calibrated-model** bootstrap (block 20, m = 2) median CAGR (tie: higher
  calibrated p5).
- **Stage 2.**
  - For a selected cell with L > 2, the gating model is the calibrated one:
    arm and base both use calibrated financing.
  - For C0 (and a selected cell with L ≤ 2), the gating model stays the base
    one.
  - In every case the non-gating model's Stage-2 result is computed and
    reported only.
  - The Stage-2 pass conditions themselves are unchanged.
- The decision rule is unchanged.
- Concerns raised at phase 1 remain disclosed and do not change any arm or
  gate. They are: in-sample grid selection; BILLS partly seen before; BOND10
  duration above IEF's; the shorter gold window and pool; 1.5x/2.5x being
  hypothetical; previous-close fills for the synthetic risk-off legs; no
  taxes.
- The phase-2 code was smoke-tested again on random synthetic data (scratch
  only), including a forced selection of an L = 3 cell to exercise the
  calibrated Stage-2 path. No real-data arm result was computed.
