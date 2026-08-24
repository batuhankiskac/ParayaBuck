"""Read-only contract tests for the V3 PAPER soak auditor."""

from __future__ import annotations

from pathlib import Path

import scripts.audit_v3_paper_soak as audit
from parayabuck.portfolio.runtime_service import PortfolioStateRepository


def test_v3_paper_audit_reports_no_data_without_writing(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.sqlite3"
    repository = PortfolioStateRepository(db_path)
    repository.close()
    before = db_path.read_bytes()

    report = audit._audit_database(db_path, None)

    assert report["read_only"] is True
    assert report["query_only"] is True
    assert report["observations"]["status"] == "no_data"
    assert db_path.read_bytes() == before
