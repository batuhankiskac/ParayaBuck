# two_leg_arms_v1 — pre-registration (committed before any arm of this batch is screened)

**Question.** Three `idea_bank_v1` ideas were registered `needs_kernel` because each needed a second
instrument inside the account: IB25 (off-state SHY sleeve), IB26 (curve-conditioned off-state IEF
leg) and IB27 (beta-matched QQQ + QLD blend). `research/fast_engine2` (task `two_leg_kernel_v1`,
`docs/research/two_leg_kernel_v1.md`) now provides exactly that kernel: one cash pool, a FIFO lot
book per leg, one taxpayer, per-leg K execution. This study screens those three ideas on it: the
arms they ask for, unchanged, with no new parameter and no sweep.

**Deliverable.** For each of the 8 arms: the framework's stage-1 record, its stage-2 holdout record
when it survives stage 1, the deflation pass over the batch's Δ vectors, and the verdict.

## 1. Control, reference, batch

* `C0 = k_control/K` — production K (`equity_leveraged_trend_v1` 1.2.0): the production E2 +
  early-re-entry target series on the synthetic 2x QLD*, rule `K`, `cash_yield = "none"`, the
  framework's own control (`research/batch_screen/PREREGISTRATION.md` §1). Every Δ is `arm − K` on
  the same resampled paths.
* `R2 = r2_control/R2` — the same target series under `RULE["C0"]`, the campaign's reference row.
* The framework's `--harness` (73 checks, including `research.fast_engine.parity --quick` and the
  two-leg kernel's parity groups) must PASS before the batch, and the control must reproduce K's
  published cells (pre-tax 15.7649 % base / 15.1785 % calibrated, max DD 46.6698 / 46.7948 %,
  after tax at $100k 10.7533 / 15.2354 / 23.9720 % on the 2000 / 2010 / 2017 windows).
* Batch `two_leg_v1`. Ideas: `ib25_2L`, `IB26_2L`, `ib27_2L` — **new modules**, because the bank's
  own modules (`research/ideas/ib25.py`, `IB26.py`, `ib27.py`) declare `NEEDS_KERNEL` at module
  level, which `api.resolve_arm` applies to *every* arm of the module, and because adding a
  `needs_kernel` key to their existing arm specs would change their `spec_hash` and make
  `--verify-registry` report `spec_drift` (the runner then refuses the batch). The old arms and
  their ledger lines are untouched; the new arms carry the suffix `_2L`.

## 2. Arms (every parameter fixed; the bank's spec plus `leg_b`)

Specs are the bank's own, with `leg_b` added (the instrument the registry pins). The readings are
fixed here; each `targets` documents them again.

| arm | spec (bank's frozen parameters) | leg_b | reading |
|---|---|---|---|
| `ib25_2L/A_2L` | `sleeve=SHY, weight=0.98, key=shy` | `shy` | sleeve target `0.98` on every session the production path is off (`on_eff == False`), else `0`; leg A is production's own target, which is `0` while off |
| `ib25_2L/B_2L` | `sleeve=SHY, residual=True, total=0.98, resize_band=0.10, key=shy` | `shy` | S2_10's shape, see §3.2 |
| `IB26_2L/A_2L` | `off_leg=IEF, ief_weight=0.98, slope_min=0.010, slope_series=T10Y3M` | `ief` | while off, sleeve target `0.98` iff `T10Y3M/100 > 0.010`, else `0` |
| `IB26_2L/B_2L` | as A + `dgs10_change_min=-0.005, dgs10_lookback=60` | `ief` | as A, and additionally `DGS10/100` must not have fallen by more than `0.005` over the last 60 sessions (condition false before the lookback is full) |
| `IB26_2L/C_2L` | as A + `mirror=True` | `ief` | as A but `T10Y3M/100 <= 0.010` |
| `ib27_2L/A_2L` | `blend=on_state_only, f=1.0` | `1x` | §3.3, blend applied where the path factor is `1`; the early leg keeps production |
| `ib27_2L/B_2L` | `blend=everywhere, f=0.5` | `1x` | §3.3 applied with the session's own path factor (so the early leg becomes unlevered QQQ) |
| `ib27_2L/C_2L` | `blend=vt_ge_0.60, vt_min=0.60` | `1x` | §3.3 applied where `vt >= 0.60`, production otherwise |

`f` in the bank's IB27 spec names the arm's path factor (1.0 for A, 0.5 for B's early leg); the
implementation reads the factor per session from `fe_signals.r2_state`, so it is documentation of
the arm rather than a constant. `slope_series` appears in the bank's arm A only; arm B/C use the
same series (`T10Y3M`).

## 3. Mechanics (fixed before the run)

**3.1 Kernel and rule.** All arms run `rule = "K"`, `leg = "2x"`, `cash_yield = "none"`, through
`research/fast_engine2.fe2_sim.simulate2` (two legs, one cash pool, per-leg FIFO books, one
taxpayer) — the framework's normal path for a `(wants_a, wants_b, leg_b)` plugin. K's 0.5 pp band,
A1u gain-scaled downsize band and H2 December deferral apply **per leg on that leg's own book**;
idle cash earns nothing; next-open fills; decide at the close. No engine or framework file is
edited by this study.

**3.2 IB25-B = tbill_sweep S2_10's shape, transcribed from its code**
(`archive/research/tbill-sweep-v1-2026-10:research/tbill_sweep_v1/screen.py`): with
`hi = total − w*` (`w*` = production's leg-A weight), S2_10 keeps the sleeve in `[hi − X, hi]` and
resets it to `max(0, hi − X/2)` on a breach, `X = resize_band = 0.10`. S2_10's decision is taken on
the *held* sleeve weight, which a target-weight plugin cannot see; it is therefore taken on the
accepted target: hold the accepted target while it lies inside `[hi − X, hi]`, else reset to
`max(0, hi − X/2)`; while off, the target is `total`. This reproduces S2_10's resizes (`hi` moving
by more than `X` triggers one) and never lets the sleeve exceed `hi` — so `wants_a + wants_b <=
0.98`, the bank's "total target is never above 0.98" (checked in
`research/two_leg_arms_v1/check_plugins.py`). Same plugin-side-band convention as `ib32`'s C arm,
where the framework's fill band is fixed. No new parameter: `X` is the spec's `resize_band`.

**3.3 IB27 = the bank's formula, plus one pre-registered long-only tie-break.**
`beta = 2 × min(0.98, f × vt)`, `cash = min(0.98, beta)`, `w_QLD = beta − cash` (leg A),
`w_QQQ = cash − w_QLD` (leg B), with `vt = min(1, 0.35/vol20)` on leg A's own vol (`data.vol`) —
the production sizing input. The bank's `beta = 2·f·vt` and this agree while `f·vt <= 0.98`; beyond
it the bank's literal weights need `w_QQQ = 1.96 − beta < 0`, a QQQ **short** (at most −0.04),
which the kernel's long-only single-cash-pool account does not model. The tie-break: cap the
delivered beta at production's own cap, `min(0.98, f·vt)` (max long-only beta at the 0.98 cap is
1.96), where the blend degenerates to production's `0.98` QLD. Fixed here, before the run, as the
only long-only reading of "same Nasdaq beta at a lower carry". The bank's parenthetical
("w_QQQ = 0 and w_QLD = C when C = B") contradicts its own formula (`w_QLD = B − C`); the formula
governs, and at `C = B` it gives the all-QQQ, minimum-carry leg the mechanism prose argues for.
Both legs are real kernel legs, so the after-tax cells are exact for the construction (the bank's
own pre-tax sketch — pricing the blend as one synthetic fund — is not used). The `1x` leg carries
the campaign's synthetic convention: no fund expense, no financing (the bank's "QQQ ≈ 20 bp/yr" is
therefore not charged; a disclosed, small bias in the arm's favour).

**3.4 Availability, pre-registered.** SHY and IEF (Yahoo adjusted close) start 2002-07-30, so the
five IB25/IB26 arms are expected `NotApplicable` on the framework's fixed main window
(2000-03-09 →): `MarketData.leg_b_usable` refuses a leg-B instrument that does not reach every
session the engine reads, and stage 1 requires the main window (and therefore the stage-1
bootstrap) to be applicable. The bank's own oracle for these two ideas is a *reduced* main window
(IB25: 2002-08; IB26: 2003-07) and it pre-registers a reduced gate for a window that is not
evaluable (`evaluation_contract.reduced_gate_convention`); the framework has no per-arm window
start, so those arms will be recorded `not_applicable` with the reason and **no claim is made about
them**. The reduced-window screen (main window from the leg's own start, with a bootstrap whose
pool is the same reduced range) is left as a follow-up: it needs a framework change this study is
not allowed to make. `ib27_2L/*` needs no extra data and is applicable everywhere.

**3.5 Reported framework bug (not patched).** `MarketData.leg_b_usable` builds its failure reason
with `self.days[finite[-1]]`, but in a bootstrap chunk the series is one column wider than the
chunk's `days` (the documented base-column convention), so the index can overflow: the framework's
own probe `probes/two_leg.py::gld_sleeve` (leg B = an extra key with an interior `NaN`) raises
`IndexError: index 2776 is out of bounds for axis 0 with size 2776` on the chunk `smoke` builds,
instead of returning the intended `NotApplicable`. It affects every two-leg arm whose leg B is an
extra series with a `NaN` inside the chunk's read window; it does *not* affect this batch's
screenable arm (`leg_b = "1x"`, all-finite prices, checked: `leg_b_usable` returns `True` on the
chunk), and the IB25/IB26 arms are refused on the main window before any chunk is built. The
framework is left unedited; `research/two_leg_arms_v1/check_plugins.py` performs the smoke's
plugin-side checks plus the weight invariants.

## 4. Gates (the framework's, unchanged)

Stage 1: after-tax $100k Δ(2000 window) > 0 **and** B1-recent median Δ > 0 **and** pre-tax max DD
≤ K + 2 pp **and** turnover ≤ 2 × K (both financing models, m = 2). Stage 2: G1–G9 vs K, the
three after-tax windows, B1/B2 (median Δ ≥ 0, share > 0 ≥ 55 %), E1 (21 ETFs, median > 0,
share ≥ 60 %), E2 (21 rolling January starts, median ≥ 0, share ≥ 60 %), FRESH (blocks 10/40,
median ≥ 0, share ≥ 55 %) and G8 (pooled OOS). Deflation: `rw_stepdown` p ≤ 0.05 and
`deflated_sharpe` ≥ 0.95 over every registered trial. Exact definitions, seeds, sample sizes and
the deflation method: `research/batch_screen/PREREGISTRATION.md` §§ 3–4 and `README.md` §4. Nothing
here changes them, and no gate is changed after seeing data.

## 5. Selection and reporting

A candidate must pass stage 2 **and** the deflation. The control and `R2` are references, never
candidates. The ledger (`REGISTRY.jsonl`) is append-only: the 8 new arms add their lines, and N
grows by the new distinct `(idea, arm, spec_hash)` trials (reported before and after). `RESULTS.json`
must be byte-identical on a re-run. `docs/research/two_leg_arms_v1.md` carries the verdict first,
the per-arm stage-1/stage-2 table and the deflation.

**Expected before the run (no claim).** `ib25_2L/*` and `IB26_2L/*`: `not_applicable` on the main
window; `ib27_2L/*`: stage 1 decides.

## 6. Amendments

* None. Any change to a parameter, a reading or a gate after this commit is appended here, dated,
  with its reason.
