# closeout_v1 — pre-registration

**Batch:** `closeout_v1` (results in `research/batch_screen/results/closeout_v1/`).
**Study:** `research/closeout_v1/` (this file, `run_batch.py`, `make_bil_star.py`, `ib38_report.py`,
`ic54_closure.py`, `DATA_MANIFEST.json`, `SUMMARY.txt`).
**Base commit:** `fc5dcfa3` (`research/batch_bank_v4b`), branch `research/closeout_v1`.
**Date:** 2026-10-17. Written and committed **before** any non-control arm was simulated on real
data (only `smoke` — `targets()` shapes, no return/NAV/gate — and the target-level state machine
check on a synthetic vote series ran before this commit).

## 1. Question

The last arms of `idea_bank_v1` / `idea_bank_v2` that are refused in `REGISTRY.jsonl` as
`needs_kernel` fall into three classes: one that the kernel options built after their registration
now express (IB35/C), one whose defining mechanism *can* be expressed on the two-leg kernel
(IB24/A-C), and four that are accounting-only or data-gap arms (ib38/A, ib38/B, IC54/A, IC54/B).
Do the two expressible ideas beat production **K** on the campaign's own protocol — and what do the
four remaining arms actually say once they are computed/reported outside the ledger?

## 2. Arms (frozen by `docs/research/idea_bank_v1.json` / `idea_bank_v2.json`; nothing tuned)

| arm key | module | spec (frozen) | status |
|---|---|---|---|
| `IB35_K/C_K` | `research/ideas/IB35_K.py` | `ib35k_band3pp`: plain fixed 3 pp resize band (band_up = band_down = 0.03, no A1u, no H2); production target series | **screened** |
| `IB24_2L/A_2L` | `research/ideas/IB24_2L.py` | `K`, `leg_b = "bilstar"`; enter the sleeve after **5** consecutive vote-off sessions, exit on re-entry | **screened** |
| `IB24_2L/B_2L` | `research/ideas/IB24_2L.py` | same with **N = 10** | **screened** |
| `IB24_2L/C_2L` | `research/ideas/IB24_2L.py` | enter on the first off session, exit only after **5** consecutive on sessions | **screened** |
| `IB38/A` | `research/ideas/ib38.py` | regime switch matrix `{R2, A1u, H2, K} x {tau=0, flat-20 %, progressive-2026} x {$10k, $100k, $1M}` | **report-only, not a trial** (`ib38_report.py`) |
| `IB38/B` | `research/ideas/ib38.py` | two $50k / three $33.3k accounts each running K, one taxpayer | **report-only, not a trial** (`ib38_report.py`) |
| `IB38/C` | `research/ideas/ib38.py` | routed split (two books of the *same* instrument, 10 pp route cap) | stays `NEEDS_KERNEL` (framework_v2 §IB38 A/B/C: two FIFO queues for one symbol is still outside every kernel) |
| `IC54/A`, `IC54/B` | `research/ideas/IC54.py` | first-30-minute close / VWAP fill | closes as **`NEEDS_DATA`** (`ic54_closure.py`: the data does not exist and cannot be sourced) |

Four arms are screened in batch `closeout_v1`; the control is the framework's own `k_control/K`
(production K, re-simulated with every batch). Both new modules are **new files**: no existing
plugin module is touched (`research/ideas/IB35.py`, `IB24.py`, `ib38.py`, `IC54.py` are byte-for-
byte as committed, so every pinned `spec_hash` in `REGISTRY.jsonl` is unchanged and
`--verify-registry` has no `spec_drift` to report).

### 2.1 IB35/C — which "plain fixed 3 pp band"

`IB35_K.py` registers `ib35k_band3pp = register_rule(band=False, usd_rule=False, defer=False,
opts={"band_up": 0.03, "band_down": 0.03})`: a *plain* band at 3 pp, **no** A1u gain band and **no**
H2 December deferral, entries and exits to zero immediate (the kernel's own behaviour).

Why that reading and not "K with a 3 pp base width": the bank calls C "the plain fixed 3 pp band,
a width between the tested 2.5 and 5 pp (the only untested width in *that family*), as the
like-for-like control", and "that family" is `ewma_band_v1`'s B25/B50/B100 — plain output bands
whose definition is *"entries and exits always trade in full; while the position is held the
strategy rebalances to target only when |target − held| > B"*, with no A1u/H2 (they post-date that
study). The campaign's vocabulary is the same one (`batch_screen/rules.py`: "`C0`/`R2` (plain
0.5 pp band)"). The alternative reading of the frozen spec's `"rule": "K"` field would keep A1u —
which on a 3 pp base band still blocks every 3–8 pp downsize whose sale's USD FIFO gain fraction
exceeds 5 % — so it would *not* be a plain band. The registered arm is the arm's own text; the
alternative is on the record here and is not screened.

### 2.2 IB24 — which expression, and the exact weights

**Expression: the sleeve as a second instrument leg** (`leg_b = "bilstar"`, the registered BIL*
extra series), on the two-leg kernel `fe2_sim` (one cash pool, one units/FIFO book per leg, K
applied per leg on that leg's own book, one taxpayer).

Why not the arm-level conditional cash-rate option (the bank's plugin note's own suggestion): the
arm's mechanism **is** the cost of the sleeve's round trips — "enter the bill sleeve only once the
vote has been off for N consecutive sessions" exists to kill flips that pay a BIL round trip each
time (`tbill_sweep_v1`: S1 turns over 5.8x/yr of bill notional; its +0.06 pp pre-tax edge goes
negative in whipsaw years and it fails the block-20 bootstrap). A conditional *cash rate* makes the
switch free and untaxed, so it cannot price the very thing the three arms vary; as an instrument,
the sleeve pays the campaign's 4 bps x m per side and its gain is taxed as an instrument gain
(YI-UFE-indexed FIFO), exactly as `tbill_sweep_v1` and `r2_tbill_combo_v1` modelled BIL — the
closest tested study, whose S1 this idea attacks.

Fixed weight reading (documented once, not tuned):

```
S_A/B = the vote has been off for >= N consecutive sessions   (exit the moment the vote is on)
S_C   = the vote is off, or it has been on for < 5 consecutive sessions and an off session
        preceded this on-run (the account starts in production, never spuriously in bills)
w_A   = production K's own target series (R2's target: the vote plus the early half-weight path)
w_B   = max(0, 0.98 - w_A) * S          (the residual cash, in bills while S is on)
```

This is `r2_tbill_combo_v1`'s construction of the sleeve against R2 verbatim ("the arm's own
`active = E2 on OR early path active`; while E2 is off **and** the early path is active, the
remainder `0.98 - w` is idle and goes to BIL*; while fully off, BIL* is held at 0.98"), and it is
what the bank's plugin note describes ("cash-like and never rebalanced against the equity leg …
a cash modifier on the target series"): the equity leg is never touched, the sleeve is exactly the
money that would otherwise earn nothing, the total never exceeds the 0.98 cap (no loan is held, no
margin feature is used), and the only axis the arms vary is the hysteresis state `S`. The
alternative reading — a fixed 0.98 sleeve while `S`, production's target scaled by `(1 - S)`, the
early re-entry suppressed while the sleeve is on — is **not** screened: it deletes a production
feature inside an arm whose registered content is the sleeve's *timing*, and it is not what the
`needs_kernel` module says is missing (the *cash*'s rate).

A state change trades the sleeve to or from zero, which every rule fills immediately (entries and
exits to zero are not banded, A1u-gated or H2-deferred in `fe_sim`/`fe2_sim`), so the sleeve's
round trips are the published S1 round trips and the hysteresis is the only change.

### 2.3 IB24's data: the BIL* extra series

`data/historical/closeout_v1_bil_star.csv` (`make_bil_star.py --write`, declared in
`EXTRA_DATA.json` as the series `BILSTAR_DAILY`, key `bilstar`): `tbill_sweep_v1`'s BIL* =
`DTB3 - 0.1356 %/yr`, accrued **per calendar day** (`/365`) rather than per session (`/252`), because
one date-keyed series has to answer every window's own session calendar (QQQ, the six OOS indices,
the 21 ETFs, a bootstrap chunk's drawn source sessions). The annual yield is the same object:
`make_bil_star.py --check` reports 1.7938 %/yr (calendar) against 1.7885 %/yr (session) over the main
window, a 0.0053 pp difference. The series starts at TB3MS's first observation (1934-01) so the `M2`
window (^GSPC 1934–1956) is covered; the rate schedule is `fe_data.rate_on` — the same one every
window already reads into its own `rate` column. No download was needed (`DATA_MANIFEST.json`
records the derivation and the two committed inputs).

## 3. Method, gates, selection rule

Unchanged from the framework's pre-registered pipeline (`research/batch_screen/README.md` §3–§4):
simulation through `fe_sim`/`fe2_sim`, tax through `fe_tax`, bootstraps through `fe_boot`.
Stage 1 = after-tax 2000-window Δ > 0 **and** B1-recent median Δ > 0 **and** pre-tax max DD ≤ K + 2 pp
(both financings, m = 2) **and** turnover ≤ 2 × K. Stage 2 = G1–G9 vs K, after tax at $100k on the
three windows, B1/B2 median Δ ≥ 0 with share > 0 ≥ 55 %, the 21 ETFs, the 21 January rolling starts,
the six OOS windows, the pooled OOS bootstrap and the fresh-seed bootstraps; a declaration
additionally needs `rw_stepdown` ≤ 0.05 **and** deflated Sharpe ≥ 0.95 over the whole ledger N.
`IB24_2L`'s arms are applicable everywhere (the series covers every window: verified by `smoke`,
0 % NaN on the main, `M2`, the `XLK` window and a bootstrap chunk).

**Report-only arms (not registered, not gated).** `ib38_report.py` computes, with the engine's own
after-tax functions outside the ledger:

* **IB38/A** — `{R2→C0, A1u, H2, K}` x `{pre-tax (tau=0), flat-20 %, progressive-2026}` x
  `{$10k, $100k, $1M}` on the framework's three after-tax windows (`TAX_WINDOWS`), plus the
  published main-window pre-tax row as the tau = 0 anchor. *Reduced window set, pre-registered:*
  the bank's "six windows" cannot carry a tax regime — only the three `TAX_WINDOWS` have the
  TRY/YI-UFE calendar (the bank's own data note says it: "the TRY/YI-UFE calendar starts 2000, so no
  after-tax OOS exists"); the six OOS windows are USD-only and are therefore not run here.
* **IB38/B** — two $50k accounts and three $33.33k accounts each running K. The engine's own rule is
  that the progressive tariff is applied to the *taxpayer's* annual net TRY gain, so with **one**
  taxpayer the accounts' annual bases aggregate: the household's base per year is the sum, i.e. the
  control's own base, and the household outcome is exactly K at $100k (Δ = 0). This is checked, not
  assumed: `mode="pre_tax"` must be scale-invariant ($50k's NAV path is $100k's halved), so the two
  $50k accounts' bases sum to the $100k base by construction. The **separate-ladder** variant (the
  bank's "multiplies the progressive ladder" arithmetic, i.e. what two independent *taxpayers* would
  pay) is reported too: household CAGR = the per-account CAGR from the engine's own $50k and
  $33.33k runs, with the ladder's size read off the engine's per-year assessed tax
  (`tax_assessed_try_by_year`: `tax(100k year)` against `2 x tax(50k year)`), all outside the gates.

## 4. Harness and suites (run before the screen, recorded in `SUMMARY.txt`)

`python -m research.fast_engine.parity --quick` (must PASS), `python -m research.batch_screen.run
--harness` (K's anchors reproduced through the engine, `HARNESS.json`), `python -m
research.batch_screen.extra --verify` (the new series' SHA-256/range), `smoke --ideas
IB35_K,IB24_2L`, `bash research/batch_screen/tests/run.sh -q`, `bash
research/fast_engine_opts/tests/run.sh -q`, then `run_batch.py --check` (the plugins' own property
checks: the state machine against a synthetic vote series, the arms' routing, the BIL* leg's
turnover on the main window).

## 5. Selection rule (fixed in advance)

Screen the four arms; report every arm's stage-1/stage-2 outcome and the gates it failed **as the
record reports them** (`gates.failed` / `stage1_gates`). Declare nothing myself: any stage-2 pass is
reported with its deflation numbers for the orchestrator to judge. Every NaN in a record is
explained; a framework bug stops the study and is reported with a minimal repro. Determinism: every
number is rounded to 6 dp and containers sorted before serialising (the framework's guarantee);
`run_batch.py --verify-identical` re-runs and compares `RESULTS.json` byte for byte.

## 6. Amendments

*(bug fixes and any deviation from the above, added as they are found — nothing here changes an arm,
a parameter, a window or a gate after data is seen.)*

* **Pre-run (before any non-control arm was simulated): the BIL* series was extended back to
  1934-01.** The first draft started at DTB3's first session (1954-01-04); `smoke` then showed
  `bilstar` 87 % NaN on the `M2` window (^GSPC 1934–1956), which would have made `M2`
  `NotApplicable` and reduced G7 to five windows. The series now starts where the campaign's own
  rate schedule starts (TB3MS, 1934-01), which is the same schedule the `M2` panel itself reads
  into `rate`; `smoke` reports 0 % NaN on every window. No arm, parameter, window or gate changed.
* **Pre-run: the BIL* CSV is written with LF line endings.** `csv.writer` defaults to CRLF and the
  repository normalises that to LF in the committed blob, so the declared SHA-256 (computed on the
  working copy) would have stopped matching after a fresh checkout and `extra --verify` would have
  failed. Same values, same 33,968 rows; the declaration and `DATA_MANIFEST.json` were updated.
  Nothing had been simulated.
