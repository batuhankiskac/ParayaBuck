from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from parayabuck.config import Config
from parayabuck.core.models import Bar, Market
from parayabuck.research.p1_paper_observation import account_identity_hash, build_p1_report

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data" / "research_manifests" / "execution_cost_transferability_p1_paper_observation_2026_09.json"
EPOCH = datetime(2026, 9, 10, tzinfo=UTC)


def _seed(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE portfolio_runtime_sessions (
                session_id TEXT PRIMARY KEY, account_id TEXT, configuration_digest TEXT, mode TEXT,
                started_at TEXT, ended_at TEXT, status TEXT
            );
            CREATE TABLE portfolio_decisions (
                decision_id TEXT PRIMARY KEY, session_id TEXT, account_id TEXT, configuration_digest TEXT,
                decision_timestamp TEXT, market TEXT, strategy_name TEXT, target_json TEXT, risk_json TEXT
            );
            CREATE TABLE portfolio_orders (
                client_order_id TEXT PRIMARY KEY, decision_id TEXT, symbol TEXT, market TEXT, side TEXT,
                quantity REAL, reference_price REAL, status TEXT, broker_order_id TEXT,
                cumulative_filled_quantity REAL, average_fill_price REAL, created_at TEXT
            );
            CREATE TABLE portfolio_execution_events (
                id INTEGER PRIMARY KEY, source TEXT, dedup_identity TEXT, client_order_id TEXT,
                broker_order_id TEXT, event_type TEXT, broker_event_at TEXT, received_at TEXT,
                raw_cumulative_filled_quantity REAL, raw_cumulative_average_fill_price REAL,
                raw_fee REAL, fee_observed INTEGER, measurement_quality TEXT
            );
            CREATE TABLE portfolio_rebalance_executions (decision_id TEXT, phase TEXT);
            """
        )
        for session, account, digest, started in (
            ("s-equity", "raw-account", "equity-digest", EPOCH),
            ("s-crypto", "raw-account", "crypto-digest", EPOCH),
            ("s-old", "raw-account", "equity-digest", EPOCH - timedelta(days=20)),
            ("s-bad", "raw-account", "wrong", EPOCH),
            ("s-other", "different-account", "equity-digest", EPOCH),
        ):
            connection.execute(
                "INSERT INTO portfolio_runtime_sessions VALUES (?, ?, ?, 'paper', ?, NULL, 'running')",
                (session, account, digest, started.isoformat()),
            )
        targets = json.dumps({"metadata": {"decision_origin": "scheduled"}})
        rows = (
            (
                "d-good",
                "s-equity",
                "raw-account",
                "equity-digest",
                EPOCH,
                "equity",
                "eq-strategy",
                "o-good",
                "AAPL",
                "filled",
            ),
            (
                "d-crypto",
                "s-crypto",
                "raw-account",
                "crypto-digest",
                EPOCH + timedelta(days=1),
                "crypto",
                "cr-strategy",
                "o-crypto",
                "BTC/USD",
                "filled",
            ),
            (
                "d-old",
                "s-old",
                "raw-account",
                "equity-digest",
                EPOCH - timedelta(days=10),
                "equity",
                "eq-strategy",
                "o-old",
                "MSFT",
                "filled",
            ),
            ("d-bad", "s-bad", "raw-account", "wrong", EPOCH, "equity", "eq-strategy", "o-bad", "NVDA", "filled"),
            (
                "d-other",
                "s-other",
                "different-account",
                "equity-digest",
                EPOCH,
                "equity",
                "eq-strategy",
                "o-other",
                "META",
                "filled",
            ),
            (
                "d-legacy",
                "s-equity",
                "raw-account",
                "equity-digest",
                EPOCH,
                "equity",
                "eq-strategy",
                "o-legacy",
                "TSLA",
                "filled",
            ),
            (
                "d-invalid",
                "s-equity",
                "raw-account",
                "equity-digest",
                EPOCH,
                "equity",
                "eq-strategy",
                "o-invalid",
                "AMZN",
                "filled",
            ),
        )
        for decision, session, account, digest, at, market, strategy, order, symbol, status in rows:
            connection.execute(
                "INSERT INTO portfolio_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}')",
                (decision, session, account, digest, at.isoformat(), market, strategy, targets),
            )
            connection.execute(
                "INSERT INTO portfolio_orders VALUES (?, ?, ?, ?, 'buy', 1, 100, ?, '', 1, 101, ?)",
                (order, decision, symbol, market, status, at.isoformat()),
            )
            if order != "o-legacy":
                connection.execute(
                    "INSERT INTO portfolio_execution_events VALUES (NULL, 'stream', ?, ?, '', 'fill', ?, ?, 1, 101, 0, 1, 'first_fill')",
                    (order, order, at.isoformat(), at.isoformat()),
                )
        connection.execute(
            "UPDATE portfolio_execution_events SET raw_fee=NULL, fee_observed=0 WHERE client_order_id='o-crypto'"
        )
        connection.execute(
            "INSERT INTO portfolio_execution_events VALUES (NULL, 'stream', 'invalid', 'o-invalid', '', 'fill', ?, ?, 0.5, 101, NULL, 0, 'out_of_order_or_decreasing_cumulative_quantity')",
            (EPOCH.isoformat(), EPOCH.isoformat()),
        )
        connection.commit()
    finally:
        connection.close()


def _bars() -> list[Bar]:
    rows: list[Bar] = []
    for symbol, market in (
        ("AAPL", Market.EQUITY),
        ("BTC/USD", Market.CRYPTO),
        ("MSFT", Market.EQUITY),
        ("NVDA", Market.EQUITY),
        ("META", Market.EQUITY),
        ("TSLA", Market.EQUITY),
        ("AMZN", Market.EQUITY),
    ):
        for day in range(-21, 4):
            rows.append(
                Bar(
                    EPOCH + timedelta(days=day),
                    symbol,
                    "1d",
                    100.0,
                    102.0,
                    99.0,
                    101.0,
                    1_000.0,
                    market,
                )
            )
    return rows


def _receipt(
    *, cutoff: datetime = EPOCH, digests: dict[str, str] | None = None, sessions: dict[str, str] | None = None
) -> dict[str, object]:
    return {
        "manifest_commit": "fixture-commit",
        "manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        "manifest_git_blob_sha": "fixture-blob",
        "manifest_commit_time": (EPOCH - timedelta(days=2)).isoformat(),
        "deployed_code_commit": "fixture-commit",
        "deployment_timestamp": (EPOCH - timedelta(days=1)).isoformat(),
        "sample_cutoff": cutoff.isoformat(),
        "account_identity_hash": account_identity_hash("raw-account"),
        "configuration_digests": digests or {"equity": "equity-digest", "crypto": "crypto-digest"},
        "first_eligible_session_ids": sessions or {"equity": "s-equity", "crypto": "s-crypto"},
        "settings_sha256": "settings-fixture",
        "local_bars_or_manifest_sha256": "fixture-sha",
        "feed_identity": "fixture-feed",
    }


def _report(
    path: Path, *, as_of: datetime = EPOCH + timedelta(days=4), receipt: dict[str, object] | None = None
) -> dict[str, object]:
    return build_p1_report(
        path,
        _bars(),
        Config(),
        dataset={"path": "fixture", "sha256": "fixture-sha", "feed_identity": "fixture-feed"},
        contract={
            "epoch_receipt": receipt or _receipt(),
            "strategy_identities": {"equity": "eq-strategy", "crypto": "cr-strategy"},
        },
        as_of=as_of,
    )


def test_p1_manifest_is_explicitly_planned_and_fee_limited() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert payload["status"] == "planned"
    assert payload["results_state"] == {
        "p1_technical_results_recorded": True,
        "paper_economic_sample_results_recorded": False,
        "cost_transferability_results_recorded": False,
    }
    assert payload["fee_boundary"]["total_cost_comparison_eligible"] is False
    assert payload["acceptance"]["economic_verdict"] == "UNKNOWN_COST_TRANSFERABILITY"
    assert payload["boundaries"]["broker_or_data_api_calls"] is False
    assert payload["technical_results"]["technical_tooling_status"] == "accepted"


def test_p1_filters_epoch_lineage_legacy_and_quality_without_writing_runtime_db(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    _seed(database)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    report = _report(database)
    after = hashlib.sha256(database.read_bytes()).hexdigest()
    assert after == before
    records = {item["client_order_id"]: item for item in report["records"]}
    assert records["o-good"]["primary_price_eligible"] is True
    assert records["o-crypto"]["primary_price_eligible"] is True
    assert records["o-good"]["fee_evidence_status"] == "observed_event_local"
    assert records["o-good"]["total_cost_comparison_eligible"] is False
    assert records["o-crypto"]["primary_price_eligible"] is True
    assert records["o-crypto"]["fee_evidence_status"] == "unknown"
    assert records["o-crypto"]["total_cost_comparison_eligible"] is False
    assert "pre_sample_cutoff" in records["o-old"]["primary_exclusion_reasons"]
    assert "configuration_digest_mismatch" in records["o-bad"]["primary_exclusion_reasons"]
    assert "account_identity_mismatch" in records["o-other"]["primary_exclusion_reasons"]
    assert "legacy_or_missing_raw_execution_events" in records["o-legacy"]["primary_exclusion_reasons"]
    assert "invalid_cumulative_quantity_or_vwap_sequence" in records["o-invalid"]["primary_exclusion_reasons"]


def test_p1_windows_are_deterministic_and_keep_markets_and_gates_separate(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    _seed(database)
    first = _report(database)
    second = _report(database)
    assert first == second
    windows = first["reporting_windows_days"]
    assert set(windows) == {"7", "30", "90"}
    assert windows["7"]["equity"]["eligible_order_count"] == 1
    assert windows["7"]["crypto"]["eligible_order_count"] == 1
    assert windows["7"]["equity"]["aggregate_actual_total_cost"] is None
    assert windows["7"]["equity"]["fee_completeness"]["total_cost_comparison_eligible"] is False
    gates = first["sample_gate_progress"]
    assert gates["equity"]["all_minimums_met"] is False
    assert gates["crypto"]["all_minimums_met"] is False
    assert first["economic_verdict"] == "UNKNOWN_COST_TRANSFERABILITY"
    assert first["promotion"] == "none"


def test_p1_per_market_digests_exclude_only_the_cross_wired_market(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    _seed(database)
    crypto_wrong = _receipt(digests={"equity": "equity-digest", "crypto": "equity-digest"})
    records = {row["client_order_id"]: row for row in _report(database, receipt=crypto_wrong)["records"]}
    assert records["o-good"]["primary_price_eligible"] is True
    assert "configuration_digest_mismatch" in records["o-crypto"]["primary_exclusion_reasons"]
    equity_wrong = _receipt(digests={"equity": "crypto-digest", "crypto": "crypto-digest"})
    records = {row["client_order_id"]: row for row in _report(database, receipt=equity_wrong)["records"]}
    assert "configuration_digest_mismatch" in records["o-good"]["primary_exclusion_reasons"]
    assert records["o-crypto"]["primary_price_eligible"] is True


def test_p1_hashes_account_and_never_serializes_raw_identity(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    _seed(database)
    report = _report(database)
    assert "raw-account" not in json.dumps(report, sort_keys=True)
    assert report["sample_epoch_receipt"]["account_identity_hash"] == account_identity_hash("raw-account")


def test_p1_rejects_bad_epoch_order_and_market_specific_pre_cutoff_session(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    _seed(database)
    bad = _receipt(cutoff=EPOCH - timedelta(days=2))
    with pytest.raises(ValueError, match="ordering"):
        _report(database, receipt=bad)
    report = _report(database, receipt=_receipt(sessions={"equity": "s-old", "crypto": "s-crypto"}))
    records = {row["client_order_id"]: row for row in report["records"]}
    assert "first_eligible_session_missing_or_mismatched" in records["o-good"]["primary_exclusion_reasons"]
    assert records["o-crypto"]["primary_price_eligible"] is True


def test_p1_as_of_hides_future_decisions_and_raw_fill_terminal_changes(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    _seed(database)
    baseline = _report(database)
    connection = sqlite3.connect(database)
    try:
        future = EPOCH + timedelta(days=10)
        target = json.dumps({"metadata": {"decision_origin": "scheduled"}})
        connection.execute(
            "INSERT INTO portfolio_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}')",
            (
                "d-future",
                "s-equity",
                "raw-account",
                "equity-digest",
                future.isoformat(),
                "equity",
                "eq-strategy",
                target,
            ),
        )
        connection.execute(
            "INSERT INTO portfolio_orders VALUES (?, ?, ?, ?, 'buy', 1, 100, 'filled', '', 1, 101, ?)",
            ("o-future", "d-future", "AAPL", "equity", future.isoformat()),
        )
        connection.execute(
            "INSERT INTO portfolio_execution_events VALUES (NULL, 'stream', 'future', 'o-good', '', 'canceled', ?, ?, 2, 102, NULL, 0, 'status_only')",
            (future.isoformat(), future.isoformat()),
        )
        connection.commit()
    finally:
        connection.close()
    report = _report(database)
    current = {row["client_order_id"]: row for row in report["records"]}
    before = {row["client_order_id"]: row for row in baseline["records"]}
    assert "o-future" not in current
    assert current["o-good"]["paper_vwap"] == before["o-good"]["paper_vwap"]
    assert current["o-good"]["terminal_reason"] == before["o-good"]["terminal_reason"]


def test_p1_terminal_truth_uses_shared_status_vocabulary(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    _seed(database)
    connection = sqlite3.connect(database)
    try:
        connection.execute("UPDATE portfolio_orders SET quantity=2 WHERE client_order_id='o-good'")
        connection.execute(
            "UPDATE portfolio_execution_events SET event_type='partial_fill' WHERE client_order_id='o-good'"
        )
        connection.commit()
    finally:
        connection.close()
    row = {item["client_order_id"]: item for item in _report(database)["records"]}["o-good"]
    assert row["is_terminal"] is False and row["fill_outcome"] == "partial"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO portfolio_execution_events VALUES (NULL, 'stream', 'cancel', 'o-good', '', 'canceled', ?, ?, 1, 101, NULL, 0, 'status_only')",
            ((EPOCH + timedelta(days=1)).isoformat(), (EPOCH + timedelta(days=1)).isoformat()),
        )
        connection.commit()
    finally:
        connection.close()
    row = {item["client_order_id"]: item for item in _report(database)["records"]}["o-good"]
    assert row["is_terminal"] is True and row["fill_outcome"] == "partial" and row["terminal_reason"] == "canceled"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE portfolio_execution_events SET raw_cumulative_filled_quantity=0, raw_cumulative_average_fill_price=NULL WHERE client_order_id='o-good'"
        )
        connection.commit()
    finally:
        connection.close()
    row = {item["client_order_id"]: item for item in _report(database)["records"]}["o-good"]
    assert row["is_terminal"] is True and row["fill_outcome"] == "zero_fill" and row["terminal_reason"] == "canceled"


def test_p1_manifest_gates_and_calendar_anniversary_are_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import parayabuck.research.p1_paper_observation as p1

    assert p1._calendar_anniversary(datetime(2024, 8, 31, tzinfo=UTC), 6) == datetime(2025, 2, 28, tzinfo=UTC)
    assert p1._calendar_anniversary(datetime(2023, 8, 31, tzinfo=UTC), 6) == datetime(2024, 2, 29, tzinfo=UTC)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for market in ("equity", "crypto"):
        manifest["minimum_sample_gates"][market] = {key: 0 for key in manifest["minimum_sample_gates"][market]}
    altered = tmp_path / "manifest.json"
    altered.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(p1, "_MANIFEST", altered)
    receipt = _receipt()
    receipt["manifest_sha256"] = hashlib.sha256(altered.read_bytes()).hexdigest()
    database = tmp_path / "runtime.sqlite3"
    _seed(database)
    assert _report(database, receipt=receipt)["sample_gate_progress"]["equity"]["all_minimums_met"] is True


def test_p1_source_has_no_broker_or_network_import() -> None:
    source = (ROOT / "src" / "parayabuck" / "research" / "p1_paper_observation.py").read_text(encoding="utf-8")
    imported = {
        alias.name.split(".")[0].lower()
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not imported & {"alpaca", "requests", "httpx", "websocket", "broker"}
