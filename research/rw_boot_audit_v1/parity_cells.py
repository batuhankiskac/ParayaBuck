"""parity_cells -- the before/after bit-identity evidence for the chunk leg-B fix.

The fix lives in `research/batch_screen/api.py::MarketData.leg_series` and is reached only when a
bootstrap chunk holds a leg B that is a **registered extra-data key** (not a panel leg `1x`/`2x`).
This module makes that claim measurable: it dumps the per-path bootstrap CAGRs
(`B.boot` pre-tax base/calibrated and after-tax progressive/flat at $100k, plus one pooled OOS
cell) of a fixed arm set over several draws, **before** the fix (the committed baseline) and
**after** it, and diffs the two archives.

The arm set spans both classes:

* single-leg and panel-leg arms (`k_control/K`, `r2_control/R2`, `IC38/A` with `leg_b = "1x"`,
  `ib27_2L/A_2L` with `leg_b = "1x"`) -- must be **bit-identical** (`np.array_equal`,
  `equal_nan=True`, NaN pattern included);
* extra-key arms (`IC37/C` -> `ief`, `IC31/A` -> `lqd`, `ib25_RW/A_RW` -> `shy`,
  `IB26_2L/A_2L` -> `ief`) -- the repair is expected to move them; the required direction is that
  no entry goes from finite to non-finite and the number of non-finite entries does not increase.

A draw's arm subset is chosen so that every arm's leg B has a price on the drawn source sessions
(`leg_b_usable`), which is why the default-pool draws carry only the panel/single-leg arms and the
extra-key arms share the `IC37/C` reduced pool.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from research.batch_screen import api, windows
from research.batch_screen import boot as B

#: the arm set (id -> (idea, arm))
ARMS = {
    "k_control/K": ("k_control", "K"),
    "r2_control/R2": ("r2_control", "R2"),
    "IC38/A": ("IC38", "A"),
    "ib27_2L/A_2L": ("ib27_2L", "A_2L"),
    "IC37/C": ("IC37", "C"),
    "IC31/A": ("IC31", "A"),
    "ib25_RW/A_RW": ("ib25_RW", "A_RW"),
    "IB26_2L/A_2L": ("IB26_2L", "A_2L"),
}
#: arms no path of which may move (single-leg, or a panel leg B)
UNAFFECTED = ("k_control/K", "r2_control/R2", "IC38/A", "ib27_2L/A_2L")
#: arms whose chunk leg B is a registered extra key -- the affected class
AFFECTED = ("IC37/C", "IC31/A", "ib25_RW/A_RW", "IB26_2L/A_2L")

SINGLE_OR_PANEL = ("k_control/K", "r2_control/R2", "IC38/A", "ib27_2L/A_2L")
EXTRA_KEY = ("k_control/K", "IC37/C", "IC31/A", "ib25_RW/A_RW", "IB26_2L/A_2L")

IC37_POOL = None  # filled lazily: the IC37/C reduced window's own pool


def _pool_ic37() -> tuple:
    global IC37_POOL
    if IC37_POOL is None:
        arm = api.load_arm("IC37", "C")
        IC37_POOL = tuple(int(v) for v in windows.boot_pool(windows.bars()[0],
                                                            windows.arm_window(arm)))
    return IC37_POOL


def draws() -> tuple:
    return (
        ("default_s20261011_b20", SINGLE_OR_PANEL,
         dict(seed=20261011, block=20, n_pre=8, n_tax=8, chunk=4)),
        ("default_s20261016_b10", SINGLE_OR_PANEL,
         dict(seed=20261016, block=10, n_pre=4, n_tax=4, chunk=4)),
        ("pool_ic37_s20261011_b20", EXTRA_KEY,
         dict(seed=20261011, block=20, n_pre=4, n_tax=4, chunk=2, pool=_pool_ic37())),
        ("pool_ic37_s20261011_b40", EXTRA_KEY,
         dict(seed=20261011, block=40, n_pre=4, n_tax=4, chunk=4, pool=_pool_ic37())),
    )


def _boot_cells() -> dict:
    out = {}
    for label, ids, kw in draws():
        arms = [api.load_arm(*ARMS[i]) for i in ids]
        res = B.boot(arms, capitals=("100k",), modes=("progressive", "flat"), quiet=True, **kw)
        for aid in ids:
            for block in ("pre_base", "pre_calibrated", "tax_progressive", "tax_flat"):
                key = f"{label}|{aid}|{block}"
                v = (res["pre"][block[4:]][aid] if block.startswith("pre")
                     else res["tax"]["100k"][block[4:]][aid])
                out[key] = np.asarray(v, dtype=np.float64)
    arms = [api.load_arm(*ARMS[i]) for i in SINGLE_OR_PANEL]
    res = B.oos_pooled(arms, markets=("M1",), per_market=2, quiet=True)
    for aid in SINGLE_OR_PANEL:
        out[f"oosM1|{aid}|pooled"] = np.asarray(res["pooled"][aid], dtype=np.float64)
    return out


def leg_convention_cells() -> dict:
    """The leg-B chunk convention on the IC37/C pool: spanning columns, read vs own returns.

    Not a parity cell: it *changes* by design (the defect is repaired). It is the fix's own
    evidence -- the number of columns whose return spans a block jump must be 0 after the fix.
    """
    from research.rw_boot_audit_v1 import repro

    c = repro.chunk_path("IC37/C", path=0)
    pan = c["panels"][("2x", 0.0)]
    raw, lvl, own, _idx, _pool = repro._ief_paths(pan, c["pos"], "ief")
    seen = raw[:, 1:] / raw[:, :-1]
    fixed = lvl[:, 1:] / lvl[:, :-1]
    legs = pan.legs["2x"][1]
    mkt = legs[:, 1:] / legs[:, :-1]
    return {
        "columns": int(seen.size),
        "spanning_columns_as_read": int((~np.isclose(seen, own, rtol=1e-12, atol=1e-15)).sum()),
        "spanning_columns_fixed": int((~np.isclose(fixed, own, rtol=1e-12, atol=1e-15)).sum()),
        "max_abs_return_minus_1_as_read": float(np.abs(seen - 1).max()),
        "max_abs_return_minus_1_own": float(np.abs(own - 1).max()),
        "max_abs_return_minus_1_market_leg": float(np.abs(mkt - 1).max()),
        "leg_b_returns_equal_market_convention_after_fix": bool(
            np.allclose(fixed, own, rtol=1e-12, atol=1e-15)),
    }


def dump(path) -> dict:
    cells = _boot_cells()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **cells)
    summary = {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "n_cells": len(cells),
        "leg_convention": leg_convention_cells(),
        "cells": {k: {"n": int(v.size), "n_nan": int(np.isnan(v).sum()),
                      "median": (None if np.isnan(v).all() else float(np.nanmedian(v)))}
                  for k, v in sorted(cells.items())},
    }
    return summary


def compare(before, after, out=None, labels=None) -> dict:
    """Diff two archives: unaffected arms bit-identical, affected arms finite-monotone."""
    b, a = np.load(before), np.load(after)
    keys = sorted(set(b.files) | set(a.files))
    hard, moved_unaffected, affected = [], [], {}
    for k in keys:
        if k not in b.files or k not in a.files:
            hard.append({"cell": k, "why": "missing in one archive"})
            continue
        x, y = np.asarray(b[k]), np.asarray(a[k])
        arm = k.split("|")[1]
        if x.shape != y.shape:
            hard.append({"cell": k, "why": f"shape {x.shape} != {y.shape}"})
            continue
        if arm in UNAFFECTED:
            if not (np.array_equal(x, y, equal_nan=True) and np.array_equal(np.isnan(x), np.isnan(y))):
                moved_unaffected.append({
                    "cell": k, "arm": arm,
                    "max_abs_diff": float(np.max(np.abs(np.nan_to_num(x) - np.nan_to_num(y))))})
            continue
        n_bad_before = int((~np.isfinite(x)).sum())
        n_bad_after = int((~np.isfinite(y)).sum())
        both = np.isfinite(x) & np.isfinite(y)
        rec = {"n_nonfinite_before": n_bad_before, "n_nonfinite_after": n_bad_after,
               "n_changed": int((both & (x != y)).sum()),
               "n_nan_to_finite": int((~np.isfinite(x) & np.isfinite(y)).sum()),
               "n_finite_to_nan": int((np.isfinite(x) & ~np.isfinite(y)).sum()),
               "max_abs_diff_finite": (float(np.max(np.abs(x[both] - y[both]))) if both.any()
                                       else None)}
        affected[k] = rec
        if n_bad_after > n_bad_before:
            hard.append({"cell": k, "why": f"non-finite entries grew {n_bad_before} -> {n_bad_after}"})
    result = {
        "before": (labels[0] if labels else str(before)),
        "after": (labels[1] if labels else str(after)),
        "before_sha256": hashlib.sha256(Path(before).read_bytes()).hexdigest(),
        "after_sha256": hashlib.sha256(Path(after).read_bytes()).hexdigest(),
        "n_cells": len(keys),
        "unaffected_arms": list(UNAFFECTED),
        "affected_arms": list(AFFECTED),
        "unaffected_arms_moved": moved_unaffected,
        "affected_cells_n_changed": int(sum(v["n_changed"] for v in affected.values())),
        "affected": affected,
        "hard_failures": hard,
        "pass": bool(not moved_unaffected and not hard),
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
        print(json.dumps(compare(sys.argv[2], sys.argv[3],
                                 sys.argv[4] if len(sys.argv) > 4 else None), indent=1,
                         sort_keys=True))
    else:
        raise SystemExit("usage: parity_cells.py dump PATH | compare BEFORE AFTER [OUT]")
