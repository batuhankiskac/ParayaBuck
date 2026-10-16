"""batch_screen.stages -- stage 1 (screen) and stage 2 (holdout) of the batch framework, and the
gate set.

Stage 1 uses only the main window (2000-03-09 -> 2026-09, QQQ/QLD*) and its block bootstrap; its
pass rule is fixed and narrow:

    after-tax 2000-window CAGR delta > 0
    B1-recent bootstrap (seed 20261011, block 20, 1000 paths, after-tax $100k, progressive) median delta > 0
    pre-tax max DD <= K + 2 pp (base and calibrated, m = 2)
    turnover <= 2 x K (base and calibrated, m = 2)

Stage 2 runs only for stage-1 survivors and applies the full gate set (G1-G9 vs K, after tax on
the three windows, B1/B2 >= 55%, the 21 ETFs, the 21 January rolling starts, the fresh-seed
bootstraps, the pooled OOS bootstrap). Both stages are measured against the control arm (K), whose
own record is computed once per batch and reused by every arm (`_control.json`).
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_sim, fe_study
from research.fast_engine.fe_config import (
    E2_START_YEARS,
    E3_BLOCKS,
    E3_PATHS,
    FIN,
    GATE_CAPITAL,
    INSTRUMENTS,
    OOS_WINDOWS,
    TAX_CAPITALS,
    TAX_WINDOWS,
    M,
)

from research.fast_engine2 import fe2_sim

from . import api
from . import boot as B
from . import windows
from .api import NotApplicable, call_targets

# stage 2's fresh-seed bootstrap: the same structure as the campaign's E3 (blocks 10/40, 1000
# after-tax paths, $100k) on a seed no earlier study used.
FRESH_SEED = 20261016

#: the holdout is evaluable only on the applicable windows: `targets` may raise `NotApplicable`
#: per window (an arm whose inputs start in 2003 has no M2 window). Below these counts the arm is
#: `holdout_insufficient` -- not a pass, listed for a manual confirmation study (see README §2).
OOS_MIN_APPLICABLE = 3
ETF_MIN_APPLICABLE = 15

#: the five markets of the pooled OOS bootstrap (G8); `GSPC_1957_1999` has no pooled draw.
OOS_POOLED_MARKETS = ("M1", "M2", "M3", "M4", "M5")

S1_MAX_DD_PP = 2.0
S1_TURNOVER_MULT = 2.0
G1_CALMAR = 1.10
G1_CAGR_SLACK = 0.005
G2_DD_PP = 1.0
G3_SLACK_PP = 1.0
G7_MEDIAN = 0.0
G7_CAGR_SLACK_PP = -0.5
G7_DD_PP = 3.0
G7_MAX_DD_PP = 8.0
G7_MIN_WINDOWS = 5
G9_TURNOVER_MULT = 2.0
SHARE_BOOT = 0.55
E1_SHARE = 0.60
E2_SHARE = 0.60
FRESH_SHARE = 0.55
SUBPERIODS = fe_study.SUBPERIODS

STAGE1_KEYS = ("at2000_gt_0", "b1_median_gt_0", "maxdd", "turnover")


# ----------------------------------------------------------------------------- one run
def run_one(arm, data, env, i0, i1, mode, m=M, capital=TAX_CAPITALS[GATE_CAPITAL], rule_index=True):
    """`targets` -> `fe_sim.simulate` (one leg) or `fe2_sim.simulate2` (two legs) on one window.

    A two-leg arm (`Targets.two_leg`, i.e. the spec declares `leg_b` and `targets` returned the
    third value) is routed to the two-leg kernel: one cash pool, both legs' FIFO books, one
    taxpayer. Its leg-B instrument must have a price on every session the engine reads
    (`leg_b_usable`), otherwise the window is `NotApplicable` -- exactly the framework's rule for
    any other missing input.

    An arm whose rule carries kernel options (`arm["opts"]`, registered through
    `rules.register_rule`) -- or that holds the option leg `2x_spy` -- is prepared and simulated by
    `research.fast_engine_opts.bridge` instead. With no options the panel and the engine call are
    the ones above, unchanged.
    """
    if arm.get("opts") or arm.get("leg") == api.LEG_SPY:
        from research.fast_engine_opts import bridge as _bridge

        data = _bridge.prepare_panel(data, arm)
    t = call_targets(arm["mod"], data, arm)
    if t.leg != arm["leg"]:
        raise ValueError(f"arm {arm['id']}: targets() changed the leg to {t.leg!r}")
    oa, ca = data.legs[t.leg]
    if t.two_leg:
        if data.P != 1:
            raise api.PluginError("stages.run_one runs one path; the bootstrap uses boot.boot")
        if arm.get("opts"):
            raise api.PluginError("the kernel options are single-leg; an arm cannot declare both")
        ok, why = data.leg_b_usable(t.leg_b, i0, i1)
        if not ok:
            raise NotApplicable(why)
        ob, cb = data.leg_series(t.leg_b)
        return fe2_sim.simulate2(oa, ca, t.wants, ob, cb, t.wants_b, env, capital, i0, i1,
                                 arm=arm["rule"], mode=mode, m=m, rule_index=rule_index)
    if arm.get("opts"):
        return _bridge.simulate_one(arm, t, data, env, capital, i0, i1, mode, m, rule_index)
    return fe_sim.simulate(oa, ca, t.wants, env, capital, i0, i1,
                           arm=arm["rule"], mode=mode, m=m, rule_index=rule_index)


def _cells(res, days, i0, i1):
    """`fe_study.pre_stats`'s cells (the published pre-tax statistic block)."""
    return fe_study.pre_stats(days, i0, i1, res)


def _win_cells(res, days, start):
    st = fe_study.window_stats(res["_nav"], days, start)
    st["turnover_annual"] = float(res["turnover_annual"])
    st["postponements"] = int(res["postponements"])
    return st


def after_tax_cells(arm, data, days, i0, i1, cash_yield, m=M, capital=TAX_CAPITALS[GATE_CAPITAL]):
    """The published after-tax window block: progressive + pre-tax at capital 1.0."""
    env = windows.try_env(days, i0, i1, cash_yield)
    rq = run_one(arm, data, env, i0, i1, "progressive", m, capital)
    rp = run_one(arm, data, env, i0, i1, "pre_tax", m, 1.0)
    return {
        "window": [days[i0], days[i1]],
        "pre_tax_cagr": float(rp["cagr_usd_liquidated"]),
        "after_tax_cagr": float(rq["cagr_usd_liquidated"]),
        "after_tax_cagr_unliquidated": float(rq["cagr_usd_unliquidated"]),
        "max_dd_after_tax": float(rq["max_dd_nav_usd"]),
        "terminal_usd": float(rq["terminal_usd_liquidated"]),
        "tax_usd": float(rq["tax_total_usd_at_payment_fx"]),
        "sells_per_year": float(rq["sells"] / rq["years"]),
        "trades_per_year": float(rq["strategy_trades"] / rq["years"]),
        "postponements": int(rq["postponements"]),
        "blocked_band": int(rq["blocked_band"]),
        "deferred": int(rq["deferred"]),
        "years": float(rq["years"]),
    }


# ----------------------------------------------------------------------------- stage 1
def stage1(arm, cash_yield="none"):
    """Main-window pre-tax block + the 2000 after-tax window + the clean bootstrap vectors.

    Returns `(record, vectors)`: the record is JSON-ready scalars, `vectors` the raw per-path
    CAGRs the batch driver hands to `fe_boot`/deflation.
    """
    days_b, p_base, i0, i1 = windows.main_panel(0.0, leg=arm["leg"])
    _, p_cal, _, _ = windows.main_panel(FIN["calibrated"], leg=arm["leg"])
    env = windows.try_env(days_b, i0, i1, cash_yield)
    env_usd = windows.usd_env(days_b, i0, i1, cash_yield)
    main = {"base": {}, "calibrated": {}}
    for name, p in (("base", p_base), ("calibrated", p_cal)):
        for m in (1, M):
            r = run_one(arm, p, env, i0, i1, "pre_tax", m)
            main[name][f"m{m}"] = _cells(r, days_b, i0, i1)
        r = run_one(arm, p, env_usd, i0, i1, "pre_tax", M, rule_index=False)
        main[name]["r_usd"] = _cells(r, days_b, i0, i1)
    a, b = TAX_WINDOWS["2000_03_to_2026_09"]
    j0, j1 = fe_study.window_indices(days_b, a, b)
    at2000 = after_tax_cells(arm, p_base, days_b, j0, j1, cash_yield)
    rec = {
        "window": {"start": days_b[i0], "end": days_b[i1], "sessions": int(i1 - i0 + 1)},
        "main": main,
        "after_tax": {"2000_03_to_2026_09": at2000},
    }
    return rec


def stage1_vectors(arm, cash_yield="none", seed=None, block=None, n_pre=None, n_tax=None,
                   quiet=True, label="stage1"):
    """The clean block bootstrap's raw per-path CAGRs (pre-tax base/calibrated, after-tax both
    modes at the gate capital). Same seeds/paths as the campaign's B1/B2."""
    from research.fast_engine.fe_config import BLOCK, PRE_BOOT_PATHS, SEED, TAX_BOOT_PATHS

    return B.boot([arm], seed=seed or SEED, block=block or BLOCK,
                  n_pre=PRE_BOOT_PATHS if n_pre is None else n_pre,
                  n_tax=TAX_BOOT_PATHS if n_tax is None else n_tax,
                  capitals=(GATE_CAPITAL,), modes=B.TAX_MODES, cash_yield=cash_yield,
                  label=label, quiet=quiet)


# ----------------------------------------------------------------------------- stage 2
def stage2_main_windows(arm, cash_yield="none"):
    """After tax at $100k on the 2010 and 2017 windows (the 2000 window is stage 1's)."""
    days, p, _i0, _i1 = windows.main_panel(0.0, leg=arm["leg"])
    out = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        j0, j1 = fe_study.window_indices(days, a, b)
        out[wn] = after_tax_cells(arm, p, days, j0, j1, cash_yield)
    return out


def stage2_oos(arm, cash_yield="none"):
    """The six OOS windows, pre-tax, calibrated, m = 2, R-USD rule (`fe_study.oos_window`).

    A window whose inputs the arm declares `NotApplicable` is recorded as
    `{"applicable": false, "reason": ...}` and is excluded from G7/G8.
    """
    out = {}
    for key in OOS_WINDOWS:
        try:
            days, p, start, _i1 = windows.oos_panel(key, leg=arm["leg"])
            env = windows.usd_env(days, start, len(days) - 1, cash_yield)
            r = run_one(arm, p, env, start, len(days) - 1, "pre_tax", M, 1.0, rule_index=False)
            out[key] = _win_cells(r, days, start)
        except NotApplicable as exc:
            out[key] = {"applicable": False, "reason": exc.reason}
    return out


def stage2_instruments(arm, cash_yield="none"):
    """Pre-tax on the 21 quality-passing ETFs (R-USD, calibrated, m = 2), plus the after-tax E1
    block at $100k under R-INDEX. Same per-instrument `NotApplicable` convention as `stage2_oos`.
    """
    pre, after = {}, {}
    for key in INSTRUMENTS:
        try:
            days, p, h, lo, _i0, _i1 = windows.instrument_panel(key, leg=arm["leg"])
            env = windows.usd_env(days, 253, len(days) - 1, cash_yield)
            r = run_one(arm, p, env, 253, len(days) - 1, "pre_tax", M, 1.0, rule_index=False)
            pre[key] = _win_cells(r, days, 253)
            start = windows.instrument_start(days, key)
            rec = after_tax_cells(arm, p, days, start, len(days) - 1, cash_yield)
            rec["start"] = days[start]
            after[key] = rec
        except NotApplicable as exc:
            pre[key] = after[key] = {"applicable": False, "reason": exc.reason}
    return pre, after


def stage2_rolling(arm, cash_yield="none"):
    """The 21 January rolling starts, after tax at $100k (the 2000 window is stage 1's)."""
    days, p, _i0, _i1 = windows.main_panel(0.0, leg=arm["leg"])
    T = len(days) - 1
    out = {}
    for yr in E2_START_YEARS:
        i0 = windows.rolling_start_index(days, yr)
        out[str(yr)] = after_tax_cells(arm, p, days, i0, T, cash_yield)
    return out


def stage2_fresh(arm, cash_yield="none", label="fresh"):
    """Fresh-seed after-tax bootstraps: seed 20261016, blocks 10 and 40, 1,000 paths, $100k."""
    out = {}
    for block in E3_BLOCKS:
        b = B.boot([arm], seed=FRESH_SEED, block=block, n_pre=0, n_tax=E3_PATHS,
                   capitals=(GATE_CAPITAL,), modes=B.TAX_MODES, cash_yield=cash_yield,
                   label=f"{label} block {block}", quiet=True)
        out[f"block{block}"] = b
    return out


def stage2_oos_pooled(arm, cash_yield="none", markets=None, arms=None):
    """The pooled OOS bootstrap (G8): M1-M5, 2,000 paths each, one shared rng, seed 20261011.

    `markets` restricts the draw (the applicable subset); `arms` draws several arms in one call
    (so they share their paths), defaulting to this arm alone.
    """
    kw = {} if markets is None else {"markets": tuple(markets)}
    return B.oos_pooled(list(arms) if arms else [arm], cash_yield=cash_yield, **kw)


def oos_pooled_markets(arm, cash_yield="none", markets=None):
    """`(applicable, not_applicable)` of the pooled OOS markets, probed one path at a time.

    The pooled bootstrap builds one chunk per market; a market whose chunk the arm cannot build
    raises `NotApplicable`. Probing with a single path identifies the market without paying for a
    2,000-path draw.
    """
    keys = list(markets) if markets else list(OOS_POOLED_MARKETS)
    ok, bad = [], {}
    for key in keys:
        try:
            B.oos_pooled([arm], cash_yield=cash_yield, markets=(key,), per_market=1)
            ok.append(key)
        except NotApplicable as exc:
            bad[key] = exc.reason
    return ok, bad


def oos_pooled_delta(arm, ctrl_vec, control_arm=None, cash_yield="none", markets=None):
    """The G8 Δ vector (arm − control) on the same paths, plus a note.

    With every market applicable the control's cached six-market vector is used -- the published
    behaviour, unchanged. With a reduced subset the control is re-drawn *inside the same call*
    (the rng sequence depends only on the market list, so the two arms share their paths, which is
    what makes the Δ paired). A market whose chunk surprises the framework after the probe (a
    chunk-level `NotApplicable`) is dropped and the draw retried.
    """
    all_markets = list(OOS_POOLED_MARKETS)
    markets = list(markets) if markets else list(all_markets)
    note = {"markets": list(markets), "control": "cached six-market vector"}
    if len(markets) == len(all_markets):
        res = B.oos_pooled([arm], cash_yield=cash_yield, markets=tuple(markets))
        return res["pooled"][arm["id"]] - np.asarray(ctrl_vec, dtype=np.float64), note
    dropped = {}
    note["control"] = "re-drawn in the same draw"
    while markets:
        try:
            res = B.oos_pooled([arm, control_arm], cash_yield=cash_yield, markets=tuple(markets))
            d = res["pooled"][arm["id"]] - res["pooled"][control_arm["id"]]
            note["markets"] = list(markets)
            if dropped:
                note["dropped"] = dropped
            return d, note
        except NotApplicable as exc:
            dropped[markets.pop()] = exc.reason
    raise NotApplicable("no OOS market's pooled bootstrap could be drawn")


# ----------------------------------------------------------------------------- gates
def applicable_oos(rec) -> list:
    """The OOS windows of a stage-2 record that were actually evaluated."""
    return [k for k in OOS_WINDOWS if "cagr" in (rec.get("oos") or {}).get(k, {})]


def applicable_etfs(rec) -> list:
    """The instruments of a stage-2 record that were actually evaluated."""
    return [k for k in INSTRUMENTS if "after_tax_cagr" in (rec.get("etf_after_tax") or {}).get(k, {})]


def applicability(rec) -> dict:
    """What a stage-2 record could evaluate, and why the rest was skipped."""
    oos, etfs = applicable_oos(rec), applicable_etfs(rec)
    out = {"oos_applicable": oos, "n_oos_applicable": len(oos),
           "etf_applicable": etfs, "n_etf_applicable": len(etfs),
           "oos_min": OOS_MIN_APPLICABLE, "etf_min": ETF_MIN_APPLICABLE,
           "reasons": {k: v["reason"] for k, v in list((rec.get("oos") or {}).items())
                       + list((rec.get("etf_after_tax") or {}).items())
                       if isinstance(v, dict) and v.get("applicable") is False}}
    out["sufficient"] = bool(len(oos) >= OOS_MIN_APPLICABLE and len(etfs) >= ETF_MIN_APPLICABLE)
    return out


def _g_return(a, c):
    """G1/G6: CAGR >= control, or Calmar >= 1.1 x control with CAGR >= control - 0.5 pp."""
    if a["cagr"] >= c["cagr"]:
        return True
    return bool(a["calmar"] is not None and c["calmar"] is not None
                and a["calmar"] >= G1_CALMAR * c["calmar"] and a["cagr"] >= c["cagr"] - G1_CAGR_SLACK)


def pre_tax_gates(rec, ctrl):
    """G1-G9 of a candidate against the control (ewma_band_v1's definitions, G9 = <= 2 x K)."""
    g = {}
    for md in ("base", "calibrated"):
        for m in (1, 2):
            a, c = rec["main"][md][f"m{m}"], ctrl["main"][md][f"m{m}"]
            key = "G6" if m == 1 else "G1"
            g[f"{key}_{md}"] = _g_return(a, c)
        a, c = rec["main"][md]["m2"], ctrl["main"][md]["m2"]
        g[f"G2_{md}"] = bool(a["max_dd"] <= c["max_dd"] + G2_DD_PP / 100 + 1e-12)
        g[f"G3_{md}"] = bool(a["2017_end"]["cagr"] >= c["2017_end"]["cagr"] - G3_SLACK_PP / 100 - 1e-12)
    a, c = rec["main"]["base"]["m2"], ctrl["main"]["base"]["m2"]
    wins = [p for p, _x, _y in SUBPERIODS if a["subperiods"][p]["cagr"] > c["subperiods"][p]["cagr"]]
    g["G4"] = bool(len(wins) >= 2)
    g["G4_wins"] = wins
    g["G9"] = bool(a["turnover_annual"] <= G9_TURNOVER_MULT * c["turnover_annual"] + 1e-12
                   and rec["main"]["calibrated"]["m2"]["turnover_annual"]
                   <= G9_TURNOVER_MULT * ctrl["main"]["calibrated"]["m2"]["turnover_annual"] + 1e-12)
    if "boot" in rec:
        for md in ("base", "calibrated"):
            g[f"G5_{md}"] = bool(rec["boot"]["pre"][md]["median_delta_pp"] >= 0)
    if "oos" in rec:
        keys = applicable_oos(rec)
        k_app = len(keys)
        if k_app >= OOS_MIN_APPLICABLE:
            dc = {k: rec["oos"][k]["cagr"] - ctrl["oos"][k]["cagr"] for k in keys}
            dd = {k: rec["oos"][k]["max_dd"] - ctrl["oos"][k]["max_dd"] for k in keys}
            n_c = sum(1 for v in dc.values() if v >= G7_CAGR_SLACK_PP / 100 - 1e-12)
            n_d = sum(1 for v in dd.values() if v <= G7_DD_PP / 100 + 1e-12)
            # the published rule allows one outlier among the six (`>= 5 of 6`); on a reduced set
            # of k applicable windows it allows one outlier among those k.
            need = G7_MIN_WINDOWS if k_app == len(OOS_WINDOWS) else max(2, k_app - 1)
            g["G7"] = bool(float(np.median(list(dc.values()))) >= G7_MEDIAN and n_c >= need
                           and n_d >= need and max(dd.values()) <= G7_MAX_DD_PP / 100 + 1e-12)
            detail = {"delta_cagr_pp": {k: v * 100 for k, v in dc.items()},
                      "delta_max_dd_pp": {k: v * 100 for k, v in dd.items()},
                      "median_cagr_pp": float(np.median(list(dc.values())) * 100),
                      "n_cagr_within": n_c, "n_dd_within": n_d,
                      "max_dd_delta_pp": float(max(dd.values()) * 100)}
            if k_app < len(OOS_WINDOWS):
                detail["n_applicable"] = k_app
                detail["n_within_required"] = need
                detail["not_applicable"] = {k: rec["oos"][k]["reason"] for k in OOS_WINDOWS
                                            if k not in keys}
            g["G7_detail"] = detail
        else:
            g["G7"] = False
            g["G7_detail"] = {
                "not_evaluable": f"{k_app} applicable OOS windows of {len(OOS_WINDOWS)} "
                                 f"(need >= {OOS_MIN_APPLICABLE})",
                "n_applicable": k_app,
                "not_applicable": {k: rec["oos"][k]["reason"] for k in OOS_WINDOWS if k not in keys},
            }
    if (rec.get("oos_pooled") or {}).get("median_delta_pp") is not None:
        g["G8"] = bool(rec["oos_pooled"]["median_delta_pp"] >= 0)
        g["G8_median_pp"] = rec["oos_pooled"]["median_delta_pp"]
        if rec["oos_pooled"].get("markets") not in (None, list(OOS_POOLED_MARKETS)):
            g["G8_detail"] = {"markets": rec["oos_pooled"]["markets"],
                              "not_applicable": rec["oos_pooled"].get("markets_not_applicable", {})}
    return g


def stage1_gates(rec, ctrl):
    """The fixed stage-1 gates of a candidate against the control (all four are reported)."""
    return {
        "after_tax_2000_delta_pp": (rec["after_tax"]["2000_03_to_2026_09"]["after_tax_cagr"]
                                    - ctrl["after_tax"]["2000_03_to_2026_09"]["after_tax_cagr"]) * 100,
        "b1_median_delta_pp": rec["boot"]["tax"]["progressive"]["median_delta_pp"],
        "max_dd_delta_pp": {md: (rec["main"][md]["m2"]["max_dd"] - ctrl["main"][md]["m2"]["max_dd"]) * 100
                            for md in ("base", "calibrated")},
        "turnover_ratio": {md: rec["main"][md]["m2"]["turnover_annual"]
                           / ctrl["main"][md]["m2"]["turnover_annual"] for md in ("base", "calibrated")},
    }


def stage1_pass(rec, ctrl):
    """The pre-registered stage-1 pass rule. Returns `(pass, gates)`."""
    g = stage1_gates(rec, ctrl)
    ok = bool(g["after_tax_2000_delta_pp"] > 0 and g["b1_median_delta_pp"] > 0
              and all(v <= S1_MAX_DD_PP + 1e-9 for v in g["max_dd_delta_pp"].values())
              and all(v <= S1_TURNOVER_MULT + 1e-9 for v in g["turnover_ratio"].values()))
    return ok, g


def after_tax_gates(rec, ctrl):
    """The after-tax decision: all three windows at $100k, and B1/B2 median >= 0 with share > 0
    >= 55%."""
    g = {"windows": {}}
    for wn in TAX_WINDOWS:
        d = rec["after_tax"][wn]["after_tax_cagr"] - ctrl["after_tax"][wn]["after_tax_cagr"]
        g["windows"][wn] = {"delta_pp": d * 100, "pass": bool(d > 0)}
    for label, key in (("B1_recent", "progressive"), ("B2_flat", "flat")):
        cell = rec["boot"]["tax"][key]
        g[label] = {"median_delta_pp": cell["median_delta_pp"], "share_gt_0": cell["share_gt_0"],
                    "pass": bool(cell["median_delta_pp"] >= 0 and cell["share_gt_0"] >= SHARE_BOOT)}
    g["pass"] = bool(all(v["pass"] for v in g["windows"].values())
                     and g["B1_recent"]["pass"] and g["B2_flat"]["pass"])
    return g


def e1_gate(rec, ctrl):
    """The quality-passing ETFs after tax (R-INDEX): median delta > 0 and share >= 60%.

    Evaluated on the applicable instruments only; below `ETF_MIN_APPLICABLE` it is not evaluable
    (`gates()` then marks the arm `holdout_insufficient`).
    """
    keys = applicable_etfs(rec)
    ds = [rec["etf_after_tax"][k]["after_tax_cagr"] - ctrl["etf_after_tax"][k]["after_tax_cagr"]
          for k in keys]
    n_pos = sum(1 for d in ds if d > 0)
    share = n_pos / len(ds) if ds else 0.0
    out = {"median_delta_pp": float(np.median(ds) * 100) if ds else 0.0,
           "share_gt_0": float(share), "n_instruments": len(ds),
           "pass": bool(ds and float(np.median(ds)) > 0 and share >= E1_SHARE)}
    if len(keys) < len(INSTRUMENTS):
        out["applicable"] = keys
        out["not_applicable"] = {k: rec["etf_after_tax"][k]["reason"] for k in INSTRUMENTS
                                 if k not in keys}
        out["evaluable"] = bool(len(keys) >= ETF_MIN_APPLICABLE)
        out["pass"] = bool(out["pass"] and out["evaluable"])
    return out


def e2_gate(rec, ctrl):
    """The 21 January rolling starts, after tax: median delta >= 0 and share >= 60%."""
    ds = [rec["rolling"][k]["after_tax_cagr"] - ctrl["rolling"][k]["after_tax_cagr"]
          for k in sorted(rec["rolling"], key=int)]
    n_ge = sum(1 for d in ds if d >= 0)
    share = n_ge / len(ds)
    return {"median_delta_pp": float(np.median(ds) * 100), "share_ge_0": float(share),
            "n_starts": len(ds), "pass": bool(float(np.median(ds)) >= 0 and share >= E2_SHARE)}


def fresh_gate(rec, ctrl):
    """The fresh-seed bootstraps (seed 20261016, blocks 10/40): every cell median >= 0 and
    share >= 55%."""
    cells = {}
    ok = True
    for blk in rec["fresh"]:
        for md in ("progressive", "flat"):
            a = np.asarray(rec["fresh"][blk]["tax"][md], dtype=np.float64)
            c = np.asarray(ctrl["fresh"][blk]["tax"][md], dtype=np.float64)
            d = a - c
            cell = B.delta_stats(d)
            good = bool(cell["median_delta_pp"] >= 0 and cell["share_gt_0"] >= FRESH_SHARE)
            cells[f"{blk}_{md}"] = {"median_delta_pp": cell["median_delta_pp"],
                                    "share_gt_0": cell["share_gt_0"], "pass": good}
            ok = ok and good
    return {"cells": cells, "pass": bool(ok)}


def gates(rec, ctrl):
    """All gates of a stage-2 record, plus the verdict.

    An arm whose holdout is not evaluable (fewer than `OOS_MIN_APPLICABLE` applicable OOS windows,
    or fewer than `ETF_MIN_APPLICABLE` applicable instruments) is **not** a pass: `g["pass"]` is
    false, `g["holdout_insufficient"]` carries the counts and the reasons, and the scoreboard
    lists the arm for a manual confirmation study.
    """
    g = pre_tax_gates(rec, ctrl)
    g.update({f"AT_{k}": v for k, v in after_tax_gates(rec, ctrl).items()})
    g["E1"] = e1_gate(rec, ctrl)
    g["E2"] = e2_gate(rec, ctrl)
    g["FRESH"] = fresh_gate(rec, ctrl)
    av = applicability(rec)
    if not av["sufficient"]:
        g["holdout_insufficient"] = av
    keys = ["G1_base", "G1_calibrated", "G2_base", "G2_calibrated", "G3_base", "G3_calibrated",
            "G4", "G5_base", "G5_calibrated", "G6_base", "G6_calibrated", "G7", "G8", "G9"]
    failed = [k for k in keys if not g.get(k, False)]
    at = after_tax_gates(rec, ctrl)
    if not at["pass"]:
        failed += [f"AT_{k}" for k in TAX_WINDOWS if not at["windows"][k]["pass"]]
        failed += [k for k in ("B1_recent", "B2_flat") if not at[k]["pass"]]
    failed += [k for k in ("E1", "E2", "FRESH") if not g[k]["pass"]]
    if not av["sufficient"]:
        failed.append("HOLDOUT_INSUFFICIENT")
    g["failed"], g["pass"] = failed, (not failed)
    return g


def control_ready(ctrl):
    """True when the control record has everything stage 2 needs."""
    need = ("main", "after_tax", "boot", "oos", "oos_pooled", "etf_pre", "etf_after_tax",
            "rolling", "fresh")
    return all(k in ctrl for k in need)


__all__ = [
    "after_tax_cells",
    "applicability",
    "applicable_etfs",
    "applicable_oos",
    "control_ready",
    "e1_gate",
    "e2_gate",
    "fresh_gate",
    "gates",
    "after_tax_gates",
    "oos_pooled_delta",
    "oos_pooled_markets",
    "pre_tax_gates",
    "run_one",
    "stage1",
    "stage1_vectors",
    "stage2_fresh",
    "stage2_instruments",
    "stage2_main_windows",
    "stage2_oos",
    "stage2_oos_pooled",
    "stage2_rolling",
]
