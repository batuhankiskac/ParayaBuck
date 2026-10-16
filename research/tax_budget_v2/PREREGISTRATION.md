# tax_budget_v2 — annual tax-budget execution on top of production K (I3 of `idea_survey_v4`)

Pre-registered 2026-10-16, **before any non-control arm of this study was simulated on real data
on this branch** (one disclosed exception, §11). Working branch `research/tax_budget_v2`; the code
lives in `research/tax_budget_v2/` (`screen.py`, and the engine fork `fe/`). Control identity:
production **K** = `equity_leveraged_trend_v1` 1.2.0 = E2 (2-of-3 QQQ vote) + R2 early re-entry +
**A1u** (gain-scaled downsize band) + **H2** (December downsize deferral).

**This is the redo of `tax_budget_v1` on the mandatory Numba fast engine.** The arms (Y1–Y4),
every parameter, the stages, the seeds, the thresholds, the gates and the selection rule below are
copied **verbatim** from `tax_budget_v1`'s pre-registration, preserved in tag
`archive/research/tax-budget-v1-oldengine-prereg-2026-10`
(`git show archive/research/tax-budget-v1-oldengine-prereg-2026-10:research/tax_budget_v1/PREREGISTRATION.md`).
Its partial results were never reviewed and are not used anywhere here. Only the **engine** and the
§2/§3 plumbing change; nothing that selects an arm changes. The old worktree is not touched.

## 0. Why this study exists

`idea_survey_v3` §I5 and `idea_survey_v4` §I3 propose the only **unused** feature of the Turkish
tax model: the progressive engine computes tax on `max(0, year_gain)` with **no loss
carry-forward** (`tax_engine.BaseAccount.assess`), so a year's net realised loss is destroyed — but
a loss is usable *inside its own year*. Two devices follow: (a) realise gains up to a same-year
loss pool (tax-free, and it steps the basis up, removing that appreciation from a later, dearer
year), and (b) fill the unused part of a lean year's bracket instead of leaving it for a later,
more expensive year. `tax_harvest_v1` H1 measured the *loss-harvesting* direction and failed
(7 firings in 26.5 y, one of them a loss); `tax_harvest_v2` H2 and `monthly_defer_v1` defer
*blindly by calendar*. **No arm in this repo has ever realised a gain on purpose.**

`idea_survey_v4` §I3 pre-registers four arms (Y1–Y4, reproduced verbatim in §4 below) and states
its own ex-ante: after tax **+0.02 … +0.25 pp**, "a small effect with a real chance of zero", with
the range *bounded by a pool that has never been measured*. This study runs those arms and
reports the pool measurement first (§6).

## 1. Question

Do Y1–Y3 beat **K** after Turkish income tax at $100k in all three windows and on both clean
after-tax bootstraps, without failing the standard pre-tax gate set (G1–G9 of `ewma_band_v1`,
with G5 relaxed per §7)? Secondary: how big is the destroyed-loss pool, and how often does each
arm fire?

## 2. Data and provenance

**Nothing is downloaded.** Every input is an existing committed research file in this worktree,
unchanged from `band_h2_combo_v1`; `DATA_MANIFEST.json` records source, byte count and SHA-256 of
each file plus the **evidence** files used for reproduction
(`research/band_h2_combo_v1/RESULTS.json`, whose K and C0 cells are compared cell-by-cell,
`research/gain_band_v3/RESULTS.json` and `research/band_h2_combo_v1/H2_ANCHORS.json`).

Uses: `stress_robustness_v1_{daily,dtb3}.csv` (QQQ/SPY/QLD bars, DTB3),
`after_tax_tr_v1_{usdtry_cbrt,yiufe}.csv` (TRY conversion, Yİ-ÜFE indexation),
`ewma_band_v1_{gspc,index,tb3ms}.csv` (the six OOS windows). The TRY/Yİ-ÜFE calendar starts
2000-01 / 1999-01, so no after-tax stage starts before 2000-03-09. The 21-instrument stage of the
parent campaign is **not** run (it is not in this campaign's gate set).

### 2.1 The engine (the difference from `tax_budget_v1`, and the only one)

`research/fast_engine/` (tag `archive/research/fast-engine-v1-2026-10`, restored unchanged into
this worktree) is the mandatory base. The arms need one **new execution mechanism**, so per the
worker protocol the engine is forked into **`research/tax_budget_v2/fe/`** (a copy; the parent
package is never edited in place):

- `fe/fe_sim.py` — the kernel gains two rule options, both **off** on the parent's code path:
  - `yearend` (0 off / 1 Y1's loss-pool fill / 2 Y2's bracket-headroom fill): on the last session
    of each calendar year fully inside the window, if the account's YTD realised net TRY gain
    passes the arm's trigger, sell-and-rebuy the matched quantity **at that close**, both legs
    (see §4). The quantity is solved in closed form from the FIFO queue's per-lot per-unit gains
    under the run's own regime (indexed for progressive, unindexed for flat/pre-tax — the engine
    books those gains, so the trigger and the quantity use the same formula).
  - `realloc` (Y3/Y4): a pending non-exit downward resize whose fill session is in the last 10
    December or the first 10 January sessions fills only when the session's own tax year is the
    cheaper of the two adjacent years; **A1u's band then applies to it as in K**. With
    `realloc=False` the parent's H2-then-band decision block is taken literally.
  - Added outputs (pure additions): `realloc_holds`, `yearend_round_trips`,
    `yearend_notional_usd`, `realised_gain_try_by_year`, the optional `_ye_events` / `_re_events`
    traces; the round-trip legs are added to `turnover_total` and booked under the new
    `"yearend"` gain reason (`fe/fe_tax.py`; never reached with `yearend=0`).
- `fe/fe_boot.py` — passes the two new rule arrays to the kernel (the published draw sequence,
  path reconstruction and result dictionary are untouched).
- `fe/fe_oos.py` — **new**: gate G8's pooled M1–M5 bootstrap as one numba kernel
  (`prange` over paths), mirroring `band_h2_combo_v1.boot_oos_market`'s draws, market
  reconstruction and statistic, with the per-path simulation in the kernel instead of a Python
  loop (the protocol forbids new per-path Python simulation loops).
- `fe/fe_config.py` — `ROOT` walks one more parent (the package sits one directory deeper).
  `fe/fe_data.py`, `fe/fe_signals.py`, `fe/fe_study.py`, `fe/fe_tax.py` are the parent's byte for
  byte apart from `TARIFF_TOP` and the `yearend` reason in `fe_tax.py`.

Parity (asserted in `--smoke` and reported in RESULTS.json):

1. `python -m research.fast_engine.parity --quick` must PASS (it compares the parent engine with
   the pure-Python `band_h2_combo_v1` harness and every published cell).
2. **Option-off bit identity**: for every parent arm (`C0`, `A1u`, `H2`, `K`) × mode
   (`pre_tax`/`progressive`/`flat`) × m (1/2/4), the fork's `simulate` reproduces
   `research.fast_engine.fe_sim.simulate` with **max |diff| = 0 on the NAV path and on every
   shared scalar** (the fork only adds result keys).
3. §3's anchors, and the fork's `jan_head` mask equals the parent's `dec_tail` mask on the main
   window (both are the last/first 10 sessions of the month in each year).

### 2.2 The two places where `tax_budget_v1`'s *harness* disagreed with its own text

The v1 harness was committed with the pre-registration but never produced a result, and it
diverges from the §4 text in two places. This study implements the **text** (the pre-registered
specification); both divergences are recorded here so the difference is not silent:

1. `tax_budget_v1/screen.py` builds the Y3 window as `realloc_win = (dec_tail | jan_head(...)) if
   spec.get("realloc") else set()`, but `dec_tail` is itself emptied whenever `defer` is false
   (lines 289-292), which is exactly the case for Y3/Y4 — so the v1 code's state window covered
   **January only**, while the text says "the last 10 December sessions **or** the first 10
   January sessions". Implemented here as the text says.
2. In the same block (lines 310-325) the state test is an `elif` arm of the chain that ends in the
   band test, so a resize that *passes* the state test skips A1u's band, while the text says
   "A1u's band still applies after the state test, exactly as in K". Implemented here as the text
   says.

Neither is a change of arm, parameter, gate or selection rule: both make Y3/Y4 match their
description. Y1/Y2 are unaffected by either.

## 3. Control (C0 = K)

Production **K**, bit for bit, through the `research/tax_budget_v2/fe/` fork of the fast engine
(R2's target series — E2 2-of-3 QQQ vote + the 0.5·vt early re-entry leg, `vt = min(1, 0.35/vol20)`,
cap 0.98 — with the production execution layer: decide on the completed close, fill at the next
open, symmetric 0.5 pp band, entries and exits to zero always fill, idle cash zero, cost
4 bps × m × |Δw|, synthetic QLD* = daily-reset 2× on QQQ total return, 0.95 %/yr expense,
financing DTB3 + {base 0.0 %, calibrated 0.75 %}/yr), **plus A1u** (a discretionary downward
resize is suppressed iff `drift ≤ B_down(g_frac)` with `B_down` = 0.5 / 3 / 8 pp at `g_frac`
≤ 1 % / ≤ 5 % / > 5 %, USD FIFO gain fraction, read-only from the live lot book) **plus H2** (a
fill that would be a non-exit downward resize whose fill session is one of the last 10 December
sessions is postponed). §2.1's parity cell 2 proves this is the parent engine's K.

**Anchors asserted in `--harness`** (tolerance 1e-6 absolute = 0.0001 pp; 0.01 pp is the brief's
bound). From `band_h2_combo_v1/RESULTS.json` (its arm `K`, its arm `C0` = this study's reference
row `R2`), reproduced cell-by-cell: `pre_tax_main` (base/calibrated × m = 1/2/4),
`pre_tax_main_r_usd`, `after_tax_windows` ($100k, all three windows), `cost_stress_m4`,
`bootstrap_clean_seed20261011` (pre-tax base/calibrated; after-tax $100k/$1M ×
progressive/flat, every `*_vs_*` cell) and the six `oos` windows. The literal brief anchors are
asserted too: pre-tax **15.7649 % base / 15.1785 % calibrated**, max DD **46.6698 % / 46.7948 %**,
turnover 5.4561/yr; after tax at $100k liquidated **10.7533 / 15.2354 / 23.9720 %**; R2 reference
row after tax **10.1600 / 14.7059 / 22.8861 %**.

## 4. Arms (5 labels; every parameter fixed, no tuning)

| Arm | Rule |
|---|---|
| **K** | production K (control, §3). |
| **Y1** | **Loss-pool fill.** At the **last session of each calendar year fully inside the window**: if the account's YTD realised **net TRY gain** `ytd < 0`, sell-and-rebuy the smallest quantity `q` whose realised indexed-TRY gain brings the year to ≈ 0 (`q = min(q_needed, 0.25 × NAV, book)`), i.e. `need = −ytd`; if `ytd ≥ 0`, do nothing. |
| **Y2** | **Bracket-headroom fill.** Same session: with `ceil = 5,300,000 × scale(year)` TRY (the top bracket's upper bound of `TARIFF_2026`, deflated by the previous October's Yİ-ÜFE exactly as `tariff_tax` does), if `ytd < 0.25 × ceil`, sell-and-rebuy `need = ceil − ytd` worth, capped identically at 25 % of NAV. |
| **Y3** | **No-new-trade year reallocation.** H2's *calendar* window is **replaced** (not stacked) by a state window: a pending non-exit downward resize whose fill session is in the **last 10 December sessions or the first 10 January sessions** fills iff the session's own tax year is the cheaper of the two adjacent years, i.e. `ytd(this year) ≤ ytd(other year)` where "other" is the next year (December, YTD 0 by definition) or the finished previous year (January); otherwise the fill is postponed, the target re-evaluated at every later close, and the ordinary rule applies once the session leaves the window. A1u's band still applies after the state test, exactly as in K. Exits to zero and up-sizes are untouched. |
| **Y4** | **Y1 + Y3** (report-only upper bound on the channel; §7). |

**Round trip (Y1/Y2) modelled exactly, and the fill convention.** The decision is taken on the
**completed close** of the last session of the year, and **both legs fill at that same close**
(`idea_survey_v4` I3: "round trip inside the decision close"). This is a deliberate, disclosed
departure from the harness's ordinary next-open fill: a next-open rebuy would place the
realisation in the *following* tax year, which destroys the arm's entire mechanism. Costs are
charged on **both** legs at the production rate (4 bps × m × notional each); the sale consumes the
FIFO queue oldest-first with the engine's own per-lot arithmetic (`BaseAccount.sell` / `_sell`),
and the new lot is written at the rebuy price **including** the fee, at that session's CBRT
USD/TRY rate and that session's month — so the indexation relief of the consumed lots is destroyed
and the new lots are not relief-rich. The traded notional is added to turnover at twice its weight
(sell + rebuy), so G9 sees the real cost. `q` is solved in closed form from the FIFO queue's
per-lot per-unit gains under **the run's own regime** (indexed for progressive, unindexed for
flat/pre-tax — the engine books those gains, so the trigger and the quantity must use the same
formula); the read-only probe is checked against the engine's own `_sell` arithmetic in `--smoke`
(the booked `yearend` reason gain must equal `ytd_after − ytd_before` for every round trip).

`q_needed` is the FIFO-prefix crossing of `need`; if the whole book's cumulative gain never
reaches it (every remaining lot at a TRY loss, so selling more cannot bring the year towards zero),
the fill falls back to the **whole capped book** — `gain_matched_units`'s documented behaviour,
and `tax_budget_v1`'s implementation — rather than to nothing. The log stage counts how often that
fallback binds (a fill whose `want` reached the whole book), so its size is visible either way; on
the main window the 25 %-NAV cap binds in every Y1/Y2 fill, and the report gives both counts.

### 4.1 The Turkish legal reading (stated honestly)

The model has **no wash-sale rule** (`tax_harvest_v1`, `idea_survey_v2` §1.2 item 5,
`idea_survey_v3` §6.5) and none is added here. What is defensible and what is **not verified**:

- **Voluntarily realising a gain is legal and raises the current year's tax.** Y1/Y2 do exactly
  that (plus a re-purchase). Nothing in the model or in the arm assumes a *reduction* of the
  current year's tax below what the same realised gain would otherwise be liable for: the gain is
  netted against the year's own loss pool (or the year's bracket), which the statute already
  allows within one calendar year.
- **GVK has no wash-sale rule on gains** in the sense of the US §1091 (which disallows a *loss*
  where a substantially identical security is repurchased). Nothing in Turkish law voids a
  *gain* that is realised and re-invested in the same security. The relevant risk is not
  §1091-style but **anti-abuse / economic-substance (muvazaa, GVK/MTV anti-abuse practice and
  the "işlemden doğan kazanç" reading)**: a same-session round trip engineered *only* to reset the
  cost basis, with no change in economic position, could in principle be challenged — including on
  the ground that the loss the fill is netted against was itself realised deliberately, or that a
  same-day round trip is not a genuine "tasarruf" (disposal and re-acquisition of economic value).
- **Not verified: whether the Turkish model as implemented (per-calendar-year netting with NO
  loss carry-forward; Yİ-ÜFE indexation of the month preceding acquisition when the index rose
  ≥ 10 %; FIFO; the 2026 tariff deflated by the previous October's Yİ-ÜFE; trade-date tax years;
  no BIST offsets, no other income, no FX cost on the tax payment) is the law, or whether the
  broker's tax lot handling permits a same-session round trip with a fresh basis.** A written
  **mali müşavir / YMM opinion** is a precondition for any live use of Y1/Y2/Y4, exactly as for
  lot-selection rules. **Y3 has no legal question at all** (it only chooses when an
  already-decided sale fills, within the same instrument and position — the pattern H2 already
  ships), which is why it is in the arm set despite being the least novel. **None of this is tax
  advice.**

## 5. Stages (all pre-registered before any non-control arm was simulated on real data)

Main window 2000-03-09 → 2026-09-29 (6,679 measured sessions, session 253 onwards), m = 2, cost
4 bps × m × |Δw|, base financing unless stated, progressive 2026 tariff deflated by the previous
October's Yİ-ÜFE where after tax, **liquidated** at the window end. Every arm label runs
production R2's target series. Stages run through `fe/fe_study.py` and `fe/fe_boot.py` (the
parent's stage code) on the fork's kernel, with arms `("C0", "K", "Y1", "Y2", "Y3", "Y4")`
(`C0` is the reference row `R2`).

- **pre** — pre-tax main window (base/calibrated × m = 1/2/4) plus the R-USD row; sub-periods
  P1 2000–08, P2 2009–16, P3 2017→end and the 2017+ cell.
- **AT** — after tax at $100k (gate capital) and $1M (report-only) on the three windows.
- **cost** — m = 4 cost stress, after tax at $100k on the three windows (report-only).
- **B12** — clean block bootstrap (seed 20261011, block 20, 5,000 pre-tax / 1,000 after-tax
  paths, $100k + $1M report, B1-recent progressive and B2 flat, fixed 2015-09-01 → 2026-09-30
  calendar with its realised USD/TRY and Yİ-ÜFE). Per-path arrays are kept (`keep_paths=True`)
  and every Δ vs **K** in §7 is computed from them (the parent's own `*_vs_C0` cells are reported
  beside them).
- **OOS** — the six `ewma_band_v1` windows (^GSPC 1957–1999, M1–M5), pre-tax, calibrated, m = 2,
  R-USD rule.
- **OOSB** — G8's pooled M1–M5 OOS bootstrap (`fe/fe_oos.py`): seed 20261011, block 20,
  2,000 paths per market, pre-tax, calibrated, m = 2, R-USD rule; pooled median Δ vs K.
- **starts** — report-only: 21 rolling January starts (2001…2021), after tax at $100k, base, m = 2,
  liquidated 2026-09-29.
- **pool** — report-only diagnostic (§6): the K path's per-year YTD realised net TRY gain at each
  year end, the loss pool, the deflated bracket ceilings and the NAV, so the size of the channel
  is measured rather than assumed.
- **log** — report-only: the Y1/Y2/Y4 round-trip events and the Y3/Y4 realloc holds on the main
  window (both after tax at $100k and pre-tax), from the kernel's event traces.

## 6. Required prior measurement (reported, not gated)

`idea_survey_v4` §I3 requires, before any arm is read: `Σ_years |min(0, ytd)|` (the destroyed
loss), the number of loss years, the year-by-year realised gain against the deflated bracket
ceilings, and how often each arm's trigger is available. The `pool` stage reports exactly that
from the **K** path at $100k on the main window (progressive, i.e. the run's own netting):
per-year `ytd_try`, `pool_try = max(0, −ytd)`, the deflated ceiling, `ytd/ceiling`, the 25 %-NAV
cap in TRY and whether it binds, plus the aggregate `pool_sum_try` / `pool_sum_usd` and the count
of years in which Y1's and Y2's triggers are available. It is a **measurement**, not a gate: it
cannot change an arm, a parameter or a threshold (it is computed from the control only).
*Not available:* the positive/negative split of a year's realisations (`idea_survey_v3` I5's
"fraction of each loss year already absorbed by same-year gains") — the engine's per-year
accumulator is net only; the report says so rather than approximating it.

## 7. Gates (pre-registered)

**Pre-tax, against K** (`ewma_band_v1`'s G1–G9, with K as the reference). "Both" = base and
calibrated financing.

| Gate | Requirement |
|---|---|
| **G1** | m = 2, both: CAGR ≥ K, or Calmar ≥ 1.1 × K with CAGR ≥ K − 0.5 pp. |
| **G2** | both: max DD ≤ K + 1.0 pp. |
| **G3** | both: 2017+ CAGR ≥ K − 1.0 pp. |
| **G4** | CAGR above K in ≥ 2 of 3 sub-periods (P1/P2/P3, base). |
| **G5** | **pre-registered as non-inferiority, not superiority**: clean bootstrap (seed 20261011, block 20, 5,000 paths), base and calibrated: median Δ vs K ≥ **−0.10 pp**. These arms are tax-timing devices whose pre-tax leg measures only the *trading cost* of moving a fill, so superiority in a pre-tax bootstrap is not the right test (`ewma_band_v1`'s "≥ 0" is that study's test for a band that changes exposure; `idea_survey_v4` §6.1 flags this asymmetry) — the reduced gate is declared here, before any data. |
| **G6** | G1 at m = 1, both. |
| **G7** | six OOS windows (calibrated, m = 2, R-USD): median Δ CAGR ≥ 0; in ≥ 5 of 6 windows Δ CAGR ≥ −0.5 pp **and** Δ DD ≤ +3 pp; Δ DD never > +8 pp. |
| **G8** | pooled M1–M5 OOS bootstrap (2,000 paths each, seed 20261011, block 20, pre-tax, calibrated, m = 2, R-USD): median Δ ≥ 0. |
| **G9** | turnover ≤ 2 × K (turnover **including** the Y1/Y2 round-trip legs). |

**After tax, against K** (progressive 2026 tariff, liquidated, base financing, m = 2):

| Gate | Requirement |
|---|---|
| **A1** | at $100k, Δ vs K **> 0** in all three windows (2000-03-09, 2010-01-01, 2017-01-03). |
| **A2** | clean after-tax bootstrap (seed 20261011, block 20, 1,000 paths, $100k): for **both** B1-recent and B2 flat, median Δ vs K ≥ 0 **and** share of paths with Δ > 0 ≥ 55 %. |

**Selection rule (fixed in advance).** Among Y1–Y3 (Y4 is report-only), select the arm with the
highest **B1-recent $100k after-tax bootstrap median Δ vs K** among those passing G1–G9 ∧ A1 ∧ A2;
tie-break: the 2000–2026 $100k after-tax Δ vs K. If no arm passes, **no arm is selected** and K
stays. No arm is dropped or added after seeing data, and no gate is re-tuned.

## 8. Pre-registered expectations (stated before the run)

1. **The pool is nearly empty.** `tax_harvest_v1` H1 found 7 year-end trigger opportunities in
   26.5 years and only one realised loss; in a tax base of *nominal TRY* gains with 25 years of
   lira depreciation, the engine's `year_gain` is positive in almost every year. So **Y1 should
   fire a handful of times and its after-tax Δ should be within ±0.05 pp of zero.** Y1's G5/G7/G8
   cells should pass trivially or fail by < 0.01 pp (its extra trades are ~2 sessions/yr at most).
2. **Y2's trigger is nearly always available and its cap nearly always binds.** At $100k,
   `ceil` (≈ 5.3 m TRY × scale) is large relative to NAV in the early years and the 25 % NAV cap
   binds, so Y2 degenerates into "sell-and-rebuy 25 % of NAV every year the trigger fires". Its
   pre-tax/A2 cost is then a systematic drag (2 legs × 4 bps × m × 25 % NAV per firing ≈ 4 bps of
   NAV/yr at m = 2), i.e. **Y2 is expected to fail G7/G8 (median Δ ≈ −0.03 … −0.10 pp) and to
   show a *positive* after-tax Δ only if the basis step-up out-earns that drag and the destroyed
   Yİ-ÜFE relief.** Expected: a wash, with a real chance of a clear fail.
3. **Y3 is the most likely arm to pass.** It adds **no** trade: it only chooses the tax year of a
   fill H2/K would already make. Its pre-tax cells should be ≈ 0 (a 10-session timing difference in
   a December/January fill), so G1–G9 should pass, and its after-tax Δ should be small and
   positive-to-zero (+0.00 … +0.10 pp), *smaller* than H2's own +0.14/+0.13/+0.27 pp, because it
   can only improve the year choice when the December year is at a net loss.
4. **Y4 (Y1 + Y3) is an upper bound on the combination** and is expected to be Y3's Δ plus Y1's
   ≈ 0.
5. Per `idea_survey_v4` §6.2, all after-tax deltas below ~0.1 pp are inside modelling
   uncertainty; the honest headline may be "the axis is closed", which is a result.
6. **G7/G8 are expected to bind for Y1/Y2/Y4 by construction** (a pre-tax OOS stage cannot show a
   tax benefit, only the round-trip cost). They are kept as pre-registered for every arm — no
   arm-specific gate set — and a failure there will be reported with its measured size.

## 9. Caveats and limits

As `band_h2_combo_v1` / `tax_harvest_v2`: one history per cell; no true after-tax out-of-sample
(the TRY calendar starts 2000); the fixed bootstrap calendar cannot price another FX/inflation
regime (and the **deferral/bracket premium is a first-order function of the PPI-vs-FX gap**, which
is never varied); the liquidated convention charges deferred tax at the window end; FIFO with
per-year netting and no carry-forward; the 2026 tariff deflated by the previous October's Yİ-ÜFE;
instalments funded by selling; dividends are credited but assessed under the engine's
22,000 TRY threshold + withholding credit (a fidelity gap the parent campaigns disclose too).
`idea_survey_v4` §6.4's dividend withholding question is inherited unchanged. **Not tax advice**
(§4.1). The round trip's same-close fill convention is a modelling choice (§4) and its fee,
spread and market-impact cost is the production 4 bps × m per leg with no slippage beyond it.

## 10. Amendments

*(Any fix made after the first complete run is recorded here, in this section only; arms,
parameters, stages, seeds, thresholds, gates and the selection rule are frozen as of this commit.)*

*(none yet)*

## 11. Process note: a disclosed pre-commit exploratory execution

Writing this study after the pre-registration text was fixed, one **exploratory execution** of the
four Y arms (and the control) on the main window, **after tax at $100k, progressive, liquidated,
m = 2**, was run to check that the new kernel options execute before the pre-registration commit.
It was a process slip: the arms, parameters, gates and selection rule above are the v1
pre-registration's text, unchanged, and nothing below was used to adjust anything (the values are
the ones the eventual `--run` reports for the same cells, and the τ = 0 cell of A1 is among them).
Disclosed in full so the reader can judge the freeze:

| Arm | $100k after-tax CAGR, 2000-03-09 → 2026-09-29 (exploratory) | round trips | realloc holds | turnover/yr |
|---|---:|---:|---:|---:|
| K (control) | 10.7533 % | 0 | 0 | 5.4561 |
| Y1 | 10.8381 % | 4 | 0 | 5.5311 |
| Y2 | 10.6943 % | 12 | 0 | 5.6835 |
| Y3 | 10.7055 % | 0 | 67 | 5.4386 |
| Y4 | 10.7903 % | 4 | 69 | 5.5139 |

No other cell (no pre-tax window, no m = 1/4 cell, no 2010/2017 window, no bootstrap, no OOS, no
pool diagnostic) had been computed at that point.
