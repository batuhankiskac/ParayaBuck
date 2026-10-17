# closeout_v1 — the last non-kernel-blocked arms (2026-10-17)

> Evidence: tag `archive/research/closeout-2026-10` (not on `main`).

**Verdict: NO CANDIDATE. Nothing here changes production K.** Four arms are screened and all four
are rejected in **Stage 1** (the after-tax 2000-window Δ and the B1-recent bootstrap median are
negative for every one of them); no arm reaches Stage 2, `declared = []`. IB35's "plain fixed 3 pp
band" — the one width of the `ewma_band_v1` plain-band family that had never been run — costs
0.46 pp/yr before tax and 0.57–0.95 pp after tax, exactly what that family's own report found at
2.5/5/10 pp. IB24's T-bill sleeve with an entry/exit hysteresis **does** help before tax
(+0.13…+0.30 pp/yr and 2.6–2.9 pp of drawdown, with fewer round trips the longer the entry lag).
After Turkish tax it loses: the tariff taxes every sleeve flip as a realisation while K's execution
layer is built to defer them, and the clean after-tax bootstraps are decisive (B1 medians
−0.31…−0.75 pp, share > 0 in 0.05–0.19 of paths). The two accounting-only arms are computed
outside the ledger: **IB38/A**'s regime matrix shows K's execution layer paying for itself in every
regime and at every capital (largest under the progressive tariff, +0.59 pp over R2 at $100k against
+0.29 pp pre-tax and +0.41 pp flat on the 2000 window), and **IB38/B** is exactly neutral for one
aggregating taxpayer but worth +0.40/+0.64/+0.80 pp (two $50k accounts) and +0.69/+1.06/+1.25 pp
(three $33.3k accounts) under *separate* ladders — i.e. the split pays only if the accounts are
separate taxpayers, which the engine's own rule (one taxpayer's annual base) does not allow.
**IB38/C** stays `NEEDS_KERNEL`; **IC54/A** and **IC54/B** close as `NEEDS_DATA` with the source's
own refusal as evidence.

Evidence: `research/closeout_v1/` (`PREREGISTRATION.md`, `run_batch.py`, `make_bil_star.py`,
`ib38_report.py`, `ic54_closure.py`, `summarise.py`, `DATA_MANIFEST.json`, `SUMMARY.txt`) and
`research/batch_screen/results/closeout_v1/`. The pre-registration, the two plugin modules, the new
BIL\* series and the manifest were committed in **`cf4da03a`** (data-file line-ending fix in
**`f3a30e75`**) **before** any non-control arm was simulated on real data; only `smoke`
(`targets()` shapes, no return/NAV/gate) and the synthetic-vote state check ran before it. The run
takes 39.5 s on 6 workers, is offline apart from IC54's closure evidence, and reproduces
`RESULTS.json` byte for byte on a second pass (SHA-256 in `SUMMARY.txt`).

## 1. The four screened arms and the readings that fixed them

| arm key | module | spec (frozen by the idea bank) | window |
|---|---|---|---|
| `IB35_K/C_K` | `research/ideas/IB35_K.py` | `ib35k_band3pp`: the plain fixed 3 pp resize band — `band_up = band_down = 0.03`, `band = usd_rule = defer = False` | published |
| `IB24_2L/A_2L` | `research/ideas/IB24_2L.py` | K, `leg_b = "bilstar"`; enter the sleeve after **5** consecutive vote-off sessions, exit on re-entry | published |
| `IB24_2L/B_2L` | `research/ideas/IB24_2L.py` | the same with **N = 10** | published |
| `IB24_2L/C_2L` | `research/ideas/IB24_2L.py` | enter on the first off session, exit only after **5** consecutive on sessions | published |

* **IB35/C is the *plain* band, not "K with a 3 pp base width".** The bank's arm text calls it
  "the plain fixed 3 pp band … the only untested width in *that family*", and that family is
  `ewma_band_v1`'s B25/B50/B100: *"entries and exits always trade in full; while the position is
  held the strategy rebalances to target only when |target − held| > B"*, with no A1u/H2 (both
  post-date that study); the campaign's own vocabulary agrees ("C0/R2 (plain 0.5 pp band)",
  `batch_screen/rules.py`). The alternative reading of the frozen spec's `"rule": "K"` field — K's
  A1u/H2 kept and only the base width changed — is on the record in
  `PREREGISTRATION.md` §2.1 and is **not** screened: on a 3 pp base band A1u would still block
  every 3–8 pp downsize whose sale's USD FIFO gain fraction exceeds 5 %, so it would not be a
  *plain* band. A property check confirms the mechanism on real data: 744 trades against the plain
  0.5 pp control's 1645 and a lower turnover (148.5 against 159.1), with the engine's own
  `C0_BAND` untouched at 0.5 pp.
* **IB24 is the sleeve as an instrument**, because the arm's mechanism *is* the cost of the
  sleeve's round trips ("enter the bill sleeve only once the vote has been off for N consecutive
  sessions" exists to kill flips that pay a BIL round trip each time — `tbill_sweep_v1`: S1 turns
  over 5.8×/yr of bill notional, its +0.06 pp pre-tax edge goes negative in whipsaw years and it
  fails the block-20 bootstrap). A conditional *cash rate* — the bank's own plugin note — would make
  the switch free and untaxed and could not price the thing the three arms vary; held as an
  instrument, the sleeve pays 4 bps × m per side and its gain is taxed as an instrument gain
  (Yİ-ÜFE-indexed FIFO, one taxpayer), exactly as `tbill_sweep_v1` and `r2_tbill_combo_v1` modelled
  BIL.
* **IB24's weights** (fixed in `PREREGISTRATION.md` §2.2): `w_A` is production K's own target
  series (the early half-weight path untouched) and `w_B = max(0, 0.98 − w_A) · S`, the residual
  cash in bills while the hysteresis state `S` is on — `r2_tbill_combo_v1`'s construction of the
  sleeve against R2 verbatim ("while E2 is off **and** the early path is active, the remainder
  `0.98 − w` is idle and goes to BIL\*; while fully off, BIL\* is held at 0.98"). The equity leg is
  never touched, the total never exceeds the 0.98 cap (no loan, no margin option), and `S` is the
  only axis: `A/B = the vote off for ≥ N consecutive sessions`, `C = the vote off, or on for < 5
  consecutive sessions after an off session` (so the account never starts spuriously in bills).
  The alternative (a fixed 0.98 sleeve with production's target scaled by `1 − S`, the early path
  suppressed while the sleeve is on) is recorded there and not screened.
* **The BIL\* series** (`data/historical/closeout_v1_bil_star.csv`, `EXTRA_DATA.json` key
  `bilstar`) is `tbill_sweep_v1`'s `BIL* = DTB3 − 0.1356 %/yr`, accrued **per calendar day** so one
  date-keyed series answers every window's own session calendar; it starts at TB3MS's first
  observation (1934-01) so that `M2` (^GSPC 1934–1956) is covered. `make_bil_star.py --check`:
  1.7938 %/yr against the per-session reference's 1.7885 %/yr over the main window (0.0053 pp).

## 2. Results — the framework's own numbers, read out of the records

Δ = arm − K in pp. K (the batch control, re-simulated by the framework): pre-tax **15.7649 %
base / 15.1785 % calibrated**, max DD **46.67 / 46.79 %**, after tax at $100k **10.7533 / 15.2354 /
23.9720 %** — the published anchors, reproduced exactly.

| arm | pre-tax CAGR base / cal | Δ pp | max DD base / cal (Δ) | turnover ×K | after tax 2000 Δ | 2010 Δ | 2017 Δ | B1 median Δ / share | B2 median Δ / share | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| `IB35_K/C_K` | 15.3006 / 14.7204 | −0.46 | 47.79 / 47.90 (+1.12 / +1.11) | 1.025 | −0.5742 | −0.5085 | −0.9538 | −0.1944 / 0.305 | −0.1218 / 0.355 | reject_stage1 |
| `IB24_2L/A_2L` | 16.0155 / 15.4278 | +0.25 | 44.04 / 44.17 (−2.63 / −2.62) | 1.563 | −0.2463 | −0.0623 | −0.0500 | −0.4175 / 0.145 | −0.8229 / 0.019 | reject_stage1 |
| `IB24_2L/B_2L` | 16.0678 / 15.4799 | +0.30 | 43.89 / 44.02 (−2.78 / −2.77) | 1.376 | −0.1129 | **+0.0203** | −0.0284 | −0.3091 / 0.189 | −0.6699 / 0.031 | reject_stage1 |
| `IB24_2L/C_2L` | 15.8974 / 15.3104 | +0.13 | 43.78 / 43.91 (−2.89 / −2.88) | 1.918 | −0.2533 | −0.2015 | **+0.0970** | −0.7510 / 0.052 | −1.2373 / 0.003 | reject_stage1 |
| *(reference)* `R2` | 15.4766 / 14.8961 | −0.29 | 46.76 / 46.88 | — | 10.1600 % | 14.7059 % | 22.8861 % | — | — | — |

Failed gates, as the records report them (`stage1_gates`; Stage 2 was never reached, so `gates` is
`null` in every arm file and there is no `gates.failed` list to quote):

| arm | failed |
|---|---|
| `IB35_K/C_K` | after-tax 2000 Δ −0.5742 pp; B1 median Δ −0.1944 pp |
| `IB24_2L/A_2L` | after-tax 2000 Δ −0.2463 pp; B1 median Δ −0.4175 pp |
| `IB24_2L/B_2L` | after-tax 2000 Δ −0.1129 pp; B1 median Δ −0.3091 pp |
| `IB24_2L/C_2L` | after-tax 2000 Δ −0.2533 pp; B1 median Δ −0.7510 pp |

Max DD ≤ K + 2 pp and turnover ≤ 2 × K pass for all four. Every B1 Δ vector is 1000/1000 finite;
`DEFLATION.json`'s `excluded_non_finite` is empty, so **there is no NaN in any record to explain**.
The deflation rejects all four (`rw_stepdown` p = 1.0 for each, deflated Sharpe 0.0 against
`sr0` = 1.126, N = 316 trials; White's Reality Check p = 1.0, best arm `IB35_K/C_K` on t = −15.9).

### 2.1 What the four numbers say

* **IB35/C closes the dead-band family.** 744 trades against the plain 0.5 pp control's 1645 and
  a lower turnover, yet −0.46 pp/yr before tax and −0.51…−0.95 pp after tax, with a slightly
  *higher* drawdown (+1.1 pp): the trading cost the wider band saves is smaller than what it loses
  by sizing to a stale vol target — `ewma_band_v1`'s conclusion at 2.5/5/10 pp, now measured at the
  only width that family had left, on production K's target series.
* **IB24's sleeve helps before tax and loses after it**, which is the arm family's own prior
  ("low-medium: the family loses after tax; this arm attacks the measured cost, not the tax").
  Pre-tax the four arms are ordered by how rarely the sleeve is entered: B_2L (N = 10) 16.07 % >
  A_2L (N = 5) 16.02 % > C_2L (exit lag) 15.90 % > K 15.76 %, and every arm cuts the drawdown by
  2.6–2.9 pp. After the tariff the 2000 window is negative for all three and the bootstrap is
  decisive everywhere. The mechanism is *not* a bigger tax bill: the arms' total USD tax is
  slightly **lower** than K's (822–829k against 831k), but it is paid **earlier** — every sleeve
  flip is a realisation, while K's A1u/H2 layer defers equity trims (47.6 % of `A_2L`'s assessed
  TRY tax falls in the final year against 49.4 % for K) — and the round trips add 43 % more fees
  (`A_2L` 45.5k against K 31.8k USD), on `qld_sells` 359 against 273.
* `C_2L`'s 2017-window **+0.0970 pp** and `B_2L`'s 2010-window **+0.0203 pp** are single cells and
  are not a pass: the stage-1 rule (2000 window **and** B1) and both clean bootstraps are
  strongly negative, and the arms' own turnover is 1.38–1.92 × K.

## 3. IB38/A — the tax-regime matrix (report-only, not a trial)

Computed with the engine's own `fe_sim.simulate` / `fe_study` / `fe_tax` outside the ledger
(`ib38_report.py --run` → `ib38_report.json`). The bank's "six windows" cannot carry a tax regime:
only the three `TAX_WINDOWS` have the TRY/Yİ-ÜFE calendar (the bank's own data note says so), so
the matrix runs on those three — the reduction pre-registered in `PREREGISTRATION.md` §3 — plus the
published main-window pre-tax row as the tau = 0 anchor. Pre-tax rows are the un-liquidated `cagr`
(the published convention), taxed rows are liquidated, m = 2; pre-tax and flat cells are
capital-independent (the tariff does not act), and the max DD shown is that row's own.

**2000-03-09 → 2026-09-29**:

| regime | capital | R2 | A1u | H2 | K | K − R2 | max DD K |
|---|---|---|---|---|---|---|---|
| pre-tax (tau = 0) | any | 15.48 | 15.72 | 15.52 | 15.76 | +0.29 | 46.67 |
| flat 20 % | any | 11.72 | 12.04 | 11.79 | 12.13 | +0.41 | 46.67 |
| progressive 2026 | $10k | 11.69 | 12.06 | 11.80 | 12.18 | +0.49 | 46.67 |
| progressive 2026 | $100k | 10.16 | 10.61 | 10.30 | 10.75 | +0.59 | 46.67 |
| progressive 2026 | $1M | 9.19 | 9.74 | 9.35 | 9.88 | +0.68 | 46.67 |

**2010-01-04 → 2026-09-29**:

| regime | capital | R2 | A1u | H2 | K | K − R2 | max DD K |
|---|---|---|---|---|---|---|---|
| pre-tax | any | 22.16 | 22.35 | 22.13 | 22.35 | +0.18 | 38.46 |
| flat 20 % | any | 16.34 | 16.70 | 16.38 | 16.77 | +0.42 | 41.09 |
| progressive | $10k | 17.03 | 17.36 | 17.13 | 17.47 | +0.44 | 41.96 |
| progressive | $100k | 14.71 | 15.10 | 14.84 | 15.24 | +0.53 | 44.63 |
| progressive | $1M | 13.62 | 14.03 | 13.76 | 14.16 | +0.54 | 46.19 |

**2017-01-03 → 2026-09-29**:

| regime | capital | R2 | A1u | H2 | K | K − R2 | max DD K |
|---|---|---|---|---|---|---|---|
| pre-tax | any | 32.54 | 32.97 | 32.58 | 33.03 | +0.49 | 31.12 |
| flat 20 % | any | 24.27 | 24.95 | 24.38 | 25.10 | +0.84 | 36.85 |
| progressive | $10k | 26.04 | 26.67 | 26.26 | 26.92 | +0.88 | 33.57 |
| progressive | $100k | 22.89 | 23.70 | 23.16 | 23.97 | +1.09 | 34.98 |
| progressive | $1M | 21.72 | 22.58 | 22.01 | 22.86 | +1.13 | 35.47 |

(The full 4 × 3 × 3 × 3 grid, with terminal USD, tax USD and trades per year per cell, is
`research/closeout_v1/ib38_report.json`.) Readings:

* **The tariff, not the rule, moves the answer** — the bank's own point, quantified: K earns
  12.18 % at $10k against 9.88 % at $1M on the 2000 window (2.30 pp), while the *rule* spread at a
  fixed capital is at most 0.69 pp (K − R2 at $1M).
* **K's execution layer pays for itself in every regime and at every capital** — the ordering
  K > A1u > H2 ≥ R2 holds in all 27 cells, so the layer never "stops paying for itself" in this
  grid. Its *size* is regime- and capital-dependent: K − R2 = **+0.29 pp** pre-tax, **+0.41 pp**
  flat, **+0.49/+0.59/+0.68 pp** progressive at $10k/$100k/$1M on the 2000 window (the layer's
  deferrals are worth more the higher the marginal rate on the base), and +0.18…+1.13 pp across the
  three windows.
* The A1u gain band is the larger half of K (A1u − R2 = +0.45 pp at $100k on the 2000 window) and
  H2 adds +0.14 pp on top of the plain band (K − A1u = +0.14 pp).

## 4. IB38/B — income splitting with one taxpayer (report-only)

`ib38_report.py` runs K at $50k and $33.33k with the engine and reads the engine's own rule out of
`fe_sim._assess`: the progressive tariff is applied to **the taxpayer's annual net TRY gain**. With
one taxpayer the accounts' annual bases therefore aggregate, and the household's base per year is
the sum — i.e. the control's own base, so the household outcome is exactly K at $100k and the Δ is
0 by construction. That is checked, not assumed: `mode = "pre_tax"` is scale-invariant to
0.0e+00 relative error (two $50k accounts are one $100k account halved), so the two halves' bases
really do sum to the whole.

| window | aggregate (one taxpayer, = K at $100k) | two $50k accounts, separate ladders | three $33.3k accounts, separate ladders |
|---|---|---|---|
| 2000-03 → 2026-09 | 10.7533 % (Δ +0.00 pp) | 11.1566 % (Δ **+0.40 pp**) | 11.4433 % (Δ **+0.69 pp**) |
| 2010-01 → 2026-09 | 15.2354 % (Δ +0.00 pp) | 15.8785 % (Δ **+0.64 pp**) | 16.2944 % (Δ **+1.06 pp**) |
| 2017-01 → 2026-09 | 23.9720 % (Δ +0.00 pp) | 24.7686 % (Δ **+0.80 pp**) | 25.2259 % (Δ **+1.25 pp**) |

The ladder's size, from the engine's per-year assessed TRY tax: the household pays
26.09M TRY as one taxpayer against 27.40M TRY as two (mean per-year ratio tax(100k)/2·tax(50k) =
1.095, 1.086 and 1.091 on the three windows) — about 9 % less tax, which is what becomes the
+0.40…+1.25 pp. **Caveat, and it is the whole point:** this gain is available only to accounts held
by *different* taxpayers. For one taxpayer, aggregating the bases is the engine's rule and the
Turkish declaration's, and the split is worth exactly 0 — the tax-unit-neutrality result. (Under
aggregation the household's paths are the control's own, so its drawdown and turnover are K's.)

## 5. IC54/A and IC54/B — closure as `NEEDS_DATA`

The two arms need the fill session's **first-30-minute** price (close for A, VWAP for B) on every
decision session of a 2000-2026 window. The kernel side exists and is tested
(`research/fast_engine_opts`: `fill_mode = 2` + a `fill` array, `check_fill_reference`); the series
does not exist and cannot be sourced. `ic54_closure.py --scan --fetch` puts three facts on the
record (`IC54_STDOUT.txt`):

* the repository holds no intraday series at all: **98** CSVs under `data/` carry no time-of-day
  column, and none of the **82** registered `batch_screen` extra series is intraday;
* Yahoo's chart API refuses the range itself — asking for a five-year 30-minute history returns
  `422 Unprocessable Entity: "30m data not available for startTime=1634552166 and endTime=1792232166.
  The requested range must be within the last 60 days."` — and the 60 days it will serve
  (2026-08-18 → 2026-10-16, 560 bars) are kept as evidence in
  `data/historical/closeout_v1_yahoo_qqq_30m.json` (URL, range and SHA-256 in `DATA_MANIFEST.json`);
* the Alpaca SIP bars API the LIVE path consumes has no offline access here (no credentials, no
  vendored bars).

A daily-data stand-in for a 30-minute print would be a different mechanism, so the arms stay
refused rather than approximated: **`NEEDS_DATA`, not a screening failure.**

## 6. Gates, registry, harness and reproducibility

* **Selection rule (fixed in advance):** screen the four arms, report every outcome and every
  failed gate from the records, declare nothing — any Stage 2 pass would have been reported with
  its deflation numbers for the orchestrator to judge. Nothing passed Stage 1.
* **Registry.** 539 → **547 lines**; **N = 312 → 316** distinct `(idea, arm, spec_hash)` — the
  deflation's multiple-testing count. `--verify-registry` **PASS** (547 lines, 316 arms, N = 316):
  no `code_hash`, `arm_missing_at_head`, `spec_drift` or `malformed_line` problem. The 12 `stale`
  arms it lists (IC34/A-C, IC41/A-C, IC45/A-C, IC46/A-C) are inherited from the base commit — the
  same list is stale at `fc5dcfa3` — because later commits added arms to those modules after their
  ledger lines; `stale` is information, not an error, and no module of this study is in it.
* **Harness.** `python -m research.fast_engine.parity --quick` **PASS** (17.2 s; `PARITY.json`
  restored afterwards, as the protocol asks) and `python -m research.batch_screen.run --harness`
  **139 checks, 0 failed** (67.7 s) — K's published anchors reproduced through the engine, which
  the batch's control cells then matched exactly (15.7649 / 15.1785 pre-tax, 46.67 / 46.79 % max DD,
  10.7533 / 15.2354 / 23.9720 % after tax at $100k).
* **Suites.** `research/batch_screen/tests/run.sh -q`: **150 passed, 1 failed** —
  `test_ledger_merge::test_the_committed_ledger_verifies` asserts `stale == []`, and the 12 arms
  above are stale at the base commit, so the failure is pre-existing and unrelated to this study
  (proved by re-computing the ledger's `stale` list against `fc5dcfa3`: the same 12 arms).
  `research/fast_engine_opts/tests/run.sh -q`: **36 passed**. `smoke --ideas IB35_K,IB24_2L`: OK,
  4 arms, 0 % NaN on the main, `M2`, `XLK` and a bootstrap chunk. `run_batch --check`: PASS.
* **Reproducibility.** Run 1 (6 workers) 39.5 s, run 2 23.3 s (warm numba cache); `RESULTS.json`
  and `SCOREBOARD.md` are **byte-identical** across the two runs (SHA-256s in `SUMMARY.txt`;
  `RUN_TIMING.json` holds both wall clocks and the per-arm times). The batch is offline; the only
  network call is IC54's closure evidence.
* **Honest boundary.** No arm was approximated: IB35/C's alternative reading and IB24's alternative
  weight reading are documented in `PREREGISTRATION.md` and *not* screened; IB38/C keeps its
  `NEEDS_KERNEL` note (two FIFO books of one instrument is still outside every kernel); IC54/A-B
  are `NEEDS_DATA`. Nothing in `research/batch_screen/` was edited except the additive
  `EXTRA_DATA.json` entries for the new series (`--verify` PASS over all 82 series), and no
  existing plugin module was touched.
