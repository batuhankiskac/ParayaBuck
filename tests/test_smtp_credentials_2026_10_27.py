"""SMTP login comes from a systemd credential file first, then the env var."""

from __future__ import annotations

from pathlib import Path

from parayabuck.monitoring.email_alerts import SMTP_PASSWORD_ENV, SMTP_USERNAME_ENV, _smtp_credentials


def test_credential_directory_wins_over_environment(tmp_path: Path) -> None:
    (tmp_path / SMTP_USERNAME_ENV).write_text("file-user\n", encoding="utf-8")
    (tmp_path / SMTP_PASSWORD_ENV).write_text("file-pass\n", encoding="utf-8")
    env = {
        "CREDENTIALS_DIRECTORY": str(tmp_path),
        SMTP_USERNAME_ENV: "env-user",
        SMTP_PASSWORD_ENV: "env-pass",
    }
    assert _smtp_credentials(env) == ("file-user", "file-pass")


def test_missing_credential_file_falls_back_to_environment(tmp_path: Path) -> None:
    (tmp_path / SMTP_USERNAME_ENV).write_text("file-user", encoding="utf-8")
    env = {
        "CREDENTIALS_DIRECTORY": str(tmp_path),
        SMTP_USERNAME_ENV: "env-user",
        SMTP_PASSWORD_ENV: "env-pass",
    }
    assert _smtp_credentials(env) == ("file-user", "env-pass")


def test_environment_only_is_unchanged() -> None:
    env = {SMTP_USERNAME_ENV: "env-user", SMTP_PASSWORD_ENV: "env-pass"}
    assert _smtp_credentials(env) == ("env-user", "env-pass")
    assert _smtp_credentials({}) == ("", "")
