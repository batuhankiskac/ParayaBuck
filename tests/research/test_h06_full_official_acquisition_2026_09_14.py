from __future__ import annotations

import gzip
import json
import tarfile
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
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


def valid_submission(
    accession: str,
    *,
    form: str = "4",
    accepted: str = "20260701123456",
    filed: str = "20260701",
) -> bytes:
    return (
        f"<ACCESSION-NUMBER>{accession}\n"
        f"<CONFORMED-SUBMISSION-TYPE>{form}\n"
        f"<ACCEPTANCE-DATETIME>{accepted}\n"
        f"<FILED-AS-OF-DATE>{filed}\n"
    ).encode()


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


def test_full_11242_accession_set_reconciles_by_accession() -> None:
    values = [f"{index:010d}-26-{index % 1_000_000:06d}" for index in range(11_242)]
    result = subject.reconcile_accessions(values, reversed(values))
    assert result["status"] == "JULY_2026_ACCESSION_RECONCILIATION_COMPLETE"
    assert result["index_count"] == result["archive_count"] == 11_242


def test_bounded_resume_skips_435_existing_cache_entries(tmp_path: Path) -> None:
    accessions = [f"{index:010d}-26-{index:06d}" for index in range(437)]
    for accession in accessions[:435]:
        (tmp_path / f"{accession}.txt").write_bytes(b"cached")
    assert subject.bounded_unresolved_accessions(
        accessions, tmp_path, max_new=1, start_after=None
    ) == [accessions[435]]


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


def test_tracked_source_complete_manifest_is_self_hashed_and_truthful() -> None:
    value = json.loads(subject.MANIFEST.read_text())
    subject.verify_seal(value)
    assert value["status"] == "H06_OFFICIAL_SOURCE_COVERAGE_COMPLETE"
    assert value["quarters_acquired"] == 30
    assert value["remaining_blockers"] == []
    acceptance = value["relevant_accession_acceptance_map"]
    assert acceptance["quarterly_target_count"] == 384_068
    assert acceptance["unresolved"] == acceptance["conflicts"] == 0


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
    entries[1]["cik"] = "2"
    audit = subject.audit_july_accession_groups(subject.group_index_accessions(entries))
    assert audit["status"] == "PASS"
    assert audit["participant_cik_multiplicity_groups"] == 1
    assert audit["path_multiplicity_groups"] == 1
    assert audit["basename_accession_conflicts"] == 0


@pytest.mark.parametrize(
    ("field", "value", "counter"),
    [
        ("form", "4/A", "form_conflicts"),
        ("filing_date", "20260702", "filing_date_conflicts"),
        ("archive_path", "edgar/data/2/0000000001-26-000002.txt", "basename_accession_conflicts"),
    ],
)
def test_july_group_audit_rejects_true_identity_conflicts(field: str, value: str, counter: str) -> None:
    base = {
        "accession": "0000000001-26-000001",
        "form": "4",
        "filing_date": "20260701",
        "archive_path": "edgar/data/1/0000000001-26-000001.txt",
        "cik": "1",
        "company_name_audit": "A",
    }
    changed = {**base, field: value, "cik": "2"}
    audit = subject.audit_july_accession_groups({base["accession"]: [base, changed]})
    assert audit["status"] == "JULY_INDEX_ACCESSION_GROUP_CONFLICT"
    assert audit[counter] == 1


def test_alias_canary_selection_is_deterministic() -> None:
    def rows(accession: str, count: int) -> list[dict[str, str]]:
        return [
            {
                "accession": accession,
                "form": "4",
                "filing_date": "20260701",
                "archive_path": f"edgar/data/{index}/{accession}.txt",
                "cik": str(index),
                "company_name_audit": str(index),
            }
            for index in range(count)
        ]

    groups = {
        "a": rows("a", 2),
        "b": rows("b", 2),
        "c": rows("c", 2),
        "d": rows("d", 3),
        "e": rows("e", 4),
        "f": rows("f", 11),
    }
    assert subject.deterministic_alias_canaries(groups) == ["a", "b", "c", "d", "e", "f"]


def test_alias_canary_requires_byte_identical_official_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    accession = "0000000001-26-000001"
    rows = [
        {
            "accession": accession,
            "form": "4",
            "filing_date": "20260701",
            "archive_path": f"edgar/data/{cik}/{accession}.txt",
            "cik": cik,
            "company_name_audit": cik,
        }
        for cik in ("1", "2")
    ]

    class Client:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def get(self, _: str, cache: Path, **__: object) -> tuple[bytes, bool]:
            body = valid_submission(accession)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(body)
            return body, False

    monkeypatch.setattr(subject, "REPO", tmp_path)
    monkeypatch.setattr(subject, "LOCAL_ROOT", tmp_path / "local")
    monkeypatch.setattr(subject, "JULY_ALIAS_CANARY_PATH", tmp_path / "receipt.json")
    monkeypatch.setattr(subject, "load_july_accession_groups", lambda: {accession: rows})
    monkeypatch.setattr(subject, "deterministic_alias_canaries", lambda _: [accession])
    monkeypatch.setattr(subject, "resolve_sec_user_agent", lambda: ("Research test@example.com", "ENV"))
    monkeypatch.setattr(subject, "FairAccessClient", Client)
    receipt = subject.prove_july_alias_semantics()
    assert receipt["status"] == "JULY_PARTICIPANT_PATH_SEMANTICS_PROVEN"
    assert receipt["canaries"][0]["result"] == "PARTICIPANT_ARCHIVE_ALIASES_BYTE_IDENTICAL"


def test_stable_inventory_ignores_run_local_reuse_status() -> None:
    base = {"quarters": [{"period": "2019Q1", "retrieval_status": "DOWNLOADED_AND_VALIDATED"}], "july_indexes": []}
    rebuilt = {"quarters": [{"period": "2019Q1", "retrieval_status": "REUSED_EXISTING_VERIFIED"}], "july_indexes": []}
    assert subject.stable_acquisition_inventory(base) == subject.stable_acquisition_inventory(rebuilt)


def test_submission_validation_requires_bound_accession_and_acceptance() -> None:
    body = valid_submission("0000000001-26-000001")
    assert subject.validate_submission_bytes(body, "0000000001-26-000001") == "2026-07-01T12:34:56"
    with pytest.raises(ValueError, match="accession_mismatch"):
        subject.validate_submission_bytes(body, "0000000001-26-000002")
    with pytest.raises(ValueError, match="acceptance_datetime_required"):
        subject.validate_submission_bytes(
            b"<ACCESSION-NUMBER>0000000001-26-000001\n<CONFORMED-SUBMISSION-TYPE>4",
            "0000000001-26-000001",
        )


def test_submission_download_tries_sorted_aliases_then_reuses_valid_cache(tmp_path: Path) -> None:
    accession = "0000000001-26-000001"
    calls: list[str] = []

    def transport(request: object) -> bytes:
        url = str(getattr(request, "full_url"))
        calls.append(url)
        return b"<html>Access Denied</html>" if url.endswith("/a.txt") else valid_submission(accession)

    target = tmp_path / f"{accession}.txt"
    client = subject.FairAccessClient("ParayaBuck research@example.com", transport=transport, sleeper=lambda _: None)
    metadata, status = client.download_submission_atomic(
        ["https://www.sec.gov/Archives/z.txt", "https://www.sec.gov/Archives/a.txt"],
        target,
        accession,
        expected_forms=["4"],
    )
    assert status == "DOWNLOADED_AND_VALIDATED"
    assert metadata["source_url"].endswith("/z.txt")
    assert calls == ["https://www.sec.gov/Archives/a.txt", "https://www.sec.gov/Archives/z.txt"]
    reuse = subject.FairAccessClient(
        "ParayaBuck research@example.com",
        transport=lambda _: (_ for _ in ()).throw(AssertionError("network called")),
        sleeper=lambda _: None,
    )
    assert reuse.download_submission_atomic([], target, accession, expected_forms=["4"])[1] == "REUSED_EXISTING_VERIFIED"
    assert not Path(f"{target}.part").exists()


def test_acceptance_timezone_normalization_and_form_validation() -> None:
    metadata = subject.submission_metadata(valid_submission("0000000001-26-000001"), "0000000001-26-000001")
    assert metadata["accepted_at_utc"] == "2026-07-01T16:34:56Z"
    assert subject.normalize_sec_acceptance("2024-11-19T23:31:42.000Z") == "2024-11-19T23:31:42Z"
    with pytest.raises(ValueError, match="form_mismatch"):
        subject.submission_metadata(valid_submission("0000000001-26-000001"), "0000000001-26-000001", ["4/A"])


def test_acceptance_metadata_duplicate_conflict_is_rejected() -> None:
    records: dict[str, dict[str, object]] = {}
    row = {
        "accessionNumber": "0000000001-26-000001",
        "acceptanceDateTime": "2026-07-01T16:34:56.000Z",
        "filingDate": "2026-07-01",
        "form": "4",
    }
    subject._merge_acceptance_record(records, row, cik="1", member="a.json", member_sha256="a", source_kind="bulk_recent")
    with pytest.raises(ValueError, match="duplicate_acceptance_metadata_conflict"):
        subject._merge_acceptance_record(
            records,
            {**row, "form": "4/A"},
            cik="2",
            member="b.json",
            member_sha256="b",
            source_kind="bulk_historical",
        )


def test_request_policy_is_source_of_truth_and_has_no_actual_rate_claim() -> None:
    assert subject.QUARTER_CONFIGURED_REQUEST_STARTS_PER_SECOND == 1
    assert subject.JULY_CONFIGURED_REQUEST_STARTS_PER_SECOND == 4
    assert subject.METADATA_CONFIGURED_REQUEST_STARTS_PER_SECOND == 1
    assert subject.DAILY_FEED_CONFIGURED_REQUEST_STARTS_PER_SECOND == 4
    assert subject.DAILY_FEED_ACQUISITION_CONCURRENCY == 4
    rendered = json.dumps(subject.blocked_artifacts())
    assert "actual_requests_per_second" not in rendered


def test_concurrent_client_uses_one_global_request_start_limiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [0.0]
    clock_lock = threading.Lock()
    starts: list[float] = []

    def monotonic() -> float:
        with clock_lock:
            return clock[0]

    def sleep(delay: float) -> None:
        with clock_lock:
            clock[0] += delay

    def transport(_: object) -> bytes:
        starts.append(monotonic())
        return b"ok"

    monkeypatch.setattr(subject.time, "monotonic", monotonic)
    client = subject.FairAccessClient(
        "ParayaBuck research@example.com", requests_per_second=4, transport=transport, sleeper=sleep
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(client.get, f"https://www.sec.gov/files/{index}", tmp_path / str(index))
            for index in range(4)
        ]
        for future in futures:
            future.result()
    assert len(starts) == 4
    assert all(right - left >= 0.25 for left, right in zip(sorted(starts), sorted(starts)[1:]))


def test_submissions_zip_integrity_and_schema(tmp_path: Path) -> None:
    path = tmp_path / "submissions.zip"
    payload = {
        "cik": "1",
        "filings": {
            "recent": {
                "accessionNumber": ["0000000001-26-000001"],
                "acceptanceDateTime": ["2026-07-01T16:34:56.000Z"],
                "filingDate": ["2026-07-01"],
                "form": ["4"],
            },
            "files": [{"name": "CIK0000000001-submissions-001.json"}],
        },
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("CIK0000000001.json", json.dumps(payload))
    result = subject.inspect_submissions_zip(path)
    assert result["zip_test"] == result["schema_status"] == "PASS"
    assert result["json_member_count"] == 1


def test_aligned_historical_submission_rows_and_reference_before_fallback() -> None:
    recent = {
        "filings": {
            "recent": {
                "accessionNumber": ["a"],
                "acceptanceDateTime": ["2026-01-01T12:00:00.000Z"],
                "filingDate": ["2026-01-01"],
                "form": ["4"],
            },
            "files": [{"name": "history.json"}],
        }
    }
    historical = {
        "accessionNumber": ["b"],
        "acceptanceDateTime": ["2025-01-01T12:00:00.000Z"],
        "filingDate": ["2025-01-01"],
        "form": ["4/A"],
    }
    assert [row["accessionNumber"] for row in subject._aligned_filing_rows(recent)] == ["a"]
    assert [row["accessionNumber"] for row in subject._aligned_filing_rows(historical)] == ["b"]
    assert recent["filings"]["files"][0]["name"] == "history.json"


def test_bulk_extraction_maps_recent_and_historical_before_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bulk = tmp_path / "submissions.zip"
    recent = {
        "cik": "1",
        "filings": {
            "recent": {
                "accessionNumber": ["a"],
                "acceptanceDateTime": ["2026-01-01T12:00:00.000Z"],
                "filingDate": ["2026-01-01"],
                "form": ["4"],
            },
            "files": [{"name": "CIK0000000001-submissions-001.json"}],
        },
    }
    history = {
        "accessionNumber": ["b"],
        "acceptanceDateTime": ["2025-01-01T12:00:00.000Z"],
        "filingDate": ["2025-01-01"],
        "form": ["4/A"],
    }
    with zipfile.ZipFile(bulk, "w") as archive:
        archive.writestr("CIK0000000001.json", json.dumps(recent))
        archive.writestr("CIK0000000001-submissions-001.json", json.dumps(history))
    monkeypatch.setattr(subject, "SUBMISSIONS_BULK_PATH", bulk)
    records, coverage = subject.extract_bulk_acceptance_records({"a", "b", "c"})
    assert set(records) == {"a", "b"}
    assert coverage["submissions_bulk_mapped"] == 1
    assert coverage["referenced_history_mapped"] == 1
    assert coverage["referenced_files_present_in_zip"] == 1
    assert "c" not in records


def test_complete_submission_header_fallback_requests_only_unresolved_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    covered = "0000000001-25-000001"
    missing = "0000000002-25-000002"
    calls: list[str] = []

    class Client:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def download_submission_atomic(
            self, urls: object, target: Path, accession: str, **__: object
        ) -> tuple[dict[str, str], str]:
            calls.append(accession)
            body = valid_submission(accession, accepted="20250102120000", filed="20250102")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            return subject.submission_metadata(body, accession), "DOWNLOADED_AND_VALIDATED"

    monkeypatch.setattr(subject, "LOCAL_ROOT", tmp_path)
    monkeypatch.setattr(subject, "REPO", tmp_path)
    monkeypatch.setattr(subject, "resolve_sec_user_agent", lambda: ("Research test@example.com", "ENV"))
    monkeypatch.setattr(subject, "FairAccessClient", Client)
    targets = {
        covered: {"form": "4", "filing_date": "2025-01-01", "issuer_cik": "1"},
        missing: {"form": "4", "filing_date": "2025-01-02", "issuer_cik": "2"},
    }
    records: dict[str, dict[str, object]] = {covered: {}}
    result = subject.acquire_header_fallbacks(targets, records)
    assert result == {"mapped": 1, "unavailable": [], "filing_date_reconciliations": []}
    assert calls == [missing]


def test_post_acceptance_correction_requires_acceptance_day_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    accession = "0001193125-25-212753"

    class Client:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        def download_submission_atomic(
            self, urls: object, target: Path, accession: str, **__: object
        ) -> tuple[dict[str, str], str]:
            body = valid_submission(accession, accepted="20250923122918", filed="20250108")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            return subject.submission_metadata(body, accession), "DOWNLOADED_AND_VALIDATED"

    monkeypatch.setattr(subject, "LOCAL_ROOT", tmp_path)
    monkeypatch.setattr(subject, "REPO", tmp_path)
    monkeypatch.setattr(subject, "resolve_sec_user_agent", lambda: ("Research test@example.com", "ENV"))
    monkeypatch.setattr(subject, "FairAccessClient", Client)
    records: dict[str, dict[str, object]] = {}
    result = subject.acquire_header_fallbacks(
        {accession: {"form": "4", "filing_date": "2025-09-23", "issuer_cik": "1030894"}},
        records,
    )
    assert result["mapped"] == 1
    assert result["filing_date_reconciliations"][0]["complete_submission_filed_as_of_date"] == (
        "2025-01-08"
    )
    assert records[accession]["filing_date"] == "2025-09-23"
    assert records[accession]["sources"][0]["source_kind"] == (
        "complete_submission_header_post_acceptance_correction"
    )


def test_bulk_acceptance_canary_requires_exact_normalized_instant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    accession = "0000000001-26-000001"
    path = tmp_path / "submission.txt"
    path.write_bytes(valid_submission(accession))
    monkeypatch.setattr(subject, "_canary_submission_paths", lambda: [("TEST", path)])
    records = {
        accession: {
            "form": "4",
            "filing_date": "2026-07-01",
            "accepted_at_utc": "2026-07-01T16:34:56Z",
            "sources": [],
        }
    }
    assert subject.validate_bulk_acceptance_canaries(records)["status"] == (
        "SUBMISSIONS_BULK_ACCEPTANCE_SEMANTICS_VALIDATED"
    )
    records[accession]["accepted_at_utc"] = "2026-07-01T16:34:57Z"
    with pytest.raises(ValueError, match="SUBMISSIONS_BULK_ACCEPTANCE_SEMANTICS_REJECTED"):
        subject.validate_bulk_acceptance_canaries(records)


def test_offline_complete_rebuild_requires_exact_stable_hash_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subject, "OFFLINE_REBUILD_PATH", tmp_path / "offline.json")
    monkeypatch.setattr(subject, "acquire_quarters_and_july_indexes", lambda **_: {"stable": True})
    monkeypatch.setattr(subject, "source_inventory_hashes", lambda _: {"hash": "same"})
    monkeypatch.setattr(subject, "rebuild_july_archive_progress", lambda: None)
    monkeypatch.setattr(subject, "derive_relevant_accession_target", lambda: None)
    monkeypatch.setattr(subject, "inspect_submissions_zip", lambda _: None)
    monkeypatch.setattr(subject, "build_relevant_acceptance_map", lambda: None)
    result = subject.offline_complete_rebuild()
    assert result["match"] is True
    assert result["status"] == "OFFICIAL_SOURCE_OFFLINE_REBUILD_MATCH"


def test_relevant_target_derivation_uses_only_structural_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "local"
    bulk = local / "sec_bulk"
    bulk.mkdir(parents=True)
    target_path = local / "relevant.tsv"
    monkeypatch.setattr(subject, "REPO", tmp_path)
    monkeypatch.setattr(subject, "LOCAL_ROOT", local)
    monkeypatch.setattr(subject, "RELEVANT_TARGET_PATH", target_path)
    submissions = (
        "ACCESSION_NUMBER\tFILING_DATE\tDOCUMENT_TYPE\tISSUERCIK\n"
        "0000000001-21-000001\t01-JUL-2021\t4\t1\n"
        "0000000001-23-000002\t01-JUL-2023\t4\t1\n"
        "0000000001-23-000003\t01-JUL-2023\t4\t1\n"
    )
    transactions = (
        "ACCESSION_NUMBER\tTRANS_CODE\tTRANS_ACQUIRED_DISP_CD\tTRANS_SHARES\tTRANS_PRICEPERSHARE\tDIRECT_INDIRECT_OWNERSHIP\tRETURN\tPNL\tSCORE\n"
        "0000000001-21-000001\tS\tD\t1\t1\tD\t999\t999\t999\n"
        "0000000001-23-000002\tP\tA\t2\t3\tD\t-999\t-999\t-999\n"
        "0000000001-23-000003\tP\tA\t0\t3\tD\t999\t999\t999\n"
    )
    with zipfile.ZipFile(bulk / "sample_form345.zip", "w") as archive:
        archive.writestr("SUBMISSION.tsv", submissions)
        archive.writestr("NONDERIV_TRANS.tsv", transactions)
    result = subject.derive_relevant_accession_target()
    assert result["routine_history_target_count"] == 3
    assert result["signal_structural_target_count"] == 1
    assert result["union_count"] == 3
    assert result["outcome_fields_used"] is False
    assert "RETURN" not in target_path.read_text()


def test_daily_feed_fallback_binds_archive_and_extracted_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    accession = "0000950170-25-003425"
    local = tmp_path / "local"
    archive_path = local / "20250108.nc.tar.gz"
    archive_path.parent.mkdir(parents=True)
    body = valid_submission(accession, accepted="20250108123000", filed="20250108")
    member_path = tmp_path / f"{accession}.nc"
    member_path.write_bytes(body)
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(member_path, arcname=f"./{accession}.nc")
    receipt_path = local / "receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "archives": [
                    {
                        "filing_date": "2025-01-08",
                        "url": "https://www.sec.gov/example",
                        "local_path": str(archive_path.relative_to(tmp_path)),
                        "sha256": subject.sha256_file(archive_path),
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(subject, "REPO", tmp_path)
    monkeypatch.setattr(subject, "DAILY_FEED_ROOT", local)
    monkeypatch.setattr(subject, "DAILY_FEED_RECEIPT_PATH", receipt_path)
    monkeypatch.setattr(
        subject,
        "DAILY_FEED_FALLBACKS",
        {"2025-01-08": {"url": "https://www.sec.gov/example", "accessions": [accession]}},
    )
    records: dict[str, dict[str, object]] = {}
    result = subject.extract_daily_feed_fallbacks(
        {accession: {"form": "4", "filing_date": "2025-01-08", "issuer_cik": "1030894"}},
        records,
    )
    assert result["mapped"] == 1
    assert records[accession]["accepted_at_utc"] == "2025-01-08T17:30:00Z"
    source = records[accession]["sources"][0]
    assert source["parent_archive_sha256"] == subject.sha256_file(archive_path)
    assert source["member_sha256"] == subject.sha256_bytes(body)


def test_oldloads_control_block_supplies_exact_acceptance_instant(tmp_path: Path) -> None:
    accession = "0000950170-25-003425"
    control = bytearray(b" " * subject.OLDLOADS_CONTROL_BLOCK_BYTES)
    control[0:20] = accession.encode()
    control[20:40] = b"4                   "
    control[160:168] = b"20250108"
    control[168:176] = b"20250108"
    control[176:182] = b"160142"
    submission = (
        f"<SUBMISSION>\n<ACCESSION-NUMBER>{accession}\n<TYPE>4\n"
        "<FILING-DATE>20250108\n</SUBMISSION>\n"
    ).encode()
    archive = tmp_path / "oldloads.gz"
    with gzip.open(archive, "wb") as handle:
        handle.write(bytes(control) + submission)
    extracted = subject._extract_oldloads_targets(archive, {accession})
    body, metadata = extracted[accession]
    assert body == bytes(control) + submission
    assert metadata["form"] == "4"
    assert metadata["filing_date"] == "2025-01-08"
    assert metadata["accepted_at_raw_eastern"] == "2025-01-08T16:01:42"
    assert metadata["accepted_at_utc"] == "2025-01-08T21:01:42Z"


def test_receipts_never_persist_sec_user_agent_value() -> None:
    manifest, receipt = subject.blocked_artifacts()
    rendered = json.dumps([manifest, receipt])
    assert "sec_user_agent_value_persisted" in rendered
    assert "ParayaBuck Research " not in rendered
    assert "@" not in rendered
