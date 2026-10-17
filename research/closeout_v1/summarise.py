"""research/closeout_v1/summarise.py -- the batch's numbers, read out of the records.

Every cell comes from `research/batch_screen/results/closeout_v1/`: the control's own record and
each arm's `stage1` / `stage1_gates` (the arms all stop at stage 1) plus the deflation's
`results/DEFLATION.json`. Nothing is recomputed here -- the failure report is what the framework
wrote, which is what the protocol asks for.

    PYTHONPATH=. ~/.venvs/pb-research/bin/python -m research.closeout_v1.summarise
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.fast_engine.fe_config import TAX_WINDOWS

HERE = Path(__file__).resolve().parent
RES = Path("research/batch_screen/results/closeout_v1")
ARMS = ("IB35_K/C_K", "IB24_2L/A_2L", "IB24_2L/B_2L", "IB24_2L/C_2L")
WIN_ORDER = tuple(TAX_WINDOWS)


def _file(arm_key: str) -> Path:
    idea, arm = arm_key.split("/")
    return RES / f"{idea}__{arm}.json"


def row(arm_key: str, ctrl: dict) -> dict:
    d = json.loads(_file(arm_key).read_text())
    s1, g = d["stage1"], d["stage1_gates"]
    base, cal = s1["main"]["base"]["m2"], s1["main"]["calibrated"]["m2"]
    cb, cc = ctrl["main"]["base"]["m2"], ctrl["main"]["calibrated"]["m2"]
    at = s1["after_tax"]
    cat = ctrl["after_tax"]
    out = {
        "arm": arm_key,
        "verdict": d["verdict"],
        "stage1_pass": bool(d["stage1_pass"]),
        "spec": d["spec"],
        "rule": d["rule"],
        "leg": d["leg"],
        "legs": d.get("legs"),
        "window": d["stage1"]["win"] if "win" in d["stage1"] else None,
        "hashes": d["hashes"],
        # the campaign's pre-tax convention: the un-liquidated `cagr` (the published pre-tax cells)
        "cagr_base_pct": float(base["cagr"]) * 100.0,
        "cagr_cal_pct": float(cal["cagr"]) * 100.0,
        "cagr_ctrl_base_pct": float(cb["cagr"]) * 100.0,
        "cagr_ctrl_cal_pct": float(cc["cagr"]) * 100.0,
        "cagr_base_delta_pp": (float(base["cagr"]) - float(cb["cagr"])) * 100.0,
        "cagr_cal_delta_pp": (float(cal["cagr"]) - float(cc["cagr"])) * 100.0,
        "max_dd_base_pct": float(base["max_dd"]) * 100.0,
        "max_dd_cal_pct": float(cal["max_dd"]) * 100.0,
        "max_dd_delta_pp": g["max_dd_delta_pp"],
        "turnover_ratio": g["turnover_ratio"],
        "sells_per_year": float(base["sells_per_year"]),
        "trades": int(base["trades"]),
        "after_tax": {
            wn: {
                "arm_pct": float(at[wn]["after_tax_cagr"]) * 100.0,
                "ctrl_pct": float(cat[wn]["after_tax_cagr"]) * 100.0,
                "delta_pp": (float(at[wn]["after_tax_cagr"]) - float(cat[wn]["after_tax_cagr"])) * 100.0,
                "tax_usd": float(at[wn]["tax_usd"]),
                "ctrl_tax_usd": float(cat[wn]["tax_usd"]),
                "max_dd_pct": float(at[wn]["max_dd_after_tax"]) * 100.0,
            }
            for wn in WIN_ORDER
        },
        "b1_median_delta_pp": g["b1_median_delta_pp"],
        "b1_share_gt_0": float(s1["boot"]["tax"]["progressive"]["share_gt_0"]),
        "b2_median_delta_pp": float(s1["boot"]["tax"]["flat"]["median_delta_pp"]),
        "b2_share_gt_0": float(s1["boot"]["tax"]["flat"]["share_gt_0"]),
        "b1_mean_delta_pp": float(s1["boot"]["tax"]["progressive"]["mean_delta_pp"]),
        "at2000_delta_pp": g["after_tax_2000_delta_pp"],
        "stage1_gates": g,
        "gates": d["gates"],
        "stage2": d["stage2"],
        "stage2_pass": d["stage2_pass"],
        "b1_vector_finite": bool(all(isinstance(x, (int, float)) and x == x for x in d["b1_delta_pp"])),
        "b1_vector_n": len(d["b1_delta_pp"]),
    }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.closeout_v1.summarise")
    ap.add_argument("--json", action="store_true", help="write research/closeout_v1/ARM_TABLE.json")
    args = ap.parse_args(argv)
    raw = json.loads((RES / "_control.json").read_text())
    ctrl = raw["record"] if isinstance(raw["record"], dict) else json.loads(raw["record"])
    ctrl = ctrl.get("stage1", ctrl)  # the control file holds the stage-1 record itself
    table = {k: row(k, ctrl) for k in ARMS}
    defl = json.loads((Path("research/batch_screen/results") / "DEFLATION.json").read_text())
    rep = {"batch": "closeout_v1", "control": "k_control/K", "arms": table,
           "deflation": {"batch": defl.get("batch"), "stage2_pass": defl.get("stage2_pass"),
                         "declared": defl.get("declared"), "n_trials": defl.get("n_trials"),
                         "white_rc": defl.get("white_rc"), "rw_stepdown": defl.get("rw_stepdown"),
                         "deflated_sharpe": {k: {"dsr": v.get("dsr"), "sr0": v.get("sr0"),
                                                 "sr_obs": v.get("sr_obs")}
                                             for k, v in (defl.get("deflated_sharpe") or {}).items()},
                         "var_sr": defl.get("var_sr"), "alpha": defl.get("alpha"),
                         "excluded_non_finite": defl.get("excluded_non_finite")},
           "control_pre_tax": {
               "base_cagr_pct": float(ctrl["main"]["base"]["m2"]["cagr"]) * 100,
               "cal_cagr_pct": float(ctrl["main"]["calibrated"]["m2"]["cagr"]) * 100,
               "base_max_dd_pct": float(ctrl["main"]["base"]["m2"]["max_dd"]) * 100,
               "cal_max_dd_pct": float(ctrl["main"]["calibrated"]["m2"]["max_dd"]) * 100,
               "after_tax": {wn: float(ctrl["after_tax"][wn]["after_tax_cagr"]) * 100
                             for wn in WIN_ORDER}}}
    if args.json:
        (HERE / "ARM_TABLE.json").write_text(json.dumps(rep, indent=1, sort_keys=True))
        print(f"wrote {HERE / 'ARM_TABLE.json'}")
    print(f"control K: pre-tax {rep['control_pre_tax']['base_cagr_pct']:.4f} / "
          f"{rep['control_pre_tax']['cal_cagr_pct']:.4f} %, maxDD "
          f"{rep['control_pre_tax']['base_max_dd_pct']:.2f} / "
          f"{rep['control_pre_tax']['cal_max_dd_pct']:.2f} %")
    print(f"  after tax $100k: " + "  ".join(
        f"{wn[:4]} {rep['control_pre_tax']['after_tax'][wn]:.4f}%" for wn in WIN_ORDER))
    hdr = (f"{'arm':<14}{'preB':>8}{'preC':>8}{'ddB':>8}{'ddC':>8}{'TOxK':>7}"
           f"{'AT2000':>9}{'AT2010':>9}{'AT2017':>9}{'B1med':>9}{'B1sh':>6}{'B2med':>9}{'B2sh':>6}")
    print("\n" + hdr)
    for k, v in table.items():
        at = v["after_tax"]
        print(f"{k:<14}{v['cagr_base_pct']:>8.2f}{v['cagr_cal_pct']:>8.2f}"
              f"{v['max_dd_base_pct']:>8.2f}{v['max_dd_cal_pct']:>8.2f}"
              f"{v['turnover_ratio']['base']:>7.3f}"
              f"{at[WIN_ORDER[0]]['delta_pp']:>9.4f}{at[WIN_ORDER[1]]['delta_pp']:>9.4f}"
              f"{at[WIN_ORDER[2]]['delta_pp']:>9.4f}"
              f"{v['b1_median_delta_pp']:>9.4f}{v['b1_share_gt_0']:>6.3f}"
              f"{v['b2_median_delta_pp']:>9.4f}{v['b2_share_gt_0']:>6.3f}")
    for k, v in table.items():
        failed = []
        if v["at2000_delta_pp"] <= 0:
            failed.append(f"AT2000 delta {v['at2000_delta_pp']:+.4f} pp")
        if v["b1_median_delta_pp"] < 0:
            failed.append(f"B1 median {v['b1_median_delta_pp']:+.4f} pp")
        if max(v["max_dd_delta_pp"].values()) > 2.0:
            failed.append(f"max DD {v['max_dd_delta_pp']['base']:+.2f}/"
                          f"{v['max_dd_delta_pp']['calibrated']:+.2f} pp")
        if max(v["turnover_ratio"].values()) > 2.0:
            failed.append(f"turnover {v['turnover_ratio']['base']:.3f}xK")
        print(f"  {k:<14} {v['verdict']:<16} failed: " + ("; ".join(failed) or "none"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
