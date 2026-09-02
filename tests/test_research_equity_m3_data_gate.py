from __future__ import annotations

import ast
import csv
import hashlib
import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import m3_data_gate_helpers as helpers  # noqa: E402
import research_equity_m3_data_gate as gate  # noqa: E402
import research_equity_m3_m4 as legacy_m3  # noqa: E402


def _session_timestamp(session: date) -> datetime:
    local_midnight = datetime.combine(session, datetime.min.time(), tzinfo=ZoneInfo(helpers.EXCHANGE_TIMEZONE))
    return local_midnight.astimezone(UTC)


def _bar(
    symbol: str,
    session: date,
    close: object = "100",
    *,
    timestamp: datetime | None = None,
    timeframe: str = helpers.CANONICAL_TIMEFRAME,
    market: str = helpers.MARKET,
) -> helpers.NormalizedBar:
    close_value = helpers.decimal_value(close, "close")
    return helpers.NormalizedBar(
        session_date=session,
        timestamp=timestamp or _session_timestamp(session),
        symbol=symbol,
        timeframe=timeframe,
        open=close_value,
        high=close_value,
        low=close_value,
        close=close_value,
        volume=Decimal("1000"),
        market=market,
    )


def _raw_bar(
    session: date,
    close: object = "100",
    *,
    symbol: str = "IWM",
    timeframe: str = helpers.RAW_SOURCE_TIMEFRAME,
    volume: object = "1000",
) -> dict[str, object]:
    value = str(close)
    return {
        "S": symbol,
        "t": helpers.canonical_timestamp(_session_timestamp(session)),
        "o": value,
        "h": value,
        "l": value,
        "c": value,
        "v": str(volume),
        "timeframe": timeframe,
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _make_bundle(
    root: Path,
    entries: list[dict[str, object]],
    *,
    page_entries: list[list[dict[str, object]]] | None = None,
    receipt_updates: dict[str, object] | None = None,
) -> Path:
    pages = page_entries or [entries]
    page_dir = root / "pages"
    page_dir.mkdir(parents=True, exist_ok=True)
    page_records: list[dict[str, object]] = []
    for index, page_bars in enumerate(pages, start=1):
        relative = f"pages/{index:06d}.json"
        payload = {
            "bars": {"IWM": page_bars},
            "next_page_token": None if index == len(pages) else f"token-{index}",
        }
        page_path = root / relative
        raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        page_path.write_bytes(raw)
        page_records.append(
            {
                "relative_path": relative,
                "raw_byte_size": len(raw),
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "request_id": f"fixture-request-{index}",
                "input_page_token": None if index == 1 else f"token-{index - 1}",
                "output_next_page_token": None if index == len(pages) else f"token-{index}",
                "row_count": len(page_bars),
            }
        )
    receipt: dict[str, object] = {
        "schema_version": "1.0.0",
        "provider": helpers.SOURCE_PROVIDER,
        "api_family_version": helpers.SOURCE_API_FAMILY_VERSION,
        "provider_api_product_version": "fixture_v1",
        "asset_class": "us_equity",
        "symbol": "IWM",
        "endpoint_identity": helpers.SOURCE_ENDPOINT,
        "request": {
            "start": "2016-01-01",
            "end": "2026-07-31",
            "timeframe": helpers.RAW_SOURCE_TIMEFRAME,
            "feed": "sip",
            "adjustment": "split",
            "sort": "asc",
        },
        "acquisition_timestamp_utc": "2026-09-01T00:00:00Z",
        "entitlement_feed_assertion": "fixture operator attests SIP entitlement",
        "no_fallback_assertion": True,
        "license_retention": {"attested": True, "retention": "fixture-only"},
        "normalizer_version": helpers.NORMALIZER_VERSION,
        "page_count": len(page_records),
        "pages": page_records,
    }
    for key, value in (receipt_updates or {}).items():
        if key == "request" and isinstance(value, dict):
            receipt["request"] = {**receipt["request"], **value}  # type: ignore[dict-item]
        else:
            receipt[key] = value
    _write_json(root / "source_receipt.json", receipt)
    return root


def _rewrite_json(path: Path, update: dict[str, object]) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(update)
    _write_json(path, payload)


def _csv_row(row: helpers.NormalizedBar) -> dict[str, str]:
    fields = row.semantic_fields()
    return dict(zip(helpers.CANONICAL_COLUMNS[1:], fields[1:], strict=True))


def _write_reference_csv(path: Path, rows: list[helpers.NormalizedBar]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(helpers.CANONICAL_COLUMNS[1:]))
        writer.writeheader()
        writer.writerows(_csv_row(row) for row in rows)


def _series_from_returns(values: list[float]) -> list[float]:
    closes = [100.0]
    for value in values:
        closes.append(closes[-1] * (1.0 + value))
    return closes


def _matrix_fixture() -> tuple[list[date], dict[str, list[float]]]:
    sessions = [date(2024, 1, 2) + timedelta(days=index) for index in range(274)]
    spy_returns = [0.0004 + 0.0007 * np.sin(index * 0.37) for index in range(273)]
    size_returns = [0.0002 * np.cos(index * 0.19) + 0.0001 * np.sin(index * 0.71) for index in range(273)]
    growth_returns = [0.0003 * np.sin(index * 0.11) + 0.0002 * np.cos(index * 0.53) for index in range(273)]
    iwm_returns = [spy + size for spy, size in zip(spy_returns, size_returns, strict=True)]
    qqq_returns = [spy + growth for spy, growth in zip(spy_returns, growth_returns, strict=True)]
    stock_returns = [
        0.0003 + 0.8 * spy + 0.4 * size - 0.2 * growth + 0.00015 * np.sin(index * 0.83)
        for index, (spy, size, growth) in enumerate(zip(spy_returns, size_returns, growth_returns, strict=True))
    ]
    return sessions, {
        "AAA": _series_from_returns(stock_returns),
        "SPY": _series_from_returns(spy_returns),
        "IWM": _series_from_returns(iwm_returns),
        "QQQ": _series_from_returns(qqq_returns),
    }


def _good_inventory(sessions: tuple[date, ...]) -> dict[str, dict[str, object]]:
    return {
        symbol: {
            "normalized_row_count": len(sessions),
            "raw_reference_rows": len(sessions),
            "normalized_reference_rows": len(sessions),
            "post_evaluation_valid_rows": 0,
            "rejected_row_count": 0,
            "missing_expected_sessions": [],
            "extra_off_calendar_sessions": [],
            "duplicate_count": 0,
            "conflicting_duplicate_count": 0,
            "nonfinite_count": 0,
            "nonpositive_close_count": 0,
            "nonpositive_ohlc_count": 0,
            "invalid_ohlc_count": 0,
            "negative_volume_count": 0,
            "zero_volume_count": 0,
            "timeframe_mismatch_count": 0,
            "market_mismatch_count": 0,
            "naive_timestamp_count": 0,
            "invalid_timestamp_count": 0,
            "source_range_violation_count": 0,
            "unexpected_schema_count": 0,
            "timeframe_values": [helpers.CANONICAL_TIMEFRAME],
            "market_values": [helpers.MARKET],
            "row_accounting": {"identity_holds": True},
        }
        for symbol in helpers.REFERENCE_SYMBOLS
    }


def _receipt_for_assertions() -> dict[str, object]:
    path = ROOT / "data/research_receipts/equity_m3_data_gate_2026_09_r2.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "schema_version": "2.0.0",
        "mission": "equity_m3_data_gate_2026_09_r2",
        "data_gate_verdict": "M3_DATA_GATE_BLOCKED",
        "corrective_infrastructure_verdict": "PASS",
        "research_only": True,
        "automatic_promotion": False,
        "human_promotion_only": True,
        "candidate_economics_run": False,
        "economic_metrics_present": False,
        "economic_mission_ready": False,
        "blocked_reasons": ["fixture blocked"],
    }


def _fake_pass_receipt(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "2.0.0",
        "mission": "equity_m3_data_gate_2026_09_r2",
        "data_gate_verdict": "M3_DATA_GATE_PASS",
        "corrective_infrastructure_verdict": "PASS",
        "research_only": True,
        "automatic_promotion": False,
        "human_promotion_only": True,
        "candidate_economics_run": False,
        "economic_metrics_present": False,
        "economic_mission_ready": False,
        "next_required_gate": "M3_CANDIDATE_OCP_GATE",
        "m3_candidate_ocp_status": "NOT_RUN_OUT_OF_SCOPE",
        "diagnostic_only": False,
        "mandatory_gate_results": {name: {"status": "PASS"} for name in gate.MANDATORY_GATES},
        "design_matrix_summary": {"total_matrix_count": 1, "pass_count": 1, "fail_count": 0},
        "forbidden_economic_fields": [],
    }
    payload.update(updates)
    payload["self_digest_sha256"] = helpers.canonical_digest(payload)
    return payload


def test_missing_iwm_blocks(tmp_path: Path) -> None:
    with pytest.raises(helpers.GateError, match="missing"):
        helpers.validate_iwm_bundle(tmp_path / "missing-iwm", tmp_path / "stage")


def test_no_network_or_broker_dependency() -> None:
    forbidden_modules = {"alpaca", "requests", "yfinance", "urllib", "httpx"}
    for path in (ROOT / "scripts/m3_data_gate_helpers.py", ROOT / "scripts/research_equity_m3_data_gate.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".", maxsplit=1)[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert imported.isdisjoint(forbidden_modules)


def test_future_rows_do_not_count_toward_coverage() -> None:
    expected = (date(2026, 7, 30),)
    rows = {symbol: [_bar(symbol, date(2026, 7, 30))] for symbol in helpers.REFERENCE_SYMBOLS}
    rows["IWM"].append(_bar("IWM", date(2026, 7, 31)))
    report = helpers.common_session_report(rows, expected_sessions=expected, evaluation_end=date(2026, 7, 30))
    assert report["common_session_count"] == 1


def test_rows_after_evaluation_end_session_do_not_count() -> None:
    expected = (date(2026, 7, 30),)
    inventory = helpers.build_reference_inventory(
        [_bar("IWM", date(2026, 7, 30)), _bar("IWM", date(2026, 7, 31))],
        expected_sessions=expected,
    )
    assert inventory["unique_session_count"] == 1
    assert inventory["post_evaluation_row_count"] == 1


def test_exchange_session_date_handles_0400_0500_utc_dst_offsets() -> None:
    summer = helpers.parse_timestamp("2024-07-01T04:00:00Z")
    winter = helpers.parse_timestamp("2024-01-02T05:00:00Z")
    assert helpers.session_date_for_timestamp(summer) == date(2024, 7, 1)
    assert helpers.session_date_for_timestamp(winter) == date(2024, 1, 2)


def test_utc_midnight_end_does_not_drop_requested_exchange_session() -> None:
    bar = _bar("IWM", date(2026, 7, 30), timestamp=helpers.parse_timestamp("2026-07-30T04:00:00Z"))
    assert bar.session_date == date(2026, 7, 30)
    report = helpers.common_session_report(
        {symbol: [bar if symbol == "IWM" else _bar(symbol, date(2026, 7, 30))] for symbol in helpers.REFERENCE_SYMBOLS},
        expected_sessions=(date(2026, 7, 30),),
    )
    assert report["common_session_count"] == 1


def test_raw_1day_normalizes_to_canonical_1d() -> None:
    row = helpers.normalize_provider_bar(_raw_bar(date(2024, 7, 1)))
    assert row.timeframe == helpers.CANONICAL_TIMEFRAME
    assert row.market == helpers.MARKET
    assert row.symbol == "IWM"


def test_unrecognized_or_mixed_timeframe_blocks(tmp_path: Path) -> None:
    with pytest.raises(helpers.GateError, match="timeframe"):
        helpers.normalize_provider_bar(_raw_bar(date(2024, 7, 1), timeframe="1Hour"))
    rows = [_bar("SPY", date(2024, 7, 1)), _bar("SPY", date(2024, 7, 2), timeframe="1Day")]
    _write_reference_csv(tmp_path / "mixed.csv", rows)
    loaded, quality = helpers.load_csv_reference_rows(tmp_path / "mixed.csv", reference_symbols=("SPY",))
    assert len(loaded["SPY"]) == 1
    assert quality["SPY"]["timeframe_mismatch"] == 1


def test_adjustment_mismatch_blocks(tmp_path: Path) -> None:
    bundle = _make_bundle(
        tmp_path / "bundle", [_raw_bar(date(2024, 7, 1))], receipt_updates={"request": {"adjustment": "raw"}}
    )
    with pytest.raises(helpers.GateError, match="adjustment"):
        helpers.validate_iwm_bundle(bundle, tmp_path / "stage")


def test_feed_mismatch_or_fallback_blocks(tmp_path: Path) -> None:
    bundle = _make_bundle(
        tmp_path / "bundle",
        [_raw_bar(date(2024, 7, 1))],
        receipt_updates={"request": {"feed": "iex"}, "no_fallback_assertion": False},
    )
    with pytest.raises(helpers.GateError, match="feed"):
        helpers.validate_iwm_bundle(bundle, tmp_path / "stage")


def test_partial_pagination_chain_blocks(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path / "bundle", [_raw_bar(date(2024, 7, 1))])
    receipt_path = bundle / "source_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["pages"][0]["output_next_page_token"] = "unfetched-token"
    _write_json(receipt_path, receipt)
    with pytest.raises(helpers.GateError, match="terminal"):
        helpers.validate_iwm_bundle(bundle, tmp_path / "stage")


def test_missing_page_blocks(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path / "bundle", [_raw_bar(date(2024, 7, 1))])
    (bundle / "pages/000001.json").unlink()
    with pytest.raises(helpers.GateError, match="missing"):
        helpers.validate_iwm_bundle(bundle, tmp_path / "stage")


def test_page_hash_mismatch_blocks(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path / "bundle", [_raw_bar(date(2024, 7, 1))])
    (bundle / "pages/000001.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(helpers.GateError, match="hash"):
        helpers.validate_iwm_bundle(bundle, tmp_path / "stage")


def test_duplicate_page_blocks(tmp_path: Path) -> None:
    entries = [_raw_bar(date(2024, 7, 1))]
    bundle = _make_bundle(tmp_path / "bundle", entries, page_entries=[entries, entries])
    with pytest.raises(helpers.GateError, match="duplicate"):
        helpers.validate_iwm_bundle(bundle, tmp_path / "stage")


def test_raw_source_symbol_mismatch_blocks(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path / "bundle", [_raw_bar(date(2024, 7, 1), symbol="SPY")])
    with pytest.raises(helpers.GateError, match="symbol"):
        helpers.validate_iwm_bundle(bundle, tmp_path / "stage")


def test_missing_license_retention_attestation_blocks(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path / "bundle", [_raw_bar(date(2024, 7, 1))])
    receipt_path = bundle / "source_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt.pop("license_retention")
    _write_json(receipt_path, receipt)
    with pytest.raises(helpers.GateError, match="retention"):
        helpers.validate_iwm_bundle(bundle, tmp_path / "stage")


def test_missing_expected_session_blocks() -> None:
    expected = (date(2024, 7, 1), date(2024, 7, 2))
    inventory = helpers.build_reference_inventory([_bar("SPY", expected[0])], expected_sessions=expected)
    assert inventory["missing_expected_sessions"] == ["2024-07-02"]


def test_extra_off_calendar_session_blocks() -> None:
    expected = (date(2024, 7, 1),)
    inventory = helpers.build_reference_inventory(
        [_bar("SPY", expected[0]), _bar("SPY", date(2024, 7, 4))], expected_sessions=expected
    )
    assert inventory["extra_off_calendar_sessions"] == ["2024-07-04"]


def test_identical_duplicate_bar_blocks() -> None:
    row = _bar("IWM", date(2024, 7, 1))
    with pytest.raises(helpers.GateError, match="duplicate"):
        helpers.ensure_unique_rows([row, row])


def test_conflicting_duplicate_bar_blocks() -> None:
    with pytest.raises(helpers.GateError, match="conflicting"):
        helpers.ensure_unique_rows([_bar("IWM", date(2024, 7, 1), "100"), _bar("IWM", date(2024, 7, 1), "101")])


def test_invalid_ohlc_blocks(tmp_path: Path) -> None:
    raw = _raw_bar(date(2024, 7, 1))
    raw["l"] = "101"
    bundle = _make_bundle(tmp_path / "bundle", [raw])
    with pytest.raises(helpers.GateError, match="OHLC"):
        helpers.validate_iwm_bundle(bundle, tmp_path / "stage")


def test_nonfinite_value_blocks(tmp_path: Path) -> None:
    raw = _raw_bar(date(2024, 7, 1))
    raw["c"] = "NaN"
    bundle = _make_bundle(tmp_path / "bundle", [raw])
    with pytest.raises(helpers.GateError, match="non-finite"):
        helpers.validate_iwm_bundle(bundle, tmp_path / "stage")


def test_zero_or_negative_close_blocks(tmp_path: Path) -> None:
    raw = _raw_bar(date(2024, 7, 1), close="0")
    bundle = _make_bundle(tmp_path / "bundle", [raw])
    with pytest.raises(helpers.GateError, match="positive"):
        helpers.validate_iwm_bundle(bundle, tmp_path / "stage")


def test_negative_volume_blocks(tmp_path: Path) -> None:
    raw = _raw_bar(date(2024, 7, 1), volume="-1")
    bundle = _make_bundle(tmp_path / "bundle", [raw])
    with pytest.raises(helpers.GateError, match="non-negative"):
        helpers.validate_iwm_bundle(bundle, tmp_path / "stage")


def test_exact_274_common_closes_required() -> None:
    sessions = tuple(date(2024, 1, 2) + timedelta(days=index) for index in range(274))
    rows = {symbol: [_bar(symbol, session) for session in sessions] for symbol in helpers.REFERENCE_SYMBOLS}
    report = helpers.common_session_report(rows, expected_sessions=sessions)
    assert report["common_session_count"] == 274


def test_273_returns_produced_from_274_closes() -> None:
    sessions, closes = _matrix_fixture()
    result = helpers.audit_design_matrix("AAA", sessions[-1], sessions, closes)
    assert result["session_count"] == 274
    assert result["return_count"] == 273


def test_individual_274_counts_do_not_imply_274_common_sessions() -> None:
    first = tuple(date(2024, 1, 2) + timedelta(days=index) for index in range(274))
    second = tuple(date(2024, 1, 3) + timedelta(days=index) for index in range(274))
    rows = {
        "SPY": [_bar("SPY", session) for session in first],
        "IWM": [_bar("IWM", session) for session in second],
        "QQQ": [_bar("QQQ", session) for session in first],
    }
    report = helpers.common_session_report(rows, expected_sessions=tuple(sorted(set(first + second))))
    assert all(len(rows[symbol]) == 274 for symbol in helpers.REFERENCE_SYMBOLS)
    assert report["common_session_count"] == 273


def test_400_calendar_days_is_not_used_as_warmup_proof() -> None:
    sessions = tuple(date(2024, 1, 2) + timedelta(days=index) for index in range(250))
    rows = {symbol: [_bar(symbol, session) for session in sessions] for symbol in helpers.REFERENCE_SYMBOLS}
    report = helpers.common_session_report(rows, expected_sessions=sessions)
    assert len(sessions) == 250
    assert report["common_session_count"] < 274


def test_non_iwm_semantic_hash_unchanged_after_merge() -> None:
    canonical = [_bar("SPY", date(2024, 7, 1)), _bar("QQQ", date(2024, 7, 1))]
    merged, hashes = helpers.merge_normalized_rows(canonical, [_bar("IWM", date(2024, 7, 1))])
    assert len(merged) == 3
    assert hashes["pre_merge_non_iwm_semantic_sha256"] == hashes["post_merge_non_iwm_semantic_sha256"]


def test_existing_iwm_conflict_blocks() -> None:
    with pytest.raises(helpers.GateError, match="conflicts"):
        helpers.merge_normalized_rows([_bar("IWM", date(2024, 7, 1), "100")], [_bar("IWM", date(2024, 7, 1), "101")])


def test_hash_parse_atomicity_detects_file_mutation(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("before\n", encoding="utf-8")

    def mutate(path: Path) -> None:
        path.write_text("after-with-different-size\n", encoding="utf-8")

    with pytest.raises(helpers.GateError, match="changed"):
        helpers.stage_file(source, tmp_path / "stage", after_copy_hook=mutate)


def test_reference_panel_digest_is_deterministic() -> None:
    rows = [_bar("IWM", date(2024, 7, 2)), _bar("SPY", date(2024, 7, 1))]
    identity = {"feed": "sip", "adjustment": "split", "provider": "fixture"}
    assert helpers.reference_panel_digest(rows, source_identity=identity) == helpers.reference_panel_digest(
        list(reversed(rows)), source_identity=dict(identity)
    )


def test_design_matrix_digest_is_deterministic() -> None:
    sessions, closes = _matrix_fixture()
    one = helpers.audit_design_matrix("AAA", sessions[-1], sessions, closes)
    two = helpers.audit_design_matrix("AAA", sessions[-1], sessions, dict(closes))
    assert one["matrix_digest_sha256"] == two["matrix_digest_sha256"]


def test_rank_deficient_matrix_blocks() -> None:
    sessions = [date(2024, 1, 2) + timedelta(days=index) for index in range(274)]
    reference_closes = [100.0 + 0.01 * np.sin(index * 0.31) for index in range(274)]
    closes = {
        "SPY": reference_closes,
        "IWM": reference_closes.copy(),
        "QQQ": reference_closes.copy(),
        "AAA": [100.0 + 0.02 * np.cos(index * 0.17) for index in range(274)],
    }
    result = helpers.audit_design_matrix("AAA", sessions[-1], sessions, closes)
    assert result["passed"] is False
    assert "rank_deficient" in result["failure_categories"]


def test_ill_conditioned_matrix_blocks() -> None:
    sessions = [date(2024, 1, 2) + timedelta(days=index) for index in range(274)]
    base = np.asarray([0.0004 + 0.0002 * np.sin(index * 0.31) for index in range(273)])
    epsilon = np.asarray([1e-12 * np.cos(index * 0.17) for index in range(273)])
    spy = base.tolist()
    size = (2 * base + epsilon).tolist()
    growth = (3 * base + 2 * epsilon).tolist()
    closes = {
        "SPY": _series_from_returns(spy),
        "IWM": _series_from_returns([a + b for a, b in zip(spy, size, strict=True)]),
        "QQQ": _series_from_returns([a + b for a, b in zip(spy, growth, strict=True)]),
        "AAA": _series_from_returns([0.001 + a for a in spy]),
    }
    result = helpers.audit_design_matrix("AAA", sessions[-1], sessions, closes)
    assert result["passed"] is False
    assert "ill_conditioned" in result["failure_categories"]


def test_nonfinite_coefficients_block(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions, closes = _matrix_fixture()

    def bad_lstsq(*args: object, **kwargs: object) -> tuple[object, ...]:
        del args, kwargs
        return np.asarray([float("nan"), 0.0, 0.0]), np.asarray([]), 3, np.asarray([1.0, 1.0, 1.0])

    monkeypatch.setattr(helpers.np.linalg, "lstsq", bad_lstsq)
    result = helpers.audit_design_matrix("AAA", sessions[-1], sessions, closes)
    assert result["passed"] is False
    assert "nonfinite_coefficients" in result["failure_categories"]


def test_nonfinite_residuals_block(monkeypatch: pytest.MonkeyPatch) -> None:
    sessions, closes = _matrix_fixture()

    def bad_lstsq(*args: object, **kwargs: object) -> tuple[object, ...]:
        del args, kwargs
        return np.asarray([float("inf"), 0.0, 0.0]), np.asarray([]), 3, np.asarray([1.0, 1.0, 1.0])

    monkeypatch.setattr(helpers.np.linalg, "lstsq", bad_lstsq)
    result = helpers.audit_design_matrix("AAA", sessions[-1], sessions, closes)
    assert result["passed"] is False
    assert "nonfinite_residuals" in result["failure_categories"]


def test_degenerate_residual_variance_blocks() -> None:
    sessions = [date(2024, 1, 2) + timedelta(days=index) for index in range(274)]
    spy = [0.0004 + 0.0002 * np.sin(index * 0.31) for index in range(273)]
    size = [0.0002 * np.cos(index * 0.19) for index in range(273)]
    growth = [0.0003 * np.sin(index * 0.11) for index in range(273)]
    closes = {
        "SPY": _series_from_returns(spy),
        "IWM": _series_from_returns([a + b for a, b in zip(spy, size, strict=True)]),
        "QQQ": _series_from_returns([a + b for a, b in zip(spy, growth, strict=True)]),
        "AAA": _series_from_returns([0.8 * a + 0.4 * b - 0.2 * c for a, b, c in zip(spy, size, growth, strict=True)]),
    }
    result = helpers.audit_design_matrix("AAA", sessions[-1], sessions, closes)
    assert result["passed"] is False
    assert "degenerate_residual" in result["failure_categories"]


def test_current_alpha_retaining_residual_convention_is_frozen() -> None:
    sessions, closes = _matrix_fixture()
    result = helpers.audit_design_matrix("AAA", sessions[-1], sessions, closes)
    assert result["residual_convention"] == "alpha_retaining: demeaned fit, non-demeaned application, no intercept"


def test_skip_window_semantics_are_frozen() -> None:
    sessions, closes = _matrix_fixture()
    result = helpers.audit_design_matrix("AAA", sessions[-1], sessions, closes)
    assert "latest 21 residual returns omitted" in str(result["skip_window_convention"])
    assert "beta fit retains all 273 returns" in str(result["skip_window_convention"])


def test_spy_iwm_qqq_are_observation_only() -> None:
    overlay = helpers.build_reference_overlay(
        {"universe": {"equity": {"maximum_stream_symbols": 10, "fallback_trade_symbols": ["SPY", "IWM", "QQQ", "AAA"]}}}
    )
    assert overlay.reference_observation_symbols == frozenset(helpers.REFERENCE_SYMBOLS)
    assert overlay.target_symbols.isdisjoint(overlay.reference_observation_symbols)


def test_reference_and_candidate_sets_are_disjoint() -> None:
    overlay = helpers.build_reference_overlay(
        {"universe": {"equity": {"maximum_stream_symbols": 10, "fallback_trade_symbols": ["SPY", "AAA"]}}}
    )
    assert overlay.tradable_candidate_symbols.isdisjoint(overlay.reference_observation_symbols)


def test_reference_and_execution_sets_are_disjoint() -> None:
    overlay = helpers.build_reference_overlay(
        {"universe": {"equity": {"maximum_stream_symbols": 10, "fallback_trade_symbols": ["QQQ", "BBB"]}}}
    )
    assert overlay.executable_symbols.isdisjoint(overlay.reference_observation_symbols)


def test_qqq_is_not_tradable_in_research_overlay() -> None:
    overlay = helpers.build_reference_overlay(
        {"universe": {"equity": {"maximum_stream_symbols": 10, "fallback_trade_symbols": ["QQQ", "AAA"]}}}
    )
    assert "QQQ" in overlay.original_configured_trade_symbols
    assert "QQQ" in overlay.removed_reference_symbols
    assert "QQQ" not in overlay.research_effective_trade_symbols
    assert "QQQ" not in overlay.filter_candidates(["QQQ", "AAA"])


def test_iwm_is_not_tradable_in_research_overlay() -> None:
    overlay = helpers.build_reference_overlay(
        {"universe": {"equity": {"maximum_stream_symbols": 10, "fallback_trade_symbols": ["IWM", "AAA"]}}}
    )
    assert "IWM" not in overlay.target_symbols
    assert "IWM" not in overlay.executable_symbols


def test_production_config_is_unchanged() -> None:
    result = subprocess.run(["git", "show", "HEAD:config/settings.yaml"], cwd=ROOT, check=True, capture_output=True)
    assert result.stdout == (ROOT / "config/settings.yaml").read_bytes()


def test_m3_prepare_uses_candidate_owned_observation_path_or_valid_receipt() -> None:
    source = (ROOT / "scripts/research_equity_m3_m4.py").read_text(encoding="utf-8")
    assert "M3_DATA_GATE_RECEIPT" in source
    assert "_valid_m3_data_gate_receipt" in source
    assert "m3_ready = all(" not in source


def test_fresh_observer_and_execution_instances() -> None:
    source = (ROOT / "scripts/research_v3_equity_roadmap.py").read_text(encoding="utf-8")
    assert "observation_strategy_factory" in source
    assert "assert_strategy_identity" in source


def test_no_future_bar_in_observation_snapshot() -> None:
    expected = (date(2026, 7, 30),)
    rows = {symbol: [_bar(symbol, date(2026, 7, 30))] for symbol in helpers.REFERENCE_SYMBOLS}
    rows["SPY"].append(_bar("SPY", date(2026, 7, 31)))
    report = helpers.common_session_report(rows, expected_sessions=expected)
    assert report["last_common_session"] == "2026-07-30"


def test_no_outside_pool_candidate_in_observation_snapshot() -> None:
    overlay = helpers.build_reference_overlay(
        {"universe": {"equity": {"maximum_stream_symbols": 10, "fallback_trade_symbols": ["AAA"]}}}
    )
    assert overlay.filter_candidates(["AAA", "OUTSIDE", "IWM"]) == ("AAA",)


def test_receipt_contains_no_economic_metrics() -> None:
    receipt = _receipt_for_assertions()
    assert helpers.forbidden_receipt_keys(receipt) == ()
    assert "economic_candidate_scores" not in json.dumps(receipt).lower()


def test_receipt_contains_research_only_true() -> None:
    assert _receipt_for_assertions()["research_only"] is True


def test_receipt_contains_automatic_promotion_false() -> None:
    assert _receipt_for_assertions()["automatic_promotion"] is False


def test_blocked_receipt_is_deterministic() -> None:
    receipt = _receipt_for_assertions()
    without_digest = dict(receipt)
    without_digest.pop("self_digest_sha256", None)
    assert helpers.canonical_digest(without_digest) == helpers.canonical_digest(dict(without_digest))


def test_pass_requires_every_mandatory_gate() -> None:
    sessions = tuple(date(2024, 1, 2) + timedelta(days=index) for index in range(274))
    inventory = _good_inventory(sessions)
    weekly = {
        "failed_after_warmup_count": 0,
        "ever_feasible": True,
        "post_feasibility_failure_count": 0,
        "accepted_decision_count": 1,
        "decision_count": 1,
        "recent_window_accepted_covers_every_required_weekly_decision": True,
    }
    common = {"common_session_count": 274}
    assert gate._reference_gate_pass(inventory, common, weekly, weekly)
    inventory["IWM"]["missing_expected_sessions"] = ["2024-01-02"]
    assert not gate._reference_gate_pass(inventory, common, weekly, weekly)


def test_absolute_local_paths_are_not_committed_in_receipt() -> None:
    receipt = _receipt_for_assertions()
    serialized = json.dumps(receipt)
    assert str(ROOT) not in serialized
    assert "/Users/" not in serialized


def test_large_external_data_are_not_committed() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "data/historical/equity_pit_symmetric_2016_2026_v5.csv"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert tracked.returncode != 0


def test_count_only_m3_ready_cannot_return_ready() -> None:
    source = (ROOT / "scripts/research_equity_m3_m4.py").read_text(encoding="utf-8")
    assert 'coverage[symbol]["count"] >= 274' not in source
    assert "blocked_data_gate" in source
    assert "data_gate_pass_m3_ocp_required" in source


def test_r1_manifest_commit_with_code_changes_is_rejected() -> None:
    r1_contract = {
        "base_commit": "0a8a726b4ce6d1dcf0d8591a860125e2fed875ba",
        "implementation_commit": "11ffe9ea5650bfbd3bcd1eda0787d50f255f6f9a",
        "implementation_tree": "unused",
        "critical_git_blobs": {},
    }
    with pytest.raises(helpers.GateError, match="manifest-only"):
        gate._validate_commit_provenance(
            r1_contract,
            manifest_path="data/research_manifests/equity_m3_data_gate_2026_09.json",
            run_commit="e76bfa0e9f43a0ddac18e38f43e5dd226ea1516d",
            run_parent="11ffe9ea5650bfbd3bcd1eda0787d50f255f6f9a",
        )


def test_r4_a8_recovery_contract_shape_is_supported() -> None:
    contract = json.loads(
        (ROOT / "data/research_manifests/equity_m3_data_gate_2026_09_r4.json").read_text(encoding="utf-8")
    )
    contract.update(
        {
            "lineage_mode": gate.R4_RECOVERY_LINEAGE_A8,
            "implementation_parent_commit": gate.R4_B7_COMMIT,
            "implementation_commit": "a" * 40,
            "implementation_tree": "b" * 40,
            "recovery_lineage": {
                "prior_implementation_commit": gate.R4_A7_COMMIT,
                "prior_manifest_commit": gate.R4_B7_COMMIT,
                "recovery_reason": "R4 PIT builder rejected frozen v5 provider daily timeframe aliases",
                "replay_exact_captured_bytes": True,
                "automatic_refetch_allowed": False,
            },
        }
    )

    gate._validate_r4_contract(contract, run_parent="a" * 40)


def test_r4_a9_recovery_contract_shape_is_supported() -> None:
    contract = json.loads(
        (ROOT / "data/research_manifests/equity_m3_data_gate_2026_09_r4.json").read_text(encoding="utf-8")
    )
    contract.update(
        {
            "lineage_mode": gate.R4_RECOVERY_LINEAGE_A9,
            "implementation_parent_commit": gate.R4_B8_COMMIT,
            "implementation_commit": "a" * 40,
            "implementation_tree": "b" * 40,
            "recovery_lineage": {
                "prior_implementation_commit": gate.R4_A8_COMMIT,
                "prior_manifest_commit": gate.R4_B8_COMMIT,
                "recovery_reason": "R4 PIT builder rejected frozen v5 intra-session symbol ordering",
                "replay_exact_captured_bytes": True,
                "automatic_refetch_allowed": False,
            },
        }
    )

    gate._validate_r4_contract(contract, run_parent="a" * 40)


@pytest.mark.parametrize("gate_name", gate.MANDATORY_GATES)
def test_removing_any_mandatory_gate_blocks(gate_name: str) -> None:
    gates = {name: {"status": gate.GATE_PASS} for name in gate.MANDATORY_GATES}
    del gates[gate_name]
    assert gate.evaluate_mandatory_gates(gates) == "M3_DATA_GATE_BLOCKED"


@pytest.mark.parametrize("state", (gate.GATE_FAIL, gate.GATE_UNKNOWN, gate.GATE_NOT_RUN))
@pytest.mark.parametrize("gate_name", gate.MANDATORY_GATES)
def test_falsifying_any_mandatory_gate_blocks(gate_name: str, state: str) -> None:
    gates = {name: {"status": gate.GATE_PASS} for name in gate.MANDATORY_GATES}
    gates[gate_name] = {"status": state}
    assert gate.evaluate_mandatory_gates(gates) == "M3_DATA_GATE_BLOCKED"


def test_terminal_payload_continuation_token_mismatch_blocks(tmp_path: Path) -> None:
    bundle = _make_bundle(tmp_path / "bundle", [_raw_bar(date(2024, 7, 1))])
    page_path = bundle / "pages/000001.json"
    payload = json.loads(page_path.read_text(encoding="utf-8"))
    payload["next_page_token"] = "unexpected-token"
    raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    page_path.write_bytes(raw)
    receipt_path = bundle / "source_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["pages"][0]["raw_byte_size"] = len(raw)
    receipt["pages"][0]["raw_sha256"] = hashlib.sha256(raw).hexdigest()
    _write_json(receipt_path, receipt)
    with pytest.raises(helpers.GateError, match="continuation"):
        helpers.validate_iwm_bundle(bundle, tmp_path / "stage")


def test_symlink_input_is_rejected_by_staging(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    source = tmp_path / "source.txt"
    target.write_text("payload\n", encoding="utf-8")
    source.symlink_to(target)
    with pytest.raises(helpers.GateError, match="symlink"):
        helpers.stage_file(source, tmp_path / "stage")


def test_path_replacement_before_open_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    replacement = tmp_path / "replacement.txt"
    source.write_text("original\n", encoding="utf-8")
    replacement.write_text("replacement\n", encoding="utf-8")
    original_open = helpers.os.open
    replaced = False

    def replacing_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal replaced
        try:
            is_source = Path(path) == source
        except TypeError:
            is_source = False
        if is_source and not replaced:
            replacement.replace(source)
            replaced = True
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(helpers.os, "open", replacing_open)
    with pytest.raises(helpers.GateError, match="replaced"):
        helpers.stage_file(source, tmp_path / "stage")


def test_truncation_during_copy_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload-before-truncation\n", encoding="utf-8")

    def truncate(path: Path) -> None:
        path.write_bytes(b"")

    with pytest.raises(helpers.GateError, match="changed"):
        helpers.stage_file(source, tmp_path / "stage", after_copy_hook=truncate)


def test_staged_content_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("payload\n", encoding="utf-8")
    with pytest.raises(helpers.GateError, match="hash mismatch"):
        helpers.stage_file(source, tmp_path / "stage", expected_sha256="0" * 64)


def test_zero_volume_reference_row_is_accounted_and_rejected(tmp_path: Path) -> None:
    row = _bar("SPY", date(2024, 7, 1))
    path = tmp_path / "zero-volume.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(helpers.CANONICAL_COLUMNS[1:]))
        writer.writeheader()
        values = _csv_row(row)
        values["volume"] = "0"
        writer.writerow(values)
    loaded, quality = helpers.load_csv_reference_rows(path, reference_symbols=("SPY",))
    assert loaded["SPY"] == []
    assert quality["SPY"]["rejected_row_count"] == 1
    assert quality["SPY"]["zero_volume_count"] == 1


def test_weekly_coverage_rejects_jointly_stale_references() -> None:
    expected = tuple(date(2024, 1, 2) + timedelta(days=index) for index in range(300))
    rows = {symbol: [_bar(symbol, session) for session in expected[:274]] for symbol in helpers.REFERENCE_SYMBOLS}
    report = helpers.weekly_decision_coverage(rows, expected_sessions=expected, start=expected[0], end=expected[-1])
    assert report["jointly_stale_count"] > 0
    assert any(item["state"] == "STALE_REFERENCE" for item in report["decisions"])


def test_weekly_coverage_distinguishes_no_feasible_window_from_warmup() -> None:
    expected = tuple(date(2024, 1, 2) + timedelta(days=index) for index in range(40))
    rows = {symbol: [_bar(symbol, session) for session in expected] for symbol in helpers.REFERENCE_SYMBOLS}
    report = helpers.weekly_decision_coverage(rows, expected_sessions=expected, start=expected[0], end=expected[-1])
    assert report["ever_feasible"] is False
    assert report["initial_warmup_decision_count"] == 0
    assert {item["state"] for item in report["decisions"]} == {"NO_FEASIBLE_REFERENCE_WINDOW"}


def test_full_window_initial_warmup_is_allowed_before_feasibility() -> None:
    expected = tuple(date(2019, 1, 2) + timedelta(days=index) for index in range(1200))
    rows = {symbol: [_bar(symbol, session) for session in expected] for symbol in helpers.REFERENCE_SYMBOLS}
    full = helpers.weekly_decision_coverage(
        rows,
        expected_sessions=expected,
        start=expected[0],
        end=expected[-1],
    )
    recent = helpers.weekly_decision_coverage(
        rows,
        expected_sessions=expected,
        start=date(2022, 1, 1),
        end=expected[-1],
    )

    assert full["initial_warmup_decision_count"] > 0
    assert full["ever_feasible"] is True
    assert full["first_accepted_decision"] == full["first_feasible_decision"]
    assert full["accepted_plus_initial_warmup_count"] == full["decision_count"]
    assert full["post_feasibility_failure_count"] == 0
    assert recent["initial_warmup_decision_count"] == 0
    assert recent["accepted_decision_count"] == recent["decision_count"]
    assert recent["post_feasibility_failure_count"] == 0
    assert gate._reference_gate_pass(_good_inventory(expected), {"common_session_count": len(expected)}, full, recent)


def test_deterministic_pit_population_excludes_references_without_alpha() -> None:
    population = helpers.deterministic_candidate_population(
        [
            {"effective_at": "2024-01-02T00:00:00+00:00", "symbols": ["AAA", "SPY"]},
            {"effective_at": "2024-02-02T00:00:00+00:00", "symbols": ["BBB", "IWM", "QQQ"]},
        ]
    )
    assert population["candidate_symbols"] == ["AAA", "BBB"]
    assert helpers.candidate_symbols_for_decision(population, "2024-01-15") == ("AAA",)
    assert helpers.validate_pit_reference_boundary(population)["status"] == "PIT_UNIVERSE_VERIFIED"


def test_alpha_ranked_pit_population_is_rejected() -> None:
    with pytest.raises(helpers.GateError, match="alpha"):
        helpers.deterministic_candidate_population(
            [
                {
                    "effective_at": "2024-01-02T00:00:00+00:00",
                    "symbols": ["AAA"],
                    "observation_strategy": "ranked_v1",
                }
            ]
        )


def test_exact_matrix_panel_uses_candidate_reference_intersection() -> None:
    sessions, closes = _matrix_fixture()
    rows = {
        symbol: [_bar(symbol, session, close) for session, close in zip(sessions, values, strict=True)]
        for symbol, values in closes.items()
    }
    rows["AAA"].pop()
    selected, panel = helpers.exact_m3_close_panel("AAA", sessions[-1], rows)
    assert len(selected) == 273
    result = helpers.audit_design_matrix("AAA", sessions[-1], selected, panel)
    assert result["passed"] is False
    assert "common_close_count" in result["failure_categories"]


def test_streaming_matrix_audit_matches_exact_panel(tmp_path: Path) -> None:
    sessions, closes = _matrix_fixture()
    reference_rows = {
        symbol: [_bar(symbol, session, close) for session, close in zip(sessions, values, strict=True)]
        for symbol, values in closes.items()
        if symbol != "AAA"
    }
    candidate_rows = [_bar("AAA", session, close) for session, close in zip(sessions, closes["AAA"], strict=True)]
    canonical = tmp_path / "candidate.csv"
    _write_reference_csv(canonical, candidate_rows)
    population = helpers.deterministic_candidate_population(
        [{"effective_at": "2024-01-02T00:00:00Z", "symbols": ["AAA"]}]
    )
    weekly = {"decisions": [{"decision_session": sessions[-1].isoformat(), "accepted": True}]}
    thresholds = helpers.DEFAULT_NUMERICAL_THRESHOLDS
    streamed = gate._r3_matrix_audit_stream(
        canonical,
        reference_rows,
        weekly,
        population,
        thresholds,
    )
    loaded_candidates, _quality = helpers.load_csv_reference_rows(canonical, reference_symbols=("AAA",))
    exact = gate._matrix_audit(
        {**reference_rows, "AAA": loaded_candidates["AAA"]},
        weekly,
        population["candidate_symbols"],
        thresholds,
        population,
    )
    assert streamed["total_matrix_count"] == exact["total_matrix_count"] == 1
    assert streamed["pass_count"] == exact["pass_count"]
    assert streamed["matrix_digest_merkle_root"] == exact["matrix_digest_merkle_root"]


def test_reference_capacity_includes_managed_and_open_order_reservations() -> None:
    with pytest.raises(helpers.GateError, match="capacity"):
        helpers.build_reference_overlay(
            {"universe": {"equity": {"maximum_stream_symbols": 4, "fallback_trade_symbols": ["AAA"]}}},
            managed_position_reservations=1,
            open_order_reservations=1,
        )


@pytest.mark.parametrize(
    ("label", "update"),
    (
        ("self-consistent fake", {}),
        ("another dataset", {"input_provenance": {"canonical_bars": {"raw_sha256": "1" * 64}}}),
        ("another implementation", {"base_lineage": {"implementation_a2_commit": "2" * 40}}),
        ("zero matrices", {"design_matrix_summary": {"total_matrix_count": 0, "pass_count": 0, "fail_count": 0}}),
        ("missing composite", {"composite_dataset_identity": {"status": "NOT_RUN_BLOCKED"}}),
        (
            "unknown mandatory gate",
            {"mandatory_gate_results": {"iwm_raw_bundle": {"status": "UNKNOWN"}}},
        ),
        (
            "altered manifest hash",
            {"manifest_identity": {"path": gate.R2_MANIFEST_PATH, "git_blob_id": "3" * 40}},
        ),
        ("wrong reference panel hash", {"reference_panel": {"normalized_reference_panel_sha256": "4" * 64}}),
    ),
)
def test_weak_or_mismatched_pass_receipts_are_rejected(
    label: str, update: dict[str, object], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    del label
    receipt_path = tmp_path / "receipt.json"
    _write_json(receipt_path, _fake_pass_receipt(**update))
    monkeypatch.setattr(legacy_m3, "M3_DATA_GATE_RECEIPT", receipt_path)
    assert legacy_m3._valid_m3_data_gate_receipt() is False


def test_m3_m4_consumer_requires_r3_receipt(monkeypatch: pytest.MonkeyPatch) -> None:
    assert legacy_m3.M3_DATA_GATE_RECEIPT == legacy_m3.M3_DATA_GATE_R3_RECEIPT
    monkeypatch.setattr(legacy_m3, "M3_DATA_GATE_RECEIPT", legacy_m3.M3_DATA_GATE_R2_RECEIPT)
    assert legacy_m3._valid_m3_data_gate_receipt() is False


def test_manual_candidate_argument_is_not_a_pass_input() -> None:
    source = (ROOT / "scripts/research_equity_m3_data_gate.py").read_text(encoding="utf-8")
    assert "--candidate-symbol" not in source
    assert "--diagnostic-candidate-symbol" in source


def test_r4_source_completeness_requires_exact_2658_session_set() -> None:
    expected = tuple(
        helpers.expected_exchange_sessions(
            helpers.REQUIRED_FIRST_REFERENCE_SESSION,
            helpers.REQUIRED_LAST_REFERENCE_SESSION,
        )
    )
    assert len(expected) == 2658
    rows = {symbol: [_bar(symbol, session) for session in expected] for symbol in helpers.REFERENCE_SYMBOLS}
    complete = gate._r4_source_completeness(rows)
    assert complete["complete"] is True
    assert complete["expected_session_count"] == 2658
    assert all(item["complete"] for item in complete["references"].values())  # type: ignore[union-attr]

    rows["IWM"].pop()
    missing = gate._r4_source_completeness(rows)
    assert missing["complete"] is False
    assert missing["references"]["IWM"]["last_session"] == "2026-07-29"  # type: ignore[index]

    rows["IWM"].append(_bar("IWM", date(2026, 7, 31)))
    extra = gate._r4_source_completeness(rows)
    assert extra["complete"] is False
    assert extra["references"]["IWM"]["extra_sessions"]["first"] == "2026-07-31"  # type: ignore[index]


def test_r4_inventory_quality_rejects_boundary_or_row_accounting_defects() -> None:
    item: dict[str, object] = {
        "normalized_row_count": gate.EXPECTED_REQUIRED_REFERENCE_SESSIONS,
        "unique_session_count": gate.EXPECTED_REQUIRED_REFERENCE_SESSIONS,
        "first_session_date": gate.REQUIRED_FIRST_REFERENCE_SESSION.isoformat(),
        "last_session_date": gate.REQUIRED_LAST_REFERENCE_SESSION.isoformat(),
        "missing_expected_sessions": [],
        "extra_off_calendar_sessions": [],
        "timeframe_values": [gate.CANONICAL_TIMEFRAME],
        "market_values": ["EQUITY"],
        "feed": gate.SOURCE_FEED,
        "adjustment": gate.SOURCE_ADJUSTMENT,
        "row_accounting": {
            "identity_holds": True,
            "raw_reference_rows": gate.EXPECTED_REQUIRED_REFERENCE_SESSIONS,
        },
    }
    item.update({field: 0 for field in gate.R4_REQUIRED_ZERO_QUALITY_FIELDS})
    inventory = {symbol: dict(item) for symbol in gate.REFERENCE_SYMBOLS}
    assert gate._r4_inventory_quality_pass(inventory) is True

    inventory["IWM"]["request_boundary_violation_count"] = 1
    assert gate._r4_inventory_quality_pass(inventory) is False
    inventory["IWM"]["request_boundary_violation_count"] = 0
    inventory["IWM"]["row_accounting"] = {"identity_holds": False, "raw_reference_rows": 2658}
    assert gate._r4_inventory_quality_pass(inventory) is False


def test_r4_gate_verdict_requires_every_frozen_mandatory_gate() -> None:
    gates = {name: {"status": gate.GATE_PASS} for name in gate.R4_MANDATORY_GATES}
    assert gate.evaluate_mandatory_gates(gates, gate.R4_MANDATORY_GATES) == "M3_DATA_GATE_PASS"
    for name in gate.R4_MANDATORY_GATES:
        falsified = {key: dict(value) for key, value in gates.items()}
        falsified[name] = {"status": gate.GATE_UNKNOWN}
        assert gate.evaluate_mandatory_gates(falsified, gate.R4_MANDATORY_GATES) == "M3_DATA_GATE_BLOCKED"


def test_r4_compact_audit_redacts_pagination_tokens() -> None:
    compact = gate._compact_iwm_audit(
        {
            "pages": [
                {
                    "relative_path": "pages/000001.json",
                    "raw_sha256": "a" * 64,
                    "input_page_token": "secret-input-token",
                    "output_next_page_token": "secret-output-token",
                }
            ]
        },
        redact_tokens=True,
    )
    serialized = json.dumps(compact)
    assert "secret-input-token" not in serialized
    assert "secret-output-token" not in serialized
    assert compact["page_count"] == 1
