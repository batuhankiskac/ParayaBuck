#!/usr/bin/env python3
"""e2_integration_v1 Stage B metrics: VT35 vs E2 at 1x/2x costs, and E2's differing entries/exits.

Metric definitions copied from the archived research/paper_config_v2/evaluate.py
(session-close NAV path; window 2017-01-03 -> 2026-07-30). Reads
stage_b/runs/{VT35,E2}_{1.0,2.0}.json.gz and their _audit files; writes
STAGE_B_RESULTS.json next to this file.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "stage_b" / "runs"
_spec = importlib.util.spec_from_file_location("pc2_eval", HERE / "archive/research/paper_config_v2/evaluate.py")
pc2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc2)

ARMS = ("VT35", "E2")
COSTS = ("1.0", "2.0")
S1_REFERENCE = {"ending_nav": 1173537.61, "fills": 615, "cagr": 0.2935, "max_drawdown": 0.2770}


def _load(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _in_market(audit: dict) -> list[tuple[str, bool, dict]]:
    """(decision session label, held target > 0, decide-log row) per strategy decision."""
    return [(row["label"][:10], bool(row["weights"]), row) for row in audit["decide_log"]]


def _episodes(states: list[tuple[str, bool, dict]]) -> list[tuple[str, str | None]]:
    """(entry decision date, exit decision date) for each in-market run of decisions."""
    out, start = [], None
    for day, held, _row in states:
        if held and start is None:
            start = day
        elif not held and start is not None:
            out.append((start, day))
            start = None
    if start is not None:
        out.append((start, None))
    return out


def main() -> None:
    results: dict[str, dict] = {}
    for arm in ARMS:
        for cost in COSTS:
            run = _load(RUNS / f"{arm}_{cost}.json.gz")
            summary = pc2._summary(run)
            summary["strategy"] = run["strategy"]
            summary["hashes"] = run["hashes"]
            summary["decisions"] = run["counts"]["decisions"]
            results.setdefault(arm, {})[cost] = summary
    vt35 = _load(RUNS / "VT35_1.0_audit.json.gz")
    e2 = _load(RUNS / "E2_1.0_audit.json.gz")
    a, b = _in_market(vt35), _in_market(e2)
    assert [x[0] for x in a] == [x[0] for x in b], "decision sessions differ"
    differing = [
        {
            "decision_date": day,
            "vt35_in": held_a,
            "e2_in": held_b,
            "votes": (row_b.get("trend_signals") or {}).get("votes_up"),
            "sma_up": ((row_b.get("trend_signals") or {}).get("sma") or {}).get("up"),
            "tsm_up": ((row_b.get("trend_signals") or {}).get("tsm") or {}).get("up"),
            "don_up": ((row_b.get("trend_signals") or {}).get("don") or {}).get("up"),
        }
        for (day, held_a, _), (_, held_b, row_b) in zip(a, b, strict=True)
        if held_a != held_b
    ]
    ep_a, ep_b = set(_episodes(a)), set(_episodes(b))
    first_states = {name: rows[0][2].get("trend_state") for name, rows in (("VT35", a), ("E2", b))}
    payload = {
        "window": ["2017-01-03", "2026-07-30"],
        "s1_reference_check": {
            "expected": S1_REFERENCE,
            "vt35_1x_ending_nav": results["VT35"]["1.0"]["ending_nav"],
            "vt35_1x_fills": results["VT35"]["1.0"]["fills"],
            "match": round(results["VT35"]["1.0"]["ending_nav"], 2) == S1_REFERENCE["ending_nav"]
            and results["VT35"]["1.0"]["fills"] == S1_REFERENCE["fills"],
        },
        "results": results,
        "first_decision_trend_state": first_states,
        "decisions_in_market_share": {
            "VT35": sum(held for _, held, _ in a) / len(a),
            "E2": sum(held for _, held, _ in b) / len(b),
        },
        "episodes": {"VT35": len(ep_a), "E2": len(ep_b)},
        "episodes_only_vt35": sorted(ep_a - ep_b, key=lambda item: item[0]),
        "episodes_only_e2": sorted(ep_b - ep_a, key=lambda item: item[0]),
        "differing_decisions": differing,
    }
    (HERE / "STAGE_B_RESULTS.json").write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
    print("S1 check:", payload["s1_reference_check"])
    print("first decision:", first_states)
    for arm in ARMS:
        for cost in COSTS:
            s = results[arm][cost]
            print(
                f"{arm:5s} {cost}x  NAV {s['ending_nav']:>13,.2f}  CAGR {s['full']['cagr'] * 100:6.2f}%"
                f"  DD {s['full']['max_drawdown'] * 100:5.2f}%  Sharpe {s['full']['sharpe']:.3f}"
                f"  turn/yr {s['turnover_per_year']:.2f}  fills {s['fills']}  2017-21 {s['cagr_2017_2021'] * 100:.2f}%"
                f"  2022-26 {s['cagr_2022_2026'] * 100:.2f}%  costs {sum(s['costs'].values()):,.0f}"
                f"  acct_ok {s['accounting_ok']}  {s['strategy']}"
            )
    print("in-market share:", payload["decisions_in_market_share"], "episodes:", payload["episodes"])
    print("differing decisions:", len(differing))
    for row in differing:
        print("  ", row)
    print("episodes only VT35:", payload["episodes_only_vt35"])
    print("episodes only E2:", payload["episodes_only_e2"])


if __name__ == "__main__":
    main()
