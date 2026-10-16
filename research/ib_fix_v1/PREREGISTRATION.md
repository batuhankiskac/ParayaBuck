# ib_fix_v1 — bug-fix re-screen of idea_bank_v1's IB12 and IB14 (pre-registered 2026-10-16)

Committed **before** any non-control arm is simulated, per the batch_screen protocol
(`research/batch_screen/PREREGISTRATION.md`, README §2). Re-screening two plugins of
`bank_v1` (`docs/research/batch_bank_v1.md`, tag `archive/research/batch-bank-v1-2026-10`) after
fixing two reported defects in their own code. **No arm, no threshold, no parameter, no gate and
no data changes** — the six arms are the bank's, with the bank's specs byte-identical
(`spec_hash` unchanged from `bank_v1`); only the two statistics' *implementation* is corrected.

## 1. Question

Do IB12 (real-10y veto) and IB14 (credit-spread momentum) change verdict after the two reported
bugs are fixed, and does either survive stage 1 and the multiple-testing deflation?

## 2. The bugs (each verified against the framework's own convention before changing code)

* **IB12 (a) — bootstrap source session off by one column.** `real_10y` read the chunk's source
  date as `extra_days[extra_index - 1]`. The framework's convention is `pool[extra_index]`:
  `batch_screen.extra.aligned` returns `pool[index]` and `boot._extra_index` builds
  `concat([idx[:, :1], idx])`, i.e. the session `data.extra(key)` itself reads for that column.
  Reading `- 1` therefore took the CPI year-on-year of the *previous* drawn session's month.
  Verified: 391 of 8,331 cells of one real clean-bootstrap chunk differ.
* **IB12 (b) — the 4th vote's missing-input fallback.** With the real rate missing, arm C
  required `votes - 1 = 3` of the 3 price votes. The plugin's own docstring and the bank's spec
  say the 4th vote *abstains*, i.e. 2 of 3 among the price votes (IB20's convention). Verified:
  the two thresholds differ on 114 sessions of the synthetic fixture.
* **IB14 — monthly series aligned by exact date + forward fill.** `credit_series` read
  `data.extra("baa_m", ffill=True) - data.extra("aaa_m", ffill=True)`: FRED stamps a monthly
  observation on the 1st, so a month whose 1st is not a trading session is never matched and the
  *previous* month's value (already one month stale under the 1-month lag) carries over. Fixed
  with an as-of lookup on the series' own monthly grid: the value of the last reference month at
  or before **M − 1** (`_asof_month(months, spread, mk - 1)`, `mk` the month ordinal of the
  session's own source date), the publication lag unchanged. The old helper `month_lag` (which
  additionally took the lag in the panel's column space, so it could not see month M − 1 in a
  panel that starts in month M, and was invalid for a chunk's non-monotone drawn sessions) is
  deleted. Verified live on all three monthly windows (M1, M2, GSPC_1957_1999).

Unit checks: `research/ib_fix_v1/check_plugins.py` (21 checks, synthetic fixtures + real panels,
no simulation, no registry write). Nothing else in the two modules is touched.

## 3. Arms (fixed, unchanged from `bank_v1`; `rule = K`, `leg = 2x`, `cash_yield = none`)

| idea | arm | rule |
|---|---|---|
| IB12 | A | `weight = 0 while real10y > 2.0 %`, else production |
| IB12 | B | `weight *= 0.5 while real10y > 1.5 %` |
| IB12 | C | 3-of-4 vote {SMA200, TSM252, DON126, real10y ≤ 2.0 %} |
| IB14 | A | `weight = 0 while BAA10Y_t − BAA10Y_{t−60} > +0.30 pp` |
| IB14 | B | `weight *= 0.5 while the same change > +0.15 pp` |
| IB14 | C | 3-of-4 vote {SMA200, TSM252, DON126, 60-session change ≤ +0.30 pp} |

`real10y = DGS10 − CPI YoY (1-month lag)`, missing → no veto / the 4th vote abstains. IB14's
series: daily `BAA10Y` on main / ETFs / bootstraps, monthly `BAA − AAA` (1-month lag) on
`GSPC_1957_1999`, M1, M2, `NotApplicable` on M3–M5 (the bank's declared US-only OOS scope, as in
`bank_v1`). Control `C0 = k_control/K`; `r2_control/R2` reported as the reference row.

## 4. Data and mechanics

Unchanged from `bank_v1`: main window QQQ/QLD* 2000-03-09 → 2026-09-29 (base and calibrated
financing, m = 2), engine RULE `K`, stage-1 clean bootstrap (seed 20261011, block 20, 5,000
pre-tax / 1,000 after-tax paths, $100k, `progressive` B1-recent and `flat` B2), stage 2 = the full
holdout set of the framework's §3. Extra inputs and their SHA-256 are declared in
`research/batch_screen/EXTRA_DATA.json` (`DGS10`, `CPIAUCSL`, `BAA`, `AAA`, `BAA10Y`); the subset
this study reads is repeated in `research/ib_fix_v1/DATA_MANIFEST.json`.

## 5. Gates (framework's, unchanged) and selection

Stage 1 (main window + its bootstrap): after-tax 2000-window Δ > 0 **and** B1-recent median Δ > 0
**and** pre-tax max DD ≤ K + 2 pp (base and calibrated) **and** turnover ≤ 2 × K. Stage 2 for
survivors: G1–G9, after tax at $100k in all three windows, B1/B2 median Δ ≥ 0 with share > 0
≥ 55 %, 21 ETFs after tax, 21 January rolling starts, six OOS windows, pooled OOS bootstrap, four
fresh-seed cells. A declaration additionally needs `rw_stepdown` ≤ 5 % **and** deflated Sharpe
≥ 0.95 in the deflation pass.

## 6. Ledger

The six arms are already registered (`bank_v1`); their specs are unchanged, so this batch appends
one line per arm (`batch = "ib_fix_v1"`, new `code_hash`) and N (distinct
`(idea, arm, spec_hash)`) is expected to stay at its previous value — reported exactly in
RESULTS.json / SUMMARY.txt. Old lines are kept; no line is removed or edited.

## 7. Amendments

(none yet)
