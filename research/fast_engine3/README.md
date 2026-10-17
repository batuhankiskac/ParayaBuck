# research/fast_engine3 — an N-leg (N ≤ 4) kernel with a margin loan

The two-leg kernel `research/fast_engine2/fe2_sim.py` holds exactly two instruments in one cash
pool. This package generalises it to **N instruments (N ≤ 4)** and adds the one mechanism the
multi-leg account still lacks: the **margin loan** of `research/fast_engine_opts` (interest on a
negative cash balance). `research/fast_engine/` and `research/fast_engine2/` are **not edited** —
the per-leg primitives (`fe2_sim._buy_leg` / `_sell_leg`) and the Turkish after-tax engine
(`fast_engine.fe_tax`) are imported unchanged, so there is exactly one implementation of every
arithmetic step.

```
fe3_sim.py     the N-leg session kernel (`sim3`/`simulate3`) and the result dict
fe3_boot.py    the N-leg block bootstrap driver (`boot3`, the same draws as `fe_boot.boot`)
fe3_parity.py  the parity cells (see below) and the `PARITY3.json` artifact
bridge3.py     the dispatch `research/batch_screen` calls for a multi-leg / margin arm
```

Run the parity suite:

```sh
R=~/.venvs/pb-research/bin/python
PYTHONPATH=. $R -m research.fast_engine3.fe3_parity            # 206 cells, ~14 s
PYTHONPATH=. $R -m research.three_leg_kernel_v1.run --run      # + speed + the framework harness
```

## What it is

One account, one cash pool, **N** instruments, each with its own units and its own FIFO lot book;
one taxpayer, so every leg's realisation (Yİ-ÜFE indexed per lot) lands in the same annual net TRY
gain and `fast_engine.fe_tax`'s kernels are reused unchanged. Per session, per leg: the production
0.5 pp resize band, the A1u gain-scaled downsize band (on *that leg's* own lot book, its own gain
fraction) and the H2 December deferral; costs are charged per leg on that leg's own traded notional;
the legs share the post-cost NAV that sizes the fill; tax-funding sales walk the legs in order
A, B, C, D.

`sim3` is `fe2_sim.sim2` with `range(N_LEGS)` widened to `range(n)`, `n = oq.shape[0]`, and the
leg-index-generic primitives imported from `fe2_sim`. `simulate3(opens, closes, wants, ...)` takes
`(N, T)` panels (or a sequence of 1-D arrays) and an optional `mrate` `(T,)` loan schedule:

```python
from research.fast_engine import fe_data, fe_sim, fe_study
from research.fast_engine3 import fe3_sim

days, o, c, _e2, w_r2, _vote = fe_data.main_inputs(0.0)
fx, ufe = fe_study.try_market(days)
env = fe_sim.RunEnv(days, fx, ufe, fe_study.START, len(days) - 1)
z = np.zeros(len(days))
r = fe3_sim.simulate3([o, o, o, z], [c, c, c, z], [w_r2 / 3, w_r2 / 3, w_r2 / 3, z], env,
                      100_000.0, fe_study.START, len(days) - 1, arm="K", mode="progressive", m=2)
r["cagr_usd_liquidated"], r["_legs"]["A"], r["_legs"]["C"]
```

The margin loan is the `fe_opts_sim.sim_one_opts` hook verbatim: at the top of each session, before
the idle-cash yield and before any tax payment, a **negative** cash balance becomes
`cash + cash * mrate[i]` and the interest is accumulated in `scal[SC_MARGIN_USD]`; `simulate3`
reports it as `opts_stats["margin_usd"]` (the key `fe_opts_sim.simulate_opts` uses), so a
single-leg loan run compares cell by cell with the option kernel. With `mrate` empty the branch is
unreachable and the kernel is `fe2_sim.sim2` exactly.

## Parity — `fe3_parity.py` (206 cells, 0 failed)

| identity | cells | diff |
|---|---:|---|
| legs 2..N ≡ 0 vs `fe_sim.simulate` — main window × 3 modes × m = 1/2/4 × both financiers, the three after-tax windows × 3 capitals × 2 modes, a non-zero idle-cash schedule | 111 | `0.0` |
| legs 3..N ≡ 0 vs `fe2_sim.simulate2` — leg B at zero, and leg B a **real** `1x` leg (every rule × mode, every after-tax window × capital) | 60 | `0.0` |
| margin **off** ≡ both of the above (the `mrate` array is empty) | 3 | `0.0` |
| margin **on** a single leg vs `fe_opts_sim.simulate_opts` (`wants = 2 × production`, so a real loan exists) — the NAV path and `margin_usd` | 3 | `0.0` |
| the clean block bootstrap, legs 2..N ≡ 0 vs `fe_boot.boot`, and vs `fe2_boot.boot2` | 16 | `0.0` |
| the published K cells through `simulate3` (`band_h2_combo_v1/RESULTS.json`) | 9 | ≤ 0.01 pp |
| positive controls: three books trade, `n = 5` is refused, the loan lowers the NAV | 3 | n/a |
| `python -m research.fast_engine.parity --quick` | 1 | PASS |

The comparison is `fe2_parity`'s `_maxdiff`: the whole NAV path **bitwise** and every reported
scalar (including the nested per-year dicts and the `_legs` rows), skipping the private `_`-keys.
The 0.0 is exact equality, not a tolerance: every extra leg adds `0.0` to a sum and is skipped by
every `un[l] > 1e-12` test, so an inert leg cannot perturb an operation.

The positive controls: a three-leg run really holds three books (`legs C` trades, `legs D` at zero)
and refuses a fifth; a loan run has `margin_usd > 0` and a lower CAGR than the same run without it.

## Speed (main window, warm cache, median of 30 runs)

| run | ms |
|---|---:|
| `fe_sim.simulate` (1 leg) | 0.15 |
| `simulate3`, 2 legs (leg B at zero) | 0.29 |
| `simulate3`, 3 legs (B/C at zero) | 0.32 |
| `simulate3`, 4 legs (B/C/D at zero) | 0.35 |
| `simulate3`, 4 **real** books | 0.69 |
| `simulate3`, 1 leg with a margin loan | 0.45 |

The zero-legged rows are the padding cost of the kernel (the extra legs' branches), the real-book
row is the cost of the account itself. The clean bootstrap (256 paths, C0+K, pre-tax + after tax)
runs 0.22 s on the engine's `fe_boot.boot` against 0.29 s on `fe3_boot.boot3` with four legs
(1.3×). `--run`'s wall clock is in `research/three_leg_kernel_v1/TIMING.json`.

## The framework dispatch — `bridge3.py`

`batch_screen` routes an arm here when `bridge3.needs_kernel3(arm)` — more than two declared legs, a
`margin_spread` loan, or `2x_spy` beside another instrument — and nowhere else (a two-leg arm with no
loan keeps the published `fe2_sim` path). `bridge3` builds the per-leg panels (`leg_series`, with
`MarketData.leg_b_usable`'s availability rule), adds the `2x_spy` option leg on demand (the window's
SPY column on a window; the drawn chunk's SPY sessions in a bootstrap; that window's own `2x` leg on
a window with no SPY series, IC40's documented degeneration), the `(P, N, T)` target panel, the
`session_fold(DTB3 + spread)` margin schedule, and the bootstrap drivers. See the plugin guide
`research/batch_screen/README.md` §1c.

## What this unblocks

* **IC36 A/B/C** — a 3- or 4-instrument inverse-vol blend in the off state, rescaled monthly.
* **IC34-C** — QLD\* with XLU and XLP held at once.
* **IC45-C / IC46-C** — a sleeve on top of production past the 0.98 cap, funded by a cash loan.
* **IC41 A/B/C** — QLD\* plus the S&P 500 2x leg (`2x_spy`) as leg B.

Still blocked (named, not approximated): two books of the *same* instrument (one FIFO queue per
symbol per taxpayer), more than four instruments in one account, a per-session instrument switch
inside one leg, several cash pools/accounts, a per-arm tax regime, and the single-leg execution
options (band_up, slicing, the DD governor, T+1, the FX cost, withholding) on a multi-leg arm.

## Caveats

* The kernel's `N_LEGS = 4` is a capacity, not a requirement: a caller passes exactly as many rows
  as the account holds, so a 1- or 2-leg run costs only the loop bound.
* The margin schedule is the bridge's session-folded `(DTB3 + spread)`; the loan has no other
  terms (no maintenance margin, no forced liquidation, no Reg-T intraday rule). A target that sums
  above the cap simply borrows.
* The `_legs` dict is keyed by position (`A`..`D`), not by instrument name; the instrument keys are
  the arm's `legs` declaration.
* No data is downloaded by this package: it reads only the CSVs other studies committed, so there
  is no `DATA_MANIFEST.json`.
