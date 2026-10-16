# three_leg_kernel_v1 — an N-leg (N ≤ 4) kernel for the batch-screen framework

**Verdict: built and parity-proven. Engineering only — no idea's performance was computed.** The
account can now hold **up to four instruments at once** in one cash pool: one units/FIFO book per
leg, the production/`A1u`/`H2` execution rule applied per leg on that leg's own book, one Turkish
taxpayer, and an optional **margin loan** that charges interest on a negative cash balance. With
every leg beyond the first identically zero (zero prices *and* zero targets) the kernel is
`research/fast_engine.fe_sim.sim_one` **bit for bit**; with every leg beyond the second identically
zero it is `research/fast_engine2.fe2_sim.sim2` **bit for bit**; with the loan off it is both, and
with the loan on a single leg it reproduces `research.fast_engine_opts`' margin cells exactly.

`research/fast_engine/` and `research/fast_engine2/` are **not edited**. The framework's runner
routes a `"legs": [...]` / `margin_spread` arm to the new kernel (§4); an arm that declares neither
takes the published code path, and the framework's harness is the published **116 checks plus 22
new ones, all green**.

## 1. Design

`research/fast_engine3/` is a copy-and-extend of the two-leg kernel, exactly as `fast_engine2` was
of the engine and `fast_engine_opts` of the single-leg kernel:

| file | what it is |
|---|---|
| `fe3_sim.py` | `sim3`/`simulate3`: `fe2_sim.sim2` with `range(N_LEGS)` widened to `range(n)`, `n = oq.shape[0]` (≤ 4), plus the margin hook; `_outputs3` is `fe2_sim._outputs` for `n` legs |
| `fe3_boot.py` | `_boot_chunk3` / `_pt3` / `boot3`: the engine's own `fe_boot.draw_positions` draws, `(W, N, T)` panels and `(n_arms, W, N, T)` target panels, per-arm `mrate` |
| `fe3_parity.py` | the parity cells, the K anchors and the engine subprocess run; `PARITY3.json` |
| `bridge3.py` | the dispatch `batch_screen` calls: the panels, the leg series and their availability, the target panel, the margin schedule, the bootstrap drivers |

The per-leg arithmetic is **not** re-implemented: `fe2_sim._buy_leg` and `_sell_leg` are already
indexed by a leg slot, and they are imported unchanged, together with `fe_sim._assess`,
`fe_sim._rule_sale`, `fe_sim._rule_sale_usd` and `fe_sim.gain_band`. That is what makes the two
identities below exact rather than approximate — an inert leg is a `0.0` added to a sum and a
counter that stays zero, in the same left-to-right order as the two-leg kernel.

The one new mechanism is the margin loan, ported from `fe_opts_sim.sim_one_opts` (IC39's option).
At the top of each session — *before* the idle-cash yield and before any tax payment, exactly the
option kernel's order — a negative cash balance becomes `cash + cash * mrate[i]`, and the interest
is accumulated in `scal[SC_MARGIN_USD]`. `simulate3` reports it as `opts_stats["margin_usd"]`
(the key `fe_opts_sim.simulate_opts` uses) when the loan is on. With `mrate` empty the branch is
unreachable. A target that sums above the account's own 0.98 cap simply borrows: there is no
maintenance margin, no forced liquidation and no Reg-T intraday rule.

`N_LEGS = 4` is a **capacity**: the kernel reads its leg count from `oq.shape[0]`, so a 1- or
2-leg run pays only the loop bound, and the framework decides per arm how many rows to pass (a
two-leg arm without a loan never reaches this kernel at all).

## 2. Parity

`python -m research.fast_engine3.fe3_parity` — **206 cells, 0 failed** (14–18 s), plus
`python -m research.fast_engine.parity --quick` **PASS**. The comparison (`fe2_parity._maxdiff`) is
the whole NAV path **bitwise** and every reported scalar, including the nested per-year dicts and
the per-leg `_legs` rows, skipping the private `_`-keys. `max |diff| == 0.0` is exact equality.

| check | cells | max abs diff |
|---|---:|---:|
| legs 2..N ≡ 0 vs `fe_sim.simulate` — main window 4 rules × 3 modes × m = 1/2/4 × both financiers (72), the three after-tax windows × $10k/$100k/$1M × C0/K × progressive/flat (36), a non-zero idle-cash schedule (3) | 111 | **0.0** |
| legs 3..N ≡ 0 vs `fe2_sim.simulate2` — leg B **real** (`2x` + `1x`), every rule × mode (24); leg B at zero, every after-tax window × capital (36) | 60 | **0.0** |
| legs 2..N ≡ 0, clean block bootstrap (300 paths) vs `fe_boot.boot` and vs `fe2_boot.boot2` | 16 | **0.0** |
| margin **off** ≡ the engine and `fe2_sim` for a target over the cap | 3 | **0.0** |
| margin **on** a single leg vs `fe_opts_sim.simulate_opts` (NAV + `margin_usd`) | 3 | **0.0** |
| positive controls: the loan lowers the NAV, three books trade and `n = 5` is refused | 3 | n/a |
| published K anchors (`band_h2_combo_v1/RESULTS.json`) through `simulate3` | 9 | 3.1e-05 pp |
| `python -m research.fast_engine.parity --quick` | 1 | PASS |

## 3. Speed

Main window (6,679 measured sessions inside a 6,932-session panel), warm numba cache, median of 30
(`research/three_leg_kernel_v1/TIMING.json`):

| run | ms |
|---|---:|
| `fe_sim.simulate` (engine, 1 leg) | 0.15 |
| `simulate3`, 2 legs (leg B at zero) | 0.29 |
| `simulate3`, 3 legs (legs B/C at zero) | 0.32 |
| `simulate3`, 4 legs (legs B/C/D at zero) | 0.35 |
| `simulate3`, 4 **real** books | 0.69 |
| `simulate3`, 1 leg with a margin loan | 0.45 |

The zero-legged rows are the extra-loop padding; the real-book row is the cost of the account
itself. The clean bootstrap (256 paths, C0+K, pre-tax + after tax) runs 0.22 s on
`fe_boot.boot` against 0.29 s on `fe3_boot.boot3` with four legs (1.3×). The whole harness
(`--run`: the parity suite, the speed cells and the framework harness) is **≈ 60 s wall**, 45 s of
it the framework harness.

## 4. Framework integration

An idea asks for the kernel through the same extension point as every other execution variant — its
**spec**:

```python
ARMS = {
    # IC36: while the trend vote is off, an inverse-vol blend of three instruments, monthly rescale
    "A": {"rule": "K", "legs": ["2x", "gld", "ief", "shy"], "cash_yield": "none",
          "start": "auto", "inputs": ["gld", "ief", "shy"]},
    # IC45-C / IC46-C: production plus a sleeve, funded past the 0.98 cap by a loan
    "C": {"rule": "k_margin", "legs": ["2x", "shy"], "cash_yield": "none", "margin_spread": 0.015},
}

def targets(data, spec):
    ...
    return {"2x": w_on, "gld": w_gld, "ief": w_ief, "shy": w_shy}   # or (weights, legs), or a list
```

* `legs` names 2..4 distinct instrument keys (leg A first: `2x`/`1x`/`2x_spy` or any registered
  extra key), validated against `leg_b_keys()`, recorded in `arm["legs"]` and pinned by
  `spec_hash`. `2x_spy` is built on demand from the window's own SPY column (the drawn chunk's SPY
  sessions inside a bootstrap), degenerating to that window's own `2x` leg on a window with no SPY
  series — IC40's documented convention.
* `targets` may return a `{leg: wants}` dict, `(weights_list, legs_list)`, or a bare sequence of
  one weight panel per declared leg. The legacy `wants` / `(wants, leg)` / `(wants_a, wants_b,
  leg_b)` forms keep their meaning; a per-session instrument selection is still `NeedsKernel`.
* `margin_spread` (≥ 0) is accepted in the rule's `opts` (IC39's route) or directly in the spec
  (both must agree), and only on a multi-leg arm; the schedule is
  `session_fold(DTB3 + spread)` over the window's own rate column.
* **Routing is one `if`**: `bridge3.needs_kernel3(arm)` is true for > 2 legs, a loan, or `2x_spy`
  beside another instrument. Everything else — including a two-leg arm with no loan — is the
  published code path, bit for bit.
* **Reduced windows** (`start = "auto"`) now cover every declared leg's inputs: the window begins
  after the last-starting extra-data leg.
* Stage 1 and stage 2 are otherwise unchanged: the same gates, the same paired-control bootstrap
  (a multi-leg group shares the chunk's drawn paths with the control, so Δ stays paired), the same
  `NotApplicable` rule, the same deflation.

The framework harness (`python -m research.batch_screen.run --harness`) went from **116 to 138
checks**, all green: the published cells plus the N-leg routing cells (`run.multi_leg_checks`,
`run.multi_leg_kernel_parity`) and the kernel's own 205 parity cells. The pytest suite is **145
tests** (`tests/test_multi_leg.py`, 14 new).

## 5. What is now expressible, and what remains blocked

**Now expressible** (the arms that were `NEEDS_KERNEL` for structural reasons):

| idea | what it needed | how it is declared |
|---|---|---|
| `IC36 A/B/C` | a 3- (arm B: 4-) instrument inverse-vol blend in one account, rescaled monthly | `"legs": ["2x", "gld", "ief"(, "shy")]`, `"start": "auto"` |
| `IC34-C` | QLD\* with XLU **and** XLP held at once | `"legs": ["2x", "sec_xlu", "sec_xlp"]` |
| `IC45-C` / `IC46-C` | a sleeve on top of production past the 0.98 cap, funded by a loan with interest | `"legs": ["2x", <sleeve>]`, `"margin_spread": …` |
| `IC41 A/B/C` | QLD\* plus the S&P 500 2x leg as leg B | `"legs": ["2x", "2x_spy"]` |

**Still blocked**, named rather than approximated:

* **two books of the *same* instrument** (one FIFO queue per symbol per taxpayer by construction);
* **more than four instruments** in one account (`N_LEGS = 4`);
* a **per-session instrument switch** inside one leg;
* **several cash pools / accounts**, or a **per-arm tax regime or capital** (batch-level settings);
* the **single-leg execution options** (`band_up`/`band_down`, `min_notional`, slicing, the
  drawdown governor, `early_mode`, `settle_floor`, `fx_cost`/`prefund`, `fill_mode`, `wht`) on a
  multi-leg arm — only `margin_spread` reaches the N-leg kernel.

## 6. Artifacts and how to reproduce

```sh
R=~/.venvs/pb-research/bin/python
PYTHONPATH=. $R -m research.fast_engine.parity --quick         # PASS (the engine)
PYTHONPATH=. $R -m research.fast_engine3.fe3_parity --write    # 206 cells -> PARITY3.json
PYTHONPATH=. $R -m research.batch_screen.run --harness         # 138 checks -> HARNESS.json
PROJ_VENV=…/ParayaBuck/.venv sh research/batch_screen/tests/run.sh   # 146 tests
PYTHONPATH=. $R -m research.three_leg_kernel_v1.run --run      # all of the above + speed
```

* `research/fast_engine3/` — the kernel, the bootstrap, the parity suite and `PARITY3.json`.
* `research/fast_engine3/README.md` — the package guide (API, parity, speed, the reach table).
* `research/batch_screen/README.md` §1c — the plugin guide's N-leg section.
* `research/batch_screen/probes/multi_leg.py` — the engineering probe (never a registered trial):
  `zeros` (three legs, two at zero — the control), `offstate_blend` (the IC36 pattern on its own
  reduced window), `margin_qqq` (IC39 on the N-leg kernel), `sleeve_margin` (IC45-C/IC46-C),
  `spy_pair` (IC41).
* `research/three_leg_kernel_v1/` — `run.py` (the harness), `RESULTS.json` (byte-identical over two
  consecutive runs), `SUMMARY.txt`, `TIMING.json`.

**No data was downloaded and no file in `data/historical/` was added**: the package reads only the
CSVs other studies committed (the main window, the registered extra series, the SPY column), so
there is no `DATA_MANIFEST.json`.

## 7. Caveats

1. The kernel is a **copy**, not an extension, of `fe2_sim`/`fe_sim`: a future change to the
   two-leg kernel or to the engine does not propagate, and the parity suite is what keeps the three
   in step. The tax engine itself (`fast_engine.fe_tax`) is shared, so a tax-model edit reaches all
   three at once.
2. The margin loan has no maintenance-margin term: it is the IC39 option's semantics and nothing
   more. A "loan" the account could not really carry (a target above the cap for a whole window)
   is simulated as written, with the interest charged per session.
3. `margin_spread` is a per-window constant added to the window's own DTB3 column; the schedule is
   `fast_engine_opts.fe_opts.session_fold`, so a session after a three-day weekend folds three
   days of the rate.
4. The framework's `_legs` dict is keyed by position (`A`..`D`); the instrument keys live in the
   arm's `legs` declaration and in the record.
5. A 4-instrument arm with four extra-data legs imposes four reduced-window constraints; on the
   main window the binding one is the latest series' start (GLD 2004-11 for the IC36 blend), and
   the window is `NotApplicable` rather than invented.
6. Nothing here changes any published number: the engine's own parity and the framework's harness
   (including the control's K anchors) still pass cell for cell, and no registered arm's `spec_hash`
   moved.
