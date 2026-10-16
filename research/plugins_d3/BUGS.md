# Framework bugs found while writing the plugins_d3 idea plugins

Not fixed here: the task forbids editing `research/batch_screen/` and `research/fast_engine*/`.

## B1 — `leg = "2x_spy"` cannot be constructed outside the bootstrap (blocks IC40)

Reproduce:

```sh
PYTHONPATH=. ~/.venvs/pb-research/bin/python -c \
  "from research.batch_screen import windows; windows.main_panel(0.0, leg='2x_spy')"
# KeyError: '2x_spy'
```

`api.panel()` builds only the `2x` and `1x` legs, `MarketData.__post_init__` then reads
`self.legs[self.leg][1]` (api.py) — so a panel handed `leg="2x_spy"` raises while it is being
built, before anything can add the leg.  The option leg is added by
`research.fast_engine_opts.bridge.prepare_panel`, which is only called *after* the panel exists
(`stages.run_one`) and which is passed a panel that was already built.

Call sites that build a panel with the arm's own leg and therefore raise for `2x_spy`:

* `stages.stage1` (2×), `stages.stage2_tax_windows`, `stages.stage2_oos`,
  `stages.stage2_instruments`, `stages.stage2_rolling` — every one calls
  `windows.{main,oos,instrument}_panel(..., leg=arm["leg"])`;
* `smoke.real_panels` — same, so `smoke --ideas <idea>` cannot even reach `targets` for such an arm.

The bootstrap path is the control for what the fix should look like: `boot.boot` builds its panels
with `kernel_opts.base_leg(g[0])` (`"2x_spy" -> "2x"`) and then calls `kernel_opts.prepare_panel`,
which adds `p.legs["2x_spy"]` and selects it — that is exactly `bridge.prepare_panel`'s job, and it
works.  Proposed fix (one expression, six call sites + `smoke.real_panels`): pass
`research.fast_engine_opts.bridge.base_leg(arm["leg"])` where `windows.*_panel` is called, and let
`run_one`'s existing `prepare_panel` add the option leg.

Measured impact: an IC40 arm (`"leg": "2x_spy"`) is unrunnable as committed — it never reaches
`targets`, and a batch dies with the `KeyError` in stage 1.  The mechanism itself is fine: with a
panel built from the `2x` leg, `prepare_panel` + the module's `targets` produce the intended
weights on every panel kind (`research/plugins_d3/SUMMARY.txt` records the check), and
`research/fast_engine_opts/checks.py::check_spy_leg` passes.

Because of this, IC40's three arms are registered as declared but refused (`needs_kernel`, reason in
`research/ideas/IC40.py`); the module keeps the arm implementation, so the arms run unchanged once
the call sites pass `base_leg`.

## B2 — `smoke` counts a declared-refused arm as a failure (informational)

`smoke.main` exits 1 when any arm of the requested idea reports `needs_kernel`/`plugin_error`
(`bad += 1 if r["status"] in ("error", "plugin_error", "needs_kernel")`), and a module-level
`NEEDS_KERNEL` makes *every* arm of a module report that status.  That is how
`research/ideas/IC59.py` has behaved since it was committed, so an idea that declares one arm
refused (IC40, IC54) always "FAIL"s the smoke even when its other arms are OK.  Reported, not
changed: a module-level declaration is the documented way to register an arm as refused.
