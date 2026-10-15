#!/usr/bin/env python3
"""Turkish after-tax replay of a k_stage_b_v1 Stage B run's own fills.

The tax model is the corrected multi-instrument engine of
``research/vt_level_tax_v1/screen.py`` as copied into
``research/gain_band_v3/tax_engine.py`` (tag ``archive/research/gain-band-v3-2026-10``,
restored under ``taxref/``): progressive 2026 tariff deflated by the previous
October's Yİ-ÜFE, FIFO lot matching with the GVK mük. 81 indexation relief
(10 % floor), annual assessment at the year boundary, the two instalments paid
the following 31 March / 31 July out of the account (selling QLD to fund them
when cash is short) and a final liquidation.

What differs from that engine is only the *trade source*. Its ``run_strategy``
re-derives every trade from a target-weight series and prices it with the
research cost model (4 bps × m per side). Here the trades are the ones the
production engine actually made: the Stage B run's fill list (session, side,
quantity, fill price, fee — the price already carries slippage and spread, the
fee the SEC/TAF/CAT charges), its QLD dividends (credited as cash, exactly as
the production ledger credited them) and its final liquidation. So the after-tax
number is the production trade stream, not a re-simulated one.

Session days are the Stage B decision sessions (2017-01-03 → 2026-07-30). The
USD/TRY buying rate is taken per day exactly as ``tax_engine.load_market`` does
(last observation at or before the day).

``scaling``: production quantities are absolute (its account pays no tax). With
``scaling=True`` every fill's quantity is multiplied by
``my_close_nav(decision session) / production_close_nav(decision session)``, so
the after-tax account keeps the production *weights* while its own NAV shrinks
with the tax paid — what a weight-targeting engine does. A sell that took the
production run flat always takes this account flat too. ``scaling=False`` keeps
the literal production quantities and reports how far cash went negative (free
leverage). The deviation is reported either way, so the choice is measured
rather than asserted.

Validation (``--check``): under the ``pre_tax`` regime the replay reproduces the
production run's session-close NAV path and ending NAV exactly; the progressive
run is then the same accounting with the tax switched on.
"""

from __future__ import annotations

import importlib.util
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
TAX_ENGINE = HERE / "taxref/research/gain_band_v3/tax_engine.py"
FX_CSV = HERE / "taxref/data/historical/after_tax_tr_v1_usdtry_cbrt.csv"
UFE_CSV = HERE / "taxref/data/historical/after_tax_tr_v1_yiufe.csv"
BARS = HERE / "stage_b/data/historical/substitute_v5_plus_warmup_bars_qqq_spy_qld.csv"


def load_tax_engine():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("gb3_tax_engine", TAX_ENGINE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def qld_open_close() -> tuple[dict[str, float], dict[str, float]]:
    """QLD open/close per session date from the same bars file the run traded on."""
    opens: dict[str, float] = {}
    closes: dict[str, float] = {}
    with BARS.open() as handle:
        header = handle.readline().rstrip("\n").split(",")
        idx = {name: header.index(name) for name in ("timestamp", "symbol", "open", "close")}
        for line in handle:
            cells = line.rstrip("\n").split(",")
            if cells[idx["symbol"]] != "QLD":
                continue
            day = cells[idx["timestamp"]][:10]
            opens[day] = float(cells[idx["open"]])
            closes[day] = float(cells[idx["close"]])
    return opens, closes


@dataclass(frozen=True)
class Trade:
    """One cash-affecting market event of the production run, in session order."""

    day: str
    decision_day: str
    side: str
    qty: float
    price: float
    fee: float
    prod_units_after: float
    liquidation: bool


def trades_from_audit(audit: dict) -> list[Trade]:
    """The production fill list (+ its final liquidation) as one ordered stream."""
    rows: list[tuple[str, str, str, float, float, float, bool]] = []
    for event in audit["equity_events"]:
        if event["event"] == "fill":
            rows.append(
                (
                    event["timestamp"][:10],
                    event["decision_timestamp"][:10],
                    event["side"],
                    float(event["qty"]),
                    float(event["fill_price"]),
                    float(event["fee"]),
                    False,
                )
            )
        elif event["event"] == "final_liquidation":
            rows.append(
                (
                    event["timestamp"][:10],
                    event["timestamp"][:10],
                    "sell",
                    float(event["quantity"]),
                    float(event["fill_price"]),
                    float(event["fee"]),
                    True,
                )
            )
    rows.sort(key=lambda row: (row[0], row[1], row[2] != "buy"))
    trades: list[Trade] = []
    units = 0.0
    for day, decision_day, side, qty, price, fee, liquidation in rows:
        units += qty if side == "buy" else -qty
        if abs(units) < 1e-9:
            units = 0.0
        trades.append(Trade(day, decision_day, side, qty, price, fee, units, liquidation))
    return trades


def dividends_from_audit(audit: dict) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for event in audit["equity_events"]:
        if event["event"] == "corporate_action":
            out[event["timestamp"][:10]] += float(event["cash_delta"])
    return out


def replay(  # noqa: PLR0913 - the engine's own inputs, all explicit
    tax,  # noqa: ANN001 - the tax_engine module
    *,
    days: list[str],
    trades: list[Trade],
    dividends: dict[str, float],
    opens: dict[str, float],
    closes: dict[str, float],
    prod_nav: dict[str, float],
    capital: float,
    kind: str,
    scaling: bool,
) -> dict:
    """Run one arm's production trade stream through the tax engine."""
    if kind not in ("pre_tax", "progressive"):
        raise ValueError(f"unknown regime {kind!r}")
    regime = tax.Regime("progressive", "tariff") if kind == "progressive" else tax.Regime("pre_tax", "none")
    mkt = tax.load_market(FX_CSV, UFE_CSV, days)
    account = tax.BaseAccount(mkt, regime, float(capital))

    by_day: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        by_day[trade.day].append(trade)

    # Production units at the start of each session: the scale for the dividend
    # cash (the production ledger credits qty × rate, so a smaller position
    # earns proportionally less).
    prod_units_start: dict[str, float] = {}
    units_scan = 0.0
    for day in days:
        prod_units_start[day] = units_scan
        if by_day.get(day):
            units_scan = by_day[day][-1].prod_units_after

    n = len(days)
    pays = tax.instalments(days, 0, n - 1)
    nav = np.empty(n + 1)
    nav[0] = float(capital)
    my_close: dict[str, float] = {}
    trades_done = 0
    tax_sales = 0
    min_cash = float(capital)
    overdraw_sessions: list[str] = []
    capped_buys = 0
    cap_events: list[tuple[str, float]] = []
    scale_min, scale_max = 1.0, 1.0
    last_ratio = 1.0

    for i, day in enumerate(days):
        if i > 0 and day[:4] != days[i - 1][:4]:
            prevy = int(day[:4]) - 1
            account.tax_due[prevy] = account.assess(prevy)

        for year, part in pays.get(i, ()):
            due = account.tax_due.setdefault(year, 0.0)
            amount_try = due / 2 if part == 0 else due - account.tax_paid_try.get(year, 0.0)
            if amount_try <= 1e-9:
                continue
            usd = amount_try / mkt.fx[i]
            if account.cash < usd:
                need = (usd - account.cash) / (1 - tax.COST)
                if account.units > 1e-12:
                    account.sell(i, opens[day], need / opens[day], need * tax.COST, "tax")
                    tax_sales += 1
            account.cash -= usd
            account.tax_paid_try[year] += amount_try
            account.tax_paid_usd += usd

        # Value of this account at the previous close, after the instalments:
        # the basis for keeping the production WEIGHTS while the account is
        # smaller by the tax already paid.
        prev_close = closes[days[i - 1]] if i > 0 else closes[day]
        day_value = account.cash + account.units * prev_close
        cash = dividends.get(day, 0.0)
        if cash:
            prod_units = prod_units_start.get(day, 0.0)
            unit_ratio = (account.units / prod_units) if scaling and prod_units > 1e-9 else 1.0
            account.cash += cash * unit_ratio
        for trade in by_day.get(day, ()):
            ratio = 1.0
            if scaling:
                # The production quantity was sized on the decision session's
                # close NAV. Keep that basis, never above what this account is
                # worth now (a tax instalment may have landed in between), and
                # carry the previous ratio for the final liquidation.
                basis = prod_nav.get(trade.decision_day)
                if trade.liquidation or not basis:
                    ratio = last_ratio
                else:
                    own = my_close.get(trade.decision_day, day_value)
                    ratio = min(own, day_value) / basis
                scale_min, scale_max = min(scale_min, ratio), max(scale_max, ratio)
            last_ratio = ratio
            if trade.side == "buy":
                fee = trade.fee * ratio
                notional = trade.qty * ratio * trade.price
                if notional > account.cash - fee:
                    # A production buy spends almost all of production's cash;
                    # scaled to a smaller account it can still exceed what is
                    # left after a tax instalment. Cap it exactly as the
                    # production engine caps a buy by its own cash.
                    cap_events.append((day, notional - (account.cash - fee)))
                    notional = max(account.cash - fee, 0.0)
                    capped_buys += 1
                account.buy(i, trade.price, notional, fee)
            else:
                units = account.units if trade.prod_units_after == 0.0 else trade.qty * ratio
                account.sell(i, trade.price, units, trade.fee * ratio, "liquidation" if trade.liquidation else "resize")
            trades_done += 1

        value = account.cash + account.units * closes[day]
        nav[i + 1] = value
        my_close[day] = value
        min_cash = min(min_cash, account.cash)
        if account.cash < -1e-6:
            overdraw_sessions.append(day)

    years = tax.trv2.calendar_years(days[0], days[-1])
    terminal = float(nav[-1])
    return {
        "window": [days[0], days[-1]],
        "years": years,
        "capital_usd": float(capital),
        "ending_nav_usd": terminal,
        "cagr_usd_liquidated": (terminal / capital) ** (1 / years) - 1,
        "max_dd_nav_usd": float(tax.trv2.max_dd(nav.reshape(1, -1))[0]),
        "tax_total_try": float(sum(account.tax_paid_try.values())),
        "tax_total_usd_at_payment_fx": float(account.tax_paid_usd),
        "trades_applied": int(trades_done),
        "tax_funding_sales": int(tax_sales),
        "fees_usd": float(account.fees),
        "realised_gain_try_by_reason": {k: float(v) for k, v in sorted(account.gain_by_reason.items())},
        "indexation_relief_try": float(account.index_relief_try),
        "min_cash_usd": float(min_cash),
        "cash_negative_sessions": len(overdraw_sessions),
        "cash_capped_buys": int(capped_buys),
        "cash_cap_events": [[day, float(delta)] for day, delta in cap_events[:20]],
        "scale_ratio_first_last": [float(scale_min), float(scale_max)] if scaling else None,
        "final_ratio": float(last_ratio),
        "_nav": nav.tolist(),
    }


def days_from_audit(audit: dict) -> list[str]:
    return [row["label"][:10] for row in audit["decide_log"]]
