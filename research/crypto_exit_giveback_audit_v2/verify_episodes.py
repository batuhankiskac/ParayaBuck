"""Independent average-cost episode verification from canonical raw replay.

No primary diagnostic or production imports. Entry fees enter inventory basis;
spread/slippage are embedded in fill prices. Extrema use active closing marks,
not settlement prices. Numeric comparisons permit arithmetic order differences.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta
from typing import Any


def _days(a: str, b: str) -> float:
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 86400


def _rank(decision: dict[str, Any]) -> list[str]:
    scored = []
    for symbol in decision["eligible_symbols"]:
        bars = decision["history"].get(symbol, [])[-23:]
        if len(bars) != 23:
            continue
        if datetime.fromisoformat(bars[-1]["timestamp"]) - datetime.fromisoformat(bars[0]["timestamp"]) != timedelta(
            days=22
        ):
            continue
        closes = [float(bar["close"]) for bar in bars]
        if any(not math.isfinite(c) or c <= 0 for c in closes):
            continue
        scored.append((statistics.stdev([b / a - 1 for a, b in zip(closes, closes[1:])]), symbol))
    return [symbol for _, symbol in sorted(scored)]


def verify(raw: dict[str, Any], diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Verify every episode, every mark, all fills and all scheduled selections."""
    failures: list[str] = []

    def check(actual: Any, expected: Any, label: str) -> None:
        if isinstance(expected, (float, int)) and not isinstance(expected, bool):
            same = (
                isinstance(actual, (int, float))
                and math.isfinite(actual)
                and math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-7)
            )
        else:
            same = actual == expected
        if not same:
            failures.append(f"{label}: observed={actual!r}; independent={expected!r}")

    decisions = {d["timestamp"]: d for d in raw["decisions"]}
    rankings = {}
    for decision in raw["decisions"]:
        stamp = decision["timestamp"]
        ranked = _rank(decision)
        rankings[stamp] = ranked
        retained = [s for s in ranked[:4] if decision["holdings"].get(s, 0) > 1e-12][:2]
        selected = (retained + [s for s in ranked if s not in retained])[:2]
        check([s for _, s in decision["scores"]], ranked, f"{stamp}.rank")
        check(decision["alpha_target"]["metadata"].get("retained", []), retained, f"{stamp}.retained")
        check(decision["alpha_target"]["metadata"].get("selected_symbols", []), selected, f"{stamp}.selected")
    counts: dict[str, int] = {}
    active: dict[str, dict[str, Any]] = {}
    episodes: list[dict[str, Any]] = []
    closed_pnl = 0.0
    fill_count = mark_count = 0
    events = [(f["timestamp"], 0, i, "fill", f) for i, f in enumerate(raw["fills"])]
    events += [(s["timestamp"], 1, i, "mark", s) for i, s in enumerate(raw["snapshots"])]
    events += [(raw["snapshots"][-1]["timestamp"], 2, i, "terminal", t) for i, t in enumerate(raw["terminal_events"])]
    for stamp, _, _, kind, event in sorted(events):
        if kind == "mark":
            check(set(event["positions"]), set(active), f"{stamp}.symbols")
            for symbol, ep in active.items():
                position = event["positions"].get(symbol)
                if position is None:
                    continue
                check(position["qty"], ep["qty"], f"{stamp}.{symbol}.qty")
                check(
                    position["qty"] * position["avg_price"] + position["entry_fee"],
                    ep["basis"],
                    f"{stamp}.{symbol}.basis",
                )
                unrealized = ep["qty"] * position["mark"] - ep["basis"]
                ep["path"].append(
                    dict(
                        timestamp=stamp,
                        pnl=ep["realized"] + unrealized,
                        realized=ep["realized"],
                        unrealized=unrealized,
                        qty=ep["qty"],
                        mark=position["mark"],
                    )
                )
            check(
                event["nav"],
                raw["metrics"]["starting_equity"] + closed_pnl + sum(e["path"][-1]["pnl"] for e in active.values()),
                f"{stamp}.nav",
            )
            mark_count += 1
            continue
        terminal = kind == "terminal"
        symbol = event["symbol"]
        side = "sell" if terminal else event["side"]
        qty = event["quantity"] if terminal else event["qty"]
        if side == "buy" and symbol not in active:
            counts[symbol] = counts.get(symbol, 0) + 1
            active[symbol] = dict(
                episode_id=f"{diagnostics['strategy']}:{symbol}:{counts[symbol]}",
                symbol=symbol,
                entry_timestamp=stamp,
                qty=0.0,
                basis=0.0,
                capital=0.0,
                realized=0.0,
                path=[],
                fills=[],
            )
        if symbol not in active:
            failures.append(f"{stamp}.{symbol}: sell without open episode")
            continue
        ep = active[symbol]
        ep["fills"].append(
            {**event, "type": "terminal", "qty": qty, "side": "sell"} if terminal else {**event, "type": "fill"}
        )
        if side == "buy":
            outlay = qty * event["fill_price"] + event["fee"]
            ep["capital"] += outlay
            ep["basis"] += outlay
            ep["qty"] += qty
        else:
            check(qty <= ep["qty"] + 1e-8, True, f"{stamp}.{symbol}.no_oversale")
            basis = ep["basis"] * qty / ep["qty"]
            ep["realized"] += qty * event["fill_price"] - event["fee"] - basis
            ep["basis"] -= basis
            ep["qty"] -= qty
            if ep["qty"] <= 1e-9:
                ep.update(
                    exit_timestamp=stamp,
                    censored=terminal and event["policy"] == "FINAL_BAR",
                    final_pnl=ep["realized"],
                    exit_event=event,
                    terminal=terminal,
                )
                closed_pnl += ep["realized"]
                episodes.append(ep)
                del active[symbol]
        fill_count += 1
    check(len(active), 0, "no_unclosed_positions")
    check(raw["metrics"]["ending_equity"], raw["metrics"]["starting_equity"] + closed_pnl, "final.nav")
    saved = {e["episode_id"]: e for e in diagnostics["episodes"]}
    check(len(saved), len(diagnostics["episodes"]), "unique_episode_ids")
    check(len(saved), len(episodes), "episode_count")
    checked_paths = 0
    for ep in episodes:
        identity = ep["episode_id"]
        actual = saved.get(identity)
        if actual is None:
            failures.append(f"missing {identity}")
            continue
        for field in ("symbol", "entry_timestamp", "exit_timestamp", "censored", "capital", "final_pnl", "fills"):
            check(actual.get(field), ep[field], f"{identity}.{field}")
        check(len(actual["path"]), len(ep["path"]), f"{identity}.path_count")
        for i, (observed, expected) in enumerate(zip(actual["path"], ep["path"])):
            for key, value in expected.items():
                check(observed.get(key), value, f"{identity}.path[{i}].{key}")
            checked_paths += 1
        if not ep["path"]:
            failures.append(f"{identity}: no completed-bar marks")
            continue
        peak = max(ep["path"], key=lambda p: p["pnl"])
        trough = min(ep["path"], key=lambda p: p["pnl"])
        values = dict(
            mfe=peak["pnl"],
            mae=trough["pnl"],
            mfe_timestamp=peak["timestamp"],
            mae_timestamp=trough["timestamp"],
            final_return=ep["final_pnl"] / ep["capital"],
            mfe_return=peak["pnl"] / ep["capital"],
            mae_return=trough["pnl"] / ep["capital"],
            giveback=peak["pnl"] - ep["final_pnl"] if peak["pnl"] > 0 else None,
            retention=ep["final_pnl"] / peak["pnl"] if peak["pnl"] > 0 else None,
            holding_days=_days(ep["entry_timestamp"], ep["exit_timestamp"]),
            time_to_mfe_days=_days(ep["entry_timestamp"], peak["timestamp"]),
            mfe_to_exit_days=_days(peak["timestamp"], ep["exit_timestamp"]),
        )
        for key, value in values.items():
            check(actual.get(key), value, f"{identity}.{key}")
        for level, row in actual.get("levels", {}).items():
            threshold = float(level)
            crossed = [p for p in ep["path"] if p["pnl"] / ep["capital"] >= threshold]
            first = crossed[0]["timestamp"] if crossed else None
            expected_level = dict(
                ever_reached=bool(crossed),
                first_crossing=first,
                crossing_to_mfe_days=_days(first, peak["timestamp"]) if first else None,
                crossing_to_exit_days=_days(first, ep["exit_timestamp"]) if first else None,
            )
            for key, value in expected_level.items():
                check(row.get(key), value, f"{identity}.levels.{level}.{key}")
        postpeak = [p for p in ep["path"] if p["timestamp"] >= peak["timestamp"]]
        below = positive_days = 0.0
        for i, point in enumerate(postpeak):
            end = postpeak[i + 1]["timestamp"] if i + 1 < len(postpeak) else ep["exit_timestamp"]
            span = _days(point["timestamp"], end)
            below += span if point["pnl"] < peak["pnl"] else 0
            positive_days += span if point["pnl"] > 0 else 0
        for key, value in dict(days_below_peak_after_mfe=below, days_positive_after_mfe=positive_days).items():
            if key in actual:
                check(actual[key], value, f"{identity}.{key}")
        observations = [*postpeak, dict(timestamp=ep["exit_timestamp"], pnl=ep["final_pnl"], settlement=True)]
        for fraction, row in actual.get("profit_erosion_times", {}).items():
            hits = (
                [
                    p
                    for p in observations
                    if p["timestamp"] > peak["timestamp"] and p["pnl"] <= peak["pnl"] * (1 - float(fraction))
                ]
                if peak["pnl"] > 0
                else []
            )
            hit = hits[0] if hits else None
            expected_erosion = dict(
                timestamp=hit["timestamp"] if hit else None,
                days=_days(peak["timestamp"], hit["timestamp"]) if hit else None,
                observation="actual_exit_settlement"
                if hit and hit.get("settlement")
                else "completed_bar"
                if hit
                else None,
            )
            check(row, expected_erosion, f"{identity}.profit_erosion_times.{fraction}")
        symbol = ep["symbol"]
        records = []
        for decision in raw["decisions"]:
            ranked = rankings[decision["timestamp"]]
            rank = ranked.index(symbol) + 1 if symbol in ranked else None
            held = [s for s in ranked[:4] if decision["holdings"].get(s, 0) > 1e-12][:2]
            selected = (held + [s for s in ranked if s not in held])[:2]
            weight = sum(w["weight"] for w in decision["target"]["weights"] if w["symbol"] == symbol)
            eligible = symbol in decision["eligible_symbols"]
            reason = "SIZE_REBALANCE"
            if not eligible:
                reason = "SCANNER_INELIGIBLE"
            elif rank is None:
                reason = "QUALIFIED_HISTORY_LOSS"
            elif rank > 4:
                reason = "RANK_ABOVE_RETENTION"
            elif symbol not in selected:
                reason = "RETENTION_CAPACITY_DISPLACEMENT"
            elif weight <= 0:
                reason = "RISK_OVERLAY_ZERO_TARGET"
            state = "OTHER_SELECTED"
            if symbol not in selected or weight <= 0:
                state = "NOT_SELECTED_OR_ZERO_TARGET_PENDING_EXIT"
            elif rank is not None and rank <= 2:
                state = "TOP_TWO_SELECTED"
            elif symbol in held and rank in (3, 4):
                state = "RANK_THREE_FOUR_HYSTERESIS_RETAINED"
            records.append(
                dict(
                    timestamp=decision["timestamp"],
                    rank=rank,
                    scanner_eligible=eligible,
                    selected=symbol in selected,
                    retained=symbol in held,
                    target_weight=weight,
                    held_quantity=decision["holdings"].get(symbol, 0.0),
                    held_symbols=sorted(s for s, q in decision["holdings"].items() if q > 1e-12),
                    selected_symbols=selected,
                    state=state,
                    reason=reason,
                )
            )
        source = ep["fills"][0].get("decision_timestamp")
        observed_path = [
            r
            for r in records
            if r["timestamp"] == source
            or ep["entry_timestamp"] <= r["timestamp"] <= ep["exit_timestamp"]
            and r["held_quantity"] > 1e-12
        ]
        asof_rows = [r for r in observed_path if r["timestamp"] <= peak["timestamp"]]
        asof = asof_rows[-1] if asof_rows else None
        after = [
            r
            for r in observed_path
            if peak["timestamp"] < r["timestamp"] <= ep["exit_timestamp"] and r["held_quantity"] > 1e-12
        ]
        sales = [f for f in ep["fills"] if f.get("side") == "sell" and f.get("type") != "terminal"]
        last_source = sales[-1].get("decision_timestamp") if sales else None
        final_decision = next((r for r in records if r["timestamp"] == last_source), None)
        initiating = None
        if final_decision and final_decision["target_weight"] <= 0:
            candidates = [r for r in observed_path if r["timestamp"] <= final_decision["timestamp"]]
            last_positive = max((i for i, r in enumerate(candidates) if r["target_weight"] > 0), default=-1)
            remaining = candidates[last_positive + 1 :]
            initiating = remaining[0] if remaining else None
        reason = (
            "TERMINAL_CENSORED"
            if ep["censored"]
            else final_decision["reason"]
            if final_decision
            else "NO_EXIT_DECISION"
        )
        expected_hysteresis = dict(
            decision_path=observed_path,
            mfe_rank_asof=asof,
            mfe_rank_is_contemporaneous_decision=bool(asof and asof["timestamp"] == peak["timestamp"]),
            after_peak_decision_states=after,
            exit_reason=reason,
            exit_final_fill_decision=final_decision,
            exit_initiating_decision=initiating,
            holding_age_lock_hours=None,
            holding_age_expiry_timestamp=None,
        )
        for key, value in expected_hysteresis.items():
            check(actual.get("hysteresis", {}).get(key), value, f"{identity}.hysteresis.{key}")
    return dict(
        status="FAIL" if failures else "PASS",
        method="independent_fee_capitalized_average_cost_and_stdlib_ranking",
        production_imports=False,
        primary_diagnostics_imports=False,
        episodes_checked=len(episodes),
        all_episodes_checked=True,
        extreme_episodes_included=True,
        completed_marks_checked=checked_paths,
        nav_snapshots_checked=mark_count,
        fills_and_terminals_checked=fill_count,
        decisions_checked=len(decisions),
        failures=failures,
    )


def verify_summary(
    episodes: list[dict[str, Any]], summary: dict[str, Any], *, net_strategy_pnl: float | None = None
) -> dict[str, Any]:
    """Independently check descriptive grouping and attribution (no reruns)."""
    errors = []

    def same(a: Any, b: Any, key: str) -> None:
        if isinstance(b, dict):
            for name, value in b.items():
                same(a.get(name), value, f"{key}.{name}")
        elif isinstance(b, (float, int)):
            if not isinstance(a, (float, int)) or not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-7):
                errors.append(key)
        elif a != b:
            errors.append(key)

    def percentile(values: list[float], q: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        offset = (len(ordered) - 1) * q
        low = math.floor(offset)
        high = math.ceil(offset)
        return ordered[low] * (high - offset) + ordered[high] * (offset - low) if high != low else ordered[low]

    def stats(values: list[float]) -> dict[str, float | None]:
        return dict(
            mean=sum(values) / len(values) if values else None,
            **{
                k: percentile(values, q)
                for k, q in [("p10", 0.1), ("p25", 0.25), ("median", 0.5), ("p75", 0.75), ("p90", 0.9)]
            },
        )

    positive = [e for e in episodes if e["mfe"] > 0]
    same(
        summary,
        dict(
            count=len(episodes),
            positive_mfe_count=len(positive),
            net_pnl=sum(e["final_pnl"] for e in episodes),
            winner_to_loser_count=sum(e["final_pnl"] < 0 for e in positive),
            winner_to_nonpositive_count=sum(e["final_pnl"] <= 0 for e in positive),
            positive_mfe_near_flat_count=sum(abs(e["final_return"]) <= 0.01 for e in positive),
        ),
        "summary",
    )
    for field in (
        "holding_days",
        "time_to_mfe_days",
        "mfe_to_exit_days",
        "mfe",
        "mae",
        "mfe_return",
        "mae_return",
        "final_pnl",
        "final_return",
        "retention",
        "giveback",
        "days_below_peak_after_mfe",
        "days_positive_after_mfe",
    ):
        same(summary[field], stats([e[field] for e in episodes if e[field] is not None]), field)
    bins = dict(at_least_75pct=0, **{"50_to_75pct": 0, "25_to_50pct": 0, "positive_below_25pct": 0, "nonpositive": 0})
    for e in positive:
        r = e["retention"]
        label = (
            "at_least_75pct"
            if r >= 0.75
            else "50_to_75pct"
            if r >= 0.5
            else "25_to_50pct"
            if r >= 0.25
            else "positive_below_25pct"
            if r > 0
            else "nonpositive"
        )
        bins[label] += 1
    for label, n in bins.items():
        same(
            summary["retention_bins"][label],
            dict(count=n, fraction=n / len(positive) if positive else None),
            f"bins.{label}",
        )
    for level, row in summary["profit_levels"].items():
        group = [e for e in episodes if any(p["pnl"] / e["capital"] >= float(level) for p in e["path"])]
        n = len(group)
        same(
            row,
            dict(
                count=n,
                mean_final_return=sum(e["final_return"] for e in group) / n if n else None,
                median_final_return=percentile([e["final_return"] for e in group], 0.5),
                profitable_fraction=sum(e["final_pnl"] > 0 for e in group) / n if n else None,
                nonpositive_fraction=sum(e["final_pnl"] <= 0 for e in group) / n if n else None,
                retention=stats([e["retention"] for e in group]),
                crossing_to_mfe_days=percentile([e["levels"][level]["crossing_to_mfe_days"] for e in group], 0.5),
                mfe_to_exit_days=percentile([e["mfe_to_exit_days"] for e in group], 0.5),
            ),
            f"levels.{level}",
        )
    winners = sorted([e for e in episodes if e["final_pnl"] > 0], key=lambda e: e["final_pnl"], reverse=True)
    positive_pnl = sum(e["final_pnl"] for e in winners)
    net = sum(e["final_pnl"] for e in episodes) if net_strategy_pnl is None else net_strategy_pnl
    for label, n in [("top1", 1), ("top3", 3), ("top5", 5), ("top10", 10), ("top10pct", math.ceil(len(episodes) / 10))]:
        chosen = winners[:n]
        pnl = sum(e["final_pnl"] for e in chosen)
        same(
            summary["right_tail"][label],
            dict(
                count=len(chosen),
                pnl=pnl,
                share_positive_pnl=pnl / positive_pnl if positive_pnl else None,
                share_net_strategy_pnl=pnl / net if net else None,
                episode_ids=[e["episode_id"] for e in chosen],
            ),
            f"right_tail.{label}",
        )
    return dict(
        status="FAIL" if errors else "PASS",
        episodes_checked=len(episodes),
        scope="threshold groups, distributions, retention bins and right-tail attribution",
        failures=errors,
    )
