"""parity_b -- PARITY (b): the two-leg kernel against `riskoff_after_tax_v1`'s engine.

The comparison target is that study's own engine (`riskoff_ref.py`, copied verbatim from tag
`archive/research/riskoff-after-tax-v1-2026-10`) driven on a **fixed synthetic two-leg case**.
Its published `L2` cells are *not* reproducible with two legs -- `L2` is a three-leg portfolio
(QLD\\* + BOND10\\* + GOLD\\*, built from `leverage_frontier_v1`'s yield/gold synthetics, with a
monthly bond-vs-gold choice and a total-weight cap) and it is assessed per account; see
"conventions that differ" in `riskoff_ref.py`. What can be reproduced, and is, is its engine's
two-book accounting.

**The fixed case** (`CASE`): the main window's QQQ sessions from 2022-11-01 to 2023-08-15
(197 sessions, one December/January boundary inside), leg A = the campaign's synthetic
daily-reset 2x QLD\\* (`fe_data.main_inputs(0.0)`), leg B = GLD's registered adjusted close
(`EXTRA_DATA.json`; an extra series carries no OHLC, so its adjusted close is both the open and
the close -- stated here, not fitted). Targets are fixed in advance as `wants_A`/`wants_B`:

* leg A holds 0.98 from the first session, trims to 0.60 and back, and exits so that its **exit
  fill lands on the last session of 2022**; it stays at zero for the whole of 2023;
* leg B stays at zero through 2022 and enters 0.98 at the first session of 2023, holding to the
  end (so its whole realisation, including the liquidation, lands in 2023).

Both legs hold exactly one instrument at a time, each leg's whole realisation falls in a single
tax year, and no leg holds units while the other leg trades -- which is exactly the configuration
in which the reference's conventions (per-account assessment, the 0.10 band on the non-equity
leg, the "resize every leg on any trade" fill) cannot differ from the kernel's. The parity cell
therefore asserts `max |diff| == 0` on the NAV path, the tax rows, the per-leg realised gains,
the fees, the trade counts and the liquidated CAGR, in pre-tax and progressive modes, at
m = 2.

`divergence()` then runs the *deliberate counter-example*: the same window with both legs
holding through 2022 and both exiting at the end of 2022, so both realise in one tax year. The
reference then charges more tax than the kernel by exactly the amount the per-account floor and
the per-leg tariff brackets add (plus, where a leg's drift falls between the two engines' bands,
the band/drag conventions -- the cell reports each leg's trade count and realised gain in both
engines so the attribution is visible). It is reported, not asserted: the kernel's pooled
assessment is the protocol's rule.
"""

from __future__ import annotations

import numpy as np

from research.fast_engine import fe_data, fe_sim
from research.fast_engine2 import fe2_sim
from research.two_leg_kernel_v1 import riskoff_ref

# ----------------------------------------------------------------------------- the fixed case
WINDOW = ("2022-11-01", "2023-08-15")
CAPITAL = 100_000.0
M = 2
SCHEDULE = {
    "leg_a": "0.98 from the first session; 0.60 for 13 sessions mid-2022; back to 0.98; "
             "exit decided at the second-to-last 2022 session (fill on the last session of 2022); "
             "zero for all of 2023",
    "leg_b": "zero through 2022; 0.98 decided at the last 2022 session (fill on the first 2023 "
             "session) and held to the end of the window",
    "trim": {"leg_a_off_at": 12, "leg_a_on_at": 25},
}


def build_case(both_legs_2022=False):
    """The fixed case: prices, targets, window and the calendar the two engines share.

    `both_legs_2022=True` is the divergence variant (leg B mirrors leg A's 2022 schedule and also
    exits at the end of 2022, so both legs realise in 2022).
    """
    days, oa, ca, _e2, _w, _v = fe_data.main_inputs(0.0)   # leg A = the campaign's QLD* 2x
    days = list(days)
    T = len(days)
    fx = fe_data.fx_at(days)
    ufe = fe_data.load_ufe()
    b = np.asarray(extra_values("GLD", days), dtype=float)
    d = np.array(days)
    i0 = int(np.argmax(d >= WINDOW[0]))
    i1 = int(np.max(np.where(d <= WINDOW[1])[0]))
    if not np.isfinite(b[i0 - 1 : i1 + 1]).all():
        raise ValueError("the GLD series has a gap inside the parity case's window")
    last2022 = int(np.max(np.where(d <= "2022-12-31")[0]))
    tA1 = i0 + SCHEDULE["trim"]["leg_a_off_at"]
    tA2 = i0 + SCHEDULE["trim"]["leg_a_on_at"]
    exit_dec = last2022 - 1          # the close that fills on the last session of 2022

    wa = np.zeros(T)
    wa[i0 - 1 : tA1] = 0.98
    wa[tA1:tA2] = 0.60
    wa[tA2:exit_dec] = 0.98
    wb = np.zeros(T)
    if both_legs_2022:
        wb[i0 - 1 : exit_dec] = 0.49          # holds through 2022, exits with leg A
    else:
        wb[last2022 : i1 + 1] = 0.98          # enters 2023, holds to the end
    return {
        "days": days, "i0": i0, "i1": i1, "last2022": last2022,
        "fx": fx, "ufe": ufe,
        "opens": np.stack([np.asarray(oa, dtype=float), b]),
        "closes": np.stack([np.asarray(ca, dtype=float), b]),
        "wants": np.stack([wa, wb]),
        "both_legs_2022": bool(both_legs_2022),
        "meta": {
            "window": [days[i0], days[i1]], "sessions": i1 - i0 + 1,
            "leg_a": "QLD* synthetic daily-reset 2x on QQQ total return (fe_data.main_inputs(0.0))",
            "leg_b": "GLD adjusted close (batch_screen EXTRA_DATA series GLD); "
                     "open = close, an extra series carries no OHLC",
            "schedule": SCHEDULE, "capital_usd": CAPITAL, "m": M, "cost_per_side": fe_sim.COST_PER_SIDE,
            "both_legs_2022": bool(both_legs_2022),
        },
    }


def extra_values(series, days):
    """`batch_screen.extra.values_on` (imported lazily: the study may run without the framework)."""
    from research.batch_screen import extra

    return extra.values_on(series, days)


# ----------------------------------------------------------------------------- the two runs
def run_reference(case, regime="progressive", m=M, capital=CAPITAL):
    """The archived engine on the case (`indexation=False` pre-tax, see the module doc)."""
    mkt = riskoff_ref.Market(case["days"], case["fx"], case["ufe"])
    return riskoff_ref.run_two_leg(mkt, regime, capital, case["i0"], case["i1"],
                                   case["opens"], case["closes"], case["wants"],
                                   cost=fe_sim.COST_PER_SIDE * m,
                                   indexation=None if regime == "progressive" else False)


def run_kernel(case, regime="progressive", m=M, capital=CAPITAL):
    env = fe_sim.RunEnv(case["days"], case["fx"], case["ufe"], case["i0"], case["i1"])
    return fe2_sim.simulate2(case["opens"][0], case["closes"][0], case["wants"][0],
                             case["opens"][1], case["closes"][1], case["wants"][1],
                             env, capital, case["i0"], case["i1"], arm="C0", mode=regime, m=m)


def _cell(got, want, tol=0.0):
    d = float(got) - float(want)
    return {"reproduced": float(got), "reference": float(want), "diff": d,
            "pass": bool(abs(d) <= tol)}


def compare(case, regime="progressive", m=M, capital=CAPITAL, assert_pass=True):
    """Every number the reference reports, against the kernel's, on one case and mode."""
    ref = run_reference(case, regime, m, capital)
    mine = run_kernel(case, regime, m, capital)
    out = {"window": case["meta"]["window"], "sessions": case["meta"]["sessions"],
           "regime": regime, "m": m, "capital_usd": capital}
    out["nav_max_abs_diff"] = float(np.max(np.abs(ref["_nav"] - mine["_nav"])))
    out["cells"] = {
        "years": _cell(mine["years"], ref["years"]),
        "terminal_usd_liquidated": _cell(mine["terminal_usd_liquidated"],
                                         ref["terminal_usd_liquidated"]),
        "cagr_usd_liquidated": _cell(mine["cagr_usd_liquidated"], ref["cagr_usd_liquidated"]),
        "ending_nav_usd": _cell(mine["ending_nav_usd"], ref["ending_nav_usd"]),
        "tax_total_try": _cell(mine["tax_total_try"], ref["tax_total_try"]),
        "tax_total_usd_at_payment_fx": _cell(mine["tax_total_usd_at_payment_fx"],
                                             ref["tax_total_usd_at_payment_fx"]),
        "indexation_relief_try": _cell(mine["index_relief_try"], ref["indexation_relief_try"]),
        "strategy_trades": _cell(mine["strategy_trades"], ref["strategy_trades"]),
        "tax_funding_sales": _cell(mine["tax_funding_sales"], ref["tax_funding_sales"]),
        "fees_usd_leg_a": _cell(mine["_legs"]["A"]["fees_usd"], ref["_accounts"][0].fees),
        "fees_usd_leg_b": _cell(mine["_legs"]["B"]["fees_usd"], ref["_accounts"][1].fees),
        "realised_gain_try_leg_a": _cell(sum(mine["_legs"]["A"]["realised_gain_try_by_reason"].values()),
                                         ref["realised_gain_try_by_leg"]["leg0"]),
        "realised_gain_try_leg_b": _cell(sum(mine["_legs"]["B"]["realised_gain_try_by_reason"].values()),
                                         ref["realised_gain_try_by_leg"]["leg1"]),
        "leg_a_trades": _cell(mine["_legs"]["A"]["trades"], ref["_accounts"][0].trades),
        "leg_b_trades": _cell(mine["_legs"]["B"]["trades"], ref["_accounts"][1].trades),
    }
    # per-year tax due, year by year
    tax_cells = {}
    # the reference's per-year dict holds only the *year-boundary* assessments (the final
    # liquidation assessment stays out of it, see `riskoff_ref.run_two_leg`); the kernel books that
    # one in its last year's due row, and `tax_total_try` (compared above) covers it.
    mine_due = {str(k): v for k, v in mine["tax_assessed_try_by_year"].items()}
    tax_cells = {}
    for y in sorted(ref["tax_by_year_try"]):
        tax_cells[y] = _cell(mine_due.get(y, 0.0), ref["tax_by_year_try"][y])
    out["tax_by_year_note"] = ("boundary-year assessments only; the reference's per-year dict "
                               "excludes the final liquidation assessment, which `tax_total_try` "
                               "compares")
    out["tax_by_year"] = tax_cells
    passed = (out["nav_max_abs_diff"] == 0.0
              and all(c["pass"] for c in out["cells"].values())
              and all(c["pass"] for c in tax_cells.values()))
    out["pass"] = bool(passed)
    if assert_pass and not passed:
        bad = [k for k, c in out["cells"].items() if not c["pass"]]
        bad += [f"tax_by_year.{k}" for k, c in tax_cells.items() if not c["pass"]]
        raise AssertionError(f"parity (b) {regime} failed (nav diff {out['nav_max_abs_diff']}): {bad}")
    return out


def divergence(capital=CAPITAL, m=M, case=None):
    """The deliberate counter-example: both legs realise in 2022, so the conventions show up."""
    case = build_case(both_legs_2022=True) if case is None else case
    ref = run_reference(case, "progressive", m, capital)
    mine = run_kernel(case, "progressive", m, capital)
    per_leg = {}
    for l, name in ((0, "A"), (1, "B")):
        per_leg[name] = {
            "ref_trades": int(ref["_accounts"][l].trades),
            "kernel_trades": mine["_legs"][name]["trades"],
            "ref_gain_try": ref["realised_gain_try_by_leg"][f"leg{l}"],
            "kernel_gain_try": sum(mine["_legs"][name]["realised_gain_try_by_reason"].values()),
        }
    return {
        "case": case["meta"],
        "ref_tax_total_try": ref["tax_total_try"],
        "kernel_tax_total_try": mine["tax_total_try"],
        "tax_diff_try": mine["tax_total_try"] - ref["tax_total_try"],
        "ref_cagr": ref["cagr_usd_liquidated"],
        "kernel_cagr": mine["cagr_usd_liquidated"],
        "cagr_diff_pp": (mine["cagr_usd_liquidated"] - ref["cagr_usd_liquidated"]) * 100,
        "per_leg": per_leg,
        "expected": ("the reference assesses each leg separately (its own annual gain floored at "
                     "zero, its own tariff brackets), the kernel pools the two legs into one "
                     "taxpayer's annual net TRY gain -- so with both legs realising in the same "
                     "year the reference charges more"),
        "note": ("the per-leg trade counts show whether the 0.10 band / 'resize every leg' "
                 "conventions are also in play on this case"),
    }


def run(m=2, capital=CAPITAL, assert_pass=True):
    """Every parity (b) cell: the exact ones (asserted) and the divergence demonstration."""
    parity = {}
    case = build_case()
    for regime in ("pre_tax", "progressive"):
        parity[f"alternating_{regime}"] = compare(case, regime, m, capital,
                                                  assert_pass=assert_pass)
    variant = {}
    for regime in ("pre_tax", "progressive"):
        variant[f"both_legs_2022_{regime}"] = compare(case, regime, m, capital,
                                                      assert_pass=False)
    return {"parity": parity, "divergence": {"variant_cells": variant,
                                             "numbers": divergence(capital, m)}}


def all_pass(result):
    """True when every *asserted* parity (b) cell passed (the divergence cells are evidence)."""
    return all(bool(c.get("pass")) for c in result["parity"].values())


__all__ = ["CAPITAL", "M", "SCHEDULE", "WINDOW", "all_pass", "build_case", "compare",
           "divergence", "run", "run_kernel", "run_reference"]
