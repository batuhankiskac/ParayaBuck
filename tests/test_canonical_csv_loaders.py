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
    old_rate: str = "",
    new_rate: str = "",
    symbol: str = "ACNB",
) -> tuple[str, ...]:
    return (
        action_type,
        symbol,
        ex_date,
        "2025-05-30",
        payable_date,
        rate,
        old_rate,
        new_rate,
        symbol,
        symbol,
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
        _canonical_action_row("stock_dividends", rate="1.05", raw_id="raw-4"),
        # Same-ex-date splits are already reflected in that day's bars, so
        # they do not rescale the dividend.
        _canonical_action_row("forward_splits", rate="", old_rate="1", new_rate="2", raw_id="raw-5"),
        _canonical_action_row("reverse_splits", rate="", old_rate="10", new_rate="1", raw_id="raw-6"),
    ]
    _write_csv(path, _CANONICAL_ACTION_HEADER, rows)

    actions = load_csv_corporate_actions(path)

    assert len(actions) == 2
    assert [action.value for action in actions] == pytest.approx([0.34, 0.12])
    assert all(action.symbol == "ACNB" for action in actions)
    assert all(action.timestamp == datetime(2025, 5, 30, tzinfo=UTC) for action in actions)
    assert all(action.action_type == CorporateActionType.DIVIDEND for action in actions)


def test_canonical_dividends_are_rescaled_by_later_splits_to_adjusted_shares(tmp_path: Path) -> None:
    # Bars are split-adjusted but v5 rates are raw per share: a dividend
    # before a later 2:1 forward and 10:1 reverse split is paid on
    # 2 / 10 = 0.2x as many adjusted shares' worth, so its per-adjusted-share
    # rate is raw / (2 * 0.1) = 5x raw. Later dividends and other symbols'
    # splits leave the rate unchanged.
    path = tmp_path / "split_adjusted.csv"
    rows = [
        _canonical_action_row("cash_dividends", ex_date="2020-01-10", rate="0.50", raw_id="d1"),
        _canonical_action_row("forward_splits", ex_date="2021-01-11", rate="", old_rate="1", new_rate="2", raw_id="s1"),
        _canonical_action_row("cash_dividends", ex_date="2021-06-10", rate="0.30", raw_id="d2"),
        _canonical_action_row(
            "reverse_splits", ex_date="2022-01-10", rate="", old_rate="10", new_rate="1", raw_id="s2"
        ),
        _canonical_action_row("cash_dividends", ex_date="2023-01-10", rate="0.40", raw_id="d3"),
        _canonical_action_row(
            "forward_splits",
            ex_date="2024-01-10",
            rate="",
            old_rate="1",
            new_rate="4",
            symbol="SPY",
            raw_id="s3",
        ),
    ]
    _write_csv(path, _CANONICAL_ACTION_HEADER, rows)

    actions = load_csv_corporate_actions(path)

    assert [action.value for action in actions] == pytest.approx([0.50 * 5, 0.30 * 10, 0.40])


def test_canonical_cash_echo_of_a_stock_dividend_is_not_credited(tmp_path: Path) -> None:
    # v5 repeats each stock dividend as a cash_dividends row whose rate is the
    # share ratio (CBSH 2020-12-01: real 0.27 cash plus a 1.05 echo of the 5%
    # stock dividend), sometimes re-rounded (CRESY 1.0098549 vs 1.00985).
    path = tmp_path / "stock_dividend_echo.csv"
    rows = [
        _canonical_action_row("cash_dividends", ex_date="2020-12-01", rate="0.27", raw_id="c1"),
        _canonical_action_row("cash_dividends", ex_date="2020-12-01", rate="1.05", raw_id="c2"),
        _canonical_action_row("stock_dividends", ex_date="2020-12-01", rate="1.05", raw_id="s1"),
        _canonical_action_row("cash_dividends", ex_date="2023-12-06", rate="1.0098549", raw_id="c3"),
        _canonical_action_row("stock_dividends", ex_date="2023-12-06", rate="1.00985", raw_id="s2"),
        # Same ratio-like amount without a stock dividend that day is real cash.
        _canonical_action_row("cash_dividends", ex_date="2021-03-01", rate="1.05", raw_id="c4"),
    ]
    _write_csv(path, _CANONICAL_ACTION_HEADER, rows)

    actions = load_csv_corporate_actions(path)

    assert [(action.timestamp.date().isoformat(), action.value) for action in actions] == [
        ("2020-12-01", pytest.approx(0.27)),
        ("2021-03-01", pytest.approx(1.05)),
    ]


def test_repeated_canonical_split_row_rescales_dividends_once(tmp_path: Path) -> None:
    path = tmp_path / "repeated_split.csv"
    rows = [
        _canonical_action_row("cash_dividends", ex_date="2020-01-10", rate="0.50", raw_id="d1"),
        _canonical_action_row("forward_splits", ex_date="2021-01-11", rate="", old_rate="1", new_rate="2", raw_id="s1"),
        _canonical_action_row("forward_splits", ex_date="2021-01-11", rate="", old_rate="1", new_rate="2", raw_id="s2"),
    ]
    _write_csv(path, _CANONICAL_ACTION_HEADER, rows)

    actions = load_csv_corporate_actions(path)

    assert [action.value for action in actions] == pytest.approx([0.25])


def test_canonical_split_rows_with_invalid_ratio_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "bad_split.csv"
    rows = [_canonical_action_row("forward_splits", rate="", old_rate="", new_rate="2", raw_id="s1")]
    _write_csv(path, _CANONICAL_ACTION_HEADER, rows)

    with pytest.raises(ValueError, match="invalid value"):
        load_csv_corporate_actions(path)


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


