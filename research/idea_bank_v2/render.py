"""idea_bank_v2 -- renderer.

Runs the four idea modules through a validation pass and writes
`docs/research/idea_bank_v2.json` (the machine-readable bank, `parayabuck.idea_bank/v1` schema) and
`docs/research/idea_bank_v2.md` (the human-readable bank).  Both files are generated from the same
Python data, so they cannot drift.

    PYTHONPATH=. python -m research.idea_bank_v2.render
"""

from __future__ import annotations

import json
from pathlib import Path

from research.idea_bank_v2 import ideas_a, ideas_b, ideas_c, ideas_d

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "research"

NAME = "idea_bank_v2"

CATEGORIES = [
    ("trend-signal", "Trend-signal algebra (new statistics)"),
    ("vol-estimator", "Volatility and risk estimators"),
    ("macro-regime", "Regime models from macro releases and financial conditions"),
    ("calendar", "Calendar and structural-date effects"),
    ("cross-asset", "Cross-asset relative signals"),
    ("off-state", "Off-state holdings beyond bills / bonds / gold"),
    ("on-state-instrument", "On-state instrument choice"),
    ("two-leg", "Two-instrument allocations (two-leg kernel)"),
    ("drawdown-recovery", "Drawdown / recovery-state sizing"),
    ("statistical-filter", "Model-free statistical filters"),
    ("execution", "Execution, frictions and the real LIVE (Alpaca) account"),
    ("tax-structure", "Tax structure and realisation policy"),
]

#: categories whose ideas are *not* signal tweaks on QQQ (task requirement: >= 20 of 60)
NON_QQQ_CATEGORIES = ["macro-regime", "cross-asset", "off-state", "on-state-instrument", "two-leg", "execution"]

CONTROL = (
    "C0 = production K = R2 (E2 2-of-3 vote + half-weight early re-entry) + A1u gain-scaled downsize "
    "band (0.5 / 3 / 8 pp at a USD FIFO gain fraction <=1% / <=5% / >5%) + H2 (a downsize filling in "
    "the last 10 NYSE December sessions defers to January).  Reference cells to reproduce before any "
    "arm runs: pre-tax 15.7649% base / 15.1785% calibrated, max DD 46.6698% / 46.7948%, after tax at "
    "$100k 10.7533 / 15.2354 / 23.9720%."
)

PRE_TAX_GATES = (
    "ewma_band_v1's G1-G9 against K: G1 return (CAGR >= C0, or Calmar >= 1.1x with CAGR >= C0 - 0.5 pp), "
    "G2 max DD <= C0 + 1 pp, G3 2017+ >= C0 - 1 pp, G4 CAGR above C0 in >= 2 of 3 sub-periods, "
    "G5 block-20 bootstrap (seed 20261011, 5,000 paths) median >= 0, G6 G1 at m = 1, G7 the six OOS "
    "windows, G8 the pooled OOS bootstrap, G9 turnover <= 2x C0; G1/G2/G3/G5/G6 under both financing models."
)

AFTER_TAX_GATES = (
    "beats K at $100k in all three windows (2000-03-09 -> 2026-09-29; 2010-01-01 ->; 2017-01-03 ->) AND "
    "clean bootstrap B1-recent and B2-flat median delta >= 0 with share > 0 >= 55% (seed 20261011, block 20)."
)

OOS_WINDOWS = [
    "GSPC_1957_1999",
    "M1 ^IXIC 1975-1999",
    "M2 ^GSPC 1935-1956",
    "M3 ^N225 1966-2026",
    "M4 ^GDAXI 1989-2026",
    "M5 ^FTSE 1985-2026",
]

ROBUSTNESS_STAGES = [
    "21 quality-passing ETFs pre-tax (calibrated, m = 2, R-USD)",
    "E1: 21 ETFs after tax under R-INDEX at $100k",
    "E2: 21 rolling January starts (2001-2021) after tax",
    "E3: fresh bootstraps (seed 20261015, blocks 10/40)",
    "m = 4 cost stress after tax at $100k",
]

REDUCED_GATE_CONVENTION = (
    "where a window or stage is not evaluable (documented per idea in `data.oos`), the study must "
    "pre-register the reduced gate before running, exactly as credit_vote_v1 did for its non-US windows; "
    "the batch_screen framework's own rule (>= 3 applicable OOS windows and >= 15 applicable instruments) "
    "applies, and an arm below either bound is `holdout_insufficient` and needs its own confirmation study."
)

PLUGIN_CONTRACT = {
    "targets_from_data": (
        "the arm is a new numpy function of the instrument's own OHLC(V) plus any external series "
        "aligned by date, producing the per-session target-weight array the engine already consumes; the "
        "K fill rule (band / USD gain fraction / December deferral) is unchanged.  This is "
        "fe_signals.arm_signals' existing extension point and needs no engine edit."
    ),
    "two_leg": (
        "the arm holds QLD in one book and a second ETF in another, each with its own FIFO queue and one "
        "cash account (the kernel being built in parallel).  The JSON names the second instrument as "
        "`second_symbol=...` inside `plugin.detail`.  Pre-tax/OOS/ETF stages may run as a blended "
        "synthetic instrument (the convention IB27 used), the after-tax stage needs the kernel."
    ),
    "kernel_option": (
        "the arm changes the fill/decision logic itself (a fill-price or fill-filter rule, a "
        "state-dependent leg or a second leg, a tax-funding route, the leg's total-return recurrence, the "
        "decision calendar).  It requires a copy of the engine inside research/<study>/ with the option "
        "added and a parity cell proving the option switched off reproduces the engine bit-for-bit "
        "(WORKER_PROTOCOL.md)."
    ),
    "hybrid": (
        "the arm is expressible by registering a new *combination* of the engine's RULE options through "
        "fe_sim.RULE (the engine's documented extension point, no engine file edited); if the RULE table "
        "cannot express it, it falls back to a kernel option.  Check fe_sim.RULE before writing a kernel."
    ),
}

DATA_INVENTORY = [
    dict(series="QQQ OHLC+adjclose+volume+dividend", file="data/historical/stress_robustness_v1_daily.csv",
         coverage="1999-03-10 -> 2026-09-29", role="main-window instrument and every signal input"),
    dict(series="SPY, QLD (real), VOO", file="data/historical/stress_robustness_v1_daily.csv",
         coverage="SPY 1999-01-04 ->; QLD 2006-06-21 ->; VOO 2010-09-09 ->", role="benchmarks, real-fund checks, the S&P second leg"),
    dict(series="synthetic daily-reset 2x QLD*", file="built by fe_signals.synthetic_2x from QQQ",
         coverage="1999-03-10 -> 2026-09-29", role="the traded leg in every stage"),
    dict(series="DTB3 (financing / bill yield)", file="data/historical/stress_robustness_v1_dtb3.csv",
         coverage="1954 -> 2026-09", role="financing, cash yield, bill-rate states"),
    dict(series="TB3MS (monthly bill rate)", file="data/historical/ewma_band_v1_tb3ms.csv", coverage="1934-01 ->",
         role="pre-1954 OOS financing"),
    dict(series="^GSPC", file="data/historical/ewma_band_v1_gspc.csv (1957-1999 close only) and ewma_band_v1_index.csv (1927-1956 OHLC)",
         coverage="1927-12-30 -> 1999-12-31", role="GSPC_1957_1999 and M2 OOS windows (the loader reads the close column only)"),
    dict(series="^IXIC / ^N225 / ^GDAXI / ^FTSE", file="data/historical/ewma_band_v1_index.csv",
         coverage="^IXIC 1971-02-05->1999-12-31; ^N225 1965->2026-09-29; ^GDAXI 1987-12-30->; ^FTSE 1984-01-03-> (OHLC)",
         role="M1-M5 OOS windows and their local-index signal/leg"),
    dict(series="21 quality-passing ETFs (XLB XLE XLF XLI XLK XLP XLU XLV XLY EWJ EWG EWU EWA EWH EWS EWL EFA EEM SPY DIA IWM)",
         file="data/historical/range_vol_v2_*.csv / range_vol_v1_*.csv (OHLC+adjclose, no volume); volume+dividend in riskoff_rotation_v4_etfs.csv",
         coverage="mostly 1999-01-04 ->; EFA 2001-08-27; EEM 2003-04-14; IWM 2000-05-26", role="breadth/robustness stages"),
    dict(series="IEF / TLT / GLD / GC=F / TQQQ", file="riskoff_rotation_v4_etfs.csv, riskoff_rotation_v2_*.csv, leverage_frontier_v1_daily.csv",
         coverage="IEF/TLT 2002-07-30 ->; GLD 2004-11-18 ->; GC=F 2000-08-30 ->; TQQQ 2010-02-11 ->",
         role="bond/gold sleeves, the risk-parity blend, the instrument arms"),
    dict(series="^VIX / ^VIX3M / ^VXN", file="data/historical/vix_filter_v1_vix.csv, _vix3m.csv, _vxn.csv, _fred.csv",
         coverage="^VIX 1990-01-02 ->; ^VXN 2001-01-23 ->; ^VIX3M 2006-07-17 ->", role="equity-vol regimes (tested)"),
    dict(series="BAA10Y / T10Y3M / DGS10 / BAA / AAA / GS10 / TB3MS", file="data/historical/credit_vote_v1_*.csv, leverage_frontier_v1_dgs10.csv",
         coverage="BAA10Y 1986-01-02 ->; T10Y3M 1982-01-04 ->; DGS10 1962-01-02 ->; BAA/AAA monthly 1919-01 ->; GS10 monthly 1953-04 ->",
         role="credit/curve/rates states (BAA10Y, curve: tested)"),
    dict(series="USD/TRY (CBRT) and YI-UFE", file="data/historical/after_tax_tr_v1_usdtry_cbrt.csv, _yiufe.csv",
         coverage="USD/TRY 2000-01-03 ->; YI-UFE 1999-01 ->", role="the after-tax engine, the TRY-indexed lot scoring, lot ageing"),
    dict(series="CAPE / earnings yield / 10y real return (Shiller)", file="data/historical/valuation_v1_shiller_monthly.csv",
         coverage="1871-01 -> 2024-09 (monthly)", role="already tested (valuation_v1); listed for completeness"),
    dict(series="^NDX daily", file="data/historical/valuation_v1_ndx_daily.csv, vol_shape_v1_ndx.csv",
         coverage="1985-10-01 -> 2000-03-22", role="pre-2000 calibration windows"),
    dict(series="^RUT", file="data/historical/breadth_vote_v1_rut.csv", coverage="1987-09-10 -> 2026-10-14",
         role="breadth (already tested)"),
    dict(series="BIL / SGOV / TLT daily (2016+)", file="data/historical/tbill_sweep_v1_etf_daily_split_sip.csv",
         coverage="2016-01-04 -> 2026-07-31", role="bill-sleeve reference"),
    dict(series="batch_screen extra-data registry (31 series, one CSV each)",
         file="research/batch_screen/EXTRA_DATA.json + data/historical/batch_screen_v1_*.csv",
         coverage=("^VIX 1990-01-02; ^VIX3M 2006-07-17; ^VXN 2001-01-23; DGS10 1962-01-02; T10YIE 2003-01-02; "
                   "DTWEXBGS 2006-01-02; BAA10Y 1986-01-02; CPIAUCSL 1947-01; SMH 2000-06-05; SOXX 2001-07-13; "
                   "HG=F 2000-08-30; SHY/IEF/TLT 2002-07-30; GLD 2004-11-18; the nine sector SPDRs 1998-12-22; "
                   "^STOXX50E 2007-03-30; ^GDAXI 1987-12-30; ^N225 1970-01-05; ^FTSE 1984-01-03; ^IXIC 1971-02-05; "
                   "^GSPC 1970-01-02; ^NDX 1985-10-01; local_index (a per-window map)"),
         role="the registered `data.extra(key)` series every extra-data arm in this bank draws on"),
]

CLOSED_AXES = [
    ("Trend horizon / aggregation (SMA 100-250, Donchian 126, 2-of-3, proportional, single-signal, hysteresis, confirmation days, exit buffers, min-hold, staged thirds)",
     "trend_robustness_v2, signal_ensemble_v1, vote_hysteresis_v1, rule_variants_v1, e2_confirmation_v1"),
    ("Total-return votes and T-bill / 2% hurdles", "tsm_tr_v1"),
    ("Vol estimator / window / output band (EWMA 0.94, vol10/40/60, max(vol20,vol60), semi-deviation, Parkinson/GK/YZ, 2.5/5/10 pp bands)",
     "vol_window_v1, ewma_band_v1, range_vol_v1-v3"),
    ("Vol-target level and leverage grid", "vt_level_tax_v1, leverage_frontier_v1, exposure_k_v1, tax_asym_v1-v3"),
    ("Vol-target shape min(1, c/sigma^p)", "vol_shape_v1"),
    ("Valuation / ERP-conditioned exposure", "valuation_v1"),
    ("Idle/risk-off asset (BIL, BOND10/IEF, GOLD, PSQ/QID, sector and country ETFs, bill sweep as a rule)",
     "tbill_sweep_v1, r2_tbill_combo_v1, leverage_frontier_v1, riskoff_rotation_v1-v4, riskoff_after_tax_v1, gold_riskoff_oos_v1, shortside_v1"),
    ("Macro / credit / VIX filters at the *level* (BAA10Y vs its SMA, curve inversion, VIX level / spike / term structure, VXN sizing)",
     "credit_vote_v1, vix_filter_v1"),
    ("Execution price and timing (MOC, next close, t+2 open, split fills, overnight hold, intraday stops)",
     "moc_execution_v1, exec_delay_v1, overnight_hold_v1, intraday_stop_v1"),
    ("Seasonality and calendar (May-Oct half, September cash, turn-of-month, month-end deferral)",
     "seasonal_overlay_v1, seasonal_placebo_v1, monthly_defer_v1"),
    ("Instrument choice among 2x/3x funds (TQQQ at 2/3, early leg in QQQ/SPY/SSO, QLD vs SSO rotation)",
     "instrument_cost_v1, early_qqq_v1, relstrength_v1"),
    ("Realization timing (year-end loss harvest, December deferral, gain-scaled band, monthly deferral, inverse hedge, state-keyed deferral, exit-leg timing, smoothing)",
     "tax_harvest_v1/v2, gain_band_v1-v3, monthly_defer_v1, tax_hedge_v1, state_defer_v1, exit_timing_v2, smooth_target_v1"),
    ("Annual tax budget / deliberate gain realization", "tax_budget_v2"),
    ("Early-leg size and trigger (ladder, 2-of-3 quorum, crash path)", "ladder_v1, early_quorum_v1, crash_entry_v1, fast_reentry_v2"),
    ("Dip buying / stops / crash entry / short side", "dip_overlay_v1, intraday_stop_v1, crash_entry_v1, shortside_v1"),
    ("Lot-level selection (FIFO blocked by the broker) and two-queue wrappers", "idea_survey_v2 I1 (never run, blocked); relstrength_v1 for the wrapper"),
    ("ML / sequence models", "timesfm_v1"),
]

PURPOSE_TMPL = (
    "60 pre-registered, parameter-frozen ideas (IC01-IC60, {arms} arms) for improving production K after "
    "Turkish income tax, for the automated two-stage screen with a held-out confirmation and a "
    "multiple-testing correction being built as research/batch_screen.  This is an idea bank: no "
    "backtest was run, no gate was evaluated, no arm was simulated, no data was downloaded."
)


def load_ideas():
    ideas = list(ideas_a.IDEAS) + list(ideas_b.IDEAS) + list(ideas_c.IDEAS) + list(ideas_d.IDEAS)
    validate(ideas)
    return ideas


def validate(ideas):
    ids = [i["id"] for i in ideas]
    assert len(ideas) == 60, f"expected 60 ideas, found {len(ideas)}"
    assert ids == [f"IC{n:02d}" for n in range(1, 61)], f"ids must be IC01..IC60 in order: {ids}"
    assert len(set(ids)) == 60
    cat_ids = [c[0] for c in CATEGORIES]
    for idea in ideas:
        assert idea["category"] in cat_ids, idea["category"]
        assert idea["strength_rank"] is None or 1 <= idea["strength_rank"] <= 15, idea["id"]
        assert len(idea["arms"]) <= 3 and idea["arms"], idea["id"]
        for arm in idea["arms"]:
            assert set(arm) == {"id", "rule", "targets_from_data", "needs_kernel_option", "kernel_scope", "params_frozen"}, arm
            assert arm["params_frozen"] is True
            if arm["needs_kernel_option"]:
                assert arm["kernel_scope"] in ("all_stages", "after_tax_stage_only")
            else:
                assert arm["kernel_scope"] is None
        assert set(idea["plugin"]) == {"model", "detail"}, idea["id"]
        assert idea["plugin"]["model"] in ("targets_from_data", "two_leg", "kernel_option", "hybrid"), idea["id"]
        assert set(idea["closest_tested"]) == {"study", "index_row", "difference"}, idea["id"]
        assert set(idea["data"]) == {"in_repo", "new_downloads", "main_window", "oos"}, idea["id"]
        assert len(idea["data"]["oos"]["evaluable"]) >= 3, idea["id"]
    ranks = sorted(i["strength_rank"] for i in ideas if i["strength_rank"] is not None)
    assert ranks == list(range(1, 16)), f"exactly 15 ideas must carry ranks 1..15: {ranks}"
    assert len({i["closest_tested"]["study"] for i in ideas}) == 60, "closest_tested.study must be distinct"


def build():
    ideas = load_ideas()
    counts = dict(
        ideas=len(ideas),
        arms=sum(len(i["arms"]) for i in ideas),
        categories=len(CATEGORIES),
        strongest_marked=15,
        ideas_outside_qqq_signals=sum(1 for i in ideas if i["category"] in NON_QQQ_CATEGORIES),
        arms_targets_from_data=sum(1 for i in ideas for a in i["arms"] if a["targets_from_data"]),
        arms_needing_kernel=sum(1 for i in ideas for a in i["arms"] if a["needs_kernel_option"]),
        arms_needing_kernel_after_tax_only=sum(1 for i in ideas for a in i["arms"] if a["kernel_scope"] == "after_tax_stage_only"),
        two_leg_ideas=sum(1 for i in ideas if i["plugin"]["model"] == "two_leg"),
        kernel_option_ideas=sum(1 for i in ideas if i["plugin"]["model"] == "kernel_option"),
        hybrid_ideas=sum(1 for i in ideas if i["plugin"]["model"] == "hybrid"),
        no_new_download_ideas=sum(1 for i in ideas if not i["data"]["new_downloads"]),
        oos_all_six=sum(1 for i in ideas if len(i["data"]["oos"]["evaluable"]) == 6),
        oos_five=sum(1 for i in ideas if len(i["data"]["oos"]["evaluable"]) == 5),
        oos_three=sum(1 for i in ideas if len(i["data"]["oos"]["evaluable"]) == 3),
    )

    # aggregate the new downloads across ideas
    agg = {}
    for idea in ideas:
        for d in idea["data"]["new_downloads"]:
            key = d["series"]
            if key not in agg:
                agg[key] = dict(series=key, source=d["source"], needed_by=[])
            agg[key]["needed_by"] = sorted(set(agg[key]["needed_by"]) | set(d["needed_by"]))
    new_downloads = [agg[k] for k in sorted(agg)]

    strong = sorted([i for i in ideas if i["strength_rank"]], key=lambda i: i["strength_rank"])
    doc = dict(
        schema="parayabuck.idea_bank/v1",
        study=NAME,
        date="2026-10-16",
        branch="research/idea_bank_v2",
        artefact_type="idea bank (no backtest, no simulation, no gate run)",
        purpose=PURPOSE_TMPL.format(arms=counts["arms"]),
        counts=counts,
        evaluation_contract=dict(
            control=CONTROL,
            pre_tax_gates=PRE_TAX_GATES,
            after_tax_gates=AFTER_TAX_GATES,
            oos_windows=OOS_WINDOWS,
            robustness_stages=ROBUSTNESS_STAGES,
            reduced_gate_convention=REDUCED_GATE_CONVENTION,
        ),
        plugin_contract=PLUGIN_CONTRACT,
        data_inventory=DATA_INVENTORY,
        new_downloads=new_downloads,
        closed_axes=[dict(family=f, closed_by=c) for f, c in CLOSED_AXES],
        categories=[dict(id=c, label=l, idea_ids=[i["id"] for i in ideas if i["category"] == c]) for c, l in CATEGORIES],
        strongest_ten=[i["id"] for i in strong[:10]],
        strongest_fifteen=[i["id"] for i in strong],
        ideas=[dict(i, n_arms=len(i["arms"])) for i in ideas],
    )
    return doc, ideas


# --------------------------------------------------------------------------- markdown

def _arms_md(idea):
    out = []
    for a in idea["arms"]:
        tag = ""
        if a["needs_kernel_option"]:
            tag = f" *(kernel: {a['kernel_scope']})*"
        elif not a["targets_from_data"]:
            tag = " *(rule-table change)*"
        out.append(f"- **{a['id']}** -- {a['rule']}{tag}")
    return "\n".join(out)


def _idea_md(idea):
    rank = f" -- **rank {idea['strength_rank']}**" if idea["strength_rank"] else ""
    lit = "; ".join(f"[{l['ref']}]({l['url']})" for l in idea["literature"]) or "none"
    d = idea["data"]
    oos = ", ".join(d["oos"]["evaluable"]) if len(d["oos"]["evaluable"]) < 6 else "all six"
    lines = [
        f"### {idea['id']} {idea['title']}{rank}",
        "",
        f"**Mechanism.** {idea['mechanism']}",
        "",
        f"**Literature.** {lit}",
        "",
        f"**Closest tested study.** {idea['closest_tested']['study']} (`{idea['closest_tested']['index_row']}`). "
        f"*Difference:* {idea['closest_tested']['difference']}",
        "",
        f"**Signal inputs.** {'; '.join(idea['signal_inputs'])}",
        "",
        f"**Data.** in repo: {'; '.join(d['in_repo']) or 'none'}. "
        f"New downloads: {'; '.join(x['series'] for x in d['new_downloads']) or 'none'}. "
        f"Main window: {d['main_window']}. OOS evaluable: {oos} -- {d['oos']['note']}",
        "",
        f"**Plugin.** `{idea['plugin']['model']}` -- {idea['plugin']['detail']}",
        "",
        "**Arms (parameters frozen).**",
        _arms_md(idea),
        "",
        f"**Honest prior.** {idea['expected_evidence']}",
        "",
    ]
    return "\n".join(lines)


def render_md(doc, ideas):
    by_cat = {c: [i for i in ideas if i["category"] == c] for c, _ in CATEGORIES}
    label = dict(CATEGORIES)
    strong = sorted([i for i in ideas if i["strength_rank"]], key=lambda i: i["strength_rank"])
    c = doc["counts"]

    out = []
    out.append("# Idea bank v2 (2026-10-16): 60 new pre-registered ideas for improving K after Turkish tax")
    out.append("")
    out.append("> Artefact: an *idea bank*, not a study.  No backtest was run, no gate was evaluated, no arm was "
               "simulated, no data was downloaded.  `docs/research/idea_bank_v2.md` and "
               "`docs/research/idea_bank_v2.json` are rendered from `research/idea_bank_v2/ideas_{a,b,c,d}.py` "
               "by `research/idea_bank_v2/render.py`, so the two files cannot drift.")
    out.append("")
    out.append("**Verdict (what this is).** 60 ideas, "
               f"{c['arms']} arms, every parameter fixed now and never tuned later, in "
               f"{c['categories']} categories and spread deliberately beyond the axes the record has closed and "
               f"beyond the 40 ideas of `idea_bank_v1` (IB01-IB40, screened in `batch_bank_v1`: 0 of 89 arms "
               f"survived the held-out stage). "
               f"{c['ideas_outside_qqq_signals']} of the 60 are outside \"signal tweaks on QQQ\" - macro-release "
               f"regimes, cross-asset signals, off-state assets other than bills/bonds/gold, on-state instrument "
               f"choice, two-instrument allocations and the costs/frictions of the real LIVE account. "
               f"Every idea names the closest *tested* study (with its INDEX row) and the difference that makes "
               f"it new; the 15 strongest are ranked 1-15. "
               f"{c['no_new_download_ideas']} ideas need no new data at all; {c['arms_needing_kernel']} of the "
               f"{c['arms']} arms need a new execution kernel "
               f"({c['arms_needing_kernel_after_tax_only']} of them only in the after-tax stage).")
    out.append("")
    out.append("**Counts.** " + " | ".join([
        f"ideas {c['ideas']}",
        f"arms {c['arms']}",
        f"categories {c['categories']}",
        f"strongest marked {c['strongest_marked']}",
        f"two-leg ideas {c['two_leg_ideas']}",
        f"kernel-option ideas {c['kernel_option_ideas']}",
        f"hybrid ideas {c['hybrid_ideas']}",
        f"OOS coverage 6/6 {c['oos_all_six']}, 5/6 {c['oos_five']}, 3/6 {c['oos_three']}",
    ]))
    out.append("")
    out.append("## The 15 strongest, in rank order")
    out.append("")
    out.append("| rank | id | idea | category | why it is ranked here |")
    out.append("|---:|---|---|---|---|")
    for i in strong:
        why = i["expected_evidence"].split(":", 1)[1].strip() if ":" in i["expected_evidence"] else i["expected_evidence"]
        out.append(f"| {i['strength_rank']} | {i['id']} | {i['title']} | {label[i['category']]} | {why} |")
    out.append("")
    out.append("## 1. How the bank is meant to be consumed")
    out.append("")
    out.append("- **Control.** " + CONTROL)
    out.append("- **Stage 1 (pre-tax).** " + PRE_TAX_GATES)
    out.append("- **Stage 2 (after tax).** " + AFTER_TAX_GATES)
    out.append("- **Held-out confirmation.** the pre-2000 OOS windows and the fresh-seed bootstraps are the "
               "confirmation set; the multiple-testing correction counts *all* arms run across the bank, not only "
               "the survivors of stage 1 (the batch_screen ledger already does this: N is the number of registered "
               "arms, not the number of survivors).")
    out.append("- **Reduced gates.** " + REDUCED_GATE_CONVENTION)
    out.append("- **Ordering.** the two-stage screen is cheap on stage 1 and expensive on stage 2, so the arms are "
               "written so that a stage-1 failure is informative: every arm states an *economic* mechanism, so a "
               "negative stage-1 result is evidence about the mechanism and not only about the parameter.")
    out.append("")
    out.append("### 1.1 The plugin contract (targets from data / two-leg / a new kernel)")
    out.append("")
    for k in ("targets_from_data", "two_leg", "kernel_option", "hybrid"):
        out.append(f"- **{k}.** {PLUGIN_CONTRACT[k]}")
    out.append("")
    out.append("Arms tagged `needs_kernel_option` in the JSON (the `kernel_scope` key says whether the cost is paid "
               "in every stage or only in the after-tax stage):")
    out.append("")
    out.append("| idea | arms | kernel scope | why a kernel is needed |")
    out.append("|---|---|---|---|")
    for i in ideas:
        kk = [a["id"] for a in i["arms"] if a["needs_kernel_option"]]
        if not kk:
            continue
        scopes = sorted({a["kernel_scope"] for a in i["arms"] if a["needs_kernel_option"]})
        out.append(f"| {i['id']} | {', '.join(kk)} | {'/'.join(scopes)} | {i['plugin']['detail']} |")
    out.append("")
    out.append("## 2. Evaluation contract (unchanged from the record)")
    out.append("")
    out.append("| element | value |")
    out.append("|---|---|")
    out.append("| control | K (see section 1) |")
    out.append("| main window | 2000-03-09 -> 2026-09-29, 6,679 sessions, m = 2, base and calibrated financing |")
    out.append(f"| pre-tax gates | {PRE_TAX_GATES} |")
    out.append(f"| after-tax gates | {AFTER_TAX_GATES} |")
    out.append(f"| OOS windows | {', '.join(OOS_WINDOWS)} |")
    out.append(f"| robustness stages | {'; '.join(ROBUSTNESS_STAGES)} |")
    out.append("")
    out.append("## 3. Data inventory")
    out.append("")
    out.append("| series | file | coverage | role |")
    out.append("|---|---|---|---|")
    for d in DATA_INVENTORY:
        out.append(f"| {d['series']} | `{d['file']}` | {d['coverage']} | {d['role']} |")
    out.append("")
    out.append(f"### 3.1 New downloads required by the bank ({len(doc['new_downloads'])} series, all public)")
    out.append("")
    out.append("| series | needed by | source |")
    out.append("|---|---|---|")
    for d in doc["new_downloads"]:
        out.append(f"| {d['series']} | {', '.join(d['needed_by'])} | {d['source']} |")
    out.append("")
    out.append("### 3.2 Structural coverage facts that decide several ideas")
    out.append("")
    out.append("- The pre-2000 OOS stage builds the 2x leg from the index itself and runs the R-USD rule, so an arm "
               "is evaluable out of sample only if its *signal* exists in that index's own history; extra series "
               "(sector SPDRs 1998, ^VIX 1990, IEF/GLD/SHY 2002, HYG 2007, DTWEXBGS 2006, EEM 2003, LQD 2002, TIP "
               "2003, GDX 2006, T10YIE 2003) are absent on GSPC_1957_1999, M1 and M2 and only live on the modern "
               "spans of M3/M4/M5 (which run to 2026) - hence the many 3-of-6 arms and the pre-registered reduced "
               "3-window G7 (the `credit_vote_v1` precedent).")
    out.append("- ^VIX (1990+) and the long FRED series (DGS10 1962, BAA10Y 1986, CPIAUCSL 1947, DFF 1954, "
               "BAMLH0A0HYM2 1996, NFCI 1971, ICSA 1967, CFNAI 1967, DCOILWTICO 1986, BAA/AAA 1919) keep 5 or 6 "
               "OOS windows; BAA-AAA is the only credit state old enough for the 1935-56 window.")
    out.append("- The harness loads the ^GSPC 1957-1999 window close-only (`fe_data.load_gspc` sets opens := the "
               "previous close), so every arm needing intraday structure (overnight/intraday decomposition, true "
               "range, intraday fills) loses that window - the raw file does carry OHLCV 1950-1999, only the "
               "loader is close-only.")
    out.append("- The after-tax stage starts in 2000 because the CBRT/YI-UFE calendar does, so no after-tax idea "
               "can ever be evaluated before 2000; and the tax is assessed per calendar year with FIFO and no loss "
               "carry-forward, which is what makes the two-leg routing and loss-netting ideas (IC42-IC44) "
               "interesting rather than cosmetic.")
    out.append("- The panel offers the window's own underlying at 2x and 1x only (`api.LEGS`), and no volume: an "
               "arm needing a second leg, a state-dependent leg or volume is a kernel/data-plumbing item, which is "
               "why IC31-IC40 and the anchored-VWAP family are tagged as they are.")
    out.append("")
    out.append("## 4. The 60 ideas at a glance")
    out.append("")
    out.append("| id | cat | rank | idea | plugin | data |")
    out.append("|---|---|---:|---|---|---|")
    for i in ideas:
        rank = i["strength_rank"] or "-"
        nd = len(i["data"]["new_downloads"])
        data = "no new data" if nd == 0 else f"{nd} new series"
        out.append(f"| {i['id']} | {i['category']} | {rank} | {i['title']} | {i['plugin']['model']} | {data} |")
    out.append("")
    out.append("## 5. Non-repetition proof: the closest INDEX study named by every idea")
    out.append("")
    out.append("| id | closest tested study (INDEX row) |")
    out.append("|---|---|")
    for i in ideas:
        out.append(f"| {i['id']} | {i['closest_tested']['study']} (`{i['closest_tested']['index_row']}`) |")
    out.append("")
    out.append("## 6. Closed axes of the record (a bank arm must state its difference from these)")
    out.append("")
    out.append("| closed family | closed by |")
    out.append("|---|---|")
    for f, c2 in CLOSED_AXES:
        out.append(f"| {f} | {c2} |")
    out.append("")
    out.append("## 7. The 60 ideas in detail")
    out.append("")
    for n, (cid, clabel) in enumerate(CATEGORIES, start=1):
        group = by_cat[cid]
        out.append(f"## 7.{n} {clabel} ({len(group)} ideas)")
        out.append("")
        for i in group:
            out.append(_idea_md(i))
    md = "\n".join(out).rstrip() + "\n"
    return md


def main():
    doc, ideas = build()
    DOCS.mkdir(parents=True, exist_ok=True)
    json_path = DOCS / f"{NAME}.json"
    md_path = DOCS / f"{NAME}.md"
    json_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_md(doc, ideas), encoding="utf-8")
    print(f"{json_path}: {json_path.stat().st_size} bytes, {len(doc['ideas'])} ideas, "
          f"{doc['counts']['arms']} arms, {len(doc['new_downloads'])} new series")
    print(f"{md_path}: {md_path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
