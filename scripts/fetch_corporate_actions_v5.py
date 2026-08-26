#!/usr/bin/env python3
"""Fetch and filter full-universe corporate actions v5.

Covers all 9,010 symbols in equity_entity_lifetimes_full_v5.csv.
Paginates through Alpaca corporate actions API, normalizes types:
- cash_dividends
- stock_dividends
- forward_splits
- reverse_splits
- unit_splits
- stock_mergers
- cash_mergers
- spinoffs
- name_changes
Writes CSV + manifest + audit.
"""

from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import time
from datetime import UTC, datetime

import requests

REPO = pathlib.Path(__file__).resolve().parents[1]
OUT_CSV = REPO / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
OUT_MANIFEST = REPO / "data/historical/equity_survivorship_full_corporate_actions_v5.manifest.json"
AUDIT_JSON = REPO / "data/research/full_survivorship_2026_08/corporate_actions_audit_v5.json"
LIFETIMES_V5 = REPO / "data/historical/equity_entity_lifetimes_full_v5.csv"

env = {}
for line in (REPO / ".env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
KEY = env.get("ALPACA_API_KEY", "")
SECRET = env.get("ALPACA_SECRET_KEY", "")
HEADERS = {"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SECRET}
BASE = "https://data.alpaca.markets/v1/corporate-actions"

universe_symbols = set()
with open(LIFETIMES_V5, newline="") as f:
    for row in csv.DictReader(f):
        universe_symbols.add(row["canonical_symbol"])
print(f"Universe v5 symbols: {len(universe_symbols)}")

START = "2016-01-01"
END = "2026-08-01"

all_raw_actions: dict[str, list[dict]] = {
    "cash_dividends": [],
    "stock_dividends": [],
    "forward_splits": [],
    "reverse_splits": [],
    "unit_splits": [],
    "stock_mergers": [],
    "cash_mergers": [],
    "spinoffs": [],
    "name_changes": [],
}

params = {"start": START, "end": END, "limit": 1000}
next_token = None
page = 0
retries = 0

print("Fetching corporate actions from Alpaca API...")
while True:
    if next_token:
        params["page_token"] = next_token
    else:
        params.pop("page_token", None)
    try:
        r = requests.get(BASE, headers=HEADERS, params=params, timeout=35)
    except Exception:
        time.sleep(2)
        retries += 1
        if retries > 5:
            break
        continue
    if r.status_code != 200:
        if r.status_code in (429, 500, 502, 503, 504) and retries < 5:
            time.sleep(2**retries)
            retries += 1
            continue
        break
    data = r.json()
    ca = data.get("corporate_actions", data)
    for k in all_raw_actions:
        items = ca.get(k, []) or []
        all_raw_actions[k].extend(items)
    page += 1
    next_token = data.get("next_page_token")
    if not next_token:
        break
    if page % 10 == 0:
        print(f"  Page {page} fetched...")

print(f"Total pages: {page}")

# Normalize and filter for universe symbols
out_rows = []
div_count = 0
split_count = 0
other_count = 0

for act_type, items in all_raw_actions.items():
    for item in items:
        sym = (
            item.get("symbol") or item.get("target_symbol") or item.get("source_symbol") or item.get("original_symbol")
        )
        if not sym or sym not in universe_symbols:
            continue
        ex_date = (
            item.get("ex_date")
            or item.get("process_date")
            or item.get("effective_date")
            or item.get("payable_date")
            or ""
        )
        rate = item.get("rate") or item.get("cash") or item.get("amount") or ""
        old_rate = item.get("old_rate") or ""
        new_rate = item.get("new_rate") or ""
        source_sym = item.get("source_symbol") or item.get("original_symbol") or sym
        target_sym = item.get("target_symbol") or item.get("new_symbol") or sym

        if "dividend" in act_type:
            div_count += 1
        elif "split" in act_type:
            split_count += 1
        else:
            other_count += 1

        out_rows.append(
            {
                "action_type": act_type,
                "symbol": sym,
                "ex_date": ex_date,
                "record_date": item.get("record_date", ""),
                "payable_date": item.get("payable_date", ""),
                "rate": str(rate),
                "old_rate": str(old_rate),
                "new_rate": str(new_rate),
                "source_symbol": source_sym,
                "target_symbol": target_sym,
                "raw_id": item.get("id", ""),
            }
        )

# Sort deterministically
out_rows.sort(key=lambda r: (r["ex_date"], r["symbol"], r["action_type"]))

with open(OUT_CSV, "w", newline="") as f:
    fieldnames = [
        "action_type",
        "symbol",
        "ex_date",
        "record_date",
        "payable_date",
        "rate",
        "old_rate",
        "new_rate",
        "source_symbol",
        "target_symbol",
        "raw_id",
    ]
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(out_rows)

h = hashlib.sha256()
with open(OUT_CSV, "rb") as f:
    while chunk := f.read(1024 * 1024):
        h.update(chunk)
ca_sha = h.hexdigest()

manifest = {
    "dataset": str(OUT_CSV),
    "sha256": ca_sha,
    "byte_size": OUT_CSV.stat().st_size,
    "row_count": len(out_rows),
    "universe_symbol_count": len(universe_symbols),
    "action_breakdown": {
        "cash_dividends": sum(1 for r in out_rows if r["action_type"] == "cash_dividends"),
        "stock_dividends": sum(1 for r in out_rows if r["action_type"] == "stock_dividends"),
        "forward_splits": sum(1 for r in out_rows if r["action_type"] == "forward_splits"),
        "reverse_splits": sum(1 for r in out_rows if r["action_type"] == "reverse_splits"),
        "unit_splits": sum(1 for r in out_rows if r["action_type"] == "unit_splits"),
        "stock_mergers": sum(1 for r in out_rows if r["action_type"] == "stock_mergers"),
        "cash_mergers": sum(1 for r in out_rows if r["action_type"] == "cash_mergers"),
        "spinoffs": sum(1 for r in out_rows if r["action_type"] == "spinoffs"),
        "name_changes": sum(1 for r in out_rows if r["action_type"] == "name_changes"),
    },
    "first_ex_date": out_rows[0]["ex_date"] if out_rows else "",
    "last_ex_date": out_rows[-1]["ex_date"] if out_rows else "",
    "generated_at": datetime.now(UTC).isoformat(),
    "note": "Symmetric v5 corporate actions dataset covering all 9,010 universe symbols",
}
OUT_MANIFEST.write_text(json.dumps(manifest, indent=2))
print(f"Wrote CA v5: {len(out_rows)} actions, SHA256: {ca_sha}")

audit_data = {
    "total_actions": len(out_rows),
    "manifest_sha256": ca_sha,
    "dividend_rows": div_count,
    "split_rows": split_count,
    "other_actions": other_count,
    "unique_dividend_symbols": len(set(r["symbol"] for r in out_rows if "dividend" in r["action_type"])),
    "unique_split_symbols": len(set(r["symbol"] for r in out_rows if "split" in r["action_type"])),
}
AUDIT_JSON.write_text(json.dumps(audit_data, indent=2))
