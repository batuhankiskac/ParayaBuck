from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

import pytest

from parayabuck.backtest.product import load_csv_bars, load_csv_corporate_actions
from parayabuck.core.models import Market
from parayabuck.data.market_dataset import CorporateActionType

_BAR_HEADER = ("timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume", "market")
_CANONICAL_ACTION_HEADER = (
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


def _write_csv(path: Path, header: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _bar_row(timeframe: str, *, timestamp: str = "2025-01-06T05:00:00+00:00") -> tuple[str, ...]:
    return (timestamp, "SPY", timeframe, "100", "101", "99", "100.5", "1000", "equity")


def _canonical_action_row(
    action_type: str,
    *,
    ex_date: str = "2025-05-30",
    rate: str = "0.34",
    payable_date: str = "2025-06-12",
    raw_id: str = "raw-1",
) -> tuple[str, ...]:
    return (
        action_type,
        "ACNB",
        ex_date,
        "2025-05-30",
        payable_date,
        rate,
        "",
        "",
        "ACNB",
        "ACNB",
        raw_id,
    )


def test_daily_timeframe_aliases_normalize_without_discarding_rows(tmp_path: Path) -> None:
    path = tmp_path / "daily.csv"
    _write_csv(path, _BAR_HEADER, [_bar_row("1d"), _bar_row("1Day", timestamp="2025-01-07T05:00:00Z")])

    bars = load_csv_bars(path, default_market=Market.EQUITY)

    assert len(bars) == 2
    assert [bar.timeframe for bar in bars] == ["1d", "1d"]


def test_canonical_intraday_timeframe_remains_supported(tmp_path: Path) -> None:
    path = tmp_path / "intraday.csv"
    _write_csv(path, _BAR_HEADER, [_bar_row("1h", timestamp="2025-01-06T14:30:00+00:00")])

    bars = load_csv_bars(path, default_market=Market.EQUITY)

    assert len(bars) == 1
    assert bars[0].timeframe == "1h"


@pytest.mark.parametrize("timeframe", ["1D", "1DAY", "day1", "1Day "])
def test_unknown_timeframe_spellings_fail_closed(tmp_path: Path, timeframe: str) -> None:
    path = tmp_path / "unsupported.csv"
    _write_csv(path, _BAR_HEADER, [_bar_row(timeframe)])

    with pytest.raises(ValueError, match="Unsupported CSV timeframe"):
        load_csv_bars(path, default_market=Market.EQUITY)


def test_legacy_dividend_schema_is_preserved_and_deduplicated(tmp_path: Path) -> None:
    path = tmp_path / "legacy.csv"
    header = ("timestamp", "symbol", "action_type", "value")
    duplicate = ("2025-05-30T00:00:00Z", "spy", "dividend", "0.50")
    _write_csv(path, header, [duplicate, duplicate])

    actions = load_csv_corporate_actions(path)

    assert len(actions) == 1
    assert actions[0].timestamp == datetime(2025, 5, 30, tzinfo=UTC)
    assert actions[0].symbol == "spy"
    assert actions[0].action_type == CorporateActionType.DIVIDEND
    assert actions[0].value == pytest.approx(0.5)


def test_canonical_actions_skip_splits_and_deduplicate_economic_dividends(tmp_path: Path) -> None:
    path = tmp_path / "canonical.csv"
    rows = [
        _canonical_action_row("cash_dividends"),
        _canonical_action_row(
            "cash_dividends",
            rate="0.340",
            payable_date="2025-06-13",
            raw_id="raw-2",
        ),
        _canonical_action_row("cash_dividends", rate="0.12", raw_id="raw-3"),
        _canonical_action_row("stock_dividends", raw_id="raw-4"),
        _canonical_action_row("forward_splits", raw_id="raw-5"),
        _canonical_action_row("reverse_splits", raw_id="raw-6"),
    ]
    _write_csv(path, _CANONICAL_ACTION_HEADER, rows)

    actions = load_csv_corporate_actions(path)

    assert len(actions) == 2
    assert [action.value for action in actions] == pytest.approx([0.34, 0.12])
    assert all(action.symbol == "ACNB" for action in actions)
    assert all(action.timestamp == datetime(2025, 5, 30, tzinfo=UTC) for action in actions)
    assert all(action.action_type == CorporateActionType.DIVIDEND for action in actions)


@pytest.mark.parametrize(
    ("action_type", "ex_date", "rate", "message"),
    [
        ("cash_mergers", "2025-05-30", "0.34", "Unsupported corporate-action type"),
        ("cash_dividends", "not-a-date", "0.34", "invalid ex_date"),
        ("cash_dividends", "2025-05-30", "nan", "finite and positive"),
        ("cash_dividends", "2025-05-30", "0", "finite and positive"),
    ],
)
def test_canonical_actions_fail_closed_on_unknown_or_malformed_rows(
    tmp_path: Path,
    action_type: str,
    ex_date: str,
    rate: str,
    message: str,
) -> None:
    path = tmp_path / "invalid.csv"
    _write_csv(path, _CANONICAL_ACTION_HEADER, [_canonical_action_row(action_type, ex_date=ex_date, rate=rate)])

    with pytest.raises(ValueError, match=message):
        load_csv_corporate_actions(path)


def test_incomplete_canonical_action_schema_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "incomplete.csv"
    _write_csv(path, ("action_type", "symbol", "ex_date", "rate"), [("cash_dividends", "SPY", "2025-05-30", "0.5")])

    with pytest.raises(ValueError, match="Canonical corporate-action CSV is missing required columns"):
        load_csv_corporate_actions(path)


def test_canonical_v5_action_receipt_has_expected_apply_once_count() -> None:
    path = Path("data/historical/equity_survivorship_full_corporate_actions_v5.csv")
    if not path.exists():
        pytest.skip("canonical v5 corporate actions are not available")

    actions = load_csv_corporate_actions(path)

    assert len(actions) == 141_751
    assert all(action.action_type == CorporateActionType.DIVIDEND for action in actions)
