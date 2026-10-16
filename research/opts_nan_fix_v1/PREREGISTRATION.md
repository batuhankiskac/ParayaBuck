# opts_nan_fix_v1 — the option-leg chunk reconstruction fix and its re-screen

Committed **before** the re-screen batch `opts_fix_v1` is run (as required: the framework amendment
is `research/batch_screen/PREREGISTRATION.md` §6.5, dated 2026-10-16). This is a **bug fix + targeted
re-screen**, not a new idea: no arm, parameter, gate, threshold, seed, sample size, control or window
is chosen or changed here.

## 1. Question

`research/fast_engine_opts.fe_opts_legs.chunk_triple` feeds `fe_signals.synthetic_2x` the drawn
sessions' **raw prices concatenated as if consecutive**. A bootstrap chunk has block jumps
(`fe_boot.draw_positions`), so the return between the columns at a jump is a ratio of two unrelated
sessions. For the 2x leg such a spanning return ≤ −50 % makes the gross factor `1 + 2r − cost` ≤ 0,
the leg's price flips non-positive and the per-path after-tax CAGR is NaN. Every arm that reaches the
function through a bootstrap chunk is affected: the `wht` leg (IC56) and the `2x_spy` leg (IC40).
`docs/research/batch_bank_v3.md` §`Known issue` diagnosed it; `research/batch_bank_v3/ic56_nan_repro.py`
reproduces it. The question this study answers:

> After repairing the reconstruction, do the affected arms' bootstrap cells become usable, does any
> arm's verdict change, and is every previously-correct path untouched?

## 2. Control

Production **K** exactly (`k_control/K`) — the batch's own control, re-simulated on each arm's window
(and, for the two reduced-window ideas, on its own `_control_<tag>.json` pool). **R2** (`r2_control/R2`)
is reported as the reference row. K's published anchors are reproduced through the **engine**
(`fe_sim.simulate`, not the option copy) in `screen.py --harness`: pre-tax base/calibrated CAGR and
max DD, and the after-tax $100k progressive CAGR on the 2000 / 2010 / 2017 windows, against
`research/band_h2_combo_v1/RESULTS.json` (tolerance ≤ 0.01 pp).

## 3. Arms (every parameter FIXED — the registered `bank_v3` specs, unchanged)

The re-screen re-runs the arms whose bank_v3 bootstrap cells were affected, and the two ideas whose
B1 vector carried a few non-finite entries; all 12 arms of the four ideas are re-run (the framework's
`--ideas` flag is per idea, so the whole ideas run as `rerun` lines — that is intended and stated):

| idea | arms | spec | bank_v3 `spec_hash` | the option / leg |
|---|---|---|---|---|
| `IC56` | A, B, C | `{"rule": "ic56_wht20"/"ic56_wht15"/"ic56_wht20", "leg": "2x", "cash_yield": "none"}` | `9421bce8ad5a` (A, C), B its own | `wht = 0.20` / `0.15` |
| `IC40` | A, B, C | `{"rule": "K", "leg": "2x_spy", "cash_yield": "none", "vote": "qqq"/"qqq"/"sp500", "inputs": []/[]/["sp500"]}` | `3e2b7f04506d` (A, B), C its own | `leg = "2x_spy"` |
| `IC31` | A, B, C | off-state LQD (`leg_b = "lqd"`, `start = "auto"`) | registered in bank_v3 | two-leg extra series — **not** the fix's path |
| `IC35` | A, B, C | off-state GDX (`leg_b = "gdx"`, `start = "auto"`) | registered in bank_v3 | two-leg extra series — **not** the fix's path |

IC31 and IC35 are included only because the orchestrator's list names their bank_v3 non-finite B1
entries (`IC31/B` 1, `IC35/A` 8); they do not reach `chunk_triple`, so their numbers are expected to
be **unchanged** and their re-run is the check of that. Every arm keeps its `spec_hash`, so N is
unchanged (303 distinct trials) and the re-runs are `status = "rerun"` ledger lines.

## 4. Data (no downloads)

No new data: the fix reads only inputs the campaign already committed, and the re-screen runs the
same battery as `bank_v3`. Sources, ranges and SHA-256 are in `DATA_MANIFEST.json`; the framework's
own `research/batch_screen/DATA_MANIFEST.json` pins the rest (`--verify` re-hashes every extra series).

## 5. Mechanics (the fix)

`chunk_triple(pos, sym)` builds the level as the drawn sessions' **own** price returns compounded
forward from the first drawn session's previous close (`lvl[t+1] = lvl[t] · c[idx_t]/c[idx_t − 1]`,
with `prev = c[idx − 1]`), and rescales the open and the dividend to that level. Each column's return
is therefore the drawn session's own return — the framework's own convention (`boot_panels`'
`prev = win.q[1][idx − 1]`) — and a block jump cannot span the calendar. On a contiguous draw the
level is algebraically the raw price path, so the fix is a no-op there.

**Reach.** `chunk_triple` (via `chunk_raw_panel`) is entered only when a bootstrap chunk needs an
option leg: `wht > 0` (IC56) or `leg = "2x_spy"` (IC40). No window path, no other option and no
non-option arm can reach it.

## 6. Acceptance conditions (fixed here, before the run)

1. **(a) Nothing correct changes.** Parity cells (`research/opts_nan_fix_v1/parity_cells.py`, 85
   cells: the published main-window bootstrap, a reduced pool, two fresh seeds/blocks and the pooled
   OOS bootstrap; arms `k_control/K`, `r2_control/R2`, the two-leg `IC38/A`, and the two option arms
   `IC56/A`, `IC40/A`): every **non-option** arm must be **bit-identical** (`np.array_equal`,
   `equal_nan=True`, NaN pattern included). The archive of the current (fixed) code is diffed against
   the committed `PRE_FIX_BASELINE.npz` (taken on the pre-fix tree) — `screen.py --parity`, committed
   as `PARITY_FIX.json`.
2. **(a′) The repair only repairs.** For the option arms, no entry may go finite → non-finite, and
   every all-NaN cell must become wholly finite. Option cells that were finite pre-fix *and change*
   are expected and reported: they were built by the defect (a jump whose spanning 2x factor happened
   not to flip the sign); a fix that preserved them would keep the bug. This is stated as a
   deviation from a literal reading of "every previously finite path" — no repair can satisfy that
   literal reading, since the defect's own finite outputs are wrong.
3. **(b) The repro is clean.** `research/batch_bank_v3/ic56_nan_repro.py` (read-only) must show 0
   non-positive columns for the rebuilt QQQ leg and the SPY leg (was 2,808 / 2,835), 0 / 0 for the
   jump-free isolation, and 0 NaN per-path Δ end to end; the jump-free rebuild must match the
   framework's leg within the campaign's documented tolerance (the two use different dividend
   conventions; measured ~3.6e-4 relative — unchanged by the fix, since it is a no-op on a
   contiguous draw).
4. **(c) Regression tests.** New tests in `research/fast_engine_opts/tests/test_opts.py`; the
   framework's 132 tests, the option package's 36, `research/fast_engine.parity --quick`, the
   framework's 116-check harness, the option package's 30-check harness and `--verify-registry` must
   all pass.
5. **Gates unchanged.** Stage 1 = `after-tax 2000-window Δ > 0` **and** `B1-recent median Δ > 0`
   **and** pre-tax max DD ≤ K + 2 pp (both financing models) **and** turnover ≤ 2 × K; Stage 2 = the
   registered holdout; then the deflation (`--deflate --batch opts_fix_v1`). A repaired B1 cell can
   only replace a NaN "fail" with a numeric verdict.
6. **Determinism.** The batch is run twice and `RESULTS.json` must be byte-identical; the run's wall
   clock is recorded in `SUMMARY.txt`.

## 7. Selection / reporting

Verdict first in `docs/research/opts_nan_fix_v1.md`: root cause, fix, old vs new Stage-1 cells per
arm, whether any verdict changed, N. `docs/research/INDEX.md` is not edited.
