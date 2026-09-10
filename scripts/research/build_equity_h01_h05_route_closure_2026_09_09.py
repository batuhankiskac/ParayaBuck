"""Build a read-only closure receipt for H01-H05 public evidence routes."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "data/research/external/equity_h01_h05_route_closure_2026_09_09"
OUTPUT = OUT_DIR / "route_closure_receipt.json"
SYMBOLS = ("A", "AA", "AAL", "AAPL", "AAP", "ABT", "ABCB", "ABM")
YAHOO_RECEIPT = REPO / "data/research/external/crossasset_public_2026_09_08/x01_yahoo/receipt.json"
STOOQ_RECEIPT = REPO / "data/research/external/crossasset_public_2026_09_08/x01_alternatives/receipt.json"
BARS = REPO / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
ACTIONS = REPO / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
SEC_RECEIPT = REPO / "data/research/external/equity_h01_h05_sec_filings_2026_09_09/acquisition_receipt.json"
SEC_CATALOG = REPO / "data/research/external/equity_h01_h05_sec_filings_2026_09_09/filing_fact_inventory.json"
SEC_CAUSAL = REPO / "data/research/external/equity_h01_h05_sec_filings_2026_09_09/causal_fact_inventory.json"
ROWS = REPO / "data/research/equity_h01_h05_causal_input_gate_2026_09_08_r14/causal_input_rows.jsonl"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def local_ref(path: Path) -> dict[str, Any]:
    return {"path": str(path.relative_to(REPO)), "bytes": path.stat().st_size, "sha256": sha256(path)}


def market_coverage() -> dict[str, Any]:
    counts: Counter[str] = Counter()
    first: dict[str, str] = {}
    last: dict[str, str] = {}
    with BARS.open(newline="") as handle:
        for row in csv.DictReader(handle):
            symbol = row["symbol"]
            if symbol not in SYMBOLS:
                continue
            counts[symbol] += 1
            first[symbol] = min(first.get(symbol, row["timestamp"]), row["timestamp"])
            last[symbol] = max(last.get(symbol, row["timestamp"]), row["timestamp"])
    action_counts: Counter[str] = Counter()
    action_types: Counter[str] = Counter()
    with ACTIONS.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row["symbol"] in SYMBOLS:
                action_counts[row["symbol"]] += 1
                action_types[row["action_type"]] += 1
    return {
        "bars": {"artifact": local_ref(BARS), "rows_by_symbol": dict(sorted(counts.items())), "first_by_symbol": first, "last_by_symbol": last, "pit_cutoff": "2026-07-30T04:00:00+00:00"},
        "corporate_actions": {"artifact": local_ref(ACTIONS), "rows_by_symbol": dict(sorted(action_counts.items())), "types": dict(sorted(action_types.items()))},
        "identity_limit": "Provider asset identity and common-security continuity are not proven by ticker-only bars/actions; no row is admitted on these artifacts alone.",
    }


def h04_check() -> dict[str, Any]:
    rows = [json.loads(line) for line in ROWS.read_text().splitlines()]
    admitted = [row for row in rows if row["features"]["h04_asset_growth"] is not None]
    periods = sorted({row["source_lineage"]["annual_assets"]["period_key"] for row in admitted})
    return {
        "formula": "current annual Assets / prior annual Assets - 1",
        "flow_policy": "10-K reported annual Assets; no monthly carry-forward is predeclared; missing annual predecessor blocks",
        "decision_grid_rows": len(rows),
        "admitted_rows": len(admitted),
        "admitted_by_symbol": dict(sorted(Counter(row["symbol"] for row in admitted).items())),
        "annual_period_keys": periods,
        "monthly_expansion": "REJECTED_UNPREDECLARED_LOOKAHEAD_OR_RULE_CHANGE",
        "row_artifact": local_ref(ROWS),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    yahoo = json.loads(YAHOO_RECEIPT.read_text())
    stooq = json.loads(STOOQ_RECEIPT.read_text())
    receipt = {
        "schema": "equity_h01_h05_public_route_closure_v1",
        "status": "CLOSED_WITH_DATA_AND_PIT_BLOCKERS",
        "scope": {"symbols": list(SYMBOLS), "decision_grid": "2022-01-31 through 2024-12-31 month-end", "cutoff": "2024-12-31"},
        "routes": {
            "cached_yahoo": {"provider": "Yahoo Finance chart", "status": yahoo["status"], "receipt": local_ref(YAHOO_RECEIPT), "records": {s: yahoo["records"].get(s) for s in ("SHY", "IEF", "TLT")}, "admission": "BLOCKED_NO_PIT_AVAILABLE_AT_AND_NO_COMMON_SECURITY_LINEAGE"},
            "cached_stooq": {"provider": "Stooq public CSV", "status": "PROBE_ONLY_UNVERIFIABLE_BODY_NOT_RETAINED", "receipt": local_ref(STOOQ_RECEIPT), "probe_count": len(stooq["probes"]), "admission": "BLOCKED_NO_RETAINED_BODY_OR_SPLIT_DISTRIBUTION_PROVENANCE"},
            "v3_market_and_actions": {"status": "PINNED_LOCAL_EVIDENCE_INSPECTED", **market_coverage()},
            "sec_numeric_facts": {"status": "VERIFIED_EXACT_TAG_EVIDENCE", "receipt": local_ref(SEC_RECEIPT), "catalog": local_ref(SEC_CATALOG), "causal_inventory": local_ref(SEC_CAUSAL), "filings": 98, "facts": 8520, "pit_eligible_facts": 2937},
            "yahoo_retry": {"provider": "Yahoo Finance chart", "endpoint": "https://query1.finance.yahoo.com/v8/finance/chart/A", "status": "HTTP_429_NO_BODY", "attempts": 1, "admission": "BLOCKED_RETRY_REQUIRED", "source_absence_claim": False, "retrieved_at": "2026-09-09T07:47:38Z"},
        },
        "h04": h04_check(),
        "terminal_blockers": ["historical common-security continuity and split-consistent price/share basis remain unproven", "Yahoo retry returned HTTP 429 and Stooq probe bodies were not retained", "H04 annual rule has no predeclared monthly carry-forward and remains 19 admitted annual observations", "no economic, production, PAPER, LIVE, or promotion run"],
    }
    OUTPUT.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(f"H01_H05_ROUTE_CLOSURE_COMPLETE sha256={sha256(OUTPUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
