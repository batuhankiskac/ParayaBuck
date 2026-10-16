# ib_fix_v1 — bug-fix re-screen of idea_bank_v1's IB12 and IB14 (2026-10-16)

> Framework `research/batch_screen` (README = the rules), batch `ib_fix_v1`, branch
> `research/ib_fix_v1` off `research/batch_run_v1` (`e49603af`). Code fix `af04eccb`; results
> `research/batch_screen/results/ib_fix_v1/`, ledger `research/batch_screen/REGISTRY.jsonl`.
> Pre-registration: `research/ib_fix_v1/PREREGISTRATION.md`; unit checks
> `research/ib_fix_v1/check_plugins.py`; inputs `research/ib_fix_v1/DATA_MANIFEST.json`.

**Verdict: no candidate, and the fixes do not change any verdict.** All three reported defects are
real and are fixed; the two were live on real data, the third is a wrong threshold on a fallback
that no main-window session reaches. IB12's arm C is still the closest of the six (after-tax 2000
Δ −0.99 pp, B1 −0.36 / share 0.41) and every arm fails stage 1 on the after-tax window and the
bootstrap; IB14's stage-1 record is **byte-identical** to `bank_v1` (its bug lives only in the
monthly OOS windows, which stage 1 never touches). N is unchanged at 107 and nothing is declared:
`rw_stepdown` rejects nothing, White's p = 1.0000.

## 1. What was wrong (each verified before the code changed)

| # | report | verified | evidence |
|---|---|---|---|
| IB12 (a) | chunk source date read as `extra_days[extra_index - 1]` | **real** | framework convention is `pool[extra_index]` (`extra.aligned` → `pool[index]`; `boot._extra_index` = `concat([idx[:, :1], idx])`), i.e. the session `data.extra()` itself reads for that column; 391 of 8,331 cells of one real clean-bootstrap chunk differed |
| IB12 (b) | missing-rate fallback demanded 3 of 3 price votes | **real** | `votes - 1 = 3` with `votes = 4`; the plugin's docstring and the bank say the 4th vote abstains → 2 of 3; the two thresholds differ on 114 fixture sessions. On the main window the fallback is reached on only 19 sessions (2025-11, the BLS shutdown month with no CPI year-on-year) and there the 3 price votes are all true, so it changes no main-window number |
| IB14 | monthly BAA−AAA aligned by exact date + forward fill | **real** | FRED stamps a monthly observation the 1st, so a month whose 1st is not a session is never matched and the *previous* month's (already lagged) value carries over: 35.1 % / 32.4 % / 34.4 % of M1 / M2 / GSPC_1957_1999 sessions differed, mean \|diff\| 0.02–0.03 pp, max 0.48 pp |

Fixes, nothing else touched (no parameter, no threshold, no window, no arm):

* IB12: `extra_days[extra_index]`; arm C's fallback threshold `spec["need"] - 1` (2 of 3).
* IB14: `_asof_month(months, spread, mk - 1)` — the value of the last reference month at or before
  M − 1, read from the series' own monthly grid, which is exactly the publication lag the module
  already documented. The old in-panel `month_lag` helper is deleted: it also could not see month
  M − 1 in a panel that starts in month M (a window's first month read NaN) and was invalid for a
  chunk's non-monotone drawn sessions.
* `research/ib_fix_v1/check_plugins.py`: 21 checks (synthetic panel + real main/OOS panels + one
  real bootstrap chunk; independent loop-based as-of/CPI references; no simulation, no ledger
  write). Every check PASSes and each proves the code is **not** the old behaviour.

No framework bug: `data.extra(..., ffill=True)` does what the README documents ("carries the last
value forward"); a monthly series whose 1st is not a session still needs the caller to take its own
as-of decision. Recorded here as a usage caveat, not a change request.

## 2. Stage 1, old (`bank_v1`) vs new, per arm

Control `k_control/K` is byte-identical between the two runs (its record and every per-path vector),
so the Δ columns are paired. K itself (main window, m = 2): base CAGR **15.7649 %**, max DD
**46.6698 %**; calibrated **15.1785 %** / **46.7948 %**; after-tax $100k 2000-window
**10.7533 %** — the published K anchors.

CAGR base/cal and max DD are the *levels* of that arm's own main-window backtest (%), not Δs.

| arm | CAGR base old→new | CAGR cal old→new | max DD base old→new | AT2000 Δ old→new | B1 med Δ old→new | B1 share old→new | B2 med Δ old→new | turnover ×K | maxDD Δ vs K | stage 1 |
|---|---|---|---|---|---|---|---|---|---|---|
| IB12/A | 12.87 → 12.87 | 12.39 → 12.39 | 45.54 → 45.54 | −2.1283 → −2.1283 | −1.5805 → **−1.8479** | 0.369 → 0.343 | −1.0910 → **−1.3991** | 1.156 | −1.13 | FAIL |
| IB12/B | 13.32 → 13.32 | 12.84 → 12.84 | 35.74 → 35.74 | −2.1598 → −2.1598 | −0.9459 → **−1.0137** | 0.358 → 0.363 | −0.6495 → **−0.6664** | 1.090 | −10.93 | FAIL |
| IB12/C | 14.58 → 14.58 | 14.01 → 14.01 | 42.93 → 42.93 | −0.9907 → −0.9907 | −0.3372 → **−0.3621** | 0.426 → 0.407 | −0.3179 → **−0.3378** | 1.014 | −3.74 | FAIL |
| IB14/A | 15.55 → 15.55 | 15.00 → 15.00 | 39.87 → 39.87 | −0.2887 → −0.2887 | −2.3055 → −2.3055 | 0.325 → 0.325 | −1.7322 → −1.7322 | 1.152 | −6.80 | FAIL |
| IB14/B | 14.58 → 14.58 | 14.05 → 14.05 | 40.90 → 40.90 | −0.8332 → −0.8332 | −1.1454 → −1.1454 | 0.345 → 0.345 | −0.7359 → −0.7359 | 1.099 | −5.77 | FAIL |
| IB14/C | 15.53 → 15.53 | 14.95 → 14.95 | 48.15 → 48.15 | −0.3224 → −0.3224 | −0.4401 → −0.4401 | 0.432 → 0.432 | −0.3380 → −0.3380 | 1.058 | +1.48 | FAIL |

Reading:

* **IB12** — the two fixes move **only the bootstrap** cells (B1/B2 median Δ, and the pre-tax
  bootstrap levels, e.g. IB12/A's `boot_level` pre median Δ 6.46 → 6.05 pp): the corrected CPI
  month makes the veto slightly *more* binding inside the resampled paths, so every IB12 arm gets
  worse. The main-window cells are unchanged at 6 dp for the reason above (19 fallback sessions,
  all with 3 of 3 price votes).
* **IB14** — every stage-1 field is identical. Stage 1 reads the daily `BAA10Y` (main window, its
  clean bootstrap, the ETF windows); the monthly `BAA − AAA` as-of path runs only on the three
  monthly OOS windows, which this arm never reaches. The fix is therefore required for the
  *held-out* evaluation to be meaningful, and is a no-op for this stage-1 record.
* IB12/C is the only arm whose after-tax 2000 Δ is inside 1 pp (−0.9907), but its B1 median is
  −0.36 pp with share 0.41 and its max DD is 42.93 % vs K's 46.67 % — still a stage-1 failure
  (`after-tax Δ > 0` and `B1 median Δ > 0` both fail).

## 3. Stage 2

Not run: no arm passed stage 1 in either batch (`stage2_pass = []`). The bank's `bank_v1` record
for these six arms (/rejected at stage 1) therefore stands unchanged — with the one substantive
correction that IB12's bootstrap evidence is now the fixed one, and IB14's monthly-window
alignment is now the bank's own rule.

## 4. Deflation (`--deflate --batch ib_fix_v1`)

On the six arms' B1-recent after-tax Δ vectors (1,000 paths, seed 20261011, block 20), N = 107:

| test | result |
|---|---|
| White's Reality Check | best arm IB14/C, `t_max = −4.7636`, p = **1.0000** |
| Romano–Wolf stepdown (α = 5 %) | rejected: **none** (all `p_adjusted = 1.0`; t ∈ −15.80 … −4.76) |
| deflated Sharpe | IB14/C −0.1506 (best), all arms negative; declared: **none** |

`variance of trial Sharpes` = 0.0213; `deflation_rejected = []`, `declared = []`.

## 5. Ledger

| | before | after |
|---|---|---|
| `REGISTRY.jsonl` lines | 140 | 152 |
| distinct arms N = `n_trials` | 107 | **107** |
| `stale` arms | IB12/A–C, IB14/A–C | none |

The six arms keep their `bank_v1` lines; the fix appended six `screened` lines (`batch =
"ib_fix_v1"`, new `code_hash`, `commit_gate = pass` at `af04eccb`) and the byte-identity re-run six
`rerun` lines. **N did not grow**: N counts distinct `(idea, arm, spec_hash)` and no spec changed —
only the code. `--verify-registry` PASSes on all 152 lines (`problems = []`, `skipped = []`,
`stale = []`).

## 6. Cost and reproduction

```sh
R=~/.venvs/pb-research/bin/python
PYTHONPATH=. $R -m research.ib_fix_v1.check_plugins                    # 21 checks, 12 s
PYTHONPATH=. $R -m research.batch_screen.run --verify-registry         # PASS, 1.5 s
PYTHONPATH=. $R -m research.batch_screen.smoke --ideas IB12,IB14       # OK, 11 s
PYTHONPATH=. $R -m research.batch_screen.run --harness                 # 73 checks PASS, 201 s
PYTHONPATH=. $R -m research.batch_screen.run --batch ib_fix_v1 --ideas IB12,IB14 --workers 4
PYTHONPATH=. $R -m research.batch_screen.run --batch ib_fix_v1 --ideas IB12,IB14 --workers 4  # byte-identity
PYTHONPATH=. $R -m research.batch_screen.run --deflate --batch ib_fix_v1
```

`--harness` PASSes 73/73 (incl. `python -m research.fast_engine.parity --quick`). Batch wall clock:
**147.4 s** for the six arms (4 workers, cold) and **25.8 s** re-run (numba cache warm). Re-run
`RESULTS.json` is byte-identical — `sha256 63aea26c7122126e1c912206db8681134832000580f2b424f59dd1eec15d84ff`,
with only `timing.json` differing.

## 7. Caveats

* The IB14 fix cannot be *validated* by this batch — no arm reached the windows where it acts.
  Confirmation is mechanical: the unit checks compare all three monthly windows against an
  independent loop-based as-of reference, and differ from the old alignment on ~1/3 of sessions.
* The IB12 bootstrap shift is small (−0.27 / −0.07 / −0.02 pp on B1 for A / B / C) and moves in the
  adverse direction; it does not change any gate outcome (all six arms failed stage 1 by wide
  margins before and after).
* 20-session blocks cannot capture longer serial dependence, and the stage-1 pass rule is a screen,
  not a test — unchanged from the framework's own stated assumptions.
