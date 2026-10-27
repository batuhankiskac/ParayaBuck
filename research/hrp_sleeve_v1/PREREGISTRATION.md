# hrp_sleeve_v1 -- preregistration

Pre-registered before any non-control real-data run. Code: `research/hrp_sleeve_v1/screen.py`,
`sleeve_data.py`, `alloc.py`, `hs_sim.py` (a copy of `research/fast_engine3/fe3_sim.py` whose only
change is a per-leg resize band). Inputs: `DATA_MANIFEST.json`.

## 1. Question (audit item 53)

Does allocating K's idle and vol-target-freed capital to a diversifier sleeve of IEF, GLD and BIL,
weighted by HRP (D1), HERC (D2) or naive inverse-volatility (D3), improve K after Turkish tax?

## 2. Control and reference

- **K (control, C0 of this study):** the production rule exactly as in `band_h2_combo_v1` arm K:
  QLD weight `w_R2(t)` (E2 + early re-entry, cap 0.98), 0.5 pp resize band, A1u gain-scaled downsize
  band, H2 December deferral, zero idle-cash yield, next-open fills, base financing, m = 2.
- **R2 (reference row only):** arm R2 of the same harness.
- Harness check (`--harness`): K and R2 through the per-leg-band kernel must match `fe3_sim` bit for
  bit (NAV path and all scalars) and reproduce the published K after-tax CAGR at $100k
  (2000 window 10.75 %, see band_h2_combo_v1/RESULTS.json).

## 3. Sleeve assets (daily total-return series, pre-registered splices)

| asset | real series | proxy before real series | proxy cost |
|---|---|---|---|
| IEF* | IEF adjusted close from 2002-07-31 | GS10 (FRED, monthly step yield), duration 7.5: r_t = y_{t-1}/252 − 7.5·Δy_t − 0.15%/252 | 0.15 %/yr |
| GLD* | GLD adjusted close from 2004-11-19 | continuous gold futures (riskoff_rotation_v2_gcf adjusted close) from 2000-08-31 to 2004-11-18, minus 0.40 %/yr; NaN before 2000-08-31 | 0.40 %/yr |
| BIL* | none (synthetic throughout) | DTB3 accrual: r_t = DTB3_{t−1}/252 − 0.1354 %/252 | 0.1354 %/yr |

A real return is used on a session only when that session and the prior session both exist in the
real file; otherwise the proxy. Opens of the three sleeve legs are set equal to closes, because the
restored files carry no open series (caveat: fill-price noise, no drift bias intended).

**Universe rule (no look-ahead):** an asset may receive weight at a decision only if its trailing
252 daily returns are all available. IEF* and BIL* qualify from the first decision (1999-03 history);
GLD* first qualifies on the first month-end with 252 valid returns (about 2001-08).

## 4. Arms (all parameters fixed here)

Sleeve legs: B = IEF*, C = GLD*, D = BIL*. Leg A = QLD with weight `w_R2(t)`, identical to K.

Sleeve weight at session t: `s_t = max(0.98 − w_R2(t), 0)`; leg target `s_t · π_k(t)`.

π(t) is recomputed on the **last session of each calendar month** from the sample covariance of the
trailing **252** daily simple returns (ddof = 1, no shrinkage), long-only, sum 1 over the universe,
and held until the next month-end decision (the decision at close t fills at the next open, as
the kernel already does for every leg). Before the first decision the sleeve is 0.

| arm | π from |
|---|---|
| **D1** | HRP (López de Prado 2016): distance √½(1−ρ), single linkage, quasi-diagonal leaf order, recursive bisection with inverse-variance cluster weights |
| **D2** | HERC (Raffinot 2017): the same tree and order as D1; within-cluster weights are equal-risk-contribution (SLSQP, ftol 1e-14); the split uses α = 1 − V_L/(V_L+V_R) with the ERC cluster variances |
| **D3** | naive inverse volatility, w_k ∝ 1/σ_k |

Execution for every leg: the K rule (`fe_sim.RULE["K"]`: A1u gain band, H2 December deferral,
next-open fills, cost 4 bp per side × m). **Resize band:** QLD keeps the production 0.5 pp
(`C0_BAND`); each sleeve leg trades only when |target − held| > **2 pp** (`band_legs`).

Tax: every sleeve sale is taxed in the multi-instrument Turkish engine (`fe_tax`, one taxpayer,
per-leg FIFO lot books, Yi-ÜFE indexation, progressive tariff, liquidated at the end).

Number of arms: 3 (D1, D2, D3). Controls K and R2 are not arms. Total trials for DSR: N = 372 + 3.

## 5. Data and windows

- Engine calendar: QQQ/SPY sessions from 1999-03-10; main window 2000-03-09 → 2026-09-29 (the same
  as K's). No data dated after 2026-09-29 is read (the input files extend to 2026-10-15 but the
  engine calendar stops at 2026-09-29; the sealed-window rule is respected).
- After-tax windows (as K): 2000-03-09 → 2026-09-29, 2010-01-01 → end, 2017-01-03 → end; $100k,
  liquidated, progressive, base financing, m = 2. Pre-tax rows run at capital 1.0 (fe_study
  convention).
- Pre/post-2020 split: 2000-03-09 → 2019-12-31 and 2020-01-01 → 2026-09-29, each run from its own
  start, $100k after tax.
- Pre-tax main window: capital 100k, `pre_tax`, m = 2, from session 253 (2000-03-09).

## 6. Gates and selection rule

Candidate = every gate below passes against K:

- **A1 after tax, three windows:** Δ after-tax CAGR > 0 in 2000, 2010 and 2017.
- **A2 pre/post 2020:** Δ after-tax CAGR > 0 both before and after 2020-01-01.
- **A3 turnover (G9 analogue):** trades per year ≤ 2 × K's, pre-tax main window.
- **A4 bootstraps:** clean after-tax B1-recent and B2 (flat 20 %) median Δ ≥ 0 and share of
  positive paths ≥ 55 %. **Run only if A1–A3 pass** (declared here so the bootstrap is not run on
  arms that already failed the windows). If not run, the verdict is "not a candidate" with
  A4 "not run", never "pass".
- **Pre-tax G1–G8 (ewma_band_v1 definitions):** NOT run. Their OOS markets (^GSPC 1957–99, ^IXIC, ^N225,
  ^GDAXI, ^FTSE) have no sleeve proxies with matching instruments (IEF/GLD/BIL are US and only exist
  in proxy form before 1999), so the OOS gates are declared unusable (reduced gate). Pre-tax main
  window CAGR and max DD are reported vs K as information only.

Reported for every arm: DSR (`research/gates_v1/stats.py`, `dsr`, with the daily after-tax
$100k NAV log-return series of the main window, N = 375, `var_trials_floor`); sells per year per
leg; PBO not applicable (grid of 3 arms, below the 4-arm threshold); provenance stamp
(`research/gates_v1/provenance.py`) into RESULTS.json.

## 7. Known limitations (declared in advance)

- The sleeve proxies before the real series (GS10 duration model, gold futures, DTB3 for BIL) are
  models, not tradable history; IEF* before 2002 uses monthly-step yield changes and therefore
  understates daily variance.
- No open prices for IEF/GLD (opens = closes).
- The gold-futures proxy has roll effects.
- No amendment to any arm, parameter, gate or the universe rule after seeing results. Bugs go in an
  "Amendments" section of the report with the reason.
