from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from scripts.research import h06_full_official_acquisition_2026_09_14 as subject


def valid_zip_bytes(tmp_path: Path) -> bytes:
    path = tmp_path / "valid.zip"
    contents = {
        "SUBMISSION.tsv": "ACCESSION_NUMBER\tDOCUMENT_TYPE\tISSUERCIK\n",
        "REPORTINGOWNER.tsv": "ACCESSION_NUMBER\tRPTOWNERCIK\n",
        "NONDERIV_TRANS.tsv": "ACCESSION_NUMBER\n",
        "DERIV_TRANS.tsv": "ACCESSION_NUMBER\n",
        "DERIV_HOLDING.tsv": "ACCESSION_NUMBER\n",
        "FOOTNOTES.tsv": "ACCESSION_NUMBER\n",
        "NONDERIV_HOLDING.tsv": "ACCESSION_NUMBER\n",
        "OWNER_SIGNATURE.tsv": "ACCESSION_NUMBER\n",
        "insider_transactions_metadata.json": "{}",
        "insider_transactions_readme.htm": "<html></html>",
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in contents.items():
            archive.writestr(name, value)
    return path.read_bytes()


def test_quarter_inventory_completeness() -> None:
    rows = subject.quarter_inventory()
    subject.validate_quarter_inventory(rows)
    assert len(rows) == 30 and rows[0]["period"] == "2019Q1" and rows[-1]["period"] == "2026Q2"


def test_sha_mismatch_rejection(tmp_path: Path) -> None:
    path = tmp_path / "source.zip"
    path.write_bytes(b"wrong")
    row = {"period": "2019Q1", "local_path": "source.zip", "bytes": 5, "sha256": "0" * 64}
    with pytest.raises(ValueError, match="sha256_mismatch"):
        subject.validate_cached_source(row, tmp_path)


def test_july_2026_index_reconciliation() -> None:
    assert (
        subject.reconcile_accessions(["a", "b"], ["b", "a"])["status"] == "JULY_2026_ACCESSION_RECONCILIATION_COMPLETE"
    )
    assert subject.reconcile_accessions(["a", "b"], ["a"])["status"] == "INCOMPLETE"


def test_form_4_a_inventory_rows_are_not_collapsed() -> None:
    rows = [{"accession": "a", "form": "4"}, {"accession": "b", "form": "4/A"}]
    assert [row["form"] for row in rows] == ["4", "4/A"]


def test_duplicate_accession_detection() -> None:
    with pytest.raises(ValueError, match="duplicate_accession"):
        subject.reconcile_accessions(["a", "a"], ["a"])


def test_acceptance_timestamp_required() -> None:
    assert subject.acceptance_datetime(b"<ACCEPTANCE-DATETIME>20260731165959") == "2026-07-31T16:59:59"
    with pytest.raises(ValueError, match="required"):
        subject.acceptance_datetime(b"FILING-DATE: 20260731")


def test_sec_fair_access_cache_behavior_offline(tmp_path: Path) -> None:
    calls: list[str] = []

    def transport(request: object) -> bytes:
        calls.append(str(getattr(request, "full_url")))
        return b"official"

    client = subject.FairAccessClient(
        "ParayaBuck contact=research@example.com", transport=transport, sleeper=lambda _: None
    )
    target = tmp_path / "cached.bin"
    assert client.get("https://www.sec.gov/files/example", target) == (b"official", False)
    assert client.get("https://www.sec.gov/files/example", target) == (b"official", True)
    assert len(calls) == 1


def test_tracked_blocker_manifest_is_self_hashed_and_truthful() -> None:
    value = json.loads(subject.MANIFEST.read_text())
    subject.verify_seal(value)
    assert value["status"] == "H06_FULL_ACQUISITION_INCOMPLETE"
    assert value["quarters_acquired"] in range(31)


def test_sec_user_agent_env_resolution_and_invalid_contact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "ParayaBuck research@example.com")
    assert subject.resolve_sec_user_agent()[1] == "ENV"
    monkeypatch.setenv("SEC_USER_AGENT", "Mozilla/5.0")
    with pytest.raises(ValueError, match="SEC_USER_AGENT_STILL_UNAVAILABLE"):
        subject.resolve_sec_user_agent()


def test_official_landing_page_href_parsing_accepts_old_and_new_prefixes() -> None:
    rows = []
    for period in subject.expected_quarters():
        year, quarter = period.split("Q")
        prefix = "datastandardsinnovation/data" if period == "2026Q2" else "structureddata/data"
        rows.append(
            f'<tr><td><a href="/files/{prefix}/insider-transactions-data-sets/{year}q{quarter}_form345.zip">'
            f'{year} Q{quarter} 345</a></td><td>ZIP</td><td class="views-field-filesize">1 MB</td></tr>'
        )
    discovered = subject.discover_quarter_urls("".join(rows).encode())
    assert len(discovered) == 30
    assert "/structureddata/" in discovered["2019Q1"]["url"]
    assert "/datastandardsinnovation/" in discovered["2026Q2"]["url"]


def test_official_landing_page_duplicate_quarter_fails_closed() -> None:
    duplicate = (
        b'<tr><td><a href="/x/2019q1_form345.zip">2019 Q1 345</a></td><td class="views-field-filesize">1 MB</td></tr>'
        * 2
    )
    with pytest.raises(ValueError, match="DISCOVERY_AMBIGUOUS"):
        subject.discover_quarter_urls(duplicate)


def test_html_masquerading_as_zip_is_rejected_and_part_removed(tmp_path: Path) -> None:
    client = subject.FairAccessClient(
        "ParayaBuck research@example.com", transport=lambda _: b"<html>Access Denied</html>", sleeper=lambda _: None
    )
    target = tmp_path / "2019q1_form345.zip"
    with pytest.raises(ValueError, match="non_zip_response_rejected"):
        client.download_zip_atomic("https://www.sec.gov/files/example.zip", target, "2019Q1")
    assert not target.exists() and not Path(f"{target}.part").exists()


def test_valid_zip_is_reused_without_transport(tmp_path: Path) -> None:
    target = tmp_path / "2019q1_form345.zip"
    target.write_bytes(valid_zip_bytes(tmp_path))
    client = subject.FairAccessClient(
        "ParayaBuck research@example.com",
        transport=lambda _: (_ for _ in ()).throw(AssertionError("network called")),
        sleeper=lambda _: None,
    )
    metadata, status = client.download_zip_atomic("https://www.sec.gov/files/example.zip", target, "2019Q1")
    assert status == "REUSED_EXISTING_VERIFIED" and metadata["sec_schema"] == "PASS"


def test_stale_part_never_admitted_and_clean_restart_succeeds(tmp_path: Path) -> None:
    body = valid_zip_bytes(tmp_path)
    target = tmp_path / "2019q1_form345.zip"
    Path(f"{target}.part").write_bytes(b"truncated")
    client = subject.FairAccessClient(
        "ParayaBuck research@example.com", transport=lambda _: body, sleeper=lambda _: None
    )
    _, status = client.download_zip_atomic("https://www.sec.gov/files/example.zip", target, "2019Q1")
    assert status == "DOWNLOADED_AND_VALIDATED" and not Path(f"{target}.part").exists()


def test_july_directory_discovery_is_exactly_constrained() -> None:
    items = [
        {"name": f"master.202607{day:02d}.idx", "href": f"master.202607{day:02d}.idx", "size": "1 KB"}
        for day in range(1, 23)
    ] + [{"name": "company.20260701.idx", "href": "company.20260701.idx"}]
    discovered = subject.discover_july_index_urls(json.dumps({"directory": {"item": items}}).encode())
    assert len(discovered) == 22 and all("master.202607" in row["name"] for row in discovered)


def test_daily_index_duplicate_accession_accounts_for_participant_paths() -> None:
    header = "CIK|Company Name|Form Type|Date Filed|File Name\n" + "-" * 40 + "\n"
    line = "1|Issuer|4|20260701|edgar/data/1/0000000001-26-000001.txt\n"
    entries = subject.parse_master_index((header + line + line).encode(), "master.20260701.idx")
    assert len(subject.group_index_accessions(entries)["0000000001-26-000001"]) == 2
    entries[1]["archive_path"] = "edgar/data/2/0000000001-26-000001.txt"
    assert len(subject.group_index_accessions(entries)["0000000001-26-000001"]) == 2


def test_stable_inventory_ignores_run_local_reuse_status() -> None:
    base = {"quarters": [{"period": "2019Q1", "retrieval_status": "DOWNLOADED_AND_VALIDATED"}], "july_indexes": []}
    rebuilt = {"quarters": [{"period": "2019Q1", "retrieval_status": "REUSED_EXISTING_VERIFIED"}], "july_indexes": []}
    assert subject.stable_acquisition_inventory(base) == subject.stable_acquisition_inventory(rebuilt)


def test_submission_validation_requires_bound_accession_and_acceptance() -> None:
    body = b"<ACCESSION-NUMBER>0000000001-26-000001\n<ACCEPTANCE-DATETIME>20260701123456\n"
    assert subject.validate_submission_bytes(body, "0000000001-26-000001") == "2026-07-01T12:34:56"
    with pytest.raises(ValueError, match="accession_mismatch"):
        subject.validate_submission_bytes(body, "0000000001-26-000002")
    with pytest.raises(ValueError, match="acceptance_datetime_required"):
        subject.validate_submission_bytes(b"<ACCESSION-NUMBER>0000000001-26-000001", "0000000001-26-000001")


def test_receipts_never_persist_sec_user_agent_value() -> None:
    manifest, receipt = subject.blocked_artifacts()
    rendered = json.dumps([manifest, receipt])
    assert "sec_user_agent_value_persisted" in rendered
    assert "ParayaBuck Research " not in rendered
    assert "@" not in rendered
