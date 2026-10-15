#!/usr/bin/env python3
"""tax_budget_v1 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Arms (control = production K = A1u + H2, `equity_leveraged_trend_v1` 1.2.0):

  K   production K (control).
  Y1  loss-pool fill: at the last session of each calendar year, if the account's YTD realised
      net TRY gain is negative, sell-and-rebuy the smallest quantity that brings the year to 0.
  Y2  bracket-headroom fill: same session, fill the year's realised gain up to the deflated top
      bracket ceiling (5,300,000 TRY x scale) when YTD < 25 % of it, capped at 25 % of NAV.
  Y3  no-new-trade year reallocation: H2's calendar window is replaced by a state window -- a
      pending non-exit downsize in the last 10 December / first 10 January sessions fills iff
      the session's own tax year is the cheaper of the two adjacent years.
  Y4  Y1 + Y3 (report-only upper bound).

The engine is `research/band_h2_combo_v1/screen.py`'s `engine_run`, copied here verbatim except
for the two year-end hooks (Y1/Y2's round trip and Y3's state window); on the K code path it is
bit-identical to the original (asserted in --smoke and --harness). Everything else -- signals,
inputs, statistics, loaders -- and the tax engine (`tax_engine.py`) are imported from that module,
so the control, the reference row and the arms share one code path.
"""

from __future__ import annotations

import bisect
import importlib.util
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def _mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


bh = _mod("bh_screen", ROOT / "research/band_h2_combo_v1/screen.py")
trv2, te = bh.trv2, bh.te

OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"
REF_K = ROOT / "research/band_h2_combo_v1/RESULTS.json"   # evidence: K's published cells

TOL = 1e-4               # 0.01 pp (the brief's reproduction tolerance; applied to every cell)
SEED, BLOCK = bh.SEED, bh.BLOCK
M, M_STRESS = bh.M, bh.M_STRESS
GATE_CAPITAL, PRE_CAPITAL = bh.GATE_CAPITAL, bh.PRE_CAPITAL
TAX_CAPITALS, TAX_WINDOWS, FIN, SUBPERIODS = bh.TAX_CAPITALS, bh.TAX_WINDOWS, bh.FIN, bh.SUBPERIODS

# ----------------------------------------------------------------------------- arms
ARMS = ("K", "Y1", "Y2", "Y3", "Y4")
CONTROL = "K"
GATED = ("Y1", "Y2", "Y3")          # Y4 is report-only (PREREGISTRATION section 7)
REPORT_ONLY = ("Y4",)
REFERENCE = "R2"                    # the pre-K incumbent, reference row only (= bh's arm "C0")
STAGE_ARMS = ARMS + (REFERENCE,)
RULE = {
    "K": {"band": True, "usd_rule": True, "defer": True, "yearend": None, "realloc": False},
    "Y1": {"band": True, "usd_rule": True, "defer": True, "yearend": "pool", "realloc": False},
    "Y2": {"band": True, "usd_rule": True, "defer": True, "yearend": "headroom", "realloc": False},
    "Y3": {"band": True, "usd_rule": True, "defer": False, "yearend": None, "realloc": True},
    "Y4": {"band": True, "usd_rule": True, "defer": False, "yearend": "pool", "realloc": True},
    REFERENCE: dict(bh.RULE["C0"]),  # band off, no deferral, no year-end hook
}
TARIFF_TOP = te.TARIFF_2026[-2][0]        # 5,300,000 TRY, the top bracket's ceiling (2026)
NAV_CAP_FRAC = 0.25                       # Y1/Y2's cap on the round-trip notional
HEADROOM_TRIGGER_FRAC = 0.25              # Y2 fires below this fraction of the ceiling
JAN_WINDOW, DEC_WINDOW = "01", bh.YEAR_END

# ---- gates (PREREGISTRATION section 7)
G1_MIN_CAGR_PP = 0.0          # or Calmar >= 1.1 x K with CAGR >= K - this ...
G1_CALMAR_CAGR_PP = -0.5
G2_MAX_DD_PP = 1.0
G3_MIN_2017_PP = -1.0
G4_MIN_SUBPERIODS = 2
G5_MIN_MEDIAN_PP = -0.10      # reduced to non-inferiority for these tax-timing arms
G6_MIN_CAGR_PP = 0.0
G7_MIN_MEDIAN_PP = 0.0
G7_MAX_WINDOWS_BAD = 1        # >= 5 of 6 with dCAGR >= -0.5 pp and dDD <= +3 pp
G7_MIN_WINDOW_CAGR_PP = -0.5
G7_MAX_WINDOW_DD_PP = 3.0
G7_MAX_DD_HARD_PP = 8.0
G8_MIN_MEDIAN_PP = 0.0
G9_MAX_TURNOVER_MULT = 2.0
A1_MIN_DELTA_PP = 0.0         # strictly greater than K
A2_MIN_MEDIAN_PP = 0.0
A2_MIN_SHARE = 0.55
CALMAR_MULT = 1.1

# ---- literal anchors from the brief (2000-03-09 -> 2026-09-29, m = 2)
ANCHOR_K = {"pre_base": 0.157649, "pre_cal": 0.151785, "dd_base": 0.466698, "dd_cal": 0.467948,
            "turnover": 5.456136,
            "at": {"2000_03_to_2026_09": 0.107533, "2010_01_to_end": 0.152354,
                   "2017_01_to_end": 0.239720}}
ANCHOR_R2_AT = {"2000_03_to_2026_09": 0.101600, "2010_01_to_end": 0.147059,
                "2017_01_to_end": 0.228861}


# ----------------------------------------------------------------------------- Y1/Y2 machinery
def jan_head(days, i0, i1, tail):
    """Session indices in the first `tail` January sessions of each calendar year in [i0, i1]
    (mirror of `band_h2_combo_v1.december_tail`)."""
    if tail <= 0:
        return set()
    by_year = defaultdict(list)
    for i in range(i0, i1 + 1):
        if days[i][5:7] == JAN_WINDOW:
            by_year[days[i][:4]].append(i)
    out = set()
    for idxs in by_year.values():
        out.update(idxs[:tail])
    return out


def lot_gain_per_unit(mkt, i, price, cost, lot, index):
    """Per-unit realised TRY gain `BaseAccount.sell` books for this FIFO lot at (i, price).

    `cost` is the per-notional fee rate; `price` is the fill price. Indexation is applied exactly
    as the engine applies it (`rg.kind != "none"`, unindexed g > 0, rise >= INDEX_MIN_RISE,
    floored at 0), so the read-only probe equals the engine's own arithmetic (`--smoke`)."""
    per_unit = price * (1.0 - cost)
    unidx = per_unit * mkt.fx[i] - lot[1] * lot[2]
    if index and unidx > 0.0:
        rise = (mkt.ufe[te.prev_month(mkt.days[i][:7])] / mkt.ufe[te.prev_month(lot[3])] - 1.0)
        if rise >= te.INDEX_MIN_RISE:
            return max(0.0, per_unit * mkt.fx[i] - lot[1] * lot[2] * (1.0 + rise))
    return unidx


def gain_matched_units(mkt, lots, i, price, cost, need, cap_units, index):
    """Smallest quantity whose FIFO-prefix realised (indexed) TRY gain reaches `need`, capped at
    `cap_units` and at the book. The gain is linear in the quantity inside each lot, so the
    crossing is solved in closed form; if it is never reached the whole capped book is returned."""
    q, cum, left = 0.0, 0.0, cap_units
    for lot in lots:
        if left <= 1e-15:
            break
        take = min(left, lot[0])
        per = lot_gain_per_unit(mkt, i, price, cost, lot, index)
        g = take * per
        if cum + g >= need:
            if per > 0.0:
                return q + (need - cum) / per
            return q + take
        cum += g
        q += take
        left -= take
    return q


def year_end_fill(qa, i, pc, nav_c, cost, kind, mkt, index):
    """Y1/Y2's year-end sell-and-rebuy at the decision close. Returns the event record or None.

    kind == "pool": fire iff the year's YTD realised net TRY gain is negative, for the smallest
    quantity that brings it to 0. kind == "headroom": fire iff YTD < 25 % of the deflated top
    bracket ceiling, for the quantity that fills the year up to that ceiling. Both are capped at
    25 % of NAV (`nav_c`, the NAV at that close after the session's ordinary trade)."""
    y = int(mkt.days[i][:4])
    ytd = qa.year_gain.get(y, 0.0)
    ceiling = TARIFF_TOP * mkt.scale(y)
    if kind == "pool":
        if ytd >= 0.0:
            return None
        need, trigger = -ytd, "pool"
    else:
        if ytd >= HEADROOM_TRIGGER_FRAC * ceiling:
            return None
        need, trigger = ceiling - ytd, "headroom"
    cap_units = NAV_CAP_FRAC * nav_c / pc
    want_q = gain_matched_units(mkt, qa.lots, i, pc, cost, need, math.inf, index)
    q = min(want_q, cap_units, qa.units)
    if q <= 1e-12:
        return None
    fee = q * pc * cost
    qa.sell(i, pc, q, fee, "yearend")
    qa.buy(i, pc, q * pc, fee)
    booked = qa.year_gain.get(y, 0.0) - ytd
    return {"session": mkt.days[i], "kind": trigger, "ytd_try_before": float(ytd),
            "ytd_try_after": float(qa.year_gain.get(y, 0.0)), "booked_gain_try": float(booked),
            "ceiling_try": float(ceiling), "units": float(q), "notional_usd": float(q * pc),
            "nav_usd": float(nav_c), "frac_nav": float(q * pc / nav_c),
            "need_try": float(need), "wanted_units": float(want_q),
            "cap_binding": bool(q < want_q - 1e-9)}


def current_year_cheaper(qa, days, i):
    """Y3's state test: is the session's own tax year the cheaper of the two adjacent years?

    December: the pair is (this year, next year); the next year's YTD is 0 by definition.
    January: the pair is (the finished previous year, this year). Ties favour this year."""
    y = int(days[i][:4])
    cur = qa.year_gain.get(y, 0.0)
    other = 0.0 if days[i][5:7] == DEC_WINDOW else qa.year_gain.get(y - 1, 0.0)
    return cur <= other


# ----------------------------------------------------------------------------- the engine
def engine_run(capital, i0, i1, days, mkt, oq, cq, wq, e2on, arm, mode, m=M, rule_index=True,
               trace_year=False):
    """One arm through the Turkish after-tax account, session by session.

    `band_h2_combo_v1.screen.engine_run`, copied verbatim except for (a) the arm spec's two
    year-end hooks and (b) the optional year-end trace. mode in {"pre_tax", "progressive",
    "flat"}; every arm shares the target series `wq` (production R2's) and the fill rules of K;
    only the year-end behaviour (Y1/Y2) and the year choice of a pending downsize (Y3) differ.
    With arm "K" and trace_year=False this is exactly the parent module's function."""
    days = list(days)
    if not isinstance(i0, int):
        i0, i1 = bisect.bisect_left(days, i0), bisect.bisect_right(days, i1) - 1
    oq, cq, wq = (np.asarray(x, dtype=float).flatten() for x in (oq, cq, wq))
    e2on = np.asarray(e2on, dtype=bool).flatten()
    cost = bh.COST_PER_SIDE * m
    spec = RULE[arm]
    yearend = spec.get("yearend")
    index = mode == "progressive"        # the account's own indexation state (rg.kind != "none")
    cashbox = {"v": float(capital)}
    if mode == "flat":
        qa = te.FlatAccount(mkt, 0.0, cashbox)
        pays = None
    else:
        rg = te.Regime("progressive", "tariff") if mode == "progressive" else te.Regime("pre_tax", "none")
        qa = te.BaseAccount(mkt, rg, 0.0, cashbox)
        pays = te.instalments(days, i0, i1)
    cashbox["v"] = float(capital)

    nav = np.empty(i1 - i0 + 2)
    nav[0] = capital
    trades = q_sells = tax_sales = postponed = 0
    turnover = 0.0
    turnover_strategy = 0.0
    rule_year = defaultdict(float)
    post_by_year = defaultdict(int)
    blocked_band = deferred = realloc_holds = 0
    defer_by_year = defaultdict(int)
    dec_tail = bh.december_tail(days, i0, i1, bh.DEC_TAIL) if spec.get("defer") else set()
    realloc_win = (dec_tail | jan_head(days, i0, i1, bh.DEC_TAIL)) if spec.get("realloc") else set()
    prev_tgt = None
    yearend_events = []
    realloc_events = []
    year_trace = [] if trace_year else None
    pos_prev = neg_prev = 0.0

    for i in range(i0, i1 + 1):
        year_i = int(days[i][:4])
        # ---- year boundary: assess the previous year (verbatim)
        if i > i0 and year_i != int(days[i - 1][:4]):
            prevy = year_i - 1
            qa.tax_due[prevy] = qa.assess(prevy)
            if mode == "flat":  # B2 pays immediately, no instalments
                due = qa.tax_due[prevy]
                if due > 1e-9:
                    usd = due / mkt.fx[i]
                    if cashbox["v"] < usd:
                        need = usd - cashbox["v"]
                        p = float(cq[i - 1])
                        if qa.units > 1e-12:
                            g, _ = bh.rule_sale(mkt, i - 1, p, need / p, need * cost, qa.lots, rule_index)
                            rule_year[year_i - 1] += g
                            qa.sell(i - 1, p, need / p, need * cost, "tax")
                            tax_sales += 1
                    cashbox["v"] -= usd
                    qa.tax_paid_try[prevy] = qa.tax_paid_try.get(prevy, 0.0) + due
                    qa.tax_paid_usd += usd

        # ---- tax instalments (funded by selling)
        if pays is not None:
            for ty, part in pays.get(i, ()):
                if ty not in qa.tax_due:
                    qa.tax_due[ty] = 0.0
                due = qa.tax_due.get(ty, 0.0)
                amt = due / 2 if part == 0 else due - qa.tax_paid_try.get(ty, 0.0)
                if amt <= 1e-9:
                    continue
                usd = amt / mkt.fx[i]
                if cashbox["v"] < usd:
                    need = (usd - cashbox["v"]) / (1 - cost)
                    if qa.units > 1e-12:
                        g, _ = bh.rule_sale(mkt, i, float(oq[i]), need / float(oq[i]), need * cost,
                                            qa.lots, rule_index)
                        rule_year[year_i] += g
                        qa.sell(i, float(oq[i]), need / float(oq[i]), need * cost, "tax")
                        tax_sales += 1
                cashbox["v"] -= usd
                qa.tax_paid_try[ty] += amt
                qa.tax_paid_usd += usd

        po, pc = float(oq[i]), float(cq[i])
        held = qa.units * po
        nav_open = cashbox["v"] + held
        if nav_open <= 0:
            nav_open = cashbox["v"]
        w = held / nav_open if nav_open > 0 else 0.0

        tgt = float(wq[i - 1]) if i > 0 else float(wq[0])
        tgt = 0.0 if tgt != tgt else tgt

        trade = abs(tgt - w) > bh.C0_BAND
        if qa.units == 0 and tgt > 1e-12 and prev_tgt is None:
            trade = True
        if qa.units > 1e-12 and tgt <= 0 and prev_tgt is not None and prev_tgt > 0:
            trade = True

        drift = abs(tgt - w) if trade else 0.0
        fee_total = nav_open * cost * drift
        nav_after = nav_open - fee_total
        target_units = tgt * nav_after / po if po > 0 else 0.0
        du = target_units - qa.units

        # ---- the arm: the state/calendar window first, then A1u's gain-scaled band, on a
        #      discretionary downward resize (exits to zero and up-resizes always fill)
        blocked = None
        if trade and tgt > 1e-12:
            if spec.get("realloc") and i in realloc_win and tgt < w:
                # Y3: the year is chosen by state, not by the calendar (replaces H2)
                if not current_year_cheaper(qa, days, i):
                    trade, blocked = False, "realloc"
                    y_i = int(days[i][:4])
                    realloc_events.append({
                        "session": days[i], "drift": float(drift),
                        "ytd_cur": float(qa.year_gain.get(y_i, 0.0)),
                        "ytd_other": float(0.0 if days[i][5:7] == DEC_WINDOW
                                           else qa.year_gain.get(y_i - 1, 0.0))})
            elif spec.get("defer") and i in dec_tail and tgt < w:
                trade, blocked = False, "deferred"
            elif du < -1e-12 and spec.get("band"):
                if spec["usd_rule"]:
                    gain, proceeds = bh.rule_sale_usd(i, po, -du, fee_total, qa.lots)
                    g_frac = max(0.0, gain) / proceeds if proceeds > 0 else 0.0
                else:
                    gain, proceeds = bh.rule_sale(mkt, i, po, -du, fee_total, qa.lots, rule_index)
                    g_frac = gain / proceeds if proceeds > 0 else 0.0
                if drift <= bh.gain_band(g_frac):
                    trade, blocked = False, "band"

        if trade:
            turnover += drift
            turnover_strategy += drift
            if du > 1e-12:
                qa.buy(i, po, du * po, fee_total)
            elif du < -1e-12:
                g, _ = bh.rule_sale(mkt, i, po, -du, fee_total, qa.lots, rule_index)
                rule_year[year_i] += g
                qa.sell(i, po, -du, fee_total, "resize")
                q_sells += 1
            trades += 1
        elif blocked is not None:
            postponed += 1
            post_by_year[year_i] += 1
            if blocked == "band":
                blocked_band += 1
            elif blocked == "realloc":
                realloc_holds += 1
            else:
                deferred += 1
                defer_by_year[year_i] += 1

        # ---- year-end hooks (the last session of each calendar year fully inside the window)
        if i + 1 <= i1 and int(days[i + 1][:4]) != year_i:
            ytd = qa.year_gain.get(year_i, 0.0)
            if year_trace is not None:
                pos_now = float(sum(qa.pos_gain_by_reason.values()))
                neg_now = float(sum(qa.neg_gain_by_reason.values()))
                year_trace.append({
                    "year": year_i, "session": days[i], "ytd_try": float(ytd),
                    "pos_try": pos_now - pos_prev, "neg_try": neg_now - neg_prev,
                    "nav_usd": float(cashbox["v"] + qa.units * pc),
                    "ceiling_try": float(TARIFF_TOP * mkt.scale(year_i)),
                    "fx": float(mkt.fx[i]), "units": float(qa.units)})
                pos_prev, neg_prev = pos_now, neg_now
            if yearend is not None and qa.units > 1e-12:
                nav_c = cashbox["v"] + qa.units * pc
                rec = year_end_fill(qa, i, pc, nav_c, cost, yearend, mkt, index)
                if rec is not None:
                    yearend_events.append(rec)
                    turnover += 2.0 * rec["notional_usd"] / nav_c

        nav[i - i0 + 1] = cashbox["v"] + qa.units * pc
        prev_tgt = tgt

    # ---- liquidation at the window end (verbatim)
    end_y = int(days[i1][:4])
    if qa.units > 0:
        qa.sell(i1, float(cq[i1]), qa.units, qa.units * float(cq[i1]) * cost, "liquidation")
    liq_tax = qa.assess(end_y) + qa.outstanding()
    qa.tax_due[end_y] = qa.tax_due.get(end_y, 0.0) - qa.tax_paid_try.get(end_y, 0.0) + liq_tax
    fx1 = mkt.fx[i1]
    liq = cashbox["v"] - (liq_tax / fx1 if fx1 != 0 else liq_tax)
    mtm = nav[-1]
    yrs = trv2.calendar_years(days[i0 - 1], days[i1])
    maxdd = float(trv2.max_dd(nav.reshape(1, -1))[0])
    return {
        "window": [days[i0], days[i1]], "years": float(yrs), "capital_usd": float(capital),
        "ending_nav_usd": float(mtm), "max_dd_nav_usd": maxdd,
        "cagr_usd_liquidated": float((liq / capital) ** (1 / yrs) - 1),
        "terminal_usd_liquidated": float(liq),
        "cagr_usd_unliquidated": float((mtm / capital) ** (1 / yrs) - 1),
        "terminal_usd_unliquidated": float(mtm),
        "tax_total_try": float(sum(qa.tax_paid_try.values()) + liq_tax),
        "tax_total_usd_at_payment_fx": float(qa.tax_paid_usd + (liq_tax / fx1 if fx1 != 0 else liq_tax)),
        "tax_assessed_try_by_year": {int(k): float(v) for k, v in sorted(qa.tax_due.items())},
        "strategy_trades": int(trades), "qld_sells": int(q_sells), "tax_funding_sales": int(tax_sales),
        "sells": int(q_sells + tax_sales),
        "turnover_total": float(turnover), "turnover_strategy_total": float(turnover_strategy),
        "turnover_annual": float(turnover / yrs),
        "postponements": int(postponed), "blocked_band": int(blocked_band),
        "deferred": int(deferred), "realloc_holds": int(realloc_holds),
        "postponements_by_year": {int(k): int(v) for k, v in sorted(post_by_year.items())},
        "deferrals_by_year": {int(k): int(v) for k, v in sorted(defer_by_year.items())},
        "fees_usd": float(qa.fees), "index_relief_try": float(qa.index_relief_try),
        "realised_gain_try_by_reason": {k: float(v) for k, v in sorted(qa.gain_by_reason.items())},
        "yearend_events": yearend_events,
        "n_yearend_round_trips": len(yearend_events),
        "yearend_notional_usd": float(sum(e["notional_usd"] for e in yearend_events)),
        "_nav": nav,
        "_year_trace": year_trace,
        "_realloc_events": realloc_events,
    }


# ----------------------------------------------------------------------------- inputs / stages
def main_inputs(spread=0.0):
    return bh.main_inputs(spread)


def pre_tax_main(arms):
    """Pre-tax main window (base/calibrated x m = 1/2/4) plus the R-USD main-window row."""
    days, o, c, w_e2, w_r2, e2 = main_inputs(0.0)
    mkt = te.load_market(bh.FX_CSV, bh.UFE_CSV, days)
    T = len(days) - 1
    usd_mkt = bh.usd_market(days)
    pre, pre_usd = {}, {}
    for md, spread in FIN.items():
        d_, o_, c_, _we, wr_, e_ = main_inputs(spread)
        pre[md], pre_usd[md] = {}, {}
        for m in (1, M, M_STRESS):
            pre[md][f"m{m}"] = {}
            for arm in arms:
                r = engine_run(PRE_CAPITAL, bh.START, T, d_, mkt, o_, c_, wr_, e_, arm, "pre_tax", m=m)
                pre[md][f"m{m}"][arm] = bh.pre_stats(d_, bh.START, T, r)
        for arm in arms:
            r = engine_run(PRE_CAPITAL, bh.START, T, d_, usd_mkt, o_, c_, wr_, e_, arm, "pre_tax",
                           m=M, rule_index=False)
            pre_usd[md][arm] = bh.pre_stats(d_, bh.START, T, r, real_days=d_)
    pre["window"] = {"start": days[bh.START], "end": days[-1], "sessions": T - bh.START + 1,
                     "years": float(trv2.calendar_years(days[bh.START - 1], days[-1]))}
    return pre, pre_usd


def _window_rec(rq, rp):
    return {"pre_tax_cagr": rp["cagr_usd_liquidated"],
            "after_tax_cagr": rq["cagr_usd_liquidated"],
            "after_tax_cagr_unliquidated": rq["cagr_usd_unliquidated"],
            "max_dd_after_tax": rq["max_dd_nav_usd"], "terminal_usd": rq["terminal_usd_liquidated"],
            "tax_usd": rq["tax_total_usd_at_payment_fx"], "sells_per_year": rq["sells"] / rq["years"],
            "trades_per_year": rq["strategy_trades"] / rq["years"],
            "turnover_annual": rq["turnover_annual"],
            "postponements": rq["postponements"], "blocked_band": rq["blocked_band"],
            "deferred": rq["deferred"], "realloc_holds": rq["realloc_holds"],
            "n_yearend_round_trips": rq["n_yearend_round_trips"],
            "yearend_notional_usd": rq["yearend_notional_usd"],
            "index_relief_try": rq["index_relief_try"],
            "realised_gain_try_by_reason": rq["realised_gain_try_by_reason"]}


def after_tax_windows(capitals, arms):
    """After tax at the given capitals (progressive 2026 tariff, liquidated, base financing,
    m = 2) on the three windows. Every arm label runs production R2's target series."""
    days, o, c, _we, w_r2, e2 = main_inputs(0.0)
    mkt = te.load_market(bh.FX_CSV, bh.UFE_CSV, days)
    win = {}
    for cap_label in capitals:
        win[cap_label] = {}
        for wn, (a, b) in TAX_WINDOWS.items():
            win[cap_label][wn] = {}
            for arm in arms:
                rq = engine_run(TAX_CAPITALS[cap_label], a, b, days, mkt, o, c, w_r2, e2, arm, "progressive")
                rp = engine_run(1.0, a, b, days, mkt, o, c, w_r2, e2, arm, "pre_tax")
                win[cap_label][wn][arm] = _window_rec(rq, rp)
    return win


def cost_stress(arms):
    """m = 4 cost stress, after tax at $100k on the three windows (report-only)."""
    days, o, c, _we, w_r2, e2 = main_inputs(0.0)
    mkt = te.load_market(bh.FX_CSV, bh.UFE_CSV, days)
    out = {}
    for wn, (a, b) in TAX_WINDOWS.items():
        out[wn] = {}
        for arm in arms:
            rq = engine_run(TAX_CAPITALS[GATE_CAPITAL], a, b, days, mkt, o, c, w_r2, e2, arm,
                            "progressive", m=M_STRESS)
            rp = engine_run(1.0, a, b, days, mkt, o, c, w_r2, e2, arm, "pre_tax", m=M_STRESS)
            out[wn][arm] = {"after_tax_cagr": rq["cagr_usd_liquidated"],
                            "pre_tax_cagr": rp["cagr_usd_liquidated"],
                            "max_dd_after_tax": rq["max_dd_nav_usd"],
                            "turnover_annual": rq["turnover_annual"],
                            "postponements": rq["postponements"]}
    return out


def rolling_starts(arms):
    """Report-only: 21 rolling January starts (2001..2021), after tax at $100k, base, m = 2."""
    days, o, c, _we, w_r2, e2 = main_inputs(0.0)
    mkt = te.load_market(bh.FX_CSV, bh.UFE_CSV, days)
    T = len(days) - 1
    out = {}
    for yr in bh.E2_START_YEARS:
        i0 = bisect.bisect_left(days, f"{yr}-01-01")
        rec = {"start": days[i0]}
        for arm in arms:
            rq = engine_run(TAX_CAPITALS[GATE_CAPITAL], i0, T, days, mkt, o, c, w_r2, e2, arm, "progressive")
            rec[arm] = {"after_tax_cagr": rq["cagr_usd_liquidated"],
                        "max_dd_after_tax": rq["max_dd_nav_usd"], "years": float(rq["years"]),
                        "tax_usd": rq["tax_total_usd_at_payment_fx"]}
        for arm in arms:
            if arm == CONTROL:
                continue
            rec[arm]["delta_after_tax_pp"] = (rec[arm]["after_tax_cagr"] - rec[CONTROL]["after_tax_cagr"]) * 100
        out[str(yr)] = rec
    return out


def oos_window(days, o, c, y, start, arms):
    """One OOS window (verbatim from the parent module's wrapper, this study's engine)."""
    so, sc = trv2.synthetic_2x(o, c, np.zeros_like(c), y + bh.EXTRA_FIN, bh.EXP_Q)
    e2 = bh.e2_up(c)[0]
    comps = bh.early_components(c)
    _w_e2, w_r2 = bh.wants_of(c, e2, trv2.vol_n(sc)[0], comps)
    mkt = bh.usd_market(days)
    res = {"window": {"start": days[start], "end": days[-1], "sessions": len(days) - start}}
    for arm in arms:
        r = engine_run(1.0, start, len(days) - 1, days, mkt, so, sc, w_r2, e2, arm, "pre_tax",
                       m=M, rule_index=False)
        st = bh.window_stats(r["_nav"], days, start)
        st["turnover_annual"] = float(r["turnover_annual"])
        st["postponements"] = int(r["postponements"])
        st["n_yearend_round_trips"] = int(r["n_yearend_round_trips"])
        res[arm] = st
    return res


def oos_stages(arms):
    """Six OOS windows (^GSPC 1957-1999 + M1-M5), pre-tax, calibrated, m = 2, R-USD rule."""
    oos = {}
    gd, go, gc, gy, gstart = bh.load_gspc()
    oos["GSPC_1957_1999"] = oos_window(gd, go, gc, gy, gstart, arms)
    for key in ("M1", "M2", "M3", "M4", "M5"):
        md_, mo_, mc_, my_, mstart_ = bh.load_market(key)
        oos[key] = oos_window(md_, mo_, mc_, my_, mstart_, arms)
    for wn in bh.OOS_WINDOWS:
        for arm in arms:
            if arm == CONTROL:
                continue
            oos[wn][arm]["delta_cagr_pp"] = (oos[wn][arm]["cagr"] - oos[wn][CONTROL]["cagr"]) * 100
            oos[wn][arm]["delta_max_dd_pp"] = (oos[wn][arm]["max_dd"] - oos[wn][CONTROL]["max_dd"]) * 100
    return oos


def boot_oos_market(key, rng, arms):
    """Pooled OOS bootstrap draws (verbatim from the parent module's `boot_oos_market`, this
    study's engine and arm set)."""
    days, o, c, y, start = bh.load_market(key)
    pool = np.arange(start, len(days))
    npool = len(pool)
    mkt = bh.usd_market(days)
    acc = {a: [] for a in arms}
    for chunk in range(0, bh.OOS_PATHS_PER_MARKET, trv2.CHUNK):
        P = min(trv2.CHUNK, bh.OOS_PATHS_PER_MARKET - chunk)
        pos = np.empty((P, bh.OOS_BOOT_L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, bh.OOS_BOOT_L)) < 1 / BLOCK
        fresh = rng.integers(0, npool, (P, bh.OOS_BOOT_L))
        for t in range(1, bh.OOS_BOOT_L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        idx = pool[pos]
        r_on = o[idx] / c[idx - 1]
        r_id = c[idx] / o[idx]
        cc = np.concatenate([np.ones((P, 1)), np.cumprod(r_on * r_id, axis=1)], axis=1)
        oo = np.concatenate([np.ones((P, 1)), cc[:, :-1] * r_on], axis=1)
        yy = y[idx - 1]
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        e2 = bh.e2_up(cc)
        so, sc = trv2.synthetic_2x(oo, cc, np.zeros_like(cc), yyp + bh.EXTRA_FIN, bh.EXP_Q)
        _we, w_r2 = bh.wants_of(cc, e2, trv2.vol_n(sc)[0])
        for arm in arms:
            for p in range(P):
                r = engine_run(1.0, bh.START, cc.shape[1] - 1, days, mkt, so[p], sc[p], w_r2[p],
                               e2[p], arm, "pre_tax", m=M, rule_index=False)
                k = len(r["_nav"]) - 1
                acc[arm].append((r["_nav"][-1] / r["_nav"][0]) ** (252 / k) - 1)
    return {a: np.array(v, dtype=float) for a, v in acc.items()}


def boot_clean(seed=SEED, block=BLOCK, arms=STAGE_ARMS, n_pre=bh.PRE_BOOT_PATHS,
               n_tax=bh.TAX_BOOT_PATHS, capitals=("100k", "1M"), modes=("progressive", "flat"),
               deltas=ARMS, baselines=(CONTROL,), label="clean"):
    """The parent module's clean block bootstrap (seed 20261011, block 20, fixed 2015-09 -> 2026-09
    calendar), with this study's engine and arm set. The path draws depend only on
    (seed, block, n_paths), so the control's cells reproduce the published ones."""
    all_days, arr, y = trv2.load()
    sub = [d for d in all_days if bh.TAX_BOOT_LO <= d <= bh.TAX_BOOT_HI]
    need = bh.TAX_BOOT_L + bh.TAX_BOOT_EXT
    if len(sub) < need:
        raise RuntimeError(f"fixed calendar {bh.TAX_BOOT_LO}..{bh.TAX_BOOT_HI} has only {len(sub)} days")
    fixed_dates = list(sub[:need])
    fixed_fx = bh.fx_at(fixed_dates)
    ufe = te.load_market(bh.FX_CSV, bh.UFE_CSV, all_days).ufe
    mkt = te.Market(fixed_dates, fixed_fx, ufe)
    q = arr["QQQ"]
    Tsrc = q.shape[1]
    rng = np.random.default_rng(seed)
    pool = np.arange(1, Tsrc)
    npool = len(pool)
    L, EXT_ = bh.TAX_BOOT_L, bh.TAX_BOOT_EXT
    i0, i1 = bh.TAX_BOOT_I0, len(fixed_dates) - 1
    pre = {(md, a): [] for md in FIN for a in arms}
    at = {cl: {mode: {a: [] for a in arms} for mode in modes} for cl in capitals}
    n_paths = max(n_pre, n_tax)
    for pidx in range(n_paths):
        pos = np.empty((1, L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, 1)
        jump = rng.random((1, L)) < 1 / block
        fresh = rng.integers(0, npool, (1, L))
        for t in range(1, L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        pre_cols = np.stack([(pos[:, 0] - k) % npool for k in range(EXT_, 0, -1)], axis=1)
        pos = np.concatenate([pre_cols, pos], axis=1)
        idx = pool[pos]
        yy = y[idx - 1]
        prev = q[1][idx - 1]
        r_on_tr = (q[0][idx] + q[2][idx]) / prev
        r_on_p = q[0][idx] / prev
        r_id = q[1][idx] / q[0][idx]
        one = np.ones((1, 1))
        qc = np.concatenate([one, np.cumprod(r_on_tr * r_id, axis=1)], axis=1)
        qo = np.concatenate([one, qc[:, :-1] * r_on_tr], axis=1)
        pc = np.concatenate([one, np.cumprod(r_on_p * r_id, axis=1)], axis=1)
        e2 = bh.e2_up(pc)[0]
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        comps = bh.early_components(pc)
        so, sc = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN["base"], bh.EXP_Q)
        so_c, sc_c = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + FIN["calibrated"], bh.EXP_Q)
        on_eff, factor = bh.r2_state(pc, e2, comps)
        w_r2 = bh.sized_wants(on_eff, factor, trv2.vol_n(sc)[0])[0]
        w_r2c = bh.sized_wants(on_eff, factor, trv2.vol_n(sc_c)[0])[0]
        legs = {"base": (so[0], sc[0], w_r2), "calibrated": (so_c[0], sc_c[0], w_r2c)}
        if pidx < n_pre:
            for md, (o_, c_, want) in legs.items():
                for arm in arms:
                    r = engine_run(PRE_CAPITAL, i0, i1, fixed_dates, mkt, o_, c_, want, e2,
                                   arm, "pre_tax", m=M)
                    k = len(r["_nav"]) - 1
                    pre[(md, arm)].append((r["_nav"][-1] / r["_nav"][0]) ** (252 / k) - 1)
        if pidx < n_tax:
            o_, c_, want = legs["base"]
            for cap_label in capitals:
                for mode in modes:
                    for arm in arms:
                        r = engine_run(TAX_CAPITALS[cap_label], i0, i1, fixed_dates, mkt, o_, c_,
                                       want, e2, arm, mode, m=M)
                        if not (r["terminal_usd_liquidated"] > 0) or r["terminal_usd_liquidated"] != r["terminal_usd_liquidated"]:
                            raise RuntimeError(f"non-positive/NaN terminal on path {pidx} arm {arm} {mode} {cap_label}")
                        at[cap_label][mode][arm].append(r["cagr_usd_liquidated"])
        if (pidx + 1) % 1000 == 0:
            print(f"  {label} path {pidx + 1}/{n_paths}", flush=True)

    out = {"n_paths_pre": n_pre, "n_paths_tax": n_tax, "seed": seed, "block": block,
           "arms": list(arms), "calendar_start": fixed_dates[0], "calendar_end": fixed_dates[-1],
           "pre": {}, "tax": {}}
    for md in (FIN if n_pre > 0 else ()):
        base = np.array(pre[(md, CONTROL)])
        out["pre"][md] = {}
        for arm in arms:
            c = np.array(pre[(md, arm)])
            d = c - base
            out["pre"][md][arm] = {
                "cagr_median": float(np.median(c)),
                "diff_vs_C0_median_pp": float(np.median(d) * 100),
                "diff_vs_C0_mean_pp": float(np.mean(d) * 100), "share_gt_0": float(np.mean(d > 0)),
                "p05_delta_pp": float(np.percentile(d, 5) * 100),
                "p95_delta_pp": float(np.percentile(d, 95) * 100)}
    for cl in capitals:
        out["tax"][cl] = {}
        for mode in modes:
            e = {"n_paths": n_tax, "capital_usd": TAX_CAPITALS[cl]}
            for arm in arms:
                e[arm] = {"cagr_median": float(np.median(at[cl][mode][arm]))}
            for ctrl in baselines:
                base = np.array(at[cl][mode][ctrl])
                for arm in deltas:
                    if arm == ctrl:
                        continue
                    d = np.array(at[cl][mode][arm]) - base
                    e[f"{arm}_vs_{ctrl}"] = {
                        "median_delta_cagr_pp": float(np.median(d) * 100),
                        "mean_delta_cagr_pp": float(np.mean(d) * 100),
                        "share_gt_0": float(np.mean(d > 0)),
                        "p05_delta_cagr_pp": float(np.percentile(d, 5) * 100),
                        "p95_delta_cagr_pp": float(np.percentile(d, 95) * 100)}
            out["tax"][cl][mode] = e
    return out


def oos_bootstrap(arms):
    """Pooled OOS bootstrap (G8): block-20 over M1-M5, rng seed SEED, arms in order."""
    rng = np.random.default_rng(SEED)
    per = {k: boot_oos_market(k, rng, arms) for k in bh.MARKETS}
    pooled = {a: np.concatenate([per[k][a] for k in bh.MARKETS]) for a in arms}
    out = {"per_market": {}, "pooled": {}}
    for k in bh.MARKETS:
        out["per_market"][k] = {a: {"median_diff_pp": float(np.median(per[k][a] - per[k][CONTROL]) * 100)}
                                for a in arms}
    for a in arms:
        d = pooled[a] - pooled[CONTROL]
        out["pooled"][a] = {"diff_vs_C0_median_pp": float(np.median(d) * 100),
                            "diff_vs_C0_mean_pp": float(np.mean(d) * 100),
                            "share_gt_0": float(np.mean(d > 0)), "paths": int(len(d))}
    return out


def yearend_log(arms=("Y1", "Y2", "Y4")):
    """Report-only: the year-end round-trip events of Y1/Y2/Y4 on the main window at $100k."""
    days, o, c, _we, w_r2, e2 = main_inputs(0.0)
    mkt = te.load_market(bh.FX_CSV, bh.UFE_CSV, days)
    T = len(days) - 1
    out = {}
    for arm in arms:
        r = engine_run(TAX_CAPITALS[GATE_CAPITAL], bh.START, T, days, mkt, o, c, w_r2, e2, arm,
                       "progressive")
        out[arm] = r["yearend_events"]
    return out


def pool_diagnostic():
    """PREREGISTRATION section 6: the destroyed-loss pool and the bracket arithmetic on the K
    path at $100k (main window), measured from the control only -- a measurement, not a gate."""
    days, o, c, _we, w_r2, e2 = main_inputs(0.0)
    mkt = te.load_market(bh.FX_CSV, bh.UFE_CSV, days)
    T = len(days) - 1
    r = engine_run(TAX_CAPITALS[GATE_CAPITAL], bh.START, T, days, mkt, o, c, w_r2, e2, CONTROL,
                   "progressive", trace_year=True)
    rows = r["_year_trace"]
    pool = [{"year": x["year"], "ytd_try": x["ytd_try"], "pool_try": max(0.0, -x["ytd_try"]),
             "nav_usd": x["nav_usd"], "fx": x["fx"],
             "ceiling_try": x["ceiling_try"],
             "ytd_over_ceiling": x["ytd_try"] / x["ceiling_try"] if x["ceiling_try"] else None,
             "headroom_gap_try": max(0.0, x["ceiling_try"] - x["ytd_try"]),
             "cap_try": NAV_CAP_FRAC * x["nav_usd"] * x["fx"],
             "cap_binds_25pct": bool(NAV_CAP_FRAC * x["nav_usd"] * x["fx"]
                                     < max(0.0, x["ceiling_try"] - x["ytd_try"]))}
            for x in rows]
    loss = [p for p in pool if p["pool_try"] > 0]
    return {
        "window": {"start": r["window"][0], "end": r["window"][1]},
        "n_years": len(pool),
        "n_loss_years": len(loss),
        "loss_years": [p["year"] for p in loss],
        "pool_sum_try": float(sum(p["pool_try"] for p in pool)),
        "pool_sum_usd": float(sum(p["pool_try"] / p["fx"] for p in pool if p["fx"])),
        "n_years_headroom_trigger": int(sum(1 for p in pool if p["ytd_over_ceiling"] is not None
                                            and p["ytd_over_ceiling"] < HEADROOM_TRIGGER_FRAC)),
        "n_years_cap_binds": int(sum(1 for p in pool if p["cap_binds_25pct"])),
        "per_year": pool,
    }


# ----------------------------------------------------------------------------- smoke / harness
def synthetic_bars(n, seed):
    return bh.synthetic_bars(n, seed)


def syn_market(days):
    """Synthetic market for --smoke: fx = 1 and a Yi-UFE index rising 20 %/yr, so the indexation
    branch of the arm's probe and of the engine are both exercised. No macro data touched."""
    years = sorted({int(d[:4]) for d in days})
    y0, y1 = min(min(years) - 1, 1999), max(max(years) + 2, 2030)
    ufe = {}
    for y in range(y0, y1 + 1):
        for mo in range(1, 13):
            ufe[f"{y}-{mo:02d}"] = 100.0 * (1.2 ** (y - y0))
    return te.Market(days, np.ones(len(days)), ufe)


def smoke():
    """Synthetic only (no market data): the copied engine must be the parent's on the K path, the
    read-only probe must equal the engine's own sale arithmetic, and both new hooks must be the
    pre-registered rule."""
    n = 3000
    o, price, div = synthetic_bars(n, 7)
    days = bh._trading_days(n)
    y = np.full(len(price), 0.01)
    so, sc = trv2.synthetic_2x(o, price, div, y, bh.EXP_Q)
    e2 = bh.e2_up(price)[0]
    comps = bh.early_components(price)
    _we, w = bh.wants_of(price, e2, trv2.vol_n(sc)[0], comps)
    mkt = syn_market(days)
    cap0, K0, T = 100_000.0, bh.START, len(days) - 1
    checks = {}

    # (a) the copied engine is the parent's, on the K code path, pre-tax and progressive
    for mode in ("pre_tax", "progressive"):
        a = engine_run(cap0, K0, T, days, mkt, so, sc, w[0], e2, "K", mode)
        b = bh.engine_run(cap0, K0, T, days, mkt, so, sc, w[0], e2, "K", mode)
        same = (np.array_equal(a["_nav"], b["_nav"], equal_nan=True)
                and a["cagr_usd_liquidated"] == b["cagr_usd_liquidated"]
                and a["turnover_total"] == b["turnover_total"]
                and a["sells"] == b["sells"] and a["postponements"] == b["postponements"]
                and a["tax_total_usd_at_payment_fx"] == b["tax_total_usd_at_payment_fx"]
                and a["realised_gain_try_by_reason"] == b["realised_gain_try_by_reason"]
                and np.array_equal(a["_nav"], bh.engine_run(
                    cap0, K0, T, days, mkt, so, sc, w[0], e2, "K", mode)["_nav"]))
        checks[f"engine_identical_K_{mode}"] = bool(same)

    # (b) the closed-form quantity equals the engine's own sale arithmetic on random lot books
    #     (the probe must exercise the Yi-UFE branch: an old, profitable lot gets relief)
    rng = np.random.default_rng(11)
    ok_q, n_idx = True, 0
    for _ in range(400):
        nl = int(rng.integers(1, 6))
        lots = [[float(rng.uniform(0.05, 5.0)), float(rng.uniform(1.0, 60.0)),
                 float(rng.uniform(1.0, 40.0)), f"{int(rng.integers(2000, 2025))}-{int(rng.integers(1, 13)):02d}"]
                for _ in range(nl)]
        i = int(rng.integers(K0, len(days)))
        price_i = float(sc[i])
        cost = 4e-4 * 2
        need = float(rng.uniform(0.0, 500.0))
        cap_u = float(rng.uniform(0.0, sum(l[0] for l in lots)))
        q = gain_matched_units(mkt, lots, i, price_i, cost, need, cap_u, True)
        if any(lot_gain_per_unit(mkt, i, price_i, cost, l, True) !=
               lot_gain_per_unit(mkt, i, price_i, cost, l, False) for l in lots):
            n_idx += 1
        g_lo = bh.rule_sale(mkt, i, price_i, max(q - 1e-7, 0.0), max(q - 1e-7, 0.0) * price_i * cost, lots)[0]
        g_hi = bh.rule_sale(mkt, i, price_i, min(q + 1e-7, cap_u), min(q + 1e-7, cap_u) * price_i * cost, lots)[0]
        if abs(g_lo - need) < 1e-6:
            ok_q = ok_q and (g_lo <= need + 1e-9)
        elif q < cap_u - 1e-9:
            ok_q = ok_q and (g_hi >= need) and (g_lo <= need)
        else:
            ok_q = ok_q and (g_lo <= need + 1e-9)
    checks["gain_matched_units_matches_engine"] = bool(ok_q)
    lots_old = [[1.0, 1.0, 1.0, "2005-01"]]
    checks["probe_exercises_indexation"] = bool(
        lot_gain_per_unit(mkt, T, 100.0, 8e-4, lots_old[0], True)
        < lot_gain_per_unit(mkt, T, 100.0, 8e-4, lots_old[0], False))

    # (c) the round trip itself: units unchanged, cash down exactly two legs, the year zeroed
    # a session whose price rose from the previous open, so the book has a known positive gain
    i = next(k for k in range(K0 + 1, len(days)) if float(sc[k]) > float(so[k - 1]) * 1.001)
    pc_i = float(sc[i])
    y_i = int(days[i][:4])
    # case 1: a pool 20 % of the book's gain -> well inside the 25 %-of-NAV cap
    cb = {"v": 0.0}
    acc = te.BaseAccount(mkt, te.Regime("progressive", "tariff"), 0.0, cb)
    cb["v"] = 100_000.0          # the engine's own convention: the cashbox is funded after init
    acc.buy(i - 1, float(so[i - 1]), 60_000.0, 0.0)
    g_book = acc.units * lot_gain_per_unit(mkt, i, pc_i, 8e-4, acc.lots[-1], True)
    acc.year_gain[y_i] = -0.2 * g_book
    units0, cash0 = acc.units, cb["v"]
    nav_c = cb["v"] + acc.units * pc_i
    rec = year_end_fill(acc, i, pc_i, nav_c, 8e-4, "pool", mkt, True)
    checks["round_trip_units_unchanged"] = bool(abs(acc.units - units0) < 1e-9)
    checks["round_trip_costs_two_legs"] = bool(
        rec is not None and abs((cash0 - cb["v"]) - 2 * rec["notional_usd"] * 8e-4) < 1e-6)
    checks["round_trip_zeroes_the_year"] = bool(rec is not None and abs(acc.year_gain[y_i]) < 1e-6
                                               and not rec["cap_binding"])
    checks["round_trip_respects_cap"] = bool(rec is not None
                                             and rec["frac_nav"] <= NAV_CAP_FRAC + 1e-9)
    # case 2: a pool bigger than the capped book -> the cap binds, the year still improves
    cb2 = {"v": 0.0}
    acc2 = te.BaseAccount(mkt, te.Regime("progressive", "tariff"), 0.0, cb2)
    cb2["v"] = 100_000.0
    acc2.buy(i - 1, float(so[i - 1]), 60_000.0, 0.0)
    acc2.year_gain[y_i] = -10_000_000.0
    nav_c2 = cb2["v"] + acc2.units * pc_i
    rec2 = year_end_fill(acc2, i, pc_i, nav_c2, 8e-4, "pool", mkt, True)
    checks["round_trip_cap_binds_when_pool_is_huge"] = bool(
        rec2 is not None and rec2["cap_binding"] and abs(rec2["frac_nav"] - NAV_CAP_FRAC) < 1e-9
        and acc2.year_gain[y_i] > -10_000_000.0)
    r1 = engine_run(cap0, K0, T, days, mkt, so, sc, w[0], e2, "K", "progressive")
    r2 = engine_run(cap0, K0, T, days, mkt, so, sc, w[0], e2, "Y1", "progressive", trace_year=True)
    ev = r2["yearend_events"]
    checks["k_has_no_yearend_events"] = bool(r1["n_yearend_round_trips"] == 0)
    checks["y1_fires_only_on_loss_years"] = bool(
        all(e["ytd_try_before"] < 0.0 and e["kind"] == "pool" for e in ev))
    checks["y1_zeroes_the_year"] = bool(all(
        abs(e["ytd_try_after"]) <= 1e-6 or e["cap_binding"] for e in ev))
    checks["y1_respects_25pct_cap"] = bool(all(e["frac_nav"] <= NAV_CAP_FRAC + 1e-9 for e in ev))
    checks["y1_only_needs_the_year_end"] = bool(len(ev) <= len(r2["_year_trace"]))
    r3 = engine_run(cap0, K0, T, days, mkt, so, sc, w[0], e2, "Y2", "progressive")
    checks["y2_fires_only_below_headroom_trigger"] = bool(all(
        e["ytd_try_before"] < HEADROOM_TRIGGER_FRAC * e["ceiling_try"] for e in r3["yearend_events"]))

    # (d) Y3's state test and the holds it produces
    days_jan = [f"2020-01-0{k}" for k in (2, 3, 6, 7, 8, 9, 10, 13, 14, 15, 16, 17)]
    days_dec = [f"2020-12-{k}" for k in (14, 15, 16, 17, 18, 21, 22, 23, 24, 28, 29, 30)]
    all_days = days_dec + days_jan
    win = jan_head(all_days, 0, len(all_days) - 1, bh.DEC_TAIL) | bh.december_tail(
        all_days, 0, len(all_days) - 1, bh.DEC_TAIL)
    checks["realloc_window_is_dec_tail_and_jan_head"] = bool(
        win == set(range(2, 12)) | set(range(12, 22)))
    qa = te.BaseAccount(mkt, te.Regime("progressive", "tariff"), 0.0, {"v": 1.0})
    qa.year_gain[2020] = 100.0
    qa.year_gain[2019] = -5.0
    checks["state_dec_positive_holds"] = bool(not current_year_cheaper(qa, all_days, 5))
    qa_dec = te.BaseAccount(mkt, te.Regime("progressive", "tariff"), 0.0, {"v": 1.0})
    qa_dec.year_gain[2020] = -1.0
    checks["state_dec_loss_fills"] = bool(current_year_cheaper(qa_dec, all_days, 5))
    checks["state_jan_dearer_holds"] = bool(not current_year_cheaper(qa, all_days, 15))
    qa2 = te.BaseAccount(mkt, te.Regime("progressive", "tariff"), 0.0, {"v": 1.0})
    qa2.year_gain[2020] = -9.0
    qa2.year_gain[2019] = 100.0
    checks["state_jan_cheaper_fills"] = bool(current_year_cheaper(qa2, all_days, 15))
    r4 = engine_run(cap0, K0, T, days, mkt, so, sc, w[0], e2, "Y3", "progressive")
    rv = r4["_realloc_events"]
    checks["y3_holds_only_in_window"] = bool(all(
        e["session"][5:7] in (JAN_WINDOW, DEC_WINDOW) for e in rv))
    checks["y3_holds_only_when_year_is_dearer"] = bool(all(e["ytd_cur"] > e["ytd_other"] for e in rv))
    checks["y3_h2_window_off"] = bool(r4["deferred"] == 0 and r4["realloc_holds"] == len(rv))
    # Y3 adds no trade of its own (no round trip); its strategy-trade count can still differ
    # slightly because a moved fill changes the held weight and hence later drifts.
    checks["y3_no_new_trade"] = bool(r4["n_yearend_round_trips"] == 0)
    r5 = engine_run(cap0, K0, T, days, mkt, so, sc, w[0], e2, "Y4", "progressive")
    checks["y4_has_both_hooks"] = bool(r5["realloc_holds"] >= 0 and r5["deferred"] == 0
                                       and r5["n_yearend_round_trips"] == len(r5["yearend_events"]))
    r6 = engine_run(cap0, K0, T, days, mkt, so, sc, w[0], e2, REFERENCE, "progressive")
    checks["reference_row_is_the_parent_C0"] = bool(
        np.array_equal(r6["_nav"], bh.engine_run(cap0, K0, T, days, mkt, so, sc, w[0], e2,
                                                 "C0", "progressive")["_nav"]))
    return {"checks": {k: bool(v) for k, v in checks.items()},
            "info": {"y3_vs_K_strategy_trades": [r4["strategy_trades"], r1["strategy_trades"]],
                     "y3_vs_K_turnover": [r4["turnover_strategy_total"], r1["turnover_strategy_total"]]},
            "pass": bool(all(checks.values()))}


def harness():
    """Control reproduction on real data (no candidate arm): every K cell this run produces must
    equal band_h2_combo_v1's published cell; the R2 row must equal the R2 anchors."""
    ref = json.loads(REF_K.read_text())
    cells, bad = [], []

    def cmp(label, mine, theirs, tol=TOL):
        d = abs(float(mine) - float(theirs))
        cells.append((label, float(mine), float(theirs), d))
        if d > tol:
            bad.append((label, float(mine), float(theirs), d))

    pre, _pre_usd = pre_tax_main((CONTROL, REFERENCE))
    for md in ("base", "calibrated"):
        for mm in ("m1", "m2", "m4"):
            for arm, rarm in ((CONTROL, "K"), (REFERENCE, "C0")):
                mine, theirs = pre[md][mm][arm], ref["pre_tax_main"][md][mm][rarm]
                for f in ("cagr", "cagr_liquidated", "max_dd", "turnover_annual", "trades", "sells_per_year"):
                    cmp(f"pre.{md}.{mm}.{arm}.{f}", mine[f], theirs[f])
        cmp(f"pre.{md}.m2.K.calmar", pre[md]["m2"][CONTROL]["calmar"], ref["pre_tax_main"][md]["m2"]["K"]["calmar"])
    # the literal brief anchors
    cmp("anchor.K.pre_base", pre["base"]["m2"][CONTROL]["cagr"], ANCHOR_K["pre_base"])
    cmp("anchor.K.pre_cal", pre["calibrated"]["m2"][CONTROL]["cagr"], ANCHOR_K["pre_cal"])
    cmp("anchor.K.dd_base", pre["base"]["m2"][CONTROL]["max_dd"], ANCHOR_K["dd_base"])
    cmp("anchor.K.dd_cal", pre["calibrated"]["m2"][CONTROL]["max_dd"], ANCHOR_K["dd_cal"])
    cmp("anchor.K.turnover", pre["base"]["m2"][CONTROL]["turnover_annual"], ANCHOR_K["turnover"])
    cmp("anchor.R2.pre_base", pre["base"]["m2"][REFERENCE]["cagr"], bh.REF_R2[("base", 2)][0])
    cmp("anchor.R2.pre_cal", pre["calibrated"]["m2"][REFERENCE]["cagr"], bh.REF_R2[("calibrated", 2)][0])
    cmp("anchor.R2.dd_base", pre["base"]["m2"][REFERENCE]["max_dd"], bh.REF_R2[("base", 2)][1])

    win = after_tax_windows((GATE_CAPITAL,), (CONTROL, REFERENCE))
    for wn in TAX_WINDOWS:
        mine = win[GATE_CAPITAL][wn][CONTROL]
        theirs = ref["after_tax_windows"][GATE_CAPITAL][wn]["K"]
        for f in ("pre_tax_cagr", "after_tax_cagr", "max_dd_after_tax", "tax_usd",
                  "sells_per_year", "trades_per_year", "turnover_annual", "postponements",
                  "blocked_band", "deferred", "index_relief_try"):
            cmp(f"at.{wn}.K.{f}", mine[f], theirs[f])
        cmp(f"at.{wn}.K.anchor", mine["after_tax_cagr"], ANCHOR_K["at"][wn])
        cmp(f"at.{wn}.R2.anchor", win[GATE_CAPITAL][wn][REFERENCE]["after_tax_cagr"], ANCHOR_R2_AT[wn])
        cmp(f"at.{wn}.R2.cell", win[GATE_CAPITAL][wn][REFERENCE]["after_tax_cagr"],
            ref["after_tax_windows"][GATE_CAPITAL][wn]["C0"]["after_tax_cagr"])

    boot = boot_clean(arms=(REFERENCE, CONTROL), deltas=(CONTROL,), baselines=(REFERENCE,),
                      capitals=("100k", "1M"), modes=("progressive", "flat"), label="harness")
    for md in ("base", "calibrated"):
        cmp(f"boot.pre.{md}.K", boot["pre"][md][CONTROL]["diff_vs_C0_median_pp"],
            ref["bootstrap_clean_seed20261011"]["pre"][md]["K"]["diff_vs_C0_median_pp"])
    for cl in ("100k", "1M"):
        for mode in ("progressive", "flat"):
            mine = boot["tax"][cl][mode][f"{CONTROL}_vs_{REFERENCE}"]
            theirs = ref["bootstrap_clean_seed20261011"]["tax"][cl][mode]["K_vs_C0"]
            for f in ("median_delta_cagr_pp", "mean_delta_cagr_pp", "share_gt_0", "p05_delta_cagr_pp",
                      "p95_delta_cagr_pp"):
                cmp(f"boot.{cl}.{mode}.K_vs_C0.{f}", mine[f], theirs[f])
    return {"cells": len(cells), "max_abs_diff": float(max(c[3] for c in cells)) if cells else 0.0,
            "failures": [{"cell": c[0], "mine": c[1], "ref": c[2], "diff": c[3]} for c in bad],
            "pass": bool(not bad), "tol": TOL}


# ----------------------------------------------------------------------------- reference check
def reference_checks(res):
    """Every K cell of this run that the parent campaign also published, compared."""
    ref = json.loads(REF_K.read_text())
    cells, bad, one_sided = [], [], []

    def cmp(label, mine, theirs):
        m, t = float(mine), float(theirs)
        if t != t:
            return
        d = abs(m - t)
        cells.append((label, m, t, d))
        if d > TOL:
            bad.append({"cell": label, "mine": m, "ref": t, "diff": d})

    for md in ("base", "calibrated"):
        for mm in ("m1", "m2", "m4"):
            for f in ("cagr", "cagr_liquidated", "max_dd", "turnover_annual", "trades", "sells_per_year"):
                cmp(f"pre_tax_main.{md}.{mm}.K.{f}", res["pre"][md][mm][CONTROL][f],
                    ref["pre_tax_main"][md][mm]["K"][f])
            for f in ("cagr", "max_dd", "turnover_annual", "trades", "sells_per_year"):
                cmp(f"pre_tax_main.{md}.{mm}.R2.{f}", res["pre"][md][mm][REFERENCE][f],
                    ref["pre_tax_main"][md][mm]["C0"][f])
    for cl in TAX_CAPITALS:
        for wn in TAX_WINDOWS:
            for f in ("pre_tax_cagr", "after_tax_cagr", "max_dd_after_tax", "tax_usd", "sells_per_year",
                      "trades_per_year", "turnover_annual", "postponements", "blocked_band", "deferred",
                      "index_relief_try"):
                cmp(f"after_tax_windows.{cl}.{wn}.K.{f}", res["windows"][cl][wn][CONTROL][f],
                    ref["after_tax_windows"][cl][wn]["K"][f])
                cmp(f"after_tax_windows.{cl}.{wn}.R2.{f}", res["windows"][cl][wn][REFERENCE][f],
                    ref["after_tax_windows"][cl][wn]["C0"][f])
    for wn in TAX_WINDOWS:
        cmp(f"cost_stress_m4.{wn}.K.after_tax_cagr", res["cost"][wn][CONTROL]["after_tax_cagr"],
            ref["cost_stress_m4"][wn]["K"]["after_tax_cagr"])
        cmp(f"cost_stress_m4.{wn}.R2.after_tax_cagr", res["cost"][wn][REFERENCE]["after_tax_cagr"],
            ref["cost_stress_m4"][wn]["C0"]["after_tax_cagr"])
    for md in ("base", "calibrated"):
        cmp(f"bootstrap.{md}.K", res["boot_ref"]["pre"][md][CONTROL]["diff_vs_C0_median_pp"],
            ref["bootstrap_clean_seed20261011"]["pre"][md]["K"]["diff_vs_C0_median_pp"])
        cmp(f"bootstrap.{md}.R2", res["boot_ref"]["pre"][md][REFERENCE]["diff_vs_C0_median_pp"],
            ref["bootstrap_clean_seed20261011"]["pre"][md]["C0"]["diff_vs_C0_median_pp"])
    for cl in ("100k", "1M"):
        for mode in ("progressive", "flat"):
            for f in ("median_delta_cagr_pp", "mean_delta_cagr_pp", "share_gt_0", "p05_delta_cagr_pp",
                      "p95_delta_cagr_pp"):
                cmp(f"boot.{cl}.{mode}.K.{f}", res["boot_ref"]["tax"][cl][mode][f"K_vs_{REFERENCE}"][f],
                    ref["bootstrap_clean_seed20261011"]["tax"][cl][mode]["K_vs_C0"][f])
                cmp(f"boot.{cl}.{mode}.R2.{f}", res["boot_ref"]["tax"][cl][mode][f"R2_vs_{REFERENCE}"][f]
                    if f"R2_vs_{REFERENCE}" in res["boot_ref"]["tax"][cl][mode] else 0.0, 0.0)
            cmp(f"boot.{cl}.{mode}.K.median_abs",
                res["boot_ref"]["tax"][cl][mode][CONTROL]["cagr_median"],
                ref["bootstrap_clean_seed20261011"]["tax"][cl][mode]["K"]["cagr_median"])
    for wn in bh.OOS_WINDOWS:
        cmp(f"oos.{wn}.K.delta_cagr_pp", res["oos"][wn][CONTROL]["delta_cagr_pp"],
            ref["oos"][wn]["K"]["delta_cagr_pp"])
        cmp(f"oos.{wn}.R2.delta_cagr_pp", 0.0, ref["oos"][wn]["C0"]["delta_cagr_pp"])
        cmp(f"oos.{wn}.K.cagr", res["oos"][wn][CONTROL]["cagr"], ref["oos"][wn]["K"]["cagr"])
    for key, mine in (("K", "K"), ("C0", "R2")):
        for yr, cell in ref.get("E2_rolling_starts", {}).items():
            one_sided.append(f"E2_rolling_starts.{yr}.{mine}")
    return {"cells": len(cells), "max_abs_diff": float(max(c[3] for c in cells)) if cells else 0.0,
            "failures": bad[:20], "n_failures": len(bad),
            "not_in_this_run": sorted(set(one_sided))[:4],
            "pass": bool(not bad), "tol": TOL}


# ----------------------------------------------------------------------------- gates
def _g1_ok(cagr, calmar, dd, k_cagr, k_calmar, k_dd):
    if cagr >= k_cagr + G1_MIN_CAGR_PP - 1e-15:
        return True
    return bool(calmar >= CALMAR_MULT * k_calmar - 1e-15
                and cagr >= k_cagr + G1_CALMAR_CAGR_PP / 100.0)


def gates(res):
    pre, win, boot_ref = res["pre"], res["windows"][GATE_CAPITAL], res["boot_ref"]
    det = {}
    for arm in GATED:
        g = {}
        cagr = {md: (pre[md]["m2"][arm]["cagr"] - pre[md]["m2"][CONTROL]["cagr"]) * 100
                for md in ("base", "calibrated")}
        ddn = {md: (pre[md]["m2"][arm]["max_dd"] - pre[md]["m2"][CONTROL]["max_dd"]) * 100
               for md in ("base", "calibrated")}
        g["G1_detail"] = {"cagr_delta_pp": cagr}
        g["G1"] = bool(all(_g1_ok(pre[md]["m2"][arm]["cagr"], pre[md]["m2"][arm]["calmar"],
                                  pre[md]["m2"][arm]["max_dd"], pre[md]["m2"][CONTROL]["cagr"],
                                  pre[md]["m2"][CONTROL]["calmar"], pre[md]["m2"][CONTROL]["max_dd"])
                           for md in ("base", "calibrated")))
        g["G2_detail"] = {"max_dd_delta_pp": ddn}
        g["G2"] = bool(all(ddn[md] <= G2_MAX_DD_PP + 1e-9 for md in ddn))
        g3 = {md: (pre[md]["m2"][arm]["2017_end"]["cagr"] - pre[md]["m2"][CONTROL]["2017_end"]["cagr"]) * 100
              for md in ("base", "calibrated")}
        g["G3_detail"] = {"cagr_2017_delta_pp": g3}
        g["G3"] = bool(all(g3[md] >= G3_MIN_2017_PP - 1e-9 for md in g3))
        sp = {}
        for name in ("P1", "P2", "P3"):
            sp[name] = (pre["base"]["m2"][arm]["subperiods"][name]["cagr"]
                        - pre["base"]["m2"][CONTROL]["subperiods"][name]["cagr"]) * 100
        n_better = sum(1 for v in sp.values() if v > 0)
        g["G4_detail"] = {"subperiod_delta_pp": sp, "n_better": n_better}
        g["G4"] = bool(n_better >= G4_MIN_SUBPERIODS)
        g5 = {md: boot_ref["pre"][md][arm]["diff_vs_C0_median_pp"] for md in ("base", "calibrated")}
        g["G5_detail"] = {"bootstrap_median_pp": g5, "required_pp": G5_MIN_MEDIAN_PP,
                          "note": "pre-registered as non-inferiority for tax-timing arms"}
        g["G5"] = bool(all(v >= G5_MIN_MEDIAN_PP for v in g5.values()))
        g6 = {md: (pre[md]["m1"][arm]["cagr"] - pre[md]["m1"][CONTROL]["cagr"]) * 100
              for md in ("base", "calibrated")}
        g["G6_detail"] = {"cagr_delta_m1_pp": g6}
        g["G6"] = bool(all(_g1_ok(pre[md]["m1"][arm]["cagr"], pre[md]["m1"][arm]["calmar"],
                                  pre[md]["m1"][arm]["max_dd"], pre[md]["m1"][CONTROL]["cagr"],
                                  pre[md]["m1"][CONTROL]["calmar"], pre[md]["m1"][CONTROL]["max_dd"])
                           for md in ("base", "calibrated")))
        cells = {wn: {"delta_cagr_pp": res["oos"][wn][arm]["delta_cagr_pp"],
                      "delta_max_dd_pp": res["oos"][wn][arm]["delta_max_dd_pp"]} for wn in bh.OOS_WINDOWS}
        med = float(np.median([c["delta_cagr_pp"] for c in cells.values()]))
        n_bad = sum(1 for c in cells.values()
                    if c["delta_cagr_pp"] < G7_MIN_WINDOW_CAGR_PP or c["delta_max_dd_pp"] > G7_MAX_WINDOW_DD_PP)
        worst_dd = max(c["delta_max_dd_pp"] for c in cells.values())
        g["G7_detail"] = {"cells": cells, "median_delta_cagr_pp": med, "n_windows_bad": n_bad,
                          "worst_delta_dd_pp": worst_dd}
        g["G7"] = bool(med >= G7_MIN_MEDIAN_PP - 1e-9 and n_bad <= G7_MAX_WINDOWS_BAD
                       and worst_dd <= G7_MAX_DD_HARD_PP + 1e-9)
        g8 = res["oos_boot"]["pooled"][arm]["diff_vs_C0_median_pp"]
        g["G8_detail"] = {"oos_bootstrap_median_pp": g8}
        g["G8"] = bool(g8 >= G8_MIN_MEDIAN_PP - 1e-9)
        t_arm = pre["base"]["m2"][arm]["turnover_annual"]
        t_k = pre["base"]["m2"][CONTROL]["turnover_annual"]
        g["G9_detail"] = {"turnover_annual": t_arm, "turnover_K": t_k, "ratio": t_arm / t_k}
        g["G9"] = bool(t_arm <= G9_MAX_TURNOVER_MULT * t_k + 1e-9)
        a1 = {wn: (win[wn][arm]["after_tax_cagr"] - win[wn][CONTROL]["after_tax_cagr"]) * 100
              for wn in TAX_WINDOWS}
        g["A1_detail"] = {"delta_after_tax_pp": a1}
        g["A1"] = bool(all(v > A1_MIN_DELTA_PP for v in a1.values()))
        a2 = {}
        for mode in ("progressive", "flat"):
            c = boot_ref["tax"][GATE_CAPITAL][mode][f"{arm}_vs_{CONTROL}"]
            a2[mode] = {"median_delta_pp": c["median_delta_cagr_pp"], "share_gt_0": c["share_gt_0"],
                        "mean_delta_pp": c["mean_delta_cagr_pp"], "p05_pp": c["p05_delta_cagr_pp"],
                        "p95_pp": c["p95_delta_cagr_pp"],
                        "pass": bool(c["median_delta_cagr_pp"] >= A2_MIN_MEDIAN_PP
                                     and c["share_gt_0"] >= A2_MIN_SHARE)}
        g["A2_detail"] = {"cells": a2, "required_median_pp": A2_MIN_MEDIAN_PP,
                          "required_share_gt_0": A2_MIN_SHARE}
        g["A2"] = bool(all(v["pass"] for v in a2.values()))
        g["pass_all"] = bool(all(g[k] for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9",
                                                "A1", "A2")))
        g["failed"] = sorted(k for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "A1", "A2")
                             if not g[k])
        det[arm] = g
    passers = [a for a in GATED if det[a]["pass_all"]]

    def rank(a):
        return (boot_ref["tax"][GATE_CAPITAL]["progressive"][f"{a}_vs_{CONTROL}"]["median_delta_cagr_pp"],
                win["2000_03_to_2026_09"][a]["after_tax_cagr"]
                - win["2000_03_to_2026_09"][CONTROL]["after_tax_cagr"])

    if passers:
        selected = max(passers, key=rank)
        reason = f"{selected} passes G1-G9 and A1-A2 and has the highest B1-recent $100k bootstrap median"
    else:
        selected = None
        reason = ("no arm clears every gate: "
                  + "; ".join(f"{a}: {','.join(det[a]['failed'])}" for a in GATED) + " -> K stays")
    return {"arms": det, "selected": selected, "reason": reason,
            "gate_table": {
                "G1": f"pre-tax m=2 CAGR >= K (or Calmar >= {CALMAR_MULT} x K with CAGR >= K - 0.5 pp), base+cal",
                "G2": f"pre-tax max DD <= K + {G2_MAX_DD_PP} pp, base+cal",
                "G3": f"2017+ CAGR >= K - {abs(G3_MIN_2017_PP)} pp, base+cal",
                "G4": f"CAGR > K in >= {G4_MIN_SUBPERIODS} of 3 sub-periods (base)",
                "G5": f"pre-tax clean bootstrap (seed {SEED}, block {BLOCK}, 5,000 paths) median >= "
                      f"{G5_MIN_MEDIAN_PP} pp (non-inferiority), base+cal",
                "G6": "G1 at m = 1, base+cal",
                "G7": "six OOS windows: median dCAGR >= 0; <= 1 of 6 bad; dDD never > 8 pp",
                "G8": "pooled M1-M5 OOS bootstrap median >= 0",
                "G9": f"turnover <= {G9_MAX_TURNOVER_MULT} x K (round trips included)",
                "A1": "after tax $100k: delta vs K > 0 in all three windows",
                "A2": f"clean after-tax bootstrap $100k, B1-recent and B2: median >= 0 and share > 0 >= {A2_MIN_SHARE:.0%}",
                "select": "highest B1-recent $100k after-tax bootstrap median among the passers; "
                          "tie-break the 2000-2026 $100k after-tax delta; Y4 report-only"},
            "report_only": {a: {"after_tax_100k_delta_pp": {
                wn: (win[wn][a]["after_tax_cagr"] - win[wn][CONTROL]["after_tax_cagr"]) * 100
                for wn in TAX_WINDOWS},
                "boot_b1_median_pp": boot_ref["tax"][GATE_CAPITAL]["progressive"][
                    f"{a}_vs_{CONTROL}"]["median_delta_cagr_pp"],
                "boot_b2_median_pp": boot_ref["tax"][GATE_CAPITAL]["flat"][
                    f"{a}_vs_{CONTROL}"]["median_delta_cagr_pp"]} for a in REPORT_ONLY}}


# ----------------------------------------------------------------------------- run
def run() -> dict:
    print("pool diagnostic (control only, main window, $100k)", flush=True)
    diag = pool_diagnostic()
    print(f"  {diag['n_years']} years, {diag['n_loss_years']} loss years, "
          f"pool {diag['pool_sum_try']:.0f} TRY", flush=True)
    print("pre-tax main window (6 arms x 2 financings x m = 1/2/4)", flush=True)
    pre, pre_usd = pre_tax_main(STAGE_ARMS)
    print("after-tax windows ($100k gate + $1M)", flush=True)
    win = after_tax_windows(tuple(TAX_CAPITALS), STAGE_ARMS)
    print("cost stress m = 4", flush=True)
    cost = cost_stress(STAGE_ARMS)
    print("OOS windows", flush=True)
    oos = oos_stages(STAGE_ARMS)
    print("OOS pooled bootstrap (M1-M5)", flush=True)
    oos_boot = oos_bootstrap(STAGE_ARMS)
    print(f"clean bootstrap (seed {SEED}, block {BLOCK}, {bh.PRE_BOOT_PATHS} pre-tax paths)", flush=True)
    boot_ref = boot_clean()
    print("rolling January starts (report only)", flush=True)
    starts = rolling_starts(STAGE_ARMS)
    print("year-end round-trip log", flush=True)
    ylog = yearend_log()
    res = {"campaign": "tax_budget_v1", "control": CONTROL, "arms": list(ARMS),
           "reference_arm": REFERENCE,
           "params": {"seed": SEED, "block": BLOCK, "m": M, "m_stress": M_STRESS,
                      "gate_capital": GATE_CAPITAL, "nav_cap_frac": NAV_CAP_FRAC,
                      "headroom_trigger_frac": HEADROOM_TRIGGER_FRAC, "tariff_top_try": TARIFF_TOP,
                      "g5_min_median_pp": G5_MIN_MEDIAN_PP, "a2_min_share": A2_MIN_SHARE,
                      "tol": TOL},
           "pool_diagnostic": diag, "pre": pre, "pre_usd": pre_usd, "windows": win, "cost": cost,
           "oos": oos, "oos_boot": oos_boot, "boot_ref": boot_ref, "rolling_starts": starts,
           "yearend_log": ylog}
    print("reference reproduction checks", flush=True)
    res["reference_check"] = reference_checks(res)
    res["gates"] = gates(res)
    return res


def _fmt(x):
    return "-" if x is None else f"{x:+.3f}"


def summary_lines(res):
    pre, win, boot_ref, g = res["pre"], res["windows"][GATE_CAPITAL], res["boot_ref"], res["gates"]
    L = []
    L.append(f"tax_budget_v1 | annual tax-budget execution on production K | control K = A1u + H2 "
             f"| main window {pre['window']['start']} -> {pre['window']['end']} "
             f"({pre['window']['sessions']} sessions)")
    L.append("arms: K (control) + Y1 loss-pool fill + Y2 bracket-headroom fill + Y3 no-new-trade "
             "year reallocation + Y4 = Y1+Y3 (report only); reference row R2")
    rc = res["reference_check"]
    L.append(f"reference reproduction vs band_h2_combo_v1/RESULTS.json: {rc['cells']} cells, "
             f"{rc['n_failures']} outside tolerance, max|diff| {rc['max_abs_diff']:.2e} -> "
             f"{'PASS' if rc['pass'] else 'FAIL'}")
    d = res["pool_diagnostic"]
    L.append(f"pool diagnostic (K, $100k, progressive): {d['n_years']} year-ends, "
             f"{d['n_loss_years']} loss years {d['loss_years']}, destroyed pool "
             f"{d['pool_sum_try']:,.0f} TRY, Y2 trigger available in {d['n_years_headroom_trigger']} "
             f"years (cap binds in {d['n_years_cap_binds']})")
    L.append("")
    L.append("per arm (pre-tax main window m = 2; after tax $100k, base, m = 2, liquidated):")
    L.append("  arm   CAGR base  CAGR cal   maxDD base  maxDD cal   turn/yr   sells/yr  rt/yr | "
             "dAT2000  dAT2010  dAT2017 (pp vs K) | pre-boot d B/C | B1 d/share   B2 d/share | OOS med d")
    for arm in STAGE_ARMS:
        p_b, p_c = pre["base"]["m2"][arm], pre["calibrated"]["m2"][arm]
        b1 = boot_ref["tax"][GATE_CAPITAL]["progressive"].get(f"{arm}_vs_{CONTROL}")
        b2 = boot_ref["tax"][GATE_CAPITAL]["flat"].get(f"{arm}_vs_{CONTROL}")
        rt = win["2000_03_to_2026_09"][arm]["n_yearend_round_trips"] / pre["window"]["years"]
        L.append(
            f"  {arm:<5} {p_b['cagr'] * 100:7.2f}%  {p_c['cagr'] * 100:7.2f}%  "
            f"{p_b['max_dd'] * 100:9.2f}%  {p_c['max_dd'] * 100:9.2f}%  {p_b['turnover_annual']:8.2f}  "
            f"{p_b['sells_per_year']:7.2f}  {rt:5.2f} | "
            f"{_fmt((win['2000_03_to_2026_09'][arm]['after_tax_cagr'] - win['2000_03_to_2026_09'][CONTROL]['after_tax_cagr']) * 100)}  "
            f"{_fmt((win['2010_01_to_end'][arm]['after_tax_cagr'] - win['2010_01_to_end'][CONTROL]['after_tax_cagr']) * 100)}  "
            f"{_fmt((win['2017_01_to_end'][arm]['after_tax_cagr'] - win['2017_01_to_end'][CONTROL]['after_tax_cagr']) * 100)} | "
            f"{_fmt(boot_ref['pre']['base'][arm]['diff_vs_C0_median_pp'])}/{_fmt(boot_ref['pre']['calibrated'][arm]['diff_vs_C0_median_pp'])} | "
            f"{_fmt(b1['median_delta_cagr_pp'] if b1 else None)}/{(b1['share_gt_0'] if b1 else 0):.3f}  "
            f"{_fmt(b2['median_delta_cagr_pp'] if b2 else None)}/{(b2['share_gt_0'] if b2 else 0):.3f} | "
            f"{_fmt(float(np.median([res['oos'][wn][arm]['delta_cagr_pp'] for wn in bh.OOS_WINDOWS])))}")
    L.append("")
    L.append("year-end round trips (main window, $100k, progressive, base, m = 2):")
    for arm in ("Y1", "Y2", "Y4"):
        ev = win["2000_03_to_2026_09"][arm]
        L.append(f"  {arm}: {ev['n_yearend_round_trips']} firings, notional "
                 f"${ev['yearend_notional_usd']:,.0f}, turnover/yr {ev['turnover_annual']:.3f}, "
                 f"sells/yr {ev['sells_per_year']:.2f}, cap-binding "
                 f"{sum(1 for e in res['yearend_log'][arm] if e['cap_binding'])}/{ev['n_yearend_round_trips']}")
    L.append("")
    L.append("gates vs K (G1-G9 pre-tax, A1-A2 after tax; Y4 report-only):")
    L.append("  arm   " + "  ".join(f"{k:>4}" for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "A1", "A2", "ALL")))
    for arm in GATED:
        gg = g["arms"][arm]
        L.append(f"  {arm:<5} " + "  ".join(f"{('P' if gg[k] else 'F'):>4}" for k in
                                             ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "A1", "A2", "pass_all")))
        L.append(f"        dCAGR m2 base/cal {_fmt(gg['G1_detail']['cagr_delta_pp']['base'])}/{_fmt(gg['G1_detail']['cagr_delta_pp']['calibrated'])} pp; "
                 f"dDD {_fmt(gg['G2_detail']['max_dd_delta_pp']['base'])}/{_fmt(gg['G2_detail']['max_dd_delta_pp']['calibrated'])} pp; "
                 f"d2017 {_fmt(gg['G3_detail']['cagr_2017_delta_pp']['base'])}/{_fmt(gg['G3_detail']['cagr_2017_delta_pp']['calibrated'])} pp; "
                 f"G4 {gg['G4_detail']['n_better']}/3; G5 {_fmt(gg['G5_detail']['bootstrap_median_pp']['base'])}/{_fmt(gg['G5_detail']['bootstrap_median_pp']['calibrated'])} pp; "
                 f"G7 median {_fmt(gg['G7_detail']['median_delta_cagr_pp'])} pp ({gg['G7_detail']['n_windows_bad']} bad); "
                 f"G8 {_fmt(gg['G8_detail']['oos_bootstrap_median_pp'])} pp; G9 ratio {gg['G9_detail']['ratio']:.3f}; "
                 f"A1 {min(gg['A1_detail']['delta_after_tax_pp'].values()):+.3f}..{max(gg['A1_detail']['delta_after_tax_pp'].values()):+.3f} pp; "
                 f"A2 B1 {_fmt(gg['A2_detail']['cells']['progressive']['median_delta_pp'])}/{gg['A2_detail']['cells']['progressive']['share_gt_0']:.3f} "
                 f"B2 {_fmt(gg['A2_detail']['cells']['flat']['median_delta_pp'])}/{gg['A2_detail']['cells']['flat']['share_gt_0']:.3f}")
    L.append("")
    L.append(f"SELECTED: {g['selected'] if g['selected'] else 'none'} -- {g['reason']}")
    L.append("")
    L.append("six OOS windows (pre-tax, calibrated, m = 2, R-USD) -- delta CAGR vs K (pp):")
    L.append("  window            K CAGR   K maxDD  " + "  ".join(f"{a:>7}" for a in STAGE_ARMS if a != CONTROL))
    for wn in bh.OOS_WINDOWS:
        row = res["oos"][wn]
        L.append(f"  {wn:<16} {row[CONTROL]['cagr'] * 100:7.2f}% {row[CONTROL]['max_dd'] * 100:8.2f}%  "
                 + "  ".join(f"{row[a]['delta_cagr_pp']:+7.3f}" for a in STAGE_ARMS if a != CONTROL))
    L.append("")
    pivot = "  ".join(f"{a}: {res['oos_boot']['pooled'][a]['diff_vs_C0_median_pp']:+.3f}/{res['oos_boot']['pooled'][a]['share_gt_0']:.3f}"
                      for a in STAGE_ARMS if a != CONTROL)
    L.append(f"oos pooled bootstrap (G8, median pp / share>0): {pivot}")
    L.append("")
    L.append("after tax, $100k liquidated (progressive, base, m = 2) and the m = 4 cost stress:")
    for wn in TAX_WINDOWS:
        L.append(f"  {wn}: " + " | ".join(
            f"{a} {win[wn][a]['after_tax_cagr'] * 100:6.3f}% ({_fmt((win[wn][a]['after_tax_cagr'] - win[wn][CONTROL]['after_tax_cagr']) * 100)}pp) tax ${win[wn][a]['tax_usd']:,.0f}"
            for a in STAGE_ARMS))
        L.append("    cost stress m=4: " + " ".join(
            f"{a} {res['cost'][wn][a]['after_tax_cagr'] * 100:.3f}% ({_fmt((res['cost'][wn][a]['after_tax_cagr'] - res['cost'][wn][CONTROL]['after_tax_cagr']) * 100)}pp)"
            for a in STAGE_ARMS))
    L.append("")
    b1 = boot_ref["tax"][GATE_CAPITAL]["progressive"]
    b2 = boot_ref["tax"][GATE_CAPITAL]["flat"]
    L.append("clean bootstrap seed 20261011 block 20, $100k (1,000 paths) -- vs K:")
    L.append("  arm   B1 med    B1 mean   B1 share  B1 p05/p95   | B2 med    B2 share  | pre-tax base/cal med")
    for arm in STAGE_ARMS:
        r1 = b1.get(f"{arm}_vs_{CONTROL}")
        r2 = b2.get(f"{arm}_vs_{CONTROL}")
        L.append(f"  {arm:<5} {_fmt(r1['median_delta_cagr_pp']) if r1 else '  -   '}  "
                 f"{_fmt(r1['mean_delta_cagr_pp']) if r1 else '  -   '}  "
                 f"{(r1['share_gt_0'] if r1 else 0):8.3f}  "
                 f"{(_fmt(r1['p05_delta_cagr_pp']) + '/' + _fmt(r1['p95_delta_cagr_pp'])) if r1 else '-':>12} | "
                 f"{_fmt(r2['median_delta_cagr_pp']) if r2 else '  -   '}  {(r2['share_gt_0'] if r2 else 0):8.3f} | "
                 f"{_fmt(boot_ref['pre']['base'][arm]['diff_vs_C0_median_pp'])}/{_fmt(boot_ref['pre']['calibrated'][arm]['diff_vs_C0_median_pp'])}")
    L.append("")
    st = res["rolling_starts"]
    for arm in STAGE_ARMS:
        if arm == CONTROL:
            continue
        ds = [st[y][arm]["delta_after_tax_pp"] for y in st if "delta_after_tax_pp" in st[y][arm]]
        L.append(f"rolling January starts (report only) {arm}: n {len(ds)}, median {np.median(ds):+.3f} pp, "
                 f"min {min(ds):+.3f}, max {max(ds):+.3f}, >0 in {sum(1 for x in ds if x > 0)}/{len(ds)}")
    L.append("")
    wer = win["2000_03_to_2026_09"]
    for arm in ("Y1", "Y2", "Y4"):
        L.append(f"{arm} year-end round trips (main window, $100k): {wer[arm]['n_yearend_round_trips']} firings, "
                 f"notional ${wer[arm]['yearend_notional_usd']:,.0f}, "
                 f"sells/yr {wer[arm]['sells_per_year']:.2f} vs K {wer[CONTROL]['sells_per_year']:.2f}, "
                 f"relief {wer[arm]['index_relief_try']:,.0f} TRY vs K {wer[CONTROL]['index_relief_try']:,.0f}")
    return L


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--smoke":
        s = smoke()
        for k, v in s["checks"].items():
            print(f"  {'ok ' if v else 'FAIL'} {k}")
        print(f"smoke {'PASS' if s['pass'] else 'FAIL'}")
        sys.exit(0 if s["pass"] else 1)
    if mode == "--harness":
        h = harness()
        print(f"harness: {h['cells']} cells, max|diff| {h['max_abs_diff']:.2e}, tol {h['tol']:.0e} -> "
              f"{'PASS' if h['pass'] else 'FAIL'}")
        for f in h["failures"][:20]:
            print(f"  FAIL {f['cell']}: mine {f['mine']!r} ref {f['ref']!r} diff {f['diff']:.3e}")
        sys.exit(0 if h["pass"] else 1)
    if mode == "--run":
        res = run()
        OUT.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
        L = summary_lines(res)
        SUMMARY.write_text("\n".join(L) + "\n")
        print("\n".join(L))
        return
    print(__doc__)
    sys.exit(2)


if __name__ == "__main__":
    main()
