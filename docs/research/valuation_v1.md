# valuation_v1 — valuation / ERP-conditioned exposure on K (idea I5)

> Evidence: branch `research/valuation_v1` (this worktree). `PREREGISTRATION.md` (commit
> `c9b8c686`, before any data download), `V0_RESULTS.json` (commit `798243f2`, before any arm ran),
> `RESULTS.json`, `HARNESS.json`, `SUMMARY.txt`, `DATA_MANIFEST.json`, `screen.py`,
> `fe_arm_boot.py`, `xls_biff.py`, `download_data.py`, `report.py`.

Date 2026-10-16. **Verdict: the valuation axis is closed — no arm passes.** V4 (the
exposure-matched deciding arm) is the only arm that survives the pre-tax return gates and it fails
the pre-tax block bootstrap (**G5**: median −0.249 pp base / −0.228 pp calibrated, share > 0
39 % / 40 %) and the after-tax set (it loses the 2017 window by **−0.008 pp**, B1-recent median
−0.053 pp on 46.8 % of paths, B2-flat −0.108 pp on 44.4 %). The level arms V1–V3 lose **1.0–4.5 pp
after tax in all three windows**. De-risking by valuation does cut the drawdown (K 46.67 % →
V1 35.95 %, V4 44.03 %) but never pays for itself in return — pre-tax or after Turkish tax.

## Method

Rebuilds K exactly: C0 = production K (A1u gain band + H2 December deferral) on the production R2
target series, all simulation/after-tax/bootstrap through `research/fast_engine`
(`fe_sim`/`fe_tax`/`fe_boot`/`fe_study`). The arms change one thing: the multiplier `μ_t ∈ (0, 1.3]`
on the on-state weight `raw = on_eff × factor × vt`, applied **before** the 0.98 cap, driven by the
**previous month's** published Shiller figure (no look-ahead). Data: Shiller's `ie_data.xls`
(single source, downloaded and hashed in `DATA_MANIFEST.json`) and the `^NDX` daily window for
V4's calibration. Reading the workbook needed a small OLE2/BIFF8 reader (`xls_biff.py`) because the
research venv has no `xlrd`; my forward 10-year real total return reproduces Shiller's own column
exactly (max |Δ| 7e-16, n = 1722) and my CAPE matches his to 0.33 points (corr 0.99998).

| arm | `μ_t` | realized mean weight (K: 67.32 %) |
|---|---|---|
| V1 | `clip(EY_{M-1}/EY_REF, 0.70, 1.15)`, `EY_REF` = 0.0654 (median E/P 1950-99) | 54.41 % (−12.91 pp) |
| V2 | `0.75` while `CAPE_{M-1} > 21.707` (1871-1999 90th pct), else `1.00` | 54.98 % (−12.34 pp) |
| V3 | `0.75`/`1.10`/`1.00` against the **expanding** median ± 1σ of CAPE up to `M-1` | 55.37 % (−11.95 pp) |
| V4 | `1.2223 ×` V2's μ (mean weight matched to K's on `^NDX` 1986-10 → 2000-03) | 64.76 % (−2.56 pp) |

**V0 (pre-run gate) passed**, so the arms ran. Regression of the forward 10-year real S&P total
return on the month-lagged-then-contemporaneous signal, 1871-01 → 1949-12:

| predictor | sample | n | slope | t (NW, 120 lags) | t (OLS) | R² |
|---|---|---:|---:|---:|---:|---:|
| EY = E/P | **1871-1949 (gate)** | 948 | **+0.9446** | **+5.27** | +17.25 | 0.239 |
| CAPE | **1871-1949 (gate)** | 829 | **−0.00570** | **−4.14** | −18.29 | 0.288 |
| EY | 1950-1999 (OOS) | 600 | +1.1824 | +3.76 | +17.24 | 0.332 |
| CAPE | 1950-1999 (OOS) | 600 | −0.00504 | −4.14 | −18.59 | 0.366 |
| EY | 2000-2014 (OOS) | 174 | +1.0592 | +1.39 | +5.05 | 0.129 |
| CAPE | 2000-2014 (OOS) | 174 | −0.00684 | −10.65 | −26.49 | 0.803 |

The relation is real and it survives out of sample — which is exactly why the *trading* result is
worth reporting: the signal predicts returns, and using it still loses money against K.

## Pre-tax (main window 2000-03-09 → 2026-09-29, m = 2)

| arm | CAGR base | CAGR cal | max DD base | max DD cal | turnover/yr | sells/yr |
|---|---:|---:|---:|---:|---:|---:|
| **K** | 15.7649 | 15.1785 | 46.6698 | 46.7948 | 5.4561 | 10.17 |
| V1 | 13.5719 | 13.1077 | 35.9463 | 36.2132 | 4.4710 | 9.71 |
| V2 | 13.9595 | 13.4940 | 37.5226 | 37.6354 | 4.6169 | 9.83 |
| V3 | 13.7301 | 13.2507 | 37.5226 | 37.6354 | 4.6229 | 9.71 |
| V4 | 15.7919 | 15.2226 | 44.0292 | 44.1528 | 5.2064 | 9.22 |
| *(R2, reference)* | *15.4766* | *14.8961* | *46.7584* | *46.8797* | *5.9915* | *29.56* |

m = 1 / m = 4 CAGR: K 16.0173/15.4303 and 15.2541/14.6703; V4 16.0334/15.4629 and
15.3025/14.7372; V1 13.7717/13.3097 and 13.1571/12.6937; V2 14.1696/13.7038 and 13.5562/13.0922;
V3 13.9401/13.4604 and 13.3271/12.8493.

Sub-periods (base, m = 2, CAGR): V4 0.515 % / 14.750 % / 32.580 % against K's
0.286 % / 14.266 % / 33.231 % — so V4 wins the 2000s and the 2010s and loses the 2017+ leg, i.e.
precisely the 2013–2021 de-risking the survey predicted.

## After tax ($100k, progressive, liquidated, m = 2) and the bootstraps

| window | K | V1 Δ | V2 Δ | V3 Δ | V4 Δ |
|---|---:|---:|---:|---:|---:|
| 2000-03-09 → | 10.7533 % | −1.326 | −0.966 | −1.159 | **+0.342** |
| 2010-01-01 → | 15.2354 % | −2.202 | −1.954 | −2.289 | **+0.069** |
| 2017-01-03 → | 23.9720 % | −4.470 | −3.828 | −3.828 | **−0.008** |

At $10k the V4 deltas are +0.121 / −0.300 / −0.228 pp; at $1M +0.361 / +0.037 / −0.073 pp.
Clean bootstrap (seed 20261011, block 20, 1,000 paths, $100k), median Δ vs K and share > 0:

| | B1-recent (progressive) | B2-flat |
|---|---|---|
| V1 | −0.181 (47.1 %) | −0.387 (44.3 %) |
| V2 | −0.142 (47.9 %) | −0.271 (45.3 %) |
| V3 | −0.142 (47.9 %) | −0.271 (45.3 %) |
| V4 | −0.053 (46.8 %) | −0.108 (44.4 %) |

Every arm's share > 0 is **below 0.5**, not merely below the 55 % bar: the valuation tilt loses to
K on the majority of resampled decades. The fresh bootstraps (seed 20261015, blocks 10/40) agree in
sign with the block-40 ones (V4 −0.159 / −0.243) and are mildly positive at block 10
(V4 +0.029 / −0.077) — noise, not signal.

## OOS (pre-tax, calibrated, m = 2) — Δ CAGR / Δ max DD in pp

| window | K CAGR | K maxDD | V1 | V2 | V3 | V4 |
|---|---:|---:|---|---|---|---|
| GSPC 1957-1999 (US) | 9.967 | 39.65 | −0.63/+0.32 | −0.58/−0.00 | −0.88/+0.12 | −0.10/+0.44 |
| M1 ^IXIC (US) | 21.273 | 41.22 | −1.79/+0.42 | −1.80/+0.00 | −1.77/+0.28 | −0.29/+0.62 |
| M2 ^GSPC (US) | 11.726 | 58.11 | +0.62/−3.10 | +0.20/−2.26 | +0.37/−2.16 | +0.61/+0.66 |
| M3 ^N225 (report) | 6.457 | 65.18 | +0.20/−9.91 | +0.29/−6.79 | +0.30/−8.01 | +0.52/−5.35 |
| M4 ^GDAXI (report) | 9.707 | 58.15 | −0.90/−11.41 | −1.16/−9.56 | −1.43/−9.56 | −0.02/−3.06 |
| M5 ^FTSE (report) | −0.623 | 85.65 | +0.89/−9.14 | +0.82/−9.98 | +0.79/−8.57 | +0.28/−1.92 |

On the 3 US windows the median Δ is negative for every arm (V4 −0.102 pp), so **G7 fails**; on the
non-US windows the tilt's drawdown reduction is large (−1.9 to −11.4 pp) and the return effect is
mixed — consistent with "a de-risking rule buys drawdown, not return".

## Gates (control K)

| arm | tier | G1 | G2 | G3 | G4 | G5 | G6 | G7 | G9 | after tax | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| V1 | level change (−12.91 pp) | n | Y | n | n | n (−1.049) | n | n (−0.627) | Y | n | **fail** |
| V2 | level change (−12.34 pp) | n | Y | n | n | n (−0.836) | n | n (−0.578) | Y | n | **fail** |
| V3 | level change (−11.95 pp) | n | Y | n | n | n (−0.836) | n | n (−0.877) | Y | n | **fail** |
| V4 | level change (−2.56 pp) | Y | Y | Y | Y | **n (−0.249)** | Y | **n (−0.102)** | Y | **n** | **fail** |

V4's failed gates: **G5** (pre-tax bootstrap median), **G7** (US OOS median), **after tax** (one
losing window and both bootstrap shares < 0.55). V1–V3 add G1, G3, G4 and G6. **G8** (the pooled
`e2_confirmation_v1` K5 OOS bootstrap) is unavailable: that per-market overnight/intraday resampling
is not ported into `research/fast_engine` and it is a different bootstrap mechanism, not a
target-weight option — a pre-declared reduced gate, recorded here as a caveat rather than papered
over. **G9** passes for every arm (turnover 4.46–5.21 against K's 5.46, and the tilt *reduces* tax
paid: V2 pays 606 k against K's 831 k USD in the 2000 window) — turnover is not where the arms die.

The deciding pair ran exactly as pre-registered: **V4** (exposure-matched by construction) and
**V3** (causal). V4's realized mean weight drifted 2.56 pp below K's, so the pre-declared two-tier
rule put it in the *level-change* tier and it took the full gates — the honest reading, and the
same one that makes its +0.027 pp pre-tax CAGR unimpressive. V3 is a level change by 11.95 pp and
fails on return. The survey's own expectation ("the likely failure is de-risking through
2013–2021"; "the honest prior is no edge") is what happened.

Robustness, report only: `R-STALE` — with the multiplier forced back to 1.0 wherever the published
figure is stale (decisions from 2024-08-01) V4 is −0.302 pp; on the fresh-signal window only
(2000-03-09 → 2024-06-28, K 14.7592 %) V4 is −0.367 pp. **V4's entire main-window pre-tax
advantage comes from the stale tail**, and it fails the gates in the full run regardless.
At m = 4 costs, after tax at $100k: V4 10.7124 / 14.8699 / 23.7034 against K's
10.3539 / 14.8202 / 23.6749.

## Harness and reproducibility

`python -m research.fast_engine.parity --quick` **PASS** (11.4 s). The one kernel change — the
bootstrap needs a target series per arm, which `fe_boot.boot` cannot express — lives in
`fe_arm_boot.py`, a copy: `draw_positions` is imported unchanged, so the drawn paths are the
published ones. Its **option-off parity cell** (all multipliers 1.0, arms C0/K, seed 20261011,
block 20, 200 pre-tax + 100 after-tax paths) reproduces `fe_boot.boot` cell for cell:
max |Δ| = **0.0**. K and R2 anchors reproduce within **0.01 pp** (worst 4.4e-5 pp): K pre-tax base
15.7649 % / calibrated 15.1785 %, max DD 46.6698 % / 46.7948 %, after tax $100k
10.7533 / 15.2354 / 23.9720 %; R2 15.4766 / 14.8961 %, DD 46.7584 / 46.8797 %, after tax
10.1600 / 14.7059 / 22.8861 %. `--run` wall clock **5.9 s** (warm numba cache), and two
consecutive runs produce byte-identical `RESULTS.json`.

## Data caveat (the one that matters)

Shiller's workbook was last saved 2024-09-04: `E` (and therefore EY and CAPE) ends **2024-06**
while `P`/`CPI` end 2024-09, so the last **564 of 6,679 measured sessions (8.4 %)** and roughly the
last two years of every 10-year bootstrap path carry the last published figure forward. That is
disclosed, not corrected: it is what an owner trading on the published figure would have done, and
the `R-STALE`/fresh-window rows above quantify it. It does not change the verdict — the arms fail
without the tail too.

## Amendments

None. No gate, arm, parameter or threshold was changed after seeing a result. Three
implementation bugs were found and fixed during construction, before the `--run` that produced
`RESULTS.json`: the Newey–West sandwich lacked the sum-vs-mean correction (V0 t-statistics were
absurdly large); `sized_mult` returns a 2-D `(1, T)` panel, so three call sites sliced the path
axis instead of time and produced NaN means; and `boot_arms` needed an explicit
arm-label → fill-rule map because the arms are target-weight variants of K, not new rule labels.
The V0 numbers committed in `798243f2` are the corrected ones (the buggy version was never
committed).
