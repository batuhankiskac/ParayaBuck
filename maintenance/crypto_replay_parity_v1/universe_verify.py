"""Independent daily/hourly adapter eligibility verifier; standard library only.

Does not import/call production scanner, candidate builder or universe adapter.
Observation ranking, fills and NAV are verified by the separate verifier.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import statistics
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

STABLE_BASES = {"USDT", "USDC", "DAI", "BUSD", "TUSD", "USDP", "GUSD", "PYUSD", "FDUSD", "USDE"}


def metrics_and_reason(
    rows: list[dict[str, Any]], policy: dict[str, Any], symbol: str, bars_per_day: int = 1
) -> tuple[dict[str, Any], str | None]:
    """Rows are independently selected from [boundary-lookback,boundary)."""
    valid = [
        r
        for r in rows
        if math.isfinite(r["close"]) and math.isfinite(r["volume"]) and r["close"] > 0 and r["volume"] >= 0
    ]
    daily: defaultdict[str, float] = defaultdict(float)
    for r in valid:
        daily[r["timestamp"][:10]] += r["close"] * r["volume"]
    ranges = [
        max(b["high"] - b["low"], abs(b["high"] - a["close"]), abs(b["low"] - a["close"]))
        for a, b in zip(valid, valid[1:])
    ]
    price = valid[-1]["close"] if valid else 0
    stats = {
        "history_count": len(valid),
        "completeness": min(1, len(valid) / (policy["lookback_days"] * bars_per_day)),
        "median_dollar_volume": statistics.median(daily.values()) if daily else 0,
        "atr_pct": statistics.mean(ranges[-14:]) / price if ranges and price > 0 else 0,
    }
    if symbol.split("/")[-1] != "USD":
        return stats, "not_usd_pair"
    if symbol.split("/")[0] in STABLE_BASES:
        return stats, "stablecoin"
    if price <= 0 or not all(math.isfinite(v) for v in stats.values()):
        return stats, "invalid_metrics"
    if not valid:
        return stats, "insufficient_history"
    if stats["completeness"] < policy["minimum_data_completeness"]:
        return stats, "data_completeness"
    if stats["median_dollar_volume"] < policy["minimum_median_dollar_volume"]:
        return stats, "minimum_liquidity"
    if not policy["minimum_atr_pct"] <= stats["atr_pct"] <= policy["maximum_atr_pct"]:
        return stats, "atr_range"
    return stats, None


def verify(payload: dict[str, Any], source_bars: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    timeframe = payload["universe_timeframe"]
    if timeframe not in {"1d", "1h"}:
        raise ValueError("Only daily and hourly supported")
    if timeframe == "1h" and source_bars is None:
        raise ValueError("Hourly verification requires independently read raw CSV")
    bars_per_day = 24 if timeframe == "1h" else 1
    config = payload["config"]["universe"]["crypto"]
    policy = config["scanner"]
    by_symbol = defaultdict(list)
    for raw in source_bars if source_bars is not None else payload["daily_bars"]:
        if raw["timeframe"] != timeframe or raw["market"] != "crypto":
            raise ValueError("Unexpected input bar contract")
        by_symbol[raw["symbol"]].append(raw)
    times = {}
    for symbol, rows in by_symbol.items():
        rows.sort(key=lambda r: r["timestamp"])
        times[symbol] = [datetime.fromisoformat(r["timestamp"]) for r in rows]
        if len(set(times[symbol])) != len(rows):
            raise ValueError("Duplicate daily key")
    allowed = set(config["trade_symbols"])
    if config["allowlist_symbols"]:
        allowed &= set(config["allowlist_symbols"])
    allowed -= set(config["denylist_symbols"])
    observed = {r["effective_at"]: r for r in payload["universe"]["snapshots"]}
    expected_boundaries = []
    mismatches: list[dict[str, Any]] = []
    counts: defaultdict[str, int] = defaultdict(int)
    metric_receipts = []
    previous_day = None
    for boundary in sorted({t for values in times.values() for t in values}):
        if boundary.date() == previous_day:
            continue
        previous_day = boundary.date()
        cutoff = boundary - timedelta(days=policy["lookback_days"])
        trailing = {
            s: rows[bisect_left(times[s], cutoff) : bisect_left(times[s], boundary)] for s, rows in by_symbol.items()
        }
        trailing = {s: rows for s, rows in trailing.items() if rows}
        candidates = sorted(set(trailing) & allowed)
        pins = [s for s in config["pinned_symbols"] if s in trailing]
        members = set(pins)
        eliminated = {}
        for symbol in candidates:
            rows = trailing[symbol]
            assert all(cutoff <= datetime.fromisoformat(r["timestamp"]) < boundary for r in rows)
            metrics, reason = metrics_and_reason(rows, policy, symbol, bars_per_day)
            metric_receipts.append([boundary.isoformat(), symbol, metrics, reason])
            counts["candidate_checks"] += 1
            if reason is None:
                members.add(symbol)
            elif symbol not in config["pinned_symbols"]:
                eliminated[symbol] = reason
            else:
                counts["pin_eligibility_bypasses"] += 1
        if not members and not candidates:
            continue
        key = boundary.isoformat()
        expected_boundaries.append(key)
        actual = observed.get(key)
        if actual is None:
            mismatches.append({"timestamp": key, "field": "missing_snapshot"})
            continue
        expected = {
            "symbols": sorted(members),
            "candidate_count": len(candidates),
            "eliminated": eliminated,
            "pinned_symbols": pins,
            "maximum_stream_symbols": config["maximum_stream_symbols"],
        }
        for field, value in expected.items():
            if actual[field] != value:
                mismatches.append({"timestamp": key, "field": field, "expected": value, "actual": actual[field]})
        for symbol, score in actual["score_breakdown"].items():
            if score.get("spread_source") != "unavailable" or (
                timeframe == "1d"
                and policy["lookback_days"] < 25
                and score.get("btc_correlation", "unavailable") != "unavailable"
            ):
                mismatches.append({"timestamp": key, "field": "unavailable_metrics", "symbol": symbol})
    extras = sorted(set(observed) - set(expected_boundaries))
    if extras:
        mismatches.append({"field": "extra_snapshots", "timestamps": extras})
    return {
        "status": "PASS" if not mismatches else "FAIL",
        "snapshots_verified": len(expected_boundaries),
        "universe_timeframe": timeframe,
        **dict(counts),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:30],
        "independence": "Standard library only; no production scanner/candidate/universe imports",
        "metric_receipts_sha256": hashlib.sha256(json.dumps(metric_receipts, sort_keys=True).encode()).hexdigest(),
        "verified": [
            "strict prior14d source-timeframe window",
            "configured pool intersection",
            "completeness",
            "daily median dollar volume",
            "true range ATR",
            "eligibility reasons",
            "pin bypass with available trailing history",
            "all snapshot boundaries",
        ],
        "limitations": [
            "Conditioned on input bars and pinned config; hourly mode reads raw CSV independently",
            "Observation alpha ranking verified separately; score and correlation quality ordering not independently recomputed",
            "No historical quote spread or broker tradability metadata; unavailable remains unavailable",
            "Daily14d window cannot supply24 return pairs; correlation unavailable",
            "Category caps and correlation penalties are not pool eligibility gates",
            "Daily public adapter is not equivalent to configured runtime1h scanner",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--hourly-csv", type=Path)
    args = parser.parse_args()
    with gzip.open(args.input, "rt") as stream:
        payload = json.load(stream)
    source_bars = None
    if args.hourly_csv:
        if hashlib.sha256(args.hourly_csv.read_bytes()).hexdigest() != payload["data"]["sha256"]:
            raise ValueError("Raw CSV bytes differ from replay data pin")
        source_bars = []
        end = datetime.fromisoformat(payload["window"]["end"])
        with args.hourly_csv.open() as stream:
            for raw in csv.DictReader(stream):
                ts = datetime.fromisoformat(raw["timestamp"].replace("Z", "+00:00"))
                if ts <= end:
                    source_bars.append(
                        {
                            **raw,
                            **{k: float(raw[k]) for k in ("open", "high", "low", "close", "volume")},
                            "timestamp": ts.isoformat(),
                            "timeframe": "1h",
                            "market": "crypto",
                        }
                    )
    result = verify(payload, source_bars)
    if args.hourly_csv:
        result["raw_csv_sha256"] = hashlib.sha256(args.hourly_csv.read_bytes()).hexdigest()
    result["input_sha256"] = hashlib.sha256(args.input.read_bytes()).hexdigest()
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
