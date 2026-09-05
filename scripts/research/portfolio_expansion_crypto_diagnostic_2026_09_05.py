"""Derived curve-only crypto mixture diagnostic; never runs an engine or trades."""
from __future__ import annotations
import hashlib, json, math
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, stdev

REPO = Path(__file__).resolve().parents[2]
MAX_PATH = REPO / 'data/research/crypto_max_return_economic_2026_09/attempt_01_canonical/economic_results.json'
SIGNED_PATH = REPO / 'data/research/crypto_signed_risk_economic_2026_09/attempt_01_canonical/economic_results.json'
OUT = REPO / 'data/research/portfolio_expansion_crypto_diagnostic_2026_09/attempt_02_corrected/diagnostic_results.json'
MANIFEST = REPO / 'data/research_manifests/portfolio_expansion_crypto_diagnostic_2026_09_attempt_02.json'

def sha(p: Path) -> str: return hashlib.sha256(p.read_bytes()).hexdigest()
def parse(s: str) -> datetime: return datetime.fromisoformat(s.replace('Z','+00:00')).astimezone(timezone.utc)
INITIAL_CASH = 100_000.0

def hourly_curve(cell):
    points = []
    for ts, value in zip(cell['equity_timestamps'], cell['equity_curve'], strict=True):
        t = parse(ts); v = float(value)
        if not math.isfinite(v): raise RuntimeError('BLOCKED_NONFINITE_CURVE')
        points.append((t, v))
    if not points or points[0][1] != INITIAL_CASH:
        raise RuntimeError('BLOCKED_INITIAL_CASH_ENDPOINT')
    return points

def daily_curve(cell):
    by_day = {}
    for ts, value in zip(cell['equity_timestamps'], cell['equity_curve'], strict=True):
        t=parse(ts); v=float(value)
        if not math.isfinite(v): raise RuntimeError('BLOCKED_NONFINITE_CURVE')
        by_day[t.date()] = (t,v)
    return sorted((t,v) for t,v in by_day.values())
def stats(curve, *, cadence, include_initial=True):
    if not curve or curve[-1][1] <= 0: raise RuntimeError('BLOCKED_INVALID_NAV')
    vals=[v for _,v in curve]
    returns=[vals[0]/INITIAL_CASH-1] + [vals[i]/vals[i-1]-1 for i in range(1,len(vals))] if include_initial else [vals[i]/vals[i-1]-1 for i in range(1,len(vals))]
    if any(not math.isfinite(x) for x in returns): raise RuntimeError('BLOCKED_NONFINITE_RETURN')
    peak=INITIAL_CASH if include_initial else vals[0]; dd=0.0
    if include_initial: peak=max(peak, vals[0]); dd=max(dd, 1-vals[0]/peak)
    for v in vals:
        peak=max(peak,v); dd=max(dd,1-v/peak)
    days=max((curve[-1][0]-curve[0][0]).total_seconds()/86400,1); total=vals[-1]/INITIAL_CASH-1; cagr=(1+total)**(365.25/days)-1
    annualization=8760.0 if cadence == 'hourly' else 365.25
    sharpe=(mean(returns)/stdev(returns)*math.sqrt(annualization)) if len(returns)>1 and stdev(returns)>0 else None
    months={}
    for (t,v) in curve: months.setdefault((t.year,t.month),[]).append(v)
    month_returns=[x[-1]/x[0]-1 for x in months.values() if x and x[0]>0]
    return {'start':curve[0][0].isoformat(),'end':curve[-1][0].isoformat(),'observations':len(curve),'total_return':total,'cagr':cagr,'sharpe':sharpe,'max_drawdown':dd,'worst_month':min(month_returns) if month_returns else None,'cadence':cadence,'annualization':annualization,'risk_free_rate_annualized':0.0,'denominator':'INITIAL_CASH' if include_initial else 'first_observation'}
def cellmap(d): return {(c['arm'],c['window'],float(c['cost_multiplier'])):c for c in d['cells']}
def main():
    maxd=json.loads(MAX_PATH.read_text()); sd=json.loads(SIGNED_PATH.read_text()); mm=cellmap(maxd); sm=cellmap(sd)
    rows=[]; checks=[]; control_checks=[]
    for window in ('full','recent'):
      for cost in (1.0,2.0,3.0):
        base=sm[('low_variance',window,cost)]; base_curve=hourly_curve(base); base_daily=daily_curve(base)
        bs=stats(base_curve,cadence='hourly'); bsd=stats([(datetime.fromisoformat(''+t.isoformat()),v) for t,v in base_daily],cadence='daily',include_initial=True)
        base_c3=cellmap(maxd)[('low_variance',window,cost)]
        c3_curve=hourly_curve(base_c3)
        control_checks.append({'window':window,'cost_multiplier':cost,'c3_c4_timestamps_equal': [x[0] for x in c3_curve] == [x[0] for x in base_curve], 'c3_c4_equity_equal': all(math.isclose(x[1],y[1],rel_tol=0,abs_tol=0) for x,y in zip(c3_curve,base_curve,strict=True)), 'initial_cash': base['cash_ledger'][0]['cash'], 'c3_ledger_sha256': hashlib.sha256(json.dumps(base_c3['cash_ledger'],separators=(',',':'),sort_keys=True).encode()).hexdigest(), 'c4_ledger_sha256': hashlib.sha256(json.dumps(base['cash_ledger'],separators=(',',':'),sort_keys=True).encode()).hexdigest()})
        for arm,srcmap,srcname in (('max',mm,'MAX'),('signed_risk',sm,'SIGNED_RISK')):
          cand=srcmap[(arm,window,cost)]; cc=hourly_curve(cand); cand_daily=daily_curve(cand)
          if [x[0] for x in cc] != [x[0] for x in base_curve]: raise RuntimeError('BLOCKED_MISALIGNED_HOURLY_CURVES')
          if [x[0] for x in cand_daily] != [x[0] for x in base_daily]: raise RuntimeError('BLOCKED_MISALIGNED_DAILY_CURVES')
          mix=[(t,(bv+cv)/2) for (t,bv),(ct,cv) in zip(base_curve,cc,strict=True) if t==ct]
          mix_daily=[(t,(bv+cv)/2) for (t,bv),(ct,cv) in zip(base_daily,cand_daily,strict=True) if t==ct]
          ms=stats(mix,cadence='hourly'); msd=stats(mix_daily,cadence='daily',include_initial=True)
          dr=[mix_daily[0][1]/INITIAL_CASH-1]+[mix_daily[i][1]/mix_daily[i-1][1]-1 for i in range(1,len(mix_daily))]
          br=[base_daily[0][1]/INITIAL_CASH-1]+[base_daily[i][1]/base_daily[i-1][1]-1 for i in range(1,len(base_daily))]
          corr=None
          if len(dr)>1 and stdev(dr)>0 and stdev(br)>0: corr=sum((a-mean(dr))*(b-mean(br)) for a,b in zip(dr,br))/(len(dr)-1)/stdev(dr)/stdev(br)
          turnover_c=cand.get('turnover',cand.get('metrics',{}).get('turnover')); turnover_b=base.get('turnover',base.get('metrics',{}).get('turnover'))
          rows.append({'mixture':f'low_variance_50_50_{srcname.lower()}','candidate_arm':arm,'window':window,'cost_multiplier':cost,'effective_weights':{'incumbent':0.025,'candidate':0.025,'total_crypto_budget':0.05},'paired_daily_observations':len(dr),'baseline_native':bs,'baseline_daily':bsd,'mixture_native':ms,'mixture_daily':msd,'delta_native':{k:ms[k]-bs[k] for k in ('total_return','cagr','sharpe','max_drawdown')},'paired_daily_return_correlation':corr,'turnover_proxy':{'baseline':turnover_b,'candidate':turnover_c,'mixture_weighted':(0.5*(turnover_b+turnover_c) if turnover_b is not None and turnover_c is not None else None)},'source_fill_counts':{'baseline':len(base.get('fills',[])),'candidate':len(cand.get('fills',[]))},'endpoint_identity':{'initial_cash':INITIAL_CASH,'first_hourly':base_curve[0][0].isoformat(),'first_daily_end':base_daily[0][0].isoformat(),'terminal':base_curve[-1][0].isoformat(),'candidate_terminal_equals_baseline':cc[-1][0]==base_curve[-1][0]},'diagnostic_only':True})
    payload={'status':'DIAGNOSTIC_CURVE_BLEND_CORRECTED','economic_run':False,'shared_ledger':False,'cell_count':len(rows),'rows':rows,'source_control_checks':control_checks,'input_artifacts':{'max':{'path':str(MAX_PATH.relative_to(REPO)),'sha256':sha(MAX_PATH),'manifest_sha256':maxd.get('manifest_sha256')},'signed_risk':{'path':str(SIGNED_PATH.relative_to(REPO)),'sha256':sha(SIGNED_PATH),'manifest_sha256':sd.get('manifest_sha256')}},'rules':'normalized standalone account NAV average; native TR/CAGR/hourly DD/Sharpe use INITIAL_CASH and hourly cadence; daily DD/Sharpe/correlation use common UTC day ends including initial-cash first return; 50/50 fixed 5% crypto budget; no joint fills/netting/shared risk inference'}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'output':str(OUT),'sha256':sha(OUT),'cell_count':len(rows),'status':payload['status']}))
if __name__=='__main__': main()
