# gain_band_v3 — confirmation of the gain-scaled downsize band on new evidence

Pre-registered 2026-10-15, **before any v3-specific quantity was computed on real data on this
branch**. Working branch `research/gain_band_v3`; the code lives in `research/gain_band_v3/`
(`screen.py`, `tax_engine.py`), copied and extended from `research/gain_band_v2/` (which is not
modified). Control identity: production **R2** = `equity_leveraged_trend_v1` 1.2.0.

## 0. Why this study exists (and what is deliberately new)

`gain_band_v2` (control = production R2, `--harness` verified) **FAILED as pre-registered on
gate G5 alone** — its pre-tax clean bootstrap required median Δ ≥ 0 and got −0.4 bp (A1) to
−4 bp (A2) with share ≈ 47–50 % — while **every after-tax gate passed**: $100k after-tax Δ vs R2
A1 +0.46 / +0.44 / +0.85 pp and A2 +0.79 / +0.93 / +1.81 pp on the 2000 / 2010 / 2017 windows,
B1-recent +0.23 pp (75.5 % of paths), B2-flat +0.09 pp (61.9 %), plus OOS, the 21 ETFs (pre-tax,
R-USD) and a 2× cost stress. The mechanism is a **tax-timing device**: suppressing downward
resizes that would realise an embedded indexed TRY gain cuts taxable FIFO realisations from
~31/yr to 6–11/yr, so its **pre-tax** effect is expected to be ≈ 0 and G5 cannot see the tax
channel by construction.

v3 therefore asks a narrower, honest question: **is the after-tax gain real and robust on
evidence v2 never looked at, and is the pre-tax cost inside a small non-inferiority margin?**
v2's own stages are re-run only as a *reference* block (they must reproduce v2 exactly); they do
not decide v3.

**Disclosure.** The pre-tax non-inferiority margin (E4: median Δ ≥ −0.10 pp instead of ≥ 0) was
chosen **after** seeing v2's G5 result. This is a deliberate, disclosed relaxation of the
pre-tax leg to a margin — not a hidden one, and not a change to any pre-tax return claim: v3
reports the same pre-tax bootstrap medians as v2 and the margin is stated in the gate table.
v3's verdict rests on E1–E3 (handled below), which v2 never ran.

## 1. Question

Does the gain-scaled downsize band (A1), the band plus the bracket budget (A2), and the
implementable USD-only variant (A1u) improve the after-Turkish-tax CAGR at $100k versus
production R2, on evidence v2 never examined (after-tax 21 ETFs under the real TRY calendar,
rolling start dates, fresh bootstrap seeds and blocks), while costing no more than a −0.10 pp
pre-tax bootstrap median and ≤ +1.0 pp of main-window drawdown?

## 2. Data and provenance

**Nothing is downloaded.** Every input is an existing committed research file, restored into this
worktree unchanged; `DATA_MANIFEST.json` records the source branch, byte count and SHA-256 of each
of the 29 files (identical to `gain_band_v2`'s inputs). Uses:
`stress_robustness_v1_{daily,dtb3}.csv` (QQQ/SPY/QLD bars, DTB3), `after_tax_tr_v1_{usdtry_cbrt,
yiufe}.csv` (TRY conversion and Yİ-ÜFE indexation), `ewma_band_v1_{gspc,index,tb3ms}.csv` (gate
G7's OOS windows), `range_vol_v2_*.csv` (19) + `range_vol_v1_{spy,dia,iwm}.csv` (the 21 ETFs;
`EWC` excluded by `range_vol_v2`'s fixed quality rule) — all re-verified in this worktree.

The TRY/Yİ-ÜFE calendar starts 2000-01 (USD/TRY) and 1999-01 (Yİ-ÜFE); there is no pre-2000
Turkish tax evidence, so the after-tax stages start no earlier than 2000-03-09.

## 3. Control (C0)

**Production R2, bit for bit**: R2's target series (E2 2-of-3 vote + the half-weight early
re-entry leg, sized `vt = min(1, 0.35/vol20)`, cap 0.98) with the production execution layer
(decide on the completed close, fill at the next open, symmetric 0.5 pp band, entries and exits
to zero always fill, idle cash zero, cost 4 bps × m × |Δw|, synthetic QLD* = daily-reset 2x on
QQQ total return, 0.95 %/yr expense, financing DTB3 + {base 0.0 %, calibrated 0.75 %}/yr),
through the single sequential engine `research/gain_band_v3/tax_engine.py` (byte copy of
`research/tax_harvest_v2/tax_engine.py`). Every arm label, control included, runs **R2's target
series**; only the fill decision differs.

**Anchors asserted in `--harness` (TOL = 1e-4 pre-tax, 0.005 pp after tax).** The control label
the run uses (`CONTROL = "C0"`) must reproduce:

| Quantity | Reference | Value |
|---|---|---|
| pre-tax CAGR, base, m = 2 / m = 1 | `fast_reentry_v1/RESULTS.json` main_window (R2) | 15.48 % / 15.75 % |
| pre-tax CAGR, calibrated, m = 2 / m = 1 | idem | 14.90 % / 15.17 % |
| pre-tax max DD, base / calibrated, m = 2 | idem | 46.76 % / 46.88 % |
| after-tax liquidated CAGR at $100k, three windows | `vt_level_tax_v1` + `r2_tbill_combo_v1` | 10.16 / 14.71 / 22.89 % |
| after-tax max DD at $100k, three windows | idem | 46.76 / 43.78 / 36.24 % |
| B1-recent / B2-flat bootstrap median after tax at $100k | `tax_harvest_v2` | 3.5155 % / 3.9678 % |

and the control's NAV path must differ from the pure-E2 anchor series (`E2`, 14.67 %/14.10 %).

## 4. Arms (4 labels; every parameter fixed, no tuning)

| Arm | Rule |
|---|---|
| **C0** | production R2 (constant 0.5 pp band, no other change) |
| **A1** | **gain-scaled band** (v2's A1): a downward resize fills iff `|tgt − w| > B_down(g_frac)`, `B_down` = **0.5 pp** if `g_frac ≤ 1 %`, **3 pp** if `1 % < g_frac ≤ 5 %`, **8 pp** if `g_frac > 5 %`. Up-resizes / entries / exits to zero keep 0.5 pp. No deferral state. |
| **A2** | v2's A2: A1 **+ bracket budget**. While `YTD net realised rule gain ≥ 1,000,000 × scale(year)` TRY (`scale(year)` = Yİ-ÜFE(previous October)/Yİ-ÜFE(2025-10), the year's deflated 27 %-bracket floor), a discretionary downward resize is **postponed** (the rule is re-evaluated at every later close, so it executes in the next tax year at the latest). Hard override: fill if `|tgt − w| > 15 pp`, or on the decision close at which the E2 vote turns off, or if the target ≤ 0. |
| **A1u** | **implementable simplification of A1**: identical tiers, identical thresholds (0.5 / 3 / 8 pp at `g_frac ≤ 1 % / ≤ 5 % / > 5 %`), but `g_frac` computed **in USD only** — FIFO over the live lot book, USD cost basis, **no FX conversion, no Yİ-ÜFE indexation, no per-lot floor** other than `gain ≥ 0` (the fraction is floored at 0; `gain_band` treats any `g_frac ≤ 1 %` identically, so the floor is a behavioural no-op). This is what the live bot can compute from its own fill ledger without a TRY calendar. |

**Shared mechanics (unchanged from v2).** Only a **downward resize** (`tgt > 0`, `tgt < held
weight`, `Δunits < 0`) can be suppressed — including the transfer between R2's full weight and
its half-weight early-re-entry leg, which is a downward resize between two positive targets and
is therefore subject to the arm's band. Entries, exits to zero and up-resizes always keep the
production 0.5 pp band (`B_up = 0.005`).

**A1/A2 decision state.** The **rule gain**: the net gain of the units the sale would consume,
computed FIFO over the live lot book with the engine's own indexation arithmetic (GVK mük. 81: if
Yİ-ÜFE(month before sale)/Yİ-ÜFE(month before purchase) − 1 ≥ 10 %, multiply the cost basis by
that factor; per-lot gain floors at 0 inside that branch) at the CBRT buying rate of the trade
date; proceeds are the same units × fill price, FX at the trade date, net of the trade's cost;
`g_frac = rule gain / rule proceeds`. `YTD` accumulates the same rule gain over every sale the
account makes in the calendar year (tax-instalment funding sales included), added after the fill.

**A1u decision state.** `g_frac = max(0, Σ_lots (take × per_unit − take × lot_usd_price)) / (units
× per_unit)`, `per_unit = (units × price − fee)/units`, no FX, no indexation.

**Instantiation rule.** R-INDEX (real USD/TRY + Yİ-ÜFE) where a TRY calendar exists: the main
window, the after-tax windows, both bootstraps, and **E1** (new: v2 ran its ETF stage under
R-USD). R-USD (`fx ≡ 1`, no indexation, budget inert, so A2 ≡ A1 and A1u ≡ A1) for the six OOS
windows and the 21-ETF pre-tax reference stage.

## 5. Stages and gates (pre-registered before any arm was simulated on real data)

Fixed stage parameters: main window 2000-03-09 → 2026-09-29 (6,679 measured sessions, session 253
onwards), $100k gate account (`PRE_CAPITAL`), m = 2, cost 4 bps × m × |Δw|, base financing unless
stated, progressive 2026 tariff deflated by the previous October's Yİ-ÜFE, **liquidated** at the
window end, idle cash zero.

**Reference block (does not decide v3).** v2's stages, re-run with seed 20261011 / block 20 /
5,000 pre-tax + 1,000 after-tax paths and 2,000 pooled-OOS paths per market: pre-tax main window
(base/calibrated × m = 1/2/4), the R-USD row, the after-tax windows at $10k/$100k/$1M, the m = 4
cost stress, the six OOS windows, the 21 R-USD ETF instruments, the clean bootstrap and the pooled
OOS bootstrap. Gate: **every cell v2 published for C0/A1/A2/A3 must match
`research/gain_band_v2/RESULTS.json` to 2e-6** (both files store `round(x, 6)`). A3 exists only
here.

### E1 — after tax on the 21 quality-passing ETFs under R-INDEX

v2's F3 list (`INSTRUMENTS`; `EWC` excluded by the fixed quality rule). Each ETF: its own adjusted
OHLC, its own E2 + own R2 early path on its close, its own synthetic QLD* leg (calibrated
financing, m = 2), **real CBRT USD/TRY + Yİ-ÜFE** market on the ETF's own trading dates, start =
`max(253 sessions of warm-up, first session ≥ 2000-03-09)`, $100k, progressive, liquidated at the
ETF window end.
**Gate: median after-tax Δ vs C0 > 0 AND share of ETFs with Δ > 0 ≥ 60 %.**

### E2 — rolling start dates on the QQQ main series

After tax at $100k (base financing, m = 2, liquidated), for every January start 2001…2021 (first
session ≥ January 1 of that year), each run to 2026-09-29 (21 runs). Signals use the full history
from 2000, so only the account's start date moves.
**Gate: Δ vs C0 > 0 in ≥ 70 % of starts AND median Δ > 0.**

### E3 — fresh after-tax bootstraps

`boot(seed = 20261015, block ∈ {10, 40}, n_pre = 0, n_tax = 1,000, capitals = ("100k",), modes =
{progressive (B1-recent), flat 20 % (B2-flat)}, arms = {C0, A1, A2, A3, A1u})` — 4 runs (2 blocks ×
2 modes), 1,000 paths each, $100k, the fixed 2015-09-01 → 2026-09-30 calendar with its realised
FX/Yİ-ÜFE, QQQ market returns resampled by block bootstrap; liquidated per path.
**Gate: each of the 4 runs has median Δ ≥ 0 AND share of paths with Δ > 0 ≥ 55 %.**

### E4 — pre-tax non-inferiority

The clean bootstrap of v2 (`seed = 20261011`, `block = 20`, 5,000 pre-tax paths, base **and**
calibrated financing, $100k account so the budget is active): **median Δ ≥ −0.10 pp**.
And the main-window pre-tax max DD at m = 2 (base and calibrated) ≤ C0 + 1.0 pp.
(Margin chosen after seeing v2's G5; disclosed in §0.)

### E5 — report only (no gate)

After tax at $30k and $300k on the three windows (base, m = 2, liquidated); and how often A1u's
fill decision differs from A1's on the main path (base, m = 2, $100k, R-INDEX, pre-tax so that
tax-funding sales do not interfere), decomposed into sessions where A1 sells and A1u holds and
vice versa.

**Pass = E1 ∧ E2 ∧ E3 ∧ E4.**

### Selection rule

Among the arms passing E1–E4: **prefer A1u** (implementable) **if its 2000-window $100k
after-tax Δ vs C0 is ≥ 70 % of A1's**; otherwise the passing arm with the **highest 2000-window
$100k after-tax CAGR**. If no arm passes, the verdict is "no candidate" and nothing is proposed
for production.

## 6. Run protocol and pre-registered expectations

`--smoke` (synthetic random-walk prices only; adds the A1u USD probe vs the engine's own sell
gain and the identity A1u ≡ A1 under R-USD), then `--harness` (control reproduction of §3), then
`--run` (reference block, E1–E5, gates, selection; writes `RESULTS.json` + `SUMMARY.txt`),
executed **twice** with byte-identical `RESULTS.json`.

Pre-registered expectations, stated before the run:

1. The reference block reproduces v2's `RESULTS.json` cell-for-cell (same code path, same seeds);
   any mismatch is a bug in v3.
2. E4's pre-tax median Δ for A1 and A2 lands where v2's G5 landed (≈ 0.0 pp / −0.03 pp base), i.e.
   inside the −0.10 pp margin; A1u should sit close to A1 (its rule differs only by the FX and
   indexation arithmetic) but need not be identical.
3. A1u's after-tax cells in E1/E2/E3 should resemble A1's, because the two rules agree whenever
   the lot book holds no old, heavily indexed lots; where they disagree, A1u (USD basis, no
   indexation) reads a **smaller** gain fraction, so it defers **less** and trades the band
   earlier than A1.
4. A2 remains the largest arm on the realised history (the budget) but the bootstrap punishes it
   most; the plateaus are not gated in v3.

## 7. Caveats and limits

As `gain_band_v2` §9 / `gain_band_v1` §9, unchanged: one history per cell; no true after-tax
out-of-sample (the TRY/Yİ-ÜFE calendar starts 2000); B1/E3-progressive holds the TRY calendar
fixed at 2015-09 → 2026-09 and B2/E3-flat charges no inflation; the liquidated convention charges
deferred tax at the window end; the rule reads the live FIFO lot book (indexation, trade-date FX);
FIFO, per-year netting without loss carry-forward, the 2026 tariff deflated by the previous
October's Yİ-ÜFE, the 10 % indexation floor and instalments funded by selling are the model's
assumptions, not tax advice. A2 ≡ A1 and A1u ≡ A1 in the R-USD stages by construction.

## 8. Amendments

None at the time of this commit. Any bug fix found after the run is recorded here, with its effect
on the arm cells, in the RESULTS/SUMMARY commit that follows.

## 9. Honesty note on the pre-run state

Before this commit, on this branch, the following ran: (a) one **timing probe** that executed
`engine_run` once for C0 and once for A1 on the main window (both v2's published arms; used only
to size the compute), (b) `--smoke` (synthetic prices only), and (c) `--harness` (the control's
anchors plus v2's unchanged `A1_nav_differs_from_C0` fidelity check). **No A1u cell, no E1–E5
stage and no gate outcome existed at the time of this commit.**
