"""Normalize the existing MCHI N-PORT body as snapshot schema evidence.

This package is deliberately separate from the authoritative H11 gate.  The
body has a report date and structured holdings, but its local receipt has no
accession or accepted timestamp and therefore cannot support historical flow
or point-in-time admission.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "data/research/external/crossasset_public_2026_09_07/x03_sec_nport/MCHI_NPORT_2025-05-31_raw.xml"
RECEIPT = REPO / "data/research/external/crossasset_public_2026_09_08/x02_x06_matrix/probes/receipts/SEC_existing_MCHI_NPORT_structured_parse_2025-05-31.receipt.json"
SUBMISSION = REPO / "data/research/external/crossasset_public_2026_09_07/x03_sec_nport/submissions_CIK0001100663.json"
BATCH = REPO / "data/research/external/equity_secondary_public_2026_09_08/nport_batch_receipt.json"
OUT = REPO / "data/research_runs/equity_h11_nport_snapshot_2026_09_09"
ROWS = OUT / "snapshot_rows.jsonl"
REPORT = OUT / "normalization_report.json"
MANIFEST = OUT / "manifest.json"
METADATA_PLAN = OUT / "metadata_cleanup_plan.md"
SCRIPT = REPO / "scripts/research/normalize_h11_nport_snapshot_2026_09_09.py"
TEST = REPO / "tests/research/test_h11_nport_snapshot_2026_09_09.py"

PLACEHOLDER_IDS = {"", "N/A", "NA", "000000000", "0000000000"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child_text(element: ET.Element, tag: str) -> str:
    for child in element.iter():
        if local_name(child.tag) == tag:
            return (child.text or "").strip()
    return ""


def child_element(element: ET.Element, tag: str) -> ET.Element | None:
    for child in element.iter():
        if local_name(child.tag) == tag:
            return child
    return None


def finite_number(value: str) -> float | None:
    if not value:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def valid_identifier(value: str) -> str | None:
    cleaned = value.strip()
    return None if cleaned.upper() in PLACEHOLDER_IDS else cleaned


def aware_utc(value: str | None) -> str | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp_must_be_aware")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def source_metadata() -> dict[str, Any]:
    source_sha = sha256(SOURCE)
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    submission = json.loads(SUBMISSION.read_text(encoding="utf-8"))
    if receipt.get("body_path") != str(SOURCE.relative_to(REPO)):
        raise ValueError("receipt_source_path_mismatch")
    if receipt.get("body_sha256") != source_sha or receipt.get("bytes") != SOURCE.stat().st_size:
        raise ValueError("receipt_source_hash_mismatch")
    if receipt.get("credential_fields_persisted") is not False:
        raise ValueError("receipt_credential_flag_invalid")
    report_date = "2025-05-31"
    submission_recent = submission.get("filings", {}).get("recent", {})
    report_matches = [
        index
        for index, date in enumerate(submission_recent.get("reportDate", []))
        if date == report_date
    ]
    cik = "0001100663"
    if str(submission.get("cik", "")).zfill(10) != cik:
        raise ValueError("submission_cik_mismatch")
    return {
        "source_path": str(SOURCE.relative_to(REPO)),
        "source_sha256": source_sha,
        "source_bytes": SOURCE.stat().st_size,
        "receipt_path": str(RECEIPT.relative_to(REPO)),
        "receipt_sha256": sha256(RECEIPT),
        "receipt_status": receipt.get("status"),
        "retrieved_at": aware_utc(receipt.get("retrieved_at")),
        "endpoint": receipt.get("endpoint"),
        "submission_path": str(SUBMISSION.relative_to(REPO)),
        "submission_sha256": sha256(SUBMISSION),
        "submission_cik": cik,
        "submission_report_date_matches": len(report_matches),
        "submission_binding_status": "UNBOUND_ACCESSION",
        "submission_binding_reason": "local submission snapshot has six rows with the same report date, but no accession/body identity is bound to this raw XML; filed and accepted timestamps therefore remain unavailable for this sample",
    }


def parse_snapshot() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    provenance = source_metadata()
    root = ET.parse(SOURCE).getroot()
    invst = [element for element in root.iter() if local_name(element.tag) == "invstOrSec"]
    if not invst:
        raise ValueError("nport_holdings_missing")
    cik = child_text(root, "regCik") or child_text(root, "cik")
    report_date = child_text(root, "repPdDate")
    form = child_text(root, "submissionType")
    date_signed = child_text(root, "dateSigned")
    series_ids = sorted({child_text(root, "seriesId")} - {""})
    class_ids = sorted({child_text(root, "classId")} - {""})
    series_names = sorted({child_text(root, "seriesName")} - {""})
    if cik != "0001100663" or report_date != "2025-05-31" or form != "NPORT-P":
        raise ValueError("nport_header_identity_mismatch")
    if len(series_ids) != 1 or len(class_ids) != 1:
        raise ValueError("nport_series_class_identity_ambiguous")
    base = {
        **provenance,
        "filer_cik": cik,
        "report_date": report_date,
        "form": form,
        "date_signed": date_signed,
        "series_id": series_ids[0],
        "series_name": series_names[0] if len(series_names) == 1 else None,
        "class_id": class_ids[0],
        "publication_time": None,
        "accepted_at": None,
        "available_at": None,
        "admission_status": "SNAPSHOT_SCHEMA_EVIDENCE_ONLY_UNBOUND",
        "flow_rows": 0,
        "pit_status": "PIT_PENDING_UNBOUND_ACCESSION",
    }
    rows: list[dict[str, Any]] = []
    identity_groups: Counter[str] = Counter()
    parsed: list[dict[str, Any]] = []
    for element in invst:
        currency = child_element(element, "currencyConditional")
        identifiers = {
            "cusip": valid_identifier(child_text(element, "cusip")),
            "isin": valid_identifier(child_text(element, "isin")),
            "lei": valid_identifier(child_text(element, "lei")),
        }
        identity_parts = [f"{key}:{value}" for key, value in identifiers.items() if value]
        if not identity_parts:
            identity_parts = [f"name:{child_text(element, 'name')}", f"title:{child_text(element, 'title')}"]
        security_identity = "|".join(identity_parts)
        identity_groups[security_identity] += 1
        parsed.append(
            {
                **base,
                "name": child_text(element, "name") or None,
                "title": child_text(element, "title") or None,
                "identifiers": identifiers,
                "security_identity": security_identity,
                "balance_raw": child_text(element, "balance") or None,
                "balance": finite_number(child_text(element, "balance")),
                "units": child_text(element, "units") or None,
                "currency": currency.attrib.get("curCd") if currency is not None else None,
                "exchange_rate": finite_number(currency.attrib.get("exchangeRt", "")) if currency is not None else None,
                "value_usd_raw": child_text(element, "valUSD") or None,
                "value_usd": finite_number(child_text(element, "valUSD")),
                "pct_value_raw": child_text(element, "pctVal") or None,
                "pct_value": finite_number(child_text(element, "pctVal")),
                "payoff_profile": child_text(element, "payoffProfile") or None,
                "asset_category": child_text(element, "assetCat") or None,
                "issuer_category": child_text(element, "issuerCat") or None,
                "investment_country": child_text(element, "invCountry") or None,
                "lei_raw": child_text(element, "lei") or None,
                "cusip_raw": child_text(element, "cusip") or None,
                "isin_raw": child_text(element, "isin") or None,
            }
        )
    occurrence: Counter[str] = Counter()
    for row in parsed:
        occurrence[row["security_identity"]] += 1
        row["security_identity_group_size"] = identity_groups[row["security_identity"]]
        row["security_identity_status"] = "IDENTIFIER_PRESENT" if any(row["identifiers"].values()) else "IDENTIFIER_MISSING_OR_PLACEHOLDER"
        row["row_identity"] = f"{row['security_identity']}|occurrence:{occurrence[row['security_identity']]}"
        rows.append(row)
    report = {
        "schema": "equity-h11-nport-snapshot-normalization-v1",
        "candidate": "H11",
        "status": "SNAPSHOT_SCHEMA_EVIDENCE_ONLY",
        "filer_cik": cik,
        "form": form,
        "report_date": report_date,
        "date_signed": date_signed,
        "series_id": series_ids[0],
        "series_name": series_names[0] if len(series_names) == 1 else None,
        "class_id": class_ids[0],
        "parsed_holding_rows": len(rows),
        "unique_security_identity_keys": len(identity_groups),
        "duplicate_identity_rows": len(rows) - len(identity_groups),
        "valid_identifier_values": sum(bool(row["identifiers"][key]) for row in rows for key in row["identifiers"]),
        "rows_with_any_valid_identifier": sum(any(row["identifiers"].values()) for row in rows),
        "field_counts": {
            key: sum(row[key] is not None for row in rows)
            for key in ("name", "title", "balance", "units", "currency", "value_usd", "pct_value", "payoff_profile", "asset_category", "issuer_category", "investment_country")
        },
        "currency_counts": dict(sorted(Counter(row["currency"] or "" for row in rows).items())),
        "flow_rows": 0,
        "pit_rows": 0,
        "available_at_rows": 0,
        "admitted_rows": 0,
        "blockers": [
            "local receipt binds body bytes/hash and retrieval only; accession, filed and accepted timestamps are absent",
            "snapshot report date is not publication availability and cannot establish historical fund flow",
            "49 rows share an issuer-level identity key; row occurrence is preserved but no exact security identifier is asserted for those groups",
            "no holdings vintage, ownership-weight history, rebalance event or flow series is admitted",
        ],
        "provenance": provenance,
    }
    return report, rows


def canonical_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build() -> dict[str, Any]:
    report, rows = parse_snapshot()
    OUT.mkdir(parents=True, exist_ok=True)
    with ROWS.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest: dict[str, Any] = {
        "schema": "equity-h11-nport-snapshot-manifest-v1",
        "candidate": "H11",
        "status": "FROZEN_SNAPSHOT_SCHEMA_EVIDENCE_ONLY",
        "research_only": True,
        "economic_run": False,
        "pit_policy": "report_date and retrieval are evidence only; explicit accepted/publication/available_at is required for admission",
        "inputs": {
            str(SOURCE.relative_to(REPO)): {"sha256": sha256(SOURCE), "bytes": SOURCE.stat().st_size},
            str(RECEIPT.relative_to(REPO)): {"sha256": sha256(RECEIPT), "bytes": RECEIPT.stat().st_size},
            str(SUBMISSION.relative_to(REPO)): {"sha256": sha256(SUBMISSION), "bytes": SUBMISSION.stat().st_size},
        },
        "artifacts": {
            str(SCRIPT.relative_to(REPO)): {"sha256": sha256(SCRIPT), "bytes": SCRIPT.stat().st_size},
            str(TEST.relative_to(REPO)): {"sha256": sha256(TEST), "bytes": TEST.stat().st_size},
            str(ROWS.relative_to(REPO)): {"sha256": sha256(ROWS), "bytes": ROWS.stat().st_size, "row_count": len(rows)},
            str(REPORT.relative_to(REPO)): {"sha256": sha256(REPORT), "bytes": REPORT.stat().st_size},
            str(METADATA_PLAN.relative_to(REPO)): {"sha256": sha256(METADATA_PLAN), "bytes": METADATA_PLAN.stat().st_size},
        },
        "report": {"path": str(REPORT.relative_to(REPO)), "sha256": sha256(REPORT), "parsed_holding_rows": len(rows), "flow_rows": 0, "admitted_rows": 0},
        "row_artifact": {"path": str(ROWS.relative_to(REPO)), "sha256": sha256(ROWS), "rows": len(rows)},
        "manifest_sha256": "PENDING_FREEZE",
    }
    manifest["manifest_sha256"] = canonical_hash(manifest)
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def verify() -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if canonical_hash({**manifest, "manifest_sha256": "PENDING_FREEZE"}) != manifest.get("manifest_sha256"):
        raise ValueError("manifest_canonical_hash_mismatch")
    for section in ("inputs", "artifacts"):
        for name, declaration in manifest[section].items():
            path = REPO / name
            if not path.is_file() or path.stat().st_size != declaration["bytes"] or sha256(path) != declaration["sha256"]:
                raise ValueError(f"manifest_file_mismatch:{name}")
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    if report.get("parsed_holding_rows") != manifest["report"]["parsed_holding_rows"] or report.get("flow_rows") != 0 or report.get("admitted_rows") != 0:
        raise ValueError("report_count_or_admission_mismatch")
    rows = ROWS.read_text(encoding="utf-8").splitlines()
    if len(rows) != manifest["row_artifact"]["rows"]:
        raise ValueError("row_count_mismatch")
    if json.loads(REPORT.read_text(encoding="utf-8")) != parse_snapshot()[0]:
        raise ValueError("report_reproduction_mismatch")
    return {"status": "H11_SNAPSHOT_MANIFEST_VERIFIED", "parsed_holding_rows": len(rows), "flow_rows": 0, "admitted_rows": 0, "manifest_sha256": manifest["manifest_sha256"]}


def verify_current_h11_batch() -> dict[str, Any]:
    batch = json.loads(BATCH.read_text(encoding="utf-8"))
    latest = {str(row["accn"]): row for row in batch["rows"] if row.get("accn")}
    valid = [row for row in latest.values() if row.get("status_code") == 200 and row.get("body_path") and not row.get("content_validation_error")]
    if len(valid) != 20 or Counter(row.get("form") for row in valid) != Counter({"N-CSR": 10, "N-CSRS": 10}):
        raise ValueError("current_h11_valid_body_count_mismatch")
    tag_counts = []
    for row in valid:
        body = REPO / row["body_path"]
        if not body.is_file() or body.stat().st_size != row.get("bytes") or sha256(body) != row.get("body_sha256"):
            raise ValueError(f"current_h11_body_receipt_mismatch:{row['accn']}")
        text = body.read_text(encoding="utf-8", errors="replace")
        tag_counts.append(len(re.findall(r"<(?:[\w.-]+:)?invstOrSec\b", text)))
    if any(tag_counts):
        raise ValueError("current_h11_body_contains_nport_holdings")
    return {"valid_body_count": len(valid), "forms": {"N-CSR": 10, "N-CSRS": 10}, "nport_holding_tags": sum(tag_counts), "retryable_accessions": batch.get("retryable_accessions", 0)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = verify() if args.verify else build()
    if args.verify:
        result["current_batch"] = verify_current_h11_batch()
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
