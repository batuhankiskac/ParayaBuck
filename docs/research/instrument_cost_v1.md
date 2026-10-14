# Instrument cost v1 (2026-10-14)

> Evidence for this report (`research/instrument_cost_v1/` code, data inputs and results) is not on
> `main`; it is preserved in tag `archive/research/instrument-cost-v1-2026-10`.

**No candidate passes, so production keeps QLD.** The one alternative instrument that
exists with real, long data - TQQQ (3x) held at 2/3 weight and the rest in cash - does
not buy the same exposure more cheaply. It fails four of the six pre-registered gates:
the 2017+ return (GT1), the real-return block bootstrap (GT3), the 2017+ after-tax
window (GT4) and the B1-recent bootstrap's share of winning paths (GT5).

- **TQQQ at 2/3 weight is not cheaper; it is a wash at best.** Pre-tax CAGR 22.23%
  against QLD's 22.20% over 15.6 years, max DD 34.76% against 37.88%. Since 2017 it
  loses 0.77 pp/yr (31.30% against 32.07%). In the block-20 bootstrap of the real
  paired returns it loses in **86.5%** of 5,000 ten-year paths, median **-0.35 pp**.
- **The reason is the financing, not the expense ratio.** TQQQ's 0.84% expense ratio is
  0.11 pp below QLD's, and at 2/3 weight you pay 0.39 pp of NAV less in expense - but you
  borrow 2 x NAV inside the fund instead of 1 x, so the extra financing is
  0.333 x DTB3 = **0.53 pp/yr over the window and 0.83 pp/yr since 2017** (DTB3 averaged
  1.58% and 2.48%). The 3x reset also decays faster for the same notional exposure
  ((L-1)/L = 0.667 against 0.5, i.e. +33%). The expense saving only wins while rates are
  near zero, which is exactly why the full-window result is a tie and 2017+ is not.
- **After Turkish tax the ranking flips in one window and the bootstrap disagrees.**
  At $100k the 2010 window favours T (+0.41 pp, 15.04% against 14.63%) because it
  realises a little less tax (7.18 pp of CAGR against QLD's 7.57 pp), and the 2017
  window still favours QLD (-0.48 pp). The clean after-tax bootstraps split: B2 (flat
  20%) favours T strongly (median +0.74 pp, 96.5% of paths) while B1-recent
  (progressive) is a coin flip (median +0.06 pp, 54.4% of paths). The pre-tax evidence
  is decisive against T; the after-tax evidence does not.
- **The research model of QLD\* is slightly conservative, not optimistic.**
  On the same sessions the real QLD compounds at 33.09%/yr against the base-financed
  synthetic's 33.65% (-0.55 pp) and the calibrated synthetic's 32.65% (**+0.44 pp**).
  TQQQ: 42.87% against 44.53% base (-1.65 pp) and 42.38% calibrated (+0.49 pp). Both real
  funds sit between the two financing models, closer to base for the full history and
  better than calibrated - so the +0.75 pp calibrated spread is mildly conservative.
- **Fund-level drag is the dominant cost and it is rate-driven.** QLD lags a costless
  2x reset by 3.85 pp/yr, TQQQ lags a costless 3x reset by 7.28 pp/yr; the per-year
  drag tracks the T-bill level (2023: -14.6 pp for QLD, -39.7 pp for TQQQ).

Evidence: `research/instrument_cost_v1/` (`PREREGISTRATION.md`, `screen.py`,
`download_data.py`, `DATA_MANIFEST.json`, `DOWNLOAD_PROVENANCE.json`, `RESULTS.json`,
`SUMMARY.txt`) and the downloaded inputs
`data/historical/instrument_cost_v1_{qqq,qld,tqqq}.csv`.

- `PREREGISTRATION.md`, `screen.py`, `DATA_MANIFEST.json` and the data were committed in
  `238e6b66`, before any non-control arm was simulated on real data. `--harness` at that
  commit simulates only the control.
- The outputs are `RESULTS.json` and `SUMMARY.txt` from `screen.py --run`. The run is
  offline and deterministic; two runs gave byte-identical `RESULTS.json`.
- Five changes to `screen.py` were made after the pre-registration commit and before the
  first completed `--run`; all are listed in the pre-registration's Amendments. Three are
  crash/report-only fixes, one is a report-only addition, and one (item 5) records that a
  pre-registered *justification* turned out to be wrong.

## Question

Production R2 (`equity_leveraged_trend_v1` 1.2.0 = E2 + early re-entry) holds QLD
(ProShares Ultra QQQ, 2x daily reset, ~0.95% expense). Every earlier simulation of it,
including the numbers used to ship R2, ran on a *synthetic* QLD\* built from QQQ
(daily-reset 2x total return, 0.95%/yr expense plus DTB3 financing). With **real fund
prices**, is there a cheaper way to hold the same exposure? Two ways were considered:
TQQQ (3x, ~0.84%) at 2/3 weight with 1/3 in cash, and any other 2x Nasdaq-100 daily ETF
with at least three years of history.

## Candidate sweep

Tickers were queried on the Yahoo chart API (`/v8/finance/chart/<sym>`, existence and
`meta.firstTradeDate`) and via `/v1/finance/search`:

| Ticker | Name | Inception | Usable? |
|---|---|---|---|
| QQQ | Invesco QQQ Trust | 1999-03-10 | 1x; signal source and benchmark only |
| QLD | ProShares Ultra QQQ | 2006-06-21 | **yes** - 2x Nasdaq-100, production instrument = control |
| TQQQ | ProShares UltraPro QQQ | 2010-02-11 | 3x, not 2x - candidate arm T at 2/3 weight |
| QLDX | - | does not exist (HTTP 404) | no |
| QQQU | Direxion Daily Magnificent 7 Bull 2X | 2024-03-07 | no - Mag-7 index, 2.4 y |
| QQUP | ProShares Ultra QQQ Mega | 2025-06-12 | no - QQQ Mega index, 1.3 y |
| EQQQ | ProShares Ultra QQQ Equal Weight | 2026-07-23 | no - equal-weight index, new |
| QQXL | ProShares Ultra QQQ Top 30 | 2025-08-15 | no - top-30 index, 1.2 y |
| QQQX | Nuveen Nasdaq 100 Dynamic Overwrite | 2007-02-01 | no - closed-end covered-call fund |
| RYVYX | Rydex NASDAQ-100 2x Strategy H | 2000-05-24 | no - mutual fund, no intraday/open price, ~1.6% ER |
| QQU.TO | BetaPro NASDAQ-100 2x Daily Bull | 2008-12-01 | no - TSX, CAD-denominated |
| 00670L.TW | Fubon NASDAQ-100 2X Leveraged | 2017-02-14 | no - TWSE, TWD-denominated |

No second USD-listed 2x Nasdaq-100 daily ETF with >= 3 years of history exists, so TQQQ
is the only candidate. This is a **15.6-year, single-market, single-history** test; the
pre-2000 out-of-sample windows used by every other study are not usable here.

## Method

- **Control (Q).** Production R2 on **real QLD**. Signals on the QQQ price close
  (E2 = 2 of 3: SMA200, 252-session price return > 0, 126-session Donchian midpoint;
  R2 = while E2 is off, half weight if close > SMA50 and 20-session close vol < its
  trailing 252-session median and close > close 20 sessions ago; exit below SMA50).
  Size `vt = min(1, 0.35/vol20)` on QLD's total-return close, total weight capped at
  0.98. Decide at the close, fill at the next open, 0.5 pp resize band, cost 4 bps x m
  (m = 2 gated, m = 1 reported). Idle cash earns zero.
- **Arms.** Q (control); **T** = the same targets x 2/3 held in real TQQQ; Q\* (report
  only) = the research synthetic QLD\* on the same real QQQ sessions; T_alt (report
  only) = TQQQ sized from TQQQ's own vol20 with no 2/3 factor.
- **T's vol convention (pre-registered).** `target_T = (2/3) x min(0.98, vt_QLD)`. It
  holds the notional exposure constant (3 x 2/3 = 2) and the risk estimate identical to
  the control, so the measured difference is the instrument's path and drag. The
  realized ratio `mean(wants_T/wants_Q)` is exactly 0.6667.
- **Data.** Yahoo chart API v8, 2010-01-04 -> 2026-10-14, analysed on the joint
  QQQ/QLD/TQQQ calendar 2010-02-11 -> **2026-09-29** (4,183 sessions). Dividends are
  reconstructed from `adjclose` so each fund's close-to-close total return is exact, and
  all arms run on the fund's total-return level (dividends reinvested at the ex-date
  open). The downloaded QQQ/QLD closes agree with the committed
  `stress_robustness_v1_daily.csv` to <= 5e-7.
- **Measured window.** Trading starts at the first session with production's 253-bar
  minimum history: **2011-02-10 -> 2026-09-29** (15.60 years, 3,930 sessions). The
  pre-registration's "2010 window" is this window.
- **After tax.** The corrected multi-instrument Turkish engine of
  `research/vt_level_tax_v1` (progressive 2026 tariff, Yi-UFE indexation, USD/TRY CBRT
  buying, $100k, liquidated at the window end) at m = 2, and the clean after-tax
  bootstraps B1-recent (progressive) / B2 (flat 20%) on a fixed real
  2015-09-01 -> 2026-09-16 calendar (2,776 sessions) with the real fund returns
  resampled jointly by date.
- **Harness.** `--harness` reproduces, on the synthetic control, `fast_reentry_v1`'s
  published C0/R2 rows (base and calibrated, m = 1 and m = 2) to **4.4e-5 pp** and
  `r2_tbill_combo_v1`'s published after-tax anchors to **4.9e-5 pp**, and checks the
  band simulator against `trend_robustness_v2.simulate_multi` both on the synthetic and
  on the real QLD series (bit for bit).
- **Gates (all must pass, all against Q).** GT1 return: CAGR(T) >= CAGR(Q) in the 2010
  and 2017 windows. GT2: max DD <= Q + 1 pp in both. GT3: block-20 bootstrap (5,000
  ten-year paths, seed 20261011, real paired returns) median Δ >= 0. GT4: after-tax
  CAGR(T) > CAGR(Q) in both windows. GT5: B1-recent and B2 median Δ >= 0 with share
  > 0 >= 55%. GT6: turnover <= 2 x Q.
- **Selection.** T iff all six pass; otherwise none, and production's QLD stands.

## Results (measured window, real funds, m = 2)

| Arm | CAGR | max DD | Calmar | turnover/yr | trades/yr | 2010 win CAGR / DD | 2017 win CAGR / DD | boot20 median | boot20 Δ vs Q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|
| **Q (real QLD, production)** | **22.20%** | **37.88%** | **0.586** | **5.84** | **60.1** | 22.20% / 37.88% | 32.07% / 31.14% | **23.86%** | — |
| **T (TQQQ at 2/3)** | **22.23%** | **34.76%** | **0.640** | **4.47** | **114.6** | 22.23% / 34.76% | 31.30% / 31.04% | 23.48% | **-0.354 pp** |
| Q\* (synthetic QLD\*, report only) | 22.45% | 38.09% | 0.589 | 5.84 | 60.1 | 22.45% / 38.09% | 32.53% / 31.01% | — | — |
| T_alt (TQQQ, vt on TQQQ, report only) | 25.60% | 43.76% | 0.585 | 6.99 | 123.3 | 25.60% / 43.76% | 37.78% / 32.89% | 26.89% | +3.022 pp |

At m = 1: Q 22.49%, T 22.45%, Q\* 22.74%, T_alt 25.95% (max DD 37.41 / 34.48 / 37.63 /
43.37%). Turnover is identical at m = 1 and m = 2.

Bootstrap detail (block-20, 5,000 ten-year paths, real paired returns, m = 2): T's Δ
against Q has median -0.354 pp, mean -0.404 pp, p05 -1.137 pp, p25 -0.638 pp,
p75 -0.125 pp, p95 +0.158 pp, and T beats Q on **13.5%** of paths. Median bootstrap
CAGR: Q 23.86%, T 23.48%. Median bootstrap turnover: Q 8.10, T 6.14.

Year by year the two arms are nearly identical (2020: Q 52.8% / T 54.3%; 2023: Q 60.6% /
T 58.0%); the whole difference is a slow bleed that is larger when rates are positive.

### What the funds cost (report only, full 2010-02-11 -> 2026-09-29 window)

| Fund | k | real CAGR | k x QQQ daily-reset CAGR | drag pp/yr | vs base synthetic | vs calibrated synthetic |
|---|---:|---:|---:|---:|---:|---:|
| QLD | 2 | 33.09% | 36.94% | **-3.85** | 33.65% (-0.55) | 32.65% (**+0.44**) |
| TQQQ | 3 | 42.87% | 50.15% | **-7.28** | 44.53% (-1.65) | 42.38% (**+0.49**) |

The `k x QQQ` benchmark is a zero-cost daily reset, so the drag is expense + financing
+ tracking + volatility decay. It moves with the T-bill rate: QLD's per-year drag is
about -1.2 to -1.9 pp in 2011-2016 (DTB3 <= 0.5%), -2.9 to -6.3 pp in 2017-2022, and
-5.4 to -14.6 pp in 2023-2026. TQQQ's is roughly 1.9x QLD's in every year (-39.7 pp in
2023). The real funds beat the *calibrated* model by ~0.44-0.49 pp/yr and lag the base
model by 0.55-1.65 pp/yr, i.e. reality sits between the two financing assumptions.

3-year average distribution yield: QQQ 0.57%, QLD 0.23%, **TQQQ 1.09%** (per year).

### Liquidity and spread (report only)

| Fund | avg $ volume/day | median $ volume/day | Corwin-Schultz mean (median, share > 0) | one-tick floor at last close | mean daily range |
|---|---:|---:|---|---:|---:|
| QLD | $242.6M | $204.1M | 56 bps (0, 48.0%) | 1.05 bps | 2.79% |
| TQQQ | $2,019.8M | $708.5M | 85 bps (0, 48.2%) | 1.29 bps | 4.20% |
| QQQ | $10,404M | — | 29 bps | 0.14 bps | 1.42% |

Both funds are far more liquid than the strategy needs: production's largest resizes are
a few percent of a portfolio, against ~$0.2-2bn of daily turnover. The Corwin-Schultz
estimator is not usable here - its median is 0 and only ~48% of days give a positive
value - so the honest spread statement is the tick floor: 1.05 bps for QLD and 1.29 bps
for TQQQ at their last closes. TQQQ is the more liquid of the two by dollar volume.
Both are dwarfed by the 8 bps round-trip cost the model already charges.

## After tax ($100k, progressive 2026 tariff, liquidated, m = 2)

| Window | Arm | pre-tax CAGR -> after-tax CAGR | max DD | trades/yr | tax paid | terminal USD |
|---|---|---:|---:|---:|---:|---:|
| 2011-02-10 -> 2026-09-29 | Q | 22.20% -> **14.63%** | 43.53% | 60.8 | 458,084 | 845,120 |
| 2011-02-10 -> 2026-09-29 | T | 22.23% -> **15.04%** | 39.37% | 115.0 | 464,933 | 893,939 |
| 2017-01-03 -> 2026-09-29 | Q | 32.06% -> **22.53%** | 36.21% | 70.3 | 347,988 | 724,367 |
| 2017-01-03 -> 2026-09-29 | T | 31.29% -> **22.04%** | 36.94% | 119.6 | 322,684 | 696,912 |

The tax wedge is 7.18-7.57 pp of CAGR over the full window and 9.25-9.54 pp since 2017.
T's wedge is 0.29-0.39 pp smaller, which is the whole of its after-tax win in the 2010
window (+0.41 pp) and not enough to overturn its pre-tax loss since 2017 (-0.48 pp).
Note the trade counts: T makes about **twice as many trades** (115/yr against 61/yr)
because the absolute 0.5 pp band is applied to a smaller weight - but its total traded
notional is 23% lower (4.47 against 5.84 turnover/yr), which is what the tax sees.

### Clean after-tax bootstrap (block-20, 1,000 paths, fixed 2015-09-01 -> 2026-09-16 calendar)

| Model | Q median CAGR | T median CAGR | median Δ | mean Δ | share Δ > 0 | p05 / p95 | median Δ max DD |
|---|---:|---:|---:|---:|---:|---|---:|
| B1-recent (progressive) | 13.59% | 13.66% | **+0.061 pp** | +0.046 pp | **54.4%** | -0.85 / +0.88 | -0.78 pp |
| B2 (flat 20%) | 14.05% | 14.89% | **+0.736 pp** | +0.738 pp | **96.5%** | +0.09 / +1.44 | -1.49 pp |

The two tax models disagree, and neither can rescue T: under the progressive regime the
median is +0.06 pp with 54.4% of paths winning (the gate needs 55%), and under the flat
regime the median is +0.74 pp with 96.5%. The realized-history after-tax ranking
(+0.41 pp in 2010, -0.48 pp in 2017) is between them.

## Gates

| Gate | T |
|---|---|
| GT1 return, 2010 window | **pass** (22.23% vs 22.20%) |
| GT2 drawdown, 2010 window | pass (34.76% vs 37.88%) |
| GT1 return, 2017 window | **fail** (31.30% vs 32.07%) |
| GT2 drawdown, 2017 window | pass (31.04% vs 31.14%) |
| GT3 real block-20 bootstrap | **fail** (median -0.354 pp, 13.5% of paths) |
| GT4 after tax, 2010 window | pass (15.04% vs 14.63%) |
| GT4 after tax, 2017 window | **fail** (22.04% vs 22.53%) |
| GT5 B1-recent median | pass (+0.061 pp) |
| GT5 B1-recent share > 0 | **fail** (54.4% < 55%) |
| GT5 B2 median | pass (+0.736 pp) |
| GT5 B2 share > 0 | pass (96.5%) |
| GT6 turnover <= 2 x Q | pass (4.47 vs 5.84) |
| **Result** | **fail** |

Selected arm: **none**. QLD (production) stands.

## Findings

- **There is no cheaper 2x Nasdaq-100 ETF, and TQQQ at 2/3 weight is a worse way to
  build one.** The expense-ratio saving (0.39 pp of NAV at 2/3 weight) is smaller than
  the extra financing the 3x fund pays (0.333 x DTB3 = 0.53 pp/yr on average, 0.83 pp/yr
  since 2017) plus the extra reset decay. The pre-tax comparison is a tie over a window
  that contains six zero-rate years and a clear loss over a window that does not.
- **Drawdown is the one axis where T wins.** Max DD 34.76% against 37.88% pre-tax and
  39.37% against 43.53% after tax. Both arms' worst episode is the same one
  (peak 2015-07-20, trough 2016-07-06, recovered 2017-05-25 for Q against 2017-05-01 for
  T). Holding a 3x fund at 2/3 weight is not the same as a 2x fund - the path through the
  2015-2016 grind happened to be shallower - but this is one episode in one history.
- **The finite-history test is rate-dependent and therefore not a verdict on TQQQ's
  design.** Since 2022 T-bills have paid 3-5%, exactly the regime where the 3x financing
  disadvantage is largest; the bootstrap median of -0.35 pp blends both regimes. If rates
  returned to zero, T would be roughly a wash with a slightly better drawdown.
- **Turnover and trade counts move in opposite directions.** T trades twice as often
  because drift-to-band scales with (1 - w) and T's weight (0.65) is far from the 0.98
  cap that pins Q in calm markets, while its traded notional is 23% lower. So T is
  better for tax per unit of turnover and worse per unit of trade count - the tax models
  disagree on which effect dominates, which is why B1 and B2 split.
- **The simulation is not biased against the strategy.** On the same real sessions the
  research synthetic QLD\* gives 22.45% against the real QLD's 22.20% over the measured
  window: -0.25 pp/yr, i.e. the assumed financing model is mildly optimistic there, while
  at fund level the real QLD is 0.55 pp/yr *behind* the base model and 0.44 pp/yr *ahead*
  of the calibrated one. The strategy is out of the market ~14% of the time, which
  dampens the fund-level drag.
- **The pre-registered justification for T's vol convention was partly wrong.** Deriving
  the weight from TQQQ's own vol20 does *not* land at 2/3 of the QLD weight: production's
  `CAP = 0.98` clamps the QLD arm in calm windows while the TQQQ arm is never clamped, so
  `mean(vt_TQQQ)/mean(vt_QLD) = 0.82`. The report-only T_alt arm therefore runs ~22% more
  notional exposure and its +3.4 pp CAGR and +5.9 pp drawdown are a leverage effect, not a
  vol-estimation effect. The candidate arm T is unaffected (realized ratio exactly 2/3).

## Caveats

- **Single history, one market, in-sample selection.** One 15.6-year Nasdaq-100 path, no
  out-of-sample window, and the candidate was chosen before the data was seen only in the
  weak sense that TQQQ is the only alternative that exists. Treat the sign as the result.
- **No financing model for the real funds.** Real fund returns already contain their own
  financing, so this study has no base/calibrated pair; m = 2 is gated and m = 1 is
  reported. The `Q*` row is the bridge to the published synthetic numbers.
- **Dividends are treated as reinvested capital gains.** The downloaded funds distribute
  0.23% (QLD) and 1.09% (TQQQ) per year on average; the engine reinvests them at the
  ex-date open and taxes them as capital gain rather than withholding tax on dividends,
  which understates T's tax by up to ~0.1 pp/yr relative to Q. This is a report-only
  note: it works *against* the selected arm's rival, and T still fails.
- **The after-tax model** carries the same assumptions and open items as
  `after_tax_tr_v1` / `tax_asym_v2` / `vt_level_tax_v1`: Yi-UFE indexation from the
  acquisition month, the 2026 tariff, the 22,000 TRY dividend threshold, liquidation at
  the window end, and a USD/TRY and Yi-UFE calendar that only exists from 2000. It is not
  tax advice. 1,000 bootstrap paths per tax model; no path had a non-positive terminal
  liquidated value.
- **Spread estimates are weak.** Only the tick floor and dollar volume are trustworthy
  here; the Corwin-Schultz estimator floors at zero on ~52% of days for both funds.

## What a production change would have required

None is proposed. Switching the held instrument to TQQQ at 2/3 weight would have needed
a strategy parameter for the weight scale, a new instrument in the universe and a Stage B
replay; the evidence does not support it.

## Decision

Production keeps QLD. No code, configuration or runtime changed.
