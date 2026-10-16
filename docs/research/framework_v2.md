# framework_v2 — reduced windows, the panel column calendar, two-leg fill options

**Verdict.** Framework engineering, not an idea: three mechanisms the `idea_bank_v2` IC31–IC46 and
`idea_bank_v1` IB25/IB26 arms need, delivered as code, tests and parity, with **no idea's
performance computed** and no new data. Everything passes: the engine's own
`research.fast_engine.parity --quick` (required), the framework harness (**116 checks**, 0 failed,
including K's published anchors cell by cell), the two-leg kernel's parity suites (128 + 2 cells),
the option copy's parity and positive controls (28 cells) and the pytest suite (128 tests). The
table below is the delivery; the last section names what is still `NEEDS_KERNEL` and why.

| what | where | parity / proof |
|---|---|---|
| reduced windows (`spec["start"]`, `spec["inputs"]`) | `research/batch_screen/{api,windows,stages,boot,run}.py` | harness `reduced_window.*` (8 cells); `tests/test_reduced_window.py`; probe batch `two_leg/gld_offstate` runs a full stage 1 + stage 2 on `w20041119` with its own `_control_w20041119.json` |
| panel column calendar (`MarketData.column_days`) | `research/batch_screen/api.py` | harness `two_leg.chunk_leg_b_gap_names_the_failing_column`; `tests/test_two_leg.py`; the reported IndexError is reproduced and gone |
| two-leg fill options (`spec["fill_knobs"]`, `spec["kernel_scope"]`) | `research/fast_engine2/fe2_opts.py` (+ `fe2_opts_parity.py`) | `fe2_opts_parity`: option **off** == `fe2_sim`/`fe2_boot` bit for bit (`max_abs_diff == 0.0` on the main window and on 20-path bootstraps), plus one positive control per option; harness `two_leg_opts_parity.*` |
| framework routing to the option copy | `research/batch_screen/{api,stages,boot}.py` | harness `two_leg.fill_knobs_off_is_the_published_kernel`, `.fill_knobs_reach_the_option_kernel`, `.after_tax_scope_leaves_the_pretax_stage_alone`, `.a_panel_leg_off_state_needs_no_reduced_window`; `tests/test_two_leg.py` |

## 1. Reduced windows

The problem: `idea_bank_v2`'s off-state ideas hold LQD (2002-07), IEF (2002-07), TIP (2003-12), GLD
(2004-11), GDX (2006-05) or HYG (2007-04) as a second instrument, and the framework refuses a
leg-B series that does not reach every session the engine reads (`[i0, i1]` and the previous close
at `i0 - 1`). Instead of inventing a price, an arm declares the window it is evaluated on:

```python
ARMS = {"A": {"rule": "K", "leg": "2x", "leg_b": "gld", "cash_yield": "none",
              "inputs": ["dgs10"], "start": "auto"}}
```

| `start` | window |
|---|---|
| absent | the published one (2000-03-09 → 2026-09-29) — the default path, unchanged |
| `"YYYY-MM-DD"` | the first session at or after it, never before session 253 (the published warm-up), never past the panel's end |
| `"auto"` | the later of the published start and **the first session every declared input covers, plus one** (the engine reads the previous close at `i0 - 1`) |

The declared inputs are `spec["inputs"]` plus `spec["leg_b"]` when it is an extra-data key; a panel
leg (`1x`/`2x`) has the window's own history and never moves the window. A window shorter than
**10 calendar years** is `NotApplicable`, as is an `"auto"` input with no value in the panel.

What moves: the pre-tax block; the three after-tax windows (**clipped up** to the window's start —
no key is dropped, each cell records the window that really ran); the January rolling starts (**the
ones that begin before the window are dropped**; fewer than 15 leaves the arm
`holdout_insufficient`); both block bootstraps (**drawn from the window's own sessions** — one
`integers`, one `random`, one `integers` per path in the published order, at the published seed and
block, `npool` = the window's session count). What does not move: the six OOS windows, the 21 ETFs
and the pooled OOS bootstrap, which are other markets with their own pools.

**The control moves with the arm.** K is re-simulated on the same window (`_control_<tag>.json`,
e.g. `_control_w20041119.json`; the published window keeps `_control.json`), so every Δ is paired
*inside the arm's window*. For the `gld_offstate` probe: `i0` = session 1434 (2004-11-19), the pool
`(1433, 6930)` (5,498 sessions), rolling starts 2005–2021 (17), and the three after-tax windows
become 2004-11-19 / 2010-01-04 / 2017-01-03. The subperiod P1 (which ends 2008-12-31) is partial
there and the cell says so; a window starting after a subperiod's end records it as
`subperiods_not_covered` and G4 then judges the covered ones (≥2 required).

The record carries the window (`win.tag`, `win.declared`, `win.inputs`, the stage-1 `window` block,
each after-tax cell's own window); the scoreboard shows its tag; `RESULTS.json` lists the batch's
window set. The deflation is on the B1 Δ vectors and keeps working because an arm's Δ is its paired
advantage over K **on its own window** — the comparable quantity — with the caveat that
different windows do not share a pool (`DEFLATION.json` names them in `window_tags`). An arm whose
Δ vector contains NaN is left out (`excluded_non_finite`) rather than making every other arm's
p-value NaN; it can never be declared (its stage-1 after-tax gate fails closed).

**The reported bug.** `MarketData.leg_b_usable` indexed `self.days` with a *column* index. On a
bootstrap chunk the market arrays carry one artificial leading base row while `days` is one shorter,
so the reported session was off by one and the span string raised `IndexError` whenever the last
column was finite — exactly the case of an extra-key leg B with an interior NaN (`dgs10`) or a
late start (`gld`) on a chunk. Reproduced first (`probes/two_leg.py::gld_sleeve`, `dgs10`), then
fixed by `MarketData.column_days` (the per-column day label; `year`/`month` are now `(T,)` like
every other panel array) and pinned by a regression test and a harness cell.
`leg_b_usable`'s `(ok, reason)` contract is unchanged on a real window.

## 2. Two-leg fill options (`fe2_opts.py`)

`research/fast_engine2/fe2_sim.py` and `fe2_boot.py` are **not** modified except for one pure
refactor: `simulate2`'s post-processing was extracted to `_outputs(..., n_reasons=N_REASONS,
reason_names=REASON_NAMES)` so a copy can reuse it with the netting reason added (defaults preserve
every existing caller; `fe2_parity`'s 128 cells and `two_leg_kernel_v1.parity_b` pin it).

`research/fast_engine2/fe2_opts.py` is a copy of the two-leg kernel with four fill decisions as
parameters. With every option off it reproduces the current kernel **bit for bit**
(`fe2_opts_parity`: 18 single-run cells × 2 financing models, `max_abs_diff == 0.0` including the
nested `_legs` dicts; and the bootstrap driver on the same drawn positions, `out_pre`/`out_at`
identical).

| knob | semantics | measured on the main window (option off → on) |
|---|---|---|
| `band_score` | which gain fraction keys K's 0.5/3/8 pp widths: `"usd"` (published), `"indexed"` (the TRY-indexed FIFO gain fraction of the units the sell would consume, Yİ-ÜFE indexation applied as the engine applies it), `"max"` | `blocked_band` 2647 → 2497 (`indexed`) / 2670 (`max`); CAGR 0.170428 → 0.170203 / 0.170628 |
| `band_step4_pp` | a fourth width above a fixed 0.15 fraction | 12 pp: `blocked_band` 2647 → 2778, CAGR 0.170628 → 0.172397 |
| `route_cap_pp` | on a session where both legs downsize, route units from the other leg to the leg with the lower indexed fraction, bounded by the cap (a weight), by the other leg's own sale and by the cheap leg's remaining units; the routed quantity is what the band test and the fill use | 10 pp: leg turnover A 159.60 → 159.55, B 33.19 → 46.13 (opposite signs, each |Δ| within its own off turnover, total |Δ| = 12.89 ≤ 2 × 0.10 × 6679); NAV moves by 115,206 |
| `loss_net_frac` | after a permitted downsize fill with a positive realised TRY gain (taxed modes only), sell the other leg's loss lots — FIFO, indexed per unit — up to this fraction of the gain | 0.25: 501 netting events, tax 28,986,005 → 28,497,773 TRY (−488,231), CAGR 0.108656 → 0.105248; **pre-tax identical** (`max_abs_diff == 0.0`) |
| `loss_net_min_ytd_try` | the netting sale only fires while the year-to-date realised TRY gain exceeds the threshold | 100k: 501 → 266 events; 1M: 139 events, the netted-gain bucket strictly smaller |
| `kernel_scope` | `"all_stages"` (default) or `"after_tax_stage_only"` (IC42/IC43): the pre-tax stages keep the published kernel | pre-tax identical to a no-knob arm; the tax phase moves |

The option path is per-arm inside the shared bootstrap kernel (`knobs_pre[a]`), so a batch can mix
optioned and plain arms in one chunk and they still share the drawn paths.

**The off-state ideas without any kernel work (a).** IC31–IC37 need no per-session instrument
switch: "while the trend vote is off, hold 0.98 in X; production target otherwise" is a fixed leg B
whose *weight* varies (the instrument never changes):

```python
def targets(data, spec):
    off = 1.0 - fe_signals.e2_up(data.price)              # the trend vote off
    return production_wants(data) * (1.0 - off), 0.98 * off, spec["leg_b"]
```

Evidence, on real data, through the framework's own plugin path:
`probes/two_leg.py::gld_offstate` (`leg_b = "gld"`, `start = "auto"`) runs a full stage 1 and stage
2 from 2004-11-19 with its own control, and `onex_offstate` (`leg_b = "1x"`, IC38: "while off, hold
0.98 in the 1x index") runs on the published window with leg B trading 176 times. Both are harness
fixtures. IC31–IC37's instruments are registered extra keys or a new download the idea's own study
declares; the mechanism is identical.

**What is still `NEEDS_KERNEL`, named rather than approximated.**

| idea | why |
|---|---|
| IC36 (GLD/IEF/SHY blend), IC34 arm C (XLU+XLP) | **three instruments in one account.** A derived "blend series" would hide the sleeve's internal monthly rescale from the taxpayer and leave it without a per-instrument FIFO book, so it is not used as a shortcut. |
| IC39 (margin-financed QQQ) | the account needs a **cash loan** and daily interest on it; the engine's financing lives inside the synthetic 2x leg's price. |
| IC40 (SPY-based 2x leg) | a **second leg family**: the panel offers only the window's own underlying at 2x/1x, and a synthetic 2x of SPY is a new leg construction (data plumbing + kernel). |
| IB38 A/B/C | **two books of the same instrument** (two FIFO queues for one symbol); one instrument has one book per taxpayer by construction. |
| IC41 | a two-leg account *is* available, but its `second_symbol = SSO` is a leg construction with no SSO price in the panel (the same plumbing as IC40). |

The gain-aware routing IC41/IC42/IC43/IC44 asked for is delivered as the three fill knobs above
(`band_score`, `route_cap_pp`, `loss_net_frac`/`loss_net_min_ytd_try`); IC42/IC44's arm variants
are parameter choices (cap 10/5 pp, the fourth band step, the `max` score) and need no further
kernel work.

## 3. Verification

| command | result |
|---|---|
| `python -m research.fast_engine.parity --quick` | PASS (required; `PARITY.json` restored to its committed bytes by the harness) |
| `python -m research.batch_screen.run --harness` | **PASS, 116 checks, 0 failed** (K's anchors pre-tax/after-tax/OOS/ETF/rolling/B1/B2; `fe_boot.boot` and the old `boot_oos_market` bit for bit; two-leg routing; the option copy's parity) |
| `python -m research.fast_engine2.fe2_parity` | PASS, 128 cells |
| `python -m research.two_leg_kernel_v1.parity_b` | all_pass True (alternating leg B, nav diff 0.0) |
| `python -m research.fast_engine2.fe2_opts_parity` | PASS, 28 cells, option off == the current kernel |
| `sh research/batch_screen/tests/run.sh -q` | 128 passed, 0 failed |
| `python -m research.batch_screen.probes.run_probe_batch --ideas two_leg` | 5 arms, isolated ledger; `gld_offstate` runs on `w20041119` with its own control; `gld_sleeve` `not_applicable`; wall 57 s |
| `python -m research.framework_v2.screen --run --tests` | **PASS**, `RESULTS.json` byte-identical across two consecutive runs, wall clock in `TIMING.json`/`SUMMARY.txt` |

Nothing was downloaded: `DATA_MANIFEST.json` lists the seven inputs the new code touches with their
SHA-256 (the main window, the DTB3/FX/Yİ-ÜFE calendars, and the `gld`/`ief`/`dgs10` series the
fixtures use). The engine (`research/fast_engine/`) is untouched. The 1,604-line registry ledger
re-verifies against git (276 lines, N = 223, no problems).

## 4. Limitations and caveats

* **Different windows are not one pool.** A reduced-window arm's Δ is paired against K on its own
  window; its deflation p-values are not the published single-pool p-values (see
  `research/batch_screen/README.md` §4.6). The screening decision is built on paired Δ vectors,
  which is preserved.
* **NaNs fail closed.** A two-leg sleeve that realises a large TRY-indexed gain can leave an
  advance tax the account cannot fund on a few drawn paths; `(NAV < 0) ** (1/years)` is NaN, the
  arm's stage-1 after-tax gate fails, and `deflate_batch` reports it in `excluded_non_finite`
  rather than poisoning every other arm's p-value. Seen once, on the naive `gld_offstate` fixture
  (3 of 200 paths, progressive mode only); the single-leg control and the `split` arm are clean on
  the same pool.
* **`route_cap` moves units, not weight.** A routed fill's cash differs from the un-routed fill by
  the two instruments' price ratio; the cap bounds both legs' deviation from their own target weight
  and the band widths a routed leg is judged on are those its routed quantity earns.
* **The mirror-view (arm − K) direction is unchanged** for every default-path arm: an arm that
  declares none of the new keys takes the published code path, and the default bootstrap pool is
  bit-identical to `pool=None` (harness cell `reduced_window.the_default_window_pool_is_the_published_draw`).
