#!/usr/bin/env python3
"""Write DATA_MANIFEST.json: SHA-256 and provenance of every k_stage_b_v1 input.

Deterministic: only file contents, paths and the recorded provenance strings go
in, so a re-run overwrites the same bytes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

R2_TAG = "archive/research/r2-stage-b-v1-2026-10"
SLEEVE_TAG = "archive/research/sleeve-candidates-v2-v3-2026-09"
ENSEMBLE_TAG = "archive/research/signal-ensemble-v1-2026-10"
GB3_TAG = "archive/research/gain-band-v3-2026-10"

INPUTS = {
    "stage_b/data/historical/substitute_v5_bars_qqq_spy_qld.csv": (
        R2_TAG,
        f"{R2_TAG}:research/r2_stage_b_v1/stage_b/data/historical/substitute_v5_bars_qqq_spy_qld.csv; "
        "QQQ/SPY/QLD rows of the profit_max_v1 Alpaca SIP campaign file (split-adjusted price closes)",
    ),
    "stage_b/data/historical/substitute_v5_actions_qqq_spy_qld.csv": (
        R2_TAG,
        f"{R2_TAG}:research/r2_stage_b_v1/stage_b/data/historical/substitute_v5_actions_qqq_spy_qld.csv; "
        "the same campaign file's corporate actions for those three symbols",
    ),
    "stage_b/data/historical/substitute_v5_plus_warmup_bars_qqq_spy_qld.csv": (
        R2_TAG,
        f"{R2_TAG}:research/r2_stage_b_v1/stage_b/data/historical/"
        "substitute_v5_plus_warmup_bars_qqq_spy_qld.csv; THE BARS THE ARMS TRADED ON "
        "(Yahoo warm-up 2014-01-02..2015-12-31 + the substitute rows; without it R2 starts 145 "
        "sessions late, see docs/research/r2_stage_b_v1.md)",
    ),
    "stage_b/data/historical/warmup_yahoo_2014_2015_bars_qqq_qld_spy.csv": (
        R2_TAG,
        f"{R2_TAG}:.../warmup_yahoo_2014_2015_bars_qqq_qld_spy.csv; the warm-up half above",
    ),
    "stage_b/data/historical/equity_entity_lifetimes_full_v5.csv": (
        R2_TAG,
        f"{R2_TAG}:.../equity_entity_lifetimes_full_v5.csv; entity lifetimes for the terminal-lifecycle "
        "model (committed on main since the campaign)",
    ),
    "taxref/data/historical/after_tax_tr_v1_usdtry_cbrt.csv": (
        GB3_TAG,
        "CBRT USD/TRY forex buying rate, as recorded by after_tax_tr_v1; SHA-256 matches "
        f"{GB3_TAG}:research/gain_band_v3/DATA_MANIFEST.json (2000-01-03..2026-10-09)",
    ),
    "taxref/data/historical/after_tax_tr_v1_yiufe.csv": (
        GB3_TAG,
        "TUIK Yİ-ÜFE monthly index, as recorded by after_tax_tr_v1; SHA-256 matches "
        f"{GB3_TAG}:research/gain_band_v3/DATA_MANIFEST.json (1999-01..2026-09)",
    ),
}

CODE = {
    "stage_b/research/paper_config_v2/run_stage_b.py": (
        R2_TAG,
        "the archived Stage B runner, this worktree's copy, plus the k_stage_b_v1 audit additions "
        "(rebalances with the A1u/H2 hold records, band fallback log lines)",
    ),
    "stage_b/research/sleeve_candidates_v2/stage_b/run_stage_b.py": (
        SLEEVE_TAG,
        "harness copy; adapt_harness.py adaptations + the single k_stage_b_v1 change "
        "(execution_policy=portfolio_execution_policy_from_config(config.execution))",
    ),
    "stage_b/research/sleeve_candidates_v2/stage_b/composite.py": (SLEEVE_TAG, "harness copy"),
    "stage_b/research/physical_availability_clock_v2/run_historical.py": (SLEEVE_TAG, "harness copy"),
    "stage_b/research/physical_availability_clock_v2/shared_replay.py": (
        SLEEVE_TAG,
        "the shared-ledger physical replay the runner drives",
    ),
    "stage_b/research/physical_availability_clock_v2/timing.py": (SLEEVE_TAG, "harness copy"),
    "pc2_evaluate.py": (
        ENSEMBLE_TAG,
        f"{ENSEMBLE_TAG}:research/paper_config_v2/evaluate.py, byte copy; the metric definitions "
        "(_returns/_metrics/_accounting/_summary) the Stage B reports share",
    ),
    "taxref/research/gain_band_v3/tax_engine.py": (
        GB3_TAG,
        f"{GB3_TAG}:research/gain_band_v3/tax_engine.py, byte copy; the corrected Turkish after-tax "
        "engine (FIFO + Yİ-ÜFE indexation + progressive tariff + March/July instalments)",
    ),
    "taxref/research/trend_robustness_v2/screen.py": (
        GB3_TAG,
        "symlinked from research/trend_robustness_v2/screen.py (on main); tax_engine.py reads "
        "COST_PER_SIDE/BAND/CAP/calendar_years/max_dd from it",
    ),
}

REFERENCES = {
    "r2_stage_b_v1 payloads": {
        "tag": R2_TAG,
        "paths": [
            f"{R2_TAG}:research/r2_stage_b_v1/stage_b/runs/{name}.json.gz"
            for name in ("R2_1.0", "R2_2.0", "E2_1.0", "E2_2.0")
        ],
        "used_for": "the reproduction targets embedded as literals in evaluate_k_stage_b.py "
        "(ending NAV, fill count, fees, decision and fill hashes)",
    },
    "gain_band_v3 results": {
        "tag": GB3_TAG,
        "paths": [f"{GB3_TAG}:research/gain_band_v3/{{RESULTS.json,SUMMARY.txt}}"],
        "used_for": "the research expectation the after-tax block is compared against "
        "(2017 window: A1u +0.815 pp, H2 +0.271 pp after tax vs R2)",
    },
    "r2_stage_b_v1 doc": {
        "paths": ["docs/research/r2_stage_b_v1.md", "docs/research/gain_band_integration_v1.md",
                  "docs/research/gain_band_v3.md", "docs/research/band_h2_combo_v1.md (main)"],
        "used_for": "method, reference values and the K composition rule",
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    files: dict[str, dict] = {}
    for group, table in (("inputs", INPUTS), ("code", CODE)):
        for relative, (tag, note) in table.items():
            path = HERE / relative
            target = path.resolve() if path.is_symlink() else path
            files[relative] = {
                "group": group,
                "bytes": target.stat().st_size,
                "sha256": sha256(target),
                "restored_from": tag,
                "note": note,
                "symlink": path.is_symlink(),
            }
    outputs = {}
    for relative in ("STAGE_B_RESULTS.json", "SUMMARY.txt"):
        path = HERE / relative
        outputs[relative] = {"sha256": sha256(path), "bytes": path.stat().st_size}
    manifest = {
        "campaign": "k_stage_b_v1",
        "branch": "research/k_stage_b_v1",
        "topic": "Stage B replay of K = A1u gain-scaled downsize band + H2 December deferral on the "
        "production engine (equity_leveraged_trend_v1 1.2.0)",
        "window": "2017-01-03 .. 2026-07-30 (the substitute bars' own range)",
        "network": "none: every input is committed in this repository or restored from an archive tag "
        "(the four git archive commands in docs/research/k_stage_b_v1.md)",
        "files": dict(sorted(files.items())),
        "references": REFERENCES,
        "outputs": outputs,
    }
    out = HERE / "DATA_MANIFEST.json"
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print("wrote", out)


if __name__ == "__main__":
    main()
