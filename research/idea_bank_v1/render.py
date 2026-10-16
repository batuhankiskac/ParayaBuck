"""Render docs/research/idea_bank_v1.{json,md} from the four ideas_*.py data modules.

No simulation, no network, no data read: this only formats the pre-registered idea list.
Run from the worktree root:  python -m research.idea_bank_v1.render
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # allow `python research/idea_bank_v1/render.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from research.idea_bank_v1 import ideas_a, ideas_b, ideas_c, ideas_d
else:
    from . import ideas_a, ideas_b, ideas_c, ideas_d

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "research"

IDEAS = (
    ideas_a.TREND
    + ideas_a.VOL
    + ideas_b.MACRO
    + ideas_b.CALENDAR
    + ideas_b.CROSS_ASSET
    + ideas_c.OFF_STATE
    + ideas_c.INSTRUMENT
    + ideas_c.DRAWDOWN
    + ideas_d.STATISTICAL
    + ideas_d.EXECUTION
    + ideas_d.TAX
)

CATEGORIES = [
    {"id": "trend-signal", "label": "Trend-signal algebra", "idea_ids": [i["id"] for i in IDEAS if i["category"] == "trend-signal"]},
    {"id": "vol-estimator", "label": "Volatility estimators and sizing inputs", "idea_ids": [i["id"] for i in IDEAS if i["category"] == "vol-estimator"]},
    {"id": "macro-filter", "label": "Macro / credit / rates filters", "idea_ids": [i["id"] for i in IDEAS if i["category"] == "macro-filter"]},
    {"id": "calendar", "label": "Calendar and structural-date effects", "idea_ids": [i["id"] for i in IDEAS if i["category"] == "calendar"]},
    {"id": "cross-asset", "label": "Cross-asset signals", "idea_ids": [i["id"] for i in IDEAS if i["category"] == "cross-asset"]},
    {"id": "off-state", "label": "Off-state holdings", "idea_ids": [i["id"] for i in IDEAS if i["category"] == "off-state"]},
    {"id": "on-state-instrument", "label": "On-state instrument choice", "idea_ids": [i["id"] for i in IDEAS if i["category"] == "on-state-instrument"]},
    {"id": "drawdown-recovery", "label": "Drawdown / recovery-state sizing", "idea_ids": [i["id"] for i in IDEAS if i["category"] == "drawdown-recovery"]},
    {"id": "statistical-filter", "label": "Model-free statistical filters", "idea_ids": [i["id"] for i in IDEAS if i["category"] == "statistical-filter"]},
    {"id": "execution", "label": "Execution timing", "idea_ids": [i["id"] for i in IDEAS if i["category"] == "execution"]},
    {"id": "tax-structure", "label": "Tax unit, regime and funding", "idea_ids": [i["id"] for i in IDEAS if i["category"] == "tax-structure"]},
]

# arms that a two-stage screen cannot express as pure target weights
KERNEL_ARMS = {
    "IB25": ["A", "B"],
    "IB26": ["A", "B", "C"],
    "IB27": ["A", "B", "C"],
    "IB28": ["A", "B", "C"],
    "IB36": ["A", "B", "C"],
    "IB37": ["A", "B", "C"],
    "IB38": ["C"],
    "IB39": ["B", "C"],
    "IB40": ["A", "B", "C"],
}
# ideas whose kernel need is confined to the after-tax stage (pre-tax runs as a blended instrument)
KERNEL_AFTER_TAX_ONLY = {"IB27", "IB28"}

EVALUATION_CONTRACT = {
    "control": ("C0 = production K = R2 (E2 2-of-3 vote + half-weight early re-entry) + A1u gain-scaled "
                "downsize band (0.5/3/8 pp at a USD FIFO gain fraction <=1% / <=5% / >5%) + H2 (a "
                "downsize filling in the last 10 NYSE December sessions defers to January).  Reference "
                "cells to reproduce before any arm runs: pre-tax 15.7649% base / 15.1785% calibrated, "
                "max DD 46.6698% / 46.7948%, after tax at $100k 10.7533 / 15.2354 / 23.9720%."),
    "pre_tax_gates": ("ewma_band_v1's G1-G9 against K: G1 return (CAGR >= C0, or Calmar >= 1.1x with "
                      "CAGR >= C0 - 0.5 pp), G2 max DD <= C0 + 1 pp, G3 2017+ >= C0 - 1 pp, G4 CAGR above "
                      "C0 in >= 2 of 3 sub-periods, G5 block-20 bootstrap (seed 20261011, 5,000 paths) "
                      "median >= 0, G6 G1 at m = 1, G7 the six OOS windows, G8 the pooled OOS bootstrap, "
                      "G9 turnover <= 2x C0; G1/G2/G3/G5/G6 under both financing models."),
    "after_tax_gates": ("beats K at $100k in all three windows (2000-03-09 -> 2026-09-29; 2010-01-01 ->; "
                        "2017-01-03 ->) AND clean bootstrap B1-recent and B2-flat median delta >= 0 with "
                        "share > 0 >= 55% (seed 20261011, block 20)."),
    "oos_windows": ["GSPC_1957_1999", "M1 ^IXIC 1975-1999", "M2 ^GSPC 1935-1956", "M3 ^N225 1966-2026",
                    "M4 ^GDAXI 1989-2026", "M5 ^FTSE 1985-2026"],
    "robustness_stages": ["21 quality-passing ETFs pre-tax (calibrated, m = 2, R-USD)",
                          "E1: 21 ETFs after tax under R-INDEX at $100k",
                          "E2: 21 rolling January starts (2001-2021) after tax",
                          "E3: fresh bootstraps (seed 20261015, blocks 10/40)",
                          "m = 4 cost stress after tax at $100k"],
    "reduced_gate_convention": ("where a window or stage is not evaluable (documented per idea), the "
                                "study must pre-register the reduced gate before running, exactly as "
                                "credit_vote_v1 did for its non-US windows."),
}

PLUGIN_CONTRACT = {
    "targets_from_data": ("the arm is a new numpy function of the instrument's own OHLC(V) plus any "
                          "external series aligned by date, producing the per-session target-weight "
                          "array the engine already consumes; the K fill rule (band / USD gain fraction "
                          "/ December deferral) is unchanged.  This is fe_signals.arm_signals' existing "
                          "extension point and needs no engine edit."),
    "kernel_option": ("the arm changes the fill/decision logic itself (a fill-price or fill-filter rule, "
                      "a second leg with its own FIFO book, a state-dependent band width, a tax-funding "
                      "route).  It requires a copy of the engine inside research/<study>/ with the option "
                      "added and a parity cell proving the option switched off reproduces the engine "
                      "bit-for-bit (WORKER_PROTOCOL.md)."),
    "screen_stage_1": ("pre-tax main window + G1-G9 + the 21-ETF and rolling-start stages; kill anything "
                       "failing G1/G2/G5/G9 or the OOS median."),
    "screen_stage_2": ("after-tax windows + B1/B2 + the cost stress for survivors only."),
    "held_out_confirmation": ("the pre-2000 OOS windows and the E3 fresh bootstraps must be the only "
                              "confirmation set; the multiple-testing correction must count *all* arms "
                              "run across the bank, not only the survivors of stage 1."),
}

DATA_INVENTORY = [
    {"series": "QQQ OHLC+adjclose+volume+dividend", "file": "data/historical/stress_robustness_v1_daily.csv",
     "coverage": "1999-03-10 -> 2026-09-29", "role": "main-window instrument and every signal input"},
    {"series": "SPY, QLD (real), VOO", "file": "data/historical/stress_robustness_v1_daily.csv",
     "coverage": "SPY 1999-01-04 ->; QLD 2006-06-21 ->; VOO 2010-09-09 ->", "role": "benchmarks, real-fund checks"},
    {"series": "synthetic daily-reset 2x QLD*", "file": "built by fe_signals.synthetic_2x from QQQ",
     "coverage": "1999-03-10 -> 2026-09-29", "role": "the traded leg in every stage"},
    {"series": "DTB3 (financing / bill yield)", "file": "data/historical/stress_robustness_v1_dtb3.csv",
     "coverage": "1954 -> 2026-09", "role": "financing, cash yield"},
    {"series": "TB3MS (monthly bill rate)", "file": "data/historical/ewma_band_v1_tb3ms.csv",
     "coverage": "1934-01 ->", "role": "pre-1954 OOS financing"},
    {"series": "^GSPC", "file": "data/historical/ewma_band_v1_gspc.csv (1957-1999 close only) and ewma_band_v1_index.csv (1927-1956 OHLC)",
     "coverage": "1927-12-30 -> 1999-12-31", "role": "GSPC_1957_1999 and M2 OOS windows"},
    {"series": "^IXIC / ^N225 / ^GDAXI / ^FTSE", "file": "data/historical/ewma_band_v1_index.csv",
     "coverage": "^IXIC 1971-02-05->1999-12-31; ^N225 1965->2026-09-29; ^GDAXI 1987-12-30->; ^FTSE 1984-01-03-> (OHLC)",
     "role": "M1-M5 OOS windows and the international lead-lag signal"},
    {"series": "21 quality-passing ETFs (XLB XLE XLF XLI XLK XLP XLU XLV XLY EWJ EWG EWU EWA EWH EWS EWL EFA EEM SPY DIA IWM)",
     "file": "data/historical/range_vol_v2_*.csv / range_vol_v1_*.csv (OHLC+adjclose, no volume); volume+dividend in riskoff_rotation_v4_etfs.csv",
     "coverage": "mostly 1999-01-04 ->; EFA 2001-08-27; EEM 2003-04-14; IWM 2000-05-26", "role": "breadth stage"},
    {"series": "IEF / TLT / GLD / GC=F / TQQQ", "file": "riskoff_rotation_v4_etfs.csv, riskoff_rotation_v2_*.csv, leverage_frontier_v1_daily.csv",
     "coverage": "IEF/TLT 2002-07-30 ->; GLD 2004-11-18 ->; GC=F 2000-08-30 ->; TQQQ 2010-02-11 ->",
     "role": "bond/gold legs and the instrument arms"},
    {"series": "^VIX / ^VIX3M / ^VXN", "file": "data/historical/vix_filter_v1_vix.csv, _vix3m.csv, _vxn.csv, _fred.csv",
     "coverage": "^VIX 1990-01-02 ->; ^VXN 2001-01-23 ->; ^VIX3M 2006-07-17 ->", "role": "VRP arm"},
    {"series": "BAA10Y / T10Y3M / DGS10 / BAA / AAA / GS10 / TB3MS", "file": "data/historical/credit_vote_v1_*.csv, leverage_frontier_v1_dgs10.csv",
     "coverage": "BAA10Y 1986-01-02 ->; T10Y3M 1982-01-04 ->; DGS10 1962-01-02 ->; BAA/AAA monthly 1919-01 ->; GS10 monthly 1953-04 ->",
     "role": "macro/credit arms"},
    {"series": "USD/TRY (CBRT) and YI-UFE", "file": "data/historical/after_tax_tr_v1_usdtry_cbrt.csv, _yiufe.csv",
     "coverage": "USD/TRY 2000-01-03 ->; YI-UFE 1999-01 ->", "role": "the after-tax engine and the FX-regime arms"},
    {"series": "CAPE / earnings yield / 10y real return (Shiller)", "file": "data/historical/valuation_v1_shiller_monthly.csv",
     "coverage": "1871-01 -> 2024-09 (monthly)", "role": "already tested (valuation_v1); listed for completeness"},
    {"series": "^NDX daily", "file": "data/historical/valuation_v1_ndx_daily.csv, vol_shape_v1_ndx.csv",
     "coverage": "1985-10-01 -> 2000-03-22", "role": "pre-2000 calibration windows"},
    {"series": "^RUT", "file": "data/historical/breadth_vote_v1_rut.csv", "coverage": "1987-09-10 -> 2026-10-14",
     "role": "breadth (already tested)"},
    {"series": "BIL / SGOV / TLT daily (2016+)", "file": "data/historical/tbill_sweep_v1_etf_daily_split_sip.csv",
     "coverage": "2016-01-04 -> 2026-07-31", "role": "bill-sleeve reference"},
]

NEW_DOWNLOADS = [
    {"series": "CPI (CPIAUCSL, monthly SA index, 1947+)", "needed_by": ["IB12"], "source": "FRED CSV"},
    {"series": "SMH (semiconductor ETF, 2000-05-05+)", "needed_by": ["IB20"], "source": "Yahoo chart API"},
    {"series": "HG=F (COMEX copper front future, 2000+)", "needed_by": ["IB23"], "source": "Yahoo chart API"},
    {"series": "SHY (1-3y Treasury ETF, inception 2002-07)", "needed_by": ["IB25"], "source": "Yahoo chart API"},
    {"series": "scheduled FOMC meeting dates 1994-2026", "needed_by": ["IB17"], "source": "federalreserve.gov public calendar (not market data)"},
]

CLOSED_AXES = [
    {"family": "Trend horizon / aggregation (SMA 100-250, Donchian 126, 2-of-3, proportional, single-signal, hysteresis, confirmation days, exit buffers, min-hold, staged thirds)",
     "closed_by": "trend_robustness_v2, signal_ensemble_v1, vote_hysteresis_v1, rule_variants_v1, e2_confirmation_v1"},
    {"family": "Total-return votes and T-bill / 2% hurdles", "closed_by": "tsm_tr_v1"},
    {"family": "Vol estimator / window / output band (EWMA 0.94, vol10/40/60, max(vol20,vol60), semi-deviation, Parkinson/GK/YZ, 2.5/5/10 pp bands)",
     "closed_by": "vol_window_v1, ewma_band_v1, range_vol_v1-v3"},
    {"family": "Vol-target level and leverage grid", "closed_by": "vt_level_tax_v1, leverage_frontier_v1, exposure_k_v1, tax_asym_v1-v3"},
    {"family": "Vol-target shape min(1, c/sigma^p)", "closed_by": "vol_shape_v1"},
    {"family": "Valuation / ERP-conditioned exposure", "closed_by": "valuation_v1"},
    {"family": "Idle/risk-off asset (BIL, BOND10/IEF, GOLD, PSQ/QID, sector and country ETFs, bill sweep as a rule)",
     "closed_by": "tbill_sweep_v1, r2_tbill_combo_v1, leverage_frontier_v1, riskoff_rotation_v1-v4, riskoff_after_tax_v1, gold_riskoff_oos_v1, shortside_v1"},
    {"family": "Macro / credit / VIX filters at the *level* (BAA10Y vs its SMA, curve inversion, VIX level / spike / term structure, VXN sizing)",
     "closed_by": "credit_vote_v1, vix_filter_v1"},
    {"family": "Execution price and timing (MOC, next close, t+2 open, split fills, overnight hold, intraday stops)",
     "closed_by": "moc_execution_v1, exec_delay_v1, overnight_hold_v1, intraday_stop_v1"},
    {"family": "Seasonality and calendar (May-Oct half, September cash, turn-of-month, month-end deferral)",
     "closed_by": "seasonal_overlay_v1, seasonal_placebo_v1, monthly_defer_v1"},
    {"family": "Instrument choice among 2x/3x funds (TQQQ at 2/3, early leg in QQQ/SPY/SSO, QLD vs SSO rotation)",
     "closed_by": "instrument_cost_v1, early_qqq_v1, relstrength_v1"},
    {"family": "Realization timing (year-end loss harvest, December deferral, gain-scaled band, monthly deferral, inverse hedge, state-keyed deferral, exit-leg timing, smoothing)",
     "closed_by": "tax_harvest_v1/v2, gain_band_v1-v3, monthly_defer_v1, tax_hedge_v1, state_defer_v1, exit_timing_v2, smooth_target_v1"},
    {"family": "Annual tax budget / deliberate gain realization", "closed_by": "tax_budget_v2"},
    {"family": "Early-leg size and trigger (ladder, 2-of-3 quorum, crash path)", "closed_by": "ladder_v1, early_quorum_v1, crash_entry_v1, fast_reentry_v2"},
    {"family": "Dip buying / stops / crash entry / short side", "closed_by": "dip_overlay_v1, intraday_stop_v1, crash_entry_v1, shortside_v1"},
    {"family": "Lot-level selection (FIFO blocked by the broker) and two-queue wrappers", "closed_by": "idea_survey_v2 I1 (never run, blocked); relstrength_v1 for the wrapper"},
    {"family": "ML / sequence models", "closed_by": "timesfm_v1"},
]

STRONGEST = [i["id"] for i in sorted((x for x in IDEAS if x["rank"]), key=lambda x: x["rank"])]


def build_json() -> dict:
    ideas = []
    for idea in IDEAS:
        arms = []
        for arm in idea["arms"]:
            needs_kernel = arm["id"] in KERNEL_ARMS.get(idea["id"], [])
            arms.append({
                "id": arm["id"],
                "rule": arm["rule"],
                "targets_from_data": not needs_kernel,
                "needs_kernel_option": needs_kernel,
                "kernel_scope": ("after_tax_stage_only"
                                 if needs_kernel and idea["id"] in KERNEL_AFTER_TAX_ONLY
                                 else ("all_stages" if needs_kernel else None)),
                "params_frozen": True,
            })
        ideas.append({
            "id": idea["id"],
            "title": idea["title"],
            "category": idea["category"],
            "strength_rank": idea["rank"],
            "mechanism": idea["mechanism"],
            "literature": idea["literature"],
            "closest_tested": idea["closest_tested"],
            "signal_inputs": idea["signal_inputs"],
            "data": idea["data"],
            "plugin": idea["plugin"],
            "arms": arms,
            "expected_evidence": idea["prior"],
            "n_arms": len(arms),
        })
    return {
        "schema": "parayabuck.idea_bank/v1",
        "study": "idea_bank_v1",
        "date": "2026-10-16",
        "branch": "research/idea_bank_v1",
        "artefact_type": "idea bank (no backtest, no simulation, no gate run)",
        "purpose": ("40 pre-registered, parameter-frozen ideas for improving production K after Turkish "
                    "income tax, for an automated two-stage screen with a held-out confirmation and a "
                    "multiple-testing correction (research/batch_screen)."),
        "counts": {"ideas": len(ideas), "arms": sum(i["n_arms"] for i in ideas),
                   "categories": len(CATEGORIES), "strongest_marked": len(STRONGEST)},
        "evaluation_contract": EVALUATION_CONTRACT,
        "plugin_contract": PLUGIN_CONTRACT,
        "data_inventory": DATA_INVENTORY,
        "new_downloads": NEW_DOWNLOADS,
        "closed_axes": CLOSED_AXES,
        "categories": CATEGORIES,
        "strongest_ten": STRONGEST,
        "ideas": ideas,
    }


def render_md(doc: dict) -> str:
    out = []
    a = out.append
    a("# Idea bank v1 (2026-10-16): 40 pre-registered ideas for improving K after Turkish tax")
    a("")
    a("> Artefact: an *idea bank*, not a study.  No backtest was run, no gate was evaluated, no arm was "
      "simulated, no data was downloaded.  `.md` and `.json` are rendered from "
      "`research/idea_bank_v1/ideas_{a,b,c,d}.py` by `research/idea_bank_v1/render.py`, so the two files "
      "cannot drift.")
    a("")
    a("**Verdict (what this is).** 40 ideas, 119 arms, every parameter fixed now and never tuned later, "
      "grouped into 11 categories and spread deliberately beyond the axes the record has already closed. "
      "Each idea names the closest tested study and the difference that makes it new; the 10 strongest are "
      "ranked 1-10. The bank is written for a two-stage automated screen (pre-tax gates G1-G9 + the "
      "robustness stages; after-tax windows + B1/B2 for survivors) with a held-out confirmation and a "
      "multiple-testing correction, and each arm is tagged with whether it is expressible as "
      "*target weights from data* (the engine's existing extension point) or needs a new execution kernel.")
    a("")
    a(f"**Counts.** ideas {doc['counts']['ideas']} | arms {doc['counts']['arms']} | categories "
      f"{doc['counts']['categories']} | strongest marked {doc['counts']['strongest_marked']} | "
      f"arms needing a kernel option "
      f"{sum(1 for i in doc['ideas'] for ar in i['arms'] if ar['needs_kernel_option'])}.")
    a("")
    a("**The 10 strongest, in rank order.**")
    a("")
    a("| rank | id | idea | why it is ranked here |")
    a("|---:|---|---|---|")
    why = {
        "IB27": "a deterministic carry saving (a cost arbitrage between two Nasdaq instruments), no signal content",
        "IB38": "the largest potential payoff (+3..+5 pp if a household member qualifies for GVK muk. 20/D) and zero market risk",
        "IB01": "a new scale-free statistic on the strategy's core vote at zero data cost",
        "IB22": "causally clean: Europe's close precedes the US close, so the signal is genuinely available at the decision",
        "IB04": "a serial-dependence vote with a literature, orthogonal to every tested price vote",
        "IB28": "the broker-legal version of lot selection: two legs, route the downsize to the cheaper FIFO head",
        "IB39": "the only tax-regime question the record names and never ran; can invalidate K's execution layer",
        "IB20": "the cheapest plausible leading cyclical signal (semis) and it is not a second index",
        "IB12": "the macro axis with the better-motivated variable (real rate, not a nominal spread level)",
        "IB32": "acts on the trim channel that carries ~91% of the realised taxable gain, with a two-line change",
    }
    rank_by_id = {i["id"]: i["rank"] for i in IDEAS}
    for iid in STRONGEST:
        idea = next(i for i in IDEAS if i["id"] == iid)
        a(f"| {rank_by_id[iid]} | {iid} | {idea['title']} | {why[iid]} |")
    a("")
    a("## 1. How the bank is meant to be consumed")
    a("")
    a(f"- **Control.** {EVALUATION_CONTRACT['control']}")
    a(f"- **Stage 1 (pre-tax).** {PLUGIN_CONTRACT['screen_stage_1']}  {EVALUATION_CONTRACT['pre_tax_gates']}")
    a(f"- **Stage 2 (after tax).** {PLUGIN_CONTRACT['screen_stage_2']}  {EVALUATION_CONTRACT['after_tax_gates']}")
    a(f"- **Held-out confirmation.** {PLUGIN_CONTRACT['held_out_confirmation']}")
    a(f"- **Reduced gates.** {EVALUATION_CONTRACT['reduced_gate_convention']}")
    a("")
    a("### 1.1 The plugin contract (targets from data vs a new kernel)")
    a("")
    a(f"- **targets from data.** {PLUGIN_CONTRACT['targets_from_data']}")
    a(f"- **kernel option.** {PLUGIN_CONTRACT['kernel_option']}")
    a("")
    a("Arms tagged `needs_kernel_option` in the JSON (the `kernel_scope` key says whether the cost is paid "
      "in every stage or only in the after-tax stage):")
    a("")
    a("| idea | arms | why a kernel is needed |")
    a("|---|---|---|")
    kernel_why = {
        "IB25": "a bond sleeve is a priced second leg (SHY), not cash; the engine's cash knob cannot carry price risk",
        "IB26": "same as IB25 for the curve-conditioned IEF leg",
        "IB27": "two legs = two FIFO lot books and one cash account; the engine holds one instrument per account (after-tax stage only: pre-tax/OOS/ETF stages run as a blended synthetic instrument)",
        "IB28": "the routing rule must see both legs' FIFO heads inside the fill decision",
        "IB36": "the fill decision must see the fill session's open and the prior close",
        "IB37": "the fill price itself changes",
        "IB38": "the routed split needs two books and a route cap; arms A and B are accounting-only",
        "IB39": "the kernel's band widths (0.5/3/8 pp) become state-dependent",
        "IB40": "tax-payment funding interacts with sim_one's cash routing inside fe_tax",
    }
    for iid, arms in KERNEL_ARMS.items():
        a(f"| {iid} | {', '.join(arms)} | {kernel_why[iid]} |")
    a("")
    a("Every idea below that needs no kernel option is a change to the target-weight array only; that is "
      "the majority (see the counts above).")
    a("")
    a("## 2. Evaluation contract (unchanged from the record)")
    a("")
    a("| element | value |")
    a("|---|---|")
    a("| control | K (see section 1) |")
    a("| main window | 2000-03-09 -> 2026-09-29, 6,679 sessions, m = 2, base and calibrated financing |")
    a(f"| pre-tax gates | {EVALUATION_CONTRACT['pre_tax_gates']} |")
    a(f"| after-tax gates | {EVALUATION_CONTRACT['after_tax_gates']} |")
    a(f"| OOS windows | {', '.join(EVALUATION_CONTRACT['oos_windows'])} |")
    a(f"| robustness stages | {'; '.join(EVALUATION_CONTRACT['robustness_stages'])} |")
    a("")
    a("## 3. Data inventory")
    a("")
    a("| series | file | coverage | role |")
    a("|---|---|---|---|")
    for row in DATA_INVENTORY:
        a(f"| {row['series']} | `{row['file']}` | {row['coverage']} | {row['role']} |")
    a("")
    a("**New downloads required by the bank (5, all public, none of them market-state data that the "
      "harness cannot get):** "
      + "; ".join(f"{d['series']} for {', '.join(d['needed_by'])} ({d['source']})" for d in NEW_DOWNLOADS) + ".")
    a("")
    a("**Structural coverage facts that decide several ideas.** (a) The pre-2000 OOS stage builds the 2x "
      "leg from the index itself and runs the R-USD rule, so an arm is evaluable out of sample only if "
      "its signal exists in that index's own history: ^VIX (1990+) kills the VRP arm's OOS clause, IEF/"
      "GLD/SMH/HG=F/SHY (2002+) kill the bond, gold, semis, copper and SHY arms' OOS clauses, and the "
      "^GSPC 1957-1999 window is close-only, which kills any high/low statistic there. (b) The after-tax "
      "stage starts in 2000 because the CBRT/YI-UFE calendar does, so no after-tax idea can ever have a "
      "true out-of-sample window; B1/B2, the 21 rolling January starts and the E3 fresh bootstraps are the "
      "substitutes. (c) Every non-evaluable window is paired with a pre-registered reduced gate "
      "(credit_vote_v1's convention), never with a silently dropped check.")
    a("")
    a("## 4. The 40 ideas")
    a("")
    a("| id | cat | rank | idea | plugin | data |")
    a("|---|---|---:|---|---|---|")
    cat_short = {"trend-signal": "trend", "vol-estimator": "vol", "macro-filter": "macro",
                 "calendar": "calendar", "cross-asset": "cross-asset", "off-state": "off-state",
                 "on-state-instrument": "instrument", "drawdown-recovery": "dd-recovery",
                 "statistical-filter": "statistical", "execution": "execution", "tax-structure": "tax"}
    for idea in IDEAS:
        kernels = KERNEL_ARMS.get(idea["id"], [])
        plugin = "targets-from-data" if not kernels else f"kernel ({', '.join(kernels)})"
        data = idea["data"]["main_window"]
        a(f"| {idea['id']} | {cat_short[idea['category']]} | {idea['rank'] or '-'} | "
          f"{idea['title']} | {plugin} | {data} |")
    a("")
    for cat in CATEGORIES:
        a(f"## 5.{CATEGORIES.index(cat) + 1} {cat['label']} ({len(cat['idea_ids'])} ideas)")
        a("")
        for iid in cat["idea_ids"]:
            idea = next(i for i in IDEAS if i["id"] == iid)
            rank = f" -- **rank {idea['rank']}**" if idea["rank"] else ""
            a(f"### {idea['id']} {idea['title']}{rank}")
            a("")
            a(f"**Mechanism.** {idea['mechanism']}")
            a("")
            a(f"**Closest tested study.** `{idea['closest_tested']['study']}` "
              f"({idea['closest_tested']['index_row']}).  *Difference:* {idea['closest_tested']['difference']}")
            a("")
            if idea["literature"]:
                a("**Literature.** " + "; ".join(f"{l['ref']} ({l['url']})" for l in idea["literature"]) + ".")
                a("")
            a(f"**Signal inputs.** {'; '.join(idea['signal_inputs'])}.")
            a("")
            a(f"**Data.** in repo: {'; '.join('`' + x + '`' for x in idea['data']['in_repo']) or 'none'}. "
              f"New downloads: {'; '.join(idea['data']['new_downloads']) or 'none'}. "
              f"Main window: {idea['data']['main_window']}. "
              f"OOS windows: {', '.join(idea['data']['oos']['evaluable']) or 'none'} -- {idea['data']['oos']['note']}")
            a("")
            a(f"**Plugin.** `{idea['plugin']['model']}` -- {idea['plugin']['detail']}.")
            a("")
            a("**Arms (parameters frozen).**")
            a("")
            for arm in idea["arms"]:
                kern = " *(kernel option)*" if arm["id"] in KERNEL_ARMS.get(idea["id"], []) else ""
                a(f"- **{arm['id']}** -- {arm['rule']}{kern}")
            a("")
            a(f"**Honest prior.** {idea['prior']}")
            a("")
    a("## 6. Suggested batching for the automated screen")
    a("")
    a("- **Wave 1 (cheapest, no new data, no kernel):** IB01, IB02, IB04, IB05, IB07, IB08, IB11, IB12, "
      "IB13, IB14, IB16, IB18, IB19, IB22, IB24, IB29, IB30, IB31, IB32, IB33, IB34, IB35, and IB38 arm A "
      "-- 23 ideas whose targets-from-data arms run on the existing loaders with the K rule unchanged.")
    a("- **Wave 2 (one small download or one kernel option):** IB03, IB06, IB10, IB15, IB20, IB21, IB23, "
      "IB25, IB26, IB36, IB37, IB39, IB40, plus IB38 arms B/C and IB27/IB28.")
    a("- **Wave 3 (weakest data situation, run last):** IB17 (no OOS coverage, a hand-maintained calendar).")
    a("- The multiple-testing correction must count the union of all waves: 119 arms is the family size, "
      "not the number of survivors.")
    a("")
    a("## 7. What is deliberately *not* here")
    a("")
    a("The bank avoids every axis the record has already closed. The closed families and the studies that "
      "closed them:")
    a("")
    a("| closed family | closed by |")
    a("|---|---|")
    for row in CLOSED_AXES:
        a(f"| {row['family']} | {row['closed_by']} |")
    a("")
    a("Four ideas sit adjacent to a closed axis and are filed as *axis-closing measurements* with a low "
      "prior, not as expected wins: IB07 (vol estimator), IB35 (output band / conditional targeting), "
      "IB39 arms B/C (state-keyed realization timing) and IB17 (calendar). Their `closest_tested` entries "
      "say so explicitly, and a study that runs them must not treat a pass as a licence to relax the "
      "multiple-testing correction.")
    a("")
    a("## 8. Caveats")
    a("")
    a("1. **No result is claimed.** Every number in this document is a *parameter* (a threshold, a "
      "window, a band width), not an outcome. Nothing here was simulated; the priors are judgements and "
      "are labelled as such.")
    a("2. **Arms are capped at three per idea and every parameter is fixed in this document**, before any "
      "arm runs. Adding a fourth arm, moving a threshold or re-splitting a window after seeing a result "
      "is a data-mining event and must be reported as an amendment.")
    a("3. **The after-tax OOS gap is inherited.** The CBRT/YI-UFE calendar starts in 2000, so no "
      "after-tax arm can be confirmed out of sample; every tax idea (IB38-IB40) is therefore gated on the "
      "measured windows, B1/B2, the 21 rolling starts and the E3 bootstraps only.")
    a("4. **Gate asymmetry.** G5 (a pre-tax bootstrap) rejects tax-only arms by construction and G2/G7 "
      "accept de-risking arms trivially on drawdown, so a de-risking idea (IB29A, IB30A, IB33, IB35) "
      "must be judged on the return/after-tax gates and an exposure-matched variant reported where the "
      "mean weight moves (vol_shape_v1's two-tier convention).")
    a("5. **Legal preconditions are not arms.** IB38's 20/D eligibility and the exemption-band question, "
      "and IB28's routing, need a mali musavir opinion before any *implementation*; the simulation can "
      "measure the arithmetic in the meantime, and this document is not tax advice.")
    a("6. **Data facts are asserted from the stored files** (paths and coverage in section 3); each new "
      "download must be recorded in the running study's DATA_MANIFEST.json with its URL, date range and "
      "SHA-256, per WORKER_PROTOCOL.md.")
    a("")
    a("## 9. Amendments")
    a("")
    a("None. This artefact predates every arm it proposes; no parameter, gate, category or rank was "
      "changed after any computation, because none was performed.")
    a("")
    return "\n".join(out) + "\n"


def main() -> None:
    doc = build_json()
    ids = [i["id"] for i in IDEAS]
    assert len(ids) == len(set(ids)) == 40, f"expected 40 unique idea ids, got {len(ids)}/{len(set(ids))}"
    for idea in IDEAS:
        assert 1 <= len(idea["arms"]) <= 3, f"{idea['id']} has {len(idea['arms'])} arms"
        assert idea["plugin"]["model"] in {"targets_from_data", "kernel_option", "hybrid"}, idea["id"]
        assert idea["closest_tested"]["index_row"], idea["id"]
    assert len(STRONGEST) == 10 and sorted(rank_by(i) for i in IDEAS if i["rank"]) == list(range(1, 11))
    (DOCS / "idea_bank_v1.json").write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    (DOCS / "idea_bank_v1.md").write_text(render_md(doc), encoding="utf-8")
    print(f"ideas={len(IDEAS)} arms={doc['counts']['arms']} strongest={STRONGEST}")


def rank_by(idea: dict) -> int:
    return idea["rank"]


if __name__ == "__main__":
    main()
