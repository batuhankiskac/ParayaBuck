"""batch_screen.run -- the framework's CLI.

    python -m research.batch_screen.run --harness
        runs `research.fast_engine.parity --quick` and reproduces, through the plugin path, every
        anchor of the three example plugins (K, R2, vol_shape p = 1.5) plus the framework's own
        parity cells (its bootstrap driver vs `fe_boot.boot`; its pooled OOS driver vs the old
        pure-Python `boot_oos_market`).

    python -m research.batch_screen.run --batch anchors_v1 --ideas k_control,r2_control,vol_shape_p15
        screens every arm of the listed ideas at stage 1, runs stage 2 for the survivors, writes
        `results/<batch>/*.json` + `RESULTS.json` + `SCOREBOARD.md` (+ `timing.json`, the only
        non-deterministic file: wall clock per arm). Arms run in parallel processes; each is paired
        against the batch's control (`k_control/K`), whose record is computed once into
        `results/<batch>/_control.json` and reused (also by the paired Δ of every other arm).

    python -m research.batch_screen.run --deflate [--batch ID]
        the multiple-testing pass over every screened arm's B1 Δ vector (White RC, Romano-Wolf
        stepdown, deflated Sharpe), written to `results/DEFLATION.json`.

Byte-identical reruns: every number is rounded to 6 dp and every container sorted before
serialising, so re-running a batch reproduces `RESULTS.json` and the per-arm files byte for byte
(`timing.json` is the deliberate exception).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np

from research.fast_engine import fe_sim
from research.fast_engine.fe_config import BLOCK, GATE_CAPITAL, PRE_BOOT_PATHS, SEED, TAX_BOOT_PATHS

from . import api, deflation, registry, stages, windows
from . import boot as B
from .rules import CONTROL_ARM, cash_rates

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
ANCHOR_BAND_H2 = api.ROOT / "research/band_h2_combo_v1/RESULTS.json"
ANCHOR_VOL_SHAPE = api.ROOT / "research/vol_shape_v1/RESULTS.json"
ANCHOR_TOL_PP = 0.01


# ----------------------------------------------------------------------------- serialisation
def rnd(x, nd=6):
    """Round every float leaf before serialising (`trend_robustness_v2.rnd`'s convention)."""
    if isinstance(x, bool) or x is None or isinstance(x, (str, int)):
        return x
    if isinstance(x, float):
        return round(x, nd)
    if isinstance(x, dict):
        return {k: rnd(v, nd) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [rnd(v, nd) for v in x]
    if isinstance(x, np.ndarray):
        return [rnd(float(v), nd) for v in x.ravel()] if x.ndim == 1 else x.tolist()
    if isinstance(x, (np.floating,)):
        return round(float(x), nd)
    if isinstance(x, (np.integer,)):
        return int(x)
    return x


def dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rnd(obj), sort_keys=True, indent=1) + "\n")


# ----------------------------------------------------------------------------- arm resolution
def control_spec():
    """The control's resolved arm (the production K plugin arm)."""
    return api.load_arm(*CONTROL_ARM.split("/"))


def plan(ideas, batch, cash_yield="none", allow_dirty=False):
    """Resolve every arm of the listed ideas, register it, and apply the commit gate."""
    out, skipped = [], []
    for idea_id in ideas:
        mod = api.load_idea(idea_id)
        path = api.idea_path(idea_id)
        if allow_dirty:
            git = {"head": registry.head_commit(), "idea_commit": registry.idea_commit(path),
                   "commit_gate": "skipped"}
        else:
            git = registry.require_committed(idea_id, path)
            git["commit_gate"] = "pass"
        c_hash = api.code_hash(idea_id)
        for arm_id, raw in mod.ARMS.items():
            spec = dict(raw)
            key = api.arm_key(idea_id, arm_id)
            try:
                arm = api.resolve_arm(mod, arm_id)
            except api.NeedsKernel as exc:
                registry.append(registry.entry_for(idea_id, arm_id, spec, api.spec_hash(spec),
                                                   c_hash, batch, "needs_kernel", git, str(exc)))
                skipped.append((key, "needs_kernel", str(exc)))
                continue
            if arm["cash_yield"] != cash_yield:
                raise SystemExit(
                    f"{key}: cash_yield {arm['cash_yield']!r} != the batch's {cash_yield!r}; the "
                    "cash-yield knob changes every published cell, so it is a whole-batch choice "
                    "(pass --cash-yield to change it)"
                )
            arm.update({"id": key, "idea": idea_id, "arm_id": arm_id, "mod": mod})
            seen = registry.is_rerun(idea_id, arm_id, api.spec_hash(spec), c_hash)
            registry.append(registry.entry_for(idea_id, arm_id, spec, api.spec_hash(spec), c_hash,
                                               batch, "rerun" if seen else "screened", git))
            out.append(arm)
    return out, skipped


# ----------------------------------------------------------------------------- control record
def control_file(batch_dir, win=None) -> Path:
    """The batch's cached control record for one window (`_control.json` for the published one)."""
    win = win or windows.DEFAULT_WINDOW
    return Path(batch_dir) / ("_control.json" if not win.reduced else f"_control_{win.tag}.json")

def control_record(cash_yield="none", stage2=True, quiet=True, win=None):
    """The control's stage-1/stage-2 record and its raw bootstrap vectors, on `win`.

    `win` defaults to the published main window; a reduced window re-simulates K itself on that
    window (and draws its block bootstrap from that window's pool), which is what keeps every
    arm's Δ paired when the arm declared a later start.
    """
    arm = control_spec()
    win = win or windows.DEFAULT_WINDOW
    t0 = time.time()
    rec = stages.stage1(arm, cash_yield, win=win)
    b12 = stages.stage1_vectors(arm, cash_yield, quiet=quiet, label="control", win=win)
    rec["boot"] = {"pre": {}, "tax": {}}
    rec["boot_level"] = {"pre": {}, "tax": {}}
    for md in ("base", "calibrated"):
        v = b12["pre"][md][CONTROL_ARM]
        rec["boot"]["pre"][md] = B.delta_stats(v - v)      # the control's Δ vs itself
        rec["boot_level"]["pre"][md] = B.delta_stats(v)    # its own bootstrap level
    for md in ("progressive", "flat"):
        v = b12["tax"][GATE_CAPITAL][md][CONTROL_ARM]
        rec["boot"]["tax"][md] = B.delta_stats(v - v)
        rec["boot_level"]["tax"][md] = B.delta_stats(v)
    rec["after_tax"].update({k: v for k, v in
                             stages.stage2_main_windows(arm, cash_yield, win=win).items()
                             if k not in rec["after_tax"]})
    vec = {"pre": {md: b12["pre"][md][CONTROL_ARM].tolist() for md in b12["pre"]},
           "tax": {md: b12["tax"][GATE_CAPITAL][md][CONTROL_ARM].tolist()
                   for md in b12["tax"][GATE_CAPITAL]}}
    if stage2:
        rec["oos"] = stages.stage2_oos(arm, cash_yield)
        rec["etf_pre"], rec["etf_after_tax"] = stages.stage2_instruments(arm, cash_yield)
        rec["rolling"] = stages.stage2_rolling(arm, cash_yield, win=win)
        fresh = stages.stage2_fresh(arm, cash_yield, label="control fresh", win=win)
        rec["fresh"] = {blk: {"tax": {md: list(fresh[blk]["tax"][GATE_CAPITAL][md][CONTROL_ARM])
                                      for md in fresh[blk]["tax"][GATE_CAPITAL]}}
                        for blk in fresh}
        vec["fresh"] = {blk: {md: list(fresh[blk]["tax"][GATE_CAPITAL][md][CONTROL_ARM])
                              for md in fresh[blk]["tax"][GATE_CAPITAL]} for blk in fresh}
        pooled = stages.stage2_oos_pooled(arm, cash_yield)
        crec_pool = B.delta_stats(pooled["pooled"][CONTROL_ARM])
        rec["oos_pooled"] = crec_pool
        vec["oos_pooled"] = pooled["pooled"][CONTROL_ARM].tolist()
    return {"arm": CONTROL_ARM, "cash_yield": cash_yield, "spec": arm["spec"], "record": rec,
            "vectors": vec, "win": win.as_dict(),
            "params": {"seed": SEED, "block": BLOCK, "n_pre": PRE_BOOT_PATHS, "n_tax": TAX_BOOT_PATHS,
                       "capital": GATE_CAPITAL, "fresh_seed": stages.FRESH_SEED,
                       "fresh_blocks": list(stages.E3_BLOCKS),
                       "oos_paths_per_market": B.OOS_PATHS_PER_MARKET},
            "wall_s": time.time() - t0}


def load_control(batch_dir: Path, cash_yield="none", require_stage2=True, quiet=True, win=None):
    """The batch's control record for one window, computed once and cached in `_control.json`
    (`_control_<tag>.json` for a reduced window)."""
    win = win or windows.DEFAULT_WINDOW
    path = control_file(batch_dir, win)
    if path.exists():
        ctrl = json.loads(path.read_text())
        if ctrl.get("cash_yield") == cash_yield and (not require_stage2 or "oos" in ctrl["record"]):
            return ctrl
    ctrl = control_record(cash_yield, stage2=require_stage2, quiet=quiet, win=win)
    dump(path, ctrl)
    return ctrl


# ----------------------------------------------------------------------------- one arm
def _na_record(out, batch_dir, where, reason, t0):
    """Write the record of an arm whose inputs do not exist for a required window."""
    out["not_applicable"] = {"where": where, "reason": str(reason)}
    out["verdict"] = "not_applicable"
    out["b1_delta_pp"] = []
    out["wall_s"] = time.time() - t0
    dump(Path(batch_dir) / f"{out['idea']}__{out['arm_id']}.json",
         {k: v for k, v in out.items() if k != "wall_s"})
    return {"arm": out["arm"], "wall_s": out["wall_s"], "verdict": "not_applicable",
            "stage1_pass": False}


def _arm_task(payload):
    """One arm end to end (runs in a worker process)."""
    idea_id, arm_id, batch, cash_yield, batch_dir, stage2_allowed, quiet = payload
    t0 = time.time()
    arm = api.load_arm(idea_id, arm_id)
    out = {
        "arm": arm["id"], "idea": idea_id, "arm_id": arm_id, "spec": arm["spec"],
        "rule": arm["rule"], "leg": arm["leg"], "cash_yield": arm["cash_yield"],
        "hashes": {"code": api.code_hash(idea_id), "spec": api.spec_hash(arm["spec"])},
        "batch": batch, "date": date.today().isoformat(), "win": None,
        "stage1": None, "stage1_gates": None, "stage1_pass": None,
        "stage2": None, "gates": None, "stage2_pass": None, "b1_delta_pp": [],
        "boot_params": {"seed": SEED, "block": BLOCK, "n_pre": PRE_BOOT_PATHS,
                        "n_tax": TAX_BOOT_PATHS, "capital": GATE_CAPITAL},
    }
    try:
        win = windows.arm_window(arm)
    except api.NotApplicable as exc:
        return _na_record(out, batch_dir, "declared window (spec[\"start\"])", exc.reason, t0)
    out["win"] = win.as_dict()
    ctrl = json.loads(control_file(batch_dir, win).read_text())
    crec, cvec = ctrl["record"], ctrl["vectors"]
    where = "main window (stage 1)"
    try:
        rec = stages.stage1(arm, cash_yield, win=win)
        where = "stage-1 block bootstrap"
        b12 = stages.stage1_vectors(arm, cash_yield, quiet=quiet, label=arm["id"], win=win)
    except api.NotApplicable as exc:
        # stage 1 requires the main window (and therefore its own bootstrap) to be applicable
        return _na_record(out, batch_dir, where, exc.reason, t0)
    rec["boot"] = {"pre": {}, "tax": {}}
    rec["boot_level"] = {"pre": {}, "tax": {}}
    delta_vec = {}
    for md in ("base", "calibrated"):
        v = b12["pre"][md][arm["id"]]
        d = v - np.asarray(cvec["pre"][md], dtype=np.float64)
        rec["boot"]["pre"][md] = B.delta_stats(d)
        rec["boot_level"]["pre"][md] = B.delta_stats(v)
        delta_vec[f"pre_{md}"] = d
    for md in ("progressive", "flat"):
        v = b12["tax"][GATE_CAPITAL][md][arm["id"]]
        d = v - np.asarray(cvec["tax"][md], dtype=np.float64)
        rec["boot"]["tax"][md] = B.delta_stats(d)
        rec["boot_level"]["tax"][md] = B.delta_stats(v)
        delta_vec[f"tax_{md}"] = d
    try:
        rec["after_tax"].update({k: v for k, v in
                                 stages.stage2_main_windows(arm, cash_yield, win=win).items()
                                 if k not in rec["after_tax"]})
    except api.NotApplicable as exc:
        return _na_record(out, batch_dir, "after-tax window (2010/2017)", exc.reason, t0)
    s1_pass, s1_gates = stages.stage1_pass(rec, crec)
    out.update({"stage1": rec, "stage1_gates": s1_gates, "stage1_pass": s1_pass,
                "b1_delta_pp": [float(v) * 100 for v in delta_vec["tax_progressive"]]})
    if s1_pass and stage2_allowed:
        s2 = {"after_tax": dict(rec["after_tax"])}
        s2["oos"] = stages.stage2_oos(arm, cash_yield)
        s2["etf_pre"], s2["etf_after_tax"] = stages.stage2_instruments(arm, cash_yield)
        where = "rolling January starts"
        try:
            s2["rolling"] = stages.stage2_rolling(arm, cash_yield, win=win)
            where = "fresh-seed bootstrap"
            fresh = stages.stage2_fresh(arm, cash_yield, label=arm["id"], win=win)
            s2["fresh"] = {blk: {"tax": {md: list(fresh[blk]["tax"][GATE_CAPITAL][md][arm["id"]])
                                         for md in fresh[blk]["tax"][GATE_CAPITAL]}}
                           for blk in fresh}
            where = "pooled OOS bootstrap"
            markets, na_markets = stages.oos_pooled_markets(arm, cash_yield)
            if markets:
                d, note = stages.oos_pooled_delta(arm, cvec["oos_pooled"], control_spec(),
                                                  cash_yield, markets)
                cell = B.delta_stats(d)
                if len(markets) != len(stages.OOS_POOLED_MARKETS):
                    cell["markets"] = note["markets"]
                    cell["markets_not_applicable"] = na_markets
            else:
                cell = {"not_evaluable": "no OOS market's pooled bootstrap could be drawn",
                        "markets_not_applicable": na_markets}
            s2["oos_pooled"] = cell
        except api.NotApplicable as exc:
            return _na_record(out, batch_dir, where, exc.reason, t0)
        joined = dict(rec)
        joined.update({"oos": s2["oos"], "etf_pre": s2["etf_pre"], "etf_after_tax": s2["etf_after_tax"],
                       "rolling": s2["rolling"], "fresh": s2["fresh"], "oos_pooled": s2["oos_pooled"]})
        out["stage2"] = s2
        out["gates"] = stages.gates(joined, crec)
        out["stage2_pass"] = bool(out["gates"]["pass"])
        if out["gates"].get("holdout_insufficient"):
            out["availability"] = out["gates"]["holdout_insufficient"]
    out["verdict"] = ("reject_stage1" if not s1_pass else
                      ("not_run_stage2" if not stage2_allowed else
                       ("holdout_insufficient" if (out["gates"] or {}).get("holdout_insufficient")
                        else ("stage2_pass" if out["stage2_pass"] else "reject_stage2"))))
    out["wall_s"] = time.time() - t0
    dump(Path(batch_dir) / f"{idea_id}__{arm_id}.json", {k: v for k, v in out.items() if k != "wall_s"})
    return {"arm": arm["id"], "wall_s": out["wall_s"], "verdict": out["verdict"],
            "stage1_pass": s1_pass}


# ----------------------------------------------------------------------------- the batch
def scoreboard(batch, batch_dir, control, arms, defl=None):
    n = registry.n_trials()
    lines = [
        f"# batch {batch} -- SCOREBOARD",
        "",
        f"control: `{CONTROL_ARM}` (production K). Stage 1: main window "
        f"{control['record']['window']['start']} -> {control['record']['window']['end']} "
        "(QQQ/QLD*, base and calibrated financing, m = 2) plus its block-20 bootstrap "
        f"(seed {SEED}, block {BLOCK}, {PRE_BOOT_PATHS} pre-tax / {TAX_BOOT_PATHS} after-tax paths). "
        "Stage 2: the holdout set (six OOS windows, 21 ETFs, after-tax 2010/2017 windows, "
        f"fresh-seed bootstraps seed {stages.FRESH_SEED} blocks 10/40, 21 January rolling starts, "
        "pooled OOS bootstrap). Δ = arm − K in pp.",
        "",
        f"registered trials N = {n} (`research/batch_screen/REGISTRY.jsonl`; N counts distinct "
        "`(idea, arm, spec_hash)`, the deflation's multiple-testing count).",
        "",
        "| arm | window | stage 1 | stage 2 | failed gates | AT2000 Δ | B1 med/share | "
        "B2 med/share | OOS med Δ | ETFs med Δ | fresh | RW p | final |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    rows, manual = [], []
    for a in arms:
        f = batch_dir / f"{a['idea']}__{a['arm_id']}.json"
        if not f.exists():
            continue
        r = json.loads(f.read_text())
        g = r.get("gates") or {}
        ctrl = bool(r.get("control"))
        na = r.get("not_applicable")
        s1 = r.get("stage1")
        at = 0.0 if ctrl else (r["stage1_gates"]["after_tax_2000_delta_pp"] if s1 else None)
        b1 = (s1 or {}).get("boot", {}).get("tax", {}).get("progressive")
        b2 = (s1 or {}).get("boot", {}).get("tax", {}).get("flat")
        oos = g.get("G7_detail", {}).get("median_cagr_pp")
        etf = (g.get("E1") or {}).get("median_delta_pp")
        fresh = (g.get("FRESH") or {}).get("pass")
        p = (defl or {}).get("rw_stepdown", {}).get("p_adjusted", {}).get(r["arm"])
        insufficient = bool(r["verdict"] == "holdout_insufficient")
        if insufficient:
            manual.append(r["arm"])
        if ctrl:
            final = "control"
        elif na:
            final = f"not applicable ({na['where']})"
        elif insufficient:
            final = "holdout insufficient"
        elif r["verdict"] == "reject_stage1":
            final = "rejected (stage 1)"
        elif r["verdict"] == "reject_stage2":
            final = "rejected (stage 2)"
        elif r["arm"] in (defl or {}).get("declared", []):
            final = "**declared**"
        else:
            final = "not declared (deflation)"
        s1_cell = "control" if ctrl else ("n/a" if na else ("PASS" if r["stage1_pass"] else "FAIL"))
        w = r.get("win") or {}
        win_cell = "-" if not w else ("default" if w.get("tag") == "default"
                                      else f"**{w['tag']}**")
        cells = [
            win_cell,
            s1_cell,
            r["verdict"],
            ", ".join(g.get("failed", [])) or "-",
            "-" if at is None else f"{at:+.4f}",
            "-" if b1 is None else f"{b1['median_delta_pp']:+.4f} / {b1['share_gt_0']:.3f}",
            "-" if b2 is None else f"{b2['median_delta_pp']:+.4f} / {b2['share_gt_0']:.3f}",
            "-" if oos is None else f"{oos:+.4f}",
            "-" if etf is None else f"{etf:+.4f}",
            "-" if fresh is None else ("PASS" if fresh else "FAIL"),
            "-" if p is None else f"{p:.4f}",
            final,
        ]
        rows.append("| `" + r["arm"] + "` | " + " | ".join(cells) + " |")
    lines += rows
    lines += [""]
    if manual:
        lines += [
            "## manual confirmation",
            "",
            "These arms came through stage 1 but their holdout is not evaluable (fewer than "
            f"{stages.OOS_MIN_APPLICABLE} applicable OOS windows or fewer than "
            f"{stages.ETF_MIN_APPLICABLE} applicable instruments): **not a pass**. Each needs its "
            "own pre-registered confirmation study on the windows its inputs reach.",
            "",
        ]
        for a in arms:
            f = batch_dir / f"{a['idea']}__{a['arm_id']}.json"
            if not f.exists():
                continue
            r = json.loads(f.read_text())
            if r["arm"] not in manual:
                continue
            av = r.get("availability") or (r.get("gates") or {}).get("holdout_insufficient") or {}
            lines.append(f"* `{r['arm']}` — {av.get('n_oos_applicable')} applicable OOS windows, "
                         f"{av.get('n_etf_applicable')} applicable instruments; reasons: "
                         f"{json.dumps(av.get('reasons', {}), sort_keys=True)}")
        lines += [""]
    return "\n".join(lines) + "\n"


def batch_windows(arms) -> dict:
    """The distinct main windows a batch's arms declared, keyed by tag (published one first).

    A window whose declaration is not evaluable (shorter than `MIN_WINDOW_YEARS`, an `"auto"`
    input with no data) is left out here; the arm's own record carries the `not_applicable`
    verdict the worker produces.
    """
    out = {"default": windows.DEFAULT_WINDOW}
    for a in arms:
        try:
            w = windows.arm_window(a)
        except api.NotApplicable:
            continue
        out[w.tag] = w
    return out

def run_batch(batch, ideas, cash_yield="none", workers=None, stage2=True, allow_dirty=False,
              quiet=False):
    t0 = time.time()
    ver = registry.verify_ledger()
    if not ver["ok"]:
        raise registry.RegistryError(
            "the trial ledger does not verify against git (a merge that left the ledger and the "
            "idea modules inconsistent?): " + json.dumps(ver["problems"], sort_keys=True))
    if ver["stale"] and not quiet:
        print(f"  ledger: {len(ver['stale'])} registered arm(s) whose module has changed since "
              f"their last screen (expected while a branch grows): {ver['stale']}", flush=True)
    batch_dir = RESULTS / batch
    batch_dir.mkdir(parents=True, exist_ok=True)
    arms, skipped = plan(ideas, batch, cash_yield, allow_dirty)
    for key, why, note in skipped:
        print(f"  {key}: {why} -- {note}", flush=True)
    wins = batch_windows(arms)
    ctrls = {}
    for tag in sorted(wins):
        if tag != "default" and not quiet:
            print(f"  reduced window {tag}: the batch's control is re-simulated on it", flush=True)
        ctrls[tag] = load_control(batch_dir, cash_yield, require_stage2=stage2, quiet=quiet,
                                  win=wins[tag])
    ctrl = ctrls["default"]
    candidates = [a for a in arms if a["id"] != CONTROL_ARM]
    timing = {}
    if candidates:
        payloads = [(a["idea"], a["arm_id"], batch, cash_yield, str(batch_dir), stage2, quiet)
                    for a in candidates]
        n_workers = workers or min(len(payloads), max(1, (os.cpu_count() or 2)))
        if n_workers <= 1:
            results = [_arm_task(p) for p in payloads]
        else:
            with ProcessPoolExecutor(max_workers=n_workers) as ex:
                results = list(ex.map(_arm_task, payloads))
        for res in results:
            timing[res["arm"]] = res["wall_s"]
            print(f"  {res['arm']}: {res['verdict']}"
                  + ("" if res["stage1_pass"] else " (stage-1 fail)")
                  + f" {res['wall_s']:.1f}s", flush=True)
    if any(a["id"] == CONTROL_ARM for a in arms):
        idea_id, arm_id = CONTROL_ARM.split("/")
        dump(batch_dir / f"{idea_id}__{arm_id}.json", {
            "arm": CONTROL_ARM, "idea": idea_id, "arm_id": arm_id, "control": True,
            "spec": ctrl["spec"], "rule": "K", "leg": "2x", "cash_yield": cash_yield,
            "hashes": {"code": api.code_hash(idea_id), "spec": api.spec_hash(ctrl["spec"])},
            "batch": batch, "date": date.today().isoformat(),
            "stage1": ctrl["record"], "stage1_gates": None, "stage1_pass": None,
            "stage2": None, "gates": None, "stage2_pass": None, "verdict": "control",
            "b1_delta_pp": [0.0] * len(ctrl["vectors"]["tax"]["progressive"]),
        })
        timing[CONTROL_ARM] = 0.0
    files = sorted(p for p in batch_dir.glob("*__*.json") if not p.name.startswith("_"))
    agg = {"batch": batch, "control": CONTROL_ARM, "cash_yield": cash_yield,
           "n_registered_trials": registry.n_trials(), "params": ctrl["params"],
           "windows": {tag: w.as_dict() for tag, w in sorted(wins.items())},
           "arms": [json.loads(p.read_text()) for p in files]}
    dump(batch_dir / "RESULTS.json", agg)
    defl = deflate_batch(batch) if stage2 else None
    (batch_dir / "SCOREBOARD.md").write_text(scoreboard(batch, batch_dir, ctrl, arms, defl))
    # `timing.json` is the batch's only non-deterministic file: wall clock per arm and the
    # ledger's line count (which grows with every declaration by construction).
    dump(batch_dir / "timing.json", {"batch": batch, "wall_s": timing,
                                     "total_wall_s": time.time() - t0,
                                     "registry_trials": registry.n_trials(),
                                     "registry_lines": registry.n_lines()})
    return {"batch": batch, "dir": str(batch_dir), "timing": timing, "deflation": defl,
            "wall_s": time.time() - t0}


# ----------------------------------------------------------------------------- deflation pass
def b1_vectors(batch=None):
    """The B1-recent Δ vector (pp) of every screened arm, from the result files."""
    out = {}
    for p in sorted(RESULTS.glob("*/*__*.json")):
        if batch is not None and p.parent.name != batch:
            continue
        r = json.loads(p.read_text())
        if r.get("b1_delta_pp") and not r.get("control"):
            out[r["arm"]] = np.asarray(r["b1_delta_pp"], dtype=np.float64)
    return out


def deflate_batch(batch=None, alpha=0.05, resamples=2000):
    """White RC + Romano-Wolf + deflated Sharpe over every screened arm's B1 Δ vector.

    `deflation_rejected` is the multiple-testing verdict on its own; `declared` is the framework's
    final candidate set -- arms that passed **stage 2** *and* the deflation test at `alpha`.
    """
    vecs = b1_vectors(batch)
    out = deflation.deflate(vecs, registry.n_trials(), alpha=alpha, resamples=resamples)
    out["batch"] = batch or "all"
    out["registry_trials"] = registry.n_trials()
    stage2 = {}
    tags = set()
    for p in sorted(RESULTS.glob("*/*__*.json")):
        if batch is not None and p.parent.name != batch:
            continue
        r = json.loads(p.read_text())
        stage2[r["arm"]] = bool(r.get("stage2_pass"))
        tags.add((r.get("win") or {}).get("tag", "default"))
    # every Δ vector is the arm's *paired* advantage over K on the window the arm declared, so
    # the comparison across arms is between advantages; the window tags say which pools are in
    # play (a single-pool batch carries `["default"]` only).
    out["window_tags"] = sorted(tags)
    out["stage2_pass"] = sorted(a for a, v in stage2.items() if v)
    out["deflation_rejected"] = list(out.get("declared", []))
    out["declared"] = sorted(a for a in out["deflation_rejected"] if stage2.get(a, False))
    dump(RESULTS / "DEFLATION.json", out)
    return out


# ----------------------------------------------------------------------------- two-leg checks
def two_leg_probe(arm_id):
    """One arm of `probes/two_leg.py` (the two-leg engineering probe, outside `research/ideas/`)."""
    import importlib.util

    path = HERE / "probes/two_leg.py"
    spec = importlib.util.spec_from_file_location("probe_two_leg_run", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    arm = api.resolve_arm(mod, arm_id)
    arm.update({"id": f"probes.two_leg/{arm_id}", "idea": "probes.two_leg", "arm_id": arm_id,
                "mod": mod})
    return arm


def two_leg_checks():
    """The framework's two-leg routing: the plugin contract, the kernel, the availability rule."""
    ctrl = control_spec()
    days, p, i0, i1 = windows.main_panel(0.0)
    env = windows.try_env(days, i0, i1)
    out = {}
    zeros = two_leg_probe("zeros")
    split = two_leg_probe("split")
    gld = two_leg_probe("gld_sleeve")
    rc = stages.run_one(ctrl, p, env, i0, i1, "pre_tax", 2)
    rz = stages.run_one(zeros, p, env, i0, i1, "pre_tax", 2)
    out["zero_leg_b_is_the_control_pretax"] = {
        "pass": bool(np.array_equal(rc["_nav"], rz["_nav"])
                     and rc["cagr_usd_liquidated"] == rz["cagr_usd_liquidated"]),
        "cagr": rz["cagr_usd_liquidated"]}
    rc2 = stages.run_one(ctrl, p, env, i0, i1, "progressive", 2, 100_000.0)
    rz2 = stages.run_one(zeros, p, env, i0, i1, "progressive", 2, 100_000.0)
    out["zero_leg_b_is_the_control_after_tax"] = {
        "pass": bool(np.array_equal(rc2["_nav"], rz2["_nav"])
                     and rc2["cagr_usd_liquidated"] == rz2["cagr_usd_liquidated"]),
        "cagr": rz2["cagr_usd_liquidated"]}
    rs = stages.run_one(split, p, env, i0, i1, "progressive", 2, 100_000.0)
    a, b = rs["_legs"]["A"], rs["_legs"]["B"]
    out["two_leg_arm_reaches_the_two_leg_kernel"] = {
        "pass": bool(a["trades"] > 0 and b["trades"] > 0 and b["turnover_total"] > 0
                     and sum(b["realised_gain_try_by_reason"].values()) != 0.0),
        "leg_a_trades": a["trades"], "leg_b_trades": b["trades"],
        "cagr": rs["cagr_usd_liquidated"]}
    try:
        stages.run_one(gld, p, env, i0, i1, "pre_tax", 2)
        out["leg_b_without_history_is_not_applicable"] = {
            "pass": False, "reason": "the GLD sleeve ran on the main window"}
    except api.NotApplicable as exc:
        out["leg_b_without_history_is_not_applicable"] = {
            "pass": bool("gld" in exc.reason), "reason": exc.reason}
    # A leg-B *extra* series on a bootstrap chunk: `column_days` is the label of each column, so a
    # series with a gap (dgs10 is not published daily, gld starts in 2004) is refused with the
    # right session and never raises -- the IndexError `leg_b_usable` used to hit on a chunk.
    bw = B.BootWindow("none")
    pos = bw.positions(np.random.default_rng(SEED), BLOCK, 4)
    pb = B.boot_panels(bw, pos, ("2x",), (0.0,))[("2x", 0.0)]
    ok, why = pb.leg_b_usable("dgs10", bw.i0, bw.i1)
    row = np.asarray(pb.leg_series("dgs10")[0], dtype=np.float64)[0]
    first_bad = int(np.argmax(~np.isfinite(row[bw.i0 - 1 : bw.i1 + 1]))) + bw.i0 - 1
    out["chunk_leg_b_gap_names_the_failing_column"] = {
        "pass": bool(not ok and str(pb.column_days[first_bad]) in why
                     and pb.column_days.size == pb.price.shape[1]),
        "reason": why, "label": str(pb.column_days[first_bad]),
        "columns": int(pb.column_days.size), "days": int(pb.days.size)}
    # the same chunk with a restricted pool (the IC31-IC37 unlock): a late-starting leg is usable
    # because every drawn source session now has a price for it
    ok_gld, why_gld = pb.leg_b_usable("gld", bw.i0, bw.i1)
    win_r = windows.arm_window(two_leg_probe("gld_offstate"))
    bwr = B.BootWindow("none", windows.boot_pool(windows.bars()[0], win_r))
    posr = bwr.positions(np.random.default_rng(SEED), BLOCK, 4)
    pr = B.boot_panels(bwr, posr, ("2x",), (0.0,))[("2x", 0.0)]
    okr, whyr = pr.leg_b_usable("gld", bwr.i0, bwr.i1)
    out["the_reduced_pool_makes_a_late_leg_b_usable"] = {
        "pass": bool(okr and not ok_gld and win_r.reduced),
        "reason": whyr, "pool": list(bwr.pool), "default_pool_reason": why_gld}
    return out


def two_leg_opts_parity(n_boot_paths=20):
    """The two-leg *option* copy's parity cells (`research.fast_engine2/fe2_opts_parity.py`).

    Each option off must reproduce the current kernel bit for bit; each option on must change the
    numbers in the direction the option specifies.
    """
    from research.fast_engine2 import fe2_opts_parity

    res = fe2_opts_parity.run(n_boot_paths=n_boot_paths, quiet=True)
    out = {}
    for k, v in sorted(res.items()):
        if isinstance(v, dict) and "pass" in v:
            out[k] = {kk: vv for kk, vv in v.items()
                      if kk in ("pass", "max_abs_diff", "note", "reason")}
        elif isinstance(v, (int, float, bool, str)):
            out[k] = {"value": v}
    return out


def reduced_window_checks():
    """The reduced-window machinery on real data (`spec["start"]`, README section 2a).

    The probe arm is `probes/two_leg.py::gld_offstate` (`start = "auto"`, a fixed leg-B sleeve
    while the trend vote is off): its window, its clipped after-tax windows, its dropped rolling
    starts, its restricted bootstrap pool, and the paired control on that pool.
    """
    arm = two_leg_probe("gld_offstate")
    days = windows.bars()[0]
    win = windows.arm_window(arm)
    ctrl_arm = control_spec()
    out = {}
    first = windows.input_start_index(days, "gld")
    out["auto_is_the_inputs_first_session_plus_one"] = {
        "pass": bool(win.reduced and win.i0 == first + 1
                     and win.tag == "w" + str(days[win.i0]).replace("-", "")
                     and win.inputs == ("gld",) and win.declared == "auto"),
        "i0": int(win.i0), "first_input_session": int(first), "window_start": str(days[win.i0]),
        "tag": win.tag}
    atw = windows.after_tax_windows(days, win)
    out["after_tax_windows_are_clipped_to_the_window"] = {
        "pass": bool(atw["2000_03_to_2026_09"][0] == win.i0
                     and atw["2010_01_to_end"][0] == windows.rolling_start_index(days, 2010)
                     and atw["2017_01_to_end"][0] == windows.rolling_start_index(days, 2017)),
        "windows": {k: [str(days[a]), str(days[b])] for k, (a, b) in atw.items()}}
    starts = windows.rolling_starts(days, win)
    out["rolling_starts_before_the_window_are_dropped"] = {
        "pass": bool(starts and min(starts, key=int) == "2005"
                     and all(windows.rolling_start_index(days, int(y)) >= win.i0 for y in starts)),
        "n": len(starts), "first": min(starts, key=int), "last": max(starts, key=int),
        "published_n": 21}
    pool = windows.boot_pool(days, win)
    bw = B.BootWindow("none", pool)
    pos = bw.positions(np.random.default_rng(SEED), BLOCK, 8)
    out["the_bootstrap_pool_is_the_window"] = {
        "pass": bool(pool == (win.i0 - 1, len(days) - 2) and int(pos.min()) >= pool[0]
                     and int(pos.max()) <= pool[1] and bw.npool_draw == pool[1] - pool[0] + 1),
        "pool": [int(pool[0]), int(pool[1])], "drawn": [int(pos.min()), int(pos.max())],
        "npool_draw": int(bw.npool_draw), "npool_published": int(bw.npool)}
    rec = stages.stage1(arm, "none", win=win)
    out["stage1_runs_on_the_declared_window"] = {
        "pass": bool(rec["window"]["start"] == str(days[win.i0]) and rec["win"]["tag"] == win.tag
                     and rec["after_tax"]["2000_03_to_2026_09"]["window"][0] == str(days[win.i0])
                     and np.isfinite(rec["main"]["base"]["m2"]["cagr"])
                     and rec["main"]["base"]["m2"]["max_dd"] > 0),
        "window": rec["window"]["start"], "cagr": rec["main"]["base"]["m2"]["cagr"],
        "max_dd": rec["main"]["base"]["m2"]["max_dd"]}
    n = 10
    mine = stages.stage1_vectors(arm, "none", win=win, n_pre=n, n_tax=n, quiet=True)
    theirs = stages.stage1_vectors(ctrl_arm, "none", win=win, n_pre=n, n_tax=n, quiet=True)
    d = (mine["tax"]["100k"]["progressive"][arm["id"]]
         - theirs["tax"]["100k"]["progressive"][ctrl_arm["id"]])
    out["the_control_shares_the_reduced_pool"] = {
        "pass": bool(len(d) == n and np.isfinite(d).all()
                     and mine["params"]["pool"] == theirs["params"]["pool"] == list(pool)
                     and np.isfinite(mine["pre"]["base"][arm["id"]]).all()),
        "pool": mine["params"]["pool"], "n": int(len(d)),
        "delta_median_pp": float(np.median(d) * 100)}
    a = stages.stage1_vectors(ctrl_arm, "none", n_pre=n, n_tax=n, quiet=True)
    b = stages.stage1_vectors(ctrl_arm, "none", win=windows.DEFAULT_WINDOW, n_pre=n, n_tax=n,
                              quiet=True)
    same = all(np.array_equal(a[grp][md if grp == "pre" else "100k"][ctrl_arm["id"]],
                              b[grp][md if grp == "pre" else "100k"][ctrl_arm["id"]])
               for grp, md in (("pre", "base"), ("pre", "calibrated")))
    out["the_default_window_pool_is_the_published_draw"] = {
        "pass": bool(same and b["params"]["pool"] == [0, len(days) - 2]
                     and a["params"]["pool"] is None),
        "pre_base_max_abs_diff": float(np.max(np.abs(
            a["pre"]["base"][ctrl_arm["id"]] - b["pre"]["base"][ctrl_arm["id"]]))),
        "default_pool": b["params"]["pool"]}
    wins = batch_windows([arm, ctrl_arm])
    out["a_batch_computes_one_control_per_window"] = {
        "pass": bool(sorted(wins) == ["default", win.tag] and wins[win.tag].i0 == win.i0),
        "windows": sorted(wins), "control_file": control_file(Path("."), win).name}
    return out


def _group_pass(cells):
    """True when every `{"pass": ...}` cell of one parity group passed."""
    v = [c for c in cells.values() if isinstance(c, dict) and "pass" in c]
    return bool(v) and all(c["pass"] for c in v)


def two_leg_kernel_parity(n_boot_paths=40, engine_parity_result=None):
    """The two-leg kernel's own parity cells (leg B == 0, bit for bit) plus the riskoff case."""
    from research.fast_engine2 import fe2_parity
    from research.two_leg_kernel_v1 import parity_b

    fe2 = fe2_parity.run(n_boot_paths=n_boot_paths, quiet=True,
                         engine_parity_result=engine_parity_result)
    pb = parity_b.run(assert_pass=True)
    out = {
        "engine_b0_nav_and_scalars": {"pass": _group_pass(fe2["main_window"])},
        "engine_b0_after_tax_windows": {"pass": _group_pass(fe2["after_tax_windows"])},
        "engine_b0_bootstrap": {"pass": _group_pass(fe2["bootstrap"])},
        "engine_b0_cash_yield": {"pass": _group_pass(fe2["cash_yield"])},
        "engine_anchors_published_K": {"pass": _group_pass(fe2["engine_anchors"])},
        "engine_parity_quick": {"pass": bool(fe2["engine_parity_quick"]["pass"]),
                                "tail": fe2["engine_parity_quick"]["tail"]},
        "cells": fe2["n_checks"], "failed": fe2["failed"],
    }
    for k, v in pb["parity"].items():
        out[f"riskoff_two_leg_case_{k}"] = {"pass": bool(v["pass"]),
                                            "nav_max_abs_diff": v["nav_max_abs_diff"]}
    out["riskoff_divergence_pp"] = {"pass": True,
                                    "cagr_diff_pp": pb["divergence"]["numbers"]["cagr_diff_pp"],
                                    "ref_tax_try": pb["divergence"]["numbers"]["ref_tax_total_try"],
                                    "kernel_tax_try": pb["divergence"]["numbers"]["kernel_tax_total_try"]}
    return out


# ----------------------------------------------------------------------------- harness
def _anchor(label, got, want, tol_pp=ANCHOR_TOL_PP):
    got, want = float(got), float(want)
    diff = (got - want) * 100
    return {"label": label, "reference": want, "reproduced": got, "diff_pp": diff,
            "pass": bool(abs(diff) <= tol_pp)}


def harness():
    """Every anchor of the framework, through the plugin path."""
    t0 = time.time()
    checks = {}
    # `parity.py` writes `research/fast_engine/PARITY.json` unconditionally (the engine's own
    # artifact, and it has no --no-write flag): keep its committed content, so running this harness
    # leaves the worktree clean.
    parity_out = api.ROOT / "research/fast_engine/PARITY.json"
    parity_before = parity_out.read_bytes() if parity_out.exists() else None
    par = subprocess.run([sys.executable, "-m", "research.fast_engine.parity", "--quick"],
                         cwd=api.ROOT, capture_output=True, text=True)
    if parity_before is not None and parity_out.read_bytes() != parity_before:
        parity_out.write_bytes(parity_before)
    checks["engine_parity_quick"] = {
        "pass": bool(par.returncode == 0 and "parity PASS" in par.stdout),
        "tail": (par.stdout.strip().splitlines() or [par.stderr[-200:]])[-1],
    }
    bh = json.loads(ANCHOR_BAND_H2.read_text())
    vs = json.loads(ANCHOR_VOL_SHAPE.read_text())
    ctrl = control_record("none", stage2=True)
    rec = ctrl["record"]
    an = {}
    for md in ("base", "calibrated"):
        for m in (1, 2):
            an[f"K_pre_{md}_m{m}"] = _anchor("", rec["main"][md][f"m{m}"]["cagr"],
                                             bh["pre_tax_main"][md][f"m{m}"]["K"]["cagr"])
        an[f"K_maxdd_{md}"] = _anchor("", rec["main"][md]["m2"]["max_dd"],
                                      bh["pre_tax_main"][md]["m2"]["K"]["max_dd"])
        an[f"K_turnover_{md}"] = _anchor("", rec["main"][md]["m2"]["turnover_annual"],
                                         bh["pre_tax_main"][md]["m2"]["K"]["turnover_annual"])
    for wn in ("2000_03_to_2026_09", "2010_01_to_end", "2017_01_to_end"):
        an[f"K_after_tax_100k_{wn}"] = _anchor(
            "", rec["after_tax"][wn]["after_tax_cagr"],
            bh["after_tax_windows"]["100k"][wn]["K"]["after_tax_cagr"])
        an[f"K_after_tax_matches_volshape_C0_{wn}"] = _anchor(
            "", rec["after_tax"][wn]["after_tax_cagr"],
            vs["after_tax_windows"]["100k"][wn]["C0"]["after_tax_cagr"])
    for key in ("GSPC_1957_1999", "M1", "M2", "M3", "M4", "M5"):
        an[f"K_oos_{key}"] = _anchor("", rec["oos"][key]["cagr"], bh["oos"][key]["K"]["cagr"])
    an["K_etf_median_cagr"] = _anchor(
        "", np.median([rec["etf_pre"][k]["cagr"] for k in rec["etf_pre"]]),
        np.median([bh["instruments"][k]["K"]["cagr"] for k in bh["instruments"]]))
    an["K_e1_median_after_tax"] = _anchor(
        "", np.median([rec["etf_after_tax"][k]["after_tax_cagr"] for k in rec["etf_after_tax"]]),
        np.median([bh["E1_after_tax_etfs_rindex"][k]["K"]["after_tax_cagr"]
                   for k in bh["E1_after_tax_etfs_rindex"]]))
    for yr in ("2001", "2011", "2021"):
        an[f"K_rolling_{yr}"] = _anchor("", rec["rolling"][yr]["after_tax_cagr"],
                                        bh["E2_rolling_starts"][yr]["K"]["after_tax_cagr"])
    for mode in ("progressive", "flat"):
        an[f"K_bootstrap_level_{mode}"] = _anchor(
            "", rec["boot_level"]["tax"][mode]["cagr_median"],
            vs["bootstrap_b12"]["tax"]["100k"][mode]["C0"]["cagr_median"])
    for md in ("base", "calibrated"):
        an[f"K_pretax_bootstrap_level_{md}"] = _anchor(
            "", rec["boot_level"]["pre"][md]["cagr_median"],
            vs["bootstrap_b12"]["pre"][md]["C0"]["cagr_median"])
    checks["control_anchors"] = an
    checks["plugin_anchors"] = plugin_anchors(bh, vs, ctrl)
    checks["parity"] = {"boot_vs_engine": boot_parity(), "oos_pooled_vs_old_code": oos_pooled_parity()}
    checks["behaviour"] = {"cash_yield": cash_yield_check(), "leg": leg_check()}
    checks["two_leg"] = two_leg_checks()
    checks["reduced_window"] = reduced_window_checks()
    checks["two_leg_kernel_parity"] = two_leg_kernel_parity(
        engine_parity_result=checks["engine_parity_quick"])
    flat = {}
    for group, val in checks.items():
        if isinstance(val, dict):
            for k, v in val.items():
                if isinstance(v, dict) and "pass" in v:
                    flat[f"{group}.{k}"] = bool(v["pass"])
                elif isinstance(v, list):   # a group that reports a list of cell names
                    flat[f"{group}.{k}"] = not v
    checks["all"] = all(flat.values())
    checks["n_checks"] = len(flat)
    checks["failed"] = sorted(k for k, v in flat.items() if not v)
    checks["wall_s"] = time.time() - t0
    return checks


def plugin_anchors(bh, vs, ctrl):
    """The three example plugins' published anchors, through their own target code."""
    out = {}
    for idea_id, arm_id in (("r2_control", "R2"), ("vol_shape_p15", "p15")):
        arm = api.load_arm(idea_id, arm_id)
        rec = stages.stage1(arm, "none")
        rec["after_tax"].update(stages.stage2_main_windows(arm, "none"))
        if idea_id == "r2_control":
            for md in ("base", "calibrated"):
                out[f"R2_pre_{md}_m2"] = _anchor("", rec["main"][md]["m2"]["cagr"],
                                                 bh["pre_tax_main"][md]["m2"]["C0"]["cagr"])
                out[f"R2_m1_{md}"] = _anchor("", rec["main"][md]["m1"]["cagr"],
                                             bh["pre_tax_main"][md]["m1"]["C0"]["cagr"])
            out["R2_maxdd_base"] = _anchor("", rec["main"]["base"]["m2"]["max_dd"],
                                           bh["pre_tax_main"]["base"]["m2"]["C0"]["max_dd"])
            for wn in ("2000_03_to_2026_09", "2010_01_to_end", "2017_01_to_end"):
                out[f"R2_after_tax_{wn}"] = _anchor(
                    "", rec["after_tax"][wn]["after_tax_cagr"],
                    bh["after_tax_windows"]["100k"][wn]["C0"]["after_tax_cagr"])
        else:
            cells = vs["main_window"]["arms"]["E3"]["cells"]
            for md in ("base", "calibrated"):
                out[f"p15_pre_{md}_m2"] = _anchor("", rec["main"][md]["m2"]["cagr"],
                                                  cells[f"{md}_m2"]["cagr"])
                out[f"p15_pre_{md}_m1"] = _anchor("", rec["main"][md]["m1"]["cagr"],
                                                  cells[f"{md}_m1"]["cagr"])
                out[f"p15_maxdd_{md}"] = _anchor("", rec["main"][md]["m2"]["max_dd"],
                                                 cells[f"{md}_m2"]["max_dd"])
                out[f"p15_turnover_{md}"] = _anchor("", rec["main"][md]["m2"]["turnover_annual"],
                                                    cells[f"{md}_m2"]["turnover_annual"])
            for wn in ("2000_03_to_2026_09", "2010_01_to_end", "2017_01_to_end"):
                out[f"p15_after_tax_{wn}"] = _anchor(
                    "", rec["after_tax"][wn]["after_tax_cagr"],
                    vs["after_tax_windows"]["100k"][wn]["E3"]["after_tax_cagr"])
            b12 = stages.stage1_vectors(arm, "none", quiet=True, label="p15 anchor")
            for md in ("base", "calibrated"):
                v = b12["pre"][md][arm["id"]]
                d = v - np.asarray(ctrl["vectors"]["pre"][md], dtype=np.float64)
                out[f"p15_pre_{md}_level"] = _anchor(
                    "", B.delta_stats(v)["cagr_median"],
                    vs["bootstrap_b12"]["pre"][md]["E3"]["cagr_median"])
                out[f"p15_pre_{md}_median_delta"] = _anchor(
                    "", B.delta_stats(d)["median_delta_pp"],
                    vs["bootstrap_b12"]["pre"][md]["E3"]["diff_vs_ctrl_median_pp"], tol_pp=0.02)
            for mode, key in (("progressive", "B1"), ("flat", "B2")):
                v = b12["tax"][GATE_CAPITAL][mode][arm["id"]]
                d = v - np.asarray(ctrl["vectors"]["tax"][mode])
                cell = B.delta_stats(d)
                pub = vs["bootstrap_b12"]["tax"]["100k"][mode]["E3_vs_C0"]
                out[f"p15_{key}_level"] = _anchor(
                    "", B.delta_stats(v)["cagr_median"],
                    vs["bootstrap_b12"]["tax"]["100k"][mode]["E3"]["cagr_median"])
                out[f"p15_{key}_median_delta"] = _anchor("", cell["median_delta_pp"],
                                                         pub["median_delta_cagr_pp"], tol_pp=0.02)
                out[f"p15_{key}_share"] = {"reference": pub["share_gt_0"], "reproduced": cell["share_gt_0"],
                                           "diff_pp": (cell["share_gt_0"] - pub["share_gt_0"]) * 100,
                                           "pass": bool(abs(cell["share_gt_0"] - pub["share_gt_0"]) <= 0.001)}
    out["K_pre_base_m2_cagr"] = _anchor("", ctrl["record"]["main"]["base"]["m2"]["cagr"],
                                        bh["pre_tax_main"]["base"]["m2"]["K"]["cagr"])
    return out


def boot_parity(n_paths=60):
    """`fe_boot.boot` and the framework's plugin driver on the same paths, cell for cell.

    The identity checked is arm-label for arm-label: the framework's control plugin
    (`k_control/K`) against the engine's own `K`, and `r2_control/R2` (rule C0) against the
    engine's `C0` -- the two production reference rows.
    """
    from research.fast_engine import fe_boot

    arms = [control_spec(), api.load_arm("r2_control", "R2")]
    mine = B.boot(arms, seed=SEED, block=BLOCK, n_pre=n_paths,
                  n_tax=n_paths, capitals=(GATE_CAPITAL,), modes=("progressive", "flat"), quiet=True)
    theirs = fe_boot.boot(seed=SEED, block=BLOCK, arms=("C0", "K"), n_pre=n_paths, n_tax=n_paths,
                          capitals=(GATE_CAPITAL,), modes=("progressive", "flat"),
                          anchor_e2=False, deltas=("C0",), quiet=True, keep_paths=True)
    pairs = [(CONTROL_ARM, "K"), ("r2_control/R2", "C0")]
    out = {}
    for mine_id, theirs_id in pairs:
        tag = "K" if theirs_id == "K" else "C0"
        for md in ("base", "calibrated"):
            d = float(np.max(np.abs(mine["pre"][md][mine_id] - theirs["paths"]["pre"][(md, theirs_id)])))
            out[f"{tag}_pre_{md}"] = {"max_abs_diff": d, "pass": bool(d == 0.0)}
        for md in ("progressive", "flat"):
            d = float(np.max(np.abs(mine["tax"][GATE_CAPITAL][md][mine_id]
                                    - theirs["paths"]["tax"][GATE_CAPITAL][md][theirs_id])))
            out[f"{tag}_tax_{md}"] = {"max_abs_diff": d, "pass": bool(d == 0.0)}
    return out


def oos_pooled_parity(per_market=40):
    """The framework's pooled OOS driver vs the old pure-Python `boot_oos_market`, 40 paths."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("old_screen_bs", api.ROOT / "research/band_h2_combo_v1/screen.py")
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    old.OOS_PATHS_PER_MARKET = per_market
    rng = np.random.default_rng(SEED)
    theirs = {k: old.boot_oos_market(k, rng, ("K",))["K"] for k in ("M1", "M2", "M3", "M4", "M5")}
    arm = control_spec()
    mine = B.oos_pooled([arm], per_market=per_market, quiet=True)
    out = {}
    for k in ("M1", "M2", "M3", "M4", "M5"):
        d = float(np.max(np.abs(mine["per_market"][k][CONTROL_ARM] - theirs[k])))
        out[k] = {"max_abs_diff": d, "pass": bool(d == 0.0)}
    return out


def cash_yield_check():
    """The knob is inert when its schedule is zero, and moves the numbers when it is not."""
    arm = control_spec()
    days, p, i0, i1 = windows.main_panel(0.0)
    env_off = windows.try_env(days, i0, i1, "none")
    zeros = np.zeros(len(days))
    env_zero = fe_sim.RunEnv(days, env_off.fx, windows.ufe(), i0, i1, cash_rate=zeros)
    env_on = windows.try_env(days, i0, i1, "tbill")
    r_off = stages.run_one(arm, p, env_off, i0, i1, "pre_tax")
    r_zero = stages.run_one(arm, p, env_zero, i0, i1, "pre_tax")
    r_on = stages.run_one(arm, p, env_on, i0, i1, "pre_tax")
    same = bool(np.array_equal(r_off["_nav"], r_zero["_nav"]))
    rates = cash_rates(list(days))
    return {
        "zero_schedule_inert": {"pass": same, "max_abs_diff": float(np.max(np.abs(r_off["_nav"] - r_zero["_nav"])))},
        "schedule_positive": {"pass": bool(rates.size == len(days) and rates.max() > 0)},
        "on_changes_numbers": {"pass": bool(abs(r_on["cagr_usd_liquidated"] - r_off["cagr_usd_liquidated"]) > 1e-6),
                               "off_cagr": r_off["cagr_usd_liquidated"],
                               "on_cagr": r_on["cagr_usd_liquidated"]},
    }


def leg_check():
    """Both legs run, and the arm's `leg` actually reaches the engine."""
    arm = control_spec()
    days, p2, i0, i1 = windows.main_panel(0.0, leg="2x")
    _d, p1, _j0, _j1 = windows.main_panel(0.0, leg="1x")
    r2 = stages.run_one(arm, p2, windows.try_env(days, i0, i1), i0, i1, "pre_tax")
    r1 = stages.run_one(arm, p1, windows.try_env(days, i0, i1), i0, i1, "pre_tax")
    v2 = float(p2.vol[0, -1])
    v1 = float(p1.vol[0, -1])
    return {
        "1x_runs": {"pass": bool(np.isfinite(r1["cagr_usd_liquidated"])),
                    "cagr_1x": r1["cagr_usd_liquidated"]},
        "leg_reaches_engine": {"pass": bool(abs(r1["cagr_usd_liquidated"] - r2["cagr_usd_liquidated"]) > 1e-4),
                               "cagr_2x": r2["cagr_usd_liquidated"],
                               "cagr_1x": r1["cagr_usd_liquidated"]},
        "1x_less_volatile": {"pass": bool(v1 < v2), "vol_2x": v2, "vol_1x": v1},
    }


# ----------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.batch_screen.run")
    ap.add_argument("--harness", action="store_true", help="run the framework's anchor checks")
    ap.add_argument("--batch", help="batch id (results/<batch>/)")
    ap.add_argument("--ideas", help="comma-separated idea ids under research/ideas/")
    ap.add_argument("--deflate", action="store_true", help="run the multiple-testing pass")
    ap.add_argument("--verify-registry", action="store_true",
                    help="re-verify the trial ledger against git (merge safety) and exit")
    ap.add_argument("--stage1-only", action="store_true", help="do not run stage 2")
    ap.add_argument("--cash-yield", default="none", choices=("none", "tbill"))
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--allow-dirty", action="store_true",
                    help="skip the pre-registration-by-commit gate (NOT for real screening)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if args.harness:
        checks = harness()
        dump(HERE / "HARNESS.json", checks)
        for k in checks["failed"]:
            print(f"  FAIL {k}", flush=True)
        print(f"harness {'PASS' if checks['all'] else 'FAIL'} ({checks['n_checks']} checks, "
              f"{len(checks['failed'])} failed, {checks['wall_s']:.1f}s)", flush=True)
        return 0 if checks["all"] else 1
    if args.verify_registry:
        v = registry.verify_ledger()
        print(json.dumps(rnd(v), indent=1, sort_keys=True), flush=True)
        print(f"registry {'PASS' if v['ok'] else 'FAIL'} ({v['lines']} lines, {v['arms']} arms, "
              f"N = {v['n_trials']})", flush=True)
        return 0 if v["ok"] else 1
    if args.deflate:
        print(json.dumps(rnd(deflate_batch(args.batch)), indent=1, sort_keys=True))
        return 0
    if args.batch:
        if not args.ideas:
            raise SystemExit("--batch needs --ideas a,b,c")
        res = run_batch(args.batch, [s for s in args.ideas.split(",") if s], args.cash_yield,
                        args.workers, not args.stage1_only, args.allow_dirty, args.quiet)
        print((RESULTS / args.batch / "SCOREBOARD.md").read_text(), flush=True)
        print(f"batch {args.batch}: wall {res['wall_s']:.1f}s; per arm "
              f"{ {k: round(v, 1) for k, v in sorted(res['timing'].items())} }", flush=True)
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
