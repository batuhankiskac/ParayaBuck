# gates_v1 — preregistration (committed before any K gate statistic is computed)

## Question

Does the production rule K survive the corrected multiple-testing gates: deflated Sharpe (DSR,
with the corrected trial count N and an effective N_eff) and the probability of backtest
overfitting (PBO, CSCV with a 252-session purge)? This is validation tooling. It evaluates K, it
does not propose a strategy and it changes no production rule.

## What is computed (all of it, nothing else)

1. **Trial registry (item 25).** `registry_backfill.py` counts N = distinct (idea, arm, spec_hash)
   of the append-only ledger (`research/batch_screen/REGISTRY.jsonl`, read-only), then adds one
   `origin: backfill` row per configuration of the three grid studies early_quorum_v1,
   vt_level_tax_v1 and leverage_frontier_v1 that the ledger does not already hold, written to
   `research/gates_v1/REGISTRY_BACKFILL.jsonl`.
2. **Effective trial count N_eff.** The rebuilt pre-tax return series of the four band_h2_combo_v1
   arms (C0 = R2, A1u, H2, K) are the only trials whose returns are rebuilt. Their average pairwise
   correlation rho_bar gives Meff = 1 + (n - 1)(1 - rho_bar) for that family. Every other trial has
   no rebuildable return series and counts at full weight. N_eff = (N_corrected - 4) + Meff.
3. **K's return series.** Daily simple returns of the pre-tax NAV from `research/fast_engine`
   (`fe_sim.simulate`, mode `pre_tax`, arm `K`, base financing, m = 2, QQQ main window
   2000-03-09 to 2026-09-29, capital 100,000). Sessions T = 6,679 returns. A secondary series is
   the after-tax NAV (mode `progressive`, same window and capital) of K.
4. **Moments and PSR / MinTRL / DSR** (`stats.py`) on K's series: per-period SR = mean/std
   (ddof 1, rf = 0), sample skewness, Pearson kurtosis, T. PSR is against SR* = 0. MinTRL at
   alpha = 0.05 against SR* = 0 and against the DSR benchmark SR0.
5. **DSR.** Benchmark SR0 = E[max SR] over N_eff trials. Two admissible variances of the trial
   Sharpe ratios are reported, and the verdict uses the LOWER one (least deflation):
   - **V_floor = 1/(T - 1)**, the null sampling variance of a zero-skill Sharpe estimate. This is
     the smallest variance the trial dispersion can have if every trial is noise.
   - **V_family** = the sample variance of the four rebuilt arm Sharpe ratios (sensitivity only).
   The trial-SR dispersion of the 365 registry trials cannot be computed: their return series are
   not stored. This gap is stated in the results, not papered over.
6. **PBO** (`pbo.py`) on the matrix of the four arms' daily returns (rows = sessions, columns =
   C0, A1u, H2, K), S = 16 blocks, purge = 252 sessions, all C(16, 8) = 12,870 combinations.

## Decision rule (fixed in advance)

K **survives** the gates iff BOTH hold on the pre-tax primary series:
- DSR (V_floor, N_eff) >= 0.95, and
- PBO < 0.5.

After-tax PSR, MinTRL, DSR and V_family values are reported as sensitivity. They do not change the
verdict. If the DSR fails at V_floor, the verdict is "does not survive", whatever V the trial
dispersion would give, because V_floor is the lowest possible.

## Anchors and harness

- `python -m research.fast_engine.parity --quick` must PASS before the analysis runs.
- K's pre-tax main-window anchor must reproduce: 15.7649 % CAGR (base, m = 2, unliquidated), per
  `research/band_h2_combo_v1/RESULTS.json`, within 0.01 pp.
- The provenance stamp (`provenance.py`) records the SHA-256 of every input file (the three grid
  RESULTS copies, the ledger, the band_h2 H2 anchors and the data files of the engine), the git
  commit and the package versions.

## Reproducibility

`run_gates.py` runs twice. The two `RESULTS.json` files must be byte-identical (no timestamps,
fixed rounding). Wall-clock time goes to `SUMMARY.txt`, not to RESULTS.json.

## Not in scope

No new arms, no parameter search, no strategy change. The PBO input is the four existing band_h2
arms, not the 365 registry trials. PBO is therefore a within-family test and says nothing about the
whole registry. This is stated again in the results.
