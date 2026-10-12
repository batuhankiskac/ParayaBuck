#!/usr/bin/env python3
"""e2_integration_v1: how far do production approximations of E2 deviate from research E2?

Offline. Re-uses the archived signal_ensemble_v1 screen (extracted from tag
archive/research/signal-ensemble-v1-2026-10 into ./archive/) and substitutes
the TSM/DON inputs. Window and mechanics are the research ones
(2000-03-09 -> 2026-09-29, decide at close, fill next open, 0.5 pp band,
cap 0.98, 4 bps x m). Writes DEVIATION.json next to this file.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SCREEN = HERE / "archive/research/signal_ensemble_v1/screen.py"
_spec = importlib.util.spec_from_file_location("se1_screen", SCREEN)
se = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(se)
trv2 = se.trv2


def flat_bill(days: int, annual: float) -> np.ndarray:
    return se.bill_level(np.full(days, annual))


def variant_signals(q, y, *, tsm_series: str, hurdle: str | float, don_series: str):
    price = q[1]
    tr = se.tr_level(q[1], q[2])
    tsm_in = tr if tsm_series == "tr" else price
    don_in = tr if don_series == "tr" else price
    if hurdle == "dtb3":
        bills = se.bill_level(y)
    else:
        bills = flat_bill(len(y), float(hurdle))
    return {"SMA": trv2.sma_up(price, se.SMA_N), "TSM": se.tsm_up(tsm_in, bills), "DON": se.don_up(don_in)}


VARIANTS = {
    # name: (tsm series, hurdle, don series)
    "E2_research": ("tr", "dtb3", "tr"),
    "E2_prod_price_h0": ("price", 0.0, "price"),
    "E2_price_dtb3": ("price", "dtb3", "price"),
    "E2_tr_h0": ("tr", 0.0, "tr"),
    "E2_price_h2": ("price", 0.02, "price"),
    "E2_price_h4": ("price", 0.04, "price"),
    "E2_price_tsm_tr_don": ("price", "dtb3", "tr"),
}


def run(spread: float, m: int) -> dict:
    days, arr, y = trv2.load()
    q = arr["QQQ"]
    o, c = trv2.synthetic_2x(q[0], q[1], q[2], y + spread, se.EXP_Q)
    vol = trv2.vol_n(c)
    start = se.START
    dates = days[start - 1 :]
    years = trv2.calendar_years(dates[0], dates[-1])
    out = {}
    research_sig = variant_signals(q, y, tsm_series="tr", hurdle="dtb3", don_series="tr")
    research_w = se.build_wants(research_sig, vol, "E2")[0, 0]
    arms = {"C0": (research_sig, "C0")}
    for name, (ts, h, ds) in VARIANTS.items():
        arms[name] = (variant_signals(q, y, tsm_series=ts, hurdle=h, don_series=ds), "E2")
    dec = slice(start - 1, len(days) - 1)
    for name, (sig, arm) in arms.items():
        w = se.build_wants(sig, vol, arm)
        nav, turn, ntr = trv2.simulate_multi(o, c, w, se.COST_PER_SIDE * m, start)
        st = trv2.curve_stats(dates, nav[0])
        on = (w[0, 0][dec] > 0)
        ron = (research_w[dec] > 0)
        seg17 = se.seg(dates, nav[0], "2017-01-02", "2026-07-30")
        seg_hi = se.seg(dates, nav[0], "2022-12-30", "2026-09-29")
        out[name] = {
            "cagr_2017_01_03_to_2026_07_30": (1 + seg17["total_return"]) ** (1 / trv2.calendar_years(seg17["from"], seg17["to"])) - 1,
            "max_dd_2017_01_03_to_2026_07_30": seg17["max_dd"],
            "cagr_2023_on": (1 + seg_hi["total_return"]) ** (1 / trv2.calendar_years(seg_hi["from"], seg_hi["to"])) - 1,
            "cagr": st["cagr"],
            "max_dd": st["max_dd"],
            "sharpe": trv2.sharpe_daily(nav[0]),
            "turnover_annual": float(turn[0] / years),
            "decision_disagreement_vs_research_E2": float((on != ron).mean()),
            "tsm_on_share": float(sig["TSM"][0, dec].mean()),
            "don_on_share": float(sig["DON"][0, dec].mean()),
        }
    return {"window": [dates[1], dates[-1]], "arms": out}


def main() -> None:
    result = {}
    for model, spread in se.FIN.items():
        for m in (1, 2):
            result[f"{model}_m{m}"] = run(spread, m)
    (HERE / "DEVIATION.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    for key, block in result.items():
        print(key, block["window"])
        for name, st in block["arms"].items():
            print(
                f"  {name:22s} CAGR {st['cagr']*100:6.2f}%  DD {st['max_dd']*100:5.1f}%  Sharpe {st['sharpe']:.3f}"
                f"  turn {st['turnover_annual']:.2f}  disagree {st['decision_disagreement_vs_research_E2']*100:5.2f}%"
                f"  2017-26 {st['cagr_2017_01_03_to_2026_07_30']*100:6.2f}%/{st['max_dd_2017_01_03_to_2026_07_30']*100:4.1f}%  2023+ {st['cagr_2023_on']*100:6.2f}%"
            )


if __name__ == "__main__":
    main()
