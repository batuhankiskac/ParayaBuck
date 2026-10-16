"""fe_study -- the study-level stages, so a new study does not write them again.

Every stage the `band_h2_combo_v1` / `gain_band_v3` / `tax_harvest_v2` campaigns share, on the
numba engine and the `fe_data` loaders, returning the same result dictionaries the current
studies write into RESULTS.json:

  pre_tax_main           pre-tax main window, base/calibrated x m = 1/2/4 + the R-USD row
  after_tax_windows      after tax (progressive) on the three windows at the given capitals
  cost_stress            m = 4 after-tax stress at the gate capital
  oos_stages             the six OOS windows (^GSPC 1957-1999 + M1-M5), pre-tax, calibrated
  instrument_stages_usd  the 21 quality-passing ETFs, pre-tax, calibrated, R-USD
  stage_e1               after tax on the 21 ETFs under R-INDEX
  stage_e2               rolling January starts 2001..2021, after tax
  stage_e3               fresh after-tax bootstraps (seed 20261015, blocks 10/40)
  stage_e5_decisions     the arms' fill-decision traces

A study that needs a new window/rule writes the target-weight arrays (numpy) and calls
`fe_sim.simulate` / `fe_boot.boot`; everything else here is the published aggregation.
"""

from __future__ import annotations

import bisect

import numpy as np

from . import fe_boot, fe_data, fe_signals, fe_sim, fe_tax
from .fe_boot import boot, oos_positions
from .fe_config import (
    E1_RINDEX_START,
    E2_START_YEARS,
    E3_BLOCKS,
    E3_PATHS,
    E3_SEED,
    EXCLUDED_INSTRUMENTS,
    EXP_Q,
    EXTRA_FIN,
    FIN,
    GATE_CAPITAL,
    INSTRUMENT_FILES,
    INSTRUMENTS,
    MARKETS,
    M_STRESS,
    OOS_BOOT_CHUNK,
    OOS_BOOT_L,
    OOS_PATHS_PER_MARKET,
    BLOCK,
    OOS_WINDOWS,
    SEED,
    START,
    TAX_CAPITALS,
    TAX_WINDOWS,
    M,
)
from .fe_sim import (
    ARMS,
    BASELINE,
    CONTROL,
    RULE,  # noqa: F401  (re-exported for study code)
    cagr,
    calendar_years,
    max_dd,
    simulate,
)

SUBPERIODS = (("P1", None, "2008-12-31"), ("P2", "2008-12-31", "2016-12-31"), ("P3", "2016-12-31", None))


# ----------------------------------------------------------------------------- stats
def calmar(c, dd):
    """`trend_robustness_v2.calmar`."""
    return float(c / dd) if dd and dd > 1e-12 else None


def seg(dates, nav, a, b):
    """`screen.seg`: from the last close <= a (first bar if a is None) to the last close <= b."""
    ia = 0 if (a is None or dates[0] > a) else max(k for k, d in enumerate(dates) if d <= a)
    ib = len(dates) - 1 if b is None else max(k for k, d in enumerate(dates) if d <= b)
    s = nav[ia : ib + 1]
    yrs = calendar_years(dates[ia], dates[ib])
    return {
        "from": dates[ia],
        "to": dates[ib],
        "total_return": float(s[-1] / s[0] - 1),
        "cagr": float((s[-1] / s[0]) ** (1 / yrs) - 1),
        "max_dd": float(max_dd(s)[0]),
    }


def pre_stats(days, i0, i1, res, real_days=None):
    """`screen.pre_stats`: pre-tax stats of one engine run (fast_reentry_v1's NAV convention)."""
    d = list(real_days if real_days is not None else days)
    dates = d[i0 - 1 : i1 + 1]
    nav = res["_nav"]
    years = calendar_years(dates[0], dates[-1])
    st = {
        "cagr": float((nav[-1] / nav[0]) ** (1 / years) - 1),
        "cagr_liquidated": float(res["cagr_usd_liquidated"]),
        "max_dd": float(max_dd(nav.reshape(1, -1))[0]),
        "turnover_annual": float(res["turnover_total"] / years),
        "trades": int(res["strategy_trades"]),
        "sells_per_year": float(res["qld_sells"] / years),
        "postponements": int(res["postponements"]),
        "blocked_band": int(res["blocked_band"]),
        "deferred": int(res["deferred"]),
        "years": float(years),
    }
    st["calmar"] = calmar(st["cagr"], st["max_dd"])
    st["2017_end"] = seg(dates, nav, "2016-12-31", None)
    st["subperiods"] = {n: seg(dates, nav, a, b) for n, a, b in SUBPERIODS}
    return st


def window_stats(nav, days, start):
    """`screen.window_stats`."""
    yrs = calendar_years(days[start - 1], days[-1])
    return {
        "cagr": float(cagr(nav[0], nav[-1], yrs)),
        "max_dd": float(max_dd(nav.reshape(1, -1))[0]),
        "years": float(yrs),
        "window": {"start": days[start], "end": days[-1], "sessions": len(days) - start},
    }


def try_market(days):
    """`te.load_market(FX_CSV, UFE_CSV, days)`: (fx, ufe) for the TRY-indexed engine."""
    return fe_data.fx_at(days), fe_data.load_ufe()


# ----------------------------------------------------------------------------- stages
def pre_tax_main(arms=ARMS):
    """Pre-tax main window (base/calibrated x m = 1/2/4) plus the R-USD main-window row."""
    days, o, c, w_e2, w_r2, e2 = fe_data.main_inputs(0.0)
    fx, ufe = try_market(days)
    T = len(days) - 1
    env = fe_sim.RunEnv(days, fx, ufe, START, T)
    env_usd = fe_sim.RunEnv(days, *fe_data.usd_market(days), START, T)
    pre, pre_usd = {}, {}
    for md, spread in FIN.items():
        d_, o_, c_, we_, wr_, e_ = fe_data.main_inputs(spread)
        W = {arm: wr_ for arm in arms}
        pre[md], pre_usd[md] = {}, {}
        for m in (1, 2, M_STRESS):
            pre[md][f"m{m}"] = {}
            for arm in arms:
                r = simulate(o_, c_, W[arm], env, TAX_CAPITALS[GATE_CAPITAL], START, T, arm=arm, mode="pre_tax", m=m)
                pre[md][f"m{m}"][arm] = pre_stats(d_, START, T, r)
        for arm in arms:
            r = simulate(
                o_,
                c_,
                W[arm],
                env_usd,
                TAX_CAPITALS[GATE_CAPITAL],
                START,
                T,
                arm=arm,
                mode="pre_tax",
                m=M,
                rule_index=False,
            )
            pre_usd[md][arm] = pre_stats(d_, START, T, r, real_days=d_)
    pre["window"] = {
        "start": days[START],
        "end": days[-1],
        "sessions": T - START + 1,
        "years": float(calendar_years(days[START - 1], days[-1])),
    }
    return pre, pre_usd


def after_tax_windows(capitals, arms=ARMS):
    """After tax at the given capitals (progressive, liquidated, base financing, m = 2)."""
    days, o, c, w_e2, w_r2, e2 = fe_data.main_inputs(0.0)
    fx, ufe = try_market(days)
    win = {}
    for cap_label in capitals:
        win[cap_label] = {}
        for wn, (a, b) in TAX_WINDOWS.items():
            i0, i1 = window_indices(days, a, b)
            env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
            win[cap_label][wn] = {}
            for arm in arms:
                rq = simulate(o, c, w_r2, env, TAX_CAPITALS[cap_label], i0, i1, arm=arm, mode="progressive")
                # v2's convention: the pre-tax leg of these rows runs at capital 1.0
                rp = simulate(o, c, w_r2, env, 1.0, i0, i1, arm=arm, mode="pre_tax")
                win[cap_label][wn][arm] = {
                    "pre_tax_cagr": rp["cagr_usd_liquidated"],
                    "after_tax_cagr": rq["cagr_usd_liquidated"],
                    "after_tax_cagr_unliquidated": rq["cagr_usd_unliquidated"],
                    "max_dd_after_tax": rq["max_dd_nav_usd"],
                    "terminal_usd": rq["terminal_usd_liquidated"],
                    "terminal_usd_unliquidated": rq["terminal_usd_unliquidated"],
                    "tax_usd": rq["tax_total_usd_at_payment_fx"],
                    "sells_per_year": rq["sells"] / rq["years"],
                    "trades_per_year": rq["strategy_trades"] / rq["years"],
                    "postponements": rq["postponements"],
                    "blocked_band": rq["blocked_band"],
                    "deferred": rq["deferred"],
                    "postponements_by_year": rq["postponements_by_year"],
                    "deferrals_by_year": rq["deferrals_by_year"],
                    "index_relief_try": rq["index_relief_try"],
                }
    return win


def window_indices(days, a, b):
    """(i0, i1) of a [a, b] date window: `screen.engine_run`'s bisect convention."""
    return bisect.bisect_left(days, a), bisect.bisect_right(days, b) - 1


def cost_stress(arms=ARMS):
    """m = 4 cost stress, after tax at $100k on the three windows."""
    days, o, c, w_e2, w_r2, e2 = fe_data.main_inputs(0.0)
    fx, ufe = try_market(days)
    cost = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        i0, i1 = window_indices(days, a, b)
        env = fe_sim.RunEnv(days, fx, ufe, i0, i1)
        cost[wn] = {}
        for arm in arms:
            rq = simulate(o, c, w_r2, env, TAX_CAPITALS[GATE_CAPITAL], i0, i1, arm=arm, mode="progressive", m=M_STRESS)
            rp = simulate(o, c, w_r2, env, 1.0, i0, i1, arm=arm, mode="pre_tax", m=M_STRESS)
            cost[wn][arm] = {
                "after_tax_cagr": rq["cagr_usd_liquidated"],
                "pre_tax_cagr": rp["cagr_usd_liquidated"],
                "max_dd_after_tax": rq["max_dd_nav_usd"],
                "turnover_annual": rp["turnover_annual"],
                "postponements": rq["postponements"],
            }
    return cost


def oos_window(days, o, c, y, start, arms=ARMS):
    """One OOS window: own E2 + own R2 early path, own 2x leg (calibrated, m = 2), R-USD rule."""
    so, sc = fe_signals.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = fe_signals.e2_up(c)[0]
    comps = fe_signals.early_components(c)
    _w_e2, w_r2 = fe_signals.wants_of(c, e2, fe_signals.vol_n(sc)[0], comps)
    env = fe_sim.RunEnv(days, *fe_data.usd_market(days), start, len(days) - 1)
    res = {"window": {"start": days[start], "end": days[-1], "sessions": len(days) - start}}
    for arm in arms:
        r = simulate(so, sc, w_r2, env, 1.0, start, len(days) - 1, arm=arm, mode="pre_tax", m=M, rule_index=False)
        st = window_stats(r["_nav"], days, start)
        st["turnover_annual"] = float(r["turnover_annual"])
        st["postponements"] = int(r["postponements"])
        res[arm] = st
    return res


def oos_stages(arms=ARMS):
    """Six OOS windows (^GSPC 1957-1999 + M1-M5), pre-tax, calibrated, m = 2, R-USD rule."""
    oos = {}
    gd, go, gc, gy, gstart = fe_data.load_gspc()
    oos["GSPC_1957_1999"] = oos_window(gd, go, gc, gy, gstart, arms)
    for key in ("M1", "M2", "M3", "M4", "M5"):
        md_, mo_, mc_, my_, mstart_ = fe_data.load_market(key)
        oos[key] = oos_window(md_, mo_, mc_, my_, mstart_, arms)
    for wn in OOS_WINDOWS:
        for arm in arms:
            if arm == CONTROL:
                continue
            oos[wn][arm]["delta_cagr_pp"] = (oos[wn][arm]["cagr"] - oos[wn][CONTROL]["cagr"]) * 100
            oos[wn][arm]["delta_max_dd_pp"] = (oos[wn][arm]["max_dd"] - oos[wn][CONTROL]["max_dd"]) * 100
    return oos


def instrument_window(days, o, c, y, arms=ARMS, start=START):
    """`screen.instrument_window`: own E2 + own R2 early path, own 2x leg (R-USD rule)."""
    so, sc = fe_signals.synthetic_2x(o, c, np.zeros_like(c), y + EXTRA_FIN, EXP_Q)
    e2 = fe_signals.e2_up(c)[0]
    comps = fe_signals.early_components(c)
    _w_e2, w_r2 = fe_signals.wants_of(c, e2, fe_signals.vol_n(sc)[0], comps)
    env = fe_sim.RunEnv(days, *fe_data.usd_market(days), start, len(days) - 1)
    res = {}
    for arm in arms:
        r = simulate(so, sc, w_r2, env, 1.0, start, len(days) - 1, arm=arm, mode="pre_tax", m=M, rule_index=False)
        st = window_stats(r["_nav"], days, start)
        st["turnover_annual"] = float(r["turnover_annual"])
        st["postponements"] = int(r["postponements"])
        res[arm] = st
    return res


def instrument_stages_usd(arms=ARMS):
    """The 21 quality-passing ETFs, pre-tax, calibrated, m = 2, R-USD rule (+ quality record)."""
    inst, quality = {}, {}
    for key in sorted(INSTRUMENT_FILES):
        idays, io, ih, il, ic, iy = fe_data.load_instrument(key)
        frac, ok = fe_data.quality_ok(ih, il, io, ic)
        quality[key] = {"bad_frac": float(frac), "pass": bool(ok), "sessions": len(idays)}
        if key in EXCLUDED_INSTRUMENTS:
            continue
        r = instrument_window(idays, io, ic, iy, arms)
        for arm in arms:
            if arm == CONTROL:
                continue
            r[arm]["delta_cagr_pp"] = (r[arm]["cagr"] - r[CONTROL]["cagr"]) * 100
            r[arm]["delta_max_dd_pp"] = (r[arm]["max_dd"] - r[CONTROL]["max_dd"]) * 100
        inst[key] = r
    return inst, quality


def stage_e1(arms=ARMS):
    """E1: after tax on the 21 quality-passing ETFs under R-INDEX, $100k, calibrated, m = 2."""
    out = {}
    for key in INSTRUMENTS:
        idays, io, ih, il, ic, iy = fe_data.load_instrument(key)
        start = max(START, bisect.bisect_left(idays, E1_RINDEX_START))
        so, sc = fe_signals.synthetic_2x(io, ic, np.zeros_like(ic), iy + EXTRA_FIN, EXP_Q)
        e2 = fe_signals.e2_up(ic)[0]
        _w_e2, w_r2 = fe_signals.wants_of(ic, e2, fe_signals.vol_n(sc)[0], fe_signals.early_components(ic))
        env = fe_sim.RunEnv(idays, *try_market(idays), start, len(idays) - 1)
        rec = {"window": {"start": idays[start], "end": idays[-1], "sessions": len(idays) - start}}
        for arm in arms:
            rq = simulate(
                so, sc, w_r2, env, TAX_CAPITALS[GATE_CAPITAL], start, len(idays) - 1, arm=arm, mode="progressive"
            )
            rp = simulate(so, sc, w_r2, env, TAX_CAPITALS[GATE_CAPITAL], start, len(idays) - 1, arm=arm, mode="pre_tax")
            rec[arm] = {
                "after_tax_cagr": rq["cagr_usd_liquidated"],
                "pre_tax_cagr": rp["cagr_usd_liquidated"],
                "max_dd_after_tax": rq["max_dd_nav_usd"],
                "terminal_usd": rq["terminal_usd_liquidated"],
                "tax_usd": rq["tax_total_usd_at_payment_fx"],
                "years": float(rq["years"]),
                "postponements": rq["postponements"],
                "blocked_band": rq["blocked_band"],
                "deferred": rq["deferred"],
                "sells_per_year": rq["sells"] / rq["years"],
            }
        for arm in arms:
            if arm == CONTROL:
                continue
            rec[arm]["delta_after_tax_pp"] = (rec[arm]["after_tax_cagr"] - rec[CONTROL]["after_tax_cagr"]) * 100
            rec[arm]["delta_pre_tax_pp"] = (rec[arm]["pre_tax_cagr"] - rec[CONTROL]["pre_tax_cagr"]) * 100
            rec[arm]["delta_max_dd_after_tax_pp"] = (
                rec[arm]["max_dd_after_tax"] - rec[CONTROL]["max_dd_after_tax"]
            ) * 100
        out[key] = rec
    return out


def stage_e2(arms=ARMS):
    """E2: rolling January starts 2001..2021 on the QQQ main series, after tax at $100k."""
    days, o, c, w_e2, w_r2, e2 = fe_data.main_inputs(0.0)
    fx, ufe = try_market(days)
    T = len(days) - 1
    out = {}
    for yr in E2_START_YEARS:
        i0 = bisect.bisect_left(days, f"{yr}-01-01")
        env = fe_sim.RunEnv(days, fx, ufe, i0, T)
        rec = {"start": days[i0], "index": int(i0)}
        for arm in arms:
            rq = simulate(o, c, w_r2, env, TAX_CAPITALS[GATE_CAPITAL], i0, T, arm=arm, mode="progressive")
            rp = simulate(o, c, w_r2, env, TAX_CAPITALS[GATE_CAPITAL], i0, T, arm=arm, mode="pre_tax")
            rec[arm] = {
                "after_tax_cagr": rq["cagr_usd_liquidated"],
                "pre_tax_cagr": rp["cagr_usd_liquidated"],
                "max_dd_after_tax": rq["max_dd_nav_usd"],
                "terminal_usd": rq["terminal_usd_liquidated"],
                "tax_usd": rq["tax_total_usd_at_payment_fx"],
                "years": float(rq["years"]),
                "sells_per_year": rq["sells"] / rq["years"],
            }
        for arm in arms:
            if arm == CONTROL:
                continue
            rec[arm]["delta_after_tax_pp"] = (rec[arm]["after_tax_cagr"] - rec[CONTROL]["after_tax_cagr"]) * 100
        out[str(yr)] = rec
    return out


def stage_e3(arms=ARMS, window=None, quiet=True):
    """E3: fresh after-tax bootstraps -- seed 20261015, blocks 10 and 40, 1,000 paths, $100k."""
    out = {}
    for block in E3_BLOCKS:
        for mode in ("progressive", "flat"):
            b = boot(
                seed=E3_SEED,
                block=block,
                arms=arms,
                n_pre=0,
                n_tax=E3_PATHS,
                capitals=(GATE_CAPITAL,),
                modes=(mode,),
                anchor_e2=False,
                deltas=tuple(a for a in arms if a != CONTROL),
                baselines=(CONTROL,),
                label=f"E3 block {block} {mode}",
                window=window,
                quiet=quiet,
            )
            out[f"block{block}_{mode}"] = b["tax"][GATE_CAPITAL][mode]
    return out


def stage_e5_decisions():
    """E5 (report only): fill-decision traces on the main path (pre-tax, $100k, m = 2)."""
    days, o, c, w_e2, w_r2, e2 = fe_data.main_inputs(0.0)
    fx, ufe = try_market(days)
    T = len(days) - 1
    env = fe_sim.RunEnv(days, fx, ufe, START, T)
    traces = {
        arm: simulate(o, c, w_r2, env, TAX_CAPITALS[GATE_CAPITAL], START, T, arm=arm, mode="pre_tax", trace=True)
        for arm in ARMS
    }
    out = {
        "window": {"start": days[START], "end": days[-1], "sessions": int(len(traces[CONTROL]["_dec"]))},
        "sells": {},
        "postponements": {},
    }
    for arm in ARMS:
        r = traces[arm]
        out["sells"][arm] = int(np.sum(r["_dec"] == 2))
        out["postponements"][arm] = {"band": int(r["blocked_band"]), "deferred": int(r["deferred"])}
    for a, b in (("K", BASELINE), ("K", CONTROL), (BASELINE, CONTROL), ("H2", BASELINE), ("H2", CONTROL)):
        da, db = traces[a]["_dec"], traces[b]["_dec"]
        out[f"{a}_vs_{b}"] = {
            "sessions_decision_differs": int(np.sum(da != db)),
            "share_decision_differs": float(np.mean(da != db)),
            f"{a}_sells_{b}_holds": int(np.sum((da == 2) & (db != 2))),
            f"{a}_holds_{b}_sells": int(np.sum((da != 2) & (db == 2))),
        }
    return out


# ----------------------------------------------------------------------------- the pooled OOS bootstrap (G8)
def oos_boot_market(key, rng, arms, control, block=BLOCK):
    """`screen.boot_oos_market` for one market, with the simulation in the numba kernel.

    The draws are the published ones (three rng calls per 500-path chunk, markets in `MARKETS`
    order from one shared generator), the market reconstruction and the R2 target series are
    `fe_signals` batched, and the per-path run is `fe_boot._boot_chunk`'s pre-tax leg.
    """
    days, o, c, y, start = fe_data.load_market(key)
    if len(days) < OOS_BOOT_L:
        raise RuntimeError(f"{key} has only {len(days)} sessions, the OOS bootstrap needs {OOS_BOOT_L}")
    pool = np.arange(start, len(days))
    npool = len(pool)
    env = fe_sim.RunEnv(days, *fe_data.usd_market(days), START, OOS_BOOT_L - 1)
    n_arms = len(arms)
    band = np.array([fe_sim.RULE[a]["band"] for a in arms], dtype=np.bool_)
    usd_rule = np.array([fe_sim.RULE[a]["usd_rule"] for a in arms], dtype=np.bool_)
    defer = np.array([fe_sim.RULE[a]["defer"] for a in arms], dtype=np.bool_)
    dfr_trig = np.array([fe_sim.RULE[a]["dfr_trig"] for a in arms], dtype=np.int64)
    dfr_state = np.array([fe_sim.RULE[a]["dfr_state"] for a in arms], dtype=np.int64)
    dfr_max = np.array([fe_sim.RULE[a]["dfr_max"] for a in arms], dtype=np.int64)
    dfr_drift = np.array([fe_sim.RULE[a]["dfr_drift"] for a in arms], dtype=np.float64)
    dfr_ufe_lag = np.array([fe_sim.RULE[a]["dfr_ufe_lag"] for a in arms], dtype=np.int64)
    acc = {a: [] for a in arms}
    for chunk in range(0, OOS_PATHS_PER_MARKET, OOS_BOOT_CHUNK):
        P = min(OOS_BOOT_CHUNK, OOS_PATHS_PER_MARKET - chunk)
        pos = oos_positions(rng, block, P, OOS_BOOT_L, npool)
        idx = pool[pos]
        r_on = o[idx] / c[idx - 1]
        r_id = c[idx] / o[idx]
        cc = np.concatenate([np.ones((P, 1)), np.cumprod(r_on * r_id, axis=1)], axis=1)
        oo = np.concatenate([np.ones((P, 1)), cc[:, :-1] * r_on], axis=1)
        yy = y[idx - 1]
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        e2 = fe_signals.e2_up(cc)
        so, sc = fe_signals.synthetic_2x(oo, cc, np.zeros_like(cc), yyp + EXTRA_FIN, EXP_Q)
        _w_e2, w_r2 = fe_signals.wants_of(cc, e2, fe_signals.vol_n(sc))
        out_pre = np.empty((2, n_arms, P), dtype=np.float64)
        fe_boot._boot_chunk(
            so, sc, w_r2, so, sc, w_r2, w_r2,
            env.fx, env.upm, env.mth_of, env.yr_of, env.scale_y, env.dec_tail,
            env.pay_ptr, env.pay_yr, env.pay_part, 1.0, np.array([1.0]), START, OOS_BOOT_L - 1,
            fe_sim.COST_PER_SIDE * M, 1.0, env.Y0, env.M_lo,
            band, usd_rule, defer, dfr_trig, dfr_state, dfr_max, dfr_drift, dfr_ufe_lag,
            env.mtail, env.dec_wide, env.early_m, env.upm2,
            np.array([fe_tax.MODE_PRE_TAX], dtype=np.int64), 0, P, 0, False,
            out_pre, np.empty((1, 1, n_arms, 1)), np.empty((1, 1)),
            np.zeros((0, 6), dtype=np.float64), np.zeros((0, 5), dtype=np.float64),
        )
        for ai, a in enumerate(arms):
            acc[a].append(out_pre[0, ai])
    return {a: np.concatenate(v) for a, v in acc.items()}


def oos_bootstrap(arms=ARMS, control=CONTROL):
    """Pooled OOS bootstrap (G8): block-20 over M1-M5, seed SEED, 2,000 paths each, R-USD rule."""
    rng = np.random.default_rng(SEED)
    per = {k: oos_boot_market(k, rng, arms, control) for k in MARKETS}
    pooled = {a: np.concatenate([per[k][a] for k in MARKETS]) for a in arms}
    out = {"per_market": {}, "pooled": {}, "control": control, "paths_per_market": OOS_PATHS_PER_MARKET}
    for k in MARKETS:
        out["per_market"][k] = {
            a: {"median_diff_pp": float(np.median(per[k][a] - per[k][control]) * 100)} for a in arms
        }
    for a in arms:
        if a == control:
            continue
        d = pooled[a] - pooled[control]
        out["pooled"][a] = {
            "diff_vs_control_median_pp": float(np.median(d) * 100),
            "diff_vs_control_mean_pp": float(np.mean(d) * 100),
            "share_gt_0": float(np.mean(d > 0)),
            "paths": int(len(d)),
        }
    return out
