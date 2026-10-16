"""checks -- one check per option, on hand-built synthetic fixtures.

Every option is exercised on a window small enough to reason about by hand (`fixture`, tens of
sessions, prices and target weights written literally), and each check returns a dict with a
`pass` key plus the numbers that make the mechanism visible. `tests/test_opts.py` asserts the same
dicts, `harness.py` prints them, so the two cannot drift.

The `.check_*` names are the harness's table; the tests add the *invariants* the numbers imply
(e.g. "an option off is the engine", "a 20 % dead zone is bit-identical", "leg inflation").
"""

from __future__ import annotations

from datetime import date as _date
from datetime import timedelta

import numpy as np

from research.fast_engine import fe_sim, fe_signals, fe_tax

from . import fe_opts_calendar as CAL
from . import fe_opts_legs as LEGS
from .bridge import prepare_panel
from .fe_opts import Opts, session_fold
from .fe_opts_sim import simulate_opts

M = fe_sim.DEFAULT_M


# ----------------------------------------------------------------------------- fixtures
def weekdays(n, first="2020-01-02"):
    """`n` consecutive weekdays from `first`, ISO strings (the engine's session convention)."""
    out, d = [], _date.fromisoformat(first)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def ufe_table(years=range(2017, 2030)):
    """A flat YI-UFE table (100.0 every month): no indexation, a working `scale_table`."""
    return {f"{y}-{m:02d}": 100.0 for y in years for m in range(1, 13)}


def fixture(prices, opens=None, *, i0=2, first="2020-01-02", fx=10.0, rate=None):
    """`(days, open, close, env, i0, i1)` of a hand-built path: prices literally as given."""
    prices = np.asarray(prices, dtype=np.float64)
    n = len(prices)
    days = weekdays(n, first)
    opn = prices.copy() if opens is None else np.asarray(opens, dtype=np.float64)
    fx_arr = np.full(n, float(fx))
    env = fe_sim.RunEnv(days, fx_arr, ufe_table(), int(i0), n - 1)
    return days, opn, prices, env, int(i0), n - 1


def steps(n, value, at=None):
    """`n` weights, `value` from index `at` (or from `at[k]`), 0 before."""
    w = np.zeros(n, dtype=np.float64)
    if at is None:
        w[:] = value
    else:
        w[at:] = value
    return w


def run(o, c, w, env, i0, i1, *, opts=None, capital=100_000.0, mode="pre_tax", m=M, arm="K", **kw):
    """`simulate_opts` on a fixture (the bridge adds nothing to a direct call)."""
    return simulate_opts(o, c, w, env, capital, i0, i1, arm=arm, mode=mode, m=m,
                         opts=opts, **kw)


# ----------------------------------------------------------------------------- IC59
def check_asym_band():
    """The up threshold and the down threshold are separate; K's A1u gate stays on downsizes."""
    # a 0.4 pp up-drift: a flat account whose target steps to 0.004 (arm C0: no A1u in the way)
    days, o, c, env, i0, i1 = fixture([100.0] * 14)
    w_up = np.zeros(14)
    w_up[6:] = 0.004
    up3 = run(o, c, w_up, env, i0, i1, arm="C0", opts=Opts(band_up=0.003))
    up5 = run(o, c, w_up, env, i0, i1, arm="C0", opts=Opts(band_up=0.005))
    up0 = run(o, c, w_up, env, i0, i1, arm="C0", opts=Opts(band_up=0.0))
    # a 0.4 pp downsize out of a flat 0.5 position
    w_dn = np.array([0.0] * 4 + [0.5] * 4 + [0.496] * 6, dtype=np.float64)
    dn3 = run(o, c, w_dn, env, i0, i1, arm="C0", opts=Opts(band_down=0.003))
    dn5 = run(o, c, w_dn, env, i0, i1, arm="C0", opts=Opts(band_down=0.005))
    dn5_up0 = run(o, c, w_dn, env, i0, i1, arm="C0", opts=Opts(band_down=0.005, band_up=0.0))
    # with production's rule the A1u gate still owns the downsize, whatever the down band says
    k3 = run(o, c, w_dn, env, i0, i1, arm="K", opts=Opts(band_down=0.003))
    k5 = run(o, c, w_dn, env, i0, i1, arm="K", opts=Opts(band_down=0.005))
    return {
        "up_trades": [up5["strategy_trades"], up3["strategy_trades"], up0["strategy_trades"]],
        "up_band_bites": bool(up5["strategy_trades"] == 0 and up3["strategy_trades"] == 1
                              and up0["strategy_trades"] == 1),
        "down_trades": [dn5["strategy_trades"], dn3["strategy_trades"]],
        "down_band_bites": bool(dn5["strategy_trades"] == 1 and dn3["strategy_trades"] == 2),
        "down_side_unchanged_by_the_up_band": bool(dn5["strategy_trades"]
                                                   == dn5_up0["strategy_trades"]),
        "a1u_still_gates_the_downsize": bool(k3["qld_sells"] == k5["qld_sells"] == 0),
        "pass": bool(up5["strategy_trades"] == 0 and up3["strategy_trades"] == 1
                     and dn5["strategy_trades"] == 1 and dn3["strategy_trades"] == 2
                     and dn5["strategy_trades"] == dn5_up0["strategy_trades"]
                     and k3["qld_sells"] == k5["qld_sells"] == 0),
    }


# ----------------------------------------------------------------------------- IC52
def check_min_notional():
    """A permitted downsize below the dollar floor is skipped, and its drift accumulates."""
    # capital 1000: a 0.6 pp drift is $6, an order the floor can bite at $250
    c = [100.0] * 6 + [100.0 * (1 + 0.006 / 0.5)] * 10
    w = np.array([0.5] * 6 + [0.4] * 10, dtype=np.float64)
    days, o, cc, env, i0, i1 = fixture(c)
    off = run(o, cc, w, env, i0, i1, opts=Opts(min_notional=0.0), capital=1000.0)
    a = run(o, cc, w, env, i0, i1, opts=Opts(min_notional=250.0), capital=1000.0)
    b = run(o, cc, w, env, i0, i1, opts=Opts(min_notional_frac=0.05), capital=1000.0)
    c = run(o, cc, w, env, i0, i1, opts=Opts(min_notional=250.0, min_notional_frac=0.30),
            capital=1000.0)  # max($250, 30 % of a $1 000 NAV) = $300
    big = run(o, cc, w, env, i0, i1, opts=Opts(min_notional=250.0), capital=100_000.0)
    ba, bb, bc = (r["opts_stats"]["notional_blocks"] for r in (a, b, c))
    return {
        "blocks": [ba, bb, bc], "sells": [off["sells"], a["sells"], b["sells"], c["sells"]],
        "floor_bites_at_1k": bool(ba > 0 and a["sells"] < off["sells"]),
        "the_threshold_is_the_max_of_the_two": bool(bb <= ba <= bc),
        "inert_at_100k": bool(big["opts_stats"]["notional_blocks"] == 0
                              and big["sells"] == off["sells"]),
        "pass": bool(ba > 0 and a["sells"] < off["sells"] and bb <= ba <= bc
                     and big["opts_stats"]["notional_blocks"] == 0
                     and big["sells"] == off["sells"]),
    }


# ----------------------------------------------------------------------------- IC53
def check_slicing():
    """A large order fills in equal parts over the following sessions' opens; N < 2 is inert."""
    c = [100.0] * 20
    w = steps(20, 0.9, 5)
    days, o, cc, env, i0, i1 = fixture(c)
    whole = run(o, cc, w, env, i0, i1, opts=Opts())
    sliced = run(o, cc, w, env, i0, i1, opts=Opts(slice_frac=0.25, slice_parts=3))
    every = run(o, cc, w, env, i0, i1, opts=Opts(slice_frac=0.0, slice_parts=2))
    inert = run(o, cc, w, env, i0, i1, opts=Opts(slice_parts=1.0))
    nav = sliced["_nav"]
    tr = sliced["opts_stats"]
    # the position path reaches the same place three sessions after the trigger
    i_trig = int(np.argmax(sliced["_dec"] == 1))
    return {
        "triggers": tr["slice_triggers"], "slice_sessions": tr["slice_sessions"],
        "trades": [whole["strategy_trades"], sliced["strategy_trades"], every["strategy_trades"]],
        "slicing_is_inert_with_one_part": bool(inert["strategy_trades"] == whole["strategy_trades"]
                                               and np.array_equal(inert["_nav"], whole["_nav"])),
        "three_parts_reach_the_target": bool(tr["slice_triggers"] == 1 and tr["slice_sessions"] == 2
                                             and abs(nav[i_trig + 3] - whole["_nav"][i_trig + 1])
                                             < 1e-6 * whole["_nav"][i_trig + 1]),
        "two_parts_every_resize": bool(every["opts_stats"]["slice_triggers"] >= 1),
        "pass": bool(tr["slice_triggers"] == 1 and tr["slice_sessions"] == 2
                     and inert["strategy_trades"] == whole["strategy_trades"]
                     and np.array_equal(inert["_nav"], whole["_nav"])
                     and abs(nav[i_trig + 3] - whole["_nav"][i_trig + 1])
                     < 1e-6 * whole["_nav"][i_trig + 1]),
    }


# ----------------------------------------------------------------------------- IC49
def check_dd_governor():
    """The governor is inert in its dead zone and de-risks once the NAV drawdown is consumed."""
    calm = [100.0 * (1.001 ** k) for k in range(60)]
    w = steps(60, 0.9, 3)
    days, o, c, env, i0, i1 = fixture(calm)
    wv = fe_signals.vol_n(c)[0]
    gov = run(o, c, w, env, i0, i1, opts=Opts(dd_start=0.20, dd_floor=0.25), wvol=wv)
    plain = run(o, c, w, env, i0, i1, opts=Opts())
    crash = [100.0] * 10 + [100.0 * (0.95 ** k) for k in range(1, 41)] + [0.0] * 10
    crash = list(np.maximum(np.array(crash), 20.0))
    d2, o2, c2, env2, j0, j1 = fixture(crash)
    wv2 = fe_signals.vol_n(np.asarray(crash))
    gov2 = run(o2, c2, steps(len(crash), 0.9, 3), env2, j0, j1,
               opts=Opts(dd_start=0.20, dd_floor=0.25), wvol=wv2)
    up_only = run(o2, c2, steps(len(crash), 0.9, 3), env2, j0, j1,
                  opts=Opts(dd_start=0.20, dd_floor=0.25, dd_upsize_only=1), wvol=wv2)
    p2 = run(o2, c2, steps(len(crash), 0.9, 3), env2, j0, j1, opts=Opts())
    return {
        "dead_zone_is_bit_identical": bool(np.array_equal(gov["_nav"], plain["_nav"])),
        "max_dd": [p2["max_dd_nav_usd"], gov2["max_dd_nav_usd"], up_only["max_dd_nav_usd"]],
        "derisks_in_a_drawdown": bool(gov2["max_dd_nav_usd"] < p2["max_dd_nav_usd"]),
        "upsize_only_never_forces_a_downsize": bool(up_only["qld_sells"] <= p2["qld_sells"]),
        "upsize_only_is_weaker_than_the_full_governor": bool(up_only["max_dd_nav_usd"]
                                                            >= gov2["max_dd_nav_usd"]),
        "pass": bool(np.array_equal(gov["_nav"], plain["_nav"])
                     and gov2["max_dd_nav_usd"] < p2["max_dd_nav_usd"]
                     and up_only["qld_sells"] <= p2["qld_sells"]),
    }


# ----------------------------------------------------------------------------- IC39
def check_margin():
    """A negative cash balance is a loan: it accrues interest at the per-session rate."""
    c = [100.0 * (1.0005 ** k) for k in range(40)]
    days, o, cc, env, i0, i1 = fixture(c)
    w = steps(40, 2.0, 3)
    buf = np.zeros((40, 6))
    free = run(o, cc, w, env, i0, i1, opts=Opts(), dbg=buf)
    rate = session_fold(days, np.full(len(days), 0.05))
    paid = run(o, cc, w, env, i0, i1, opts=Opts(), mrate=rate)
    loan = float(np.min(buf[: i1 - i0 + 1, 0]))
    return {
        "loan_usd": loan,
        "nav_without": free["terminal_usd_liquidated"], "nav_with": paid["terminal_usd_liquidated"],
        "interest_usd": paid["opts_stats"]["margin_usd"],
        "a_loan_is_held": bool(loan < 0.0),
        "interest_charged": bool(paid["opts_stats"]["margin_usd"] > 0
                                 and paid["terminal_usd_liquidated"]
                                 < free["terminal_usd_liquidated"]),
        "interest_scales_with_the_rate": bool(
            run(o, cc, w, env, i0, i1, opts=Opts(),
                mrate=rate * 2.0)["opts_stats"]["margin_usd"]
            > 1.9 * paid["opts_stats"]["margin_usd"]),
        "pass": bool(loan < 0.0 and paid["opts_stats"]["margin_usd"] > 0
                     and paid["terminal_usd_liquidated"] < free["terminal_usd_liquidated"]),
    }


# ----------------------------------------------------------------------------- IC57
def check_early_close():
    """The calendar, and the two session behaviours (skip the decision / skip the downsize)."""
    days, o, c, env, i0, i1 = fixture([100.0] * 12)
    # the decision at close 4 (the fill at session 5) is the one that enters; skipping it delays
    # the entry to the fill at session 6, which then uses the decision at close 5
    w = np.array([0.0, 0.0, 0.0, 0.0, 0.5, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    early = np.zeros(12, dtype=np.bool_)
    early[4] = True
    plain = run(o, c, w, env, i0, i1, opts=Opts(), trace=True)
    skip = run(o, c, w, env, i0, i1, opts=Opts(early_mode=1), early=early, trace=True)
    up = np.zeros(12, dtype=np.bool_)
    up[10] = True  # a downsize is filled at session 10 (the decision at close 9)
    w_down = np.array([0.5] * 9 + [0.2, 0.2, 0.2])
    off_dn = run(o, c, w_down, env, i0, i1, opts=Opts(), trace=True)
    blk_dn = run(o, c, w_down, env, i0, i1, opts=Opts(early_mode=2), early=up, trace=True)
    # the calendar: every derived date must be a session the engine's own bars carry
    from research.fast_engine import fe_data

    qqq_days = set(fe_data.load()[0])
    last = max(qqq_days)
    span = [CAL.early_close_dates(y) for y in range(2000, 2027)]
    flat = [d for row in span for d in row]
    non_session = [d.isoformat() for d in flat
                   if d.isoformat() <= last and d.isoformat() not in qqq_days]
    vol = _volume_ratios()
    hist = [vol.get(d.isoformat(), float("nan")) for d in flat]
    med = float(np.median([v for v in hist if v == v]))
    return {
        "skips": skip["opts_stats"]["early_skips"],
        "plain_dec": [int(x) for x in plain["_dec"]], "skip_dec": [int(x) for x in skip["_dec"]],
        "entry_delayed_one_session": bool(skip["opts_stats"]["early_skips"] == 1
                                          and plain["_dec"][3] == 1 and skip["_dec"][3] == 0
                                          and skip["_dec"][4] == 1),
        "blocks": blk_dn["opts_stats"]["early_blocks"],
        "downsize_moved_one_session": bool(blk_dn["opts_stats"]["early_blocks"] == 1
                                           and off_dn["_dec"][8] == 2 and blk_dn["_dec"][8] == 0
                                           and blk_dn["_dec"][9] == 2),
        "calendar_2001": [d.isoformat() for d in CAL.early_close_dates(2001)],
        "calendar_2021": [d.isoformat() for d in CAL.early_close_dates(2021)],
        "calendar_2026": [d.isoformat() for d in CAL.early_close_dates(2026)],
        "dates_per_year": sorted({len(r) for r in span}),
        "sessions_per_year": sorted({len(r) for r in span}),
        "not_a_session": non_session,
        "median_volume_ratio": med,
        "pass": bool(skip["opts_stats"]["early_skips"] == 1 and plain["_dec"][3] == 1
                     and skip["_dec"][3] == 0 and skip["_dec"][4] == 1
                     and blk_dn["opts_stats"]["early_blocks"] == 1
                     and off_dn["_dec"][8] == 2 and blk_dn["_dec"][8] == 0
                     and blk_dn["_dec"][9] == 2
                     and not non_session
                     and len(CAL.early_close_dates(2001)) == 3
                     and len(CAL.early_close_dates(2021)) == 1 and med < 0.75),
    }


def _volume_ratios():
    """QQQ volume of every session over a 41-session centred median -- the calendar's corroboration.

    A 13:00 close trades a thin session, so the derived dates should sit well below the median
    session's volume. It is corroboration, not the source: see the calendar module's docstring.
    """
    import csv
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "data/historical/stress_robustness_v1_daily.csv"
    rows = [(r["date"], float(r["volume"])) for r in csv.DictReader(path.open())
            if r["symbol"] == "QQQ"]
    dates = [d for d, _v in rows]
    v = np.array([x for _d, x in rows])
    med = np.array([np.median(v[max(0, k - 20): k + 21]) for k in range(len(v))])
    return {d: float(v[k] / med[k]) for k, d in enumerate(dates) if med[k] > 0}


# ----------------------------------------------------------------------------- IC58
def check_settlement():
    """A buy may only spend settled cash: same-session sale proceeds are not spendable."""
    c = [100.0 * (1.0005 ** k) for k in range(20)]
    days, o, cc, env, i0, i1 = fixture(c)
    w = steps(20, 1.5, 3)  # over-leveraged on the 1x leg: the buy needs more cash than it has
    free = run(o, cc, w, env, i0, i1, opts=Opts())
    strict = run(o, cc, w, env, i0, i1, opts=Opts(settle_floor=0.0))
    half = run(o, cc, w, env, i0, i1, opts=Opts(settle_floor=0.5))
    return {
        "nav": [free["terminal_usd_unliquidated"], strict["terminal_usd_unliquidated"],
                half["terminal_usd_unliquidated"]],
        "defers": strict["opts_stats"]["settle_defers"],
        "strict_caps_at_settled_cash": bool(strict["opts_stats"]["settle_defers"] > 0
                                            and strict["terminal_usd_unliquidated"]
                                            < free["terminal_usd_unliquidated"]),
        "half_allows_the_deficit": bool(abs(half["terminal_usd_unliquidated"]
                                           - free["terminal_usd_unliquidated"])
                                        < 1e-9 * free["terminal_usd_unliquidated"]),
        "pass": bool(strict["opts_stats"]["settle_defers"] > 0
                     and strict["terminal_usd_unliquidated"] < free["terminal_usd_unliquidated"]
                     and abs(half["terminal_usd_unliquidated"]
                             - free["terminal_usd_unliquidated"])
                     < 1e-9 * free["terminal_usd_unliquidated"]),
    }


def check_settlement_tax_sale():
    """The mechanism's real case: an instalment's funding sale cannot fund a same-session buy."""
    n = 700
    prices = 100.0 * (1.003 ** np.arange(n))
    days, o, c, env, i0, i1 = fixture(prices, i0=2, first="2019-01-02")
    sess, yrs, parts = fe_tax.instalments(days, i0, i1)
    first = np.flatnonzero((yrs == 2019) & (parts == 0))
    if first.size == 0:
        return {"pass": False, "reason": "this fixture has no 2019 instalment session"}
    ip = int(sess[first[0]])
    w = np.full(n, 0.98)
    w[:3] = 0.0
    w[199:200] = 0.0          # a full round trip realises the year's gain (the 2019 assessment)
    w[200:] = 0.98
    w[ip - 1:] = 1.5          # the instalment session also up-resizes (a same-session sale + buy)
    off = run(o, c, w, env, i0, i1, opts=Opts(), mode="progressive", capital=100_000.0)
    on = run(o, c, w, env, i0, i1, opts=Opts(settle_floor=0.0), mode="progressive",
             capital=100_000.0)
    return {
        "instalment_session": days[ip], "instalment_year": 2019,
        "tax_sales": [off["tax_funding_sales"], on["tax_funding_sales"]],
        "defers": on["opts_stats"]["settle_defers"],
        "sales_happen": bool(off["tax_funding_sales"] > 0),
        "buy_is_deferred": bool(on["opts_stats"]["settle_defers"] > 0),
        "pass": bool(off["tax_funding_sales"] > 0 and on["opts_stats"]["settle_defers"] > 0),
    }


# ----------------------------------------------------------------------------- IC55
def check_fx_cost_and_prefund():
    """The conversion's spread, and paying the liability early out of idle TRY cash."""
    n = 700
    prices = 100.0 * (1.003 ** np.arange(n))
    days, o, c, env, i0, i1 = fixture(prices, i0=2, first="2019-01-02")
    w = np.full(n, 0.98)
    w[:3] = 0.0
    w[199:200] = 0.0      # a round trip realises a gain, so instalments are due
    w[200:] = 0.98
    off = run(o, c, w, env, i0, i1, opts=Opts(), mode="progressive")
    cost = run(o, c, w, env, i0, i1, opts=Opts(fx_cost=0.005), mode="progressive")
    # idle cash: the reserve can be funded, and the tax payments still come out of the account's
    # own cash (the prefund never forces a sale), so the liability is the same one
    rich = np.full(n, 0.2)
    rich[:3] = 0.0
    rich[199:200] = 0.98
    rich[200:] = 0.2
    rich_off = run(o, c, rich, env, i0, i1, opts=Opts(), mode="progressive")
    rich_pre = run(o, c, rich, env, i0, i1, opts=Opts(prefund_sessions=63.0), mode="progressive")
    paid = off["tax_total_usd_at_payment_fx"]
    ratio = cost["opts_stats"]["fx_cost_usd"] / paid
    return {
        "tax_paid_usd": [paid, cost["tax_total_usd_at_payment_fx"]],
        "cost_usd": cost["opts_stats"]["fx_cost_usd"],
        "cost_is_the_spread": bool(0.004 < ratio < 0.006),
        "prefund_usd": rich_pre["opts_stats"]["prefund_usd"],
        "prefund_fires_on_idle_cash": bool(rich_pre["opts_stats"]["prefund_usd"] > 0),
        "prefund_leaves_the_liability_alone": bool(
            abs(rich_pre["tax_total_try"] - rich_off["tax_total_try"])
            < 0.01 * rich_off["tax_total_try"]),
        "prefund_never_forces_a_sale": bool(rich_pre["tax_funding_sales"]
                                            <= rich_off["tax_funding_sales"]),
        "no_idle_cash_cannot_prefund": bool(
            run(o, c, w, env, i0, i1, opts=Opts(prefund_sessions=63.0),
                mode="progressive")["opts_stats"]["prefund_usd"] == 0.0),
        "nav_side_effect_pp": float(100 * (rich_pre["terminal_usd_liquidated"]
                                           / rich_off["terminal_usd_liquidated"] - 1)),
        "pass": bool(0.004 < ratio < 0.006 and rich_pre["opts_stats"]["prefund_usd"] > 0
                     and abs(rich_pre["tax_total_try"] - rich_off["tax_total_try"])
                     < 0.01 * rich_off["tax_total_try"]
                     and rich_pre["tax_funding_sales"] <= rich_off["tax_funding_sales"]
                     and abs(rich_pre["terminal_usd_liquidated"]
                             / rich_off["terminal_usd_liquidated"] - 1) < 0.01),
    }


# ----------------------------------------------------------------------------- IC54
def check_fill_reference():
    """The fill reference: the open (production), the session's midpoint, or a supplied array."""
    prices = [100.0] * 12
    opens = [95.0] * 12  # a 5 % opening gap down: the decision's fill price matters
    days, o, c, env, i0, i1 = fixture(prices, opens)
    w = steps(12, 0.5, 3)
    buf = np.zeros((12, 6))
    openf = run(o, c, w, env, i0, i1, opts=Opts(), dbg=buf)
    open_units = float(np.max(buf[: i1 - i0 + 1, 1]))
    buf_m = np.zeros((12, 6))
    midf = run(o, c, w, env, i0, i1, opts=Opts(fill_mode=1), dbg=buf_m)
    given = run(o, c, w, env, i0, i1, opts=Opts(fill_mode=2), fill=np.full(12, 98.0))
    mid_units = float(np.max(buf_m[: i1 - i0 + 1, 1]))
    fee = 100_000.0 * fe_sim.COST_PER_SIDE * M * 0.5
    want_mid = 0.5 * (100_000.0 - fee) / 97.5  # the entry: 0.5 weight at the (95, 100) midpoint
    return {
        "open_units": open_units, "mid_units": mid_units, "want_mid_units": want_mid,
        "mid_units_match_the_midpoint": bool(abs(mid_units - want_mid) < 1e-6),
        "mid_differs_from_open": bool(midf["terminal_usd_liquidated"]
                                      != openf["terminal_usd_liquidated"]),
        "given_array_used": bool(given["terminal_usd_liquidated"]
                                 != openf["terminal_usd_liquidated"]),
        "mid_beats_the_gap_down_open_for_a_buy": bool(midf["terminal_usd_unliquidated"]
                                                      < openf["terminal_usd_unliquidated"]),
        "pass": bool(abs(mid_units - want_mid) < 1e-6 and mid_units < open_units
                     and given["terminal_usd_liquidated"] != openf["terminal_usd_liquidated"]
                     and midf["terminal_usd_unliquidated"] < openf["terminal_usd_unliquidated"]),
    }


# ----------------------------------------------------------------------------- IC56 / IC40
def check_withholding_panel():
    """`repanel(wht=...)` nets the leg's dividends; a price-only panel is unchanged."""
    n = 40
    rng = np.random.default_rng(11)
    price = 100.0 * np.cumprod(1 + rng.normal(0.0005, 0.01, n))
    div = np.zeros(n)
    div[2:] = price[:-2] * 0.0004
    from research.batch_screen import api

    days = weekdays(n, "2000-01-03")
    p = api.panel(days, price=price, div=div, rate=np.zeros(n), o2=price * 0.999, c2=price,
                  d2=div, window="tiny")
    gross = p.legs["2x"][1][0]
    net = LEGS.repanel(p, wht=0.20).legs["2x"][1][0]
    want = fe_signals.synthetic_2x(np.atleast_2d(price * 0.999), np.atleast_2d(price),
                                   np.atleast_2d(div * 0.8), np.zeros((1, n)), 0.0095)[1][0]
    zero = LEGS.repanel(api.panel(days, price=price, div=np.zeros(n), rate=np.zeros(n),
                                  o2=price * 0.999, c2=price, d2=np.zeros(n), window="tiny"),
                        wht=0.20).legs["2x"][1][0]
    p0 = api.panel(days, price=price, div=np.zeros(n), rate=np.zeros(n), o2=price * 0.999,
                   c2=price, d2=np.zeros(n), window="tiny")
    return {
        "net_below_gross": bool(net[-1] < gross[-1]),
        "matches_net_dividends": bool(np.allclose(net, want, rtol=1e-12, atol=0)),
        "price_only_unchanged": bool(np.array_equal(zero, p0.legs["2x"][1][0])),
        "gross_end": float(gross[-1]), "net_end": float(net[-1]),
        "pass": bool(net[-1] < gross[-1] and np.allclose(net, want, rtol=1e-12, atol=0)
                     and np.array_equal(zero, p0.legs["2x"][1][0])),
    }


def check_spy_leg():
    """`leg = "2x_spy"` adds the S&P 500 leg on the main window and degenerates elsewhere."""
    from research.batch_screen import api, windows

    days, p, i0, i1 = windows.main_panel(0.0)
    arm = {"leg": api.LEG_SPY, "opts": {}, "params": {}, "rule": "K"}
    got = prepare_panel(p, arm)
    qqq_spy = got.legs[api.LEG_SPY][1]
    qqq_2x = got.legs["2x"][1]
    ratio = float(np.corrcoef(np.diff(np.log(qqq_spy))[:2000],
                              np.diff(np.log(qqq_2x))[:2000])[0, 1])
    # an OOS-like panel (no SPY series): the option leg is an alias of the window's own 2x
    d2, p2, j0, j1 = windows.oos_panel("M2")
    got2 = prepare_panel(p2, dict(arm, leg=api.LEG_SPY))
    alias = np.array_equal(got2.legs[api.LEG_SPY][1], p2.legs["2x"][1])
    return {
        "leg_present": api.LEG_SPY in got.legs, "panel_leg": got.leg,
        "corr_2x_vs_spy_2x": ratio,
        "different_underlying": bool(0.5 < ratio < 0.999),
        "oos_degenerates_to_alias": bool(alias),
        "pass": bool(api.LEG_SPY in got.legs and got.leg == api.LEG_SPY
                     and 0.5 < ratio < 0.999 and alias),
    }


# ----------------------------------------------------------------------------- the table
CHECKS = {
    "IC59_asym_band": check_asym_band,
    "IC52_min_notional": check_min_notional,
    "IC53_slicing": check_slicing,
    "IC49_dd_governor": check_dd_governor,
    "IC39_margin": check_margin,
    "IC57_early_close": check_early_close,
    "IC58_settlement": check_settlement,
    "IC58_tax_sale": check_settlement_tax_sale,
    "IC55_fx_prefund": check_fx_cost_and_prefund,
    "IC54_fill_reference": check_fill_reference,
    "IC56_withholding": check_withholding_panel,
    "IC40_spy_leg": check_spy_leg,
}

__all__ = ["CHECKS", "fixture", "run", "steps", "ufe_table", "weekdays"]
