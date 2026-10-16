# State-keyed realization deferral with a hard drift cap (state_defer_v1, 2026-10-16)

> Evidence: `research/state_defer_v1/` (`PREREGISTRATION.md`, `screen.py`, `engine/` — the forked
> fast engine, `DATA_MANIFEST.json`, `RESULTS.json`, `SUMMARY.txt`). The pre-registration, the
> screen, the engine fork and the inputs were committed in `8a6b4a26` **before any non-control arm
> was simulated on real data**; `RESULTS.json` (SHA-256
> `a0a41dddb5993768d28c80698acb123b4b1df96cd97199ae589f127f4232dad6`) is byte-identical over two
> `--run` executions. All inputs are existing repository files (no download); the engine and the
> reference campaign are restored from tags.

**Verdict: FAIL as pre-registered. No arm passes every gate, none is selected.** Idea I3 of
`idea_survey_v5` is measured and the axis closes. **D3** — the arm with no calendar at all, which
defers a downsize whenever its estimated tax exceeds 0.05 % of NAV, capped at 21 sessions and 10 pp
of drift — is the near-miss and the only arm that is a *pre-tax* improvement on production K:
it passes **G1-G9 in full** (CAGR 15.8546 % base / 15.2667 % calibrated vs K's 15.7649 / 15.1785;
max DD +0.51 pp; 2017+ +0.20 pp; sub-periods P1/P2/P3 all above K; pre-tax bootstrap +0.024 pp
median on 53.1 %; six OOS windows and the pooled OOS bootstrap positive; turnover 0.98 × K) and
fails **only the three after-tax gates**: after tax at $100k it is **+0.044 / +0.047 / −0.070 pp**
against K on the 2000 / 2010 / 2017 windows (T1 fails on the 2017 window), the inflation-aware
B1-recent bootstrap is **−0.003 pp on 49.1 %** of paths (T2) and the flat-20 % B2 bootstrap
**−0.020 pp on 47.0 %** (T3). The pre-tax edge is real, the tax edge is not: D3 defers 72.5 m TRY of
indexed gain over 263 episodes, holds a 0.12 pp *higher* mean weight than K, pays **2.8 % more tax**
(854,216 vs 830,903 USD) and is marginally *behind* after tax in the recent window. D1, D2 and D4
fail on the pre-tax side as well (G1/G4/G6, and G5 for D1, G7 for D2/D4).

The measured verdict on the survey's mechanism: **a state-keyed deferral trigger fires plenty
(995 deferrals for D3) but the deferred tax is not a deferral — it is a deferral of *small* gains
that K's A1u band has already left on the table.** A1u already removes 91 % of the taxable trims;
what is left for a deferral device is the residue, and moving that residue a month (or into
January) is worth ±0.05 pp, not the +0.1 … +0.5 pp the survey hoped for.

## 1. Question and arms

I3 of `idea_survey_v5` (§4.3), the natural next test named by `monthly_defer_v1`'s own caveat
("*a version that deferred only when the indexed gain is positive (dropped from the arm set)*"):
defer a downsize when the **tax state** of the fill says so rather than when the calendar does, and
bound the stale weight with a **hard drift cap**. Control = production **K**; R2 (`C0`) is a
reference row. Every arm keeps A1u and the 0.5 pp band; an overlay only *suppresses* a fill.

| Arm | Rule (all parameters fixed in the pre-registration) |
|---|---|
| **C0 = K** | production K (A1u + H2's last-10-December-sessions deferral). |
| **D1** | a pending downsize filling in the last **10** sessions of any calendar month is postponed while its FIFO walk nets a **positive indexed-TRY gain**; episode cap **21** sessions; no drift cap. |
| **D2** | D1 + **10 pp drift cap** (never postpone while `held − target > 10 pp`). |
| **D3** | **no calendar**: postpone while `estimated tax > 0.05 % × NAV` (fill-year marginal bracket rate × the sale's indexed-TRY gain); episode cap 21 sessions; drift cap 10 pp. |
| **D4** | H2 with a **20-session** December window + the 10 pp drift cap (the survey's measured upper bound, H2_20, with the risk control it lacked). |

The reading of the survey's ambiguous "max 21 sessions" (the episode cap, not the tail width), the
declaration that **D1-D4 are the last arms ever run on this mechanism**, and the gate treatment are
fixed in `PREREGISTRATION.md` §2.1, §2.2 and §6. Nothing was tuned after seeing a result.

## 2. Method

- Decide on the completed close, fill at the next open; 0.5 pp band; entries and exits to zero always
  trade; idle cash zero; cost 4 bps × m × |Δw|; synthetic QLD\* = daily-reset 2× on QQQ total return,
  0.95 %/yr expense, financing DTB3 + {base 0.0 %, calibrated +0.75 %}/yr; main window 2000-03-09 →
  2026-09-29 (6,679 sessions), m = 1/2/4, base/calibrated.
- After tax: the corrected Turkish engine (per-calendar-year netting in TRY, Yİ-ÜFE indexation with
  the 10 % floor, the 2026 tariff deflated by the previous October's index, FIFO, instalments 31 Mar
  / 31 Jul funded by selling, liquidation at the window end) at $10k / $100k / $1M (gates at $100k);
  windows 2000-03-09 →, 2010-01-01 →, 2017-01-03 →.
- Bootstraps: clean B1-recent (progressive) and B2-flat (20 %) on the fixed real 2015-09 → 2026-09
  calendar, block 20, seed 20261011, 1,000 after-tax / 5,000 pre-tax paths, $100k and $1M; the pooled
  OOS bootstrap (G8) is block-20 over M1-M5, 2,000 paths per market; E3 (seed 20261015, blocks 10/40)
  and the 21-ETF block (E1) are reporting cells.
- **Engine.** The mandatory fast engine drives every simulation, tax account and bootstrap. The new
  mechanism is a **kernel option in a fork** under `research/state_defer_v1/engine/` (never an
  in-place edit of `research/fast_engine/`): `fe_sim.sim_one` gains `dfr_trig` / `dfr_state` /
  `dfr_max` / `dfr_drift` / `dfr_ufe_lag` plus the month-tail, December-tail, month-opening and M−2
  Yİ-UFE tables; `fe_boot._boot_chunk` passes it; `fe_tax` exports its tariff arrays; `fe_study` gains
  the pooled OOS bootstrap. With `dfr_trig == 0` the fork is **bit-identical** to the shared engine —
  the harness's option-off parity cell is `max|diff| = 0.000e+00` over 3,616 cells (four engine arms ×
  three modes × two capitals × two cost levels, NAV path, decision trace and every numeric leaf of the
  returned dict), and `python -m research.fast_engine.parity --quick` **PASSES**.
- The state is computed with indexation **on in every mode**, so the pre-tax leg prices the *same*
  decision series tax-free. D3's threshold is NAV-relative, so its decisions are a function of the
  account's NAV and differ slightly between the pre-tax and tax-charging runs (disclosed in the
  pre-registration; measured: 995 deferrals pre-tax, 995 in the progressive $100k run).

## 3. Harness (passed before `--run`: 71 checks, 0 outside tolerance)

K and R2 reproduce their published pre-tax anchors (15.7649 / 15.1785 % and 15.4766 / 14.8961 % CAGR,
max DD 46.6698 / 46.7948 % and 46.7584 / 46.8797 %) **and** every corresponding cell of the restored
`band_h2_combo_v1/RESULTS.json` (m = 1/2/4 × both financings, including the deferral counter) within
0.01 pp; the after-tax $100k anchors reproduce (K 10.753 / 15.235 / 23.972 %, R2 10.160 / 14.706 /
22.886 %); H2's own deferral counts reproduce exactly (51 pre-tax, 53 / 32 / 19); the option-off
parity cell is exactly zero; and the mechanism invariants all hold — events == counter, every event a
genuine downsize, D1/D2 events inside the month tail, D4 inside the 20-session December tail,
episode starts within the drift cap, episode lengths within the cap, sells ≤ K's, and D1 defers on
73 of K's 71 A1u-vs-K deferral sessions (it covers them). The published R2 January-gap episodes are
reproduced from the fork (−0.699 pp for 2015-12→2016-01, −0.318 pp for 2024-12→2025-01, exactly 0.000
for 2018, with the December-first-session → January-last-session, H2 − R2 convention).

## 4. Pre-tax main window (m = 2, `turn` and `sells` per year)

| Arm | CAGR base | CAGR cal. | max DD base | max DD cal. | m = 1 base | 2017+ base | P1 / P2 / P3 | turn/yr | sells/yr | deferrals | band blocks |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| **C0 = R2** | 15.4766 % | 14.8961 % | 46.7584 % | 46.8797 % | 15.7538 % | 32.7139 % | −0.06 / 14.30 / 32.71 | 5.9915 | 29.56 | 0 | 0 |
| **K (control)** | 15.7649 % | 15.1785 % | 46.6698 % | 46.7948 % | 16.0173 % | 33.2306 % | +0.29 / 14.27 / 33.23 | 5.4561 | 10.17 | 71 | 1,239 |
| D1 | 15.7322 % | 15.1434 % | 47.5238 % | 47.6451 % | 15.9794 % | 32.7864 % | +0.28 / 14.63 / 32.79 | 5.3309 | 8.92 | 792 | 623 |
| D2 | 15.7366 % | 15.1500 % | 47.5238 % | 47.6451 % | 15.9877 % | 33.1337 % | +0.10 / 14.51 / 33.13 | 5.4151 | 9.49 | 710 | 641 |
| **D3** | **15.8546 %** | **15.2667 %** | 47.1824 % | 47.3047 % | **16.1030 %** | **33.4302 %** | +0.13 / 14.55 / 33.43 | 5.3523 | 9.04 | 995 | 456 |
| D4 | 15.7241 % | 15.1382 % | 47.1972 % | 47.3192 % | 15.9768 % | 33.1332 % | +0.12 / 14.45 / 33.13 | 5.4627 | 10.05 | 112 | 1,194 |

At m = 4 the ordering is unchanged (D3 +0.045 / +0.020 / −0.071 pp vs K after tax). D3 is the only
arm that improves the pre-tax CAGR **and** the 2017+ leg **and** two of the three sub-periods (P2
14.55 vs K's 14.27, P3 33.43 vs 33.23; P1 is 0.13 against K's 0.29, which is why G4 passes on 2 of 3
and not 3), and it does so with *lower* turnover (0.98 × K) and fewer sells (9.04 vs 10.17/yr) — but
with **+0.51 pp of max DD**, which is inside G2's +1 pp only because K's own drawdown is low.

## 5. After tax, progressive 2026 tariff, liquidated, base financing, m = 2

| Window | Arm | pre-tax | after tax | Δ vs K | Δ vs R2 | max DD | tax USD | sells/yr | deferrals |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2000-03 → 2026-09 | K | 15.7649 % | **10.7533 %** | — | +0.593 | 46.670 % | 830,903 | 10.66 | 81 |
| | D1 | 15.7322 % | 10.8052 % | **+0.052** | +0.645 | 47.524 % | 836,751 | 9.41 | 1,053 |
| | D2 | 15.7366 % | 10.7107 % | −0.043 | +0.551 | 47.524 % | 832,737 | 10.02 | 971 |
| | D3 | 15.8546 % | 10.7977 % | **+0.044** | +0.638 | 47.182 % | 854,216 | 9.64 | 1,522 |
| | D4 | 15.7241 % | 10.6476 % | −0.106 | +0.488 | 47.197 % | 828,300 | 10.54 | 132 |
| 2010-01 → end | K | 22.3435 % | **15.2354 %** | — | +0.530 | 44.63 % | 562,873 | — | — |
| | D1 | 22.1988 % | 15.1559 % | −0.080 | +0.450 | 44.55 % | 552,554 | — | — |
| | D2 | 22.3633 % | 15.1762 % | −0.059 | +0.470 | 44.15 % | 565,623 | — | — |
| | D3 | 22.5444 % | 15.2828 % | **+0.047** | +0.577 | 43.98 % | 578,308 | — | — |
| | D4 | 22.3834 % | 15.1156 % | −0.120 | +0.410 | 44.00 % | 566,397 | — | — |
| 2017-01 → end | K | 33.0215 % | **23.9720 %** | — | +1.086 | 34.98 % | 371,487 | — | — |
| | D1 | 32.5780 % | 23.6774 % | −0.295 | +0.791 | 34.69 % | 358,665 | — | — |
| | D2 | 32.9247 % | 23.8139 % | −0.158 | +0.928 | 34.75 % | 371,099 | — | — |
| | D3 | 33.2195 % | 23.9018 % | **−0.070** | +1.016 | 36.14 % | 377,546 | — | — |
| | D4 | 32.9243 % | 23.6345 % | −0.338 | +0.748 | 36.21 % | 369,726 | — | — |

At $10k the D3 deltas are +0.008 / +0.013 / −0.126 pp and at $1M +0.087 / +0.100 / +0.026 pp — so
D3's T1 failure is not a capital artefact: the 2017 window is behind K at $100k and $10k and only
+0.026 pp ahead at $1M.

**D4 vs the survey's bound.** The survey quoted H2_20 (th2's 20-session December window, *without*
A1u and without a drift cap) at +0.332 / +0.437 / +0.769 pp after tax at $100k **vs R2**. D4 with A1u
and the drift cap measures +0.488 / +0.410 / +0.748 pp **vs R2** — the same order — but against K,
which is A1u + H2_10, it is −0.106 / −0.120 / −0.338 pp. **The drift cap is not the fix H2_20 needed;
it is a tax cost**: it forces the fill back at 10 pp of drift exactly where the wider December window
was buying the deferral, and K's narrow 10-session window with no cap keeps the whole +0.59 / +0.53 /
+1.09 pp edge over R2.

## 6. Clean bootstraps ($100k, seed 20261011, block 20, 1,000 paths)

| Mode | Arm | median CAGR | Δ vs K median | share > 0 | p05 / p95 |
|---|---|---:|---:|---:|---|
| B1-recent | K | 3.8577 % | — | — | — |
| | D1 | 3.7110 % | −0.001 pp | 49.8 % | −1.31 / +1.19 |
| | D2 | 3.7523 % | −0.025 pp | 46.4 % | −0.55 / +0.41 |
| | D3 | 3.8081 % | **−0.003 pp** | **49.1 %** | −0.59 / +0.53 |
| | D4 | 3.8098 % | −0.032 pp | 37.4 % | −0.55 / +0.30 |
| B2-flat | K | 4.0376 % | — | — | — |
| | D1 | 3.9270 % | −0.124 pp | 43.3 % | −1.52 / +1.11 |
| | D2 | 4.0510 % | −0.036 pp | 44.0 % | −0.54 / +0.44 |
| | D3 | 4.0803 % | **−0.020 pp** | **47.0 %** | −0.58 / +0.54 |
| | D4 | 4.0365 % | −0.025 pp | 39.0 % | −0.48 / +0.37 |

Pre-tax (5,000 ten-year paths, median Δ base / calibrated): D1 −0.168 / −0.171, D2 +0.011 / +0.011,
D3 **+0.024 / +0.024** (53.1 %), D4 0.000 / 0.000. So the *pre-tax* bootstrap says D3 is a tiny but
genuine improvement, and both after-tax bootstraps say it is not one (medians within ±0.02 pp, shares
below 50 %). That split is the whole study in one line: the device moves the tax, and the tax it
moves is the residue A1u left behind.

## 7. Out of sample and reporting cells

| Arm | GSPC 1957-99 | M1 | M2 | M3 | M4 | M5 | pooled OOS (G8) | E1 21 ETFs median Δ / worst ΔDD | E2 21 January starts | E3 (b10 prog / b10 flat / b40 flat / b40 prog) |
|---|---:|---:|---:|---:|---:|---:|---|---|---|---|
| D1 | +0.04/+0.00 | +0.53/+0.00 | +0.23/+0.18 | +0.12/+0.64 | −0.33/+2.33 | +0.01/+0.16 | +0.062 pp (56.7 %) | −0.035 / +3.19 pp (20/21 ≤ 3) | −0.078 (38.1 %) | −0.070/−0.222/−0.013/+0.093 |
| D2 | 0.00/0.00 | +0.12/0.00 | −0.09/+0.49 | −0.04/+0.23 | +0.02/−0.27 | −0.02/+0.06 | +0.002 pp (51.0 %) | +0.007 / +1.14 pp | −0.059 (28.6 %) | −0.029/−0.027/−0.022/−0.006 |
| D3 | +0.02/0.00 | 0.00/0.00 | +0.06/−0.40 | −0.05/−0.06 | −0.12/+1.29 | +0.02/−0.09 | +0.012 pp (53.3 %) | +0.003 / +1.19 pp | **+0.050 (71.4 %)** | −0.017/−0.027/+0.019/+0.035 |
| D4 | 0.00/0.00 | −0.07/0.00 | −0.05/+0.13 | −0.03/+0.33 | −0.06/+0.06 | 0.00/−0.11 | 0.000 pp (36.9 %) | −0.012 / +1.68 pp | −0.128 (0 %) | −0.024/−0.021/−0.018/−0.019 |

(D1/D2/D4 fail G7 on the median or the M4/M1 drawdown; D3 passes G7 — six windows, median +0.007 pp,
all six within −0.5 pp and +3 pp.) The E1/E2/E3 blocks agree with the main result: D3 is the only arm
that is ever majority-positive (E2 71.4 % of the 21 January starts, E3 +0.019/+0.035 pp in the
block-40 runs), and even it is below 55 % in three of the four E3 runs. D4's E2 record is 0/21 —
widening the December window with a drift cap makes every rolling January start worse.

## 8. What the mechanism actually did (mandatory reporting)

| Arm | deferrals | episodes | max episode | deferred indexed gain (TRY, per event / per episode) | event drift max / p95 | path max `held−target` | mean weight | mean abs. gap | sells/yr | tax USD | index relief TRY |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| K | 71 | — | — | — | — | — | 0.6787 | 0.0276 | 10.17 | 830,903 | 22.61 m |
| D1 | 792 | 188 | 9 | 393.6 m / **68.0 m** | 0.4797 / 0.1404 | 0.9827 | 0.6818 | 0.0302 | 8.92 | 836,751 | 23.04 m |
| D2 | 710 | 202 | 9 | 202.1 m / **52.7 m** | 0.1000 / 0.0850 | 0.9827 | 0.6792 | 0.0279 | 9.49 | 832,737 | 22.39 m |
| D3 | 995 | 263 | 20 | 413.1 m / **72.5 m** | 0.1000 / 0.0853 | 0.9827 | 0.6799 | 0.0284 | 9.04 | 854,216 | 23.08 m |
| D4 | 112 | 26 | 19 | 34.8 m / **6.0 m** | 0.0985 / 0.0827 | 0.9827 | 0.6785 | 0.0274 | 10.05 | 828,300 | 22.02 m |

(K's H2 device has no fork episode/gain counter — its 71 deferrals are H2's own counter, so those
columns are empty for K. The "per event" gain sums the same pending sale at every re-evaluation and
is therefore inflated; the **per-episode** number is the honest "deferred indexed gain".)

- **The state fires.** D3's trigger — the tax state with no calendar in it — fires 995 times, 263
  episodes, the longest exactly at the 21-session cap, on 2.4 % of sessions (160 of 6,679 decisions
  differ from K's). The survey's risk (i) ("the state never fires") was wrong for D3 and right for D4.
- **The cap binds where it should.** D1/D2's episodes end with the month (max 9 sessions, the cap of
  21 never reached); D3's reach 20 (the cap); D4's reach 19 (the 20-session December window).
- **The drift cap is binding and visible.** Without it (D1) the deferral holds up to **+0.48 of NAV
  above target** at an event and up to 0.98 on the path; with it (D2/D3/D4) the decision-time maximum
  is exactly 0.10. That is what removes D1's pre-tax bootstrap failure (G5 −0.168 → +0.011 for D2)
  and what costs D2/D3/D4 their tax edge.
- **Deferrals by year (D3):** 2003 94, 2007 55, 2009 37, 2010 29, 2011 25, 2012 33, 2013 21,
  2014 87, 2015 20, **2017 94, 2018 57, 2019 34, 2020 94**, 2021 60, 2022 28, 2023 35, 2024 65,
  2025 54, 2026 50 (and 1-8/yr in 2000-2002, 2004, 2006). K's H2 fires only in December and mostly in
  2001-2013 (5, 3, 5, 8, 2, 10, 9, 10, 10, 8, 1).
- **Tax paid rises, not falls.** D3's realised gain by reason is resize 56.1 m, liquidation 11.9 m,
  tax-funding 0.87 m TRY against K's 58.3 / 9.1 / 0.57 m: the deferral moves the same gain later and
  makes the surviving units older, exactly as `band_h2_combo_v1` found for the band itself. D3 pays
  2.8 % *more* nominal tax and ends 0.04 pp/ahead in two windows and 0.07 pp behind in the third.
- **The January-gap episodes are not the problem here.** Δ vs K in the December-first → January-last
  window: 2016 (the −0.699 pp episode on R2) D1 −0.010, D2 +1.020, D3 +1.251, D4 +1.251; 2019 all
  0.000 (R2 flat); 2025 (R2 −0.318 pp) D1 +0.256, D2 +0.337, D3 +0.436, D4 +0.123. The D arms do not
  inherit H2's gap risk; they *avoid* part of it.
- **Yİ-ÜFE month convention (report-only sensitivity).** D3 recomputed with the M−2 index in the first
  three sessions of each month (where the M−1 index is not yet published) gives 998 instead of 995
  deferrals and an identical after-tax CAGR on the 2000 window (0.107977 both). The convention is
  immaterial here, and D1/D2/D4's triggers (a month tail, a December tail) are outside the window
  where it could matter at all.
- **Exposure is nearly neutral.** Mean held weight 0.6799 (D3) vs 0.6787 (K) — +0.12 pp — and mean
  |target − held| 0.0284 vs 0.0276. The arms are not exposure bets; G1/G2 were applied as written
  anyway, per the pre-registration.

## 9. Gates

All keys must pass. G1-G9 as `ewma_band_v1` defines them, with K as the control; T1 = after tax at
$100k strictly beats K in all three windows; T2/T3 = B1-recent/B2-flat $100k median Δ ≥ 0 with
share > 0 ≥ 55 %.

| Arm | G1 | G2 | G3 | G4 | G5 | G6 | G7 | G8 | G9 | T1 | T2 | T3 | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| D1 | **F** | P | P | **F** | **F** | **F** | P | P | P | **F** | **F** | **F** | **FAIL** |
| D2 | **F** | P | P | **F** | P | **F** | **F** | P | P | **F** | **F** | **F** | **FAIL** |
| **D3** | P | P | P | P | P | P | P | P | P | **F** | **F** | **F** | **FAIL** |
| D4 | **F** | P | P | **F** | P | **F** | **F** | P | P | **F** | **F** | **F** | **FAIL** |
| C0 = R2 (reference) | F | P | P | F | P | F | F | **F** | P | F | F | F | FAIL |

Failed details. D1: G1 (15.7322 vs 15.7649 base, Calmar 0.3310 vs K's 0.3378), G4 (only P2 above K), G5 (−0.168 / −0.171 pp), G6, T1 (2010 −0.080, 2017 −0.295), T2 (−0.001 pp, 49.8 %),
T3 (−0.124 pp, 43.3 %). D2: G1, G4, G6, G7 (median −0.010 pp), T1 (all three negative), T2, T3.
**D3: T1 only** (2017 −0.070 pp; 2000 +0.044, 2010 +0.047), T2 (−0.003 pp, 49.1 %), T3
(−0.020 pp, 47.0 %) — 16 of 19 keys pass, and the three that fail are the three after-tax gates.
D4: G1, G4, G6, G7, T1, T2, T3. **Selected arm: none.**

## 10. Verdict and reading

1. **The axis closes, and it closes for a structural reason, not a tuning one.** A1u removes 91 % of
   the taxable resizes; a deferral device therefore only ever sees the residue, and the residue's tax
   is worth ±0.05 pp. D3 is the cleanest possible demonstration: it fires 995 times, defers 72.5 m TRY
   of indexed gain, improves the pre-tax CAGR by +0.09 pp and the 2017+ leg by +0.20 pp, passes every
   pre-tax gate — and lands **−0.003 pp** on the deciding bootstrap and **−0.070 pp** in the 2017
   window after tax. That is `idea_survey_v5` bound C's arithmetic in practice: the whole
   realization-timing family is worth ~+1.6 pp/yr if *every* realization could be moved to the
   liquidation, and the reachable part of it is already spent.
2. **The hard drift cap is the wrong instrument for this family.** It works exactly as designed —
   D1's stale weight reaches 0.48 of NAV above target and its G5 fails at −0.168 pp; the cap removes
   that — but every capped arm (D2/D3/D4) then loses its after-tax edge, and D4's widening of H2's
   window (the survey's own upper bound) becomes **worse than K's narrow window** (+0.49/+0.41/+0.75
   pp vs R2 against K's +0.59/+0.53/+1.09 pp). The device that pays for deferral is *patience*, and
   the pre-registered risk control is what pays it back.
3. **The calendar is not the binding constraint; the state is too permissive.** D1/D2 (month tail +
   positive indexed gain) and D3 (tax state, no calendar) are different rules with different firing
   sets, yet D2 and D3 land within 0.04 pp of each other after tax and both land below K. Replacing
   the December window with the tax state changed *when* the deferral happens, not *whether* it pays.
4. **D3's pre-tax profile is genuinely attractive and should be recorded as such** (CAGR +0.09 pp,
   2017+ +0.20 pp, turnover 0.98 × K, 21/21 ETFs within +3 pp DD, 71 % of January starts positive,
   pooled OOS +0.012 pp). It is a *pre-tax* improvement on production K; it simply is not an
   *after-tax* one, and for a Turkish-resident owner that is the only verdict that counts.
5. **A null result here is the expected result.** The survey priced I3 at +0.1 … +0.5 pp and said the
   most likely outcome was "the state never fires" or "the cap makes it H2 again". Measured: the state
   fires, the cap does not make it H2, and the value is 0.00 ± 0.03 pp. The survey's own ordering
   (I1 first, then I2, then I3) stands, with I3 now closed.

## 11. Caveats

- **One history per cell.** The after-tax window deltas are single paths; the counterweight is the
  1,000-path bootstrap, whose D3 medians are −0.003 pp (B1) and −0.020 pp (B2) with p05/p95 of
  −0.59/+0.53 and −0.58/+0.54 — i.e. the sign of the after-tax verdict is genuinely uncertain, and
  the *gate* (median ≥ 0 with share ≥ 55 %) is what it fails, not a large negative effect.
- **No true after-tax out-of-sample**: the TRY/Yİ-ÜFE calendar starts 2000, so the after-tax stages
  cannot reach the pre-2000 OOS windows; E2's rolling starts and E3's seed/block variations are the
  substitutes, and D3 is positive in E2 (71.4 %) but below the 55 % floor in three of four E3 runs.
- **The tax model is not tax advice**: FIFO, per-calendar-year netting without loss carry-forward,
  the 2026 tariff deflated by the previous October's Yİ-ÜFE, a 10 % indexation floor, instalments
  funded by selling, liquidation at the window end, one CBRT buying-rate series, and a monthly Yİ-ÜFE
  step with the M−1 convention (sensitivity reported). D3's NAV-relative threshold makes its decision
  a function of the account's own NAV, so its pre-tax leg and its tax-charging legs are two
  self-consistent but not identical decision series (disclosed in the pre-registration): 995 deferrals
  on the tax-free account against **1,522** on the progressive $100k account (a lower NAV makes the
  0.05 % threshold easier to clear). The *calendar*- and *gain*-keyed arms (D1/D2/D4) are
  mode-independent.
- **The "per event" deferred-gain column double-counts** a pending sale that is re-evaluated at each
  close; the per-episode column is the honest aggregate, and both are reported.
- **D1's reading of the survey's "max 21 sessions"** (the episode cap, not the tail width) is fixed in
  `PREREGISTRATION.md` §2.1; with a 10-session tail the cap never binds for D1/D2, and the report says
  so rather than pretending otherwise.
- **Not a repeat of** `monthly_defer_v1` (its A1/A2/A3 keyed the *month*; its dropped arm is D1, run
  here for the first time), `tax_harvest_v2`/`band_h2_combo_v1` (H2's fixed 10-session December window
  is the control), `gain_band_v1-v3` (a wider band, not a fill-date device), or `exit_timing_v2`.

## 12. Amendments and honesty notes

**A1. Pre-commit fixes (all before `8a6b4a26`; none touches an arm, parameter, threshold, gate,
window or selection rule).** (i) The fork's `fe_config.ROOT` needed `parents[3]` (the fork sits one
level deeper than `research/fast_engine/`). (ii) `_boot_chunk`'s argument order for the new kernel
parameters was wrong (numba typing error). (iii) The fork's deferral block double-counted
`postponements`/`deferred`/`deferrals_by_year` — the shared reporting block already counts a
`blocked == 1` postponement; found by `--smoke` on synthetic prices, and it changed *counters*, never
a fill decision. (iv) A scal-slot index (`SC_DEFEP`/`N_SCAL`) was inconsistent. (v) The fork's
`stage_e3` derived its delta cells from a hard-coded candidate list. **Before the pre-registration
commit, no non-control arm was ever simulated on real data**: the real-data work in that period was
the shared engine's own `parity --quick`, the control-only anchor checks (K, R2, H2) and a
control-only probe of every stage function with deliberately tiny bootstrap path counts
(40/12/12 instead of 5,000/1,000/1,000). `--smoke` uses synthetic random-walk prices only.

**A2. Post-commit fixes (all before the first `--run`; again none touches an arm, parameter,
threshold, gate, window or selection rule).** (i) The harness's 0.01 pp tolerance was mis-scaled by
100×. (ii) The harness's fork-reproduction block fed the base-financing target series to the
calibrated rows. (iii) The January-gap definition: the published `tax_harvest_v2` convention was
recovered by search over endpoint conventions and is December's **first** session → January's
**last** session, H2 − R2 (it reproduces −0.699 / 0.000 / −0.318 pp exactly). (iv) Two wall-clock
fields were removed from `RESULTS.json` (they made two runs differ) — the wall clock is reported in
`SUMMARY.txt` instead. (v) Two reporting scalars demanded by the pre-registration §6
(`mean_held_weight`, `mean_abs_target_gap`) were added to the kernel as **new output keys only**; the
option-off parity cell was re-run afterwards and is still exactly 0.

**A3. Run log.** `--smoke`, `--parity` and `--harness` all PASS; `--run` executed twice, byte-identical
`RESULTS.json` (`a0a41dddb5993768d28c80698acb123b4b1df96cd97199ae589f127f4232dad6`), wall clock
28.9 s / 32.7 s (SUMMARY.txt reports the last run's). No gate, threshold, arm, parameter or selection
rule was changed after any stage result was read.

**A4. `--harness` rewrites `research/fast_engine/PARITY.json`.** The mandatory check
`python -m research.fast_engine.parity --quick` writes its own `PARITY.json` next to the shared
engine; because it runs in `--quick` mode the file it writes is shorter than the tagged one. The
shared engine itself is **unmodified** in this worktree, and the tagged `PARITY.json` was restored
before committing, so the recorded tree matches tag `archive/research/fast-engine-v1-2026-10`. A
reader who re-runs `--harness` will see that file change again; it is the engine's own artifact, not
this study's.

Not tax advice. The model caveats of `vt_level_tax_v1` / `tax_harvest_v2` / `monthly_defer_v1` apply
verbatim.
