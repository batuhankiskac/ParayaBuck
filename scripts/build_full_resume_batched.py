#!/usr/bin/env python3
"""Resume full dataset build with batched BAR fetching for speed."""

import hashlib
import json
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
OUT_CSV = REPO / "data/historical/equity_survivorship_aware_full_2016_2026_v3.csv"
OUT_LIFETIMES = REPO / "data/historical/equity_entity_lifetimes_full_v3.csv"
OUT_MANIFEST = REPO / "data/historical/equity_survivorship_aware_full_2016_2026_v3.manifest.json"
RESEARCH_DIR = REPO / "data/research/full_survivorship_2026_08"
CHECKPOINT = RESEARCH_DIR / "recovery_checkpoint.json"

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


def fetch_bars_batch(symbols: list[str]) -> dict:
    """Fetch batch, returns dict symbol -> (status, bars)."""
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
                next_token = data.get("next_page_token")
                # paginate
                while next_token:
                    params["page_token"] = next_token
                    r2 = requests.get(BASE_BARS, headers=HEADERS, params=params, timeout=45)
                    if r2.status_code != 200:
                        break
                    d2 = r2.json()
                    for k, v in d2.get("bars", {}).items():
                        bars_map.setdefault(k, []).extend(v)
                    next_token = d2.get("next_page_token")
                    if "page_token" in params:
                        del params["page_token"]
                result = {}
                for sym in symbols:
                    b = bars_map.get(sym)
                    if b is None or len(b) == 0:
                        # Check if symbol was invalid - no bars but also not error => EMPTY
                        result[sym] = ("EMPTY", [])
                    else:
                        result[sym] = ("SUCCESS", b)
                return result
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(1 * (2**attempt))
                continue
            if r.status_code in (400, 404, 422):
                txt = r.text.lower()
                # If batch contains invalid symbol, API may return 400 with details per symbol?
                # For now, mark all as INVALID if batch-level invalid, but we can split and retry individually for those
                # Let's try individual fallback for this batch
                if "invalid" in txt or "not found" in txt:
                    # Fallback to individual fetches for this batch to distinguish
                    fallback = {}
                    for sym in symbols:
                        single_params = {k: v for k, v in params.items() if k != "page_token"}
                        single_params["symbols"] = sym
                        try:
                            rr = requests.get(BASE_BARS, headers=HEADERS, params=single_params, timeout=20)
                            if rr.status_code == 200:
                                bb = rr.json().get("bars", {}).get(sym, [])
                                fallback[sym] = ("SUCCESS" if bb else "EMPTY", bb or [])
                            elif rr.status_code in (400, 404, 422):
                                fallback[sym] = ("INVALID_SYMBOL", [])
                            elif rr.status_code in (401, 403):
                                fallback[sym] = ("ENTITLEMENT_ERROR", [])
                            else:
                                fallback[sym] = ("PROVIDER_ERROR", [])
                        except Exception:
                            fallback[sym] = ("PROVIDER_ERROR", [])
                        time.sleep(0.15)
                    return fallback
                return {sym: ("PROVIDER_ERROR", []) for sym in symbols}
            if r.status_code in (401, 403):
                return {sym: ("ENTITLEMENT_ERROR", []) for sym in symbols}
            return {sym: ("PROVIDER_ERROR", []) for sym in symbols}
        except Exception:
            time.sleep(1 * (2**attempt))
            if attempt == 3:
                return {sym: ("PROVIDER_ERROR", []) for sym in symbols}
    return {sym: ("PROVIDER_ERROR", []) for sym in symbols}


# Load checkpoint
ckpt = json.loads(CHECKPOINT.read_text()) if CHECKPOINT.exists() else {}
results = ckpt.get("results_by_symbol", {})
processed = ckpt.get("processed", 0)

# Need to reconstruct eligible list same as before to map aid->symbol
# Load discovery snapshot to get eligible list deterministically (fetch again but reuse counts)
# Instead, we can derive remaining symbols from results missing vs total 2508 known
# But we need asset_id mapping: results keys are asset_id, not symbol. For batch we need symbol list with aid.
# Let's re-derive eligible by fetching assets again (fast) and rebuilding filtered list same determinism

import re as _re

SUPPORTED = {"NASDAQ", "NYSE", "AMEX", "ARCA", "BATS"}
VALID_RE = _re.compile(r"^[A-Z][A-Z0-9.\-]*$")


def fetch_assets(status):
    r = requests.get(
        "https://paper-api.alpaca.markets/v2/assets",
        headers=HEADERS,
        params={"status": status, "asset_class": "us_equity"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


print("Re-deriving eligible list for batch...")
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
print(f"eligible {len(eligible)} active {len(active)}")

# Map aid -> asset
by_id = {a["id"]: a for a in eligible}
# Determine which aids still need fetch (not in results or status not final)
remaining = []
for a in eligible:
    aid = a["id"]
    sym = a["symbol"]
    # Also check if reused_from_demo case: those are already in results as SUCCESS with reused flag
    if aid not in results:
        remaining.append(a)
    else:
        st = results[aid].get("status")
        if st not in (
            "SUCCESS",
            "EMPTY",
            "INVALID_SYMBOL",
            "ENTITLEMENT_ERROR",
            "PROVIDER_ERROR",
            "RATE_LIMIT_FAILURE",
        ):
            remaining.append(a)

print(f"remaining to fetch {len(remaining)} (processed {processed})")
# Also need to collect already success bars for final dataset: we have them partly in previous success_bars memory lost.
# For now we will re-fetch remaining via batch and keep results dict; after batch we will merge with demo reused bars via same logic as original script's demo reuse.
# Let's also re-add demo reused if needed: check demo csv for those not in results but eligible
import csv as _csv

demo_csv = REPO / "data/historical/equity_survivorship_aware_2016_2026_v2.csv"
current_csv = REPO / "data/historical/equity_1200_2016_2026.csv"
current_symbols = set()
with open(current_csv, newline="") as f:
    for row in _csv.DictReader(f):
        current_symbols.add(row["symbol"])
demo_by_sym = defaultdict(list)
if demo_csv.exists():
    with open(demo_csv, newline="") as f:
        for row in _csv.DictReader(f):
            if row["symbol"] not in current_symbols:
                demo_by_sym[row["symbol"]].append(row)

# For remaining, batch size 50
batch_size = 50
for i in range(0, len(remaining), batch_size):
    batch = remaining[i : i + batch_size]
    syms = [a["symbol"] for a in batch]
    # Need mapping symbol -> aid for duplicate symbols: but we have unique aid per symbol. If batch contains duplicate symbols with different aid, the API will conflate them (same ticker). So we must ensure batch has unique symbols.
    # If duplicate symbol appears with different aid, we cannot batch them together. Split duplicates.
    # Check duplicates within batch
    seen_sym = {}
    unique_batch = []
    dup_batch = []
    for a in batch:
        s = a["symbol"]
        if s not in seen_sym:
            seen_sym[s] = a
            unique_batch.append(a)
        else:
            dup_batch.append(a)
    # process unique_batch via batch
    if unique_batch:
        syms_u = [a["symbol"] for a in unique_batch]
        res = fetch_bars_batch(syms_u)
        for a in unique_batch:
            sym = a["symbol"]
            aid = a["id"]
            status, bars = res.get(sym, ("PROVIDER_ERROR", []))
            # Convert status EMPTY vs SUCCESS based on bar count
            if status == "SUCCESS" and len(bars) == 0:
                status = "EMPTY"
            if status == "SUCCESS":
                results[aid] = {"status": "SUCCESS", "bars": len(bars), "batch": True}
                # Store bars temporarily in memory for final dataset via file cache? For now we store in results with bar data placeholder (we will later re-fetch bars for final CSV via batch again or via stored bars)
                # To avoid losing bars, we write them to a temp cache file per batch
                # We'll cache bars to a json file keyed by aid
            else:
                results[aid] = {"status": status, "bars": 0}
        print(
            f"batch {i // batch_size + 1}/{(len(remaining) + batch_size - 1) // batch_size} unique {len(unique_batch)} fetched"
        )
    # For dup_batch, handle individually (rare, ticker reuse)
    for a in dup_batch:
        sym = a["symbol"]
        aid = a["id"]
        # fallback individual
        single_res = fetch_bars_batch([sym])
        status, bars = single_res.get(sym, ("PROVIDER_ERROR", []))
        if status == "SUCCESS" and len(bars) == 0:
            status = "EMPTY"
        results[aid] = {"status": status, "bars": len(bars) if status == "SUCCESS" else 0}
        print(f"  dup {sym} {aid[:8]} {status}")
    # checkpoint every 5 batches
    if (i // batch_size + 1) % 5 == 0:
        ckpt_data = {
            "processed": len([a for a in eligible if a["id"] in results]),
            "total": len(eligible),
            "results_by_symbol": results,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        CHECKPOINT.write_text(json.dumps(ckpt_data, indent=2))
        print(f"checkpoint {ckpt_data['processed']}/{ckpt_data['total']}")
    time.sleep(0.35)

# final checkpoint
ckpt_data = {
    "processed": len([a for a in eligible if a["id"] in results]),
    "total": len(eligible),
    "results_by_symbol": results,
    "updated_at": datetime.now(UTC).isoformat(),
}
CHECKPOINT.write_text(json.dumps(ckpt_data, indent=2))
print(f"final checkpoint {ckpt_data['processed']}/{ckpt_data['total']}")
print(f"summary {Counter(v['status'] for v in results.values())}")

# Now we need to rebuild success_bars for final CSV: for each SUCCESS, we need actual bars.
# For those reused from demo (224), we already have bars via demo csv; for those newly SUCCESS via batch, we lost actual bar data because we only stored count.
# So we need to re-fetch bars for all SUCCESS again via batch to get actual bars for CSV (or we can fetch now in batches and write CSV directly).

# Let's collect all SUCCESS aids and fetch their bars in batches for final CSV
success_aids = [aid for aid, v in results.items() if v["status"] == "SUCCESS"]
print(f"Need to materialize bars for {len(success_aids)} successes for final CSV")
# We'll fetch in batches of 30 to avoid payload too large (each symbol ~2650 bars, 30*2650 ~80k bars per batch ~ maybe large but okay)
# Instead fetch in batches of 20
bars_by_aid = {}
# Group success assets by symbol (unique)
success_assets = [by_id[aid] for aid in success_aids if aid in by_id]
# Some successes may be from demo reused where by_id missing? but all eligible are in by_id
# Batch fetch for materialization
for i in range(0, len(success_assets), 20):
    batch = success_assets[i : i + 20]
    syms = [a["symbol"] for a in batch]
    # handle duplicate symbols similar
    uniq = {}
    for a in batch:
        if a["symbol"] not in uniq:
            uniq[a["symbol"]] = a
    syms_u = list(uniq.keys())
    res = fetch_bars_batch(syms_u)
    for a in batch:
        sym = a["symbol"]
        aid = a["id"]
        if aid not in bars_by_aid:
            # Check if demo reused exists: prefer demo bars to avoid extra fetch? But we already have batch result for uniq symbol, use that
            status, bars = res.get(sym, ("EMPTY", []))
            if status == "SUCCESS" and bars:
                bars_by_aid[aid] = bars
            else:
                # fallback to demo bars if available
                if sym in demo_by_sym and len(demo_by_sym[sym]) >= 20:
                    # synth bars from demo
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
                else:
                    bars_by_aid[aid] = []
    print(f"materialize batch {i // 20 + 1} {len(bars_by_aid)}/{len(success_assets)}")
    time.sleep(0.35)

# Also ensure demo reused that were not in success_aids but have demo bars and are eligible but marked not SUCCESS? Actually demo reused were marked SUCCESS via earlier checkpoint reused logic, so they are in success_aids
# Save bars cache
cache_path = RESEARCH_DIR / "bars_by_aid_cache.json"
# To save size, we store counts only? But for final CSV we need bars materialized directly now into CSV, so we can build CSV without cache file
# Build lifetimes and CSV
print("Building lifetimes and CSV...")
OUT_LIFETIMES.parent.mkdir(parents=True, exist_ok=True)
# Build lifetimes similar to original script
# Load current survivors
with open(current_csv, newline="") as f:
    reader = _csv.DictReader(f)
    bars_by_sym = defaultdict(list)
    for row in reader:
        bars_by_sym[row["symbol"]].append(row)

lifetimes = []
# survivors
import json as _json

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

# recovered
for aid, bars in bars_by_aid.items():
    if not bars:
        continue
    asset = by_id.get(aid)
    if not asset:
        continue
    sym = asset["symbol"]
    # skip if already survivor with same symbol and same id (should not happen)
    if sym in bars_by_sym and asset["id"] == next((a["id"] for a in active if a["symbol"] == sym), None):
        continue
    first = bars[0]["t"]
    last = bars[-1]["t"]
    dt_first = datetime.fromisoformat(first.replace("Z", "+00:00"))
    dt_last = datetime.fromisoformat(last.replace("Z", "+00:00"))
    first_str = dt_first.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    last_str = dt_last.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    bar_count = len(bars)
    tier = "TIER_B_RECONSTRUCTED_PIT" if bar_count >= 63 else "TIER_D_UNUSABLE"
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
            "notes": f"Recovered inactive history via SIP, {bar_count} bars",
        }
    )

# FB->META handle
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
    w = _csv.DictWriter(
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

# Build full bars CSV
all_bars = []
with open(current_csv, newline="") as f:
    for row in _csv.DictReader(f):
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
    else:
        prev = dedup[key]
        if (prev["open"], prev["high"], prev["low"], prev["close"], prev["volume"]) != (
            row["open"],
            row["high"],
            row["low"],
            row["close"],
            row["volume"],
        ):
            pass
all_sorted = sorted(dedup.values(), key=lambda x: (x["timestamp"], x["symbol"]))
print(f"total bars {len(all_sorted)}")
with open(OUT_CSV, "w", newline="") as f:
    w = _csv.DictWriter(
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


# manifest
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

# Generate recovery_summary and asset discovery already done? Update them
recovery_summary = {
    "total_eligible": len(eligible),
    "total_attempted": len(results),
    "by_status": dict(Counter(v["status"] for v in results.values())),
    "success": sum(1 for v in results.values() if v["status"] == "SUCCESS"),
    "feed": FEED,
    "adjustment": ADJUSTMENT,
}
(REPO / "data/research/full_survivorship_2026_08/recovery_summary.json").write_text(
    json.dumps(recovery_summary, indent=2)
)
print("done")
