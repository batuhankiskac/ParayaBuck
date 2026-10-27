"""Audit wave 2 runbook fixes (worker O2): D2-5-1, D2-5-2, D2-5-4 and the restore follow-up.

Each test pins one statement from docs/audit/raw-r7.md (section D2-5) or from the
restore follow-up: the restore must install the peak sidecars and the halt copy from
the same backup set, and must list symlinks first. The checks read repository docs and
the deadman module constants only; nothing runs systemd, pip or the network.
"""

from __future__ import annotations

import re
from pathlib import Path

from parayabuck.monitoring import deadman

REPO = Path(__file__).resolve().parents[1]
DEPLOYMENT = REPO / "docs" / "deployment.md"
OPERATIONS = REPO / "docs" / "operations.md"
OPERATOR_RUNBOOK = REPO / "docs" / "operator_runbook.md"


def _section(text: str, heading: str) -> str:
    """The text under one markdown heading, up to the next heading of the same or higher level.

    Lines inside a fenced code block are never headings, so bash comments do not end a section.
    """
    lines = text[text.index(heading) :].splitlines(keepends=True)
    level = len(heading) - len(heading.lstrip("#"))
    collected: list[str] = []
    in_fence = False
    for index, line in enumerate(lines):
        if index and not in_fence and re.match(rf"#{{1,{level}}} ", line):
            break
        if line.startswith("```"):
            in_fence = not in_fence
        collected.append(line)
    return "".join(collected)


def test_restore_block_lists_symlinks_and_installs_one_set() -> None:
    text = OPERATIONS.read_text(encoding="utf-8")
    restore = _section(text, "## State, backup and restore")
    assert "find config data -type l" in restore
    assert restore.index("find config data -type l") < restore.index(
        'sudo install -o "$PB_USER" -g "$PB_USER" -m 600 data/backups/<file>'
    )
    assert "same backup set" in restore
    assert "data/backups/<stem>.paper_equity_peak.json" in restore
    assert "data/backups/<stem>.OPERATOR_HALT" in restore


def test_operator_runbook_database_restore_names_sidecars_halt_and_symlinks() -> None:
    text = OPERATOR_RUNBOOK.read_text(encoding="utf-8")
    database = _section(text, "## Database issue")
    assert "paper_equity_peak.json" in database
    assert "live_equity_peak.json" in database
    assert "<stem>.OPERATOR_HALT" in database
    assert "find config data -type l" in database


def test_dead_man_section_states_the_success_exceptions_the_code_implements() -> None:
    section = _section(DEPLOYMENT.read_text(encoding="utf-8"), "### Dead-man switch")
    for state in sorted(deadman._NORMAL_NOT_READY_STATES):
        assert f"engine_state={state}" in section
    for code in sorted(deadman._DOCUMENTED_HOLD_CODES):
        assert code in section
    assert "cannot see them" in section


def test_key_install_names_the_service_owner() -> None:
    section = _section(DEPLOYMENT.read_text(encoding="utf-8"), "### Changing API keys on the VM")
    assert 'sudo install -m 0600 -o "$PB_USER" -g "$PB_USER"' in section
    assert "sudo install -m 0600 …" not in section
