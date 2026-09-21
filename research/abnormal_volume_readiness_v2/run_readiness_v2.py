#!/usr/bin/env python3
"""Causal, structural-only G1 reconstruction for abnormal-volume DESIGN_V2.

The runner reads the canonical split-adjusted daily bars once, retaining only
bounded trailing histories.  It never computes a post-formation return,
portfolio value, P&L, or performance statistic.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from pandas_market_calendars import get_calendar  # noqa: E402

from parayabuck.backtest.lifetimes import (  # noqa: E402
    EntityLifetimeRecord,
    LifetimeLoadResult,
    load_entity_lifetimes,
)
from parayabuck.config import Config, load_config  # noqa: E402
from parayabuck.core.models import Bar, Market  # noqa: E402
from parayabuck.markets.month_end import MonthEndFirstOpen, month_end_first_open  # noqa: E402
from parayabuck.universe.scanner import (  # noqa: E402
    EquityCandidate,
    EquityUniverseScanner,
    equity_candidate_from_bars,
)

try:  # Script execution and namespace-package import both remain supported.
    from .readiness_models import (
        RESERVED_ROLES,
        ResearchBar,
        SignalRecord,
        decimal_text,
        rank_signals,
        reconstruct_signal,
        target_weight,
    )
except ImportError:  # pragma: no cover - exercised by direct CLI invocation
    from readiness_models import (
        RESERVED_ROLES,
        ResearchBar,
        SignalRecord,
        decimal_text,
        rank_signals,
        reconstruct_signal,
        target_weight,
    )

BARS_PATH = ROOT / "data/historical/equity_pit_symmetric_2016_2026_v5.csv"
LIFETIMES_PATH = ROOT / "data/historical/equity_entity_lifetimes_full_v5.csv"
CORPORATE_ACTIONS_PATH = ROOT / "data/historical/equity_survivorship_full_corporate_actions_v5.csv"
CONFIG_PATH = ROOT / "config/settings.yaml"
OUTPUT_PATH = ROOT / "research/abnormal_volume_readiness_v2/RUNNER_OUTPUT.json"

CANDIDATE = "AV49_D1_NORMALRET_M1_LONG / DESIGN_V2"
CANONICAL_ENDPOINT = date(2026, 7, 30)
INITIAL_FORMATION_MONTH = (2016, 12)
NEW_YORK = ZoneInfo("America/New_York")
BAR_FIELDS = ("timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume", "market")
CORPORATE_ACTION_FIELDS = (
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
)


class ReadinessInputError(ValueError):
    """Canonical structural input is malformed or violates a frozen contract."""


@dataclass(frozen=True, slots=True)
class IdentityResolver:
    records_by_entity: dict[str, EntityLifetimeRecord]
    aliases: dict[str, tuple[EntityLifetimeRecord, ...]]
    unresolved_entity_ids: frozenset[str]

    @classmethod
    def from_result(cls, result: LifetimeLoadResult) -> IdentityResolver:
        aliases: dict[str, list[EntityLifetimeRecord]] = defaultdict(list)
        for record in result.records:
            symbols = {record.canonical_symbol, record.historical_symbol}
            symbols.update(interval.symbol for interval in record.rename_chain)
            for symbol in symbols:
                aliases[symbol].append(record)
        return cls(
            records_by_entity={record.entity_id: record for record in result.records},
            aliases={symbol: tuple(records) for symbol, records in aliases.items()},
            unresolved_entity_ids=frozenset(item.entity_id for item in result.unresolved),
        )

    def resolve(self, symbol: str, session: date) -> EntityLifetimeRecord:
        matches = [record for record in self.aliases.get(symbol, ()) if _alias_valid(record, symbol, session)]
        if len(matches) != 1:
            raise ReadinessInputError(
                f"identity resolution for {symbol} on {session.isoformat()} produced {len(matches)} matches"
            )
        return matches[0]

    def active_at(self, record: EntityLifetimeRecord, session: date) -> bool:
        if record.entity_id in self.unresolved_entity_ids:
            return False
        if session < _session_date(record.valid_from):
            return False
        return record.valid_to is None or session <= _session_date(record.valid_to)


def _session_date(timestamp: datetime) -> date:
    return timestamp.astimezone(NEW_YORK).date()


def _alias_valid(record: EntityLifetimeRecord, symbol: str, session: date) -> bool:
    # The canonical bar artifact may retrospectively label the whole chain by
    # its terminal canonical symbol (META), while the interval chain remains
    # available to resolve contemporaneous aliases (FB).  Both map to the same
    # stable entity; neither creates a second history.
    if symbol == record.canonical_symbol:
        return True
    if record.rename_chain:
        for interval in record.rename_chain:
            if interval.symbol != symbol:
                continue
            if interval.valid_from is not None and session < interval.valid_from:
                continue
            if interval.valid_to is not None and session > interval.valid_to:
                continue
            return True
        return False
    return symbol in {record.canonical_symbol, record.historical_symbol}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _parse_timestamp(value: str, row_number: int) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReadinessInputError(f"bars row {row_number}: invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReadinessInputError(f"bars row {row_number}: timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _parse_decimal(value: str | None, field: str, row_number: int) -> Decimal:
    if value is None or not value.strip():
        raise ReadinessInputError(f"bars row {row_number}: blank {field}")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ReadinessInputError(f"bars row {row_number}: nonnumeric {field}") from exc
    if not parsed.is_finite():
        raise ReadinessInputError(f"bars row {row_number}: nonfinite {field}")
    return parsed


def _calendar_windows(endpoint: date) -> tuple[MonthEndFirstOpen, ...]:
    windows: list[MonthEndFirstOpen] = []
    year, month = INITIAL_FORMATION_MONTH
    while True:
        window = month_end_first_open(year, month)
        if window.decision_session > endpoint or window.fill_session > endpoint:
            break
        windows.append(window)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return tuple(windows)


def _expected_sessions(endpoint: date) -> tuple[date, ...]:
    schedule = get_calendar("NYSE").schedule(start_date=date(2015, 9, 1), end_date=endpoint)
    return tuple(index.date() for index in schedule.index)


def _validate_corporate_action_schema(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        try:
            header = tuple(next(reader))
        except StopIteration as exc:
            raise ReadinessInputError("corporate-action CSV is empty") from exc
        if header != CORPORATE_ACTION_FIELDS or len(header) != len(set(header)):
            raise ReadinessInputError("unexpected corporate-action CSV schema")
        return sum(1 for _row in reader)


def _as_production_bar(bar: ResearchBar) -> Bar:
    return Bar(
        timestamp=bar.timestamp,
        symbol=bar.symbol,
        timeframe="1d",
        open=float(bar.open),
        high=float(bar.high),
        low=float(bar.low),
        close=float(bar.close),
        volume=float(bar.volume),
        market=Market.EQUITY,
    )


def _median_dollar_volume(history: Sequence[ResearchBar]) -> float:
    return median([float(bar.close) * float(bar.volume) for bar in history[-20:]]) if history else 0.0


def _scanner_candidates(
    histories: dict[str, deque[ResearchBar]],
    resolver: IdentityResolver,
    decision_session: date,
    config: Config,
) -> tuple[list[EquityCandidate], dict[str, str], dict[str, str]]:
    """Reproduce the production Stage-A shortlist and scanner candidate inputs."""

    cutoff = decision_session - timedelta(days=60)
    trailing: list[tuple[str, tuple[ResearchBar, ...]]] = []
    for entity_id, history in histories.items():
        selected = tuple(bar for bar in history if cutoff < bar.session <= decision_session)
        if selected:
            trailing.append((entity_id, selected))
    trailing.sort(key=lambda item: (-_median_dollar_volume(item[1]), item[1][-1].symbol))
    shortlisted = trailing[: config.universe.equity.target_shortlist]

    candidates: list[EquityCandidate] = []
    symbol_to_entity: dict[str, str] = {}
    prefilter_reasons: dict[str, str] = {}
    for entity_id, candidate_history in shortlisted:
        latest = candidate_history[-1]
        record = resolver.records_by_entity[entity_id]
        symbol_to_entity[latest.symbol] = entity_id
        active = resolver.active_at(record, decision_session) and latest.session == decision_session
        if record.entity_id in resolver.unresolved_entity_ids:
            prefilter_reasons[entity_id] = "unresolved_lifecycle"
        elif latest.session != decision_session:
            prefilter_reasons[entity_id] = "no_decision_session_bar"
        elif not resolver.active_at(record, decision_session):
            prefilter_reasons[entity_id] = "inactive_at_decision"
        candidates.append(
            equity_candidate_from_bars(
                latest.symbol,
                [_as_production_bar(bar) for bar in candidate_history],
                expected_bar_count=20,
                exchange=record.exchange,
                tradable=active,
                status="active" if active else "inactive",
            )
        )
    return candidates, symbol_to_entity, prefilter_reasons


def _exclusion_hash(rows: Iterable[tuple[str, str, str]]) -> str:
    return _canonical_sha256([list(row) for row in sorted(rows)])


def _decision_payload(
    window: MonthEndFirstOpen,
    histories: dict[str, deque[ResearchBar]],
    resolver: IdentityResolver,
    expected_51: tuple[date, ...],
    config: Config,
) -> dict[str, Any]:
    candidates, symbol_to_entity, prefilter = _scanner_candidates(histories, resolver, window.decision_session, config)
    scanner = EquityUniverseScanner(config.universe.equity).scan(
        candidates,
        data_timestamp=window.decision_timestamp,
        selection_timestamp=window.fill_timestamp,
    )

    exclusions: list[tuple[str, str, str]] = []
    for symbol, reason in scanner.eliminated.items():
        entity_id = symbol_to_entity.get(symbol, "UNRESOLVED")
        exclusions.append((entity_id, symbol, prefilter.get(entity_id, reason)))

    signals: list[SignalRecord] = []
    for symbol in scanner.selected_symbols:
        resolved_entity_id = symbol_to_entity.get(symbol)
        if resolved_entity_id is None:
            exclusions.append(("UNRESOLVED", symbol, "reserved_without_decision_history"))
            continue
        entity_id = resolved_entity_id
        if symbol in RESERVED_ROLES:
            exclusions.append((entity_id, symbol, "reserved_role"))
            continue
        history = tuple(histories[entity_id])
        if len(history) < 51:
            exclusions.append((entity_id, symbol, "insufficient_51_close_history"))
            continue
        signal_window = history[-51:]
        if tuple(bar.session for bar in signal_window) != expected_51:
            exclusions.append((entity_id, symbol, "missing_exchange_session_in_signal_window"))
            continue
        signal, reason = reconstruct_signal(signal_window)
        if signal is None:
            assert reason is not None
            exclusions.append((entity_id, symbol, reason))
            continue
        signals.append(signal)

    ranked = rank_signals(tuple(signals))
    weight = target_weight(len(ranked)) if ranked else None
    selected_ids = {record.entity_id for record in ranked}
    for signal in signals:
        if signal.entity_id not in selected_ids:
            exclusions.append((signal.entity_id, signal.symbol, "below_top_six"))

    eligible_payload = [record.to_dict() for record in sorted(signals, key=lambda item: item.entity_id)]
    targets: list[dict[str, Any]] = []
    for rank, record in enumerate(ranked, start=1):
        target = record.to_dict(include_evidence=True)
        target.update(
            {
                "candidate_rank": rank,
                "target_weight": decimal_text(weight) if weight is not None else None,
                "fill_available": False,
                "fill_exclusion_reason": "fill_bar_not_observed",
            }
        )
        targets.append(target)
    reason_counts = Counter(reason for _entity, _symbol, reason in exclusions)
    return {
        "decision_session": window.decision_session.isoformat(),
        "decision_timestamp": window.decision_timestamp.isoformat(),
        "fill_session": window.fill_session.isoformat(),
        "fill_timestamp": window.fill_timestamp.isoformat(),
        "scanner_data_timestamp": window.decision_timestamp.isoformat(),
        "scanner_selection_timestamp": window.fill_timestamp.isoformat(),
        "scanner_candidate_count": len(scanner.candidate_symbols),
        "scanner_pool_count": len(scanner.selected_symbols),
        "evaluated_count": len(scanner.selected_symbols),
        "exclusion_reason_counts": dict(sorted(reason_counts.items())),
        "exclusions_sha256": _exclusion_hash(exclusions),
        "eligible": eligible_payload,
        "targets": targets,
        "terminal_events": [],
    }


def _finalize_fill(
    decision: dict[str, Any],
    batch: dict[str, ResearchBar],
    resolver: IdentityResolver,
) -> None:
    fill_session = date.fromisoformat(str(decision["fill_session"]))
    for target in decision["targets"]:
        assert isinstance(target, dict)
        entity_id = str(target["entity_id"])
        record = resolver.records_by_entity[entity_id]
        bar = batch.get(entity_id)
        reason: str | None = None
        if not resolver.active_at(record, fill_session):
            reason = "inactive_at_fill"
        elif _session_date(record.last_observed_bar) < fill_session:
            reason = "terminated_between_decision_and_fill"
        elif bar is None or bar.session != fill_session:
            reason = "missing_first_open_bar"
        elif bar.open <= 0:
            reason = "nonpositive_first_open"
        target["fill_available"] = reason is None
        target["fill_exclusion_reason"] = reason


def _attach_terminal_events(
    decisions: list[dict[str, Any]],
    resolver: IdentityResolver,
) -> None:
    for index, decision in enumerate(decisions):
        next_fill = (
            date.fromisoformat(str(decisions[index + 1]["fill_session"])) if index + 1 < len(decisions) else None
        )
        fill_session = date.fromisoformat(str(decision["fill_session"]))
        events: list[dict[str, str]] = []
        for target in decision["targets"]:
            if not target["fill_available"]:
                continue
            record = resolver.records_by_entity[str(target["entity_id"])]
            if record.terminal_event_class is None:
                continue
            terminal_session = _session_date(record.last_observed_bar)
            if terminal_session < fill_session or (next_fill is not None and terminal_session >= next_fill):
                continue
            events.append(
                {
                    "entity_id": record.entity_id,
                    "symbol": record.canonical_symbol,
                    "terminal_session": terminal_session.isoformat(),
                    "event_class": record.terminal_event_class,
                }
            )
        decision["terminal_events"] = sorted(events, key=lambda item: (item["terminal_session"], item["entity_id"]))


def run_readiness(
    bars_path: Path,
    lifetimes_path: Path,
    corporate_actions_path: Path,
    *,
    endpoint: date = CANONICAL_ENDPOINT,
    config_path: Path = CONFIG_PATH,
) -> dict[str, Any]:
    """Run fresh G1 reconstruction and return a JSON-safe structural payload."""

    config = load_config(str(config_path))
    lifetimes = load_entity_lifetimes(lifetimes_path)
    resolver = IdentityResolver.from_result(lifetimes)
    corporate_action_rows = _validate_corporate_action_schema(corporate_actions_path)
    windows = _calendar_windows(endpoint)
    window_by_decision = {window.decision_session: window for window in windows}
    decision_by_fill: dict[date, dict[str, Any]] = {}
    sessions = _expected_sessions(endpoint)
    session_index = {session: index for index, session in enumerate(sessions)}

    histories: dict[str, deque[ResearchBar]] = defaultdict(lambda: deque(maxlen=80))
    decisions: list[dict[str, Any]] = []
    batch: dict[str, ResearchBar] = {}
    batch_session: date | None = None
    last_timestamp: datetime | None = None
    row_count = 0
    zero_volume_rows = 0

    def finish_batch() -> None:
        nonlocal batch, batch_session
        if batch_session is None:
            return
        for entity_id, bar in batch.items():
            histories[entity_id].append(bar)
        fill_decision = decision_by_fill.get(batch_session)
        if fill_decision is not None:
            _finalize_fill(fill_decision, batch, resolver)
        window = window_by_decision.get(batch_session)
        if window is not None:
            index = session_index.get(batch_session)
            if index is None or index < 50:
                raise ReadinessInputError(f"cannot reconstruct 51 exchange sessions at {batch_session}")
            payload = _decision_payload(
                window,
                histories,
                resolver,
                sessions[index - 50 : index + 1],
                config,
            )
            decisions.append(payload)
            decision_by_fill[window.fill_session] = payload
        batch = {}
        batch_session = None

    with bars_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        if fields != BAR_FIELDS or len(fields) != len(set(fields)):
            raise ReadinessInputError("unexpected canonical bar CSV schema")
        for row_number, raw in enumerate(reader, start=2):
            timestamp = _parse_timestamp(str(raw.get("timestamp") or ""), row_number)
            session = _session_date(timestamp)
            if session > endpoint:
                break  # The partial 2026-07-31 cross-section is not consumed.
            if last_timestamp is not None and timestamp < last_timestamp:
                raise ReadinessInputError(f"bars row {row_number}: input not timestamp-sorted")
            timeframe = str(raw.get("timeframe") or "").strip().lower()
            market = str(raw.get("market") or "").strip().lower()
            if timeframe not in {"1d", "1day"} or market != "equity":
                raise ReadinessInputError(f"bars row {row_number}: expected equity 1d row")
            symbol = str(raw.get("symbol") or "").strip()
            if not symbol:
                raise ReadinessInputError(f"bars row {row_number}: blank symbol")
            record = resolver.resolve(symbol, session)
            values = {
                name: _parse_decimal(raw.get(name), name, row_number)
                for name in ("open", "high", "low", "close", "volume")
            }
            bar = ResearchBar(
                timestamp=timestamp,
                session=session,
                symbol=symbol,
                entity_id=record.entity_id,
                **values,
            )
            if batch_session is not None and session != batch_session:
                finish_batch()
            if batch_session is None:
                batch_session = session
            if record.entity_id in batch:
                raise ReadinessInputError(f"bars row {row_number}: duplicate entity/session")
            batch[record.entity_id] = bar
            row_count += 1
            zero_volume_rows += int(bar.volume == 0)
            last_timestamp = timestamp
    finish_batch()

    if len(decisions) != len(windows):
        raise ReadinessInputError(f"reconstructed {len(decisions)} of {len(windows)} scheduled decisions")
    _attach_terminal_events(decisions, resolver)
    structural = {
        "candidate": CANDIDATE,
        "endpoint": endpoint.isoformat(),
        "decisions": decisions,
    }
    return {
        "schema_version": 2,
        "candidate": CANDIDATE,
        "endpoint": endpoint.isoformat(),
        "inputs": {
            "bars": {"path": str(bars_path), "sha256": _file_sha256(bars_path)},
            "lifetimes": {"path": str(lifetimes_path), "sha256": lifetimes.receipt.source_sha256},
            "corporate_actions": {
                "path": str(corporate_actions_path),
                "sha256": _file_sha256(corporate_actions_path),
                "row_count": corporate_action_rows,
                "used_for_signal": False,
                "note": "Bars are split-adjusted; manifest is identity evidence only.",
            },
        },
        "data_validation": {
            "status": "PASS",
            "rows_consumed_through_endpoint": row_count,
            "zero_volume_rows": zero_volume_rows,
            "blank_nonnumeric_nonfinite_negative_volume_rows": 0,
            "partial_2026_07_31_rows_consumed": 0,
            "lifetime_row_count": lifetimes.receipt.row_count,
            "unresolved_lifetime_count": lifetimes.receipt.unresolved_count,
        },
        "decisions": decisions,
        "reconstruction_sha256": _canonical_sha256(structural),
        "economic_outputs_produced": 0,
        "economic_fields": [],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=Path, default=BARS_PATH)
    parser.add_argument("--lifetimes", type=Path, default=LIFETIMES_PATH)
    parser.add_argument("--corporate-actions", type=Path, default=CORPORATE_ACTIONS_PATH)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--endpoint", type=date.fromisoformat, default=CANONICAL_ENDPOINT)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = run_readiness(
        args.bars,
        args.lifetimes,
        args.corporate_actions,
        endpoint=args.endpoint,
        config_path=args.config,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(f"wrote {args.output} ({len(payload['decisions'])} decisions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
