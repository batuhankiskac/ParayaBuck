# TSM total-return inputs v1 (2026-10-14)

> Evidence for this report (`research/tsm_tr_v1/` code, data inputs and results) is not on
> `main`; it is preserved in tag `archive/research/tsm-tr-v1-2026-10`.

> Report-only per-year table: `research/tsm_tr_v1/yearly.py` (not gated).
> Pre-registration: `research/tsm_tr_v1/PREREGISTRATION.md`.

**Verdict: FAIL — none.** No production-feasible TSM/DON variant clears the pre-registered gates
on top of R2. Keep production R2 as it is: TSM on the QQQ **price** return with a zero hurdle and
DON on the **price** close.

- **P1 (TSM on QQQ total return > 0, DON on price)** and **P2 (P1 + DON on total return)** are the
  only arms with a positive pre-tax edge: **+0.13 pp** and **+0.24 pp** base CAGR (m = 2), with
  **identical** max DD (46.76 %), sharper Calmar/Sharpe, and every pre-tax gate passed **except
  G4** — the whole gain is one episode in 2016 (+5.57 pp; P2 also +2.60 pp in 2015), offset by
  2005 (−1.45 pp), 2008 (−0.43 pp), 2019 (−1.27 pp) and, for P2, 2023 (−1.35 pp).
- **The after-tax gate kills them, as the pre-registration assumed it would.** At $100 k they beat
  R2 in the 2000 and 2010 windows (+0.12 / +0.30 pp for P1, +0.21 / +0.44 pp for P2) and **lose
  the 2017 window** (−0.09 / −0.18 pp). The clean after-tax bootstraps give a median Δ of
  **+0.000 pp (P1)** and **+0.012 pp (P2)** with share > 0 of **43.0 %** and **51.3 %**, against a
  pre-registered bar of 55 %.
- **P3 (TSM total return > a flat 2 %/yr)** is worse than production on 11 of the 14 gates
  (−0.56 pp base CAGR) → FAIL.
- **P4 (research E2: TSM total return vs cumulative DTB3, DON on total return)** is the best arm
  before tax (+0.29 pp) and the only one passing G4 and G7, but fails G8 (pooled OOS bootstrap
  median −0.015 pp) and fails after tax the same way (−0.72 pp in the 2017 window). It is
  report-only: it needs a rate series production does not have.
- **The study cannot resolve these variants out of sample, and that is a result, not an excuse.**
  The six OOS windows are price-only, so P1/P2 are *bit-identical* to R2 there (confirmed: the
  entire `oos` block for P1 and P2 equals P0 exactly, as pre-registered). And because the arms
  change only 0.21–0.48 % of decision sessions, a 10-year resampled path usually contains none of
  them: the main and after-tax bootstrap medians are therefore **exactly 0.000 pp** for P1, P3 and
  P4 (mean +0.014 pp, p05 −0.49 / p95 +0.53). G5 passes trivially; the 55 %-of-paths bar is what
  the after-tax gate actually tests.

## Question

Production R2 (`equity_leveraged_trend_v1` 1.2.0) reads the TSM vote from the QQQ split-adjusted
**price** close against a **zero** hurdle and DON from the price close, because production had
neither a dividend nor a rate series. Alpaca's corporate-actions API does supply QQQ cash
dividends, so production *could* build a **total-return** QQQ series; a rate series is still
missing, but a fixed hurdle is possible. `e2_integration_v1` measured the production
simplification at −0.15 pp CAGR against research E2 and warned that the hurdle choice alone moves
the result by ±0.7 pp. This campaign re-measures the substitution **inside R2** (E2 *plus* the
early re-entry path, which reads the same vote) and, decisively, **after Turkish income tax**,
where R2 itself was selected on 2026-10-14.

## Method

**Control P0 = production R2 exactly.** E2's 2-of-3 vote on the QQQ price close (close > SMA200;
252-session price return > 0; close > midpoint of the 126-session max/min), sized
`vt = min(1, 0.35/vol20)` on synthetic QLD\* (daily-reset 2x on QQQ total return, 0.95 %/yr
expense + DTB3, +0.75 %/yr calibrated), cap 0.98, decide at the close, fill at the next open,
0.5 pp resize band; plus production's early re-entry path at 0.5 × `vt` (active only while the
vote is off). `C0` = the same vote **without** the early path, used only to reproduce the
published E2 anchors.

**Arms** (every parameter fixed in the pre-registration; only the TSM/DON inputs differ; the SMA
vote is always on the price close):

| Arm | tsm series | hurdle | don series | Production-feasible |
|---|---|---|---|---|
| **P0** | price | 0 | price | control = production R2 |
| **P1** | total return | 0 | price | yes (Alpaca dividends) |
| **P2** | total return | 0 | total return | yes |
| **P3** | total return | flat 2 %/yr | price | yes (fixed constant) |
| **P4** | total return | cumulative DTB3 | total return | **no** (needs a rate series) |
| C0 | price | 0 | price | harness only, no early path |

**Data.** No network: byte copies of committed research data (SHA-256 in `DATA_MANIFEST.json`).
Main window **2000-03-09 → 2026-09-29** (6,679 sessions) from `stress_robustness_v1_daily.csv`
(QQQ open/close/cash dividend) + `stress_robustness_v1_dtb3.csv`; OOS from
`ewma_band_v1_{gspc,index,tb3ms}.csv` (^GSPC 1957–1999 and M1–M5, **price only**); after tax from
`after_tax_tr_v1_{usdtry_cbrt,yiufe}.csv`. The total-return level is
`cumprod(1 + (close[i] + dividend[i]) / close[i-1] − 1)` — a **signal** input only: every arm
trades the identical instrument.

**Harness (PASS; `--run` stops otherwise).** 72 reproduction checks, 0 outside tolerance:
C0 = `e2_integration_v1`'s production E2 (14.67 % / 14.10 %, DD 49.56 %) and P0 =
`fast_reentry_v1`'s R2 (base 15.48 % / calibrated 14.90 %, DD 46.76 %, m = 1 15.75 %) to 0.01 pp;
the E2-only variants P0/P2/P4 reproduce `E2_INTEGRATION_DEVIATION.json`'s `E2_prod_price_h0`,
`E2_tr_h0` and `E2_research`, and the flat-hurdle code path reproduces `E2_price_h2`, for
base/calibrated × m = 1/2 (CAGR, max DD, turnover) — exactly; the P0-spec vote equals the direct
production construction; the band simulator equals `trend_robustness_v2.simulate_multi` bit for
bit; the after-tax engine reproduces `vt_level_tax_v1`'s C0 anchors (≤ 0.00005 pp) and
`r2_tbill_combo_v1`'s R2 after-tax figures (10.16 / 14.71 / 22.89 %), and its NAV path equals the
vendored `tax_engine.run_strategy` exactly.

## Results (main window, m = 2)

| Arm | CAGR base | CAGR cal. | Max DD base | Calmar | Sharpe | 2017+ | sub-periods | Turnover/yr | m = 1 CAGR | Boot. med. Δ vs P0 | Δ vs P0 boot |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| C0 (E2, no early path) | 14.67 % | 14.10 % | 49.56 % | 0.296 | 0.654 | 32.44 % | −1.10 / 13.26 / 32.44 | 5.97 | 14.95 % | −0.683 pp | 31.1 % |
| **P0 (production R2)** | **15.48 %** | **14.90 %** | **46.76 %** | **0.331** | **0.678** | **32.71 %** | −0.06 / 14.30 / 32.71 | 5.99 | **15.75 %** | — | — |
| P1 (TR TSM) | 15.61 % | 15.03 % | 46.76 % | 0.334 | 0.682 | 32.59 % | −0.22 / 15.08 / 32.59 | 5.99 | 15.89 % | +0.000 pp | 64.3 % |
| P2 (TR TSM + TR DON) | **15.72 %** | 15.13 % | 46.76 % | **0.336** | **0.686** | 32.48 % | −0.22 / 15.54 / 32.48 | 5.91 | 15.99 % | +0.033 pp | 56.8 % |
| P3 (TR TSM, 2 % hurdle) | 14.92 % | 14.35 % | 46.76 % | 0.319 | 0.661 | 31.50 % | −0.21 / 13.95 / 31.50 | 6.31 | 15.21 % | −0.002 pp | 49.8 % |
| P4 (research E2, report only) | 15.77 % | 15.19 % | 46.76 % | 0.337 | 0.688 | 31.82 % | +0.47 / 15.54 / 31.82 | 6.11 | 16.05 % | +0.000 pp | 50.7 % |

Boot. med. Δ and share ≥ P0 are from the main block-20 bootstrap (5,000 paths, m = 2). Sub-period
CAGRs are →2008-12-31 / 2009–2016 / 2017→. Cost stress (report only), base CAGR at m = 1 / 2 / 4:
P0 15.75 / 15.48 / 14.92, P1 15.89 / 15.61 / 15.06, P2 15.99 / 15.72 / 15.17, P3 15.21 / 14.92 /
14.34, P4 16.05 / 15.77 / 15.21 — all five keep R2's m = 4 drawdown (47.44 %).

### Decision sessions that change vs production

| Arm | Vote sessions ≠ P0 | Effective (target) sessions ≠ P0 | Years | Blocks |
|---|---:|---:|---|---|
| P1 | 14 (0.21 %) | 11 (0.16 %) | 2005:1 2006:1 2008:1 2016:9 2019:2 | 9 single sessions in Mar/Jun/Jul 2016 plus 2005-04-07, 2006-09-12, 2008-08-27, 2019-01-18, 2019-01-31 |
| P2 | 20 (0.30 %) | 16 (0.24 %) | + 2011:2 2012:2 2015:1 2023:1 | same 2016 cluster, plus 2011-09-26, 2011-12-15, 2012-11-02, 2012-11-26, 2015-10-14, 2023-01-… |
| P3 | 19 (0.28 %) | 12 (0.18 %) | 2004:5 2006:1 2008:4 2011:2 2016:2 2019:5 | 2004-09-20, 2004-10-12→15 (4), 2008-06, 2008-08-19, 2011-12-16/21, 2016-03-11/16, 2019-02-04 |
| P4 | 32 (0.48 %) | 25 (0.37 %) | 2004:2 2006:5 2008:6 2011:2 2012:2 2015:1 2016:9 2019:4 2023:1 | widest: 2004-10-13/14, 2006-09-05/13/14/19/22, 2008-05 to 08, 2011-09-26, 2011-12-15, 2016-03-15→23, … |

**Why so few.** QQQ paid **no** dividend before 2003-12-24 (the 90 payments over the window total
27.58 index points, i.e. 0.6–1 %/yr in recent years). A total-return 252-session return is the
price return plus roughly the dividend yield, so the TSM vote can only flip while the
252-session price return sits inside (−yield, 0) — a narrow band crossed rarely. In the
2017–2026 window the only changed sessions are 2019 (and 2023 for P2).

### Per-year returns (base, m = 2; years with any difference)

| Year | P0 | P1 | P2 | P1 − P0 | P2 − P0 |
|---|---:|---:|---:|---:|---:|
| 2000–2004 | — | = | = | 0.000 | 0.000 |
| 2005 | −2.98 % | −4.43 % | −4.43 % | **−1.45** | **−1.45** |
| 2006 | 6.46 % | 7.17 % | 7.17 % | **+0.71** | **+0.71** |
| 2008 | −22.33 % | −22.76 % | −22.76 % | **−0.43** | **−0.43** |
| 2011 | −22.26 % | −22.26 % | −22.03 % | 0.000 | **+0.23** |
| 2015 | −12.35 % | −12.35 % | −9.75 % | 0.000 | **+2.60** |
| 2016 | −0.18 % | **+5.39 %** | **+5.39 %** | **+5.57** | **+5.57** |
| 2019 | 44.94 % | 43.67 % | 43.67 % | **−1.27** | **−1.27** |
| 2023 | 61.05 % | 61.05 % | 59.70 % | 0.000 | **−1.35** |
| others | — | = | = | 0.000 | 0.000 |

The entire pre-tax edge is the 2016 cluster (both arms) plus 2015 for P2 (DON on the total-return
level), against losses in 2005, 2008, 2019 and 2023. Every other year is unchanged to < 0.005 pp.

## Gates (all measured against P0; `ewma_band_v1`'s G1–G9)

| Gate | Rule | P1 | P2 | P3 | P4 (report only) |
|---|---|---|---|---|---|
| G1 return, base / calibrated | CAGR ≥ P0, or Calmar ≥ 1.1 × P0 and CAGR ≥ P0 − 0.5 pp | P / P | P / P | **F / F** | P / P |
| G2 DD ≤ P0 + 1 pp, base / cal. | m = 2 | P / P | P / P | P / P | P / P |
| G3 2017+ ≥ P0 − 1 pp | | P / P | P / P | **F / F** | P / P |
| G4 sub-periods | above P0 in ≥ 2 of 3 | **F** (1: 2009–16) | **F** (1: 2009–16) | **F** (0) | P (2) |
| G5 bootstrap median Δ ≥ 0 | base / calibrated | P / P | P / P | **F / F** | P / P |
| G6 G1 at m = 1 | | P / P | P / P | **F / F** | P / P |
| G7 six OOS windows | median Δ ≥ 0, ≥5/6 ≥ −0.5 pp, ≥5/6 DD ≤ +3 pp, none > +8 pp | **waived** (non-identifying) | **waived** | **F** | P |
| G8 pooled OOS bootstrap | median Δ ≥ 0 | **waived** | **waived** | **F** | **F** (−0.015 pp) |
| G9 turnover ≤ 2 × P0 | ≤ 11.98/yr | P (5.99) | P (5.91) | P (6.31) | P (6.11) |
| **Pre-tax result** | | **FAIL (G4)** | **FAIL (G4)** | **FAIL (11 gates)** | FAIL (G8) |
| After-tax: all three windows | beat P0 | **FAIL (2017)** | **FAIL (2017)** | **FAIL (all 3)** | **FAIL (2017)** |
| After-tax B1/B2 median ≥ 0 and share > 0 ≥ 55 % | | **FAIL (43.0 %)** | **FAIL (51.3 %)** | **FAIL (43.0 %)** | **FAIL (48.6 %)** |
| **Overall** | | **FAIL** | **FAIL** | **FAIL** | FAIL |

G7/G8 are **non-identifying and waived** for P1/P2 exactly as pre-registered: the six OOS windows
carry price-only series, total return *is* the price level there, and the arm's vote — hence the
whole NAV path — equals P0's bit for bit (verified in `RESULTS.json`: the `oos` block of P1 and P2
is identical to P0's in all six windows). No out-of-sample evidence on the total-return
substitution is available from this harness for those two arms.

## After tax ($100 k, base financing, m = 2, liquidated at the end, progressive 2026 tariff)

| Window | Arm | Pre-tax CAGR | After-tax CAGR | Max DD | Tax paid (USD) | Trades/yr | Δ vs P0 |
|---|---|---:|---:|---:|---:|---:|---:|
| 2000-03 → 2026-09 | P0 | 15.47 % | **10.16 %** | 46.76 % | 766,160 | 62.7 | — |
| | P1 | 15.61 % | **10.28 %** | 46.76 % | 788,274 | 62.7 | **+0.12** |
| | P2 | 15.71 % | **10.37 %** | 46.76 % | 806,125 | 62.8 | **+0.21** |
| | P3 | 14.92 % | 9.73 % | 46.76 % | 687,629 | 62.8 | −0.43 |
| | P4 | 15.77 % | 10.41 % | 46.76 % | 818,243 | 62.9 | +0.25 |
| 2010-01 → end | P0 | 22.16 % | **14.71 %** | 43.78 % | 553,536 | 62.2 | — |
| | P1 | 22.49 % | **15.00 %** | 42.22 % | 579,432 | 62.2 | **+0.30** |
| | P2 | 22.66 % | **15.14 %** | 40.61 % | 593,061 | 62.3 | **+0.44** |
| | P3 | 21.33 % | 14.05 % | 44.41 % | 499,063 | 62.1 | −0.65 |
| | P4 | 22.31 % | 14.84 % | 40.61 % | 565,928 | 62.2 | +0.13 |
| 2017-01 → end | P0 | 32.53 % | **22.89 %** | 36.24 % | 360,297 | 70.9 | — |
| | P1 | 32.41 % | **22.79 %** | 36.24 % | 357,010 | 71.3 | **−0.09** |
| | P2 | 32.30 % | **22.70 %** | 36.77 % | 354,409 | 71.3 | **−0.18** |
| | P3 | 31.32 % | 21.97 % | 36.18 % | 329,204 | 71.3 | −0.92 |
| | P4 | 31.64 % | 22.16 % | 36.74 % | 336,491 | 71.2 | −0.72 |

C0 (E2 without the early path) reproduces `vt_level_tax_v1`: 9.53 / 14.50 / 22.69 %.

| Δ vs P0 | B1-recent (progressive) | B2 (flat 20 %) |
|---|---|---|
| P1 median Δ / share > 0 | +0.000 pp / **43.0 %** | +0.000 pp / **43.1 %** |
| P2 median Δ / share > 0 | +0.012 pp / **51.3 %** | +0.012 pp / **51.3 %** |
| P3 median Δ / share > 0 | −0.000 pp / **43.0 %** | +0.000 pp / **43.5 %** |
| P4 median Δ / share > 0 | +0.000 pp / **48.6 %** | +0.000 pp / **48.5 %** |
| B1 (B2) median after-tax CAGR | P0 3.52 % (3.97 %), P1 3.51 % (4.00 %), P2 3.51 % (4.00 %), P3 3.44 % (3.84 %), P4 3.39 % (3.77 %) | |

Clean after-tax bootstrap: block-20 stationary bootstrap, 1,000 paths, seed 20261011, 10-year
paths on the fixed 2015-09-01 → 2026-09-30 TRY calendar with its own FX and Yi-UFE, $100 k,
liquidated at the end.

## Findings

1. **The production simplification is not worth undoing on R2's own evidence.** Rebuilding the TSM
   vote on a total-return series is worth **+0.13 pp (P1) / +0.24 pp (P2)** CAGR before tax and
   **−0.09 / −0.18 pp** in the 2017–2026 after-tax window. `e2_integration_v1`'s E2-level
   measurement (+0.15 / +0.25 pp before tax) is confirmed on R2, and its warning stands: the
   substitution is a fraction of the ±0.7 pp that the hurdle choice alone moves.
2. **The pre-tax edge is one episode.** 2016 (+5.57 pp for both P1 and P2, P2 plus +2.60 pp in
   2015) against 2005/2008/2019/2023. That is why G4 fails: the sub-period 2000–2008 is *worse*
   (−0.22 pp) and 2017→ is worse (−0.12 / −0.23 pp) for both arms.
3. **After tax the sign flips where it matters most.** P1 and P2 win the 2000 and 2010 windows
   (+0.12 / +0.30 and +0.21 / +0.44 pp) — the same ordering `tbill_sweep_v1` and
   `riskoff_after_tax_v1` found, i.e. extra ON-sessions compound — but lose 2017–2026, and the
   bootstrap has them behind or at parity on essentially every resampled path (43 % and 51 % above
   P0). The extra realisations (tax USD +22 k / +40 k over the 2000 window) are the mechanism.
4. **A fixed hurdle is not a substitute for the rate series.** P3 (TR TSM > 2 %/yr) is below
   production on 11 of 14 gates and −0.56 pp pre-tax; `e2_integration_v1`'s price-channel result
   (−1.08 pp at the E2 level) is reproduced in spirit. Any hurdle a production-feasible variant
   can use is a *constant*, and the constant is wrong in every regime it does not match.
5. **P4 (research E2) would be the best arm if production could run it** — +0.29 pp before tax,
   the only arm passing G4 and G7 — but it still fails the after-tax windows (2017: −0.72 pp) and
   G8. The rate series is not the missing piece: the total-return/DTB3 construction is not a
   reliable improvement here either.
6. **Statistical power is the binding constraint, and it is structural.** The substitution changes
   0.21–0.48 % of decision closes. The main and clean after-tax bootstrap medians are exactly
   0.000 pp for P1, P3 and P4 (mean +0.014 pp) because a resampled path usually contains none of
   the changed sessions: the gate that passed (G5 median ≥ 0) passed by degeneracy, and the gate
   that failed (share > 0 ≥ 55 %) is the informative one. A campaign of this shape cannot be
   rescued by more bootstrap paths; it needs more independent episodes, which 26 years of QQQ
   dividends do not provide.

## Caveats

- **Single history, in-sample differences.** The 2016 and 2019 clusters are single events; the
  gate outcomes rest on them. No OOS evidence exists for P1/P2 in this harness (price-only OOS
  data), which is exactly why the after-tax gate is decisive here.
- **The dividend series is the harness's existing one** (Yahoo-style split-adjusted cash
  dividends in `stress_robustness_v1_daily.csv`), already used to build synthetic QLD\*'s total
  return. Since all arms trade the identical instrument, a bias in the dividend series cancels
  between arms; only the signal level is affected. QQQ's distributions are 100 % ordinary
  dividends, so reinvesting them is the correct total-return convention.
- **The after-tax model is not tax advice** and keeps all of `vt_level_tax_v1`'s simplifications
  (single marginal investor, FIFO lots, indexation relief when Yi-UFE rose ≥ 10 %, instalments
  funded by selling the portfolio, frozen 2026 tariff, no BIST offsets).
- **The signal is evaluated at the close and filled at the next open**, as everywhere in this
  series; the early path's own inputs stay on the price close for every arm, by pre-registration.
- **G7/G8 are waived for P1/P2** (pre-registered, non-identifying). Their omission is not a pass.

## Amendments (post-pre-registration changes)

The pre-registration (`f263b693`, together with `screen.py` and `DATA_MANIFEST.json`) fixed the
control, the five arms, the gates, the after-tax conditions and the selection rule before any
non-control arm ran on real data. One implementation defect was found and fixed by the harness
*before* any arm result was produced, and one pre-registered report item was computed outside
`RESULTS.json`:

1. **`variant_up` initially ignored the `hurdle` element of the arm spec** and always used the
   DTB3 cumulative level, so P0 reproduced `E2_price_dtb3` instead of production E2 and every
   check failed. The harness (`--harness`, 72 checks) caught it: the fix derives the bill level
   from the spec (`bill_level(rates)` for `"dtb3"`, `flat_bills(T, rate)` otherwise). Arms,
   parameters, gates and the decision rule are unchanged; the harness went from 40 failures to 0
   outside 0.01 pp.
2. **The "per-year returns of the main window" report item** (pre-registered as reported but not
   gated) is produced by `research/tsm_tr_v1/yearly.py`, which imports `screen.py` and re-simulates
   the four main arms; it is not part of `RESULTS.json`.

## Reproduction

```
cd /Users/batuhankiskac/Desktop/ParayaBuck-wt/tsm_tr_v1
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/tsm_tr_v1/screen.py --smoke
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/tsm_tr_v1/screen.py --harness
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/tsm_tr_v1/screen.py --run      # ~13 min
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/tsm_tr_v1/yearly.py            # report only
```

Offline and deterministic: two consecutive `--run` invocations produce byte-identical
`RESULTS.json` (SHA-256 `6ffc9bff2c19d0053a16f0603f4419699ad150b199b0f72f9276ffd91e90e034`) and
`SUMMARY.txt` (SHA-256 `38d8d73f9034c04730aa614aafb65f77ca78eec8d43609480123bbc8273453ff`).

## Decision

**Do not change production.** Keep R2's price TSM with the zero hurdle and the price DON. The
production-feasible total-return variants (P1, P2) are within +0.13–0.24 pp pre-tax on one
in-sample episode and lose the 2017–2026 after-tax window; the fixed-hurdle variant (P3) is worse
on nearly every gate. Rebuilding a total-return signal path in production therefore buys nothing
and would add a corporate-actions dependency to the runtime.
