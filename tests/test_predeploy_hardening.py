import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from pydantic import ValidationError

from parayabuck.backup import BackupVerificationError, backup_database, verify_backup
from parayabuck.config import LiveConfig, RuntimeConfig
from parayabuck.execution.alpaca import AlpacaBroker, RiskStatePersistenceError
from parayabuck.execution.risk_peak import RiskPeakService
from parayabuck.monitoring import alerts
from parayabuck.portfolio import portfolio_strategy_from, product_strategy_from
from parayabuck.storage import SQLiteStore, StorageInitializationError
from parayabuck.web.server import SettingsPayload, manager

# --- Peak state fail-closed ---


def test_peak_missing_bootstrap(tmp_path, monkeypatch):
    peak = tmp_path / "paper_equity_peak.json"
    broker = AlpacaBroker("k", "s", paper=True, peak_state_path=peak)
    # Should bootstrap to 0.0 when missing and NOT create file until save
    assert broker._peak_equity == 0.0
    assert not peak.exists()


def test_peak_valid_restore(tmp_path):
    peak = tmp_path / "paper_equity_peak.json"
    peak.write_text(json.dumps({"peak_equity": 12345.67}))
    broker = AlpacaBroker("k", "s", paper=True, peak_state_path=peak)
    assert broker._peak_equity == 12345.67


def test_peak_corrupt_json_fail_closed(tmp_path):
    peak = tmp_path / "paper_equity_peak.json"
    peak.write_text("{ not json")
    with pytest.raises(RiskStatePersistenceError):
        AlpacaBroker("k", "s", paper=True, peak_state_path=peak)


def test_peak_truncated_json_fail_closed(tmp_path):
    peak = tmp_path / "paper_equity_peak.json"
    peak.write_text('{"peak_equity": 123')
    with pytest.raises(RiskStatePersistenceError):
        AlpacaBroker("k", "s", paper=True, peak_state_path=peak)


def test_peak_nan_fail_closed(tmp_path):
    for val in ["NaN", "Infinity", "-Infinity"]:
        peak = tmp_path / f"peak_{val}.json"
        peak.write_text(json.dumps({"peak_equity": float(val)}))  # json dumps NaN as null? Use raw
        # Write raw with .nan form to bypass json? Use manual
        peak.write_text(f'{{"peak_equity": {val}}}')
        with pytest.raises(RiskStatePersistenceError):
            AlpacaBroker("k", "s", paper=True, peak_state_path=peak)


def test_peak_negative_fail_closed(tmp_path):
    peak = tmp_path / "paper_equity_peak.json"
    peak.write_text(json.dumps({"peak_equity": -100}))
    with pytest.raises(RiskStatePersistenceError):
        AlpacaBroker("k", "s", paper=True, peak_state_path=peak)


def test_peak_atomic_write_survives(tmp_path):
    peak = tmp_path / "paper_equity_peak.json"
    peak.write_text(json.dumps({"peak_equity": 1000}))
    broker = AlpacaBroker("k", "s", paper=True, peak_state_path=peak)
    broker._peak_equity = 2000
    broker._save_peak_equity()
    assert json.loads(peak.read_text())["peak_equity"] == 2000
    # Simulate interruption: write tmp file without replace should not corrupt original
    # atomic_write already tested via backup; just ensure previous good survives
    assert peak.exists()


def test_peak_atomic_write_fault_injection_preserves_old(tmp_path):
    """Fault-injection: os.replace failure must leave old valid file byte-identical."""

    peak = tmp_path / "paper_equity_peak.json"
    original_payload = json.dumps({"peak_equity": 1000, "epoch": 0})
    peak.write_text(original_payload)
    original_bytes = peak.read_bytes()
    broker = AlpacaBroker("k", "s", paper=True, peak_state_path=peak)
    broker._peak_equity = 2000
    # Patch the replace inside atomic_write to raise before the file is swapped
    with patch("parayabuck.utils.io.os.replace", side_effect=OSError("injected failure")):
        try:
            broker._save_peak_equity()
            assert False, "expected OSError from injected fault"
        except OSError as exc:
            assert "injected failure" in str(exc)
    # Old file must be byte-identical, no partial temp authoritative
    assert peak.read_bytes() == original_bytes
    assert json.loads(peak.read_text())["peak_equity"] == 1000
    # No stray temp files should remain as authoritative peak
    assert not list(tmp_path.glob(".paper_equity_peak.json.*")) or all(
        not p.exists() for p in tmp_path.glob(".paper_equity_peak.json.*")
    )
    # Next load must restore old peak, not 2000
    broker2 = AlpacaBroker("k", "s", paper=True, peak_state_path=peak)
    assert broker2._peak_equity == 1000


def test_peak_sidecar_deletion_restores_persisted(tmp_path):
    """Peak sidecar deleted but SQLite holds peak -> restore max persisted (account-scoped)."""
    from datetime import UTC, datetime

    from parayabuck.execution.ledger import ExecutionLedger
    from parayabuck.portfolio.runtime_service import PortfolioStateRepository
    from parayabuck.risk.state import RiskState

    db = tmp_path / "persist.sqlite3"
    ledger = ExecutionLedger(str(db))
    repo = PortfolioStateRepository(str(db), store=ledger._store)
    # Persist historical peak 100000 via high_water and risk_state
    repo.update_high_water("acct-test", 100000.0)
    # also via risk_state
    ledger.save_risk_state(
        RiskState(
            account_id="acct-test",
            session_date=datetime.now(UTC).date(),
            day_start_equity=100000.0,
            peak_equity=100000.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            fees=0.0,
            consecutive_losses=0,
            circuit_breaker_reason=None,
            updated_at=datetime.now(UTC),
            peak_epoch=0,
        )
    )
    # Create broker with legacy sidecar (pre-account-scoped format) then delete sidecar to simulate loss
    peak = tmp_path / "paper_equity_peak.json"
    peak.write_text(json.dumps({"peak_equity": 100000.0, "epoch": 0}))

    class FakeAccountInit:
        equity = 100000.0
        last_equity = 100000.0
        cash = 100000.0
        account_blocked = False
        trading_blocked = False
        trade_suspended_by_user = False
        id = "acct-test"
        crypto_status = "ACTIVE"
        non_marginable_buying_power = None

    class FakeClientInit:
        def get_account(self):
            return FakeAccountInit()

        def get_all_positions(self):
            return []

        _session = None

    broker = AlpacaBroker("k", "s", paper=True, peak_state_path=peak, ledger=ledger)
    broker._client = FakeClientInit()
    # Trigger account-scoped reconciliation (legacy sidecar -> max with persisted, rebind)
    snap_init = broker.account()
    assert snap_init.peak_equity == 100000.0
    assert broker._peak_equity == 100000.0
    # Sidecar should now be bound to acct-test
    assert json.loads(peak.read_text()).get("account_id") == "acct-test"
    # Delete sidecar, simulate restart with current equity 94000
    peak.unlink()
    # Create new broker instance sharing same ledger (restart)
    broker2 = AlpacaBroker("k", "s", paper=True, peak_state_path=peak, ledger=ledger)
    # Before account() peak is 0 (not yet reconciled); after account() must restore 100000, not rebase to 0 or 94000
    assert broker2._peak_equity == 0.0
    assert broker2._peak_account_id is None

    # Simulate account fetch at 94000 -> drawdown 6%
    class FakeAccount:
        equity = 94000.0
        last_equity = 94000.0
        cash = 94000.0
        account_blocked = False
        trading_blocked = False
        trade_suspended_by_user = False
        id = "acct-test"
        crypto_status = "ACTIVE"
        non_marginable_buying_power = None

    class FakeClient:
        def get_account(self):
            return FakeAccount()

        def get_all_positions(self):
            return []

        _session = None

    broker2._client = FakeClient()
    snap = broker2.account()
    assert snap.peak_equity == 100000.0
    assert abs(snap.drawdown_pct - 0.06) < 1e-9
    # Sidecar should have been restored bound to acct-test
    assert peak.exists()
    assert json.loads(peak.read_text())["peak_equity"] == 100000.0
    assert json.loads(peak.read_text()).get("account_id") == "acct-test"


def test_peak_missing_with_no_persisted_bootstraps_zero(tmp_path):
    """Case A: sidecar absent and no persisted peak -> genuine first run 0.0."""
    from parayabuck.execution.ledger import ExecutionLedger

    db = tmp_path / "empty.sqlite3"
    ledger = ExecutionLedger(str(db))
    peak = tmp_path / "paper_equity_peak.json"
    # No persisted peak: fresh DB, no high_water, no risk_state
    broker = AlpacaBroker("k", "s", paper=True, peak_state_path=peak, ledger=ledger)
    assert broker._peak_equity == 0.0
    assert broker._peak_epoch == 0


def test_peak_max_when_both_exist(tmp_path):
    from parayabuck.execution.ledger import ExecutionLedger
    from parayabuck.portfolio.runtime_service import PortfolioStateRepository

    db = tmp_path / "both.sqlite3"
    ledger = ExecutionLedger(str(db))
    repo = PortfolioStateRepository(str(db), store=ledger._store)
    repo.update_high_water("acct-test", 90000.0)
    peak = tmp_path / "paper_equity_peak.json"
    peak.write_text(json.dumps({"peak_equity": 95000.0, "epoch": 0}))

    class FakeAccountA:
        equity = 95000.0
        last_equity = 95000.0
        cash = 95000.0
        account_blocked = False
        trading_blocked = False
        trade_suspended_by_user = False
        id = "acct-test"
        crypto_status = "ACTIVE"
        non_marginable_buying_power = None

    class FakeClientA:
        def get_account(self):
            return FakeAccountA()

        def get_all_positions(self):
            return []

        _session = None

    broker = AlpacaBroker("k", "s", paper=True, peak_state_path=peak, ledger=ledger)
    broker._client = FakeClientA()
    snap = broker.account()
    # sidecar 95k > persisted 90k same epoch -> max wins 95k
    assert snap.peak_equity == 95000.0
    assert broker._peak_equity == 95000.0
    # Now persist higher 100k and reload sidecar still 95k -> should pick 100k (account-scoped)
    repo.update_high_water("acct-test", 100000.0)
    peak.write_text(json.dumps({"account_id": "acct-test", "peak_equity": 95000.0, "epoch": 0}))
    broker2 = AlpacaBroker("k", "s", paper=True, peak_state_path=peak, ledger=ledger)
    broker2._client = FakeClientA()
    # Need to mock same account but with current equity maybe still 95k; reconciliation should pick persisted 100k
    # Use a fresh fake that returns 95000 equity (below persisted peak)
    snap2 = broker2.account()
    assert snap2.peak_equity == 100000.0
    assert broker2._peak_equity == 100000.0


def test_peak_epoch_reset_not_undone_by_old_history(tmp_path):
    """Explicit reset epoch increments; old higher peak must not resurrect (account-scoped)."""
    from datetime import UTC, datetime

    from parayabuck.execution.ledger import ExecutionLedger
    from parayabuck.portfolio.runtime_service import PortfolioStateRepository
    from parayabuck.risk.state import RiskState

    db = tmp_path / "epoch.sqlite3"
    ledger = ExecutionLedger(str(db))
    repo = PortfolioStateRepository(str(db), store=ledger._store)
    peak = tmp_path / "paper_equity_peak.json"
    # Historical peak 100k epoch 0
    repo.update_high_water("acct-test", 100000.0)
    peak.write_text(json.dumps({"peak_equity": 100000.0, "epoch": 0}))

    class FakeAccount100:
        equity = 100000.0
        last_equity = 100000.0
        cash = 100000.0
        account_blocked = False
        trading_blocked = False
        trade_suspended_by_user = False
        id = "acct-test"
        crypto_status = "ACTIVE"
        non_marginable_buying_power = None

    class FakeClient100:
        def get_account(self):
            return FakeAccount100()

        def get_all_positions(self):
            return []

        _session = None

    broker = AlpacaBroker("k", "s", paper=True, peak_state_path=peak, ledger=ledger)
    broker._client = FakeClient100()
    snap = broker.account()
    assert snap.peak_equity == 100000.0
    assert broker._peak_equity == 100000.0
    # Operator explicit reset to 94000 -> epoch 1 (account-scoped)
    broker.reset_peak_equity(94000.0)
    repo.reset_high_water("acct-test", 94000.0)
    ledger.save_risk_state(
        RiskState(
            account_id="acct-test",
            session_date=datetime.now(UTC).date(),
            day_start_equity=94000.0,
            peak_equity=94000.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            fees=0.0,
            consecutive_losses=0,
            circuit_breaker_reason=None,
            updated_at=datetime.now(UTC),
            peak_epoch=broker._peak_epoch,
        )
    )
    assert broker._peak_epoch == 1
    # Simulate old risk_state row with epoch 0 still present (historical)
    # Already present; new broker restart with sidecar deleted should NOT resurrect 100k for acct-test
    peak.unlink()
    broker2 = AlpacaBroker("k", "s", paper=True, peak_state_path=peak, ledger=ledger)
    assert broker2._peak_equity == 0.0
    assert broker2._peak_account_id is None

    # Reconcile via account() — epoch 1 should win, not old 100k epoch 0
    class FakeAccount94:
        equity = 94000.0
        last_equity = 94000.0
        cash = 94000.0
        account_blocked = False
        trading_blocked = False
        trade_suspended_by_user = False
        id = "acct-test"
        crypto_status = "ACTIVE"
        non_marginable_buying_power = None

    class FakeClient94:
        def get_account(self):
            return FakeAccount94()

        def get_all_positions(self):
            return []

        _session = None

    broker2._client = FakeClient94()
    snap2 = broker2.account()
    assert snap2.peak_equity == 94000.0
    assert broker2._peak_equity == 94000.0
    assert broker2._peak_epoch == 1


def test_peak_preserved_when_equity_below(tmp_path, monkeypatch):
    peak = tmp_path / "paper_equity_peak.json"
    peak.write_text(json.dumps({"peak_equity": 5000}))
    broker = AlpacaBroker("k", "s", paper=True, peak_state_path=peak)
    assert broker._peak_equity == 5000

    # Simulate account returns lower equity, peak should stay
    class FakeAccount:
        equity = 4000
        last_equity = 4000
        cash = 4000
        account_blocked = False
        trading_blocked = False
        trade_suspended_by_user = False
        id = "test"
        crypto_status = "ACTIVE"
        non_marginable_buying_power = None

    class FakeClient:
        def get_account(self):
            return FakeAccount()

        def get_all_positions(self):
            return []

        _session = None

    broker._client = FakeClient()
    broker.ledger = None
    snap = broker.account()
    assert snap.peak_equity == 5000
    assert json.loads(peak.read_text())["peak_equity"] == 5000


# --- SQLite WAL invariant ---


def test_wal_success(tmp_path):
    db = tmp_path / "test.sqlite3"
    store = SQLiteStore(db)
    assert store.path == db
    # verify pragma
    assert store.connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    store.connection.close()


def test_wal_non_wal_fails(monkeypatch, tmp_path):

    class FakeConn:
        def execute(self, sql):
            if sql.strip().lower() == "pragma journal_mode":

                class R:
                    def fetchone(self):
                        return ("delete",)

                return R()
            if "journal_mode=wal" in sql.lower():

                class R2:
                    def fetchone(self):
                        return None

                return R2()
            return type("R", (), {"fetchone": lambda self: ("wal",)})()

        def close(self):
            pass

    # Instead directly test the logic: create store and then manually check that non-WAL raises
    # We'll patch sqlite3.connect to return FakeConn that after WAL setting still reports delete
    def fake_connect2(path, check_same_thread=False):
        return FakeConn()

    with patch("parayabuck.storage.sqlite3.connect", side_effect=fake_connect2):
        with pytest.raises(StorageInitializationError, match="journal_mode"):
            SQLiteStore(tmp_path / "fake.sqlite3")


def test_foreign_keys_off_fails(tmp_path):
    # Similar: foreign_keys off should raise
    class FakeConn2:
        def execute(self, sql):
            low = sql.lower().strip()
            if low == "pragma journal_mode":

                class R:  # wal ok
                    def fetchone(self):
                        return ("wal",)

                return R()
            if low == "pragma foreign_keys":

                class R:
                    def fetchone(self):
                        return (0,)

                return R()
            if "foreign_keys=on" in low or "journal_mode=wal" in low or "synchronous" in low or "busy_timeout" in low:

                class R:
                    def fetchone(self):
                        return None

                return R()
            return type("R", (), {"fetchone": lambda self: ("wal",)})()

        def close(self):
            pass

    def fake_connect2(path, check_same_thread=False):
        return FakeConn2()

    with patch("parayabuck.storage.sqlite3.connect", side_effect=fake_connect2):
        with pytest.raises(StorageInitializationError, match="foreign_keys"):
            SQLiteStore(tmp_path / "fake2.sqlite3")


def test_db_parent_unwritable_fails(tmp_path):
    # Attempt to use a path where parent is a file, not directory
    parent_file = tmp_path / "notadir"
    parent_file.write_text("x")
    db = parent_file / "db.sqlite3"
    with pytest.raises(StorageInitializationError):
        SQLiteStore(db)


# --- Backup verify before prune ---


def test_backup_verify_before_prune(tmp_path):
    src = tmp_path / "src.sqlite3"
    conn = sqlite3.connect(src)
    conn.execute("CREATE TABLE t(x INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    backup_dir = tmp_path / "backups"
    dest = backup_database(src, backup_dir, retention_days=0.0001)
    assert verify_backup(dest)
    assert dest.exists()


def test_backup_corrupt_deleted_and_preserved(tmp_path):
    src = tmp_path / "src2.sqlite3"
    conn = sqlite3.connect(src)
    conn.execute("CREATE TABLE t(x INTEGER)")
    conn.commit()
    conn.close()
    backup_dir = tmp_path / "backups2"
    # Create a good backup first
    good = backup_database(src, backup_dir, retention_days=7)
    assert verify_backup(good)
    # Now corrupt the validation by patching verify_backup to return False
    with patch("parayabuck.backup.verify_backup", return_value=False):
        with pytest.raises(BackupVerificationError):
            backup_database(src, backup_dir, retention_days=7)
    # Good backup should still exist, no new corrupt file should remain
    assert good.exists()
    # No pruning should have happened before validation; we verify by checking only good remains
    files = list(backup_dir.glob("*.sqlite3"))
    assert len(files) == 1 and files[0] == good


# --- Config finite validation ---


def test_live_hard_dollar_cap_finite():
    with pytest.raises(ValueError, match="non-negative|finite"):
        LiveConfig(enabled=False, hard_dollar_cap=float("nan"))
    with pytest.raises(ValueError, match="non-negative|finite"):
        LiveConfig(enabled=False, hard_dollar_cap=float("inf"))


def test_runtime_scanner_refresh_finite():
    with pytest.raises(ValueError):
        RuntimeConfig(scanner_refresh_hours=float("nan"))
    with pytest.raises(ValueError):
        RuntimeConfig(scanner_refresh_hours=float("inf"))


# --- Settings API extra forbid and patch semantics ---


def test_settings_payload_typo_rejected():
    with pytest.raises(ValidationError):
        SettingsPayload(max_drawdwon_pct=5)  # typo


def test_settings_payload_extra_forbid():
    with pytest.raises(ValidationError):
        SettingsPayload(unknown_field=123)


def test_settings_patch_preserves_omitted(tmp_path, monkeypatch):
    # Create temp config from real canonical config
    orig_cfg = Path("config/settings.yaml").read_text()
    cfg_path = tmp_path / "settings.yaml"
    cfg_path.write_text(orig_cfg)
    monkeypatch.setenv("CONFIG_PATH", str(cfg_path))
    # Patch manager to use tmp config
    orig_path = manager.config_path
    manager.config_path = cfg_path
    try:
        # GET should return correct defaults
        import asyncio

        from parayabuck.web.server import get_settings

        result = asyncio.run(get_settings())
        assert result["daily_loss_limit_pct"] == 20.0
        assert result["max_drawdown_pct"] == 75.0
        assert result["crypto_trading_enabled"] is False  # paper_config_v2: crypto disabled
        assert "config_digest" in result
        # PATCH with only log_level should preserve risk
        payload = SettingsPayload(log_level="DEBUG")
        assert "daily_loss_limit_pct" not in payload.model_fields_set
        validated = manager._validated_settings_payload(payload)
        assert validated.cfg_raw["risk"]["daily_loss_limit_pct"] == 0.20
        assert validated.cfg_raw["risk"]["max_drawdown_pct"] == 0.75
        assert validated.cfg_raw["app"]["log_level"] == "DEBUG"
    finally:
        manager.config_path = orig_path


def test_settings_concurrency_409(tmp_path, monkeypatch):
    orig_cfg = Path("config/settings.yaml").read_text()
    cfg_path = tmp_path / "settings.yaml"
    cfg_path.write_text(orig_cfg)
    orig_path = manager.config_path
    manager.config_path = cfg_path
    try:
        current = manager._config_digest(paper=True)
        # Change config to B
        # modify risk to 0.015 via yaml edit
        import yaml as _yaml

        data = _yaml.safe_load(cfg_path.read_text())
        data["risk"]["daily_loss_limit_pct"] = 0.015
        cfg_path.write_text(_yaml.safe_dump(data))
        new_digest = manager._config_digest(paper=True)
        assert current != new_digest
        # Attempt to save with old digest should raise 409 via handler
        payload = SettingsPayload(log_level="DEBUG", expected_config_digest=current)
        import asyncio

        from fastapi import HTTPException

        from parayabuck.web.server import _handle_save_settings

        with pytest.raises(HTTPException) as exc:
            asyncio.run(_handle_save_settings(payload))
        assert exc.value.status_code == 409
        # B remains unchanged
        final_raw = yaml.safe_load(cfg_path.read_text())
        assert final_raw["risk"]["daily_loss_limit_pct"] == 0.015
    finally:
        manager.config_path = orig_path


# --- Product strategy gate ---


def test_product_gate_rejects_non_product():
    # Use a deliberately unsupported name.
    with pytest.raises(ValueError, match="Unsupported"):
        product_strategy_from("nonexistent_strategy_xyz", {})
    # Generic construction remains available for known strategy classes.
    strat = portfolio_strategy_from(
        "equity_leveraged_trend_v1",
        {
            "signal_symbol": "QQQ",
            "hold_symbol": "QLD",
            "sma_bars": 200,
            "vol_target": 0.35,
            "vol_window": 20,
            "rebalance_frequency": "hours:24",
        },
        timeframe="1d",
    )
    assert strat.name == "equity_leveraged_trend_v1"


# --- Container credential semantics ---


def test_container_credential_mutation_rejected(tmp_path, monkeypatch):
    orig_cfg = Path("config/settings.yaml").read_text()
    cfg_path = tmp_path / "settings.yaml"
    cfg_path.write_text(orig_cfg)
    env_path = tmp_path / ".env"
    env_path.write_text('ALPACA_API_KEY="oldkey"\nALPACA_SECRET_KEY="oldsecret"\n')
    orig_cfg = manager.config_path
    orig_env = manager.env_path
    manager.config_path = cfg_path
    manager.env_path = env_path
    monkeypatch.setenv("PARAYABUCK_CONTAINER", "YES")
    try:
        payload = SettingsPayload(api_key="newkey", secret_key="newsecret")
        with pytest.raises(ValueError, match="container mode"):
            manager._validated_settings_payload(payload)
    finally:
        manager.config_path = orig_cfg
        manager.env_path = orig_env
        monkeypatch.delenv("PARAYABUCK_CONTAINER", raising=False)


# --- Webhook truthful delivery ---


def test_webhook_success_and_failure():
    alerts.reset_for_tests()
    alerts.configure_alerting(
        enabled=True, webhook_url="https://example.com/hook", events=tuple(alerts.ALERT_KINDS), throttle_seconds=0
    )

    # Mock requests.post
    class FakeResp:
        def __init__(self, code):
            self.status_code = code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception(f"HTTP {self.status_code}")

    with patch("parayabuck.monitoring.alerts.requests.post", return_value=FakeResp(200)):
        alerts.emit("runtime_started", message="ok")
        # Give daemon thread time? Our _post_webhook is async via thread; we called directly via emit which spawns thread
        # For test, call _post_webhook directly synchronously
        alerts._post_webhook("https://example.com/hook", {"kind": "runtime_started", "message": "ok"})
        stats = alerts.alert_stats()
        assert stats["webhook_succeeded"] >= 1

    with patch("parayabuck.monitoring.alerts.requests.post", return_value=FakeResp(500)):
        alerts._post_webhook("https://example.com/hook", {"kind": "runtime_started", "message": "fail"})
        stats = alerts.alert_stats()
        assert stats["webhook_failed"] >= 1

    with patch("parayabuck.monitoring.alerts.requests.post", side_effect=TimeoutError("timeout")):
        alerts._post_webhook("https://example.com/hook", {"kind": "runtime_started", "message": "timeout"})
        stats = alerts.alert_stats()
        assert stats["webhook_failed"] >= 1


def test_webhook_throttle_distinct_incidents():
    alerts.reset_for_tests()
    alerts.configure_alerting(
        enabled=True, webhook_url="https://example.com/hook", events=("paper_incident",), throttle_seconds=60
    )
    with patch("parayabuck.monitoring.alerts.requests.post") as mock_post:
        mock_post.return_value.raise_for_status = lambda: None
        # First incident
        alerts.emit("paper_incident", message="inc1", category="risk", incident_id="id1")
        # Different incident id should not be throttled
        alerts.emit("paper_incident", message="inc2", category="risk", incident_id="id2")
        stats = alerts.alert_stats()
        # Both delivered (suppressed =0)
        assert stats["suppressed"] == 0
        # Same incident duplicate should be throttled
        alerts.emit("paper_incident", message="inc1 dup", category="risk", incident_id="id1")
        stats2 = alerts.alert_stats()
        assert stats2["suppressed"] == 1


# --- Account-scoped peak recovery A-G ---


def _make_fake_account(account_id: str, equity: float):
    class FakeAccount:
        pass

    fa = FakeAccount()
    fa.equity = equity
    fa.last_equity = equity
    fa.cash = equity
    fa.account_blocked = False
    fa.trading_blocked = False
    fa.trade_suspended_by_user = False
    fa.id = account_id
    fa.crypto_status = "ACTIVE"
    fa.non_marginable_buying_power = None
    return fa


def _make_broker_for_account(tmp_path, ledger, peak_path, account_id, equity):
    broker = AlpacaBroker("k", "s", paper=True, peak_state_path=peak_path, ledger=ledger)

    class FakeClient:
        def get_account(self):
            return _make_fake_account(account_id, equity)

        def get_all_positions(self):
            return []

        _session = None

    broker._client = FakeClient()
    return broker


def test_peak_account_scoped_A_isolation(tmp_path):
    """A: peaks are isolated per account — A 100k does not leak to B 50k."""

    from parayabuck.execution.ledger import ExecutionLedger
    from parayabuck.portfolio.runtime_service import PortfolioStateRepository

    db = tmp_path / "a_isolation.sqlite3"
    ledger = ExecutionLedger(str(db))
    repo = PortfolioStateRepository(str(db), store=ledger._store)
    repo.update_high_water("acct-A", 100000.0)
    repo.update_high_water("acct-B", 50000.0)
    peak = tmp_path / "paper_equity_peak.json"
    # No sidecar
    if peak.exists():
        peak.unlink()
    # Account A reconciles to 100k
    broker_a = _make_broker_for_account(tmp_path, ledger, peak, "acct-A", 100000.0)
    snap_a = broker_a.account()
    assert snap_a.peak_equity == 100000.0
    assert broker_a._peak_account_id == "acct-A"
    assert broker_a._peak_epoch == 0
    # Simulate B on same broker path but fresh broker instance (restart as B)
    # Delete sidecar to force DB restore for B
    peak.unlink()
    broker_b = _make_broker_for_account(tmp_path, ledger, peak, "acct-B", 50000.0)
    snap_b = broker_b.account()
    assert snap_b.peak_equity == 50000.0
    assert snap_b.drawdown_pct == 0.0
    # Ensure B's drawdown not computed against A's 100k
    assert broker_b._peak_equity == 50000.0
    # Now B drops to 47000 -> 6% drawdown vs 50k, not vs 100k (which would be 53%)
    broker_b2 = _make_broker_for_account(tmp_path, ledger, peak, "acct-B", 47000.0)
    # Reuse same ledger, but need to keep sidecar bound to B (reuse peak file)
    snap_b2 = broker_b2.account()
    assert abs(snap_b2.drawdown_pct - 0.06) < 1e-9
    assert snap_b2.peak_equity == 50000.0


def test_peak_account_scoped_B_legacy_max(tmp_path):
    """B: legacy sidecar without account_id -> max(legacy, persisted) then rebind."""
    from parayabuck.execution.ledger import ExecutionLedger
    from parayabuck.portfolio.runtime_service import PortfolioStateRepository

    db = tmp_path / "b_legacy.sqlite3"
    ledger = ExecutionLedger(str(db))
    repo = PortfolioStateRepository(str(db), store=ledger._store)
    repo.update_high_water("acct-test", 90000.0)
    peak = tmp_path / "paper_equity_peak.json"
    # Legacy sidecar 95000 (higher than persisted 90k)
    peak.write_text(json.dumps({"peak_equity": 95000.0, "epoch": 0}))
    broker = _make_broker_for_account(tmp_path, ledger, peak, "acct-test", 92000.0)
    snap = broker.account()
    # Legacy 95k vs persisted 90k -> max is 95k, sidecar rewritten bound
    assert snap.peak_equity == 95000.0
    assert json.loads(peak.read_text())["account_id"] == "acct-test"
    assert json.loads(peak.read_text())["peak_equity"] == 95000.0
    # Now legacy lower than persisted: persisted 100k, legacy 95000 -> persisted wins
    repo.update_high_water("acct-test", 100000.0)
    peak.write_text(json.dumps({"peak_equity": 95000.0, "epoch": 0}))  # legacy again
    broker2 = AlpacaBroker("k", "s", paper=True, peak_state_path=peak, ledger=ledger)

    class FakeClient:
        def get_account(self):
            return _make_fake_account("acct-test", 95000.0)

        def get_all_positions(self):
            return []

        _session = None

    broker2._client = FakeClient()
    snap2 = broker2.account()
    assert snap2.peak_equity == 100000.0
    assert json.loads(peak.read_text())["peak_equity"] == 100000.0


def test_peak_account_scoped_C_sidecar_mismatch_ignored(tmp_path):
    """C: sidecar bound to acct-A is ignored when current account is B."""
    from parayabuck.execution.ledger import ExecutionLedger
    from parayabuck.portfolio.runtime_service import PortfolioStateRepository

    db = tmp_path / "c_mismatch.sqlite3"
    ledger = ExecutionLedger(str(db))
    repo = PortfolioStateRepository(str(db), store=ledger._store)
    repo.update_high_water("acct-B", 70000.0)
    peak = tmp_path / "paper_equity_peak.json"
    # Sidecar bound to A with 120k
    peak.write_text(json.dumps({"account_id": "acct-A", "peak_equity": 120000.0, "epoch": 0}))
    broker = _make_broker_for_account(tmp_path, ledger, peak, "acct-B", 70000.0)
    snap = broker.account()
    # Must ignore A's 120k and use B's 70k
    assert snap.peak_equity == 70000.0
    assert broker._peak_account_id == "acct-B"
    # Sidecar rewritten to B
    data = json.loads(peak.read_text())
    assert data["account_id"] == "acct-B"
    assert data["peak_equity"] == 70000.0


def test_peak_account_scoped_D_no_cross_account_pollution(tmp_path):
    """D: deleting sidecar for A does not resurrect B's peak."""
    from parayabuck.execution.ledger import ExecutionLedger
    from parayabuck.portfolio.runtime_service import PortfolioStateRepository

    db = tmp_path / "d_pollution.sqlite3"
    ledger = ExecutionLedger(str(db))
    repo = PortfolioStateRepository(str(db), store=ledger._store)
    repo.update_high_water("acct-A", 200000.0)
    repo.update_high_water("acct-B", 30000.0)
    peak = tmp_path / "paper_equity_peak.json"
    peak.write_text(json.dumps({"account_id": "acct-A", "peak_equity": 200000.0, "epoch": 0}))
    # Broker for A correctly restores 200k
    broker_a = _make_broker_for_account(tmp_path, ledger, peak, "acct-A", 200000.0)
    snap_a = broker_a.account()
    assert snap_a.peak_equity == 200000.0
    # Now switch to B, sidecar still bound to A, but B should get 30k not 200k
    broker_b = _make_broker_for_account(tmp_path, ledger, peak, "acct-B", 30000.0)
    # Before account(), peak is 0; after, should be 30k
    assert broker_b._peak_equity == 0.0
    snap_b = broker_b.account()
    assert snap_b.peak_equity == 30000.0
    assert snap_b.peak_equity != 200000.0


def test_peak_account_scoped_E_epoch_isolated_per_account(tmp_path):
    """E: epoch reset for A does not affect B."""
    from datetime import UTC, datetime

    from parayabuck.execution.ledger import ExecutionLedger
    from parayabuck.portfolio.runtime_service import PortfolioStateRepository
    from parayabuck.risk.state import RiskState

    db = tmp_path / "e_epoch.sqlite3"
    ledger = ExecutionLedger(str(db))
    repo = PortfolioStateRepository(str(db), store=ledger._store)
    repo.update_high_water("acct-A", 100000.0)
    repo.update_high_water("acct-B", 80000.0)
    peak = tmp_path / "paper_equity_peak.json"
    # Bootstrap A at epoch 0 100k
    peak.write_text(json.dumps({"account_id": "acct-A", "peak_equity": 100000.0, "epoch": 0}))

    broker_a = _make_broker_for_account(tmp_path, ledger, peak, "acct-A", 100000.0)
    snap_a = broker_a.account()
    assert snap_a.peak_equity == 100000.0
    assert broker_a._peak_epoch == 0
    # Reset A to epoch 1 at 60000
    broker_a.reset_peak_equity(60000.0)
    repo.reset_high_water("acct-A", 60000.0)
    ledger.save_risk_state(
        RiskState(
            account_id="acct-A",
            session_date=datetime.now(UTC).date(),
            day_start_equity=60000.0,
            peak_equity=60000.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            fees=0.0,
            consecutive_losses=0,
            circuit_breaker_reason=None,
            updated_at=datetime.now(UTC),
            peak_epoch=broker_a._peak_epoch,
        )
    )
    assert broker_a._peak_epoch == 1
    # B should still be epoch 0 and peak 80k, not affected
    # Need fresh broker file bound to B; delete A-bound sidecar first to test DB restore for B
    peak.unlink()
    broker_b = _make_broker_for_account(tmp_path, ledger, peak, "acct-B", 80000.0)
    snap_b = broker_b.account()
    assert snap_b.peak_equity == 80000.0
    assert broker_b._peak_epoch == 0
    # A's epoch 1 still holds when we come back to A (even with sidecar deleted)
    peak.unlink()
    broker_a2 = _make_broker_for_account(tmp_path, ledger, peak, "acct-A", 60000.0)
    snap_a2 = broker_a2.account()
    assert snap_a2.peak_equity == 60000.0
    assert broker_a2._peak_epoch == 1
    # Simulate old high_water row for A epoch 0 still at 100k — must not resurrect
    assert snap_a2.peak_equity != 100000.0


def test_peak_account_scoped_F_identity_change_fails_closed(tmp_path):
    """F: changing account_id on same broker instance raises RuntimeError."""
    from parayabuck.execution.ledger import ExecutionLedger

    db = tmp_path / "f_identity.sqlite3"
    ledger = ExecutionLedger(str(db))
    peak = tmp_path / "paper_equity_peak.json"
    broker = _make_broker_for_account(tmp_path, ledger, peak, "acct-A", 50000.0)
    broker.account()
    assert broker._peak_account_id == "acct-A"
    # Now try to fetch as different account on same broker (should fail closed)
    broker._client.get_account = lambda: _make_fake_account("acct-B", 50000.0)
    with pytest.raises(RuntimeError, match="Account identity changed"):
        broker.account()


def test_peak_account_scoped_G_first_run_zero_and_legacy_no_sqlite(tmp_path):
    """G: first run with no sidecar and no SQLite bootstraps to 0, then to equity;
    legacy sidecar with no SQLite is used with warning and rebound."""
    from parayabuck.execution.ledger import ExecutionLedger

    db = tmp_path / "g_first.sqlite3"
    ledger = ExecutionLedger(str(db))
    peak = tmp_path / "paper_equity_peak.json"
    # No persisted, no sidecar -> first run 0 then max with equity
    broker = _make_broker_for_account(tmp_path, ledger, peak, "acct-new", 42000.0)
    assert broker._peak_equity == 0.0
    assert broker._peak_account_id is None
    snap = broker.account()
    # Peak should now be max(0, 42000) = 42000
    assert snap.peak_equity == 42000.0
    assert broker._peak_equity == 42000.0
    assert broker._peak_account_id == "acct-new"
    assert json.loads(peak.read_text())["account_id"] == "acct-new"
    assert json.loads(peak.read_text())["peak_equity"] == 42000.0
    # Legacy with no SQLite persisted: use legacy and rebound
    db2 = tmp_path / "g_legacy.sqlite3"
    ledger2 = ExecutionLedger(str(db2))
    peak2 = tmp_path / "paper_equity_peak2.json"
    peak2.write_text(json.dumps({"peak_equity": 77000.0, "epoch": 0}))  # legacy, no account_id, no DB row
    broker2 = _make_broker_for_account(tmp_path, ledger2, peak2, "acct-legacy", 77000.0)
    snap2 = broker2.account()
    assert snap2.peak_equity == 77000.0
    assert json.loads(peak2.read_text())["account_id"] == "acct-legacy"
    # Reset must require account known
    broker3 = AlpacaBroker("k", "s", paper=True, peak_state_path=tmp_path / "paper_equity_peak3.json", ledger=ledger2)
    with pytest.raises(RuntimeError, match="before account is known"):
        broker3.reset_peak_equity(10000.0)


def test_peak_db_no_history_first_run_allowed(tmp_path):
    """A: no sidecar, SQLite succeeds, no rows -> genuine first run 0 allowed."""
    from parayabuck.execution.ledger import ExecutionLedger

    db = tmp_path / "db_no_history.sqlite3"
    ledger = ExecutionLedger(str(db))
    peak = tmp_path / "paper_equity_peak.json"
    # No sidecar, no persisted rows for acct-new
    broker = _make_broker_for_account(tmp_path, ledger, peak, "acct-new", 42000.0)
    # Before account(), peak is 0 (deferred)
    assert broker._peak_equity == 0.0
    assert broker._peak_account_id is None
    snap = broker.account()
    # Genuine first run: peak becomes current equity
    assert snap.peak_equity == 42000.0
    assert broker._peak_equity == 42000.0
    assert broker._peak_account_id == "acct-new"
    assert json.loads(peak.read_text())["account_id"] == "acct-new"


def test_peak_db_high_water_raises_fail_closed(tmp_path):
    """B: no sidecar, portfolio_high_water SELECT raises -> fail closed."""
    from parayabuck.execution.ledger import ExecutionLedger

    db = tmp_path / "db_hw_fail.sqlite3"
    ledger = ExecutionLedger(str(db))
    peak = tmp_path / "paper_equity_peak.json"
    broker = _make_broker_for_account(tmp_path, ledger, peak, "acct-A", 50000.0)
    with patch.object(
        RiskPeakService,
        "query_persisted_peak",
        side_effect=RiskStatePersistenceError(
            "portfolio_high_water read failed for acct-A: injected high_water failure"
        ),
    ):
        with pytest.raises(RiskStatePersistenceError, match="portfolio_high_water read failed"):
            broker.account()
    # Must not have created sidecar or mutated peak to 50000
    assert not peak.exists() or json.loads(peak.read_text()).get("peak_equity") != 50000.0
    # Peak should remain 0 and not reconciled
    assert broker._peak_equity == 0.0
    assert broker._peak_account_id is None or broker._peak_account_id != "acct-A"


def test_peak_db_risk_state_raises_fail_closed(tmp_path):
    """C: no sidecar, risk_state SELECT raises -> fail closed."""
    from parayabuck.execution.ledger import ExecutionLedger

    db = tmp_path / "db_rs_fail.sqlite3"
    ledger = ExecutionLedger(str(db))
    peak = tmp_path / "paper_equity_peak.json"
    broker = _make_broker_for_account(tmp_path, ledger, peak, "acct-A", 50000.0)
    with patch.object(
        RiskPeakService,
        "query_persisted_peak",
        side_effect=RiskStatePersistenceError("risk_state read failed for acct-A: injected risk_state failure"),
    ):
        with pytest.raises(RiskStatePersistenceError, match="risk_state read failed"):
            broker.account()
    assert broker._peak_equity == 0.0


def test_peak_db_persisted_100k_injected_failure_not_rebase(tmp_path):
    """D: persisted 100k, sidecar missing, injected failure -> must not rebase to 94000."""
    from datetime import UTC, datetime

    from parayabuck.execution.ledger import ExecutionLedger
    from parayabuck.portfolio.runtime_service import PortfolioStateRepository
    from parayabuck.risk.state import RiskState

    db = tmp_path / "db_persisted_fail.sqlite3"
    ledger = ExecutionLedger(str(db))
    repo = PortfolioStateRepository(str(db), store=ledger._store)
    repo.update_high_water("acct-A", 100000.0)
    ledger.save_risk_state(
        RiskState(
            account_id="acct-A",
            session_date=datetime.now(UTC).date(),
            day_start_equity=100000.0,
            peak_equity=100000.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            fees=0.0,
            consecutive_losses=0,
            circuit_breaker_reason=None,
            updated_at=datetime.now(UTC),
            peak_epoch=0,
        )
    )
    peak = tmp_path / "paper_equity_peak.json"
    assert not peak.exists()
    broker = _make_broker_for_account(tmp_path, ledger, peak, "acct-A", 94000.0)
    with patch.object(RiskPeakService, "query_persisted_peak", side_effect=RiskStatePersistenceError("injected failure")):
        with pytest.raises(RiskStatePersistenceError):
            broker.account()
    # Must not have rebased to 94000
    assert broker._peak_equity == 0.0
    # DB still has 100k, sidecar not created as 94k
    assert not peak.exists() or json.loads(peak.read_text()).get("peak_equity") != 94000.0
    # After removing injection, restart must restore 100k, not 94k
    broker2 = _make_broker_for_account(tmp_path, ledger, peak, "acct-A", 94000.0)
    snap2 = broker2.account()
    assert snap2.peak_equity == 100000.0
    assert snap2.drawdown_pct == pytest.approx(0.06, rel=1e-6)


def test_peak_db_sidecar_valid_sqlite_fails_fail_closed(tmp_path):
    """E: sidecar valid and account-bound, SQLite fails -> fail closed."""
    from parayabuck.execution.ledger import ExecutionLedger

    db = tmp_path / "db_sidecar_valid_fail.sqlite3"
    ledger = ExecutionLedger(str(db))
    peak = tmp_path / "paper_equity_peak.json"
    peak.write_text(json.dumps({"account_id": "acct-A", "peak_equity": 100000.0, "epoch": 0}))
    broker = _make_broker_for_account(tmp_path, ledger, peak, "acct-A", 94000.0)
    with patch.object(RiskPeakService, "query_persisted_peak", side_effect=RiskStatePersistenceError("injected")):
        with pytest.raises(RiskStatePersistenceError):
            broker.account()
    # Must not have used sidecar alone to return 100k
    assert broker._peak_equity == 0.0


def test_peak_db_all_rows_corrupt_fail_closed(tmp_path):
    """Malformed risk_state rows all corrupt -> fail closed, not no-history."""
    from datetime import UTC, datetime

    from parayabuck.execution.ledger import ExecutionLedger

    db = tmp_path / "db_corrupt_rows.sqlite3"
    ledger = ExecutionLedger(str(db))
    # Insert corrupt payloads for acct-A directly
    ledger.connection.execute(
        "INSERT INTO risk_state (account_id, session_date, payload, updated_at) VALUES (?, ?, ?, ?)",
        ("acct-A", "2026-08-27", "{ not json", datetime.now(UTC).isoformat()),
    )
    ledger.connection.execute(
        "INSERT INTO risk_state (account_id, session_date, payload, updated_at) VALUES (?, ?, ?, ?)",
        ("acct-A", "2026-08-28", json.dumps({"peak_equity": "nan", "peak_epoch": 0}), datetime.now(UTC).isoformat()),
    )
    ledger.connection.commit()
    peak = tmp_path / "paper_equity_peak.json"
    broker = _make_broker_for_account(tmp_path, ledger, peak, "acct-A", 50000.0)
    with pytest.raises(RiskStatePersistenceError, match="all unreadable"):
        broker.account()


def test_peak_db_different_account_rows_ignored(tmp_path):
    """F: different account rows remain ignored; DB failure for current still fails closed."""
    from parayabuck.execution.ledger import ExecutionLedger
    from parayabuck.portfolio.runtime_service import PortfolioStateRepository

    db = tmp_path / "db_diff_acct.sqlite3"
    ledger = ExecutionLedger(str(db))
    repo = PortfolioStateRepository(str(db), store=ledger._store)
    repo.update_high_water("acct-B", 500000.0)
    peak = tmp_path / "paper_equity_peak.json"
    broker = _make_broker_for_account(tmp_path, ledger, peak, "acct-A", 50000.0)
    # Should not see B's 500k
    snap = broker.account()
    assert snap.peak_equity == 50000.0
    # Now inject failure for A, ensure still fail closed even though B has data
    broker2 = _make_broker_for_account(tmp_path, ledger, tmp_path / "paper2.json", "acct-A", 50000.0)
    # Force fresh reconciliation by clearing flag
    broker2._peak_reconciled = False
    broker2._peak_account_id = None
    with patch.object(RiskPeakService, "query_persisted_peak", side_effect=RiskStatePersistenceError("injected for A")):
        with pytest.raises(RiskStatePersistenceError):
            broker2.account()
