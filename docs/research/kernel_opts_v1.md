# kernel_opts_v1 — single-leg execution options for the Numba engine

**Verdict: built and parity-proven. Engineering only — no idea's performance was computed.**
Nine execution mechanisms and two panel mechanisms, every one inert by default, now reachable from
a `research/ideas/` plugin through the rule it registers. With every option off the kernel is the
engine **bit for bit** (1,260 main-window cells and the bootstrap driver: max |diff| = 0), K's
published anchors reproduce through it to ≤ 0.0001 pp, and each mechanism has a check on a
hand-built synthetic fixture and a pytest test. 29 new tests pass, the framework's 110 still pass,
`python -m research.fast_engine.parity --quick` PASSES.

This unblocks the `kernel_option` arms of IC39, IC40, IC49, IC52, IC53, IC54-C, IC55, IC56, IC57
and IC58. IC54's arms A and B stay blocked on data. Nothing here decides an arm's fate: the ideas
still have to be registered, screened and deflated by the batch framework.

## Design — a copy, and one plumbing point

`research/fast_engine/` is not edited. `research/fast_engine_opts/` holds `sim_one_opts`, a copy of
`fe_sim.sim_one` with the option code inserted at the places the mechanisms act on, plus
`chunk_opts`/`pt_opts` (copies of `batch_screen.boot`'s two numba drivers). The copy — rather than
extending `research/fast_engine2`'s single-leg path — is the choice that minimises conflicts: the
sibling worker owns `fast_engine2` and `batch_screen`'s window handling, and this package can be
reviewed, tested and reverted on its own.

The framework plumbing is one point, through the extension point the framework already had:

* `rules.register_rule(name, band, usd_rule, defer, opts={...})` records a rule's kernel options;
* `api.resolve_arm` returns them as `arm["opts"]` (`{}` for the engine's own rules);
* `stages.run_one`, `boot.boot` and `boot.oos_pooled` route an arm that has them to
  `research.fast_engine_opts.bridge` (`prepare_panel` / `simulate_one` / `chunk` / `pt`);
* `boot`'s grouping key gains `bridge.panel_key(arm)`, so two arms whose legs are built differently
  (different withholding) are not pooled into one group and the paired Δ stays paired.

With no options every call site is the engine's own code path, unchanged. `api.panel` now records
the raw `(open, close, dividend)` triple of each leg (`MarketData.raw`), which is what lets the two
panel mechanisms be rebuilt without re-reading data.

## The options

| # | option (default) | mechanism | idea | check |
|---|---|---|---|---|
| 1 | `band_up` / `band_down` (0.005) | per-direction resize thresholds; A1u and H2 still gate downsizes | IC59 | `check_asym_band` |
| 2 | `min_notional` / `_frac` (0.0) | skip a *permitted* downsize below `max($N, f × NAV)` | IC52 | `check_min_notional` |
| 3 | `slice_frac` / `slice_parts` (0 / 1) | fill a large order in N equal parts on the next opens | IC53 | `check_slicing` |
| 4 | `dd_start` / `dd_floor` (0 / 1) | scale the target by the realised NAV drawdown | IC49 | `check_dd_governor` |
| 5 | `mrate[…]` (off) | interest on a negative cash balance (a Reg-T loan) | IC39 | `check_margin` |
| 6 | `early_mode` (0) + the NYSE calendar | skip an early-close decision / its downsize | IC57 | `check_early_close` |
| 7 | `settle_floor` (−1) | sale proceeds are not spendable until the next session | IC58 | `check_settlement`, `…_tax_sale` |
| 8 | `fx_cost` / `prefund_sessions` (0) | spread on every TRY conversion; pre-fund the liability | IC55 | `check_fx_cost_and_prefund` |
| 9 | `fill_mode` (0) + `fill[…]` | open / midpoint / caller-supplied fill reference | IC54 | `check_fill_reference` |
| 10 | `wht` (panel) | leg dividends net of withholding | IC56 | `check_withholding_panel` |
| 11 | `leg = "2x_spy"` (panel) | the S&P 500 daily-reset 2x leg | IC40 | `check_spy_leg` |

The state each mechanism needs lives inside the kernel (the realised NAV drawdown, the pending
slice, the unsettled proceeds, the TRY reserve, the loan balance, the decision index) — nothing was
pushed into `targets`, which is what makes them execution options rather than signals.

## Parity — options off is the engine

| check | cells | max abs diff |
|---|---:|---:|
| `off_side_by_side`: `fe_sim.simulate` vs `simulate_opts(Opts())`, 4 arms × 3 modes × 5 capitals | 1,260 | **0** |
| …the NAV path, bitwise (`np.array_equal`) | 60 | **0** |
| `off_boot_parity`: `boot._chunk` vs `chunk_opts` with an all-off bundle, 3 arms × 2 models × (2 pre-tax + 2 modes), 2,520-session paths | 24 | **0** |
| `harness.anchors`: K's published pre-tax base/calibrated and the three after-tax windows | 14 | ≤ 0.0001 pp |

The after-tax/liquidation path, the Yİ-ÜFE indexation, the instalment schedule and the FIFO books
are exercised by the same 1,260 cells (three modes × five capitals), and the bootstrap cell is the
driver the gates use. The residual 1e-4 pp in the anchors is `RESULTS.json`'s own six-decimal
rounding, not a modelling difference — the same tolerance the framework's harness uses.

| anchor (through the option kernel) | published | reproduced | Δ pp |
|---|---:|---:|---:|
| K pre-tax CAGR, base / calibrated | 15.7649 / 15.1785 % | 15.7649 / 15.1785 % | ≤ 0.0001 |
| K max DD, base / calibrated | 46.6698 / 46.7948 % | 46.6698 / 46.7948 % | ≤ 0.0001 |
| K after tax $100k, 2000 / 2010 / 2017 | 10.7533 / 15.2354 / 23.9720 % | same | ≤ 0.0001 |
| R2 (C0) after tax $100k, 2000 / 2010 / 2017 | 10.1600 / 14.7059 / 22.8861 % | same | ≤ 0.0001 |

## Speed

One main-window run (2000-03-09 → 2026-09-29: 6,679 measured sessions inside a 6,932-session panel, warm numba cache), median of 30:

| run | ms |
|---|---:|
| `fe_sim.simulate` (engine) | 0.11 |
| `simulate_opts(Opts())` | 0.12 |
| …with `band_up = 0` | 0.13 |
| …with `dd_start = 0.20` | 0.22 |
| …with `slice_parts = 3` | 0.12 |
| …with a margin rate array | 0.12 |
| the NYSE calendar's flags for that 6,932-session window | 0.69 (once per window, not per run) |

The option code costs 0-0.1 ms per run, i.e. nothing against a stage: the mechanisms only add
branches the kernel would otherwise skip, and the arrays a mechanism reads are built once per
(window, arm) by `bridge.bundle_for`. The whole `harness` (the engine's own parity subprocess, the
option parity both ways, the anchors and the twelve mechanism checks) is **20.7 s wall**, of which
~12 s is `research.fast_engine.parity --quick`; `parity_opts` alone is 1.4 s and the twelve
mechanism checks 0.5 s. `HARNESS_OPTS.json` is byte-identical over repeated runs (the wall clock is
printed, not stored).

## What is now expressible, and what stays blocked

| idea | status | how a plugin asks for it |
|---|---|---|
| IC59 asym band | **expressible** | `opts={"band_up": 0.0}` / `{"band_up": 0.0025}`; arm C also `usd_rule=False` |
| IC52 min notional | **expressible** | `opts={"min_notional": 250.0}` / `1000.0` / `{"min_notional": 250.0, "min_notional_frac": 0.0005}` |
| IC53 slicing | **expressible** | `opts={"slice_frac": 0.25, "slice_parts": 3}` / `{0.50, 5}` / `{0.0, 2}` |
| IC49 DD governor | **expressible** | `opts={"dd_start": 0.20, "dd_floor": 0.25}` (+ `dd_upsize_only`); `targets` must return the *un-capped* target |
| IC39 margin QQQ | **expressible** | `opts={"margin_spread": 0.015}` + `leg="1x"` + a 2× target (B caps at 1.6, C stresses +2 pp) |
| IC57 early close | **expressible** | `opts={"early_mode": 1}` / `{"early_mode": 2}`; B is the engine (see below) |
| IC58 T+1 funding | **expressible** | `opts={"settle_floor": 0.0}` / `0.01` — arms A and C coincide single-leg |
| IC55 FX cost / schedule | **expressible** | `opts={"fx_cost": 0.005}` / `0.01` / `{"prefund_sessions": 63}` |
| IC56 withholding | **expressible** | `opts={"wht": 0.20}` / `0.15` (arm C's credit needs the tax model) |
| IC40 S&P 2x leg | **expressible** | `"leg": "2x_spy"`; arm C also needs the vote on the S&P path (targets) |
| IC54 delayed intraday fill | **partly — arm C only** | `opts={"fill_mode": 1}` (the open-close midpoint). Arms A/B need a 30-minute series: Yahoo's 30-minute history reaches ~730 days and the Alpaca SIP feed is not available offline → `NEEDS_DATA`. The generic mechanism (`fill_mode=2` + a `fill` array) is implemented and tested, so only the data is missing. |

Findings the ideas' authors should read before registering arms (all in
`research/fast_engine_opts/README.md`, §"Semantics worth knowing"):

* **IC49's governor must be given the un-capped target.** The plugin must return
  `on_eff × factor × min(1, 0.35/vol20)` without `sized_wants`' 0.98 cap; the kernel applies the cap
  exactly as the engine does. The dead zone is exact: with a path that never draws down 20 % the
  governor is *bit-identical* to production.
* **IC57-B is the engine.** The engine already decides on the previous full session's close and
  fills at the early-close session's open, which is what arm B asks for; arm A (skip the decision)
  and arm C (skip only the downsize) are the two behaviours the option adds. Arm A's effect is a
  one-session decision lag; arm C's is a deferred downsize (not a cancelled one).
* **IC58's arms A and C coincide** in a single-leg account: only a same-session sale's proceeds can
  fund a same-session buy. The real trigger is an instalment's funding sale followed by an
  up-resize in the same session (`check_settlement_tax_sale` builds exactly that).
* **IC55's pre-funding uses idle cash only.** A fully invested account reports `prefund_usd = 0`,
  so this arm can never force a sale and never changes the tax base; making it sell would realize
  gains, which is a different mechanism (a plugin can lower the target early instead).
* **IC52 at $100k is inert** for arm A's $250 floor ($0.5 pp of NAV = $500) — the floor bites in
  proportion to account *size*, so its stage-1/2 evidence will come from the small-capital and
  21-ETF cells. Arm C's `0.05 % of NAV` only exceeds $250 above a $500k NAV.
* **The early-close calendar is the published NYSE rule**, corroborated against the engine's own
  QQQ bars: every derived date 2000-2026 is a session the data carries and the derived dates'
  median volume is 0.40 of a session's against 1.00 for all sessions. The ceiling is that it is a
  rule, not an authoritative day-by-day list (the July 3 dates in thin holiday weeks are the
  ambiguous ones); a wrong flag costs one session of decision lag a few times a year.

## Artifacts

* `research/fast_engine_opts/` — `HARNESS_OPTS.json` (the run's checks, byte-identical across
  runs), `PARITY_OPTS.json`, `README.md` (the option table, the per-idea request recipes, the
  semantics), `checks.py`, `harness.py`, `tests/`.
* No data was downloaded and no file in `data/historical/` was added: the package reads only the
  CSVs other studies committed (`stress_robustness_v1_daily.csv` for the main window, the SPY
  column and the volume corroboration). Hence no `DATA_MANIFEST.json`.
* The framework's own tests (110) and `parity --quick` still pass, and `research/fast_engine/`
  is untouched (`PARITY.json` restored byte for byte by the harness).
