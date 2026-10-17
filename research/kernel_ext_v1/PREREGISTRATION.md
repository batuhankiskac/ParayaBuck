# kernel_ext_v1 — pre-registration (committed before any non-control arm is screened)

Branch `research/kernel_ext_v1`, base commit `fc5dcfa3` (`research/batch_bank_v4b`). Study dir
`research/kernel_ext_v1/`. Report `docs/research/kernel_ext_v1.md`. The framework's own
`research/batch_screen/PREREGISTRATION.md` §6.7 is the dated amendment that records the kernel
extension this study adds, and is committed with this file.

## Question

Close the last **kernel-blocked** arms of the idea banks, with the kernel extended rather than the
arms approximated:

1. `IC36_N/B_N` — the bank's `IC36/B`: an off-state inverse-vol blend over **{GLD, IEF, SHY, XLU}**
   plus leg A = **five** instruments in one cash pool, more than the kernel's old `N_LEGS = 4`.
2. `IC41/A_N, B_N, C_N` — the bank's `IC41/A-C`: QLD\* **plus the S&P 500 2x leg** (`2x_spy`) with
   the **gain-aware downsize router** (`route_cap_pp = 10`, `route_score = "usd"`,
   `up_resize = "furthest_below_share"`), which existed only in the two-leg option copy.
3. `ib38/C` — the bank's `IB38/C`: a **routed split** — two FIFO books of the **same** instrument in
   one cash pool, downsizes routed to the cheaper FIFO head, route cap 10 pp.

`IC41/A-C` and `ib38/C` were registered `needs_kernel`; `IC36_N/B_N` was registered `needs_kernel`
on capacity. The question is whether any of the three families beats production **K** after Turkish
tax, on the frozen gate set.

## Control and reference

* **Control `k_control/K`** = production K (E2 + early re-entry, `equity_leveraged_trend_v1` 1.2.0,
  plus the A1u gain band and the H2 December deferral). Its record is computed once per declared
  window by the framework (`results/kernel_ext_v1/_control*.json`) and every arm's Δ is paired
  against it.
* **Reference `r2_control/R2`** (rule C0): reported, not gated.
* **K anchors the `--harness` must reproduce** (main window 2000-03-09 → 2026-09-29, m = 2):
  pre-tax CAGR **15.7649 / 15.1785 %** (base / calibrated), max DD **46.67 / 46.79 %**, after tax at
  $100k **10.7533 / 15.2354 / 23.9720 %** on the 2000 / 2010 / 2017 windows, and the six OOS windows,
  the 21 ETFs, the 21 January rolling starts and the B1/B2 bootstrap levels — every cell of
  `research/band_h2_combo_v1/RESULTS.json` through the framework, tol 0.01 pp (the framework harness).

## Arms (every parameter FIXED; nothing tuned)

The arms are the **frozen bank specs**, carried under new names because the old names are pinned in
the ledger (see §Method). Their instruments and parameters are the bank's; the kernel mechanism each
needed now exists.

| arm | idea | spec | window |
|---|---|---|---|
| `IC36_N/B5_N` | IC36/B | `rule K`; `legs ["2x","gld","ief","shy","sec_xlu"]`; `start "auto"`; while the E2 vote is **off**, hold `0.98 × (1/vol20_i)/Σ_j(1/vol20_j)` in the four sleeves, recomputed on the first session of each month; while on, leg A holds production's target and the sleeves are zero | `w20041119` (GLD 2004-11-18 →) |
| `IC41_R/A_N_R` | IC41/A | `rule K`; `legs ["2x","2x_spy"]`; each leg carries **0.5** of production's target; `route_cap_pp 10.0`, `route_score "usd"`, `up_resize "furthest_below_share"` | default |
| `IC41_R/B_N_R` | IC41/B | as A_N_R but leg weights **∝ 1/vol20** of each index (leg A the 2x leg's own vol, leg B the `2x_spy` leg's own vol), normalised to 1 | default |
| `IC41_R/C_N_R` | IC41/C | as A_N_R but the **early half-weight path is held only in the Nasdaq leg** (leg B holds half the on-state target only while the E2 vote is on, zero while early) | default |
| `ib38_C/C` | IB38/C | `rule K`; `legs ["2x","2x"]` — two FIFO books of the **same** instrument in one cash pool, **0.5** of production's target each; `route_cap_pp 10.0`, `route_score "usd"`, `up_resize "furthest_below_share"` | default |

Fixed readings (documented in each module; not tuned):

* `IC36_N/B5_N` is `IC36_N/B_N`'s frozen declaration with the capacity refusal lifted (same
  parameters), exactly as `IC36_N/A_N`/`C_N` were the runnable twins of `IC36/A`/`C`.
* `IC41_R/*` and `ib38_C/C` use the bank's **USD** FIFO gain fraction for the router ("the lower
  USD FIFO gain fraction"; IB28's only quantified reading); the kernel's `indexed` score is the one
  proven bit-for-bit against `fe2_opts`, and the USD score is the arms' own frozen choice.
* `ib38_C/C`'s "two accounts" is read as **two FIFO books of one instrument in one cash pool** (the
  task's frozen reading): each book carries half the production target, so the total stays under the
  0.98 cap. The consequence is stated: the 0.5 pp resize band acts on each book's own half-weight, so
  this split is *not* a relabelling of one full-weight book, and the bank's "two unrouted identical
  accounts reproduce K's NAV exactly" belongs to the two-independent-accounts construction, not to
  this one-pool one.
* `IC41_R/*`'s "up-resizes in the leg further below its share" is realised by construction: each leg
  carries its own fixed share, so on an up-resize the further-below book is exactly the one with the
  larger positive target difference; the router only re-allocates **downsize** units.
* Pre-tax and OOS stages run on the same kernel (the router is inert pre-tax only in the sense that
  it changes no *level*, only which book realises a resize). The OOS windows with no SPY series
  degenerate the `2x_spy` leg to the window's own `2x` leg (IC40's convention, the bank's own note).

## Data

No new data is downloaded. The study reads only committed inputs: `stress_robustness_v1_daily.csv`
+ `_dtb3.csv` (QQQ/SPY/QLD bars, DTB3 — the main window, the OOS/ETF markets and the bootstrap),
`batch_screen_v1_{gld,ief,shy,xlu}.csv` (the blend sleeves and the `2x_spy` cross-check),
`after_tax_tr_v1_{usdtry_cbrt,yiufe}.csv` (TRY + YI-UFE), `ewma_band_v1_{gspc,index,tb3ms}.csv`,
`range_vol_v2_*.csv` + `range_vol_v1_{spy,dia,iwm}.csv` (the 21 ETFs), and the two published evidence
files the harness anchors on. `DATA_MANIFEST.json` records each file's SHA-256 (copied from
`research/batch_screen/DATA_MANIFEST.json`, which pins the same files) and states that no input was
fetched.

## Mechanics

* Simulation, after-tax accounting and bootstraps go through the **fast engine**
  (`research/fast_engine.fe_sim`/`fe_tax`/`fe_boot` and their N-leg extensions in
  `research/fast_engine3`): `fe3_sim.sim3`/`simulate3`, `fe3_boot._boot_chunk3`/`_pt3`, one account,
  one cash pool, one units/FIFO book per leg, one Turkish taxpayer. The screen only builds
  target-weight panels with vectorised numpy.
* The kernel extension is `research/fast_engine3` (`N_LEGS = 5`, the router) and the declaration layer
  `research/batch_screen.api` / `bridge3` — see §6.7. Parity: `fe3_parity` **215 cells, 0 failed**
  (the new router group proves `route_score = indexed` reproduces `fe2_opts` bit for bit on the
  two-leg parity cells, including both per-leg rows; `route_cap = 0` is the published kernel), plus
  `python -m research.fast_engine.parity --quick` **PASS** and the framework harness **143 checks**.
* Batch `kernel_ext_v1` screens **only** the five new arms. `run.plan` iterates every arm of a listed
  module, so the study drives the framework's own plan/control/worker/scoreboard/deflation code with
  the arm set restricted (`research/kernel_ext_v1/run_batch.py`); **no framework file is edited by the
  driver** and no pinned arm is re-run or re-registered.

## Gates (fixed; identical to the framework's)

Stage 1 (main window + its block-20 bootstrap, paired vs K on the arm's own declared window):
after-tax 2000-window Δ > 0; B1-recent median Δ > 0; pre-tax max DD ≤ K + 2 pp (base and calibrated,
m = 2); turnover ≤ 2 × K (both models). Stage 2, for survivors: G1–G9 (ewma_band_v1's definitions,
G9 = turnover ≤ 2 × K), the after-tax decision (beats K at $100k in **all three** windows AND
B1-recent and B2 median Δ ≥ 0 with share > 0 ≥ 55 %), the 21 ETFs (E1) and the 21 rolling starts
(E2), the fresh-seed bootstraps (FRESH), the pooled OOS bootstrap (G8) — and an arm whose holdout is
not evaluable is `holdout_insufficient`, **not** a pass. Seeds/blocks/path counts are the framework's
(B1/B2 seed 20261011 block 20, 5,000 pre-tax / 1,000 after-tax paths; pooled OOS 2,000/market;
fresh seed 20261016 blocks 10/40; $100k).

No reduced gate is pre-registered: an arm whose declared window has no OOS/instrument coverage is
reported `holdout_insufficient` and is *not* a candidate.

## Selection rule

An arm is **declared** iff it passes stage 2 **and** the multiple-testing deflation
(`rw_stepdown` ≤ 0.05 **and** `deflated_sharpe` ≥ 0.95); `declared = []` if none. Everything else is
rejected, with the failing gates read from `gates.failed` / `stage1_gates`.

## Method notes (committed before the screen)

* New arm names, not edited specs: `IC41_R` and `ib38_C` are new modules (their parents carry a
  module-level `NEEDS_KERNEL`), `IC36_N/B5_N` is a new arm in `IC36_N.py`. Each parent module keeps
  its committed bytes and every pinned `spec_hash`; add-only is checked with
  `git diff -U0 fc5dcfa3 -- research/ideas/ | grep '^-[^-]'` (empty).
* The batch is run once (a second full run would append duplicate ledger lines for no evidence); the
  five per-arm records are then re-simulated through the framework's own `run._arm_task` and compared
  byte for byte, and `RESULTS.json`/`SUMMARY.txt` byte-identical over two `--run`s.
* Honesty: a `NaN` anywhere in a B1 Δ vector is reported and explained; an implausible bootstrap Δ
  (≫ the fixed-window Δ) is checked against `rw_boot_audit_v1`'s defect signature before reporting.
