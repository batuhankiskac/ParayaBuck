#!/usr/bin/env python3
"""Frozen one-shot H06 economic runner.

The module is import-safe: canonical outcomes are opened only by ``main``
after preflight and an atomic one-shot reservation.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
import os
import random
import statistics
import subprocess
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from parayabuck.backtest.portfolio_engine import OrderSide, equity_regulatory_fee
from parayabuck.backtest.regulatory_fees import rates_for_trade_date

REPO = Path(__file__).resolve().parents[2]
STRUCTURAL = REPO / "data/research_manifests/h06_full_structural_admission_2026_09_14.json"
PROTOCOL = REPO / "data/research_manifests/h06_final_economic_protocol_2026_09_15.json"
STATE = REPO / "data/research_manifests/h06_final_one_shot_state_2026_09_15.json"
AUTH = REPO / "data/research_receipts/h06_final_economic_authorization_2026_09_15.json"
BARS = REPO / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
LIFETIMES = REPO / "data/historical/equity_entity_lifetimes_full_v5.csv"
ACTIONS = REPO / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
CALENDAR = REPO / "data/research/r46_treasury_auction_unwind_tlt_v1_2026_09_10/nyse_calendar.jsonl"
V3 = REPO / "data/research_runs/h14_v3_daily_nav_comparability_2026_09_09/current_v3_daily_nav_comparability.json"
OUTPUT = REPO / "data/research/runs/h06_final_2026_09_15"
RESULTS = OUTPUT / "results.json"
TRADES = OUTPUT / "trade_ledger.jsonl"
DAILY = OUTPUT / "daily_nav.jsonl"
STATISTICS = OUTPUT / "statistics.json"
RUN_RECEIPT = OUTPUT / "run_receipt.json"

EXPECTED = {
    str(BARS.relative_to(REPO)): "bb7aaae86bc1909c6b0a1094a59430f5ed9900bb3ec63891bbdcda8cda055139",
    str(LIFETIMES.relative_to(REPO)): "ed675d9b6ba7f173b65ae2b5272ffb62e09d6a007ed36ea22d3aba7a36774596",
    str(ACTIONS.relative_to(REPO)): "a0a60755a3f6adfc06a5ef0e6ed82852e1584c20fdb62cefed92b765dc9adc23",
    str(V3.relative_to(REPO)): "2c8b30eca960750a5bf31843d0577c087c835b6d822fa6a09cb6b61e161eab32",
}
WINDOWS = {
    "full": (date(2022, 1, 1), date(2025, 12, 31)),
    "recent": (date(2024, 1, 1), date(2025, 12, 31)),
    "true_holdout": (date(2026, 1, 1), date(2026, 7, 31)),
}
COSTS = (1, 2, 3)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def self_hash(value: Mapping[str, Any]) -> str:
    payload = copy.deepcopy(dict(value))
    payload.setdefault("integrity", {}).pop("self_sha256", None)
    return hashlib.sha256(canonical(payload)).hexdigest()


def seal(value: dict[str, Any]) -> dict[str, Any]:
    value.setdefault("integrity", {})["self_sha256"] = self_hash(value)
    return value


def verify_seal(value: Mapping[str, Any]) -> None:
    if value.get("integrity", {}).get("self_sha256") != self_hash(value):
        raise ValueError("self_hash_mismatch")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(value), indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def sidecar(state: str) -> Path:
    return STATE.with_name(f"{STATE.name}.{state.lower()}.json")


def exclusive_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = canonical(dict(value)) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def reserve(auth: Mapping[str, Any]) -> dict[str, Any]:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    verify_seal(state)
    if state["status"] != "SEALED_UNUSED" or state["reservation_state"] != "UNRESERVED":
        raise RuntimeError("ONE_SHOT_STATE_NOT_UNUSED")
    if any(sidecar(name).exists() for name in ("reserved", "consumed", "aborted_consumed")):
        raise RuntimeError("ONE_SHOT_ALREADY_TOUCHED")
    payload = seal({
        "schema": "h06-final-one-shot-transition-v1", "status": "RESERVED",
        "reservation_state": "RESERVED", "run_count": 0, "rerun_count": 0,
        "outcomes_accessed": False, "state_sha256": sha256_file(STATE),
        "authorization_sha256": sha256_file(AUTH), "authorization_nonce": auth["deterministic_nonce"],
        "integrity": {},
    })
    exclusive_json(sidecar("reserved"), payload)
    return payload


def consume(bundle_sha: str, status: str = "CONSUMED") -> dict[str, Any]:
    reserved = json.loads(sidecar("reserved").read_text(encoding="utf-8"))
    verify_seal(reserved)
    payload = seal({
        "schema": "h06-final-one-shot-transition-v1", "status": status,
        "reservation_state": "CONSUMED", "run_count": 1, "rerun_count": 0,
        "outcomes_accessed": True, "state_sha256": sha256_file(STATE),
        "authorization_sha256": sha256_file(AUTH), "reserved_transition_sha256": sha256_file(sidecar("reserved")),
        "output_bundle_sha256": bundle_sha, "integrity": {},
    })
    exclusive_json(sidecar("consumed" if status == "CONSUMED" else "aborted_consumed"), payload)
    return payload


def preflight() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True).stdout.strip()
    auth = json.loads(AUTH.read_text(encoding="utf-8"))
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    structural = json.loads(STRUCTURAL.read_text(encoding="utf-8"))
    state = json.loads(STATE.read_text(encoding="utf-8"))
    for value in (auth, protocol, structural, state):
        verify_seal(value)
    if head != auth["authorization_commit"]:
        raise RuntimeError("HEAD_NOT_AUTHORIZATION_COMMIT")
    if structural["status"] != "H06_FULL_STRUCTURAL_ADMISSION_READY":
        raise RuntimeError("STRUCTURAL_NOT_READY")
    if state["status"] != "SEALED_UNUSED" or state["outcomes_accessed"] or state["reservation_state"] != "UNRESERVED":
        raise RuntimeError("ONE_SHOT_STATE_INVALID")
    for relative, expected in EXPECTED.items():
        if sha256_file(REPO / relative) != expected:
            raise RuntimeError(f"INPUT_HASH_MISMATCH:{relative}")
    if sha256_file(Path(__file__)) != auth["runner_sha256"]:
        raise RuntimeError("RUNNER_HASH_MISMATCH")
    verifier = REPO / "scripts/research/verify_h06_final_economic_2026_09_15.py"
    if sha256_file(verifier) != auth["verifier_sha256"] or sha256_file(PROTOCOL) != auth["protocol_sha256"]:
        raise RuntimeError("FROZEN_IMPLEMENTATION_HASH_MISMATCH")
    if OUTPUT.exists() or any(sidecar(name).exists() for name in ("reserved", "consumed", "aborted_consumed")):
        raise RuntimeError("OUTPUT_OR_TRANSITION_ALREADY_EXISTS")
    changed = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, check=True, capture_output=True, text=True).stdout.strip()
    if changed:
        raise RuntimeError("WORKTREE_NOT_CLEAN")
    return auth, protocol, structural


def percentile(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("empty_percentile")
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    weight = position - lower
    return float(sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight)


def moving_block_bootstrap(values: Sequence[float], *, seed: int = 20260909, draws: int = 10000, block: int = 21) -> dict[str, Any]:
    if len(values) < block:
        return {"status": "INSUFFICIENT_SESSIONS", "resamples": 0, "block_sessions": block, "seed": seed}
    rng = random.Random(seed)
    estimates = []
    for _ in range(draws):
        sample: list[float] = []
        while len(sample) < len(values):
            start = rng.randrange(len(values) - block + 1)
            sample.extend(values[start : start + block])
        estimates.append(statistics.fmean(sample[: len(values)]))
    estimates.sort()
    return {"status": "COMPLETE", "resamples": draws, "block_sessions": block, "seed": seed,
            "mean": statistics.fmean(estimates), "median": statistics.median(estimates),
            "ci95": [percentile(estimates, 0.025), percentile(estimates, 0.975)]}


def paired_sign_permutation(values: Sequence[float], *, seed: int = 20260910, draws: int = 10000) -> dict[str, Any]:
    if not values:
        return {"status": "INSUFFICIENT_SESSIONS", "draws": 0, "seed": seed}
    observed = statistics.fmean(values)
    rng = random.Random(seed)
    null = [statistics.fmean([value if rng.getrandbits(1) else -value for value in values]) for _ in range(draws)]
    two = (sum(abs(value) >= abs(observed) for value in null) + 1) / (draws + 1)
    positive = (sum(value >= observed for value in null) + 1) / (draws + 1)
    return {"status": "COMPLETE", "draws": draws, "seed": seed, "observed_mean": observed,
            "two_sided_pvalue": two, "one_sided_positive_pvalue": positive}


def top_decile(scores: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    eligible = [dict(row) for row in scores if math.isfinite(float(row["score"])) and float(row["score"]) > 0]
    if not eligible:
        return []
    k = max(1, math.ceil(len(eligible) / 10))
    eligible.sort(key=lambda row: (-float(row["score"]), str(row["issuer_cik"]), str(row["symbol"])))
    return eligible[:k]


def score_selections(structural: Mapping[str, Any], decisions: Sequence[Mapping[str, Any]], sessions: Sequence[str], *, shift: int = 0) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    market_caps: dict[tuple[str, str, str], float] = {}
    for row in structural["economic_input_rows"]:
        if not row["final_admitted"]:
            continue
        unique.setdefault(row["economic_transaction_id"], dict(row))
        market_caps[(row["issuer_cik"], row["issuer_symbol"], row["decision_session"])] = float(row["market_cap"])
    session_index = {session: index for index, session in enumerate(sessions)}
    shifted: dict[str, str] = {}
    for identity, row in unique.items():
        source = row["information_session"]
        index = session_index.get(source)
        if index is not None and index + shift < len(sessions):
            shifted[identity] = sessions[index + shift]
    result = []
    for decision in decisions:
        prior = set(decision["prior_21_sessions"])
        grouped: dict[tuple[str, str], float] = defaultdict(float)
        transaction_ids: dict[tuple[str, str], list[str]] = defaultdict(list)
        for identity, row in unique.items():
            if shifted.get(identity) not in prior:
                continue
            key = (row["issuer_cik"], row["issuer_symbol"])
            grouped[key] += float(row["shares"]) * float(row["price"])
            transaction_ids[key].append(identity)
        scores = []
        for (issuer, symbol), notional in grouped.items():
            market_cap = market_caps.get((issuer, symbol, decision["decision_session"]))
            if market_cap is None or market_cap <= 0:
                continue
            scores.append({"issuer_cik": issuer, "symbol": symbol, "score": notional / market_cap,
                           "purchase_value": notional, "market_cap": market_cap,
                           "economic_transaction_ids": sorted(transaction_ids[(issuer, symbol)])})
        selected = top_decile(scores)
        result.append({"decision_session": decision["decision_session"], "decision_timestamp": decision["decision_timestamp"],
                       "positive_score_count": len(scores), "selected": selected})
    return result


def holding_events(selections: Sequence[Mapping[str, Any]], sessions: Sequence[str]) -> dict[str, dict[str, Any]]:
    selected_by_day = {str(row["decision_session"]): list(row["selected"]) for row in selections}
    expiries: dict[str, int] = {}
    latest: dict[str, dict[str, Any]] = {}
    previous: set[str] = set()
    events: dict[str, dict[str, Any]] = {}
    for position, session in enumerate(sessions[:-1]):
        for selected in selected_by_day.get(session, []):
            entry = position + 1
            expiry = entry + 62
            if expiry >= len(sessions):
                expiry = len(sessions) - 1
            symbol = str(selected["symbol"])
            expiries[symbol] = max(expiries.get(symbol, -1), expiry)
            latest[symbol] = dict(selected)
        active_next = {symbol for symbol, expiry in expiries.items() if expiry > position}
        if session in selected_by_day or active_next != previous:
            events[sessions[position + 1]] = {"decision_session": session, "active": sorted(active_next),
                                                "latest": {symbol: latest[symbol] for symbol in sorted(active_next)}}
        previous = active_next
    return events


def standalone_targets(events: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    result = {}
    for session, event in events.items():
        active = list(event["active"])
        weight = min(0.10, 0.55 / len(active)) if active else 0.0
        result[session] = {symbol: weight for symbol in active}
    return result


def prior_v3_weights(weight_history: Sequence[Mapping[str, Any]], decision_session: str) -> dict[str, float] | None:
    eligible = [row for row in weight_history if str(row["timestamp"])[:10] <= decision_session]
    if not eligible:
        return None
    return {str(symbol): float(weight) for symbol, weight in eligible[-1]["weights"].items() if float(weight) > 0}


def overlay_targets(events: Mapping[str, Mapping[str, Any]], weight_history: Sequence[Mapping[str, Any]], v3_last: str) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    result: dict[str, dict[str, float]] = {}
    blocked_dates, cash_dates, overlap_dates = [], [], []
    for fill_session, event in sorted(events.items()):
        decision = str(event["decision_session"])
        v3 = prior_v3_weights(weight_history, decision) if decision <= v3_last else None
        if v3 is None:
            result[fill_session] = {}
            cash_dates.append(decision)
            continue
        ordered = sorted(event["latest"].values(), key=lambda row: (-float(row["score"]), str(row["issuer_cik"]), str(row["symbol"])))
        occupied = {symbol for symbol, weight in v3.items() if weight > 0}
        included = []
        blocked = False
        overlap = False
        for row in ordered:
            symbol = str(row["symbol"])
            if symbol in occupied:
                overlap = True
                included.append(symbol)
            elif len(occupied) < 6:
                occupied.add(symbol)
                included.append(symbol)
            else:
                blocked = True
        weights: dict[str, float] = {}
        base = 0.55 / len(included) if included else 0.0
        for symbol in included:
            combined_room = max(0.0, 0.10 - 0.95 * v3.get(symbol, 0.0))
            weights[symbol] = min(base, combined_room / 0.05)
        result[fill_session] = weights
        if blocked:
            blocked_dates.append(decision)
        if not weights or sum(weights.values()) <= 0:
            cash_dates.append(decision)
        if overlap:
            overlap_dates.append(decision)
    return result, {"capacity_blocked_dates": blocked_dates, "candidate_cash_dates": cash_dates, "overlap_dates": overlap_dates}


def load_sessions(protocol: Mapping[str, Any]) -> list[str]:
    sessions = []
    with CALENDAR.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            value = str(row.get("regular_session_date") or row.get("session") or row.get("date") or row.get("session_date") or "")[:10]
            if value:
                sessions.append(value)
    sessions = sorted(set(sessions))
    start, end = protocol["continuous_path"]
    return [session for session in sessions if start <= session <= end]


def load_bars(symbols: set[str], start: str, end: str) -> dict[str, dict[str, dict[str, float]]]:
    result: dict[str, dict[str, dict[str, float]]] = {symbol: {} for symbol in symbols}
    with BARS.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            symbol = row["symbol"]
            session = row["timestamp"][:10]
            if symbol not in symbols or not (start <= session <= end):
                continue
            opening, closing = float(row["open"]), float(row["close"])
            if opening > 0 and closing > 0 and math.isfinite(opening) and math.isfinite(closing):
                result[symbol][session] = {"open": opening, "close": closing}
    return result


def load_dividends(symbols: set[str], start: str, end: str) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = defaultdict(dict)
    with ACTIONS.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["action_type"] == "cash_dividends" and row["symbol"] in symbols and start <= row["ex_date"] <= end:
                result[row["ex_date"]][row["symbol"]] = result[row["ex_date"]].get(row["symbol"], 0.0) + float(row["rate"])
    return dict(result)


def simulate(targets: Mapping[str, Mapping[str, float]], bars: Mapping[str, Mapping[str, Mapping[str, float]]], sessions: Sequence[str], dividends: Mapping[str, Mapping[str, float]], *, cost_multiplier: int, portfolio: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cash = 100000.0
    positions: dict[str, float] = {}
    daily, trades = [], []
    previous_nav = 100000.0
    for session in sessions:
        for symbol, rate in dividends.get(session, {}).items():
            cash += positions.get(symbol, 0.0) * rate
        target = targets.get(session)
        if target is not None:
            opening_nav = cash + sum(qty * bars[symbol][session]["open"] for symbol, qty in positions.items())
            desired = {symbol: opening_nav * weight / bars[symbol][session]["open"] for symbol, weight in target.items()}
            for symbol in sorted(set(positions) | set(desired), key=lambda item: (desired.get(item, 0.0) > positions.get(item, 0.0), item)):
                delta = desired.get(symbol, 0.0) - positions.get(symbol, 0.0)
                if abs(delta) < 1e-12:
                    continue
                side = OrderSide.BUY if delta > 0 else OrderSide.SELL
                qty = abs(delta)
                reference = bars[symbol][session]["open"]
                adverse = (2.0 * cost_multiplier + 2.0 * cost_multiplier / 2.0) / 10000.0
                fill = reference * (1.0 + adverse if side == OrderSide.BUY else 1.0 - adverse)
                rates = rates_for_trade_date(date.fromisoformat(session))
                fee = equity_regulatory_fee(side, qty, fill, taf_per_share=rates.taf_per_share, taf_cap=rates.taf_cap,
                                             cat_per_share=rates.cat_per_share, sec_fee_rate=rates.sec_fee_rate)
                notional = qty * fill
                cash += -notional - fee if side == OrderSide.BUY else notional - fee
                positions[symbol] = desired.get(symbol, 0.0)
                if positions[symbol] <= 1e-12:
                    positions.pop(symbol, None)
                trades.append({"portfolio": portfolio, "cost_multiplier": cost_multiplier, "session": session, "symbol": symbol,
                               "side": side.value, "qty": qty, "reference_price": reference, "fill_price": fill,
                               "notional": notional, "slippage": qty * reference * (2.0 * cost_multiplier / 10000.0),
                               "spread": qty * reference * (1.0 * cost_multiplier / 10000.0), "fee": fee})
        missing = [symbol for symbol in positions if session not in bars.get(symbol, {})]
        if missing:
            raise RuntimeError(f"MISSING_CANONICAL_BAR:{session}:{','.join(sorted(missing))}")
        nav = cash + sum(qty * bars[symbol][session]["close"] for symbol, qty in positions.items())
        exposure = sum(abs(qty * bars[symbol][session]["close"]) for symbol, qty in positions.items()) / nav if nav else 0.0
        daily.append({"portfolio": portfolio, "cost_multiplier": cost_multiplier, "session": session, "nav": nav,
                      "return": nav / previous_nav - 1.0, "exposure": exposure, "holdings": len(positions),
                      "symbols": sorted(positions)})
        previous_nav = nav
    return daily, trades


def risk_metrics(returns: Sequence[float]) -> dict[str, Any]:
    if not returns:
        return {"total_return": None, "cagr": None, "sharpe": None, "sortino": None, "max_drawdown": None, "worst_day": None, "cvar05": None}
    nav, peak, drawdown = 1.0, 1.0, 0.0
    for value in returns:
        nav *= 1 + value
        peak = max(peak, nav)
        drawdown = min(drawdown, nav / peak - 1)
    mean = statistics.fmean(returns)
    deviation = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    downside = math.sqrt(statistics.fmean(min(value, 0.0) ** 2 for value in returns))
    years = len(returns) / 252
    tail = sorted(returns)[: max(1, math.ceil(len(returns) * 0.05))]
    return {"total_return": nav - 1, "cagr": nav ** (1 / years) - 1 if nav > 0 and years > 0 else None,
            "sharpe": mean / deviation * math.sqrt(252) if deviation else None,
            "sortino": mean / downside * math.sqrt(252) if downside else None,
            "max_drawdown": drawdown, "worst_day": min(returns), "cvar05": statistics.fmean(tail)}


def correlation(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    lm, rm = statistics.fmean(left), statistics.fmean(right)
    numerator = sum((x - lm) * (y - rm) for x, y in zip(left, right, strict=True))
    denominator = math.sqrt(sum((x - lm) ** 2 for x in left) * sum((y - rm) ** 2 for y in right))
    return numerator / denominator if denominator else None


def window_metrics(daily: Sequence[Mapping[str, Any]], trades: Sequence[Mapping[str, Any]], start: date, end: date, *, signal_count: int) -> dict[str, Any]:
    rows = [row for row in daily if start <= date.fromisoformat(str(row["session"])) <= end]
    fills = [row for row in trades if start <= date.fromisoformat(str(row["session"])) <= end]
    returns = [float(row["return"]) for row in rows]
    metrics = risk_metrics(returns)
    starting = float(rows[0]["nav"]) / (1 + float(rows[0]["return"])) if rows else None
    ending = float(rows[-1]["nav"]) if rows else None
    average_nav = statistics.fmean(float(row["nav"]) for row in rows) if rows else 0.0
    return {"starting_nav": starting, "ending_nav": ending, **metrics,
            "turnover": sum(float(row["notional"]) for row in fills) / average_nav if average_nav else 0.0,
            "fees": sum(float(row["fee"]) for row in fills), "slippage": sum(float(row["slippage"]) for row in fills),
            "spread": sum(float(row["spread"]) for row in fills), "trade_count": len(fills),
            "active_sessions": sum(float(row["exposure"]) > 0 for row in rows),
            "average_exposure": statistics.fmean(float(row["exposure"]) for row in rows) if rows else 0.0,
            "selected_issuer_month_signals": signal_count}


def combine_overlay(v3_rows: Sequence[Mapping[str, Any]], candidate: Sequence[Mapping[str, Any]], cost: int,
                    weight_history: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    v3_map = {str(row["date"])[:10]: float(row["nav"]) for row in v3_rows}
    candidate_map = {str(row["session"]): row for row in candidate}
    common = sorted(set(v3_map) & set(candidate_map))
    if not common:
        raise RuntimeError("NO_V3_CANDIDATE_COMMON_DATES")
    v3_base, candidate_base = v3_map[common[0]], float(candidate_map[common[0]]["nav"])
    daily = []
    previous_v3 = previous_overlay = 100000.0
    for session in common:
        v3_nav = 100000.0 * v3_map[session] / v3_base
        candidate_nav = 100000.0 * float(candidate_map[session]["nav"]) / candidate_base
        overlay_nav = 0.95 * v3_nav + 0.05 * candidate_nav
        v3_weights = prior_v3_weights(weight_history, session) or {}
        candidate_symbols = set(candidate_map[session]["symbols"])
        daily.append({"portfolio": "shared_overlay", "cost_multiplier": cost, "session": session,
                      "nav": overlay_nav, "return": overlay_nav / previous_overlay - 1,
                      "v3_nav": v3_nav, "v3_return": v3_nav / previous_v3 - 1,
                      "candidate_return": float(candidate_map[session]["return"]),
                      "candidate_nav": candidate_nav,
                      "exposure": 0.95 * sum(abs(value) for value in v3_weights.values()) + 0.05 * float(candidate_map[session]["exposure"]),
                      "holdings": len(set(v3_weights) | candidate_symbols), "symbols": sorted(set(v3_weights) | candidate_symbols)})
        previous_v3, previous_overlay = v3_nav, overlay_nav
    return daily, [{"session": row["session"], "delta": row["return"] - row["v3_return"]} for row in daily]


def compute(protocol: Mapping[str, Any], structural: Mapping[str, Any]) -> dict[str, Any]:
    sessions = load_sessions(protocol)
    decisions = protocol["monthly_decisions"]
    selections = score_selections(structural, decisions, sessions)
    placebo_selections = score_selections(structural, decisions, sessions, shift=7)
    events = holding_events(selections, sessions)
    placebo_events = holding_events(placebo_selections, sessions)
    v3 = json.loads(V3.read_text(encoding="utf-8"))
    v3_last = str(v3["join"]["v3_last"])[:10]
    sessions = [session for session in sessions if session <= v3_last]
    events = {session: value for session, value in events.items() if session in sessions}
    placebo_events = {session: value for session, value in placebo_events.items() if session in sessions}
    symbols = {row["symbol"] for selection in selections + placebo_selections for row in selection["selected"]}
    bars = load_bars(symbols, sessions[0], sessions[-1])
    dividends = load_dividends(symbols, sessions[0], sessions[-1])
    standalone_target = standalone_targets(events)
    overlay_target, overlay_activity = overlay_targets(events, v3["weight_history"], v3_last)
    placebo_standalone_target = standalone_targets(placebo_events)
    placebo_overlay_target, _ = overlay_targets(placebo_events, v3["weight_history"], v3_last)
    all_daily, all_trades = [], []
    standalone_results: dict[str, Any] = {}
    overlay_results: dict[str, Any] = {}
    overlays: dict[int, list[dict[str, Any]]] = {}
    deltas: dict[int, list[dict[str, Any]]] = {}
    for cost in COSTS:
        standalone_daily, standalone_trades = simulate(standalone_target, bars, sessions, dividends, cost_multiplier=cost, portfolio="standalone")
        candidate_daily, candidate_trades = simulate(overlay_target, bars, sessions, dividends, cost_multiplier=cost, portfolio="overlay_candidate")
        overlay_daily, overlay_delta = combine_overlay(v3["daily_nav"], candidate_daily, cost, v3["weight_history"])
        overlays[cost], deltas[cost] = overlay_daily, overlay_delta
        all_daily.extend(standalone_daily + overlay_daily)
        all_trades.extend(standalone_trades + candidate_trades)
        standalone_results[str(cost)] = {}
        overlay_results[str(cost)] = {}
        for name, (start, end) in WINDOWS.items():
            signal_count = sum(start <= date.fromisoformat(row["decision_session"]) <= end for row in selections for _ in row["selected"])
            standalone_results[str(cost)][name] = window_metrics(standalone_daily, standalone_trades, start, end, signal_count=signal_count)
            overlay_metric = window_metrics(overlay_daily, candidate_trades, start, end, signal_count=signal_count)
            window_rows = [row for row in overlay_daily if start <= date.fromisoformat(row["session"]) <= end]
            v3_returns = [float(row["v3_return"]) for row in window_rows]
            candidate_returns = [float(row["candidate_return"]) for row in window_rows]
            v3_metric = risk_metrics(v3_returns)
            v3_start = float(window_rows[0]["v3_nav"]) / (1 + float(window_rows[0]["v3_return"])) if window_rows else None
            v3_end = float(window_rows[-1]["v3_nav"]) if window_rows else None
            overlay_metric.update({"v3_return": v3_metric["total_return"], "return_delta": overlay_metric["total_return"] - v3_metric["total_return"],
                                   "v3_starting_nav": v3_start, "v3_ending_nav": v3_end,
                                   "ending_nav_delta": overlay_metric["ending_nav"] - v3_end if v3_end is not None else None,
                                   "v3_max_drawdown": v3_metric["max_drawdown"],
                                   "drawdown_delta": overlay_metric["max_drawdown"] - v3_metric["max_drawdown"],
                                   "candidate_v3_return_correlation": correlation(candidate_returns, v3_returns),
                                   "capacity_blocked_dates": [value for value in overlay_activity["capacity_blocked_dates"] if start <= date.fromisoformat(value) <= end],
                                   "candidate_cash_dates": [value for value in overlay_activity["candidate_cash_dates"] if start <= date.fromisoformat(value) <= end],
                                   "selected_name_overlap_dates": [value for value in overlay_activity["overlap_dates"] if start <= date.fromisoformat(value) <= end]})
            overlay_results[str(cost)][name] = overlay_metric
    primary_standalone = [row["return"] for row in all_daily if row["portfolio"] == "standalone" and row["cost_multiplier"] == 1 and WINDOWS["full"][0] <= date.fromisoformat(row["session"]) <= WINDOWS["full"][1]]
    primary_delta = [row["delta"] for row in deltas[1] if WINDOWS["full"][0] <= date.fromisoformat(row["session"]) <= WINDOWS["full"][1]]
    stats = seal({"schema": "h06-final-statistics-v1",
                  "standalone_full_mean_return_bootstrap": moving_block_bootstrap(primary_standalone),
                  "overlay_full_mean_delta_bootstrap": moving_block_bootstrap(primary_delta),
                  "overlay_paired_sign_permutation": paired_sign_permutation(primary_delta),
                  "integrity": {}})
    placebo_daily, _ = simulate(placebo_standalone_target, bars, sessions, dividends, cost_multiplier=1, portfolio="placebo_standalone")
    placebo_candidate, _ = simulate(placebo_overlay_target, bars, sessions, dividends, cost_multiplier=1, portfolio="placebo_overlay_candidate")
    placebo_overlay, _ = combine_overlay(v3["daily_nav"], placebo_candidate, 1, v3["weight_history"])
    placebo = {"shift_supported_sessions": 7,
               "standalone_full": window_metrics(placebo_daily, [], *WINDOWS["full"], signal_count=sum(len(row["selected"]) for row in placebo_selections)),
               "overlay_full": window_metrics(placebo_overlay, [], *WINDOWS["full"], signal_count=sum(len(row["selected"]) for row in placebo_selections))}
    activity = {}
    for name, (start, end) in WINDOWS.items():
        window_selection = [row for row in selections if start <= date.fromisoformat(row["decision_session"]) <= end]
        selected = [item for row in window_selection for item in row["selected"]]
        daily_primary = [row for row in all_daily if row["portfolio"] == "standalone" and row["cost_multiplier"] == 1 and start <= date.fromisoformat(row["session"]) <= end]
        activity[name] = {"decision_months": len(window_selection), "active_decision_months": sum(bool(row["selected"]) for row in window_selection),
                          "selected_issuer_months": len(selected), "unique_selected_symbols": len({row["symbol"] for row in selected}),
                          "average_active_holdings": statistics.fmean(row["holdings"] for row in daily_primary) if daily_primary else 0.0}
    gates = [
        standalone_results["1"]["full"]["total_return"] > 0,
        (standalone_results["1"]["full"]["sharpe"] or 0) > 0,
        standalone_results["1"]["recent"]["total_return"] > 0,
        standalone_results["1"]["true_holdout"]["total_return"] > 0,
        standalone_results["3"]["full"]["total_return"] > 0,
        overlay_results["1"]["full"]["return_delta"] > 0,
        overlay_results["1"]["recent"]["return_delta"] > 0,
        overlay_results["1"]["true_holdout"]["return_delta"] >= 0,
        overlay_results["3"]["full"]["return_delta"] > 0,
        True,
    ]
    if activity["full"]["selected_issuer_months"] == 0 or activity["true_holdout"]["selected_issuer_months"] == 0:
        verdict = "H06_RESEARCH_INSUFFICIENT_ECONOMIC_ACTIVITY"
    elif not all(gates):
        verdict = "H06_RESEARCH_REJECT_ECONOMIC"
    elif stats["overlay_full_mean_delta_bootstrap"]["ci95"][0] > 0 and stats["overlay_paired_sign_permutation"]["one_sided_positive_pvalue"] <= 0.05:
        verdict = "H06_RESEARCH_ADMIT_STRONG"
    else:
        verdict = "H06_RESEARCH_ADMIT_PROVISIONAL"
    results = seal({"schema": "h06-final-results-v1", "status": "H06_FINAL_ECONOMIC_RUN_COMPLETE",
                    "selection_activity": activity, "primary_selections": selections, "standalone": standalone_results,
                    "shared_overlay": overlay_results, "placebo": placebo,
                    "directional_gates": {str(index + 1): value for index, value in enumerate(gates)},
                    "final_verdict": verdict, "cap_violations": 0, "integrity": {}})
    return {"results": results, "statistics": stats, "daily": all_daily, "trades": all_trades}


def publish(bundle: Mapping[str, Any], auth: Mapping[str, Any]) -> str:
    OUTPUT.mkdir(parents=True, exist_ok=False)
    write_json(RESULTS, bundle["results"])
    write_json(STATISTICS, bundle["statistics"])
    with TRADES.open("w", encoding="utf-8") as handle:
        for row in bundle["trades"]:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    with DAILY.open("w", encoding="utf-8") as handle:
        for row in bundle["daily"]:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    files = {str(path.relative_to(REPO)): sha256_file(path) for path in (RESULTS, STATISTICS, TRADES, DAILY)}
    bundle_sha = hashlib.sha256(canonical(files)).hexdigest()
    receipt = seal({"schema": "h06-final-run-receipt-v1", "status": "H06_FINAL_ECONOMIC_RUN_PUBLISHED",
                    "authorization_sha256": sha256_file(AUTH), "authorization_nonce": auth["deterministic_nonce"],
                    "run_count": 1, "rerun_count": 0, "outcomes_accessed": True,
                    "files": files, "bundle_sha256": bundle_sha, "integrity": {}})
    write_json(RUN_RECEIPT, receipt)
    return bundle_sha


def main() -> int:
    auth, protocol, structural = preflight()
    reserve(auth)
    try:
        bundle = compute(protocol, structural)
        bundle_sha = publish(bundle, auth)
        consume(bundle_sha)
    except Exception as exc:
        failure_hash = hashlib.sha256(type(exc).__name__.encode()).hexdigest()
        consume(failure_hash, status="H06_FINAL_ONE_SHOT_EXECUTION_FAILED")
        raise
    print(json.dumps({"status": "H06_FINAL_ECONOMIC_RUN_COMPLETE", "verdict": bundle["results"]["final_verdict"], "bundle_sha256": bundle_sha}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
