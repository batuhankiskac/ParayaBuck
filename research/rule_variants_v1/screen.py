#!/usr/bin/env python3
"""Rule variants v1: close-to-close screen of VT35 rule changes (see PREREGISTRATION.md).

Same simulator convention as research/profit_max_v1/stage_a2.py (tag
archive/research/profit-max-v1-2026-09): signal on the completed close of day
t-1, trade at the open of day t, dividends reinvested at the ex-date open,
whole-book re-set to targets when any weight drifts > 0.5 pp, 4 bps x m per unit
of traded notional, cash earns 0 unless an arm holds a T-bill ETF.

Inputs: the archived profit_max_v1 CSVs (recover with ``recover_inputs.sh``)
plus this campaign's TLT/IEF/BIL/SGOV/GLD download.

    python3 research/rule_variants_v1/screen.py
"""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ARCHIVE_DIR = ROOT / "data/research/rule_variants_v1"
SOURCES = (
    (ARCHIVE_DIR / "profit_max_v1_daily_split_sip.csv", ARCHIVE_DIR / "profit_max_v1_corporate_actions.csv",
     ("SPY", "QQQ", "QLD", "VOO")),
    (HERE / "data/rule_variants_v1_daily_split_sip.csv", HERE / "data/rule_variants_v1_corporate_actions.csv",
     ("GLD", "TLT", "IEF", "BIL", "SGOV")),
)
START, END = "2017-01-03", "2026-07-30"
WINDOWS = {"full": (START, END), "2017_2021": (START, "2021-12-31"), "2022_2026": ("2022-01-01", END)}
SMA, VOL_N, VOL_TARGET, BAND, SIDE_BPS, BUDGET = 200, 20, 0.35, 0.005, 4.0, 0.93
STAGE_B_VT35 = {"cagr": 0.2898, "max_dd": 0.2916, "sharpe": 1.068}

Targets = dict[str, float]


# ---------------------------------------------------------------- data

def load(sources=SOURCES):
    bars: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    divs: dict[str, dict[str, float]] = defaultdict(dict)
    for bars_path, actions_path, symbols in sources:
        with bars_path.open() as handle:
            for row in csv.DictReader(handle):
                if row["symbol"] in symbols:
                    bars[row["symbol"]][row["timestamp"][:10]] = (float(row["open"]), float(row["close"]))
        with actions_path.open() as handle:
            for row in csv.DictReader(handle):
                if row["symbol"] in symbols:
                    d = divs[row["symbol"]]
                    d[row["ex_date"]] = d.get(row["ex_date"], 0.0) + float(row["rate"])
    return bars, divs


# ---------------------------------------------------------------- signals (pure, testable)

def sma(closes: list[float], k: int, n: int = SMA) -> float | None:
    """Mean of closes[k-n+1 .. k] or None when history is short."""
    return None if k < n - 1 else sum(closes[k - n + 1 : k + 1]) / n


def realized_vol(closes: list[float], k: int, n: int = VOL_N) -> float:
    """Annualized sample std of the n daily returns ending at closes[k] (Stage A2 _vol)."""
    rets = [closes[j] / closes[j - 1] - 1 for j in range(k - n + 1, k + 1)]
    mu = sum(rets) / n
    return math.sqrt(sum((r - mu) ** 2 for r in rets) / (n - 1)) * math.sqrt(252)


def vt_weight(vol: float, target: float = VOL_TARGET) -> float:
    return min(1.0, target / vol) if vol > 0 else 1.0


def buffered_regime(prev_in: bool, close: float, avg: float, exit_buffer: float) -> bool:
    """Enter above the SMA; once in, exit only below SMA x (1 - exit_buffer)."""
    return close > avg * (1 - exit_buffer) if prev_in else close > avg


def min_hold_regime(prev_in: bool, raw: bool, sessions_since_switch: int, min_hold: int) -> bool:
    """Follow the raw signal unless the last switch was fewer than min_hold sessions ago."""
    return raw if raw == prev_in or sessions_since_switch >= min_hold else prev_in


def staged_exposure(prev: float, up: bool, step: float = 1 / 3) -> float:
    goal = 1.0 if up else 0.0
    return min(goal, prev + step) if goal > prev else max(goal, prev - step)


def trend_strength_level(close: float, avg: float, threshold: float = 0.05) -> int:
    """2 = QLD, 1 = QQQ, 0 = cash."""
    dist = close / avg - 1
    return 2 if dist > threshold else 1 if dist > 0 else 0


# ---------------------------------------------------------------- arms

class Market:
    def __init__(self, bars, divs):
        self.bars, self.divs = bars, divs
        self.days = sorted(d for d in bars["SPY"] if d <= END)
        self.first = self.days.index(next(d for d in self.days if d >= START))
        self.close = {s: [bars[s][d][1] if d in bars[s] else math.nan for d in self.days] for s in bars}

    def has(self, symbol: str, i: int) -> bool:
        return self.days[i] in self.bars[symbol]


def make_arm(spec: dict, m: Market) -> Callable[[int], tuple[Targets, tuple]]:
    """Return f(i) -> (target weights for day i, regime tuple), using data up to day i-1."""
    kind, budget = spec["kind"], spec.get("budget", BUDGET)
    q, qld = m.close["QQQ"], m.close["QLD"]
    state = {"in": False, "since": 10**9, "stage": 0.0, "x_in": False}

    def vt(k: int) -> float:
        return vt_weight(realized_vol(qld, k))

    def core(k: int) -> tuple[float, bool]:
        """QLD fraction (0..1) and regime for the trend sleeve under this arm's rule."""
        avg = sma(q, k)
        raw = avg is not None and q[k] > avg
        if kind == "buffer":
            state["in"] = avg is not None and buffered_regime(state["in"], q[k], avg, spec["b"])
        elif kind == "min_hold":
            new = min_hold_regime(state["in"], raw, state["since"], spec["n"])
            state["since"] = 0 if new != state["in"] else state["since"] + 1
            state["in"] = new
        else:
            state["in"] = raw
        if kind == "staged":
            state["stage"] = staged_exposure(state["stage"], raw)
            return state["stage"] * vt(k), state["in"]
        return (vt(k) if state["in"] else 0.0), state["in"]

    def f(i: int) -> tuple[Targets, tuple]:
        k = i - 1
        if kind == "hold":
            return {spec["asset"]: budget}, (True,)
        if kind == "strength":
            avg = sma(q, k)
            level = 0 if avg is None else trend_strength_level(q[k], avg)
            if level == 2:
                return {"QLD": budget * vt(k)}, (level,)
            if level == 1:
                return {"QQQ": budget * vt_weight(realized_vol(q, k))}, (level,)
            return {}, (level,)
        frac, regime = core(k)
        split = spec.get("split", 1.0)
        out: Targets = {"QLD": budget * split * frac} if frac > 0 else {}
        if kind == "second":
            x = m.close[spec["x"]]
            xa = sma(x, k)
            state["x_in"] = xa is not None and x[k] > xa
            if state["x_in"]:
                out[spec["x"]] = budget * (1 - split)
            return out, (regime, state["x_in"])
        if kind == "tbill" and not regime:
            etf = spec["etf"]
            if etf == "SGOV" and not (m.has("SGOV", k) and m.has("SGOV", i)):
                etf = "BIL"
            out = {etf: budget}
        return out, (regime,)

    return f


ARMS: dict[str, dict] = {
    "VOO_hold_100": {"kind": "hold", "asset": "VOO", "budget": 1.0},
    "VOO_hold_93": {"kind": "hold", "asset": "VOO"},
    "VT35_100": {"kind": "base", "budget": 1.0},
    "VT35": {"kind": "base"},
    "A2": {"kind": "buffer", "b": 0.02},
    "A3": {"kind": "buffer", "b": 0.03},
    "B5": {"kind": "min_hold", "n": 5},
    "B10": {"kind": "min_hold", "n": 10},
    "C3": {"kind": "staged"},
    "D5": {"kind": "strength"},
    **{f"E-{x}-{int(s * 100)}": {"kind": "second", "x": x, "split": s}
       for x in ("GLD", "TLT", "IEF") for s in (0.8, 0.7)},
    "F-BIL": {"kind": "tbill", "etf": "BIL"},
    "F-SGOV": {"kind": "tbill", "etf": "SGOV"},
}
REFERENCE = {"VOO_hold_100", "VOO_hold_93", "VT35_100", "VT35"}


# ---------------------------------------------------------------- simulator

def simulate(m: Market, target_fn, mult: float):
    cost = SIDE_BPS * mult / 10_000
    nav, weights, curve, regimes, traded = 1.0, {}, [], [], 0.0
    for i in range(m.first, len(m.days)):
        day, prev = m.days[i], m.days[i - 1]
        want, regime = target_fn(i)
        regimes.append(regime)
        # overnight: prev close -> open (+ dividend on ex-date)
        value = {s: w * (m.bars[s][day][0] + m.divs[s].get(day, 0.0)) / m.bars[s][prev][1] for s, w in weights.items()}
        total = 1.0 - sum(weights.values()) + sum(value.values())
        nav *= total
        weights = {s: v / total for s, v in value.items()}
        names = set(want) | set(weights)
        if any(abs(want.get(s, 0.0) - weights.get(s, 0.0)) > BAND for s in names):
            turnover = sum(abs(want.get(s, 0.0) - weights.get(s, 0.0)) for s in names)
            traded += turnover
            nav *= 1 - cost * turnover
            weights = {s: w for s, w in want.items() if w > 0}
        value = {s: w * m.bars[s][day][1] / m.bars[s][day][0] for s, w in weights.items()}
        total = 1.0 - sum(weights.values()) + sum(value.values())
        nav *= total
        weights = {s: v / total for s, v in value.items()}
        curve.append((day, nav))
    return curve, regimes, traded


def switches(regimes: list[tuple]) -> list[int]:
    """Regime changes per component, counting the first entry from an initial cash state."""
    if not regimes:
        return []
    start = tuple(False if isinstance(r, bool) else 0 for r in regimes[0])
    counts = [0] * len(start)
    for a, b in zip([start, *regimes], regimes):
        for j in range(len(counts)):
            counts[j] += a[j] != b[j]
    return counts


def metrics(curve, a, b):
    seg = [v for d, v in curve if a <= d <= b]
    years = len(seg) / 252
    peak, dd = seg[0], 0.0
    for v in seg:
        peak = max(peak, v)
        dd = max(dd, 1 - v / peak)
    rets = [seg[i] / seg[i - 1] - 1 for i in range(1, len(seg))]
    mu = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / len(rets))
    return {"cagr": (seg[-1] / seg[0]) ** (1 / years) - 1, "max_dd": dd, "sharpe": mu / sd * math.sqrt(252) if sd else 0.0}


def gate(res: dict, base: dict) -> dict:
    f, bf = res["full"], base["full"]
    return {
        "cagr_or_sharpe_up": f["cagr"] > bf["cagr"] or f["sharpe"] > bf["sharpe"],
        "dd_within_2pp": f["max_dd"] <= bf["max_dd"] + 0.02,
        "wins_2017_2021": res["2017_2021"]["cagr"] > base["2017_2021"]["cagr"],
        "wins_2022_2026": res["2022_2026"]["cagr"] > base["2022_2026"]["cagr"],
    }


def run(m: Market) -> dict:
    out: dict = {"window": [START, END], "budget": BUDGET, "costs": {}}
    for mult in (1, 2):
        table = {}
        for arm, spec in ARMS.items():
            curve, regimes, traded = simulate(m, make_arm(spec, m), mult)
            res = {name: metrics(curve, a, b) for name, (a, b) in WINDOWS.items()}
            res["switches"] = switches(regimes)
            res["annual_turnover"] = traded / (len(curve) / 252)
            res["ending_multiple"] = curve[-1][1]
            table[arm] = res
        for arm in ARMS:
            if arm not in REFERENCE:
                table[arm]["gate"] = gate(table[arm], table["VT35"])
        out["costs"][str(mult)] = table
    out["promoted"] = [a for a in ARMS if a not in REFERENCE
                       and all(all(out["costs"][c][a]["gate"].values()) for c in ("1", "2"))]
    vt = out["costs"]["1"]["VT35"]["full"]
    out["reproduction_vs_stage_b"] = {"stage_b": STAGE_B_VT35, "screen_budget_093_1x": vt,
                                      "screen_budget_100_1x": out["costs"]["1"]["VT35_100"]["full"]}
    return out


def main() -> None:
    out = run(Market(*load()))
    (HERE / "RESULTS.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    for mult in ("1", "2"):
        print(f"--- costs {mult}x")
        for arm, r in out["costs"][mult].items():
            g = r.get("gate")
            print(f"{arm:13s} CAGR {r['full']['cagr']*100:6.2f}%  Sh {r['full']['sharpe']:.3f}  DD {r['full']['max_dd']*100:5.1f}%  "
                  f"17-21 {r['2017_2021']['cagr']*100:6.2f}%  22-26 {r['2022_2026']['cagr']*100:6.2f}%  "
                  f"sw {r['switches']}  to {r['annual_turnover']:.2f}"
                  + ("" if g is None else "  " + "".join("Y" if v else "n" for v in g.values())))
    print("promoted:", out["promoted"])


if __name__ == "__main__":
    main()
