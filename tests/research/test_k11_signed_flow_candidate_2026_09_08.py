from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scripts.research.k11_signed_flow_candidate_2026_09_08 import (
    LOOKBACK_HOURS,
    native_history_declaration,
    next_spot_bar_open,
    signed_flow_signal,
)


def _expectation(rows: list[dict[str, str]], instrument: str) -> dict[str, object]:
    ids = [int(row["agg_trade_id"]) for row in rows if row["instrument"] == instrument]
    return {
        "symbol_day": {"venue": "synthetic", "instrument": instrument, "event_date_utc": "2026-07-01"},
        "window": {"start_utc": "2026-07-01T00:00:00Z", "end_utc": "2026-07-02T00:00:00Z"},
        "archive": {
            "kind": "synthetic",
            "expected_row_count": len(ids),
            "first_agg_trade_id": min(ids),
            "last_agg_trade_id": max(ids),
            "expected_id_set": ids,
            "id_contract": "explicit_expected_id_set",
        },
    }


def _rows() -> list[dict[str, str]]:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    rows: list[dict[str, str]] = []
    trade_id = 1
    for hour in range(LOOKBACK_HOURS):
        event = start + timedelta(hours=hour)
        available = event + timedelta(minutes=1)
        for instrument, maker_values in (("BTCUSDT", ("false", "true")), ("ETHUSDT", ("true", "false"))):
            for maker in maker_values:
                quantity = "2" if (instrument == "BTCUSDT" and maker == "false") or (instrument == "ETHUSDT" and maker == "true") else "1"
                rows.append(
                    {
                        "venue": "synthetic",
                        "instrument": instrument,
                        "event_time_utc": event.isoformat().replace("+00:00", "Z"),
                        "available_at": available.isoformat().replace("+00:00", "Z"),
                        "price": "100",
                        "quantity": quantity,
                        "is_buyer_maker": maker,
                        "source_file": "fixture.zip",
                        "agg_trade_id": str(trade_id),
                    }
                )
                trade_id += 1
    return rows


def _spot_bars(decision: datetime) -> list[dict[str, str]]:
    return [
        {
            "instrument": instrument,
            "bar_kind": "spot",
            "event_time_utc": (decision + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "open": "123",
            "available_at": (decision + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        }
        for instrument in ("BTCUSDT", "ETHUSDT")
    ]


def test_fixed_sign_and_threshold_are_multi_symbol_and_causal() -> None:
    rows = _rows()
    decision = datetime(2026, 7, 2, tzinfo=UTC)
    btc = signed_flow_signal(rows, "BTCUSDT", decision, _spot_bars(decision), _expectation(rows, "BTCUSDT"))
    eth = signed_flow_signal(rows, "ETHUSDT", decision, _spot_bars(decision), _expectation(rows, "ETHUSDT"))
    assert btc["status"] == "SIGNAL_READY"
    assert btc["expected_raw_rows"] == 48
    assert btc["signal"] == "long_spot"
    assert btc["execution"]["execution_timestamp"] == "2026-07-02T01:00:00Z"
    assert btc["execution"]["open"] == 123
    assert btc["imbalance_24h"] > 0
    assert eth["signal"] == "cash"
    assert eth["imbalance_24h"] < 0


def test_future_rows_and_late_availability_do_not_change_signal() -> None:
    rows = _rows()
    decision = datetime(2026, 7, 2, tzinfo=UTC)
    rows.append({**rows[-1], "event_time_utc": "2026-07-02T00:00:00Z", "available_at": "2026-07-02T00:01:00Z", "price": "1000000"})
    expectation = _expectation(rows, "BTCUSDT")
    assert signed_flow_signal(rows, "BTCUSDT", decision, _spot_bars(decision), expectation)["signal"] == "long_spot"
    rows[0]["available_at"] = "2026-07-02T00:01:00Z"
    blocked = signed_flow_signal(rows, "BTCUSDT", decision, expectation=expectation)
    assert blocked["status"] == "INCOMPLETE_CAUSAL_WINDOW"
    assert blocked["expected_raw_rows"] == 48
    assert blocked["late_rows"] == ["1"]


def test_missing_hour_fails_closed() -> None:
    rows = _rows()
    rows = [row for row in rows if not (row["instrument"] == "BTCUSDT" and row["event_time_utc"] == "2026-07-01T12:00:00Z")]
    result = signed_flow_signal(rows, "BTCUSDT", datetime(2026, 7, 2, tzinfo=UTC), expectation=_expectation(rows, "BTCUSDT"))
    assert result["status"] == "INCOMPLETE_CAUSAL_WINDOW"
    assert result["signal"] == "cash"


def test_omitted_trade_row_blocks_against_frozen_expected_set() -> None:
    rows = _rows()
    expectation = _expectation(rows, "BTCUSDT")
    omitted = [row for row in rows if row["agg_trade_id"] != "1"]
    result = signed_flow_signal(omitted, "BTCUSDT", datetime(2026, 7, 2, tzinfo=UTC), expectation=expectation)
    assert result["status"] == "EXPECTATION_BLOCKED"
    assert result["expectation_error"] == "EXPECTATION_ROW_COUNT_MISMATCH:47:48"


def test_expectation_is_bound_to_requested_symbol_and_window() -> None:
    rows = _rows()
    decision = datetime(2026, 7, 2, tzinfo=UTC)
    btc_expectation = _expectation(rows, "BTCUSDT")
    result = signed_flow_signal(rows, "ETHUSDT", decision, expectation=btc_expectation)
    assert result["status"] == "EXPECTATION_BLOCKED"
    assert result["expectation_error"] == "EXPECTATION_CALLER_SYMBOL_MISMATCH"
    shifted = signed_flow_signal(rows, "BTCUSDT", datetime(2026, 7, 2, 1, tzinfo=UTC), expectation=btc_expectation)
    assert shifted["expectation_error"] == "EXPECTATION_CALLER_WINDOW_MISMATCH"


def test_blank_available_at_blocks_even_with_early_archive_evidence() -> None:
    rows = [{**row, "available_at": ""} for row in _rows()]
    result = signed_flow_signal(rows, "BTCUSDT", datetime(2026, 7, 2, tzinfo=UTC), expectation=_expectation(rows, "BTCUSDT"))
    assert result["status"] == "INCOMPLETE_CAUSAL_WINDOW"
    assert len(result["missing_availability_rows"]) == 48


def test_actual_native_declaration_is_pilot_only() -> None:
    declaration = native_history_declaration()
    assert declaration["instruments"] == ["BTCUSDT"]
    assert declaration["causal_pilot_days"] == 1
    assert declaration["full_recent_economic_ready"] is False


def test_next_spot_bar_excludes_decision_timestamp_and_selects_next_open() -> None:
    decision = datetime(2026, 7, 2, tzinfo=UTC)
    bars = [
        {"instrument": "BTCUSDT", "bar_kind": "spot", "event_time_utc": "2026-07-02T00:00:00Z", "open": "99", "available_at": "2026-07-02T00:00:00Z"},
        {"instrument": "BTCUSDT", "bar_kind": "spot", "event_time_utc": "2026-07-02T01:00:00Z", "open": "101", "available_at": "2026-07-02T01:00:00Z"},
        {"instrument": "BTCUSDT", "bar_kind": "spot", "event_time_utc": "2026-07-02T02:00:00Z", "open": "102", "available_at": "2026-07-02T02:00:00Z"},
    ]
    result = next_spot_bar_open(bars, "BTCUSDT", decision)
    assert result["status"] == "EXECUTION_READY"
    assert result["execution_timestamp"] == "2026-07-02T01:00:00Z"
    assert result["open"] == 101.0


@pytest.mark.parametrize("opening", ["abc", "nan", "inf", "-inf"])
def test_malformed_spot_open_is_structured_block(opening: str) -> None:
    decision = datetime(2026, 7, 2, tzinfo=UTC)
    row = {
        "instrument": "BTCUSDT",
        "bar_kind": "spot",
        "event_time_utc": "2026-07-02T01:00:00Z",
        "open": opening,
        "available_at": "2026-07-02T01:00:00Z",
    }
    result = next_spot_bar_open([row], "BTCUSDT", decision)
    assert result["status"] == "INVALID_SPOT_BAR_OPEN"
    assert result["open"] is None


def test_empty_caller_or_bar_instrument_blocks() -> None:
    decision = datetime(2026, 7, 2, tzinfo=UTC)
    assert next_spot_bar_open([], " ", decision)["status"] == "INVALID_INSTRUMENT"
    row = {"instrument": " ", "bar_kind": "spot", "event_time_utc": "2026-07-02T01:00:00Z", "open": "101", "available_at": "2026-07-02T01:00:00Z"}
    assert next_spot_bar_open([row], "BTCUSDT", decision)["status"] == "INVALID_INSTRUMENT"


def test_naive_decision_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="naive_datetime:decision_time"):
        next_spot_bar_open([], "BTCUSDT", datetime(2026, 7, 2))
    with pytest.raises(ValueError, match="naive_datetime:decision_time"):
        signed_flow_signal(_rows(), "BTCUSDT", datetime(2026, 7, 2), expectation=_expectation(_rows(), "BTCUSDT"))


@pytest.mark.parametrize("kind", ["missing", "late", "ambiguous"])
def test_next_spot_bar_blocks_missing_late_or_ambiguous_open(kind: str) -> None:
    decision = datetime(2026, 7, 2, tzinfo=UTC)
    row = {"instrument": "BTCUSDT", "bar_kind": "spot", "event_time_utc": "2026-07-02T01:00:00Z", "open": "101", "available_at": "2026-07-02T01:00:00Z"}
    if kind == "missing":
        bars = []
    elif kind == "late":
        bars = [{**row, "available_at": "2026-07-02T01:00:01Z"}]
    else:
        bars = [row, {**row, "open": "102"}]
    result = next_spot_bar_open(bars, "BTCUSDT", decision)
    assert result["status"] == {"missing": "MISSING_SPOT_BAR", "late": "LATE_SPOT_BAR", "ambiguous": "AMBIGUOUS_SPOT_BAR"}[kind]


def test_signal_execution_is_blocked_when_spot_bars_are_absent() -> None:
    decision = datetime(2026, 7, 2, tzinfo=UTC)
    rows = _rows()
    result = signed_flow_signal(rows, "BTCUSDT", decision, [], _expectation(rows, "BTCUSDT"))
    assert result["status"] == "EXECUTION_BLOCKED"
    assert result["execution"]["status"] == "MISSING_SPOT_BAR"
