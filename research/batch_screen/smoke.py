"""batch_screen.smoke -- run an idea's `targets` without computing anything about performance.

    python -m research.batch_screen.smoke --ideas my_idea
    python -m research.batch_screen.smoke --ideas my_idea --arm my_idea/fast
    python -m research.batch_screen.smoke --keys

Purpose: let a plugin author test that their code *runs* -- shapes, finiteness, the leg, the extra
keys it reads and how much of each series is missing -- **before** anything is simulated. The smoke
computes no return, no NAV, no CAGR and no gate, writes no registry line and reads no result file:
only `targets` is called, on

* a synthetic panel (3 paths, 800 sessions, a random walk: the shape test),
* the real main window (1 path, QQQ sessions 2000-03-09 -> 2026-09-29),
* the real `^GSPC` 1935-1956 OOS window (`M2`: the narrowest inputs),
* the real `XLK` instrument window (the ETF stage), and
* a real block-bootstrap chunk (4 paths, the only `P > 1` panel: the shape most plugins get wrong).

An arm whose inputs do not exist for a window may raise `api.NotApplicable(reason)`; the smoke
reports that as `n/a` and it is never an error. A genuine exception is reported with its traceback
tail and exits non-zero.
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import date, timedelta

import numpy as np

from research.fast_engine import fe_boot
from research.fast_engine.fe_config import BLOCK, SEED, TAX_BOOT_EXT, TAX_BOOT_L

from . import api, extra, windows
from . import boot as B

SYNTH_P = 3
SYNTH_T = 800
BOOT_P = 4


# ----------------------------------------------------------------------------- panels
def synthetic_panel(P=SYNTH_P, T=SYNTH_T, leg=api.DEFAULT_LEG, seed=20261016) -> api.MarketData:
    """A random-walk panel of the window shape, for the pure-shape test."""
    rng = np.random.default_rng(seed)
    days, d = [], date(2000, 1, 3)
    while len(days) < T:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    price = 100.0 * np.cumprod(1 + rng.normal(0.0004, 0.012, (P, T)), axis=1)
    div = np.zeros((P, T))
    rate = np.full((P, T), 0.04)
    return api.panel(days, price=price, div=div, rate=rate, o2=price * 0.998, c2=price, d2=div,
                     window="synthetic", leg=leg)


def real_panels(leg=api.DEFAULT_LEG) -> dict:
    """One real panel per kind of window a plugin is screened on (no simulation)."""
    out = {}
    _days, p, _i0, _i1 = windows.main_panel(0.0, leg=leg)
    out["main"] = p
    _days, p, _start, _i1 = windows.oos_panel("M2", leg=leg)
    out["oos:M2"] = p
    _days, p, _h, _lo, _i0, _i1 = windows.instrument_panel("XLK", leg=leg)
    out["etf:XLK"] = p
    win = B.BootWindow("none")
    pos = fe_boot.draw_positions(np.random.default_rng(SEED), BLOCK, BOOT_P, TAX_BOOT_L,
                                win.npool, TAX_BOOT_EXT)
    out["boot"] = B.boot_panels(win, pos, (leg,), (0.0,))[(leg, 0.0)]
    return out


# ----------------------------------------------------------------------------- one arm
def smoke_arm(idea_id: str, arm_id: str, panels: dict | None = None) -> dict:
    """Call one arm's `targets` on every panel; report shape/leg/extras, never a metric."""
    if panels is None:
        panels = smoke_panels()
    out = {"arm": api.arm_key(idea_id, arm_id), "status": "ok", "panels": {}, "extras": {}}
    t0 = time.perf_counter()
    try:
        mod = api.load_idea(idea_id)
        arm = api.resolve_arm(mod, arm_id)
    except api.NeedsKernel as exc:
        out["status"] = "needs_kernel"
        out["reason"] = str(exc)
        return out
    except api.PluginError as exc:
        out["status"] = "plugin_error"
        out["reason"] = str(exc)
        return out
    for name, p in panels.items():
        before = len(p.extra_log)
        rec = {"shape": [int(p.P), int(p.T)], "sessions": [p.days[0], p.days[-1]]}
        try:
            wants, leg = api.call_targets(mod, p, arm)
        except api.NotApplicable as exc:
            rec.update({"status": "n/a", "reason": exc.reason})
        except api.NeedsKernel as exc:
            rec.update({"status": "needs_kernel", "reason": str(exc)})
            out["status"] = "needs_kernel"
        except Exception as exc:  # noqa: BLE001 -- the point of the smoke: show the author
            rec.update({"status": "error", "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc().strip().splitlines()[-6:]})
            out["status"] = "error"
        else:
            rec.update({"status": "ok", "leg": leg,
                        "wants_min": float(np.min(wants)), "wants_max": float(np.max(wants)),
                        "wants_nonzero": float(np.mean(wants > 0))})
            if leg != arm["leg"]:
                rec["status"] = "error"
                rec["error"] = f"targets changed the leg to {leg!r} (the arm holds {arm['leg']!r})"
                out["status"] = "error"
        for e in p.extra_log[before:]:
            cell = out["extras"].setdefault(e["key"], {"ffill": e["ffill"], "nan_share": {}})
            cell["nan_share"][name] = e["nan_share"]
            cell["ffill"] = cell["ffill"] or e["ffill"]
        out["panels"][name] = rec
    if out["panels"].get("main", {}).get("status") == "n/a":
        out["status"] = "not_applicable_main"
    out["seconds"] = round(time.perf_counter() - t0, 3)
    return out


def smoke_panels(leg=api.DEFAULT_LEG) -> dict:
    """The synthetic panel plus the real ones, with the synthetic panel first."""
    out = {"synthetic": synthetic_panel(leg=leg)}
    out.update(real_panels(leg=leg))
    return out


# ----------------------------------------------------------------------------- extra keys
def keys_report() -> list:
    """Per key: what it resolves to in two windows and how much of it the windows carry."""
    days_main = windows.main_panel(0.0)[0]
    days_m2 = windows.oos_panel("M2")[0]
    rows = []
    for key in extra.keys():
        row = {"key": key}
        for label, w, days in (("main", "main", days_main), ("M2", "M2", days_m2)):
            try:
                name = extra.resolve_series(key, w)
                v = extra.aligned(key, w, days, None, None, (1, len(days)))
                have = np.isfinite(v[0])
                row[label] = {"series": name, "present": float(have.mean()),
                              "first": str(np.asarray(days)[have][0]) if have.any() else None,
                              "last": str(np.asarray(days)[have][-1]) if have.any() else None}
            except Exception as exc:  # noqa: BLE001
                row[label] = {"error": f"{type(exc).__name__}: {exc}"}
        rows.append(row)
    return rows


# ----------------------------------------------------------------------------- report + CLI
def format_arm(r: dict) -> str:
    if r["status"] in ("needs_kernel", "plugin_error"):
        return f"  {r['arm']:28s} {r['status'].upper():16s} {r.get('reason', '')}"
    parts = []
    for name, p in r["panels"].items():
        if p["status"] == "ok":
            parts.append(f"{name} ok[{p['shape'][0]}x{p['shape'][1]}]")
        else:
            parts.append(f"{name} {p['status']}: {p.get('reason') or p.get('error')}")
    line = f"  {r['arm']:28s} {r['status'].upper():16s} " + " | ".join(parts)
    if r["extras"]:
        line += "\n" + " " * 47 + "extras: " + "; ".join(
            f"{k} (ffill={v['ffill']}; NaN " + ", ".join(
                f"{n} {s:.0%}" for n, s in v["nan_share"].items()) + ")"
            for k, v in sorted(r["extras"].items()))
    for name, p in r["panels"].items():
        if p["status"] == "error" and p.get("traceback"):
            line += "\n" + "\n".join(" " * 8 + t for t in p["traceback"])
    return line


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.batch_screen.smoke")
    ap.add_argument("--ideas", help="comma-separated idea ids under research/ideas/")
    ap.add_argument("--arms", help="comma-separated idea/arm pairs (default: every arm)")
    ap.add_argument("--keys", action="store_true", help="print the registered extra-data keys")
    ap.add_argument("--leg", default=api.DEFAULT_LEG, choices=api.LEGS)
    args = ap.parse_args(argv)
    if args.keys:
        print(f"extra-data keys (`research/batch_screen/EXTRA_DATA.json`, {len(extra.keys())} keys, "
              f"{len(extra.series_names())} series; `data.extra(key)`; `ffill=True` to carry "
              "values forward):")
        for r in keys_report():
            for label in ("main", "M2"):
                x = r[label]
                cell = (f"{x['series']} present {x['present']:.0%} {x['first']} -> {x['last']}"
                        if "error" not in x else x["error"])
                print(f"  {r['key']:12s} {label:5s} {cell}")
        return 0
    if not args.ideas:
        ap.print_help()
        return 2
    pairs = []
    if args.arms:
        pairs = [tuple(s.split("/", 1)) for s in args.arms.split(",") if s]
    else:
        for idea_id in [s for s in args.ideas.split(",") if s]:
            try:
                mod = api.load_idea(idea_id)
            except api.PluginError as exc:
                print(f"  {idea_id:28s} PLUGIN_ERROR     {exc}", flush=True)
                return 1
            pairs += [(idea_id, a) for a in mod.ARMS]
    print("smoke: targets() only -- no return, no NAV, no gate, no registry write.")
    panels = smoke_panels(args.leg)
    bad = 0
    for idea_id, arm_id in pairs:
        r = smoke_arm(idea_id, arm_id, panels)
        print(format_arm(r), flush=True)
        bad += 1 if r["status"] in ("error", "plugin_error", "needs_kernel") else 0
    if bad:
        print(f"smoke FAIL ({bad} of {len(pairs)} arms)")
        return 1
    print(f"smoke OK ({len(pairs)} arms)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
