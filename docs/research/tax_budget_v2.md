# Annual tax-budget execution on top of K — loss-pool fill, bracket headroom, state-keyed year choice (tax_budget_v2, 2026-10-16)

> Evidence: tag `archive/research/tax-budget-v2-2026-10` (not on `main`).

> Evidence: `research/tax_budget_v2/` (pre-registration, `DATA_MANIFEST.json`, `screen.py`, the
> engine fork `fe/`, `RESULTS.json`, `SUMMARY.txt`) and the restored `research/fast_engine/` +
> `research/band_h2_combo_v1/` (tag `archive/research/fast-engine-v1-2026-10`) and
> `research/trend_robustness_v2/screen.py` (tag `archive/research/main-research-dir-2026-10`).
> This is the redo of `tax_budget_v1` on the mandatory Numba fast engine; its arms, parameters,
> gates and selection rule are copied verbatim from that study's pre-registration (tag
> `archive/research/tax-budget-v1-oldengine-prereg-2026-10`). Runs: `--run` 24.2 s / 24.0 s
> (two consecutive runs, byte-identical `RESULTS.json`, `--harness` PASS both times).

**Verdict: no arm is selected — the I3 axis is closed under the pre-registered gates, and the
measurement that closes it is worth keeping. Every gated arm fails G1 (the strict pre-tax
CAGR ≥ K requirement, missed by 0.007–0.057 pp), G4, G6 and A1; Y1/Y2/Y4 also fail G7/G8 (the
pre-registered OOS pre-tax cells a tax-timing arm cannot win, §8.6), and A2 fails for all three
because the *flat* (loss-carry-forward) bootstrap kills the effect. The one real signal is Y1's
after-tax effect **in the progressive regime only**: +0.085 / +0.211 / 0.000 pp at $100k
(+0.09 / +0.27 / 0.00 at $1M), clean-bootstrap B1-recent median **+0.046 pp on 75.6 % of paths**,
median +0.101 pp over 21 rolling January starts, but B2-flat median **−0.0045 pp (8.9 % > 0)** and
a 2017 window in which Y1 never fires at all (its Δ is exactly 0.0000 pp, bit-identical to K,
because the window's only loss year — 2022 — is one the strategy spends flat).
Y2 (bracket headroom) is a systematic drag (−0.044 / −0.038 pp pre-tax, −0.06/+0.17/+0.04 pp after
tax, B1 −0.052 pp) exactly as pre-registered. Y3 (state-keyed year choice) adds no trade yet loses
0.048 pp in the 2000 window and is inert in the other two, and its B1 cell fails the share test
(43.2 % > 0). Pre-registered expectation 5 ("the honest headline may be *the axis is closed*") is
what happened; the destroyed-loss pool itself was never nearly empty (8 loss years, 383,859 TRY),
so the reason for failure is *not* an absent channel but the cap, the flat regime and the gates.**

- **The pool is real and was measured first.** On the control's own path at $100k (progressive,
  main window 2000-03-09 → 2026-09-29): **8 loss years of 26** (2000, 2001, 2002, 2004, 2005,
  2008, 2016, 2022), `Σ|min(0, ytd)| = 383,859 TRY ≈ $92,108`, and the 25 %-NAV cap binds in 15 of
  the 26 years. Y1's trigger is available in all 8 loss years, Y2's in 17 years. This falsifies the
  pre-registered expectation 1 ("the pool is nearly empty") — `tax_harvest_v1` H1's failure was a
  *loss-harvesting* failure, not evidence that losses are absent from K's year-by-year netting.
- **Y1 fires 4 times in 26.5 years** (2001, 2004, 2005, 2016), always with the 25 %-NAV cap
  binding ($16.5 k–$50.1 k notional per event, 59,440 TRY of gain booked in total); in the pre-tax
  mirror of the same path it fires a fifth time, a $11 fill in 2013 (0.00 % of NAV). **Y2 fires 12
  times**, and in 10 of them the whole book's prefix gain still had not reached `ceiling − ytd`
  (the ceilings are 0.4–5.3 m TRY), so it degenerates into the pre-registered "sell-and-rebuy 25 %
  of NAV every year the trigger fires".
- **Y3 changes 5 of 6,679 decisions** in the 2000 window and none in the 2010/2017 windows: 53 of
  its 67 holds are December sessions that K's own H2 window already postpones (identical outcome),
  and 14 are January sessions. Net effect: −0.048 / 0.000 / 0.000 pp after tax and −0.057 pp
  pre-tax, with max DD +0.70 pp.
- **The only after-tax edge is Y1's, and it is a progressive-regime artefact by construction.**
  In a net-loss year the assessed tax is already zero, so a fill up to the pool is taxed at 0 % and
  steps the basis up, removing the future appreciation of that notional from the terminal (top-rate)
  liquidation. Y1 pays **$9,349 more** nominal tax over the 2000 window at $100k ($840,252 vs K's
  $830,903) and ends with **$30,959 more** wealth ($1,537,944 vs $1,506,985); in the 2010 window it
  pays $11,960 more and ends $33,460 ahead. Under B2 (flat 20 %, losses carried forward) there is no
  destroyed loss to price at 0 %, and the same round trips are a pure cost — 8.9 % of paths positive.
- **Nothing here is tax advice.** Y1/Y2/Y4 rest on a same-session sell-and-rebuy whose Turkish
  treatment is unverified (no wash-sale rule exists in the model; an anti-abuse/economic-substance
  objection is not excluded). A written mali müşavir opinion is a precondition for live use. Y3 is
  the only arm with no legal question, and it is the one with no measurable benefit.
- **Still the incumbent.** Production K (`equity_leveraged_trend_v1` 1.2.0) is unchanged; no file
  outside `research/tax_budget_v2/` and `docs/research/` was written.

## Method

- **Control (K).** Production `equity_leveraged_trend_v1` 1.2.0 = E2 (2-of-3 QQQ vote) + R2 early
  re-entry + **A1u** (gain-scaled downsize band, `B_down` = 0.5/3/8 pp at `g_frac` ≤ 1 %/≤ 5 %/> 5 %,
  USD FIFO gain fraction) + **H2** (a non-exit downward resize whose *fill* session is one of the
  last 10 December sessions is postponed). Decide on the completed close, fill at the next open,
  symmetric 0.5 pp band, entries and exits to zero always fill, idle cash zero, cost 4 bps × m × |Δw|,
  synthetic QLD\* = daily-reset 2× on QQQ total return with a 0.95 %/yr expense, financing DTB3 +
  {base 0 %, calibrated 0.75 %}/yr. `C0` (production R2, K's execution layer off) is the report-only
  reference row.
- **Engine.** `research/fast_engine/` (tag `archive/research/fast-engine-v1-2026-10`), forked into
  `research/tax_budget_v2/fe/` for the two new execution mechanisms (the parent package is never
  edited). `fe_sim.sim_one` gains `yearend` (0/1/2 = off / Y1's pool fill / Y2's headroom fill) and
  `realloc` (Y3/Y4's state window); with both off every added branch is skipped. The fork also adds
  the event traces the report uses (`ye_tr`: ytd before/after, quantity, wanted quantity, NAV,
  ceiling, units before/after; `re_tr`: ytd of both candidate years and the drift), a `yearend` gain
  reason and `TARIFF_TOP` (`fe_tax`), the two rule arrays in `fe_boot`, `jan_head` in `RunEnv`, and
  `fe_oos` (gate G8's pooled M1–M5 bootstrap as one numba kernel, `prange` over paths — the protocol
  forbids new per-path Python simulation loops).
- **Arms (every parameter fixed in the pre-registration; nothing tuned).**
  - **Y1** loss-pool fill: at the last session of each calendar year **fully inside** the window, if
    the account's YTD realised net TRY gain `ytd < 0`, sell-and-rebuy the smallest quantity whose
    realised indexed-TRY gain brings the year to ≈ 0 (`need = −ytd`), capped at `min(25 % NAV, book)`.
  - **Y2** bracket-headroom fill: same session, fire while `ytd < 0.25 × ceiling`, where
    `ceiling = 5,300,000 × scale(year)` TRY is the 2026 top bracket deflated by the previous
    October's Yİ-ÜFE; fill `need = ceiling − ytd`, capped identically.
  - **Y3** no-new-trade year reallocation: H2's calendar window is *replaced* by a state window — a
    pending non-exit downward resize whose fill session is in the last 10 December or the first 10
    January sessions fills only when the session's own tax year is the cheaper of the two adjacent
    years (`ytd(this) ≤ ytd(other)`, other = 0 for December, the finished previous year for January);
    **A1u's band then applies as in K**. Exits and up-sizes are untouched.
  - **Y4** = Y1 + Y3 (report-only upper bound).
- **The round trip is modelled exactly.** Both legs fill at the **decision close** (a disclosed,
  deliberate departure from the next-open convention: a next-open rebuy would place the realisation
  in the following tax year and destroy the mechanism). Costs are charged on both legs at the
  production rate; the sale consumes the FIFO queue oldest-first with the engine's own per-lot
  arithmetic, and the new lot is written at the rebuy price including the fee, at that session's
  CBRT rate and month — so the consumed lots' indexation relief is destroyed. The traded notional is
  added to turnover at twice its weight, so G9 sees the real cost (`sells/yr` deliberately excludes
  these trades, as pre-registered). The read-only probe and the engine's booking agree exactly in
  every one of the 42 logged round trips (`booked == Σ(ytd_after − ytd_before) == the "yearend"
  reason gain`, checked in `--smoke` and in the `log` stage).
- **Where the fill falls short of the pool.** `q_needed` is the FIFO-prefix crossing; if the whole
  book's cumulative gain never reaches it (every lot at a TRY loss — selling more cannot move the
  year towards zero), the fill falls back to the whole capped book (`gain_matched_units`'s documented
  behaviour, `tax_budget_v1`'s implementation). Measured incidence at $100k progressive: **1 of Y1's
  4** and **10 of Y2's 12** round trips; in 2001 this *deepened* the year's loss (−497 → −4,443 TRY).
  The report keeps both counts visible so the alternative reading ("no fill when the crossing does
  not exist") can be priced by the reader: it would leave Y1 with 3 firings (2004, 2005, 2016).
- **Stages.** `pre` (main window, base/calibrated × m = 1/2/4, plus the R-USD row), `AT` (after tax
  at $100k and $1M on the three windows), `cost` (m = 4), `B12` (clean block bootstrap, seed
  20261011, block 20, 5,000 pre-tax / 1,000 after-tax paths, B1-recent progressive and B2 flat,
  $100k + $1M, fixed 2015-09 → 2026-09 calendar), `OOS` (the six `ewma_band_v1` windows, pre-tax,
  calibrated, m = 2, R-USD), `OOSB` (G8's pooled M1–M5 bootstrap, 2,000 paths per market), `starts`
  (21 rolling January starts), `pool` (the §6 measurement from the control only), `log` (the arms'
  events).
- **Harness (all reproduced, `--harness` PASS, zero mismatches).** (1)
  `python -m research.fast_engine.parity --quick` → PASS (`engine_side_by_side` 1,009 cells with
  max|diff| **0**). (2) The fork's option-off path is the parent engine **bit for bit**: `C0/A1u/H2/K`
  × `pre_tax/progressive/flat` × m = 1/2/4 on the main window → NAV paths identical
  (`np.array_equal`, no tolerance) and every shared scalar identical, 936 cells; the fork only adds
  keys. (3) **3,094 published cells** of `research/band_h2_combo_v1/RESULTS.json` across 189 blocks
  (`pre_tax_main` base/calibrated × m1/m2/m4 for all four parent arms, `pre_tax_main_r_usd`,
  `after_tax_windows` at $10k/$30k/$100k/$300k/$1M × three windows, `cost_stress_m4`, `oos`, and
  every cell of `bootstrap_clean_seed20261011` including the `*_vs_*` deltas and the pure-E2 anchor)
  → **0 mismatches, worst |diff| 5.0·10⁻⁷** (the 6-dp rounding `RESULTS.json` stores); the literal
  brief anchors are asserted at the brief's own 0.01 pp tolerance (K pre-tax 15.7649 / 15.1785 %,
  max DD 46.6698 / 46.7948 %, turnover 5.4561; K after tax 10.7533 / 15.2354 / 23.9720 %; R2 after
  tax 10.1600 / 14.7059 / 22.8861 %; E2 pre-tax 14.6749 / 14.1043 %, max DD 49.5643 %).

## Results

### Pre-tax main window (m = 2; delta vs K in pp)

| Arm | CAGR base | Δ | CAGR cal. | Δ | max DD base | Δ | Calmar | 2017+ | turnover/yr | trades | sells/yr |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C0 (R2) | 15.4766 % | −0.288 | 14.8961 % | −0.282 | 46.758 % | +0.089 | 0.3310 | 32.71 % | 5.9915 | 1,645 | 29.56 |
| **K (control)** | **15.7649 %** | — | **15.1785 %** | — | **46.670 %** | — | 0.3378 | 33.23 % | 5.4561 | 877 | 10.17 |
| Y1 | 15.7582 % | −0.007 | 15.1719 % | −0.007 | 46.691 % | +0.021 | 0.3375 | 33.23 % | 5.5315 | 877 | 10.17 |
| Y2 | 15.7209 % | −0.044 | 15.1401 % | −0.038 | 46.691 % | +0.021 | 0.3367 | 33.15 % | 5.6788 | 878 | 10.24 |
| Y3 | 15.7076 % | −0.057 | 15.1216 % | −0.057 | 47.366 % | +0.697 | 0.3316 | 33.23 % | 5.4390 | 878 | 10.17 |
| Y4 | 15.7009 % | −0.064 | 15.1150 % | −0.063 | 47.387 % | +0.717 | 0.3313 | 33.23 % | 5.5143 | 878 | 10.17 |

Sub-periods (base, m = 2): K 0.29 / 14.27 / 33.23 %; Y1 0.27 / 14.26 / 33.23; Y2 0.26 / 14.24 /
33.15; Y3 0.14 / 14.27 / 33.23; Y4 0.12 / 14.26 / 33.23 — every arm loses P1 and wins (at most) P3.
At m = 1 the same ordering holds (K 16.0173 % / 15.4303 %, Y1 −0.003 / −0.003 pp, Y2 −0.010 /
−0.028, Y3 −0.058 / −0.058, Y4 −0.062 / −0.061); at m = 4 the deficits widen slightly
(K 15.2541 %, Y1 −0.013, Y2 −0.059, Y3 −0.056, Y4 −0.069). No arm beats K pre-tax anywhere.

### After tax at $100k (progressive, liquidated, m = 2; delta vs K in pp)

| Arm | 2000-03 → 2026-09 | Δ | 2010-01 → end | Δ | 2017-01 → end | Δ | tax paid 2000 ($) | terminal 2000 ($) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| C0 (R2) | 10.1600 % | −0.59 | 14.7059 % | −0.53 | 22.8861 % | −1.09 | 766,160 | 1,306,627 |
| **K** | **10.7533 %** | — | **15.2354 %** | — | **23.9720 %** | — | 830,903 | 1,506,985 |
| Y1 | 10.8381 % | **+0.085** | 15.4466 % | **+0.211** | 23.9720 % | 0.000* | 840,252 | 1,537,944 |
| Y2 | 10.6943 % | −0.059 | 15.4102 % | +0.175 | 24.0161 % | +0.044 | 807,756 | 1,485,811 |
| Y3 | 10.7055 % | −0.048 | 15.2354 % | 0.000* | 23.9720 % | 0.000* | 820,567 | 1,489,817 |
| Y4 | 10.7903 % | +0.037 | 15.4466 % | +0.211 | 23.9720 % | 0.000* | 829,800 | 1,520,396 |

\* bit-identical to K's NAV: in the 2017 window Y1/Y3/Y4 never fire — its only loss year (2022,
135,354 TRY) is one in which K holds nothing at the year end, so the fill has no lot to act on —
and no strict "> 0" can be produced. Y2 fires once there (its headroom trigger is met in 2017).

At **$1M**: K 9.8773 / 14.1650 / 22.8581 %; Y1 +0.092 / +0.270 / 0.000; Y2 +0.075 / +0.232 / +0.037;
Y3 −0.052 / 0.000 / 0.000; Y4 +0.040 / +0.270 / 0.000. Under the **m = 4 cost stress** at $100k:
K 10.3539 / 14.8202 / 23.6749 %; Y1 +0.079 / +0.202 / 0.000; Y2 −0.074 / +0.174 / +0.039;
Y3 −0.046 / 0.000 / 0.000.

Rolling January starts (report-only, after tax at $100k, 21 starts 2001…2021): Y1 median
**+0.101 pp** (16 of 21 positive, max +0.211); Y4 +0.103 (16/21); Y2 +0.016 (12/21);
Y3 0.000 (2/21).

### Bootstrap / OOS (delta vs K, pp)

| Arm | B12 pre base (5,000) | B12 pre cal. | B1-recent $100k | share > 0 | B2 flat $100k | share > 0 | G8 OOS pooled (10,000) |
|---|---:|---:|---:|---:|---:|---:|---:|
| Y1 | −0.005 | −0.005 | **+0.046** | **75.6 %** | −0.0045 | 8.9 % | −0.012 |
| Y2 | −0.014 | −0.015 | −0.052 | 36.8 % | −0.0705 | 7.1 % | −0.030 |
| Y3 | 0.000 | 0.000 | 0.000 | 43.2 % | 0.0000 | 37.6 % | 0.000 |
| Y4 | −0.004 | −0.004 | +0.046 | 66.3 % | −0.0037 | 34.3 % | −0.009 |
| C0 (row) | +0.007 | +0.012 | −0.286 | 25.0 % | −0.1311 | 36.0 % | −0.037 |

Y1's B1-recent distribution is nearly one-sided: mean +0.067, p05 −0.022, p95 +0.231 pp; under B2
it is degenerate the other way (mean −0.007, p95 +0.010). Y3's B1 median is exactly 0 with 43.2 % of
paths positive — most paths never meet the state window's condition. Six OOS windows (Δ CAGR, pp):
Y1 −0.004 (^GSPC) / −0.006 / −0.007 / −0.009 / −0.021 / −0.015 (M1–M5); Y2 −0.035 … −0.042;
Y3 −0.001 / +0.054 / 0.000 / +0.009 / +0.113 / +0.006; Y4 −0.005 / +0.048 / −0.007 / −0.001 /
+0.098 / −0.009. Max Δ DD over the six windows: Y1 +0.06 pp, Y2 +0.09, Y3 +0.35, Y4 +0.41 — never
near the +8 pp limit; only the *median* Δ CAGR separates them.

### Gates (vs K; G5 pre-registered as non-inferiority at ≥ −0.10 pp)

| Arm | G1 | G2 | G3 | G4 | G5 | G6 | G7 | G8 | G9 | A1 | A2 | Result |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Y1 | **fail** −0.007/−0.007 | pass | pass | **fail** (P3 only) | pass −0.005/−0.005 | **fail** −0.003 | **fail** −0.008 med | **fail** −0.012 | pass 1.014× | **fail** (+0.085/+0.211/0.000) | **fail** (B2) | **FAIL** |
| Y2 | **fail** −0.044/−0.038 | pass | pass | **fail** (0/3) | pass −0.014/−0.015 | **fail** −0.010 | **fail** −0.033 med | **fail** −0.030 | pass 1.041× | **fail** (−0.06/+0.18/+0.04) | **fail** (B2) | **FAIL** |
| Y3 | **fail** −0.057/−0.057 | pass | pass | **fail** (0/3) | pass 0.000 | **fail** −0.058 | pass +0.008 med | pass 0.000 | pass 0.997× | **fail** (−0.05/0.00/0.00) | **fail** (B1 share 43 %) | **FAIL** |
| Y4 | **fail** −0.064/−0.064 | pass | pass | **fail** (P3 only) | pass −0.004 | **fail** −0.062 | **fail** −0.003 med | **fail** −0.009 | pass 1.011× | **fail** (+0.04/+0.21/0.00) | **fail** (B2) | report-only |

Selection rule: highest B1-recent $100k after-tax bootstrap median Δ among passers, tie-break the
2000-2026 $100k Δ. **`selected = null`; production K stays.** Failed gates per arm: Y1 G1 G4 G6 G7
G8 A1 A2; Y2 the same set; Y3 G1 G4 G6 A1 A2. The verdict does not hinge on any single gate:
relaxing the two strict pre-tax return gates to the same −0.10 pp non-inferiority G5 uses (a
*post-hoc* observation, not a gate) leaves Y1/Y3 failing **A1** (a window in which the arm is
bit-identical to K) and **A2** (B2-flat), and Y2 failing A1 outright.

### The measurement that closes the channel (§6, from the control only)

| Year | YTD realised net TRY | pool | ceiling TRY | ytd/ceiling | 25 %-NAV cap TRY | cap binds |
|---|---:|---:|---:|---:|---:|---|
| 2000 | −14,977 | 14,977 | 27,415 | −0.55 | 11,937 | yes |
| 2001 | −497 | 497 | 39,247 | −0.01 | 23,845 | yes |
| 2002 | −13,793 | 13,793 | 75,234 | −0.18 | 25,341 | yes |
| 2004 | −5,331 | 5,331 | 112,281 | −0.05 | 32,403 | yes |
| 2005 | −8,876 | 8,876 | 134,326 | −0.07 | 31,573 | yes |
| 2008 | −32,244 | 32,244 | 159,574 | −0.20 | 35,276 | yes |
| 2016 | −55,430 | 55,430 | 285,687 | −0.19 | 175,163 | yes |
| 2022 | −252,711 | 252,711 | 878,652 | −0.29 | 2,749,177 | no |

`Σ pool = 383,859 TRY ≈ $92,108`; 8 loss years of 26. **The pool and the exposure do not
coincide**: the book is *empty* at the last session of 2000, 2002, 2008 and 2022 (units = 0 on the
K path) and non-empty at 2001, 2004, 2005 and 2016 — exactly the four years Y1 fires in. So
**313,725 TRY of the 383,859 (81.7 %) is unfillable by construction**: the biggest pools (2008:
32,244; 2022: 252,711) belong to the bear years in which the exit that realised the loss also
flattened the book, and a same-session basis step-up has nothing left to step up. The fillable
18.3 % (2001/2004/2005/2016, 70,134 TRY) is what the 25 %-NAV cap then truncates. The same split
explains the window cells: the 2010 window's loss years are 2010 (483,513 units at the year end),
2016 (148,627) and 2022 (0) — two fillings — while the 2017 window's *only* loss year is 2022 with
an empty book, so Y1 cannot fire there at all. The *pos/neg split* of a loss year's realisations
(`idea_survey_v3` I5's "fraction already absorbed by same-year gains") is not reportable: the
engine's per-year accumulator is net only; the report says so rather than approximating it.

### Event log ($100k, m = 2, main window)

Y1, progressive — 4 round trips, all cap-bound: 2001-12-31 (ytd −497 → −4,443 TRY, 25 % NAV,
$16,483 notional; the whole-book fallback), 2004-12-31 (−5,329 → −1,103, $24,239), 2005-12-30
(−13,149 → −8,838, $23,510), 2016-12-30 (−55,767 → −919, $50,077). 59,440 TRY of gain booked. The
same arm at pre-tax also fires in 2013 (a 20.7-unit, $11 crossing) — a reminder that the trigger is
regime-specific by construction.

Y2, progressive — 12 round trips, 10 of them whole-book fallbacks, every one capped at 25 % of NAV
($16.5 k–$82.6 k notional, $428,212 in total; 333,080 TRY of gain booked), including 2013-12-31
(ytd +13 TRY) and 2017-12-29 (ytd 0 → +169,351 TRY inside a year whose ceiling is 293,798 TRY).

Y3, progressive — 67 realloc holds, 53 in December and 14 in January; every hold satisfies
`ytd(this year) > ytd(other year)`, and only 5 of the 6,679 sessions' decisions differ from K's.

## Findings

- **Closing the axis was the point, and it is closed.** The pre-registration pre-committed G5's
  non-inferiority reading *precisely because* a tax-timing arm measures only trading cost pre-tax;
  the arms pass G5 (all three, both financings) and still fail the other eight pre-tax gates. The
  binding ones are G1/G6 (a strict pre-tax CAGR ≥ K, missed by 0.007 pp for Y1) and G4 (2 of 3
  sub-periods), which no arm could satisfy because its effect is a few sessions a year at the year
  end. On a fair reading, pre-tax G1 for these arms is not a test of the idea at all — but A1/A2
  are, and they fail on their own merits (below), so no re-tune is proposed.
- **Y1's edge is real, small, regime-specific and bounded by the 25 % cap.** +0.046 pp median on
  75.6 % of B1-recent paths (mean +0.067, p05 −0.022) and +0.101 pp over 21 rolling starts is the
  clearest after-tax signal any single-fill overlay in this line has produced since H2 — but it
  vanishes under B2-flat (−0.0045 median, 8.9 % positive) and it is exactly 0 on the 2017 window.
  Both facts are structural, not noise: B2 carries losses forward, which removes the 0 %-rate
  offset that *is* the mechanism; and in 2017–2025 the only K-path loss year (2022) is a year the
strategy ends flat, so there is no lot to step up.
- **The 25 %-NAV cap, not the pool, is the binding constraint.** All four Y1 firings (and all twelve
  Y2 firings) hit the cap; in 2016 the cap truncated a wanted 54,337 units (≈$50,916) to 53,442
  units ($50,077) and still left the year at −919 TRY. Any future attempt on this channel would have
  to raise the cap, which is a *different* (and much larger) round trip than the one pre-registered
  here.
- **The whole-book fallback matters and is now measured.** In 2001 the crossing did not exist
  (need 497 TRY, the whole 116,967-unit book at a TRY loss) and the fill *increased* the year's loss
  (−497 → −4,443 TRY); 10 of Y2's 12 firings were the same case, where the arm's intent ("fill the
  bracket") is not achievable at all. This is `gain_matched_units`'s documented fallback
  (`tax_budget_v1`'s implementation); the alternative reading ("no fill") would leave Y1 with 3
  firings, and the report states both counts rather than choosing silently.
- **Y3 is inert where it matters.** All 67 of its holds are state-decided, but 53 of them are
  December holds that reproduce what H2 already did (the same fill session, postponed either way),
  and only the 14 January holds are new; between them they change five decisions in 26.5 years.
  Result: −0.048 pp in the window with the most loss years, 0.0000 in the other two, and a +0.70 pp
  max-DD cost pre-tax. It passes G7/G8 (it adds no trade) and still fails A1 and A2 — a clean
  example of a rule that is *harmless and pointless*.
- **The market-side of the bootstrap says something useful about the tax model.** The unchanged
  control C0's own 2015–2026 bootstrap cell under the progressive regime (−0.286 pp median vs K,
  25 % of paths positive) versus B2-flat (−0.131, 36 %) shows how much of K's own edge is the
  progressive no-carry-forward asymmetry — the same asymmetry Y1 tries to monetise.

## Caveats

- **One history per cell**, and **no true after-tax out-of-sample**: the CBRT/Yİ-ÜFE calendar starts
  in 2000. Every after-tax number here is one path plus a fixed 2015-09 → 2026-09 bootstrap calendar.
  The deferral/step-up premium is a first-order function of the PPI-vs-FX gap, which is never varied.
- **B2-flat prices a different tax system** (20 % on the annual net realised gain with loss
  carry-forward). It is in the gate set by the pre-registration, and it is the regime in which Y1
  and Y2 have no mechanism — that is the honest reading, not a modelling artefact to be removed.
- **After-tax deltas below ~0.1 pp are inside modelling uncertainty** (`idea_survey_v4` §6.2): the
  Yİ-ÜFE month convention alone is worth 0.2–0.6 pp on some arms. Y1's +0.085 pp in the 2000 window
  and +0.211 pp in the 2010 window sit at the edge of that band; its B1-recent distribution
  (p05 −0.022) is the reason to believe the *sign* rather than the size.
- **The round trip is modelled at the decision close** on both legs, at the production 4 bps per leg
  with no spread, slippage or market-impact beyond it, and the broker is assumed to accept a
  same-session sell-and-rebuy with a fresh basis. Under real execution the fill would land at the
  next open and the realisation could fall in the *following* tax year — which destroys the
  mechanism. The 4 bps figure is the same fee the rest of the campaign uses; a wider spread makes
  Y1/Y2 worse and Y3 unchanged.
- **Not tax advice** (see the pre-registration §4.1); the model's legal premises are unverified and a
  written mali müşavir/YMM opinion is a precondition for Y1/Y2/Y4.
- **Two v1-harness divergences from its own text** were fixed here (documented in the
  pre-registration §2.2): the v1 code's Y3 window covered January only (its `dec_tail` was emptied
  whenever `defer` was false) and its state test skipped A1u's band when it passed. The text governs;
  both changes make Y3/Y4 match their description. Y1/Y2 are unaffected by either.
- **One disclosed process slip**: a single exploratory execution of the four Y arms on the main
  window at $100k/progressive was run *before* the pre-registration commit; its values are printed
  in the pre-registration §11. No arm, parameter, stage, threshold, gate or selection rule was
  changed in response (all are `tax_budget_v1`'s frozen text).
- `--harness` rewrites `research/fast_engine/PARITY.json` (the parent `parity.py` writes its own
  report); the committed copy is the tag's, and `RESULTS.json` excludes every wall-clock figure so
  two consecutive runs are byte-identical (`70d8ac28…`).

## What a production change would require

Nothing is proposed. Rejected at the gates; Y3 additionally has no after-tax benefit anywhere and a
+0.70 pp pre-tax drawdown cost, and Y1 — the only arm with a positive signal — would need a written
mali müşavir opinion for a same-session sell-and-rebuy, a higher cap (a different arm), and it only
pays in the progressive regime over horizons that contain a K-path loss year. K
(`equity_leveraged_trend_v1` 1.2.0) stays; no code, config or runtime was touched.

## Amendments

*(Any fix made after the first complete run is recorded here; arms, parameters, stages, seeds,
thresholds, gates and the selection rule are frozen in `PREREGISTRATION.md`.)*

1. **Post-run**: none of substance. Three presentation defects found while reading the first
   `--run` output were fixed *after* it and before the report — the SUMMARY's gate column order
   (`G1 G2 G3 G5 G6 G4 …` was printed under a `G1 G2 G3 G4 G5 G6 …` header), wall-clock fields
   removed from `RESULTS.json` so consecutive runs are byte-identical, and the parity subprocess's
   timing string dropped from the harness block. No gated number changed (the first run's
   `RESULTS.json` differs from the committed one only in the two timing fields and the SUMMARY's
   column order).
2. **Before the first complete run** (so not an amendment to any reported number, recorded for
   completeness): two harness bring-up bugs were fixed — `fe_oos` first took the resampled price
   series' own length as the session window instead of the fixed-calendar convention
   `[START, OOS_BOOT_L]` (which produced a NaN bootstrap), and `fe_boot`'s pure-E2 anchor call
   passed one extra `False` for the new rule arguments (a numba typing error). Both aborted the run
   before any result existed; neither touches an arm, a parameter or a gate.

## Reproduction

```bash
WK=/Users/batuhankiskac/Desktop/ParayaBuck-wt/tax_budget_v2
cd $WK
V=/Users/batuhankiskac/.venvs/pb-research/bin/python
$V -m research.tax_budget_v2.screen --smoke     # synthetic only, 29 checks
$V -m research.tax_budget_v2.screen --harness   # parity --quick + 3,094 published cells + anchors
$V -m research.tax_budget_v2.screen --run       # every stage, gates, selection; ~24 s; writes
                                                # RESULTS.json + SUMMARY.txt
```

`PREREGISTRATION.md`, `screen.py`, `DATA_MANIFEST.json` and the engine fork `fe/` were committed in
`bf1b07e1`, **before any gated arm was simulated on real data** (the disclosed exploratory run of
§11 preceded it). Two consecutive `--run` invocations produced byte-identical `RESULTS.json`
(SHA-256 `70d8ac286f670ba3e794f5cb59f6561a6c2aa434266488ead2b85d7db03e1135`); `SUMMARY.txt` differs
between them only in its wall-clock line. `docs/research/INDEX.md` was not edited.
