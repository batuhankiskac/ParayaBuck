"""Audit wave 2 fixes M2: file modes and umask (S2-4), subprocess and git use (S2-7).

Every test works on ``tmp_path`` or in-memory fixtures. Nothing touches the
network, credentials, data/ or logs/, and no production path is written.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import json
import logging
import os
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import parayabuck.backup as backup
import parayabuck.cli as cli
import parayabuck.live_safety as live_safety
import parayabuck.monitoring.diagnostic as diagnostic
import parayabuck.web.managers.engine as engine_module
from parayabuck.monitoring.paper_incident import write_anonymized_fixture

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_repair_script() -> Any:
    path = _REPO_ROOT / "scripts" / "repair_local_state.py"
    spec = importlib.util.spec_from_file_location("repair_local_state_m2", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def restore_umask() -> Any:
    """Put the process umask back after a test that calls a umask-setting entry point."""
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


# --- S2-7-1: the backup unit's start budget covers the off-host copy budget ---


def test_backup_unit_start_timeout_covers_the_offhost_budget() -> None:
    unit = (_REPO_ROOT / "deploy" / "systemd" / "parayabuck-backup.service").read_text(encoding="utf-8")
    values = [line.split("=", 1)[1].strip() for line in unit.splitlines() if line.startswith("TimeoutStartSec=")]
    assert len(values) == 1, "the backup unit must set TimeoutStartSec exactly once"
    assert int(values[0]) >= backup.OFFHOST_TIMEOUT_SECONDS * 2


# --- S2-7-4: the copy command does not inherit the SMTP login or other secrets ---


def test_offhost_child_env_drops_smtp_and_secret_names_only() -> None:
    source = {
        "PATH": "/usr/bin",
        "HOME": "/home/parayabuck",
        "CONFIG_PATH": "./config/settings.yaml",
        "GOOGLE_APPLICATION_CREDENTIALS": "/opt/parayabuck/gcs.json",
        backup.OFFHOST_CMD_ENV: "gsutil cp {path} gs://bucket/",
        "PARAYABUCK_SMTP_USERNAME": "mailer",
        "PARAYABUCK_SMTP_PASSWORD": "smtp-secret",
        "SOME_SERVICE_TOKEN": "tok",
        "ALPACA_SECRET_KEY": "alpaca",
    }
    child = backup._offhost_child_env(source)
    assert child == {
        "PATH": "/usr/bin",
        "HOME": "/home/parayabuck",
        "CONFIG_PATH": "./config/settings.yaml",
        "GOOGLE_APPLICATION_CREDENTIALS": "/opt/parayabuck/gcs.json",
        backup.OFFHOST_CMD_ENV: "gsutil cp {path} gs://bucket/",
    }


def test_copy_offhost_hands_the_child_the_filtered_environment(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        backup.OFFHOST_CMD_ENV: "gsutil cp {path} gs://bucket/",
        "PARAYABUCK_SMTP_PASSWORD": "smtp-secret",
    }
    assert backup.copy_offhost(tmp_path / "s.sqlite3", env=env, runner=runner) is True
    assert "PARAYABUCK_SMTP_PASSWORD" not in seen["env"]
    assert seen["env"]["PATH"] == env["PATH"]


# --- S2-7-5: a non-UTF-8 byte from the copy tool is a copy failure, not a crash ---


def test_offhost_copy_non_utf8_stderr_is_an_offhost_failure(tmp_path: Path) -> None:
    script = "import sys; sys.stderr.buffer.write(b'\\xff\\xfe bad'); sys.exit(3)"
    template = f'{sys.executable} -c "{script}"'
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), backup.OFFHOST_CMD_ENV: template}
    with pytest.raises(backup.OffhostCopyError, match="exited 3"):
        backup.copy_offhost(tmp_path / "s.sqlite3", env=env)


# --- S2-4-4: a loose backup environment file is reported on the off-host path ---


def test_offhost_copy_warns_when_the_backup_env_file_is_group_readable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    env_file = tmp_path / ".env.backup"
    env_file.write_text("PARAYABUCK_SMTP_PASSWORD=x\n", encoding="utf-8")
    os.chmod(env_file, 0o644)
    env = {backup.OFFHOST_CMD_ENV: "cp {path} /tmp/x", "ENV_PATH": str(env_file)}

    with caplog.at_level(logging.WARNING, logger="parayabuck.backup"):
        assert backup.copy_offhost(tmp_path / "s.sqlite3", env=env, runner=_ok_runner) is True
    assert any("not owner-only" in record.getMessage() for record in caplog.records)


def test_offhost_copy_is_quiet_for_an_owner_only_backup_env_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    env_file = tmp_path / ".env.backup"
    env_file.write_text("PARAYABUCK_SMTP_PASSWORD=x\n", encoding="utf-8")
    os.chmod(env_file, 0o600)
    env = {backup.OFFHOST_CMD_ENV: "cp {path} /tmp/x", "ENV_PATH": str(env_file)}

    with caplog.at_level(logging.WARNING, logger="parayabuck.backup"):
        assert backup.copy_offhost(tmp_path / "s.sqlite3", env=env, runner=_ok_runner) is True
    assert not any("not owner-only" in record.getMessage() for record in caplog.records)


def _ok_runner(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")


# --- S2-4-6: the diagnostic bundle staging file is not a predictable path ---


def test_bundle_is_staged_through_mkstemp_and_never_follows_a_planted_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me", encoding="utf-8")
    out_dir = tmp_path / "diagnostics"
    out_dir.mkdir()
    out = out_dir / "bundle.zip"
    # The old staging name was predictable: .<name>.<pid>.tmp. Plant a link there.
    planted = out_dir / f".{out.name}.{os.getpid()}.tmp"
    planted.symlink_to(outside)

    monkeypatch.setattr(
        diagnostic,
        "collect_bundle_data",
        lambda **_kwargs: {"manifest": {"ok": True}, "status": {}, "doctor": {}, "recent_events": []},
    )
    result = diagnostic.create_bundle_zip(out)

    assert result == out
    assert outside.read_text(encoding="utf-8") == "keep me"
    with zipfile.ZipFile(out) as archive:
        assert "manifest.json" in archive.namelist()
    assert sorted(item.name for item in out_dir.iterdir()) == sorted({"bundle.zip", planted.name})


# --- S2-4-1: the paper-incident fixture is owner-only ---


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_anonymized_fixture_is_written_owner_only(tmp_path: Path) -> None:
    target = tmp_path / "fixtures" / "replay.json"
    write_anonymized_fixture(target, {"incident": "x"})
    assert target.read_text(encoding="utf-8").startswith("{")
    assert target.stat().st_mode & 0o777 == 0o600


# --- S2-4-1: the repair script sets the owner-only umask before it writes ---


@pytest.mark.skipif(os.name != "posix", reason="umask is POSIX")
def test_repair_script_sets_an_owner_only_umask(restore_umask: None) -> None:
    repair = _load_repair_script()
    with pytest.raises(SystemExit):
        repair.main(["--help"])
    current = os.umask(0o022)
    assert current == 0o077


# --- S2-4-3: orphaned credential temp files are ignored by git ---


def test_gitignore_covers_atomic_write_env_temp_files_and_not_the_backup_file() -> None:
    patterns = [
        line.strip()
        for line in (_REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    # atomic_write names its temp file ".<target name>.XXXXXXXX", so for .env
    # the name starts with two dots.
    assert any(fnmatch.fnmatchcase("..env.a1b2c3d4", pattern) for pattern in patterns), (
        "a mkstemp .env temp file must be ignored"
    )
    assert not any(fnmatch.fnmatchcase(".env.backup", pattern) for pattern in patterns), (
        ".env.backup is an operator file and must not be ignored"
    )


# --- S2-7-2: the tracked-secret scan fails closed when git cannot list files ---


def test_tracked_secret_scan_refuses_when_the_tracked_list_is_unavailable(tmp_path: Path) -> None:
    # tmp_path is outside any repository, so `git ls-files` cannot run there.
    warning = engine_module._scan_tracked_secrets(tmp_path)
    assert warning is not None
    assert warning.startswith("GÜVENLİK ENGELİ")


# --- S2-7-3: a stopped snapshot does not clear the engine without systemd ---


def _snapshot_config(snapshot: Path) -> SimpleNamespace:
    return SimpleNamespace(observability=SimpleNamespace(snapshot_path=str(snapshot), snapshot_interval_seconds=10))


def _write_fresh_stopped_snapshot(path: Path) -> None:
    path.write_text(
        json.dumps({"runtime_state": "stopped", "written_at": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )


def test_stopped_snapshot_is_trusted_only_when_systemd_confirms_inactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repair = _load_repair_script()
    snapshot = tmp_path / "snapshot.json"
    _write_fresh_stopped_snapshot(snapshot)
    config = _snapshot_config(snapshot)

    monkeypatch.setattr(repair, "_systemd_unit_state", lambda: "inactive")
    assert repair._engine_is_active(config) is False

    # No systemctl: the fresh 'stopped' record alone is not proof of a down engine.
    monkeypatch.setattr(repair, "_systemd_unit_state", lambda: None)
    assert repair._engine_is_active(config) is True


# --- S2-7-6: the tracked-file list covers the whole repository from a subdirectory ---


def test_tracked_repository_files_lists_the_top_level_from_a_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=30)
    (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    subprocess.run(["git", "-C", str(tmp_path), "add", ".env"], check=True, timeout=30)
    monkeypatch.chdir(tmp_path / "sub")

    tracked = cli._tracked_repository_files()
    assert ".env" in tracked


# --- S2-7-7: the CLI resolves credentials as the engine does ---


@pytest.fixture
def clean_credential_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CREDENTIALS_DIRECTORY", "ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_mode_credentials_reads_the_credential_file_like_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_credential_env: None
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ALPACA_API_KEY=file-key\nALPACA_SECRET_KEY=file-secret\n", encoding="utf-8")
    monkeypatch.setenv("ENV_PATH", str(env_file))
    assert live_safety.mode_credentials(live=False) == ("file-key", "file-secret")


def test_mode_credentials_refuses_when_process_and_file_disagree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_credential_env: None
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ALPACA_API_KEY=new-key\nALPACA_SECRET_KEY=new-secret\n", encoding="utf-8")
    monkeypatch.setenv("ENV_PATH", str(env_file))
    monkeypatch.setenv("ALPACA_API_KEY", "stale-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "new-secret")
    # The key differs between the two sources: the pair is refused, not guessed.
    assert live_safety.mode_credentials(live=False) == ("", "")


def test_mode_credentials_uses_the_process_environment_when_no_file_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_credential_env: None
) -> None:
    monkeypatch.setenv("ENV_PATH", str(tmp_path / "missing.env"))
    monkeypatch.setenv("ALPACA_API_KEY", "env-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "env-secret")
    assert live_safety.mode_credentials(live=False) == ("env-key", "env-secret")


def test_mode_credentials_with_an_explicit_mapping_ignores_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_credential_env: None
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ALPACA_API_KEY=file-key\nALPACA_SECRET_KEY=file-secret\n", encoding="utf-8")
    monkeypatch.setenv("ENV_PATH", str(env_file))
    assert live_safety.mode_credentials(live=False, env={}) == ("", "")
