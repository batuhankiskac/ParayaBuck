"""riskoff_ref -- `riskoff_after_tax_v1`'s after-tax engine, copied verbatim as the parity (b)
oracle of the two-leg kernel.

Provenance: the code below is copied from
`research/riskoff_after_tax_v1/screen.py` at tag `archive/research/riskoff-after-tax-v1-2026-10`
(sha256 `abc4a02066ed8960302db83c4bc619ff2611ee96c18b73379feb3c17a6bd72d7`): `tariff_tax`,
`Regime`, `BaseAccount`, `instalments`, the constants they read, and the two-leg core of
`run_strategy`'s session loop. Three edits only:

* `Market` is reduced to what the account actually reads (days, fx, ufe, `scale(year)`) --
  the copied one also loads `trv2`/`leverage_frontier_v1`/`riskoff_rotation_v3` to *build* the
  strategy's price paths, which the parity case supplies directly instead;
* `run_two_leg` takes the `(legs, T)` open/close/target arrays as arguments instead of calling
  `v3.main_legs_and_wants`, and its `cost` is a parameter (the archived code uses the module
  constant `COST = trv2.COST_PER_SIDE * M`, i.e. 8 bp at m = 2 -- the same number);
* the reporting tail drops the fields the parity case does not compare (TRY-denominated CAGRs,
  the "defensive"/instrument labels) and keeps every number it does, and adds one instrumentation
  counter that the archived code does not have: `BaseAccount.trades` counts the sessions in which
  that leg's fill rule fired (its leg-0 rule or its forced/10 pp rule), so a parity cell can
  compare a per-leg trade count with the two-leg kernel's per-leg counter. It changes no fill,
  no fee and no tax number (the archived `trades` scalar is kept unchanged as well).

Conventions of that engine that the two-leg kernel **does not** share (they are why parity (b)
is run on a fixed synthetic case built so that they cannot differ, and why the published L2
cells are not the comparison target -- see `parity_b.py` and `docs/research/two_leg_kernel_v1.md`):

1. **Assessment is per account, not pooled.** `run_strategy` sums `assess(year)` over the
   accounts, and each account floors its own annual gain at zero, so a loss in one leg does not
   offset a gain in the other and the tariff's brackets apply to each leg's gain separately.
   The two-leg kernel pools every leg's realisation into one taxpayer's annual net TRY gain
   (`fe_sim._assess` over a shared `year_gain` row). Equal exactly when at most one leg realises
   in a tax year -- which the fixed case arranges.
2. **The off-equity legs trade on a 10 pp band, not 0.5 pp** (`drift[l] > 0.10` for `l > 0`,
   plus a forced fill whenever holding flips), and leg 0 uses 0.5 pp. The kernel uses 0.5 pp for
   both legs (plus the A1u/H2 rule options). Equal when every target change is far above 10 pp --
   which the fixed case arranges (0.98 <-> 0 or 0.98 <-> 0.60).
3. **Every leg is resized to the post-cost NAV whenever any leg trades** (`du = tgt *
   nav_after_cost / po - units` for all `l`), so a leg that did not trigger is still nudged by
   the other leg's fee. The kernel executes only the legs whose own rule fired. Equal when the
   non-trading leg holds no units on every trade session -- which the fixed case arranges.
4. No A1u gain band, no H2 December deferral, no idle-cash yield, no per-leg rule options: the
   reference is the plain-band engine. The fixed case therefore runs the kernel with rule `C0`.
5. Its `L2` arm is a *three*-leg portfolio (QLD\\* + BOND10\\* + GOLD\\* built by
   `leverage_frontier_v1`, with a monthly bond/gold choice and a total-weight cap), so the
   published L2 cells are not reproducible with two legs at all; that is recorded in the study
   doc rather than approximated here.
"""

from __future__ import annotations

import bisect
import math
from collections import defaultdict
from datetime import date as _date

import numpy as np

# ---- copied constants (screen.py:37-45)
TARIFF_2026 = ((190_000.0, 0.15), (400_000.0, 0.20), (1_000_000.0, 0.27), (5_300_000.0, 0.35),
               (math.inf, 0.40))
DIV_THRESHOLD_2026 = 22_000.0
ANCHOR = "2025-10"
US_DIV_WHT = 0.20
INDEX_MIN_RISE = 0.10


def calendar_years(d0, d1):
    """`trend_robustness_v2.calendar_years` (the archived engine's `yrs`)."""
    return (_date.fromisoformat(d1) - _date.fromisoformat(d0)).days / 365.25


def prev_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


# ---- copied verbatim (screen.py:104-111)
def tariff_tax(base: float, scale: float) -> float:
    tax, lo = 0.0, 0.0
    for hi, rate in TARIFF_2026:
        hi = hi * scale if not math.isinf(hi) else math.inf
        if base <= lo:
            break
        tax += (min(base, hi) - lo) * rate
        lo = hi
    return tax


# ---- copied verbatim (screen.py:112-127)
class Regime:
    def __init__(self, name, kind, rate=0.0, indexation=True, index_base="prev"):
        self.name, self.kind, self.rate, self.indexation, self.index_base = (
            name, kind, rate, indexation, index_base)

    def tax(self, base, year, mkt):
        if self.kind == "none" or base <= 0:
            return 0.0
        if self.kind == "flat":
            return base * self.rate
        return tariff_tax(base, mkt.scale(year))


REGIMES = {"progressive": Regime("progressive", "tariff")}


class Market:
    """`screen.Market` reduced to what `BaseAccount` reads (`scale` is `Market.scale`)."""

    def __init__(self, days, fx, ufe):
        self.days = list(days)
        self.fx = np.asarray(fx, dtype=float)
        self.ufe = dict(ufe)
        self.scale_cache = {}

    def scale(self, year: int) -> float:
        if year not in self.scale_cache:
            self.scale_cache[year] = self.ufe[f"{year - 1}-10"] / self.ufe[ANCHOR]
        return self.scale_cache[year]


class BaseAccount:
    """Copied verbatim from `screen.py:129-233` (`buy`, `sell`, `assess`, `outstanding`)."""

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
        # instrumentation only (not in the archived code): sessions whose fill rule fired
        self.trades = 0

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


# ---- copied verbatim (screen.py:236-243)
def instalments(days, i0, i1):
    out = defaultdict(list)
    for y in range(int(days[i0][:4]), int(days[i1][:4]) + 1):
        for md, part in (("03-31", 0), ("07-31", 1)):
            k = bisect.bisect_left(days, f"{y}-{md}")
            if i0 <= k <= i1:
                out[k].append((y - 1, part))
    return out


def run_two_leg(mkt, regime_name, capital, i0, i1, opens, closes, wants, cost, band=0.005,
                band_off=0.10, indexation=None):
    """`run_strategy`'s session loop over two legs, arrays injected.

    `opens`/`closes`/`wants` are `(nleg, T)`; `opens[:, i]` is the fill price of the session whose
    target was decided at the close of `i - 1`. Everything else is the archived code -- including
    the per-leg rule (leg 0: `drift > band`; legs > 0: forced flip or `drift > band_off`), the
    "resize every leg to the post-cost NAV on any trade" fill, `cashbox` shared by the accounts,
    the March/July instalments funded by the first leg with units, and the per-account assessment.
    """
    regime = Regime(regime_name, "tariff") if regime_name == "progressive" else Regime(regime_name, "none")
    if regime_name != "progressive":
        regime = Regime("pre_tax", "none")
    if indexation is not None:
        # the archived `Regime` defaults to `indexation=True` even for its pre-tax regime, so the
        # pre-tax run reports *indexation-adjusted* realised gains (and pays no tax on them, so the
        # NAV is unaffected). The two-leg kernel computes nominal gains in pre-tax mode; the parity
        # case therefore switches the reference's pre-tax indexation off (it cannot move any NAV,
        # cash, fee or tax number -- `assess` returns 0 for kind "none" before it reads a gain).
        regime = Regime(regime.name, regime.kind, regime.rate, indexation=indexation,
                        index_base=regime.index_base)
    days_all = mkt.days
    opens = np.asarray(opens, dtype=float)
    closes = np.asarray(closes, dtype=float)
    wants = np.asarray(wants, dtype=float)
    nleg = opens.shape[0]

    cashbox = {"v": float(capital)}
    accounts = [BaseAccount(mkt, regime, 0.0, cashbox) for _ in range(nleg)]
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
            total_assess = sum(acct.assess(prevy) for acct in accounts)
            accounts[0].tax_due[prevy] = total_assess
            for acct in accounts[1:]:
                acct.tax_due[prevy] = 0.0
        for ty, part in pays.get(i, ()):
            total_due = sum(acct.tax_due.get(ty, 0.0) for acct in accounts)
            if ty not in accounts[0].tax_due and total_due == 0:
                continue
            if ty not in accounts[0].tax_due:
                accounts[0].tax_due[ty] = total_due
            amt_total = accounts[0].tax_due[ty] / 2 if part == 0 else accounts[0].tax_due[ty] - sum(a.tax_paid_try.get(ty, 0.0) for a in accounts)
            if amt_total <= 0:
                continue
            usd = amt_total / mkt.fx[i]
            if cashbox["v"] < usd:
                need = (usd - cashbox["v"]) / (1 - cost)
                for li in range(nleg):
                    if accounts[li].units > 1e-12:
                        accounts[li].sell(i, opens[li, i], need / opens[li, i], need * cost, "tax")
                        tax_sales += 1
                        break
            cashbox["v"] -= usd
            accounts[0].tax_paid_try[ty] += amt_total
            accounts[0].tax_paid_usd += usd

        po = opens[:, i]
        held_units = np.array([accounts[l].units for l in range(nleg)], dtype=float)
        held_value = held_units * po
        nav_open = cashbox["v"] + float(np.nansum(held_value))
        if nav_open <= 0:
            nav_open = cashbox["v"]
        w = np.zeros(nleg, dtype=float)
        if nav_open > 0:
            w = (held_units * po) / nav_open
        tgt = wants[:, i - 1] if i > 0 else wants[:, 0]
        tgt = np.nan_to_num(tgt, nan=0.0)
        drift = np.abs(tgt - w)

        trade_leg = np.zeros(nleg, dtype=bool)
        trade_leg[0] = drift[0] > band
        for l in range(1, nleg):
            heldl = w[l]
            desl = tgt[l]
            forced = (heldl > 1e-9) != (desl > 1e-9)
            trade_leg[l] = bool(forced) or (drift[l] > band_off)
        for l in range(nleg):
            if trade_leg[l]:
                accounts[l].trades += 1

        trade = bool(trade_leg.any())
        if trade:
            dsum = float(np.sum(drift[trade_leg]))
            fee_total = nav_open * cost * dsum if dsum > 0 else 0.0
            nav_after_cost = nav_open - fee_total
            for l in range(nleg):
                curr_units = accounts[l].units
                target_units = tgt[l] * nav_after_cost / po[l] if po[l] > 0 else 0.0
                du = target_units - curr_units
                leg_fee = nav_open * cost * drift[l] if trade_leg[l] else 0.0
                if abs(du) <= 1e-12:
                    continue
                if du > 0:
                    accounts[l].buy(i, po[l], du * po[l], leg_fee)
                else:
                    accounts[l].sell(i, po[l], -du, leg_fee, "resize" if l == 0 else "defensive")
            trades += 1

        nav[i - i0 + 1] = cashbox["v"] + float(np.sum(
            np.array([accounts[l].units for l in range(nleg)]) * closes[:, i]))

    end_y = int(days_all[i1][:4])
    fx1 = mkt.fx[i1]
    for l in range(nleg):
        if accounts[l].units > 0:
            accounts[l].sell(i1, closes[l, i1], accounts[l].units,
                             accounts[l].units * closes[l, i1] * cost, "liquidation")
    liq_tax = 0.0
    for acct in accounts:
        liq_tax += acct.assess(end_y) + acct.outstanding()
    liq = cashbox["v"] - liq_tax / fx1
    ending_nav = nav[-1]

    fx0 = mkt.fx[i0 - 1]
    yrs = calendar_years(days_all[i0 - 1], days_all[i1])
    tax_total_try = sum(sum(a.tax_paid_try.values()) for a in accounts) + liq_tax
    tax_total_usd = sum(a.tax_paid_usd for a in accounts) + liq_tax / fx1

    def cg(v0, v1):
        return (v1 / v0) ** (1 / yrs) - 1

    gains_by_leg = {f"leg{l}": float(sum(accounts[l].gain_by_reason.values())) for l in range(nleg)}
    combined_tax_due = defaultdict(float)
    for acct in accounts:
        for yk, v in acct.tax_due.items():
            combined_tax_due[yk] += v
    return {
        "window": [days_all[i0], days_all[i1]],
        "years": float(yrs),
        "capital_usd": float(capital),
        "fx_start": float(fx0),
        "fx_end": float(fx1),
        "ending_nav_usd": float(ending_nav),
        "cagr_usd_liquidated": float(cg(capital, liq)),
        "terminal_usd_liquidated": float(liq),
        "cagr_usd_unliquidated": float(cg(capital, ending_nav)),
        "terminal_usd_unliquidated": float(ending_nav),
        "tax_total_try": float(tax_total_try),
        "tax_total_usd_at_payment_fx": float(tax_total_usd),
        "strategy_trades": int(trades),
        "tax_funding_sales": int(tax_sales),
        "fees_usd": float(sum(a.fees for a in accounts)),
        "tax_by_year_try": {str(k): float(v) for k, v in sorted(combined_tax_due.items())},
        "realised_gain_try_by_leg": gains_by_leg,
        "indexation_relief_try": float(sum(a.index_relief_try for a in accounts)),
        "leg_units_end": [float(a.units) for a in accounts],
        "_accounts": accounts,
        "_nav": nav,
    }
