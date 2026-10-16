# valuation_v1 — PRE-REGISTRATION (committed before any non-control simulation)

Branch `research/valuation_v1`. Date 2026-10-16. Test of idea **I5** of
`docs/research/idea_survey_v5.md` (§3, §6, I5 read in full): valuation / ERP-conditioned exposure.

**Control C0 = production K** (`equity_leveraged_trend_v1` 1.2.0 execution layer: A1u gain band +
H2 December deferral) on the production R2 target series (E2 2-of-3 QQQ vote + half-weight early
re-entry + `min(1, 0.35/vol20)` on the harness's synthetic 2× QLD\* close, total cap 0.98, 0.5 pp
resize band, 4 bps × m cost, next-open fills). R2 (`C0` of `band_h2_combo_v1`) is reported as a
reference row only.

Engine: `research/fast_engine` (restored from tag `archive/research/fast-engine-v1-2026-10`;
`python -m research.fast_engine.parity --quick` must PASS). Every simulation, after-tax
accounting and bootstrap goes through `fe_sim` / `fe_tax` / `fe_boot` / `fe_study`. `screen.py`
only builds target-weight arrays (vectorised numpy) and selects rule options.

---

## 1. Question

K's only state variables are price trend and volatility. Does the one signal class with a
documented long-horizon relation to *expected return* — valuation — add after-tax CAGR to K for a
Turkish-resident owner, at matched exposure and with anchors frozen before the test window?

## 2. Data (single source)

| file | source | notes |
|---|---|---|
| `data/historical/valuation_v1_shiller_monthly.csv` | Shiller's `ie_data.xls`, sheet `Data` (shillerdata.com; the download URL is recorded in `DATA_MANIFEST.json`) | month dates `YYYY.MM`, prices `P`, dividends `D`, earnings `E`, `CPI`; 1871-01 → 2024-09. Read with the in-repo minimal OLE/BIFF8 reader `xls_biff.py` (the research venv has no `xlrd`) |
| `data/historical/valuation_v1_ndx_daily.csv` | Yahoo chart API `^NDX` 1d, 1985-10-01 → 2000-03-20 | calibration window of V4 only; open/close, no dividends (price index) |

Derived monthly series (all computed from the raw `P`, `D`, `E`, `CPI` columns, **not** from the
workbook's own derived columns):

* **Earnings yield** `EY_t = E_t / P_t`.
* **CAPE** `CAPE_t = (P_t/CPI_t) / mean_{k=t-119..t}(E_k/CPI_k)` (10-year trailing average of real
  earnings), defined from 1881-01.
* **Forward 10-year real total return** `F_t = (Π_{k=t+1..t+120}(1+r_k))^(1/10) - 1` with
  `r_k = (P_k + D_k/12)/P_{k-1} × CPI_{k-1}/CPI_k - 1` (Shiller's own convention for dividends).

Validation rows committed with the data (Phase 1, `--v0`): my `F_t` against the workbook's column
"10 Year Annualized Stock Real Return" and my `CAPE_t` against the workbook's "P/E10 or CAPE"
column; both are reported with n, max |Δ| and correlation.

**Stale-publication rule (no look-ahead).** The signal used for a decision on session date `d`
whose calendar month is `M` is the value of month `M-1`. Shiller's `E` (and therefore my `EY` and
`CAPE`) ends 2024-06 while `P`/`CPI` end 2024-09 (the workbook was last saved 2024-09-04), so for
decisions from 2024-08 onward the last published figure is carried forward. This is the behaviour
of an owner who trades on the most recent published value; it is disclosed, and its share of the
main window is 564 of 6,679 measured sessions (8.4 %). A **report-only** robustness row
(`R-STALE`, not a gate) re-prices the arms with the multiplier set to 1.0 on the frozen-signal
sessions, and a second report-only row gives the arm−K Δ on 2000-03-09 → 2024-06-28.

Frozen constants (computed once from the committed CSV, before any simulation):

| constant | definition | value (filled by `--v0`) |
|---|---|---|
| `EY_REF` | median of monthly `EY` over 1950-01 → 1999-12 | see `RESULTS.json:constants` |
| `CAPE_Q90` | 90th percentile of monthly `CAPE` over 1871-01 → 1999-12 (defined months, linear interpolation) | see `RESULTS.json:constants` |
| `V4_C` | the scalar solved so that V4's mean target weight equals K's over the `^NDX` window 1985-10-01 → 2000-03-08 (sessions from `START`=253 on; secant solve to 1e-9) | see `RESULTS.json:constants` |

## 3. V0 — pre-run signal gate (gate, not an arm)

Exactly as specified in I5, first, and committed on its own before any arm runs.

Sample: months 1871-01 → 1949-12 with both `X_t` and `F_t` defined. OLS `F_t = a + b·X_t + u`.

* Efficiency: the dependent variable is a 10-year forward return, so monthly observations overlap
  by 120 months. The **primary** t-statistic is Newey–West HAC with 120 lags; the plain OLS
  t-statistic is reported alongside.
* Gate: **EY** slope `b > 0` and `|t_NW| ≥ 2`, **and** **CAPE** slope `b < 0` and `|t_NW| ≥ 2`.
* OOS evidence (reported, not gating): the same two regressions on 1950-1999-12 and 2000-01 →
  2014-09 (the `F_t` of the last 10 years is not yet realized, so the 2000s sample ends 2014-09).
* **Stopping rule.** If V0 fails, the study ends here: `RESULTS.json` + `SUMMARY.txt` +
  `docs/research/valuation_v1.md` are written with the V0 tables and the statement that arms
  V1–V4 were not run. Arms that are not run are not reported as results.

## 4. Arms (all parameters fixed here; multipliers applied to the on-state weight)

Common to every arm: K's execution layer, unchanged (`band`, `usd_rule`, `defer`, `DEC_TAIL = 10`,
`GAIN_TIERS_B = 0.5/3/8 pp`, `COST_PER_SIDE = 4 bps × m`, `CAP = 0.98`, `BAND = 0.5 pp`); the
multiplier `μ_t ∈ (0, 1.3]` is applied to `raw = on_eff × factor × vt` — i.e. to both the full E2
leg and the half-weight early leg — **before** the 0.98 cap, so the cap remains an invariant. `μ_t`
is a function of the calendar month of the decision session, from the month-lagged signal.

| arm | `μ_t` |
|---|---|
| **V1** | `clip(EY_{M-1} / EY_REF, 0.70, 1.15)` |
| **V2** | `0.75` while `CAPE_{M-1} > CAPE_Q90`, else `1.00` |
| **V3** | `0.75` while `CAPE_{M-1} > med_{M-1} + sd_{M-1}`; `1.10` while `CAPE_{M-1} < med_{M-1} - sd_{M-1}`; else `1.00`, where `med`/`sd` are the expanding median and population sd (ddof = 0) of every `CAPE` observation from 1881-01 up to **and including** `M-1` (no frozen anchor) |
| **V4** | `V4_C × V2's μ` (the level-free interpretation arm: same two-state tilt, rescaled so its mean target weight over 1985-10-01 → 2000-03-08 equals K's) |

Reported per arm, mandatorily: the realized mean target weight over the main window (K's is
67.3182 %), the mean |Δw| per session, the share of sessions where μ ≠ 1, and the de-risked-session
share. **Selection rule (pre-declared):** the verdict is taken on **V4** (exposure-matched) and
**V3** (causal, no frozen anchor). V1 and V2 are the *level* variants; they cannot be counted as
gains while their realized mean weight is below K's, and they are reported as such.

## 5. Gates (all must pass; the candidate must pass on its own, not on a portfolio of arms)

Two-tier rule, pre-declared verbatim from `idea_survey_v3` §6.1 as I5 requires: an arm whose
realized mean target weight over the main window is within ±1.0 pp of K's is **exposure-neutral**
and is gated pre-tax on *non-inferiority*; an arm outside that band is a **level change** and takes
the full gates. The tier is decided from the realized mean weight, mechanically, after the run.

*Exposure-neutral tier (pre-tax):* G5 median Δ ≥ −0.05 pp; G9 turnover/yr ≤ 2 × K. G1–G4, G6, G7
are reported but not gating.
*Level-change tier (pre-tax):* G1–G7 below, plus G9.

| gate | condition (vs K, m = 2) |
|---|---|
| G1 | CAGR ≥ K, **or** (Calmar ≥ 1.1 × K and CAGR ≥ K − 0.5 pp); base and calibrated |
| G2 | max DD ≤ K + 1.0 pp; base and calibrated |
| G3 | 2017-01-03 → 2026-09-29 CAGR ≥ K − 1 pp; base and calibrated |
| G4 | CAGR > K in ≥ 2 of 3 sub-periods P1 → 2008-12-31, P2 2009 → 2016, P3 2017 → ; base |
| G5 | main block bootstrap (stationary, mean block 20, seed 20261011, 5,000 ten-year paths, draws identical to `fast_engine`'s, both financing models, same paths for all arms): median over paths of (arm − K) CAGR ≥ 0 |
| G6 | G1 at m = 1; base and calibrated |
| G7 | **reduced, pre-declared:** the valuation state is defined only for US markets, so G7 runs on the **3 US OOS windows** (`GSPC_1957_1999`, `M1` ^IXIC, `M2` ^GSPC; calibrated, m = 2, R-USD rule as `fe_study.oos_stages` builds them): median over the 3 of (arm − K) CAGR ≥ 0, **and** arm CAGR ≥ K − 0.5 pp in ≥ 2 of 3, **and** max DD ≤ K + 3 pp in ≥ 2 of 3 and never > K + 8 pp. The non-US windows M3–M5 are **reported only** (applying a US valuation state to the Nikkei/DAX/FTSE is not the tested object) |
| G8 | **unavailable — pre-declared reduced gate.** `e2_confirmation_v1`'s K5 pooled OOS bootstrap is not ported into `research/fast_engine` (its per-market overnight/intraday resampling is a different bootstrap mechanism, not a target-weight option), so G8 cannot be produced on the mandatory engine. No substitute is claimed; it is recorded as a caveat. The pooled statistic over the 3 US OOS windows is reported next to G7 |
| G9 | turnover/yr ≤ 2 × K's (m = 2, base) |

*After tax (decisive, `vt_level_tax_v1` engine, liquidated convention, $100k, m = 2, base
financing):* the arm must **beat K in all three windows** (2000-03-09 → 2026-09-29, 2010-01-01 →
, 2017-01-03 → ) **and** the clean bootstrap (seed 20261011, block 20, $100k) must give
B1-recent (progressive, real 2016–26 TRY calendar) **and** B2-flat median Δ ≥ 0 with share > 0
≥ 55 %. $10k and $1M are reported.

*Exposure-normalised CAGR* (reported for every arm, required by the two-tier rule):
`(1+CAGR)^(1/mean_w) - 1`.

## 6. Mechanics, windows, reproducibility

* Main window 2000-03-09 → 2026-09-29 (6,679 measured sessions), `START` = 253, base and calibrated
  (`+0.75 %` financing) financing.
* Three after-tax windows: 2000-03-09 →, 2010-01-01 →, 2017-01-03 → (all → 2026-09-29).
* `--harness`: `python -m research.fast_engine.parity --quick` must PASS, and K's anchors must be
  reproduced within 0.01 pp through the engine — pre-tax base 15.7649 % / calibrated 15.1785 %
  CAGR, max DD 46.6698 % / 46.7948 %; after tax at $100k 10.7533 / 15.2354 / 23.9720 %; R2 (C0)
  15.4766 / 14.8961 %, DD 46.7584 / 46.8797 %, after tax 10.1600 / 14.7059 / 22.8861 %.
* The bootstrap needs a per-arm target series (the engine's `boot` drives every arm from one
  `wents` array), so `fe_arm_boot.py` is a **copy** of `research/fast_engine/fe_boot.py` in which
  the wants array gains an arm axis; the only behavioural change is that axis. Its parity cell
  asserts that with identical wants for every arm it reproduces `fe_boot.boot` bit for bit for the
  same seed/block/paths.
* `--run` is run twice; `RESULTS.json` must be byte-identical between the two runs; the wall-clock
  time of `--run` is recorded in `SUMMARY.txt`.
* Bug fixes found after the first run go in an `## Amendments` section of the report, never into
  the arm or gate definitions.
