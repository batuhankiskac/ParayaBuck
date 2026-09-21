#!/usr/bin/env python3
"""Independent structural verifier for abnormal-volume readiness V2.

This module deliberately does not import the readiness runner or any of its
signal, quantile, rank, or serialization helpers.  It rebuilds the causal
month-end decisions from the raw bars and lifetime table, then compares that
result with the runner's persisted structural output.  It never reads a price
after the decision session except the presence and positivity of the intended
fill session's opening print.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import sys
from collections import Counter, defaultdict, deque
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from pandas_market_calendars import get_calendar  # noqa: E402

from parayabuck.markets.sectors import is_leveraged_or_inverse_equity  # noqa: E402

_NY = ZoneInfo("America/New_York")
_RESERVED = frozenset({"SPY", "SH", "PSQ", "DOG"})
_ENDPOINT = date(2026, 7, 30)
_FIRST_DECISION = date(2016, 12, 30)
_MIN_PRICE = Decimal("5")
_MIN_MEDIAN_DV = Decimal("20000000")
_MIN_ATR = Decimal("0.005")
_MAX_ATR = Decimal("0.12")
_ZERO = Decimal(0)
_ONE = Decimal(1)


class VerificationError(ValueError):
    """The raw input or runner output cannot be verified faithfully."""


@dataclass(frozen=True)
class RawBar:
    session: date
    symbol: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class Lifetime:
    entity_id: str
    canonical_symbol: str
    valid_from: date
    valid_to: date | None
    last_observed_bar: date
    current_status: str
    exchange: str
    terminal_event_class: str | None
    unresolved: bool
    aliases: tuple[tuple[str, date | None, date | None], ...]

    def active(self, session: date) -> bool:
        return self.valid_from <= session and (self.valid_to is None or session <= self.valid_to)


@dataclass
class PendingDecision:
    payload: dict[str, Any]
    fill_session: date


def _decimal(raw: str, *, field: str, row_number: int) -> Decimal:
    if raw.strip() == "":
        raise VerificationError(f"bars row {row_number}: blank {field}")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise VerificationError(f"bars row {row_number}: nonnumeric {field}") from exc
    if not value.is_finite():
        raise VerificationError(f"bars row {row_number}: nonfinite {field}")
    return value


def _timestamp(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise VerificationError(f"naive timestamp: {raw!r}")
    return parsed.astimezone(UTC)


def _canonical_decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    rendered = format(value.normalize(), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _average_rank(values: Sequence[Decimal], index: int) -> Decimal:
    """Ascending one-based average rank, including all exact ties."""
    value = values[index]
    lower = sum(item < value for item in values)
    equal = sum(item == value for item in values)
    return Decimal(lower + 1) + Decimal(equal - 1) / 2


def _q90_x44(values: Sequence[Decimal]) -> Decimal:
    """Binding V2 quantile: sorted prior-49 order statistic x_(44)."""
    if len(values) != 49:
        raise VerificationError(f"Q0.90 requires exactly 49 values, got {len(values)}")
    if any(not value.is_finite() or value <= 0 for value in values):
        raise VerificationError("Q0.90 inputs must all be finite and positive")
    return sorted(values)[43]


def _sha256_json(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _date_or_none(raw: str) -> date | None:
    return _timestamp(raw).astimezone(_NY).date() if raw.strip() else None


def _parse_aliases(raw: str, canonical_symbol: str) -> tuple[tuple[str, date | None, date | None], ...]:
    if not raw.strip():
        return ((canonical_symbol, None, None),)
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid rename_chain for {canonical_symbol}") from exc
    aliases: list[tuple[str, date | None, date | None]] = []
    for entry in entries:
        aliases.append(
            (
                str(entry["symbol"]).strip().upper(),
                date.fromisoformat(entry["valid_from"]) if entry.get("valid_from") else None,
                date.fromisoformat(entry["valid_to"]) if entry.get("valid_to") else None,
            )
        )
    return tuple(aliases)


def _load_lifetimes(path: Path) -> tuple[dict[str, Lifetime], dict[str, list[Lifetime]]]:
    by_entity: dict[str, Lifetime] = {}
    by_alias: dict[str, list[Lifetime]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {
            "entity_id",
            "canonical_symbol",
            "historical_symbol",
            "valid_from",
            "valid_to",
            "last_observed_bar",
            "current_status",
            "exchange",
            "rename_chain",
            "terminal_event_class",
        }
        if not required.issubset(reader.fieldnames or ()):
            raise VerificationError("lifetime table is missing required fields")
        for row_number, row in enumerate(reader, start=2):
            canonical = row["canonical_symbol"].strip().upper()
            status = row["current_status"].strip().lower()
            valid_to = _date_or_none(row["valid_to"])
            terminal = row["terminal_event_class"].strip()
            unresolved = status not in {"active", "inactive"} or (
                status == "inactive" and (valid_to is None or not terminal)
            )
            lifetime = Lifetime(
                entity_id=row["entity_id"].strip(),
                canonical_symbol=canonical,
                valid_from=_date_or_none(row["valid_from"]) or date.min,
                valid_to=valid_to,
                last_observed_bar=_date_or_none(row["last_observed_bar"]) or date.min,
                current_status=status,
                exchange=row["exchange"].strip().upper(),
                terminal_event_class=terminal or None,
                unresolved=unresolved,
                aliases=_parse_aliases(row["rename_chain"], canonical),
            )
            if lifetime.entity_id in by_entity:
                raise VerificationError(f"duplicate entity lifetime {lifetime.entity_id} at row {row_number}")
            by_entity[lifetime.entity_id] = lifetime
            names = {canonical, row["historical_symbol"].strip().upper()}
            names.update(alias for alias, _start, _end in lifetime.aliases)
            for name in names:
                by_alias[name].append(lifetime)
    return by_entity, dict(by_alias)


def _resolve_lifetime(raw_symbol: str, session: date, aliases: Mapping[str, Sequence[Lifetime]]) -> Lifetime | None:
    matches: list[Lifetime] = []
    for lifetime in aliases.get(raw_symbol, ()):
        alias_valid = any(
            alias == raw_symbol and (start is None or start <= session) and (end is None or session <= end)
            for alias, start, end in lifetime.aliases
        )
        if raw_symbol == lifetime.canonical_symbol or alias_valid:
            matches.append(lifetime)
    unique = {item.entity_id: item for item in matches}
    return next(iter(unique.values())) if len(unique) == 1 else None


def _schedule() -> tuple[list[date], dict[date, dict[str, Any]], set[date]]:
    frame = get_calendar("NYSE").schedule(start_date="2016-01-01", end_date="2026-08-31")
    sessions = [item.date() for item in frame.index]
    windows: dict[date, dict[str, Any]] = {}
    fill_sessions: set[date] = set()
    by_month: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index, session in enumerate(sessions):
        by_month[(session.year, session.month)].append(index)
    for (year, month), indexes in sorted(by_month.items()):
        decision_index = indexes[-1]
        if decision_index + 1 >= len(sessions):
            continue
        decision_session = sessions[decision_index]
        fill_session = sessions[decision_index + 1]
        if decision_session < _FIRST_DECISION or fill_session > _ENDPOINT:
            continue
        decision_row = frame.iloc[decision_index]
        fill_row = frame.iloc[decision_index + 1]
        windows[decision_session] = {
            "decision_session": decision_session.isoformat(),
            "decision_timestamp": decision_row["market_close"].to_pydatetime().astimezone(UTC).isoformat(),
            "fill_session": fill_session.isoformat(),
            "fill_timestamp": fill_row["market_open"].to_pydatetime().astimezone(UTC).isoformat(),
            "required_history_sessions": sessions[decision_index - 50 : decision_index + 1],
        }
        fill_sessions.add(fill_session)
    return sessions, windows, fill_sessions


def _bars_by_session(path: Path, aliases: Mapping[str, Sequence[Lifetime]]) -> Iterator[tuple[date, dict[str, RawBar]]]:
    expected_header = {"timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume", "market"}
    current_session: date | None = None
    current: dict[str, RawBar] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if set(reader.fieldnames or ()) != expected_header:
            raise VerificationError("bars CSV schema differs from canonical daily-bar schema")
        for row_number, row in enumerate(reader, start=2):
            if row["timeframe"].strip().lower() not in {"1d", "1day"} or row["market"].strip().lower() != "equity":
                raise VerificationError(f"bars row {row_number}: non-equity-daily row")
            timestamp = _timestamp(row["timestamp"])
            session = timestamp.astimezone(_NY).date()
            if session > _ENDPOINT:
                break
            if current_session is not None and session < current_session:
                raise VerificationError(f"bars row {row_number}: sessions not ordered")
            if current_session is not None and session != current_session:
                yield current_session, current
                current = {}
            current_session = session
            raw_symbol = row["symbol"].strip().upper()
            lifetime = _resolve_lifetime(raw_symbol, session, aliases)
            if lifetime is None:
                raise VerificationError(f"bars row {row_number}: ambiguous or absent identity {raw_symbol}")
            symbol = raw_symbol
            bar = RawBar(
                session=session,
                symbol=symbol,
                open=_decimal(row["open"], field="open", row_number=row_number),
                high=_decimal(row["high"], field="high", row_number=row_number),
                low=_decimal(row["low"], field="low", row_number=row_number),
                close=_decimal(row["close"], field="close", row_number=row_number),
                volume=_decimal(row["volume"], field="volume", row_number=row_number),
            )
            if bar.volume < 0:
                raise VerificationError(f"bars row {row_number}: negative volume")
            if lifetime.entity_id in current:
                raise VerificationError(f"bars row {row_number}: duplicate entity/session {lifetime.entity_id}")
            current[lifetime.entity_id] = bar
    if current_session is not None:
        yield current_session, current


def _median(values: Sequence[Decimal]) -> Decimal:
    return Decimal(str(statistics.median(values)))


def _scanner_eligible(history: Sequence[RawBar], lifetime: Lifetime, session: date) -> bool:
    if lifetime.unresolved or not lifetime.active(session):
        return False
    if lifetime.exchange in {"OTC", "OTCQX", "OTCQB", "PINK"}:
        return False
    if is_leveraged_or_inverse_equity(history[-1].symbol):
        return False
    cutoff = session - timedelta(days=60)
    window = [bar for bar in history if cutoff < bar.session <= session]
    valid = [bar for bar in window if bar.close.is_finite() and bar.close > 0 and bar.volume >= 0][-20:]
    if not valid or valid[-1].session != session or len(valid) < 19:
        return False
    if valid[-1].close < _MIN_PRICE:
        return False
    if _median([bar.close * bar.volume for bar in valid]) < _MIN_MEDIAN_DV:
        return False
    if len(valid) < 2:
        return False
    true_ranges = [
        max(current.high - current.low, abs(current.high - previous.close), abs(current.low - previous.close))
        for previous, current in zip(valid, valid[1:])
    ]
    atr_pct = (sum(true_ranges) / Decimal(len(true_ranges))) / valid[-1].close
    return _MIN_ATR <= atr_pct <= _MAX_ATR


def _shortlist(histories: Mapping[str, Sequence[RawBar]], session: date) -> set[str]:
    cutoff = session - timedelta(days=60)
    ranked: list[tuple[Decimal, str, str]] = []
    for entity_id, history in histories.items():
        window = [bar for bar in history if cutoff < bar.session <= session]
        valid = [bar for bar in window if bar.close > 0 and bar.volume >= 0][-20:]
        median_dv = _median([bar.close * bar.volume for bar in valid]) if valid else _ZERO
        ranked.append((median_dv, entity_id, history[-1].symbol))
    ranked.sort(key=lambda item: (-item[0], item[2]))
    return {entity_id for _median_dv, entity_id, _symbol in ranked[:300]}


def _reconstruct_candidate(
    history: Sequence[RawBar],
    required_sessions: Sequence[date],
    lifetime: Lifetime,
) -> tuple[dict[str, Any] | None, str]:
    if len(history) < 51:
        return None, "insufficient_history"
    bars = list(history)[-51:]
    if [bar.session for bar in bars] != list(required_sessions):
        return None, "noncontiguous_exchange_history"
    if any(bar.close <= 0 or bar.volume <= 0 for bar in bars):
        return None, "nonpositive_signal_input"
    dollar_volumes = [bar.close * bar.volume for bar in bars[1:]]
    returns = [(bars[index].close / bars[index - 1].close) - _ONE for index in range(1, 51)]
    volume_rank = _average_rank(dollar_volumes, 49)
    return_rank = _average_rank(returns, 49)
    quantile = _q90_x44(dollar_volumes[:49])
    intensity = dollar_volumes[-1] / quantile
    payload = {
        "entity_id": lifetime.entity_id,
        "symbol": bars[-1].symbol,
        "volume_rank": _canonical_decimal(volume_rank),
        "intensity": _canonical_decimal(intensity),
        "formation_return": _canonical_decimal(returns[-1]),
        "return_rank": _canonical_decimal(return_rank),
        "quantile_x44": _canonical_decimal(quantile),
        "formation_dollar_volume": _canonical_decimal(dollar_volumes[-1]),
        "reference_sessions": [bar.session.isoformat() for bar in bars[1:-1]],
        "reference_dollar_volumes": [_canonical_decimal(value) for value in dollar_volumes[:49]],
        "sorted_reference_dollar_volumes": [_canonical_decimal(value) for value in sorted(dollar_volumes[:49])],
    }
    if volume_rank < 46:
        return None, "volume_rank_below_46"
    if not Decimal(16) <= return_rank <= Decimal(35):
        return None, "formation_return_outside_middle_40"
    return payload, "eligible"


def _independent_reconstruction(
    bars_path: Path,
    lifetimes_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    lifetimes, aliases = _load_lifetimes(lifetimes_path)
    _sessions, schedule, fill_sessions = _schedule()
    histories: dict[str, deque[RawBar]] = defaultdict(lambda: deque(maxlen=61))
    pending: list[PendingDecision] = []
    fill_opens: dict[date, set[str]] = {}
    rows = 0
    session_count = 0

    for session, session_bars in _bars_by_session(bars_path, aliases):
        rows += len(session_bars)
        session_count += 1
        for entity_id, bar in session_bars.items():
            histories[entity_id].append(bar)
        if session in fill_sessions:
            fill_opens[session] = {entity_id for entity_id, bar in session_bars.items() if bar.open > 0}
        window = schedule.get(session)
        if window is None:
            continue
        shortlist = _shortlist(histories, session)
        exclusion_reasons: Counter[str] = Counter()
        eligible: list[dict[str, Any]] = []
        evaluated = 0
        required = window["required_history_sessions"]
        for entity_id in sorted(shortlist):
            evaluated += 1
            lifetime = lifetimes.get(entity_id)
            if lifetime is None:
                exclusion_reasons["missing_lifetime"] += 1
                continue
            current_symbol = histories[entity_id][-1].symbol
            if current_symbol in _RESERVED:
                exclusion_reasons["reserved_role"] += 1
                continue
            if not _scanner_eligible(histories[entity_id], lifetime, session):
                exclusion_reasons["scanner_ineligible"] += 1
                continue
            candidate, reason = _reconstruct_candidate(histories[entity_id], required, lifetime)
            if candidate is None:
                exclusion_reasons[reason] += 1
                continue
            eligible.append(candidate)
        eligible.sort(
            key=lambda item: (
                -Decimal(item["volume_rank"]),
                -Decimal(item["intensity"]),
                item["entity_id"],
            )
        )
        selected = eligible[:6]
        target_weight = min(Decimal("0.10"), Decimal("0.55") / Decimal(len(selected))) if selected else _ZERO
        targets: list[dict[str, Any]] = []
        for candidate_rank, item in enumerate(selected, start=1):
            target = dict(item)
            target.update(
                candidate_rank=candidate_rank,
                target_weight=_canonical_decimal(target_weight),
                fill_available=None,
                fill_exclusion_reason=None,
            )
            targets.append(target)
        exclusion_payload = dict(sorted(exclusion_reasons.items()))
        payload = {key: value for key, value in window.items() if key != "required_history_sessions"}
        payload.update(
            evaluated_count=evaluated,
            exclusion_reason_counts=exclusion_payload,
            exclusions_sha256=_sha256_json(exclusion_payload),
            eligible=eligible,
            targets=targets,
            terminal_events=[],
        )
        pending.append(PendingDecision(payload=payload, fill_session=date.fromisoformat(window["fill_session"])))

    decisions: list[dict[str, Any]] = []
    for pending_decision in pending:
        available_symbols = fill_opens.get(pending_decision.fill_session, set())
        for target in pending_decision.payload["targets"]:
            lifetime = lifetimes[target["entity_id"]]
            active = not lifetime.unresolved and lifetime.active(pending_decision.fill_session)
            opening_print = target["entity_id"] in available_symbols
            target["fill_available"] = bool(active and opening_print)
            target["fill_exclusion_reason"] = (
                None if active and opening_print else ("inactive_at_fill" if not active else "missing_first_open_bar")
            )
        decisions.append(pending_decision.payload)

    for index, decision in enumerate(decisions):
        next_fill = date.fromisoformat(decisions[index + 1]["fill_session"]) if index + 1 < len(decisions) else None
        fill_session = date.fromisoformat(decision["fill_session"])
        terminal_events: list[dict[str, str]] = []
        for target in decision["targets"]:
            if not target["fill_available"]:
                continue
            lifetime = lifetimes[target["entity_id"]]
            terminal_session = lifetime.last_observed_bar
            if lifetime.terminal_event_class is None or terminal_session < fill_session:
                continue
            if next_fill is not None and terminal_session >= next_fill:
                continue
            terminal_events.append(
                {
                    "entity_id": lifetime.entity_id,
                    "symbol": lifetime.canonical_symbol,
                    "terminal_session": terminal_session.isoformat(),
                    "event_class": lifetime.terminal_event_class,
                }
            )
        decision["terminal_events"] = sorted(
            terminal_events,
            key=lambda event: (event["terminal_session"], event["entity_id"]),
        )

    return decisions, {
        "bars_rows_through_endpoint": rows,
        "sessions_through_endpoint": session_count,
        "lifetime_records": len(lifetimes),
        "decision_count": len(decisions),
        "reserved_target_count": sum(
            target["symbol"] in _RESERVED for decision in decisions for target in decision["targets"]
        ),
    }


def _boundary_fixtures() -> list[dict[str, Any]]:
    fixtures: list[tuple[str, list[Decimal], Decimal]] = [
        ("distinct_1_to_49", [Decimal(item) for item in range(1, 50)], Decimal(44)),
        ("five_high_outliers", [Decimal(1)] * 44 + [Decimal(100)] * 5, Decimal(1)),
        ("heavy_ties", [Decimal(7)] * 49, Decimal(7)),
        ("single_extreme_outlier", [Decimal(item) for item in range(1, 49)] + [Decimal("1e30")], Decimal(44)),
    ]
    results: list[dict[str, Any]] = []
    for name, values, expected in fixtures:
        actual = _q90_x44(values)
        results.append(
            {
                "name": name,
                "expected": _canonical_decimal(expected),
                "actual": _canonical_decimal(actual),
                "pass": actual == expected,
            }
        )
    threshold = Decimal(44)
    for name, formation, relation in (
        ("formation_just_below", Decimal("43.999"), "below"),
        ("formation_equal", Decimal(44), "equal"),
        ("formation_just_above", Decimal("44.001"), "above"),
    ):
        actual_relation = "below" if formation < threshold else ("above" if formation > threshold else "equal")
        results.append(
            {"name": name, "expected": relation, "actual": actual_relation, "pass": relation == actual_relation}
        )
    return results


_CORE_CANDIDATE_FIELDS = (
    "entity_id",
    "symbol",
    "volume_rank",
    "intensity",
    "formation_return",
    "return_rank",
)
_CORE_TARGET_FIELDS = (
    *_CORE_CANDIDATE_FIELDS,
    "candidate_rank",
    "target_weight",
    "fill_available",
    "fill_exclusion_reason",
)


def _core_record(record: Mapping[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    core: dict[str, Any] = {}
    numeric = {"volume_rank", "intensity", "formation_return", "return_rank", "target_weight"}
    for field in fields:
        value = record.get(field)
        core[field] = _canonical_decimal(Decimal(str(value))) if field in numeric and value is not None else value
    return core


def _comparison_view(decisions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    view: list[dict[str, Any]] = []
    for decision in decisions:
        view.append(
            {
                "decision_session": decision.get("decision_session"),
                "decision_timestamp": decision.get("decision_timestamp"),
                "fill_session": decision.get("fill_session"),
                "fill_timestamp": decision.get("fill_timestamp"),
                "eligible": sorted(
                    (_core_record(item, _CORE_CANDIDATE_FIELDS) for item in decision.get("eligible", ())),
                    key=lambda item: str(item["entity_id"]),
                ),
                "targets": sorted(
                    (_core_record(item, _CORE_TARGET_FIELDS) for item in decision.get("targets", ())),
                    key=lambda item: int(item["candidate_rank"]),
                ),
                "terminal_events": sorted(
                    decision.get("terminal_events", ()),
                    key=lambda item: (str(item["terminal_session"]), str(item["entity_id"])),
                ),
            }
        )
    return view


def verify(
    *,
    bars_path: Path,
    lifetimes_path: Path,
    corporate_actions_path: Path,
    runner_output_path: Path,
) -> dict[str, Any]:
    runner = json.loads(runner_output_path.read_text(encoding="utf-8"))
    runner_decisions = runner.get("decisions")
    if not isinstance(runner_decisions, list):
        raise VerificationError("runner output has no decisions list")
    decisions, coverage = _independent_reconstruction(bars_path, lifetimes_path)
    runner_view = _comparison_view(runner_decisions)
    verifier_view = _comparison_view(decisions)
    mismatched_sessions: list[str] = []
    runner_by_session = {str(item.get("decision_session")): item for item in runner_view}
    verifier_by_session = {str(item.get("decision_session")): item for item in verifier_view}
    for session in sorted(set(runner_by_session) | set(verifier_by_session)):
        if runner_by_session.get(session) != verifier_by_session.get(session):
            mismatched_sessions.append(session)
    boundary = _boundary_fixtures()
    schedules_match = all(item["fill_session"] > item["decision_session"] for item in verifier_view)
    runner_economics = int(runner.get("economic_outputs_produced", -1))
    passed = (
        not mismatched_sessions
        and all(item["pass"] for item in boundary)
        and schedules_match
        and coverage["reserved_target_count"] == 0
        and runner_economics == 0
    )
    return {
        "schema_version": 2,
        "verifier": "independent-readiness-v2-verifier-v1",
        "candidate": "AV49_D1_NORMALRET_M1_LONG / DESIGN_V2",
        "status": "PASS" if passed else "FAIL",
        "runner_output": str(runner_output_path),
        "inputs": {
            "bars": {"path": str(bars_path), "sha256": _sha256_file(bars_path)},
            "lifetimes": {"path": str(lifetimes_path), "sha256": _sha256_file(lifetimes_path)},
            "corporate_actions": {
                "path": str(corporate_actions_path),
                "sha256": _sha256_file(corporate_actions_path),
            },
        },
        "coverage": coverage,
        "quantile_rule": {"sample_size": 49, "zero_based_index": 43, "one_based_order_statistic": 44},
        "boundary_fixtures": boundary,
        "comparison": {
            "runner_decision_count": len(runner_view),
            "verifier_decision_count": len(verifier_view),
            "runner_reconstruction_sha256": runner.get("reconstruction_sha256"),
            "runner_comparison_sha256": _sha256_json(runner_view),
            "verifier_comparison_sha256": _sha256_json(verifier_view),
            "exact_structural_match": runner_view == verifier_view,
            "mismatched_decision_sessions": mismatched_sessions,
        },
        "gates": {
            "quantile_uniqueness": all(item["pass"] for item in boundary),
            "month_end_first_open_schedule": schedules_match,
            "reserved_roles_zero_targets": coverage["reserved_target_count"] == 0,
            "runner_economic_outputs_zero": runner_economics == 0,
            "independent_structural_match": not mismatched_sessions,
        },
        "economic_outputs_produced": 0,
        "independent_reconstruction_sha256": _sha256_json(decisions),
        "evidence_retention": "Runner retains full per-boundary evidence; verifier retains independent hashes and exact comparison results.",
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=Path, required=True)
    parser.add_argument("--lifetimes", type=Path, required=True)
    parser.add_argument("--corporate-actions", type=Path, required=True)
    parser.add_argument("--runner-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    with localcontext() as context:
        context.prec = 50
        result = verify(
            bars_path=args.bars,
            lifetimes_path=args.lifetimes,
            corporate_actions_path=args.corporate_actions,
            runner_output_path=args.runner_output,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
