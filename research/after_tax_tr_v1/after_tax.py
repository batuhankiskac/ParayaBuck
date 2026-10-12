#!/usr/bin/env python3
"""after_tax_tr_v1: E2 / VT35 / SPY / QQQ after Turkish personal income tax (see PREREGISTRATION.md).

Offline. Reads data/historical/stress_robustness_v1_{daily,dtb3}.csv through the archived
signal_ensemble_v1 harness (se1_screen.py = byte copy of
archive/research/signal-ensemble-v1-2026-10:research/signal_ensemble_v1/screen.py, which imports
research/trend_robustness_v2/screen.py), plus data/historical/after_tax_tr_v1_{usdtry_cbrt,yiufe}.csv.

Modes
  --check   pre-tax reproduction only: the dollar/lot simulator must reproduce the harness NAV of
            VT35 (C0) and production E2 (price TSM, zero hurdle, price channel) to 1e-9 relative,
            and the published 13.39% / 14.67% CAGR and 49.6% max DD (m = 2, base financing).
  --run     the check, then every arm x window x regime x capital; writes RESULTS.json, SUMMARY.txt.

Tax model (PREREGISTRATION.md has the sources):
  * One USD cash account. QLD* is the harness synthetic 2x QQQ (total return embedded), modelled as
    non-distributing. SPY / QQQ buy-and-hold carry their actual cash dividends.
  * FIFO lots. TRY cost = USD cost (incl. commission) x CBRT USD ForexBuying on the buy date; TRY
    proceeds = USD proceeds (net of commission) x the sale date's rate. Cost is indexed by Yi-UFE
    (month before sale / month before purchase) when that rise is >= 10% (GVK mukerrer 81);
    indexation can cut a gain to zero but never creates or enlarges a loss.
  * Gains and losses net within a calendar year; a net loss is lost (no carry-forward).
  * Dividends: 20% US withholding (treaty Art. 10, RIC). The gross TRY dividend is declared only when
    the year's total exceeds the GVK 86 threshold (22,000 TL in 2026, deflated); then it joins the
    base and the US tax is credited up to the Turkish tax on it (GVK 123).
  * Tariff: GVK 103 non-wage 2026 brackets, deflated to year Y by Yi-UFE Oct(Y-1)/Oct(2025);
    or a flat-rate grid. Tax for year Y is paid from the account in two equal instalments at the
    first US session on/after 31 March and 31 July of Y+1, selling FIFO lots at the open if cash
    is short. Those sales are taxable in Y+1.
  * Liquidation view: every lot sold at the last close of the window (cost m x 4 bps); the year's
    tax and any unpaid instalment are deducted at the last close's rate.
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
_spec = importlib.util.spec_from_file_location("se1_screen", HERE / "se1_screen.py")
se = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(se)
trv2 = se.trv2

FX_CSV = ROOT / "data/historical/after_tax_tr_v1_usdtry_cbrt.csv"
UFE_CSV = ROOT / "data/historical/after_tax_tr_v1_yiufe.csv"
OUT = HERE / "RESULTS.json"
SUMMARY = HERE / "SUMMARY.txt"

# ----------------------------------------------------------------------------- pre-registered constants
M = 2  # cost multiplier (4 bps x m per side): the e2_integration_v1 headline
COST = se.COST_PER_SIDE * M
FIN_SPREAD = 0.0  # base financing
BAND = trv2.BAND  # 0.5 pp
# GVK 103, income other than wages, 2026 (GVK Genel Tebligi Seri No. 332, RG 31.12.2025 / 33124 5. mukerrer)
TARIFF_2026 = ((190_000.0, 0.15), (400_000.0, 0.20), (1_000_000.0, 0.27), (5_300_000.0, 0.35), (math.inf, 0.40))
DIV_THRESHOLD_2026 = 22_000.0  # GVK 86/1: non-withheld MSI + GMSI declared only above this
ANCHOR = "2025-10"  # 2026 amounts follow the revaluation rate measured to October 2025
US_DIV_WHT = 0.20
INDEX_MIN_RISE = 0.10
FLAT_RATES = (0.15, 0.27, 0.35, 0.40)
CAPITALS = (10_000.0, 100_000.0)
WINDOWS = {
    "2000_03_to_2026_09": ("2000-03-09", "2026-09-29"),
    "2010_01_to_2026_09": ("2010-01-04", "2026-09-29"),
    "2017_01_to_2026_07": ("2017-01-03", "2026-07-30"),
}
ARMS = ("E2", "VT35", "SPY", "QQQ")
E2_BANDS = {"E2_band5": 0.05, "E2_band10": 0.10, "E2_no_resize": math.inf}
TOL_REL = 1e-9


# ----------------------------------------------------------------------------- data
def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def prev_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


class Market:
    def __init__(self):
        days, arr, y = trv2.load()
        self.days = days
        q = arr["QQQ"]
        self.o, self.c = trv2.synthetic_2x(q[0], q[1], q[2], y + FIN_SPREAD, se.EXP_Q)
        vol = trv2.vol_n(self.c)
        zero = se.bill_level(0.0 * y)
        sig = {"SMA": trv2.sma_up(q[1], se.SMA_N), "TSM": se.tsm_up(q[1], zero), "DON": se.don_up(q[1])}
        self.wants = {"VT35": se.build_wants(sig, vol, "C0")[0, 0], "E2": se.build_wants(sig, vol, "E2")[0, 0]}
        self.bh = {s: (arr[s][0], arr[s][1], arr[s][2]) for s in ("SPY", "QQQ")}
        fd, fv = [], []
        with FX_CSV.open() as fh:
            for r in csv.DictReader(fh):
                fd.append(r["date"])
                fv.append(float(r["usdtry_forex_buying"]))
        # rate of the trade date; without a bulletin that day (Turkish holiday) the latest earlier one
        self.fx = np.array([fv[bisect.bisect_right(fd, d) - 1] for d in days])
        self.fx_same_day = float(np.mean([d in set(fd) for d in days[se.START - 1:]]))
        with UFE_CSV.open() as fh:
            self.ufe = {r["month"]: float(r["yiufe_index"]) for r in csv.DictReader(fh)}

    def scale(self, year: int) -> float:
        """Deflator for 2026 TRY amounts to tax year `year`."""
        return self.ufe[f"{year - 1}-10"] / self.ufe[ANCHOR]


# ----------------------------------------------------------------------------- tax regimes
def tariff_tax(base: float, scale: float) -> float:
    tax, lo = 0.0, 0.0
    for hi, rate in TARIFF_2026:
        hi = hi * scale
        if base <= lo:
            break
        tax += (min(base, hi) - lo) * rate
        lo = hi
    return tax


class Regime:
    def __init__(self, name: str, kind: str, rate: float = 0.0, indexation: bool = True, index_base: str = "prev"):
        self.name, self.kind, self.rate, self.indexation, self.index_base = name, kind, rate, indexation, index_base

    def tax(self, base: float, year: int, mkt: Market) -> float:
        if self.kind == "none" or base <= 0:
            return 0.0
        if self.kind == "flat":
            return base * self.rate
        return tariff_tax(base, mkt.scale(year))


REGIMES = {
    "pre_tax": Regime("pre_tax", "none"),
    "progressive": Regime("progressive", "tariff"),
    **{f"flat_{int(r * 100)}": Regime(f"flat_{int(r * 100)}", "flat", r) for r in FLAT_RATES},
    "progressive_no_indexation": Regime("progressive_no_indexation", "tariff", indexation=False),
    "progressive_index_purchase_month": Regime("progressive_index_purchase_month", "tariff", index_base="same"),
}


# ----------------------------------------------------------------------------- account
class Account:
    """USD cash + FIFO lots of one security. Lot: [units, usd cost per unit, fx at buy, buy month]."""

    def __init__(self, mkt: Market, regime: Regime, cash: float):
        self.mkt, self.rg = mkt, regime
        self.cash = cash
        self.lots: list[list] = []
        self.units = 0.0
        self.year_gain = defaultdict(float)  # net TRY gain (after indexation) per year
        self.year_div = defaultdict(float)  # gross dividends, TRY
        self.year_wht = defaultdict(float)  # US withholding, TRY
        self.gain_by_reason = defaultdict(float)
        self.pos_gain_by_reason = defaultdict(float)
        self.neg_gain_by_reason = defaultdict(float)
        self.index_relief_try = 0.0
        self.tax_due: dict[int, float] = {}
        self.tax_paid_try = defaultdict(float)
        self.tax_paid_usd = 0.0
        self.wht_usd = 0.0
        self.fees = 0.0

    def buy(self, i: int, price: float, usd: float, fee: float):
        if usd <= 0:
            return
        u = usd / price
        self.cash -= usd + fee
        self.fees += fee
        self.units += u
        self.lots.append([u, (usd + fee) / u, self.mkt.fx[i], self.mkt.days[i][:7]])

    def sell(self, i: int, price: float, units: float, fee: float, reason: str):
        units = min(units, self.units)
        if units <= 0:
            return
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
                base_m = prev_month(lot[3]) if self.rg.index_base == "prev" else lot[3]
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

    def dividend(self, i: int, per_unit: float) -> float:
        if per_unit <= 0 or self.units <= 0:
            return 0.0
        gross = self.units * per_unit
        wht = gross * US_DIV_WHT if self.rg.kind != "none" else 0.0
        fx = self.mkt.fx[i]
        y = int(self.mkt.days[i][:4])
        self.year_div[y] += gross * fx
        self.year_wht[y] += wht * fx
        self.wht_usd += wht
        self.cash += gross - wht
        return gross - wht

    def assess(self, year: int) -> float:
        g = max(0.0, self.year_gain.get(year, 0.0))
        d = self.year_div.get(year, 0.0)
        if self.rg.kind == "none":
            return 0.0
        if d <= DIV_THRESHOLD_2026 * self.mkt.scale(year):
            d = 0.0  # not declared; the US withholding is final
        base = g + d
        tax = self.rg.tax(base, year, self.mkt)
        if d > 0 and base > 0:
            tax -= min(self.year_wht.get(year, 0.0), tax * d / base)
        return max(0.0, tax)

    def outstanding(self) -> float:
        return sum(v - self.tax_paid_try[y] for y, v in self.tax_due.items())


# ----------------------------------------------------------------------------- simulation
def instalments(days, i0, i1):
    out = defaultdict(list)
    for y in range(int(days[i0][:4]), int(days[i1][:4]) + 1):
        for md, part in (("03-31", 0), ("07-31", 1)):
            k = bisect.bisect_left(days, f"{y}-{md}")
            if i0 <= k <= i1:
                out[k].append((y - 1, part))
    return out


def run_arm(mkt: Market, arm: str, rg: Regime, capital: float, a: str, b: str, band: float = BAND) -> dict:
    days = mkt.days
    i0 = bisect.bisect_left(days, a)
    i1 = bisect.bisect_right(days, b) - 1
    acct = Account(mkt, rg, capital)
    pays = instalments(days, i0, i1)
    nav = np.empty(i1 - i0 + 2)
    nav[0] = capital
    bh = arm in ("SPY", "QQQ")
    if bh:
        po, pc, pdv = mkt.bh[arm]
        acct.buy(i0 - 1, pc[i0 - 1], capital, 0.0)  # at the close before the first session, as the harness
    else:
        po, pc = mkt.o, mkt.c
        want = mkt.wants["VT35" if arm == "VT35" else "E2"]
    trades = tax_sales = 0
    for i in range(i0, i1 + 1):
        y = int(days[i][:4])
        if i > i0 and y != int(days[i - 1][:4]):
            acct.tax_due[y - 1] = acct.assess(y - 1)
        for ty, part in pays.get(i, ()):
            if ty not in acct.tax_due:
                continue
            amt = acct.tax_due[ty] / 2 if part == 0 else acct.tax_due[ty] - acct.tax_paid_try[ty]
            if amt <= 0:
                continue
            usd = amt / mkt.fx[i]
            if acct.cash < usd:
                need = (usd - acct.cash) / (1 - COST)
                acct.sell(i, po[i], need / po[i], need * COST, "tax")
                tax_sales += 1
            acct.cash -= usd
            acct.tax_paid_try[ty] += amt
            acct.tax_paid_usd += usd
        if bh:
            if pdv[i] > 0:
                acct.buy(i, pc[i], acct.dividend(i, pdv[i]), 0.0)  # reinvested at the ex-date close
        else:
            nav_open = acct.cash + acct.units * po[i]
            w = acct.units * po[i] / nav_open
            tgt = want[i - 1]
            flip = (tgt > 0) != (acct.units > 0)
            if flip or abs(tgt - w) > band:
                fee = nav_open * COST * abs(tgt - w)
                du = tgt * (nav_open - fee) / po[i] - acct.units
                if tgt == 0:
                    acct.sell(i, po[i], acct.units, fee, "exit")
                elif du > 0:
                    acct.buy(i, po[i], du * po[i], fee)
                elif du < 0:
                    acct.sell(i, po[i], -du, fee, "resize")
                trades += 1
        nav[i - i0 + 1] = acct.cash + acct.units * pc[i]
    end_y = int(days[i1][:4])
    fx0, fx1 = mkt.fx[i0 - 1], mkt.fx[i1]
    yrs = trv2.calendar_years(days[i0 - 1], days[i1])
    accrued = acct.assess(end_y) + acct.outstanding()  # realised but unpaid, TRY
    mtm = nav[-1] - accrued / fx1
    gains_pre_liq = dict(acct.gain_by_reason)
    unreal_usd = acct.units * pc[i1] - sum(l[0] * l[1] for l in acct.lots)
    if acct.units > 0:
        acct.sell(i1, pc[i1], acct.units, acct.units * pc[i1] * COST, "liquidation")
    liq_tax = acct.assess(end_y) + acct.outstanding()
    liq = acct.cash - liq_tax / fx1
    tax_total_try = sum(acct.tax_paid_try.values()) + liq_tax
    tax_total_usd = acct.tax_paid_usd + liq_tax / fx1
    cg = lambda v0, v1: (v1 / v0) ** (1 / yrs) - 1  # noqa: E731
    return {
        "window": [days[i0], days[i1]],
        "years": yrs,
        "capital_usd": capital,
        "fx_start": float(fx0),
        "fx_end": float(fx1),
        "ending_nav_usd": float(nav[-1]),
        "max_dd_nav_usd": float(trv2.max_dd(nav)[0]),
        "cagr_usd_liquidated": float(cg(capital, liq)),
        "cagr_try_liquidated": float(cg(capital * fx0, liq * fx1)),
        "terminal_usd_liquidated": float(liq),
        "terminal_try_liquidated": float(liq * fx1),
        "cagr_usd_unliquidated": float(cg(capital, mtm)),
        "cagr_try_unliquidated": float(cg(capital * fx0, mtm * fx1)),
        "terminal_usd_unliquidated": float(mtm),
        "unrealised_gain_usd_at_end": float(unreal_usd),
        "tax_total_try": float(tax_total_try),
        "tax_total_usd_at_payment_fx": float(tax_total_usd),
        "tax_at_liquidation_try": float(liq_tax - accrued),
        "tax_by_year_try": {str(k): float(v) for k, v in sorted(acct.tax_due.items())},
        "us_dividend_withholding_usd": float(acct.wht_usd),
        "realised_gain_try_by_reason_pre_liquidation": {k: float(v) for k, v in sorted(gains_pre_liq.items())},
        "realised_gain_try_by_reason": {k: float(v) for k, v in sorted(acct.gain_by_reason.items())},
        "positive_lot_gain_try_by_reason": {k: float(v) for k, v in sorted(acct.pos_gain_by_reason.items())},
        "negative_lot_gain_try_by_reason": {k: float(v) for k, v in sorted(acct.neg_gain_by_reason.items())},
        "indexation_relief_try": float(acct.index_relief_try),
        "fees_usd": float(acct.fees),
        "strategy_trades": trades,
        "tax_funding_sales": tax_sales,
        "_nav": nav,
    }


# ----------------------------------------------------------------------------- check
def check(mkt: Market) -> dict:
    out = {}
    pub = {"VT35": (0.1339, 0.496), "E2": (0.1467, 0.496)}
    for arm in ("VT35", "E2"):
        ref, _, _ = trv2.simulate_multi(mkt.o, mkt.c, mkt.wants[arm][None, :], COST, se.START)
        ref = ref[0]
        r = run_arm(mkt, arm, REGIMES["pre_tax"], 1.0, mkt.days[se.START], mkt.days[-1])
        rel = float(np.max(np.abs(r["_nav"] / ref - 1)))
        st = trv2.curve_stats(mkt.days[se.START - 1:], ref)
        ok = rel < TOL_REL and round(st["cagr"], 4) == pub[arm][0] and round(st["max_dd"], 3) == pub[arm][1]
        out[arm] = {"max_rel_nav_diff_vs_harness": rel, "cagr": st["cagr"], "max_dd": st["max_dd"], "pass": bool(ok)}
    out["pass"] = all(v["pass"] for v in out.values())
    return out


# ----------------------------------------------------------------------------- run
def strip(r: dict) -> dict:
    return {k: v for k, v in r.items() if not k.startswith("_")}


def run_all(mkt: Market, chk: dict) -> dict:
    res = {"windows": {}}
    for wn, (a, b) in WINDOWS.items():
        wres = {}
        for cap in CAPITALS:
            ck = f"usd_{int(cap)}"
            for rn, rg in REGIMES.items():
                if rn.startswith("progressive_") and cap != 100_000.0:
                    continue
                for arm in ARMS + tuple(E2_BANDS):
                    band = E2_BANDS.get(arm, BAND)
                    base_arm = "E2" if arm in E2_BANDS else arm
                    r = run_arm(mkt, base_arm, rg, cap, a, b, band)
                    wres.setdefault(ck, {}).setdefault(rn, {})[arm] = strip(r)
        res["windows"][wn] = wres
    # derived: drag and edges
    for wn, wres in res["windows"].items():
        for ck, cres in wres.items():
            pre = cres["pre_tax"]
            for rn, rres in cres.items():
                for arm, r in rres.items():
                    r["tax_drag_pp_usd"] = 100 * (pre[arm]["cagr_usd_liquidated"] - r["cagr_usd_liquidated"])
                    r["tax_drag_pp_try"] = 100 * (pre[arm]["cagr_try_liquidated"] - r["cagr_try_liquidated"])
                for bm in ("SPY", "QQQ"):
                    rres["E2"][f"edge_pp_usd_vs_{bm}_liquidated"] = 100 * (rres["E2"]["cagr_usd_liquidated"] - rres[bm]["cagr_usd_liquidated"])
                    rres["E2"][f"edge_pp_usd_vs_{bm}_unliquidated"] = 100 * (rres["E2"]["cagr_usd_unliquidated"] - rres[bm]["cagr_usd_unliquidated"])
    res["harness_check"] = chk
    res["config"] = {
        "cost_multiplier_m": M, "cost_per_side": COST, "band": BAND, "financing": "base (DTB3 + 0)",
        "tariff_2026": [[None if math.isinf(h) else h, r] for h, r in TARIFF_2026],
        "dividend_threshold_2026_try": DIV_THRESHOLD_2026, "deflator_anchor": ANCHOR,
        "us_dividend_withholding": US_DIV_WHT, "indexation_min_rise": INDEX_MIN_RISE,
        "windows": WINDOWS, "capitals_usd": CAPITALS, "e2_band_variants": {k: (None if math.isinf(v) else v) for k, v in E2_BANDS.items()},
        "fx_share_sessions_with_same_day_cbrt_bulletin": mkt.fx_same_day,
        "bracket_scale_by_year": {str(y): mkt.scale(y) for y in range(2000, 2027)},
    }
    res["inputs_sha256"] = {
        p: sha256(ROOT / p) for p in (
            "data/historical/stress_robustness_v1_daily.csv", "data/historical/stress_robustness_v1_dtb3.csv",
            "data/historical/after_tax_tr_v1_usdtry_cbrt.csv", "data/historical/after_tax_tr_v1_yiufe.csv",
            "research/trend_robustness_v2/screen.py", "research/after_tax_tr_v1/se1_screen.py",
        )
    }
    return res


def pct(x):
    return f"{100 * x:6.2f}%"


def summary(res: dict) -> str:
    L = ["after_tax_tr_v1 summary (m = 2, base financing; liquidated = all lots sold and taxed at the window end)", ""]
    chk = res["harness_check"]
    L.append(f"harness check: VT35 {pct(chk['VT35']['cagr'])} / DD {pct(chk['VT35']['max_dd'])}, "
             f"E2 {pct(chk['E2']['cagr'])} / DD {pct(chk['E2']['max_dd'])}, "
             f"max rel NAV diff {max(chk['VT35']['max_rel_nav_diff_vs_harness'], chk['E2']['max_rel_nav_diff_vs_harness']):.1e} -> {'PASS' if chk['pass'] else 'FAIL'}")
    for wn, wres in res["windows"].items():
        for ck, cres in wres.items():
            L += ["", f"== {wn}  capital {ck}"]
            L.append(f"{'regime':34s} {'arm':13s} {'CAGR USD':>9s} {'CAGR TRY':>9s} {'drag pp':>8s} {'end USD':>13s} {'tax TRY':>15s} {'unliq USD':>9s}")
            for rn, rres in cres.items():
                for arm, r in rres.items():
                    L.append(f"{rn:34s} {arm:13s} {pct(r['cagr_usd_liquidated']):>9s} {pct(r['cagr_try_liquidated']):>9s} "
                             f"{r['tax_drag_pp_usd']:8.2f} {r['terminal_usd_liquidated']:13,.0f} {r['tax_total_try']:15,.0f} "
                             f"{pct(r['cagr_usd_unliquidated']):>9s}")
                e = rres["E2"]
                L.append(f"{'':34s} E2 edge vs SPY {e['edge_pp_usd_vs_SPY_liquidated']:+.2f} pp, vs QQQ {e['edge_pp_usd_vs_QQQ_liquidated']:+.2f} pp (liquidated)")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--run", action="store_true")
    args = ap.parse_args()
    mkt = Market()
    chk = check(mkt)
    print(json.dumps(chk, indent=2))
    if not chk["pass"]:
        raise SystemExit("harness check failed")
    if args.check:
        return
    res = run_all(mkt, chk)
    OUT.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
    SUMMARY.write_text(summary(res))
    print(summary(res))


if __name__ == "__main__":
    main()
