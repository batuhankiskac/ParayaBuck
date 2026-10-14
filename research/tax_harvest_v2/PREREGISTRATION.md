# tax_harvest_v2 — confirmation of H2 (December vol-resize deferral) on production R2 (pre-registration)

Committed **before** any non-control arm was simulated on real data. Everything below is frozen:
question, arms, parameters, data, mechanics, gates, selection rule. Bug fixes or later changes go
in *Amendments* at the end.

## Question

`tax_harvest_v1` (branch `research/tax_harvest_v1`, doc `docs/research/tax_harvest_v1.md`) selected
**H2**: production R2, but a *downward* vol resize (not a trend exit, not an early-path exit — both
always fill in full) whose **fill** session falls in the last **10** trading sessions of December is
postponed; the ordinary decision rule applies again from the first session of January. v1 measured
$100k after-tax CAGR deltas vs R2 of **+0.14 / +0.13 / +0.27 pp** on the 2000 / 2010 / 2017 windows,
and clean-bootstrap medians of **+0.055 / +0.045 pp** (64–70 % of paths), against a pre-tax
15.514 % vs R2's 15.473 % and −0.50 pp of max drawdown. v1 did **not** test the length of the
postponement window, any out-of-sample window, any other instrument, or a cost stress.

This campaign is the confirmation v1 asked for, and it is deliberately adversarial: the mechanism
holds a **larger** weight through the year end, so the failure mode is a late-December selloff
(December 2018 is the obvious candidate) with no compensating tax benefit inside a ten-year
bootstrap-style horizon.

1. **Plateau (gate P).** Is the effect a property of the rule or of the exact 10-session window?
   Two neighbours in each direction: last **5** and **15** and **20** December sessions.
2. **Pre-tax risk (gate F2/F3).** Does H2 survive outside the 2000–2026 US large-cap history — the
   six `ewma_band_v1` out-of-sample windows and the 21 independent equity ETFs of
   `fast_reentry_v2`'s instrument test, each with its own E2+R2 logic and its own synthetic 2x leg?
3. **Cost (gate F4).** Does the after-tax edge survive a doubled cost multiplier (m = 4)?
4. Reported, not gated: the late-December episodes where H2 lost the most (dates + loss); $10k and
   $1M after tax; the clean after-tax bootstrap of every arm.

Legal framing: this is a *timing* overlay on the execution layer. It realises nothing extra; it
moves one fill across a calendar-year boundary. v1's finding that the edge is deferral of the
assessment (plus ~3 % fewer sells), not a saved tax, is carried forward unchanged.

## Control and arms

- **Control R2** = production `equity_leveraged_trend_v1` 1.2.0: at least 2 of 3 votes on the QQQ
  **price** close (close > SMA200; 252-session price return > 0; close > the 126-session max/min
  midpoint), sized `vt = min(1, 0.35 / vol20)` on synthetic QLD\* (daily-reset 2x on QQQ total
  return), total cap 0.98; while the E2 vote is off, `0.5 x vt` if QQQ close > SMA50, QQQ vol20 <
  its trailing 252-session median and close > close 20 sessions ago, exited below SMA50; E2's own
  vote overrides.
- **Arms (5 = control + 4 candidates).** Every arm shares R2's target series **bit for bit**; only
  the execution layer differs:
  - **H2** = v1's arm: postpone a downward vol resize whose **fill** session is one of the last
    **10** December sessions (v1's exact parameter).
  - **H2_5 / H2_15 / H2_20** = the same rule with a **5 / 15 / 20**-session postponement window.
    These are the plateau neighbours; nothing else changes anywhere.
- Down-resize test: at the fill's open, `0 < target < held weight` (equivalently the unit delta is
  negative and the target is positive). Exits (`target = 0`) and up-resizes always execute in full.
- A postponed fill is **not** re-tried; the ordinary rule is evaluated again at every subsequent
  close (so if the target has recovered by the first January session, no trade happens at all).
- Number of arms is 5 (≤ 6 as the protocol requires); all parameters are fixed above.

## Data

All inputs already exist in the repository (no download in this campaign; SHA-256 in
`DATA_MANIFEST.json`):

- `data/historical/stress_robustness_v1_daily.csv` (QQQ/SPY/QLD open/close/dividend),
  `data/historical/stress_robustness_v1_dtb3.csv` (DTB3),
  `data/historical/after_tax_tr_v1_usdtry_cbrt.csv` (USD/TRY CBRT buying),
  `data/historical/after_tax_tr_v1_yiufe.csv` (Yi-UFE monthly) — restored from
  `research/vt_level_tax_v1`.
- `data/historical/ewma_band_v1_{gspc,index,tb3ms}.csv` — the six `ewma_band_v1` out-of-sample
  windows (^GSPC 1957–1999 + `e2_confirmation_v1`'s M1–M5), restored from
  `archive/research/ewma-band-v1-2026-10`.
- `data/historical/range_vol_v1_{dia,iwm,spy}.csv` and `range_vol_v2_*.csv` — the 22 independent
  instruments of `fast_reentry_v2`'s gate I, restored from `research/range_vol_v2`; `EWC` is
  dropped by `range_vol_v2`'s fixed quality rule (> 5 % invalid/flat bars) **before** simulation,
  leaving **21**.

## Mechanics (identical in every arm)

- Decide on the completed close, fill at the next open; 0.5 pp resize band (`trv2.BAND`); entries
  from flat and exits to zero always trade in full; idle cash earns zero; cost
  `4 bps x m x |delta w|` per traded unit.
- Synthetic QLD\* = daily-reset 2x on QQQ total return, expense 0.95 %/yr, financing DTB3 +
  {base 0.0, calibrated +0.75 %}/yr. Pre-tax reported at m = 1 and m = 2, both financings; the
  after-tax stage is base financing at m = 2 and the cost stress at m = 4 (base financing).
- Main window 2000-03-09 → 2026-09-29 (6,679 sessions), start index 253.
- December windows: the December sessions of a calendar year are the sessions whose date month is
  `-12`; "last N" = the last `min(N, count)` of them. Session index sets are built on the simulated
  window's own session list (so a bootstrap path or an OOS window uses its own Decembers).
- **Fill session** = the session whose open executes a trade decided at the previous close. The
  overlay looks at that session.

## After-tax model

`research/tax_harvest_v2/tax_engine.py`, byte copy of `research/tax_harvest_v1/tax_engine.py`
(itself the corrected `vt_level_tax_v1` / `r2_tbill_combo_v1` engine, SHA-256 in the manifest).
Model, unchanged from v1: realised gains/losses netted **per calendar year** (progressive regime
taxes `max(0, net)`, no loss carry-forward; the flat-20 % bootstrap variant carries losses forward);
**trade date** determines the tax year; gains in **TRY** at the one CBRT buying-rate series;
Yi-UFE indexation of the acquisition cost when the index rose >= 10 % (indexation floor); 2026
tariff scaled by the previous October's Yi-UFE (15/20/27/35/40 % brackets); instalments Mar 31 and
Jul 31 of the following year, funded by selling the position if cash is short; FIFO; liquidation at
the window end. Windows: 2000-03-09 → 2026-09-29, 2010-01-01 → 2026-09-29, 2017-01-03 →
2026-09-29; capitals **$10,000 / $100,000 / $1,000,000**; after-tax CAGR on the **liquidated**
terminal USD value.

**Clean after-tax bootstrap** (`tax_asym_v2` / `vt_level_tax_v1` design, as re-used by
`r2_tbill_combo_v1`): stationary block-20, seed 20261011, **1,000** paths of 10 measured years
(2,520 sessions + 3 back-extension, warm-up 253) drawn from QQQ open/close/dividend and DTB3; the
signals are recomputed on the resampled price level; the **calendar stays real and fixed**
(2015-09-01 → 2026-09-30 with its realised USD/TRY and Yi-UFE), so the December windows are that
calendar's. Two tax variants: **B1-recent** (progressive) and **B2** (20 % flat), at $100k and $1M.

Legal assumptions (not tax advice): no wash-sale-style rules (irrelevant here — nothing is sold
because of the overlay); no BIST offsets, no other income, no FX cost on the tax payment, frozen
2026 thresholds, one CBRT buying-rate series.

## Gates

The verdict is on **H2** (the v1 candidate under confirmation). Gate P applies to the *neighbours*
as a plateau. F2/F3/F4 are evaluated on H2; every arm's values are reported.

- **Gate P (plateau).** For **each** of `H2_5`, `H2_15`, `H2_20`: after-tax CAGR (progressive,
  liquidated, base financing, m = 2) at **$100k** is **strictly greater than R2's** in **all three**
  windows (2000 / 2010 / 2017). Pre-tax reported alongside.
- **Gate F2 (OOS).** H2 vs R2, pre-tax, calibrated financing, m = 2, on the **six**
  `ewma_band_v1` out-of-sample windows (^GSPC 1957–1999, M1 ^IXIC 1975–1999, M2 ^GSPC 1935–1956,
  M3 ^N225, M4 ^GDAXI, M5 ^FTSE): **no window with Δ max DD > +2 pp**, and the **median Δ CAGR over
  the six windows >= −0.05 pp**. (Δ = H2 − R2.)
- **Gate F3 (instruments).** H2 vs R2, pre-tax, calibrated financing, m = 2, on the **21**
  quality-passing equity ETFs (each with its own E2+R2 logic and its own synthetic 2x leg): **share
  of instruments with Δ max DD <= +2 pp >= 90 %** (>= 19 of 21). Median Δ CAGR and the share
  Δ CAGR > 0 are reported.
- **Gate F4 (cost stress).** H2 vs R2 at **m = 4** (base financing, progressive, liquidated),
  $100k: after-tax CAGR **>= R2's in all three windows**.
- **Reported, not gated:** the pre-tax main-window table (both financings, m = 1/2, turnover,
  sells/yr, deferral counts); the after-tax table at $10k / $100k / $1M; the clean bootstrap of
  every arm (H1-style gate not applied — v1 already passed it for H2 and this campaign's verdict
  rule is the task's, P + ∈{F2,F3,F4}); the late-December episodes of the main window, defined as
  per calendar year the calendar-days window `[last 20 December sessions of year Y] ∪ [first 20
  January sessions of year Y+1]`, with the H2−R2 NAV log-ratio change over it (pp), ranked by the
  most negative.

**Verdict.** `CONFIRMED` iff P **and** F2 **and** F3 **and** F4 all pass. If a neighbour passes P
but H2 fails F2/F3/F4, the verdict is `FAIL` (the plateau is not the binding constraint in that
case and must not be read as one). If some neighbours pass P and others do not, P fails as
pre-registered; H2's own F-gates are still reported and the failure is recorded as a plateau
failure.

## Verification plan

- `--smoke` (synthetic prices, no market data; run before this commit): `probe_gain` == the
  engine's own sell arithmetic on 300 random lot books; overlay `none` reproduces the reference
  pre-tax `simulate_band` NAV path to < 1e-9; the band simulator's **H2 deferral** reproduces the
  full tax-engine `tax_run_overlay` pre-tax H2 NAV path to < 1e-9 for every tail (5/10/15/20) — the
  OOS/instrument stages use the band simulator only, so this pins them to the main-window engine;
  every arm finite; after-tax <= pre-tax.
- `--harness` (control only): R2 and C0 pre-tax CAGR / max DD / turnover reproduce
  `fast_reentry_v1/RESULTS.json` and `e2_integration_v1`'s deviation table at m = 1/2, base and
  calibrated, within 0.01 pp; the after-tax engine reproduces the R2 and C0 anchors at $100k on all
  three windows (CAGR, max DD) within 0.005 pp; the control's clean-bootstrap medians reproduce
  `r2_tbill_combo_v1` within 0.10 pp. No candidate arm is simulated on real data in this mode.
- `--run` twice must give byte-identical `RESULTS.json`.

## Amendments

- **A1 (2026-10-14, after this commit and before any candidate arm was simulated on real data).**
  The committed `screen.py` referenced an undefined `DTB3` name in the new out-of-sample loaders,
  so `--run` aborted with a `NameError` as soon as it reached the OOS stage. One line was added —
  `DTB3 = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"`, the same file `trv2.load()`
  already reads for financing — and that is the only difference between the committed
  pre-registration code and the code that produced `RESULTS.json`. No arm, parameter, window,
  data input or gate was changed; the smoke and harness checks and the reference anchors were
  re-run unchanged after the fix and pass as before.
