#!/usr/bin/env python3
"""After-tax engine for breadth_vote_v1 (byte copy of research/gain_band_v2/tax_engine.py).

The tax model, accounts and liquidation mechanics below are a byte-faithful copy of
research/vt_level_tax_v1/screen.py lines 1009-1287 (branch research/vt_level_tax_v1 -- the
"corrected" engine, in which the control and the candidate both run on the same synthetic
QLD*). That file is itself the model imported from tax_frontier_v1 / tax_asym_v2. The only
changes are:

  1. `Market` is built from injected `days`/`fx`/`ufe` instead of reading the CSVs inside
     the class (arithmetic unchanged); the `_patched_scale` fallback of vt_level_tax_v1 is
     folded into `Market.scale`.
  2. `DAYS`, `PRICES` and `TARGETS` are injected by `init()` instead of being fixed to the
     VT arms.

Fidelity is checked in screen.py by reproducing vt_level_tax_v1's C0 after-tax anchors.
"""

from __future__ import annotations

import bisect
import csv
import importlib.util
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("trv2_screen", ROOT / "research/trend_robustness_v2/screen.py")
trv2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trv2)

M = 2
COST = trv2.COST_PER_SIDE * M
BAND = trv2.BAND
CAP = trv2.CAP

TARIFF_2026 = ((190_000.0, 0.15), (400_000.0, 0.20), (1_000_000.0, 0.27), (5_300_000.0, 0.35), (math.inf, 0.40))
DIV_THRESHOLD_2026 = 22_000.0
ANCHOR = "2025-10"
INDEX_MIN_RISE = 0.10

# injected state (set by init())
DAYS = None
TARGETS = {}
PRICES = {}


class Market:
    """USD/TRY + Yi-UFE market for the Turkish after-tax model (injected inputs)."""

    def __init__(self, days, fx, ufe):
        self.days = list(days)
        self.fx = np.asarray(fx, dtype=float)
        self.ufe = dict(ufe)

    def scale(self, year: int) -> float:
        # uf cache via simple dict; indexation anchor is the 2025-10 Yi-UFE
        if not hasattr(self, "_sc"):
            self._sc = {}
        if year not in self._sc:
            key = f"{year - 1}-10"
            if key not in self.ufe:  # fallback to the earliest available October (vt_level_tax_v1)
                ks = sorted(k for k in self.ufe if k.endswith("-10"))
                key = ks[0] if ks else ANCHOR
            self._sc[year] = self.ufe[key] / self.ufe[ANCHOR]
        return self._sc[year]


def load_market(fx_csv, ufe_csv, days):
    fd, fv = [], []
    with open(fx_csv) as fh:
        for r in csv.DictReader(fh):
            fd.append(r["date"])
            fv.append(float(r["usdtry_forex_buying"]))
    fx = np.array([fv[bisect.bisect_right(fd, d) - 1] for d in days])
    with open(ufe_csv) as fh:
        ufe = {r["month"]: float(r["yiufe_index"]) for r in csv.DictReader(fh)}
    return Market(days, fx, ufe)


def init(days, prices, targets):
    """Inject the strategy inputs: prices[arm] = (opens, closes), targets[arm] = wants (1-D)."""
    global DAYS, PRICES, TARGETS
    DAYS = list(days)
    PRICES = prices
    TARGETS = targets


# ================== copied verbatim from vt_level_tax_v1 (lines 1009-1287) ==================
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
    if isinstance(a, int):
        i0 = a
        i1 = b
    else:
        i0 = bisect.bisect_left(days_all, a)
        i1 = bisect.bisect_right(days_all, b) - 1
    if regime_name == "progressive":
        regime = Regime("progressive", "tariff")
    else:
        regime = Regime("pre_tax", "none")

    opens_arr, closes_arr = PRICES[arm]
    wants_arr = TARGETS[arm]

    cashbox = {"v": float(capital)}
    account = BaseAccount(mkt, regime, 0.0, cashbox)
    cashbox["v"] = float(capital)

    pays = instalments(days_all, i0, i1)
    nav = np.empty(i1 - i0 + 2)
    nav[0] = capital
    trades = 0
    tax_sales = 0

    prev_tgt = None

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
        resize_target = tgt

        # trade decision (all arms use the production resize rule)
        trade = abs(tgt - w) > BAND

        if account.units == 0 and tgt > 1e-12 and prev_tgt is None:
            trade = True
        if account.units > 1e-12 and tgt <= 0 and prev_tgt is not None and prev_tgt > 0:
            trade = True

        if trade:
            drift = abs(resize_target - w)
            dsum = drift
            fee_total = nav_open * COST * dsum if dsum > 0 else 0.0
            nav_after_cost = nav_open - fee_total
            # single leg adjust units
            curr_units = account.units
            target_units = resize_target * nav_after_cost / po if po > 0 else 0.0
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



class FlatAccount(BaseAccount):
    """B2: 20% flat tax on annual net realized USD gain, losses carried forward.

    Copied verbatim from research/vt_level_tax_v1/screen.py lines 1319-1334 (branch
    research/vt_level_tax_v1). Not present in the fast_reentry_v2 copy this file is based
    on; the two-book runner in screen.py uses it for the B2 bootstrap."""
    def __init__(self, mkt, cash, cashbox=None):
        super().__init__(mkt, Regime("none", "none"), cash, cashbox)
        self.loss_cf = 0.0

    def assess(self, year):
        g = self.year_gain.get(year, 0.0)
        net = g + self.loss_cf
        if net > 0:
            tax = 0.20 * net
            self.loss_cf = 0.0
        else:
            tax = 0.0
            self.loss_cf = net
        return max(0.0, tax)
