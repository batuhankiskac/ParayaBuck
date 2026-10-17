"""batch_screen.smoke -- run an idea's `targets` without computing anything about performance.

    python -m research.gate_power_v2.smoke --ideas my_idea
    python -m research.gate_power_v2.smoke --ideas my_idea --arm my_idea/fast
    python -m research.gate_power_v2.smoke --keys

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
from research.fast_engine.fe_config import BLOCK, SEED, TAX_BOOT_I0

from . import api, extra, windows
from . import boot as B

SYNTH_P = 3
SYNTH_T = 800
BOOT_P = 4

_PANELS = {}


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


def _prepared(p, arm, pos=None) -> api.MarketData:
    """`p` prepared for the arm: `bridge.prepare_panel` when the arm asks for something the panel's
    own construction does not have (the `2x_spy` option leg, the `wht` leg), `p` itself otherwise.

    Exactly what `stages.run_one` does for a real run, so the smoke sees the panel the arm will.
    """
    if arm is None or not (arm.get("opts") or arm.get("leg") == api.LEG_SPY):
        return p
    from research.fast_engine_opts import bridge

    return bridge.prepare_panel(p, arm, pos=pos)

def real_panels(leg=api.DEFAULT_LEG, win=None, arm=None) -> dict:
    """One real panel per kind of window a plugin is screened on (no simulation).

    `win` is the main window the arm declared (`windows.arm_window`): the `main` panel is the
    window's own panel (a reduced window only changes the sessions the *engine* reads), and the
    bootstrap chunk is drawn from `win`'s own pool -- exactly what the arm will see.

    Every panel is built from `bridge.base_leg(leg)` (an option leg is added on top of the window's
    own `2x` leg, never built by `api.panel`) and prepared for `arm` when one is given, so a
    `2x_spy` arm reaches `targets` here as it does in a real run (research/plugins_d3/BUGS.md B1);
    the drawn positions `pos` are handed to `prepare_panel` for the chunk panel, the same
    argument `boot.boot` passes.
    """
    from research.fast_engine_opts import bridge

    leg = bridge.base_leg(leg)
    out = {}
    _days, p, _i0, _i1 = windows.main_panel(0.0, leg=leg)
    out["main"] = p
    _days, p, _start, _i1 = windows.oos_panel("M2", leg=leg)
    out["oos:M2"] = p
    _days, p, _h, _lo, _i0, _i1 = windows.instrument_panel("XLK", leg=leg)
    out["etf:XLK"] = p
    pool = None if win is None else windows.boot_pool(windows.bars()[0], win)
    bw = B.BootWindow("none", pool)
    pos = bw.positions(np.random.default_rng(SEED), BLOCK, BOOT_P)
    out["boot"] = B.boot_panels(bw, pos, (leg,), (0.0,))[(leg, 0.0)]
    return {name: _prepared(p, arm, pos if name == "boot" else None)
            for name, p in out.items()}


# ----------------------------------------------------------------------------- one arm
def smoke_arm(idea_id: str, arm_id: str, panels: dict | None = None, leg: str | None = None) -> dict:
    """Call one arm's `targets` on every panel; report shape/leg/extras, never a metric.

    The arm's declared window (`spec["start"]`) is resolved first: an arm that declares one smokes
    on its own window (and only its own bootstrap pool), which is the point of the reports below.
    `leg` overrides the panel's leg (the CLI's `--leg`); by default it is the arm's own.
    """
    out = {"arm": api.arm_key(idea_id, arm_id), "status": "ok", "panels": {}, "extras": {},
           "win": None}
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
    try:
        win = windows.arm_window(arm)
    except api.NotApplicable as exc:
        out["status"] = "not_applicable_window"
        out["reason"] = exc.reason
        return out
    out["win"] = win.as_dict()
    if panels is None:
        panels = smoke_panels(leg or arm["leg"], win, arm)
    for name, p in panels.items():
        before = len(p.extra_log)
        rec = {"shape": [int(p.P), int(p.T)], "sessions": [p.days[0], p.days[-1]]}
        try:
            t = api.call_targets(mod, p, arm)
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
            rec.update({"status": "ok", "leg": t.leg, "leg_b": t.leg_b,
                        "wants_min": float(np.min(t.wants)), "wants_max": float(np.max(t.wants)),
                        "wants_nonzero": float(np.mean(t.wants > 0)),
                        "two_leg": bool(t.two_leg)})
            if t.leg != arm["leg"]:
                rec["status"] = "error"
                rec["error"] = f"targets changed the leg to {t.leg!r} (the arm holds {arm['leg']!r})"
                out["status"] = "error"
            elif t.two_leg:
                if p.P > 1:                       # a chunk's engine range is fixed (fe_boot)
                    j0, j1 = TAX_BOOT_I0, p.T - 2
                elif name == "main":              # the arm's own declared window
                    j0, j1 = win.i0, p.T - 1
                else:                             # the OOS/ETF convention
                    j0, j1 = 253, p.T - 1
                ok, why = p.leg_b_usable(t.leg_b, j0, j1)
                rec["leg_b_usable"] = bool(ok)
                rec["leg_b_reason"] = why
        for e in p.extra_log[before:]:
            cell = out["extras"].setdefault(e["key"], {"ffill": e["ffill"], "nan_share": {}})
            cell["nan_share"][name] = e["nan_share"]
            cell["ffill"] = cell["ffill"] or e["ffill"]
        out["panels"][name] = rec
    if out["panels"].get("main", {}).get("status") == "n/a":
        out["status"] = "not_applicable_main"
    out["seconds"] = round(time.perf_counter() - t0, 3)
    return out


def smoke_panels(leg=api.DEFAULT_LEG, win=None, arm=None) -> dict:
    """The synthetic panel plus the real ones, with the synthetic panel first.

    Cached per `(leg, window tag, arm)`: a reduced-window arm smokes on its own window, which is
    the only difference (the main panel keeps the whole history; its bootstrap chunk moves to the
    window's pool). `arm` prepares every panel for that arm (`_prepared`), including the synthetic
    one, which `real_panels` does not build.
    """
    from research.fast_engine_opts import bridge

    leg = bridge.base_leg(leg)
    key = (leg, "default" if win is None else win.tag,
           None if arm is None else (arm.get("leg"), bridge.panel_key(arm)))
    if key not in _PANELS:
        out = {"synthetic": _prepared(synthetic_panel(leg=leg), arm)}
        out.update(real_panels(leg=leg, win=win, arm=arm))
        _PANELS[key] = out
    return _PANELS[key]


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
    if r.get("win") and r["win"].get("tag") != "default":
        line += f"   [window {r['win']['tag']}]"
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
    ap = argparse.ArgumentParser(prog="python -m research.gate_power_v2.smoke")
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
    bad = 0
    for idea_id, arm_id in pairs:
        r = smoke_arm(idea_id, arm_id, leg=args.leg)
        print(format_arm(r), flush=True)
        bad += 1 if r["status"] in ("error", "plugin_error", "needs_kernel") else 0
    if bad:
        print(f"smoke FAIL ({bad} of {len(pairs)} arms)")
        return 1
    print(f"smoke OK ({len(pairs)} arms)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
