#!/usr/bin/env python3
"""tax_hedge_v1: reduce Turkish tax by hedging exposure changes instead of trimming QLD*.

Offline. Reuses after_tax_tr_v1 / riskoff_after_tax_v1 multi-account tax model.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import importlib.util
import json
import math
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


trv2 = imp("trv2", ROOT / "research/trend_robustness_v2/screen.py")
lf = imp("lf", ROOT / "research/leverage_frontier_v1/screen.py")
v3 = imp("v3", ROOT / "research/riskoff_rotation_v3/screen.py")

M = 2
COST = trv2.COST_PER_SIDE * M
BAND = trv2.BAND
CAP = trv2.CAP
START = v3.START
FIN_SPREAD = 0.0
FX_CSV = ROOT / "data/historical/after_tax_tr_v1_usdtry_cbrt.csv"
UFE_CSV = ROOT / "data/historical/after_tax_tr_v1_yiufe.csv"
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

TARIFF_2026 = ((190_000.0, 0.15), (400_000.0, 0.20), (1_000_000.0, 0.27), (5_300_000.0, 0.35), (math.inf, 0.40))
DIV_THRESHOLD_2026 = 22_000.0
ANCHOR = "2025-10"
US_DIV_WHT = 0.20
INDEX_MIN_RISE = 0.10
CAPITALS = (10_000.0, 100_000.0, 1_000_000.0)
WINDOWS = {
    "2000_03_to_2026_09": ("2000-03-09", "2026-09-29"),
    "2010_01_to_end": ("2010-01-04", "2026-09-29"),
    "2017_01_to_end": ("2017-01-03", "2026-09-29"),
}
ARM_PARAMS = {
    "H1": {"factor": 2.0 / 3.0, "long_mult": 3.0, "hedge_mult": -3.0,
           "long_exp": 0.0084, "hedge_exp": 0.0095},
    "H2": {"factor": 0.98, "long_mult": 2.0, "hedge_mult": -2.0,
           "long_exp": 0.0095, "hedge_exp": 0.0095},
}

TOL = 1e-4


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def prev_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


class Market:
    def __init__(self):
        days, arr, y, y10 = v3.load_main()
        self.days = days
        self.arr = arr
        self.y = y
        self.y10 = y10
        fd, fv = [], []
        with FX_CSV.open() as fh:
            for r in csv.DictReader(fh):
                fd.append(r["date"])
                fv.append(float(r["usdtry_forex_buying"]))
        self.fx = np.array([fv[bisect.bisect_right(fd, d) - 1] for d in days])
        with UFE_CSV.open() as fh:
            self.ufe = {r["month"]: float(r["yiufe_index"]) for r in csv.DictReader(fh)}
        self.scale_cache = {}

    def scale(self, year: int) -> float:
        if year not in self.scale_cache:
            self.scale_cache[year] = self.ufe[f"{year - 1}-10"] / self.ufe[ANCHOR]
        return self.scale_cache[year]


def tariff_tax(base: float, scale: float) -> float:
    tax, lo = 0.0, 0.0
    for hi, rate in TARIFF_2026:
        hi = hi * scale if not math.isinf(hi) else math.inf
        if base <= lo:
            break
        tax += (min(base, hi) - lo) * rate
        lo = hi
    return tax


class Regime:
    def __init__(self, name, kind, rate=0.0, indexation=True, index_base="prev"):
        self.name, self.kind, self.rate, self.indexation, self.index_base = name, kind, rate, indexation, index_base

    def tax(self, base, year, mkt):
        if self.kind == "none" or base <= 0:
            return 0.0
        if self.kind == "flat":
            return base * self.rate
        return tariff_tax(base, mkt.scale(year))


class BaseAccount:
    def __init__(self, mkt, regime, cash, cashbox=None):
        self.mkt = mkt
        self.rg = regime
        if cashbox is None:
            self._cashbox = {"v": float(cash)}
        else:
            self._cashbox = cashbox
            self._cashbox["v"] = float(cash)
        self.units = 0.0
        self.lots = []
        self.year_gain = defaultdict(float)
        self.year_div = defaultdict(float)
        self.year_wht = defaultdict(float)
        self.gain_by_reason = defaultdict(float)
        self.pos_gain_by_reason = defaultdict(float)
        self.neg_gain_by_reason = defaultdict(float)
        self.index_relief_try = 0.0
        self.tax_due = {}
        self.tax_paid_try = defaultdict(float)
        self.tax_paid_usd = 0.0
        self.wht_usd = 0.0
        self.fees = 0.0

    @property
    def cash(self):
        return self._cashbox["v"]

    @cash.setter
    def cash(self, v):
        self._cashbox["v"] = float(v)

    def buy(self, i, price, usd, fee):
        if usd <= 0 or price <= 0:
            return
        u = usd / price
        self.cash -= usd + fee
        self.fees += fee
        self.units += u
        self.lots.append([u, (usd + fee) / u, self.mkt.fx[i], self.mkt.days[i][:7]])

    def sell(self, i, price, units, fee, reason):
        units = min(units, self.units)
        if units <= 0 or price <= 0:
            return 0.0
        gross = units * price
        self.cash += gross - fee
        self.fees += fee
        fx_s = self.mkt.fx[i]
        sale_month = self.mkt.days[i][:7]
        year = int(self.mkt.days[i][:4])
        per_unit = (gross - fee) / units
        left, gain = units, 0.0
        while left > 0 and self.lots:
            lot = self.lots[0]
            take = min(left, lot[0])
            proceeds = take * per_unit * fx_s
            cost = take * lot[1] * lot[2]
            g = proceeds - cost
            if self.rg.indexation and g > 0:
                base_m = (prev_month(lot[3]) if self.rg.index_base == "prev" else lot[3])
                rise = self.mkt.ufe[prev_month(sale_month)] / self.mkt.ufe[base_m] - 1
                if rise >= INDEX_MIN_RISE:
                    gi = max(0.0, proceeds - cost * (1 + rise))
                    self.index_relief_try += g - gi
                    g = gi
            gain += g
            if g >= 0:
                self.pos_gain_by_reason[reason] += g
            else:
                self.neg_gain_by_reason[reason] += g
            lot[0] -= take
            left -= take
            if lot[0] <= take * 1e-12:
                self.lots.pop(0)
        self.units -= units
        if self.units <= 1e-12 * max(1.0, units) or not self.lots:
            self.units, self.lots = 0.0, []
        self.year_gain[year] += gain
        self.gain_by_reason[reason] += gain
        return gain

    def assess(self, year):
        g = max(0.0, self.year_gain.get(year, 0.0))
        d = self.year_div.get(year, 0.0)
        if self.rg.kind == "none":
            return 0.0
        if d <= DIV_THRESHOLD_2026 * self.mkt.scale(year):
            d = 0.0
        base = g + d
        tax = self.rg.tax(base, year, self.mkt)
        if d > 0 and base > 0:
            tax -= min(self.year_wht.get(year, 0.0), tax * d / base)
        return max(0.0, tax)

    def outstanding(self):
        return sum(v - self.tax_paid_try[y] for y, v in self.tax_due.items())

    def dividend(self, i, per_unit):
        return 0.0


def instalments(days, i0, i1):
    out = defaultdict(list)
    for y in range(int(days[i0][:4]), int(days[i1][:4]) + 1):
        for md, part in (("03-31", 0), ("07-31", 1)):
            k = bisect.bisect_left(days, f"{y}-{md}")
            if i0 <= k <= i1:
                out[k].append((y - 1, part))
    return out


def synth(mult, exp, q_open, q_close, q_div, y):
    prev = q_close[:-1]
    rcc = (q_close[1:] + q_div[1:]) / prev - 1
    ron = (q_open[1:] + q_div[1:]) / prev - 1
    fin = y[:-1]
    cost = (abs(mult) - 1) * fin if abs(mult) > 1 else 0.0
    g = 1 + mult * rcc - (exp + cost) / 252
    close = np.concatenate([[1.0], np.cumprod(g)])
    opn = np.concatenate([[1.0], close[:-1] * (1 + mult * ron)])
    return opn, close


def build_prod_target(arr, y):
    up = v3.e2_up(arr["QQQ"][1])
    eq_o, eq_c = lf.synthetic_L(arr["QQQ"][0], arr["QQQ"][1], arr["QQQ"][2], y, 2.0, lf.EXPENSE[2.0], 0.0)
    eq_vol = trv2.vol_n(eq_c)[0]
    vt = trv2.vt_of(eq_vol)
    raw = up.astype(float) * vt
    raw = np.where(raw > CAP, CAP, raw)
    return raw, eq_o, eq_c


def e2_on_series(prod_target):
    return prod_target > 0


def run_hedge(arm, regime_name, capital, a, b, mkt, days, arr, y, y10, long_o, long_c, hedge_o, hedge_c, factor, long_mult, hedge_mult, prod_target):
    regime = Regime("progressive", "tariff") if regime_name == "progressive" else Regime("pre_tax", "none")
    i0 = bisect.bisect_left(days, a)
    i1 = bisect.bisect_right(days, b) - 1

    cashbox = {"v": float(capital)}
    accL = BaseAccount(mkt, regime, 0.0, cashbox)
    accH = BaseAccount(mkt, regime, 0.0, cashbox)
    cashbox["v"] = float(capital)

    pays = instalments(days, i0, i1)
    nav = np.empty(i1 - i0 + 2)
    nav[0] = capital
    trades = 0
    tax_sales = 0
    sim_long_o, sim_long_c = long_o, long_c
    sim_hedge_o, sim_hedge_c = hedge_o, hedge_c
    both_hold_days = 0

    for i in range(i0, i1 + 1):
        yyr = int(days[i][:4])
        if i > i0 and yyr != int(days[i - 1][:4]):
            prevy = yyr - 1
            total_assess = accL.assess(prevy) + accH.assess(prevy)
            accL.tax_due[prevy] = total_assess
            accH.tax_due[prevy] = 0.0
        # tax payments
        for ty, part in pays.get(i, ()):
            total_due = accL.tax_due.get(ty, 0.0) + accH.tax_due.get(ty, 0.0)
            if ty not in accL.tax_due:
                accL.tax_due[ty] = total_due
            amt = accL.tax_due[ty] / 2 if part == 0 else accL.tax_due[ty] - (accL.tax_paid_try.get(ty, 0.0) + accH.tax_paid_try.get(ty, 0.0))
            if amt <= 0:
                continue
            usd = amt / mkt.fx[i]
            if cashbox["v"] < usd:
                need = (usd - cashbox["v"]) / (1 - COST)
                usell = need / sim_long_o[i] if sim_long_o[i] > 0 else 0.0
                if usell > 0 and accL.units > 0:
                    accL.sell(i, sim_long_o[i], usell, usell * sim_long_o[i] * COST, "tax")
                    tax_sales += 1
            cashbox["v"] -= usd
            accL.tax_paid_try[ty] += amt
            accL.tax_paid_usd += usd

        pL = sim_long_o[i]
        pH = sim_hedge_o[i]
        cL = sim_long_c[i]
        cH = sim_hedge_c[i]
        uL = accL.units
        uH = accH.units
        nav_open = cashbox["v"] + uL * pL + uH * pH
        if nav_open <= 0:
            nav_open = cashbox["v"] if cashbox["v"] != 0 else 1.0
        wL = uL * pL / nav_open if nav_open > 0 else 0.0
        wH = uH * pH / nav_open if nav_open > 0 else 0.0
        cur_beta = long_mult * wL + hedge_mult * wH

        tgt_idx = i - 1
        if tgt_idx < 0:
            tgt_idx = 0
        if tgt_idx >= len(prod_target):
            tgt_idx = len(prod_target) - 1
        pt = float(prod_target[tgt_idx])
        e2_on = pt > 0
        desired_beta = (long_mult * factor * pt) if e2_on else 0.0

        if accL.units > 0 and accH.units > 0:
            both_hold_days += 1

        traded = False
        if not e2_on:
            if accL.units > 0:
                accL.sell(i, pL, accL.units, accL.units * pL * COST, "exit")
                traded = True
            if accH.units > 0:
                accH.sell(i, pH, accH.units, accH.units * pH * COST, "exit")
                traded = True
        else:
            gap = desired_beta - cur_beta
            if abs(gap) >= 0.005 or (uL <= 1e-12 and uH <= 1e-12):
                if gap > 0:
                    avail_hedge_beta = (-hedge_mult) * wH
                    if gap <= avail_hedge_beta + 1e-12:
                        units = (gap / abs(hedge_mult)) * nav_open / pH if pH > 0 else 0.0
                        if units > 0 and accH.units > 0:
                            accH.sell(i, pH, units, units * pH * COST, "resize")
                            traded = True
                    else:
                        if accH.units > 0:
                            accH.sell(i, pH, accH.units, accH.units * pH * COST, "resize")
                            traded = True
                        remaining_gap = gap - avail_hedge_beta
                        if remaining_gap > 1e-12:
                            units = (remaining_gap / long_mult) * nav_open / pL if pL > 0 else 0.0
                            if units > 0:
                                accL.buy(i, pL, units * pL, units * pL * COST)
                                traded = True
                elif gap < 0:
                    D = -gap
                    cash_cost_needed = (D / abs(hedge_mult)) * nav_open
                    if cash_cost_needed <= cashbox["v"] + 1e-9:
                        units = (D / abs(hedge_mult)) * nav_open / pH if pH > 0 else 0.0
                        if units > 0:
                            accH.buy(i, pH, units * pH, units * pH * COST)
                            traded = True
                    else:
                        units = cashbox["v"] / pH if pH > 0 else 0.0
                        if units > 0:
                            accH.buy(i, pH, units * pH, units * pH * COST)
                            traded = True
                        reduction1 = (abs(hedge_mult) * units * pH / nav_open) if nav_open > 0 else 0.0
                        rem = D - reduction1
                        if rem > 1e-12:
                            units2 = (rem / long_mult) * nav_open / pL if pL > 0 else 0.0
                            if units2 > 0 and accL.units > 0:
                                accL.sell(i, pL, units2, units2 * pL * COST, "resize")
                                traded = True
        if traded:
            trades += 1

        nav[i - i0 + 1] = cashbox["v"] + accL.units * cL + accH.units * cH

    # mtm (unliquidated) before final liquidation
    end_y = int(days[i1][:4])
    fx1 = mkt.fx[i1]
    accrued = (accL.assess(end_y) + accH.assess(end_y)) + (accL.outstanding() + accH.outstanding())
    mtm = nav[-1] - accrued / fx1

    gains_pre_liq = defaultdict(float)
    for acc in (accL, accH):
        for k, v in acc.gain_by_reason.items():
            gains_pre_liq[k] += v

    if accL.units > 0:
        accL.sell(i1, sim_long_c[i1], accL.units, accL.units * sim_long_c[i1] * COST, "liquidation")
    if accH.units > 0:
        accH.sell(i1, sim_hedge_c[i1], accH.units, accH.units * sim_hedge_c[i1] * COST, "liquidation")

    liq_tax = accL.assess(end_y) + accH.assess(end_y) + accL.outstanding() + accH.outstanding()
    liq = cashbox["v"] - liq_tax / fx1

    fx0 = mkt.fx[i0 - 1]
    yrs = trv2.calendar_years(days[i0 - 1], days[i1])

    all_reasons = defaultdict(float)
    for acc in (accL, accH):
        for k, v in acc.gain_by_reason.items():
            all_reasons[k] += v
    gain_long = float(sum(accL.gain_by_reason.values()))
    gain_hedge = float(sum(accH.gain_by_reason.values()))

    tax_total_try = (accL.tax_paid_try.__wrapped__ if hasattr(accL.tax_paid_try, "__wrapped__") else 0) if False else None
    tax_paid_try_total = sum(accL.tax_paid_try.values()) + sum(accH.tax_paid_try.values()) + liq_tax
    # simpler: reconstruct paid TRY (instalments already TRY) from arrays
    tax_paid_try_total = sum(accL.tax_paid_try.values()) + sum(accH.tax_paid_try.values()) + (liq_tax - (accL.assess(end_y) + accH.assess(end_y) - 0))
    # Actually tax_paid_try already includes assessed amounts paid; liq_tax includes unpaid end-year.
    # Keep consistent with after_tax: tax_total_try = paid TRY + liq_tax (where paid TRY are instalments net? riskoff used sum tax_paid_try + liq_tax)
    tax_paid_try_total = sum(accL.tax_paid_try.values()) + sum(accH.tax_paid_try.values()) + liq_tax

    tax_total_usd = accL.tax_paid_usd + accH.tax_paid_usd + liq_tax / fx1

    cg = lambda v0, v1: (v1 / v0) ** (1 / yrs) - 1

    return {
        "window": [days[i0], days[i1]],
        "years": yrs,
        "capital_usd": capital,
        "fx_start": float(fx0),
        "fx_end": float(fx1),
        "ending_nav_usd": float(nav[-1]),
        "max_dd_nav_usd": float(trv2.max_dd(nav.reshape(1, -1))[0]),
        "cagr_usd_liquidated": float(cg(capital, liq)),
        "cagr_try_liquidated": float(cg(capital * fx0, liq * fx1)),
        "terminal_usd_liquidated": float(liq),
        "cagr_usd_unliquidated": float(cg(capital, mtm)),
        "cagr_try_unliquidated": float(cg(capital * fx0, mtm * fx1)),
        "terminal_usd_unliquidated": float(mtm),
        "tax_total_try": float(tax_paid_try_total),
        "tax_total_usd_at_payment_fx": float(tax_total_usd),
        "tax_paid_per_year_usd": float(tax_total_usd / yrs),
        "sells_per_year": float((trades + tax_sales) / yrs),  # strategy + tax funding sales
        "strategy_trades": int(trades),
        "tax_funding_sales": int(tax_sales),
        "strategy_trades_per_year": float(trades / yrs),
        "fees_usd": float(accL.fees + accH.fees),
        "tax_by_year_try": {},
        "realised_gain_try_by_reason": {k: float(v) for k, v in sorted(all_reasons.items())},
        "realised_gain_try_by_instrument": {"long": gain_long, "hedge": gain_hedge},
        "indexation_relief_try": float(accL.index_relief_try + accH.index_relief_try),
        "both_legs_held_days": int(both_hold_days),
        "both_legs_held_share": float(both_hold_days / (i1 - i0 + 1)),
        "_nav": nav,
    }


def strip(r):
    return {k: v for k, v in r.items() if not k.startswith("_")}


REGIMES = {"progressive": "progressive", "pre_tax": "pre_tax"}


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--run", action="store_true")
    args = ap.parse_args()

    mkt = Market()
    days = mkt.days
    arr = mkt.arr
    y = mkt.y
    y10 = mkt.y10

    prod_target, eq_o, eq_c = build_prod_target(arr, y)

    # harness check via ewma (control C0)
    import importlib.util
    ew_spec = importlib.util.spec_from_file_location("ew_screen", ROOT / "research/ewma_band_v1/screen.py")
    ew = importlib.util.module_from_spec(ew_spec)
    ew_spec.loader.exec_module(ew)
    hc = ew.harness()
    print(json.dumps({"harness_pass": hc["pass"], "max_diff_pp": max(abs(v["diff_pp"]) for v in hc["deviation_checks"].values())}, indent=2))
    if not hc["pass"]:
        raise SystemExit("harness check failed")

    if args.check:
        # smoke synthetic random NAV no real run
        rng = np.random.default_rng(12345)
        lo = eq_c.copy() * (1 + rng.normal(0, 0.01, size=eq_c.shape))
        # not a full strategy run; just report synthetic generation ran
        print("smoke: synthetic arrays ok", lo.shape)
        return

    # pre-tax main window for H1/H2
    pre_tax_results = {}
    for arm in ("H1", "H2"):
        p = ARM_PARAMS[arm]
        long_o, long_c = synth(p["long_mult"], p["long_exp"], arr["QQQ"][0], arr["QQQ"][1], arr["QQQ"][2], y)
        hedge_o, hedge_c = synth(p["hedge_mult"], p["hedge_exp"], arr["QQQ"][0], arr["QQQ"][1], arr["QQQ"][2], y)
        r = run_hedge(arm, "pre_tax", 100000.0, days[START - 1 + 1 - 1 + 1], days[-1], mkt, days, arr, y, y10,
                      long_o, long_c, hedge_o, hedge_c, p["factor"], p["long_mult"], p["hedge_mult"], prod_target)
        # fix start date: production measured window start = days[START] ? ew uses days[START-1]. after_tax i0 by date. Use days[START-1]? main window start should 2000-03-09.
        pre_tax_results[arm] = strip(r)

    # proper pre-tax main window: rerun with correct start date
    pre_tax_results = {}
    main_start = "2000-03-09"
    main_end = "2026-09-29"
    for arm in ("H1", "H2"):
        p = ARM_PARAMS[arm]
        long_o, long_c = synth(p["long_mult"], p["long_exp"], arr["QQQ"][0], arr["QQQ"][1], arr["QQQ"][2], y)
        hedge_o, hedge_c = synth(p["hedge_mult"], p["hedge_exp"], arr["QQQ"][0], arr["QQQ"][1], arr["QQQ"][2], y)
        r = run_hedge(arm, "pre_tax", 100000.0, main_start, main_end, mkt, days, arr, y, y10,
                      long_o, long_c, hedge_o, hedge_c, p["factor"], p["long_mult"], p["hedge_mult"], prod_target)
        pre_tax_results[arm] = strip(r)

    # after-tax runs
    after_tax = {}
    for wn, (a, b) in WINDOWS.items():
        after_tax[wn] = {}
        for cap in CAPITALS:
            ck = f"usd_{int(cap)}"
            after_tax[wn][ck] = {}
            for arm in ("H1", "H2"):
                p = ARM_PARAMS[arm]
                long_o, long_c = synth(p["long_mult"], p["long_exp"], arr["QQQ"][0], arr["QQQ"][1], arr["QQQ"][2], y)
                hedge_o, hedge_c = synth(p["hedge_mult"], p["hedge_exp"], arr["QQQ"][0], arr["QQQ"][1], arr["QQQ"][2], y)
                r = run_hedge(arm, "progressive", float(cap), a, b, mkt, days, arr, y, y10,
                              long_o, long_c, hedge_o, hedge_c, p["factor"], p["long_mult"], p["hedge_mult"], prod_target)
                after_tax[wn][ck][arm] = strip(r)

    out = {
        "campaign": "tax_hedge_v1",
        "harness_check": hc,
        "pre_tax_main_base_m2": pre_tax_results,
        "after_tax": after_tax,
        "capitals": list(CAPITALS),
        "windows": WINDOWS,
        "arms": list(ARM_PARAMS.keys()),
        "status": "complete",
    }
    OUT.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")

    # summary
    L = ["tax_hedge_v1 summary", ""]
    for arm in ("H1", "H2"):
        r = pre_tax_results[arm]
        L.append(f"{arm} pre-tax main: CAGR {r['cagr_usd_liquidated']*100:.2f}% maxDD {r['max_dd_nav_usd']*100:.2f}% sells/yr {r['strategy_trades_per_year']:.2f} both-leg-days {r['both_legs_held_share']*100:.1f}%")
    for wn in WINDOWS:
        L.append(f"\n== {wn}")
        for cap in CAPITALS:
            ck = f"usd_{int(cap)}"
            for arm in ("H1", "H2"):
                r = after_tax[wn][ck][arm]
                L.append(f"  {arm} {ck}: after-tax CAGR {r['cagr_usd_liquidated']*100:.2f}% tax/yr ${r['tax_paid_per_year_usd']:,.0f} sells/yr {r['sells_per_year']:.2f}")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
