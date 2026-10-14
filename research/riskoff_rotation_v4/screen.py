#!/usr/bin/env python3
"""riskoff_rotation_v4 screen. Offline; reads data/historical only."""
from __future__ import annotations
import csv, importlib.util, json, math, bisect
from collections import defaultdict
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

def imp(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

trv2 = imp("trv2", ROOT/"research/trend_robustness_v2/screen.py")
lf = imp("lf", ROOT/"research/leverage_frontier_v1/screen.py")
v3 = imp("v3", ROOT/"research/riskoff_rotation_v3/screen.py")

CAP = trv2.CAP
COST_PER_SIDE = trv2.COST_PER_SIDE
START = v3.START
SEED, PATHS = 20261011, 2000
CHUNK = 500
BLOCK = 20
WARM = lf.WARM
MEASURED = lf.MEASURED
EXT = 3
FIN_EXTRA = 0.0075
EXPENSE = lf.EXPENSE[2.0]

OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"
DTB3_CSV = ROOT / "data/historical" / "stress_robustness_v1_dtb3.csv"
ETF_CSV = ROOT / "data/historical" / "riskoff_rotation_v4_etfs.csv"

EQUITY_ETFS = ["XLB","XLE","XLF","XLI","XLK","XLP","XLU","XLV","XLY",
    "EWJ","EWG","EWU","EWA","EWH","EWS","EWL","EFA","EEM","SPY","DIA","IWM"]

# ---------- data
def load_bars():
    by = defaultdict(dict)
    with ETF_CSV.open() as fh:
        for r in csv.DictReader(fh):
            by[r["symbol"]][r["date"]] = (float(r["open"]),float(r["high"]),float(r["low"]),float(r["close"]),float(r["adjclose"]),float(r["dividend"]))
    return by
_BARS=None
def bars():
    global _BARS
    if _BARS is None: _BARS=load_bars()
    return _BARS

def load_rates():
    d3=[]
    with DTB3_CSV.open() as fh:
        for r in csv.DictReader(fh):
            if r["DTB3"] not in (".",""):
                d3.append((r["observation_date"], float(r["DTB3"])/100))
    return d3
_D3=None
def rates():
    global _D3
    if _D3 is None: _D3=load_rates()
    return _D3
def rate_on(dates, d3):
    out=[]; last=None; it=0
    for d in dates:
        while it<len(d3) and d3[it][0]<=d:
            last=d3[it][1]; it+=1
        if last is None: raise RuntimeError(d)
        out.append(last)
    return np.array(out, dtype=float)

def get_bars(sym):
    b = bars()[sym]
    dates = sorted(b)
    o=np.array([b[d][0] for d in dates]); h=np.array([b[d][1] for d in dates])
    l=np.array([b[d][2] for d in dates]); c=np.array([b[d][3] for d in dates])
    a=np.array([b[d][4] for d in dates]); dv=np.array([b[d][5] for d in dates])
    return dates,o,h,l,c,a,dv

def aligned(sym):
    ed, eo, eh, el, ec, ea, edv = get_bars(sym)  # raw close eq
    idates, io_, ih, il, ic, ia, idv = get_bars("IEF")
    gdates, go, gh, gl, gc, ga, gdv = get_bars("GLD")
    common = sorted(set(ed)&set(idates)&set(gdates))
    def pick(dates_all, arrs, common):
        m = {d:i for i,d in enumerate(dates_all)}
        out=[arrs[m[d]] for d in common]
        return np.array(out, dtype=float)
    eo_a=pick(ed, eo, common); ec_a=pick(ed, ec, common); edv_a=pick(ed, edv, common)
    # total-return adjusted equity open? risk asset handled separately (synthetic on raw close+div)
    # IEF/GLD total return
    io_a=pick(idates, io_, common); ic_a=pick(idates, ic, common); ia_a=pick(idates, ia, common)
    ief_open_tr = io_a*(ia_a/ic_a)
    ief_close_tr = ia_a
    go_a=pick(gdates, go, common); gc_a=pick(gdates, gc, common); ga_a=pick(gdates, ga, common)
    gld_open_tr = go_a*(ga_a/gc_a)
    gld_close_tr = ga_a
    y = rate_on(common, rates())
    return {
        "sym":sym, "days":common,
        "eq_open":eo_a, "eq_high":pick(ed,eh,common), "eq_low":pick(ed,el,common),
        "eq_close":ec_a, "eq_div":edv_a,
        "ief_open":ief_open_tr, "ief_close":ief_close_tr,
        "gld_open":gld_open_tr, "gld_close":gld_close_tr,
        "y":y,
    }

# ---------- signals/helpers
def e2_up_1d(price):
    return v3.e2_up(price)

def trend_up_1d(close):
    return v3.trend_up(close)

def monthly_choice_1d(chosen_bond, up, days):
    return v3.monthly_choice_1d(chosen_bond, up, days)

def bills_levels(y):
    g = lf.bills_gross(y)
    opn, cls = lf.levels(g, np.where(np.isnan(g), np.nan, 1.0))  # levels expects overnight,intraday; bills all intraday?lf levels(gross_on,gross_id)
    # correct: lf.levels(gross_on, gross_id); bills_gross returns per-session gross all-intraday? Use same as lf: levels(np.ones_like(g), g) maybe?
    return None

# fix bills levels using lf.levels with overnight=1
def bills_close_levels(y):
    g = lf.bills_gross(y)
    on = np.ones_like(g)
    opn, cls = lf.levels(on, g)
    return cls

def make_legs(d, arm, spread):
    days = d["days"]
    y = d["y"]
    # risk asset synthetic 2x
    syn_o, syn_c = lf.synthetic_L(d["eq_open"], d["eq_close"], d["eq_div"], y, 2.0, EXPENSE, spread)
    up = v3.e2_up(d["eq_close"])  # 1d
    eq_vol = trv2.vol_n(syn_c[np.newaxis,:])[0]
    eq_target = up.astype(float)*np.minimum(CAP, trv2.vt_of(eq_vol))

    ief_c = d["ief_close"]; gld_c = d["gld_close"]
    ief_o = d["ief_open"]; gld_o = d["gld_open"]
    bond_vol = trv2.vol_n(ief_c[np.newaxis,:])[0]
    gold_vol = trv2.vol_n(gld_c[np.newaxis,:])[0]
    bond_ret = v3.ret252(ief_c)
    gold_ret = v3.ret252(gld_c)
    bills_c = bills_close_levels(y)
    bills_ret = v3.ret252(bills_c)
    bond_trend = v3.trend_up(ief_c)
    gold_trend = v3.trend_up(gld_c)

    # chosen bond daily
    gold_nan = np.isnan(gold_ret); bond_nan=np.isnan(bond_ret)
    chosen_bond_daily = np.where(gold_nan & ~bond_nan, True,
                      np.where(bond_nan & ~gold_nan, False,
                      np.where(~bond_nan & ~gold_nan, bond_ret >= gold_ret, False)))
    cond_daily = ~np.isnan(np.where(chosen_bond_daily, bond_ret, gold_ret)) & ~np.isnan(bills_ret) & (np.where(chosen_bond_daily, bond_ret, gold_ret) > bills_ret)

    if arm=="C0":
        opens=syn_o[np.newaxis,:]; closes=syn_c[np.newaxis,:]; wants=eq_target[np.newaxis,:]
        return opens, closes, wants

    # L2
    if arm=="L2":
        chosen_bond = v3.monthly_choice_1d(chosen_bond_daily, up, days)
    else:
        chosen_bond = chosen_bond_daily
    chosen_ret = np.where(chosen_bond, bond_ret, gold_ret)
    cond = ~np.isnan(chosen_ret) & ~np.isnan(bills_ret) & (chosen_ret > bills_ret)

    bond_vt_w = np.where(~np.isnan(bond_vol), np.minimum(1.0, 0.10 / np.where(bond_vol>0,bond_vol,1.0)), 0.0)
    gold_vt_w = np.where(~np.isnan(gold_vol), np.minimum(1.0, 0.10 / np.where(gold_vol>0,gold_vol,1.0)), 0.0)

    bond_active = (~up) & chosen_bond & cond
    gold_active = (~up) & (~chosen_bond) & cond
    bond_w = np.where(bond_active, bond_vt_w, 0.0)
    gold_w = np.where(gold_active, gold_vt_w, 0.0)

    opens = np.stack([syn_o, ief_o, gld_o], axis=0)
    closes = np.stack([syn_c, ief_c, gld_c], axis=0)
    wants = np.stack([eq_target, bond_w, gold_w], axis=0)
    wants = v3.cap_total(wants)
    return opens, closes, wants

def run_instrument(sym, spread=FIN_EXTRA, m=2, cost_mult=None):
    if cost_mult is None: cost_mult=m
    d = aligned(sym)
    if len(d["days"]) < START+50:
        return None
    # C0
    oC,cC,wC = make_legs(d,"C0",spread)
    oC=oC[np.newaxis,:,:] if oC.ndim==2 else oC
    cC=cC[np.newaxis,:,:] if cC.ndim==2 else cC
    wC=wC[np.newaxis,:,:] if wC.ndim==2 else wC
    navC,turnC,ntrC = trv2.simulate_multi(oC,cC,wC,COST_PER_SIDE*m,START)
    navC=navC[0]; turnC=turnC[0]; ntrC=ntrC[0]
    # L2
    oL,cL,wL = make_legs(d,"L2",spread)
    oL=oL[np.newaxis,:,:] if oL.ndim==2 else oL
    cL=cL[np.newaxis,:,:] if cL.ndim==2 else cL
    wL=wL[np.newaxis,:,:] if wL.ndim==2 else wL
    navL,turnL,ntrL = v3.simulate_lows(oL,cL,wL,COST_PER_SIDE*m,START)
    navL=navL[0]; turnL=turnL[0]; ntrL=ntrL[0]
    days = d["days"]
    dates = days[START-1:]
    def stats(nav,turn,ntr):
        st = v3.simple_stats(dates, nav, turn, ntr)
        return st
    sC = stats(navC,turnC,ntrC)
    sL = stats(navL,turnL,ntrL)
    return {"sym":sym, "n":len(days), "C0":sC, "L2":sL, "days":days,
            "navC":navC, "navL":navL}

# ---------- harness
def harness():
    ref = json.loads((ROOT/"research/ewma_band_v1/E2_INTEGRATION_DEVIATION.json").read_text())
    r = v3.run_main(("C0",), m_list=(1,2))
    checks={}
    for md in ("base","calibrated"):
        for m in (1,2):
            rr = ref[f"{md}_m{m}"]["arms"]["E2_prod_price_h0"]
            x = r[md][f"m{m}"]["C0"]
            for k in ("cagr","max_dd"):
                checks[f"{md}_m{m}_{k}"] = {"reference":rr[k], "reproduced":x[k], "diff_pp":(x[k]-rr[k])*100}
    ok = all(abs(v["diff_pp"])<=v3.TOL*100 for v in checks.values())
    return {"window":r["window"], "deviation_checks":checks, "tolerance_pp":v3.TOL*100, "pass":bool(ok)}

def main():
    import argparse
    ap=argparse.ArgumentParser()
    ap.add_argument("--harness", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--bootstrap", action="store_true")
    args=ap.parse_args()
    if args.harness:
        h=harness()
        OUT.parent and None
        print(json.dumps(h, indent=2, default=str))
        return
    if args.bootstrap:
        r = run_bootstrap_all()
        Path("research/riskoff_rotation_v4/BOOTSTRAP.json").write_text if False else None
        (HERE / "BOOTSTRAP.json").write_text(__import__("json").dumps(r, indent=2, default=str))
        print("written", HERE / "BOOTSTRAP.json")
        return
    if not args.run:
        print("use --harness or --run")
        return
    # Main F-test
    res = {"instruments":{}}
    for sym in EQUITY_ETFS:
        r = run_instrument(sym)
        if r is None: 
            print("skip", sym); continue
        res["instruments"][sym] = {k: r[k] for k in ("n","C0","L2")}
        # save navs? skip for size
        print(sym, r["C0"]["cagr"], r["L2"]["cagr"], r["C0"]["max_dd"], r["L2"]["max_dd"], flush=True)
    # metrics
    instruments = list(res["instruments"].keys())
    dcagr = np.array([res["instruments"][s]["L2"]["cagr"]-res["instruments"][s]["C0"]["cagr"] for s in instruments])
    ddd = np.array([res["instruments"][s]["L2"]["max_dd"]-res["instruments"][s]["C0"]["max_dd"] for s in instruments])
    med = float(np.median(dcagr))
    pos = float(np.mean(dcagr>=0))
    f1 = bool(med>=0 and pos>=0.6)
    f2 = bool(np.mean(ddd<=0.03)>=0.8)
    res["F_main"] = {
        "median_dcagr_cal":med, "share_pos":pos, "share_dd_ok":float(np.mean(ddd<=0.03)),
        "F1":f1, "F2":f2,
        "n_instruments":len(instruments),
    }
    # doubled cost check
    dc2=[]
    for sym in instruments:
        r2 = run_instrument(sym, spread=FIN_EXTRA, m=4)
        dc2.append(r2["L2"]["cagr"]-r2["C0"]["cagr"])
    dc2=np.array(dc2)
    f4 = bool(np.median(dc2)>=0 and np.mean(dc2>=0)>=0.6)
    res["F4_doubled_cost"] = {"median_dcagr":float(np.median(dc2)), "share_pos":float(np.mean(dc2>=0)), "F4":f4}
    res["instruments_doubled"] = None
    print("F1", f1, "median", med, "pos", pos)
    print("F2", f2, "share_dd_ok", np.mean(ddd<=0.03))
    print("F4", f4, "median", np.median(dc2), "pos", np.mean(dc2>=0))
    OUT.write_text(json.dumps(res, indent=2, default=str))
    print("written", OUT)

# ---------- bootstrap
def _ief_gld_factors(d):
    ief_on = np.ones(len(d["days"]))
    ief_id = np.ones(len(d["days"]))
    gld_on = np.ones(len(d["days"]))
    gld_id = np.ones(len(d["days"]))
    ief_on[1:] = d["ief_open"][1:]/d["ief_close"][:-1]
    ief_id[1:] = d["ief_close"][1:]/d["ief_open"][1:]
    gld_on[1:] = d["gld_open"][1:]/d["gld_close"][:-1]
    gld_id[1:] = d["gld_close"][1:]/d["gld_open"][1:]
    return ief_on, ief_id, gld_on, gld_id

def boot_instrument(sym, total_paths=PATHS):
    d = aligned(sym)
    days = d["days"]
    T = len(days)
    global DAYS_GLOBAL
    DAYS_GLOBAL = days
    v3.DAYS_GLOBAL = days
    Lp = min(WARM + MEASURED, T - EXT - 10)
    if Lp < 500:
        Lp = max(300, T-EXT-10)
    ief_on, ief_id, gld_on, gld_id = _ief_gld_factors(d)
    rng = np.random.default_rng(SEED)
    pool = np.arange(1, T)
    npool = T-1
    start = trv2.SMA_MAX + 1 + EXT
    deltas = []
    done = 0
    EQUITY_OPEN=d["eq_open"]; EQUITY_CLOSE=d["eq_close"]; EQUITY_DIV=d["eq_div"]; Y=d["y"]
    IEF_OPEN=d["ief_open"]; IEF_CLOSE=d["ief_close"]
    GLD_OPEN=d["gld_open"]; GLD_CLOSE=d["gld_close"]
    while done < total_paths:
        P = min(CHUNK, total_paths-done)
        pos = np.empty((P, Lp), dtype=np.int64)
        pos[:,0] = rng.integers(0, npool, P)
        jump = rng.random((P,Lp)) < 1.0/BLOCK
        fresh = rng.integers(0, npool, (P,Lp))
        for t in range(1,Lp):
            pos[:,t] = np.where(jump[:,t], fresh[:,t], (pos[:,t-1]+1)%npool)
        pre = np.stack([(pos[:,0]-k)%npool for k in range(EXT,0,-1)], axis=1)
        pos = np.concatenate([pre,pos], axis=1)
        idx = pool[pos]  # [P, EXT+Lp]

        prev = EQUITY_CLOSE[np.clip(idx-1,0,T-1)]
        r_on_tr = (EQUITY_OPEN[idx] + EQUITY_DIV[idx])/prev
        r_on_p = EQUITY_OPEN[idx]/prev
        r_id = EQUITY_CLOSE[idx]/EQUITY_OPEN[idx]
        one = np.ones((P,1))
        qc = np.concatenate([one, np.cumprod(r_on_tr*r_id, axis=1)], axis=1)
        qo = np.concatenate([one, qc[:,:-1]*r_on_tr], axis=1)
        pc = np.concatenate([one, np.cumprod(r_on_p*r_id, axis=1)], axis=1)
        up = v3.e2_up_nd(pc)
        up = np.atleast_2d(up)
        if up.shape[0]==1 and P>1: up=np.broadcast_to(up,(P,up.shape[1]))
        yy = Y[np.clip(idx-1,0,T-1)]
        yyp = np.concatenate([yy, yy[:,-1:]], axis=1)
        eo, ec = lf.synthetic_L(qo, qc, np.zeros_like(qc), yyp, 2.0, EXPENSE, FIN_EXTRA)
        eq_vol = trv2.vol_n(ec)
        eq_target = up.astype(float)*np.minimum(CAP, trv2.vt_of(eq_vol))

        def build_close(on_arr, id_arr):
            g_on = on_arr[idx]; g_id = id_arr[idx]
            gp = np.concatenate([np.ones((P,1)), g_on], axis=1)
            gp2 = np.concatenate([np.ones((P,1)), g_id], axis=1)
            return lf.levels(gp, gp2)[1]
        def build_open(on_arr, id_arr):
            g_on = on_arr[idx]; g_id = id_arr[idx]
            gp = np.concatenate([np.ones((P,1)), g_on], axis=1)
            gp2 = np.concatenate([np.ones((P,1)), g_id], axis=1)
            return lf.levels(gp, gp2)[0]
        bond_close = build_close(ief_on, ief_id)
        gold_close = build_close(gld_on, gld_id)
        bond_open = build_open(ief_on, ief_id)
        gold_open = build_open(gld_on, gld_id)

        bond_vol = trv2.vol_n(bond_close)
        gold_vol = trv2.vol_n(gold_close)
        bond_ret = v3.ret252_path(bond_close)
        gold_ret = v3.ret252_path(gold_close)
        bills_gross = 1.0 + yy/252.0
        bills_close = np.concatenate([np.ones((P,1)), np.cumprod(bills_gross, axis=1)], axis=1)
        bills_ret = v3.ret252_path(bills_close)
        bond_trend = v3.trend_up_path_nd(bond_close)
        gold_trend = v3.trend_up_path_nd(gold_close)

        gold_nan = np.isnan(gold_ret); bond_nan = np.isnan(bond_ret)
        chosen_bond = np.where(gold_nan & ~bond_nan, True,
                  np.where(bond_nan & ~gold_nan, False,
                  np.where(~bond_nan & ~gold_nan, bond_ret >= gold_ret, False)))
        chosen_ret = np.where(chosen_bond, bond_ret, gold_ret)
        # monthly choice on off
        chosen_bond_eff = v3.monthly_choice_2d(chosen_bond, up, idx)
        chosen_ret_eff = np.where(chosen_bond_eff, bond_ret, gold_ret)
        sel_cond_eff = ~np.isnan(chosen_ret_eff) & ~np.isnan(bills_ret) & (chosen_ret_eff > bills_ret)

        bond_vt_w = np.where(~np.isnan(bond_vol), np.minimum(1.0, 0.10/np.where(bond_vol>0,bond_vol,1.0)), 0.0)
        gold_vt_w = np.where(~np.isnan(gold_vol), np.minimum(1.0, 0.10/np.where(gold_vol>0,gold_vol,1.0)), 0.0)
        bond_active = (~up) & chosen_bond_eff & sel_cond_eff
        gold_active = (~up) & (~chosen_bond_eff) & sel_cond_eff
        bond_w = np.where(bond_active, bond_vt_w, 0.0)
        gold_w = np.where(gold_active, gold_vt_w, 0.0)
        wantsL = np.stack([eq_target, bond_w, gold_w], axis=1)
        wantsL = v3.cap_total_path(wantsL)
        opensL = np.stack([eo, bond_open, gold_open], axis=1)
        closesL = np.stack([ec, bond_close, gold_close], axis=1)

        # C0 wants
        wantsC = eq_target[:, None, :]
        opensC = eo[:, None, :]
        closesC = ec[:, None, :]

        navL = v3.simulate_lows(opensL, closesL, wantsL, COST_PER_SIDE*2, start)[0]
        navC = trv2.simulate_multi(opensC, closesC, wantsC, COST_PER_SIDE*2, start)[0]
        k = navL.shape[1]-1
        cagrL = (navL[:,-1]/navL[:,0])**(252.0/k)-1.0
        cagrC = (navC[:,-1]/navC[:,0])**(252.0/k)-1.0
        deltas.extend(list(cagrL-cagrC))
        done += P
        print(sym, "bootstrap", done, "/", total_paths, flush=True)
    return np.array(deltas, dtype=float)

def run_bootstrap_all():
    pooled=[]
    per={}
    for sym in EQUITY_ETFS:
        dv = boot_instrument(sym, total_paths=PATHS)
        per[sym] = {"median_delta": float(np.median(dv)), "share_pos": float(np.mean(dv>=0)), "paths": int(len(dv))}
        pooled.extend(list(dv))
        print("DONE", sym, per[sym], flush=True)
    pooled = np.array(pooled, dtype=float)
    res = {
        "per_instrument": per,
        "pooled": {"median_delta": float(np.median(pooled)), "share_pos": float(np.mean(pooled>=0)), "paths": int(len(pooled))},
        "F3": bool(np.median(pooled) >= 0),
    }
    return res

if __name__ == "__main__":
    main()
