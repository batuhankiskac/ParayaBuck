from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
        cwd=root,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
    )


@pytest.fixture()
def clean_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "research.py").write_text("VERSION = 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "baseline")
    return root


def test_dirty_tree_refuses_research_fingerprint(clean_repo: Path) -> None:
    import run_portfolio_research

    (clean_repo / "research.py").write_text("VERSION = 2\n", encoding="utf-8")
    (clean_repo / "new_module.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch_root(clean_repo)
    with pytest.raises(SystemExit, match="REDDEDİLDİ"):
        run_portfolio_research._source_fingerprint(allow_dirty=False)


def test_allow_dirty_records_tracked_and_untracked_hashes(clean_repo: Path) -> None:
    import run_portfolio_research

    (clean_repo / "research.py").write_text("VERSION = 2\n", encoding="utf-8")
    (clean_repo / "new_module.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch_root(clean_repo)
    fingerprint = run_portfolio_research._source_fingerprint(allow_dirty=True)
    assert fingerprint["dirty"] == "true"
    assert fingerprint["tracked_diff_hash"]
    assert fingerprint["untracked_source_hash"]
    assert fingerprint["code_revision"].startswith(fingerprint["revision"])
    assert "-dirty(" in fingerprint["code_revision"]


def test_clean_tree_fingerprint_has_no_dirty_state(clean_repo: Path) -> None:
    import run_portfolio_research

    monkeypatch_root(clean_repo)
    fingerprint = run_portfolio_research._source_fingerprint()
    assert fingerprint["dirty"] == "false"
    assert fingerprint["tracked_diff_hash"] == ""
    assert fingerprint["untracked_source_hash"] == ""
    assert fingerprint["code_revision"] == fingerprint["revision"]


def monkeypatch_root(root: Path) -> None:
    import run_portfolio_research

    run_portfolio_research.ROOT = root


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
