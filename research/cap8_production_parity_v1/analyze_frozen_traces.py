"""Read frozen migration Git blobs; never run the replay or contact a broker."""

from __future__ import annotations

import bisect
import gzip
import json
import statistics
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

REF = "9c5e61382e8cdb71fdd3442d945719024accedd9"
ROOT = "research/account_capacity_migration_8_v3"
OUT = Path(__file__).with_name("GROSS_EXCURSION_ANALYSIS.json")


def percentile(values: list[float], p: float) -> float:
    values = sorted(values)
    index = (len(values) - 1) * p
    lower = int(index)
    return values[lower] + (values[min(lower + 1, len(values) - 1)] - values[lower]) * (index - lower)


def analyze(cap: int) -> dict[str, Any]:
    arm = "CONTROL_CAP6" if cap == 6 else "TREATMENT_CAP8"
    blob = subprocess.check_output(["git", "show", f"{REF}:{ROOT}/{arm}_TRACE_V3.json.gz"])
    data = json.loads(gzip.decompress(blob))
    marks = [e for e in data["events"] if e["event"] == "account_mark"]
    assert len(marks) == len(data["timestamps"]) == len(data["cash_path"]) == len(data["nav_path"]) == 50194
    assert all(e["timestamp"] == ts for e, ts in zip(marks, data["timestamps"], strict=True))
    above = [i for i, e in enumerate(marks) if e["gross"] > 0.60 + 1e-12]
    episodes: list[dict[str, Any]] = []
    for i in above:
        if not episodes or i != episodes[-1]["last_index"] + 1:
            episodes.append({"first_index": i, "last_index": i})
        else:
            episodes[-1]["last_index"] = i
    for episode in episodes:
        first, last = episode["first_index"], episode["last_index"]
        episode.update(
            first_timestamp=marks[first]["timestamp"],
            last_timestamp=marks[last]["timestamp"],
            first_gross=marks[first]["gross"],
            peak_gross=max(marks[i]["gross"] for i in range(first, last + 1)),
            observed_span_hours=(
                datetime.fromisoformat(marks[last]["timestamp"]) - datetime.fromisoformat(marks[first]["timestamp"])
            ).total_seconds()
            / 3600,
        )
    # A left-held mark integral describes the observation grid, not continuous
    # exposure between marks. Terminal mark has no forward interval.
    hours = [
        (datetime.fromisoformat(marks[i + 1]["timestamp"]) - datetime.fromisoformat(mark["timestamp"])).total_seconds()
        / 3600
        for i, mark in enumerate(marks[:-1])
    ]
    excess = [marks[i]["gross"] - 0.60 for i in above]
    fill_times = [e["timestamp"] for e in data["events"] if e["event"] == "fill"]
    fill_times.sort()
    decision_times = sorted(e["timestamp"] for e in data["events"] if e["event"] == "decision")
    for episode in episodes:
        first = episode["first_index"]
        lo = marks[first - 1]["timestamp"] if first else marks[first]["timestamp"]
        hi = marks[first]["timestamp"]
        episode["first_mark_after_fill"] = bisect.bisect_right(fill_times, hi) > bisect.bisect_left(fill_times, lo)
        end = episode["last_index"] + 1
        episode["next_mark_timestamp"] = marks[end]["timestamp"] if end < len(marks) else None
        episode["resolution_at_next_mark"] = end < len(marks) and marks[end]["gross"] <= 0.60 + 1e-12
        episode["decisions_during_observed_episode"] = bisect.bisect_right(
            decision_times, marks[episode["last_index"]]["timestamp"]
        ) - bisect.bisect_left(decision_times, marks[first]["timestamp"])

    # Reconstruct held sleeve values from trace fills, corporate-action
    # quantity updates, and per-position price marks. Compare to account gross;
    # report attribution only where that independently reconciles.
    positions: dict[tuple[str, str], float] = {}
    prices: dict[tuple[str, str], float] = {}
    sleeve: list[dict[str, float] | None] = []
    over_mark_order_counts: Counter[str] = Counter()
    previous_gross = 0.0
    for event in data["events"]:
        kind = event["event"]
        if kind == "fill":
            key = event["market"], event["symbol"]
            positions[key] = positions.get(key, 0.0) + (event["qty"] if event["side"] == "buy" else -event["qty"])
            if positions[key] <= 1e-9:
                positions.pop(key)
            prices[key] = event["reference_price"]
        elif kind in {"terminal_liquidation", "final_liquidation"}:
            positions.pop((event["market"], event["symbol"]), None)
        elif kind == "corporate_action":
            key = event["market"], event["symbol"]
            if key in positions and event["position_qty_before"] != event["position_qty_after"]:
                positions[key] = event["position_qty_after"]
        elif kind == "mark":
            prices[event["market"], event["symbol"]] = event["price"]
        elif kind == "reservation_created" and event["side"] == "buy" and previous_gross > 0.60 + 1e-12:
            over_mark_order_counts["buy_reservations_after_over_limit_observation"] += 1
        elif kind == "account_mark":
            nav = event["nav"]
            equity = sum(abs(qty) * prices.get(key, 0.0) for key, qty in positions.items() if key[0] == "equity") / nav
            crypto = sum(abs(qty) * prices.get(key, 0.0) for key, qty in positions.items() if key[0] == "crypto") / nav
            error = abs(equity + crypto - event["gross"])
            sleeve.append(
                {"equity": equity, "crypto": crypto, "reconciliation_error": error} if error <= 1e-6 else None
            )
            previous_gross = event["gross"]
    matched = [value for i in above if (value := sleeve[i]) is not None]
    cash_below = [
        i
        for i, (cash, nav) in enumerate(zip(data["cash_path"], data["nav_path"], strict=True))
        if cash / nav < 0.40 - 1e-12
    ]
    return {
        "cap": cap,
        "trace_ref": REF,
        "mark_count": len(marks),
        "over_60_count": len(above),
        "episode_count": len(episodes),
        "episodes": episodes,
        "episode_observed_span_hours_distribution": {
            "median": statistics.median([e["observed_span_hours"] for e in episodes]) if episodes else 0,
            "p95": percentile([e["observed_span_hours"] for e in episodes], 0.95) if episodes else 0,
            "max": max([e["observed_span_hours"] for e in episodes], default=0),
        },
        "peak_realized_gross": max(e["gross"] for e in marks),
        "peak_target_gross": max(e["target_gross"] for e in marks),
        "excess_fraction_median": statistics.median(excess) if excess else 0,
        "excess_fraction_p95": percentile(excess, 0.95) if excess else 0,
        "left_hold_excess_fraction_hours": sum(max(0, marks[i]["gross"] - 0.60) * hours[i] for i in range(len(hours))),
        "left_hold_observed_hours": sum(hours),
        "episode_first_mark_after_fill_count": sum(e["first_mark_after_fill"] for e in episodes),
        "episode_first_mark_without_fill_since_previous_mark_count": sum(
            not e["first_mark_after_fill"] for e in episodes
        ),
        "episodes_with_decision_during_observed_episode": sum(
            e["decisions_during_observed_episode"] > 0 for e in episodes
        ),
        "episodes_without_decision_during_observed_episode": sum(
            e["decisions_during_observed_episode"] == 0 for e in episodes
        ),
        "sleeve_attribution_reconciled_over_marks": len(matched),
        "sleeve_attribution_unreconciled_over_marks": len(above) - len(matched),
        "reconciled_over_mark_sleeve_means": {
            k: statistics.mean(s[k] for s in matched) if matched else None for k in ("equity", "crypto")
        },
        "cash_below_40_count": len(cash_below),
        "minimum_realized_cash_ratio": min(
            cash / nav for cash, nav in zip(data["cash_path"], data["nav_path"], strict=True)
        ),
        **over_mark_order_counts,
    }


if __name__ == "__main__":
    result = {
        "method": "Consecutive above-limit account marks form an episode. Durations are first-to-last observed spans; a singleton is zero hours. Left-hold integral is descriptive only. A reservation count uses the last observed account gross and does not prove account gross at exact submission time.",
        "control": analyze(6),
        "treatment": analyze(8),
    }
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(OUT)
