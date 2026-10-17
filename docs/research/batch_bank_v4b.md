# Batch screen of the idea bank, batch 4b (batch_bank_v4b, 2026-10-17)

> Evidence: the N-leg kernel `research/fast_engine3/` (`docs/research/three_leg_kernel_v1.md`,
> `README.md`, `PARITY3.json`); the framework `research/batch_screen/` — its pre-registered
> pipeline is `README.md` (§1c the N-leg section) plus `PREREGISTRATION.md` §1–6.6, and its docs are
> `batch_screen_v1.md` / `framework_v2.md`; the five new `_N` arm declarations in
> `research/ideas/IC34.py`, `IC36_N.py` (new module), `IC41.py`, `IC45.py`, `IC46.py`; the ledger
> `research/batch_screen/REGISTRY.jsonl`; per-arm records
> `research/batch_screen/results/bank_v4b/` (+ one `_control_*.json` per declared window); the
> deflation `research/batch_screen/results/DEFLATION.json`; this study's `PREREGISTRATION.md`,
> `run_batch.py`, `SUMMARY.txt` and the runner's stdout
> (`research/batch_bank_v4b/RUN_STDOUT.txt`). Ideas: `docs/research/idea_bank_v2.json` (IC34, IC36,
> IC41, IC45, IC46). Batch `bank_v3` is `batch_bank_v3.md`; the leg-B bootstrap defect the audit
> found is `rw_boot_audit_v1.md`. The per-study discipline of the common protocol is replaced by the
> framework's pre-registered pipeline, as in `bank_v3`: **no plugin's old arm, no gate and no
> framework file was touched**, the arms were committed *before* the screen, and the study's only
> code is the batch driver, which calls the framework's own functions (§Method).

**Verdict: no candidate.** Nine idea-bank arms that `bank_v3` had to refuse as `NEEDS_KERNEL` were
re-declared on the N-leg kernel (`"legs": [...]`, `margin_spread`). Five are now simulated and four
are still refused, for reasons checked in the code; **one of the five passes Stage 1
(`IC45/C_N`, the gold sleeve + loan) and its holdout is not evaluable at all** (0 of the 6 OOS
windows, 0 of the 21 instruments — GLD starts 2004-11-18), so it is `holdout_insufficient`, not a
pass; **all 5 fail Stage 2 or Stage 1** (`reject_stage1` ×4, `holdout_insufficient` ×1);
`declared = []`. Nothing here is a candidate for an owner decision, and nothing is queued for a
weaponisation study except the one `holdout_insufficient` arm, which would need new windows
(§`IC45/C_N`).

| stage | arms |
|---|---|
| registered in this batch (`REGISTRY.jsonl`, `batch = bank_v4b`) | 9 (5 ideas) |
| simulated | 5 (`IC34/C_N`, `IC36_N/A_N`, `IC36_N/C_N`, `IC45/C_N`, `IC46/C_N`) |
| refused: 5 instruments > `N_LEGS` = 4 | 1 (`IC36_N/B_N`) |
| refused: the gain-aware downsize router does not reach `fe3_sim` | 3 (`IC41/A_N`, `IC41/B_N`, `IC41/C_N`) |
| Stage 1 (main window + its block-20 bootstrap): pass | 1 (`IC45/C_N`) |
| Stage 2 (holdout): pass | **0** |
| Stage-2 rejection mode | `reject_stage1` 4, `holdout_insufficient` 1 |
| declared after the multiple-testing deflation | **0** |
| old arms re-simulated, re-specified or re-registered | **0** |

N (the deflation's multiple-testing count) = **312** distinct `(idea, arm, spec_hash)` over the whole
campaign ledger (539 lines; `bank_v4b` contributes 9 of the 312 — 5 `screened`, 4 `needs_kernel`;
N was 303 before). `white_rc`'s `n_arms` is 5 — every simulated arm of this batch has a finite B1 Δ
vector. Wall clock of the batch: **91.9 s** on 6 workers including the three paired controls
(`results/bank_v4b/timing.json`, `total_wall_s = 91.857819`; per arm 18.7–28.3 s). The control
`k_control/K` (= production K) reproduces K's published anchors through the framework harness —
**139/139** checks, engine parity `--quick` **PASS**: default-window pre-tax m = 2 CAGR
15.7649 / 15.1785 % base / calibrated, max DD 46.67 / 46.79 %, after tax at $100k
10.7533 / 15.2354 / 23.9720 % on the 2000 / 2010 / 2017 windows (`research/batch_screen/HARNESS.json`,
`results/bank_v4b/_control.json`).

## What became expressible, and what did not

`three_leg_kernel_v1` gave the account up to **four** instruments in one cash pool, the production
execution rule per leg on that leg's own FIFO book, one Turkish taxpayer, and a **margin loan** on a
negative cash balance (`fe3_sim.sim3`'s `mrate`). Against that:

| idea / arm | what it needed | outcome |
|---|---|---|
| `IC34/C` | QLD\* with XLU **and** XLP at once | **expressed**: `IC34/C_N`, `legs ["2x","sec_xlu","sec_xlp"]`; simulated |
| `IC36/A` | a 3-instrument inverse-vol blend in one account, rescaled monthly | **expressed**: `IC36_N/A_N`, `legs ["2x","gld","ief","shy"]`; simulated |
| `IC36/C` | the same, the blend's total = production `vt` | **expressed**: `IC36_N/C_N`; simulated |
| `IC36/B` | the same over {GLD, IEF, SHY, **XLU**} | **still blocked**: leg A + four sleeves = 5 instruments > `N_LEGS` = 4 |
| `IC41/A–C` | QLD\* + the S&P 500 2x leg (`"legs": ["2x","2x_spy"]`, one FIFO book per index) **and** a downsize router to the leg with the lower USD FIFO gain fraction under a 10 pp cap | **still blocked**: the account is available, the *router* is not — see below |
| `IC45/C` | 0.88 × production + the frozen gold sleeve, funded past the 0.98 cap by a loan | **expressed**: `IC45/C_N`, `legs ["2x","gld"]`, `margin_spread` 0.015; simulated |
| `IC46/C` | 0.90 × production + the frozen IEF sleeve, funded past the cap by a loan | **expressed**: `IC46/C_N`, `legs ["2x","ief"]`, `margin_spread` 0.015; simulated |

**Why `IC41` stays refused (`NEEDS_KERNEL_ROUTER`).** Its old refusal — leg B must be an S&P-500 2x
book — is now out of date: `bridge3.prepare` builds `2x_spy` on demand from the window's own SPY
column, so `"legs": ["2x","2x_spy"]` really is two books with one FIFO queue each. What is *not*
available is the arm's defining mechanism. `route_cap_pp` (fe2_opts, the two-leg option copy) is the
only implementation of "route a downsize to the leg whose USD FIFO gain fraction is lower, bounded
by 10 pp"; `api.resolve_arm` refuses `fill_knobs` on any `legs` declaration (a multi-leg arm may
carry only `margin_spread`), and `fe3_sim.sim3` has no cross-leg routing at all — each leg's own
band is judged and filled on its own lot book (`research/fast_engine2/fe2_opts.py` line 390 ff. vs
`fe3_sim.sim3`'s per-leg pass 2; both checked, as the brief asks). Screening the 50/50 weight split
alone would silently drop the reason the idea exists, so the three `_N` arms are registered with
their frozen specs and that reason and are **not** approximated.

**Why `IC36`'s runnable arms are a second module.** `research/ideas/IC36.py` carries a
**module-level** `NEEDS_KERNEL`, which `api.resolve_arm` applies to *every* arm of the module
(`api.py` line 424). It cannot stay while new arms in the same file become runnable, and adding a
per-arm `needs_kernel` to the three old arms would re-hash their specs — `verify_ledger` reads that
as `spec_drift` and refuses every batch. The module is therefore left **byte-for-byte** as
committed and the runnable arms live in `research/ideas/IC36_N.py` under the `_N` names — precisely
the pattern `research/ideas/IC59_K.py` documents for IC59 (and `batch_bank_v3.md`'s addendum
screened as batch `bank_v3b`). `IC34`, `IC45` and `IC46` instead use *per-arm* `needs_kernel`, so
their new arms go in the same file; `IC41`'s are refused in theirs. The brief's "in the same
modules" is met wherever the module's own refusal structure allows it.

**Add-only.** `git diff -U0 851bcbae -- research/ideas/ | grep '^-[^-]'` prints nothing, and every
old arm's `spec_hash` re-computes unchanged (`IC34/A-B-C`, `IC36/A-B-C`, `IC41/A-B-C`, `IC45/A-B-C`,
`IC46/A-B-C`, verified against the blobs at `851bcbae`). The new arms live in new `spec["legs"]`
branches (`IC34`, `IC45`, `IC46`) and new modules (`IC36_N`), so an old arm takes the identical code
path — which is what the smoke's `OK` rows for `IC34/A-B`, `IC45/A-B`, `IC46/A-B` and the
`NEEDS_KERNEL` rows for the old refused arms show.

## Per-arm table (Δ vs K in pp, on the arm's own declared window, against K re-simulated there)

Stage-1 gates: `AT` = after-tax $100k 2000-window Δ > 0, `B1` = B1-recent median Δ > 0,
`DD` = pre-tax max DD ≤ K + 2 pp in *both* financing models, `TO` = turnover ≤ 2 × K in both.

| arm | window | AT2000 Δ | AT2010 Δ | AT2017 Δ | B1 med / share | B2 med / share | maxDD Δ (b/c) | turnover ×K | failed gates | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| `IC34/C_N` | default | **−0.8236** | +2.5593 | +0.6504 | +0.3521 / 0.559 | −0.2120 / 0.466 | +16.32 / +16.26 | 2.084 | AT, DD, TO | reject_stage1 |
| `IC36_N/A_N` | **w20041119** | **−0.5376** | −0.4954 | −0.4179 | **−0.7232** / 0.265 | −1.1753 / 0.144 | +1.35 / +1.34 | 1.998 | AT, B1 | reject_stage1 |
| `IC36_N/C_N` | **w20041119** | **−0.4941** | −0.4260 | −0.3186 | **−0.8918** / 0.223 | −1.1257 / 0.148 | +1.54 / +1.54 | 1.924 | AT, B1 | reject_stage1 |
| `IC45/C_N` | **w20041119** | **+0.5029** | **−0.0137** | +0.0549 | +0.8978 / 0.705 | +0.6050 / 0.654 | −5.73 / −5.83 | 0.975 | G4, G7, G8, AT_2010, E1, HOLDOUT_INSUFFICIENT | holdout_insufficient |
| `IC46/C_N` | **w20020731** | **−0.5770** | −0.9763 | −2.3834 | −0.0754 / 0.472 | −0.3961 / 0.363 | −4.84 / −4.86 | 0.922 | AT, B1 | reject_stage1 |
| `k_control/K` | — | 0 (control) | 0 | 0 | 0 | 0 | 0 | 1.000 | — | control |

Bold = the cell that fails. `maxDD Δ` is pre-tax base/calibrated at m = 2; `turnover ×K` is the base
model (the calibrated model agrees to within 0.001 × K on every row). Every Δ is finite: all five `b1_delta_pp` vectors are
1000/1000 finite and `DEFLATION.json`'s `excluded_non_finite` is `{}` — no NaN appears anywhere in
this batch's records, so the leg-B chunk repair (`rw_boot_audit_v1`) holds for these **panel**- and
**extra-key**-leg arms alike (the loans' leg B is GLD/IEF, the blend's are GLD/IEF/SHY, and the
blend's sleeve vol is the modules' own NaN-aware estimator, §Method).

Read plainly: the two "off-state" families are **negative** on their own window and on both later
windows — holding a defensive sleeve instead of the early half-weight QLD path when E2 is off costs
more than it saves, pre- and post-tax. `IC34/C_N` is the worst of them **and** breaks the drawdown and
turnover gates (+16.3 pp DD, 2.084 × K): it holds a full 0.98 of defensive equity where production
holds cash or half-weight QLD, so the drawdown grows (the family's pattern — `IC34/A` was +20.9 pp in
`rw_fix_v1` — and both sector books trade ~360 times). `IC46/C_N`'s negative every-window row is the
`r2_tbill_combo_v1` lesson repeated — a duration sleeve held into the equity bull market loses after
tax, permanent or state-keyed. `IC36_N/A_N` and `C_N` differ only by the `0.98 vs vt` total and land
within 0.1 pp of each other; the blend's turnover sits at the gate (1.998 × K for A_N, 1.924 for C_N)
because the extra books trade on top of leg A — per-leg turnover units on the A_N window are leg A
116.2, SHY 91.7, IEF 16.6, GLD 7.2.

## `IC45/C_N` — the one Stage-1 survivor, and why it is not declared

`IC45/C_N` is the only arm of the batch with a real after-tax edge on its own window:

| cell | value |
|---|---|
| after tax at $100k, 2004-11-19 → 2026-09-29 | **14.0655 %** vs K's 13.5626 % → **+0.5029 pp** |
| after tax 2010 / 2017 windows (clipped) | −0.0137 / +0.0549 pp |
| pre-tax m = 2 Δ (base) | +0.541 pp; max DD Δ −5.73 / −5.83 pp (32.73 % vs K's 38.46 %) |
| turnover | 0.975 × K |
| B1-recent / B2 medians | +0.8978 pp (share 0.705) / +0.6050 pp (share 0.654) |
| its B1 Δ vector (1000 paths) | median +0.8978, mean +0.780, sd 1.653 pp; p5/p95 −2.086 / +3.279; min/max −6.075 / +5.434 → **no fat tail**, advantage-Sharpe 0.4717 |
| fresh seeds (seed 20261016, blocks 10/40) | all four cells PASS: +0.9059/0.721, +0.6447/0.659, +0.7765/0.665, +0.5109/0.633 |
| 21 January rolling starts | 17 applicable (window starts 2004-11), median +0.2432 pp, share ≥ 0 = 0.706 |
| loan actually charged | `opts_stats.margin_usd` = 15,857 USD over the window (the excess over 0.98 is borrowed at DTB3 + 1.5 pp) |
| **failed gates** | **G4, G7, G8, AT_2010 (−0.0137 pp), E1, HOLDOUT_INSUFFICIENT** |

**Plausibility, checked as the brief asks.** Its bootstrap Δ is +0.90 pp (median) against fixed-window
Δs of +0.50 pp (2000), −0.01 pp (2010) and +0.05 pp (2017) — the **same order of magnitude**, which is
the opposite of the `rw_boot_audit_v1` defect signature (a bootstrap Δ ≈ 20 × the window Δ: `IC37/C`'s
+12.2 pp against +0.59 pp, whose B1 collapsed to −0.24 pp once the chunk leg B was rebuilt from the
drawn sessions' own returns). Its 1000-path Δ vector is finite everywhere with no fat tail at all
(mean +0.78 pp, sd 1.65 pp, max +5.4 pp, 29.5 % of paths negative) — unlike the pre-fix `IC45/A`,
whose fat tail (a handful of paths of hundreds of pp) drove its Sharpe to 4.05; here the Sharpe is
0.472 and the tail is ordinary. Its fresh seeds agree with B1/B2, and the two pre-tax financing models
agree to 0.1 pp. Its inputs are GLD (a Yahoo ETF print) and the panel's own QQQ/SPY triple — **no
revised macro series** of the kind `ic12_confirm_v1` warns about (the NFCI vintage trap does not
apply). I find nothing implausible.

**But it is not a pass and nothing is declared.** It fails Stage 2 outright on the after-tax 2010
window (−0.0137 pp) and its holdout is *not evaluable*: `leg_b_usable` refuses **all six** OOS windows
and all **21** instrument windows because GLD has no price before 2004-11-18 (even `EEM`, whose window
starts latest, begins 2004-04-14), so `G7`, `G8` and `E1` fail by construction and the record is
`holdout_insufficient`. That is the `IC37/C` situation (`batch_bank_v3.md`): it needs its own
pre-registered confirmation study — **new OOS windows and a re-based instrument set whose windows
start after 2004-11-18**, or a reduced gate fixed in advance — before any claim. The deflation also
fails it: `rw_stepdown` "rejects" it (`p_adj = 0.0000`, which per `batch_bank_v3.md`'s caveat only
means its Δ vector is positive) but its advantage-Sharpe 0.4717 is far below `sr0 = 1.297`, so
`dsr = 0.0 < 0.95`.

## Deflation (`results/DEFLATION.json`, batch `bank_v4b`)

N = 312; 2,000 second-level resamples; seed 20261016, α = 0.05; Δ vectors = each arm's B1-recent
after-tax progressive Δ in pp (the pre-registered input).

* **5 of the 5 simulated arms have a finite Δ vector**; `excluded_non_finite = {}`, `n_arms = 5`.
* `white_rc`: `t_max = 14.9167` for the best arm `IC45/C_N`, `p = 0.0`.
* `rw_stepdown` rejects **2 of the 5** at 5 %: `IC45/C_N` (`p_adj = 0.0000`) and `IC34/C_N`
  (`p_adj = 0.0385`); the three negative arms are not rejected.
* `deflated_sharpe`: `var(SR) = 0.2454`, `sr0 = 1.296996` (N = 312); the best observed
  advantage-Sharpe is **0.4717** (`IC45/C_N`: Δ median +0.8978 pp, mean +0.780 pp, sd 1.653 pp over
  its 1000 paths) → `dsr = 0.0`; for every other arm `dsr = 0.0`, `pass_5pct = false` for all five.
* `stage2_pass = []`, `declared = []`. A candidate needs Stage 2 **and** `rw_stepdown` ≤ 0.05
  **and** `dsr` ≥ 0.95; the batch reaches the first two at most.
* **Reported framework bug (display only, not fixed — no framework file is edited by this study).**
  `arm_sharpe`'s `mean_pp` / `sd_pp` cells (`research/batch_screen/deflation.py` lines 133/137) are
  the Δ sample's mean/std multiplied by 100, although every input to the deflation is *already* in pp
  (`b1_delta_pp`). For `IC45/C_N` they read `mean_pp = 78.032` / `sd_pp = 165.425` where the vector's
  mean/sd are 0.780 / 1.653 pp — a factor 100. Every decision-relevant cell (`sharpe`, `var_sr`,
  `sr0`, `dsr`, the White/Romano-Wolf p-values) is scale-free and therefore unaffected, and
  `batch_bank_v3.md`'s quoted `IC45/A` "mean Δ 2,197.8 pp" is the same artefact (its real mean is
  21.98 pp). One line in the framework's report-writer; no number in any verdict changes.

Because three different windows are in play, `DEFLATION.json`'s `window_tags` names them
(`default`, `w20020731`, `w20041119`): an arm's Δ is its paired advantage over K *on its own
window*, which is the comparable quantity, but these p-values are not the published single-pool
ones (`README.md` §4.6).

## What remains blocked, and why

* **`IC36_N/B_N` (the bank's `IC36/B`)** — the {GLD, IEF, SHY, XLU} blend is leg A plus **four**
  sleeves: five instruments in one cash pool, one more than `N_LEGS = 4`. Needs a kernel with
  `N_LEGS ≥ 5` (a copy of `fe3_sim` plus its parity cells), not a plugin.
* **`IC41/A_N`, `IC41/B_N`, `IC41/C_N` (and the pinned `IC41/A-C`)** — the path is an S&P 500 2x
  book, but the arms' *mechanism* is the gain-aware downsize router (`route_cap_pp`, 10 pp), which
  does not reach the N-leg kernel (checked in the code, §`What became expressible`). Needs a router
  in the N-leg kernel plus a parity cell proving it inert when off.
* **The old arms stay pinned.** `IC34/C`, `IC36/A-C`, `IC41/A-C`, `IC45/C`, `IC46/C` keep their
  `bank_v3` `needs_kernel` ledger lines, their specs and their `spec_hash`. `IC34/C`, `IC45/C` and
  `IC46/C` are now pinned *only by the ledger text* — their runnable twins are the `*_N` arms here.
  `IC36/A-C` are also pinned by `IC36.py`'s module-level declaration; their runnable twins are
  `IC36_N/A_N` and `IC36_N/C_N` (`B_N` stays blocked on capacity). `IC41/A-C`'s twins are also
  refused.
* **Unchanged from `bank_v3`:** `IC59/A-C` (an asymmetric band — expressible since
  `kernel_opts_v1`, but its three original arms are pinned; its runnable twins were screened as
  `bank_v3b`) and `IC54/A-B` (a data gap: no intraday fill series).

## Method, checks, determinism

* **The batch screens only the new arms.** `research.batch_screen.run.plan` iterates `mod.ARMS` and
  has no per-arm selector, so running the CLI over the five modules would re-simulate and
  re-register the pinned old arms. `research/batch_bank_v4b/run_batch.py` therefore rebinds the
  runner's `plan` global **in its own process** with a restricted arm set and otherwise calls the
  framework's `run_batch` unchanged — the same control computation, the same `run._arm_task`
  workers, the same `SCOREBOARD`/`RESULTS`/`timing`/deflation writers. **No file in
  `research/batch_screen/`, `research/fast_engine*` or `research/ideas/` existing arm changed**; the
  batch dir holds exactly the 5 new arm records and the 3 control files, with no rerun line for an
  old arm in the ledger.
* **Everything existing was left alone.** No gate, seed, sample size, window or rule was changed;
  `run --verify-registry` PASSes (539 lines, 312 arms, N = 312 — `stale` lists the 12 old arms of
  the edited modules, which is information, not an error), and `run --harness` is **139/139**
  (engine parity `--quick` PASS), the same count as `three_leg_kernel_v1`.
* **Determinism.** After the batch, all five arms were re-run through the framework's own
  `run._arm_task` with the same controls and compared byte for byte: **5/5 identical** (`cmp`), the
  same verdicts, the same numbers. The framework's writer rounds every float to 6 dp and sorts every
  container before serialising, which is what makes that identity hold. The batch itself was run
  once, as `bank_v3` was: `run.plan` has no silent mode, so a second full run would append nine
  duplicate ledger lines for no evidence.
* **Runs of the engine.** Every simulated arm went through `fe3_sim.simulate3` (one N-leg window),
  `fe3_boot._boot_chunk3`/`_pt3` (the block bootstrap and the pooled OOS) and the shared
  `fe_sim`/`fe_tax` primitives — no new per-day or per-path Python loop, and `research/fast_engine/`
  is untouched. An arm whose targets return a `{leg: wants}` dict is routed to the N-leg kernel by
  `bridge3.needs_kernel3`; a two-leg arm with a loan is this kernel's job with `margin_spread`; the
  study's own `--check` mode asserts the routing, the two loans (`margin_usd` > 0 and both books
  trading) and the `_roll_vol` identity below.
* **Fixed conventions, documented in the modules (not tuned).** (i) The blend/sleeve 20-session vol
  does not exist for the reduced window's first 19 sessions (the window starts one session after the
  last input's first), so the affected sleeve is held in **cash** (weight 0) there. (ii) `vol20` of
  an extra-data leg is that leg's own price path (`data.leg_series`), the series the kernel trades,
  and the modules carry a **NaN-aware** rolling vol: it is `fe_signals.vol_n` **bit for bit** on a
  gap-free series (asserted) and it recovers after a leading gap, where `vol_n`'s cumsum never does
  (GLD's first 19 window sessions). (iii) `margin_spread = 0.015` for `IC45/C_N` / `IC46/C_N`: the
  bank fixes no spread, so IC39's frozen broker rate over the window's own DTB3 column is used — the
  IC39 convention, stated at the top of both modules.
* **Harness before the batch:** `research.fast_engine.parity --quick` PASS;
  `research.batch_screen.run --harness` PASS 139/139 in 87.1 s;
  `smoke --ideas IC34,IC36,IC36_N,IC41,IC45,IC46` — the five new arms `OK` on all five panels
  (synthetic, main, `oos:M2`, `etf:XLK`, boot), the `NEEDS_KERNEL` rows being the pinned old arms
  and the four new refusals.
* **Wall clock.** Batch **91.9 s** on 6 workers (`results/bank_v4b/timing.json`): 20.2 / 23.4 /
  23.5 / 28.3 / 18.7 s for `IC34/C_N` / `A_N` / `C_N` / `IC45/C_N` / `IC46/C_N`, plus the three
  controls (19.0 + 20.0 + 19.6 = 58.6 s, computed once per declared window).

## Amendments and bugs

One bug, found by the smoke and fixed **before any arm was simulated** (so it is not an amendment
after data, and no gate, arm, parameter or window was ever changed):

* `run_batch.py --check` and the smoke: the first draft of the modules' NaN-aware `_roll_vol` sliced
  a `(P, T+1)` cumulative array against a `(P, T)` output, so **every** panel raised
  `ValueError: could not broadcast input array from shape (P, T-1+1) into (P, T-1)`. Fixed by
  building the cumulative sums from the `(T-1)`-length return series exactly as `fe_signals.vol_n`
  does; `--check` then asserted bit-identity with `vol_n` on a gap-free series (two path counts) and
  the leading-gap behaviour (first finite column ≡ gap start + 20 vs `vol_n`'s never).

* **Reported, not fixed** (framework files are off-limits to this study): `arm_sharpe`'s
  `mean_pp` / `sd_pp` cells are the pp Δ sample's mean/sd multiplied by 100 — a display-only factor
  that changes no decision (§Deflation).

Two deviations from the brief's literal instruction, both deliberate and argued above:

* **`IC36`'s runnable arms are in a new module** (`IC36_N.py`) rather than the same file, because
  `IC36.py`'s module-level `NEEDS_KERNEL` refuses every arm of that file and can be lifted only by
  re-specifying the three pinned old arms (`verify_ledger` → `spec_drift`). The `IC59_K` precedent.
* **The batch is driven by a study-local script** rather than the CLI, because the CLI has no
  per-arm selector and the brief requires that the pinned old arms not be re-screened.

## Caveats

1. Δ is always `arm − K` on the same resampled paths and on the arm's own declared window; arms on
   three different windows are not directly comparable, and the deflation's pools are named in
   `DEFLATION.json`.
2. Stage 1's pass rule is a strict `> 0`, so an arm equal to K passes on rounding noise; none of
   these five is (the smallest |AT2000 Δ| is 0.4941 pp).
3. Two of the five arms have a turnover ratio within 0.2 % of the 2 × K gate (`IC36_N/A_N` 1.998,
   `IC36_N/C_N` 1.924): the monthly inverse-vol rescue trades SHY hard. A slightly different
   rescale rule would flip those two Stage-1 cells, which is another reason not to read the row as
   "close".
4. 20-session blocks cannot capture serial dependence beyond 20 sessions, so the p-values are
   optimistic under long memory; N = 312 is the whole campaign ledger.
5. Every arm of this batch has no usable OOS window and (except `IC45/C_N`'s single `EEM`) no usable
   instrument window, because GLD/IEF start in 2004/2002: a Stage-1 survivor here can only ever be
   `holdout_insufficient`. Its Δ is still valid **on the published protocol's own terms** — a paired
   advantage over K on its own window — but the level on a window that can start no earlier than
   2004-11-19 is not the published 2000-window level (K itself compounds at 19.70 % pre-tax there
   against 15.76 % on the published window).
6. Ideas and arms were fixed by committed plugin code *before* any result existed; the plugin
   modules and the framework are unchanged by this study's numbers apart from the new arms and the
   new module. Batches 1–3 (`bank_v1`, `bank_v2`, `bank_v3`, `bank_v3b`) stay in N.
