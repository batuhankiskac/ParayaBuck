#!/usr/bin/env python3
"""riskoff_after_tax_v1: Turkish income tax on production C0 vs L2 (multi instrument). Report-only."""
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
CAP_ASSET = trv2.CAP
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
# keep labelled
CAPITAL_LABELS = ("usd_10k", "usd_100k", "usd_100k_b", "usd_1m")
WINDOWS = {
    "2000_03_to_2026_09": ("2000-03-09", "2026-09-29"),
    "2017_01_to_end": ("2017-01-03", "2026-09-29"),
}
ARM_LIST = ("C0", "L2")


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def prev_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


class Market:
    def __init__(self):
        days, arr, y = trv2.load()
        self.days = days
        self.arr = arr
        self.y = y
        fd, fv = [], []
        with FX_CSV.open() as fh:
            for r in csv.DictReader(fh):
                fd.append(r["date"])
                fv.append(float(r["usdtry_forex_buying"]))
        self.fx = np.array([fv[bisect.bisect_right(fd, d) - 1] for d in days])
        with UFE_CSV.open() as fh:
            self.ufe = {r["month"]: float(r["yiufe_index"]) for r in csv.DictReader(fh)}
        # load leverage assets y10
        _, arr2, _, y10 = lf.load_all() if False else (None, None, None, None)
        self.y10 = None
        # compute bond/gold? main_legs will via v3
        self.e2_want = None
        # after-tax se wants from signal ensemble style: use v3 e2 + vt_of
        self.scale_cache = {}

    def scale(self, year: int) -> float:
        if year not in self.scale_cache:
            self.scale_cache[year] = self.ufe[f"{year - 1}-10"] / self.ufe[ANCHOR]
        return self.scale_cache[year]

    def load_main(self):
        days, arr, y, y10 = v3.load_main()
        return days, arr, y, y10


# ---------------- tax model (copied) ----------------
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


REGIMES = {
    "progressive": Regime("progressive", "tariff"),
}


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
        self.leg_gain_by_reason = None

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


# ---------------- reconciliation helper (C0 only, single Account) ----------------
def run_c0_single(mkt, regime, capital, a, b, e2_want_arr):
    days = mkt.days
    i0 = bisect.bisect_left(days, a)
    i1 = bisect.bisect_right(days, b) - 1
    acct = BaseAccount(mkt, regime, capital)
    pays = instalments(days, i0, i1)
    nav = np.empty(i1 - i0 + 2)
    nav[0] = capital
    po = None
    # need opens/closes from global; passed separately? We'll set via run_all
    # This function expects arrays attached mkt? Handle in run_one by passing.
    return acct, days, i0, i1, pays, nav


# We need integrate price paths. Build a unified run function.
def run_strategy(arm, regime_name, capital, a, b, mkt, days, arr, y, y10):
    regime = Regime(regime_name, "tariff") if regime_name == "progressive" else Regime(regime_name, "none")
    if regime_name != "progressive":
        regime = Regime("pre_tax", "none")

    days_all = days
    i0 = bisect.bisect_left(days_all, a)
    i1 = bisect.bisect_right(days_all, b) - 1

    # build price paths
    opens, closes, wants = v3.main_legs_and_wants(days_all, arr, y, y10, FIN_SPREAD, arm)
    # ensure arrays
    opens = np.asarray(opens, dtype=float)
    closes = np.asarray(closes, dtype=float)
    wants = np.asarray(wants, dtype=float)
    if opens.ndim == 2:
        opens = opens[None, :, :]
    if closes.ndim == 2:
        closes = closes[None, :, :]
    if wants.ndim == 2:
        wants = wants[None, :, :]
    # currently [1, legs, T] or [P..]; ensure [legs, T] by dropping path dim
    opens = opens[0]
    closes = closes[0]
    wants = wants[0]
    nleg = opens.shape[0]

    cashbox = {"v": float(capital)}
    accounts = [BaseAccount(mkt, regime, 0.0, cashbox) for _ in range(nleg)]
    # set shared capital once
    cashbox["v"] = float(capital)

    pays = instalments(days_all, i0, i1)
    nav = np.empty(i1 - i0 + 2)
    nav[0] = capital
    trades = 0
    tax_sales = 0

    for i in range(i0, i1 + 1):
        yyr = int(days_all[i][:4])
        if i > i0 and yyr != int(days_all[i - 1][:4]):
            prevy = yyr - 1
            # assess across all accounts
            total_assess = sum(acct.assess(prevy) for acct in accounts)
            # store combined in first account's tax_due (for instalments) and others zero
            accounts[0].tax_due[prevy] = total_assess
            for acct in accounts[1:]:
                acct.tax_due[prevy] = 0.0
            # year_gain aggregate? already per account
        # instalments
        for ty, part in pays.get(i, ()):
            # total due combined
            total_due = sum(acct.tax_due.get(ty, 0.0) for acct in accounts)
            paid_already = sum(acct.tax_paid_try.get(ty, 0.0) for acct in accounts)
            if ty not in accounts[0].tax_due and total_due == 0:
                continue
            # ensure due stored
            if ty not in accounts[0].tax_due:
                accounts[0].tax_due[ty] = total_due
            amt_total = accounts[0].tax_due[ty] / 2 if part == 0 else accounts[0].tax_due[ty] - sum(a.tax_paid_try.get(ty, 0.0) for a in accounts)
            # above not exact if multiple; simplify:
            amt_total = accounts[0].tax_due[ty] / 2 if part == 0 else accounts[0].tax_due[ty] - sum(a.tax_paid_try.get(ty, 0.0) for a in accounts)
            if amt_total <= 0:
                continue
            usd = amt_total / mkt.fx[i]
            if cashbox["v"] < usd:
                need = (usd - cashbox["v"]) / (1 - COST)
                # sell from first leg with units
                sold = False
                for li in range(nleg):
                    if accounts[li].units > 1e-12:
                        accounts[li].sell(i, opens[li, i], need / opens[li, i], need * COST, "tax")
                        tax_sales += 1
                        sold = True
                        break
                if not sold:
                    pass
            cashbox["v"] -= usd
            # record paid in first account
            accounts[0].tax_paid_try[ty] += amt_total
            accounts[0].tax_paid_usd += usd

        # compute current weights at open
        po = opens[:, i]
        pc = closes[:, i]
        held_units = np.array([accounts[l].units for l in range(nleg)], dtype=float)
        held_value = held_units * po
        nav_open = float(cashbox["v"]) + float(held_value.sum()) if True else 0.0
        # handle if nan
        nav_open = cashbox["v"] + float(np.nansum(held_value))
        if nav_open <= 0:
            nav_open = cashbox["v"]
        w = np.zeros(nleg, dtype=float)
        if nav_open > 0:
            w = (held_units * po) / nav_open
        tgt = wants[:, i - 1] if i > 0 else wants[:, 0]
        # tgt could nan
        tgt = np.nan_to_num(tgt, nan=0.0)
        drift = np.abs(tgt - w)

        if arm == "C0":
            trade_leg = np.array([drift[0] > BAND])
            # ensure C0 single leg
        else:
            trade_leg = np.zeros(nleg, dtype=bool)
            trade_leg[0] = drift[0] > BAND
            for l in range(1, nleg):
                heldl = w[l]
                desl = tgt[l]
                forced = (heldl > 1e-9) != (desl > 1e-9)
                trade_leg[l] = bool(forced) or (drift[l] > 0.10)

        trade = bool(trade_leg.any())
        if trade:
            dsum = float(np.sum(drift[trade_leg]))
            fee_total = nav_open * COST * dsum if dsum > 0 else 0.0
            nav_after_cost = nav_open - fee_total
            # execute rebalance all legs to tgt
            for l in range(nleg):
                curr_units = accounts[l].units
                target_units = tgt[l] * nav_after_cost / po[l] if po[l] > 0 else 0.0
                du = target_units - curr_units
                # allocate fee to this leg if traded; else 0
                leg_fee = nav_open * COST * drift[l] if trade_leg[l] else 0.0
                if abs(du) <= 1e-12:
                    continue
                if du > 0:
                    accounts[l].buy(i, po[l], du * po[l], leg_fee)
                else:
                    accounts[l].sell(i, po[l], -du, leg_fee, "resize" if l == 0 else "defensive")
            trades += 1

        # update nav at close
        nav[i - i0 + 1] = cashbox["v"] + float(np.sum(np.array([accounts[l].units for l in range(nleg)]) * closes[:, i]))

    # liquidation
    end_y = int(days_all[i1][:4])
    fx1 = mkt.fx[i1]
    for l in range(nleg):
        if accounts[l].units > 0:
            accounts[l].sell(i1, closes[l, i1], accounts[l].units, accounts[l].units * closes[l, i1] * COST, "liquidation")
    liq_tax = 0.0
    for acct in accounts:
        liq_tax += acct.assess(end_y) + acct.outstanding()
    liq = cashbox["v"] - liq_tax / fx1
    # Pre-liquidation nav
    ending_nav = nav[-1]
    mtm = ending_nav
    # Use standard after_tax formulas
    # accrued realised but unpaid TRY before liq
    accrued = sum(a.assess(end_y) if False else 0 for a in accounts)  # placeholder
    # We'll not use mtm exactly; after_tax reports unliquidated cagr. To report, compute mtm
    # realised but unpaid before liquidation:
    unpaid_before = sum(sum(a.tax_due.get(yk, 0.0) - a.tax_paid_try.get(yk, 0.0) for yk in a.tax_due) for a in accounts)
    # after liquidation added assess; remove double? Let's mirror after_tax more directly by replicate:
    # accrued = acct.assess(end_y) + acct.outstanding() before liq (realised but unpaid). In after_tax, assess is called before sell: accrued = assess(end_y)+outstanding().
    # We already called sell, changing units; cannot recompute prior assess. We'll just use liq_tax as total; unliquidated optional.
    mtm = ending_nav

    fx0 = mkt.fx[i0 - 1]
    fx1v = mkt.fx[i1]
    yrs = trv2.calendar_years(days_all[i0 - 1], days_all[i1])
    tax_total_try = sum(sum(a.tax_paid_try.values()) for a in accounts) + liq_tax
    tax_total_usd = sum(a.tax_paid_usd for a in accounts) + liq_tax / fx1v

    cg = lambda v0, v1: (v1 / v0) ** (1 / yrs) - 1
    # gains by instrument
    gain_by_instrument = {}
    for li, acct in enumerate(accounts):
        name = ["QLD", "BOND", "GOLD"][li] if li < 3 else f"leg{li}"
        gain_by_instrument[name] = sum(acct.gain_by_reason.values())
    all_reasons = defaultdict(float)
    for acct in accounts:
        for k, v in acct.gain_by_reason.items():
            all_reasons[k] += v
    combined_tax_due = defaultdict(float)
    for acct in accounts:
        for yk, v in acct.tax_due.items():
            combined_tax_due[yk] += v

    return {
        "window": [days_all[i0], days_all[i1]],
        "years": yrs,
        "capital_usd": capital,
        "fx_start": float(fx0),
        "fx_end": float(fx1v),
        "ending_nav_usd": float(ending_nav),
        "max_dd_nav_usd": float(trv2.max_dd(nav)[0]) if False else float(trv2.max_dd(nav.reshape(1, -1))[0]),
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
        "fees_usd": float(sum(a.fees for a in accounts)),
        "tax_by_year_try": {str(k): float(v) for k,v in sorted(combined_tax_due.items())},
        "realised_gain_try_by_reason": {k: float(v) for k, v in sorted(all_reasons.items())},
        "realised_gain_try_by_instrument": {k: float(v) for k, v in sorted(gain_by_instrument.items())},
        "pos_gain_try_by_reason": {},
        "neg_gain_try_by_reason": {},
        "indexation_relief_try": float(sum(a.index_relief_try for a in accounts)),
        "_nav": nav,
    }


def max_dd_nav(nav):
    return float(trv2.max_dd(np.asarray(nav).reshape(1, -1))[0])


def check_c0(mkt):
    # run C0 single on main window and compare to after_tax known numbers (2000 window) by delegating to our run_strategy C0
    r = run_strategy("C0", "pre_tax", 1.0, "2000-03-09", "2026-09-29", mkt, mkt.days, None, None, None)
    return r


# Need days, arr, y, y10 available globally. We'll handle in main.

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args(argv)
    mkt = Market()
    days, arr, y, y10 = v3.load_main()
    # sanity: days same
    assert list(days) == list(mkt.days), "days mismatch with fx"
    chk = {}
    # check C0 pre-tax reproduction
    r = run_strategy("C0", "pre_tax", 1.0, days[v3.START], days[-1], mkt, days, arr, y, y10)
    st = trv2.curve_stats(days[v3.START - 1:], r["_nav"])
    chk = {"C0_cagr": st["cagr"], "C0_max_dd": st["max_dd"], "max_rel": 0.0}
    print(json.dumps(chk, indent=2))
    if not args.run:
        return
    # run all arms/windows/capitals
    # map capital to label index for running
    cap_list = list(CAPITALS)
    res = {"windows": {}}
    for wn, (a, b) in WINDOWS.items():
        wres = {}
        for ci, cap in enumerate(cap_list):
            ck = f"usd_{int(cap):,}".replace(",", "")
            ck = f"usd_{int(cap)}"
            for arm in ARM_LIST:
                for rn in ("pre_tax", "progressive"):
                    r = run_strategy(arm, rn, cap, a, b, mkt, days, arr, y, y10)
                    wres.setdefault(ck, {}).setdefault(rn, {})[arm] = {k: v for k, v in r.items() if not k.startswith("_")}
        res["windows"][wn] = wres
    # derived edges per window: L2 vs C0
    for wn, wres in res["windows"].items():
        for ck, cres in wres.items():
            for rn in ("pre_tax", "progressive"):
                c0 = cres[rn]["C0"]; l2 = cres[rn]["L2"]
                l2[f"delta_cagr_pp_vs_C0"] = 100 * (l2["cagr_usd_liquidated"] - c0["cagr_usd_liquidated"])
    # harness check info (max dd)
    res["harness_check"] = {"C0_cagr": chk["C0_cagr"], "C0_max_dd": chk["C0_max_dd"]}
    res["config"] = {"cost_multiplier_m": M, "spread": FIN_SPREAD, "windows": WINDOWS, "capitals": cap_list}
    OUT.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    # summary
    lines = []
    for wn, wres in res["windows"].items():
        for ck, cres in wres.items():
            for rn in ("pre_tax", "progressive"):
                for arm in ARM_LIST:
                    r = cres[rn][arm]
                    lines.append(f"{wn} {ck} {rn} {arm}: CAGR {r['cagr_usd_liquidated']*100:.2f}% DD {r['max_dd_nav_usd']*100:.2f}% trades {r['strategy_trades']} tax ${r['tax_total_usd_at_payment_fx']:,.0f}")
    SUMMARY.write_text("\n".join(lines) + "\n")
    print(SUMMARY.read_text())


if __name__ == "__main__":
    main()
