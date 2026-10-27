# hmm_regime_v1: causal two-state HMM regime overlay on K after Turkish tax (audit item 52)

Pre-registration, committed before any non-control arm was simulated on real data. Code: `research/hmm_regime_v1/hmm.py` (kernels) and `screen.py` (driver). Inputs and hashes: `DATA_MANIFEST.json`. The K and R2 harness anchors were reproduced before this commit (`--harness`, PASS).

## Question

After Nystrup et al.: does a two-state Gaussian hidden Markov model on QQQ daily returns, with states ordered by variance (calm = state 0, turbulent = state 1), improve K after Turkish income tax, without materially worse drawdown? Scope is one overlay family (H1-H3) on K, with K's execution layer unchanged.

## Control and reference

- **K** (control): production rule, reproduced by the harness to 0.01 pp (pre-tax 15.76 % / max DD 46.67 %; after tax 10.75 / 15.24 / 23.97 %).
- **R2** (reference only, not counted in the gates): production with the C0 band.

## Observations (the HMM's data)

- Daily **log returns**. QQQ closes from 1999-03-10 (K's main calendar).
- **Prefix (same device as vol_regime_leverage_v1):** before QQQ's first session, ^IXIC log returns. Training data starts 1990-01-01. The splice is 1999-03-09 to 1999-03-11, with the first QQQ return dropped.
- The HMM never sees a future observation: every probability used at a close uses returns up to and including that close.

## HMM specification (fixed constants, `hmm.py`)

- Two states, Gaussian emissions, full 2×2 transition matrix.
- Baum-Welch (scaled forward-backward, numba) from a fixed start: π = (½, ½); diagonal persistence (0.98, 0.95); both means = sample mean; state sds = 0.6 and 1.6 × sample sd. At most 1,000 iterations, stop when the relative log-likelihood change is ≤ 1e-10. No randomness. After the fit, states are relabelled so state 1 has the larger sd (turbulent). The sd floor is 1e-4.
- **Refit schedule (yearly, expanding):** at the first session of each calendar year Y from 2000 onward, fit on all observations dated in [1990-01-01, Y first session). The first refit is 2000-01-03 (training start year + 10). No mid-year refits.
- **Filtered probability only:** P_t = P(state 1 | returns up to close t), from the forward recursion with prior = stationary distribution of the fitted chain. No smoothing anywhere. Between refits the same parameters are used, and the filter runs over the observations up to the next refit.
- **Warm-up:** P is undefined before 2000-01-03. All arms use K's weight there, and the main window starts 2000-03-09, so the first refit is in place before the first measured session.

## Arms (each parameter fixed; K's execution layer for all)

All three arms feed their target-weight array to K's execution rule (`K`: A1u gain band, H2 December deferral, 0.5 pp band, 0.98 cap, decision at close, fill at the next open). Only the target differs.

Let K_t = K's weight at close t (E2 vote, R2 early leg, vol target, cap). Let V_t = the E2 vote (≥ 2 of 3 on QQQ).

- **H1, continuous de-risking:** target_t = K_t × (1 − P_t).
- **H2, gated by hysteresis:** gate G_t is on when P_t > 0.8 at two consecutive closes (t−1 and t). It is off when P_t < 0.5 (weight restored). Between 0.5 and 0.8 the state is unchanged. Target_t = 0 while G_t = 1, else K_t.
- **H3, H2 as an extra exit only:** latch L_t = 1 iff V_t = 1 and G_t = 1 and (L_{t−1} = 1, or V_{t−1} = 1 and H3's target at t−1 > 0). Target_t = K_t × (1 − L_t). The latch clears when the vote turns off or G turns off. K's own entry rules are not touched, and L can be set only while the vote was already on and the position was held, so H3 never blocks a new vote-on entry. A re-entry after G turns off is K's own weight.

Control K and the three arms share every mechanic except the target. The arm kernel (`hmm.hmm_arms`) matches an explicit per-close reference loop on random inputs (harness, PASS).

## Bootstrap paths (B1, B2 and the pre-tax bootstrap): decision

**Fixed parameters, applied by causal filtering.** Every resampled 10-year path gets the parameters of the main path's **final** yearly refit (fitted on data through 2025-12-31). P is filtered forward through each path's returns from the stationary distribution, with no refit within a path.

Why this and not a per-path refit: a synthetic 10-year path has at most nine years of history, too short for a stable two-state fit, and its warm-up would dominate. It would also mean about 27 refits × 6,000 paths. Fixed parameters keep the state definition identical across paths and equal to the main path's final state definition.

Caveat (also stated in the report): these parameters were estimated on the same history the bootstrap resamples, so the bootstrap evidence for H1-H3 is optimistic about estimation error. The out-of-sample checks are the expanding main path and the OOS windows.

## OOS windows (G7, G8)

Same causal device, applied to each window's own closes: training starts at the window's first session, the first refit is 10 years later, and then yearly expanding refits. K's weight before that. Consequence: the early part of each OOS window runs as K, so the OOS evidence for H1-H3 is over a shorter effective window than K's. This is recorded, not corrected.

## Fixed windows, costs and tax (as K)

- Main pre-tax window 2000-03-09 → 2026-09-29, m = 2 (base financing, calibrated ^ spread).
- After tax: $100k, liquidated; 2000-03-09 / 2010-01-01 / 2017-01-03 starts to 2026-09-29, progressive and flat-20 %. Halves: pre-2020 (2000-03-09 → 2019-12-31) and post-2020.
- Bootstrap: 5,000 pre-tax paths and 1,000 after-tax paths, block 20, seed 20261011 (fe_boot), common draws for all arms.

## Gates (identical to vol_regime_leverage_v1; a candidate must pass all)

- **G1** CAGR or Calmar (Calmar ≥ 1.1 × K's and CAGR ≥ K − 0.5 pp), base and calibrated, m = 2.
- **G2** max DD ≤ K + 1 pp (base and calibrated).
- **G3** CAGR to 2017-end ≥ K − 1 pp (base and calibrated).
- **G4** beats K in at least two of the sub-periods P1–P3 (base).
- **G5** pre-tax bootstrap median Δ ≥ 0 (base and calibrated).
- **G6** G1 at m = 1.
- **G7** six OOS windows, calibrated, m = 2: median Δ CAGR ≥ 0; ≥ 5 of 6 with Δ ≥ −0.5 pp; ≥ 5 of 6 with ΔDD ≤ +3 pp; max ΔDD ≤ +8 pp.
- **G8 (reduced, as in vol_regime):** block-20 circular bootstrap of each M1–M5 daily Δ vs K, 2,000 replications, statistic = mean over M1–M5 of 252 × mean daily Δ; median ≥ 0.
- **G9** annual turnover ≤ 2 × K's.
- **A1** after-tax CAGR > K in all three windows.
- **A2** B1 (progressive) median Δ ≥ 0 and share > 0 ≥ 55 %.
- **A3** B2 (flat-20 %) likewise.
- **A4** after-tax Δ > 0 in both halves (pre-2020 and post-2020).

## Deflated Sharpe

N = 369 + 3 = 372 trials: 369 as orchestrator-specified (the count used by vol_regime_leverage_v1) plus the three arms of this study. Reported for pre-tax daily returns and after-tax progressive NAV, with gates_v1's `dsr` and `var_trials_floor`. Bar 0.95 (informational).

## Selection rule

An arm is a candidate only if every G and A gate passes. Otherwise K stays. This study cannot change production, whatever the result.

## Reporting

RESULTS.json (with a provenance stamp: input SHA-256, git commit, package versions, code SHA-256), SUMMARY.txt (wall clock of the run), `docs/research/hmm_regime_v1.md` (verdict first). Two runs must give a byte-identical RESULTS.json.

## Pre-registration status

No non-control arm has been simulated on real data. The HMM kernels were verified on synthetic data only: parameter recovery, causality, the arm kernel against a per-close reference loop, and the expanding schedule's causality and warm-up. Any change after the first real run goes in an "Amendments" section of the report.

## Not applied

The orchestrator's brief names a "sealed window"; neither WORKER_PROTOCOL.md nor the research docs define one, so none is applied.
