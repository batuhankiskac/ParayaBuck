# Year-end vol-resize deferral on R2, confirmation (tax_harvest_v2, 2026-10-14)

> Evidence for this report (`research/tax_harvest_v2/` code, inputs, results) is not on
> `main`; it lives on branch `research/tax_harvest_v2` and is reachable from this worktree.

**Verdict: FAIL as pre-registered — but the failure is one cell of the plateau gate, by 0.7 bp.**
H2 itself (v1's exact tail-10 arm) passes every other pre-registered gate and reproduces
`tax_harvest_v1` bit for bit (**+0.14 / +0.13 / +0.27 pp** after tax at $100k, bootstrap medians
**+0.055 / +0.045 pp** on 67.4 % / 64.4 % of paths, pre-tax 15.5137 % vs R2 15.4732 %), and passes
the new pre-tax out-of-sample, instrument and doubled-cost tests. The pre-registered **plateau**
gate (P) requires the 5- and 20-session neighbours to beat R2 too, and `H2_5` does **not** in the
2010 window: **14.6992 % vs R2 14.7059 % = −0.007 pp** after tax at $100k (identical at $10k and
$1M, and −0.007 pp at m = 4). Gate P is a conjunction, so the campaign's verdict is **FAIL**;
`H2_15` and `H2_20` pass every gate, and the whole plateau moves monotonically *upward* with the
window length.

- **Gate P (plateau) — FAIL.** Nine cells (tails 5/15/20 x three windows, $100k after tax):
  `H2_5` +0.006 / **−0.007** / +0.010 pp, `H2_15` +0.295 / +0.295 / +0.517 pp, `H2_20`
  +0.332 / +0.437 / +0.769 pp. Eight of nine are positive; the tie-breaking cell is inside the
  tax model's own rounding-level noise, and pre-tax it is the same story (H2_5 15.4512 % vs
  15.4732 %). The tail-5 window defers only 15–17 fills in 26.5 years (tail 10: 51–53,
  tail 15: 70–72, tail 20: 95–97), so at 5 sessions the overlay is nearly inert and its sign is
  whatever the December tape of that one calendar happened to be.
- **Gate F2 (six `ewma_band_v1` OOS windows) — PASS.** H2 median Δ CAGR **+0.028 pp**, worst
  Δ max DD **+0.004 pp** (M2); every arm passes with median Δ CAGR in +0.003 … +0.087 pp and
  the worst Δ max DD at +0.06 pp. The 1970s/1930s/1980s Non-US windows reproduce the same
  picture as the US history: a tiny positive drift, no drawdown penalty.
- **Gate F3 (21 independent ETFs) — PASS, comfortably.** 21/21 within Δ max DD +2 pp for every
  arm (H2's worst is +0.71 pp on XLY; H2_20's is +1.87 pp on XLI). Median Δ CAGR +0.025 pp,
  81 % of instruments positive for H2. `EWC` is excluded before simulation by `range_vol_v2`'s
  fixed quality rule (5.6 % invalid/flat bars).
- **Gate F4 (m = 4 cost stress) — PASS for H2, FAIL for H2_5.** H2 beats R2 after tax in all
  three windows at doubled cost (+0.15 / +0.13 / +0.28 pp); `H2_5` fails the same 2010 cell
  (−0.007 pp) that fails gate P.
- **The flagged risk (December 2018) does not exist.** R2 is **flat** from 2018-11-12 through
  January 2019 (E2's vote off, the early path never re-enters), so there is no position to hold
  through the selloff and no fill to defer: the measured H2−R2 difference for 2018 is exactly
  **0.000 pp**. The risk that *did* materialise is a **January** whipsaw: the worst episode is
  **2015-12-03 → 2016-01-29, −0.699 pp** (worst session 2016-01-04, −0.549 pp) and the second is
  **2024-12-03 → 2025-01-31, −0.318 pp** (worst session 2024-12-31, −0.217 pp). The third worst
  year is 2002 at −0.054 pp; the other 23 years are ≥ −0.000 pp, and the sum of all 26 episodes
  is **+0.930 pp**, i.e. essentially the whole H2−R2 pre-tax NAV differential.
- **Longer postponement is monotonically better, and still costs almost no drawdown.** Pre-tax
  CAGR base/calibrated: H2_5 15.4512 / 14.8708, H2 15.5137 / 14.9325, H2_15 15.5833 / 15.0016,
  H2_20 15.5075 / 14.9254; after tax at $100k the 2000/2010/2017 deltas are +0.006/+0.007/+0.010
  (H2_5), +0.142/+0.128/+0.266 (H2), +0.295/+0.295/+0.517 (H2_15) and +0.332/+0.437/+0.769
  (H2_20). The m = 4 stress keeps every sign. The one arm whose drawdown *is* worth watching is
  H2_20 (worst instrument Δ max DD +1.87 pp against the +2 pp bar); H2's is +0.71 pp.
- **Tax identity of the mechanism is unchanged from v1.** H2 pays more nominal tax
  (775,620 vs 766,160 USD over 2000–2026 at $100k, +1.2 %) and ends wealthier (1,352,539 vs
  1,306,627 USD): the deferral moves the assessment into the next year and removes ~3 % of the
  sells (29.78 vs 30.69 per year). Turnover is *lower* than R2's for every arm (5.91–5.99 vs
  5.99/yr, gate G9 is 2x), so the overlay cannot be a cost problem.
- **Not tax advice**, and the same model caveats as v1 apply verbatim (trade-date tax years,
  per-year netting without progressive loss carry-forward, FIFO, frozen-2026 tariff scaled by
  Yi-UFE, instalments funded by selling, no wash-sale rules needed here because the overlay
  realises nothing on its own).

## Method

- **Control (R2).** Production `equity_leveraged_trend_v1` 1.2.0: at least 2 of 3 votes on the
  QQQ **price** close (close > SMA200; 252-session price return > 0; close > the 126-session
  max/min midpoint), sized `vt = min(1, 0.35 / vol20)` on synthetic QLD\*, cap 0.98; while the
  vote is off, `0.5 x vt` if QQQ close > SMA50, QQQ vol20 < its trailing 252-session median and
  close > close 20 sessions ago, exited below SMA50; E2's vote overrides.
- **Arms (5).** All share R2's target series bit for bit; only the execution layer differs:
  **H2** = v1's arm (postpone a downward vol resize whose **fill** session is one of the last
  **10** December sessions), and **H2_5 / H2_15 / H2_20** = the same with 5 / 15 / 20 sessions.
  Exits to zero and up-resizes always fill; a postponed fill is not re-tried (the ordinary rule
  is re-evaluated at every later close).
- **Mechanics.** Decide at the completed close, fill at the next open; 0.5 pp resize band; idle
  cash earns zero; cost 4 bps x m x |delta w|; synthetic QLD\* = daily-reset 2x on QQQ total
  return, 0.95 %/yr expense, financing DTB3 + {base 0.0, calibrated 0.75 %}/yr. Main window
  2000-03-09 → 2026-09-29 (6,679 sessions).
- **After tax.** The corrected Turkish engine (`tax_engine.py`, byte copy of
  `tax_harvest_v1/tax_engine.py` = the `vt_level_tax_v1` / `r2_tbill_combo_v1` engine): per-calendar-year
  netting in TRY, trade-date years, Yi-UFE indexation with the 10 % floor, 2026 tariff scaled by
  the previous October's index, FIFO, instalments Mar 31/Jul 31 funded by selling, liquidation at
  the window end. Windows 2000-03-09 → 2026-09-29, 2010-01-01 → 2026-09-29, 2017-01-03 →
  2026-09-29; capitals $10k / $100k / $1M; base financing, m = 2 (m = 4 for the cost stress).
- **Gate F2 (OOS).** The six `ewma_band_v1` windows — ^GSPC 1957-01-02 → 1999-12-31 and
  `e2_confirmation_v1`'s M1 ^IXIC 1975–1999, M2 ^GSPC 1935–1956, M3 ^N225, M4 ^GDAXI, M5 ^FTSE —
  calibrated financing, m = 2, each with its own E2 + R2 early path on the market's own close and
  its own synthetic 2x leg.
- **Gate F3 (instruments).** 21 equity ETFs (19 `range_vol_v2` + SPY/DIA/IWM from `range_vol_v1`,
  `EWC` excluded by the fixed quality rule), each with its own E2 + R2 logic and own 2x leg,
  calibrated, m = 2 — `fast_reentry_v2`'s gate-I design.
- **Bootstrap (reported, not gated).** Stationary block-20, seed 20261011, 1,000 ten-year paths,
  fixed 2015-09-01 → 2026-09-30 calendar with its realized USD/TRY and Yi-UFE (B1-recent
  progressive, B2 flat 20 %), at $100k and $1M.
- **Harness.** `--harness` PASS, 0 checks outside tolerance: R2/C0 pre-tax CAGR, max DD and
  turnover reproduce `fast_reentry_v1/RESULTS.json` and `e2_integration_v1`'s deviation table at
  m = 1/2, base/calibrated (30 checks); the after-tax anchors of R2 and C0 at $100k reproduce
  within 0.00005 pp on all three windows; the control's bootstrap medians reproduce
  `r2_tbill_combo_v1` exactly (B1 R2 3.5155 %, B2 R2 3.9678 %); overlay `none` equals the
  reference `simulate_band` NAV path to 1.3·10⁻¹³. `--smoke` additionally proves that the
  OOS/instrument simulator (`simulate_band_overlay`) equals the tax engine's own pre-tax NAV path
  for every tail (5/10/15/20) and equals `simulate_band` when no deferral is armed.

## Results

### Pre-tax main window (m = 2)

| Arm | CAGR base | CAGR cal. | max DD base | max DD cal. | m = 1 base | trades/yr | sells/yr | turn/yr | deferrals |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **R2 (control)** | **15.4732 %** | **14.8927 %** | **46.7584 %** | 46.8797 % | **15.7521 %** | 61.94 | 29.56 | 5.9915 | 0 |
| **H2 (= v1 arm)** | **15.5137 %** | **14.9325 %** | **46.2542 %** | 46.3786 % | **15.7907 %** | 60.39 | 28.65 | 5.9482 | 51 |
| H2_5 | 15.4512 % | 14.8708 % | 46.7584 % | 46.8797 % | 15.7298 % | 61.48 | 29.25 | 5.9851 | 15 |
| H2_15 | 15.5833 % | 15.0016 % | 45.5794 % | 45.7061 % | 15.8592 % | 59.60 | 28.20 | 5.9204 | 70 |
| H2_20 | 15.5075 % | 14.9254 % | 46.5952 % | 46.7234 % | 15.7826 % | 58.96 | 27.71 | 5.9081 | 95 |

G1 (CAGR ≥ R2 − 0.10 pp) and G2 (max DD ≤ R2 + 1 pp) pass in both financings for every arm;
G9 (turnover ≤ 2x R2) passes with room (every arm is *below* R2).

### After tax, progressive 2026 tariff, liquidated (base financing, m = 2)

At **$100k** — the gate capital:

| Window | Arm | Pre-tax | After-tax | Δ vs R2 | max DD | Tax paid (USD) | Sells/yr | Deferrals |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 2000-03 → 2026-09 | R2 | 15.47 % | **10.16 %** | — | 46.76 % | 766,160 | 30.69 | 0 |
| | **H2** | 15.51 % | **10.30 %** | **+0.142** | 46.25 % | 775,620 | 29.78 | 53 |
| | H2_5 | 15.45 % | 10.17 % | +0.006 | 46.76 % | 765,197 | 30.38 | 17 |
| | H2_15 | 15.58 % | 10.45 % | +0.295 | 45.58 % | 788,423 | 29.33 | 72 |
| | H2_20 | 15.51 % | 10.49 % | +0.332 | 46.60 % | 776,768 | 28.84 | 97 |
| 2010-01 → end | R2 | 22.16 % | **14.71 %** | — | 43.78 % | 553,536 | 31.35 | 0 |
| | **H2** | 22.13 % | **14.84 %** | **+0.128** | 44.07 % | 551,338 | 30.34 | 32 |
| | H2_5 | 22.12 % | 14.70 % | **−0.007** | 43.95 % | 551,357 | 30.94 | 14 |
| | H2_15 | 22.16 % | 15.00 % | +0.295 | 43.88 % | 552,840 | 29.98 | 39 |
| | H2_20 | 22.16 % | 15.14 % | +0.437 | 43.88 % | 552,414 | 29.68 | 46 |
| 2017-01 → end | R2 | 32.53 % | **22.89 %** | — | 36.24 % | 360,297 | 35.60 | 0 |
| | **H2** | 32.57 % | **23.16 %** | **+0.266** | 34.86 % | 360,065 | 34.68 | 19 |
| | H2_5 | 32.51 % | 22.90 % | +0.010 | 36.18 % | 359,567 | 35.19 | 6 |
| | H2_15 | 32.59 % | 23.40 % | +0.517 | 33.56 % | 359,554 | 34.27 | 23 |
| | H2_20 | 32.60 % | 23.66 % | +0.769 | 31.62 % | 359,063 | 33.86 | 28 |

At **$10k** and **$1M** the signs are identical and the magnitudes nearly so (the progressive
tariff is the only scale dependence): H2 Δ vs R2 **+0.12 / +0.10 / +0.23 pp** at $10k and
**+0.16 / +0.14 / +0.28 pp** at $1M (v1's numbers); H2_5 −0.00 / −0.01 / +0.01 at both capitals
and H2_20 +0.27 / +0.35 / +0.58 ($10k) and +0.35 / +0.44 / +0.79 ($1M). R2's own after-tax CAGR
at $10k / $1M is 11.69 / 9.19 (2000 window), 17.03 / 13.62 (2010) and 26.04 / 21.72 (2017), i.e.
the brackets move about one pp of CAGR between $10k and $1M.

### Cost stress, m = 4, after tax at $100k

| Window | Arm | Pre-tax m4 | After-tax m4 | Δ vs R2 | max DD |
|---|---|---:|---:|---:|---:|
| 2000-03 → 2026-09 | R2 | 14.92 % | 9.73 % | — | 47.44 % |
| | **H2** | 14.96 % | 9.87 % | **+0.145** | 46.92 % |
| | H2_5 | 14.90 % | 9.74 % | +0.007 | 47.44 % |
| | H2_15 | 15.03 % | 10.03 % | +0.297 | 46.24 % |
| | H2_20 | 14.96 % | 10.07 % | +0.337 | 47.23 % |
| 2010-01 → end | R2 | 21.55 % | 14.23 % | — | 44.56 % |
| | **H2** | 21.52 % | 14.36 % | **+0.134** | 44.85 % |
| | H2_5 | 21.51 % | 14.22 % | **−0.007** | 44.73 % |
| | H2_15 | 21.55 % | 14.52 % | +0.295 | 44.65 % |
| | H2_20 | 21.56 % | 14.67 % | +0.440 | 44.65 % |
| 2017-01 → end | R2 | 32.09 % | 22.54 % | — | 36.72 % |
| | **H2** | 32.12 % | 22.82 % | **+0.276** | 35.34 % |
| | H2_5 | 32.06 % | 22.55 % | +0.010 | 36.66 % |
| | H2_15 | 32.14 % | 23.06 % | +0.517 | 34.04 % |
| | H2_20 | 32.15 % | 23.33 % | +0.779 | 32.12 % |

The deferral arms lose *less* to quadrupled cost than R2 does, because they trade less (turnover
5.91–5.99 vs 5.99/yr at m = 2; the m = 4 turnover column is in `RESULTS.json`).

### Gate F2 — six out-of-sample windows (pre-tax, calibrated, m = 2)

| Window | R2 CAGR / max DD | H2 Δ CAGR | H2 Δ max DD | H2_5 / H2_15 / H2_20 Δ CAGR |
|---|---:|---:|---:|---|
| ^GSPC 1957–1999 | 9.89 % / 39.63 % | +0.01 pp | +0.00 pp | +0.00 / +0.05 / +0.04 |
| M1 ^IXIC 1975–1999 | 21.18 % / 40.97 % | +0.07 pp | −0.00 pp | +0.02 / +0.27 / +0.48 |
| M2 ^GSPC 1935–1956 | 11.70 % / 58.33 % | +0.03 pp | +0.00 pp | +0.00 / +0.05 / +0.15 |
| M3 ^N225 1966–2026 | 6.40 % / 65.03 % | +0.02 pp | −0.29 pp | +0.00 / +0.03 / +0.08 |
| M4 ^GDAXI 1989–2026 | 9.47 % / 58.10 % | +0.08 pp | −0.00 pp | +0.02 / +0.08 / +0.09 |
| M5 ^FTSE 1985–2026 | −0.59 % / 85.45 % | +0.01 pp | −0.04 pp | +0.00 / +0.03 / +0.06 |

Median Δ CAGR over the six windows: H2 **+0.028 pp**, H2_5 +0.003, H2_15 +0.051, H2_20 +0.087.
Worst Δ max DD: H2 **+0.004 pp** (M2), H2_20 +0.06 pp (M4). Every arm passes both limbs.

### Gate F3 — 21 independent ETFs (pre-tax, calibrated, m = 2)

| Arm | Median Δ CAGR | Share Δ CAGR > 0 | Δ max DD ≤ +2 pp | Worst Δ max DD |
|---|---:|---:|---:|---:|
| **H2** | **+0.025 pp** | **81.0 %** (17/21) | **21/21 (100 %)** | +0.71 pp (XLY) |
| H2_5 | +0.002 pp | 76.2 % | 21/21 | +0.22 pp |
| H2_15 | +0.024 pp | 76.2 % | 21/21 | +1.05 pp |
| H2_20 | +0.031 pp | 66.7 % | 21/21 | +1.87 pp (XLI) |

Excluded by `range_vol_v2`'s fixed quality rule: `EWC` (5.6 % invalid/flat bars).

### Late-December episodes (main window, base, m = 2, pre-tax)

H2 − R2 NAV log-ratio change over `[last 20 December sessions] ∪ [first 20 January sessions]`:

| Year | Window | Δ pp | Worst session | Worst pp |
|---|---|---:|---|---:|
| **2015** | 2015-12-03 → 2016-01-29 | **−0.699** | 2016-01-04 | −0.549 |
| **2024** | 2024-12-03 → 2025-01-31 | **−0.318** | 2024-12-31 | −0.217 |
| 2002 | 2002-12-03 → 2003-01-30 | −0.054 | 2002-12-18 | −0.065 |
| 2012 | 2012-12-03 → 2013-01-30 | −0.000 | 2012-12-20 | −0.000 |
| 2010 | 2010-12-03 → 2011-01-31 | −0.000 | 2010-12-10 | −0.000 |
| 2013 | 2013-12-03 → 2014-01-30 | −0.000 | 2014-01-02 | −0.000 |
| ... | ... | +0.000 | | |
| 2007 | 2007-12-03 → 2008-01-30 | +0.382 | 2007-12-27 | −0.110 |
| 2021 | 2021-12-03 → 2022-01-31 | +0.504 | 2021-12-28 | −0.082 |
| 2001 | 2001-12-03 → 2002-01-30 | +0.996 | 2001-12-24 | −0.184 |

**December 2018 is exactly 0.000 pp**: R2 is flat from 2018-11-12 (an exit, which always fills)
through January 2019, so the last ten December sessions contain no down-resize to postpone. The
overlay's real risk is the *January* gap — 2015/2016 and 2024/2025 are the only two episodes
above 0.06 pp, and the 26 yearly windows together account for practically all of the H2−R2
terminal log-NAV difference (+0.930 pp of the measured +0.931 pp).

### Clean after-tax bootstrap (block-20, 1,000 paths, fixed 2015-09 → 2026-09 calendar)

| Capital | Mode | Arm | Median Δ vs R2 | Mean Δ | Share > 0 | p05 / p95 | Deferrals/10 yr |
|---|---|---|---:|---:|---:|---|---:|
| $100k | B1-recent | **H2** | **+0.055 pp** | +0.103 | **67.4 %** | −0.31 / +0.61 | 24 |
| $100k | B1-recent | H2_5 | +0.023 pp | +0.046 | 64.6 % | −0.17 / +0.29 | 10 |
| $100k | B1-recent | H2_15 | +0.089 pp | +0.148 | 65.8 % | −0.47 / +0.88 | 38 |
| $100k | B1-recent | H2_20 | +0.115 pp | +0.172 | 64.0 % | −0.63 / +1.17 | 52 |
| $100k | B2 flat | H2 | +0.045 pp | +0.069 | 64.4 % | −0.37 / +0.53 | 24 |
| $1M | B1-recent | H2 | +0.071 pp | +0.124 | 69.7 % | −0.28 / +0.65 | 24 |
| $1M | B2 flat | H2 | +0.045 pp | +0.069 | 64.4 % | −0.37 / +0.53 | 24 |

H2's four cells reproduce `tax_harvest_v1` exactly (+0.055 / +0.045 at $100k, +0.071 / +0.045 at
$1M). Every arm and every cell has a non-negative median and a positive share above 55 %
(H2_20's p05 is −0.74 pp in B2 at $100k, the widest tail of the family; H2's worst is −0.37 pp).
The bootstrap is *reported*,
not gated — this campaign's verdict rule is the task's (P + F2 + F3 + F4); note that H2 would
also pass the standing bootstrap gate, H2_5 and H2_20 would too (H2_20 at 61.6–67.8 %).

### Gates

| Arm | P (plateau member) | F2 OOS | F3 instruments | F4 m = 4 | 100k after-tax cells | Bootstrap cells |
|---|---|---|---|---|---|---|
| **H2** | n/a (candidate) | **PASS** (+0.028 pp, +0.004 pp) | **PASS** (21/21) | **PASS** (3/3) | **3/3** | 4/4 positive |
| H2_5 | **FAIL** (2010: −0.007 pp) | PASS | PASS | **FAIL** (2010: −0.007 pp) | 2/3 | positive |
| H2_15 | **PASS** | PASS | PASS | PASS | 3/3 | positive |
| H2_20 | **PASS** | PASS | PASS | PASS | 3/3 | positive |

**Pre-registered verdict rule: CONFIRMED iff P ∧ F2 ∧ F3 ∧ F4.** Gate P is a conjunction over
the three plateau neighbours, so **verdict = FAIL**, and H2 is *not* confirmed — the plateau is
not flat in the strict sense: shortening the postponement to five sessions makes the overlay
nearly inert (one of the nine cells is −0.007 pp, the other eight lie within ±0.01 pp), while
lengthening it to 15/20 sessions makes the after-tax effect 2–3x larger in the same direction.

## Findings

- **H2 is confirmed in isolation and rejected as a plateau, and the rejection is one knife-edge
  cell.** Every part of the v2 test battery that is about H2 itself passes, including two of the
  three things v1 never tested (six OOS windows, 21 instruments, doubled cost). The only failure
  is the pre-registered requirement that the *5-session* neighbour be positive in the 2010
  window; it is −0.0067 pp (a 0.07 % relative difference in terminal wealth).
- **The rule's true parameter response is monotone in the window length, not flat.** Deferrals per
  26.5 years: 15 (tail 5) → 51 (10) → 70 (15) → 95 (20); after-tax deltas at $100k rise with the
  window (+0.006 → +0.142 → +0.295 → +0.332 pp over 2000–2026), and the instrument drawdown cost
  rises with it too, from +0.22 pp (H2_5) to +1.87 pp (H2_20) at the worst ETF. So the honest
  reading of the plateau test is "the effect is real but small, and it needs a *long* window to
  be visible" — the opposite of a flat plateau.
- **The mechanism is a January, not a December, risk.** The two loss episodes are 2015/2016 and
  2024/2025 (−0.70 and −0.32 pp on the 26.5-year path); both are the deferred weight being held
  into a January drawdown. December 2018 — the case the task flagged — is a zero because the
  strategy is already flat (the E2 exit fires on 2018-11-12 and the early path stays out), which
  is a general property: a year-end selloff that is severe enough to matter is preceded by an
  E2 exit, and exits always fill.
- **The tax channel is deferral + fewer sells, unchanged.** H2 pays 775,620 USD of tax over
  2000–2026 at $100k vs R2's 766,160 (+1.2 %) and ends 45,912 USD wealthier (1,352,539 vs
  1,306,627 USD); the drag falls from 5.31 (R2) to 5.21 pp (H2). H2_20's mechanism is the same,
  only larger: 1,415,489 USD terminal (Δ vs R2 +108,862 USD) with 776,768 USD of tax paid and a
  5.02 pp drag (H2_15: 1,402,677 USD, 788,423 tax, 5.13 pp drag).
- **Turnover and cost are not constraints.** Every arm's turnover is at or below R2's, and the
  m = 4 stress preserves the sign of every arm's edge.

## Caveats

- **The FAIL is a pre-registration artefact of a conjunction, not evidence of harm.**
  H2_5's failing cell is −0.007 pp; pre-tax it is −0.022 pp. No arm is *harmful* anywhere in the
  measured set (worst plateau cell −0.007 pp after tax; worst instrument Δ max DD +1.87 pp against
  a +2 pp bar; worst OOS Δ max DD +0.06 pp). I did not change the gate after seeing the data.
- **Nine plateau cells and 21 instruments and six OOS windows are a lot of comparisons for a
  ~0.05 pp effect.** The agent maximizing any of these statistics after the fact would pick
  H2_20 (the longest window), which is also the arm with the deepest instrument drawdown cost —
  that is exactly the kind of selection this gate exists to prevent, and it is not done here.
- **One history per cell.** The bootstrap agrees in sign everywhere for every arm, but its median
  is +0.02 to +0.12 pp, and the B1 $100k p05 for H2_20 is −0.63 pp.
- **OOS windows are price-only** for the long histories (^GSPC 1935–56, ^N225, ^GDAXI, ^FTSE) and
  the ETF sample overlaps heavily in calendar time (all 21 end in 2026); independence is by
  instrument, not by time — the same caveat `fast_reentry_v2` and `range_vol_v2` record.
- **Not tax advice**, one tax model, one FX/CPI calendar (the frozen 2015-09 → 2026-09 bootstrap
  calendar cannot price a different TRY regime).
- **Nothing was shipped.** No `src/`, `config/`, runtime or production change of any kind.

## Reproduction

```
cd /Users/batuhankiskac/Desktop/ParayaBuck-wt/tax_harvest_v2
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/tax_harvest_v2/screen.py --smoke    # synthetic only
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/tax_harvest_v2/screen.py --harness  # control only
/Users/batuhankiskac/Desktop/ParayaBuck/.venv/bin/python research/tax_harvest_v2/screen.py --run      # ~2m20s
```

`PREREGISTRATION.md`, `screen.py`, `tax_engine.py`, `DATA_MANIFEST.json` and the restored CSV
inputs were committed in `1c04954a`, **before any non-control arm was simulated on real data**
(only `--smoke` and the control-only `--harness` had run); the A1 one-line fix, `RESULTS.json`,
`SUMMARY.txt` and this report follow in the next commit on the branch. Two consecutive `--run`
invocations produced byte-identical `RESULTS.json` (SHA-256
`aea7f3807830578c43afa1eaf60923a5a22f3f7ea8cecf723c8d0d842f231117`) and `SUMMARY.txt`
(`e9fc81a4d410e5da167c36145ac66badaea5db2430e64de5c6578a1e45c846cb`); each run takes ~2m20s.

## Amendments

- **A1 (2026-10-14, before any candidate arm touched real data).** The `screen.py` committed with
  the pre-registration referenced an undefined `DTB3` name in the new out-of-sample loaders
  (`--run` aborted with a `NameError` at the OOS stage). One line was added —
  `DTB3 = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"` — the same file
  `trend_robustness_v2.load()` already reads for financing. No arm, parameter, window, data input
  or gate changed; the same commit records the fix and the results, and every number in this
  report comes from the fixed code. No gate was relaxed after seeing data.
