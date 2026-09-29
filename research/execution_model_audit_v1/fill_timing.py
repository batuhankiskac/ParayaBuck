#!/usr/bin/env python3
"""Light VT35 fill-timing simulation (execution model audit v1).

Reads the profit_max_v1 campaign bars and restated dividends from a directory
holding the files of tag ``archive/research/profit-max-v1-2026-09``::

    mkdir -p /tmp/pm && git archive archive/research/profit-max-v1-2026-09 data | tar -x -C /tmp/pm
    python3 research/execution_model_audit_v1/fill_timing.py /tmp/pm

The rule is VT35 exactly as ``equity_leveraged_trend_v1`` computes it: decide
on the completed close of session t (QQQ close > mean of the last 200 closes;
weight ``min(1, 0.35 / annualized sample vol of the last 20 QLD close-to-close
returns)``), rebalance only when |target - current| >= 0.005. Fill variants:

* ``close``  - trade at the decision close itself (ideal, not achievable live);
* ``open_w`` - trade at the next open, weight re-sized at the open price;
* ``open``   - trade at the next open, share quantity sized at the decision
  close (what the Stage B engine and the live runtime do);
* ``nclose`` - trade at the next session's close (one-session delay).

Costs are ``bps`` per side on the fill price (Stage B 1x = 2 bps slippage +
1 bp half spread = 3 bps); regulatory fees are ignored (< 0.05% of NAV over
the whole window in Stage B). Cash earns nothing; dividends are credited per
held share on the ex-date.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict

START, END = "2017-01-03", "2026-07-30"


def load(root: str) -> tuple[list[str], list[float], list[float], list[float], dict[str, float]]:
    bars: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    with open(root + "/data/historical/profit_max_v1_daily_split_sip.csv") as handle:
        for row in csv.DictReader(handle):
            bars[row["symbol"]][row["timestamp"][:10]] = (float(row["open"]), float(row["close"]))
    divs: dict[str, float] = defaultdict(float)
    with open(root + "/data/historical/profit_max_v1_corporate_actions.csv") as handle:
        for row in csv.DictReader(handle):
            if row["symbol"] == "QLD" and row.get("rate"):
                divs[row["ex_date"]] += float(row["rate"])
    days = sorted(d for d in bars["QQQ"] if d <= END and d in bars["QLD"])
    return (
        days,
        [bars["QQQ"][d][1] for d in days],
        [bars["QLD"][d][0] for d in days],
        [bars["QLD"][d][1] for d in days],
        divs,
    )


def target(i: int, qqq: list[float], qc: list[float]) -> float:
    if i < 199 or not qqq[i] > sum(qqq[i - 199 : i + 1]) / 200:
        return 0.0
    returns = [qc[k] / qc[k - 1] - 1 for k in range(i - 19, i + 1)]
    mean = sum(returns) / 20
    vol = math.sqrt(sum((x - mean) ** 2 for x in returns) / 19 * 252)
    return 1.0 if vol <= 0 else min(1.0, 0.35 / vol)


def run(data, mode: str, alloc: float = 0.93, bps: float = 3.0, band: float = 0.005):  # type: ignore[no-untyped-def]
    days, qqq, qo, qc, divs = data
    first = days.index(next(d for d in days if d >= START))
    cash, shares, curve, trades = 1.0, 0.0, [], 0
    pending: tuple[int, float, str] | None = None
    unit = bps / 1e4

    def trade(price: float, new_shares: float) -> None:
        nonlocal cash, shares, trades
        delta = new_shares - shares
        if delta == 0:
            return
        fill = price * (1 + unit) if delta > 0 else price * (1 - unit)
        if delta > 0 and delta * fill > cash:
            delta = cash / fill  # an unfundable buy is capped at available cash
        cash -= delta * fill
        shares += delta
        trades += 1

    for i in range(first, len(days)):
        day = days[i]
        if shares and day in divs:
            cash += shares * divs[day]
        if pending is not None and pending[0] == i:
            _, value, kind = pending
            pending = None
            if kind == "open_w":
                trade(qo[i], value * (cash + shares * qo[i]) / qo[i])
            elif kind == "open":
                trade(qo[i], value)
            else:
                trade(qc[i], value)
        nav = cash + shares * qc[i]
        weight = target(i, qqq, qc) * alloc
        current = shares * qc[i] / nav
        if abs(weight - current) >= band and not (weight == 0 and shares == 0) and i + 1 < len(days):
            quantity = weight * nav / qc[i]
            if mode == "close":
                trade(qc[i], quantity)
                nav = cash + shares * qc[i]
            elif mode == "open_w":
                pending = (i + 1, weight, "open_w")
            elif mode == "open":
                pending = (i + 1, quantity, "open")
            else:
                pending = (i + 1, quantity, "nclose")
        curve.append((day, nav))
    return curve, trades


def metrics(curve, a: str = START, b: str = END) -> dict[str, float]:  # type: ignore[no-untyped-def]
    seg = [v for d, v in curve if a <= d <= b]
    peak, drawdown = seg[0], 0.0
    for value in seg:
        peak = max(peak, value)
        drawdown = max(drawdown, 1 - value / peak)
    rets = [seg[k] / seg[k - 1] - 1 for k in range(1, len(seg))]
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((x - mean) ** 2 for x in rets) / len(rets))
    return {
        "cagr": (seg[-1] / seg[0]) ** (252 / len(seg)) - 1,
        "max_dd": drawdown,
        "sharpe": mean / sd * math.sqrt(252),
        "multiple": seg[-1] / seg[0],
    }


def main() -> int:
    data = load(sys.argv[1])
    out: dict[str, dict[str, object]] = {}
    for alloc in (1.0, 0.93):
        for bps in (3.0, 6.0, 9.0):
            key = f"alloc={alloc} bps={bps}"
            out[key] = {}
            for mode in ("close", "open_w", "open", "nclose"):
                curve, trades = run(data, mode, alloc, bps)
                row = metrics(curve)
                row["cagr_2017_2021"] = metrics(curve, START, "2021-12-31")["cagr"]
                row["cagr_2022_2026"] = metrics(curve, "2022-01-01", END)["cagr"]
                row["trades"] = trades
                out[key][mode] = row
                print(
                    f"{key:22s} {mode:7s} CAGR {row['cagr'] * 100:6.2f}%  MaxDD {row['max_dd'] * 100:5.2f}%  "
                    f"Sharpe {row['sharpe']:.3f}  x{row['multiple']:6.2f}  17-21 {row['cagr_2017_2021'] * 100:6.2f}%  "
                    f"22-26 {row['cagr_2022_2026'] * 100:6.2f}%  trades {trades}"
                )
    if len(sys.argv) > 2:
        with open(sys.argv[2], "w") as handle:
            json.dump(out, handle, indent=2, sort_keys=True)
            handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
