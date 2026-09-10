#!/usr/bin/env python3
"""Build a date-known equity eligibility manifest from v5 lifetimes.

The manifest is an economic-universe input contract, not an index-membership
claim.  It keeps inactive and short-history entities and records unresolved
SEC identity mappings instead of dropping them.  It intentionally does not
use the survivor-selected fundamentals pilot for eligibility.
"""

from __future__ import annotations

import csv
from datetime import datetime, UTC
import hashlib
import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
LIFETIMES = REPO / "data/historical/equity_entity_lifetimes_full_v5.csv"
TICKERS = REPO / "data/research/external/equity_fundamentals_pilot_2026_09_07_v2/company_tickers.json"
OUTPUT = REPO / "data/research/external/equity_pit_universe_2026_09_07"
MANIFEST = REPO / "data/research_manifests/equity_pit_universe_2026_09_07.json"
EXCHANGES = {"NYSE", "NASDAQ", "ARCA", "BATS", "AMEX"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    if OUTPUT.exists():
        raise RuntimeError(f"REFUSE_EXISTING_OUTPUT:{OUTPUT}")
    OUTPUT.mkdir(parents=True)
    ticker_data = json.loads(TICKERS.read_text(encoding="utf-8"))
    by_ticker = {
        str(value["ticker"]).upper(): {
            "cik": f"{int(value['cik_str']):010d}",
            "title": str(value["title"]),
        }
        for value in ticker_data.values()
    }
    rows: list[dict[str, object]] = []
    with LIFETIMES.open(newline="", encoding="utf-8") as handle:
        for source in csv.DictReader(handle):
            if source.get("exchange") not in EXCHANGES:
                continue
            symbol = str(source.get("canonical_symbol", ""))
            historical = str(source.get("historical_symbol", ""))
            sec = by_ticker.get(symbol.upper()) or by_ticker.get(historical.upper())
            rows.append(
                {
                    "entity_id": source["entity_id"],
                    "provider_asset_id": source["provider_asset_id"],
                    "canonical_symbol": symbol,
                    "historical_symbol": historical,
                    "exchange": source["exchange"],
                    "valid_from": source.get("valid_from") or None,
                    "valid_to": source.get("valid_to") or None,
                    "first_observed_bar": source.get("first_observed_bar") or None,
                    "last_observed_bar": source.get("last_observed_bar") or None,
                    "current_status": source.get("current_status"),
                    "evidence_class": source.get("evidence_class"),
                    "pit_quality_tier": source.get("pit_quality_tier"),
                    "sec_mapping": (
                        {"status": "current_ticker_match", **sec}
                        if sec
                        else {
                            "status": "UNRESOLVED",
                            "reason": "no_current_sec_ticker_mapping; archived original CIK or issuer evidence required",
                        }
                    ),
                    "eligibility_basis": "date_known_lifetime_interval; active and inactive entities retained",
                }
            )
    rows.sort(key=lambda row: (str(row["valid_from"] or ""), str(row["canonical_symbol"]), str(row["entity_id"])))
    out = OUTPUT / "pit_eligibility_intervals.jsonl"
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    mapped = sum(row["sec_mapping"]["status"] == "current_ticker_match" for row in rows)  # type: ignore[index]
    receipt = {
        "status": "COMPLETE",
        "created_at": datetime.now(UTC).isoformat(),
        "source": str(LIFETIMES.relative_to(REPO)),
        "source_sha256": sha256(LIFETIMES),
        "sec_ticker_snapshot": str(TICKERS.relative_to(REPO)),
        "sec_ticker_snapshot_sha256": sha256(TICKERS),
        "output": str(out.relative_to(REPO)),
        "output_sha256": sha256(out),
        "row_count": len(rows),
        "current_ticker_mapped": mapped,
        "unresolved_sec_mapping": len(rows) - mapped,
        "economic_universe_warning": "This is date-known local instrument eligibility, not an index-membership archive; no survivor/completeness filter was applied.",
        "pilot_exclusion": "The 20-name SEC fundamentals pilot was not used to choose these rows.",
        "credential_fields_persisted": False,
    }
    MANIFEST.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"PIT_UNIVERSE_COMPLETE rows={len(rows)} mapped={mapped} unresolved={len(rows)-mapped} output={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
