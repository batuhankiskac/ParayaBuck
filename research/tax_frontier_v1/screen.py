#!/usr/bin/env python3
"""tax_frontier_v1: which E2-family rule maximizes after-tax return."""
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

# constants
M = 2
COST = trv2.COST_PER_SIDE * M
BAND = trv2.BAND
CAP = trv2.CAP
START = v3.START
FX_CSV = ROOT / "data/historical/after_tax_tr_v1_usdtry_cbrt.csv"
UFE_CSV = ROOT / "data/historical/after_tax_tr_v1_yiufe.csv"
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

TARIFF_2026 = ((190_000.0, 0.15), (400_000.0, 0.20), (1_000_000.0, 0.27), (5_300_000.0, 0.35), (math.inf, 0.40))
DIV_THRESHOLD_2026 = 22_000.0
ANCHOR = "2025-10"
INDEX_MIN_RISE = 0.10
CAPITALS = (10_000.0, 100_000.0, 1_000_000.0)
WINDOWS = {
    "2000_03_to_2026_09": ("2000-03-09", "2026-09-29"),
    "2010_01_to_end": ("2010-01-01", "2026-09-29"),
    "2017_01_to_end": ("2017-01-03", "2026-09-29"),
}
ARMS = ("C0", "A1", "A2", "A3", "A4", "A5", "A6")
TRADE_MODE = {"C0": "prod", "A1": "flip", "A2": "downward", "A3": "quarter", "A4": "prod", "A5": "hold", "A6": "hold"}

# load data at import
DAYS, ARR, Y, Y10 = lf.load_all()
UP = v3.e2_up(ARR["QQQ"][1])
QQQ = ARR["QQQ"]
EQ2_O, EQ2_C = lf.synthetic_L(QQQ[0], QQQ[1], QQQ[2], Y, 2, lf.EXPENSE[2.0], 0.0)
QQ1_O, QQ1_C = lf.synthetic_L(QQQ[0], QQQ[1], QQQ[2], Y, 1, lf.EXPENSE[1.0], 0.0)
SPY1_O, SPY1_C = lf.synthetic_L(ARR["SPY"][0], ARR["SPY"][1], ARR["SPY"][2], Y, 1, lf.EXPENSE[1.0], 0.0)
EQ2_VOL = trv2.vol_n(EQ2_C)[0]
VT035 = lf.vt_weight(EQ2_VOL, 0.35)
T = len(DAYS)

# target arrays per arm (1-D over sessions)
TARGETS = {}
TARGETS["C0"] = UP.astype(float) * np.minimum(CAP, VT035)
TARGETS["A1"] = UP.astype(float) * CAP
TARGETS["A2"] = UP.astype(float) * np.minimum(CAP, VT035)
TARGETS["A3"] = UP.astype(float) * np.minimum(CAP, VT035)
TARGETS["A4"] = UP.astype(float) * CAP
TARGETS["A5"] = np.ones(T)
TARGETS["A6"] = np.ones(T)

PRICES = {
    "C0": (EQ2_O, EQ2_C), "A1": (EQ2_O, EQ2_C), "A2": (EQ2_O, EQ2_C), "A3": (EQ2_O, EQ2_C),
    "A4": (QQ1_O, QQ1_C), "A5": (QQ1_O, QQ1_C), "A6": (SPY1_O, SPY1_C),
}


class Market:
    def __init__(self):
        fd, fv = [], []
        with FX_CSV.open() as fh:
            for r in csv.DictReader(fh):
                fd.append(r["date"])
                fv.append(float(r["usdtry_forex_buying"]))
        self.fx = np.array([fv[bisect.bisect_right(fd, d) - 1] for d in DAYS])
        with UFE_CSV.open() as fh:
            self.ufe = {r["month"]: float(r["yiufe_index"]) for r in csv.DictReader(fh)}
        # days for Market are DAYS

    def scale(self, year: int) -> float:
        # uf cache via simple dict
        if not hasattr(self, "_sc"):
            self._sc = {}
        if year not in self._sc:
            self._sc[year] = self.ufe[f"{year - 1}-10"] / self.ufe[ANCHOR]
        return self._sc[year]

    days = DAYS


Market.DAYS = DAYS


# tax model copied from riskoff
def prev_month(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def tariff_tax(base, scale):
    tax, lo = 0.0, 0.0
    for hi, rate in TARIFF_2026:
        hi = hi * scale if not math.isinf(hi) else math.inf
        if base <= lo:
            break
        tax += (min(base, hi) - lo) * rate
        lo = hi
    return tax


class Regime:
    def __init__(self, name, kind):
        self.name, self.kind = name, kind

    def tax(self, base, year, mkt):
        if self.kind == "none" or base <= 0:
            return 0.0
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
        if usd <= 0:
            return
        u = usd / price
        self.cash -= usd + fee
        self.fees += fee
        self.units += u
        self.lots.append([u, (usd + fee) / u, self.mkt.fx[i], self.mkt.days[i][:7]])

    def sell(self, i, price, units, fee, reason):
        units = min(units, self.units)
        if units <= 0:
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
            if self.rg.kind != "none" and g > 0:
                base_m = prev_month(lot[3])
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


def instalments(days, i0, i1):
    out = defaultdict(list)
    for y in range(int(days[i0][:4]), int(days[i1][:4]) + 1):
        for md, part in (("03-31", 0), ("07-31", 1)):
            k = bisect.bisect_left(days, f"{y}-{md}")
            if i0 <= k <= i1:
                out[k].append((y - 1, part))
    return out


def run_strategy(arm, regime_name, capital, a, b, mkt):
    days_all = DAYS
    i0 = bisect.bisect_left(days_all, a)
    i1 = bisect.bisect_right(days_all, b) - 1
    if regime_name == "progressive":
        regime = Regime("progressive", "tariff")
    else:
        regime = Regime("pre_tax", "none")

    opens_arr, closes_arr = PRICES[arm]
    wants_arr = TARGETS[arm]
    mode = TRADE_MODE[arm]

    cashbox = {"v": float(capital)}
    account = BaseAccount(mkt, regime, 0.0, cashbox)
    cashbox["v"] = float(capital)

    pays = instalments(days_all, i0, i1)
    nav = np.empty(i1 - i0 + 2)
    nav[0] = capital
    trades = 0
    tax_sales = 0

    prev_tgt = None
    prev_quarter = None
    quarter_first = False

    for i in range(i0, i1 + 1):
        # year boundary assess
        if i > i0 and int(days_all[i][:4]) != int(days_all[i - 1][:4]):
            prevy = int(days_all[i][:4]) - 1
            acct_tax = account.assess(prevy)
            account.tax_due[prevy] = acct_tax

        # instalments
        for ty, part in pays.get(i, ()):
            if ty not in account.tax_due:
                account.tax_due[ty] = 0.0
            due = account.tax_due.get(ty, 0.0)
            amt_total = due / 2 if part == 0 else due - account.tax_paid_try.get(ty, 0.0)
            if amt_total <= 1e-9:
                continue
            usd = amt_total / mkt.fx[i]
            if cashbox["v"] < usd:
                need = (usd - cashbox["v"]) / (1 - COST)
                if account.units > 1e-12:
                    account.sell(i, float(opens_arr[i]), need / float(opens_arr[i]), need * COST, "tax")
                    tax_sales += 1
            cashbox["v"] -= usd
            account.tax_paid_try[ty] += amt_total
            account.tax_paid_usd += usd

        po = float(opens_arr[i])
        pc = float(closes_arr[i])
        held_units = account.units
        held_value = held_units * po
        nav_open = cashbox["v"] + held_value
        if nav_open <= 0:
            nav_open = cashbox["v"]
        w = (held_units * po) / nav_open if nav_open > 0 else 0.0

        tgt = float(wants_arr[i - 1]) if i > 0 else float(wants_arr[0])
        tgt = 0.0 if math.isnan(tgt) else tgt

        # quarter boundary
        qk = (days_all[i][:4], (int(days_all[i][5:7]) - 1) // 3)
        if qk != prev_quarter:
            quarter_first = True
            prev_quarter = qk
        else:
            quarter_first = False

        trend_flip = (prev_tgt is not None) and ((tgt == 0) != (prev_tgt == 0))

        # trade decision
        trade = False
        if mode == "prod":
            trade = abs(tgt - w) > BAND
        elif mode == "flip":
            if prev_tgt is None:
                trade = abs(tgt - w) > 1e-12
            else:
                trade = (tgt == 0) != (prev_tgt == 0)
        elif mode == "hold":
            if prev_tgt is None:
                trade = abs(tgt - w) > 1e-12
            else:
                trade = abs(tgt - prev_tgt) > 1e-12
        elif mode == "quarter":
            if trend_flip:
                trade = True
            elif quarter_first:
                trade = abs(tgt - w) > BAND
            else:
                # entries handled by trend_flip; downward drift only if flip
                trade = False
            if account.units == 0 and tgt > 0:
                trade = True
            if account.units > 0 and tgt == 0:
                trade = True
        elif mode == "downward":
            if tgt > w + BAND:
                trade = True
            elif tgt < w - BAND:
                if prev_tgt is not None and prev_tgt > 0 and tgt == 0:
                    trade = True
                elif tgt < 0.5 * max(w, 1e-12):
                    trade = True
            if account.units == 0 and tgt > 0:
                trade = True
            if account.units > 0 and tgt == 0:
                trade = True

        if account.units == 0 and tgt > 1e-12 and prev_tgt is None:
            trade = True
        if account.units > 1e-12 and tgt <= 0 and prev_tgt is not None and prev_tgt > 0:
            trade = True

        if trade:
            drift = abs(tgt - w)
            dsum = drift
            fee_total = nav_open * COST * dsum if dsum > 0 else 0.0
            nav_after_cost = nav_open - fee_total
            # single leg adjust units
            curr_units = account.units
            target_units = tgt * nav_after_cost / po if po > 0 else 0.0
            du = target_units - curr_units
            leg_fee = nav_open * COST * drift if drift > 0 else 0.0
            if du > 1e-12:
                account.buy(i, po, du * po, leg_fee)
            elif du < -1e-12:
                account.sell(i, po, -du, leg_fee, "resize")
            trades += 1

        # update nav at close
        nav[i - i0 + 1] = cashbox["v"] + account.units * pc

        prev_tgt = tgt

    # liquidation
    end_y = int(days_all[i1][:4])
    for _ in range(0):
        pass
    if account.units > 0:
        account.sell(i1, float(closes_arr[i1]), account.units, account.units * float(closes_arr[i1]) * COST, "liquidation")
    liq_tax = account.assess(end_y) + account.outstanding()
    fx1 = mkt.fx[i1]
    liq = cashbox["v"] - liq_tax / fx1

    ending_nav = nav[-1]
    mtm = ending_nav

    fx0 = mkt.fx[i0 - 1]
    fx1v = mkt.fx[i1]
    yrs = trv2.calendar_years(days_all[i0 - 1], days_all[i1])
    tax_total_try = sum(account.tax_paid_try.values()) + liq_tax
    tax_total_usd = account.tax_paid_usd + liq_tax / fx1v

    cg = lambda v0, v1: (v1 / v0) ** (1 / yrs) - 1
    all_reasons = dict(account.gain_by_reason)

    # max dd
    nav_arr = nav.reshape(1, -1)
    maxdd = float(trv2.max_dd(nav_arr)[0])

    return {
        "window": [days_all[i0], days_all[i1]],
        "years": yrs,
        "capital_usd": capital,
        "fx_start": float(fx0),
        "fx_end": float(fx1v),
        "ending_nav_usd": float(ending_nav),
        "max_dd_nav_usd": maxdd,
        "cagr_usd_liquidated": float(cg(capital, liq)),
        "cagr_try_liquidated": float(cg(capital * fx0, liq * fx1v)),
        "terminal_usd_liquidated": float(liq),
        "cagr_usd_unliquidated": float(cg(capital, mtm)),
        "cagr_try_unliquidated": float(cg(capital * fx0, mtm * fx1v)),
        "terminal_usd_unliquidated": float(mtm),
        "tax_total_try": float(tax_total_try),
        "tax_total_usd_at_payment_fx": float(tax_total_usd),
        "strategy_trades": int(trades),
        "tax_funding_sales": int(tax_sales),
        "fees_usd": float(account.fees),
        "realised_gain_try_by_reason": {k: float(v) for k, v in sorted(all_reasons.items())},
        "indexation_relief_try": float(account.index_relief_try),
        "_nav": nav,
    }


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args(argv)
    mkt = Market()
    # use global DAYS as mkt.days
    mkt.days = DAYS
    mkt.arrdays = DAYS

    # check C0 reproduction
    r = run_strategy("C0", "pre_tax", 1.0, "2000-03-09", "2026-09-29", mkt)
    nav = r["_nav"]
    dates = [DAYS[bisect.bisect_left(DAYS, "2000-03-09") - 1]] + DAYS[bisect.bisect_left(DAYS, "2000-03-09"):bisect.bisect_right(DAYS, "2026-09-29")]
    st_cagr = r["cagr_usd_liquidated"]  # pre-tax run regime none => liquidated = nav, no tax
    # Actually pre_tax regime sets tax 0, liq_tax 0, so cagr_liquidated = nav cagr
    print(json.dumps({"C0_pre_tax_cagr": r["cagr_usd_liquidated"], "C0_max_dd": r["max_dd_nav_usd"]}, indent=2))
    if not args.run:
        if args.check:
            pass
        return

    res = {"windows": {}}
    for wn, (a, b) in WINDOWS.items():
        wres = {}
        for cap in CAPITALS:
            ck = f"usd_{int(cap)}"
            for arm in ARMS:
                for rn in ("pre_tax", "progressive"):
                    r = run_strategy(arm, rn, cap, a, b, mkt)
                    wres.setdefault(ck, {}).setdefault(rn, {})[arm] = {k: v for k, v in r.items() if not k.startswith("_")}
        res["windows"][wn] = wres

    # pre-tax cagr/dd summary convenience
    res["config"] = {"cost_multiplier_m": M, "band": BAND, "cap": CAP, "windows": WINDOWS, "capitals": list(CAPITALS)}
    OUT.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")

    # summary
    lines = []
    for wn, wres in res["windows"].items():
        for ck, cres in wres.items():
            for arm in ARMS:
                r = cres.get("pre_tax", {}).get(arm, {})
                ar = cres.get("progressive", {}).get(arm, {})
                lines.append(f"{wn} {ck} {arm}: pre-tax CAGR {r.get('cagr_usd_liquidated',0)*100:.2f}% maxDD {r.get('max_dd_nav_usd',0)*100:.2f}% after-tax CAGR {ar.get('cagr_usd_liquidated',0)*100:.2f}% trades {r.get('strategy_trades',0)} sells/yr {r.get('strategy_trades',0)/r.get('years',1):.1f} tax ${ar.get('tax_total_usd_at_payment_fx',0):,.0f}")
    SUMMARY.write_text("\n".join(lines) + "\n")
    print(SUMMARY.read_text())


if __name__ == "__main__":
    main()
