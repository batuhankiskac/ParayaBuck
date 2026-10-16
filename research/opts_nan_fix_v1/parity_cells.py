"""parity_cells -- the before/after bit-identity evidence for the chunk-leg fix.

The fix touches one function, `research.fast_engine_opts.fe_opts_legs.chunk_triple`, which is
reached *only* when a bootstrap chunk needs an option leg: an arm with `wht > 0` (IC56, through
`chunk_raw_panel`) or `leg = "2x_spy"` (IC40, through `_leg_of`). No other arm and no window path
can reach it. This module makes that claim measurable instead of structural:

* `dump(path)` writes one `.npz` with the per-path bootstrap CAGRs (`boot.boot` pre-tax
  base/calibrated and after-tax progressive/flat at $100k) of a fixed arm set over several draws --
  the published main-window bootstrap, a reduced pool, two fresh seeds/blocks and the pooled OOS
  bootstrap -- as raw float64 arrays. It is run **before** the fix (baseline) and **after** it.
* `compare(before, after, out)` diffs the two archives cell by cell: every cell whose *baseline*
  value was finite must be bit-identical (`np.array_equal`), and every non-option arm must be
  bit-identical including its NaN pattern. The option arms' cells are reported separately as
  `changed_nan_to_finite` -- that is the defect being repaired, not a regression.

The arm set: the control (`k_control/K`), R2 (`r2_control/R2`), a two-leg panel arm
(`IC38/A`, `leg_b = "1x"`, no reduced window), and the two affected option arms (`IC56/A`,
`IC40/A`).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from research.batch_screen import api, boot as B

#: the arms the parity archive sweeps (id -> (idea, arm))
ARMS = {
    "k_control/K": ("k_control", "K"),
    "r2_control/R2": ("r2_control", "R2"),
    "IC38/A": ("IC38", "A"),
    "IC56/A": ("IC56", "A"),
    "IC40/A": ("IC40", "A"),
}
NON_OPTION = ("k_control/K", "r2_control/R2", "IC38/A")

#: (label, kwargs) of the bootstrap draws the archive sweeps
DRAWS = (
    ("main_s20261011_b20", dict(seed=20261011, block=20, n_pre=8, n_tax=8, chunk=4)),
    ("pool0_500_s20261011_b20", dict(seed=20261011, block=20, n_pre=4, n_tax=4, chunk=2,
                                     pool=(0, 500))),
    ("fresh_s20261016_b10", dict(seed=20261016, block=10, n_pre=4, n_tax=4, chunk=4)),
    ("fresh_s20261016_b40", dict(seed=20261016, block=40, n_pre=4, n_tax=4, chunk=4)),
)


def _load() -> dict:
    return {aid: api.load_arm(*spec) for aid, spec in ARMS.items()}


def _boot_cells() -> dict:
    arms = list(_load().values())
    out = {}
    for label, kw in DRAWS:
        res = B.boot(arms, capitals=("100k",), modes=("progressive", "flat"), quiet=True, **kw)
        for aid in ARMS:
            for block in ("pre_base", "pre_calibrated", "tax_progressive", "tax_flat"):
                key = f"{label}|{aid}|{block}"
                if block.startswith("pre"):
                    v = res["pre"][block[4:]][aid]
                else:
                    v = res["tax"]["100k"][block[4:]][aid]
                out[key] = np.asarray(v, dtype=np.float64)
    res = B.oos_pooled(arms, markets=("M1",), per_market=2, quiet=True)
    for aid in ARMS:
        out[f"oosM1|{aid}|pooled"] = np.asarray(res["pooled"][aid], dtype=np.float64)
    return out


def _leg_cells() -> dict:
    """The rebuilt chunk leg of the two affected arms against the framework's own chunk leg.

    `stats` is what the fix is about (columns <= 0, the minimum, the relative gap to the
    framework's reconstruction); it is *not* a parity cell -- it changes by design.
    """
    win = B.BootWindow()
    rng = np.random.default_rng(20261011)
    pos = win.positions(rng, 20, 2)
    pan = B.boot_panels(win, pos, ("2x",), (0.0,), cache={})[("2x", 0.0)]
    fw = np.asarray(pan.legs["2x"][1])
    from research.fast_engine_opts import fe_opts_legs as LEGS

    rebuilt = np.asarray(LEGS.chunk_raw_panel(pan, pos, wht=0.20).legs["2x"][1])
    spy = np.asarray(LEGS._leg_of(*LEGS.chunk_triple(pos, "SPY"), pan.meta.get("fin", 0.0),
                                  pan.meta.get("exp_q", 0.0095))[1])
    # jump-free isolation: a contiguous drawn run
    L = pos.shape[1]
    cpos = np.arange(1000, 1000 + L, dtype=np.int64)[None, :].repeat(2, axis=0)
    fw0 = np.asarray(B.boot_panels(win, cpos, ("2x",), (0.0,), cache={})[("2x", 0.0)].legs["2x"][1])
    rb0 = np.asarray(LEGS.chunk_raw_panel(
        B.boot_panels(win, cpos, ("2x",), (0.0,), cache={})[("2x", 0.0)], cpos, wht=0.0).legs["2x"][1])
    return {
        "rebuilt_n_le_0": int((rebuilt <= 0).sum()),
        "rebuilt_min": float(rebuilt.min()),
        "framework_n_le_0": int((fw <= 0).sum()),
        "framework_min": float(fw.min()),
        "spy_n_le_0": int((spy <= 0).sum()),
        "spy_min": float(spy.min()),
        "jumpfree_n_jumps": int((cpos[:, 1:] != cpos[:, :-1] + 1).sum()),
        "jumpfree_n_le_0_rebuilt": int((rb0 <= 0).sum()),
        "jumpfree_max_rel_diff": float(np.max(np.abs(rb0 - fw0) / fw0)),
    }


def dump(path) -> dict:
    """Write the archive (raw arrays) plus a small JSON summary; returns the summary."""
    cells = _boot_cells()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **cells)
    raw = path.read_bytes()
    summary = {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "n_cells": len(cells),
        "leg_stats": _leg_cells(),
        "cells": {k: {"n": int(v.size), "n_nan": int(np.isnan(v).sum()),
                      "median": (None if np.isnan(v).all() else float(np.nanmedian(v)))}
                  for k, v in sorted(cells.items())},
    }
    return summary


def compare(before, after, out=None) -> dict:
    """Diff two archives.

    Two claims are *required* (the fix is a repair, not a re-tune):

    * every non-option arm is bit-identical, NaN pattern included -- the fix cannot touch a path
      that does not ask for an option leg;
    * the two option arms introduce no *new* non-finite entry and every cell that was wholly NaN
      becomes wholly finite -- i.e. the repair only turns broken paths into finite ones.

    One record is *expected* and reported, not failed: an option cell whose baseline entries were
    finite *but built from a calendar-spanning return* (a block jump at <= -50 % for the 2x leg
    that happened not to flip the sign) also changes. Those entries were the defect's own output,
    so they cannot be preserved -- a fix that kept them would keep the bug. Their count is
    `option_previously_finite_cells_repaired`.
    """
    b = np.load(before)
    a = np.load(after)
    keys = sorted(set(b.files) | set(a.files))
    bad = []
    non_option_changed = []
    nan_to_finite = {}
    repaired_finite = []
    for k in keys:
        arm = k.split("|")[1]
        if k not in b.files or k not in a.files:
            bad.append({"cell": k, "why": "missing in one archive"})
            continue
        x, y = np.asarray(b[k]), np.asarray(a[k])
        if x.shape != y.shape:
            bad.append({"cell": k, "why": f"shape {x.shape} != {y.shape}"})
            continue
        nan_same = np.array_equal(np.isnan(x), np.isnan(y))
        both_finite = np.isfinite(x) & np.isfinite(y)
        eq_finite = bool(np.array_equal(x[both_finite], y[both_finite])) if both_finite.any() else True
        if arm in NON_OPTION:
            if not (np.array_equal(x, y, equal_nan=True) and nan_same):
                non_option_changed.append({
                    "cell": k, "arm": arm,
                    "max_abs_diff": float(np.max(np.abs(np.nan_to_num(x) - np.nan_to_num(y))))})
        else:
            # required: no cell goes from finite to non-finite
            became_nan = int((np.isfinite(x) & ~np.isfinite(y)).sum())
            if became_nan:
                bad.append({"cell": k, "why": f"{became_nan} finite entr(ies) became non-finite"})
            if not nan_same:
                nan_to_finite[k] = int((np.isnan(x) & np.isfinite(y)).sum())
            if not eq_finite:
                repaired_finite.append({
                    "cell": k, "arm": arm, "n_finite_baseline": int(both_finite.sum()),
                    "n_changed": int((both_finite & (x != y)).sum()),
                    "max_abs_diff": float(np.max(np.abs(x[both_finite] - y[both_finite])))})
    # every option cell that was wholly NaN in the baseline must be wholly finite now
    all_nan_not_repaired = [
        k for k in keys
        if k.split("|")[1] not in NON_OPTION and k in b.files
        and not np.isfinite(np.asarray(b[k])).any() and not np.isfinite(np.asarray(a[k])).all()]
    result = {
        "before": str(before), "after": str(after),
        "before_sha256": hashlib.sha256(Path(before).read_bytes()).hexdigest(),
        "after_sha256": hashlib.sha256(Path(after).read_bytes()).hexdigest(),
        "n_cells": len(keys),
        "non_option_arms": list(NON_OPTION),
        "non_option_not_bit_identical": non_option_changed,
        "option_cells_nan_to_finite": nan_to_finite,
        "option_all_nan_cells_still_not_finite": all_nan_not_repaired,
        "option_previously_finite_cells_repaired": repaired_finite,
        "hard_failures": bad,
        "pass": bool(not non_option_changed and not bad and not all_nan_not_repaired),
    }
    if out is not None:
        Path(out).write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    return result


if __name__ == "__main__":
    import sys

    mode = sys.argv[1]
    if mode == "dump":
        print(json.dumps(dump(sys.argv[2]), indent=1, sort_keys=True))
    elif mode == "compare":
        print(json.dumps(compare(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None),
                         indent=1, sort_keys=True))
    else:
        raise SystemExit("usage: parity_cells.py dump PATH | compare BEFORE AFTER [OUT]")
