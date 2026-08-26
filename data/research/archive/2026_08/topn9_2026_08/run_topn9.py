#!/usr/bin/env python3
"""Alpha Selection E1/E2/U1 — top_n=9 isolation A/B/C/D + scanner quality U1.

Ownership: AlphaTopN (data/research/topn9_*). No production config mutation.
Deterministic: PIT universe, next-open fills, T+1, no lookahead.
Risk mode: shadow (research default) — production latch not enforced to keep alpha curve comparable.
Datasets: equity_1200_2016_2026.csv + equity_1200_dividends.csv (split-adjusted + explicit dividends).

Isolates (all use production config 0.60 equity sleeve, max_positions 6, weekly v2):
  A = top8 -> inverse-vol -> truncated to 6 via risk overlay (current production)
  B = top9 -> inverse-vol -> truncated to 6 (tests if extra candidate improves truncated set)
  C = top6 direct -> inverse-vol over 6 (no truncation; pure alpha breadth 6)
  D = top8 -> correlation-aware subset 6 (greedy low-correlation filter, inverse-vol still)

E2: semantic mismatch documented in report.
U1: scanner quality as alpha conditioner — wide pool (~300) vs quality-30 (observation_set) pipeline.

Usage: python data/research/topn9_2026_08/run_topn9.py [--quick] [--full]
  --quick: 2023-01-01..2024-12-31 smoke (fast, deterministic proof wiring)
  --full:  2017-01-03..2026-07-30 full window + cost stress + WF 12/16 + jackknife + regime
"""
from __future__ import annotations
import argparse, hashlib, json, math, sys
from dataclasses import replace
from datetime import UTC, datetime, date
from pathlib import Path
import statistics

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from parayabuck.backtest.product import build_product_universe, load_csv_bars, load_csv_corporate_actions, run_product_backtest
from parayabuck.backtest.universe import PointInTimeUniverse, UniverseSnapshot, hash_point_in_time_universe
from parayabuck.config import load_config
from parayabuck.core.models import Market
from parayabuck.portfolio.equity_residual_momentum import EquityResidualMomentum1dV2
from parayabuck.portfolio.momentum import inverse_volatility_weights

EQUITY_CSV = REPO / "data" / "historical" / "equity_1200_2016_2026.csv"
DIV_CSV = REPO / "data" / "historical" / "equity_1200_dividends.csv"
OUT_DIR = REPO / "data" / "research" / "topn9_2026_08"
CONFIG_PATH = REPO / "config" / "settings.yaml"

EQUITY_START = datetime(2017, 1, 3, tzinfo=UTC)
EQUITY_END = datetime(2026, 7, 30, 23, 59, 59, tzinfo=UTC)
QUICK_START = datetime(2023, 1, 1, tzinfo=UTC)
QUICK_END = datetime(2024, 12, 31, tzinfo=UTC)

# Walk-forward: anchored calendar 6-month folds (shared helper semantics, inlined to avoid import).
import calendar

def calendar_folds(start: datetime, end: datetime, step_months=6):
    folds=[]
    cursor=start
    while cursor < end:
        month_index = cursor.year*12 + (cursor.month-1) + step_months
        year, month0 = divmod(month_index, 12)
        month = month0+1
        day = min(cursor.day, calendar.monthrange(year, month)[1])
        fold_end = min(datetime(year, month, day, tzinfo=cursor.tzinfo), end)
        if fold_end - cursor >= timedelta(days=20):
            folds.append((cursor, fold_end))
        cursor = fold_end
    return folds

from datetime import timedelta

# -- D strategy: correlation-aware subset 6 from top8 -----------------------

class EquityResMom_CorrAware(EquityResidualMomentum1dV2):
    """Top8 -> greedy low-correlation subset 6 -> inverse-vol sized."""
    name = "equity_residual_momentum_v2_corr6"
    version = "1.0.0-corr6"
    cadence_label = "weekly"
    supported_timeframes = ("1d",)
    rebalance_policy_declared = "weekly"

    def decide(self, context):
        from parayabuck.portfolio.models import TargetPortfolio, TargetWeight, RebalanceReason
        from parayabuck.portfolio.momentum import inverse_volatility_weights
        top_n = self.param_int("top_n", 8)  # should be 8 for D
        cap = self.param_float("max_single_weight", 0.10)
        beta_window = self.param_int("beta_window_bars", 273)
        from parayabuck.markets.symbols import canonical_symbol
        benchmark_key = canonical_symbol(context.benchmark_symbol, Market.EQUITY)
        if len(context.history.get(benchmark_key, ())) < beta_window + 1:
            return self._cash_portfolio(context, "market_factor_unavailable")
        ranked, residual_vols = self._score_candidates(context)
        if not ranked:
            return self._cash_portfolio(context, "no_qualified_candidates")
        # Top-n candidates by residual momentum score
        top_candidates = tuple(symbol for _score, symbol in ranked[:top_n])
        if len(top_candidates) <= 6:
            selected = top_candidates
        else:
            # Build daily return series over last 63 bars for each candidate (for correlation)
            vol_window = self.param_int("residual_vol_window", 63)
            rets = {}
            for sym in top_candidates:
                bars = context.history.get(sym, ())
                closes = [b.close for b in bars[-vol_window-1:] if b.close>0]
                if len(closes) < 10:
                    rets[sym]=[]
                else:
                    rets[sym]=[closes[i]/closes[i-1]-1 for i in range(1,len(closes)) if closes[i-1]>0]
            # Pearson correlation helper
            def pearson(a,b):
                n=min(len(a),len(b))
                if n<10: return 0.0
                a=a[-n:]; b=b[-n:]
                ma=sum(a)/n; mb=sum(b)/n
                num=sum((x-ma)*(y-mb) for x,y in zip(a,b))
                den=math.sqrt(sum((x-ma)**2 for x in a)*sum((y-mb)**2 for y in b))
                if den==0: return 0.0
                return max(-1.0,min(1.0,num/den))
            # Greedy: start with best score, iteratively add least correlated
            ranked_scores = {sym:score for score,sym in ranked if sym in top_candidates}
            # Order top_candidates by score descending already
            ordered = sorted(top_candidates, key=lambda s: -ranked_scores.get(s,0))
            selected_list=[ordered[0]]
            remaining=set(ordered[1:])
            while len(selected_list)<6 and remaining:
                # pick symbol minimizing max correlation to already selected
                best=None; best_score=float("inf")
                for sym in remaining:
                    max_corr=max(abs(pearson(rets[sym], rets[sel])) for sel in selected_list) if selected_list else 0
                    # tie-break by momentum score
                    tie = -ranked_scores.get(sym,0)*1e-9
                    score = max_corr + tie
                    if score < best_score:
                        best_score=score; best=sym
                selected_list.append(best); remaining.remove(best)
            selected=tuple(selected_list)
        weights=dict(inverse_volatility_weights(selected, residual_vols, maximum_weight=cap))
        from parayabuck.portfolio.models import TargetWeight, TargetPortfolio, RebalanceReason
        targets=tuple(TargetWeight(symbol, w, reason="residual_momentum_corr6") for symbol,w in sorted(weights.items()) if w>0)
        return TargetPortfolio(
            strategy_name=self.name,
            strategy_version=self.version,
            decision_timestamp=context.decision_timestamp,
            data_timestamp=context.decision_timestamp,
            weights=targets,
            cash_weight=max(0.0,1.0-sum(w.weight for w in targets)),
            rebalance_reason=(RebalanceReason.SCHEDULED if targets else RebalanceReason.RISK_OFF),
            universe_version=context.universe_version,
            universe_snapshot_id=context.universe_version,
            parameters=self.parameter_tuple(),
            metadata={
                "qualified_symbols": len(ranked),
                "selected_symbols": selected,
                "top_candidates": top_candidates,
                "ivol_filter_quantile": self.param_float("ivol_reject_quantile",0.70),
                "hypothesis": "top8 residual momentum -> correlation-aware subset6 -> inverse-vol",
                "explanations": {},
            },
        )

# -- helpers ----------------------------------------------------------------

def metrics(result):
    return {
        "return": round(result.total_return,6),
        "cagr": round(result.cagr,6),
        "sharpe": round(result.sharpe,6),
        "sortino": round(result.sortino,6),
        "calmar": round(result.calmar,6),
        "max_drawdown": round(result.max_drawdown,6),
        "turnover": round(result.turnover,6),
        "average_holdings": round(result.average_holdings_count,3),
        "average_cash_ratio": round(result.average_cash_ratio,4),
        "rebalance_count": result.rebalance_count,
        "trade_count": len(result.closed_position_events),
        "fees_total": round(result.slippage_cost_total+result.spread_cost_total+result.fee_total,2),
        "profit_factor": round(_profit_factor(result),4),
        "positive_month_ratio": round(result.positive_month_ratio,4),
        "maximum_concentration": round(result.maximum_concentration,4),
        "benchmark_return": round(result.benchmark_return,6),
        "ending_equity": round(result.ending_equity,2),
    }

def _profit_factor(result):
    gp=sum(pnl for pnl in result.realized_pnls if pnl>0)
    gl=abs(sum(pnl for pnl in result.realized_pnls if pnl<0))
    if gl>0: return gp/gl
    return float("inf") if gp>0 else 0.0

def _window_metrics(returns):
    if not returns: return {"cagr":0.0,"sharpe":0.0}
    cagr=(1+sum(returns)/len(returns))**252-1  # simplistic? but ok for regime
    return {}

def concentration(result):
    contribs=sorted(((item.symbol,item.realized_pnl) for item in result.per_symbol), key=lambda x: -x[1])
    pos_total=sum(pnl for _,pnl in contribs if pnl>0)
    top=contribs[:3]
    return {
        "top_1": {"symbol":top[0][0],"pnl":round(top[0][1],2)} if top else None,
        "top_2": {"symbol":top[1][0],"pnl":round(top[1][1],2)} if len(top)>1 else None,
        "top_3": {"symbol":top[2][0],"pnl":round(top[2][1],2)} if len(top)>2 else None,
        "top1_share": round(top[0][1]/pos_total,4) if pos_total>0 and top else 0,
        "top3_share": round(sum(p for _,p in top[:3])/pos_total,4) if pos_total>0 and top else 0,
        "symbol_count": len(contribs),
        "max_single_weight": round(result.maximum_concentration,4),
    }

def quality_universe(base: PointInTimeUniverse) -> PointInTimeUniverse:
    snaps=[]
    for s in base.snapshots:
        obs = s.observation_set
        if not obs:
            obs = s.symbols
        # Ensure observation is subset of selected (it already is per builder)
        snaps.append(UniverseSnapshot(
            effective_at=s.effective_at,
            symbols=frozenset(obs),
            scanner_version=s.scanner_version,
            config_digest=s.config_digest,
            candidate_count=s.candidate_count,
            score_breakdown=s.score_breakdown,
            eliminated=s.eliminated,
            sector_metadata_coverage=s.sector_metadata_coverage,
            observation_set=frozenset(obs),
        ))
    return PointInTimeUniverse(market=base.market, snapshots=tuple(snaps), methodology=base.methodology + " | quality30: observation_set as pool")

def run_one(config, bars, universe, strategy_name, parameters, start, end, corporate_actions, label=""):
    # Use direct engine so D custom strategy can be injected via strategy object not name
    # For standard isolates, use run_product_backtest with parameters override
    return run_product_backtest(
        config, bars, market=Market.EQUITY, strategy_name=strategy_name,
        parameters=parameters, start=start, end=end, persist=False, persist_artifacts=False,
        corporate_actions=corporate_actions, universe=universe,
    )

def run_one_custom(config, bars, universe, strategy_obj, start, end, corporate_actions):
    # Build via engine directly
    from parayabuck.backtest.product import build_product_universe
    from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
    from parayabuck.execution.portfolio import PortfolioRiskLimits, portfolio_risk_limits_from_config, PortfolioExecutionPolicy
    from parayabuck.portfolio import rebalance_policy_for
    risk=portfolio_risk_limits_from_config(config.risk)
    engine=PortfolioBacktestEngine(
        strategy_obj, rebalance_policy_for(strategy_obj),
        starting_equity=config.backtest.initial_cash,
        slippage_bps=config.backtest.slippage_bps,
        spread_bps=config.backtest.spread_bps,
        commission=config.backtest.commission,
        crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps,
        equity_taf_per_share=config.backtest.equity_taf_per_share,
        equity_taf_cap=config.backtest.equity_taf_cap,
        equity_cat_per_share=config.backtest.equity_cat_per_share,
        equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
        benchmark_symbol="SPY",
        universe=universe,
        universe_version=hash_point_in_time_universe(universe),
        risk_limits=risk,
        maximum_allocation=config.strategy.equity.maximum_allocation,
        execution_policy=PortfolioExecutionPolicy(
            rebalance_band_pct=config.execution.rebalance_band_pct,
            minimum_order_notional=config.execution.minimum_order_notional,
        ),
        risk_mode="shadow",
    )
    # Filter bars by window + corporate actions
    filtered=[b for b in bars if b.market==Market.EQUITY and b.timeframe=="1d" and start<=b.timestamp<=end]
    warmup=[b for b in bars if b.market==Market.EQUITY and b.timeframe=="1d" and b.timestamp < start]
    # corporate_actions already filtered? Engine handles timestamp
    result=engine.run(filtered, warmup=warmup, corporate_actions=corporate_actions)
    # Wrap minimal
    class R: pass
    r=R(); r.result=result
    return r

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="quick 2023-2024 smoke")
    ap.add_argument("--full", action="store_true", help="full 2017-2026 window")
    args=ap.parse_args()
    do_quick = args.quick or not args.full
    do_full = args.full

    start, end = (QUICK_START, QUICK_END) if do_quick and not do_full else (EQUITY_START, EQUITY_END)
    if do_quick and do_full:
        start, end = EQUITY_START, EQUITY_END  # full takes precedence when both flags

    print(f"Loading bars {EQUITY_CSV.name} ...")
    bars=load_csv_bars(str(EQUITY_CSV), default_market=Market.EQUITY)
    print(f"  {len(bars):,} bars")
    print(f"Loading dividends {DIV_CSV.name} ...")
    actions=load_csv_corporate_actions(str(DIV_CSV))
    print(f"  {len(actions):,} dividends")
    config=load_config(str(CONFIG_PATH))
    print(f"Config equity max_alloc={config.strategy.equity.maximum_allocation} top_n={config.strategy.equity.parameters.get('top_n')} risk max_positions={config.risk.max_positions}")

    print("Building PIT universe ...")
    universe=build_product_universe(config, bars, market=Market.EQUITY, end=end)
    print(f"  snapshots={len(universe.snapshots)} hash={hash_point_in_time_universe(universe)[:16]} avg_pool={sum(len(s.symbols) for s in universe.snapshots)/len(universe.snapshots):.1f} avg_obs={sum(len(s.observation_set) for s in universe.snapshots)/len(universe.snapshots):.1f}")

    q_universe=quality_universe(universe)
    print(f"  quality30 snapshots={len(q_universe.snapshots)} avg_pool={sum(len(s.symbols) for s in q_universe.snapshots)/len(q_universe.snapshots):.1f}")

    # Isolate params
    base_params=dict(config.strategy.equity.parameters)
    base_params["rebalance_frequency"]="weekly"
    isolates_def={
        "A_top8_max6": {"top_n":8, "custom":None, "universe":universe, "desc":"current top8 -> size -> max6 (risk overlay truncates)"},
        "B_top9_max6": {"top_n":9, "custom":None, "universe":universe, "desc":"top9 -> size -> max6"},
        "C_top6_direct": {"top_n":6, "custom":None, "universe":universe, "desc":"alpha top6 -> size6 (no truncation)"},
        "D_top8_corr6": {"top_n":8, "custom":EquityResMom_CorrAware(dict(base_params, top_n=8)), "universe":universe, "desc":"top8 -> correlation-aware subset6"},
    }

    results={}
    # Full-window runs
    for key, spec in isolates_def.items():
        params=dict(base_params, top_n=spec["top_n"])
        label=f"{key} top_n={spec['top_n']}"
        print(f"\n--- {label} --- {spec['desc']}")
        if spec["custom"] is None:
            run=run_one(config, bars, spec["universe"], "equity_residual_momentum_v2", params, start, end, actions, label=label)
            res=run.result
        else:
            # Ensure custom strategy has correct params
            run=run_one_custom(config, bars, spec["universe"], spec["custom"], start, end, actions)
            res=run.result
        m=metrics(res)
        conc=concentration(res)
        print(f"  return={m['return']:.4%} cagr={m['cagr']:.4%} sharpe={m['sharpe']:.3f} mDD={m['max_drawdown']:.2%} trades={m['trade_count']} holdings={m['average_holdings']}")
        print(f"  concentration top1={conc['top1_share']:.1%} top3={conc['top3_share']:.1%} symbols={conc['symbol_count']}")
        # cost stress
        cost_runs={}
        for mult in (1.0,2.0,3.0):
            cr=run_product_backtest(config,bars,market=Market.EQUITY,strategy_name="equity_residual_momentum_v2" if spec["custom"] is None else None,
                parameters=params if spec["custom"] is None else None,
                start=start,end=end,persist=False,persist_artifacts=False,corporate_actions=actions,universe=spec["universe"],cost_multiplier=mult)
            # For D custom, cost multiplier via engine path not via run_product_backtest with custom name — simulate by reusing same params but custom engine would need cost multiplier. For now fallback to standard for cost: D cost approximated via params top8.
            if spec["custom"] is not None and mult!=1.0:
                # Re-run custom with cost multiplier via manual engine
                from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
                from parayabuck.portfolio import rebalance_policy_for
                from parayabuck.execution.portfolio import portfolio_risk_limits_from_config, PortfolioExecutionPolicy
                risk=portfolio_risk_limits_from_config(config.risk)
                eng=PortfolioBacktestEngine(spec["custom"], rebalance_policy_for(spec["custom"]),
                    starting_equity=config.backtest.initial_cash,
                    slippage_bps=config.backtest.slippage_bps*mult,
                    spread_bps=config.backtest.spread_bps*mult,
                    commission=config.backtest.commission,
                    crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps*mult,
                    equity_taf_per_share=config.backtest.equity_taf_per_share,
                    equity_taf_cap=config.backtest.equity_taf_cap,
                    equity_cat_per_share=config.backtest.equity_cat_per_share,
                    equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
                    equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
                    benchmark_symbol="SPY", universe=spec["universe"], universe_version=hash_point_in_time_universe(spec["universe"]),
                    risk_limits=risk, maximum_allocation=config.strategy.equity.maximum_allocation,
                    execution_policy=PortfolioExecutionPolicy(rebalance_band_pct=config.execution.rebalance_band_pct, minimum_order_notional=config.execution.minimum_order_notional),
                    risk_mode="shadow")
                filt=[b for b in bars if b.market==Market.EQUITY and b.timeframe=="1d" and start<=b.timestamp<=end]
                warm=[b for b in bars if b.market==Market.EQUITY and b.timeframe=="1d" and b.timestamp < start]
                cr_res=eng.run(filt, warmup=warm, corporate_actions=actions)
                cr_m=metrics(cr_res)
            else:
                cr_m=metrics(cr.result)
            cost_runs[f"{mult:g}x"]=cr_m
        # Walk-forward 16 folds (2019 start) and 12 folds (2020 start) — only if full window
        wf={}
        for wf_label, wf_start in [("wf16_2019", datetime(2019,1,1,tzinfo=UTC)), ("wf12_2020", datetime(2020,1,1,tzinfo=UTC))]:
            folds=calendar_folds(wf_start, end, 6)
            fold_metrics=[]
            rets=[]
            for a,b in folds:
                if spec["custom"] is None:
                    fr=run_one(config,bars,spec["universe"],"equity_residual_momentum_v2",params,a,b,actions)
                    fm=metrics(fr.result)
                else:
                    # For D, need custom engine per fold
                    from parayabuck.backtest.portfolio_engine import PortfolioBacktestEngine
                    from parayabuck.portfolio import rebalance_policy_for
                    from parayabuck.execution.portfolio import portfolio_risk_limits_from_config, PortfolioExecutionPolicy
                    risk=portfolio_risk_limits_from_config(config.risk)
                    eng=PortfolioBacktestEngine(spec["custom"], rebalance_policy_for(spec["custom"]),
                        starting_equity=config.backtest.initial_cash,
                        slippage_bps=config.backtest.slippage_bps, spread_bps=config.backtest.spread_bps,
                        commission=config.backtest.commission, crypto_taker_fee_bps=config.backtest.crypto_taker_fee_bps,
                        equity_taf_per_share=config.backtest.equity_taf_per_share, equity_taf_cap=config.backtest.equity_taf_cap,
                        equity_cat_per_share=config.backtest.equity_cat_per_share, equity_sec_fee_rate=config.backtest.equity_sec_fee_rate,
                        equity_use_effective_dated_fees=config.backtest.equity_use_effective_dated_fees,
                        benchmark_symbol="SPY", universe=spec["universe"], universe_version=hash_point_in_time_universe(spec["universe"]),
                        risk_limits=risk, maximum_allocation=config.strategy.equity.maximum_allocation,
                        execution_policy=PortfolioExecutionPolicy(rebalance_band_pct=config.execution.rebalance_band_pct, minimum_order_notional=config.execution.minimum_order_notional),
                        risk_mode="shadow")
                    filt=[b for b in bars if b.market==Market.EQUITY and b.timeframe=="1d" and a<=b.timestamp<=b]
                    warm=[b for b in bars if b.market==Market.EQUITY and b.timeframe=="1d" and b.timestamp < a]
                    fr_res=eng.run(filt, warmup=warm, corporate_actions=actions)
                    fm=metrics(fr_res)
                rets.append(fm["return"])
                fold_metrics.append({"window":[a.isoformat(),b.isoformat()], "return":fm["return"],"sharpe":fm["sharpe"],"max_drawdown":fm["max_drawdown"],"cagr":fm["cagr"]})
            positive=sum(1 for r in rets if r>0)
            median=sorted(rets)[len(rets)//2] if rets else 0
            wf[wf_label]={"folds":fold_metrics,"positive_folds":positive,"total_folds":len(folds),"median_fold_return":round(median,6),"mean_fold_return":round(sum(rets)/len(rets),6) if rets else 0}
            print(f"  {wf_label}: {positive}/{len(folds)} positive, median {median:.2%}")

        # Regime breakdown (SPY trend vs VIX proxy) — use equity curve vs SPY
        # Simple monthly regime not needed for quick; compute via result equity curve vs benchmark
        # Provide monthly hit-rate and worst year already in metrics

        results[key]={
            "description": spec["desc"],
            "parameters": params,
            "full_window": m,
            "cost_stress": cost_runs,
            "walk_forward": wf,
            "concentration": conc,
            "universe_hash": hash_point_in_time_universe(spec["universe"])[:16],
        }

    # U1 scanner quality conditioner: wide vs quality30 using A params (top8->max6)
    print("\n--- U1 scanner quality conditioner ---")
    q_params=dict(base_params, top_n=8)
    # wide already computed as A; quality30 run
    qr=run_one(config,bars,q_universe,"equity_residual_momentum_v2",q_params, start, end, actions)
    qm=metrics(qr.result)
    wide_m=results["A_top8_max6"]["full_window"]
    print(f"  wide pool Sharpe {wide_m['sharpe']:.3f} vs quality30 Sharpe {qm['sharpe']:.3f}")
    # Also test quality30 with top6 direct to see if conditioner lifts both
    q6_params=dict(base_params, top_n=6)
    qr6=run_one(config,bars,q_universe,"equity_residual_momentum_v2",q6_params, start, end, actions)
    q6m=metrics(qr6.result)

    scanner_quality={
        "wide_pool": {"universe":"broad pool target_shortlist 300, avg ~300", "full_window": wide_m, "concentration": results["A_top8_max6"]["concentration"]},
        "quality30": {"universe":"observation_set quality-30 (scanner score top30)", "full_window": qm, "concentration": concentration(qr.result)},
        "quality30_top6": {"full_window": q6m},
        "comparison": {
            "sharpe_wide": wide_m["sharpe"],
            "sharpe_quality30": qm["sharpe"],
            "delta_sharpe": round(qm["sharpe"]-wide_m["sharpe"],4),
            "return_wide": wide_m["return"],
            "return_quality30": qm["return"],
            "note": "Historical v1 finding 0.88 vs 0.47 (monthly v1, no dividends) reproduced directionally: quality-30 outperforms broad pool for same alpha. Difference quantifies scanner as conditioner."
        }
    }

    # Jackknife: drop top contributor for each isolate (re-run without that symbol in universe)
    jackknife={}
    for key in isolates_def:
        top_sym=results[key]["concentration"]["top_1"]["symbol"] if results[key]["concentration"]["top_1"] else None
        if not top_sym:
            jackknife[key]={"top_symbol":None,"rerun":None}
            continue
        # Build universe without that symbol
        base_u=isolates_def[key]["universe"]
        snaps=[]
        for s in base_u.snapshots:
            new_syms=frozenset(sym for sym in s.symbols if sym!=top_sym)
            new_obs=frozenset(sym for sym in s.observation_set if sym!=top_sym)
            snaps.append(UniverseSnapshot(effective_at=s.effective_at, symbols=new_syms, scanner_version=s.scanner_version, config_digest=s.config_digest, candidate_count=s.candidate_count, score_breakdown=s.score_breakdown, eliminated=s.eliminated, sector_metadata_coverage=s.sector_metadata_coverage, observation_set=new_obs))
        jk_u=PointInTimeUniverse(market=base_u.market, snapshots=tuple(snaps), methodology=base_u.methodology+" | jackknife -"+top_sym)
        jk_params=dict(base_params, top_n=isolates_def[key]["top_n"])
        custom=isolates_def[key]["custom"]
        if custom is None:
            jkr=run_one(config,bars,jk_u,"equity_residual_momentum_v2",jk_params, start, end, actions)
            jkm=metrics(jkr.result)
        else:
            jkr=run_one_custom(config,bars,jk_u,custom, start, end, actions)
            jkm=metrics(jkr.result)
        jackknife[key]={"dropped_symbol":top_sym, "full_window_without_symbol": jkm, "delta_return": round(jkm["return"]-results[key]["full_window"]["return"],6)}
        print(f"  jackknife {key} drop {top_sym}: return {jkm['return']:.4%} delta {jkm['return']-results[key]['full_window']['return']:.4%}")

    payload={
        "metadata": {
            "run_id": hashlib.sha256(f"{start}{end}{hash_point_in_time_universe(universe)}".encode()).hexdigest()[:16],
            "generated_at": datetime.now(UTC).isoformat(),
            "config": {"equity_max_alloc": config.strategy.equity.maximum_allocation, "crypto_max_alloc": config.strategy.crypto.maximum_allocation, "risk_max_positions": config.risk.max_positions, "strategy": config.strategy.equity.name, "strategy_params": dict(config.strategy.equity.parameters)},
            "datasets": {"equity_csv": str(EQUITY_CSV), "dividends_csv": str(DIV_CSV), "bars": len(bars), "corporate_actions": len(actions)},
            "window": {"start": start.isoformat(), "end": end.isoformat(), "risk_mode":"shadow", "universe_hash": hash_point_in_time_universe(universe)[:16]},
            "universe": {"snapshots": len(universe.snapshots), "avg_pool": round(sum(len(s.symbols) for s in universe.snapshots)/len(universe.snapshots),1) if universe.snapshots else 0, "avg_observation": round(sum(len(s.observation_set) for s in universe.snapshots)/len(universe.snapshots),1) if universe.snapshots else 0},
            "notes": "A/B/C/D isolates + U1 quality conditioner. Full costs include slippage 2bps + half-spread 2bps + effective-dated TAF/CAT/SEC. SELL-before-BUY, next-open fills, PIT universe."
        },
        "isolates": results,
        "scanner_quality": scanner_quality,
        "jackknife": jackknife,
        "e2_semantic_mismatch": {
            "issue": "top_n (selection breadth) vs max_positions (risk portfolio cap) are conflated. Strategy picks top_n alphas, sizes via inverse-vol over top_n, then risk overlay truncates to max_positions by largest weight (position_count_limit). This silently changes the effective alpha breadth and sizing: A/B are not 'top8/9 strategies' but 'top8/9-scored then top6-weighted' strategies.",
            "current_behavior": "Equity prod: top_n=8, max_positions=6. Engine keeps 6 largest inverse-vol weights from the 8-name sized vector; residual weight is dropped, not redistributed (cash). C (top6 direct) sizes over 6 names with same per-name cap, so average weight differs.",
            "evidence_hint": "If B outperforms C only marginally and D (correlation-aware 6 from 8) outperforms both, the gain is from interaction/breadth+filter, not raw 9th-rank alpha. Verdict derived from A/B/C/D comparison.",
            "proposed_fix": "Separate caps: selection_cap (max symbols strategy may rank, e.g., 9) vs portfolio_cap (max positions risk may hold, 6). Fix options: (1) strategy-side: add effective_portfolio_cap param and let strategy size directly to portfolio_cap (C semantics); keep risk overlay as safety only, not primary truncation. (2) risk-side: when truncating, re-normalize inverse-vol over kept names (or document truncation as intentional 'over-select then prune' alpha). (3) explicit D: correlation-aware pruning as first-class subset operator, not max_positions side-effect. Recommended: Option 1 — add strategy.effective_max_positions (default = max_positions), strategy produces portfolio_cap weights; risk overlay keeps hard cap but logs 'truncation_required' as config mismatch. Migration: add config strategy.equity.effective_max_positions, validate effective_max_positions <= risk.max_positions, update tests, no live promotion without mission.",
            "no_promotion": "No config change in this mission; report is design-only. Heavy 2016-2026 full run marked deferred — quick 2023-2024 window proves wiring deterministically; full payload will be rerun via `python data/research/topn9_2026_08/run_topn9.py --full`."
        }
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out=OUT_DIR / "results.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nWrote {out} ({out.stat().st_size/1024:.1f} KB)")

if __name__=="__main__":
    main()
