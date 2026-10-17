# batch_bank_v4b — pre-registration

**Batch:** `bank_v4b` (results in `research/batch_screen/results/bank_v4b/`).
**Study:** `research/batch_bank_v4b/` (this file, `run_batch.py`, `SUMMARY.txt`).
**Base commit:** `851bcbae` (`research/batch_run_v1`), branch `research/batch_bank_v4b`.
**Date:** 2026-10-17. Written and committed **before** any non-control arm was simulated.

## 1. Question

Nine arms of the idea bank were refused in `bank_v3` as `NEEDS_KERNEL` because they needed more than
two instruments in one account, a cash loan, or the S&P 500 2x leg beside QLD*. `three_leg_kernel_v1`
(the N-leg kernel `research/fast_engine3`, `"legs": [...]` + `margin_spread` + `2x_spy`) makes the
*account* expressible. Do any of the nine beat production **K** on the campaign's own protocol once
they can actually run?

## 2. Arms (all parameters frozen by `docs/research/idea_bank_v2.json`; nothing tuned)

| arm key | module | spec (frozen) | status |
|---|---|---|---|
| `IC34/C_N` | `research/ideas/IC34.py` | `legs ["2x","sec_xlu","sec_xlp"]`, `start auto`; while E2 is off leg A is 0 and the account holds 0.49 XLU + 0.49 XLP (the bank's fixed equal split, no selection); production target otherwise | screened |
| `IC36_N/A_N` | `research/ideas/IC36_N.py` | `legs ["2x","gld","ief","shy"]`, `start auto`; while E2 is off, 0.98 × inverse-vol shares of {GLD, IEF, SHY} (`1/vol20`, rescaled on each month's first session) | screened |
| `IC36_N/B_N` | `research/ideas/IC36_N.py` | the same over {GLD, IEF, SHY, XLU} | refused: 5 instruments > `N_LEGS` = 4 |
| `IC36_N/C_N` | `research/ideas/IC36_N.py` | as A_N but the blend's total is production's own `vt` (`0.98 → vt`) | screened |
| `IC41/A_N` | `research/ideas/IC41.py` | `legs ["2x","2x_spy"]`; 0.5 of the target per book; downsizes fill in the leg with the lower USD FIFO gain fraction, 10 pp route cap; up-resizes in the leg further below its share | refused: the gain-aware router (`route_cap_pp`) does not reach `fe3_sim` |
| `IC41/B_N` | `research/ideas/IC41.py` | as A_N with leg weights ∝ `1/vol20` of each index | refused: same |
| `IC41/C_N` | `research/ideas/IC41.py` | as A_N, the early (half-weight) path only in the Nasdaq book | refused: same |
| `IC45/C_N` | `research/ideas/IC45.py` | `legs ["2x","gld"]`, `start auto`; 0.88 × production + gold sleeve `0.10 × clip(vol20(QLD)/vol20(GLD), 0.5, 2.0)`; the excess over the 0.98 cap is a **cash loan** | screened |
| `IC46/C_N` | `research/ideas/IC46.py` | `legs ["2x","ief"]`, `start auto`; 0.90 × production + IEF sleeve `0.08 × clip(vol20(QLD)/vol20(IEF), 0.5, 3.0)`; excess borrowed | screened |

Five new arms are simulated; four are registered and refused (their reasons above). The old arms
(`IC34/A-B`, `IC36/A-C`, `IC41/A-C`, `IC45/A-B`, `IC46/A-B`, `IC34/C`, `IC45/C`, `IC46/C`) are
**pinned**: their specs, code paths and ledger lines are untouched (`git diff -U0 851bcbae --
research/ideas/ | grep '^-[^-]'` prints nothing; every old arm's `spec_hash` is unchanged). The new
arms live in new, separate code paths (`spec["legs"]` branches + new functions), so an old arm takes
the identical code path.

### 2.1 Why `IC36`'s runnable arms are a second module

`research/ideas/IC36.py` carries a **module-level** `NEEDS_KERNEL`, which `api.resolve_arm` applies to
*every* arm of that module; it cannot stay while new arms in the same file become runnable, and
adding a per-arm `needs_kernel` to the old arms would re-hash their specs (`verify_ledger` reads that
as `spec_drift`). The module is therefore left byte-for-byte as committed and the runnable arms live
in `research/ideas/IC36_N.py` under `_N` names — the pattern `research/ideas/IC59_K.py` documents for
IC59. `IC34`, `IC45` and `IC46` instead use *per-arm* `needs_kernel`, so their new arms go in the
same file; `IC41`'s new arms are refused (same module).

### 2.2 Fixed readings (documented once, not tuned)

* **"while off"** is production's E2 trend vote (`fe_signals.e2_up`) being off; while it is on, leg A
  holds production's own target and the sleeve/blend is zero. As in `IC34`/`IC45`/`IC46`, the early
  re-entry's half-weight path is *replaced* while E2 is off.
* **`margin_spread` = 0.015** for `IC45/C_N` and `IC46/C_N`: the bank's arm text fixes no spread, so
  IC39's frozen broker rate (1.5 pp/yr over the window's own DTB3 column) is used — the IC39
  convention, stated at the top of each module.
* **`vol20` of an extra-data leg** is that leg's own price path (`data.leg_series`), the series the
  kernel trades. A leading gap must not poison later columns, so the modules carry a NaN-aware
  rolling vol that is **bit-identical to `fe_signals.vol_n` on a gap-free series** (asserted in
  `run_batch.py --check`).
* **Warm-up.** The blend/sleeve vol does not exist for the reduced window's first 19 sessions (the
  window starts one session after the last input's first). The affected sleeve is held in **cash**
  (weight 0) there; a fixed convention, documented in the modules, not a parameter.
* **Reduced windows** (`start = "auto"`): `IC34/C_N` stays on the published window
  (`sec_xlu`/`sec_xlp` start 1998-12-22); `IC36_N/A_N`, `IC36_N/C_N`, `IC45/C_N` run on `w20041119`
  (GLD 2004-11-18); `IC46/C_N` on `w20020731` (IEF 2002-07-30). The control K is re-simulated on
  each of those windows, so every Δ stays paired.

## 3. Method, gates, selection rule

Unchanged from the framework's pre-registered pipeline (`research/batch_screen/README.md` §3–§4,
`PREREGISTRATION.md` §1–§6.4): simulation through `fe_sim`/`fe2_sim`/`fe3_sim`, tax through
`fe_tax`, bootstraps through `fe_boot`/`fe3_boot`; stage 1 = after-tax 2000-window Δ > 0 **and** B1
median Δ > 0 **and** max DD ≤ K + 2 pp **and** turnover ≤ 2 × K; stage 2 = G1–G9 vs K, after tax at
$100k on all three windows, B1/B2 median Δ ≥ 0 with share > 0 ≥ 55 %, the 21 ETFs, the 21 January
rolling starts, the six OOS windows, the pooled OOS bootstrap and the fresh-seed bootstraps; a
declaration additionally needs `rw_stepdown` ≤ 0.05 **and** deflated Sharpe ≥ 0.95 over the whole
ledger N.

**Selection rule (fixed in advance).** Screen the five new arms; report every arm's stage-1/stage-2
outcome and its `gates.failed`; declare nothing myself — any stage-2 pass is reported with its
deflation numbers for the orchestrator to judge. Determinism: every number is rounded to 6 dp and
containers sorted before serialising (the framework's guarantee).

## 4. Harness (run before the screen, recorded in `SUMMARY.txt`)

`python -m research.fast_engine.parity --quick` (must PASS), `python -m
research.batch_screen.run --harness` (K's anchors reproduced), the pre-edit smoke baseline, and the
post-edit `smoke --ideas IC34,IC36,IC36_N,IC41,IC45,IC46`.

## 5. Amendments

*(bug fixes and any deviation from the above, added as they are found — nothing here changes an arm
or a gate after data is seen.)*
