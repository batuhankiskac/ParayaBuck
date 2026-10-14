# Monthly deferral of downward vol resizes across the Yi-UFE month boundary (monthly_defer_v1, 2026-10-14)

**Verdict: FAIL as pre-registered. No arm passes every gate, none is selected.** All three
candidates clear the after-tax window gates at $100k, the six out-of-sample windows, the pooled OOS
bootstrap, the pre-registered 2x-turnover limit and (except A2) the ETF drawdown gate; **A3 fails
exactly one gate — G5, the pre-tax 10-year bootstrap (median Δ −0.048 pp, 41.9 % of 5,000 paths
positive)** — and that single failure is decisive. A1 fails G5 (−0.255 pp, 37.9 %) and the flat-20 %
after-tax bootstrap T3 (−0.024 pp, 48.8 %); A2 fails G2 (max DD +3.27 pp), G5 (−1.000 pp), the ETF
drawdown gate G10 (16/21 instruments, worst EWG +8.19 pp), T2 and T3.

Idea **I2** of `research/idea_survey_v2`: generalise `tax_harvest_v1/v2`'s H2 (which postponed a
downward vol resize filling in the last 10 December sessions, +0.14/+0.13/+0.27 pp after tax at $100k)
from the single December boundary to **all twelve** month boundaries, on the theory that the Yi-UFE
indexation numerator steps once per calendar month. The mechanism is real and visible — indexation
relief rises from 18.6 m to 22.4 m TRY and 30.7 → 19.2 sells/yr at $100k — but the overlay is not the
"pure accounting" move the survey hoped for: it holds an above-target weight for up to a month, so it
also buys or loses exposure. **A1 pays *more* nominal tax (826,311 vs 766,160 USD) and ends
wealthier**; the after-tax gain is a bigger (better-taxed) position, not a smaller tax bill. And the
pre-tax block bootstrap says the timing bet itself is worth about zero-to-negative on resampled
10-year paths in every arm.

The two long-window results that keep the idea alive for an owner decision, in the same spirit as
`tax_harvest_v2`'s H2, are (i) **A3: +0.23 / +0.12 / +0.17 pp after tax at $100k, +0.042 pp median
on the flat-20 % bootstrap (57.7 % of paths), +0.122 pp on the inflation-aware B1-recent bootstrap
(70.3 % of paths), 21/21 ETFs within +3 pp DD, max DD +0.56 pp, but G5 −0.048 pp**, and (ii) **A2's
large after-tax numbers (+1.47 / +1.54 / +3.30 pp) which are bought with +3.27 pp of max drawdown,
one ETF at +8.19 pp and an 85 % cut in sells — a different strategy, and it fails both bootstraps
and the drawdown gate.**

Not tax advice; the model caveats of `vt_level_tax_v1` / `tax_harvest_v2` apply verbatim.

Evidence: `research/monthly_defer_v1/` (`PREREGISTRATION.md`, `screen.py`, `tax_engine.py`,
`DATA_MANIFEST.json`, `RESULTS.json`, `SUMMARY.txt`). Pre-registration, screen and inputs were
committed in `693ebc0f`; `RESULTS.json` (SHA-256 `2a5a1aa69c25434d588802fefdb467ab4c88b610a817f3f3e0579a39bfec31a6`)
is byte-identical over two runs. All inputs are existing repository files (no download);
the pre-registration's Amendment A1 records exactly which executions preceded the commit.

## Question

GVK mük. 81 rescales a lot's TRY cost basis by `Yi-UFE(month before sale) / Yi-UFE(month before
purchase)` when the index rose ≥ 10 %. The numerator steps once per calendar month, so moving a
**downward** vol resize from the end of month `M` to month `M+1` (a) shrinks the indexed gain on
every lot that sale consumes and (b) in December also moves the realisation into the next tax year.
H2 measured the December case. I2 asks: (1) does the effect survive when it is scaled to twelve
boundaries per year, and (2) what does holding a stale (too high) weight through the month end cost
in drawdown and out of sample.

## Method

- **Control C0** = production `equity_leveraged_trend_v1` 1.2.0 (R2): ≥ 2 of 3 votes on the QQQ
  **price** close (close > SMA200; 252-session price return > 0; close > the 126-session max/min
  midpoint), sized `vt = min(1, 0.35/vol20)` on synthetic QLD\*, cap 0.98, 0.5 pp resize band; while
  the E2 vote is off, `0.5 × vt` if QQQ close > SMA50, QQQ vol20 < its trailing 252-session median
  and close > close 20 sessions ago, exited below SMA50.
- **4 arms (control + 3 candidates)**, all sharing C0's target series bit for bit; only the fill
  decision changes, and an overlay can only *suppress* a trade, never create one:
  - **A1**: a downward resize (`0 < target < held` at the fill's open) whose **fill** session is one
    of the last **10** sessions of its calendar month is suppressed — all 12 months.
  - **A2**: the same with **21** sessions (a month has 20–23 sessions, so the whole month).
  - **A3**: A1 + hard override — fill immediately when `held − target > 10 pp` or on the fill
    session that follows a close at which the E2 vote turned off.
- **Anchor H2\*** (not a candidate): `tax_harvest_v2`'s published arm (last 10 December sessions),
  re-run only to check this file's overlay machinery against an independent published result.
- **Suppression, not a forced fill.** The survey's A1 read literally ("postponed to the first
  session of M+1 … and filled at that open") would create sales; the pre-registration resolves it to
  H2's proven mechanism (suppress, re-evaluate the band at every later close), which coincides with
  the forced fill whenever the target is still below the held weight at the month's last close.
  The survey's A2 ("only when the deferred sale would net a positive indexed gain") was dropped to
  keep the arm count at four — 91 % of R2's realised gain comes from exactly these trims, so it would
  be near-identical to A1 while requiring the identity of the lot the sale would consume.
- **Mechanics.** Decide on the completed close, fill at the next open; 0.5 pp band; idle cash earns
  zero; cost `4 bps × m × |Δw|`; synthetic QLD\* = daily-reset 2x on QQQ total return, 0.95 %/yr
  expense, financing DTB3 + {base 0.0, calibrated +0.75 %}/yr. Main window 2000-03-09 → 2026-09-29
  (6,679 sessions). Sub-periods P1 ≤ 2008-12-31, P2 2009–2016, P3 2017 →.
- **After tax.** The corrected Turkish engine (byte copy of `tax_harvest_v2/tax_engine.py`):
  per-calendar-year netting in TRY, trade-date years, Yi-UFE indexation with the 10 % floor, 2026
  tariff scaled by the previous October's index, FIFO, instalments Mar 31 / Jul 31 funded by
  selling, liquidation at the window end; windows 2000-03-09 →, 2010-01-01 →, 2017-01-03 →;
  capitals $10k / $100k / $1M (gates at $100k); clean bootstraps B1-recent (progressive) and B2
  (flat 20 %) on the fixed real 2015-09 → 2026-09 calendar, block-20, seed 20261011, 1,000 paths.
- **Harness (must pass before `--run`; 0 checks outside tolerance).** C0/R2 pre-tax CAGR, max DD and
  turnover reproduce `fast_reentry_v1/RESULTS.json` and `e2_integration_v1`'s deviation table at
  m = 1/2 in both financings (tol 0.01 pp); R2's sub-period and 2017+ CAGRs reproduce
  `fast_reentry_v1` (tol 0.005 pp); the R2 and C0 after-tax anchors reproduce `tax_harvest_v2`'s
  $100k table (tol 0.005 pp); the 5,000-path pre-tax bootstrap reproduces `fast_reentry_v1`'s R2
  medians (cagr 0.078856, Δ vs E2 0.006826, share 0.6892, turnover 9.339146 — all exact) and the
  pooled OOS bootstrap reproduces R2 − E2 = 0.003596; the control's clean after-tax bootstrap
  reproduces `r2_tbill_combo_v1` (B1 3.5155 %, B2 3.9678 %); all six OOS windows reproduce
  `fast_reentry_v1`; `simulate_band` identity 1.3·10⁻¹³; and the **H2 anchor reproduces
  `tax_harvest_v2/RESULTS.json` to 1e-7 on CAGR and exactly on the deferral counts (51 pre-tax;
  53 / 32 / 19 in the three after-tax windows)**.

## Results

### Pre-tax main window (m = 2; `turn` and `sells` are per year)

| Arm | CAGR base | CAGR cal. | max DD base | max DD cal. | m = 1 base | Calmar | P1 / P2 / P3 | turn/yr | sells/yr | deferrals |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|
| **C0 (R2)** | **15.47 %** | **14.89 %** | **46.76 %** | 46.88 % | 15.75 % | 0.331 | −0.06 / 14.30 / 32.71 | 5.99 | 29.56 | 0 |
| A1 (10, all months) | 15.74 % | 15.15 % | 46.45 % | 46.58 % | 15.99 % | 0.339 | +0.49 / 14.77 / 32.43 | 5.47 | 18.26 | 594 |
| A2 (21, all months) | 16.26 % | 15.65 % | 50.03 % | 50.15 % | 16.48 % | 0.325 | −0.59 / 15.21 / 34.96 | 4.59 | 4.22 | 1,662 |
| A3 (10 + override) | 15.55 % | 14.97 % | 47.32 % | 47.44 % | 15.82 % | 0.329 | +0.13 / 14.47 / 32.57 | 5.70 | 19.54 | 498 |
| *H2\* (anchor)* | *15.51 %* | *14.93 %* | *46.25 %* | *46.38 %* | *15.79 %* | *0.335* | *+0.09 / 14.20 / 32.75* | *5.95* | *28.65* | *51* |

A1 improves the pre-tax CAGR by +0.27 pp **and** lowers max DD by 0.31 pp while cutting sells by
38 % — the most attractive pre-tax profile of the three. A2 buys +0.79 pp with +3.27 pp of drawdown
and an 86 % fall in sells (it suppresses almost every trim for a whole month). Time in market is
identical (76.76 %) in every arm, as it must be: only the execution layer changes.

### Clean bootstraps

| Arm | pre-tax Δ median (base / cal.) | share > 0 | B1-recent Δ median | share > 0 | B2 flat-20 % Δ median | share > 0 |
|---|---:|---:|---:|---:|---:|---:|
| A1 | **−0.255 / −0.261 pp** | 37.9 % / 37.7 % | +0.160 pp | 56.6 % | **−0.024 pp** | 48.8 % |
| A2 | **−1.000 / −1.034 pp** | 35.8 % / 35.1 % | −0.016 pp | 49.8 % | **−0.438 pp** | 43.4 % |
| A3 | **−0.048 / −0.050 pp** | 41.9 % / 41.5 % | +0.122 pp | 70.3 % | +0.042 pp | 57.7 % |

(pre-tax: 5,000 ten-year paths, seed 20261011, block 20, m = 2; B1/B2: 1,000 paths on the fixed real
2015-09 → 2026-09 calendar with its realised USD/TRY and Yi-UFE, $100k. At $1M, A1 +0.224 pp /
A3 +0.150 pp on B1; B2 is scale-free.)

The sign split is the whole story of this study: the overlay is worth **+** inside the recent Turkish
calendar (high PPI inflation, so the indexation step is a real credit) and **−** on resampled
US 2000–2026 paths where it is only a tracking error. B2 (flat 20 %, no inflation, losses carried
forward) separates A1 from A3: A1's B1 win does not survive it.

### After tax, progressive 2026 tariff, liquidated, $100k (gated)

| Window | Arm | pre-tax | after-tax | Δ vs R2 | max DD | tax USD | sells/yr | deferrals |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 2000-03 → 2026-09 | C0 | 15.47 % | **10.16 %** | — | 46.76 % | 766,160 | 30.69 | 0 |
| | A1 | 15.74 % | 10.67 % | **+0.51** | 46.45 % | 826,311 | 19.20 | 610 |
| | A2 | 16.26 % | 11.63 % | **+1.47** | 50.03 % | 966,169 | 4.97 | 1,874 |
| | A3 | 15.55 % | 10.39 % | **+0.23** | 47.32 % | 788,308 | 20.52 | 515 |
| | *H2\** | *15.51 %* | *10.30 %* | *+0.14* | *46.25 %* | *—* | *—* | *53* |
| 2010-01 → end | C0 | 22.16 % | **14.71 %** | — | 43.78 % | 553,536 | 31.35 | 0 |
| | A1 | 22.13 % | 14.98 % | **+0.28** | 43.50 % | 549,125 | 19.89 | 360 |
| | A2 | 23.12 % | 16.25 % | **+1.54** | 44.24 % | 620,406 | 5.32 | 1,221 |
| | A3 | 22.12 % | 14.82 % | **+0.12** | 43.60 % | 549,080 | 21.02 | 309 |
| 2017-01 → end | C0 | 32.53 % | **22.89 %** | — | 36.24 % | 360,297 | 35.60 | 0 |
| | A1 | 32.25 % | 23.15 % | **+0.26** | 34.88 % | 349,365 | 21.85 | 257 |
| | A2 | 34.75 % | 26.18 % | **+3.30** | 35.77 % | 427,592 | 4.31 | 772 |
| | A3 | 32.39 % | 23.06 % | **+0.17** | 34.98 % | 355,600 | 23.19 | 219 |
| | *H2\** | *32.57 %* | *23.16 %* | *+0.27* | *34.86 %* | *—* | *—* | *19* |

At $10k the deltas are A1 +0.41/+0.18/+0.08, A2 +1.19/+1.29/+2.77, A3 +0.16/+0.05/−0.01; at $1M
A1 +0.60/+0.31/+0.31, A2 +1.74/+1.63/+3.58, A3 +0.27/+0.15/+0.24. Terminal wealth at $100k over the
2000 window: C0 1,306,627 → A1 1,475,678, A2 1,857,745, A3 1,379,468 USD.

**Where the money comes from (identical window, $100k).** The overlay does not reduce the tax bill:
A1 pays 826,311 USD of tax against R2's 766,160 and still ends 13 % wealthier. Its realised resize
gain rises from 51.6 m to 56.7 m TRY and its indexation relief from 18.6 m to 22.4 m TRY. What
changes is that the position sits *above* the vol target for up to a month, so the deferred sale is
larger and later, with one more month of PPI credit attached. A2 (four sells a year) is the extreme
version: +1.47…+3.30 pp after tax, +3.27 pp of drawdown, 66.2 m TRY of resize gain realised.

### Out of sample and other instruments (pre-tax, calibrated, m = 2)

| Window | A1 ΔCAGR / ΔDD | A2 ΔCAGR / ΔDD | A3 ΔCAGR / ΔDD |
|---|---:|---:|---:|
| ^GSPC 1957–1999 | −0.01 / +1.29 pp | +0.10 / +1.33 pp | +0.03 / −0.02 pp |
| M1 ^IXIC 1975–1999 | +0.63 / +0.00 | +0.79 / −0.40 | +0.20 / +0.00 |
| M2 ^GSPC 1935–1956 | +0.27 / +0.24 | +1.58 / −2.07 | +0.00 / +0.16 |
| M3 ^N225 | +0.09 / +0.67 | +0.55 / −1.00 | −0.01 / −0.20 |
| M4 ^GDAXI | −0.11 / +1.87 | +0.19 / **+4.33** | +0.04 / +0.93 |
| M5 ^FTSE | −0.02 / +0.35 | +0.08 / +0.21 | +0.01 / +0.01 |
| median Δ CAGR | +0.042 | +0.369 | +0.019 |
| pooled OOS bootstrap median | +0.138 pp (61.9 %) | +0.241 pp (57.9 %) | +0.017 pp (55.8 %) |
| 21 ETFs: ΔDD ≤ +3 pp | 21/21 (worst EWA +2.18 pp) | **16/21 (worst EWG +8.19 pp)** | 21/21 (worst XLU +1.33 pp) |
| 21 ETFs: median Δ CAGR | +0.039 pp | +0.365 pp | +0.018 pp |

A3 is essentially the control on every out-of-sample and instrument statistic (worst ETF ΔDD
+1.33 pp, 17 of 21 ETF ΔCAGR within ±0.1 pp, range −0.15 … +0.29 pp). A2's ETF drawdown is the
pre-registered fail.

### Cost stress (m = 4, $100k after tax)

A1 +0.54/+0.30/+0.29 pp, A2 +1.57/+1.62/+3.41, A3 +0.24/+0.13/+0.20 — every arm's edge grows
slightly with doubled cost (turnover falls).

### Where A1 and A3 lose (per-year NAV log-ratio vs R2, pre-tax, base, m = 2)

A1: sum +6.08 pp over the 26 years, worst years 2018 −1.86, 2020 −1.66, 2026 −0.60, 2019 −0.55,
2003 −0.32; best 2007 +1.75, 2009 +1.68, 2004 +1.67. A3: sum +1.86 pp, worst 2026 −0.74, 2020
−0.68, 2000 −0.56, 2019 −0.54, 2001 −0.36; best 2007 +1.27, 2009 +0.77, 2021 +0.75. Both lose in
2018 and 2020 — the mid-month-selloff risk the survey flagged — and the safe years are the
recoveries. 50 of the window's 319 Yi-UFE months are *down* versus the previous month, so part of the
deferral is strictly harmful for those lots; the net effect is the sum of the two.

## Gates

All must pass (G1–G9 as `ewma_band_v1` / `fast_reentry_v1`, control = R2; G10 = this task's 21-ETF
drawdown gate; T1–T3 after tax at $100k). `G-KEYS`: `P` = pass, `F` = fail.

| Arm | G1 base/cal | G2 base/cal | G3 base/cal | G4 | G5 base/cal | G6 base/cal | G7 | G8 | G9 | G10 | T1 | T2 | T3 | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A1 | P / P | P / P | P / P | P | **F / F** | P / P | P | P | P | P | P | P | **F** | **FAIL** |
| A2 | P / P | **F / F** | P / P | P | **F / F** | P / P | P | P | P | **F** | P | **F** | **F** | **FAIL** |
| A3 | P / P | P / P | P / P | P | **F / F** | P / P | P | P | P | P | P | P | P | **FAIL** |

Failed details: G5 medians −0.255 (A1) / −1.000 (A2) / −0.048 pp (A3) against a required ≥ 0;
G2 A2 max DD 50.03 % vs 46.76 % (+3.27 pp against a +1 pp allowance); G10 A2 76.2 % of instruments
within +3 pp against the required 80 %; T2 A2 median −0.016 pp / 49.8 %; T3 A1 −0.024 pp / 48.8 %,
A2 −0.438 pp / 43.4 %. Selected arm: **none**.

## Verdict and reading

- **The indexation step is real, and it is small once it is not the December boundary.** H2 (one
  boundary a year) measured +0.14/+0.13/+0.27 pp; A1 (twelve boundaries) measures +0.51/+0.28/+0.26
  pp — barely more, because the December boundary is the only one that also moves the tax *year*.
  A1's extra +0.27 pp pre-tax CAGR is the price of holding a stale weight, not of indexation.
- **The pre-registered bootstrap is what kills it.** G5 is the same 5,000-path block-20 bootstrap
  that H2's campaign passed; here every arm's median is negative, A3's by 4.8 bp. The main-window
  gains are concentrated in a handful of recovery years (A1: +1.75/+1.68/+1.67 pp in 2007/2009/2004,
  −1.86 in 2018) and the bootstrap does not draw them reliably.
- **A3 is the near-miss and the only arm worth an owner's attention**: 16 of the 18 gate keys pass
  (G5 in both financings fails), the after-tax benefit is positive in all three windows at all three
  capitals, the inflation-aware B1 bootstrap favours it on 70 % of paths, and its worst
  ETF/out-of-sample drawdown cost is ~1 pp. It fails G5 by 0.048 pp and nothing else.
- **A2 is not a tax overlay.** It suppresses nearly every monthly trim (4.2 sells/yr), ends +1.5 to
  +3.6 pp wealthier after tax, and carries +3.3 pp of max DD plus an 8.2 pp ETF drawdown outlier.
  Under this study's gates that is a fail, and the drawdown gate is exactly the one the survey said
  would decide the idea.
- Not a repeat of `tax_harvest_v1/v2` (December-only, H2 reproduced here to 1e-7 as an anchor), of
  `ewma_band_v1`/`after_tax_tr_v1` (constant wider bands, which change the target, not the fill
  date) or of `fast_reentry_v1/v2` (entry timing).

Caveats: FIFO and the monthly indexation step are modelled as `vt_level_tax_v1` does (no özelge for
foreign ETFs, month convention disputed); B2 charges no inflation, so the B1/B2 split is the honest
sensitivity; the pre-2003 Yi-UFE proxy applies equally to the control; the two containers of
evidence (indexation credit vs extra exposure) cannot be separated inside this framework — a version
that deferred only when the indexed gain is positive (dropped from the arm set) is the natural next
test, and `gain_band_v1` (I3) is the sibling that targets the same trims with a wider band.
