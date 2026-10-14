#!/usr/bin/env python3
"""tax_asym_v3 screen (see PREREGISTRATION.md). Offline; reads data/historical only.

Question: does candidate A2 (production E2 with downward-only vol resizes, k = 50%)
still beat production E2 C0 on the clean after-tax bootstrap once C0 trades the same
daily-reset 2x Nasdaq-100 synthetic as every candidate? `tax_asym_v2` ran C0 in its
bootstrap on *unlevered* QQQ while A1/A2 ran on 2x, so its verdict on A2 is void
(`vt_level_tax_v1`, Amendment 3).

Arms (fixed, see PREREGISTRATION.md): C0 = production E2 1.1.0; A2 = downward-only
vol resizes at k = 0.50 (the `tax_asym_v2` candidate); A2_40 and A2_60 = the same
rule at the plateau neighbours k = 0.40 and k = 0.60.

Modes
  --harness  C0 only, no arm: reproduces the production-E2 deviation row
             (2000-03-09 -> 2026-09-29, base/calibrated x m=1/m=2) to 0.01 pp,
             checks simulate_band at 0.5 pp equals trend_robustness_v2.simulate_multi
             bit for bit, and checks that the after-tax engine's pre-tax C0 NAV equals
             the pre-tax engine's C0 NAV (same sessions, same trades).
  --verify   C0/A2 only, B1-recent: re-runs the bootstrap with `tax_asym_v2`'s C0
             pricing (unlevered QQQ) and asserts the per-path C0 arrays equal
             tax_asym_v2/RESULTS.json exactly (replication), then re-runs it with the
             corrected 2x C0 and reports the C0 median CAGR / max DD of both. This is
             the numerical verification of the Amendment 3 claim.
  --smoke    deterministic random-walk prices, 3 paths, no real market data, no output
             files: exercises the whole run path (signals, both tax engines, gates).
  --run      re-runs --harness, stops if it fails, then runs the four pre-registered
             variants (B1-recent progressive, B1-early progressive, B2 flat 20%,
             pre-tax control) x the four arms, applies the after-tax gate and writes
             RESULTS.json and SUMMARY.txt.

Pre-tax engine (signals, vol, synthetic 2x, band simulator, metrics, deviation
reference) is the `ewma_band_v1` screen, built on research/trend_robustness_v2/
screen.py (its main() is never run). The after-tax engine (tax model, accounts,
USD/TRY + Yi-UFE handling, clean block-20 bootstrap, B2 flat-tax loop) is
`tax_asym_v2`'s, with the one substantive fix: C0's PRICES entry is the same
synthetic QLD* as every other arm.
"""

from __future__ import annotations

import bisect
import csv
import importlib.util
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def imp(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


trv2 = imp("trv2", ROOT / "research/trend_robustness_v2/screen.py")
lf = imp("lf", ROOT / "research/leverage_frontier_v1/screen.py")

DTB3 = ROOT / "data/historical/stress_robustness_v1_dtb3.csv"
DEVIATION = HERE / "REFERENCE_E2_DEVIATION.json"
V2_RESULTS = ROOT / "research/tax_asym_v2/RESULTS.json"
FX_CSV = ROOT / "data/historical/after_tax_tr_v1_usdtry_cbrt.csv"
UFE_CSV = ROOT / "data/historical/after_tax_tr_v1_yiufe.csv"
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

# ----------------------------------------------------------------------------- pre-registered constants
SMA_N, TSM_N, DON_N = 200, 252, 126
CAP = trv2.CAP
BAND = trv2.BAND
COST_PER_SIDE = trv2.COST_PER_SIDE
EXP_Q = trv2.EXP_Q
M = 2
COST = COST_PER_SIDE * M  # 8 bp per unit traded
FIN = {"base": 0.0, "calibrated": 0.0075}
START = TSM_N + 1  # 253 -> first measured session 2000-03-09 (e2_integration_v1)
SEED, PATHS, CHUNK, BLOCK = 20261011, 5000, 500, 20
WARM, MEASURED = trv2.WARM, trv2.MEASURED
EXT = TSM_N - trv2.SMA_MAX + 1  # 3 back-extension sessions (signal_ensemble_v1)
TOL = 1e-4  # 0.01 pp harness tolerance
TARIFF_2026 = ((190_000.0, 0.15), (400_000.0, 0.20), (1_000_000.0, 0.27), (5_300_000.0, 0.35), (math.inf, 0.40))
DIV_THRESHOLD_2026 = 22_000.0
ANCHOR = "2025-10"
INDEX_MIN_RISE = 0.10
WINDOW_START, WINDOW_END = "2000-03-09", "2026-09-29"

# --- arms (fixed before any run; PREREGISTRATION.md) ---
K_PRIMARY = 0.50
ARM_K = {"A2": 0.50, "A2_40": 0.40, "A2_60": 0.60}
ARMS = ("C0", "A2", "A2_40", "A2_60")
CANDIDATES = ("A2", "A2_40", "A2_60")
TRADE_MODE = {"C0": "prod", "A2": "downward", "A2_40": "downward", "A2_60": "downward"}

# --- bootstrap variants and the after-tax gate (fixed before any run) ---
# B1-recent / B1-early: Turkish progressive regime on a fixed real calendar.
# B2: 20% flat tax on annual net realized USD gain with loss carry-forward, recent calendar.
# pre-tax: the same paths and mechanics with no tax (context only, not gated).
CAL_RECENT = ("2015-09-01", "2026-09-30")
CAL_EARLY = ("2005-09-01", "2016-09-30")
GATE_MEDIAN_PP = 0.50
GATE_SHARE = 0.65
GATE_WINDOWS = ("B1_recent", "B1_early", "B2_flat")
BOOT_PATHS, BOOT_CAPITAL = 1000, 100_000.0


# ----------------------------------------------------------------------------- signals (production E2)
def tsm_up_price(c, n=TSM_N):
    """up[t]: n-session price return > 0 (production 1.1.0: zero hurdle on split-adjusted price)."""
    c = np.atleast_2d(c)
    up = np.zeros(c.shape, dtype=bool)
    up[:, n:] = (c[:, n:] / c[:, :-n]) > 1.0
    return up


def don_up(c, n=DON_N):
    """up[t]: close[t] > midpoint of the max/min of the n closes ending at t (inclusive)."""
    c = np.atleast_2d(c)
    up = np.zeros(c.shape, dtype=bool)
    win = sliding_window_view(c, n, axis=1)
    mid = (win.max(axis=2) + win.min(axis=2)) / 2
    up[:, n - 1:] = c[:, n - 1:] > mid
    return up


def e2_up(price):
    """Production E2 vote on the price close: at least 2 of SMA200, TSM252 (> 0), DON126."""
    s = trv2.sma_up(price, SMA_N).astype(int)
    t = tsm_up_price(price).astype(int)
    d = don_up(price).astype(int)
    return (s + t + d) >= 2


def wants_for(e2, vol):
    """[P, T] QLD* targets: E2 vote x vt(vol), total capped at 0.98 as trend_robustness_v2.build_wants."""
    raw = np.atleast_2d(e2.astype(float) * trv2.vt_of(vol))
    f = np.where(raw > CAP, CAP / np.where(raw > 0, raw, 1.0), 1.0)
    return raw * f


# ----------------------------------------------------------------------------- pre-tax engine
def simulate_band(opens, closes, wants, cost, start, band):
    """Single-leg trend_robustness_v2 mechanics with a resize band.

    Per session i: overnight growth; target = wants[i-1] (decided at the completed close);
    an entry (held 0, target > 0) or exit (held > 0, target 0) always trades to target;
    otherwise (both > 0) the position is resized to target only if |target - held| > band.
    Cost = cost x |delta w|; then intraday growth. Idle cash earns zero.
    With band = 0.005 this equals trend_robustness_v2.simulate_multi (one leg)."""
    opens, closes, wants = (np.atleast_2d(np.asarray(x, dtype=float)) for x in (opens, closes, wants))
    P, T = closes.shape
    nav = np.ones(P)
    w = np.zeros(P)
    out = np.empty((P, T - start + 1))
    out[:, 0] = 1.0
    for i in range(start, T):
        g_on = opens[:, i] / closes[:, i - 1]
        g_on = np.where(np.isnan(g_on), 1.0, g_on)
        va = w * g_on
        tot = 1 - w + va
        nav = nav * tot
        w = va / tot
        want = wants[:, i - 1]
        d = np.abs(want - w)
        held, tgt = w > 0, want > 0
        flip = held != tgt
        trade = flip | (d > band)
        nav = np.where(trade, nav * (1 - cost * d), nav)
        w = np.where(trade, want, w)
        ga = closes[:, i] / opens[:, i]
        va = w * ga
        tot = 1 - w + va
        nav = nav * tot
        w = va / tot
        out[:, i - start + 1] = nav
    return out


def main_inputs(spread):
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y + spread, EXP_Q)
    return days, arr, o, c, e2_up(q[1])[0], trv2.vol_n(c)[0]


def harness():
    """C0 only. Deviation row + band-simulator identity + after-tax/pre-tax engine identity."""
    ref = json.loads(DEVIATION.read_text())
    checks = {}
    for md, spread in FIN.items():
        days, arr, o, c, e2, v20 = main_inputs(spread)
        w = wants_for(e2, v20)[0]
        for m in (1, 2):
            nav = simulate_band(o, c, w, COST_PER_SIDE * m, START, BAND)[0]
            years = trv2.calendar_years(days[START - 1], days[-1])
            r = ref[f"{md}_m{m}"]["arms"]["E2_prod_price_h0"]
            got = {"cagr": float(trv2.cagr(nav[0], nav[-1], years)), "max_dd": float(trv2.max_dd(nav)[0])}
            for k in ("cagr", "max_dd"):
                checks[f"{md}_m{m}_{k}"] = {"reference": r[k], "reproduced": got[k],
                                            "diff_pp": (got[k] - r[k]) * 100}
    ok_dev = all(abs(v["diff_pp"]) <= TOL * 100 for v in checks.values())

    # band simulator at 0.5 pp == trend_robustness_v2.simulate_multi (bit for bit), base m = 2
    days, arr, o, c, e2, v20 = main_inputs(0.0)
    w = wants_for(e2, v20)
    nav_b = simulate_band(o, c, w, COST_PER_SIDE * 2, START, BAND)
    nav_m = trv2.simulate_multi(o, c, w[:, None, :], COST_PER_SIDE * 2, START)[0]
    sim_equal = bool(np.array_equal(nav_b, nav_m))

    # after-tax engine (pre-tax regime) C0 NAV == pre-tax engine C0 NAV, same measured window
    mkt = Market()
    mkt.days = DAYS
    mkt.fx = BASE_MKT.fx.copy()
    mkt.ufe = BASE_MKT.ufe
    at = run_strategy("C0", "pre_tax", 1.0, START, len(DAYS) - 1, mkt)
    at_nav = at["_nav"]
    ref_nav = nav_b[0]
    n = min(len(at_nav), len(ref_nav))
    nav_rel = float(np.max(np.abs(at_nav[:n] / at_nav[0] - ref_nav[:n] / ref_nav[0])))
    same_len = len(at_nav) == len(ref_nav)

    ok = ok_dev and sim_equal and nav_rel < 1e-9 and same_len
    return {"deviation_checks": checks, "tolerance_pp": TOL * 100,
            "band_simulator_equals_simulate_multi": sim_equal,
            "after_tax_pre_tax_nav_max_rel_diff": nav_rel,
            "after_tax_pre_tax_nav_same_length": bool(same_len),
            "after_tax_engine_trades": int(at["strategy_trades"]),
            "pass": bool(ok)}


# ============================================================================ after-tax engine
DAYS, ARR, Y, Y10 = lf.load_all()
QQQ = ARR["QQQ"]
EQ2_O, EQ2_C = lf.synthetic_L(QQQ[0], QQQ[1], QQQ[2], Y, 2, lf.EXPENSE[2.0], 0.0)
QQ1_O, QQ1_C = lf.synthetic_L(QQQ[0], QQQ[1], QQQ[2], Y, 1, lf.EXPENSE[1.0], 0.0)
EQ2_VOL = trv2.vol_n(EQ2_C)[0]
VT035 = lf.vt_weight(EQ2_VOL, 0.35)
UP = e2_up(QQQ[1])[0]
T = len(DAYS)

_A2_TARGET = UP.astype(float) * np.minimum(CAP, VT035)
TARGETS = {"C0": _A2_TARGET}
PRICES = {"C0": (EQ2_O, EQ2_C)}
for _a in CANDIDATES:
    TARGETS[_a] = _A2_TARGET
    PRICES[_a] = (EQ2_O, EQ2_C)


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

    def scale(self, year: int) -> float:
        if not hasattr(self, "_sc"):
            self._sc = {}
        if year not in self._sc:
            self._sc[year] = self.ufe[f"{year - 1}-10"] / self.ufe[ANCHOR]
        return self._sc[year]

    days = DAYS


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


class FlatAccount(BaseAccount):
    """B2: 20% flat tax on annual net realized USD gain, losses carried forward."""
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


def instalments(days, i0, i1):
    out = defaultdict(list)
    for y in range(int(days[i0][:4]), int(days[i1][:4]) + 1):
        for md, part in (("03-31", 0), ("07-31", 1)):
            k = bisect.bisect_left(days, f"{y}-{md}")
            if i0 <= k <= i1:
                out[k].append((y - 1, part))
    return out


def run_strategy(arm, regime_name, capital, a, b, mkt):
    """tax_asym_v2's progressive engine: TRADE_MODE dispatch (prod / downward), tariff or pre-tax."""
    days_all = DAYS
    if isinstance(a, int):
        i0, i1 = a, b
    else:
        i0 = bisect.bisect_left(days_all, a)
        i1 = bisect.bisect_right(days_all, b) - 1
    regime = Regime("progressive", "tariff") if regime_name == "progressive" else Regime("pre_tax", "none")

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

    for i in range(i0, i1 + 1):
        if i > i0 and int(days_all[i][:4]) != int(days_all[i - 1][:4]):
            prevy = int(days_all[i][:4]) - 1
            account.tax_due[prevy] = account.assess(prevy)

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
        held_value = account.units * po
        nav_open = cashbox["v"] + held_value
        if nav_open <= 0:
            nav_open = cashbox["v"]
        w = (account.units * po) / nav_open if nav_open > 0 else 0.0

        tgt = float(wants_arr[i - 1]) if i > 0 else float(wants_arr[0])
        tgt = 0.0 if math.isnan(tgt) else tgt
        resize_target = tgt

        trade = False
        if mode == "prod":
            trade = abs(tgt - w) > BAND
        elif mode == "downward":
            kk = ARM_K.get(arm, 0.5)
            if tgt > w + BAND:
                trade = True
                resize_target = tgt
            elif tgt < w - BAND:
                trim_case = (tgt > 0) and (tgt < kk * max(w, 1e-12))
                if prev_tgt is not None and prev_tgt > 0 and tgt == 0:
                    trade = True
                    resize_target = tgt
                elif trim_case:
                    trade = True
                    resize_target = tgt
            if account.units == 0 and tgt > 0:
                trade = True
                resize_target = tgt
            if account.units > 0 and tgt == 0:
                trade = True
                resize_target = 0.0

        if account.units == 0 and tgt > 1e-12 and prev_tgt is None:
            trade = True
        if account.units > 1e-12 and tgt <= 0 and prev_tgt is not None and prev_tgt > 0:
            trade = True

        if trade:
            drift = abs(resize_target - w)
            fee_total = nav_open * COST * drift if drift > 0 else 0.0
            nav_after_cost = nav_open - fee_total
            curr_units = account.units
            target_units = resize_target * nav_after_cost / po if po > 0 else 0.0
            du = target_units - curr_units
            leg_fee = nav_open * COST * drift if drift > 0 else 0.0
            if du > 1e-12:
                account.buy(i, po, du * po, leg_fee)
            elif du < -1e-12:
                account.sell(i, po, -du, leg_fee, "resize")
            trades += 1

        nav[i - i0 + 1] = cashbox["v"] + account.units * pc
        prev_tgt = tgt

    end_y = int(days_all[i1][:4])
    if account.units > 0:
        account.sell(i1, float(closes_arr[i1]), account.units, account.units * float(closes_arr[i1]) * COST, "liquidation")
    liq_tax = account.assess(end_y) + account.outstanding()
    fx1 = mkt.fx[i1]
    liq = cashbox["v"] - liq_tax / fx1

    mtm = nav[-1]
    fx1v = mkt.fx[i1]
    yrs = trv2.calendar_years(days_all[i0 - 1], days_all[i1])

    def cg(v0, v1):
        return (v1 / v0) ** (1 / yrs) - 1

    nav_arr = nav.reshape(1, -1)
    return {
        "window": [days_all[i0], days_all[i1]],
        "years": yrs,
        "capital_usd": capital,
        "max_dd_nav_usd": float(trv2.max_dd(nav_arr)[0]),
        "cagr_usd_liquidated": float(cg(capital, liq)),
        "terminal_usd_liquidated": float(liq),
        "cagr_usd_unliquidated": float(cg(capital, mtm)),
        "tax_total_try": float(sum(account.tax_paid_try.values()) + liq_tax),
        "tax_total_usd": float(account.tax_paid_usd + liq_tax / fx1v),
        "strategy_trades": int(trades),
        "tax_funding_sales": int(tax_sales),
        "fees_usd": float(account.fees),
        "_nav": nav,
    }


def run_strategy_flat(arm, capital, i0, i1, dates, opens_arr, closes_arr, wants_arr, mkt):
    """B2 engine: run_strategy's session loop with a flat-tax account (tax paid at the year boundary)."""
    days_all = dates
    opens_arr = np.asarray(opens_arr, float).flatten()
    closes_arr = np.asarray(closes_arr, float).flatten()
    wants_arr = np.asarray(wants_arr, float).flatten()
    cashbox = {"v": float(capital)}
    account = FlatAccount(mkt, 0.0, cashbox)
    cashbox["v"] = float(capital)
    nav = np.empty(i1 - i0 + 2)
    nav[0] = capital
    trades = 0
    tax_sales = 0
    prev_tgt = None
    mode = TRADE_MODE[arm]
    for i in range(i0, i1 + 1):
        if i > i0 and int(days_all[i][:4]) != int(days_all[i - 1][:4]):
            prevy = int(days_all[i][:4]) - 1
            acct_tax = account.assess(prevy)
            account.tax_due[prevy] = acct_tax
            due = acct_tax
            if due > 1e-9:
                usd = due / mkt.fx[i] if mkt.fx[i] != 0 else due
                if cashbox["v"] < usd:
                    need = (usd - cashbox["v"])
                    if account.units > 1e-12:
                        price = float(closes_arr[i - 1])
                        units_sell = need / price if price > 0 else 0.0
                        account.sell(i - 1, price, units_sell, units_sell * price * COST, "tax")
                        tax_sales += 1
                cashbox["v"] -= usd
                account.tax_paid_try[prevy] = account.tax_paid_try.get(prevy, 0.0) + due
                account.tax_paid_usd += usd

        po = float(opens_arr[i])
        pc = float(closes_arr[i])
        held_value = account.units * po
        nav_open = cashbox["v"] + held_value
        if nav_open <= 0:
            nav_open = cashbox["v"]
        w = (account.units * po) / nav_open if nav_open > 0 else 0.0
        tgt = float(wants_arr[i - 1]) if i > 0 else float(wants_arr[0])
        tgt = 0.0 if (tgt != tgt) else tgt
        resize_target = tgt

        trade = False
        if mode == "downward":
            kk = ARM_K.get(arm, 0.5)
            if tgt > w + BAND:
                trade = True
                resize_target = tgt
            elif tgt < w - BAND:
                trim_case = (tgt > 0) and (tgt < kk * max(w, 1e-12))
                if prev_tgt is not None and prev_tgt > 0 and tgt == 0:
                    trade = True
                    resize_target = tgt
                elif trim_case:
                    trade = True
                    resize_target = tgt
            if account.units == 0 and tgt > 1e-12:
                trade = True
                resize_target = tgt
            if account.units > 1e-12 and tgt <= 0:
                trade = True
                resize_target = 0.0
        else:
            trade = abs(tgt - w) > BAND
            if account.units == 0 and tgt > 1e-12:
                trade = True
            if account.units > 1e-12 and tgt <= 0:
                trade = True
            resize_target = tgt

        if trade:
            drift = abs(resize_target - w)
            nav_after = nav_open - (nav_open * COST * drift if drift > 0 else 0.0)
            fee = nav_open * COST * drift if drift > 0 else 0.0
            target_units = resize_target * nav_after / po if po > 0 else 0.0
            du = target_units - account.units
            if du > 1e-12:
                account.buy(i, po, du * po, fee if du > 0 else 0.0)
            elif du < -1e-12:
                account.sell(i, po, -du, fee if du < 0 else 0.0, "resize")
            trades += 1

        nav[i - i0 + 1] = cashbox["v"] + account.units * pc
        prev_tgt = tgt

    end_y = int(days_all[i1][:4])
    if account.units > 0:
        account.sell(i1, float(closes_arr[i1]), account.units, account.units * float(closes_arr[i1]) * COST, "liquidation")
    liq_tax = account.assess(end_y) + account.outstanding()
    fx1 = mkt.fx[i1]
    liq = cashbox["v"] - (liq_tax / fx1 if fx1 != 0 else liq_tax)
    mtm = nav[-1]
    fx1v = mkt.fx[i1]
    yrs = trv2.calendar_years(days_all[i0 - 1], days_all[i1])
    cg = lambda v0, v1: (v1 / v0) ** (1.0 / yrs) - 1 if yrs > 0 else float("nan")  # noqa: E731
    return {
        "window": [days_all[i0], days_all[i1]],
        "years": float(yrs),
        "capital_usd": capital,
        "max_dd_nav_usd": float(trv2.max_dd(nav.reshape(1, -1))[0]),
        "cagr_usd_liquidated": float(cg(capital, liq)),
        "terminal_usd_liquidated": float(liq),
        "cagr_usd_unliquidated": float(cg(capital, mtm)),
        "tax_total_usd": float(account.tax_paid_usd + (liq_tax / fx1v if fx1v != 0 else liq_tax)),
        "strategy_trades": int(trades),
        "tax_funding_sales": int(tax_sales),
        "fees_usd": float(account.fees),
    }


def _patched_scale(self, year):
    key = f"{year - 1}-10"
    if key not in self.ufe:
        ks = sorted(k for k in self.ufe if k.endswith("-10"))
        key = ks[0] if ks else ANCHOR
    return self.ufe[key] / self.ufe[ANCHOR]


Market.scale = _patched_scale

BASE_MKT = Market()
BASE_MKT.days = DAYS
FX_BY_DATE = {DAYS[i]: float(BASE_MKT.fx[i]) for i in range(len(DAYS))}

# ----------------------------------------------------------------------------- clean after-tax bootstrap
FIXED_NEED = 2776       # EXT(3) + WARM(253) + MEASURED(10*252 = 2520)
FIXED_EXT = 3
FIXED_L = 253 + 10 * 252
FIXED_I0 = FIXED_EXT + 253


def make_fixed_calendar(lo, hi, need):
    all_days = lf.load_all()[0]
    sub = [d for d in all_days if lo <= d <= hi]
    if len(sub) < need:
        raise RuntimeError(f"fixed calendar {lo}..{hi} has only {len(sub)} days, need {need}")
    return list(sub[:need])


def after_tax_bootstrap_variant(n_paths=BOOT_PATHS, capital=BOOT_CAPITAL, seed=SEED,
                                cal_lo=CAL_RECENT[0], cal_hi=CAL_RECENT[1],
                                tax_kind="progressive", arms=ARMS, c0_on_1x=False):
    """tax_asym_v2's design: stationary block-20 resampling of QQQ+DTB3 sessions, fixed real calendar.

    Signals are re-computed on the resampled QQQ price level, the instrument is the daily-reset 2x
    synthetic. `tax_kind`: 'progressive' (B1), 'flat' (B2, 20% with loss carry-forward) or 'pretax'
    (the same paths with no tax). `c0_on_1x=True` reproduces tax_asym_v2's C0 pricing bug (C0 traded
    unlevered QQQ total return while every other arm traded the 2x synthetic); it is used only by
    --verify. Raises if any path liquidates to a non-positive value.
    """
    global DAYS
    q = ARR["QQQ"]
    Tsrc = q.shape[1]
    L, EXT_, need = FIXED_L, FIXED_EXT, FIXED_NEED
    fixed_dates = make_fixed_calendar(cal_lo, cal_hi, need)
    fixed_fx = np.array([FX_BY_DATE.get(d, BASE_MKT.fx[-1]) for d in fixed_dates], dtype=float)

    rng = np.random.default_rng(seed)
    pool = np.arange(1, Tsrc)
    npool = len(pool)

    out = {arm: {"cagr": [], "maxdd": []} for arm in arms}
    deltas = {arm: {"cagr": [], "dd": []} for arm in arms if arm != "C0"}
    neg_count = 0

    for pidx in range(n_paths):
        P = 1
        pos = np.empty((P, L), dtype=np.int64)
        pos[:, 0] = rng.integers(0, npool, P)
        jump = rng.random((P, L)) < 1 / BLOCK
        fresh = rng.integers(0, npool, (P, L))
        for t in range(1, L):
            pos[:, t] = np.where(jump[:, t], fresh[:, t], (pos[:, t - 1] + 1) % npool)
        pre = np.stack([(pos[:, 0] - k) % npool for k in range(EXT_, 0, -1)], axis=1)
        pos = np.concatenate([pre, pos], axis=1)
        idx = pool[pos]
        yy = Y[idx - 1]
        prev = q[1][idx - 1]
        r_on_tr = (q[0][idx] + q[2][idx]) / prev
        r_on_p = q[0][idx] / prev
        r_id = q[1][idx] / q[0][idx]
        one = np.ones((P, 1))
        qc = np.concatenate([one, np.cumprod(r_on_tr * r_id, axis=1)], axis=1)
        qo = np.concatenate([one, qc[:, :-1] * r_on_tr], axis=1)
        pc = np.concatenate([one, np.cumprod(r_on_p * r_id, axis=1)], axis=1)
        e2 = e2_up(pc)
        yyp = np.concatenate([yy, yy[:, -1:]], axis=1)
        so, sc = trv2.synthetic_2x(qo, qc, np.zeros_like(qc), yyp + 0.0, EXP_Q)

        base_vt = np.minimum(CAP, lf.vt_weight(trv2.vol_n(sc)[0], 0.35))
        wants = {arm: e2.astype(float)[0] * base_vt for arm in arms}

        old_days = list(DAYS)
        old_targets = {k: TARGETS.get(k) for k in arms}
        old_prices = {k: PRICES.get(k) for k in arms}

        DAYS = list(fixed_dates)
        c0_prices = (qo.flatten(), qc.flatten()) if c0_on_1x else (so.flatten(), sc.flatten())
        for arm in arms:
            TARGETS[arm] = np.atleast_1d(wants[arm]).flatten().copy()
            PRICES[arm] = c0_prices if arm == "C0" else (so.flatten(), sc.flatten())

        i0 = FIXED_I0
        i1 = len(fixed_dates) - 1
        res = {}
        for arm in arms:
            mkt = Market()
            mkt.days = list(fixed_dates)
            mkt.fx = fixed_fx.copy()
            mkt.ufe = BASE_MKT.ufe
            if tax_kind == "flat":
                r = run_strategy_flat(arm, capital, i0, i1, fixed_dates, PRICES[arm][0], PRICES[arm][1],
                                      TARGETS[arm], mkt)
            else:
                r = run_strategy(arm, "progressive" if tax_kind == "progressive" else "pre_tax",
                                 capital, i0, i1, mkt)
            res[arm] = r

        DAYS = old_days
        for k in arms:
            TARGETS[k] = old_targets[k]
            PRICES[k] = old_prices[k]

        for arm in arms:
            liq = res[arm]["terminal_usd_liquidated"]
            if (not (liq == liq)) or liq is None or liq <= 0:
                neg_count += 1
                raise RuntimeError(f"NEGATIVE/NONFINITE terminal USD on path {pidx} arm {arm}: {liq}")

        c0c = res["C0"]["cagr_usd_liquidated"]
        c0dd = res["C0"]["max_dd_nav_usd"]
        out["C0"]["cagr"].append(c0c)
        out["C0"]["maxdd"].append(c0dd)
        for arm in arms:
            if arm == "C0":
                continue
            ac = res[arm]["cagr_usd_liquidated"]
            ad = res[arm]["max_dd_nav_usd"]
            out[arm]["cagr"].append(ac)
            out[arm]["maxdd"].append(ad)
            deltas[arm]["cagr"].append(ac - c0c)
            deltas[arm]["dd"].append(ad - c0dd)

    def summarize_deltas(dcs, dds):
        arr = np.array(dcs, dtype=float)
        dd = np.array(dds, dtype=float)
        return {
            "n_paths": int(len(arr)),
            "delta_cagr_median_pp": float(np.median(arr) * 100),
            "delta_cagr_mean_pp": float(np.mean(arr) * 100),
            "share_delta_gt_0": float(np.mean(arr > 0)),
            "p05_delta_cagr_pp": float(np.percentile(arr, 5) * 100),
            "p95_delta_cagr_pp": float(np.percentile(arr, 95) * 100),
            "delta_maxdd_median_pp": float(np.median(dd) * 100),
            "delta_maxdd_p05_pp": float(np.percentile(dd, 5) * 100),
            "delta_maxdd_p95_pp": float(np.percentile(dd, 95) * 100),
        }

    res_out = {
        "tax_kind": tax_kind,
        "calendar_lo": cal_lo,
        "calendar_hi": cal_hi,
        "n_paths_total": n_paths,
        "n_paths_finite": int(len(out["C0"]["cagr"])),
        "calendar_days": len(fixed_dates),
        "calendar_start": fixed_dates[0],
        "calendar_end": fixed_dates[-1],
        "c0_on_1x": bool(c0_on_1x),
        "C0": {k: [float(x) for x in v] for k, v in out["C0"].items()},
    }
    for arm in arms:
        if arm == "C0":
            continue
        res_out[arm] = {k: [float(x) for x in v] for k, v in out[arm].items()}
        res_out[f"{arm}_vs_C0"] = summarize_deltas(deltas[arm]["cagr"], deltas[arm]["dd"])
    res_out["negative_paths"] = int(neg_count)
    return res_out


# ----------------------------------------------------------------------------- gate and selection
def after_tax_gate(results, arm):
    """Pre-registered: CONFIRMED iff in B1-recent, B1-early AND B2 median after-tax
    Δ CAGR >= +0.50 pp and share of paths with Δ > 0 >= 65%."""
    per = {}
    for name in GATE_WINDOWS:
        s = results[name][f"{arm}_vs_C0"]
        med, share = s["delta_cagr_median_pp"], s["share_delta_gt_0"]
        per[name] = {"delta_cagr_median_pp": med, "share_delta_gt_0": share,
                     "median_ok": bool(med >= GATE_MEDIAN_PP), "share_ok": bool(share >= GATE_SHARE),
                     "pass": bool(med >= GATE_MEDIAN_PP and share >= GATE_SHARE)}
    return {"per_window": per, "pass": bool(all(v["pass"] for v in per.values()))}


# ----------------------------------------------------------------------------- verification
def verify_c0_unlevered(n_paths=BOOT_PATHS):
    """Amendment 3 check: (1) re-run B1-recent with tax_asym_v2's C0 pricing (unlevered QQQ) and
    compare the per-path C0 arrays with tax_asym_v2/RESULTS.json; (2) re-run it with the corrected
    2x C0 and report C0's median CAGR / max DD under both pricings."""
    v2 = json.loads(V2_RESULTS.read_text())["results"]
    replica = after_tax_bootstrap_variant(n_paths=n_paths, tax_kind="progressive",
                                          cal_lo=CAL_RECENT[0], cal_hi=CAL_RECENT[1],
                                          arms=("C0", "A2"), c0_on_1x=True)
    exact = {}
    for kind in ("cagr", "maxdd"):
        a = np.array(replica["C0"][kind], dtype=float)
        b = np.array(v2["B1_recent"]["C0"][kind], dtype=float)
        exact[f"C0_{kind}_max_abs_diff"] = float(np.max(np.abs(a - b))) if a.shape == b.shape else None
        exact[f"C0_{kind}_bit_identical"] = bool(a.shape == b.shape and np.array_equal(a, b))
    rep_a2 = replica["A2_vs_C0"]
    pub_a2 = v2["B1_recent"]["A2_vs_C0"]
    a2_match = {
        "replica_median_pp": rep_a2["delta_cagr_median_pp"],
        "v2_published_median_pp": pub_a2["delta_cagr_median_pp"],
        "replica_share": rep_a2["share_delta_gt_0"],
        "v2_published_share": pub_a2["share_delta_gt_0"],
        "median_abs_diff_pp": abs(rep_a2["delta_cagr_median_pp"] - pub_a2["delta_cagr_median_pp"]),
    }
    corrected = after_tax_bootstrap_variant(n_paths=n_paths, tax_kind="progressive",
                                           cal_lo=CAL_RECENT[0], cal_hi=CAL_RECENT[1],
                                           arms=("C0", "A2"), c0_on_1x=False)
    unl = np.array(replica["C0"]["maxdd"], dtype=float)
    cor = np.array(corrected["C0"]["maxdd"], dtype=float)
    return {
        "replica_bit_identical": exact,
        "replica_A2_vs_C0": a2_match,
        "C0_unlevered": {"median_cagr": float(np.median(replica["C0"]["cagr"])),
                         "median_max_dd": float(np.median(unl))},
        "C0_corrected_2x": {"median_cagr": float(np.median(corrected["C0"]["cagr"])),
                            "median_max_dd": float(np.median(cor))},
        "A2_vs_C0_unlevered_median_pp": rep_a2["delta_cagr_median_pp"],
        "A2_vs_C0_corrected_median_pp": corrected["A2_vs_C0"]["delta_cagr_median_pp"],
        "A2_vs_C0_corrected_share": corrected["A2_vs_C0"]["share_delta_gt_0"],
        "A2_vs_C0_corrected_median_dd_pp": corrected["A2_vs_C0"]["delta_maxdd_median_pp"],
    }


# ----------------------------------------------------------------------------- smoke (synthetic prices)
def smoke(n_paths=3):
    """Run the whole run path on a deterministic random walk; touches no real market data."""
    global ARR, Y
    keep_arr, keep_y = ARR, Y
    rng = np.random.default_rng(7)
    n = keep_arr["QQQ"].shape[1]
    lr = rng.normal(0.0003, 0.012, n)
    close = 100 * np.exp(np.cumsum(lr))
    open_ = close * np.exp(rng.normal(0.0, 0.002, n))
    div = np.zeros(n)
    ARR = {"QQQ": np.vstack([open_, close, div])}
    Y = np.full(n, 0.02)
    try:
        out = {}
        for kind in ("progressive", "flat", "pretax"):
            r = after_tax_bootstrap_variant(n_paths=n_paths, tax_kind=kind)
            out[kind] = {"n_paths_finite": r["n_paths_finite"], "negative_paths": r["negative_paths"],
                         "C0_median_cagr": float(np.median(r["C0"]["cagr"]))}
        gate = {arm: after_tax_gate({"B1_recent": r, "B1_early": r, "B2_flat": r}, arm)["pass"]
                for arm in CANDIDATES}
        return {"n_paths": n_paths, "variants": out, "gate_runs": gate, "pass": True}
    finally:
        ARR, Y = keep_arr, keep_y


# ----------------------------------------------------------------------------- main
def main_run():
    hc = harness()
    print(f"harness {'PASS' if hc['pass'] else 'FAIL'}: band==simulate_multi "
          f"{hc['band_simulator_equals_simulate_multi']}, after-tax/pre-tax NAV rel diff "
          f"{hc['after_tax_pre_tax_nav_max_rel_diff']:.3e}, trades {hc['after_tax_engine_trades']}")
    if not hc["pass"]:
        raise SystemExit("harness failed")

    verification = verify_c0_unlevered(BOOT_PATHS)
    print("verification done")

    results = {}
    for name, kind, cal in (("B1_recent", "progressive", CAL_RECENT),
                            ("B1_early", "progressive", CAL_EARLY),
                            ("B2_flat", "flat", CAL_RECENT),
                            ("pretax", "pretax", CAL_RECENT)):
        print(f"{name} ...", flush=True)
        results[name] = after_tax_bootstrap_variant(n_paths=BOOT_PATHS, tax_kind=kind,
                                                    cal_lo=cal[0], cal_hi=cal[1])

    gates = {arm: after_tax_gate({k: results[k] for k in GATE_WINDOWS}, arm) for arm in CANDIDATES}
    a2_pass = gates["A2"]["pass"]
    neighbours = [a for a in ("A2_40", "A2_60") if gates[a]["pass"]]
    if not a2_pass:
        verdict, plateau = "FAIL", "n/a"
    elif neighbours:
        verdict, plateau = f"PASS {', '.join(neighbours)}", "robust"
    else:
        verdict, plateau = "PASS A2", "weak"
    selection = {"verdict": verdict, "primary": "A2", "primary_pass": bool(a2_pass),
                 "plateau": plateau, "neighbour_pass": neighbours}

    doc = {
        "study": "tax_asym_v3",
        "question": "Does A2 still beat C0 on the clean after-tax bootstrap with C0 on the same 2x synthetic?",
        "harness": hc,
        "verification": verification,
        "config": {"arms": list(ARMS), "arm_k": ARM_K, "gate_median_pp": GATE_MEDIAN_PP,
                   "gate_share": GATE_SHARE, "paths": BOOT_PATHS, "capital": BOOT_CAPITAL,
                   "seed": SEED, "block": BLOCK, "measured_years": 10,
                   "calendars": {"recent": CAL_RECENT, "early": CAL_EARLY}, "cost_per_side_m2": COST},
        "results": results,
        "gates": gates,
        "selection": selection,
    }
    OUT.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")

    L = ["tax_asym_v3 — corrected-C0 rerun of tax_asym_v2's after-tax bootstrap",
         f"verdict: {verdict} (plateau {plateau})"]
    L.append("arm      | " + " | ".join(f"{w} med/sh" for w in GATE_WINDOWS))
    for arm in CANDIDATES:
        row = [f"{results[w][f'{arm}_vs_C0']['delta_cagr_median_pp']:+.3f}/"
               f"{results[w][f'{arm}_vs_C0']['share_delta_gt_0']:.3f}" for w in GATE_WINDOWS]
        L.append(f"{arm:8s} | " + " | ".join(row) + f"  -> {'PASS' if gates[arm]['pass'] else 'FAIL'}")
    for w in GATE_WINDOWS + ("pretax",):
        L.append(f"{w}: C0 median CAGR {np.median(results[w]['C0']['cagr'])*100:.3f}% "
                 f"median max DD {np.median(results[w]['C0']['maxdd'])*100:.2f}%")
    for arm in CANDIDATES:
        d = results["B1_recent"][f"{arm}_vs_C0"]
        L.append(f"B1_recent {arm}: median ΔmaxDD {d['delta_maxdd_median_pp']:+.2f} pp "
                 f"[p05 {d['delta_maxdd_p05_pp']:+.2f}, p95 {d['delta_maxdd_p95_pp']:+.2f}]; "
                 f"ΔCAGR p05 {d['p05_delta_cagr_pp']:+.3f} p95 {d['p95_delta_cagr_pp']:+.3f}")
        p = results["pretax"][f"{arm}_vs_C0"]
        L.append(f"pretax (same paths) {arm}: median ΔCAGR {p['delta_cagr_median_pp']:+.3f} pp "
                 f"share>0 {p['share_delta_gt_0']:.3f}")
    v = verification
    L.append(f"verification: v2 replica C0 arrays bit-identical "
             f"{v['replica_bit_identical']['C0_cagr_bit_identical']}/"
             f"{v['replica_bit_identical']['C0_maxdd_bit_identical']}; "
             f"C0 median CAGR {v['C0_unlevered']['median_cagr']*100:.3f}% (unlevered) vs "
             f"{v['C0_corrected_2x']['median_cagr']*100:.3f}% (2x); median max DD "
             f"{v['C0_unlevered']['median_max_dd']*100:.2f}% vs {v['C0_corrected_2x']['median_max_dd']*100:.2f}%")
    SUMMARY.write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--harness":
        h = harness()
        for k, v in h["deviation_checks"].items():
            print(f"harness {k}: ref {v['reference']:.8f} repro {v['reproduced']:.8f} diff {v['diff_pp']:+.6f} pp")
        print(json.dumps({k: v for k, v in h.items() if k != "deviation_checks"}, indent=1))
        sys.exit(0 if h["pass"] else 1)
    elif mode == "--verify":
        print(json.dumps(verify_c0_unlevered(BOOT_PATHS), indent=1))
    elif mode == "--smoke":
        print(json.dumps(smoke(), indent=1))
    elif mode == "--run":
        main_run()
    else:
        print(__doc__)
        sys.exit(2)
