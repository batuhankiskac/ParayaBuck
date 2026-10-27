"""Regression tests for the test-hygiene audit fixes (worker W21).

Covers the PAPER account-environment check on a PA account with an AK key,
and the production-state guard's classification of the directories it protects.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from parayabuck.live_safety import (
    ACCOUNT_ENVIRONMENT_UNKNOWN,
    LiveApprovalError,
    classify_account_environment,
    verify_account_environment,
)

from .conftest import _REPO_ROOT, protected_state_path


def test_paper_with_a_pa_account_and_an_ak_key_is_not_refused() -> None:
    """A PA account is paper evidence; an AK key id alone is not enough to refuse PAPER."""
    assert classify_account_environment("PA3XYZ", "AKTEST") == ACCOUNT_ENVIRONMENT_UNKNOWN
    verify_account_environment(live=False, account_number="PA3XYZ", api_key="AKTEST")


def test_paper_with_an_ak_key_and_no_account_number_is_refused() -> None:
    """Without a PA account number the AK key is unambiguous live evidence."""
    with pytest.raises(LiveApprovalError, match="PAPER start refused"):
        verify_account_environment(live=False, account_number="", api_key="AKTEST")


def test_paper_with_a_pk_key_and_a_pa_account_passes() -> None:
    verify_account_environment(live=False, account_number="PA3XYZ", api_key="PKTEST")


def test_the_protected_directories_include_backups_and_research_runs() -> None:
    for name in ("data/backups/parayabuck-2026-10-01.sqlite3", "data/research_runs/2026-10-01/summary.json"):
        assert protected_state_path(_REPO_ROOT / name) is not None, name


def test_credentials_directory_is_stripped_for_the_test_session() -> None:
    """systemd's credential directory must never reach a test (see conftest)."""
    assert "CREDENTIALS_DIRECTORY" not in os.environ


def test_shipped_config_is_readable_but_not_writable_by_tests() -> None:
    from .conftest import _is_writing_flags, _is_writing_mode, _protected_config_path

    assert _is_writing_mode("r") is False
    assert _is_writing_mode("rb") is False
    assert _is_writing_mode("w") is True
    assert _is_writing_mode("a+b") is True
    assert _is_writing_flags(os.O_RDONLY) is False
    assert _is_writing_flags(os.O_WRONLY | os.O_CREAT) is True
    shipped = _REPO_ROOT / "config" / "settings.yaml"
    assert _protected_config_path(shipped) == shipped.resolve()
    assert _protected_config_path(Path("/tmp/not-config/settings.yaml")) is None
