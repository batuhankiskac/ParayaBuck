#!/usr/bin/env python3
"""smooth_target_v1 diagnostics (report only; reads RESULTS.json, changes no arm or gate).

Written *after* the pre-registered run to explain the gate outcomes. Answers four questions
from the pre-registration's expectations and from `deriv_vote_v1`'s finding that "the tax base
is the vol target, not the vote":

1. Did the arms do what they say - did Sum|dw|, turnover and the realised TRY gain fall?
2. If Sum|dw| fell, why did sells/yr not fall with it (trade size vs trade count)?
3. Where exactly did each gate miss, and by how much?
4. What is the after-tax channel - less gain, or the same gain paid later?

Usage: python research/smooth_target_v1/diagnostics.py [RESULTS.json path]
Writes research/smooth_target_v1/diagnostics.json and prints a human-readable table.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARMS = ("C0", "S1", "S2", "S3", "S4")
CANDIDATES = ("S1", "S2", "S3", "S4")
WINDOWS = ("2000_03_to_2026_09", "2010_01_to_end", "2017_01_to_end")


def load(path):
    return json.loads(Path(path).read_text())


def activity(d):
    """Traded volume vs trade counts, main window and 2017+, base financing m = 2."""
    out = {}
    for arm in ARMS:
        a = d["main_window"]["base"]["m2"][arm]
        f = a["events_full"]
        g = a["events_2017_on"]
        resizes = f["counts"]["resize_up"] + f["counts"]["resize_down"]
        out[arm] = {
            "sum_abs_dw": a["sum_abs_dw"],
            "turnover_annual": a["turnover_annual"],
            "mean_target": a["mean_target"],
            "trades": f["counts"]["entries"] + f["counts"]["exits"] + resizes,
            "resizes": resizes,
            "sells": f["counts"]["exits"] + f["counts"]["resize_down"],
            "mean_trade_size": a["sum_abs_dw"] / max(1, f["counts"]["entries"] + f["counts"]["exits"] + resizes),
            "mean_resize_size": (f["turnover_from_resizes_per_year"] * f["years"]) / max(1, resizes),
            "turnover_entries_exits_per_year": f["turnover_from_entries_exits_per_year"],
            "turnover_resizes_per_year": f["turnover_from_resizes_per_year"],
            "sells_per_year": f["sells_per_year"],
            "mean_sell_size": ((f["turnover_from_resizes_per_year"] + f["turnover_from_entries_exits_per_year"])
                               * f["years"]) / max(1, f["counts"]["exits"] + f["counts"]["resize_down"]),
            "sells_per_year_2017": g["sells_per_year"],
            "turnover_2017": g["turnover_per_year"],
        }
    return out


def gate_margins(d):
    """How far each arm is from every gate, in the gate's own units."""
    c0 = d["main_window"]["base"]["m2"]["C0"]
    out = {}
    for arm in CANDIDATES:
        g = d["gates_g1_g9"][arm]
        a = d["main_window"]["base"]["m2"][arm]
        ac = d["main_window"]["calibrated"]["m2"][arm]
        c0c = d["main_window"]["calibrated"]["m2"]["C0"]
        m = {
            "G1_cagr_delta_pp": (a["cagr"] - c0["cagr"]) * 100,
            "G1_calmar_ratio": a["calmar"] / c0["calmar"],
            "G1_calibrated_cagr_delta_pp": (ac["cagr"] - c0c["cagr"]) * 100,
            "G2_dd_delta_pp": (a["max_dd"] - c0["max_dd"]) * 100,
            "G3_2017_delta_pp": (a["2017_end"]["cagr"] - c0["2017_end"]["cagr"]) * 100,
            "G4_wins": g["G4_wins"],
            "G5n_base_median_pp": d["bootstrap_main_block20_m2"]["base"][arm]["diff_vs_C0_median"] * 100,
            "G5n_cal_median_pp": d["bootstrap_main_block20_m2"]["calibrated"][arm]["diff_vs_C0_median"] * 100,
            "G7_median_dcagr_pp": g["G7_detail"]["median_cagr_diff"] * 100,
            "G7_max_ddd_pp": g["G7_detail"]["max_dd_diff_max"] * 100,
            "G7_windows_cagr_ok": g["G7_detail"]["windows_cagr_ge_minus_0_5pp"],
            "G7_windows_dd_ok": g["G7_detail"]["windows_dd_within_3pp"],
            "G8_pooled_median_pp": g["G8_pooled_median_diff"] * 100,
            "G9_turnover_ratio_vs_c0": a["turnover_annual"] / c0["turnover_annual"],
            "gateI_share_dd_within_3pp": d["gate_I_instruments"][arm]["share_delta_dd_within_3pp"],
            "result": g["pass_all"] and d["gates_g1_g9"][arm]["T_after_tax"],
        }
        for wn in WINDOWS:
            t = d["after_tax_100k"]["windows"][wn]
            m[f"AT_{wn}_delta_pp"] = (t[arm]["progressive"]["cagr"] - t["C0"]["progressive"]["cagr"]) * 100
        for mode, key in (("progressive", "B1"), ("flat", "B2")):
            e = d["after_tax_bootstrap"][mode]["arms"][arm]
            m[f"{key}_median_delta_pp"] = e["delta_vs_C0_median_pp"]
            m[f"{key}_share_gt_0"] = e["share_delta_gt_0"]
        out[arm] = m
    return out


def tax_channel(d):
    """Where the realised TRY gain lands (2000 window, progressive) and what was paid."""
    out = {}
    for arm in ARMS:
        p = d["after_tax_100k"]["windows"]["2000_03_to_2026_09"][arm]["progressive"]
        s = p["gain_share_from_trims_vs_exits"]
        by = s["gain_try_by_reason"]
        total = s["total_net_gain_try"]
        out[arm] = {
            "trim_try_m": s["trim_gain_try"] / 1e6,
            "exit_try_m": s["exit_gain_try"] / 1e6,
            "tax_funding_try_m": by.get("tax", 0.0) / 1e6,
            "liquidation_try_m": by.get("liquidation", 0.0) / 1e6,
            "total_gain_try_m": total / 1e6,
            "trim_share": s["trim_share_of_total"],
            "deferred_share": (by.get("liquidation", 0.0) + s["exit_gain_try"]) / total if total else None,
            "tax_paid_usd": p["tax_usd"],
            "after_tax_cagr": p["cagr"],
            "pre_tax_cagr": d["after_tax_100k"]["windows"]["2000_03_to_2026_09"][arm]["pre_tax"]["cagr"],
            "tax_drag_pp": (d["after_tax_100k"]["windows"]["2000_03_to_2026_09"][arm]["pre_tax"]["cagr"]
                            - p["cagr"]) * 100,
        }
    return out


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "RESULTS.json"
    d = load(path)
    act = activity(d)
    gm = gate_margins(d)
    tx = tax_channel(d)
    out = {"source": str(path), "campaign": d.get("campaign"), "verdict": d.get("verdict"),
           "note": "report only: written after the pre-registered run, changes no arm, gate or parameter",
           "activity": act, "gate_margins": gm, "tax_channel_2000_window": tx}
    (HERE / "diagnostics.json").write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")

    print("activity (base, m=2, main window): arm | Sum|dw| | turn/yr | trades | resizes | sells | "
          "mean trade | mean resize | mean sell | 2017+ sells/yr")
    for arm in ARMS:
        a = act[arm]
        print(f"  {arm:4s} {a['sum_abs_dw']:8.2f} {a['turnover_annual']:7.3f} {a['trades']:6d} "
              f"{a['resizes']:7d} {a['sells']:6d} {a['mean_trade_size']:10.5f} {a['mean_resize_size']:11.5f} "
              f"{a['mean_sell_size']:9.5f} {a['sells_per_year_2017']:7.2f}")
    print()
    print("gate margins:")
    for arm in CANDIDATES:
        m = gm[arm]
        print(f"  {arm}: dCAGR {m['G1_cagr_delta_pp']:+.3f} pp, Calmar x{m['G1_calmar_ratio']:.3f} "
              f"({'P' if m['G1_cagr_delta_pp'] >= 0 or (m['G1_calmar_ratio'] >= 1.1 and m['G1_cagr_delta_pp'] >= -0.5) else 'F'}), "
              f"dDD {m['G2_dd_delta_pp']:+.3f} pp, 2017+ {m['G3_2017_delta_pp']:+.3f} pp, G4 {m['G4_wins']}, "
              f"G5n {m['G5n_base_median_pp']:+.3f} / {m['G5n_cal_median_pp']:+.3f} pp, "
              f"G7 {m['G7_median_dcagr_pp']:+.3f} pp ({m['G7_windows_cagr_ok']}/6 cagr, {m['G7_windows_dd_ok']}/6 dd), "
              f"G8 {m['G8_pooled_median_pp']:+.3f} pp, G9 x{m['G9_turnover_ratio_vs_c0']:.2f}, "
              f"gateI dDD {m['gateI_share_dd_within_3pp'] * 100:.1f}%")
        print("       AT " + " / ".join(f"{m[f'AT_{w}_delta_pp']:+.2f}" for w in WINDOWS) +
              f" pp | B1 {m['B1_median_delta_pp']:+.3f} pp {m['B1_share_gt_0'] * 100:.1f}% | "
              f"B2 {m['B2_median_delta_pp']:+.3f} pp {m['B2_share_gt_0'] * 100:.1f}% | "
              f"pass_all {m['result']}")
    print()
    print("tax channel (2000 window, progressive): arm | trims | exits | tax sale | liquidation | total | "
          "trim share | deferred share | tax USD | drag pp")
    for arm in ARMS:
        t = tx[arm]
        print(f"  {arm:4s} {t['trim_try_m']:8.2f}m {t['exit_try_m']:8.2f}m {t['tax_funding_try_m']:6.2f}m "
              f"{t['liquidation_try_m']:8.2f}m {t['total_gain_try_m']:8.2f}m {t['trim_share'] * 100:7.1f}% "
              f"{t['deferred_share'] * 100:8.1f}% {t['tax_paid_usd']:11,.0f} {t['tax_drag_pp']:6.2f}")


if __name__ == "__main__":
    main()
