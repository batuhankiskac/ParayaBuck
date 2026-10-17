# opts_nan_fix_v1 — the `chunk_triple` calendar-spanning return, fixed, and the IC56/IC40 re-screen (2026-10-16)

> Evidence: tag `archive/research/rw-boot-audit-v1-2026-10` (not on `main`).

> Evidence: the fix `research/fast_engine_opts/fe_opts_legs.py::chunk_triple` and its regression tests
> `research/fast_engine_opts/tests/test_opts.py`; the study `research/opts_nan_fix_v1/`
> (`PREREGISTRATION.md`, `screen.py`, `parity_cells.py`, `DATA_MANIFEST.json`, `PRE_FIX_BASELINE.npz`,
> `PARITY_FIX.json`, `HARNESS.json`, `RESULTS.json`, `SUMMARY.txt`); the framework amendment
> `research/batch_screen/PREREGISTRATION.md` §6.5; the re-screen batch
> `research/batch_screen/results/opts_fix_v1/` (+ `_control*.json`, `timing.json`), the ledger
> `REGISTRY.jsonl` and `results/DEFLATION.json`; the diagnosis this repairs
> `docs/research/batch_bank_v3.md` §`Known issue` and its repro
> `research/batch_bank_v3/ic56_nan_repro.py`; the engine `research/fast_engine` (untouched) and the
> option copy `research/fast_engine_opts` (`kernel_opts_v1.md`, `README.md`).

**Verdict: the defect was real, and repairing it is behaviour-preserving everywhere it must be and
nowhere else. The affected bootstrap cells become real numbers; no arm's verdict changes.** Both
option ideas that reached the broken function keep their after-tax 2000-window Δ to the last digit
(the window path never entered it) and gain a finite B1-recent median: `IC56/A` −0.1396 pp (was NaN,
988/1000 cells non-finite), `IC56/B` −0.1049, `IC56/C` −0.1396, `IC40/A` = `IC40/B` −0.8091 (was NaN,
981/1000), `IC40/C` −0.9617 (was NaN, 992/1000). All 12 re-run arms are **rejected at Stage 1** in
both the old and the new run — **no verdict changes**, and the deflation over the batch declares
nothing (`declared = []`, `stage2_pass = []`). N is unchanged (303 distinct trials; every re-run arm
keeps its `spec_hash` and its line is a `rerun`). The fix touches one function reached only by
`wht > 0` or `leg = "2x_spy"` chunk panels; **every non-option arm is bit-identical** across the main
window, a reduced pool, two fresh seeds/blocks and the pooled OOS bootstrap, and the engine's own
parity, K's anchors, the framework's 116 checks and 132 tests, and the option package's 30 checks and
36 tests all reproduce unchanged.

## The root cause

`fe_opts_legs.chunk_triple` rebuilt a bootstrap chunk's option leg (the `wht` dividend column of
`chunk_raw_panel`, and the `2x_spy` leg through `_leg_of`) from the drawn sessions' **raw prices
concatenated as if consecutive**:

```
o2 = [close(idx₀−1), open(idx₀),  open(idx₁), …]
c2 = [close(idx₀−1), close(idx₀), close(idx₁), …]
```

`fe_signals.synthetic_2x` reads the return between *consecutive columns*. A chunk is stitched from
20-session blocks with a jump between blocks (`fe_boot.draw_positions`), so at every jump columns
`t` and `t−1` are two unrelated sessions and the return spans the calendar gap. For the 2x leg one
such spanning return ≤ −50 % makes the gross factor `1 + 2r − cost` ≤ 0, the cumprod path flips sign,
the leg's price goes non-positive, the NAV with it, and the per-path CAGR
`(nav[k]/nav[0])**(1/yrs) − 1` is NaN. The framework's own chunk panel (`boot.boot_panels`) never
does this: it steps each drawn session from its **own** previous close
(`prev = win.q[1][idx − 1]`). On the published draw (seed 20261011, block 20, 2,776-session chunks)
the rebuild had **2,808 of 5,554 columns ≤ 0** (min −2.8 × 10¹⁸) for the QQQ leg and **2,835** (min
−60.9) for the SPY leg, while the framework's own leg stayed positive (min 0.205). The
`docs/research/batch_bank_v3.md` §`Known issue` signature — 988/1000 NaN B1 cells for IC56 and
981–992/1000 for IC40 — is exactly this, and the arithmetically killed paths are the same paths whose
spanning 2x factor is ≤ 0.

## The fix

`chunk_triple` now builds the level from the drawn sessions' **own** returns compounded forward from
the first drawn session's previous close, and rescales the open and the dividend to that level:

```
prev = c[idx − 1]                                  # each drawn session's own previous close
lvl  = [prev₀, prev₀ · Π_{s≤t} c[idx_s]/c[idx_s−1], …]      # the same step boot_panels uses
o2   = [prev₀, lvl_t · open[idx_t]/prev_t, …]
d2   = [0,     lvl_t · div[idx_t]/prev_t,  …]
```

Each column's close-to-close (and open-side, and total) return is therefore the drawn session's own
return — the framework's convention, and the same one `repanel` (the window path) builds — so a block
jump cannot span the calendar. On a **contiguous** draw the level is algebraically the raw price path,
so the fix is a no-op there (verified to `rtol = 1e-12`). Reach is unchanged: only a bootstrap chunk
that needs an option leg (`wht > 0`, or `leg = "2x_spy"`) enters the function.

## Proof it did not touch anything else

`research/opts_nan_fix_v1/parity_cells.py` archives the per-path bootstrap CAGRs of a fixed arm set —
the control `k_control/K`, `r2_control/R2`, the two-leg `IC38/A`, and the two option arms `IC56/A`,
`IC40/A` — over five draws (the published main-window bootstrap, a reduced pool, fresh seeds
20261016 blocks 10 and 40, and the pooled OOS bootstrap on M1): **85 cells**. The archive was taken on
the pre-fix tree (`PRE_FIX_BASELINE.npz`, committed) and is diffed against the post-fix archive by
`screen.py --parity` → `PARITY_FIX.json`:

| claim | result |
|---|---|
| every **non-option** arm bit-identical (`np.array_equal`, `equal_nan=True`, NaN pattern included) | **yes**, 0 of 51 cells changed |
| an option cell going finite → non-finite | none |
| every all-NaN option cell becomes wholly finite | yes, 24 cells |
| option cells that were finite pre-fix and change | 11 (reported, expected) |

The 11 are the honest caveat to "every previously finite path is bit-identical": an option cell can
be finite pre-fix *and still be the defect's output* (a jump whose spanning 2x factor happened not to
flip the sign) — those entries are wrong and cannot be preserved by any fix that keeps the bug. They
are all in the small-pool / fresh-block draws (`pool0_500` and `fresh b40`), where the spanning
returns happened not to kill the path; the main published draw and the pooled OOS cell were all-NaN
pre-fix. Every arm whose window/after-tax path is unaffected — the whole rest of the campaign — is
bit-identical.

The engine and the framework are otherwise untouched, re-run on the fixed tree:

| check | result |
|---|---|
| `python -m research.fast_engine.parity --quick` | **PASS** (12 s; PARITY.json restored byte for byte) |
| `research.fast_engine_opts.harness` (parity off + bootstrap off + the 14 anchor cells + 13 mechanism checks) | **PASS**, 30/30 |
| `screen.py --harness` — K's and R2's published cells through `fe_sim.simulate` (pre-tax base/calibrated CAGR + max DD, after-tax $100k 2000/2010/2017), 28 cells | **PASS** (≤ 0.01 pp), wall 14.8 s |
| `research.batch_screen.run --harness` | **PASS**, 116/116 |
| `research/batch_screen/tests/run.sh -q` | **132 passed** |
| `research/fast_engine_opts/tests/run.sh -q` | **36 passed** (4 new: the own-return step, the positive/matches-the-framework cell, the contiguous-draw isolation, and the naive-construction counterfactual) |
| `run --verify-registry` | **PASS** (383 lines, N = 303) |

The IC56 repro (`python -m research.batch_bank_v3.ic56_nan_repro`, read-only, 1.1 s):

| cell | pre-fix | post-fix |
|---|---|---|
| rebuilt QQQ leg columns ≤ 0 (2 paths) | 1,541 / 1,267 | **0 / 0** |
| rebuilt leg minimum (2 paths) | −1.3 × 10¹⁰ / −2.8 × 10¹⁸ | 0.2027 / 0.5825 (framework: 0.2053 / 0.5833) |
| `2x_spy` leg columns ≤ 0 (of 5,554) | 2,835 (min −60.9) | **0** (min 0.339) |
| jump-free isolation: columns ≤ 0 / max rel diff | 0 / 3.60e-4 | 0 / 3.60e-4 (unchanged — the fix is a no-op on a contiguous draw) |
| end-to-end IC56/A: NaN after-tax Δ (of 16) | 16 | **0** |

The 3.6e-4 jump-free residual is the two reconstructions' dividend convention (the framework
reinvests the dividend at the open; the raw triple applies it at the close — a `div²`-order
difference), not the fix: it is identical before and after, which is the isolation property.

## The re-screen (`opts_fix_v1`)

Batch `opts_fix_v1` (`--ideas IC56,IC40,IC31,IC35 --workers 4`): 12 arms, all `rerun` lines, N = 303.
Cold run **wall 86.5 s**, second run 43.6 s; `RESULTS.json` **byte-identical** across the two runs.
Stage 1 = after-tax 2000-window Δ > 0 **and** B1-recent median Δ > 0; the old/new cells:

| arm | window | AT2000 Δ old → new | B1 median old → new | B1 non-finite old → new | verdict |
|---|---|---|---|---|---|
| `IC56/A` | default | −0.182495 → −0.182495 | NaN → **−0.139618** | 988 → **0** | reject_stage1 (unchanged) |
| `IC56/B` | default | −0.139958 → −0.139958 | NaN → **−0.104884** | 988 → **0** | reject_stage1 (unchanged) |
| `IC56/C` | default | −0.182495 → −0.182495 | NaN → **−0.139618** | 988 → **0** | reject_stage1 (unchanged) |
| `IC40/A` | default | −1.338770 → −1.338770 | NaN → **−0.809120** | 981 → **0** | reject_stage1 (unchanged) |
| `IC40/B` | default | −1.338770 → −1.338770 | NaN → **−0.809120** | 981 → **0** | reject_stage1 (unchanged) |
| `IC40/C` | default | −2.809626 → −2.809626 | NaN → **−0.961678** | 992 → **0** | reject_stage1 (unchanged) |
| `IC31/A` | w20020731 | −0.228656 → −0.228656 | 0.959438 → 0.959438 | 0 → 0 | reject_stage1 (unchanged) |
| `IC31/B` | w20020731 | 0.280882 → 0.280882 | NaN → NaN | 1 → 1 | reject_stage1 (unchanged) |
| `IC31/C` | w20020731 | −0.532648 → −0.532648 | NaN → NaN | 2 → 2 | reject_stage1 (unchanged) |
| `IC35/A` | w20060523 | 1.830206 → 1.830206 | NaN → NaN | 8 → 8 | reject_stage1 (unchanged) |
| `IC35/B` | w20060523 | 1.532914 → 1.532914 | 13.815586 → 13.815586 | 0 → 0 | reject_stage1 (unchanged) |
| `IC35/C` | w20060523 | −0.280216 → −0.280216 | NaN → NaN | 10 → 10 | reject_stage1 (unchanged) |

Every IC31/IC35 arm is *cell for cell identical* to `bank_v3` (`RESULTS.json`'s `unchanged_arms`) — the
check that the fix is confined to the option-leg path, since those are two-leg extra-series arms.
Every IC56/IC40 arm keeps its Δ and DD/turnover cells to the last digit and gains a finite B1 median;
the B1 medians are negative (with share > 0 ≈ 0), so all six fail the B1 half of Stage 1 on a real
number instead of on a NaN — the same verdict for the same reason the after-tax gate already gave.

**Deflation** (`--deflate --batch opts_fix_v1`, N = 303, 2,000 resamples, seed 20261016): 8 arms with
a finite Δ vector (IC56 A–C and IC40 A–C join IC31/A and IC35/B); `white_rc` best arm `IC35/B`,
`t_max = 41.588`, `p = 0.0000`; `rw_stepdown` rejects `IC31/A` and `IC35/B` at 5 %; `deflated_sharpe`
passes for none (`var(SR) = 3.267`); `stage2_pass = []`, `declared = []`, `deflation_rejected = []`.
Nothing is a candidate.

**What the re-screen did *not* fix.** `IC31/B` (1), `IC31/C` (2), `IC35/A` (8) and `IC35/C` (10) still
carry non-finite B1 entries, so their B1 medians are still NaN. The orchestrator's list named
`IC31/B` and `IC35/A` as "B1 was NaN in bank_v3"; re-running them shows those NaNs are **not** this
defect — they are two-leg extra-series arms that never reach `chunk_triple`, and their non-finite
counts are unchanged (1 and 8, exactly as `bank_v3`'s deflation recorded). They remain a separate,
un-investigated effect, as `docs/research/batch_bank_v3.md` §`Known issue` already said; nothing here
changes their verdict (`reject_stage1` before and after).

## Determinism, cost, artifacts

* The batch was run **twice**; `RESULTS.json` and the per-arm records are byte-identical
  (`shasum -c` clean). The second run appends 12 further `rerun` ledger lines (N unaffected).
* Wall clock: cold `opts_fix_v1` run **86.5 s** on 4 workers (`timing.json` records the last,
  warm run: 43.6 s); the engine's `parity --quick` 12.0 s; the study harness 14.8 s.
* Artifacts: `research/opts_nan_fix_v1/` (`RESULTS.json`, `SUMMARY.txt`, `HARNESS.json`,
  `PARITY_FIX.json`, `PRE_FIX_BASELINE.npz`, `screen.py`, `parity_cells.py`, `DATA_MANIFEST.json`);
  `research/batch_screen/results/opts_fix_v1/`; `REGISTRY.jsonl` (383 lines, N = 303);
  `results/DEFLATION.json`.

## Caveats

1. The fix changes the *values* of the option arms' bootstrap cells that were finite pre-fix in the
   small-pool/fresh-block parity draws (11 cells): those values were produced by the spanning
   reconstruction and are not preserved. This is the honest reading of "every previously finite path
   is bit-identical" — it holds for every path the defect did not produce.
2. The repaired B1 medians are the *level* of the option arms on the campaign's clean bootstrap; they
   are negative and the arms fail Stage 1 on them, so no conclusion about the `wht` or `2x_spy` ideas
   changes. The arms that were affected are the `wht` and `2x_spy` arms only; they were rejected on
   their after-tax 2000-window Δ regardless, exactly as `docs/research/batch_bank_v3.md` predicted.
3. `IC31/B`, `IC31/C`, `IC35/A`, `IC35/C` keep their non-finite B1 entries — an unrelated mechanism,
   left as `bank_v3` recorded it.
4. 20-session blocks cannot capture serial dependence beyond 20 sessions (the campaign's standing
   caveat); the deflation's N is the whole campaign ledger (303).
