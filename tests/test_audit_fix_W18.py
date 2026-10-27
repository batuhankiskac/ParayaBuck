"""Deployment and supply-chain invariants from the 2026-10-27 audit (W18).

Reads only the checked-in deploy, CI and dependency files. No network, no
credentials, no production paths.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_PIN_LINE = re.compile(r"^[A-Za-z0-9_.-]+==\S+")


def _lines(relative: str) -> list[str]:
    return (REPO / relative).read_text(encoding="utf-8").splitlines()


def _section(text: str, name: str) -> str:
    """Return the body of one ``[Name]`` section of a systemd unit file."""
    body: list[str] = []
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            inside = stripped == f"[{name}]"
            continue
        if inside:
            body.append(line)
    return "\n".join(body)


def test_runtime_and_dev_locks_are_hash_pinned() -> None:
    # S7-1: every pinned requirement carries sha256 hashes on the continuation lines.
    for relative in ("requirements.lock", "requirements-dev.lock"):
        lines = _lines(relative)
        pins = [i for i, line in enumerate(lines) if _PIN_LINE.match(line)]
        assert pins, f"{relative} has no pins"
        for index in pins:
            assert index + 1 < len(lines), f"{relative}: pin at the end of file has no hash"
            assert lines[index + 1].lstrip().startswith("--hash=sha256:"), (
                f"{relative}: {lines[index]!r} is not hash-pinned"
            )


def test_ci_and_docs_export_hashed_locks_only() -> None:
    # The CI freshness check must regenerate exactly what the repo ships.
    ci = (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    for text in (ci, readme):
        assert "--no-hashes" not in text
    assert "uv export --locked --no-emit-project --no-dev --format requirements.txt -o requirements.lock" in ci
    assert "pip install --require-hashes -r requirements-dev.lock" in ci
    assert "pip install --require-hashes -r requirements.lock" in (REPO / "Dockerfile").read_text(encoding="utf-8")


def test_build_backend_is_pinned_exactly() -> None:
    # S7-5: the build environment is fetched at build time and must not drift.
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    requires = data["build-system"]["requires"]
    assert requires, "build-system.requires is empty"
    for requirement in requires:
        assert re.fullmatch(r"[A-Za-z0-9_.-]+==[0-9][^,\s]*", requirement), (
            f"build requirement is not an exact pin: {requirement!r}"
        )


def test_engine_unit_has_start_limit_and_tolerant_reports_path() -> None:
    # R7-5 and R8-1: bounded restart loop; a missing reports/ must not break namespace setup.
    unit = (REPO / "deploy/systemd/parayabuck.service").read_text(encoding="utf-8")
    unit_section = _section(unit, "Unit")
    assert "StartLimitIntervalSec=600" in unit_section
    assert "StartLimitBurst=5" in unit_section
    service_section = _section(unit, "Service")
    assert "ReadWritePaths=" in service_section
    assert (
        " -/opt/parayabuck/reports" in service_section or "ReadWritePaths=-/opt/parayabuck/reports" in service_section
    )


def test_backup_unit_reads_only_the_narrow_env_file() -> None:
    # S-2: the backup and its off-host child must not inherit the trading secrets.
    unit = (REPO / "deploy/systemd/parayabuck-backup.service").read_text(encoding="utf-8")
    service_section = _section(unit, "Service")
    env_files = [line.split("=", 1)[1] for line in service_section.splitlines() if line.startswith("EnvironmentFile=")]
    assert env_files == ["/opt/parayabuck/.env.backup"]
    assert "Environment=ENV_PATH=/opt/parayabuck/.env.backup" in service_section
