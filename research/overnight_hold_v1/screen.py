#!/usr/bin/env python3
from __future__ import annotations
import csv, importlib.util, json, hashlib
from collections import defaultdict, OrderedDict
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
_spec = importlib.util.spec_from_file_location("trv2", ROOT/"research/trend_robustness_v2/screen.py")
trv2 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(trv2)
_spec2 = importlib.util.spec_from_file_location("ewma", ROOT/"research/ewma_band_v1/screen.py")
ewma = importlib.util.module_from_spec(_spec2); _spec2.loader.exec_module(ewma)

STRESS_DAILY = ROOT/"data/historical/stress_robustness_v1_daily.csv"
DTB3 = ROOT/"data/historical/stress_robustness_v1_dtb3.csv"
OUT = HERE/"RESULTS.json"
SUMMARY = HERE/"SUMMARY.txt"

FIN = ewma.FIN
EXP_Q = trv2.EXP_Q
START = ewma.START
SEED, PATHS, CHUNK, BLOCK = ewma.SEED, ewma.PATHS, ewma.CHUNK, ewma.BLOCK
WARM, MEASURED = ewma.WARM, ewma.MEASURED
EXT = ewma.EXT
COSTS = (0.0001,0.0002,0.0004,0.0008)
COST_LABELS = {0.0001:"1bps",0.0002:"2bps",0.0004:"4bps",0.0008:"8bps"}

# ---------------- data ----------------
def qqq_stress():
    days, arr, y = trv2.load()  # days, arr dict QQQ/SPY/QLD, y DTB3
    return days, arr["QQQ"], np.asarray(y,float)

def main_market(spread):
    days, q, y = qqq_stress()
    oqld, cqld = trv2.synthetic_2x(q[0],q[1],q[2], y + spread, EXP_Q)
    e2 = ewma.e2_up(q[1])[0]
    v20 = trv2.vol_n(cqld)[0]
    wants = ewma.wants_for(e2,v20)[0]
    dcost = (EXP_Q + spread + np.asarray(y,float))/252.0
    dates = days[START-1:]
    return days, np.asarray(q[0],float), np.asarray(q[1],float), np.asarray(q[2],float), np.asarray(y,float), oqld, cqld, e2, v20, wants, dcost, dates

def load_etf_csv(path):
    rows = []
    with open(path) as fh:
        for r in csv.DictReader(fh):
            rows.append(r)
    return rows

def etf_market(ticker, spread):
    # returns (days, qo, qc, qd, y, o2x, c2x, e2, vol20, wants, dcost, dates)
    path = ROOT/f"data/historical/overnight_hold_v1_{ticker.lower()}.csv"
    rows = load_etf_csv(path)
    # map DTB3 rates
    rate = {}
    last=None
    global_dates = sorted({r["date"] for r in rows})
    with open(DTB3) as fh:
        tb = {r["observation_date"]:r["DTB3"] for r in csv.DictReader(fh)}
    for d in sorted(set(list(tb.keys())+global_dates)):
        v=tb.get(d,".")
        if v not in (".",""):
            last=float(v)/100
        if d in global_dates:
            rate[d]=last if last is not None else 0.0
    days=[]; qo=[]; qc=[]; qd=[]
    for r in rows:
        days.append(r["date"]); qo.append(float(r["open"])); qc.append(float(r["close"])); qd.append(float(r["dividend"]))
    qo=np.asarray(qo); qc=np.asarray(qc); qd=np.asarray(qd)
    # trim to where enough? keep all, start computed
    y=np.asarray([rate[d] for d in days])
    o2x,c2x = trv2.synthetic_2x(qo,qc,qd,y+spread,EXP_Q) if False else ( None, None)
    # vol/e2 need synthetic close; build total 2x
    oo,cc = trv2.synthetic_2x(qo,qc,qd,y+spread,EXP_Q)
    e2 = ewma.e2_up(qc[np.newaxis,:])[0]  # e2_up expects 2D
    # vol_n expects 2D; pass close 2D
    vol20 = trv2.vol_n(cc[np.newaxis,:])[0]
    wants = ewma.wants_for(e2,vol20)[0]
    dcost = (EXP_Q + spread + y)/252.0
    T = len(days)
    # start index = max(START, 253) but array days may start late; START bars require 253 closes
    start = START if T>START else (T-1)
    if start < 253:
        start = 253
    dates = days[start-1:]
    return days,qo,qc,qd,y,oo,cc,e2,vol20,wants,dcost,dates,start

# ---------------- simulators ----------------
def simulate_on(qo,qc,qd,wants,dcost,coeff_n,coeff_i,s,start):
    T=len(qc)
    nav=1.0; w=0.0; out=[1.0]; turn=0.0; ntr=0
    for i in range(start,T):
        gon=(qo[i]+qd[i])/qc[i-1]
        va=w*gon; tot=1-w+va
        if tot<=0 or not np.isfinite(tot): tot=1.0
        nav=nav*tot; w=va/tot
        tgt_i=coeff_i*float(wants[i-1])
        d=abs(tgt_i-w); held=w>0; tpos=tgt_i>0; flip=held!=tpos
        if flip or d>1e-12:
            nav*=1-s*d; turn+=d; ntr+=1; w=tgt_i
        gin=qc[i]/qo[i]
        va=w*gin; tot=1-w+va
        if tot<=0 or not np.isfinite(tot): tot=1.0
        nav=nav*tot; w=va/tot
        if coeff_i!=0 and tgt_i!=0:
            nav*=1-dcost[i]*tgt_i
        tgt_n=coeff_n*float(wants[i])
        d=abs(tgt_n-w); held=w>0; tpos=tgt_n>0; flip=held!=tpos
        if flip or d>1e-12:
            nav*=1-s*d; turn+=d; ntr+=1; w=tgt_n
        if coeff_n!=0 and tgt_n!=0:
            nav*=1-dcost[i]*tgt_n
        out.append(nav)
    return np.array(out),turn,ntr

def simulate_on_paths(gon,gin,wants,dcost,coeff_n,coeff_i,s,start):
    P,T = gon.shape
    nav=np.ones(P); w=np.zeros(P); turn=np.zeros(P); ntr=np.zeros(P,dtype=np.int64)
    out=np.ones((P,T-start+1))
    for idx,i in enumerate(range(start,T)):
        g=gon[:,i]
        va=w*g; tot=1-w+va
        tot=np.where((tot<=0)|~np.isfinite(tot),1.0,tot)
        nav=nav*tot; w=va/tot
        tgt_i=coeff_i*wants[:,i-1]
        d=np.abs(tgt_i-w); held=w>0; tpos=tgt_i>0; flip=held!=tpos
        trade=flip|(d>1e-12)
        nav=np.where(trade,nav*(1-s*d),nav)
        turn=turn+np.where(trade,d,0.0)
        ntr=ntr+trade.astype(np.int64)
        w=np.where(trade,tgt_i,w)
        g2=gin[:,i]
        va=w*g2; tot=1-w+va
        tot=np.where((tot<=0)|~np.isfinite(tot),1.0,tot)
        nav=nav*tot; w=va/tot
        if coeff_i!=0:
            nav=nav*np.where(tgt_i!=0,1.0-dcost[:,i]*tgt_i,1.0)
        tgt_n=coeff_n*wants[:,i]
        d=np.abs(tgt_n-w); held=w>0; tpos=tgt_n>0; flip=held!=tpos
        trade=flip|(d>1e-12)
        nav=np.where(trade,nav*(1-s*d),nav)
        turn=turn+np.where(trade,d,0.0)
        ntr=ntr+trade.astype(np.int64)
        w=np.where(trade,tgt_n,w)
        if coeff_n!=0:
            nav=nav*np.where(tgt_n!=0,1.0-dcost[:,i]*tgt_n,1.0)
        out[:,idx+1]=nav
    return out,turn,ntr

# ---------------- main ----------------
def main_runs():
    res = {"main":{},"year_split":{}}
    for md,spread in FIN.items():
        days,qo,qc,qd,y,oqld,cqld,e2,v20,wants,dcost,dates = main_market(spread)
        mdres = {}
        # C0 at 4bps standard
        wfull = ewma.wants_for(e2,v20)
        c0 = {}
        for m in (1,2):
            nav,turn,ntr,_,_ = ewma.simulate_band(oqld,cqld,wfull,0.0004*m,START,0.005)
            st = trv2.curve_stats(dates, nav[0])
            c0[f"m{m}"] = {"cagr":float(st["cagr"]),"max_dd":float(st["max_dd"]),"turnover_annual":float(turn[0]/trv2.calendar_years(dates[0],dates[-1]))}
        # N arms
        arms = {}
        for cname,coeff in (("N1",(1,0)),("N2",(1,0.5))):
            arms[cname] = {}
            for s in COSTS:
                percost={}
                for m in (1,2):
                    nav,turn,ntr = simulate_on(qo,qc,qd,wants,dcost,coeff[0],coeff[1],s*m,START)
                    # cost grid uses s per side, but m? m multiplier on instrument leverage, not cost. cost independent; C0 uses 4bps*m?? In ewma cost is 4bps*m where m=leverage multiplier? Production model cost per side = 4bps * m (because notional m×). For our custom, trading notional is w (0-1) for both arms, not QLD leverage? The arm target w is QLD weight (<=.98), and trading that notional. Trading cost should 4bps per unit w, not multiplied by m. m in C0 context is G1 at m=1/m=2 financing? Here no 2x leverage? Wait our arms exposure through QLD 2x, but trade notional is fund notional. Cost model production charges cost × m (leverage) because QLD notional=2×? Hmm ewma simulate_band uses COST_PER_SIDE * m, with COST_PER_SIDE=4 bps. For C0 m=1 vs m=2 comparison likely adjusts cost? In ewma report "Cost is 4 bps × m." They simulate m=1/m=2 separately for capacity. For our N arms, report G1 under base/cal and m=1/m=2? User says gate uses s=4 for G1-G6 and requires G1 (m=2 return) and G6 (m=1). So trading cost multiplier m applies. We'll set cost = s * m in simulate. But s grid are auction slippage per side; gate at s=4 bps and m=2 => 8 bps. Our cost parameter should be s*m (bps). Let's correct: simulate_on cost argument s already per-side cash; for gate s=4bps m=2 => 0.0008. We'll pass s*m? In call simulate_on(..., s, ...) currently s includes maybe cost per side. For main grid we can just evaluate s in COSTS for m=2? The user asks evaluate s=1,2,4,8 bps, gate uses s=4. They don't separate m trading multiple? G6 checks m=1 with same s. We need both m=1 and m=2. For m=1 trading cost s; for m=2 trading cost s*m? Production uses 4 bps × m. So yes cost = s * m. We'll compute for each s in COSTS and m in (1,2): cost = s*m.
                    nav,turn,ntr = simulate_on(qo,qc,qd,wants,dcost,coeff[0],coeff[1], s*m, START)
                    st = trv2.curve_stats(dates, nav)
                    percost[f"m{m}"] = {"cagr":float(st["cagr"]),"max_dd":float(st["max_dd"]),"turnover_annual":float(turn/trv2.calendar_years(dates[0],dates[-1])),"trades":int(ntr)}
                arms[cname][COST_LABELS[s]] = percost
        mdres = {"C0":c0,"N_arms":arms}
        res["main"][md]=mdres

    # year split
    days,q,y = qqq_stress()
    qo,qc,qd = q
    on_by=defaultdict(lambda:1.0); in_by=defaultdict(lambda:1.0); cnt=defaultdict(int)
    for i in range(1,len(days)):
        yr=days[i][:4]
        if not (2017<=int(yr)<=2026): continue
        onr=(qo[i]+qd[i])/qc[i-1]-1
        inr=qc[i]/qo[i]-1
        if cnt[yr]==0:
            on_by[yr]=1+onr; in_by[yr]=1+inr
        else:
            on_by[yr]*=(1+onr); in_by[yr]*=(1+inr)
        cnt[yr]+=1
    for yr in sorted(on_by):
        res["year_split"][yr]={"overnight":float(on_by[yr]-1),"intraday":float(in_by[yr]-1),"sessions":cnt[yr]}
    return res

# ---------------- bootstrap ----------------
def bootstrap_main():
    _, arr, y = trv2.load()
    q = arr["QQQ"]; T=q.shape[1]
    rng = np.random.default_rng(SEED)
    pool = np.arange(1,T); npool=len(pool)
    L = WARM+MEASURED
    start = trv2.SMA_MAX+1+EXT
    cg = {}
    for md in FIN:
        for a in ["C0","N1","N2"]:
            cg[(md,a)] = []
    tv = {k:[] for k in cg}
    for chunk in range(0,PATHS,CHUNK):
        P = min(CHUNK, PATHS-chunk)
        pos = np.empty((P,L),dtype=np.int64)
        pos[:,0] = rng.integers(0,npool,P)
        jump = rng.random((P,L)) < 1/BLOCK
        fresh = rng.integers(0,npool,(P,L))
        for t in range(1,L):
            pos[:,t] = np.where(jump[:,t], fresh[:,t], (pos[:,t-1]+1)%npool)
        pre = np.stack([(pos[:,0]-k)%npool for k in range(EXT,0,-1)], axis=1)
        pos = np.concatenate([pre,pos],axis=1)
        idx = pool[pos]                      # (P, EXT+L)
        M = idx.shape[1]
        yy = y[idx-1]                        # (P, M)
        prev = q[1][idx-1]
        r_on_tr = (q[0][idx] + q[2][idx])/prev
        r_on_p = q[0][idx]/prev
        r_id = q[1][idx]/q[0][idx]
        one = np.ones((P,1))
        qc = np.concatenate([one, np.cumprod(r_on_tr*r_id, axis=1)], axis=1)   # (P,M+1)
        qo = np.concatenate([one, qc[:,:-1]*r_on_tr], axis=1)                  # (P,M+1)
        pc = np.concatenate([one, np.cumprod(r_on_p*r_id, axis=1)], axis=1)
        e2 = ewma.e2_up(pc)
        yyp = np.concatenate([yy, yy[:,-1:]], axis=1)  # (P,M+1)
        Tt = qo.shape[1]
        gon = np.ones((P,Tt))
        gon[:,1:] = qo[:,1:]/qc[:,:-1]
        gin = np.ones((P,Tt))
        gin[:,1:] = qc[:,1:]/qo[:,1:]
        for md in FIN:
            so, scl = trv2.synthetic_2x(qo,qc,np.zeros_like(qc), yyp + FIN[md], EXP_Q)
            vol20 = trv2.vol_n(scl)
            wfull = ewma.wants_for(e2, vol20)  # (P,Tt)
            dcost = (EXP_Q + FIN[md] + yyp)/252.0  # (P,Tt)
            for arm in ["C0","N1","N2"]:
                if arm=="C0":
                    nav,turn,_,_,_ = ewma.simulate_band(so,scl,wfull,0.0004*2, start, 0.005)
                    k = nav.shape[1]-1
                    cagr = (nav[:,-1]/nav[:,0])**(252/k)-1
                    tvv = turn/(k/252)
                elif arm=="N1":
                    out,turn,ntr = simulate_on_paths(gon,gin,wfull,dcost,1,0,0.0004, start)
                    k=out.shape[1]-1
                    cagr = (out[:,-1]/out[:,0])**(252/k)-1
                    tvv = turn/(k/252)
                elif arm=="N2":
                    out,turn,ntr = simulate_on_paths(gon,gin,wfull,dcost,1,0.5,0.0004, start)
                    k=out.shape[1]-1
                    cagr = (out[:,-1]/out[:,0])**(252/k)-1
                    tvv = turn/(k/252)
                cg[(md,arm)].append(cagr)
                tv[(md,arm)].append(tvv)
    out_cg = {k: np.concatenate(v) for k,v in cg.items()}
    out_tv = {k: np.concatenate(v) for k,v in tv.items()}
    # summary
    boot = {}
    for md in FIN:
        boot[md] = {}
        c0 = out_cg[(md,"C0")]
        for a in ["N1","N2"]:
            c = out_cg[(md,a)]
            d = c-c0
            boot[md][a] = {
                "cagr_median": float(np.median(c)),
                "diff_vs_C0_median": float(np.median(d)),
                "diff_vs_C0_p25": float(np.percentile(d,25)),
                "diff_vs_C0_p75": float(np.percentile(d,75)),
                "share_ge_C0": float(np.mean(d>=0)),
                "turnover_median": float(np.median(out_tv[(md,a)])),
            }
    return boot

# ---------------- OOS reduced ----------------
def oos_runs():
    res = {}
    for ticker in ["SPY","DIA","IWM"]:
        for md,spread in FIN.items():
            try:
                days,qo,qc,qd,y,oo,cc,e2,vol20,wants,dcost,dates,start = etf_market(ticker,spread)
            except Exception as e:
                res.setdefault(ticker,{}).setdefault(md, {"error":str(e)}); continue
            # C0 standard
            wfull = ewma.wants_for(e2,vol20)
            nav,turn,_,_,_ = ewma.simulate_band(oo,cc,wfull,0.0004*2,start,0.005)
            stc = trv2.curve_stats(dates, nav[0])
            # N1
            nav1,turn1,_ = simulate_on(qo,qc,qd,wants,dcost,1,0,0.0004*2,start)
            st1 = trv2.curve_stats(dates, nav1)
            res.setdefault(ticker,{}).setdefault(md,{})
            res[ticker][md] = {
                "C0": {"cagr":float(stc["cagr"]),"max_dd":float(stc["max_dd"])},
                "N1": {"cagr":float(st1["cagr"]),"max_dd":float(st1["max_dd"]),"turnover":float(turn1/trv2.calendar_years(dates[0],dates[-1]))},
                "start": dates[0], "end": dates[-1], "sessions": len(dates)
            }
    return res

if __name__=="__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv)>1 else "--run"
    if mode=="--harness":
        res = main_runs()
        # print C0 base m2
        print("C0 base m2:", res["main"]["base"]["C0"]["m2"] if "m2" in res["main"]["base"]["C0"] else res["main"]["base"]["C0"])
        raise SystemExit(0)
    res = {}
    res["main"] = main_runs()["main"] if False else None
    mr = main_runs()
    res["main"] = mr["main"]
    res["year_split_2017_2026"] = mr["year_split"]
    res["bootstrap"] = bootstrap_main()
    res["oos"] = oos_runs()
    OUT.write_text(json.dumps(res, indent=2, default=str))
    # summary
    lines=[]
    for md in FIN:
        lines.append(f"== {md} ==")
        c0 = res["main"][md]["C0"]
        lines.append(f"C0 m1 {c0['m1']}  m2 {c0['m2']}")
        for cname in ("N1","N2"):
            for cost in ("1bps","2bps","4bps","8bps"):
                v = res["main"][md]["N_arms"][cname][cost]
                lines.append(f"{cname} {cost}: m1 {v['m1']} m2 {v['m2']}")
    if "bootstrap" in res:
        for md in FIN:
            for a in ("N1","N2"):
                b=res["bootstrap"][md][a]
                lines.append(f"BOOT {md} {a} median diff {b['diff_vs_C0_median']*100:.3f} pp share {b['share_ge_C0']:.3f}")
    SUMMARY.write_text("\n".join(lines))
    print("done")
