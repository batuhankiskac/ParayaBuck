# ic37c_confirm_v1 — confirmation of IC37-C on an extended bond history (2026-10-16)

> **Evidence.** Branch `research/ic37c_confirm_v1` (worktree
> `/Users/batuhankiskac/Desktop/ParayaBuck-wt/ic37c_confirm_v1`), commits `c6d656d0` (§pre-reg +
> inputs), `88b27d20` (harness), `299a172e` (results + this report), plus the harness/report
> follow-up (§`git log`). Files:
> `research/ic37c_confirm_v1/` — `PREREGISTRATION.md` (committed **before** any non-control arm was
> simulated), `data.py` (FRED/ALFRED/Yahoo fetch + the two derived series), `proxy.py` (in-memory
> registration of the derived series into the framework's extra-data registry), `screen.py`,
> `DATA_MANIFEST.json`, `HARNESS.json` (30/30), `E1.json`, `RESULTS.json`
> (sha256 `b0c51d2e0ca2ca2ea50fb1eef3b3145cb09b9de8134938e00113dabcf15aa244`, reproduced
> byte-identically by a second and third run), `SUMMARY.txt`. Inputs: `data/historical/ic37c_confirm_v1_*.csv`.
> Arms: `research/ideas/IC37.py` (**committed, imported unchanged**) and `research/ideas/k_control.py`
> (control K). Engine: `research/fast_engine` (`parity --quick` **PASS**),
> `research/fast_engine2` (two-leg kernel), stages: `research/batch_screen/{stages,boot,run,api,
> extra,windows}.py`, **all unmodified** (`--harness` asserts it with `git status`).

**Verdict: FAIL — on exactly one gate cell, and only because one drawn path of 1,000 is
undefined.** IC37-C passes **every** pre-registered gate except **E4**, and E4 fails on a single
cell (`block10_progressive`, seed 20261018) where **1 of the 1,000 paths** leaves the two-leg
account's after-tax NAV ≤ 0 — an advance tax the account cannot fund. The campaign's convention is
that such a path fails *closed* (`framework_v2.md` §4: "a two-leg sleeve that realises a large
TRY-indexed gain can leave an advance tax the account cannot fund … `(NAV < 0) ** (1/years)` is
NaN, the arm's stage-1 after-tax gate fails"). With that one path treated as undefined, the same
cell is **+47.65 pp at 98.99 % of paths > 0**; the framework's own fresh gate (seed 20261016)
passes **4/4** for this arm. Under the pre-registered rule, however, the cell is a failure, so
**the study is a FAIL and no production change is recommended.**

Everything else is strongly positive on the extended window: pre-tax CAGR **17.81 / 17.21 %**
(base / calibrated) against K's 15.76 / 15.18 % (**Δ +2.04 / +2.03 pp**) with a **7.13 pp *lower*
max drawdown** (39.54 % vs 46.67 %); after tax at $100k **Δ +1.11 / +0.19 / +0.65 pp** on the
2000 / 2010 / 2017 windows; B1 **+25.05 pp** at 96.1 % share; the OOS windows (M1/M4/M5, the first
holdout this arm has ever had) median **+1.94 pp**, all three within tolerance; the 21 rolling
January starts median **+0.42 pp** at 85.7 %; the 21-ETF after-tax cell **+0.61 pp** at 85.7 %;
turnover 1.897 × K.

Because IC37-C was selected **after** seeing `batch_bank_v3` — **N = 303** distinct
`(idea, arm, spec_hash)` trials in the campaign ledger — even a full pass would only have been a
**candidate for owner decision**. A near-pass is not a discovery, and the deflated Sharpe there was
`dsr = 0.000`. This report's only sound content is the new evidence (§2–§6).

---

## 0. Why this study, and why the prior is low

`batch_bank_v3` screened 27 blind ideas (80 arms). IC37-C was its one Stage-1 survivor worth a
dedicated study: after tax at $100k +0.588 pp on its own 2003-01-03 → window (+0.176 / +0.643 on
2010 / 2017), B1 +12.23 pp at 87.8 %, B2 +14.51 pp at 90.7 %, max DD +0.81 pp, turnover 1.81 × K.
It was never *confirmed*: `holdout_insufficient` — the campaign's six OOS windows all begin before
IEF's first session (2002-07-30), so **0 of 6** OOS windows and **2 of 21** ETFs were evaluable, and
the arm's whole state (and its 0.25 % threshold) rested on one macro series (T10YIE, 2003-01-02 →)
over a 3-year-short common history.

What the study adds (pre-registered, `PREREGISTRATION.md`): a bond history that reaches back past
the arm's own instrument (E1), the **standard** main window rather than a 2003-onwards one (E2), an
evaluable OOS set (E3), a real-yield gate that exists before its breakeven series does (§3), and
fresh-seed bootstraps (E4). The plugin is **not** modified: the study extends the two *series* its
own keys read.

## 1. Method

**The arm.** `IC37/C` is `research/ideas/IC37.py` arm C exactly as committed: leg A = production's
target unchanged; leg B = **the residual `max(0, 0.98 − w)` in IEF while the E2 vote is off and
`DGS10 − T10YIE > +0.25`**, else zero; execution = production K on both books (one cash pool, two
FIFO lot books, one Turkish taxpayer). `resolve_arm(IC37, "C")` is asserted unchanged by the harness.

**Data substitution.** The plugin's spec still declares `leg_b = "ief"`, `inputs = ["dgs10",
"t10yie"]`, `start = "auto"`. `proxy.use("proxy")` adds two derived series to the *cached* registry
document of `research/batch_screen.extra` **in memory** (no file under `research/batch_screen/` is
written) and re-points exactly two keys:

| key | proxy mode (the study) | real mode (E5 / the reproduction check) |
|---|---|---|
| `ief` (leg B) | `IC37C_IEF_PROXY` — synthetic IEF total return, 1969-07-01 → | committed Yahoo IEF adjusted close, 2002-07-30 → |
| `t10yie` (the gate) | `IC37C_BE_PROXY` — T10YIE where published, else the point-in-time CPI 12-month change | committed FRED T10YIE |
| `dgs10` (the gate) | unchanged | unchanged |

Because the proxy series start in 1969, `start = "auto"` now resolves to the **published** main
window, so the arm is measured on 2000-03-09 → 2026-09-29 with K re-simulated on the same window
(paired Δ). All simulation, after-tax accounting and every bootstrap go through `fe_sim` / `fe_tax` /
`fe_boot` / `fe2_sim` / `fe2_boot` driven by `batch_screen.stages` / `boot` / `run.control_record`;
`screen.py` only builds arms and aggregates. No per-day or per-path Python loop was added.

**Δ convention.** Δ is `arm − K` in pp on the same resampled paths. Pre-tax at m = 2, base and
calibrated financing; after tax at $100k, progressive (B2: flat), liquidated.

## 2. E1 — the synthetic IEF proxy, and its validation

```
y75(t) = (5/6)·DGS7(t) + (1/6)·DGS10(t)        the 7.5-year point of the (DGS7, DGS10) line
r(t)   = −7.5·(y75(t) − y75(t−1))/100          modified-duration price return (D = 7.5 y)
         + y75(t)/100/252                       coupon accrual
         − 0.0015/252                            IEF's expense ratio
TR(t)  = 100·Π(1 + r)                           published on every calendar day (carried forward)
```

DGS7/DGS10 are FRED constant-maturity yields (`DGS7` begins 1969-07-01, so the proxy does).
The series is carried forward to every calendar day because the framework's leg-B rule needs a
price on **every** session the engine reads, including foreign OOS sessions that are US
bond-market holidays; on US sessions (the main window and every bootstrap chunk) no value is
carried.

| E1 statistic, real IEF 2002-07-30 → 2026-09-29 (6,080 sessions) | observed | threshold | pass |
|---|---|---|---|
| Pearson correlation of daily total returns | **0.9567** | ≥ 0.95 | ✔ |
| annualized tracking error `std(Δr)·√252` | **2.06 %** | ≤ 2.5 % | ✔ |
| \|CAGR difference\| over the overlap | **−1.13 pp** | ≤ 2.0 pp | ✔ |
| (report-only) daily-return beta | 0.9985 | — | — |

**E1 is valid.** The 12 sessions the fetched IEF file carries beyond the pre-registered end
(2026-09-30 → 2026-10-15) are reported separately and do not change the verdict (corr 0.95677 →
0.95675, TE 2.0606 → 2.0614 %, gap −1.1271 → −1.1315 pp).

Year by year the model tracks well (gaps mostly −0.1 to −1.6 pp/yr) with two exceptions:
**2003 (−5.37 pp)** — the repo's Yahoo IEF series returns +5.3 % in a year the 10-year yield rose
43 bp, which a 7-10-year Treasury fund should not do; and the level bias the curve's slope forces.
The 2022 bond crash — the arm's worst risk — is captured almost exactly (proxy −15.10 % vs real
−15.16 %, gate-year gap **+0.06 pp**). The bias is negative (the proxy *understates* bond returns),
so a positive result on the proxy is conservative.

**Strategy-level fidelity (E5).** On the only window where both exist (2002-08-01 → 2026-09-29) the
arm's after-tax CAGR is **14.34 % on the proxy vs 14.31 % on the real IEF (proxy − real = +0.03 pp)**
— i.e. the index-level bias almost vanishes once the sleeve is held part-time and measured as a Δ
against K.

## 3. The extended real-yield gate (E1b)

`BE(t) = T10YIE(t)` where FRED publishes it (2003-01-02 →), else `100·(CPI_pit(t)/CPI_pit(t−1 yr) −
1)` with `CPI_pit` the newest CPIAUCSL value **published** at `t`, from the **ALFRED vintage** current
at `t` (367 vintages, 1972-07-21 → 2002-12-17; the whole pre-2003 segment is point-in-time, which is
the IC12 lesson). Every session the study reads is after the ALFRED archive's start (earliest leg-B
read: 1974-12-31), so no revised-CPI fallback is used, and the join to T10YIE is exact at
2003-01-02 (1.64 / 1.62 both ways). The gate `DGS10 − BE > 0.25` is **on 75.9 %** of sessions since
1969-07-01 (75.6 % before T10YIE existed) — 100 % in 2000–2003 and 2008, 57.8 % in 2022, 1.2 % in
2020.

## 4. Result — extended main window, 2000-03-09 → 2026-09-29, $100k

| arm | pre-CAGR base / cal | Δ base / cal | max DD base / cal | ΔDD base | turn / K | B1 med / share | B2 med / share | OOS med | pooled G8 | AT Δ 2000 / 2010 / 2017 | E2 med / share | E1-ETF med / share |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **IC37/C** | **17.81 / 17.21 %** | **+2.04 / +2.03** | 39.54 / 39.77 % | **−7.13** | **1.897** | **+25.05 / 0.961** | **+28.20 / 0.973** | **+1.94** | +23.53 | **+1.11 / +0.19 / +0.65** | **+0.42 / 0.857** | +0.61 / 0.857 |
| IC37/A | 16.92 / 16.33 % | +1.15 / +1.15 | 40.32 / 40.54 % | −6.35 | 1.913 | +22.32 / 0.946 | +26.03 / 0.969 | +2.55 | +19.50 | +0.56 / **−0.06** / +0.35 | +0.01 / 0.619 | — |
| IC37/B | 16.77 / 16.19 % | +1.01 / +1.01 | 39.41 / 39.64 % | −7.26 | 1.764 | +36.87 / 0.990 | +41.37 / 0.992 | +2.21 | +49.83 | +0.52 / +0.13 / +0.54 | +0.13 / 0.714 | — |
| K (control) | 15.76 / 15.18 % | 0 | 46.67 / 46.79 % | 0 | 1.000 | 0 | 0 | 0 | 0 | 10.7533 / 15.2354 / 23.9720 % | 0 | 0 |

`IC37/A` and `IC37/B` are **reference rows only** — not gated, and they cannot pass or fail this
study. (A reproduces its bank_v3 weakness on 2010, −0.06 pp.) Δ's are in pp on the same paths.

## 5. Gates

Pre-registered rule (`PREREGISTRATION.md` §5): `pass = E1 ∧ (G1–G9) ∧ (AT ∧ AT-B1 ∧ AT-B2) ∧ E2 ∧ E3
∧ E4`.

| gate | cell | IC37/C |
|---|---|---|
| E1 | proxy validity (§2) | **PASS** (corr 0.9567, TE 2.06 %, gap −1.13 pp) |
| G1 / G6 | CAGR ≥ K or Calmar ≥ 1.1×K, m = 2 / m = 1, both models | PASS (all four) |
| G2 | max DD ≤ K + 1 pp, both models | PASS (−7.13 / −7.03 pp) |
| G3 | 2017-to-end CAGR ≥ K − 1 pp, both models | PASS |
| G4 | ≥ 2 of 3 pre-tax subperiods beat K | PASS (`P1`, `P3`) |
| G5 | B1 pre-tax median Δ ≥ 0, both models | PASS (+34.12 / +33.99 pp) |
| G7 = E3 | OOS median Δ ≥ 0, k = 3 applicable | PASS (M1 +4.03, M4 +1.88, M5 +1.94 pp; 3/3 within −0.5 pp CAGR, 3/3 within +3 pp DD, max DD Δ +0.75 pp) |
| G8 | pooled OOS bootstrap median Δ ≥ 0 on {M1, M4, M5} | PASS (+23.53 pp) |
| G9 | turnover ≤ 2 × K, both models | PASS (1.897) |
| AT | Δ > 0 in all three after-tax windows | PASS (+1.113 / +0.187 / +0.648 pp) |
| AT-B1 / AT-B2 | median Δ ≥ 0 and share > 0 ≥ 55 % | PASS (+25.05/0.961, +28.20/0.973) |
| E2 | 21 January starts 2001–2021: median Δ > 0 and share ≥ 60 % | PASS (+0.420 pp, 85.7 %) |
| E4 | seed 20261018, blocks 10/20/40 × prog/flat: median ≥ 0 and share ≥ 55 % in 6 cells | **FAIL** — 5 of 6 cells pass; `block10_progressive` is undefined (see §6) |

**Report-only** (not in the required set): the 21-ETF after-tax cell **+0.61 pp at 85.7 %** (PASS),
the framework's own fresh gate (seed 20261016, blocks 10/40) **4/4 PASS** (+47.31 pp on the same
block-10/progressive cell), turnaround 1.897 × K, 15.21 sells/yr, 1,985 band blocks, 2,068 H2
postponements.

### E4 in full

| cell | median Δ | share > 0 | NaN paths (arm) | median over finite Δ | pass |
|---|---|---|---|---|---|
| block10 flat | +53.72 | 0.995 | 0 | +53.72 | ✔ |
| **block10 progressive** | **NaN** | 0.989 | **1** | **+47.65** | **✘** |
| block20 flat | +28.10 | 0.975 | 0 | +28.10 | ✔ |
| block20 progressive | +23.87 | 0.958 | 0 | +23.87 | ✔ |
| block40 flat | +14.84 | 0.936 | 0 | +14.84 | ✔ |
| block40 progressive | +12.78 | 0.917 | 0 | +12.78 | ✔ |

## 6. Why E4 fails: one path, diagnosed

`boot.delta_stats` uses `np.median`, which **propagates NaN**: one undefined path makes
`median_delta_pp` NaN and the gate fails closed (the campaign's documented convention,
`framework_v2.md` §4). In the seed-20261018 / block-10 / progressive draw the arm's per-path CAGR is
NaN on exactly **path 646 of 1,000**; the control on that same path is finite (+4.51 %/yr).

Diagnosis of that path (all through the framework's own driver):

* its **pre-tax** CAGR is fine and extreme — **+51.6 %/yr** against the control's +7.95 %/yr;
* its **flat-mode** after-tax CAGR is **+40.7 %/yr**;
* only **progressive** mode is undefined.

So the path is a large-realised-TRY-gain path on which the progressive tariff's advance tax exceeds
the account's cash: `(NAV/capital)^(1/yrs)` with `NAV ≤ 0` is undefined. That is exactly the
mechanism `framework_v2.md` §4 names for a two-leg sleeve in progressive mode. It is a (rare, 0.1 %)
genuine failure mode of *this* arm — the bond sleeve's gains raise the advance tax — not a defect in
the study's code, and not proxy-specific. It is the one thing that fails.

**What this means.** IC37-C is **not** a pass under the pre-registered rule, and no production change
is recommended on this evidence. A future study could pre-register a NaN-aware cell (the median over
the defined paths, with the count disclosed) or a tax-funding rule; neither may be introduced here
after seeing the result.

## 7. E5 — report only

* **The IEF leg is held on 22.7 % of main-window sessions** (mean weight 0.216; 0.951 when held).
  By year: 2001 96 %, 2002 96 %, 2000 46 %, 2008 81 %, 2022 58 %, 2016 21 %, 2004–2006 20–34 %,
  2007 / 2012 / 2013 / 2017 / 2021 0 %.
* **Its contribution.** The arm−K Δ of log NAV totals **+27.16 pp** over the window, and it is
  dominated by the off-state bond bull: **2000 +8.9, 2001 +6.9, 2002 +5.5, 2008 +13.0 pp**; the
  drawdowns are 2009 −6.3, **2010 −8.2**, 2022 −3.6 pp. Per leg (progressive, $100k, whole window):
  leg A turnover 5.457 (≈ K's 5.456), leg B **4.902**; leg B realises 6.06 M TRY of indexed gain from
  resizes and 109 sells.
* **2022, the bond crash, in detail.** The gate is on 57.8 % of 2022 sessions and the leg is held at
  a mean weight 0.539 on 58.2 % of them; the arm still comes out only **−3.62 pp of log NAV** behind
  K for the year, because the sleeve's −15 % IEF year is smaller than the QLD* exposure K carried
  (the proxy's 2022 return matches real IEF to +0.06 pp).
* **Real IEF next to the proxy** (§2): +0.03 pp on the 2002-08 → window.

## 8. Method, determinism, cost

* `--harness`: **30/30** — `research.fast_engine.parity --quick` PASS (with `PARITY.json` restored to
  its committed bytes), K's anchors to ≤ 5e-05 pp (15.7649 / 15.1785 %, 46.6698 / 46.7948 %,
  10.7533 / 15.2354 / 23.9720 %), the R2 reference row (15.4766 / 14.8961 %, m = 1 15.7538 %,
  DD 46.7584 %; published 15.48 / 14.90 / 15.75 / 46.76) and the E2/C0 reference row
  (14.6749 / 14.1043 %, DD 49.5643 %; published 14.67 / 14.10 / 49.56 — all within 0.005 pp),
  IC37/C reproducing its `batch_bank_v3`
  record **cell for cell** in real mode (arm/control after tax 0.146754 / 0.140875, 0.154118 /
  0.152354, 0.246151 / 0.239720 on the w20030103 window, 5,972 sessions; B1 +12.2296, B2 +14.5108 pp),
  no framework drift, `resolve_arm` and the IC37 module bytes unchanged, and every data file's
  SHA-256 matching `DATA_MANIFEST.json`.
* **Determinism.** `--run` was executed twice from scratch at each code revision (control recomputed
  both times); the two `RESULTS.json` are **byte-identical**, and the hash is unchanged by the
  final import-only cleanup (sha256 `b0c51d2e…`).
* **Wall clock:** **97.4 – 123.5 s** across six clean `--run` executions (each with the control
  recomputed from scratch; `SUMMARY.txt` records the wall clock of the run that wrote it: 117.7 s).
  Wall clock of `--harness`: 24 s.

## 9. Caveats

1. **Selection.** IC37-C comes out of N = 303 ledger trials and its bank_v3 deflated Sharpe was
   0.000. The numbers above are *new evidence*, not a discovery, and the near-miss on E4 is not
   promotion-worthy.
2. **The E4 failure is a single-path, convention-driven failure** (§6). Anyone reading "FAIL" as
   "the arm loses to K" is misreading it: five of six E4 cells and every other gate pass, and the
   one failing cell is +47.65 pp over its 999 defined paths.
3. **The proxy is a duration model.** Correlation 0.957, tracking error 2.06 %/yr, a −1.13 pp/yr
   level bias (conservative), and a 2003 outlier that is a property of the repo's Yahoo IEF series
   (+5.3 % in a year the 10-year yield rose 43 bp), not of the model. The pre-2003 gate rests on
   point-in-time CPI; CPI is a revised series but the vintages are the published ones.
4. **The extended window's edge is concentrated.** +21.3 of the +27.2 pp of Δ log NAV come from
   2000–2002, the one period in the sample where US Treasuries rallied hard while the trend vote was
   off. That is exactly the period the new data adds, and exactly why the edge did not show up
   before; it also means the arm is a *conditional* overlay whose value depends on bonds being a
   safe haven in the next off-state, which 2022 shows they are not.
5. **Turnover** is 1.897 × K — inside the gate but with only 5 % of headroom.
6. The proxy-mode arm cannot be compared cell-by-cell with bank_v3's IC37/C (a different window and
   pool); §8's real-mode reproduction is the only bank_v3 claim this study makes.

## 10. Amendments

**None** to any arm, parameter, gate, threshold, window, seed or dataset. The post-result edits were
to *reporting* only: the E4 cells now carry the NaN count and the finite-paths median (§6), and the
harness gained the `fast_reentry_v1` **E2/C0** reference row (§8) — a report-only anchor the common
protocol names but `PREREGISTRATION.md` §7.2 did not list. Neither alters a gate, and
`PREREGISTRATION.md` itself was not edited. `research/batch_screen/`, `research/fast_engine*/` and
`research/ideas/IC37.py` are unmodified (asserted by `--harness`), and `docs/research/INDEX.md` was
not edited.
