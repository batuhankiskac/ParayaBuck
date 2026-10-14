# Year-end Turkish tax-timing overlays on production R2 (tax_harvest_v1, 2026-10-14)

> Evidence for this report (`research/tax_harvest_v1/` code, inputs, results) is not on
> `main`; it lives on branch `research/tax_harvest_v1` and is reachable from this worktree.

**Verdict: H2 (defer the December vol-resize fills into January) passes every pre-registered
gate; H1/H1b (year-end loss harvest) and H3 (H1+H2) fail decisively; H2b passes and is
statistically indistinguishable from H2. H2 is selected — but it is the smallest pass of any
campaign in this series (+0.04 pp pre-tax CAGR, +0.10 to +0.28 pp after tax, bootstrap median
+0.045 to +0.071 pp on 64–71 % of paths), so it is reported as a *candidate*, not shipped.**

- **H1/H1b fail on both legs.** The year-end harvest fires 7 (H1) / 6 (H1b) times in 26.5 years,
  and in the engine's TRY terms the book is almost always at a **gain**: only the 2007 event
  realized a net TRY loss (−4,758 TRY), the other six realized +4,720 to +5,504,662 TRY of
  taxable gain (TRY depreciation dominates any USD drawdown). It also costs 7 year-end overnight
  gaps, which are large: the 2011-12-30 night cost 3.43 % of NAV, the 2015-12-31 night *earned*
  3.58 % back — net −4.92 % cumulative, i.e. ≈ −0.19 pp/yr of pre-tax CAGR (the measured gap is
  −0.22 pp; the rest is the extra round-trip fees, turnover 6.23 vs 5.99). After tax, H1 loses
  **0.87 / 1.30 / 2.26 pp** at $100k and 0.88 / 1.33 / 2.33 pp at $1M, and the bootstrap median
  is −0.33 pp (progressive) / −0.18 pp (flat) with only 18–21 % of paths positive.
- **H2 is the whole story of the study and it is positive everywhere.** It beats R2 after tax in
  **all six** window x capital cells (+0.14 / +0.13 / +0.27 pp at $100k; +0.16 / +0.14 / +0.28 pp
  at $1M) and in all four bootstrap cells (median +0.055 / +0.045 pp at $100k and +0.071 /
  +0.045 pp at $1M; 64.4–69.7 % of paths positive). Pre-tax it is marginally *better* than R2
  (15.51 % vs 15.47 % base, 14.93 % vs 14.89 % calibrated) with 0.50 pp less drawdown and 3 %
  fewer sells.
- **The mechanism is deferral, and it can pay more nominal tax and still win.** H2 pays
  9,460 USD *more* tax over the 2000–2026 window at $100k (775,620 vs 766,160) yet ends with
  45,912 USD more wealth: 51 (pre-tax) / 53 (with the tax-funding sales present) December fills
  move into January, so the gain is assessed one year
  later (instalments Mar/Jul of the following year) and the position is held through year end
  instead of being trimmed into it. H1 is the mirror image: it pays 3.06M TRY *less* nominal tax
  and still loses 0.87 pp of CAGR, because it realizes earlier and eats the year-end gaps.
- **The tax drag falls for H2 and rises for H1.** 2000–2026 at $100k: R2 15.47 → 10.16 (drag
  5.31 pp), H2 15.51 → 10.30 (drag 5.21 pp), H1 15.25 → 9.29 (drag 5.96 pp).
- **Not tax advice.** The model has no wash-sale-style rule (so the H1 round trip is legal in the
  model), uses trade-date tax years (H1b is the settlement-date-robust twin), FIFO lots, and the
  frozen 2026 tariff; the assumptions are listed under *Method* and *Caveats*.

## Method

- **Control (R2).** Production `equity_leveraged_trend_v1` 1.2.0: at least 2 of 3 votes on the
  QQQ price close (close > SMA200; 252-session price return > 0; close > the 126-session max/min
  midpoint), sized `vt = min(1, 0.35 / vol20)` on synthetic QLD\* with a 0.98 cap; while the vote
  is off, 0.5 x vt if QQQ close > SMA50, QQQ vol20 < its trailing 252-session median and close >
  close 20 sessions ago, exited below SMA50.
- **Arms (5 candidates, all sharing R2's target series bit for bit; only the execution layer
  differs).** H1, H1b, H2, H2b, H3 — definitions frozen in the pre-registration:
  - **H1**: at the close of the last December session, if the position is non-empty, **any** FIFO
    lot has an unrealized loss under the engine's indexed-TRY formula, **and** the calendar year's
    net realized TRY gain so far is > 0, sell 100 % at that close and re-enter at the strategy's
    target at the next open.
  - **H1b**: identical at the close of the **second-to-last** December session (re-entry at the
    last session's open), so the sale is safe under a settlement-date reading.
  - **H2**: a downward vol resize (not an exit to zero — exits always fill) whose **fill** session
    is one of the last 10 December sessions is not executed; the ordinary rule applies from the
    first January session (if the target has recovered, no trade happens at all).
  - **H2b**: as H2, but only when the units that would be sold realize a **net gain** in engine
    TRY terms.
  - **H3** = H1 + H2.
- **Mechanics.** Decide at the completed close, fill at the next open; 0.5 pp resize band; entries
  and exits always in full; idle cash earns zero; cost 4 bps x m x |delta w|; synthetic QLD\* =
  daily-reset 2x on QQQ total return, 0.95 %/yr expense, financing DTB3 + {0, +0.75 %}/yr; main
  window 2000-03-09 → 2026-09-29 (6,679 sessions); m = 1/2, both financings pre-tax; base
  financing at m = 2 after tax.
- **After-tax model.** The corrected Turkish engine (`tax_engine.py`, byte copy of
  `r2_tbill_combo_v1/tax_engine.py` = the `vt_level_tax_v1` engine), liquidation at the window
  end, three windows x {$100k, $1M}, and the clean block-20 bootstraps (B1-recent progressive,
  B2 flat 20 %) at both capitals, 1,000 ten-year paths on the fixed 2015-09 → 2026-09 calendar
  with its realized USD/TRY and Yi-UFE.
- **Harness (all reproduced, `--harness` PASS, 0 checks outside tolerance).** R2 and C0 reproduce
  `fast_reentry_v1/RESULTS.json` and `e2_integration_v1`'s deviation table at m = 1/2, base and
  calibrated, to < 0.0001 pp (R2 base m = 2 unliquidated 15.4766 %, max DD 46.7584 %, turnover
  5.9915; C0 14.6749 % / 49.5643 %); overlay `none` reproduces the reference `simulate_band` NAV
  path to 1.3·10⁻¹³; the after-tax anchors of R2 and C0 at $100k reproduce within 0.0001 pp
  (R2 10.16 / 14.71 / 22.89 %; C0 9.53 / 14.50 / 22.69 %); the control's bootstrap medians
  reproduce `r2_tbill_combo_v1` exactly (B1 C0 3.079 / R2 3.5155 %; B2 3.5201 / 3.9678 %).
  `--smoke` additionally proves the H2b gain probe equals the engine's own sell arithmetic on 300
  random lot books.

## Results

### Pre-tax main window (m = 2; G1 = CAGR ≥ R2 − 0.10 pp, G2 = max DD ≤ R2 + 1 pp)

| Arm | CAGR base | CAGR cal. | max DD base | max DD cal. | m = 1 base | trades/yr | sells/yr | turnover/yr | harvests | deferrals |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **R2 (control)** | **15.473 %** | **14.893 %** | **46.758 %** | **46.880 %** | **15.752 %** | 61.94 | 29.56 | 5.99 | 0 | 0 |
| H1 | 15.254 % | 14.675 % | 46.758 % | 46.880 % | 15.554 % | 62.09 | 29.48 | 6.23 | 7 | 0 |
| H1b | 15.496 % | 14.915 % | 46.758 % | 46.880 % | 15.793 % | 62.05 | 29.56 | 6.19 | 6 | 0 |
| **H2** | **15.514 %** | **14.933 %** | **46.254 %** | **46.379 %** | **15.791 %** | 60.39 | 28.65 | 5.95 | 0 | 51 |
| H2b | 15.458 % | 14.878 % | 46.758 % | 46.880 % | 15.736 % | 60.69 | 28.80 | 5.97 | 0 | 38 |
| H3 | 15.317 % | 14.737 % | 46.254 % | 46.379 % | 15.616 % | 60.50 | 28.50 | 6.18 | 7 | 51 |

The reference (unliquidated) figures are 15.4766 % / 14.8961 % base and calibrated for R2 with
46.758 % / 46.880 %; all arms here are liquidated, so the whole table sits 0.003 pp below the
reference convention. G1 fails for H1 and H3 in **both** financings (H1 −0.22 pp base, −0.22 pp
calibrated; H3 −0.16 / −0.16). Every arm is inside G9 (2 x R2 turnover, 11.98/yr).

### After tax, progressive 2026 tariff, liquidated (base financing, m = 2)

At **$100k**:

| Window | Arm | Pre-tax | After-tax | Δ vs R2 | max DD | Tax paid (USD) | Drag | Terminal USD |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 2000-03 → 2026-09 | **R2** | 15.47 % | **10.16 %** | — | 46.76 % | 766,160 | 5.31 | 1,306,627 |
| | H1 | 15.25 % | 9.29 % | **−0.87** | 46.76 % | 740,801 | 5.96 | 1,058,558 |
| | H1b | 15.50 % | 9.45 % | **−0.71** | 46.76 % | 761,432 | 6.05 | 1,099,377 |
| | **H2** | 15.51 % | **10.30 %** | **+0.14** | 46.25 % | 775,620 | **5.21** | 1,352,539 |
| | H2b | 15.46 % | 10.26 % | **+0.10** | 46.76 % | 766,790 | 5.20 | 1,338,721 |
| | H3 | 15.32 % | 9.35 % | **−0.81** | 46.25 % | 750,386 | 5.97 | 1,073,297 |
| 2010-01 → end | **R2** | 22.16 % | **14.71 %** | — | 43.78 % | 553,536 | 7.45 | 994,825 |
| | H1 | 21.90 % | 13.40 % | **−1.30** | 43.06 % | 545,540 | 8.50 | 821,409 |
| | H1b | 22.18 % | 13.62 % | **−1.09** | 44.21 % | 556,498 | 8.56 | 847,711 |
| | **H2** | 22.13 % | **14.84 %** | **+0.13** | 44.07 % | 551,338 | 7.29 | 1,014,399 |
| | H2b | 22.13 % | 14.84 % | **+0.13** | 44.07 % | 551,242 | 7.29 | 1,014,247 |
| | H3 | 21.91 % | 13.42 % | **−1.28** | 42.83 % | 546,012 | 8.49 | 823,980 |
| 2017-01 → end | **R2** | 32.53 % | **22.89 %** | — | 36.24 % | 360,297 | 9.65 | 745,349 |
| | H1 | 32.05 % | 20.62 % | **−2.26** | 42.91 % | 357,844 | 11.43 | 621,842 |
| | H1b | 32.51 % | 21.13 % | **−1.76** | 42.12 % | 368,160 | 11.38 | 647,800 |
| | **H2** | 32.57 % | **23.16 %** | **+0.28** | 34.86 % | 360,065 | **9.41** | 761,754 |
| | H2b | 32.57 % | 23.16 % | +0.28 | 34.86 % | 360,065 | 9.41 | 761,754 |
| | H3 | 32.09 % | 20.67 % | **−2.21** | 42.68 % | 358,626 | 11.41 | 624,335 |

At **$1M** the picture is identical in sign and slightly larger for H2: Δ vs R2
**+0.16 / +0.14 / +0.28 pp** (H2) and +0.11 / +0.14 / +0.28 (H2b); H1 −0.88 / −1.33 / −2.33,
H1b −0.75 / −1.15 / −1.81, H3 −0.82 / −1.31 / −2.28. R2's own after-tax CAGR at $1M is
9.19 / 13.62 / 21.72 %, i.e. 0.97 / 1.09 / 1.17 pp below the $100k column (progressive brackets).

### Clean after-tax bootstrap (block-20, 1,000 paths, fixed 2015-09 → 2026-09 calendar)

| Capital | Model | Arm | median CAGR | median Δ vs R2 | mean Δ | share > 0 | p05 / p95 | harvests/10 yr |
|---|---|---|---:|---:|---:|---:|---|---:|
| $100k | B1-recent | R2 | 3.52 % | — | — | — | — | 0.0 |
| | | H1 | 3.22 % | **−0.328 pp** | −0.418 | 17.9 % | −1.38 / +0.22 | 3.0 |
| | | H1b | 3.28 % | **−0.274 pp** | −0.364 | 18.9 % | −1.27 / +0.27 | 3.0 |
| | | **H2** | **3.60 %** | **+0.055 pp** | +0.103 | **67.4 %** | −0.31 / +0.61 | 0.0 |
| | | H2b | 3.61 % | **+0.055 pp** | +0.108 | **68.0 %** | −0.27 / +0.61 | 0.0 |
| | | H3 | 3.37 % | **−0.231 pp** | −0.296 | 29.5 % | −1.32 / +0.51 | 2.0 |
| $100k | B2 flat | R2 | 3.97 % | — | — | — | — | 0.0 |
| | | H1 | 3.67 % | **−0.177 pp** | −0.224 | 19.7 % | −0.81 / +0.17 | 3.0 |
| | | H1b | 3.78 % | −0.159 pp | −0.202 | 21.2 % | −0.76 / +0.18 | 3.0 |
| | | **H2** | 3.97 % | **+0.045 pp** | +0.069 | **64.4 %** | −0.37 / +0.53 | 0.0 |
| | | H2b | 3.97 % | +0.043 pp | +0.070 | **67.3 %** | −0.32 / +0.51 | 0.0 |
| | | H3 | 3.82 % | −0.122 pp | −0.151 | 33.9 % | −0.86 / +0.43 | 2.0 |
| $1M | B1-recent | R2 | 2.35 % | — | — | — | — | 0.0 |
| | | H1 | 1.86 % | **−0.369 pp** | −0.459 | 16.3 % | −1.50 / +0.25 | 3.0 |
| | | H1b | 1.97 % | −0.296 pp | −0.401 | 18.9 % | −1.38 / +0.28 | 3.0 |
| | | **H2** | **2.46 %** | **+0.071 pp** | +0.124 | **69.7 %** | −0.28 / +0.65 | 0.0 |
| | | H2b | 2.48 % | +0.071 pp | +0.129 | **70.8 %** | −0.25 / +0.65 | 0.0 |
| | | H3 | 2.04 % | −0.242 pp | −0.314 | 29.2 % | −1.38 / +0.52 | 2.0 |
| $1M | B2 flat | R2 | 3.97 % | — | — | — | — | 0.0 |
| | | H1 | 3.67 % | −0.177 pp | −0.224 | 19.7 % | −0.81 / +0.17 | 3.0 |
| | | H1b | 3.78 % | −0.159 pp | −0.202 | 21.2 % | −0.76 / +0.18 | 3.0 |
| | | **H2** | 3.97 % | **+0.045 pp** | +0.069 | **64.4 %** | −0.37 / +0.53 | 0.0 |
| | | H2b | 3.97 % | +0.043 pp | +0.070 | **67.3 %** | −0.32 / +0.51 | 0.0 |
| | | H3 | 3.82 % | −0.122 pp | −0.151 | 33.9 % | −0.86 / +0.43 | 2.0 |

(The two flat rows are identical because the flat tax is scale-free; the progressive rows are
not, which is why $1M cells are reported separately. Bootstrap max-DD medians: R2 57.7 % / 56.1 %,
H2 57.7 % / 56.3 %, H1 58.3 % / 56.3 % in B1/B2 — no tail blow-up from either overlay.)

### Gates

| Arm | G1 (both) | G2 (both) | G3 window cells (6) | G4 bootstrap cells (4) | Result |
|---|---|---|---|---|---|
| H1 | **fail** (−0.22 / −0.22 pp) | pass | **0/6** | **0/4** | **FAIL** |
| H1b | pass (+0.02 / +0.02) | pass | **0/6** | **0/4** | **FAIL** |
| **H2** | pass (**+0.04 / +0.04**) | pass (−0.50 pp DD) | **6/6** | **4/4** | **PASS** |
| H2b | pass (+0.02 / +0.02) | pass | **6/6** | **4/4** | **PASS** |
| H3 | **fail** (−0.16 / −0.16) | pass | **0/6** | **0/4** | **FAIL** |

Selection rule: the highest B1-recent $100k median delta passes; H2 and H2b tie there
(+0.055 pp each, H2 67.4 % vs H2b 68.0 % of paths), so the pre-registered tie-break
(2000–2026 $100k after-tax delta) gives **H2** (+0.143 vs +0.101 pp). `selected = H2`.

### H1's harvest log ($100k, 2000-03 → 2026-09): the failure mechanism

| Session | Realized gain (TRY) | Year gain before → after (TRY) |
|---|---:|---|
| 2003-12-31 | +4,720 | +20,641 → +25,361 |
| 2007-12-31 | **−4,758** | +26,737 → +21,979 |
| 2011-12-30 | +2,045 | +37,953 → +39,998 |
| 2015-12-31 | +24,589 | +156,150 → +180,739 |
| 2020-12-31 | +374,115 | +1,981,745 → +2,355,861 |
| 2021-12-31 | +2,201,454 | +1,817,263 → +4,018,717 |
| 2025-12-31 | +5,504,662 | +8,697,294 → +14,201,956 |

Six of seven harvests *increase* the taxable base, and the 2021 one more than doubles it, because
"any lot at a TRY loss" is satisfied by a single reverse-FIFO survivor while the rest of the book
is deeply up in TRY. The overlay is not a loss harvest in this tax base; it is an early
realization. Its 7 forced year-end round trips cost −4.92 % of NAV cumulatively (nights
−1.28 %, −0.23 %, −3.43 %, +3.58 %, −0.90 %, −0.49 %, −1.82 %), i.e. ≈ −0.19 pp/yr, and between
harvests the two NAVs differ by a constant factor (the round trip closes back to the strategy's
target, so there is no residual drift).

### Tax per calendar year and the deferred-vs-saved split ($100k, 2000-03 → 2026-09, progressive)

Δ = arm − R2 on the year's assessed tax, TRY. Negative = tax taken out of that year, positive =
tax added to it. Per-year for the two arms that matter (TRY thousands; the terminal year lumps the
liquidation and any unsettled instalment, and the last two rows are aggregates):

| Arm | 2021 | 2023 | 2024 | 2025 | 2026 | Σ pushed later | Σ pulled earlier | **net total Δ** |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| H2 | −140,228 | +46,622 | −158,436 | +327,530 | +528,044 | 300,812 | 922,537 | **+621,725** |
| H1 | +789,802 | −224,074 | −947,016 | +1,508,542 | −4,303,853 | 5,498,051 | 2,433,551 | **−3,064,499** |
| H1b | — | — | — | — | — | 5,040,783 | 2,859,294 | −2,181,490 |
| H2b | −147,368 | — | — | — | — | 366,180 | 730,026 | +363,846 |
| H3 | — | — | — | — | — | 5,600,256 | 2,830,271 | −2,769,985 |

Interpretation: **H2's entire edge is deferral plus fewer realizations.** It pays 621,725 TRY
(+2.7 %) *more* nominal tax over the window than R2 — the extra weight held through year end earns
more, and indexation bases shift — but the assessment moves a year later and 3 % of sells
disappear, so its drag on CAGR is 0.10 pp smaller (5.21 vs 5.31 pp at $100k; 9.41 vs 9.65 pp in
the 2017 window). **H1 is the opposite**: it pays 3.06M TRY less nominal tax (the harvest resets
the FIFO book and destroys accumulated indexation) while pushing 2.43M TRY of tax *earlier* into
the harvest years and taking −4.92 % of pre-tax NAV from the year-end gaps. No part of either
result is a "saved" tax in the permanent sense beyond the bracket/indexation interaction; the
honest label is *timing*, and only H2's timing is favourable.

## Findings

- **H1/H1b are dead, and for a reason that generalises beyond R2.** In a TRY tax base with
  double-digit annual depreciation, a foreign-equity lot is rarely at a TRY loss; harvesting
  needs *one* such lot (by the pre-registered trigger) while the rest of the book is up, so the
  overlay accelerates the tax rather than deferring it. Realizing in December to "use" losses
  that mostly do not exist, and paying the year-end gap (+/-3.5 % nights on a 2x position), is a
  losing combination on this history and, on the evidence of the bootstrap (median −0.33 pp,
  18 % of paths positive), not a coin flip that happened to land badly.
- **The settlement/trade-date question is not decisive.** H1b (2 sessions earlier, so safe under
  T+1) performs the same as H1 to within 0.2 pp, and both fail. The engine uses trade date; the
  conclusion does not depend on it.
- **H2's pre-tax identity is not neutral, and that matters for reading G1.** Selling nothing in
  the last 10 December sessions means holding the December weight through the year end, which is
  worth +0.04 pp of CAGR and −0.50 pp of max DD by itself; the tax component of the after-tax
  delta is the remaining ~0.10 pp of drag reduction. The arm is therefore not a pure tax overlay,
  and its G1/G2 pass is partly a small "trade less at year end" effect.
- **H2's edge is small but unanimous.** 6/6 window x capital cells, 4/4 bootstrap cells, median
  +0.045 to +0.071 pp, 64–71 % of paths. It is one order of magnitude smaller than R2's own
  pre-tax edge (+0.68 pp) and a third of R2's after-tax edge (+0.20 to +0.63 pp), and it is
  larger at $1M than at $100k only in the progressive cells.
- **H2b is not separable from H2.** Deferring only gain-realizing resizes (38 vs 51 fills) gives
  +0.043 to +0.071 pp on 67–71 % of paths, i.e. within the noise of H2 and with *more* paths on
  the positive side at $1M. The pre-registered selection picks H2 on a 0.042 pp tie-break, so the
  choice between them should not be read as evidence that either is better.

## Caveats

- **One history.** The after-tax window result is one 26.5-year path; the bootstrap is the
  counterweight and it agrees in sign everywhere for H2, but the median is +0.05 pp.
- **The tax model is not tax advice** and is the corrected `vt_level_tax_v1` engine: trade-date
  tax years (no settlement), per-calendar-year netting with **no loss carry-forward** in the
  progressive regime (B2 carries losses forward), Yi-UFE indexation of the month preceding the
  acquisition when the index rose >= 10 %, FIFO, 2026 tariff scaled by the previous October's
  index, 22,000 TRY dividend threshold, instalments Mar 31/Jul 31 funded by selling, one CBRT
  buying-rate series, liquidation at the window end. **Wash-sale-style rules do not exist in the
  model** — the H1 round trip is treated as a clean realization and re-purchase; under real rules
  with a wash-sale restriction the overlay would need a longer gap or a different instrument and
  the result would be worse, not better. No BIST offsets, no other income, no FX cost on the tax
  payment.
- **The fixed bootstrap calendar** (2015-09 → 2026-09) cannot explore FX or inflation regimes
  outside that period, so B1/B2 cannot price a different TRY-depreciation regime.
- **Implementation detail.** "Sell all and re-enter at the target" was pre-registered as written;
  the measured consequence is that the round trip also resets the weight to target. The NAV ratio
  between harvests is constant, so this reset has no measurable residual drift on this history —
  but it would on a path where the harvested weight differs materially from the target.
- **No 2x-cost stress and no OOS windows** were pre-registered for this campaign (the pre-tax
  gates were deliberately loose because the overlays barely touch exposure); H2's cost is one
  fewer trade per year, so a cost stress would help it, but that is not evidence.

## What a production change would require

Not done here. H2 would live in the rebalance layer of `equity_leveraged_trend_v1` (skip
downward fills in the last 10 December sessions), i.e. a strategy-version bump, a new golden
backtest, a Stage B replay on the production engine (which would also have to reproduce the
realized FX calendar), and a new config/live-authorization digest. Given the +0.05 pp bootstrap
median, the owner's call on whether that is worth the churn; nothing was changed.

## Decision

`selected = H2` on the pre-registered rule (all gates pass). **No code, configuration or runtime
was changed by this campaign**; production R2 stays `equity_leveraged_trend_v1` 1.2.0, and the
finding is recorded for a future consideration.

## Reproduction

```
cd /Users/batuhankiskac/Desktop/ParayaBuck-wt/tax_harvest_v1
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/tax_harvest_v1/screen.py --smoke    # synthetic only
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/tax_harvest_v1/screen.py --harness  # control only
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/tax_harvest_v1/screen.py --run      # ~9-11 min, writes RESULTS.json/SUMMARY.txt
```

`PREREGISTRATION.md`, `screen.py`, `tax_engine.py` and `DATA_MANIFEST.json` were committed in
`2eaade23`, **before any non-control arm was simulated on real data**; only `--smoke` (synthetic
random walks, no market data) had run at that point. Two consecutive `--run` invocations produced
byte-identical `RESULTS.json` (SHA-256
`e4affff9ef434cfda1d9308ad1675eb6e783de84caf10a71805b494ff8bcc4a5`) and `SUMMARY.txt`
(`fac1023a172399a367874175f3a13da6dfa468f24c559dbba47608e66c6fbcaa`); each run takes ~10 min.

## Amendments

1. **Reporting only (after the pre-registration commit, before the first `--run`):** the
   per-year tax block was relabelled from "deferred/saved" to the explicit pair
   "tax pushed later / tax pulled earlier" plus "net total Δ", and the runner gained a
   `deferrals_by_year` counter. No arm, parameter, window, gate, selection rule or mechanic
   changed; the gated numbers in `RESULTS.json` are unchanged by it.
