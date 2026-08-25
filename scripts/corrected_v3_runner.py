#!/usr/bin/env python3
"""Corrected V3 runner (G_FULL freeze, no results yet).
Uses:
- v4 entity-safe dataset (285M, 3554 symbols, 3554 entities)
- TerminalAwareResearchEngine with path-dependent T0/T1/T2/T3
- Corporate actions v4 for portfolio dividend cash (signal remains price-only)
- Actual risk geometry (engine._positions holds live holdings, stale zombies removed)
"""
from __future__ import annotations
import csv, json, hashlib, pathlib
from datetime import datetime, UTC
from collections import defaultdict

REPO = pathlib.Path(__file__).resolve().parents[1]
DATASET_V4 = REPO / "data/historical/equity_survivorship_aware_full_2016_2026_v4.csv"
DATASET_V2 = REPO / "data/historical/equity_survivorship_aware_2016_2026_v2.csv"
DATASET_OLD = REPO / "data/historical/equity_1200_2016_2026.csv"
LIFETIMES_V4 = REPO / "data/historical/equity_entity_lifetimes_full_v4.csv"
CA_V4 = REPO / "data/historical/equity_survivorship_full_corporate_actions_v4.csv"
MANIFEST_V4 = REPO / "data/historical/equity_survivorship_aware_full_2016_2026_v4.manifest.json"

# Runner freeze identity
RUNNER_IDENTITY = {
    "runner_version": "corrected_v3_v4_1.0",
    "engine": "TerminalAwareResearchEngine (path-dependent)",
    "datasets": {
        "FULL_v4": str(DATASET_V4),
        "DEMO_v2": str(DATASET_V2),
        "OLD": str(DATASET_OLD),
    },
    "corporate_actions": str(CA_V4),
    "lifetimes": str(LIFETIMES_V4),
    "policies": ["T0_STALE", "T1_LAST_CLOSE", "T2_HAIRCUT_50", "T3_ZERO"],
    "runs_predeclared": ["OLD_T0", "DEMO_T0", "FULL_T0", "FULL_T1", "FULL_T2", "FULL_T3"],
    "invariants": [
        "Remove terminal-held position at last tradable close + fee (T1/T2/T3), delete from _positions/_last_price/_quantities",
        "No stale last_price reintroduction; exposure/holdings from live _positions only",
        "Terminal event fires only after last_bar, no lookahead",
        "Signal price-only (split-adjusted bars), CA dividends affect portfolio cash only",
        "Entity-safe: engine_symbol == display_symbol, no (symbol,timestamp) stitching",
    ],
    "frozen": True,
    "no_results_yet": True,
}

def hash_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1<<20), b""):
            h.update(chunk)
    return h.hexdigest()

def build_terminal_map():
    """Build symbol->TerminalInfo from lifetimes v4 + CA worthless_removals."""
    # Load CA worthless for verified
    worthless = set()
    if CA_V4.exists():
        for row in csv.DictReader(open(CA_V4)):
            if row["action_type"] == "worthless_removals":
                worthless.add(row["symbol"])
    tmap = {}
    for row in csv.DictReader(open(LIFETIMES_V4)):
        sym = row["canonical_symbol"]
        last_bar = datetime.fromisoformat(row["last_observed_bar"].replace("Z","+00:00")) if row["last_observed_bar"] else None
        # classify: if worthless then VERIFIED else POSSIBLE if ends before dataset end
        dataset_end = datetime.fromisoformat("2026-07-31T04:00:00+00:00")
        if last_bar and last_bar < dataset_end and row["current_status"] != "active":
            ev = "VERIFIED_DELISTING" if sym in worthless else "POSSIBLE_DELISTING"
            # will be filled properly in engine via evidence
            tmap[sym] = {
                "entity_id": row["entity_id"],
                "symbol": sym,
                "last_bar": last_bar,
                "terminal_event_class": ev,
                "evidence_class": row["pit_quality_tier"],
            }
    return tmap

if __name__ == "__main__":
    print(json.dumps(RUNNER_IDENTITY, indent=2))
    # Verify manifests exist
    for p in [DATASET_V4, DATASET_V2, LIFETIMES_V4, CA_V4]:
        print(f"{p.name}: exists={p.exists()} size={p.stat().st_size if p.exists() else 0}")
    # Quick sanity: no duplicate symbols in v4 lifetimes
    from collections import Counter
    rows = list(csv.DictReader(open(LIFETIMES_V4)))
    cnt = Counter(r["canonical_symbol"] for r in rows)
    dups = [k for k,v in cnt.items() if v>1]
    print(f"v4 duplicate symbols: {len(dups)} (expected 0)")
    print(f"terminal_map size: {len(build_terminal_map())}")
    print("Runner frozen (no results).")
