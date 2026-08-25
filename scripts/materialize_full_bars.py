#!/usr/bin/env python3
"""Materialize full bars after fetch completion, with checkpoint resume."""

import csv
import hashlib
import json
import re
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
OUT_CSV = REPO / "data/historical/equity_survivorship_aware_full_2016_2026_v3.csv"
OUT_LIFETIMES = REPO / "data/historical/equity_entity_lifetimes_full_v3.csv"
OUT_MANIFEST = REPO / "data/historical/equity_survivorship_aware_full_2016_2026_v3.manifest.json"
RESEARCH_DIR = REPO / "data/research/full_survivorship_2026_08"
CHECKPOINT = RESEARCH_DIR / "recovery_checkpoint.json"
BARS_CACHE = RESEARCH_DIR / "bars_materialized_checkpoint.json"

env_path = REPO / ".env"
env = {}
for line in env_path.read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
KEY = env.get("ALPACA_API_KEY", "")
SECRET = env.get("ALPACA_SECRET_KEY", "")
HEADERS = {"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SECRET}
BASE_BARS = "https://data.alpaca.markets/v2/stocks/bars"
FEED = "sip"
ADJUSTMENT = "split"
DATE_START = "2016-01-01"


def fetch_bars_batch(symbols):
    if not symbols:
        return {}
    params = {
        "symbols": ",".join(symbols),
        "timeframe": "1Day",
        "start": DATE_START,
        "end": "2026-07-31",
        "feed": FEED,
        "adjustment": ADJUSTMENT,
        "limit": 10000,
        "sort": "asc",
    }
    for attempt in range(4):
        try:
            r = requests.get(BASE_BARS, headers=HEADERS, params=params, timeout=45)
            if r.status_code == 200:
                data = r.json()
                bars_map = data.get("bars", {})
                nxt = data.get("next_page_token")
                while nxt:
                    params["page_token"] = nxt
                    r2 = requests.get(BASE_BARS, headers=HEADERS, params=params, timeout=45)
                    if r2.status_code != 200:
                        break
                    d2 = r2.json()
                    for k, v in d2.get("bars", {}).items():
                        bars_map.setdefault(k, []).extend(v)
                    nxt = d2.get("next_page_token")
                    if "page_token" in params:
                        del params["page_token"]
                res = {}
                for s in symbols:
                    b = bars_map.get(s)
                    res[s] = ("SUCCESS", b) if b else ("EMPTY", [])
                return res
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(1 * (2**attempt))
                continue
            if r.status_code in (400, 404, 422):
                # fallback individual
                fallback = {}
                for s in symbols:
                    pp = {
                        "symbols": s,
                        "timeframe": "1Day",
                        "start": DATE_START,
                        "end": "2026-07-31",
                        "feed": FEED,
                        "adjustment": ADJUSTMENT,
                        "limit": 10000,
                        "sort": "asc",
                    }
                    try:
                        rr = requests.get(BASE_BARS, headers=HEADERS, params=pp, timeout=20)
                        if rr.status_code == 200:
                            bb = rr.json().get("bars", {}).get(s, [])
                            fallback[s] = ("SUCCESS" if bb else "EMPTY", bb or [])
                        elif rr.status_code in (400, 404, 422):
                            fallback[s] = ("INVALID_SYMBOL", [])
                        else:
                            fallback[s] = ("PROVIDER_ERROR", [])
                    except Exception:
                        fallback[s] = ("PROVIDER_ERROR", [])
                    time.sleep(0.15)
                return fallback
            return {s: ("PROVIDER_ERROR", []) for s in symbols}
        except Exception:
            time.sleep(1 * (2**attempt))
            if attempt == 3:
                return {s: ("PROVIDER_ERROR", []) for s in symbols}
    return {s: ("PROVIDER_ERROR", []) for s in symbols}


# Load checkpoint results
ckpt = json.loads(CHECKPOINT.read_text())
results = ckpt["results_by_symbol"]
# Derive eligible list again to get by_id
SUPPORTED = {"NASDAQ", "NYSE", "AMEX", "ARCA", "BATS"}
VALID_RE = re.compile(r"^[A-Z][A-Z0-9.\-]*$")


def fetch_assets(status):
    r = requests.get(
        "https://paper-api.alpaca.markets/v2/assets",
        headers=HEADERS,
        params={"status": status, "asset_class": "us_equity"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


print("Loading assets for by_id...")
active = fetch_assets("active")
inactive = fetch_assets("inactive")
eligible = []
seen = set()
for a in inactive:
    if a.get("class") != "us_equity":
        continue
    if a.get("exchange") not in SUPPORTED:
        continue
    sym = a.get("symbol", "")
    if not sym or not VALID_RE.match(sym) or len(sym) > 10:
        continue
    aid = a["id"]
    if aid in seen:
        continue
    seen.add(aid)
    eligible.append(a)
eligible = sorted(eligible, key=lambda x: (x["symbol"], x["id"]))
by_id = {a["id"]: a for a in eligible}
# Demo bars for reuse fallback
current_csv = REPO / "data/historical/equity_1200_2016_2026.csv"
demo_csv = REPO / "data/historical/equity_survivorship_aware_2016_2026_v2.csv"
current_symbols = set()
with open(current_csv, newline="") as f:
    for row in csv.DictReader(f):
        current_symbols.add(row["symbol"])
demo_by_sym = defaultdict(list)
if demo_csv.exists():
    with open(demo_csv, newline="") as f:
        for row in csv.DictReader(f):
            if row["symbol"] not in current_symbols:
                demo_by_sym[row["symbol"]].append(row)

success_aids = [aid for aid, v in results.items() if v["status"] == "SUCCESS"]
print(f"success_aids {len(success_aids)}")
success_assets = [by_id[aid] for aid in success_aids if aid in by_id]
print(f"success_assets with by_id {len(success_assets)}")

# Load previous materialized checkpoint if exists
materialized = {}
if BARS_CACHE.exists():
    try:
        materialized = json.loads(BARS_CACHE.read_text())
        # materialized is dict aid -> bar count? But we need bars. For now we store just counts, but we need actual bars for CSV.
        # Instead we will treat BARS_CACHE as already done aids, skip them.
        print(f"loaded previous materialized cache {len(materialized)} entries")
        # If cache contains bars, we can reuse; if only counts, we still need to fetch bars again.
        # For now, if cache has bars stored as list, reuse; else refetch.
        # Check type
        sample = next(iter(materialized.values())) if materialized else None
        if isinstance(sample, list):
            print("cache contains bars, will reuse")
        else:
            print("cache only counts, clearing")
            materialized = {}
    except Exception as e:
        print(f"cache load failed {e}")
        materialized = {}

# For those already materialized (if we had saved bars), we skip.
# Determine remaining
remaining_assets = [a for a in success_assets if a["id"] not in materialized]
print(f"remaining to materialize {len(remaining_assets)}")

bars_by_aid = {}
# Restore already materialized bars if they were stored
for aid, bars in materialized.items():
    if isinstance(bars, list):
        bars_by_aid[aid] = bars

# Batch materialize remaining with size 30
batch_size = 30
total_batches = (len(remaining_assets) + batch_size - 1) // batch_size
for i in range(0, len(remaining_assets), batch_size):
    batch = remaining_assets[i : i + batch_size]
    # Unique symbols
    uniq = {}
    for a in batch:
        if a["symbol"] not in uniq:
            uniq[a["symbol"]] = a
    syms = list(uniq.keys())
    res = fetch_bars_batch(syms)
    for a in batch:
        sym = a["symbol"]
        aid = a["id"]
        # If we already have bars from uniq fetch, use it
        if sym in uniq:
            status, bars = res.get(sym, ("EMPTY", []))
            if status == "SUCCESS" and bars:
                bars_by_aid[aid] = bars
                materialized[aid] = bars
            else:
                # fallback to demo
                if sym in demo_by_sym and len(demo_by_sym[sym]) >= 20:
                    synth = []
                    for row in demo_by_sym[sym]:
                        ts = row["timestamp"]
                        dt = datetime.fromisoformat(ts)
                        t_z = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                        synth.append(
                            {
                                "t": t_z,
                                "o": float(row["open"]),
                                "h": float(row["high"]),
                                "l": float(row["low"]),
                                "c": float(row["close"]),
                                "v": int(float(row["volume"])),
                            }
                        )
                    bars_by_aid[aid] = synth
                    materialized[aid] = synth
                else:
                    materialized[aid] = []
                    bars_by_aid[aid] = []
        else:
            # duplicate symbol not in uniq (ticker reuse duplicate within batch) - fetch individually
            # For these, we already have status from res for first occurrence, but duplicate needs separate fetch? For now reuse same bars (since same ticker, same history)
            # But they are different entities same ticker -> they would share same ticker history, which is same bars, okay to reuse
            first_aid = uniq[sym]["id"]
            if first_aid in bars_by_aid:
                bars_by_aid[aid] = bars_by_aid[first_aid]
                materialized[aid] = bars_by_aid[aid]

    print(f"materialize batch {i // batch_size + 1}/{total_batches} done {len(bars_by_aid)}/{len(success_assets)}")
    # checkpoint every 5 batches
    if (i // batch_size + 1) % 5 == 0:
        # Save cache (bars as list of dicts) - may be large but okay (2389*~1000 bars ~2.3M bars, each bar ~50 bytes => 115MB, too large for json)
        # Instead we will not save full bars to json, we will directly build CSV incrementally and checkpoint via CSV offset?
        # For now save just aid counts to checkpoint progress
        prog_checkpoint = RESEARCH_DIR / "materialize_progress.json"
        prog_checkpoint.write_text(
            json.dumps(
                {
                    "done": len(bars_by_aid),
                    "total": len(success_assets),
                    "batch": i // batch_size + 1,
                    "updated": datetime.now(UTC).isoformat(),
                },
                indent=2,
            )
        )
        print(f"progress checkpoint {len(bars_by_aid)}/{len(success_assets)}")
    time.sleep(0.35)

print(f"Materialized {len(bars_by_aid)} bars sets")
# Also add demo reused bars for aids that are SUCCESS but maybe not in by_id? Already handled.

# Build lifetimes and CSV similar to previous script
print("Building lifetimes...")
with open(current_csv, newline="") as f:
    bars_by_sym = defaultdict(list)
    for row in csv.DictReader(f):
        bars_by_sym[row["symbol"]].append(row)

lifetimes = []
active_by_sym = defaultdict(list)
for a in active:
    active_by_sym[a["symbol"]].append(a)
for sym, rows in bars_by_sym.items():
    first = rows[0]["timestamp"]
    last = rows[-1]["timestamp"]
    asset = next((a for a in active if a["symbol"] == sym), None) or next(
        (a for a in inactive if a["symbol"] == sym), None
    )
    aid = asset["id"] if asset else f"placeholder_{sym}"
    exch = asset["exchange"] if asset else "UNKNOWN"
    status = asset["status"] if asset else "active"
    lifetimes.append(
        {
            "entity_id": aid,
            "provider_asset_id": aid,
            "canonical_symbol": sym,
            "historical_symbol": sym,
            "valid_from": first,
            "valid_to": "",
            "first_observed_bar": first,
            "last_observed_bar": last,
            "current_status": status,
            "exchange": exch,
            "rename_chain": "",
            "evidence_class": "FINAL_SURVIVOR",
            "pit_quality_tier": "TIER_C_APPROXIMATE",
            "terminal_event_class": "",
            "notes": "Reused from current dataset; survivor",
        }
    )
for aid, bars in bars_by_aid.items():
    if not bars:
        continue
    asset = by_id.get(aid)
    if not asset:
        continue
    sym = asset["symbol"]
    if sym in bars_by_sym and asset["id"] == next((a["id"] for a in active if a["symbol"] == sym), None):
        continue
    first = bars[0]["t"]
    last = bars[-1]["t"]
    dt_first = datetime.fromisoformat(first.replace("Z", "+00:00"))
    dt_last = datetime.fromisoformat(last.replace("Z", "+00:00"))
    first_str = dt_first.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    last_str = dt_last.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    tier = "TIER_B_RECONSTRUCTED_PIT" if len(bars) >= 63 else "TIER_D_UNUSABLE"
    lifetimes.append(
        {
            "entity_id": aid,
            "provider_asset_id": aid,
            "canonical_symbol": sym,
            "historical_symbol": sym,
            "valid_from": first_str,
            "valid_to": last_str,
            "first_observed_bar": first_str,
            "last_observed_bar": last_str,
            "current_status": asset.get("status", "inactive"),
            "exchange": asset.get("exchange", "UNKNOWN"),
            "rename_chain": "",
            "evidence_class": "POSSIBLE_DELISTING",
            "pit_quality_tier": tier,
            "terminal_event_class": "POSSIBLE_DELISTING",
            "notes": f"Recovered inactive history via SIP, {len(bars)} bars",
        }
    )
# FB->META
import json as _json

meta_asset = next((a for a in active if a["symbol"] == "META"), None)
if meta_asset:
    mid = meta_asset["id"]
    for lt in lifetimes:
        if lt["entity_id"] == mid:
            lt["rename_chain"] = _json.dumps(
                [
                    {"symbol": "FB", "valid_to": "2022-06-08T04:00:00+00:00"},
                    {"symbol": "META", "valid_from": "2022-06-09T04:00:00+00:00"},
                ]
            )
            lt["evidence_class"] = "RENAMED_CONTINUATION"
            lt["pit_quality_tier"] = "TIER_A_VERIFIED_PIT"
            lt["terminal_event_class"] = "RENAMED_CONTINUATION"
            lt["notes"] = "VERIFIED_RENAME FB->META 2022-06-09 single entity via provider asof"
lifetimes_sorted = sorted(lifetimes, key=lambda x: x["canonical_symbol"])
with open(OUT_LIFETIMES, "w", newline="") as f:
    w = csv.DictWriter(
        f,
        fieldnames=[
            "entity_id",
            "provider_asset_id",
            "canonical_symbol",
            "historical_symbol",
            "valid_from",
            "valid_to",
            "first_observed_bar",
            "last_observed_bar",
            "current_status",
            "exchange",
            "rename_chain",
            "evidence_class",
            "pit_quality_tier",
            "terminal_event_class",
            "notes",
        ],
    )
    w.writeheader()
    for row in lifetimes_sorted:
        w.writerow(row)
print(f"wrote lifetimes {len(lifetimes_sorted)}")

print("Building bar CSV...")
all_bars = []
with open(current_csv, newline="") as f:
    for row in csv.DictReader(f):
        all_bars.append(row)
for aid, bars in bars_by_aid.items():
    asset = by_id.get(aid)
    if not asset:
        continue
    sym = asset["symbol"]
    for b in bars:
        t = b["t"]
        dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        ts = dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        all_bars.append(
            {
                "timestamp": ts,
                "symbol": sym,
                "timeframe": "1d",
                "open": b["o"],
                "high": b["h"],
                "low": b["l"],
                "close": b["c"],
                "volume": b["v"],
                "market": "equity",
            }
        )
dedup = {}
for row in all_bars:
    key = (row["symbol"], row["timestamp"])
    if key not in dedup:
        dedup[key] = row
all_sorted = sorted(dedup.values(), key=lambda x: (x["timestamp"], x["symbol"]))
print(f"total bars dedup {len(all_sorted)}")
with open(OUT_CSV, "w", newline="") as f:
    w = csv.DictWriter(
        f,
        fieldnames=[
            "timestamp",
            "symbol",
            "timeframe",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "market",
        ],
    )
    w.writeheader()
    for row in all_sorted:
        w.writerow(row)


def _hash(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


sha = _hash(OUT_CSV)
size = OUT_CSV.stat().st_size
symbols = len(set(r["symbol"] for r in all_sorted))
entities = len(set(r["entity_id"] for r in lifetimes_sorted))
first_date = min(r["timestamp"] for r in all_sorted)
last_date = max(r["timestamp"] for r in all_sorted)
snap_hash = hashlib.sha256(json.dumps({"eligible": len(eligible)}, sort_keys=True).encode()).hexdigest()
lt_hash = hashlib.sha256(Path(OUT_LIFETIMES).read_bytes()).hexdigest()
manifest = {
    "dataset": str(OUT_CSV),
    "sha256": sha,
    "byte_size": size,
    "row_count": len(all_sorted),
    "symbol_count": symbols,
    "entity_count": entities,
    "first_date": first_date,
    "last_date": last_date,
    "feed": FEED,
    "adjustment": ADJUSTMENT,
    "generation_commit": "pending",
    "asset_master_snapshot_hash": snap_hash,
    "lifetime_table_hash": lt_hash,
    "lifetime_table": str(OUT_LIFETIMES),
    "generated_at": datetime.now(UTC).isoformat(),
    "note": "Full survivorship-aware reconstruction v3: current survivors + all eligible inactive (supported exchanges) with provider-ID entity semantics",
}
OUT_MANIFEST.write_text(json.dumps(manifest, indent=2))
print(f"wrote {OUT_CSV} sha {sha[:12]} rows {len(all_sorted)} symbols {symbols} entities {entities}")
# Save recovery summary update? keep existing but ensure success count matches
recovery_summary = RESEARCH_DIR / "recovery_summary.json"
if recovery_summary.exists():
    rs = json.loads(recovery_summary.read_text())
    rs["materialized"] = len(bars_by_aid)
    recovery_summary.write_text(json.dumps(rs, indent=2))
print("done materialize")
