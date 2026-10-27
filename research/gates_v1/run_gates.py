"""Apply the gates_v1 tooling to K (see PREREGISTRATION.md). Writes RESULTS.json, SUMMARY.txt and
DATA_MANIFEST.json. Run from the worktree root:

    /Users/batuhankiskac/.venvs/pb-research/bin/python -m research.gates_v1.run_gates

RESULTS.json carries no timestamps and rounded floats, so two runs at the same git commit must be
byte-identical. Wall-clock time goes to SUMMARY.txt only.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np

from research.fast_engine import fe_config, fe_data, fe_sim, fe_study
from research.fast_engine.fe_config import GATE_CAPITAL, START, TAX_CAPITALS
from research.gates_v1 import pbo, provenance, registry_backfill, stats

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT_RESULTS = HERE / "RESULTS.json"
OUT_SUMMARY = HERE / "SUMMARY.txt"
OUT_MANIFEST = HERE / "DATA_MANIFEST.json"

ARMS = ("C0", "A1u", "H2", "K")  # the band_h2_combo_v1 arms (C0 = production R2 control)
N_BLOCKS = 16
PURGE = 252
ANNUAL = 252


def _r(x, nd=10):
    """Deterministic float for JSON: rounded, NaN/inf as None."""
    if x is None:
        return None
    if isinstance(x, (np.floating, float)):
        x = float(x)
        if not math.isfinite(x):
            return None
        return round(x, nd)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, dict):
        return {k: _r(v, nd) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_r(v, nd) for v in x]
    return x


def arm_series():
    """Pre-tax and progressive after-tax NAV-based daily returns for the band_h2 arms and K."""
    days, o, c, _we, wr, _e2 = fe_data.main_inputs(0.0)
    fx, ufe = fe_study.try_market(days)
    T = len(days) - 1
    env = fe_sim.RunEnv(days, fx, ufe, START, T)
    cap = TAX_CAPITALS[GATE_CAPITAL]
    out = {"pre_tax": {}, "after_tax": {}}
    anchors = {}
    for arm in ARMS:
        r = fe_sim.simulate(o, c, wr, env, cap, START, T, arm=arm, mode="pre_tax", m=2)
        nav = np.asarray(r["_nav"], dtype=float)
        out["pre_tax"][arm] = nav[1:] / nav[:-1] - 1.0
        anchors[arm] = {
            "pre_tax_cagr_unliquidated": float((nav[-1] / nav[0]) ** (1.0 / r["years"]) - 1.0),
        }
    r = fe_sim.simulate(o, c, wr, env, cap, START, T, arm="K", mode="progressive", m=2)
    nav = np.asarray(r["_nav"], dtype=float)
    out["after_tax"]["K"] = nav[1:] / nav[:-1] - 1.0
    anchors["K"]["after_tax_cagr_liquidated"] = float(r["cagr_usd_liquidated"])
    anchors["K"]["window"] = {"start": str(days[START]), "end": str(days[-1]), "years": float(r["years"])}
    return out, anchors


def inputs_manifest():
    files = {
        "ledger": ROOT / "research/batch_screen/REGISTRY.jsonl",
        "grid_early_quorum": HERE / "inputs/early_quorum_v1_RESULTS.json",
        "grid_vt_level_tax": HERE / "inputs/vt_level_tax_v1_RESULTS.json",
        "grid_leverage_frontier": HERE / "inputs/leverage_frontier_v1_RESULTS.json",
        "band_h2_results": ROOT / "research/band_h2_combo_v1/RESULTS.json",
        "band_h2_h2_anchors": ROOT / "research/band_h2_combo_v1/H2_ANCHORS.json",
        "data_bars": fe_config.BARS,
        "data_dtb3": fe_config.DTB3_CSV,
        "data_fx": fe_config.FX_CSV,
        "data_yiufe": fe_config.UFE_CSV,
        "data_gspc": fe_config.GSPC_CSV,
        "data_index": fe_config.INDEX_CSV,
        "data_tb3ms": fe_config.TB3MS_CSV,
    }
    return {k: Path(v) for k, v in files.items()}


def run() -> dict:
    t0 = time.perf_counter()
    series, anchors = arm_series()
    t_sim = time.perf_counter() - t0

    # -- anchor check: K pre-tax base m=2 unliquidated CAGR, published 15.7649 %
    k_cagr = anchors["K"]["pre_tax_cagr_unliquidated"]
    anchor_pp = abs(k_cagr * 100.0 - 15.7649)

    # -- registry (item 25)
    reg = registry_backfill.build_backfill()
    n_corr = reg["N_corrected_raw"]

    # -- effective trials from the rebuilt family (C0, A1u, H2, K pre-tax)
    mat = np.column_stack([series["pre_tax"][a] for a in ARMS])
    corr = np.corrcoef(mat, rowvar=False)
    meff = stats.n_eff_meff(corr)
    n_eff = (n_corr - len(ARMS)) + meff["meff"]

    # -- K statistics
    def gate_block(ret, label):
        m = stats.sample_moments(ret)
        sr, t, skew, kurt = m["sr"], m["T"], m["skew"], m["kurt"]
        v_floor = stats.var_trials_floor(t)
        arm_srs = [stats.sample_moments(series["pre_tax"][a])["sr"] for a in ARMS] if label == "pre_tax" else None
        v_family = float(np.var(arm_srs, ddof=1)) if arm_srs else None
        d_floor = stats.dsr(sr, t, skew, kurt, n_eff, v_floor)
        d_raw = stats.dsr(sr, t, skew, kurt, n_corr, v_floor)
        block = {
            "T": t,
            "sr_per_period": sr,
            "sr_annualised": sr * math.sqrt(ANNUAL),
            "skew": skew,
            "kurt_pearson": kurt,
            "psr_vs_0": stats.psr(sr, 0.0, t, skew, kurt),
            "min_trl_vs_0_sessions": stats.min_trl(sr, 0.0, skew, kurt, 0.05),
            "min_trl_vs_0_years": stats.min_trl(sr, 0.0, skew, kurt, 0.05) / ANNUAL,
            "var_floor": v_floor,
            "dsr_primary": {"N_eff": n_eff, "V": v_floor, "sr0_per_period": d_floor["sr0"],
                            "sr0_annualised": d_floor["sr0"] * math.sqrt(ANNUAL), "dsr": d_floor["dsr"]},
            "min_trl_vs_sr0_sessions": stats.min_trl(sr, d_floor["sr0"], skew, kurt, 0.05),
            "dsr_no_corr_adjust": {"N": n_corr, "V": v_floor, "sr0_per_period": d_raw["sr0"],
                                   "dsr": d_raw["dsr"]},
        }
        if v_family is not None:
            d_fam = stats.dsr(sr, t, skew, kurt, n_eff, v_family)
            block["dsr_sensitivity_family_variance"] = {"V_family": v_family, "dsr": d_fam["dsr"],
                                                        "sr0_per_period": d_fam["sr0"]}
        return block

    k_pre = gate_block(series["pre_tax"]["K"], "pre_tax")
    k_at = gate_block(series["after_tax"]["K"], "after_tax")

    # -- PBO over the four rebuilt arms (pre-tax)
    pbo_res = pbo.pbo_cscv(mat, n_blocks=N_BLOCKS, purge=PURGE)

    # -- provenance
    inputs = inputs_manifest()
    stamp = provenance.stamp(inputs, alfred_vintage=None, cwd=ROOT)

    survive = (k_pre["dsr_primary"]["dsr"] >= 0.95) and (pbo_res["pbo"] < 0.5)
    results = {
        "campaign": "gates_v1",
        "verdict": {"K_survives_DSR_and_PBO": bool(survive),
                    "rule": "DSR(V_floor, N_eff) >= 0.95 AND PBO < 0.5 on the pre-tax primary series"},
        "anchor_check": {"K_pre_tax_cagr_pct": _r(k_cagr * 100.0, 6), "published_pct": 15.7649,
                         "abs_diff_pp": _r(anchor_pp, 8), "window": anchors["K"]["window"]},
        "registry": {"N_ledger": reg["N_ledger"], "backfill_configs_by_study": reg["backfill_configs_by_study"],
                     "backfill_added": reg["backfill_added"], "N_corrected": n_corr,
                     "N_eff": n_eff, "family": {"arms": list(ARMS), "rho_bar": meff["rho_bar"],
                                                "meff": meff["meff"]}},
        "K_pre_tax_primary": k_pre,
        "K_after_tax_sensitivity": k_at,
        "PBO": {"arms": list(ARMS), **pbo_res},
        "provenance": stamp,
    }
    results = _r(results)
    manifest = {"campaign": "gates_v1", "files": {}}
    for name, p in inputs.items():
        rel = str(p.resolve().relative_to(ROOT)) if str(p.resolve()).startswith(str(ROOT)) else str(p)
        manifest["files"][rel] = {"sha256": provenance.sha256_file(p), "bytes": p.stat().st_size, "role": name}
    t_total = time.perf_counter() - t0
    summary_lines = [
        "gates_v1 SUMMARY (K vs corrected registry; see docs/research/gates_v1.md)",
        f"verdict: K survives DSR and PBO = {survive}",
        f"K pre-tax anchor: {k_cagr * 100:.4f} % (published 15.7649 %, diff {anchor_pp:.6f} pp)",
        f"N ledger {reg['N_ledger']}, backfill +{reg['backfill_added']}, N corrected {n_corr}",
        f"family ({', '.join(ARMS)}): rho_bar {meff['rho_bar']:.6f}, Meff {meff['meff']:.4f}, N_eff {n_eff:.2f}",
        f"K pre-tax: SR ann {k_pre['sr_annualised']:.4f}, skew {k_pre['skew']:.4f}, kurt {k_pre['kurt_pearson']:.4f}, T {k_pre['T']}",
        f"K pre-tax PSR(0) {k_pre['psr_vs_0']:.6f}; MinTRL(0) {k_pre['min_trl_vs_0_years']:.4f} y",
        f"K pre-tax DSR (V_floor, N_eff) {k_pre['dsr_primary']['dsr']:.6f}; SR0 ann {k_pre['dsr_primary']['sr0_annualised']:.4f}",
        f"K pre-tax MinTRL vs SR0 {k_pre['min_trl_vs_sr0_sessions']} sessions",
        f"K pre-tax DSR, no correlation adjustment (N={n_corr}): {k_pre['dsr_no_corr_adjust']['dsr']:.6f}",
        f"K pre-tax DSR, family variance (sensitivity): {k_pre.get('dsr_sensitivity_family_variance', {}).get('dsr')}",
        f"K after-tax (progressive) PSR(0) {k_at['psr_vs_0']:.6f}; DSR (V_floor, N_eff) {k_at['dsr_primary']['dsr']:.6f}",
        f"PBO (CSCV, S={N_BLOCKS}, purge {PURGE}, {pbo_res['n_combinations']} combinations) = {pbo_res['pbo']:.6f}",
        f"wall clock: simulation {t_sim:.1f} s, total {t_total:.1f} s",
        "",
    ]
    return {"results": results, "manifest": manifest, "summary": "\n".join(summary_lines)}


def main() -> None:
    out = run()
    OUT_RESULTS.write_text(json.dumps(out["results"], indent=1, sort_keys=True) + "\n")
    OUT_MANIFEST.write_text(json.dumps(out["manifest"], indent=1, sort_keys=True) + "\n")
    OUT_SUMMARY.write_text(out["summary"])
    print(out["summary"])


if __name__ == "__main__":
    main()
