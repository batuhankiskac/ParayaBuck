# framework_v2 — pre-registration (the framework mechanisms; committed before any arm uses them)

This branch is an **engineering** task, not an idea: it adds mechanisms to the batch-screen
framework (`research/batch_screen/`), an option copy of the two-leg kernel
(`research/fast_engine2/fe2_opts.py`) and their parity cells. **No idea's performance is
computed**, so this file pre-registers the *mechanisms and their rules* — the things a later idea
study must take as given — and the gates that make the delivery honest.

Pre-registered here, before any arm uses any of it (§6.2 of `research/batch_screen/
PREREGISTRATION.md` is the normative copy of the same rules; this file states the delivery):

## 1. What is delivered

1. **Reduced windows** (`spec["start"]` / `spec["inputs"]`): an arm may declare the window it is
   evaluated on; stage 1, stage 2 and both block bootstraps then run on it, with K **re-simulated on
   the same window** as control so every Δ stays paired. `"auto"` resolves to the later of the
   published start and the first session every declared input covers, plus one session; a window
   shorter than **10 calendar years** is `NotApplicable`; the after-tax windows are clipped up to the
   window's start, the January rolling starts that begin before it are dropped, and the block
   bootstrap draws from the window's own pool at the published seed/block/rng order. The record, the
   scoreboard and `DEFLATION.json` carry the window (`win.tag`, `window_tags`).
2. **The panel column calendar** (`MarketData.column_days`): the per-column day label, which fixes
   the reported `leg_b_usable` IndexError on a bootstrap chunk whose extra-key leg B has an interior
   NaN and makes `year`/`month` `(T,)` like every other panel array.
3. **Two-leg fill options** (`spec["fill_knobs"]` / `spec["kernel_scope"]`, kernel copy
   `research/fast_engine2/fe2_opts.py`): `band_score` (the gain fraction K's widths key on:
   `"usd"`/`"indexed"`/`"max"`), `band_step4_pp` (a fourth step above a fixed 0.15 fraction),
   `route_cap_pp` (downsize routing between the two books by indexed gain), `loss_net_frac` +
   `loss_net_min_ytd_try` (loss netting with a year-to-date gate, taxed modes only), and
   `kernel_scope` (`"all_stages"` | `"after_tax_stage_only"`). Every option **off** reproduces the
   published kernel bit for bit; `fe2_sim`/`fe2_boot` are untouched except for one pure refactor
   (`simulate2`'s post-processing extracted to `_outputs`, defaults preserving behaviour).
4. **Documentation of what remains `NEEDS_KERNEL`**, with the reason: a per-session instrument
   switch (no longer needed — the off-state ideas are a *fixed* leg B whose weight varies), three
   instruments in one account (IC36, IC34 arm C), a cash loan (IC39), a second leg *family* (IC40),
   two books of the same instrument (IB38).

## 2. Data and mechanics (fixed)

* No new data: every input is already committed. `DATA_MANIFEST.json` lists the files the new code
  touches with their SHA-256, the series each provides and the provenance of the extra series.
* The engine (`research/fast_engine/`) is not modified. The one edit outside the new files is the
  `fe2_sim.py` extraction named above; `fe2_parity` (128 cells) and
  `research/two_leg_kernel_v1/parity_b.py` pin the reference implementations.
* Seeds, blocks, path counts, capitals and gates are exactly the published ones (§1–§5 of the
  framework's pre-registration); the reduced window changes *which sessions* are drawn, never the
  rng order or the counts.

## 3. Gates for the delivery (all fixed in advance)

* `research/fast_engine.parity --quick` **PASS**, and the harness reproduces K's published anchors
  (pre-tax base/calibrated m=1/m=2, max DD, turnover, the three after-tax windows, OOS, ETFs,
  rolling starts, B1/B2 levels) — the framework's own gate set, unchanged.
* `research.fast_engine2.fe2_parity` (128 cells) and `research.two_leg_kernel_v1.parity_b` PASS:
  the refactor changed nothing.
* `research.fast_engine2.fe2_opts_parity` PASS: **option off == the current kernel bit for bit**
  (`max_abs_diff == 0.0`, main window and bootstrap), and each option on moves the numbers with the
  specified structure.
* The framework's harness passes end to end (115 checks) and the pytest suite passes (128 tests).
* Two consecutive `--run` invocations produce a byte-identical `RESULTS.json`.
* An arm that declares none of the new keys takes the published code path byte for byte: the
  default bootstrap pool is bit-identical to `pool=None`, and `control_*.json`/`RESULTS.json` of
  the earlier batches are unchanged by these edits (they are not re-run here).

## 4. Selection rule and reporting

There is no candidate and no declaration: the branch's output is the mechanism, its tests, its
parity cells and its documentation. `RESULTS.json` holds the check set; `SUMMARY.txt` the verdicts,
the wall clock and the caveats; `docs/research/framework_v2.md` the reference text.

## 5. Caveats, stated before the fact

* A reduced-window arm's Δ is paired against its own window's K; arms on different windows do not
  share a pool, so their deflation p-values are not the published single-pool p-values (the
  comparison across arms is between *advantages*, which is the quantity the screening decision uses).
* A two-leg sleeve that realises a large TRY-indexed gain can leave an advance tax the account
  cannot fund on a few drawn paths; the after-tax CAGR of such a path is NaN. The framework leaves
  those arms out of the deflation (`excluded_non_finite`) instead of making every other arm's
  p-value NaN, and such an arm can never be declared.
* The `route_cap` router moves *units*, so a routed fill's cash differs from the un-routed fill by
  the two instruments' price ratio; the cap bounds both legs' deviation from their own target
  weight. The band widths a routed leg is judged on are the ones its routed quantity earns.
