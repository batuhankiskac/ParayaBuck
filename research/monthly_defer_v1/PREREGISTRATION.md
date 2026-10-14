# monthly_defer_v1 — defer every downward vol resize across the Yi-UFE month boundary (pre-registration)

Committed **before** any non-control arm was simulated on real data. Everything below is frozen:
question, arms, parameters, data, mechanics, gates, selection rule. Bug fixes or later changes go in
*Amendments* at the end.

## Question

Idea **I2** of `research/idea_survey_v2` (`docs/research/idea_survey_v2.md`, section 3).

Turkish tax (GVK mük. 81) rescales a lot's TRY cost basis by
`Yi-UFE(month before sale) / Yi-UFE(month before purchase)` when the index rose >= 10 %. The
numerator therefore **steps once per calendar month**: a sale whose fill lands in month `M+1`
instead of month `M` receives one more month of PPI applied to the whole indexed cost basis of
every lot it consumes, and (in December) also moves the realisation into the next tax year.
`tax_harvest_v1/v2` proved this mechanism for the one boundary tested so far — H2, which postpones a
downward vol resize filling in the last **10** December sessions: **+0.142 / +0.128 / +0.266 pp**
after tax at $100k on the 2000 / 2010 / 2017 windows, monotone in the postponement length
(+0.332 / +0.437 / +0.769 pp at 20 sessions), at a small drawdown cost. There are **12** month
boundaries per year and no study has used the other 11.

1. **Does the month-boundary version work / how big is it?** Defer a downward vol resize whose fill
   falls in the last `N` sessions of **any** calendar month. Compared with H2 it has ~12x the events
   but only ~1/12 of the year-boundary benefit (no tax-year move except in December).
2. **What does the deferral cost in drawdown?** Holding a stale (too high) weight is the risk this
   overlay buys; the survey flags mid-month sell-offs (2001, 2008, 2020 started mid-month) and the
   pre-registered ETF drawdown gate below is the one that decides the idea.

Not tax advice; the model caveats of `vt_level_tax_v1` / `tax_harvest_v2` apply verbatim.

## Control and arms

- **Control C0** = production `equity_leveraged_trend_v1` 1.2.0 (= **R2**): at least 2 of 3 votes on
  the QQQ **price** close (close > SMA200; 252-session price return > 0; close > the 126-session
  max/min midpoint), sized `vt = min(1, 0.35 / vol20)` on synthetic QLD* (daily-reset 2x on QQQ
  total return), total cap 0.98, 0.5 pp resize band; while the E2 vote is off, `0.5 x vt` if QQQ
  close > SMA50, QQQ vol20 < its trailing 252-session median and close > close 20 sessions ago,
  exited below SMA50; E2's own vote overrides.
- **4 arms = control + 3 candidates.** Every arm shares C0's target series **bit for bit**; only
  the execution layer differs. An overlay can only *suppress* a trade; it never creates one.
  - **A1 — month-tail 10.** A downward resize whose **fill** session is one of the last **10**
    sessions of its calendar month is suppressed. Applies to all 12 months.
  - **A2 — month-tail 21.** The same with **21** sessions. A month has 20-23 sessions, so for a
    month with `m <= 21` sessions the whole month is in the window (equivalently: a downward resize
    decided on any close from the first session of the month on is pushed to the next month). This
    is the survey's literal parameterisation ("max 21 sessions", mean deferral ~10 sessions).
  - **A3 — A1 + hard override.** A1, except that the fill executes immediately when
    `held - target > 10 pp` **or** on the fill session that immediately follows a close at which
    the E2 vote turned off (the survey's A4 tail protection).
- **Anchor (not a candidate, harness only): H2** = `tax_harvest_v2`'s published arm: the same
  suppression restricted to the last 10 December sessions. It is re-run here purely as a
  reproduction check of the overlay machinery against a published independent result; no gate is
  decided on it.
- **Downward-resize test** (identical to `tax_harvest_v2`): at the fill's open,
  `0 < target < held weight` (the unit delta is negative and the target is positive). Exits
  (`target = 0`) and up-resizes always execute in full and are never suppressed.
- **A suppressed fill is not re-tried.** The ordinary band rule is re-evaluated at every subsequent
  close, so the trade happens at the first close after the window whose breach still exceeds the
  band, filled at the next open. If the target has recovered, no trade happens at all.

### Pre-registration decisions on the survey's ambiguous points

1. **Survey A1 says "a downward vol resize whose fill would fall in month M is postponed to the
   first session of M+1 (max 21 sessions) and filled at that open"** — a *forced* fill at the first
   open of the next month. Resolved to the **suppression** form above. Reasons: (a) it is H2's
   proven mechanism, which produced the only measured Turkish indexation-deferral number in this
   repository, whereas the forced-fill variant is untested and would *create* sales (a tax cost H2
   never paid) whenever the target has recovered by the month end; (b) the two forms coincide
   whenever the target is still below the held weight at the month's last close — the only
   difference is that suppression avoids the unnecessary sales.
2. **Window definition.** "The last N sessions of a month" = the last `min(N, m)` sessions of that
   month's own session list, per simulated window (main window, each OOS window, each instrument,
   each bootstrap path). At `N = 21` a month with 21 or fewer sessions is fully covered.
3. **Survey A2** ("only when the deferred sale would net an indexed TRY gain > 0") is **dropped**
   to keep the arm count at 4. Note `tax_harvest_v2` measured that 91 % of R2's realised taxable
   gain comes from the trims this overlay targets, so A2 would be near-identical to A1 while
   requiring knowledge of the gain at decision time (a lottery on which lot the sale would consume,
   since the sale may not happen at all).
4. **Bootstrap calendars.** For the *pre-tax* bootstraps (gates G5, G8) a resampled position keeps
   the **calendar date of its source session**, so the month window of a drawn position is that of
   the source session (blocks are contiguous in the source, so the month structure inside a block is
   real). The *after-tax* bootstrap uses the fixed real 2015-09 → 2026-09 calendar (as
   `tax_asym_v2` / `vt_level_tax_v1` do), so its month windows are that calendar's.

## Data

No download: every input already exists in the repository (SHA-256 in `DATA_MANIFEST.json`).

- `data/historical/stress_robustness_v1_daily.csv` (QQQ/SPY/QLD open/close/dividend),
  `data/historical/stress_robustness_v1_dtb3.csv` (DTB3),
  `data/historical/after_tax_tr_v1_usdtry_cbrt.csv` (USD/TRY CBRT buying),
  `data/historical/after_tax_tr_v1_yiufe.csv` (Yi-UFE monthly) — restored from
  `research/vt_level_tax_v1`.
- `data/historical/ewma_band_v1_{gspc,index,tb3ms}.csv` — the six `ewma_band_v1` out-of-sample
  windows (^GSPC 1957-1999 + `e2_confirmation_v1`'s M1-M5), restored from
  `archive/research/ewma-band-v1-2026-10`.
- `data/historical/range_vol_v1_{dia,iwm,spy}.csv` and `range_vol_v2_*.csv` — the independent
  instruments; `EWC` is dropped by `range_vol_v2`'s fixed quality rule (> 5 % invalid/flat bars)
  **before** simulation, leaving **21**.
- The Yİ-ÜFE series is monthly and starts 2003-01 (pre-2003: the OECD/FRED Turkish manufacturing
  PPI proxy carried by `after_tax_tr_v1_yiufe.csv`); this is the same limitation every after-tax
  study here has and it applies to the control identically.

## Mechanics (identical in every arm)

- Decide on the completed close, fill at the next open; 0.5 pp resize band (`trv2.BAND`); entries
  from flat and exits to zero always trade in full; idle cash earns zero; cost
  `4 bps x m x |delta w|` per traded unit.
- Synthetic QLD* = daily-reset 2x on QQQ total return, expense 0.95 %/yr, financing DTB3 +
  {base 0.0, calibrated +0.75 %}/yr. Pre-tax at m = 1 and m = 2 in both financings; the after-tax
  stage is base financing at m = 2.
- Main window 2000-03-09 → 2026-09-29 (6,679 sessions, start index 253); sub-periods
  P1 <= 2008-12-31, P2 2009-2016, P3 2017 → end (the `fast_reentry_v1` split).
- **Fill session** = the session whose open executes a trade decided at the previous close. The
  overlay tests that session's calendar month.

## After-tax model

`research/monthly_defer_v1/tax_engine.py`, byte copy of `research/tax_harvest_v2/tax_engine.py`
(itself the byte-faithful copy of the corrected `vt_level_tax_v1` / `r2_tbill_combo_v1` engine).
Model, unchanged: realised gains/losses netted **per calendar year** (progressive regime taxes
`max(0, net)+dividends`, no loss carry-forward; the flat-20 % bootstrap variant carries losses
forward); **trade date** determines the tax year; gains in **TRY** at the one CBRT buying-rate
series; Yi-UFE indexation of the acquisition cost when the index rose >= 10 % (indexation floor,
`prev_month(sale) / prev_month(buy)`); 2026 tariff scaled by the previous October's Yi-UFE
(15/20/27/35/40 % brackets); instalments Mar 31 and Jul 31 of the following year, funded by
selling the position if cash is short; FIFO; liquidation at the window end. Windows:
2000-03-09 → 2026-09-29, 2010-01-01 → 2026-09-29, 2017-01-03 → 2026-09-29; capitals
**$10,000 / $100,000 / $1,000,000** (gates at $100k); after-tax CAGR on the **liquidated** terminal
USD value.

**Clean after-tax bootstrap** (`tax_asym_v2` / `vt_level_tax_v1` design): stationary block-20, seed
20261011, **1,000** paths of 10 measured years (2,520 sessions + 3 back-extension, warm-up 253)
drawn from QQQ open/close/dividend and DTB3; signals recomputed on the resampled price level; the
**calendar stays real and fixed** (2015-09-01 → 2026-09-30 with its realised USD/TRY and Yi-UFE).
Variants: **B1-recent** (progressive) and **B2** (20 % flat), at $100k and $1M (gate at $100k).

## Gates

Pre-tax, arm vs **C0 = R2**, exactly as `ewma_band_v1` / `fast_reentry_v1` define them ("both" =
base and calibrated financing):

- **G1 (m = 2, both).** CAGR >= C0, or Calmar >= 1.10 x C0 with CAGR >= C0 - 0.5 pp.
- **G2 (both).** max DD <= C0 + 1 pp.
- **G3 (both).** 2017+ CAGR >= C0 - 1 pp.
- **G4.** CAGR above C0 in >= 2 of the 3 sub-periods (base, m = 2).
- **G5 (both).** block-20 stationary bootstrap (seed 20261011, 5,000 ten-year paths, draws identical
  to `signal_ensemble_v1` / `fast_reentry_v1`) median of arm - C0 >= 0.
- **G6 (both).** G1 at m = 1.
- **G7.** six out-of-sample windows (^GSPC 1957-1999, M1 ^IXIC, M2 ^GSPC 1935-1956, M3 ^N225,
  M4 ^GDAXI, M5 ^FTSE), calibrated, m = 2: median Δ CAGR >= 0; Δ CAGR >= -0.5 pp in >= 5 of 6;
  Δ DD <= +3 pp in >= 5 of 6 and never > +8 pp. (Δ = arm - C0.)
- **G8.** pooled bootstrap over M1-M5 (2,000 paths per market) median Δ >= 0.
- **G9.** turnover/yr <= 2 x C0 (base, m = 2).
- **G10 (this task: drawdown on other instruments, because the overlay changes exposure).** Pre-tax,
  calibrated, m = 2, on the **21** quality-passing equity ETFs (each with its own E2+R2 logic on its
  own adjusted close and its own synthetic 2x leg): the share of instruments with
  **Δ max DD <= +3 pp must be >= 80 %** (>= 17 of 21). Median Δ CAGR and share Δ CAGR > 0 reported.

After tax, arm vs C0 = R2 at $100k, base financing, m = 2, progressive tariff, liquidated:

- **T1.** after-tax CAGR strictly greater than C0's in **all three** windows.
- **T2 (B1-recent).** bootstrap progressive median Δ CAGR >= 0 **and** share of paths with Δ > 0
  >= 55 %.
- **T3 (B2).** bootstrap flat-20 % median Δ CAGR >= 0 **and** share of paths with Δ > 0 >= 55 %.

**Verdict per arm:** PASS iff G1-G10 and T1-T3 all pass. **Selection:** the passing arm with the
highest base m = 2 pre-tax CAGR; if none passes, the verdict is FAIL and no arm is selected.
Reported, not gated: the $10k/$1M after-tax tables, deferral counts, the worst per-year
month-boundary episodes, the m = 4 cost stress, and the Yi-UFE months in which the deferred
numerator actually *fell* (a month-on-month PPI decline makes the deferral strictly harmful for
those lots).

## Verification plan

- `--smoke` (synthetic prices, no market data): `probe_gain` == the engine's own sell arithmetic on
  300 random lot books; overlay `none` == the reference pre-tax `simulate_band` NAV path (< 1e-9);
  the band simulator's deferral reproduces the full tax-engine `tax_run_overlay` pre-tax NAV path
  (< 1e-9) for **every** arm and for H2; the month window contains the December window at every
  month (`month_mask(12) subset of month_mask(all)`), and `month_mask(10, December) ==
  December-tail-10`; every arm finite; after-tax <= pre-tax.
- `--harness` (real data; control reproduction + one published non-control anchor, no new arm):
  C0/R2 pre-tax CAGR / max DD / turnover reproduce `fast_reentry_v1/RESULTS.json` and
  `e2_integration_v1`'s deviation table at m = 1/2 in both financings (tol 0.01 pp); R2's
  sub-period and 2017+ CAGRs reproduce `fast_reentry_v1/RESULTS.json` (tol 0.005 pp); the R2 and C0
  after-tax anchors at $100k reproduce `tax_harvest_v2`'s (tol 0.005 pp); the R2 and E2 bootstrap
  medians reproduce `fast_reentry_v1`'s 5,000-path bootstrap (tol 0.01 pp) — this pins the
  bootstrap draws and the overlay-free path bit for bit; all six OOS windows reproduce
  `fast_reentry_v1/RESULTS.json` (tol 0.01 pp); and the **H2 anchor** reproduces
  `tax_harvest_v2/RESULTS.json` (pre-tax main window CAGR/max DD/deferrals and the three $100k
  after-tax CAGRs and deferral counts, tol 0.005 pp).
- `--run` twice must give byte-identical `RESULTS.json`.

## Amendments

- **A1 (2026-10-14). Ordering of the first executions versus this commit.** This pre-registration and
  `screen.py` / `tax_engine.py` / `DATA_MANIFEST.json` were frozen (files final) before any execution,
  but the first three executions happened minutes before the commit that adds them:
  (i) `--smoke` (synthetic prices only), (ii) `--harness` (control reproduction plus the *published*
  `tax_harvest_v2` H2 anchor, whose numbers were published before this campaign), and (iii) a
  throwaway driver that calls `run()` and `gates_and_selection()` with reduced bootstrap counts
  (`PATHS=100`, `TAX_BOOT_PATHS=5`, `OOS_PATHS_PER_MARKET=100`) to debug the implementation.
  **No arm, parameter, window, cost, gate or selection rule was changed after any of them.** The
  changes made after (ii)/(iii) were implementation bugs only: a `mode` variable shadowing that made
  `--harness` fall through into `run()`, a wrong arm-label key in the bootstrap spec list, the
  `share >= 0` vs `share > 0` convention in one reproduction check, and the bootstrap deferral-mask
  column alignment (the resampled price arrays carry one extra leading column). None of these
  touches a pre-registered rule; the gates' `share > 0` convention is implemented exactly as written
  above, and `RESULTS.json` was produced by the frozen rules below (A2 covers a SUMMARY-printing-only
  fix made after the pre-registration commit).
- **A2 (2026-10-14, after commit `693ebc0f`, before the runs whose results are reported).** The first
  `--run` finished every computation and wrote `RESULTS.json`, then aborted with a `KeyError` while
  *building the SUMMARY text*: the H2-anchor row of the pre-tax table was read through the
  candidate-arm accessor. The fix is confined to `main()`'s SUMMARY construction (no computation,
  no parameter, no gate, no output field). Proof that it is inert: `RESULTS.json` has the same
  SHA-256 before and after (`2a5a1aa69c25434d588802fefdb467ab4c88b610a817f3f3e0579a39bfec31a6` — the
  crashed run had already written it), and the two full runs after the fix are byte-identical to
  each other and to it. This amendment and the fix were committed on top of the results commit
  (`0bd37b2e`), so that commit's `screen.py` differs from `main()`'s printer only.
