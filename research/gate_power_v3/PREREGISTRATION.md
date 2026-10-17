# gate_power_v3 — pre-registration (audit of the batch screen's power; third pass)

Committed **before any non-control arm of this study is simulated**. This is an **audit**, not a
trading-idea search: no new execution mechanism is proposed and no gate of the campaign is changed
here. The study re-checks the two earlier passes (`gate_power_v1`, `gate_power_v2`), whose reports
are treated as **untrusted**, and every number below is written to a file this study commits.

## 1. Question

The campaign screened ~100 ideas / **303 registered arms** against production K with the
`research/batch_screen` two-stage protocol and declared **0**. Owner's question: *can the screen
detect a real improvement, or is it too strict / buggy?* This study measures the screen's power
with known-edge synthetic arms, audits the multiple-testing (deflation) step, and tallies the 303
real verdicts from the committed records.

## 2. Control and data

* **Control C0 = production K** (`k_control/K`): E2's 2-of-3 vote + the half-weight early re-entry,
  sized `min(1, 0.35/vol20)`, cap 0.98, decided at the close, filled next open, 0.5 pp band, under
  `fe_sim.RULE["K"]` (A1u band + H2 December deferral). Its published anchors
  (`research/band_h2_combo_v1/RESULTS.json`, m = 2): pre-tax CAGR **base 15.7649 / calibrated
  15.1785 %**, max DD **46.6698 / 46.7948 %**; after tax $100k **10.7533 / 15.2354 / 23.9720 %**
  on the 2000 / 2010 / 2017 windows.
* **Alternate controls** for the positive controls: `r2_control/R2` (rule C0), `a1u_control/A1u`
  (rule A1u), `e2_control/E2` (rule C0, no early re-entry). All three plugin modules are committed.
* **Data**: every input is an existing committed file (see `DATA_MANIFEST.json`); no download. Main
  window QQQ/QLD* 2000-03-09 → 2026-09-29, base (`spread = 0`) and calibrated (`0.0075`)
  financing, `EXP_Q = 0.0095`, `m = 2`, next-open fills; Turkish after-tax engine
  (`fe_tax`, CBRT USD/TRY, FIFO, Yİ-ÜFE, 2026 tariff), capital $100k; stage-1 bootstrap seed
  **20261011**, block **20**, 5,000 pre / 1,000 after-tax paths; stage-2 holdout exactly as
  `batch_screen` §3 (six OOS windows, 21 ETFs, 21 January rolling starts, fresh seeds
  **20261016** blocks 10/40, pooled OOS bootstrap).
* **Engine**: `research/fast_engine` only. Simulation, after-tax accounting and bootstraps go
  through `fe_sim` / `fe_tax` / `fe_boot`. This study's code builds target-weight arrays and
  selects the leg; it writes **no** per-day or per-path Python loop.

## 3. Framework used (fixed)

The study drives the **campaign's own** stage/orchestration code, copied verbatim into
`research/gate_power_v3/` from `research/batch_screen/` (`api boot deflation extra registry rules
stages windows`), except `api.py`'s synthetic-leg construction (§5). The campaign copy is neither
edited nor run in place; the copied modules are the same bytes (only the synthetic-leg block and
its `SYNTH_LEGS` table differ). The gates, seeds, sample sizes and deflation are the campaign's.

## 4. Positive controls (fixed arms; predecessor as control)

| batch | candidate | control | pre-registered expectation |
|---|---|---|---|
| `pc_r2_vs_e2` | `r2_control/R2` | `e2_control/E2` | **Stage 2 pass** (R2 is a deployed improvement) |
| `pc_a1u_vs_r2` | `a1u_control/A1u` | `r2_control/R2` | Stage 1 pass, **Stage 2 fail on G5_calibrated** |
| `pc_k_vs_a1u` | `k_control/K` | `a1u_control/A1u` | **Stage 2 pass** |
| `pc_k_vs_r2` | `k_control/K` | `r2_control/R2` | Stage 1 pass, **Stage 2 fail on G5_base + G5_calibrated** |

These expectations are the campaign's own verdicts (`docs/research/gain_band_v2.md`): the A1u/H2
layer is an after-tax improvement whose *pre-tax* bootstrap median stays near zero, so it fails G5
— an owner-accepted tension, reproduced here, not a gate bug.

## 5. Synthetic power arms (fixed; the v2 defect corrected)

The whole point of an audit of power is a known edge. An arm holds **production K's own target
series, unchanged** and its edge is its **leg**, one of the pinned instruments of
`api.SYNTH_LEGS`. Each leg's parameters are read **only from its own name**, in the panel builder:

| arm | leg | edge |
|---|---|---|
| `syn_null0` | `2x` | none (production K's own leg; the identity cell) |
| `syn_d0` | `syn_all_0000` | zero-magnitude drift (the drift machinery's identity cell) |
| `syn_d025` | `syn_all_0250` | +0.25 pp/yr on every session |
| `syn_d050` | `syn_all_0500` | +0.50 pp/yr |
| `syn_d100` | `syn_all_1000` | +1.00 pp/yr |
| `syn_d200` | `syn_all_2000` | +2.00 pp/yr |
| `syn_inv200` | `syn_inv_2000` | +2.00 pp/yr, only while the trend vote is invested |
| `syn_pre200` | `syn_pre_2000` | +2.00 pp/yr, only on invested sessions whose **drawn source** year < 2013 |
| `syn_noise_p` | `syn_np_0500` | mirrored i.i.d. noise, `eps ~ N(0, 0.5 %)`, fixed seed |
| `syn_noise_m` | `syn_nm_0500` | the mirror of `syn_noise_p` |

The drift multiplies the leg's open and close by a cumulative factor `(1 + d/252)`, so it adds
`≈ d/252` to every session's leg return and flows through the engine (and the bootstrap) as an
ordinary return; it is taxed like any gain. **This replaces `gate_power_v2`'s env-global
`GATE_POWER_DRIFT`**, which applied one batch-level drift to every synthetic leg and made
`syn_null0` a copy of `syn_edge` — the defect this study found and fixes. `syn_null0` and `syn_d0`
must reproduce K **bit for bit** (`b1_delta_pp` all zero, Δ = 0), and `--harness` checks every
leg's own price path (the identity, the per-session drift increment, the noise mean/sd/mirroring).

## 6. Gates, selection and reporting (unchanged)

An arm is a **Stage-1 pass** iff `after-tax 2000-window Δ > 0` AND `B1-recent median Δ > 0` AND
`pre-tax max DD ≤ K + 2 pp` AND `turnover ≤ 2× K` (base and calibrated, m = 2). A survivor runs the
full holdout (G1–G9, after tax on all three windows, B1/B2 median ≥ 0 with share > 0 ≥ 55 %, the 21
ETFs, the 21 January starts, the fresh seeds, the pooled OOS bootstrap). The framework's
declaration additionally needs `rw_stepdown` p ≤ 0.05 **and** `deflated_sharpe` ≥ 0.95 with
N = 303.

Reported per arm: Stage-1 pass/fail and Δ, `b1_median/share`, Stage-2 pass/fail with `failed`
gates, the three after-tax window Δ, the RW p, and the DSR (framework and null-variance). The
**minimum detectable edge** is read off the synthetic curve at each of the three decision points
(Stage 1, Stage 2, declaration). No gate, threshold, seed, sample size or arm changes after data.

## 7. Amendments

### 7.1 (this study) — per-arm synthetic legs and a standalone ladder

Committed **before any non-control arm is simulated**. Reason: v2's synthetic power is invalid
(env-global drift). Changes: (a) `api.SYNTH_LEGS` — the synthetic legs are pinned by name and
selected by the arm's `leg`, never by an environment variable; (b) the study drives the copied
stages with its own `engine.py` and its own result files, so it never writes the campaign ledger.
No gate, threshold, seed, sample size or control differs from `batch_screen` §§ 1–5.

### 7.2 PROPOSED amendment to `research/batch_screen/` (not applied here) — the deflated Sharpe

*Text ready to paste as `PREREGISTRATION.md` §6.7 of `research/batch_screen` (do not edit that file
from this study):*

> **§6.7 — the deflated Sharpe's `var_sr` is the null sampling variance, not the trial dispersion.**
> `deflation.deflate` currently sets `var_sr` to the cross-sectional variance of the per-arm B1-Δ
> path-Sharpes across the arms of the deflated batch. That is the dispersion of *genuinely
> different arms' true effects* (K-clone arms are exactly 0, deliberately catastrophic arms are
> ≈ −1.5), not the variance of a Sharpe *estimate under the null*. Bailey & López de Prado's sr0 is
> the expected maximum of N draws from the null distribution of the Sharpe **estimate**; at SR = 0
> its variance is `(1 − skew·0 + (kurt−1)/4·0)/(n_obs − 1) = 1/(n_obs − 1)`. **Amendment:** compute
> `var_sr` as the mean null sampling variance `var_sr_null = mean_a[(1 + SR_a²/2)/(n_obs_a − 1)]`
> (equivalently `1/(n_obs−1)` when all arms share `n_obs`), so
> `sr0 = sqrt(var_sr_null)·E[max_N]`. The cross-sectional variance and the implied
> `var_true_effects = max(0, var_total − var_sr_null)` are reported alongside as diagnostics only.
> Nothing else in the deflation changes: `white_rc`, `rw_stepdown` and the stage-2 gates are
> untouched, and `declared = stage-2 pass ∧ rw ≤ 0.05 ∧ dsr ≥ 0.95`.

Section 4 of the report gives the counterfactual: which positive controls and synthetic arms the
corrected rule would declare, and that **no** real arm's verdict changes (none passed Stage 2).

### 7.3 (this study) — the counterfactual declaration rule

For the audit only, the corrected DSR is computed with `var_sr_null` as in §7.2 and reported as
`declared_null_dsr` next to the framework's `declared_framework`; the RW-only verdict
(`declared_rw_only`) is reported as well. These are reported, never adopted; the campaign's own
declaration rule is unchanged.
