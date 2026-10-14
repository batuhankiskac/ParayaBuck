# Early QQQ v1 (2026-10-14) — deliver the early-re-entry leg in the unlevered fund (idea I5)

**REJECT — the swap is a small but consistent loss, not a saving, and it can be a witness for the
whole idea: even with the execution cost set to zero the arm is a tie at best.** The survey's
+0.2 … +0.5 pp/yr estimate is an annualisation error of 10–20×; the correct arithmetic bound is
**+0.024 pp/yr**, and the realised result is **−0.14 pp/yr** pre-tax because the swap converts a
free internal rebalance into a two-sided instrument switch at every early-leg entry.

- **A1 (`min(0.98, w)` QQQ in the early leg) fails seven of the pre-tax gates and all three tax
  gates.** Base m=2 CAGR 15.34 % against R2's 15.48 %, max DD 47.20 % against 46.76 %, a lose in all
  three subperiods (G4), a block-20 bootstrap median of **−0.200 pp with only 0.4 % of 5,000 paths
  ahead of R2**, six OOS windows all on the wrong side (median −0.120 pp), and after tax
  **−0.105 / −0.049 / −0.011 pp** on 2000 / 2010 / 2017 with B1-recent median −0.261 pp (1.4 % of
  paths ahead) and B2 −0.273 pp (0.8 %).
- **The reason is one line of arithmetic.** Every early-leg entry that happens on the same close as
  E2 turning off is a *direct switch* from the QLD book to the QQQ book, charged on both sides
  (`4 bp x m x (sold + bought)`). It happens **31 times in 26.6 years** (1.17/yr, 39 % of the 80
  entries) and costs `1.65 x 0.08 % = 0.133 pp/yr` of NAV. The gross benefit of the swap is
  `w_t x ((DTB3 + 0.75 % + 0.95 %)/2 - 0.20 %)/252` per early session = **+0.024 pp/yr** on the real
  path (164 early sessions of 6,680, mean `w` 0.562, mean DTB3 2.13 %). The switch cost is 5.6× the
  benefit; the remaining ≈ 0.03 pp is the reset-convexity/churn term. **Setting the switch cost to
  zero would make A1 a tie with R2 (15.47 %), not a winner.**
- **The tax side agrees and does not rescue it.** The early leg is entered and exited on the same
  sessions, so the *pattern* of realisations is unchanged — but the notional is ~2× (mean 0.56
  against R2's 0.28), so every resize and every entry/exit realises twice the stock. Tax paid over
  2000–2026 is $745k against R2's $766k (slightly *less*, because the arm compounds a smaller
  gross), which shaves the loss to −0.105 pp after tax. The survey's "tax-neutral" claim is right
  about the pattern and wrong about the size.
- **The 21-ETF drawdown test passes, and that is the strongest evidence *against* the idea.** The
  same construction on 21 quality-passing ETFs (EWC excluded, 5.62 % bad bars) never worsens max DD
  by more than 1.25 pp (EWJ) and 21/21 are inside +3 pp — but ΔCAGR is **negative in 21 of 21**
  (median −0.147 pp; SPY −0.06, XLY −0.05, XLK −0.13, DIA −0.09). The loss is systematic, not a
  Nasdaq-100 quirk.
- **The real-fund surface is the only near-neutral reading** (QQQ/QLD adjusted closes,
  2011-01-03 → 2026-09-21, 15.7 y): 22.13 % against 22.16 % CAGR, ΔDD 0.00 pp. Real QLD costs
  ~0.55 pp/yr more than the synthetic QLD\* (`instrument_cost_v1`), so on real prices the swap buys
  back most of the switch cost — and still loses.
- **The survey's arithmetic, repaired.** The survey multiplied the while-on saving of 2.0–2.8 pp/yr
  by the early path's session share (2.45 %) but never annualised it over the window — the correct
  bound is `Σ_sessions 1_early x w_t x rate / 252` annualised over 26.6 years = +0.024 pp/yr. I5's
  expected value was therefore never in the +0.2 … +0.5 pp/yr band.

Evidence: `research/early_qqq_v1/` (`PREREGISTRATION.md`, `screen.py`, `tax_engine.py`,
`DATA_MANIFEST.json`, `RESULTS.json`, `SUMMARY.txt`) and the downloaded inputs
`data/historical/early_qqq_v1_{qqq,qld}.csv`.

- `PREREGISTRATION.md`, `screen.py`, `tax_engine.py` and `DATA_MANIFEST.json` were committed in
  `f27e3218` (amended from `5f959bab` for the input line endings), **before any non-control arm was
  simulated on real data**; `--harness` simulated only the control and the E2-only reference.
- Outputs are `RESULTS.json` and `SUMMARY.txt` from `screen.py --run`, offline and deterministic;
  two runs gave byte-identical `RESULTS.json`.
- Post-commit `screen.py` changes are listed in the pre-registration's Amendments (one report-only
  NaN fix, one crash fix in the gate assembly, one requirement that `--harness` simulate the control
  only, one line-ending fix). No gate, arm, parameter or window changed after any arm had been run.

## Question

Production R2 (`equity_leveraged_trend_v1` 1.2.0) delivers the `0.5 w` early-re-entry leg in QLD
(2× Nasdaq-100 daily reset, 0.95 %/yr): half own capital, half embedded borrowing at DTB3 + spread,
for a beta of about `w = min(1, 0.35 / vol20)`. Holding `min(0.98, w)` of the **unlevered** fund of
the same index (QQQ, 0.20 %/yr) delivers the same daily beta with no embedded borrow, and this
account's idle cash earns 0 %. Idea I5 claims that substitution is a free saving, tax-neutral, and
worth +0.2 … +0.5 pp/yr after tax at $100k. Does it beat R2 after Turkish tax without worse drawdown?

## Arms (fixed in the pre-registration)

`QLD*` = daily-reset 2× QQQ total return − 0.95 %/yr − (DTB3 + spread); `QQQ*` = 1× QQQ − 0.20 %/yr;
`SPY*` = 1× SPY − 0.0945 %/yr; `SSO*` = 2× SPY − 0.89 %/yr − (DTB3 + spread). Signals, the early path
(SMA50, vol20 < 252-session median, close > close 20 sessions ago, exit below SMA50, E2 override),
sizing (`w_t = min(1, 0.35/vol20)` on the QLD\* vol), the 0.98 total cap, the 0.5 pp resize band on
the total, close decision / next-open fill and 4 bp × m per side are production R2 in every arm.

| arm | E2 leg | early leg | role |
|---|---|---|---|
| C0 | `min(0.98, w_t)` QLD\* | `0.5 w_t` QLD\* | control = production R2 |
| A1 | `min(0.98, w_t)` QLD\* | `min(0.98, w_t)` QQQ\* | **the idea** (candidate) |
| A2 | `min(0.98, w_t)` QLD\* | `min(0.98, w_t)` SPY\* | equal-beta other index (candidate) |
| A3 | `min(0.98, w_t)` QLD\* | `min(0.98, w_t)` SSO\* | survey A3 literal; 2× beta, sensitivity only |

Resolutions taken in the pre-registration, before any run: the survey's A2 (`0.98 QQQ + (w-0.98)/2
QLD`) cannot be implemented without margin (its total reaches 0.99, above the 0.98 cap); its
cap-respecting version is worth ≤ 0.02 of beta on 14 of 164 early sessions and is reported as an
analytic bound (`analytic_bounds.cap_bound`, ≤ 4e-5 pp/yr). The survey's A3 (SSO) is not an
equal-beta replacement, so the equal-beta reading was added as A2 and SSO kept as a non-candidate.
The survey's A4 became a report-only real-fund surface rather than an arm.

## Control reproduction (`--harness`)

| check | reference | reproduced |
|---|---|---|
| E2 base/calibrated m=2 CAGR, max DD, turnover (8 checks) | 14.6749 / 14.1043 %, 49.5643 / 49.6498 %, 5.9689/yr | within 0.01 pp |
| R2 base/calibrated × m=1/m=2 CAGR, max DD, turnover (12) | 15.4766 / 14.8961 %, 46.7584 / 46.8797 %, 5.9915/yr | within 0.01 pp |
| multi-instrument simulator vs `trend_robustness_v2.simulate_multi` (single leg) | bit-identical | true |
| after-tax $100k anchors, E2 and R2, 3 windows × (pre-tax, progressive, DD) (18) | 9.534 / 14.497 / 22.693 % and **10.16 / 14.706 / 22.886 %** | max 0.00005 pp |

## Main window (2000-03-09 → 2026-09-29, 6,679 sessions), m = 2

| arm | CAGR base | CAGR cal | max DD base | max DD cal | 2017+ | P1 | P2 | P3 | turnover/yr | switches/yr |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C0 | 15.48 % | 14.90 % | 46.76 % | 46.88 % | 32.71 % | −0.06 % | 14.30 % | 32.71 % | 5.99 | 0.00 |
| A1 | 15.34 % | 14.76 % | 47.20 % | 47.29 % | 32.70 % | −0.31 % | 14.16 % | 32.70 % | 7.66 | 1.17 |
| A2 | 14.99 % | 14.42 % | 49.13 % | 49.21 % | 32.34 % | −0.64 % | 13.82 % | 32.34 % | 7.66 | 1.17 |
| A3 | 15.39 % | 14.80 % | 48.46 % | 48.61 % | 32.21 % | −0.02 % | 14.49 % | 32.21 % | 7.67 | 1.17 |

Block-20 bootstrap (5,000 paths, m = 2), base: A1 median **−0.200 pp**, share of paths ahead of C0
**0.4 %** (mean −0.207, p5 −7.60 % against C0's −7.36 %); A2 −0.506 pp / 33.4 %; A3 −0.250 pp /
41.5 %. Calibrated: A1 −0.174 pp / 0.9 %.

### Where the −0.14 pp goes

| term | pp/yr of NAV |
|---|---:|
| gross financing + expense saved by the swap (analytic, duty-cycled) | **+0.024** |
| instrument switches: 1.652 of traded notional/yr × 8 bp | **−0.133** |
| entries/exits at ~2× the early-leg notional (4.234 against 3.837 /yr × 8 bp) | −0.032 |
| fewer resizes (1.776 against 2.154 /yr × 8 bp) | +0.030 |
| reset convexity and compounding (residual, incl. 2× daily reset vs 1× during the trending episodes) | ≈ −0.03 |

The switch term is not an artefact of an unrealistic execution assumption: the two orders are in
different instruments at their own next-open prices, so both sides pay a spread. Zeroing it entirely
gives A1 ≈ 15.47 % against R2's 15.48 %, i.e. a tie.

Event counts (base, m = 2, whole window) are otherwise the same shape: 80 entries, 79 exits, 1,456
resizes (C0: 1,486), plus A1's 31 switches; time in market 76.8 % in both.

## Out-of-sample and bootstrap

Six OOS windows, calibrated, m = 2: A1's ΔCAGR = −0.54 pp (^GSPC 1957–1999), −0.005 (^IXIC
1975–1999), −2.09 (^GSPC 1934–1956), −1.50 (^N225), −0.89 (^GDAXI), −1.54 pp (^FTSE); median
−0.120 pp, all six inside −0.5 pp and all six inside +3 pp of DD, worst ΔDD +1.40 pp. Pooled OOS
bootstrap median −0.114 pp (A2 −0.108, A3 +0.050). G7 fails only on the median-≥-0 condition; G8
fails on the pooled median. Equities 1934–1956 and the German/Japanese/French history are the worst
cells, i.e. the swap is worst exactly where trend-following's early re-entry does the most work.

## After Turkish tax at $100k (liquidated, base financing, m = 2)

| window | R2 (C0) pre / after / DD | A1 pre / after / DD | A2 | A3 | Δ after tax (A1) |
|---|---|---|---|---:|---:|
| 2000-03 → 2026-09 | 15.47 / **10.16** / 46.76 % | 15.33 / **10.06** / 47.20 % | 9.80 | 10.10 | −0.105 pp |
| 2010-01 → 2026-09 | 22.16 / **14.71** / 43.78 % | 22.10 / **14.66** / 44.03 % | 14.43 | 14.39 | −0.049 pp |
| 2017-01 → 2026-09 | 32.53 / **22.89** / 36.24 % | 32.52 / **22.88** / 36.31 % | 22.59 | 22.47 | −0.011 pp |

Clean after-tax bootstraps on the fixed 2015-09 → 2026-09 TRY calendar (1,000 paths):
B1-recent (progressive) A1 median **−0.261 pp**, 1.4 % of paths ahead; B2 (flat 20 %) median
**−0.273 pp**, 0.8 % ahead. A2 and A3 are worse on the median but have fat right tails (share > 0
30 % / 37 %) because they carry different beta. Tax paid over 2000–2026: $745k (A1) against $766k
(R2); indexation relief 18.1 m against 18.6 m TRY.

## 21-ETF drawdown generalisation (E1)

Every `range_vol_v[12]_*` file through range_vol_v2's quality rule (≤ 5 % of bars with `high == low`
or an invalid open): EWC fails at 5.62 % and is excluded, **21/21 pass**, each with its own E2 and
early signals, its own 2× synthetic leg (0.95 %) and its own 1× self leg, base financing, m = 2,
window from its own session 253.

| ETF | Δ max DD | Δ CAGR | | ETF | Δ max DD | Δ CAGR |
|---|---:|---:|---|---|---:|---:|
| DIA | +0.42 pp | −0.09 pp | | SPY | +0.09 pp | −0.06 pp |
| EEM | +0.57 | −0.15 | | XLB | +0.13 | −0.17 |
| EFA | +0.51 | −0.15 | | XLE | +0.35 | −0.22 |
| EWA | +0.54 | −0.14 | | XLF | +0.36 | −0.21 |
| EWG | +0.14 | −0.17 | | XLI | +0.26 | −0.08 |
| EWH | +0.46 | −0.22 | | XLK | +0.28 | −0.13 |
| EWJ | +1.25 | −0.26 | | XLP | +0.77 | −0.14 |
| EWL | +0.33 | −0.19 | | XLU | +0.58 | −0.13 |
| EWS | +0.48 | −0.13 | | XLV | +0.28 | −0.12 |
| EWU | +0.91 | −0.17 | | XLY | −0.00 | −0.05 |
| IWM | +0.81 | −0.16 | | | | |

E1 passes (100 % inside +3 pp, median ΔDD +0.42 pp, worst +1.25 pp) and G9 passes on every ETF. But
ΔCAGR is negative in 21/21 (median −0.147 pp): the extra turnover created by the swap (11.3
against 8.8 per year) and the switches cost a little in every underlying, exactly as on Nasdaq-100.

## Real-fund surface (report-only, not an arm)

QQQ and QLD real adjusted closes (Yahoo chart API), 2011-01-03 → 2026-09-21, 3,952 sessions, real
expenses and real financing inside the prices, m = 2: R2 22.16 % CAGR / 40.21 % DD, A1 22.13 % /
40.21 %, i.e. ΔCAGR **−0.031 pp**, ΔDD 0.00 pp, 4 switches (0.25/yr) against 31 in the synthetic
run. The narrower loss is consistent with `instrument_cost_v1`: real QLD lags the synthetic QLD\*,
so on real prices the swap's gross benefit is larger and nearly cancels its switch cost.

## Gates

| gate | A1 | A2 | A3 |
|---|---|---|---|
| G1 base / calibrated | F / F | F / F | F / F |
| G2 base / calibrated | P / P | F / F | F / F |
| G3 base / calibrated | P / P | P / P | P / P |
| G4 (subperiods won) | F (0/3) | F (0/3) | F (0/3) |
| G5 base / calibrated (bootstrap median ≥ 0) | F / F | F / F | F / F |
| G6 base / calibrated (m = 1) | F / F | F / F | F / P |
| G7 (6 OOS windows) | F | F | F |
| G8 (pooled OOS bootstrap median) | F | F | P |
| G9 (turnover ≤ 2× R2) | P | P | P |
| T1 (beats R2 in all 3 tax windows) | F | F | F |
| T2 (B1-recent median ≥ 0 and share > 0 ≥ 55 %) | F | F | F |
| T3 (B2 flat 20 %) | F | F | F |
| E1 (21-ETF ΔDD ≤ +3 pp in ≥ 80 %) | P | n/a | n/a |
| **all** | **FAIL** | **FAIL** | **FAIL** |

Selection rule → **no candidate adopted; production keeps R2 unchanged.**

## Limits and caveats

- The after-tax engine folds distributions into the price (no dividend cash flow, no 20 % US
  withholding, no 22,000 TL annual dividend threshold), for every arm. That is mildly favourable to
  the QQQ/SPY legs (0.55 % yield against QLD's ~0.23 %) by ≲ 0.003 pp/yr once duty-cycled, i.e. two
  orders below the effect being tested; the direction is *against* the verdict, so the rejection
  does not depend on it.
- The pre-tax engine's financing model (base = DTB3 exactly, calibrated = DTB3 + 0.75 %) brackets
  the real QLD drag (`instrument_cost_v1`); both models are reported and both lose.
- The after-tax stage has no true out-of-sample: the TRY/Yİ-ÜFE calendar starts in 2000 and both
  clean bootstraps hold it fixed (B1-recent 2015–2026, B2 flat). No arm can be validated in a TRY
  regime that has not happened.
- Not tax advice. The tax model is `after_tax_tr_v1`'s / `vt_level_tax_v1`'s (FIFO lots, Yİ-ÜFE
  indexation, 2026 tariff deflated by the previous October, within-year netting only, instalments
  31-03/31-07) and requires an adviser before any implementation.

## Untested adjacent idea (arithmetic only, not simulated)

The same deleveraging can be applied to the **E2 leg** whenever `2 w_t ≤ 0.98` (vol20 ≥ 71.4 %),
where the unlevered fund could carry the whole beta within the cap. Per session that saves
`w_t x (DTB3 + 0.55 %)/252`; the state is a high-volatility one (2008–09, 2020) whose session share
is a few percent, and this study did not simulate it. It is *not* I5 and it is not proposed here.

## Reproduce

```
PYTHONPATH=src /Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/early_qqq_v1/screen.py --harness
PYTHONPATH=src /Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/early_qqq_v1/screen.py --run
```
