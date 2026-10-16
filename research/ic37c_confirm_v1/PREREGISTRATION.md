# ic37c_confirm_v1 — pre-registration

**Study.** Confirmation of **IC37-C** (the `idea_bank_v2` off-state Treasury sleeve: while the E2
trend vote is *off*, hold the residual `0.98 − w` in IEF, gated on the 10-year **real** yield
`DGS10 − T10YIE > +0.25 %`) on an **extended bond history**. Branch `research/ic37c_confirm_v1`,
worktree `/Users/batuhankiskac/Desktop/ParayaBuck-wt/ic37c_confirm_v1`.

**Status of this file.** Committed *before* any non-control arm was simulated on real data. No arm,
parameter, gate, window, seed or threshold below is changed after seeing a result; any later change
goes in §9 Amendments with its date and reason.

---

## 0. Provenance and prior (read this first)

IC37-C is the **only Stage-1 survivor of `batch_bank_v3`** (`docs/research/batch_bank_v3.md`) and
was **selected after seeing that batch**. The selection is the multiple-testing problem:

* the campaign's trial ledger `research/batch_screen/REGISTRY.jsonl` holds **N = 303** distinct
  `(idea, arm, spec_hash)` trials at this branch point;
* for IC37-C the deflated Sharpe in `batch_bank_v3` was `dsr = 0.000` (advantage-Sharpe 1.087
  against `sr0 = 4.338`), and Romano–Wolf stepdown alone would have "rejected" it — the stepdown
  rejects an arm when its Δ vector is positive, which is the price of N = 303, not a confirmation;
* the reason it survived to a confirmation study at all is `holdout_insufficient`
  (`docs/research/batch_bank_v3.md` §`holdout_insufficient`): the campaign's six OOS windows all
  begin before IEF's first session (2002-07-30), so 0 of 6 OOS windows and 2 of 21 ETFs were
  evaluable. Its Stage-1 numbers were +0.588 pp after tax at $100k on the main window (+0.176 /
  +0.643 pp on 2010 / 2017), B1 +12.23 pp at 87.8 % share, B2 +14.51 pp at 90.7 %, DD +0.81 pp,
  turnover 1.81 × K, 18 rolling starts with a +0.433 pp median at 83 % ≥ 0.

**Consequences, fixed here.**

1. The prior on IC37-C is **low**. The bank_v3 numbers are *not* evidence and are never re-tested
   here; they enter §7 only as a reproduction check that the engine path is wired correctly.
2. Only **new evidence** counts: a bond history long enough to give the arm a real holdout
   (E1), the after-tax main-window result on the *standard* window rather than a 2003-onwards one
   (E2), an evaluable OOS set (E3) and fresh-seed bootstraps (E4).
3. A single-arm pass may not be declared a discovery. **Even on a full pass the study's verdict is
   only "candidate for owner decision"**, with the N = 303 selection stated in the verdict line of
   both `SUMMARY.txt` and `docs/research/ic37c_confirm_v1.md`.
4. The study changes **no** production file: `src/`, `config/`, `tests/`, `scripts/`,
   `benchmarks/`, `research/batch_screen/`, `research/fast_engine*/` and
   `research/ideas/IC37.py` are all untouched (verified in §7.4).

---

## 1. Arms (fixed, four; no others)

| arm | what it is | role |
|---|---|---|
| `k_control/K` | production **K** (`research/ideas/k_control.py`): E2 + early re-entry, vol-target, A1u band, H2 December deferral | **control C0** |
| `IC37/C` | the committed `research/ideas/IC37.py` arm C, **imported unchanged** (`form="residual"`, `total=0.98`, `gate="real10y"`, `real_min_pp=0.25`, `leg_b="ief"`, `inputs=["dgs10","t10yie"]`, `start="auto"`, rule K) | **the arm under test** |
| `IC37/A` | the committed arm A (`form="replace"`, `weight=0.98`, `real_min_pp=0.25`) | reference row only |
| `IC37/B` | the committed arm B (`real_min_pp=1.00`) | reference row only |

`IC37/A` and `IC37/B` are **not gated** and cannot fail or pass the study; they are reported next to
C for context (`batch_bank_v3` rejected both, and this study does not revisit that).

**"Imported unchanged" is literal, and the data is what moves.** The plugin's code and spec are the
committed ones. This study extends the two *series* the plugin already reads, by registering them
into the framework's extra-data registry **in memory** (no framework file is edited; see §4.4):

| the plugin reads | `proxy` mode (the study, default) | `real` mode (E5 comparison, the bank_v3 data) |
|---|---|---|
| `data.extra("ief")` (leg B) | `IC37C_IEF_PROXY`, the synthetic IEF total-return index (§2) | the committed Yahoo IEF adjusted close |
| `data.extra("t10yie")` (inside the gate) | `IC37C_BE_PROXY`, the breakeven proxy (§3) | the committed FRED T10YIE |
| `data.extra("dgs10")` (inside the gate) | unchanged: FRED DGS10 | unchanged |

Because the proxy series start before the main window, `start="auto"` resolves to the **published**
main window for IC37/C (session 253, 2000-03-09) rather than to the 2003-01-03 the breakeven used to
force.

---

## 2. E1 — the synthetic IEF total-return proxy (formula fixed here)

Built from FRED constant-maturity yields (`data/historical/ic37c_confirm_v1_dgs7.csv`,
`..._dgs10.csv`, fetched by `research.ic37c_confirm_v1.data --fetch`). On the sessions DGS7 and
DGS10 share (`DGS7` starts 1969-07-01, so the proxy starts there):

```
y75(t) = (5/6) * DGS7(t) + (1/6) * DGS10(t)        the 7.5-year point of the (DGS7, DGS10) line
r(t)   = -7.5 * (y75(t) - y75(t-1)) / 100          price return, modified duration D = 7.5
         + y75(t) / 100 / 252                      coupon accrual (carry)
         - 0.0015 / 252                            IEF's 0.15 %/yr expense ratio
TR(t)  = 100 * prod_{s<=t} (1 + r(s))              the proxy series the engine holds
```

D = 7.5 y is IEF's average modified duration and 0.15 % its expense ratio; the 7.5-year point is the
linear interpolation of the two constant-maturity yields the task names. The series is then
**published on every calendar day**, carrying the last value forward, because the framework's
leg-B rule requires a price on every session the engine reads — including the foreign OOS markets'
sessions that are US bond-market holidays. On US sessions (the main window and every bootstrap
chunk, which draws from the main window's own US calendar) no value is ever carried.

**Validation, on real IEF, 2002-07-30 → 2026-09-29 (the ETF's whole life), pre-registered
thresholds — all three must hold for E1 to be valid:**

| statistic (daily total returns, common dates) | threshold | rationale |
|---|---|---|
| Pearson correlation | **≥ 0.95** | a duration model of a 7-10y Treasury fund must track its price nearly one-for-one |
| annualized tracking error (`std(Δr) · √252`) | **≤ 2.5 %/yr** | the residual is convexity, curve shape, roll-down and IEF's own duration drift |
| \|CAGR difference\| over the overlap | **≤ 2.0 pp/yr** | the level bias a duration approximation inherits from the curve's slope |

A yearly breakdown and the 2022 bond crash are reported alongside; they are **information, not
gates**. If any threshold fails, E1 is invalid: the study **reports and stops** — E2 and E4 are not
run on the proxy and no candidate can be declared (§5). (E3/E5 may still be reported as invalid.)

---

## 3. E1b — the real-yield gate input (formula fixed here)

The gate is the plugin's own: `DGS10 − t10yie > 0.25`. `t10yie` is re-pointed to

```
BE(t) = T10YIE(t)                                      where FRED publishes it (2003-01-02 ->)
      = 100 * (CPI_pit(t) / CPI_pit(t − 1 year) − 1)   before that
```

where `CPI_pit(x)` is the newest CPIAUCSL value **published** on or before `x`, taken from the
**ALFRED vintage** current at `x` (`data/historical/ic37c_confirm_v1_cpi_vintages.csv`: one row per
vintage, `vintage_date,obs_date,value`, ALFRED archive 1972-07-21 → 2002-12-31). This is a strict
point-in-time construction: no revision published later can enter, which is the IC12 lesson
(`docs/research/ic12_confirm_v1.md` — a revised macro series turned +0.71 pp into −0.56 pp).

The one-month publication lag the task names is *inside* the construction: on a session in month
`m` the newest published CPI is month `m−1`'s, so `CPI(t)/CPI(t−1 yr)` is the 12-month change
through `m−1`. Every session the study reads (earliest: 1974-12-31, M1's leg-B read) is after the
ALFRED archive's start, so **no revised-CPI fallback is needed** and none is used. The join with
T10YIE happens at T10YIE's first published date, 2003-01-02.

`BE` is published on every calendar day like the proxy. The gate series itself is report-only (a
real-yield plot and a gate-on share are in E5).

---

## 4. Mechanics (fixed)

### 4.1 Windows
* **E2 / L2:** the **standard main window**, 2000-03-09 → 2026-09-29 (session 253 → end), base and
  calibrated financing, m = 2. K is the control on the same window, so every Δ is paired.
* **after tax** at **$100k**, progressive, liquidated: the three published windows
  (`2000_03_to_2026_09`, `2010_01_to_end`, `2017_01_to_end` — all three fully inside the window, no
  clipping) and the **21 January rolling starts 2001–2021**.
* **OOS (E3):** the framework's six windows, evaluated on the applicable set. An OOS window is
  applicable iff the proxy reaches the window's measured start − 1 session. Measured starts:
  `GSPC_1957_1999` 1957-01-02, `M1` 1975-01-02, `M2` 1935-01-08, `M3` 1966-01-19, `M4` 1989-01-03,
  `M5` 1985-01-02. With the proxy starting 1969-07-01 the applicable set is **{M1, M4, M5}**
  (**k = 3**, the pre-registered minimum); `GSPC_1957_1999`, `M2` and `M3` are `NotApplicable`.
  The pooled OOS bootstrap (G8) runs on the same three markets, with the control re-drawn inside the
  same draw so the Δ stays paired.

### 4.2 Engine paths (unchanged from the campaign)
Pre-tax m = 1/2/4 in both financing models and the R-USD row, `fe_study`'s pre-tax block and
subperiods; the three after-tax windows; rolling January starts; the six OOS windows; the 21
quality-passing ETFs (report-only, §5); the pooled OOS bootstrap. No new per-day or per-path Python
loop: simulation, after-tax accounting and every bootstrap go through `research.fast_engine`
(`fe_sim` / `fe_tax` / `fe_boot`) and `research/fast_engine2` (the two-leg kernel
`fe2_sim`/`fe2_boot`), driven by `research/batch_screen.stages` and `research/batch_screen.boot`,
both **unmodified**.

### 4.3 Bootstraps
* **B1-recent / B2** (stage-1 gate and the protocol's after-tax gate): seed **20261011**, block
  **20**, 5,000 pre-tax / 1,000 after-tax paths — the campaign's published draw
  (`fe_config.SEED`/`BLOCK`). The proxy arm and K share the draw, so every Δ is paired.
* **E4 (fresh)**: seed **20261018**, blocks **10 / 20 / 40**, 1,000 after-tax paths each, $100k,
  progressive and flat — six cells on a seed no earlier trial used.
* The framework's own fresh stage (seed 20261016, blocks 10/40) is reported as `FRESH` for
  continuity but is **not** in the required gate set.

### 4.4 Registration of the extended series (no framework edit)
`research.ic37c_confirm_v1.proxy.use("proxy")` mutates the *cached* registry document of
`research.batch_screen.extra` in place: it adds the two derived series and re-points the keys `ief`
and `t10yie`. No file under `research/batch_screen/` is written; `use("real")` restores the
committed mapping. `data.extra`, `leg_b_usable`, `leg_series` and `resolve_arm` then see the proxy
with no plugin change.

---

## 5. Gates (fixed) — pass rule

Candidate = **`IC37/C`** (required). A **pass** requires **every** cell below. All Δ are
`IC37/C − K` in pp on the same paths; all after-tax cells at $100k, progressive (B2: flat), liquidated.

| # | gate | rule |
|---|---|---|
| **E1** | proxy validity | §2: corr ≥ 0.95, TE ≤ 2.5 %/yr, \|ΔCAGR\| ≤ 2.0 pp/yr (else the study stops) |
| **G1–G3, G6** | pre-tax | as `ewma_band_v1` defines them: G1/G6 = CAGR ≥ K or Calmar ≥ 1.1×K with CAGR ≥ K − 0.5 pp (m = 2, m = 1), both financing models; G2 = max DD ≤ K + 1 pp; G3 = 2017-to-end CAGR ≥ K − 1 pp |
| **G4** | subperiods | ≥ 2 of the three subperiods beat K |
| **G5** | pre-tax bootstrap | B1 pre-tax median Δ ≥ 0, base and calibrated |
| **G7** | OOS | on the applicable set (k = 3): median Δ CAGR ≥ 0, ≥ 2 of 3 within −0.5 pp CAGR, ≥ 2 of 3 within +3 pp DD, max DD Δ ≤ +8 pp |
| **G8** | pooled OOS bootstrap | median Δ ≥ 0 on {M1, M4, M5} |
| **G9** | turnover | ≤ 2 × K, base and calibrated (m = 2) |
| **AT** | after tax, 3 windows | Δ > 0 in **all three** windows |
| **AT-B1 / AT-B2** | after-tax bootstrap | median Δ ≥ 0 **and** share > 0 ≥ 55 % |
| **E2** | rolling starts | 21 January starts 2001–2021: median Δ **> 0** and share Δ > 0 ≥ **60 %** |
| **E3** | OOS pre-tax | = G7 above on k = 3 (k ≥ 3 required; a smaller set makes E3 not evaluable) |
| **E4** | fresh bootstraps | seed 20261018, blocks 10/20/40 × progressive/flat: median Δ ≥ 0 **and** share > 0 ≥ 55 % in **all six** cells |

**Report-only, not required:** the framework's 21-ETF after-tax cell (`E1_etf`), its `FRESH` cell
(seed 20261016), the `R2` reference row, and everything in E5.

**Selection rule.** `pass = E1 ∧ (G1–G9) ∧ (AT ∧ AT-B1 ∧ AT-B2) ∧ E2 ∧ E3 ∧ E4`. A pass is
recorded as **"candidate for owner decision"**, never as a discovery (§0.3); a failure is recorded
as a failure with the failed cells listed. If E1 fails, the verdict is `E1_invalid` and E2/E4 are
not run.

---

## 6. E5 — report only

No gate. Reported: how often the IEF leg is held (share of sessions with a positive leg-B target,
and its mean weight, by calendar year); the arm's per-leg trade/turnover/realised-gain block
(`_legs`); the arm−K Δ per calendar year over the main window, with **2022 (the bond crash) called
out** in detail; and the **real-IEF-only window** (2002-08-01 → 2026-09-29) run in `real` mode next
to the proxy mode on the same window, so the proxy's level bias is visible in strategy terms.

---

## 7. Harness (must be run and PASS before `--run`)

1. `python -m research.fast_engine.parity --quick` → PASS.
2. **K's anchors** through the engine, ≤ 0.01 pp: pre-tax m = 2 CAGR 15.7649 / 15.1785 % (base /
   calibrated), max DD 46.6698 / 46.7948 %, after tax at $100k 10.7533 / 15.2354 / 23.9720 % on the
   2000 / 2010 / 2017 windows. The **R2** reference row (15.48 % base, 46.76 % DD) is reproduced too.
3. **IC37/C reproduces its `batch_bank_v3` record** in `real` mode (the committed data, window
   2003-01-03 → 2026-09-29): after-tax 2000/2010/2017 Δ +0.58787 / +0.176424 / +0.643077 pp,
   B1 median +12.229612 pp, B2 +14.510757 pp, window start 2003-01-03, 5,972 sessions. This is the
   only thing the study claims about the bank_v3 numbers.
4. **No framework drift**: the working tree's `research/batch_screen/`, `research/fast_engine/`,
   `research/fast_engine2/`, `research/fast_engine_opts/` and `research/ideas/IC37.py` are compared
   to `HEAD` (git status must be clean for those paths).
5. **Registry integrity**: `proxy.use("proxy")` leaves `leg_b_keys()` (still contains `ief`) and
   `resolve_arm(IC37, "C")` unchanged, and every registered key resolves to a file whose SHA-256
   matches `DATA_MANIFEST.json`.

---

## 8. Outputs

`PREREGISTRATION.md` (this file), `screen.py`, `data.py`, `proxy.py`, `DATA_MANIFEST.json`,
`HARNESS.json`, `E1.json`, `RESULTS.json`, `SUMMARY.txt`, and `docs/research/ic37c_confirm_v1.md`.
`RESULTS.json` is deterministic (every float rounded to 6 dp, every container sorted) and is written
twice with a byte-identity check. `docs/research/INDEX.md` is **not** edited.

## 9. Amendments

*(none — this study changed no arm, parameter, gate, threshold, window, seed or dataset after
seeing a result.)*
