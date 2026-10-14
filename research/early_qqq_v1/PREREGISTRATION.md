# early_qqq_v1 — pre-registration (idea I5: deliver the early-re-entry leg in the unlevered fund)

Written and committed **before** any non-control arm was simulated on real data. Control (C0)
numbers were reproduced first in `--harness` mode (that is the harness, not an arm run).

Branch: `research/early_qqq_v1`. Sources read: `research/idea_survey_v2:docs/research/idea_survey_v2.md`
§1 and §6 (idea I5), `docs/research/INDEX.md`, `docs/research/fast_reentry_v1.md`,
`docs/research/ewma_band_v1.md`, `docs/research/vt_level_tax_v1.md`,
`docs/research/tax_asym_v3.md`, `docs/research/r2_tbill_combo_v1.md`,
`docs/research/instrument_cost_v1.md`, `docs/research/range_vol_v2.md`.

## 1. Question

Production R2 (`equity_leveraged_trend_v1` 1.2.0 = E2 + early re-entry) delivers the half-weight
early-re-entry leg as `0.5·w` of **QLD** (2× Nasdaq-100, daily reset), i.e. `0.5·w` of own capital
plus `0.5·w` of embedded borrowing (DTB3 + spread) plus 0.95 %/yr fund expense, for a beta of
approximately `w = min(1, 0.35/vol20)`. Holding `min(0.98, w)` of the **unlevered** fund of the same
index (QQQ, 0.20 %/yr) delivers the **same daily beta** with no embedded borrow, and this account's
idle cash earns 0 %. I5 claims the substitution therefore raises return at the same beta, is
tax-neutral (the leg is entered and exited on the same sessions), and is worth **+0.2 … +0.5 pp/yr
after tax at $100k**.

Test: does delivering the early leg in the unlevered fund of the same underlying beat production R2
**after Turkish tax** without worse drawdown, on the pre-registered gates?

## 2. Pre-registered arm set (4 arms total, every parameter fixed here)

Instruments (all synthetic on the harness's own bars, `data/historical/stress_robustness_v1_daily.csv`,
`trv2.load()` QQQ∩SPY sessions, DTB3 from `stress_robustness_v1_dtb3.csv`):

| tag | construction | expense | financing |
|---|---|---|---|
| `QLD*` | daily-reset 2× QQQ total return | `EXP_Q` = 0.95 %/yr | `+1×(DTB3 + spread)` |
| `QQQ*` | daily-reset 1× QQQ total return | 0.20 %/yr | none |
| `SPY*` | daily-reset 1× SPY total return | 0.0945 %/yr | none |
| `SSO*` | daily-reset 2× SPY total return | `EXP_S` = 0.89 %/yr | `+1×(DTB3 + spread)` |

`spread` = 0 (base) or 0.0075 (calibrated), both reported, exactly as `fast_reentry_v1`.

Sizing, signals and the early path are **production R2 unchanged** in every arm:
`w_t = min(1, 0.35/vol20_t)` with `vol20` the 20-session close-to-close vol of `QLD*` (production
convention); E2 vote = at least 2 of 3 on the QQQ price close (close > SMA200; 252-session price
return > 0; close > 126-session max/min midpoint); the early path = QQQ close > SMA50 **and**
`vol20 < ` its 252-session median **and** close > close 20 sessions ago, exit when close < SMA50,
E2's own vote clears/overrides the early position first. Decision at the close, fill at the next open,
resize band 0.5 pp on the **total** equity weight, total equity cap 0.98, cost 4 bps × m per side on
the traded notional, idle cash earns 0 %.

| arm | E2 leg (unchanged) | early leg | candidate |
|---|---|---|---|
| **C0** | `min(0.98, w_t)` `QLD*` | `0.5·w_t` `QLD*` (production R2) | control |
| **A1** | `min(0.98, w_t)` `QLD*` | `min(0.98, w_t)` `QQQ*` | **yes** |
| **A2** | `min(0.98, w_t)` `QLD*` | `min(0.98, w_t)` `SPY*` | **yes** |
| **A3** | `min(0.98, w_t)` `QLD*` | `min(0.98, w_t)` `SSO*` | no (sensitivity) |

A3 is pre-registered as a **non-candidate sensitivity**: `SSO*` is a 2× fund, so its early leg
carries `2·min(0.98, w_t) ≈ 1.96` of beta, not `w_t`. It is run and reported to show that A1's result
is not a leverage effect, but it is not eligible for adoption. Selection rule: adopt a change to
production only if a **candidate** (A1 or A2) passes every pre-tax gate, the new ETF gate and the
after-tax gates; if both candidates pass, prefer the larger after-tax B1-recent median Δ.

### 2.1 Deviations from the survey's arm spec (resolved here, before any data run)

1. **Survey A2 (`0.98 QQQ + (w−0.98)/2 QLD`, "beta exact when w > 0.98") is not simulated as an
   arm.** Its total weight is `0.98 + (w−0.98)/2 ≤ 0.99`, which breaches the account's total equity
   cap of 0.98 and is not implementable without margin. The cap-respecting version is
   `0.96 QQQ + 0.02 QLD` when `w_t = 1`, beta 1.000 vs A1's 0.980, i.e. `+0.02` of beta. Measured on
   the real path (§4 of `RESULTS.json`, `analytic_bounds`): `w_t = 1` on **8.5 %** of the 164
   early-active decision sessions (14 sessions of 6 680), so the whole correction is ≤ 0.005 pp/yr.
   It is reported as an analytic bound instead of spending one of the four arm slots on a rounding
   correction, and the 0.98 cap's cost is quantified in the report.
2. **Survey A3 ("A1 with SSO instead of QQQ") is ambiguous**: SSO is a 2× S&P 500 fund, so it is not
   an equal-beta replacement for `0.5·w` QLD. The literal reading is kept as **A3** (non-candidate,
   see above); the **equal-beta** reading of the same question ("does the cheaper unlevered
   replacement have to be the same index?") is added as candidate **A2** with the 1× S&P instrument
   `SPY*`. This is the reading the survey's own justification asks for ("the S&P-vs-Nasdaq basis cost
   of the cheaper-but-different replacement").
3. **Survey A4 (real QLD/QQQ prices 2011-01 → 2026-09) is an evaluation surface, not an arm.** A1 is
   re-run on real Yahoo adjusted-close QQQ/QLD (report-only, same rules, no synthetic 2×) and reported
   as such; it is not one of the four arms and cannot be a candidate, because the reference gates need
   the synthetic control series.
4. **Instrument switching.** In A1/A2/A3 the E2 leg is `QLD*` and the early leg a different fund, so a
   transition "E2 on (holding QLD*) → early entry" on the same close is a **direct switch**: the
   position is closed at that session's open and reopened in the new instrument at the same open,
   forcing the trade even if the total weight is unchanged, with cost charged on **both** sides
   (`4 bp × m × (sold + bought)`), and counted as event code 5 (`switch`). Number of such switches is
   reported. If the switch never occurs, the switch rule is inactive and arms differ only through the
   early leg's instrument.

## 3. Data

| input | source | role |
|---|---|---|
| `data/historical/stress_robustness_v1_daily.csv` | stress_robustness_v1 (Yahoo) | QQQ/SPY/QLD daily open/close/dividend, 1993-01-29 → 2026-09-29 |
| `data/historical/stress_robustness_v1_dtb3.csv` | FRED DTB3 | financing rate |
| `data/historical/ewma_band_v1_{gspc,index,tb3ms}.csv` | ewma_band_v1 copies | OOS ^GSPC 1957-1999 and M1–M5 |
| `data/historical/after_tax_tr_v1_{usdtry_cbrt,yiufe}.csv` | CBRT / TÜİK via after_tax_tr_v1 | USD/TRY forex buying, Yİ-ÜFE monthly |
| `data/historical/range_vol_v1_{dia,iwm,spy}.csv`, `range_vol_v2_*.csv` | range_vol_v1/v2 (Yahoo) | 21-ETF drawdown generalisation (EWC excluded, see below) |
| `data/historical/early_qqq_v1_{qqq,qld}.csv` | Yahoo chart API, downloaded by this study | real-fund evaluation surface (A4) |

SHA-256 of every file in `DATA_MANIFEST.json`. The ETF universe is **all 22** `range_vol_v[12]_*`
files put through range_vol_v2's quality rule (share of bars with `high == low` or an invalid open
≤ 5 %); `EWC` fails it (5.62 %) and is excluded, leaving **21** instruments — the count the task
fixes. The rule is re-applied at run time and the passing set must be exactly those 21.

## 4. Mechanics and windows

- Main window **2000-03-09 → 2026-09-29** (`START = 253`, `m = 2`).
- Pre-tax control references that `--harness` must reproduce to 0.01 pp (from
  `data/historical` and `fast_reentry_v1/RESULTS.json`): C0 (E2) base m=2 **14.6749 %** CAGR /
  DD **49.5643 %**, calibrated m=2 14.1043 % / 49.6498 %; **R2** base m=2 **15.4766 %** /
  DD **46.7584 %**, calibrated m=2 **14.8961 %** / 46.8797 %, base m=1 15.7538 % / 46.4143 %,
  turnover 5.9915/yr. After-tax anchors at $100k, liquidated, **base** financing, m = 2
  (`vt_level_tax_v1` C0 = E2: 9.534 / 14.497 / 22.693 %; R2: **10.16 / 14.706 / 22.886 %** on
  2000-03-09→2026-09-29 / 2010-01-01→2026-09-29 / 2017-01-03→2026-09-29).
- Bootstrap (stationary block, mean block 20, seed 20261011): 5 000 paths × 10 years on the main
  window for G5; 2 000 paths per market on six OOS markets for G8 (pooled).
- OOS windows (G7): ^GSPC 1957-01-02→1999-12-31, plus M1 ^IXIC 1975→1999, M2 ^GSPC 1934→1956,
  M3 ^N225, M4 ^GDAXI, M5 ^FTSE full history, calibrated financing (+0.75 pp) as in
  `e2_confirmation_v1`/`fast_reentry_v1`.
- After tax: corrected multi-instrument Turkish engine (`tax_engine.py`, a byte copy of
  `r2_tbill_combo_v1/tax_engine.py` = `vt_level_tax_v1`'s corrected engine), $100k, **liquidated**,
  FIFO lots per instrument, one pooled annual tax base across instruments (within-year netting),
  Yİ-ÜFE indexation (GVK mük. 81), 2026 progressive tariff deflated by the previous October's
  Yİ-ÜFE, instalments 31-03/31-07, base financing, m = 2. Clean after-tax bootstraps on the fixed
  2015-09-01→2026-09-30 TRY calendar: **B1-recent** (progressive) and **B2** (flat 20 %, losses
  carried forward), 1 000 paths, seed 20261011, block 20.

## 5. Gates (all versus C0 = production R2)

Pre-tax, exactly as `ewma_band_v1` defines them (both financing models `base` and `calibrated`,
m = 2 unless stated):

- **G1** CAGR ≥ C0, or Calmar ≥ 1.10 × C0 and CAGR ≥ C0 − 0.5 pp.
- **G2** max DD ≤ C0 + 1 pp.
- **G3** 2017→end CAGR ≥ C0 − 1 pp.
- **G4** CAGR beats C0 in ≥ 2 of the 3 subperiods P1/P2/P3.
- **G5** block-20 bootstrap median Δ CAGR ≥ 0 (both financing models).
- **G6** G1's rule at m = 1 (both financing models).
- **G7** six OOS windows: median Δ CAGR ≥ 0, Δ CAGR ≥ −0.5 pp in ≥ 5 windows, Δ DD ≤ +3 pp in
  ≥ 5 windows, max Δ DD ≤ +8 pp.
- **G8** pooled OOS bootstrap median Δ CAGR ≥ 0.
- **G9** main-window turnover ≤ 2 × C0 turnover.

After tax, at $100k, liquidated:

- **T1** beats C0 in **all three** windows (2000/2010/2017).
- **T2** B1-recent: median Δ ≥ 0 **and** share of paths with Δ > 0 ≥ 55 %.
- **T3** B2 (flat 20 %): median Δ ≥ 0 **and** share of paths with Δ > 0 ≥ 55 %.

New, task-mandated:

- **E1 (ETF drawdown generalisation)** — the idea applied to the 21 quality-passing ETFs, each with
  its **own** E2 and early signals, its own 2× synthetic leg (expense `EXP_Q`) and its own 1× self leg
  (the ETF's own adjusted total return), base financing, m = 2, window = its own session 253 → end:
  `share(Δ max DD ≤ +3 pp) ≥ 80 %`, where Δ max DD = DD(A1-style) − DD(C0-style) on that ETF. Median
  Δ CAGR and share Δ CAGR > 0 are reported alongside (no gate).
- **G9** applies to every candidate (turnover ≤ 2 × C0).

A candidate is **adopted** only if G1–G9 **and** T1–T3 **and** E1 pass. If a window or measurement
turns out unusable, the reduced gate is the one pre-registered here (E1 excludes only instruments
failing the quality rule; if fewer than 21 pass, the 80 % share is applied to the instruments that do).

## 6. Predictions (fixed in advance, so the result can falsify them)

The survey's mechanism implies a *financing-and-expense* saving of
`w_t · ((DTB3 + 0.75 % + 0.95 %)/2 − 0.20 %)` per session while the early leg is on. The early path is
active on a small share of sessions (2.46 % in `fast_reentry_v1`), so the pre-registered expectation
is that the whole effect is **an order of magnitude below the survey's +0.2 … +0.5 pp/yr**, i.e.
≈ +0.02…0.03 pp/yr pre-tax, and the arms should fail T2/T3 (bootstrap share > 0 near 50 %). It can
also be negative: `0.5·w` QLD compounds 2× *daily* on half the notional, so in a V-shaped recovery it
earns more than `w` QQQ (reset convexity), which is priced against A1 in exactly the 2023-01 episode
that supplies most of R2's gain over E2. Turnover and drawdown should be nearly unchanged (same
beta), so G2/G9/E1 are expected to pass and G1/G5 to be the binding pre-tax gates.

## 7. Amendments

1. **`arm_stats` report-only NaN fix** (after the pre-registration commit, before the first
   completed `--run`). `time_in_market` and `early_time_in_market` sliced the per-arm target arrays,
   which `arm_state` returns with a leading singleton path axis; the slice took zero rows and
   produced `NaN` plus a numpy "mean of empty slice" warning. Fixed by flattening before slicing.
   Report-only statistics; no arm, gate, parameter or simulated path changed.
2. **`--harness` restricted to the control.** The first version of `--harness` called
   `run_main(ARMS)`, which would have simulated the candidate arms on the main window before the
   pre-registration commit. It now runs `run_main(("C0",))` and the E2-only reference only (the
   committed version is the restricted one). No gate or parameter change.
3. **Line endings of the two downloaded real-fund inputs.** The Yahoo CSVs were first written with
   CRLF; they were rewritten with LF and the `DATA_MANIFEST.json` hashes updated, so the manifest
   matches the committed bytes. No data value changed.
4. **`gates` key error and the E1 applicability flag** (after the first `--run` crashed at the very
   end, after every simulation had completed). The gate table was indexed with all four arms while
   entries were built for the candidates plus A3, so the final assembly raised `KeyError: 'C0'`; C0
   now has an explicit `{"control": true}` gate entry and the T1–T3 loop covers the three evaluated
   arms. The adoption flag `pass_all_with_tax_and_etf` was only defined for A1; E1 is the drawdown
   generalisation of the *unlevered early leg in the underlying's own ETF*, which is A1's
   construction, so it is recorded as not applicable for A2/A3 and binds on A1 alone (this is the
   pre-registered E1 reading: the ETF test varies the underlying, not the delivery basis).
5. None of the gates, arms, parameters, windows or the selection rule were changed after any arm was
   simulated on real data.
