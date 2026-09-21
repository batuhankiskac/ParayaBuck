"""Independent verifier for the one frozen abnormal-volume economic run.

This module deliberately does not import ``run_economic_v1``.  It consumes
the runner's serialized output, the frozen G1 evidence, and the canonical raw
inputs, then rebuilds the target, execution, ledger, metric, and G2--G9 paths.
The verifier is fail closed: absent evidence is a verification failure rather
than an invitation to infer a favourable value.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pandas_market_calendars import get_calendar

from parayabuck.config import load_config
from parayabuck.execution.portfolio import PortfolioExecutionPolicy, portfolio_risk_limits_from_config

ROOT = Path(__file__).resolve().parents[2]
ECONOMIC_DIR = ROOT / "research" / "abnormal_volume_economic_v1"
RUNNER_PATH = ECONOMIC_DIR / "RUNNER_ECONOMIC_OUTPUT.json"
OUTPUT_PATH = ECONOMIC_DIR / "INDEPENDENT_ECONOMIC_VERIFICATION.json"
RECEIPT_PATH = ECONOMIC_DIR / "FROZEN_ECONOMIC_INPUT_RECEIPT.json"
READINESS_PATH = ROOT / "research" / "abnormal_volume_readiness_v2" / "RUNNER_OUTPUT.json"
CONTROL_PATH = ECONOMIC_DIR / "CONTROL_A_RESULTS.json"
CONTROL_COMPARISON_PATH = ECONOMIC_DIR / "CONTROL_A_PARITY.json"
CONFIG_PATH = ROOT / "config" / "settings.yaml"

CANDIDATE = "AV49_D1_NORMALRET_M1_LONG / DESIGN_V2"
EXPECTED_SCHEMA = "abnormal-volume-economic-runner-v1"
ENDPOINT = date(2026, 7, 30)
DEVELOPMENT_START = date(2017, 1, 3)
DEVELOPMENT_END = date(2021, 12, 31)
VALIDATION_START = date(2022, 1, 3)
RESERVED = frozenset({"SPY", "SH", "PSQ", "DOG"})
NY = ZoneInfo("America/New_York")

REL_TOL = 1e-9
ABS_TOL = 1e-7
BASE_SLIPPAGE_BPS = 2.0
BASE_SPREAD_BPS = 2.0
MAX_PARTICIPATION = 0.10
LIQUIDITY_LOOKBACK = 20
REBALANCE_BAND = 0.005
MINIMUM_ORDER_NOTIONAL = 10.0


class VerificationError(ValueError):
    """A required persisted or canonical fact cannot be verified."""


@dataclass(frozen=True)
class RawBar:
    timestamp: datetime
    session: date
    symbol: str
    entity_id: str | None
    open: float
    close: float
    volume: float


@dataclass
class Position:
    qty: float
    average_price: float
    entry_fee: float
    realized_accumulator: float = 0.0


@dataclass(frozen=True)
class Lifetime:
    entity_id: str
    canonical_symbol: str
    valid_from: date
    valid_to: date | None
    last_observed: date
    status: str
    terminal_event_class: str | None
    unresolved: bool
    aliases: tuple[tuple[str, date | None, date | None], ...]

    def active(self, session: date) -> bool:
        return self.valid_from <= session and (self.valid_to is None or session <= self.valid_to)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise VerificationError(f"{path}: expected a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _number(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise VerificationError(f"{label}: expected a finite number") from exc
    if not math.isfinite(result):
        raise VerificationError(f"{label}: expected a finite number")
    return result


def _decimal(value: Any, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:  # Decimal raises several input-specific subclasses.
        raise VerificationError(f"{label}: expected a finite decimal") from exc
    if not result.is_finite():
        raise VerificationError(f"{label}: expected a finite decimal")
    return result


def _timestamp(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise VerificationError(f"{label}: invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise VerificationError(f"{label}: timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _close(left: Any, right: Any) -> bool:
    return math.isclose(_number(left, "left"), _number(right, "right"), rel_tol=REL_TOL, abs_tol=ABS_TOL)


def _check_close(issues: list[str], label: str, actual: Any, expected: Any) -> None:
    if not _close(actual, expected):
        issues.append(f"{label}: runner={actual!r} verifier={expected!r}")


def _average_rank(values: Sequence[Decimal], selected: Decimal) -> Decimal:
    lower = sum(value < selected for value in values)
    equal = sum(value == selected for value in values)
    if equal == 0:
        raise VerificationError("selected value is absent from rank multiset")
    return Decimal(lower + 1) + Decimal(equal - 1) / Decimal(2)


def _read_lifetimes(path: Path) -> tuple[dict[str, Lifetime], dict[str, list[Lifetime]]]:
    by_entity: dict[str, Lifetime] = {}
    by_symbol: dict[str, list[Lifetime]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row_number, row in enumerate(reader, start=2):
            entity_id = str(row.get("entity_id") or "").strip()
            canonical = str(row.get("canonical_symbol") or "").strip().upper()
            if not entity_id or not canonical:
                raise VerificationError(f"lifetimes row {row_number}: missing identity")
            valid_from = _timestamp(row["valid_from"], "valid_from").astimezone(NY).date()
            valid_to = _timestamp(row["valid_to"], "valid_to").astimezone(NY).date() if row.get("valid_to") else None
            last_observed = _timestamp(row["last_observed_bar"], "last_observed_bar").astimezone(NY).date()
            status = str(row.get("current_status") or "").strip().lower()
            terminal = str(row.get("terminal_event_class") or "").strip() or None
            unresolved = status not in {"active", "inactive"}
            if status == "active":
                unresolved = unresolved or valid_to is not None or terminal is not None
            else:
                unresolved = unresolved or valid_to is None or terminal is None or valid_to != last_observed
            aliases: list[tuple[str, date | None, date | None]] = []
            raw_chain = str(row.get("rename_chain") or "").strip()
            if raw_chain:
                try:
                    chain = json.loads(raw_chain)
                except json.JSONDecodeError as exc:
                    raise VerificationError(f"lifetimes row {row_number}: malformed rename_chain") from exc
                for item in chain:
                    aliases.append(
                        (
                            str(item["symbol"]).strip().upper(),
                            date.fromisoformat(item["valid_from"]) if item.get("valid_from") else None,
                            date.fromisoformat(item["valid_to"]) if item.get("valid_to") else None,
                        )
                    )
            if not aliases:
                aliases.append((canonical, valid_from, valid_to))
            lifetime = Lifetime(
                entity_id,
                canonical,
                valid_from,
                valid_to,
                last_observed,
                status,
                terminal,
                unresolved,
                tuple(aliases),
            )
            if entity_id in by_entity:
                raise VerificationError(f"duplicate lifetime entity_id {entity_id}")
            by_entity[entity_id] = lifetime
            for symbol, _start, _end in aliases:
                by_symbol[symbol].append(lifetime)
            if canonical not in {alias[0] for alias in aliases}:
                by_symbol[canonical].append(lifetime)
    return by_entity, by_symbol


def _resolve_entity(by_symbol: dict[str, list[Lifetime]], symbol: str, session: date) -> str | None:
    matches: list[Lifetime] = []
    for lifetime in by_symbol.get(symbol.upper(), ()):
        for alias, start, end in lifetime.aliases:
            if alias == symbol.upper() and (start is None or session >= start) and (end is None or session <= end):
                matches.append(lifetime)
                break
    unique = {item.entity_id for item in matches}
    return next(iter(unique)) if len(unique) == 1 else None


def _needed_identities(readiness: dict[str, Any], runner: dict[str, Any]) -> tuple[set[str], set[str]]:
    entities: set[str] = set()
    symbols: set[str] = set()
    for decision in readiness.get("decisions", []):
        for target in decision.get("targets", []):
            entities.add(str(target["entity_id"]))
            symbols.add(str(target["symbol"]).upper())
    scenarios = runner.get("candidate_results", {}).get("cost_scenarios", {})
    for run in scenarios.values():
        for fill in run.get("fills", []):
            symbols.add(str(fill["symbol"]).upper())
            if fill.get("entity_id"):
                entities.add(str(fill["entity_id"]))
        for event in run.get("terminal_events", []):
            symbols.add(str(event["symbol"]).upper())
            if event.get("entity_id"):
                entities.add(str(event["entity_id"]))
    return entities, symbols


def _read_relevant_bars(
    path: Path,
    needed_entities: set[str],
    needed_symbols: set[str],
    by_symbol: dict[str, list[Lifetime]],
) -> tuple[dict[str, dict[date, RawBar]], dict[str, dict[date, RawBar]], int]:
    by_entity: dict[str, dict[date, RawBar]] = defaultdict(dict)
    by_raw_symbol: dict[str, dict[date, RawBar]] = defaultdict(dict)
    rows_through_endpoint = 0
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        expected = ("timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume", "market")
        if tuple(reader.fieldnames or ()) != expected:
            raise VerificationError("canonical bars schema mismatch")
        for row_number, row in enumerate(reader, start=2):
            timestamp = _timestamp(row["timestamp"], f"bars row {row_number} timestamp")
            session = timestamp.astimezone(NY).date()
            if session > ENDPOINT:
                break
            rows_through_endpoint += 1
            symbol = str(row["symbol"]).strip().upper()
            entity_id = _resolve_entity(by_symbol, symbol, session)
            if symbol not in needed_symbols and entity_id not in needed_entities:
                continue
            bar = RawBar(
                timestamp,
                session,
                symbol,
                entity_id,
                _number(row["open"], "open"),
                _number(row["close"], "close"),
                _number(row["volume"], "volume"),
            )
            if bar.open <= 0 or bar.close <= 0 or bar.volume < 0:
                raise VerificationError(f"bars row {row_number}: invalid relevant bar")
            if session in by_raw_symbol[symbol]:
                raise VerificationError(f"duplicate raw symbol/session {symbol} {session}")
            by_raw_symbol[symbol][session] = bar
            if entity_id in needed_entities:
                if session in by_entity[entity_id]:
                    raise VerificationError(f"duplicate entity/session {entity_id} {session}")
                by_entity[entity_id][session] = bar
    return by_entity, by_raw_symbol, rows_through_endpoint


def _read_cash_actions(path: Path) -> dict[tuple[str, date, float], str]:
    actions: dict[tuple[str, date, float], str] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), start=2):
            if str(row.get("action_type") or "").lower() != "cash_dividends":
                continue
            key = (
                str(row["symbol"]).strip().upper(),
                date.fromisoformat(str(row["ex_date"])),
                _number(row["rate"], f"actions row {row_number} rate"),
            )
            actions.setdefault(key, str(row["raw_id"]))
    return actions


def _calendar_windows() -> dict[date, tuple[date, datetime, datetime]]:
    schedule = get_calendar("NYSE").schedule(start_date="2016-12-01", end_date=ENDPOINT)
    grouped: dict[tuple[int, int], list[Any]] = defaultdict(list)
    for index, row in schedule.iterrows():
        grouped[(index.year, index.month)].append((index.date(), row))
    windows: dict[date, tuple[date, datetime, datetime]] = {}
    keys = sorted(grouped)
    for current, following in zip(keys, keys[1:]):
        if following != ((current[0] + 1, 1) if current[1] == 12 else (current[0], current[1] + 1)):
            continue
        decision_session, decision_row = grouped[current][-1]
        fill_session, fill_row = grouped[following][0]
        windows[decision_session] = (
            fill_session,
            decision_row["market_close"].to_pydatetime().astimezone(UTC),
            fill_row["market_open"].to_pydatetime().astimezone(UTC),
        )
    return windows


def _verify_readiness_signal(
    readiness: dict[str, Any],
    bars_by_entity: dict[str, dict[date, RawBar]],
    issues: list[str],
) -> dict[str, int]:
    checked = 0
    boundary_checked = 0
    for decision_index, decision in enumerate(readiness.get("decisions", [])):
        decision_session = date.fromisoformat(decision["decision_session"])
        eligible = sorted(
            decision.get("eligible", []),
            key=lambda row: (
                -_decimal(row["volume_rank"], "volume_rank"),
                -_decimal(row["intensity"], "intensity"),
                row["entity_id"],
            ),
        )
        expected_target_ids = [row["entity_id"] for row in eligible[:6]]
        actual_target_ids = [row["entity_id"] for row in decision.get("targets", [])]
        if actual_target_ids != expected_target_ids:
            issues.append(f"readiness decision {decision_session}: top-six ordering mismatch")
        for rank, target in enumerate(decision.get("targets", []), start=1):
            checked += 1
            entity_id = str(target["entity_id"])
            sessions = [date.fromisoformat(value) for value in target["reference_sessions"]]
            if len(sessions) != 49 or sessions != sorted(set(sessions)) or sessions[-1] >= decision_session:
                issues.append(f"{decision_session} {entity_id}: invalid exact prior-49 session evidence")
                continue
            history = bars_by_entity.get(entity_id, {})
            try:
                reference = [
                    Decimal(str(history[session].close)) * Decimal(str(history[session].volume)) for session in sessions
                ]
                formation = history[decision_session]
                chronological = sorted(history)
                first_index = chronological.index(sessions[0])
                preceding_session = chronological[first_index - 1]
                return_sessions = [preceding_session, *sessions, decision_session]
                closes = [Decimal(str(history[session].close)) for session in return_sessions]
            except (KeyError, ValueError, IndexError):
                issues.append(f"{decision_session} {entity_id}: raw history cannot reproduce signal")
                continue
            persisted_reference = [
                _decimal(value, "reference_dollar_volume") for value in target["reference_dollar_volumes"]
            ]
            if reference != persisted_reference:
                issues.append(f"{decision_session} {entity_id}: dollar-volume evidence mismatch")
            sorted_reference = sorted(reference)
            if sorted_reference != [
                _decimal(value, "sorted_reference") for value in target["sorted_reference_dollar_volumes"]
            ]:
                issues.append(f"{decision_session} {entity_id}: sorted reference multiset mismatch")
            q90 = sorted_reference[43]
            formation_dv = Decimal(str(formation.close)) * Decimal(str(formation.volume))
            with localcontext() as context:
                context.prec = 50
                intensity = formation_dv / q90
                returns = [closes[index] / closes[index - 1] - Decimal(1) for index in range(1, len(closes))]
            volume_rank = _average_rank([*reference, formation_dv], formation_dv)
            return_rank = _average_rank(returns, returns[-1])
            for label, actual, expected in (
                ("q90", target["q90"], q90),
                ("formation_dollar_volume", target["formation_dollar_volume"], formation_dv),
                ("intensity", target["intensity"], intensity),
                ("formation_return", target["formation_return"], returns[-1]),
                ("volume_rank", target["volume_rank"], volume_rank),
                ("return_rank", target["return_rank"], return_rank),
            ):
                if _decimal(actual, label) != expected:
                    issues.append(f"{decision_session} {entity_id}: {label} mismatch")
            if volume_rank < 46 or not Decimal(16) <= return_rank <= Decimal(35):
                issues.append(f"{decision_session} {entity_id}: target violates frozen eligibility")
            if int(target["candidate_rank"]) != rank:
                issues.append(f"{decision_session} {entity_id}: candidate rank mismatch")
            count = len(decision.get("targets", []))
            expected_weight = min(Decimal("0.10"), Decimal("0.55") / Decimal(count)) if count else Decimal(0)
            if _decimal(target["target_weight"], "target_weight") != expected_weight:
                issues.append(f"{decision_session} {entity_id}: frozen weight mismatch")
            if decision_index in {0, len(readiness["decisions"]) - 1} or decision_session.year in {2021, 2022}:
                boundary_checked += 1
    return {"targets_reconstructed_from_raw": checked, "boundary_targets_reconstructed": boundary_checked}


def _verify_timing_and_targets(
    readiness: dict[str, Any],
    scenarios: dict[str, Any],
    issues: list[str],
) -> dict[str, int]:
    windows = _calendar_windows()
    expected: dict[date, tuple[datetime, datetime, date, list[tuple[str, float]]]] = {}
    for decision in readiness.get("decisions", []):
        session = date.fromisoformat(decision["decision_session"])
        if session not in windows:
            issues.append(f"{session}: no independent exchange-calendar window")
            continue
        fill_session, close_ts, open_ts = windows[session]
        if (
            decision["fill_session"] != fill_session.isoformat()
            or _timestamp(decision["decision_timestamp"], "decision_timestamp") != close_ts
            or _timestamp(decision["fill_timestamp"], "fill_timestamp") != open_ts
            or not close_ts < open_ts
        ):
            issues.append(f"{session}: month-end/first-open timing mismatch")
        expected[session] = (
            close_ts,
            open_ts,
            fill_session,
            [
                (str(target["symbol"]), float(target["target_weight"]))
                for target in decision.get("targets", [])
                if target.get("fill_available")
            ],
        )
    baseline: list[Any] | None = None
    for multiplier, run in sorted(scenarios.items()):
        decisions = run.get("decisions", [])
        normalized: list[Any] = []
        for row in decisions:
            engine_ts = _timestamp(row["decision_timestamp"], "economic decision timestamp")
            session = (
                date.fromisoformat(row["decision_session"])
                if row.get("decision_session")
                else engine_ts.astimezone(NY).date()
            )
            actual: list[tuple[str, float]] = [
                (str(symbol), _number(weight, "target weight")) for symbol, weight in row["target_weights"]
            ]
            contract = expected.get(session)
            if contract is None:
                issues.append(f"{multiplier}x {engine_ts.isoformat()}: unexpected economic decision")
                continue
            close_ts, open_ts, fill_session, exp = contract
            if not row.get("contract_decision_timestamp") or not row.get("contract_fill_timestamp"):
                issues.append(f"{multiplier}x {session}: missing explicit contract timestamps")
            else:
                if _timestamp(row["contract_decision_timestamp"], "contract decision") != close_ts:
                    issues.append(f"{multiplier}x {session}: contract decision-close mismatch")
                if _timestamp(row["contract_fill_timestamp"], "contract fill") != open_ts:
                    issues.append(f"{multiplier}x {session}: contract first-open mismatch")
            if row.get("fill_session") != fill_session.isoformat():
                issues.append(f"{multiplier}x {session}: persisted first-open session mismatch")
            if len(actual) != len(exp) or any(
                a[0] != e[0] or not _close(a[1], e[1]) for a, e in zip(actual, exp, strict=False)
            ):
                issues.append(f"{multiplier}x {session}: economic targets differ from readiness")
            if len(actual) > 6 or any(symbol in RESERVED for symbol, _weight in actual):
                issues.append(f"{multiplier}x {session}: slot or reserved-role breach")
            total = sum(weight for _symbol, weight in actual)
            if total > 0.55 + ABS_TOL or any(weight > 0.10 + ABS_TOL or weight < 0 for _symbol, weight in actual):
                issues.append(f"{multiplier}x {session}: frozen allocation breach")
            _check_close(issues, f"{multiplier}x {session} cash target", row["target_cash_weight"], 1.0 - total)
            normalized.append([session.isoformat(), actual])
        if baseline is None:
            baseline = normalized
        elif normalized != baseline:
            issues.append(f"{multiplier}x target generation changed across cost cells")
    return {"scheduled_decisions": len(expected), "cost_cells": len(scenarios)}


def _rates(trade_date: date) -> tuple[float, float, float, float]:
    taf_schedule = (
        (date(2013, 1, 1), 0.000119, 5.95),
        (date(2022, 1, 1), 0.000130, 6.49),
        (date(2023, 1, 1), 0.000145, 7.27),
        (date(2024, 1, 1), 0.000166, 8.30),
        (date(2026, 1, 1), 0.000195, 9.79),
    )
    sec_schedule = (
        (date(2020, 2, 18), 0.0000221),
        (date(2021, 2, 25), 0.0000051),
        (date(2022, 5, 14), 0.0000229),
        (date(2024, 2, 16), 0.0000080),
        (date(2024, 5, 22), 0.0000278),
        (date(2025, 5, 14), 0.0),
        (date(2026, 4, 4), 0.0000206),
    )
    cat_schedule = (
        (date(2024, 9, 1), 0.000035),
        (date(2024, 10, 1), 0.000048),
        (date(2025, 1, 1), 0.000035),
        (date(2025, 7, 1), 0.000022),
        (date(2025, 12, 1), 0.0),
        (date(2026, 5, 1), 0.000003),
    )
    taf, cap = taf_schedule[0][1:]
    for effective, value, maximum in taf_schedule:
        if trade_date >= effective:
            taf, cap = value, maximum
    sec = 0.0000207
    for effective, value in sec_schedule:
        if trade_date >= effective:
            sec = value
    cat = 0.0
    for effective, value in cat_schedule:
        if trade_date >= effective:
            cat = value
    return taf, cap, cat, sec


def _fee(side: str, qty: float, price: float, trade_date: date, multiplier: float) -> float:
    taf_rate, taf_cap, cat_rate, sec_rate = _rates(trade_date)
    value = qty * cat_rate
    if side == "sell":
        value += min(qty * taf_rate, taf_cap) + qty * price * sec_rate
    return value * multiplier


def _verify_costs(run: dict[str, Any], multiplier: float, issues: list[str]) -> dict[str, float]:
    slippage = spread = fees = notional = 0.0
    previous: tuple[datetime, str, str] | None = None
    for index, fill in enumerate(run.get("fills", [])):
        ts = _timestamp(fill["timestamp"], "fill timestamp")
        decision = _timestamp(fill["decision_timestamp"], "fill decision timestamp")
        symbol = str(fill["symbol"])
        side = str(fill["side"]).lower()
        contracted_fill_session = date.fromisoformat(str(fill["fill_session"]))
        if ts.astimezone(NY).date() != contracted_fill_session:
            issues.append(f"{multiplier}x fill {index}: execution missed contracted first-open session")
        if side not in {"buy", "sell"} or not decision < ts:
            issues.append(f"{multiplier}x fill {index}: side or chronology invalid")
        order = (ts, symbol, side)
        if previous is not None and order < previous:
            issues.append(f"{multiplier}x fills are not deterministically ordered")
        previous = order
        qty = _number(fill["qty"], "fill qty")
        reference = _number(fill["reference_price"], "fill reference")
        actual_price = _number(fill["fill_price"], "fill price")
        if qty <= 0 or reference <= 0:
            issues.append(f"{multiplier}x fill {index}: nonpositive quantity/reference")
            continue
        adverse = (BASE_SLIPPAGE_BPS + BASE_SPREAD_BPS / 2) * multiplier
        expected_price = reference * (1 + (1 if side == "buy" else -1) * adverse / 10_000)
        expected_slippage = qty * reference * BASE_SLIPPAGE_BPS * multiplier / 10_000
        expected_spread = qty * reference * (BASE_SPREAD_BPS / 2) * multiplier / 10_000
        expected_fee = _fee(side, qty, expected_price, ts.date(), multiplier)
        _check_close(issues, f"{multiplier}x fill {index} price", actual_price, expected_price)
        _check_close(issues, f"{multiplier}x fill {index} slippage", fill["slippage_cost"], expected_slippage)
        _check_close(issues, f"{multiplier}x fill {index} spread", fill["spread_cost"], expected_spread)
        _check_close(issues, f"{multiplier}x fill {index} fee", fill["fee"], expected_fee)
        slippage += expected_slippage
        spread += expected_spread
        fees += expected_fee
        notional += qty * reference
    for event in run.get("terminal_events", []):
        slippage += _number(event.get("slippage_cost", 0), "terminal slippage")
        spread += _number(event.get("spread_cost", 0), "terminal spread")
        fees += _number(event.get("fee", 0), "terminal fee")
    return {"slippage_cost": slippage, "spread_cost": spread, "fee_cost": fees, "traded_notional": notional}


def _verify_first_open_orders(
    label: str,
    run: dict[str, Any],
    bars_by_symbol: dict[str, dict[date, RawBar]],
    issues: list[str],
) -> None:
    orders = {str(row["client_order_id"]): row for row in run.get("orders", [])}
    if len(orders) != len(run.get("orders", [])):
        issues.append(f"{label}: duplicate client order id")
    fills_by_order: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fill in run.get("fills", []):
        fills_by_order[str(fill["client_order_id"])].append(fill)
    expirations = {str(row["client_order_id"]): row for row in run.get("first_open_expirations", [])}
    if len(expirations) != len(run.get("first_open_expirations", [])):
        issues.append(f"{label}: duplicate first-open expiration")
    orders_by_decision_symbol = {
        (str(row["decision_timestamp"]), str(row["symbol"])): row for row in run.get("orders", [])
    }
    expected_keys: set[tuple[str, str]] = set()
    for decision_index, decision in enumerate(run.get("decisions", [])):
        timestamp = str(decision["decision_timestamp"])
        session = _timestamp(timestamp, "decision timestamp").astimezone(NY).date()
        target = {str(symbol): _number(weight, "target weight") for symbol, weight in decision["target_weights"]}
        pre_trade = {
            str(symbol): _number(weight, "pre-trade weight") for symbol, weight in decision["pre_trade_weights"]
        }
        equity = _number(decision["equity_at_decision"], "equity at decision")
        for symbol in set(target) | set(pre_trade):
            delta_weight = target.get(symbol, 0.0) - pre_trade.get(symbol, 0.0)
            if abs(delta_weight) < REBALANCE_BAND:
                continue
            bar = bars_by_symbol.get(symbol, {}).get(session)
            if bar is None or bar.close <= 0:
                continue
            notional = abs(delta_weight) * equity
            if notional < MINIMUM_ORDER_NOTIONAL:
                continue
            key = (timestamp, symbol)
            expected_keys.add(key)
            order = orders_by_decision_symbol.get(key)
            if order is None:
                issues.append(f"{label} decision {decision_index} {symbol}: expected order missing")
                continue
            _check_close(issues, f"{label} {symbol} decision reference", order["decision_reference_price"], bar.close)
            _check_close(issues, f"{label} {symbol} requested quantity", order["requested_qty"], notional / bar.close)
            expected_side = "buy" if delta_weight > 0 else "sell"
            if order.get("side") != expected_side:
                issues.append(f"{label} decision {decision_index} {symbol}: order side mismatch")
    unexpected_keys = set(orders_by_decision_symbol) - expected_keys
    if unexpected_keys:
        issues.append(f"{label}: unexpected orders {sorted(unexpected_keys)}")
    for client_order_id, order in orders.items():
        contracted_cycle = _timestamp(order["contract_fill_cycle_timestamp"], "order contract fill cycle")
        contracted_open = _timestamp(order["contract_fill_timestamp"], "order contract exchange open")
        decision_close = _timestamp(order["contract_decision_timestamp"], "order contract exchange close")
        if not decision_close < contracted_open:
            issues.append(f"{label} {client_order_id}: contract close/open chronology invalid")
        if contracted_cycle.astimezone(NY).date().isoformat() != str(order["fill_session"]):
            issues.append(f"{label} {client_order_id}: fill cycle is not on contracted first session")
        requested = _number(order["requested_qty"], "requested quantity")
        fills = fills_by_order.pop(client_order_id, [])
        for fill in fills:
            for field in ("symbol", "side", "decision_timestamp"):
                if fill.get(field) != order.get(field):
                    issues.append(f"{label} {client_order_id}: fill/order {field} mismatch")
        if any(_timestamp(fill["timestamp"], "fill timestamp") != contracted_cycle for fill in fills):
            issues.append(f"{label} {client_order_id}: fill outside the single contracted first-open cycle")
        filled = sum(_number(fill["qty"], "fill quantity") for fill in fills)
        if filled > requested + ABS_TOL:
            issues.append(f"{label} {client_order_id}: overfilled order")
        residual = max(0.0, requested - filled)
        expiry = expirations.pop(client_order_id, None)
        if residual > ABS_TOL:
            if expiry is None:
                issues.append(f"{label} {client_order_id}: residual order was not explicitly expired")
                continue
            for field in ("symbol", "side", "decision_timestamp", "contract_fill_cycle_timestamp"):
                if expiry.get(field) != order.get(field):
                    issues.append(f"{label} {client_order_id}: expiry/order {field} mismatch")
            _check_close(issues, f"{label} {client_order_id} expired quantity", expiry["expired_qty"], residual)
            _check_close(issues, f"{label} {client_order_id} filled quantity", expiry["filled_qty"], filled)
            if _timestamp(expiry["expiry_cycle_timestamp"], "expiry cycle") != contracted_cycle:
                issues.append(f"{label} {client_order_id}: residual expired outside first-open cycle")
            expected_reason = "FIRST_OPEN_PARTIAL_EXPIRED" if filled > ABS_TOL else "FIRST_OPEN_UNFILLED_EXPIRED"
            if expiry.get("reason") != expected_reason:
                issues.append(f"{label} {client_order_id}: wrong first-open expiry reason")
        elif expiry is not None:
            issues.append(f"{label} {client_order_id}: fully filled order has an expiration")
    if fills_by_order:
        issues.append(f"{label}: fills exist without persisted orders")
    if expirations:
        issues.append(f"{label}: expirations exist without persisted orders")


def _execution_notionals(label: str, run: dict[str, Any], issues: list[str] | None = None) -> dict[str, float]:
    orders_by_decision_symbol: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    fills_by_order: dict[str, float] = defaultdict(float)
    for order in run.get("orders", []):
        orders_by_decision_symbol[(str(order["decision_timestamp"]), str(order["symbol"]))].append(order)
    for fill in run.get("fills", []):
        fills_by_order[str(fill["client_order_id"])] += _number(fill["qty"], "fill quantity")
    total_target = total_completed = total_unfilled = 0.0
    for index, decision in enumerate(run.get("decisions", [])):
        target = {str(symbol): _number(weight, "target weight") for symbol, weight in decision["target_weights"]}
        pre_trade = {
            str(symbol): _number(weight, "pre-trade weight") for symbol, weight in decision["pre_trade_weights"]
        }
        equity = _number(decision["equity_at_decision"], "equity at decision")
        full_target = completed = 0.0
        timestamp = str(decision["decision_timestamp"])
        for symbol in set(target) | set(pre_trade):
            intended = abs(target.get(symbol, 0.0) - pre_trade.get(symbol, 0.0)) * equity
            full_target += intended
            orders = orders_by_decision_symbol.get((timestamp, symbol), [])
            requested_qty = sum(_number(order["requested_qty"], "requested quantity") for order in orders)
            filled_qty = sum(fills_by_order[str(order["client_order_id"])] for order in orders)
            completion = min(1.0, filled_qty / requested_qty) if requested_qty > 0 else 0.0
            completed += intended * completion
        unfilled = max(0.0, full_target - completed)
        if issues is not None:
            for field, expected in (
                ("full_target_notional", full_target),
                ("completed_target_notional", completed),
                ("unfilled_target_notional", unfilled),
            ):
                _check_close(issues, f"{label} decision {index} {field}", decision.get(field), expected)
        total_target += full_target
        total_completed += completed
        total_unfilled += unfilled
    return {
        "full_target_notional": total_target,
        "completed_target_notional": total_completed,
        "unfilled_target_notional": total_unfilled,
    }


def _verify_lifecycle_events(
    label: str,
    run: dict[str, Any],
    lifetimes: dict[str, Lifetime],
    issues: list[str],
) -> None:
    for index, event in enumerate(run.get("terminal_events", [])):
        policy = str(event.get("effective_policy", event.get("policy", "")))
        timestamp = _timestamp(event["timestamp"], "terminal timestamp")
        if policy == "TERMINAL_BAR":
            entity_id = str(event.get("entity_id") or "")
            lifetime = lifetimes.get(entity_id)
            if lifetime is None or lifetime.unresolved:
                issues.append(f"{label} terminal {index}: missing resolved PIT lifetime")
                continue
            if timestamp.astimezone(NY).date() != lifetime.last_observed:
                issues.append(f"{label} terminal {index}: liquidation is not on final observed bar")
            if event.get("terminal_event_class") != lifetime.terminal_event_class:
                issues.append(f"{label} terminal {index}: lifecycle event class mismatch")
        elif policy == "FINAL_BAR":
            if timestamp.astimezone(NY).date() != ENDPOINT:
                issues.append(f"{label} terminal {index}: final liquidation is not at frozen endpoint")
        else:
            issues.append(f"{label} terminal {index}: unsupported liquidation policy {policy!r}")


def _event_rows(run: dict[str, Any]) -> dict[datetime, dict[str, list[dict[str, Any]]]]:
    events: dict[datetime, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for kind, key in (("dividend", "dividend_events"), ("fill", "fills"), ("terminal", "terminal_events")):
        for row in run.get(key, []):
            events[_timestamp(row["timestamp"], f"{kind} timestamp")][kind].append(row)
    return events


def _verify_ledger(
    run: dict[str, Any],
    bars_by_symbol: dict[str, dict[date, RawBar]],
    canonical_actions: dict[tuple[str, date, float], str],
    multiplier: float,
    issues: list[str],
) -> dict[str, Any]:
    summary = run.get("summary", {})
    cash = _number(summary.get("starting_equity", 100_000), "starting equity")
    positions: dict[str, Position] = {}
    realized: list[list[Any]] = []
    closed: list[list[Any]] = []
    dividends = 0.0
    symbol_net_cash: dict[str, float] = defaultdict(float)
    symbol_gross_cash: dict[str, float] = defaultdict(float)
    entity_by_symbol: dict[str, str] = {}
    episode_costs: dict[str, float] = defaultdict(float)
    episode_dividends: dict[str, float] = defaultdict(float)
    episode_start: dict[str, str] = {}
    gross_episodes: list[dict[str, Any]] = []
    symbol_pnl_snapshots: list[tuple[datetime, float, dict[str, float]]] = []
    cost_totals = _verify_costs(run, multiplier, issues)
    events = _event_rows(run)
    observed_action_keys: set[tuple[str, date, float]] = set()
    for row in run.get("dividend_events", []):
        key = (
            str(row["symbol"]).upper(),
            date.fromisoformat(str(row["ex_date"])),
            _number(row["rate"], "dividend rate"),
        )
        if key in observed_action_keys:
            issues.append(f"{multiplier}x duplicate dividend event {key}")
        observed_action_keys.add(key)
        if canonical_actions.get(key) != row.get("raw_id"):
            issues.append(f"{multiplier}x dividend event lacks matching canonical raw action {key}")
    nav_rows = run.get("nav_curve", [])
    persisted_snapshots = {
        _timestamp(row["timestamp"], "holdings snapshot timestamp"): row for row in run.get("holdings_snapshots", [])
    }
    if not nav_rows:
        issues.append(f"{multiplier}x: empty NAV curve")
        return {"nav_points": 0}
    previous_nav_ts: datetime | None = None
    max_positions = 0
    max_exposure = 0.0
    max_concentration = 0.0
    cash_ratios: list[float] = []
    holding_counts: list[int] = []
    for nav_index, nav_row in enumerate(nav_rows):
        nav_ts = _timestamp(nav_row["timestamp"], "NAV timestamp")
        if previous_nav_ts is not None and nav_ts <= previous_nav_ts:
            issues.append(f"{multiplier}x: NAV timestamps not strictly ordered")
        # Corporate actions are effective at 00:00 UTC and therefore precede
        # that session's opening fills. Include any event since the prior NAV.
        due_timestamps = sorted(
            ts for ts in events if (previous_nav_ts is None or ts > previous_nav_ts) and ts <= nav_ts
        )
        for event_ts in due_timestamps:
            bucket = events[event_ts]
            for row in bucket.get("dividend", []):
                symbol = str(row["symbol"]).upper()
                if row.get("entity_id"):
                    entity_by_symbol[symbol] = str(row["entity_id"])
                qty = positions.get(symbol, Position(0, 0, 0)).qty
                rate = _number(row["rate"], "dividend rate")
                credit = qty * rate
                _check_close(issues, f"{multiplier}x dividend {event_ts} {symbol} qty", row["qty"], qty)
                _check_close(issues, f"{multiplier}x dividend {event_ts} {symbol} credit", row["cash_credit"], credit)
                cash += credit
                dividends += credit
                symbol_net_cash[symbol] += credit
                symbol_gross_cash[symbol] += credit
                if symbol in positions:
                    episode_dividends[symbol] += credit
            # Sells before buys is a canonical engine invariant.
            fills = sorted(bucket.get("fill", []), key=lambda row: 0 if str(row["side"]).lower() == "sell" else 1)
            for row in fills:
                symbol = str(row["symbol"]).upper()
                if row.get("entity_id"):
                    entity_by_symbol[symbol] = str(row["entity_id"])
                side = str(row["side"]).lower()
                qty = _number(row["qty"], "qty")
                price = _number(row["fill_price"], "fill price")
                fee = _number(row["fee"], "fee")
                fill_cost = fee + _number(row["slippage_cost"], "slippage") + _number(row["spread_cost"], "spread")
                if side == "buy":
                    current = positions.get(symbol)
                    if current is None:
                        episode_start[symbol] = event_ts.isoformat()
                    cash -= qty * price + fee
                    symbol_net_cash[symbol] -= qty * price + fee
                    symbol_gross_cash[symbol] -= qty * _number(row["reference_price"], "reference price")
                    if current is None:
                        positions[symbol] = Position(qty, price, fee)
                    else:
                        total = current.qty + qty
                        current.average_price = (current.qty * current.average_price + qty * price) / total
                        current.qty = total
                        current.entry_fee += fee
                    episode_costs[symbol] += fill_cost
                else:
                    current = positions.get(symbol)
                    if current is None or qty > current.qty + ABS_TOL:
                        issues.append(f"{multiplier}x {event_ts} {symbol}: sell exceeds independent inventory")
                        continue
                    close_qty = min(qty, current.qty)
                    fee_share = current.entry_fee * close_qty / max(1e-12, current.qty)
                    current.entry_fee -= fee_share
                    pnl = (price - current.average_price) * close_qty - fee - fee_share
                    current.realized_accumulator += pnl
                    episode_costs[symbol] += fill_cost
                    current.qty -= close_qty
                    cash += close_qty * price - fee
                    symbol_net_cash[symbol] += close_qty * price - fee
                    symbol_gross_cash[symbol] += close_qty * _number(row["reference_price"], "reference price")
                    realized.append([event_ts.isoformat(), symbol, pnl])
                    if current.qty <= ABS_TOL:
                        lifetime = current.realized_accumulator
                        closed.append([event_ts.isoformat(), symbol, lifetime])
                        gross_episodes.append(
                            {
                                "symbol": symbol,
                                "entity_id": entity_by_symbol.get(symbol),
                                "opened_at": episode_start.get(symbol),
                                "closed_at": event_ts.isoformat(),
                                "net_price_pnl": lifetime,
                                "dividend_cash": episode_dividends[symbol],
                                "transaction_cost": episode_costs[symbol],
                                "gross_pnl": lifetime + episode_dividends[symbol] + episode_costs[symbol],
                            }
                        )
                        episode_costs.pop(symbol, None)
                        episode_dividends.pop(symbol, None)
                        episode_start.pop(symbol, None)
                        positions.pop(symbol, None)
            for row in bucket.get("terminal", []):
                symbol = str(row["symbol"]).upper()
                if row.get("entity_id"):
                    entity_by_symbol[symbol] = str(row["entity_id"])
                current = positions.get(symbol)
                if current is None:
                    issues.append(f"{multiplier}x {event_ts} {symbol}: terminal event without inventory")
                    continue
                qty = _number(row["quantity"], "terminal quantity")
                fill_price = _number(row["fill_price"], "terminal fill price")
                last_close = _number(row["last_close"], "terminal last close")
                fee = _number(row.get("fee", 0), "terminal fee")
                terminal_cost = (
                    fee
                    + _number(row.get("slippage_cost", 0), "terminal slippage")
                    + _number(row.get("spread_cost", 0), "terminal spread")
                )
                if not _close(qty, current.qty):
                    issues.append(f"{multiplier}x {event_ts} {symbol}: terminal quantity mismatch")
                pnl = (fill_price - current.average_price) * qty - current.entry_fee - fee
                lifetime = current.realized_accumulator + pnl
                episode_costs[symbol] += terminal_cost
                cash += qty * fill_price - fee
                symbol_net_cash[symbol] += qty * fill_price - fee
                symbol_gross_cash[symbol] += qty * last_close
                expected_fill = last_close * (1 - (BASE_SLIPPAGE_BPS + BASE_SPREAD_BPS / 2) * multiplier / 10_000)
                expected_slippage = qty * last_close * BASE_SLIPPAGE_BPS * multiplier / 10_000
                expected_spread = qty * last_close * (BASE_SPREAD_BPS / 2) * multiplier / 10_000
                expected_fee = _fee("sell", qty, expected_fill, event_ts.date(), multiplier)
                _check_close(issues, f"{multiplier}x terminal {symbol} fill price", fill_price, expected_fill)
                _check_close(
                    issues,
                    f"{multiplier}x terminal {symbol} slippage",
                    row.get("slippage_cost", 0),
                    expected_slippage,
                )
                _check_close(
                    issues, f"{multiplier}x terminal {symbol} spread", row.get("spread_cost", 0), expected_spread
                )
                _check_close(issues, f"{multiplier}x terminal {symbol} fee", fee, expected_fee)
                realized.append([event_ts.isoformat(), symbol, pnl])
                closed.append([event_ts.isoformat(), symbol, lifetime])
                gross_episodes.append(
                    {
                        "symbol": symbol,
                        "entity_id": entity_by_symbol.get(symbol),
                        "opened_at": episode_start.get(symbol),
                        "closed_at": event_ts.isoformat(),
                        "net_price_pnl": lifetime,
                        "dividend_cash": episode_dividends[symbol],
                        "transaction_cost": episode_costs[symbol],
                        "gross_pnl": lifetime + episode_dividends[symbol] + episode_costs[symbol],
                    }
                )
                episode_costs.pop(symbol, None)
                episode_dividends.pop(symbol, None)
                episode_start.pop(symbol, None)
                positions.pop(symbol, None)
        session = nav_ts.astimezone(NY).date()
        values: list[float] = []
        for symbol, position in positions.items():
            bar = bars_by_symbol.get(symbol, {}).get(session)
            if bar is None:
                issues.append(f"{multiplier}x NAV {session}: missing held-symbol close for {symbol}")
                continue
            values.append(position.qty * bar.close)
        expected_nav = cash + sum(values)
        actual_nav = _number(nav_row["nav"], "NAV")
        _check_close(issues, f"{multiplier}x NAV {nav_index} {session}", actual_nav, expected_nav)
        persisted_snapshot = persisted_snapshots.get(nav_ts)
        if persisted_snapshot is None:
            issues.append(f"{multiplier}x NAV {session}: missing holdings snapshot")
        else:
            _check_close(issues, f"{multiplier}x snapshot cash {session}", persisted_snapshot["cash"], cash)
            _check_close(issues, f"{multiplier}x snapshot NAV {session}", persisted_snapshot["nav"], expected_nav)
            persisted_positions = {str(row["symbol"]).upper(): row for row in persisted_snapshot["positions"]}
            if set(persisted_positions) != set(positions):
                issues.append(f"{multiplier}x snapshot inventory symbols {session} mismatch")
            for symbol, position in positions.items():
                row = persisted_positions.get(symbol)
                if row is None:
                    continue
                bar = bars_by_symbol.get(symbol, {}).get(session)
                _check_close(issues, f"{multiplier}x {session} {symbol} qty", row["qty"], position.qty)
                _check_close(
                    issues, f"{multiplier}x {session} {symbol} average price", row["avg_price"], position.average_price
                )
                _check_close(
                    issues, f"{multiplier}x {session} {symbol} entry fee", row["entry_fee"], position.entry_fee
                )
                if bar is not None:
                    _check_close(issues, f"{multiplier}x {session} {symbol} close", row["current_price"], bar.close)
                    _check_close(
                        issues,
                        f"{multiplier}x {session} {symbol} market value",
                        row["market_value"],
                        position.qty * bar.close,
                    )
        if actual_nav > 0:
            exposure = sum(values) / actual_nav
            concentration = max(values, default=0.0) / actual_nav
            cash_ratio = cash / actual_nav
            max_exposure = max(max_exposure, exposure)
            max_concentration = max(max_concentration, concentration)
            cash_ratios.append(cash_ratio)
        holding_counts.append(len(positions))
        symbol_values = {
            symbol: position.qty * bars_by_symbol[symbol][session].close
            for symbol, position in positions.items()
            if session in bars_by_symbol.get(symbol, {})
        }
        symbol_pnl_snapshots.append(
            (
                nav_ts,
                actual_nav,
                {
                    symbol: symbol_net_cash.get(symbol, 0.0) + symbol_values.get(symbol, 0.0)
                    for symbol in set(symbol_net_cash) | set(symbol_values)
                },
            )
        )
        max_positions = max(max_positions, len(positions))
        previous_nav_ts = nav_ts
    _check_close(
        issues, f"{multiplier}x ending equity", summary["ending_equity"], _number(nav_rows[-1]["nav"], "ending NAV")
    )
    _check_close(issues, f"{multiplier}x dividend total", summary.get("dividend_total", 0), dividends)
    _compare_event_values(issues, f"{multiplier}x realized PnL events", run.get("realized_pnl_events", []), realized)
    _compare_event_values(
        issues, f"{multiplier}x closed position events", run.get("closed_position_events", []), closed
    )
    persisted_episodes = run.get("gross_closed_episodes", [])
    if len(persisted_episodes) != len(gross_episodes):
        issues.append(f"{multiplier}x gross closed episode count mismatch")
    for index, (persisted, rebuilt) in enumerate(zip(persisted_episodes, gross_episodes, strict=False)):
        for field in ("symbol", "entity_id", "opened_at", "closed_at"):
            if persisted.get(field) != rebuilt.get(field):
                issues.append(f"{multiplier}x gross episode {index} {field} mismatch")
        for field in ("net_price_pnl", "dividend_cash", "transaction_cost", "gross_pnl"):
            _check_close(
                issues,
                f"{multiplier}x gross episode {index} {field}",
                persisted.get(field),
                rebuilt[field],
            )
    positive_gross_profit = sum(max(0.0, _number(row["gross_pnl"], "episode gross P&L")) for row in gross_episodes)
    _check_close(
        issues,
        f"{multiplier}x positive gross profit",
        summary.get("positive_gross_profit"),
        positive_gross_profit,
    )
    persisted_contributions = {
        str(row["symbol"]).upper(): _number(row["gross_pnl"], "gross contribution")
        for row in summary.get("per_symbol", [])
    }
    if set(persisted_contributions) != set(symbol_gross_cash):
        issues.append(f"{multiplier}x gross-contribution symbol set mismatch")
    for symbol, value in symbol_gross_cash.items():
        if symbol in persisted_contributions:
            _check_close(issues, f"{multiplier}x gross contribution {symbol}", persisted_contributions[symbol], value)
    entity_gross: dict[str, float] = defaultdict(float)
    entity_symbols: dict[str, set[str]] = defaultdict(set)
    for symbol, value in symbol_gross_cash.items():
        entity_id = entity_by_symbol.get(symbol, f"UNMAPPED:{symbol}")
        entity_gross[entity_id] += value
        entity_symbols[entity_id].add(symbol)
    persisted_entities = {str(row["entity_id"]): row for row in summary.get("per_entity", [])}
    if set(persisted_entities) != set(entity_gross):
        issues.append(f"{multiplier}x gross-contribution entity set mismatch")
    for entity_id, value in entity_gross.items():
        if entity_id not in persisted_entities:
            continue
        _check_close(
            issues,
            f"{multiplier}x gross contribution entity {entity_id}",
            persisted_entities[entity_id]["gross_pnl"],
            value,
        )
        if sorted(persisted_entities[entity_id]["symbols"]) != sorted(entity_symbols[entity_id]):
            issues.append(f"{multiplier}x entity alias set mismatch {entity_id}")
    absolute_gross = sum(abs(value) for value in entity_gross.values())
    positive_entities = [entity for entity, value in entity_gross.items() if value > 0]
    largest_positive_entity = (
        max(positive_entities, key=lambda entity: (entity_gross[entity], entity)) if positive_entities else None
    )
    largest_absolute_share = (
        max((abs(value) for value in entity_gross.values()), default=0.0) / absolute_gross
        if absolute_gross > 0
        else 0.0
    )
    dominant_symbols = (
        entity_symbols.get(largest_positive_entity, set()) if largest_positive_entity is not None else set()
    )
    leave_one_curve = [
        {
            "timestamp": timestamp.isoformat(),
            "nav": nav - sum(contributions.get(symbol, 0.0) for symbol in dominant_symbols),
        }
        for timestamp, nav, contributions in symbol_pnl_snapshots
    ]
    leave_one_run = dict(run)
    leave_one_run["nav_curve"] = leave_one_curve
    leave_one_metrics = {
        "full": _period_metrics(leave_one_run, DEVELOPMENT_START, ENDPOINT),
        "validation": _period_metrics(leave_one_run, VALIDATION_START, ENDPOINT),
    }
    exposures = [1.0 - value for value in cash_ratios]
    return {
        "nav_points": len(nav_rows),
        "ending_cash": cash,
        "ending_positions": len(positions),
        "dividend_total": dividends,
        "maximum_positions": max_positions,
        "maximum_exposure": max_exposure,
        "maximum_concentration": max_concentration,
        "minimum_cash_ratio": min(cash_ratios, default=1.0),
        "average_cash_ratio": statistics.fmean(cash_ratios) if cash_ratios else 1.0,
        "average_exposure": statistics.fmean(exposures) if exposures else 0.0,
        "median_exposure": statistics.median(exposures) if exposures else 0.0,
        "average_positions": statistics.fmean(holding_counts) if holding_counts else 0.0,
        "median_holdings": statistics.median(holding_counts) if holding_counts else 0.0,
        "decisions_with_four_ratio": 0.0,
        "gross_contribution_by_symbol": dict(sorted(symbol_gross_cash.items())),
        "gross_contribution_by_entity": dict(sorted(entity_gross.items())),
        "positive_gross_profit": positive_gross_profit,
        "largest_positive_entity": largest_positive_entity,
        "largest_positive_entity_symbols": sorted(dominant_symbols),
        "largest_absolute_contribution_share": largest_absolute_share,
        "leave_one_nav_curve": leave_one_curve,
        "leave_one_metrics": leave_one_metrics,
        **cost_totals,
    }


def _normalize_event(row: Any) -> tuple[str, str, float]:
    if isinstance(row, dict):
        return (str(row["timestamp"]), str(row["symbol"]), _number(row.get("pnl", row.get("value")), "pnl"))
    return (str(row[0]), str(row[1]), _number(row[2], "pnl"))


def _compare_event_values(issues: list[str], label: str, actual: list[Any], expected: list[Any]) -> None:
    if len(actual) != len(expected):
        issues.append(f"{label}: count runner={len(actual)} verifier={len(expected)}")
        return
    for index, (left, right) in enumerate(zip(actual, expected, strict=True)):
        a = _normalize_event(left)
        b = _normalize_event(right)
        if a[:2] != b[:2] or not _close(a[2], b[2]):
            issues.append(f"{label}[{index}] mismatch")


def _sample_std(values: Sequence[float]) -> float:
    return statistics.stdev(values) if len(values) >= 2 else 0.0


def _one_way_turnover(target_rows: Sequence[Sequence[Any]], pre_trade_rows: Sequence[Sequence[Any]]) -> float:
    target = {str(symbol): _number(weight, "target weight") for symbol, weight in target_rows}
    pre_trade = {str(symbol): _number(weight, "pre-trade weight") for symbol, weight in pre_trade_rows}
    return 0.5 * sum(
        abs(target.get(symbol, 0.0) - pre_trade.get(symbol, 0.0)) for symbol in set(target) | set(pre_trade)
    )


def _period_metrics(
    run: dict[str, Any],
    start: date,
    end: date,
) -> dict[str, Any]:
    nav = [(_timestamp(row["timestamp"], "NAV timestamp"), _number(row["nav"], "NAV")) for row in run["nav_curve"]]
    selected = [
        index for index, (timestamp, _value) in enumerate(nav) if start <= timestamp.astimezone(NY).date() <= end
    ]
    if not selected:
        raise VerificationError(f"no NAV points in {start}..{end}")
    first = selected[0]
    indices = ([first - 1] if first else []) + selected
    points = [nav[index] for index in indices]
    timestamps = [item[0] for item in points]
    values = [item[1] for item in points]
    returns = [values[index] / values[index - 1] - 1 for index in range(1, len(values)) if values[index - 1] > 0]
    years = (timestamps[-1] - timestamps[0]).total_seconds() / (365.0 * 86_400)
    total_return = values[-1] / values[0] - 1 if values[0] > 0 else -1.0
    cagr = (values[-1] / values[0]) ** (1 / years) - 1 if years > 0 and values[-1] > 0 else 0.0
    std = _sample_std(returns)
    downside_std = _sample_std([value for value in returns if value < 0])
    annualizer = math.sqrt(max(1.0, len(returns) / years)) if returns and years > 0 else 1.0
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak)
    monthly: dict[tuple[int, int], float] = {}
    for timestamp, value in points:
        monthly[(timestamp.year, timestamp.month)] = value
    month_values = list(monthly.values())
    month_returns = [month_values[index] / month_values[index - 1] - 1 for index in range(1, len(month_values))]
    fills = [
        row
        for row in run.get("fills", [])
        if start <= _timestamp(row["timestamp"], "fill").astimezone(NY).date() <= end
    ]
    terminals = [
        row
        for row in run.get("terminal_events", [])
        if start <= _timestamp(row["timestamp"], "terminal").astimezone(NY).date() <= end
    ]
    decisions = [
        row
        for row in run.get("decisions", [])
        if start <= _timestamp(row["decision_timestamp"], "decision").astimezone(NY).date() <= end
    ]
    slippage = sum(_number(row["slippage_cost"], "slippage") for row in fills) + sum(
        _number(row.get("slippage_cost", 0), "terminal slippage") for row in terminals
    )
    spread = sum(_number(row["spread_cost"], "spread") for row in fills) + sum(
        _number(row.get("spread_cost", 0), "terminal spread") for row in terminals
    )
    fees = sum(_number(row["fee"], "fee") for row in fills) + sum(
        _number(row.get("fee", 0), "terminal fee") for row in terminals
    )
    costs = slippage + spread + fees
    average_nav = statistics.fmean(values)
    years_for_turnover = max(years, 1 / 365.0)
    realized_notional = sum(_number(row["qty"], "qty") * _number(row["reference_price"], "reference") for row in fills)
    target_turnover = 0.0
    for index, row in enumerate(decisions):
        independently_reconstructed = _one_way_turnover(row["target_weights"], row["pre_trade_weights"])
        if "one_way_turnover" not in row:
            raise VerificationError(f"decision {index}: missing canonical one-way turnover")
        if not _close(row["one_way_turnover"], independently_reconstructed):
            raise VerificationError(f"decision {index}: one-way turnover mismatch")
        target_turnover += independently_reconstructed
    annualized_target_turnover = target_turnover / years_for_turnover
    annualized_realized_turnover = (
        sum(_number(row.get("realized_turnover", 0), "realized turnover") for row in decisions) / years_for_turnover
    )
    realized_events = [
        _normalize_event(row)[2]
        for row in run.get("closed_position_events", [])
        if start <= _timestamp(_normalize_event(row)[0], "closed event").astimezone(NY).date() <= end
    ]
    gross_profit = sum(value for value in realized_events if value > 0)
    gross_loss = -sum(value for value in realized_events if value < 0)
    period_snapshots = [
        row
        for row in run.get("holdings_snapshots", [])
        if start <= _timestamp(row["timestamp"], "snapshot timestamp").astimezone(NY).date() <= end
    ]
    exposures = [
        sum(_number(position["market_value"], "market value") for position in row["positions"])
        / _number(row["nav"], "snapshot NAV")
        if _number(row["nav"], "snapshot NAV") > 0
        else 0.0
        for row in period_snapshots
    ]
    position_counts = [len(row["positions"]) for row in period_snapshots]
    realized_concentrations = [
        max(
            (
                _number(position["market_value"], "market value") / _number(row["nav"], "snapshot NAV")
                for position in row["positions"]
            ),
            default=0.0,
        )
        if _number(row["nav"], "snapshot NAV") > 0
        else 0.0
        for row in period_snapshots
    ]
    return {
        "start_timestamp": timestamps[0].isoformat(),
        "end_timestamp": timestamps[-1].isoformat(),
        "curve_point_count": len(values),
        "net_total_return": total_return,
        "cagr": cagr,
        "annualized_volatility": std * annualizer if std > 0 else 0.0,
        "sharpe": statistics.fmean(returns) / std * annualizer if returns and std > 0 else 0.0,
        "sortino": statistics.fmean(returns) / downside_std * annualizer if returns and downside_std > 0 else 0.0,
        "max_drawdown": max_drawdown,
        "calmar": cagr / max_drawdown if max_drawdown > 0 else 0.0,
        "worst_month": min(month_returns, default=0.0),
        "positive_month_ratio": sum(value > 0 for value in month_returns) / len(month_returns)
        if month_returns
        else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "gross_loss": gross_loss,
        "annualized_target_turnover": annualized_target_turnover,
        "annualized_realized_turnover": annualized_realized_turnover,
        "realized_notional_turnover": realized_notional / average_nav / years_for_turnover if average_nav > 0 else 0.0,
        "transaction_costs": costs,
        "slippage_cost": slippage,
        "spread_cost": spread,
        "fee_cost": fees,
        "cost_to_positive_gross_profit": costs / gross_profit if gross_profit > 0 else None,
        "scheduled_decision_count": len(decisions),
        "actual_decision_count": len(decisions),
        "fill_count": len(fills),
        "terminal_event_count": len(terminals),
        "average_exposure": statistics.fmean(exposures) if exposures else 0.0,
        "median_exposure": statistics.median(exposures) if exposures else 0.0,
        "average_positions": statistics.fmean(position_counts) if position_counts else 0.0,
        "maximum_target_concentration": max(
            (
                max((_number(weight, "target weight") for _symbol, weight in row["target_weights"]), default=0.0)
                for row in decisions
            ),
            default=0.0,
        ),
        "maximum_realized_concentration": max(realized_concentrations, default=0.0),
        "gross_profit": gross_profit,
        "years": years,
    }


def _compare_metrics(run: dict[str, Any], issues: list[str], multiplier: str) -> dict[str, dict[str, Any]]:
    spans = {
        "development": (DEVELOPMENT_START, DEVELOPMENT_END),
        "validation": (VALIDATION_START, ENDPOINT),
        "full": (DEVELOPMENT_START, ENDPOINT),
    }
    verified: dict[str, dict[str, Any]] = {}
    persisted = run.get("metrics", {})
    aliases = {
        "max_drawdown": ("max_drawdown", "maximum_drawdown"),
        "worst_month": ("worst_month", "worst_month_return"),
    }
    for period, (start, end) in spans.items():
        calculated = _period_metrics(run, start, end)
        verified[period] = calculated
        actual = persisted.get(period, {})
        for key, expected in calculated.items():
            if key == "years" or expected is None:
                continue
            candidate_keys = aliases.get(key, (key,))
            present = next((name for name in candidate_keys if name in actual), None)
            if present is None:
                issues.append(f"{multiplier}x {period}: missing metric {key}")
            elif isinstance(expected, str):
                if actual[present] != expected:
                    issues.append(f"{multiplier}x {period} {key}: timestamp mismatch")
            else:
                _check_close(issues, f"{multiplier}x {period} {key}", actual[present], expected)
    yearly = persisted.get("validation_years", {})
    for year in range(2022, 2027):
        calculated = _period_metrics(run, date(year, 1, 1), ENDPOINT if year == 2026 else date(year, 12, 31))
        actual = yearly.get(str(year), {})
        for key in ("net_total_return", "cagr", "sharpe", "max_drawdown"):
            persisted_key = "maximum_drawdown" if key == "max_drawdown" and "maximum_drawdown" in actual else key
            if persisted_key not in actual:
                issues.append(f"{multiplier}x validation year {year}: missing {key}")
            else:
                _check_close(
                    issues, f"{multiplier}x validation year {year} {key}", actual[persisted_key], calculated[key]
                )
    return verified


def _gate_table(
    metrics: dict[str, dict[str, dict[str, Any]]],
    ledger: dict[str, dict[str, Any]],
    runs: dict[str, Any],
    integrity_pass: bool,
    architecture_pass: bool,
) -> dict[str, dict[str, Any]]:
    one = metrics["1.0"]
    two = metrics["2.0"]
    three = metrics["3.0"]
    full = one["full"]
    validation = one["validation"]
    development = one["development"]
    execution_notional = _execution_notionals("1.0x", runs["1.0"])
    target_notional = execution_notional["full_target_notional"]
    unfilled_notional = execution_notional["unfilled_target_notional"]
    filled_ratio = 1.0 - unfilled_notional / target_notional if target_notional > 0 else 0.0
    unfilled_ratio = unfilled_notional / target_notional if target_notional > 0 else 1.0
    total_cost = full["transaction_costs"]
    gross_profit = ledger["1.0"]["positive_gross_profit"]
    loo_metrics = metrics.get("leave_one", {})
    loo_full = loo_metrics.get("full", {})
    loo_validation = loo_metrics.get("validation", {})
    decision_holdings = [len(row.get("target_weights", [])) for row in runs["1.0"].get("decisions", [])]
    median_holdings = statistics.median(decision_holdings) if decision_holdings else 0.0
    four_ratio = sum(value >= 4 for value in decision_holdings) / len(decision_holdings) if decision_holdings else 0.0
    maximum_target = max(
        (
            _number(weight, "target weight")
            for row in runs["1.0"].get("decisions", [])
            for _symbol, weight in row.get("target_weights", [])
        ),
        default=0.0,
    )
    target_exposures = [
        sum(_number(weight, "target weight") for _symbol, weight in row.get("target_weights", []))
        for row in runs["1.0"].get("decisions", [])
    ]
    maximum_target_exposure = max(target_exposures, default=0.0)
    minimum_target_cash = min((1.0 - value for value in target_exposures), default=1.0)
    maximum_target_positions = max(decision_holdings, default=0)
    largest_share = ledger["1.0"]["largest_absolute_contribution_share"]
    gates = {
        "G1": integrity_pass,
        "G2": full["net_total_return"] > 0 and full["cagr"] > 0,
        "G3": development["net_total_return"] > 0 and development["cagr"] > 0,
        "G4": validation["net_total_return"] > 0
        and validation["cagr"] > 0
        and validation["sharpe"] > 0
        and validation["max_drawdown"] <= 0.30,
        "G5": full["sharpe"] >= 0.35
        and full["sortino"] >= 0.45
        and full["calmar"] >= 0.20
        and full["max_drawdown"] <= 0.30,
        "G6": two["full"]["net_total_return"] > 0
        and two["full"]["cagr"] > 0
        and two["validation"]["net_total_return"] > 0
        and two["validation"]["cagr"] > 0
        and three["full"]["net_total_return"] > 0
        and three["full"]["cagr"] > 0
        and three["full"]["sharpe"] > 0,
        "G7": full["annualized_target_turnover"] <= 12.0
        and filled_ratio >= 0.95
        and 0 <= unfilled_ratio <= 0.05
        and gross_profit > 0
        and total_cost <= 0.35 * gross_profit,
        "G8": median_holdings >= 5
        and four_ratio >= 0.90
        and maximum_target <= 0.10 + ABS_TOL
        and 0 <= largest_share <= 0.35
        and _number(loo_full.get("net_total_return", -1), "LOO full return") > 0
        and _number(loo_full.get("cagr", -1), "LOO full CAGR") > 0
        and _number(loo_validation.get("net_total_return", -1), "LOO validation return") > 0
        and _number(loo_validation.get("cagr", -1), "LOO validation CAGR") > 0,
        "G9": maximum_target_exposure <= 0.55 + ABS_TOL
        and minimum_target_cash >= 0.40 - ABS_TOL
        and maximum_target_positions <= 6
        and ledger["1.0"]["maximum_exposure"] <= 0.60 + ABS_TOL
        and ledger["1.0"]["minimum_cash_ratio"] >= 0.40 - ABS_TOL
        and ledger["1.0"]["maximum_positions"] <= 6
        and architecture_pass,
    }
    return {name: {"status": "PASS" if passed else "FAIL"} for name, passed in gates.items()}


def _verify_hashes(receipt: dict[str, Any], runner: dict[str, Any], issues: list[str]) -> None:
    for group in ("authorities", "canonical_data"):
        for item in receipt.get(group, []):
            path = ROOT / item["path"]
            if not path.is_file():
                issues.append(f"missing frozen input {item['path']}")
            elif _sha256(path) != item["sha256"]:
                issues.append(f"frozen input hash mismatch {item['path']}")
    if receipt.get("candidate") != CANDIDATE or runner.get("candidate") != CANDIDATE:
        issues.append("candidate identity mismatch")
    identity = runner.get("identity", {})
    for runner_field, receipt_field in (
        ("production_main_sha", "production_main_sha"),
        ("design_v2_sha", "frozen_design_v2_sha"),
        ("readiness_v2_sha", "validated_readiness_v2_sha"),
    ):
        if identity.get(runner_field) != receipt.get(receipt_field):
            issues.append(f"runner identity mismatch: {runner_field}")
    input_identity = runner.get("input_identity", {})
    for item in receipt.get("canonical_data", []):
        name = Path(item["path"]).name
        matches = [
            value
            for value in input_identity.values()
            if isinstance(value, dict) and Path(str(value.get("path", ""))).name == name
        ]
        if not matches or matches[0].get("sha256") != item["sha256"]:
            issues.append(f"runner did not pin canonical input {name}")
    stored_hash = runner.get("run_sha256")
    if stored_hash:
        payload = dict(runner)
        payload.pop("run_sha256", None)
        if stored_hash != _canonical_sha256(payload):
            issues.append("runner run_sha256 mismatch")
    deterministic = dict(runner)
    deterministic_stored = deterministic.pop("deterministic_payload_sha256", None)
    deterministic.pop("run_sha256", None)
    deterministic.pop("generated_at_utc", None)
    if deterministic_stored != _canonical_sha256(deterministic):
        issues.append("runner deterministic_payload_sha256 mismatch")


def _verify_control_parity(runner: dict[str, Any], issues: list[str]) -> dict[str, Any]:
    control = _load_json(CONTROL_PATH)
    comparison = _load_json(CONTROL_COMPARISON_PATH)
    parity = control.get("parity")
    if control.get("parity_sha256") != _canonical_sha256(parity):
        issues.append("fresh V3 control parity payload hash mismatch")
    if (
        comparison.get("verdict") != "V3_PARITY_PASS"
        or comparison.get("failed_exact_checks")
        or comparison.get("metric_differences")
        or not all(comparison.get("exact_checks", {}).values())
    ):
        issues.append("fresh V3 control did not establish exact canonical parity")
    persisted = runner.get("control", {})
    expected_hashes = {
        "fresh_control_sha256": _sha256(CONTROL_PATH),
        "fresh_control_comparison_sha256": _sha256(CONTROL_COMPARISON_PATH),
    }
    for field, expected in expected_hashes.items():
        if persisted.get(field) != expected:
            issues.append(f"runner control receipt missing or mismatched {field}")
    return {"verdict": comparison.get("verdict"), **expected_hashes}


def _verify_architecture_parity(runner: dict[str, Any], issues: list[str]) -> dict[str, Any]:
    """Recompute G9 architecture evidence from canonical config/control facts."""

    config = load_config(str(CONFIG_PATH))
    control = _load_json(CONTROL_PATH)["parity"]
    readiness = _load_json(READINESS_PATH)
    expected = {
        "starting_capital": float(config.backtest.initial_cash),
        "maximum_allocation": 0.55,
        "max_participation_pct": MAX_PARTICIPATION,
        "liquidity_lookback_bars": LIQUIDITY_LOOKBACK,
        "execution_policy": asdict(
            PortfolioExecutionPolicy(
                rebalance_band_pct=config.execution.rebalance_band_pct,
                minimum_order_notional=config.execution.minimum_order_notional,
            )
        ),
        "risk_limits": asdict(portfolio_risk_limits_from_config(config.risk)),
        "ordinary_alpha_excluded_symbols": list(config.universe.equity.ordinary_alpha_excluded_symbols),
        "terminal_mapping_count": control["input_identity"]["lifetimes"]["terminal_mapping_count"],
        "terminal_mapping_sha256": control["input_identity"]["lifetimes"]["terminal_mapping_sha256"],
        "effective_dated_regulatory_fees": bool(config.backtest.equity_use_effective_dated_fees),
        "drawdown_halt_mode": "shadow",
        "first_open_residual_policy": "expire_without_later_session_fallback",
        "engine_class": "PortfolioBacktestEngine via additive AuditedPortfolioBacktestEngine subclass",
        "pit_scanner_contract": "readiness-v2 exact frozen target stream from canonical PIT reconstruction",
        "readiness_runner_sha256": _sha256(READINESS_PATH),
        "readiness_reconstruction_sha256": readiness["reconstruction_sha256"],
        "readiness_independent_comparison_sha256": "8baed348f477f4f12746c1f27bc7c7a2b8214a2c35fe5271260a82a1e6515e33",
    }
    persisted = runner.get("candidate_results", {}).get("architecture_parity", {})
    contract = persisted.get("candidate_execution_contract")
    if contract != expected:
        issues.append("candidate execution contract differs from independently reconstructed canonical contract")
    control_summary = control["runs"]["1.0"]["metrics"]["report_summary"]
    checks = {
        "same_starting_capital": expected["starting_capital"] == float(control_summary["starting_equity"]),
        "same_maximum_allocation": expected["maximum_allocation"]
        == float(control["production_identity"]["equity"]["maximum_allocation"]),
        "same_lifecycle_mapping": expected["terminal_mapping_sha256"]
        == control["input_identity"]["lifetimes"]["terminal_mapping_sha256"],
        "same_config_hash": runner.get("input_identity", {}).get("config", {}).get("sha256") == _sha256(CONFIG_PATH),
        "same_execution_framework": contract == expected,
        "same_global_risk_constraints": contract == expected,
        "same_pit_scanner_inputs": all(
            runner.get("input_identity", {}).get(name, {}).get("sha256") == control["input_identity"][name]["sha256"]
            for name in ("bars", "lifetimes", "corporate_actions")
        ),
    }
    for key, value in checks.items():
        if persisted.get(key) is not value:
            issues.append(f"architecture parity receipt mismatch: {key}")
    for field in ("economic_models", "economic_runner", "independent_verifier"):
        item = runner.get("input_identity", {}).get(field, {})
        path = ROOT / str(item.get("path", ""))
        if not path.is_file() or item.get("sha256") != _sha256(path):
            issues.append(f"economic implementation source hash mismatch: {field}")
    all_pass = all(checks.values()) and contract == expected
    if persisted.get("all_pass") is not all_pass:
        issues.append("architecture parity aggregate mismatch")
    return {"checks": checks, "all_pass": all_pass, "execution_contract": expected}


def _verify_run_hashes(label: str, run: dict[str, Any], issues: list[str]) -> None:
    payload = dict(run)
    stored = payload.pop("run_sha256", None)
    if stored != _canonical_sha256(payload):
        issues.append(f"{label}: run payload hash mismatch")
    key_map = {
        "decisions_sha256": "decisions",
        "fills_sha256": "fills",
        "terminal_events_sha256": "terminal_events",
        "nav_curve_sha256": "nav_curve",
        "holdings_snapshots_sha256": "holdings_snapshots",
        "dividend_events_sha256": "dividend_events",
        "gross_closed_episodes_sha256": "gross_closed_episodes",
        "orders_sha256": "orders",
        "first_open_expirations_sha256": "first_open_expirations",
    }
    hashes = run.get("hashes", {})
    for hash_key, rows_key in key_map.items():
        if hashes.get(hash_key) != _canonical_sha256(run.get(rows_key, [])):
            issues.append(f"{label}: {hash_key} mismatch")


def _verify_diagnostics(
    candidate_results: dict[str, Any],
    ledger: dict[str, dict[str, Any]],
    issues: list[str],
) -> None:
    one_summary = candidate_results["cost_scenarios"]["1.0"].get("summary", {})
    concentration = one_summary.get("concentration", candidate_results.get("concentration_diagnostics", {}))
    if "largest_absolute_contribution_share" not in concentration:
        issues.append("runner omitted gross contribution concentration")
    else:
        _check_close(
            issues,
            "largest absolute gross contribution share",
            concentration["largest_absolute_contribution_share"],
            ledger["1.0"]["largest_absolute_contribution_share"],
        )
    persisted_loo = candidate_results.get("leave_one_dominant_name_out", {})
    if not persisted_loo:
        issues.append("runner omitted prespecified leave-one-dominant-name result")
        return
    dominant_entity = ledger["1.0"]["largest_positive_entity"]
    if dominant_entity is None:
        if persisted_loo.get("status") != "NOT_APPLICABLE_NO_POSITIVE_CONTRIBUTOR":
            issues.append("no-positive-contributor path lacks the frozen fail-closed LOO status")
        return
    omitted_entity = str(persisted_loo.get("omitted_entity_id") or "")
    omitted_symbols = {str(symbol) for symbol in persisted_loo.get("omitted_symbols", [])}
    readiness = _load_json(READINESS_PATH)
    expected_aliases = {
        str(target["symbol"])
        for decision in readiness["decisions"]
        for target in decision["targets"]
        if str(target["entity_id"]) == dominant_entity
    }
    if omitted_entity != dominant_entity:
        issues.append("leave-one omitted entity is not the independently dominant positive contributor")
    if omitted_symbols != expected_aliases:
        issues.append("leave-one alias set does not match the dominant entity")
    if not all(key in persisted_loo for key in ("decisions", "fills", "nav_curve", "metrics")):
        issues.append("leave-one payload is not a persisted independently replayable economic run")
        return
    baseline_decisions = candidate_results["cost_scenarios"]["1.0"].get("decisions", [])
    loo_decisions = persisted_loo.get("decisions", [])
    if len(loo_decisions) != len(baseline_decisions):
        issues.append("leave-one decision count differs from the frozen stream")
    for index, (baseline, loo) in enumerate(zip(baseline_decisions, loo_decisions, strict=False)):
        expected_weights = [row for row in baseline.get("target_weights", []) if str(row[0]) not in omitted_symbols]
        if loo.get("target_weights") != expected_weights:
            issues.append(f"leave-one decision {index}: target stream was reselected or redistributed")
        for field in (
            "decision_session",
            "contract_decision_timestamp",
            "fill_session",
            "contract_fill_timestamp",
        ):
            if loo.get(field) != baseline.get(field):
                issues.append(f"leave-one decision {index}: {field} changed")
    if any(str(fill.get("symbol")) in omitted_symbols for fill in persisted_loo.get("fills", [])):
        issues.append("leave-one path traded an alias of the omitted dominant entity")


def verify(runner_path: Path = RUNNER_PATH) -> dict[str, Any]:
    runner = _load_json(runner_path)
    receipt = _load_json(RECEIPT_PATH)
    readiness = _load_json(READINESS_PATH)
    issues: list[str] = []
    schema = runner.get("schema_version", runner.get("schema"))
    if schema != EXPECTED_SCHEMA:
        issues.append(f"runner schema mismatch: {schema!r}")
    _verify_hashes(receipt, runner, issues)
    control_verification = _verify_control_parity(runner, issues)
    architecture_verification = _verify_architecture_parity(runner, issues)
    if readiness.get("candidate") != CANDIDATE or readiness.get("endpoint") != ENDPOINT.isoformat():
        issues.append("readiness candidate/endpoint mismatch")
    structural = runner.get("structural_identity", {})
    if structural.get("readiness_reconstruction_sha256") != readiness.get("reconstruction_sha256"):
        issues.append("runner readiness reconstruction hash mismatch")
    if (
        structural.get("independent_comparison_sha256")
        != "8baed348f477f4f12746c1f27bc7c7a2b8214a2c35fe5271260a82a1e6515e33"
    ):
        issues.append("runner readiness independent-comparison anchor mismatch")
    if structural.get("scheduled_decision_count") != 115 or structural.get("reserved_role_target_count") != 0:
        issues.append("runner scheduled-decision or reserved-role structural identity mismatch")
    scenarios = runner.get("candidate_results", {}).get("cost_scenarios", {})
    if set(scenarios) != {"1.0", "2.0", "3.0"}:
        raise VerificationError("runner must persist exactly the 1.0/2.0/3.0 candidate cost cells")
    for multiplier, run in scenarios.items():
        _verify_run_hashes(f"{multiplier}x", run, issues)
    lifetime_item = next(item for item in receipt["canonical_data"] if "lifetimes" in item["path"])
    bars_item = next(item for item in receipt["canonical_data"] if "equity_pit_symmetric" in item["path"])
    actions_item = next(item for item in receipt["canonical_data"] if "corporate_actions" in item["path"])
    _by_lifetime_id, lifetimes_by_symbol = _read_lifetimes(ROOT / lifetime_item["path"])
    canonical_actions = _read_cash_actions(ROOT / actions_item["path"])
    needed_entities, needed_symbols = _needed_identities(readiness, runner)
    bars_by_entity, bars_by_symbol, raw_rows = _read_relevant_bars(
        ROOT / bars_item["path"], needed_entities, needed_symbols, lifetimes_by_symbol
    )
    signal_coverage = _verify_readiness_signal(readiness, bars_by_entity, issues)
    timing_coverage = _verify_timing_and_targets(readiness, scenarios, issues)
    ledger: dict[str, dict[str, Any]] = {}
    metrics: dict[str, dict[str, dict[str, Any]]] = {}
    for multiplier, run in sorted(scenarios.items()):
        _verify_first_open_orders(f"{multiplier}x", run, bars_by_symbol, issues)
        _execution_notionals(f"{multiplier}x", run, issues)
        _verify_lifecycle_events(f"{multiplier}x", run, _by_lifetime_id, issues)
        ledger[multiplier] = _verify_ledger(run, bars_by_symbol, canonical_actions, float(multiplier), issues)
        metrics[multiplier] = _compare_metrics(run, issues, multiplier)
    leave_one_run = runner["candidate_results"].get("leave_one_dominant_name_out", {})
    if leave_one_run.get("status") == "NOT_APPLICABLE_NO_POSITIVE_CONTRIBUTOR":
        if ledger["1.0"]["largest_positive_entity"] is not None:
            issues.append("runner claimed no positive contributor but verifier found one")
    elif leave_one_run:
        _verify_run_hashes("leave-one", leave_one_run, issues)
        _verify_first_open_orders("leave-one", leave_one_run, bars_by_symbol, issues)
        _execution_notionals("leave-one", leave_one_run, issues)
        _verify_lifecycle_events("leave-one", leave_one_run, _by_lifetime_id, issues)
        ledger["leave_one"] = _verify_ledger(leave_one_run, bars_by_symbol, canonical_actions, 1.0, issues)
        metrics["leave_one"] = _compare_metrics(leave_one_run, issues, "leave-one")
    else:
        raise VerificationError("runner omitted the prespecified full leave-one-dominant-name path")
    _verify_diagnostics(runner["candidate_results"], ledger, issues)
    pre_gate_integrity = not issues
    gates = _gate_table(metrics, ledger, scenarios, pre_gate_integrity, architecture_verification["all_pass"])
    runner_gates = runner.get("candidate_results", {}).get("gate_evaluation", {}).get("gates", {})
    if runner_gates:
        for gate, verdict in gates.items():
            if gate == "G1":
                continue
            persisted = runner_gates.get(gate, {})
            persisted_pass = persisted.get("passed") if isinstance(persisted, dict) else persisted
            if bool(persisted_pass) != (verdict["status"] == "PASS"):
                issues.append(f"{gate}: runner/verifier gate disagreement")
    integrity_pass = not issues
    gates["G1"]["status"] = "PASS" if integrity_pass else "FAIL"
    valid = integrity_pass
    classification = (
        "ALPHA_VALID_COMBINATION_UNTESTED"
        if valid and all(item["status"] == "PASS" for item in gates.values())
        else "ALPHA_REJECTED"
        if valid
        else "RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT"
    )
    comparison = {
        "candidate": CANDIDATE,
        "endpoint": ENDPOINT.isoformat(),
        "raw_rows_through_endpoint": raw_rows,
        "signal_coverage": signal_coverage,
        "timing_coverage": timing_coverage,
        "control_parity": control_verification,
        "architecture_parity": architecture_verification,
        "ledger": ledger,
        "metrics": metrics,
        "gates": gates,
    }
    return {
        "schema_version": "abnormal-volume-independent-economic-verification-v1",
        "status": "PASS" if valid else "FAIL",
        "research_run_validity": "RESEARCH_RUN_VALID" if valid else "RESEARCH_RUN_INVALID",
        "durable_classification": classification,
        "runner_path": str(runner_path.relative_to(ROOT)),
        "runner_sha256": _sha256(runner_path),
        "independence": {
            "imports_runner_economic_calculations": False,
            "consumes_persisted_runner_artifact": True,
            "replays_raw_canonical_inputs": True,
            "recomputes_costs_ledger_metrics_and_gates": True,
        },
        "issues": issues,
        "comparison": comparison,
        "comparison_sha256": _canonical_sha256(comparison),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner-output", type=Path, default=RUNNER_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = verify(args.runner_output.resolve())
    except (OSError, VerificationError, KeyError, TypeError, ValueError) as exc:
        result = {
            "schema_version": "abnormal-volume-independent-economic-verification-v1",
            "status": "FAIL",
            "research_run_validity": "RESEARCH_RUN_INVALID",
            "durable_classification": "RESEARCH_INCONCLUSIVE_DATA_OR_VALIDATION_LIMIT",
            "issues": [f"{type(exc).__name__}: {exc}"],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "research_run_validity", "durable_classification")}))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
