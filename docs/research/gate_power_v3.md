# gate_power_v3 — third-pass audit of the batch screen's power

## 1. Verdict in plain words

**The screen detects real improvements and is not too strict — but its multiple-testing step (the
deflated Sharpe) is broken.** A real, taxed **+0.25 pp/yr** edge on the held leg is detected at
Stage 1 and passes the entire Stage 2 holdout; d = 0.25 / 0.5 / 1 / 2 pp/yr all pass, pure i.i.d.
noise and a pre-2013 edge that vanished are correctly rejected, and the four positive controls
reproduce their campaign verdicts. The campaign's **0 of 303** is therefore **not** a power failure
of the gates *or* of the deflation: **no real arm ever reached Stage 2** (245 `reject_stage1`,
19 `reject_stage2`, 8 `not_applicable`, 1 `holdout_insufficient`, **0 `stage2_pass`**), because the
idea bank's median arm is **−0.70 pp after tax** versus K. The one thing that is genuinely wrong is
the deflated Sharpe's null threshold: it is built from the **cross-sectional dispersion of the
trial Sharpes** (~1.07 on the campaign pool, ~360× the null sampling variance), so its threshold
`sr0` is **1.82–6.69** — bigger than the best real arm's advantage-Sharpe (0.55) and even bigger
than the **+2.00 pp/yr** arm's (5.30). Under the framework's own rule **nothing is declarable, not
even a 2 pp/yr edge**; with the corrected `var_sr` the drift arms are declared, and **no real arm's
verdict changes** (none passed Stage 2). Minimum detectable edge: **≤ +0.25 pp/yr** at Stage 1,
Stage 2 and (corrected) declaration; **none** under the framework's DSR.

## 2. Method

The audit is built on the mandatory engine `research/fast_engine` (simulation, Turkish after-tax
accounting and the block bootstrap go through `fe_sim` / `fe_tax` / `fe_boot`; this study writes no
per-day or per-path Python loop). It drives the **campaign's own** stage code, copied verbatim from
`research/batch_screen/` into `research/gate_power_v3/` (`api boot deflation extra registry rules
stages windows`, `EXTRA_DATA.json`); the campaign copy is neither edited nor run in place, and the
only behavioural difference is `api.py`'s synthetic-leg construction (§5, §7.1). The controls, the
stages, the gates, the seeds, the sample sizes and the deflation are the campaign's own. `engine.py`
is the framework's `run.control_record` / `run._arm_task` without the ledger, so **any** control
(K, R2, A1u, E2) drives the same protocol.

* Data: the committed inputs of `research/fast_engine` (SHA-256 in `DATA_MANIFEST.json`); no
  download. Main window QQQ/QLD* 2000-03-09 → 2026-09-29, base and calibrated financing, m = 2;
  after tax at $100k (2000 / 2010 / 2017 windows); stage-1 bootstrap seed 20261011 block 20,
  5,000 pre / 1,000 after-tax paths; the full stage-2 holdout.
* `--harness` runs `research.fast_engine.parity --quick` (PASS), reproduces K's anchors through
  this copy (`research/band_h2_combo_v1/RESULTS.json`), reproduces the **published K-control
  record exactly** (its `main`/`after_tax` cells and its stored B1 vector at the published 6-dp
  precision; the residual 5e-7 *is* that rounding — see §5 on the K clones), and checks each
  synthetic leg's own price path.

## 3. Is the screen able to detect a real improvement? — yes

### 3.1 Positive controls (the campaign's own alternate-control machinery)

| batch | candidate vs control | Stage 1 | Stage 2 | failed gates | AT2000 Δ | B1 med/share | OOS med | RW p |
|---|---|---|---|---|---|---|---|---|
| `pc_r2_vs_e2` | R2 vs E2 | PASS | **stage2_pass** | — | +0.6260 | +0.4846 / 0.646 | +0.0678 | 0.0000 |
| `pc_a1u_vs_r2` | A1u vs R2 | PASS | reject_stage2 | G5_calibrated | +0.4545 | +0.2264 / 0.754 | +0.0404 | 0.0000 |
| `pc_k_vs_a1u` | K vs A1u | PASS | **stage2_pass** | — | +0.1388 | +0.0416 / 0.610 | +0.0272 | 0.0000 |
| `pc_k_vs_r2` | K vs R2 | PASS | reject_stage2 | G5_base, G5_calibrated | +0.5933 | +0.2856 / 0.750 | +0.0653 | 0.0000 |

These reproduce `gate_power_v1` within 0.0001 pp. The two failures are **not** a gate bug: A1u and
the whole K layer are *after-tax* improvements whose **pre-tax** bootstrap median stays near zero,
so they fail G5 — the tension the campaign itself recorded in `gain_band_v2.md` and adopted K
**despite**. Two of the four deployed steps (A1u, K-vs-R2) are therefore rejected by the screen's
Stage 2 on a metric the owner deliberately overrode; that is the one real strictness of the screen,
and it is a stated, owner-accepted trade-off, not a defect.

### 3.2 Synthetic power (K's own targets unchanged; the arm's `leg` is the edge)

| d (pp/yr) | arm | Stage 1 | Stage 2 | after-tax 2000/2010/2017 Δ | B1 med/share | Sharpe | framework decl | corrected decl |
|---|---|---|---|---|---|---|---|---|
| 0 (`2x`) | `syn_null0` | FAIL | — | 0.000 / 0.000 / 0.000 | 0.000 / 0.000 | 0.00 | no | no |
| 0 (drift leg) | `syn_d0` | FAIL | — | 0.000 / 0.000 / 0.000 | 0.000 / 0.000 | 0.00 | no | no |
| 0.25 | `syn_d025` | PASS | **stage2_pass** | +0.149 / +0.184 / +0.189 | 0.127 / 1.000 | 4.70 | **no** | YES |
| 0.50 | `syn_d050` | PASS | **stage2_pass** | +0.290 / +0.371 / +0.382 | 0.255 / 1.000 | 5.08 | **no** | YES |
| 1.00 | `syn_d100` | PASS | **stage2_pass** | +0.592 / +0.746 / +0.769 | 0.504 / 1.000 | 5.24 | **no** | YES |
| 2.00 | `syn_d200` | PASS | **stage2_pass** | +1.197 / +1.492 / +1.558 | 1.016 / 1.000 | 5.30 | **no** | YES |
| 2.00 invested | `syn_inv200` | PASS | **stage2_pass** | +1.183 / +1.466 / +1.545 | 0.981 / 1.000 | 5.04 | **no** | YES |
| 2.00 pre-2013 | `syn_pre200` | PASS | reject_stage2 | +0.426 / +0.241 / −0.000 (AT_2017, E2) | 0.431 / 1.000 | 4.02 | no | no |
| ±noise | `syn_noise_p/m` | FAIL | — | −0.389 / +0.103 (Stage 1) | −0.051 / 0.479 | ≈0.00 | no | no |

`syn_null0` (leg `2x`) and `syn_d0` (the zero-magnitude drift leg) reproduce K **exactly**: Δ =
0.000000, `b1_delta_pp` all 0.0, Stage 1 fails because the rule requires Δ > 0 (the correct
identity). The drift arms are **linear in d** (realised ≈ 0.6 × d, the average exposure). The
pre-2013 arm is correctly rejected: its recent windows are flat (−0.000 pp in 2017+) and AT_2017 +
E2 fail. The noise arms fail at Stage 1.

**Minimum detectable edge:** Stage 1 **≤ +0.25 pp/yr**, Stage 2 **≤ +0.25 pp/yr**, corrected
declaration **≤ +0.25 pp/yr**. Under the framework's own DSR the answer is **none** (§4): the
+2.00 pp/yr arm is *not* declared.

## 4. The deflated Sharpe is the defect

`deflation.deflate` sets `var_sr` to the **cross-sectional variance of the per-arm B1-Δ
path-Sharpes** over the arms of the deflated pool. In Bailey & López de Prado (2014), the variance
inside `sr0` is the variance of the Sharpe **estimate** under the null, which at SR = 0 is exactly
`1/(n_obs − 1)` (their non-normality factor reduces to it). The campaign's pool mixes arms whose
*targets are the control* (Sharpe ≈ 0), real arms, and deliberately catastrophic idea-bank arms
(Sharpe ≈ −1.5 or worse), so the cross-section's variance estimates effect *dispersion*, not
sampling noise:

| pool | n_arms | var_total | var_sr_null | sr0_framework | sr0_null | best Sharpe | framework declares |
|---|---|---|---|---|---|---|---|
| campaign, official `DEFLATION.json` | 41 | 1.0655 | — | **2.692** | — | 0.549 (IC38/B) | none |
| campaign, this study (latest per arm) | 265 | 0.4853 | 0.00135 | **1.817** | 0.096 | 0.558 | none (0 Stage-2 passes) |
| `gp3_synth` batch | 9 | 6.5712 | 0.00826 | **6.686** | 0.237 | 5.301 | none (not even 2 pp/yr) |
| all pc + synth arms | 13 | 6.0039 | 0.00663 | **6.391** | 0.212 | 5.301 | none |

`var_sr` is **~360×** the null sampling variance. Consequences: (i) the implied threshold is not a
null quantity — it depends on which arms happen to share the pool (1.8 → 6.7), and a pool that
contains a few catastrophic arms makes even a +2 pp/yr edge undeclarable; (ii) no real arm (best
advantage-Sharpe 0.55) can ever clear sr0 ≥ 1.8; (iii) the framework's per-batch `declared` is
degenerate for a **one-arm** batch (`var_sr = 0` → `sr0 = 0` → DSR = 1), which is exactly how
`gate_power_v1`'s scoreboard "declared" R2-vs-E2 and K-vs-A1u.

**Is the path-Sharpe of a Δ vector even the right statistic?** The B1 Δ vector is the per-path
after-tax CAGR advantage over 1000 *bootstrap paths*; its "Sharpe" is a mean/std **across paths**,
not across time. The DSR is defined for the Sharpe of a return series with `n_obs` i.i.d.
observations; treating 1000 whole-backtest CAGRs as 1000 i.i.d. per-period returns is a category
error, and the `sqrt(n_obs − 1)` scaling that makes the test look decisive is not justified here.
The methodologically consistent multiplicity-controlled test for "is maxₐ E[Δₐ] ≤ 0" on paired Δ
vectors is the framework's **White RC / Romano–Wolf stepdown**, which it already computes. The DSR
is at best redundant and, as implemented, actively misleading. `rw_stepdown` alone rejects the six
synthetic arms with a nonzero advantage (the five drift arms and the pre-2013 arm) and correctly
leaves the null/noise arms alone; on each PC batch it rejects exactly the one arm whose Δ ≠ 0.

To complete the "which PC / synthetic arms would be declared" question under the corrected rule
(`var_sr_null`): the **five drift arms** (`syn_d025/d050/d100/d200/inv200`) plus the two PC passes
(R2-vs-E2, K-vs-A1u — already declared, and unchanged, since their stage-2 verdict is what decides
them). **Which real arms' verdicts would change: none** — confirmed from the records: **0 of 273**
latest records have `stage2_pass`, so `declared = stage2 pass ∧ rw ∧ dsr` is empty before and after
the fix.

## 5. The 303 real arms

273 latest records per `(idea, arm)` from `research/batch_screen/results/`: **245 reject_stage1,
19 reject_stage2, 8 not_applicable, 1 holdout_insufficient, 0 stage2_pass.**

* Per-gate failures among the 19 Stage-2 rejects: AT_2017 **16**, FRESH 12, AT_2010 11, B1_recent
  11, E1 10, G5_base 10, B2_flat 10, G1_base 8, G6_calibrated 8, E2 6, G8 4, G7 3, G4 3, G2 2,
  G5_calibrated 2. The after-tax "all three windows" rule and the fresh-seed bootstrap dominate —
  appropriate for a Turkish-resident owner.
* **K clones (8):** IB31/C, IC26/C, IC52/A, IC52/C, IC57/B, IC58/A/B/C — targets numerically K,
  each failing the same 9 gates. Their Δ is **+0.000006 pp**, which is *not* a real difference: it is
  the 6-dp rounding of the framework's own `_control.json` (the same arm with this study's exact
  control gives Δ = 0.000000). They are not candidates.
* **Real near-misses (11)** with after-tax Δ (pp) vs K, 2000 / 2010 / 2017: IC12/A +0.708 / +0.190 /
  −1.353 (fails AT_2017, E2); IC12/B +0.700 / +0.490 / −0.347 (fails AT_2017 only); ib32/A +0.519 /
  +0.575 / +0.958 (G5, G7, G8, B2, FRESH); vol_shape_p15 +0.490 / +0.440 / +0.203 (G8, E1); IB10/B
  +0.396 / +0.400 / +0.658 (G5×2, B1, B2, FRESH); IC60/A +0.174 / +0.382 / −0.046; IC47/C +0.120 /
  +0.032 / −0.027; IC60/C +0.110 / +0.279 / −0.266; IB10/C +0.045 / −0.016 / −0.031; IB08/C +0.024
  / −0.029 / −0.136; IC57/A +0.007 / −0.004 / −0.008.
* **Remove-one-gate counterfactual:** removing *any single* gate rescues **at most one** arm
  (IC12/B via AT_2017). The gates are redundant, not individually lethal.

**Why the hit rate fell** (the owner's framing "~1 real improvement per 5–10 ideas, now 0/303"): the
early wins (E2 → R2 → A1u → K) were harvested; K already removed ~91 % of the realised Turkish
taxable gain by suppressing vol-target trims (~30 → ~10 sells/yr), so trim-reduction ideas are
spent; and the screen's after-tax, multi-window, multi-instrument holdout — correctly, for a
Turkish-tax objective — rejects ideas that look good on one window or one seed. The idea bank's
median arm is −0.70 pp after tax: the program is in the diminishing-returns part of *its own* idea
space, not blocked by the screen.

## 6. What was wrong in v1 and v2

* **v1** — the positive controls' "declared" column came from a **one-arm batch deflation** (var_sr
  = 0 ⇒ sr0 = 0 ⇒ DSR = 1, a degenerate pass); the committed `results/DEFLATION.json` is only for
  `pc_synth` and declares `[]`, so the report's declaration column is unsupported. v1's synthetic
  power scaled the **weights** (+5 / +10 % exposure), not the leg's return — the wrong object — and
  its "null" perturbed the weights, so it found no Stage-2 power for the wrong reason.
* **v2** — the drift is an **environment-global** (`GATE_POWER_DRIFT`, `GATE_POWER_NOISE_SEED`): one
  batch-level drift is applied to *every* synthetic leg, so `syn_null0` is an exact copy of
  `syn_edge`, and `syn_noise_p/m` are K clones (the noise seed defaults to −1, so no noise is ever
  added). v2's synthetic-power table is therefore invalid. Its conclusion (Stage 1 MDE ≈ +0.5 pp/yr,
  Stage 2 declares +2.0 pp/yr) is wrong in both directions: with the corrected per-arm drift, Stage 1
  and Stage 2 detect **+0.25 pp/yr**, and under the framework's own deflation the +2.0 pp/yr arm is
  **not** declared. v2's D1 tally (19 rejects, 8 clones, 11 near-misses, IC12/B the only
  single-gate failure) is confirmed here.

## 7. Proposed amendment (text ready to paste as `research/batch_screen` §6.7)

`research/gate_power_v3/PREREGISTRATION.md` §7.2 carries the amendment verbatim. In short: compute
`var_sr` as the **null sampling variance** of the Sharpe estimate,
`var_sr_null = mean_a[(1 + SR_a²/2)/(n_obs_a − 1)]` (equivalently `1/(n_obs − 1)` when all arms
share `n_obs`), so `sr0 = sqrt(var_sr_null)·E[max_N]`; report the cross-sectional variance and
`var_true_effects = max(0, var_total − var_sr_null)` as diagnostics only. Nothing else changes:
`white_rc`, `rw_stepdown` and the stage-2 gates are untouched, and
`declared = stage-2 pass ∧ rw ≤ 0.05 ∧ dsr ≥ 0.95`. It would declare the five synthetic drift arms
and leave every PC verdict and **every real arm's verdict** unchanged (0 Stage-2 passes).

## 8. Reproduction

```sh
R=~/.venvs/pb-research/bin/python
PYTHONPATH=. $R -m research.fast_engine.parity --quick
PYTHONPATH=. $R -m research.gate_power_v3.screen --harness   # PASS; K anchors + control bit-identical
PYTHONPATH=. $R -m research.gate_power_v3.screen --run       # 374.5 s; controls + synthetic + deflation
PYTHONPATH=. $R -m research.gate_power_v3.screen --tally
```

Records under `research/gate_power_v3/results/` (`pc_*/`, `gp3_synth/`, `DEFLATION_*.json`,
`DSR.json`, `DSR_campaign.json`). Re-running `--run` reproduces the aggregate SHA-256 of every
per-arm record and `DEFLATION_*.json` byte for byte (`66f530ed…`); only `RESULTS.json` (wall clock)
differs.

**Caveats.** (1) A += drift is a *synthetic* edge on the held leg, not a strategy idea; the power
curve bounds what *any* strategy-shaped edge of that size would score, it does not prove a real
idea exists. (2) Only d ∈ {0.25, 0.5, 1, 2} pp/yr were pre-registered, so "≤ 0.25 pp/yr" is the
strongest claim; the true MDE may be lower. (3) The deflation's `var_sr` is pool-dependent, so the
DSR audit is reported per pool; the RW-only verdict is pool-independent given paired arms. (4) The
"pre-2013" arm's chunk semantics use the *drawn source* session's year (§ its builder), which is
what the bootstrap resamples.

## Orchestrator review (2026-10-17) — the proposed DSR amendment is NOT adopted

The study's measurements stand (tally, positive controls, the plain-drift power table, the v1/v2 error list).
Its proposed §7.2 amendment is rejected, for this reason:

* The B1-Δ "path-Sharpe" is `mean/sd` of the **bootstrap distribution** of an arm's Δ. A block bootstrap
  approximates the *sampling* distribution of the Δ estimator, so `mean/sd ≈ Δ̂ / se(Δ̂)` — a z-score.
  Under the null (true Δ = 0) that z-score is ≈ N(0, 1) **whatever the number of paths**; 1,000 paths do not
  make it 1,000 independent observations. The null variance of the statistic is therefore ≈ 1, not
  `1/(n_obs − 1)`, and `sr0 = E[max of N ≈ 312 null z-scores] ≈ 2.7–2.9` — what the framework computes (its
  cross-sectional `var_sr` ≈ 0.84–1.07 happens to sit near 1). The amendment would shrink sr0 ≈ 30× and
  declare almost any arm with a positive median.
* So the framework's DSR behaves as a multiple-testing bar: after ~300 trials an arm must have roughly
  ≥ 99.5 % of bootstrap paths positive to be declared. The campaign's deployed winners (R2, A1u, H2, K) had
  63–75 % (z 0.29–0.70): they were adopted on mechanism (Turkish-tax mechanics) plus robustness gates, not on
  multiple-testing significance — and they would not be "declared" by this rule either.
* The plain-drift synthetic arms reach path-Sharpe ≈ 5 because the drift is added identically on every
  bootstrap path (no estimation noise), so the "minimum detectable edge ≤ +0.25 pp/yr" holds only for a
  noise-free edge. A real edge must clear the Stage-2 robustness gates (as R2 vs E2 and H2 vs A1u do) and,
  for declaration, a z of ≈ 2.8.
* Conversely, `rw_stepdown` studentizes with `sd/sqrt(n_paths)` (t = path-Sharpe × √1000), treating
  bootstrap paths as independent observations; it is lenient (p ≈ 0.0005 for a 50 %-share arm). Because a
  declaration needs both RW and DSR, the DSR binds and the deflation as a whole is not lenient. Recorded as a
  known issue; no verdict depends on it (no real arm has reached the deflation: 0 Stage-2 passes).

**Answer to the owner's question.** The screen is not broken. Of the ~100 ideas (now 312 trials) none passed
Stage 2 — the robustness/holdout stage that two of the campaign's four real winners pass. The fall in hit rate
comes from the stronger baseline (K already captures the large tax-mechanics gains; the median idea is
−0.70 pp/yr after tax vs K) and from the robustness gates (2017+ after tax, rolling starts, fresh seeds,
21 ETFs), not from a defective gate. The final multiple-testing bar is strict by design and has not yet been
the binding constraint for any real idea.
