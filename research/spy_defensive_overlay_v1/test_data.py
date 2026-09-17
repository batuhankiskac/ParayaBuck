from __future__ import annotations

import json
from pathlib import Path

import pytest

from parayabuck.backtest.portfolio_engine import TerminalLifecycle
from parayabuck.data.market_dataset import CorporateActionType
from research.spy_defensive_overlay_v1.data import (
    ACTION_FILE,
    BAR_FILE,
    FROZEN_HASHES,
    LIFETIME_FILE,
    load_bars_csv,
    load_dividend_actions_csv,
    load_lifetimes_csv,
    sha256_file,
    validate_manifest_semantics,
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_bar_schema_parses_only_real_source_rows(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "bars.csv",
        "timestamp,symbol,timeframe,open,high,low,close,volume,market\n"
        "2024-11-07T05:00:00+00:00,SPY,1Day,590,592,588,591,2000,EQUITY\n"
        "2024-11-07T05:00:00+00:00,SH,1d,40,41,39,40.5,1000,equity\n",
    )

    bars = load_bars_csv(path, symbols={"SPY", "SH"})

    assert [(bar.symbol, bar.close) for bar in bars] == [("SPY", 591.0), ("SH", 40.5)]
    with pytest.raises(ValueError, match="Requested symbols have no real bars"):
        load_bars_csv(path, symbols={"SPY", "SH", "FAKE_SH"})


def test_bar_schema_skips_zero_placeholder_without_synthesizing_a_bar(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "bars.csv",
        "timestamp,symbol,timeframe,open,high,low,close,volume,market\n"
        "2024-01-02T05:00:00+00:00,SH,1d,0,0,0,0,0,equity\n"
        "2024-01-02T05:00:00+00:00,SPY,1d,470,472,469,471,1000,equity\n",
    )

    bars = load_bars_csv(path)

    assert [bar.symbol for bar in bars] == ["SPY"]


@pytest.mark.parametrize(
    "row",
    (
        "2024-01-02T05:00:00+00:00,SH,1d,0,1,0,1,10,equity",
        "2024-01-02T05:00:00+00:00,SH,1d,nan,1,1,1,10,equity",
        "2024-01-02T05:00:00+00:00,SH,1d,1,2,1,1,-1,equity",
        "2024-01-02T05:00:00+00:00,SH,1d,3,2,1,1,10,equity",
    ),
)
def test_bar_schema_rejects_malformed_non_placeholder_rows(tmp_path: Path, row: str) -> None:
    path = _write(
        tmp_path / "bars.csv",
        "timestamp,symbol,timeframe,open,high,low,close,volume,market\n" + row + "\n",
    )

    with pytest.raises(ValueError, match="bar row|OHLC"):
        load_bars_csv(path)


def test_lifetime_schema_returns_typed_terminal_lifecycle(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "lifetimes.csv",
        "entity_id,provider_asset_id,canonical_symbol,historical_symbol,valid_from,valid_to,"
        "first_observed_bar,last_observed_bar,current_status,exchange,rename_chain,evidence_class,"
        "pit_quality_tier,terminal_event_class,notes\n"
        "id-a,asset-a,AAA,AAA,2020-01-02T05:00:00+00:00,2021-01-04T05:00:00+00:00,"
        "2020-01-02T05:00:00+00:00,2021-01-04T05:00:00+00:00,inactive,NYSE,,"
        "POSSIBLE_DELISTING,TIER_A_VERIFIED_PIT,POSSIBLE_DELISTING,test\n"
        "id-sh,asset-sh,SH,SH,2016-01-04T05:00:00+00:00,,2016-01-04T05:00:00+00:00,"
        "2026-07-30T04:00:00+00:00,active,ARCA,,FINAL_SURVIVOR,TIER_A_VERIFIED_PIT,,test\n",
    )

    lifetimes = load_lifetimes_csv(path)

    assert lifetimes["AAA"].terminal_lifecycle == TerminalLifecycle(
        lifetimes["AAA"].last_observed_bar,
        "POSSIBLE_DELISTING",
    )
    assert lifetimes["SH"].terminal_lifecycle is None


def test_actual_corporate_action_schema_emits_dividends_only(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "actions.csv",
        "action_type,symbol,ex_date,record_date,payable_date,rate,old_rate,new_rate,"
        "source_symbol,target_symbol,raw_id\n"
        "cash_dividends,SPY,2024-09-20,2024-09-20,2024-10-31,1.745531,,,SPY,SPY,div-1\n"
        "reverse_splits,SH,2024-11-07,2024-11-07,2024-11-07,,4,1,SH,SH,split-1\n"
        "cash_dividends,SH,2024-12-23,2024-12-23,2024-12-31,0.703865,,,SH,SH,div-2\n",
    )

    actions = load_dividend_actions_csv(path, symbols={"SPY", "SH"})

    assert [(action.symbol, action.value) for action in actions] == [("SPY", 1.745531), ("SH", 0.703865)]
    assert all(action.action_type == CorporateActionType.DIVIDEND for action in actions)


def test_manifest_semantics_require_exact_split_adjusted_v5_identity() -> None:
    bar_manifest = {
        "dataset": BAR_FILE,
        "sha256": FROZEN_HASHES[BAR_FILE],
        "feed": "sip",
        "adjustment": "split",
        "lifetime_table": LIFETIME_FILE,
        "lifetime_table_hash": FROZEN_HASHES[LIFETIME_FILE],
    }
    action_manifest = {
        "dataset": ACTION_FILE,
        "sha256": FROZEN_HASHES[ACTION_FILE],
        "action_breakdown": {"cash_dividends": 141_908, "reverse_splits": 1_207},
    }

    validate_manifest_semantics(bar_manifest, action_manifest)

    bad = json.loads(json.dumps(bar_manifest))
    bad["adjustment"] = "all"
    with pytest.raises(ValueError, match="split-adjusted"):
        validate_manifest_semantics(bad, action_manifest)


def test_sha256_file_is_byte_exact(tmp_path: Path) -> None:
    path = _write(tmp_path / "payload", "abc")
    assert sha256_file(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
