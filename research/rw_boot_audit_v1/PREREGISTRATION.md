# rw_boot_audit_v1 — pre-registration (engineering audit: the reduced-window two-leg bootstrap Δ)

**Kind of task.** Engineering audit of a *suspected framework bug*, not an idea. No new arm, no new
parameter, no gate, no threshold, no seed and no window is introduced or changed. This file is
committed **before** any non-control arm is re-simulated for this study.

**Question.** In batch `bank_v3` the reduced-window two-leg arms whose leg B is an **extra-data key**
report after-tax bootstrap B1 per-path Δ vs K of +10…+40 pp/yr (median ≈ +12 pp; `IC37/C`
`b1_delta_pp`, `research/batch_screen/results/bank_v3/IC37__C.json`), while the same arm's
*main-window* pre-tax CAGR is only ≈ +2 pp above K and its fixed-window after-tax Δs are
+1.1/+0.2/+0.65 pp. A full-window two-leg arm whose leg B is a **panel** leg (`IB27`, `leg_b = "1x"`)
has a sane B1 (≈ −1.7 pp). Is the bootstrap Δ real, or a framework defect?

**Symptom, exactly as recorded (read before this file was written).**

| cell (`IC37/C`, `w20030103`, pool `(959, 6930)`) | value |
|---|---|
| pre-tax bootstrap B1 median Δ (base) | **+18.4999 pp** (share > 0 = 0.9218, n = 5000) |
| pre-tax bootstrap B1 median Δ (calibrated) | +18.4027 pp |
| after-tax B1 (progressive) median Δ | **+12.2296 pp** (share > 0 = 0.878, n = 1000) |
| after-tax B2 (flat) median Δ | +14.5108 pp |
| after-tax window Δ, $100k, 2000/2010/2017 | +0.5879 / +0.1764 / +0.6431 pp |
| pre-tax window CAGR Δ (m = 2, base) | ≈ +2 pp |

The Δ is already huge **pre-tax** (and ≈ +35.6 pp in *level*), so this is not a tax-funding or
tax-accounting effect: the arm's *bootstrap market path* for leg B is different from the arm's real
window path.

## Hypotheses to test (all four, in this order)

1. **(a) pairing.** Is K's reduced-window control drawn from the same pool, the same start/length,
   the same capital and the same TRY calendar as the arm? (Structural answer: `boot_pool`,
   `_control_<tag>.json`, `BootWindow`. Empirically: the arm's and the control's raw per-path
   vectors, re-derived here.)
2. **(b) the leg-B chunk construction.** Is an extra-key leg-B series on a drawn chunk built from
   each drawn session's **own previous close** — the convention `boot.boot_panels` uses for the
   market leg and the one `research/opts_nan_fix_v1` established for `chunk_triple` — or is it a
   per-drawn-session *level* whose return at a block jump spans the calendar? (Pool legs
   `1x`/`2x` are built correctly by `boot_panels`; only extra keys go through
   `MarketData.leg_series`.)
3. **(c) units / annualisation.** Is the Δ a difference of like quantities (both per-path CAGRs
   annualised over the *same* session count / years), evaluated in pp, both legs' weights in
   fractions?
4. **(d) tax funding.** In the two-leg kernel, when an advance tax exceeds cash, does the funding
   sale leave the NAV ≤ 0 (the path the follow-up study `research/ic37c_confirm_v1` reported)?

## Pre-registered diagnostic method (item 1–2 of the task)

`research/rw_boot_audit_v1/repro.py`:

* draws **one** chunk (seed 20261011, block 20, one path) on `IC37/C`'s own window pool and, for
  the same draw, runs (i) the arm through `boot._two_leg_chunk`/`fe2_sim.sim2` and (ii) K through
  the framework's one-leg driver, at the same `i0/i1`, capital, calendar and rule;
* decomposes the Δ into: the two legs' identity on the drawn sessions (leg-A market path equality
  with the control's), the leg-B price path's column-to-column returns against the drawn sessions'
  **own** returns `IEF[idx]/IEF[idx−1]`, the pre-tax NAV paths, the tax paid per year, and the
  drawn sessions themselves (pool, start, length);
* reports, for the IC37/C window chunk, the number of columns where
  `return(leg-B column t) ≠ own return(drawn session t−1)` — the direct signature of (b);
* repeats the same for `leg_b = "1x"` (a panel leg) as the control for (b).

## Decision rule (fixed before the diagnosis)

* If (b) is confirmed (a non-zero count of calendar-spanning leg-B columns, and the arm's chunk
  leg-B path is not the own-return-compounded path), the study is a **bug**: apply the minimal fix
  and re-screen, as below.
* If (a)/(c)/(d) is confirmed instead, the fix targets that item; the rest of the plan is unchanged.
* If none is confirmed, the Δ is **not** a bug and the study reports, with numbers, why the
  bootstrap Δ is +12 pp while the fixed windows show +1 pp. No fix, no re-screen.

## The fix (if (b) is confirmed) — minimal, single layer

`research/batch_screen/api.py::MarketData.leg_series` is the *only* place an extra-key leg B is
turned into a price path. It currently returns the per-column level `extra(key)`. The fix adds
`MarketData.chunk_price_path(key)`, used by `leg_series` when the panel is a bootstrap chunk
(`extra_index is not None`), which rebuilds the level from each drawn session's own
close-to-close return compounded forward from the base (normalised to 1):

```
level[:, 0] = 1
level[:, t] = level[:, t-1] * pool[idx_{t-1}] / pool[idx_{t-1} - 1]      # idx = extra_index[:, 1:]
```

which is exactly `boot_panels`' convention for the market leg. On a **contiguous** draw the two
constructions agree up to scale. Nothing else changes: `extra()` (used by every state condition,
and by `ffill`) keeps its published per-drawn-session semantics; `leg_b_usable` follows
`leg_series` and therefore also checks the rebuilt path. No kernel, no gate, no arm, no spec.

The framework files are off-limits to *plugins* but this audit is explicitly chartered to repair the
framework; the engine (`research/fast_engine/`) and every plugin (`research/ideas/`) stay untouched.

## Pre-registered regression tests (must fail before the fix, pass after)

In `research/batch_screen/tests/test_two_leg.py` (pytest, `research/batch_screen/tests/run.sh`):

1. **own-return convention.** On a real chunk panel, `panel.leg_series("ief")[0]` must equal
   `concat([1], cumprod(pool[idx]/pool[idx−1]))` **and** must *differ* from `extra("ief")` — the
   pre-fix value. (This is the cell that would have caught the defect; it asserts non-vacuously.)
2. **isolation.** On a jump-free (contiguous) draw the rebuilt path must be *proportional* to the
   raw series (no jump ⇒ no change), so the fix cannot alter a contiguous draw.
3. **no calendar-spanning column.** On a real chunk no leg-B column-to-column return may exceed the
   instrument's own maximum daily move by more than a hair; the pre-fix construction violates this
   (max |r − 1| ≈ 1.2 vs ≈ 0.03 in the probe).
4. **availability semantics preserved.** A gapped series (`dgs10`) is still refused on a chunk by
   `leg_b_usable`, with the failing column named; the reduced pool still makes a late-starting leg
   usable (the existing harness cells).

Plus a **paired-arm sanity arm**: an arm whose leg B is a *constant* series (zero returns) must give
B1 Δ ≈ 0 vs K — included as an invariance check even though it is not a discriminator (a constant
series has zero returns under both conventions).

## Unaffected-path proof (bit-identity, pre- vs post-fix)

`research/rw_boot_audit_v1/parity_cells.py` archives the per-path bootstrap CAGRs
(`B.boot` pre-tax base/calibrated, after-tax progressive/flat at $100k) of a fixed arm set over
several draws — the published main-window draw, the `IC37/C` reduced pool, two fresh seeds/blocks —
and the pooled OOS bootstrap. The archive is dumped **on the pre-fix tree** (`PRE_FIX_BASELINE.npz`,
committed) and diffed against the post-fix archive (`PARITY_LEGB.json`). Arm set:

| arm | leg B | why it is in the archive |
|---|---|---|
| `k_control/K` | — | single-leg control (must be bit-identical) |
| `r2_control/R2` | — | single-leg R2 (must be bit-identical) |
| `IC38/A` | `1x` (panel) | full-window two-leg, panel leg ⇒ untouched |
| `ib27_2L/A_2L` | `1x` (panel) | full-window two-leg, panel leg ⇒ untouched |
| `IC37/C` | `ief` (extra) | the reported arm — expected to change (that is the repair) |
| `IC31/A` | `lqd` (extra) | extra-key arm ⇒ expected to change |
| `ib25_RW/A_RW` | `shy` (extra) | extra-key arm ⇒ expected to change |
| `IB26_2L/A_2L` | `ief` (extra) | extra-key arm ⇒ expected to change |

Required: every *panel/single-leg* arm bit-identical (`np.array_equal`, `equal_nan=True`), including
its NaN pattern; no extra-key cell may gain a non-finite entry; the number of non-finite extra-key
entries must not increase.

## Re-screen batch `rw_fix_v1` (run only if the fix is applied)

Every registered arm whose bootstrap went through extra-key leg B, plus the panel-leg arms as the
collateral check (specs and code unchanged ⇒ every line is a `rerun`, N = 303 unchanged):

`--ideas IC31,IC32,IC33,IC34,IC35,IC37,IC38,IC42,IC43,IC44,IC45,IC46,ib25_RW,ib25_2L,IB26_RW,IB26_2L,ib27_2L`

* extra-key (affected): `IB26_RW` A–C, `IC31` A–C, `IC32` A–C, `IC33` A–C, `IC34` A/B, `IC35` A–C,
  `IC37` A–C, `IC45` A/B, `IC46` A/B, `ib25_RW` A/B, `ib25_2L` A/B, `IB26_2L` A–C;
* panel-leg (collateral, expected bit-identical): `IC38` A–C, `IC42` A–C, `IC43` A–C, `IC44` A–C,
  `ib27_2L` A–C;
* `IC34/C`, `IC41/*`, `IC45/C`, `IC46/C` remain `needs_kernel` (re-registered, never simulated).

Then `python -m research.batch_screen.run --deflate --batch rw_fix_v1`.

**Gates unchanged, no selection.** Stage 1 is the framework's pass rule, stage 2 its holdout, the
deflation its three tests — all as pre-registered in `research/batch_screen/PREREGISTRATION.md`.
This study declares **no candidate**: the re-screen is a repair, and a verdict that changes is
reported as a change, not adopted. Selection rule for the report: `old vs new Stage-1 cells` for
every re-screened arm, and `verdict_changed` listed explicitly.

## Determinism, cost, artifacts

* `--harness` runs `python -m research.fast_engine.parity --quick` (must PASS, `PARITY.json`
  restored), K's published anchors through `fe_sim`, the paired-control cells, the leg-B convention
  cells and the pre/post parity diff.
* The batch is run **twice**; `RESULTS.json` (and the per-arm records) must be byte-identical.
* Wall clock of the `--run` is recorded in `SUMMARY.txt`.
* Artifacts: `repro.py`, `parity_cells.py`, `screen.py`, `DATA_MANIFEST.json`,
  `PRE_FIX_BASELINE.npz`, `PARITY_LEGB.json`, `HARNESS.json`, `RESULTS.json`, `SUMMARY.txt`, and
  the report `docs/research/rw_boot_audit_v1.md`.
