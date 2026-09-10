"""Frozen, read-only local normalization of SEC Form 4 and Form 4/A bodies.

This package is deliberately separate from the authoritative equity gate.  It
parses only receipt-bound bodies already present in the workspace; it does not
request data or infer security continuity, routine thresholds, or economics.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
INPUT_ROOT = REPO / "data/research/external/equity_secondary_public_2026_09_08"
BATCH_RECEIPT = INPUT_ROOT / "sec_batch_receipt.json"
BODY_ROOT = INPUT_ROOT / "sec_batches"
OUT = REPO / "data/research/external/equity_h06_local_normalization_2026_09_09"
ROWS = OUT / "form4_transaction_rows.json"
INVENTORY = OUT / "input_inventory.json"
REPORT = OUT / "report.json"
MANIFEST = OUT / "manifest.json"
DOC = REPO / "docs/research/equity_h06_local_normalization_2026_09_09.md"
CUTOFF = "2026-07-31T23:59:59Z"
FROZEN_AT = "2026-09-09T00:00:00Z"
TAXONOMY_VERSION = "H06_FORM4_FROZEN_TAXONOMY_V1"

EXCLUDED_CODES = frozenset({"A", "C", "D", "E", "F", "G", "M"})
SUPPORTED_CODES = frozenset({"P", "S"})


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def clean(value: str) -> str:
    return " ".join(html.unescape(value).split())


def iso_date(value: str) -> str | None:
    match = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", value)
    if not match:
        return None
    try:
        return date(int(match.group(3)), int(match.group(1)), int(match.group(2))).isoformat()
    except ValueError:
        return None


def accepted_utc(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"accepted_timestamp_not_timezone_aware:{value}")
    return parsed.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def number(value: str) -> float | None:
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", value.replace("−", "-"))
    if not match:
        return None
    return float(match.group(0).replace(",", ""))


def number_with_footnotes(value: str) -> tuple[float | None, list[str]]:
    return number(value), re.findall(r"\((\d+)\)", value)


class Table:
    def __init__(self) -> None:
        self.rows: list[list[str]] = []
        self.text: list[str] = []


class ReceiptHTMLParser(HTMLParser):
    """Small tolerant parser for SEC's rendered Form 4 HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[Table] = []
        self._tables: list[Table] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._all_text: list[str] = []
        self.anchors: list[tuple[str, str]] = []
        self._anchor_href: str | None = None
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        if tag.lower() == "table":
            table = Table()
            self._tables.append(table)
        elif tag.lower() == "tr" and self._tables and self._row is None:
            self._row = []
        elif tag.lower() in {"td", "th"} and self._row is not None and self._cell is None:
            self._cell = []
        elif tag.lower() == "a":
            self._anchor_href = attrs_map.get("href") or ""
            self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(clean(" ".join(self._cell)))
            self._cell = None
        elif lower == "tr" and self._row is not None and self._tables:
            self._tables[-1].rows.append(self._row)
            self._row = None
        elif lower == "table" and self._tables:
            table = self._tables.pop()
            self.tables.append(table)
        elif lower == "a" and self._anchor_href is not None:
            self.anchors.append((self._anchor_href, clean(" ".join(self._anchor_text))))
            self._anchor_href = None
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if not data.strip():
            return
        self._all_text.append(data)
        if self._tables:
            self._tables[-1].text.append(data)
        if self._cell is not None:
            self._cell.append(data)
        if self._anchor_href is not None:
            self._anchor_text.append(data)

    @property
    def all_text(self) -> str:
        return clean(" ".join(self._all_text))


def _href_cik(href: str) -> str | None:
    match = re.search(r"CIK=(\d+)", href, re.I)
    return match.group(1).zfill(10) if match else None


def _identity(parser: ReceiptHTMLParser) -> dict[str, Any]:
    ciks = [(href, text, _href_cik(href)) for href, text in parser.anchors if _href_cik(href)]
    if len(ciks) < 2:
        raise ValueError("form4_identity_links_missing")
    owner_href, owner_name, owner_cik = ciks[0]
    issuer_href, issuer_name, issuer_cik = ciks[1]
    text = parser.all_text
    ticker_match = re.search(r"\[\s*([A-Z][A-Z0-9.\-]*)\s*\]", text)
    earliest_match = re.search(r"Date of Earliest Transaction.*?(\d{2}/\d{2}/\d{4})", text, re.I)
    amendment_match = re.search(r"If Amendment, Date of Original Filed.*?(\d{2}/\d{2}/\d{4})", text, re.I)
    roles = [role for role in ("Director", "10% Owner", "Officer", "Other") if re.search(r"X\s+" + re.escape(role), text, re.I)]
    return {
        "reporting_owner_cik": owner_cik,
        "reporting_owner_name": owner_name,
        "issuer_cik": issuer_cik,
        "issuer_name": issuer_name,
        "ticker": ticker_match.group(1) if ticker_match else None,
        "earliest_transaction_date": iso_date(earliest_match.group(1)) if earliest_match else None,
        "original_filed_date": iso_date(amendment_match.group(1)) if amendment_match else None,
        "relationship_roles": roles,
        "ten_b5_one_plan_checked": bool(re.search(r"X\s+Check this box to indicate that a transaction was made pursuant", text, re.I)),
        "no_longer_subject_checked": bool(re.search(r"X\s+Check this box if no longer subject", text, re.I)),
    }


def _transaction_tables(parser: ReceiptHTMLParser) -> list[tuple[bool, list[str]]]:
    result: list[tuple[bool, list[str]]] = []
    for table in parser.tables:
        table_text = clean(" ".join(table.text))
        if "Table I - Non-Derivative" in table_text:
            expected = 11
            derivative = False
        elif "Table II - Derivative" in table_text:
            expected = 16
            derivative = True
        else:
            continue
        for row in table.rows:
            if len(row) != expected or not any(row) or row[0].startswith("1.") or row[0] == "Code":
                continue
            result.append((derivative, row))
    return result


def parse_body(path: Path, metadata: dict[str, Any], receipt_sha256: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    body = path.read_bytes()
    actual = hashlib.sha256(body).hexdigest()
    declared = metadata.get("body_sha256")
    if declared != actual or not re.fullmatch(r"[0-9a-f]{64}", str(declared)):
        raise ValueError(f"body_sha256_mismatch:{metadata.get('accn')}")
    parser = ReceiptHTMLParser()
    parser.feed(body.decode("utf-8", errors="replace"))
    title = parser.all_text[:80]
    if not re.search(r"SEC FORM 4(?:/A)?", parser.all_text, re.I):
        raise ValueError(f"form4_content_invalid:{metadata.get('accn')}:{title}")
    identity = _identity(parser)
    if identity["issuer_cik"] != str(metadata.get("cik", "")).zfill(10):
        raise ValueError(f"issuer_cik_metadata_mismatch:{metadata.get('accn')}")
    form = metadata["form"]
    rows: list[dict[str, Any]] = []
    for index, (derivative, cells) in enumerate(_transaction_tables(parser)):
        if derivative:
            title_value, conversion_price, tx_date, deemed_date, code, _, acquired, disposed, exercisable, expiration, underlying_title, underlying_amount, price, following, ownership, indirect_nature = cells
            amount_text = acquired or disposed
            acquired_disposed = "A" if acquired else "D" if disposed else None
            price_text = price
            following_text = following
            ownership_text = ownership
            security_title = title_value
        else:
            security_title, tx_date, deemed_date, code, _, amount_text, acquired_disposed, price_text, following_text, ownership_text, indirect_nature = cells
            conversion_price = None
            exercisable = expiration = underlying_title = underlying_amount = None
            price = None
        code = code.split("(", 1)[0].strip().upper() or None
        if code:
            code = re.sub(r"\s*/\s*", "/", code)
        amount, amount_footnotes = number_with_footnotes(amount_text)
        following_value, following_footnotes = number_with_footnotes(following_text)
        price_value = number(price_text)
        if form == "4/A":
            disposition = "AMENDMENT_FORM_4A"
        elif derivative:
            disposition = "EXCLUDED_DERIVATIVE_SECURITY"
        elif not code:
            disposition = "NON_TRANSACTION_HOLDING_ROW"
        elif code == "P" and acquired_disposed == "A":
            disposition = "CANDIDATE_OPEN_MARKET_PURCHASE"
        elif code in EXCLUDED_CODES:
            disposition = "EXCLUDED_FROZEN_TAXONOMY"
        elif code in SUPPORTED_CODES:
            disposition = "EXCLUDED_NON_PURCHASE_DIRECTION"
        else:
            disposition = "UNKNOWN_CODE_OR_DIRECTION"
        rows.append(
            {
                **identity,
                "accession": metadata["accn"],
                "form": form,
                "accepted_at": accepted_utc(metadata["accepted"]),
                "filing_date": metadata.get("filing_date"),
                "report_date": metadata.get("report_date"),
                "transaction_row_index": index,
                "derivative": derivative,
                "transaction_type": "derivative_security" if derivative else "non_derivative_security",
                "security_title": security_title,
                "transaction_date": iso_date(tx_date),
                "deemed_execution_date": iso_date(deemed_date),
                "transaction_code": code,
                "acquired_disposed": acquired_disposed,
                "shares_or_derivative_units": amount,
                "price_per_share": price_value,
                "post_transaction_holdings": following_value,
                "ownership_form": ownership_text or None,
                "indirect_ownership_nature": indirect_nature or None,
                "conversion_or_exercise_price": number(conversion_price or ""),
                "underlying_security_title": underlying_title,
                "underlying_amount": number(underlying_amount or ""),
                "exercise_date": iso_date(exercisable or ""),
                "expiration_date": iso_date(expiration or ""),
                "footnote_markers": sorted(set(amount_footnotes + following_footnotes)),
                "source_body_path": metadata["body_path"],
                "source_body_sha256": actual,
                "source_receipt_path": str(BATCH_RECEIPT.relative_to(REPO)),
                "source_receipt_sha256": receipt_sha256,
                "body_content_type": "SEC_FORM_4A_HTML" if form == "4/A" else "SEC_FORM_4_HTML",
                "routine_history_status": "NOT_CLASSIFIED_FROZEN_RULE_ONLY",
                "routine_history_prior_rows": 0,
                "routine_history_rule": "same issuer/owner/security/code observations are counted package-wide; no horizon or threshold is inferred",
                "exclusion_reason": disposition if disposition.startswith("EXCLUDED") or disposition.startswith("AMENDMENT") else None,
                "disposition": disposition,
            }
        )
    context = {
        "accession": metadata["accn"],
        "form": form,
        "body_path": metadata["body_path"],
        "body_sha256": actual,
        "accepted_at": accepted_utc(metadata["accepted"]),
        "issuer_cik": identity["issuer_cik"],
        "reporting_owner_cik": identity["reporting_owner_cik"],
        "reporting_owner_name": identity["reporting_owner_name"],
        "transaction_rows": len(rows),
        "content_valid": True,
    }
    return rows, context


def _receipt_metadata() -> tuple[list[dict[str, Any]], str]:
    receipt_sha = sha256(BATCH_RECEIPT)
    payload = json.loads(BATCH_RECEIPT.read_text(encoding="utf-8"))
    records = [row for row in payload["rows"] if row.get("form") in {"4", "4/A"} and row.get("status_code") == 200 and row.get("body_path")]
    return records, receipt_sha


def _link_amendments(rows: list[dict[str, Any]]) -> dict[str, Any]:
    originals = [row for row in rows if row["form"] == "4" and row["transaction_date"]]
    by_identity: defaultdict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in originals:
        by_identity[(row["issuer_cik"], row["reporting_owner_cik"], row["transaction_date"])].append(row)
    amendment_rows = [row for row in rows if row["form"] == "4/A"]
    linked: dict[str, str] = {}
    unmatched: list[str] = []
    for row in amendment_rows:
        key = (row["issuer_cik"], row["reporting_owner_cik"], row["transaction_date"] or row["earliest_transaction_date"] or "")
        candidates = by_identity.get(key, [])
        if len(candidates) == 1:
            linked[row["accession"]] = candidates[0]["accession"]
            row["amendment_lineage"] = {
                "original_accession": candidates[0]["accession"],
                "replaced_accession": candidates[0]["accession"],
                "link_status": "PROVABLE_IDENTITY_PERIOD_UNIQUE",
            }
        else:
            unmatched.append(row["accession"])
            row["amendment_lineage"] = {
                "original_accession": None,
                "replaced_accession": None,
                "link_status": "UNMATCHED_OR_NON_UNIQUE",
            }
    all_amendment_accessions = {row["accession"] for row in amendment_rows}
    linked_accessions = set(linked)
    unmatched_accessions = set(unmatched)
    mixed_accessions = linked_accessions & unmatched_accessions
    linked_only_accessions = linked_accessions - unmatched_accessions
    entirely_unmatched_accessions = unmatched_accessions - linked_accessions
    document_level = {
        "total_documents": len(all_amendment_accessions),
        "uniquely_linked_documents": len(linked_accessions),
        "linked_only_documents": len(linked_only_accessions),
        "mixed_documents": len(mixed_accessions),
        "entirely_unmatched_documents": len(entirely_unmatched_accessions),
        "accounting_check": len(linked_only_accessions) + len(mixed_accessions) + len(entirely_unmatched_accessions) == len(all_amendment_accessions),
        "all_amendment_accessions": sorted(all_amendment_accessions),
        "uniquely_linked_accessions": sorted(linked_accessions),
        "linked_only_accessions": sorted(linked_only_accessions),
        "mixed_accessions": sorted(mixed_accessions),
        "entirely_unmatched_accessions": sorted(entirely_unmatched_accessions),
    }
    row_level = {
        "row_count": len(unmatched),
        "accession_refs": unmatched,
        "unique_accession_count": len(unmatched_accessions),
        "unique_accessions": sorted(unmatched_accessions),
    }
    for row in rows:
        row.setdefault(
            "amendment_lineage",
            {"original_accession": None, "replaced_accession": None, "link_status": "NOT_AN_AMENDMENT"},
        )
    return {
        "amendment_documents": len(all_amendment_accessions),
        "linked_amendments": len(linked),
        "row_level_unmatched_or_non_unique": row_level,
        "document_level": document_level,
        "linked_pairs": linked,
    }


def _apply_routine_history(rows: list[dict[str, Any]]) -> None:
    """Count prior package observations without choosing a horizon or threshold."""
    seen: defaultdict[tuple[str, str, str, str | None], int] = defaultdict(int)
    ordered = sorted(
        rows,
        key=lambda row: (
            row["issuer_cik"],
            row["reporting_owner_cik"],
            row["security_title"],
            row["transaction_code"] or "",
            row["transaction_date"] or "9999-99-99",
            row["accession"],
            row["transaction_row_index"],
        ),
    )
    for row in ordered:
        key = (row["issuer_cik"], row["reporting_owner_cik"], row["security_title"], row["transaction_code"])
        row["routine_history_prior_rows"] = seen[key]
        row["routine_history_status"] = "OBSERVED_PACKAGE_HISTORY" if seen[key] else "NO_PRIOR_PACKAGE_ROW"
        if row["transaction_date"]:
            seen[key] += 1


def build_package() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    records, receipt_sha = _receipt_metadata()
    by_path = {row["body_path"]: row for row in records}
    body_files = sorted(BODY_ROOT.glob("*.xml"))
    parsed_rows: list[dict[str, Any]] = []
    contexts: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for body in body_files:
        rel = str(body.relative_to(REPO))
        metadata = by_path.get(rel)
        if metadata is None:
            invalid.append({"body_path": rel, "reason": "NO_RECEIPT_BOUND_FORM4_METADATA"})
            continue
        try:
            rows, context = parse_body(body, metadata, receipt_sha)
        except (ValueError, UnicodeError) as exc:
            invalid.append({"body_path": rel, "accession": metadata.get("accn"), "reason": str(exc)})
            continue
        parsed_rows.extend(rows)
        contexts.append(context)
    amendment = _link_amendments(parsed_rows)
    _apply_routine_history(parsed_rows)
    by_accession = {ctx["accession"]: ctx for ctx in contexts}
    disposition_counts = Counter(row["disposition"] for row in parsed_rows)
    explicit_excluded = sum(
        value for key, value in disposition_counts.items() if key.startswith("EXCLUDED") or key == "AMENDMENT_FORM_4A"
    )
    unknown_rows = disposition_counts.get("UNKNOWN_CODE_OR_DIRECTION", 0)
    source_inventory = {
        "schema": "H06_FORM4_LOCAL_INPUT_INVENTORY_V1",
        "frozen_at_utc": FROZEN_AT,
        "window": {"start": "2022-01-01T00:00:00Z", "end": CUTOFF},
        "receipt_path": str(BATCH_RECEIPT.relative_to(REPO)),
        "receipt_sha256": receipt_sha,
        "metadata_form4_rows": sum(row["form"] == "4" for row in records),
        "metadata_form4a_rows": sum(row["form"] == "4/A" for row in records),
        "body_files_seen": len(body_files),
        "receipt_valid_body_documents": len(contexts),
        "invalid_or_unbound_body_documents": len(invalid),
        "invalid_or_unbound": invalid,
        "documents": sorted(contexts, key=lambda item: item["accession"]),
        "global_accession_unique": len(by_accession) == len(contexts),
    }
    inventory = {
        **source_inventory,
        "body_sha256_inventory": {ctx["body_path"]: ctx["body_sha256"] for ctx in sorted(contexts, key=lambda item: item["body_path"])},
    }
    report = {
        "schema": "H06_FORM4_LOCAL_NORMALIZATION_REPORT_V1",
        "status": "LOCAL_NORMALIZATION_COMPLETE_WITH_DATA_PIT_BLOCKERS" if not invalid else "LOCAL_NORMALIZATION_COMPLETE_WITH_INVALID_INPUTS",
        "frozen_at_utc": FROZEN_AT,
        "historical_cutoff": CUTOFF,
        "taxonomy_version": TAXONOMY_VERSION,
        "economic_run": False,
        "no_auto_promotion": True,
        "input_documents": {"receipt_form4_rows": len(records), "receipt_valid_bodies": len(contexts), "invalid_or_unbound_bodies": len(invalid)},
        "parsed_transaction_rows": len(parsed_rows),
        "disposition_counts": dict(sorted(disposition_counts.items())),
        "row_accounting": {
            "parsed": len(parsed_rows),
            "candidate_for_future_gate": sum(row["disposition"] == "CANDIDATE_OPEN_MARKET_PURCHASE" for row in parsed_rows),
            "admitted_economic": 0,
            "explicitly_excluded": explicit_excluded,
            "non_transaction_holding": disposition_counts.get("NON_TRANSACTION_HOLDING_ROW", 0),
            "unknown": unknown_rows,
        },
        "amendment_lineage": amendment,
        "routine_history": {
            "rows_with_prior_package_observation": sum(row["routine_history_prior_rows"] > 0 for row in parsed_rows),
            "rows_without_prior_package_observation": sum(row["routine_history_prior_rows"] == 0 for row in parsed_rows),
            "classification": "descriptive_only_no_horizon_or_threshold",
        },
        "body_content_validation": {"valid_form4_or_4a_documents": len(contexts), "invalid_documents": len(invalid), "body_sha256_mismatches": 0},
        "admitted_economic_rows": 0,
        "candidate_rows_for_future_gate": sum(row["disposition"] == "CANDIDATE_OPEN_MARKET_PURCHASE" for row in parsed_rows),
        "blockers": [
            "H06 remains data/PIT gated until security continuity, historical ticker mapping, routine threshold and complete source coverage are independently frozen",
            "Form 4/A rows are preserved and linked only when issuer/owner/period identity is unique; amendments are never counted as new candidate transactions",
            "routine history is observed descriptively without inventing a horizon or threshold",
            "no economic signal, candidate approval, PAPER or LIVE action is produced",
        ],
        "source_receipt_path": str(BATCH_RECEIPT.relative_to(REPO)),
        "source_receipt_sha256": receipt_sha,
    }
    return inventory, report, {"rows": parsed_rows, "contexts": contexts}


def write_package() -> dict[str, Any]:
    inventory, report, payload = build_package()
    write_json(ROWS, {"schema": "H06_FORM4_TRANSACTION_ROWS_V1", "frozen_at_utc": FROZEN_AT, "rows": payload["rows"]})
    write_json(INVENTORY, inventory)
    write_json(REPORT, report)
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(
        "\n".join(
            [
                "# H06 local Form 4 normalization — 2026-09-09",
                "",
                "This is a separate research-only normalization package over receipt-bound SEC Form 4 and Form 4/A bodies already present locally. It performs no network access, economic run, PAPER/LIVE action, or security-continuity inference.",
                "",
                f"The package validates `{inventory['receipt_valid_body_documents']}` body hashes and form-specific HTML content. It parses `{report['parsed_transaction_rows']}` typed Table I/II rows and retains `{report['candidate_rows_for_future_gate']}` pre-taxonomy candidate purchase rows for a future gate; economic admission remains zero.",
                "",
                f"Disposition counts: `{json.dumps(report['disposition_counts'], sort_keys=True)}`.",
                "",
                f"Amendment lineage: `{report['amendment_lineage']['linked_amendments']}` uniquely linked of `{report['amendment_lineage']['amendment_documents']}` Form 4/A documents; `{report['amendment_lineage']['document_level']['mixed_documents']}` documents contain both linked and unmatched rows, while `{report['amendment_lineage']['document_level']['entirely_unmatched_documents']}` are entirely unmatched. Row-level unmatched/non-unique evidence is `{report['amendment_lineage']['row_level_unmatched_or_non_unique']['row_count']}` references across `{report['amendment_lineage']['row_level_unmatched_or_non_unique']['unique_accession_count']}` unique accessions, retained with explicit status.",
                "",
                "The frozen taxonomy excludes Form 4/A, derivative rows, grants, conversions, issuer dispositions, gifts, tax withholding and option exercises; open-market purchases use code P with acquired direction A. Other codes remain explicitly unknown or excluded by direction. Routine history is descriptive only because no horizon or threshold is invented.",
                "",
                "H06 remains blocked for security/ticker continuity, complete historical coverage, and a separately frozen routine rule. This package must not be treated as economic or candidate approval.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    artifacts = {path: sha256(path) for path in (ROWS, INVENTORY, REPORT, DOC, Path(__file__))}
    inputs = {str(BATCH_RECEIPT.relative_to(REPO)): {"sha256": sha256(BATCH_RECEIPT), "kind": "receipt"}}
    inputs.update({ctx["body_path"]: {"sha256": ctx["body_sha256"], "kind": "receipt_valid_body"} for ctx in inventory["documents"]})
    manifest = {
        "schema": "H06_FORM4_LOCAL_NORMALIZATION_MANIFEST_V1",
        "package": "equity_h06_local_normalization_2026_09_09",
        "status": "FROZEN_LOCAL_NORMALIZATION",
        "frozen_at_utc": FROZEN_AT,
        "historical_cutoff": CUTOFF,
        "taxonomy_version": TAXONOMY_VERSION,
        "economic_run": False,
        "no_auto_promotion": True,
        "inputs": dict(sorted(inputs.items())),
        "artifacts": {str(path.relative_to(REPO)): {"sha256": value} for path, value in sorted(artifacts.items(), key=lambda item: str(item[0]))},
        "counts": {"receipt_valid_bodies": inventory["receipt_valid_body_documents"], "invalid_or_unbound_bodies": inventory["invalid_or_unbound_body_documents"], "parsed_rows": report["parsed_transaction_rows"], "candidate_rows_for_future_gate": report["candidate_rows_for_future_gate"], "economic_rows": 0},
        "canonical_hash_policy": "canonical JSON hash excludes only manifest_sha256",
        "manifest_sha256": "PENDING_FREEZE",
    }
    manifest["manifest_sha256"] = hashlib.sha256(canonical(manifest)).hexdigest()
    write_json(MANIFEST, manifest)
    return {"status": report["status"], "valid_bodies": inventory["receipt_valid_body_documents"], "parsed_rows": report["parsed_transaction_rows"], "candidate_rows": report["candidate_rows_for_future_gate"], "manifest_sha256": manifest["manifest_sha256"]}


def verify_manifest(path: Path = MANIFEST) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for name, declaration in manifest["inputs"].items():
        item = REPO / name
        if sha256(item) != declaration["sha256"]:
            raise ValueError(f"input_hash_mismatch:{name}")
    for name, declaration in manifest["artifacts"].items():
        item = REPO / name
        if sha256(item) != declaration["sha256"]:
            raise ValueError(f"artifact_hash_mismatch:{name}")
    canonical_manifest = dict(manifest)
    declared = canonical_manifest["manifest_sha256"]
    canonical_manifest["manifest_sha256"] = "PENDING_FREEZE"
    if hashlib.sha256(canonical(canonical_manifest)).hexdigest() != declared:
        raise ValueError("manifest_canonical_hash_mismatch")
    report_path = REPO / "data/research/external/equity_h06_local_normalization_2026_09_09/report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report["economic_run"] is not False or report["admitted_economic_rows"] != 0:
        raise ValueError("economic_output_present")
    return {"status": "H06_LOCAL_NORMALIZATION_MANIFEST_VERIFIED", "checked_inputs": len(manifest["inputs"]), "checked_artifacts": len(manifest["artifacts"]), "manifest_sha256": declared}


def main() -> None:
    print(json.dumps(write_package(), sort_keys=True))


if __name__ == "__main__":
    main()
