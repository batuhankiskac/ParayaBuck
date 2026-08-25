#!/usr/bin/env python3
"""Fetch full-universe corporate actions via Alpaca v1/corporate-actions.

Fetches all 8+ types for 2016-2026, paginates completely, creates v4 CSV + manifest,
and audits dividend parity.
"""
import csv
import hashlib
import json
import pathlib
import sys
import time
from datetime import UTC, datetime

REPO = pathlib.Path(__file__).resolve().parents[1]
OUT_CSV = REPO / "data/historical/equity_survivorship_full_corporate_actions_v4.csv"
OUT_MANIFEST = REPO / "data/historical/equity_survivorship_full_corporate_actions_v4.manifest.json"
AUDIT_JSON = REPO / "data/research/full_survivorship_2026_08/corporate_actions_audit_v4.json"
OLD_DIV = REPO / "data/historical/equity_1200_dividends.csv"
LIFETIMES_V4 = REPO / "data/historical/equity_entity_lifetimes_full_v4.csv"
LIFETIMES_V3 = REPO / "data/historical/equity_entity_lifetimes_full_v3.csv"

# Load env
env = {}
for line in (REPO/".env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k,v=line.split("=",1)
        env[k.strip()]=v.strip()
KEY=env.get("ALPACA_API_KEY","")
SECRET=env.get("ALPACA_SECRET_KEY","")
if not KEY:
    print("No ALPACA_API_KEY", file=sys.stderr)
    sys.exit(1)

import requests

HEADERS={"APCA-API-KEY-ID":KEY,"APCA-API-SECRET-KEY":SECRET}
BASE="https://data.alpaca.markets/v1/corporate-actions"

# Determine universe symbols for filtering
# Use v4 lifetimes if exists else v3
lt_path = LIFETIMES_V4 if LIFETIMES_V4.exists() else LIFETIMES_V3
universe_symbols = set()
if lt_path.exists():
    for row in csv.DictReader(open(lt_path)):
        universe_symbols.add(row["canonical_symbol"])
else:
    # fallback to bar CSV symbols
    for row in csv.DictReader(open(REPO/"data/historical/equity_survivorship_aware_full_2016_2026_v4.csv")):
        universe_symbols.add(row["symbol"])
print(f"Universe symbols {len(universe_symbols)}")

# Also old 1194
old_symbols = set()
if OLD_DIV.exists():
    # Old div CSV symbols are dividend payers, not full universe; we need old universe from bars
    old_bars = REPO/"data/historical/equity_1200_2016_2026.csv"
    if old_bars.exists():
        for row in csv.DictReader(open(old_bars)):
            old_symbols.add(row["symbol"])
print(f"Old universe {len(old_symbols)}")

START="2016-01-01"
END="2026-08-01"

# Fetch with pagination
# Docs: use start/end, limit, page_token
all_actions = []
# Market dataset uses timestamp UTC, CA uses ex_date/process_date as date strings
# We'll paginate by iterating with page_token
params = {"start": START, "end": END, "limit": 1000}
next_token = None
page = 0
retries = 0
while True:
    if next_token:
        params["page_token"] = next_token
    else:
        params.pop("page_token", None)
    try:
        r = requests.get(BASE, headers=HEADERS, params=params, timeout=30)
    except Exception as e:
        print(f"Request failed {e}, retry")
        time.sleep(2)
        retries+=1
        if retries>5:
            break
        continue
    if r.status_code != 200:
        print(f"HTTP {r.status_code} {r.text[:500]}")
        if r.status_code in (429,500,502,503,504) and retries<5:
            time.sleep(2**retries)
            retries+=1
            continue
        break
    data = r.json()
    # data has structure {"corporate_actions": {...}, "next_page_token": "..."}
    ca = data.get("corporate_actions", data)  # handle both
    # Log keys
    if page==0:
        print(f"Keys: {list(ca.keys())}")
        if "next_page_token" in data:
            print(f"next_page_token present: {data.get('next_page_token')[:20] if data.get('next_page_token') else None}")
    # Collect all actions across types
    for action_type, items in ca.items():
        # items is list
        if isinstance(items, list):
            for item in items:
                # item has symbol, ex_date etc, we will add action_type field
                item["_action_type"] = action_type
                all_actions.append(item)
        elif isinstance(items, dict):
            # sometimes nested?
            pass
    next_token = data.get("next_page_token")
    page+=1
    print(f"Page {page} collected {len(all_actions)} total")
    if not next_token:
        break
    time.sleep(0.1)  # rate limit
    if page>200:
        print("Too many pages, breaking")
        break

print(f"Fetched {len(all_actions)} total actions across types")
# Deduplicate by id
by_id = {}
for a in all_actions:
    aid = a.get("id") or f"{a.get('symbol')}_{a.get('ex_date')}_{a.get('_action_type')}_{a.get('rate')}"
    if aid not in by_id:
        by_id[aid]=a
print(f"Dedup {len(by_id)} unique")
# Filter to universe if needed? But we should keep all, then audit
# For audit we need to know how many are in universe vs not
universe_actions = [a for a in by_id.values() if a.get("symbol") in universe_symbols]
old_universe_actions = [a for a in by_id.values() if a.get("symbol") in old_symbols]
inactive_symbols = universe_symbols - old_symbols
inactive_actions = [a for a in by_id.values() if a.get("symbol") in inactive_symbols]
print(f"Universe actions {len(universe_actions)}, old {len(old_universe_actions)}, inactive {len(inactive_actions)}")

# Create CSV for full universe actions (filtered to universe)
# Need to map action_type to normalized lower type
# Keep fields: timestamp (ex_date), symbol, action_type, value, raw
# For dividends: rate; for mergers: cash_amount, etc; for splits: ratio
# We'll store value as rate or cash or ratio depending
OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
fieldnames = ["timestamp","symbol","action_type","value","raw_id","ex_date","process_date"]
with open(OUT_CSV, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    for a in sorted(universe_actions, key=lambda x: (x.get("ex_date",""), x.get("symbol",""))):
        at = a.get("_action_type","")
        # Normalize: strip trailing s, lower
        val = a.get("rate") or a.get("cash_amount") or a.get("ratio") or a.get("new_rate") or a.get("old_rate") or ""
        # For stock splits, ratio may be like "2/1"
        # Use ex_date as timestamp
        ex = a.get("ex_date") or a.get("process_date") or a.get("announcement_date") or ""
        # Convert to timestamp ISO with UTC 00:00
        try:
            ts = datetime.fromisoformat(ex).replace(tzinfo=UTC).isoformat() if ex else ""
        except:
            ts = ex
        w.writerow({
            "timestamp": ts,
            "symbol": a.get("symbol",""),
            "action_type": at,
            "value": val,
            "raw_id": a.get("id",""),
            "ex_date": a.get("ex_date",""),
            "process_date": a.get("process_date",""),
        })

print(f"Wrote {OUT_CSV} rows {len(universe_actions)}")

# Audit
# Count by action_type
from collections import Counter

cnt_all = Counter(a.get("_action_type") for a in by_id.values())
cnt_uni = Counter(a.get("_action_type") for a in universe_actions)
cnt_old = Counter(a.get("_action_type") for a in old_universe_actions)
cnt_inact = Counter(a.get("_action_type") for a in inactive_actions)
# Dividend-specific
div_types = [k for k in cnt_all if "dividend" in k.lower()]
print(f"Dividend types {div_types}")
# Symbols with dividends
syms_with_div = set(a["symbol"] for a in universe_actions if "dividend" in a.get("_action_type","").lower())
syms_old_div = set(a["symbol"] for a in old_universe_actions if "dividend" in a.get("_action_type","").lower())
syms_inact_div = set(a["symbol"] for a in inactive_actions if "dividend" in a.get("_action_type","").lower())
total_cash = sum(float(a.get("rate") or 0) for a in universe_actions if "dividend" in a.get("_action_type","").lower() and a.get("rate"))
total_old_cash = sum(float(a.get("rate") or 0) for a in old_universe_actions if "dividend" in a.get("_action_type","").lower() and a.get("rate"))
total_inact_cash = sum(float(a.get("rate") or 0) for a in inactive_actions if "dividend" in a.get("_action_type","").lower() and a.get("rate"))

audit = {
    "fetched_at": datetime.now(UTC).isoformat(),
    "params": {"start": START, "end": END},
    "total_unique_actions": len(by_id),
    "total_universe_actions": len(universe_actions),
    "counts_by_type_all": dict(cnt_all),
    "counts_by_type_universe": dict(cnt_uni),
    "counts_by_type_old_1194": dict(cnt_old),
    "counts_by_type_inactive": dict(cnt_inact),
    "symbols_with_dividends_universe": len(syms_with_div),
    "symbols_with_dividends_old": len(syms_old_div),
    "symbols_with_dividends_inactive": len(syms_inact_div),
    "total_cash_dividend_rate_sum_universe": total_cash,
    "total_cash_dividend_rate_sum_old": total_old_cash,
    "total_cash_dividend_rate_sum_inactive": total_inact_cash,
    "old_dividend_csv_count": sum(1 for _ in open(OLD_DIV)) -1 if OLD_DIV.exists() else 0,
    "universe_size": len(universe_symbols),
    "old_universe_size": len(old_symbols),
    "inactive_size": len(inactive_symbols),
    "coverage_note": "Alpaca v1/corporate-actions coverage for historical CA; if zero for some types, state as incomplete",
    "price_only_note": "Signal bars remain split-adjusted PRICE only; corporate-actions used for portfolio PnL dividend cash only, not for total-return signal",
}

# Check completeness: if no mergers etc, note
expected_types = ["cash_dividends","stock_dividends","splits","name_changes","mergers","spin_offs","redemptions","worthless_removals"]
missing = [t for t in expected_types if not any(t.replace("_","").lower() in k.replace("_","").lower() for k in cnt_all.keys())]
audit["expected_types"] = expected_types
audit["missing_types_in_response"] = missing
audit["raw_keys"] = list(cnt_all.keys())

# Check if we got 8 types: at least we have cash_dividends, name_changes etc
# The actual keys from earlier: we saw cash_dividends, but need to check all
print(json.dumps(audit, indent=2))

AUDIT_JSON.write_text(json.dumps(audit, indent=2))
print(f"Wrote audit {AUDIT_JSON}")

# Manifest
sha = hashlib.sha256(OUT_CSV.read_bytes()).hexdigest()
manifest = {
    "dataset": str(OUT_CSV),
    "sha256": sha,
    "byte_size": OUT_CSV.stat().st_size,
    "row_count": len(universe_actions),
    "symbol_count": len(set(a["symbol"] for a in universe_actions)),
    "action_types": list(cnt_uni.keys()),
    "fetched_at": datetime.now(UTC).isoformat(),
    "params": {"start": START, "end": END, "feed": "alpaca_v1_corporate_actions"},
    "universe_symbols": len(universe_symbols),
    "note": "Full-universe corporate actions v4: cash_dividend etc for portfolio PnL parity; signal remains price-only",
}
OUT_MANIFEST.write_text(json.dumps(manifest, indent=2))
print(f"Manifest {OUT_MANIFEST} sha {sha[:12]}")
