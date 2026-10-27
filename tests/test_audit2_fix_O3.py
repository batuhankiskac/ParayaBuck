"""Audit wave 2 fixes O3: systemd units and drop-ins, the doctor lock/env checks, the web bind, CI and Docker.

Each test pins one invariant from docs/audit/raw-r7.md (sections S2-3, S2-10, D2-7).
Everything reads repository files or tmp_path; nothing runs systemd, pip or the network.
"""

from __future__ import annotations

import importlib.metadata
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from parayabuck import cli
from parayabuck.web import app as web_app

REPO = Path(__file__).resolve().parents[1]
UNITS = REPO / "deploy" / "systemd"
VM_DROPIN = UNITS / "vm-dropin"
ENGINE = UNITS / "parayabuck.service"
BACKUP = UNITS / "parayabuck-backup.service"
DEADMAN = UNITS / "parayabuck-deadman.service"

HARDENING = (
    "LimitCORE=0",
    "ProtectClock=true",
    "ProtectHostname=true",
    "RestrictRealtime=true",
    "KeyringMode=private",
    "RemoveIPC=true",
)


def _directives(path: Path, section: str | None = None) -> list[str]:
    """Non-comment, non-empty lines of ``path``, optionally only those in ``[section]``."""
    current: str | None = None
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            current = line.strip("[]")
            continue
        if section is None or current == section:
            out.append(line)
    return out


# --- D2-7-3: the trading-host designation is a drop-in, not template text ---------------


def test_template_unit_has_no_inline_trading_host_designation() -> None:
    # Comments may name the variable; the invariant is that no directive sets it.
    assert not [line for line in _directives(ENGINE) if "PARAYABUCK_TRADING_HOST" in line]


def test_trading_host_drop_in_sets_the_designation_under_service() -> None:
    drop_in = VM_DROPIN / "trading-host.conf"
    assert _directives(drop_in, "Service") == ["Environment=PARAYABUCK_TRADING_HOST=1"]


def test_template_keeps_the_fresh_install_paths() -> None:
    service = _directives(ENGINE, "Service")
    assert "WorkingDirectory=/opt/parayabuck" in service
    assert "User=parayabuck" in service
    assert "EnvironmentFile=/opt/parayabuck/.env" in service


# --- D2-7-1: the VM overrides live in a drop-in that resets list keys before re-adding -------


def test_vm_paths_drop_in_replaces_the_template_layout_for_the_home_dir() -> None:
    service = _directives(VM_DROPIN / "vm-paths.conf", "Service")
    assert "User=batuhankiskac" in service
    assert "WorkingDirectory=/home/batuhankiskac/ParayaBuck" in service
    assert "ProtectHome=read-only" in service
    # EnvironmentFile=, ExecStart= and ExecStartPre= are list keys: an empty assignment
    # first, or the template value would stay and the unit would have two of them.
    for key in ("EnvironmentFile", "ExecStart", "ExecStartPre", "ReadWritePaths"):
        index = [i for i, line in enumerate(service) if line.startswith(f"{key}=")]
        assert index, key
        assert service[index[0]] == f"{key}=", f"{key} must be reset before it is re-added"
    # No fresh-install path leaks into the VM drop-in.
    assert not [line for line in service if "/opt/parayabuck" in line]


def test_vm_paths_drop_in_keeps_the_preflight_and_the_backup_read_only_rule() -> None:
    service = _directives(VM_DROPIN / "vm-paths.conf", "Service")
    assert (
        "ExecStartPre=/home/batuhankiskac/ParayaBuck/.venv/bin/parayabuck doctor --strict --configured-mode --offline"
        in service
    )
    assert not [line for line in service if line.startswith("ReadOnlyPaths")]


# --- D2-7-2: the watchdog state is documented as a drop-in -----------------------------


def test_watchdog_drop_in_enables_notify_and_the_300_second_watchdog() -> None:
    service = _directives(VM_DROPIN / "watchdog.conf", "Service")
    assert service == ["Type=notify", "NotifyAccess=main", "WatchdogSec=300"]


def test_template_engine_unit_keeps_notify_and_the_watchdog() -> None:
    service = _directives(ENGINE, "Service")
    assert "Type=notify" in service
    assert "WatchdogSec=300" in service
    assert "NotifyAccess=main" in service


# --- S2-10-3 / S2-10-5 / S2-10-4: hardening set, backups read-only, MDWE not enabled --


@pytest.mark.parametrize("unit", [ENGINE, BACKUP, DEADMAN], ids=["engine", "backup", "deadman"])
def test_every_unit_carries_the_hardening_set(unit: Path) -> None:
    service = _directives(unit, "Service")
    for directive in HARDENING:
        assert directive in service, f"{unit.name} lacks {directive}"


def test_hardening_drop_in_matches_the_same_set() -> None:
    assert _directives(VM_DROPIN / "hardening.conf", "Service") == list(HARDENING)


def test_engine_keeps_the_backup_path_writable_for_the_doctor_probe() -> None:
    # S2-10-5 is blocked: ExecStartPre doctor writes a probe file into data/backups
    # ("backup path writable" is a blocker), and systemd applies ReadOnlyPaths to
    # ExecStartPre too. The read-only line may only come back together with a
    # read-only probe in cli.py.
    service = _directives(ENGINE, "Service")
    assert not [line for line in service if line.startswith("ReadOnlyPaths")]
    assert "ReadWritePaths=/opt/parayabuck/config /opt/parayabuck/data -/opt/parayabuck/reports" in service
    cli_text = (REPO / "src" / "parayabuck" / "cli.py").read_text(encoding="utf-8")
    assert '("backup path writable", config.backup.path)' in cli_text


def test_memory_deny_write_execute_is_not_enabled_anywhere() -> None:
    for path in [ENGINE, BACKUP, DEADMAN, *sorted(VM_DROPIN.glob("*.conf"))]:
        for line in _directives(path):
            assert not line.startswith("MemoryDenyWriteExecute"), f"{path.name}: {line}"


# --- S2-10-2: EnvironmentFile= must not carry the unit-only keys -----------------------


def test_env_file_scan_names_unit_only_keys_and_never_their_values(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "ALPACA_API_KEY=PKsecretvalue\n"
        "HOST=0.0.0.0\n"
        "export PARAYABUCK_CONTAINER=YES\n"
        "PARAYABUCK_TRADING_HOST=0\n"
        "PARAYABUCK_TRADING_HOST=1\n",
        encoding="utf-8",
    )
    found = cli._unit_only_keys_in_env_file(env)
    assert found == ["HOST", "PARAYABUCK_CONTAINER", "PARAYABUCK_TRADING_HOST"]
    assert "PKsecretvalue" not in " ".join(found)
    assert "0.0.0.0" not in " ".join(found)


def test_env_file_scan_passes_a_clean_file_and_a_missing_one(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("ALPACA_API_KEY=PKx\nPARAYABUCK_ENABLE_LIVE=NO\n", encoding="utf-8")
    assert cli._unit_only_keys_in_env_file(env) == []
    assert cli._unit_only_keys_in_env_file(tmp_path / "absent.env") == []


def test_web_bind_refuses_the_container_wildcard_under_systemd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PARAYABUCK_TRADING_HOST", raising=False)
    monkeypatch.setenv("PARAYABUCK_CONTAINER", "YES")
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("INVOCATION_ID", "0123456789abcdef")  # set by systemd for every unit
    monkeypatch.setattr(web_app, "setup_logging", lambda level: None)
    monkeypatch.setattr(web_app, "_probe_web_bind", lambda host, port: pytest.fail("must refuse before binding"))
    with pytest.raises(ValueError, match="systemd-managed"):
        web_app.main()


def test_web_bind_keeps_the_container_wildcard_outside_systemd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PARAYABUCK_TRADING_HOST", raising=False)
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.setenv("PARAYABUCK_CONTAINER", "YES")
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setattr(web_app, "setup_logging", lambda level: None)

    class _Probe(Exception):
        pass

    def probe(host: str, port: int) -> None:
        raise _Probe(host)

    monkeypatch.setattr(web_app, "_probe_web_bind", probe)
    # The container refusal must not fire: the bind reaches the probe.
    with pytest.raises(_Probe):
        web_app.main()


# --- S2-3-2 / S2-3-6: the marker check and the unlocked-package WARN are stdlib-only ----


def test_marker_check_works_without_packaging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "packaging", None)
    monkeypatch.setitem(sys.modules, "packaging.markers", None)
    assert cli._marker_applies("sys_platform == 'win32'") is (sys.platform == "win32")
    assert cli._marker_applies("sys_platform != 'no-such-platform'") is True
    assert cli._marker_applies("sys_platform == 'no-such-platform'") is False
    # Markers the stdlib check does not model apply, so the pin is still checked.
    assert cli._marker_applies("python_version >= '3.11'") is True
    assert cli._marker_applies("sys_platform == 'win32' or os_name == 'nt'") is True


def test_unlocked_installed_distributions_skip_locked_and_tooling(tmp_path: Path) -> None:
    installed = importlib.metadata.version("pyyaml")
    lock = tmp_path / "requirements.lock"
    lock.write_text(
        f"PyYAML=={installed} \\\n    --hash=sha256:00\n",
        encoding="utf-8",
    )
    unlocked = cli._unlocked_installed_distributions(lock)
    assert "pyyaml" not in unlocked
    assert not {"pip", "setuptools", "wheel", "parayabuck"} & set(unlocked)
    # pytest runs this test and is not in the one-line lock above.
    assert "pytest" in unlocked


# --- S2-3-5 / S2-3-9 / S2-3-4: CI and Docker -------------------------------------------


def _ci() -> dict[str, Any]:
    return yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))


def test_quality_checkout_does_not_persist_the_token() -> None:
    steps = _ci()["jobs"]["quality"]["steps"]
    checkouts = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout")]
    assert checkouts
    for step in checkouts:
        assert step.get("with", {}).get("persist-credentials") is False


def test_ci_tooling_installs_are_version_pinned() -> None:
    text = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for line in text.splitlines():
        match = re.search(r"pip install (.*)$", line)
        if not match:
            continue
        for token in match.group(1).split():
            if token.startswith("-") or token in {".", "dist/*.whl"} or token.endswith(".lock"):
                continue
            assert "==" in token, f"unpinned CI install: {token!r} in {line.strip()!r}"


def test_docker_source_tree_stays_root_owned() -> None:
    text = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"chown -R \S+ /app(\s|$)", text) is None
    assert "chown -R parayabuck:parayabuck /app/config /app/data /app/reports" in text
    assert "pip install pip==26.2.1" in text
