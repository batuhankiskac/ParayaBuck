# research/fast_engine_opts — single-leg execution options for the Numba engine

`research/fast_engine` is **not edited**. This package is a `research/<study>/`-style copy of its
session kernel with the execution mechanisms that the idea bank's `kernel_option` arms need, every
one of them inert by default, plus the plugin plumbing that lets a `research/ideas/` plugin request
one **through the rule it registers** (the framework's existing extension point).

```
fe_opts.py          the option set (`Opts`), its packing, the session fold of a rate
fe_opts_sim.py      `sim_one_opts` (fe_sim.sim_one + the hooks) and `simulate_opts`
fe_opts_boot.py     `chunk_opts` / `pt_opts`: `batch_screen.boot`'s two numba drivers, with options
fe_opts_calendar.py the NYSE early-close (13:00 ET) calendar, derived from the exchange's rules
fe_opts_legs.py     the two *panel* mechanisms: dividend withholding, a second underlying
bridge.py           what `batch_screen` calls: an arm's option bundle and the two dispatchers
parity_opts.py      options-off parity (pre-tax, after-tax, bootstrap) + the mechanism table
checks.py           one check per option, on hand-built synthetic fixtures
harness.py          the single entry point (engine parity, option parity, K's anchors, the checks)
HARNESS_OPTS.json   the harness's artifact (deterministic: two runs are byte-identical)
PARITY_OPTS.json    the last standalone parity run
tests/              pytest: parity, one test per mechanism, the calendar, the plumbing
```

```sh
R=~/.venvs/pb-research/bin/python
PYTHONPATH=. $R -m research.fast_engine_opts.harness            # everything; wall clock at the end
PYTHONPATH=. $R -m research.fast_engine_opts.parity_opts        # just the parity cells
PYTHONPATH=. $R -m research.fast_engine_opts.checks             # the mechanism table as JSON
research/fast_engine_opts/tests/run.sh                          # pytest
```

## Why a copy, and what "options off" means

The engine's own README says it: "Extending the *rule family* (a new mechanism, not a new
combination) means editing `sim_one` and adding a parity cell — the honest cost of having the
state machine inside a kernel." The campaign protocol makes that a copy inside `research/<study>/`
plus a parity cell, and `research/fast_engine2` was already being extended for two-leg routing by
another worker, so this package takes the copy (it also lets this package be reviewed, tested and
reverted on its own).

`sim_one_opts` is `fe_sim.sim_one` verbatim with the option code inserted at the places the
mechanisms act on. Nothing in the production path was re-ordered or re-associated, and every
option branch is guarded by a threshold that `Opts()` leaves at its production value:

* **`parity_opts.off_side_by_side`** — `fe_sim.simulate` vs `simulate_opts(Opts())` on the QQQ main
  window, 4 arms × 3 modes × 5 capitals: **1260 cells, max |diff| = 0, the NAV path bitwise
  identical** (not a tolerance — `np.array_equal`).
* **`parity_opts.off_boot_parity`** — the framework's `boot._chunk` vs `fe_opts_boot.chunk_opts`
  with an all-off bundle, on the same drawn paths and target panels, 3 arms × 2 financing models ×
  (2 pre-tax + 2 modes × 1 capital) over the window's own 2,520 sessions: **max |diff| = 0**, NaN
  patterns included (a bootstrap path can ruin the account, and both sides must ruin it in the same
  cells). The cell initialises its outputs with a sentinel and refuses to compare a cell that was
  never written, and it draws a *full* `TAX_BOOT_L`-column path: a shorter draw with the full
  calendar's `i0 -> i1` reads past the end of the price arrays (numba does not bounds-check) and
  produced garbage cells that looked like a 1 % flake. `python -m research.fast_engine_opts.harness`
  is also the fastest way to re-check it 200 times in a row.
* **`harness.anchors`** — K's published cells (pre-tax base/calibrated, the three after-tax
  windows at $100k, R2 as the reference row) reproduced **through** `simulate_opts`, ≤ 0.01 pp of
  `research/band_h2_combo_v1/RESULTS.json`.

## The options

`Opts` field, unit, and the mechanism it switches on. `off` is the production value, and with all
of them at `off` the kernel is the engine. The last column names the idea of
`docs/research/idea_bank_v2.json` the option exists for (each entry's arms in
`git show main:docs/research/idea_bank_v2.json`).

| option | default | mechanism | idea |
|---|---|---|---|
| `band_up` | 0.005 | the up-resize threshold (production's 0.5 pp is symmetric) | IC59 |
| `band_down` | 0.005 | the down-resize threshold; A1u and H2 still gate downsizes on top | IC59 |
| `min_notional` | 0.0 | skip a *permitted* downsize below this USD notional | IC52 |
| `min_notional_frac` | 0.0 | ... combined as `max(min_notional, frac × NAV)` | IC52 |
| `slice_frac` | 0.0 | an order larger than this fraction of NAV is sliced | IC53 |
| `slice_parts` | 1.0 | into this many equal parts, on the next sessions' opens | IC53 |
| `dd_start` | 0.0 | the realised NAV drawdown where the governor starts to bite | IC49 |
| `dd_floor` | 1.0 | the governor's `sigma_budget` floor, as a fraction of the vol target | IC49 |
| `dd_upsize_only` | 0 | the governor caps the target; it never forces a downsize | IC49-C |
| `fx_cost` | 0.0 | the spread charged on every TRY conversion (tax payments, liquidation) | IC55 |
| `prefund_sessions` | 0.0 | convert the liability into a TRY reserve this many sessions early | IC55-B |
| `settle_floor` | −1.0 | ≥0 turns T+1 settlement on; the value is the deficit allowed (0 = cash) | IC58 |
| `early_mode` | 0 | 1 = skip the decision on an early close, 2 = skip only its downsize | IC57 |
| `fill_mode` | 0 | 0 = the open (production), 1 = the session's `(open + close)/2`, 2 = a supplied array | IC54 |

Four options read a **per-session array** instead of a scalar (an empty array = off, which is what
the bridge builds when nothing asks for it):

| array | shape | mechanism | idea |
|---|---|---|---|
| `mrate` | `(T,)` | the loan accrues `cash × mrate[i]` interest while the cash balance is negative | IC39 |
| `early` | `(T,)` bool | which sessions are early closes (`fe_opts_calendar.flags`) | IC57 |
| `fill` | `(T,)` | the fill reference itself (`fill_mode = 2`) | IC54-A/B |
| `wvol` | `(T,)` | the leg's `vol20` panel row, the governor's sizing input | IC49 |

Two mechanisms are **panel** construction, not kernel logic, so they live in `fe_opts_legs` and are
built from the panel's recorded raw triple (`MarketData.raw`, which `api.panel` now keeps):

| request | mechanism | idea |
|---|---|---|
| rule opt `wht=0.20` | the leg is rebuilt from dividends net of the withholding rate | IC56 |
| spec `"leg": "2x_spy"` | the S&P 500 daily-reset 2x leg (from the window's own SPY column, or the drawn chunk's SPY sessions) | IC40 |

## Requesting an option from a plugin

Options travel the way the framework already travels execution variants: **through the arm's
`rule`**. The plugin registers a rule at import time with the option values and sets `"rule"` to
its name:

```python
# research/ideas/ic59_asym.py
from research.batch_screen import rules
from research.fast_engine import fe_signals

rules.register_rule("kopt_band_up0", band=True, usd_rule=True, defer=True,
                    opts={"band_up": 0.0})          # IC59-A: K's downsizes, immediate upsizes

ARMS = {
    "A": {"rule": "kopt_band_up0", "leg": "2x", "cash_yield": "none"},
    "B": {"rule": "kopt_band_up25", "leg": "2x", "cash_yield": "none"},
    "C": {"rule": "kopt_band_up0_try", "leg": "2x", "cash_yield": "none"},
}
```

```python
rules.register_rule("kopt_margin", band=True, usd_rule=True, defer=True,
                    opts={"margin_spread": 0.015})  # IC39-A: a loan at DTB3 + 1.5 %
```

What a spec can ask for, per idea (parameters frozen by the plugin's `spec_hash`, as always):

| idea | arm | the plugin registers / declares |
|---|---|---|
| IC59 | A / B / C | `opts={"band_up": 0.0}` / `0.0025` / `{"band_up": 0.0}` with `usd_rule=False` (the indexed-TRY gain fraction) |
| IC52 | A / B / C | `opts={"min_notional": 250.0}` / `1000.0` / `{"min_notional": 250.0, "min_notional_frac": 0.0005}` |
| IC53 | A / B / C | `opts={"slice_frac": 0.25, "slice_parts": 3}` / `{0.50, 5}` / `{"slice_frac": 0.0, "slice_parts": 2}` |
| IC49 | A / B / C | `opts={"dd_start": 0.20, "dd_floor": 0.25}` / `{0.15, 0.15}` / A + `dd_upsize_only=1`; `targets` returns the **un-capped** `min(1, 0.35/vol)` target (the kernel applies the 0.98 cap, as `sized_wants` would) |
| IC39 | A / B / C | `opts={"margin_spread": 0.015}` (A/B) and `0.035` (C); `targets` emits `2 × vt` on `leg="1x"` (B caps at 1.6) |
| IC57 | A / B / C | `opts={"early_mode": 1}` / `{}` (B is the control: the engine already decides on the previous full session's close and fills in the early-close session) / `{"early_mode": 2}`; every window's `early` flags come from the calendar, the plugin does nothing |
| IC58 | A / C / B | `opts={"settle_floor": 0.0}` (A and C coincide in a single-leg account, see below) / `0.01` |
| IC55 | A / C / B | `opts={"fx_cost": 0.005}` / `0.01` / `{"prefund_sessions": 63}` |
| IC54 | C | `opts={"fill_mode": 1}` (A/B need an intraday series: `fill_mode=2` + a `fill` array, which no in-repo data carries) |
| IC56 | A / B / C | `opts={"wht": 0.20}` / `0.15` / `0.20` (C's foreign-tax-credit reporting is an after-tax-model change, not a kernel one) |
| IC40 | A / B / C | `"leg": "2x_spy"` (A), also for the early leg (B), C also needs the vote computed on the S&P path, which is `targets`' business |

The framework's plumbing is one point: `rules.register_rule(..., opts=...)` records the options,
`api.resolve_arm` returns them as `arm["opts"]`, and `stages.run_one`, `boot.boot` and
`boot.oos_pooled` route an arm that has them to `bridge` (`prepare_panel` / `simulate_one` /
`chunk` / `pt`). With no options every call site is the engine's own code path, unchanged.

`bridge.panel_key(arm)` goes into the bootstrap's grouping key: two arms that build their legs
differently (different `wht`) are not the same group, so the paired `Δ` stays paired.

## Semantics worth knowing before reading a result

* **The governor takes an un-capped target.** With `dd_start > 0` the plugin must return
  `on_eff × factor × min(1, 0.35/vol20)` *without* the 0.98 cap; the kernel applies the cap exactly
  as `fe_signals.sized_wants` does. It is inert (bit-identical to production) while the NAV
  drawdown is under `dd_start` — the dead zone is exact, see `check_dd_governor`.
* **Slicing freezes the order, not the target.** A sliced order fills in `parts` equal pieces at
  the following sessions' opens and the session's own target logic is suspended while it drains
  (otherwise the next session would simply re-place the whole order). The gate (A1u/H2) ran once,
  at the trigger.
* **A settled trade.** `settle_floor = 0` caps a buy at the cash it has minus the sale proceeds
  credited *this* session; `settle_floor = f` allows the balance to go `f × NAV` negative. A
  clipped buy is not cancelled: the target is unchanged, so the next session's drift re-attempts it
  — that is the T+1 cost, one session of exposure.
* **Pre-funding uses idle cash only.** The reserve is converted out of a positive cash balance; an
  account that is fully invested cannot pre-fund (the run reports `prefund_usd = 0`), so this arm
  never forces a sale and never changes the tax base. Making it sell would realize gains — a
  different mechanism, and a plugin can express the intent by lowering the target early instead.
* **Withholding is a leg construction.** `wht` nets the dividend in the engine's own 2x recurrence;
  a price-only window (the OOS indices, the 21 ETFs) has no dividend, hence no effect there.
* **`2x_spy` on a window without SPY** (any OOS index, any of the 21 ETFs) resolves to a copy of
  that window's own `2x` leg — IC40's documented degeneration, since those windows' on-state leg is
  already 2x of their own index.
* **The early-close calendar is the published rule**, not an authoritative day-by-day list: it is
  corroborated against the engine's own QQQ bars (every derived date is a session the data carries,
  and the derived dates' median volume is 0.40 of a session's against 1.00 for all sessions), but a
  handful of July 3 dates sit in thin holiday weeks where the volume signature is ambiguous. A
  wrong flag costs one session of decision lag a few times a year.
* **Single-leg only.** An arm cannot combine `opts` with `leg_b` (`api.resolve_arm` refuses it);
  two legs need `fe2_sim`.
* **The IC58 arms A and C coincide** in a single-leg account: only same-session sale proceeds can
  fund a same-session buy, which is exactly when A defers.
* **What is still blocked.** IC54's arms A and B (a 30-minute intraday fill reference) need an
  intraday series the repository does not carry — Yahoo's 30-minute history reaches back only ~730
  days and the Alpaca SIP feed is not available offline; the mechanism (`fill_mode = 2` + a `fill`
  array) is implemented and tested. IC40's optional SSO cross-check (not one of its arms) would
  need a Yahoo chart download; the arms themselves run on the in-repo SPY column. No data was
  downloaded for this package: it reads only `data/historical/**` that the other studies committed.
