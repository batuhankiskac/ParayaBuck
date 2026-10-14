# tsm_tr_v1 pre-registration (2026-10-14)

## Question

Production R2 (`equity_leveraged_trend_v1` 1.2.0) reads the TSM vote from the QQQ
**split-adjusted price** close against a **zero** hurdle and DON from the price close, because
production had neither a dividend nor a rate series. Alpaca's corporate-actions API does supply
QQQ **cash dividends**, so production could build a QQQ **total-return** series; a rate series is
still missing, but a **fixed** hurdle is possible instead.

`e2_integration_v1` measured the production simplification at **−0.15 pp CAGR** versus research
E2 (price TSM + zero hurdle + price DON = 14.67 %, research E2 = 14.82 %, m = 2, base, 2000–2026)
and reported that the hurdle choice alone moves the result by ±0.7 pp — larger than E2's whole
bootstrap edge. This campaign re-measures those substitutions **on top of R2** (E2 *plus* the
early re-entry path, which itself reads the same vote) and, decisively, **after Turkish income
tax**, where the 2026-10-14 production change (R2) was selected and where a ~5 pp/yr tax cost
amplifies small pre-tax edges.

Question: **on top of production R2, which production-feasible TSM/DON variant is best, pre-tax
and after Turkish tax?**

## Control P0 = production R2 exactly

E2's 2-of-3 vote on the QQQ **price** close — (1) close > SMA200, (2) 252-session **price**
return > 0 (zero hurdle), (3) close > midpoint of the 126-session max/min — sized
`vt = min(1, 0.35/vol20)` on synthetic QLD\* (daily-reset 2x on QQQ total return, 0.95 %/yr
expense + DTB3, +0.75 %/yr calibrated), total cap 0.98, decide at the completed close, fill at
the next open, 0.5 pp resize band; **plus** the early re-entry path (active only while the vote
is off; enter at **0.5 ×** `vt` if close > SMA50 and QQQ vol20 < its own trailing 252-session
median and close > close 20 sessions ago; exit below SMA50; the vote overrides and ends the early
position).

`--harness` must reproduce `research/fast_reentry_v1/RESULTS.json`'s R2 to 0.01 pp: base
15.48 % / calibrated 14.90 % CAGR (m = 2), max DD 46.76 %, m = 1 15.75 %; and C0 (the same vote
without the early path) must reproduce `e2_integration_v1`'s production-E2 row (14.67 % / 14.10 %,
DD 49.56 %). The after-tax engine must reproduce `vt_level_tax_v1`'s C0 anchors and
`r2_tbill_combo_v1`'s R2 after-tax figures (10.16 / 14.71 / 22.89 % on the 2000 / 2010 / 2017
windows at $100 k).

## Arms (every parameter fixed; exactly these five, plus one harness-only reference arm)

`(tsm series, hurdle, DON series)` in the `e2_integration_v1` naming
(`research/e2_integration_v1/deviation.py`). The **SMA200 vote is always on the price close**
(production); TSM compares the 252-session return of its series with the cumulative hurdle return
over the same 252 sessions; DON is the 126-session max/min midpoint test on its series.

| Arm | tsm series | hurdle | don series | Production-feasible? |
|---|---|---|---|---|
| **P0** (control) | price | 0 | price | yes (today's production) |
| **P1** | total return | 0 | price | yes: Alpaca dividends build the TR series |
| **P2** | total return | 0 | total return | yes |
| **P3** | total return | flat 2 %/yr | price | yes: fixed constant, no rate feed |
| **P4** (report only) | total return | cumulative DTB3 | total return | **no** — needs a rate series production does not have |
| C0 (harness only) | price | 0 | price | P0's vote with the early path removed; never a candidate |

Fixed interpretations, so nothing is chosen after seeing data:

- **Total return** = the level of `cumprod(1 + (close[i] + dividend[i]) / close[i-1] - 1)`, i.e.
  the same dividend series the harness already credits to synthetic QLD\*. It is a *signal* input
  only: every arm trades the identical instrument, so the arms differ **only** in the vote.
- **Flat 2 %/yr hurdle** = the cumulative level of a constant 2 % annual rate accruing
  `rate/252` per session (`(1 + 0.02/252)^252 − 1 = 2.020 %` over the 252-session window).
- **P1/P2 keep the early path's own inputs on the price close** (SMA50, vol20, 20-session
  momentum, its vol-median) — the early path is production's and is not re-parameterised; only
  its on/off trigger (the E2 vote) changes with the arm.
- Everything else — vol sizing, cap, band, costs (4 bps × m per side), financing models, window,
  early-path weight 0.5 — is identical across arms.

## Data

No network. All inputs are byte copies of committed research data (SHA-256 in
`DATA_MANIFEST.json`):

- Main window **2000-03-09 → 2026-09-29** (6,679 sessions; `START = 253`), from
  `stress_robustness_v1_daily.csv` (QQQ open/close/cash dividend, `stress_robustness_v1_dtb3.csv`
  for DTB3) — the same files `fast_reentry_v1` and `r2_tbill_combo_v1` use, so the control is
  reproduced exactly.
- OOS: `^GSPC` 1957-01-02 → 1999-12-31 and `e2_confirmation_v1`'s M1–M5, from
  `ewma_band_v1_gspc.csv` / `ewma_band_v1_index.csv` + `ewma_band_v1_tb3ms.csv`.
- After tax: `after_tax_tr_v1_usdtry_cbrt.csv` (CBRT USD/TRY forex buying) and
  `after_tax_tr_v1_yiufe.csv` (Yi-UFE).

Known data property (stated in advance, not a finding): QQQ paid **no** cash dividend from
1999-03-10 until 2003-12-24 (first payment 0.014 on 2003-12-24; the 90 payments over the window
total 27.58 index points). Therefore P1–P4 are *identical* to P0/P1 for the first years of the
main window and can only differ from 2004 on. This is a property of QQQ, not of the test.

## Mechanics and engine identity (checked by `--harness`, which stops `--run` on failure)

1. C0 and P0 reproduce the published anchors above (0.01 pp).
2. The E2-only variants P0/P2/P4 and the flat-hurdle **price** variant
   (`E2_price_h2` = price/2 %/price) reproduce `E2_INTEGRATION_DEVIATION.json`'s
   `E2_prod_price_h0` / `E2_tr_h0` / `E2_research` / `E2_price_h2` CAGR, max DD and turnover for
   base/calibrated × m = 1/m = 2 — i.e. the P1–P4 signal construction is the published one.
3. The P0-spec vote equals the direct production construction (SMA200 + price-252-return > 0 +
   DON126 ≥ 2).
4. The band simulator equals `trend_robustness_v2.simulate_multi` bit for bit at m = 2.
5. The after-tax single-book runner equals the vendored `tax_engine.run_strategy` NAV path
   exactly (pre-tax) and reproduces the C0 progressive anchors and the R2 after-tax figures
   (0.005 pp).

## Pre-registered pre-tax gates (all measured against **P0**, m = 2 unless stated)

G1–G9 are `ewma_band_v1`'s gates with the control changed from C0 to P0 (production R2):

- **G1_base / G1_calibrated**: CAGR ≥ P0, or (Calmar ≥ 1.10 × P0 and CAGR ≥ P0 − 0.5 pp).
- **G2_base / G2_calibrated**: m = 2 max DD ≤ P0 + 1 pp.
- **G3_base / G3_calibrated**: 2017-01-02 → 2026-07-30 CAGR ≥ P0 − 1 pp.
- **G4**: CAGR above P0 in ≥ 2 of the 3 sub-periods (→2008-12-31, 2009–2016, 2017→).
- **G5_base / G5_calibrated**: main block-20 stationary bootstrap (seed 20261011, 5,000 paths,
  m = 2) median Δ CAGR vs P0 ≥ 0.
- **G6_base / G6_calibrated**: G1 at m = 1.
- **G7**: the six OOS windows (^GSPC 1957–1999, M1–M5, calibrated, m = 2) — median Δ CAGR ≥ 0,
  ≥ 5 of 6 at Δ CAGR ≥ −0.5 pp, ≥ 5 of 6 at Δ max DD ≤ +3 pp, and none worse than +8 pp.
- **G8**: pooled OOS bootstrap (M1–M5, 2,000 paths each, block 20) median Δ ≥ 0.
- **G9**: turnover/yr ≤ 2 × P0's.

### Pre-registered reduced OOS gate

The six OOS windows carry **price-only** index series; no dividend series exists in this harness
for any of them (and building one is out of scope). On those windows total return **is** the
price level, so **P1 and P2 have exactly the same vote as P0 there and their OOS Δ is 0 by
construction** — G7/G8 cannot discriminate them (they would pass vacuously). Pre-registered:

- For **P1 and P2** the OOS sub-gates G7 and G8 are **non-identifying and waived**; they are
  reported with a `non_identifying` flag and excluded from the pass set. The study states
  explicitly that no out-of-sample evidence on the total-return substitution is available from
  this harness, and that P1/P2's decision therefore rests on the main window (in-sample, single
  history) plus the after-tax gates.
- For **P3 and P4** G7/G8 are identifying (the hurdle is not the price level) and are applied in
  full.
- G1–G6 and G9 are applied in full to every candidate; the main-window gates are what the six
  OOS windows in `ewma_band_v1` were meant to back up, and the after-tax gates are decisive.

## Pre-registered after-tax gates ($100 k, base financing, m = 2, liquidated at the end)

Progressive 2026 Turkish tariff, the corrected multi-instrument engine
(`tax_engine.py` = `vt_level_tax_v1`'s model): windows 2000-03-09 → 2026-09-29,
2010-01-01 → 2026-09-29 and 2017-01-03 → 2026-09-29. A candidate must

1. beat P0 in **all three** windows, **and**
2. in the clean after-tax bootstraps **B1-recent** (progressive, real 2015-09-01 → 2026-09-30
   TRY calendar with its own FX and Yi-UFE, market returns resampled block-20, seed 20261011,
   1,000 paths, 10-year paths, $100 k, liquidated) and **B2** (flat 20 %) show
   **median Δ CAGR vs P0 ≥ 0** and **share of paths above 0 ≥ 55 %**.

## Selection rule

Selected = the arm with the **highest main-window m = 2 base CAGR** among P1–P3 that passes every
applicable pre-tax gate **and** the after-tax condition; otherwise **"none"** (production R2
stays). P4 is report-only and can never be selected; C0 is never a candidate.

## Reported but not gated

How many decision closes differ from P0 per arm (vote vs effective target), by calendar year and
as contiguous blocks; the early path's episodes and their P&L vs C0; cost stress at m = 1/2/4;
both financing models; per-year returns of the main window.

## Amendments

None at the time of the pre-registration commit. Any post-run change goes here, with the reason.
