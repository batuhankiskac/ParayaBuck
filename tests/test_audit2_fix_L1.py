"""Audit wave 2 fix L1: ops/halt/doctor/rebase writers and exit codes (raw-r7 F2-9, D2-6 and leftovers).

Every test uses tmp_path state, a synthetic ledger and fakes for the broker, the
journal and systemd. Nothing reaches the network, the credentials or data/.
"""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
from argparse import Namespace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from parayabuck import cli
from parayabuck.cli import main
from parayabuck.core.models import Market
from parayabuck.execution.portfolio import PortfolioOrderRequest, PortfolioOrderSide, RiskOverlayDecision
from parayabuck.portfolio.models import TargetPortfolio
from parayabuck.portfolio.runtime_repository import (
    OwnershipRebaseRefused,
    PortfolioStateRepository,
    ownership_rebase_plan_digest,
)

ACCOUNT = "acct-uuid"
SYMBOL = "QLD"
ENTRY_PRICE = 50.0
T0 = datetime(2026, 9, 1, 13, 30, tzinfo=UTC)
REPO_SETTINGS = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"


# --- ledger and config fixtures ---------------------------------------------


def _buy_100(db: Path) -> PortfolioStateRepository:
    repo = PortfolioStateRepository(db)
    target = TargetPortfolio("vt35", "1", T0, (), 1.0)
    risk = RiskOverlayDecision(target, False, (), 0.0, 0.0)
    session = repo.begin_session(ACCOUNT, "digest", "paper")
    decision_id = repo.record_decision_with_orders(session, ACCOUNT, "digest", target, risk, Market.EQUITY, ())
    cid = "cid-buy-100"
    repo.record_order(
        PortfolioOrderRequest(
            client_order_id=cid,
            decision_id=decision_id,
            symbol=SYMBOL,
            market=Market.EQUITY,
            side=PortfolioOrderSide("buy"),
            quantity=100.0,
            reference_price=ENTRY_PRICE,
            decision_timestamp=T0,
            strategy_name="vt35",
            strategy_version="1",
        ),
        "submitted",
    )
    repo.apply_trade_update(
        client_order_id=cid,
        broker_order_id="broker-cid-buy-100",
        status="filled",
        cumulative_filled_quantity=100.0,
        average_fill_price=ENTRY_PRICE,
        occurred_at=T0,
        activity_id="activity-cid-buy-100",
        fee=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
    )
    return repo


def _owned(repo: PortfolioStateRepository) -> float:
    return repo.owned_quantities(ACCOUNT, Market.EQUITY).get(SYMBOL, 0.0)


def _config(tmp_path: Path, db: Path, *, pinned: str | None = None, mode: str = "paper") -> SimpleNamespace:
    """A stand-in for the loaded Config with every path under tmp_path."""
    return SimpleNamespace(
        runtime=SimpleNamespace(decision_db_path=str(db), mode=mode),
        broker=SimpleNamespace(expected_account_id=pinned),
        universe=SimpleNamespace(equity=SimpleNamespace(symbols=[SYMBOL])),
        observability=SimpleNamespace(
            journal_path=str(tmp_path / "journal.sqlite3"),
            snapshot_path=str(tmp_path / "snapshot.json"),
            snapshot_interval_seconds=30.0,
        ),
    )


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[PortfolioStateRepository, Path, SimpleNamespace]:
    """A 100-share ledger, a fake config, and no engine heartbeat."""
    db = tmp_path / "runtime.sqlite3"
    repo = _buy_100(db)
    config = _config(tmp_path, db)
    monkeypatch.setattr("parayabuck.cli.load_config", lambda _path=None: config)
    monkeypatch.setattr(cli, "_engine_may_be_running", lambda _config: False)
    return repo, db, config


def _rebase(db: Path, *argv: str) -> int:
    return main(["ops", "rebase-ownership", SYMBOL, "--db", str(db), "--account-id", ACCOUNT, *argv])


def _dry_run_digest(db: Path, *flags: str) -> str:
    """Run the dry run for these flags and return the plan digest it printed."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert _rebase(db, *flags) == 0
    line = next(item for item in buffer.getvalue().splitlines() if item.startswith("Plan digest"))
    return line.split(":", 1)[1].strip()


# --- F2-9-1: --confirm binds the plan digest ---------------------------------


def test_confirm_without_a_plan_digest_writes_nothing(
    ledger: tuple[PortfolioStateRepository, Path, SimpleNamespace], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, db, _ = ledger

    code = _rebase(db, "--broker-qty", "50", "--broker-average-cost", "100", "--confirm", "100")

    assert code == 64
    assert "--plan-digest" in capsys.readouterr().err
    assert _owned(repo) == pytest.approx(100.0)


def test_confirm_with_the_dry_run_digest_books_the_split(
    ledger: tuple[PortfolioStateRepository, Path, SimpleNamespace],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, db, config = ledger
    events: list[dict[str, object]] = []
    monkeypatch.setattr(
        "parayabuck.monitoring.journal.emit_journal",
        lambda event, path=None: events.append({"kind": event.kind, **dict(event.fields)}) or True,
    )
    flags = ("--broker-qty", "50", "--broker-average-cost", "100")
    digest = _dry_run_digest(db, *flags)

    code = _rebase(db, *flags, "--confirm", "100", "--plan-digest", digest)

    assert code == 0
    assert _owned(repo) == pytest.approx(50.0)
    # F2-9-4: the operator write leaves one journal row carrying the digest and the proof.
    assert [event["kind"] for event in events] == ["ownership_rebased"]
    assert events[0]["plan_digest"] == digest
    assert events[0]["broker_average_cost"] == 100.0


def test_dropping_the_broker_average_cost_changes_the_plan_and_is_refused(
    ledger: tuple[PortfolioStateRepository, Path, SimpleNamespace], capsys: pytest.CaptureFixture[str]
) -> None:
    """The F2-9-1 scenario: the dry run showed a split; a confirm without the cost would sell."""
    repo, db, _ = ledger
    digest = _dry_run_digest(db, "--broker-qty", "50", "--broker-average-cost", "100")

    code = _rebase(db, "--broker-qty", "50", "--confirm", "100", "--plan-digest", digest)

    assert code == 1
    assert "plan digest" in capsys.readouterr().err
    assert _owned(repo) == pytest.approx(100.0)
    assert repo.ownership_adjustments(ACCOUNT) == ()


def test_repository_refuses_a_write_under_another_plan_digest(tmp_path: Path) -> None:
    repo = _buy_100(tmp_path / "runtime.sqlite3")

    with pytest.raises(OwnershipRebaseRefused, match="dry run"):
        repo.rebase_ownership_to_broker_quantity(
            ACCOUNT,
            SYMBOL,
            Market.EQUITY,
            50.0,
            broker_average_cost=100.0,
            expected_owned=100.0,
            plan_digest="000000000000",
        )

    assert _owned(repo) == pytest.approx(100.0)
    assert repo.ownership_adjustments(ACCOUNT) == ()


def test_repository_note_records_the_proof_the_booking_rested_on(tmp_path: Path) -> None:
    """F2-9-4: the stored note names the broker average cost, the expected owned quantity and the digest."""
    repo = _buy_100(tmp_path / "runtime.sqlite3")
    plan = repo.plan_ownership_rebase(ACCOUNT, SYMBOL, Market.EQUITY, 50.0, broker_average_cost=100.0)
    digest = ownership_rebase_plan_digest(
        ACCOUNT, SYMBOL, Market.EQUITY, 50.0, plan, broker_average_cost=100.0, unit_cost=None
    )

    repo.rebase_ownership_to_broker_quantity(
        ACCOUNT,
        SYMBOL,
        Market.EQUITY,
        50.0,
        note="QLD 1:2 reverse split",
        broker_average_cost=100.0,
        expected_owned=100.0,
        plan_digest=digest,
    )

    (row,) = repo.ownership_adjustments(ACCOUNT)
    assert row["note"].startswith("QLD 1:2 reverse split [proof: ")
    assert "broker_average_cost=100.0" in row["note"]
    assert "expected_owned=100" in row["note"]
    assert f"plan_digest={digest}" in row["note"]


def test_dry_run_states_the_broker_average_cost(
    ledger: tuple[PortfolioStateRepository, Path, SimpleNamespace], capsys: pytest.CaptureFixture[str]
) -> None:
    """F2-8-3: the dry run shows the split proof it was given, or that none was given."""
    _, db, _ = ledger

    assert _rebase(db, "--broker-qty", "50") == 0
    assert "Broker avg  : not given" in capsys.readouterr().out

    assert _rebase(db, "--broker-qty", "50", "--broker-average-cost", "100") == 0
    assert "Broker avg  : 100 per share" in capsys.readouterr().out


# --- F2-9-2: the pin accepts the account UUID or the PA number ----------------


class _FakeBroker:
    """Stands in for AlpacaBroker: reports one account and records every construction."""

    constructed: list[str] = []
    account_id = ACCOUNT
    account_number = "PA3XYZ"

    def __init__(self, key: str, secret: str, paper: bool = True) -> None:
        type(self).constructed.append("paper" if paper else "live")

    def account(self) -> None:
        return None

    def close(self) -> None:
        return None


@pytest.fixture
def fake_broker(monkeypatch: pytest.MonkeyPatch) -> type[_FakeBroker]:
    _FakeBroker.constructed = []
    monkeypatch.setattr(cli, "AlpacaBroker", _FakeBroker)
    monkeypatch.setattr("parayabuck.live_safety.mode_credentials", lambda live: ("key", "secret"))
    return _FakeBroker


def _pinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, db: Path, pinned: str, mode: str = "paper"
) -> SimpleNamespace:
    config = _config(tmp_path, db, pinned=pinned, mode=mode)
    monkeypatch.setattr("parayabuck.cli.load_config", lambda _path=None: config)
    return config


def test_a_pa_number_pin_is_verified_against_the_broker_account(
    ledger: tuple[PortfolioStateRepository, Path, SimpleNamespace],
    fake_broker: type[_FakeBroker],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, db, _ = ledger
    _pinned(monkeypatch, tmp_path, db, "PA3XYZ")

    assert _rebase(db, "--broker-qty", "50") == 0
    assert "Dry run" in capsys.readouterr().out
    assert fake_broker.constructed == ["paper"]


def test_a_pa_number_pin_naming_another_account_is_refused(
    ledger: tuple[PortfolioStateRepository, Path, SimpleNamespace],
    fake_broker: type[_FakeBroker],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, db, _ = ledger
    _pinned(monkeypatch, tmp_path, db, "PA9999")

    assert _rebase(db, "--broker-qty", "50") == 1
    assert "broker.expected_account_id" in capsys.readouterr().err


def test_a_uuid_pin_naming_another_account_is_refused_without_a_broker_call(
    ledger: tuple[PortfolioStateRepository, Path, SimpleNamespace],
    fake_broker: type[_FakeBroker],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, db, _ = ledger
    _pinned(monkeypatch, tmp_path, db, "some-other-uuid")

    assert _rebase(db, "--broker-qty", "50") == 1
    assert "broker.expected_account_id" in capsys.readouterr().err
    assert fake_broker.constructed == []


# --- F2-9-3: a live engine refuses the write ----------------------------------


def _write_snapshot(tmp_path: Path, *, written_at: datetime, state: str = "running") -> None:
    (tmp_path / "snapshot.json").write_text(
        json.dumps({"written_at": written_at.isoformat(), "runtime_state": state}), encoding="utf-8"
    )


@pytest.fixture
def no_systemd(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(cli.subprocess, "run", _missing)


def test_confirm_refuses_while_the_engine_heartbeat_is_fresh(
    tmp_path: Path,
    no_systemd: None,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "runtime.sqlite3"
    repo = _buy_100(db)
    monkeypatch.setattr("parayabuck.cli.load_config", lambda _path=None: _config(tmp_path, db))
    _write_snapshot(tmp_path, written_at=datetime.now(UTC))

    digest = _dry_run_digest(db, "--broker-qty", "50")
    code = _rebase(db, "--broker-qty", "50", "--confirm", "100", "--plan-digest", digest)

    assert code == 1
    assert "engine heartbeat is live" in capsys.readouterr().err
    assert _owned(repo) == pytest.approx(100.0)


@pytest.mark.parametrize(
    ("written_offset", "state", "expected"),
    [
        (timedelta(seconds=5), "stopped", False),  # a clean stop is not live, however fresh
        (timedelta(hours=2), "running", False),  # a stale snapshot is not live
        (timedelta(seconds=5), "running", True),  # a fresh running snapshot is live
    ],
)
def test_engine_liveness_reads_the_snapshot(
    tmp_path: Path,
    no_systemd: None,
    written_offset: timedelta,
    state: str,
    expected: bool,
) -> None:
    _write_snapshot(tmp_path, written_at=datetime.now(UTC) - written_offset, state=state)

    assert cli._engine_may_be_running(_config(tmp_path, tmp_path / "runtime.sqlite3")) is expected


def test_engine_liveness_fails_closed_on_an_unreadable_snapshot(tmp_path: Path, no_systemd: None) -> None:
    (tmp_path / "snapshot.json").write_text("{not json", encoding="utf-8")

    assert cli._engine_may_be_running(_config(tmp_path, tmp_path / "runtime.sqlite3")) is True


# --- F2-9-5 / R2-9-6 / R2-9-8: halt and resume ---------------------------------


def _settings(tmp_path: Path, *, raw_override: str | None = None) -> Path:
    path = tmp_path / "settings.yaml"
    if raw_override is not None:
        path.write_text(raw_override, encoding="utf-8")
        return path
    raw = yaml.safe_load(REPO_SETTINGS.read_text(encoding="utf-8"))
    raw["runtime"]["decision_db_path"] = str(tmp_path / "runtime" / "parayabuck.sqlite3")
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def halt_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ENV_PATH", str(tmp_path / "absent.env"))
    (tmp_path / "runtime").mkdir()
    return tmp_path


def test_halt_states_mode_and_marker_and_resume_reports_the_persisted_row(
    halt_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _settings(halt_env)
    db = halt_env / "runtime" / "parayabuck.sqlite3"
    with contextlib.closing(sqlite3.connect(db)) as connection:
        connection.execute(
            "CREATE TABLE safety_incidents (account_id TEXT, reason TEXT, scope TEXT, activated_at TEXT, updated_at TEXT)"
        )
        connection.execute(
            "INSERT INTO safety_incidents VALUES ('acct', 'operator_halt', 'global', '2026-10-01', '2026-10-01')"
        )
        connection.commit()

    assert main(["--config", str(settings), "halt", "--reason", "drill"]) == 0
    halted = capsys.readouterr().out
    assert "mode=paper" in halted
    assert str(halt_env / "runtime" / "OPERATOR_HALT") in halted

    assert main(["--config", str(settings), "resume"]) == 0
    resumed = capsys.readouterr().out
    assert "A persisted operator_halt incident row still exists" in resumed


def test_halt_with_a_broken_configuration_says_no_marker_was_written(
    halt_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = _settings(halt_env, raw_override="runtime: {mode: backtest}\n")

    assert main(["--config", str(broken), "halt"]) == 1
    assert "no marker was written or removed" in capsys.readouterr().err
    assert not (halt_env / "runtime" / "OPERATOR_HALT").exists()


# --- F2-9-7: revoking the standing authorization says what keeps running ------


def test_revoking_the_standing_authorization_states_that_a_running_engine_keeps_trading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    standing = tmp_path / "standing.json"
    standing.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("parayabuck.live_authorization.standing_authorization_file", lambda _config: standing)

    code = cli._authorize_live(SimpleNamespace(), Namespace(revoke_standing=True, operator=None))

    assert code == 0
    assert not standing.exists()
    assert "keeps trading" in capsys.readouterr().out


# --- F2-9-6: tax import refuses a missing ledger -------------------------------


def test_tax_import_refuses_to_create_a_missing_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from parayabuck.tax import cli as tax_cli

    ledger_path = tmp_path / "absent" / "ledger.sqlite3"
    monkeypatch.setattr(tax_cli, "_database_path", lambda *_a, **_k: ledger_path)
    monkeypatch.setattr("parayabuck.live_safety.mode_credentials", lambda live: ("key", "secret"))

    class _NoNetwork:
        def __init__(self, *_a: object, **_k: object) -> None:
            raise AssertionError("no broker call may happen before the ledger check")

    monkeypatch.setattr("parayabuck.execution.alpaca_client.AlpacaClient", _NoNetwork)

    code = tax_cli._import_activities(Namespace(config=None, live=False, since="2026-01-01"))

    assert code == 1
    assert "does not exist" in capsys.readouterr().err
    assert not ledger_path.parent.exists()


# --- F2-9-4 companion / D2-6: exit codes and their documented values ----------


def test_usage_errors_exit_64_not_the_critical_code(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["ops", "events", "--severity", "bogus"])

    assert excinfo.value.code == 64
    assert "usage" in capsys.readouterr().err.lower()


def test_an_invalid_time_argument_exits_64() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli._parse_time_arg("soon")

    assert excinfo.value.code == 64


@pytest.mark.parametrize(
    ("overall", "code"),
    [("OK", 0), ("DEGRADED", 1), ("BLOCKED", 1), ("UNKNOWN", 1), ("CRITICAL", 2)],
)
def test_ops_status_and_doctor_share_one_health_exit_mapping(overall: str, code: int) -> None:
    assert cli._health_exit_code(overall) == code


def test_ops_explain_of_an_unknown_decision_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("parayabuck.monitoring.forensics.decision_reconstruction", lambda *_a, **_k: {})

    code = main(
        [
            "--config",
            str(tmp_path / "missing.yaml"),
            "ops",
            "explain",
            "dec-unknown",
            "--json",
            "--db",
            str(tmp_path / "decisions.sqlite3"),
            "--journal",
            str(tmp_path / "absent.sqlite3"),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["decision_id"] == "dec-unknown"
    assert payload["decision"] is None
    assert "not found" in payload["message"]


def test_events_help_says_naive_times_are_utc(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["ops", "events", "--help"])

    # argparse wraps the help text, so compare with the whitespace collapsed.
    assert "naive ISO time is read as UTC" in " ".join(capsys.readouterr().out.split())


# --- D2-8-5 follow-up / F2-7-11: ops status ------------------------------------


def test_ops_status_prints_the_email_delivery_block_and_flags_an_unconfirmed_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "written_at": datetime.now(UTC).isoformat(),
                "alerts": {
                    "suppressed": 0,
                    "delivered": {},
                    "email": {"sent": 3, "failed": 1, "dropped": 0, "last_success_age_seconds": 12.4},
                },
            }
        ),
        encoding="utf-8",
    )
    code = main(["ops", "status", "--snapshot", str(snapshot), "--db", str(tmp_path / "absent.sqlite3")])
    out = capsys.readouterr().out
    assert code in {0, 1, 2}
    assert "e-mail sent 3, failed 1, dropped 0, last success 12s ago" in out

    absent = tmp_path / "no_snapshot.json"
    main(["ops", "status", "--snapshot", str(absent), "--db", str(tmp_path / "absent.sqlite3")])
    assert "no health snapshot to confirm it" in capsys.readouterr().out


# --- R2-10-7: printout of labelled backups and bundles -------------------------


def test_disk_cost_line_counts_and_sizes_the_matching_files(tmp_path: Path) -> None:
    (tmp_path / "parayabuck-keep-predeploy-1.sqlite3").write_bytes(b"x" * 1024)
    (tmp_path / "parayabuck-keep-predeploy-1.sqlite3.sidecar").write_bytes(b"y" * 1024)
    (tmp_path / "parayabuck-20261027.sqlite3").write_bytes(b"z" * 4096)

    line = cli._disk_cost_line(tmp_path, "parayabuck-keep-*", "Labelled backups")

    assert line.startswith("Labelled backups: 2 file(s), 0.0 MB in ")
    assert "No automatic retention applies" in line


# --- D2-7-4 / D2-7-5 / S2-10-5 prerequisite: doctor rows -----------------------


def test_doctor_warns_on_an_inline_comment_by_key_name_and_never_prints_the_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env_file = tmp_path / "env"
    env_file.write_text(
        '# a full-line comment\nSMTP_PASSWORD=s3cr3tVALUE # trailing note\nPLAIN=ok\nQUOTED="a # b"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ENV_PATH", str(env_file))

    assert cli._inline_comment_keys_in_env_file(env_file) == ["SMTP_PASSWORD"]

    settings = _settings(tmp_path)
    main(["--config", str(settings), "doctor", "--offline"])
    out = capsys.readouterr().out
    assert "WARN credential file inline comments: SMTP_PASSWORD" in out
    assert "s3cr3tVALUE" not in out


def test_doctor_reports_a_missing_reports_dir_as_a_note_with_install_d(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ENV_PATH", str(tmp_path / "absent.env"))
    blocker_file = tmp_path / "not_a_dir"
    blocker_file.write_text("", encoding="utf-8")
    raw = yaml.safe_load(REPO_SETTINGS.read_text(encoding="utf-8"))
    raw["runtime"]["decision_db_path"] = str(tmp_path / "runtime" / "parayabuck.sqlite3")
    raw["backtest"]["output_dir"] = str(blocker_file / "reports")
    raw["backup"]["path"] = str(tmp_path / "backups")
    settings = tmp_path / "settings.yaml"
    settings.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    main(["--config", str(settings), "doctor", "--offline"])
    out = capsys.readouterr().out

    assert "WARN reports path writable:" in out
    assert "install -d -m 0700 -o <service user>" in out
    assert "FAIL reports path writable" not in out


def test_doctor_checks_the_backup_directory_without_writing_a_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ENV_PATH", str(tmp_path / "absent.env"))
    backups = tmp_path / "backups"
    backups.mkdir()
    raw = yaml.safe_load(REPO_SETTINGS.read_text(encoding="utf-8"))
    raw["runtime"]["decision_db_path"] = str(tmp_path / "runtime" / "parayabuck.sqlite3")
    raw["backup"]["path"] = str(backups)
    raw["backtest"]["output_dir"] = str(tmp_path / "reports")
    settings = tmp_path / "settings.yaml"
    settings.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    main(["--config", str(settings), "doctor", "--offline"])

    assert f"PASS backup path writable: {backups}" in capsys.readouterr().out
    assert not any(backups.iterdir())
