# gates_v1 — does K survive the corrected DSR and PBO gates?

## Verdict

**No, K does not survive the corrected deflated-Sharpe gate.** Its pre-tax DSR is **0.712** against
the pre-registered bar of 0.95, using the corrected trial count N = 365 and an effective
N_eff = 362. The after-tax sensitivity is worse (DSR 0.425). **PBO passes**: 0.393, below 0.5,
with K the in-sample winner in 59 % of combinations. The gate that fails is the
multiple-testing one; the deflation is not borderline.

K's Sharpe is 0.684 per year pre-tax and the PSR against zero is 0.9997. That is a real return
series. It is not distinguishable from the best of ~360 zero-skill trials at the N and variance the
registry supports. The gate result is what the pre-registered rule says, and it was not tuned.

> **Orchestrator note (2026-10-27).** The DSR counts the 365 registry trials as nearly independent
> (N_eff 362). Almost all of them are close variants of the same QQQ-trend/vol-target rule; the four
> band_h2 arms alone correlate at 0.9998. A smaller true N_eff raises the DSR, so 0.712 is a
> pessimistic reading of the multiple-testing evidence, not proof that K has no edge. The trial-SR
> variance is also floored (caveat 2). The result is still recorded as a FAIL under the
> pre-registered rule. It does not change production: gates_v2 found no arm that beats K (SPA
> p 0.58), and K stays in the 10 % model confidence set.

## What was built (research/gates_v1/)

| item | file | status |
|---|---|---|
| 25 registry backfill | `registry_backfill.py`, `REGISTRY_BACKFILL.jsonl` | done; 44 rows, `origin: backfill` |
| 26 PSR / MinTRL / DSR | `stats.py` | done; DSR matches the Bailey & López de Prado numerical example |
| 31 provenance stamp | `provenance.py` | done; `compare_results` raises on data or code mismatch |
| 32 PBO (CSCV, purge 252) | `pbo.py` | done; pure noise averages PBO 0.49 over 8 seeds; a true edge gives PBO < 0.10 |
| apply to K | `run_gates.py`, `RESULTS.json`, `SUMMARY.txt`, `DATA_MANIFEST.json` | done; two runs byte-identical |
| preregistration | `PREREGISTRATION.md` | committed before the K statistics were computed |

Tests: `tests_gates.py`, 12 tests, all pass (run with the project venv and `PYTHONPATH=.`; the
research venv has no pytest).

## Trial registry (item 25)

- Ledger `research/batch_screen/REGISTRY.jsonl` (closeout tag): 574 lines. Under the ledger's own
  N definition (distinct `(idea, arm, spec_hash)`) it holds **N = 321**. The brief's N = 312 is
  **not reproduced** by this ledger; I used 321.
- Backfill, one row per configuration (not per numeric cell):
  - early_quorum_v1: 3 (Q1, Q2, Q3). Its "1,015 cells" are verification cells, not configurations.
  - vt_level_tax_v1: 5 (VT30, VT40, VT45, VT50, NOVT; C0 is the control).
  - leverage_frontier_v1: 36 (33 stage-1 L × VT cells plus 3 stage-2 variants).
  - Total +44, **corrected N = 365**. None were already in the ledger.
- Not done: the brief asked for "any other grid in docs/research/*.md". I only covered the three
  named grids. The remaining grids are not enumerated, so N = 365 is a **lower bound** on the
  true count.

## Effective trials N_eff

Only the four band_h2 arms (C0 = R2, A1u, H2, K) have rebuildable daily returns. Their average
pairwise correlation is **rho_bar = 0.99985**, so Meff = 1.0005. Every other trial has no stored
return series and counts at full weight. **N_eff = (365 - 4) + 1.0005 = 362.0.** Without the
correlation adjustment the DSR is 0.711, so the adjustment hardly matters.

## K gate statistics (pre-tax, base financing, m = 2, 2000-03-09 to 2026-09-29)

Anchor check: K's pre-tax CAGR reproduces **15.7649 %** (published 15.7649 %, difference 0.000008 pp).

| statistic | value |
|---|---|
| T (daily returns) | 6,679 |
| SR, annualised | 0.684 |
| skewness / Pearson kurtosis | -0.598 / 6.801 |
| PSR vs 0 | 0.99974 |
| MinTRL vs 0 (alpha 0.05) | 5.95 years |
| SR0, expected max over N_eff = 362, V = 1/(T-1), annualised | 0.574 |
| **DSR (V floor, N_eff)** | **0.712** (bar 0.95: fails) |
| DSR, no correlation adjustment (N = 365) | 0.711 |
| MinTRL vs SR0 | ~57,800 sessions, i.e. not reachable in practice |

**After tax (progressive, $100k):** SR 0.537 annualised, PSR vs 0 = 0.9968, DSR = **0.425**.

The V floor is the null sampling variance 1/(T-1), the smallest variance the trial dispersion can
have. A larger true trial dispersion raises SR0 and lowers the DSR, so 0.712 is an **upper bound**
on the DSR: K cannot pass with a larger dispersion. The variance across the four arms (V_family = 3e-8) is far below the floor because the
arms are almost identical. It is shown as a sensitivity only and gives DSR 0.9997, which does not
mean K passes: it reflects the arms being near-copies.

## PBO (CSCV, S = 16 blocks, purge 252 sessions, 12,870 combinations)

- PBO = **0.393** (pre-registered bar: below 0.5, so it passes).
- The in-sample winner was K in 7,651 of 12,870 combinations (59 %), H2 in 2,400, A1u in 1,449,
  C0 in 1,370. The median out-of-sample rank of the winner is 0.60 of the family, so K's in-sample
  advantage mostly carries out of sample.
- Caveat: this is PBO over four near-identical arms. It tests whether choosing K inside its own
  family overfits. It does not test the ~360 registry trials. A PBO over the whole registry needs
  their daily returns, which are not stored.

## Method notes and corrections

- PBO noise check: a single seed swings from 0.07 to 0.75 with 16 blocks, so the noise test
  averages 8 seeds; the expectation is 0.5 (mean 0.49 with the purge). The purge
  is applied to in-sample rows within 252 sessions of any out-of-sample row, on both sides.
- The DSR benchmark uses the Bailey & López de Prado closed form. That form is a Gumbel
  approximation: it gives 2.531 for N = 100 where the exact expected maximum is about 2.508.
  The unit test pins the formula's own value, not the exact one.
- The published check is the paper's numerical example (T = 1250, SR = 2.5 / sqrt(250), kurtosis
  10, N = 46, V = 0.5 per year, DSR = 0.9505). The extracted PDF text lost the skewness and
  variance symbols, so **skewness = -3 and V = 0.5/250 per period are reconstructed**, not read
  directly. The reconstruction reproduces 0.9505 to 5e-4. I could not check MinTRL against a
  published number; it is verified only by its defining property (PSR at MinTRL = 1 - alpha).

## Reproduction

- `python -m research.fast_engine.parity --quick`: PASS (run with this branch).
- `python -m research.gates_v1.run_gates` run twice: `RESULTS.json` byte-identical
  (sha256 `b2317a4dfab0d41ee6a8b0f08e1587611285212780ae17ecd3a690177c3219a2`).
- Provenance stamp: data SHA-256 for the ledger, the three grid copies, the band_h2 results and
  the engine's data files; git commit f9dd29c7; package versions in `RESULTS.json`.

## Caveats

1. Trial-SR dispersion across the ~365 registry trials cannot be computed without their return
   series. The DSR uses the least-deflating admissible variance, so the verdict is conservative
   in K's favour.
2. N = 365 is a lower bound: the other grids in `docs/research/` were not scanned.
3. The ledger holds 321 distinct trials, not the 312 in the brief. I did not reproduce 312.
4. PBO covers only the four band_h2 arms.
5. The gates use pre-tax daily NAV returns for the primary series. The protocol's decisive metric
   is after tax, so the after-tax series is shown as sensitivity; it fails harder.
