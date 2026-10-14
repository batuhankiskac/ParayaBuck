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
    args=ap.parse_args()
    if args.harness:
        h=harness()
        OUT.parent and None
        print(json.dumps(h, indent=2, default=str))
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

if __name__ == "__main__":
    main()
