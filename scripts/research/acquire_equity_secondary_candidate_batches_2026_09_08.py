#!/usr/bin/env python3
"""Enumerate frozen equity CIK filing coverage and run resumable public probes.

The inventory is complete for the locally frozen 20 CIK pilot's available
submission arrays. Body acquisition is bounded by --batch-size and resumes by
the saved cursor; it never presents current EDGAR metadata as a PIT vintage.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from scripts.research.equity_secondary_causal_gate_2026_09_08 import parse_sec_document_content

REPO = Path(__file__).resolve().parents[2]
INPUT = REPO / "data/research/external/equity_fundamentals_pilot_2026_09_07_v2"
ARCHIVE = REPO / "data/research/external/equity_sec_archived_submissions_2026_09_07"
OUT = REPO / "data/research/external/equity_secondary_public_2026_09_08"
CUTOFF = datetime.fromisoformat("2026-07-31T23:59:59+00:00")
START = datetime.fromisoformat("2022-01-01T00:00:00+00:00")
FORMS = {"4", "4/A", "10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A", "6-K", "6-K/A", "DEFM14A", "DEFM14C", "SC TO", "SC TO-I", "SC TO-T", "14D-9", "14D-9/A", "10", "10-12B", "10-12G", "NPORT-P", "NPORT-P/A", "N-CSR", "N-CSR/A", "N-CSRS", "N-CSRS/A"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def aware(value: str) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result.astimezone(UTC) if result.tzinfo else None


def rows_from_recent(payload: dict[str, Any]) -> list[dict[str, Any]]:
    recent = payload.get("filings", {}).get("recent", {})
    keys = ["accessionNumber", "filingDate", "acceptanceDateTime", "form", "reportDate", "primaryDocument", "items"]
    count = max((len(recent.get(key, [])) for key in keys), default=0)
    return [{key: (recent.get(key, [""] * count)[i] if i < len(recent.get(key, [])) else "") for key in keys} for i in range(count)]


def rows_from_archive(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("accessionNumber"), list):
        return []
    keys = ["accessionNumber", "filingDate", "acceptanceDateTime", "form", "reportDate", "primaryDocument", "items"]
    count = len(payload["accessionNumber"])
    return [{key: (payload.get(key, [""] * count)[i] if i < len(payload.get(key, [])) else "") for key in keys} for i in range(count)]


def load_universe() -> list[dict[str, str]]:
    selection = json.loads((INPUT / "pilot_selection.json").read_text(encoding="utf-8"))
    return [{"symbol": str(x["symbol"]), "cik": str(x["cik"]).zfill(10)} for x in selection["rows"]]


def inventory() -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    accession_owner: dict[str, tuple[str, str]] = {}
    duplicate_accessions: list[dict[str, Any]] = []
    conflicting_accessions: list[dict[str, Any]] = []
    for entity in load_universe():
        paths = [INPUT / f"submissions_{entity['cik']}.json"] + sorted(ARCHIVE.glob(f"CIK{entity['cik']}-submissions-*.json"))
        for path in paths:
            if not path.is_file():
                continue
            rows = rows_from_recent(json.loads(path.read_text(encoding="utf-8"))) if path.parent == INPUT else rows_from_archive(path)
            for row in rows:
                form = str(row.get("form", ""))
                stamp = aware(str(row.get("acceptanceDateTime", "")))
                accn = str(row.get("accessionNumber", ""))
                if form not in FORMS or not accn:
                    continue
                owner = (entity["symbol"], entity["cik"])
                prior = accession_owner.get(accn)
                if prior is not None:
                    duplicate = {"accn": accn, "first_symbol": prior[0], "first_cik": prior[1], "duplicate_symbol": owner[0], "duplicate_cik": owner[1], "metadata_source": str(path.relative_to(REPO))}
                    (conflicting_accessions if prior != owner else duplicate_accessions).append(duplicate)
                    continue
                accession_owner[accn] = owner
                in_window = stamp is not None and START <= stamp <= CUTOFF
                records.append({"symbol": entity["symbol"], "cik": entity["cik"], "form": form, "accn": accn, "filing_date": row.get("filingDate", ""), "accepted": row.get("acceptanceDateTime", ""), "report_date": row.get("reportDate", ""), "primary_document": row.get("primaryDocument", ""), "items": row.get("items", ""), "in_window": in_window, "metadata_source": str(path.relative_to(REPO))})
    counts: dict[str, int] = {}
    window_counts: dict[str, int] = {}
    for row in records:
        counts[row["form"]] = counts.get(row["form"], 0) + 1
        if row["in_window"]:
            window_counts[row["form"]] = window_counts.get(row["form"], 0) + 1
    return {"universe": load_universe(), "window": {"start": START.isoformat(), "cutoff": CUTOFF.isoformat()}, "records": records, "counts": counts, "window_counts": window_counts, "record_count": len(records), "deduplicated_by_accession": True, "duplicate_accessions": duplicate_accessions, "conflicting_accessions": conflicting_accessions, "global_accession_count": len(accession_owner), "pit_note": "Submission metadata is current/archived endpoint evidence; only bodies with immutable accepted receipt are admissible as PIT evidence. Accession identity is global across issuers and forms; conflicting issuer bindings are excluded."}


def h09_scan() -> dict[str, Any]:
    terms = {"customer": 0, "supplier": 0, "segment": 0, "named_relationship_candidates": 0}
    files: list[dict[str, Any]] = []
    receipt = REPO / "data/research/external/equity_sec_original_documents_pilot_2026_09_07/document_acquisition_receipt.json"
    if not receipt.is_file():
        return {"files": [], "term_counts": terms, "note": "no acquired filing bodies"}
    rows = list(json.loads(receipt.read_text(encoding="utf-8"))["documents"])
    batch = OUT / "sec_batch_receipt.json"
    if batch.is_file():
        rows.extend(json.loads(batch.read_text(encoding="utf-8")).get("rows", []))
    seen: set[str] = set()
    for row in rows:
        if row.get("form") not in {"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A"} or row.get("accn") in seen:
            continue
        seen.add(str(row.get("accn")))
        path = REPO / (row.get("output") or row.get("body_path") or "")
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        low = re.sub(r"<[^>]+>", " ", text).lower()
        hits = {term: low.count(term) for term in ("customer", "supplier", "segment")}
        for term, count in hits.items():
            terms[term] += int(count > 0)
        if any(hits.values()):
            terms["named_relationship_candidates"] += 1
        files.append({"symbol": row.get("symbol"), "form": row.get("form"), "accn": row.get("accn"), "accepted": row.get("accepted"), "path": str(path.relative_to(REPO)), "body_sha256": sha256(path), "explicit_keyword_hits": hits, "relationship_rows": 0, "inference_used": False})
    return {"files": files, "term_counts": terms, "scanned_files": len(files), "note": "keyword presence is a retrieval index only; no relationship identity, direction or termination is inferred."}


def _body_items(text: str) -> list[str]:
    visible = re.sub(r"<[^>]+>", " ", text)
    tags = set(re.findall(r"(?:item|section)\s*([0-9]+(?:\.[0-9]+)?)", visible, re.IGNORECASE))
    return sorted(tags)


def _exhibit_links(text: str) -> list[str]:
    return sorted(set(re.findall(r"href=[\"']([^\"']*(?:ex[-_ ]?99|exhibit)[^\"']*)[\"']", text, re.IGNORECASE)))


def analyse_sec_batch() -> dict[str, Any]:
    receipt_path = OUT / "sec_batch_receipt.json"
    if not receipt_path.is_file():
        return {"status": "NO_BATCH_RECEIPT"}
    all_rows = json.loads(receipt_path.read_text(encoding="utf-8")).get("rows", [])
    rows_by_accn: dict[str, dict[str, Any]] = {}
    for row in all_rows:
        if row.get("accn"):
            rows_by_accn[str(row["accn"])] = row
    rows = list(rows_by_accn.values())
    form4: list[dict[str, Any]] = []
    filings: list[dict[str, Any]] = []
    events = {"8-K": 0, "8-K/A": 0, "6-K": 0, "6-K/A": 0, "item_2_02_metadata": 0, "item_9_01_metadata": 0, "item_2_02_body": 0, "item_9_01_body": 0}
    body_confirmed = 0
    exhibit_confirmed = 0
    release_time_rows = 0
    for row in rows:
        body = REPO / row["body_path"] if row.get("body_path") else None
        text = body.read_text(encoding="utf-8", errors="replace") if body and body.is_file() else ""
        visible = re.sub(r"<[^>]+>", " ", text)
        if row["form"] in {"4", "4/A"}:
            codes = sorted({x.upper() for x in re.findall(r'class=["\'][^"\']*(?:FormData|SmallFormData)[^"\']*["\'][^>]*>\s*([A-Z])\s*</span>', text, re.I) if x.upper() in set("ADFGIMPS")})
            owner_ciks = sorted({x for x in re.findall(r"CIK[=\s]+(\d{10})", text, re.I) if x != row["cik"]})
            form4.append({"symbol": row["symbol"], "accn": row["accn"], "form": row["form"], "accepted": row["accepted"], "body_sha256": row.get("body_sha256"), "issuer_cik": row["cik"], "owner_ciks": owner_ciks, "transaction_codes": codes, "transaction_date_present": bool(re.search(r"\d{2}/\d{2}/\d{4}", visible)), "shares_or_price_markers_present": "Securities".lower() in visible.lower() and "Price".lower() in visible.lower(), "amendment": row["form"].endswith("/A"), "original_amendment_linkage": "UNRESOLVED_FROM_BATCH", "routine_or_exclusion_flags": {"gift": "gift" in visible.lower(), "exercise": "exercise" in visible.lower(), "amendment": row["form"].endswith("/A")}, "open_market_purchase_code_present": "P" in codes})
        if row["form"] in {"10-K", "10-K/A", "10-Q", "10-Q/A"}:
            doc_type = re.search(r'name=["\']dei:DocumentType["\'][^>]*>(.*?)</ix:nonNumeric>', text, re.I | re.S)
            value = re.sub(r"<[^>]+>", "", doc_type.group(1)).strip().upper() if doc_type else ""
            filings.append({"symbol": row["symbol"], "accn": row["accn"], "form": row["form"], "accepted": row["accepted"], "body_sha256": row.get("body_sha256"), "document_type": value, "section_markers": {"management": "management" in visible.lower(), "financial_statements": "financial statements" in visible.lower()}, "amendment": row["form"].endswith("/A"), "version_pairing": "UNRESOLVED_FROM_BATCH"})
        if row["form"] in {"8-K", "8-K/A", "6-K", "6-K/A"}:
            events[row["form"]] += 1
            metadata_items = sorted(set(re.findall(r"\d+\.\d+", str(row.get("items", "")))))
            body_items = _body_items(text)
            exhibits = _exhibit_links(text)
            body_valid = False
            if body and body.is_file():
                try:
                    parse_sec_document_content(body, row)
                    body_valid = True
                except (TypeError, ValueError):
                    body_valid = False
            body_has_event = body_valid and bool(body_items) and (row["form"].startswith("6-") or any(item in body_items for item in ("2.02", "9.01")))
            body_confirmed += int(body_has_event)
            exhibit_confirmed += int(body_valid and bool(exhibits))
            release_time_present = bool(body_has_event and re.search(r"(?:am|pm)|\d{1,2}:\d{2}", visible, re.IGNORECASE))
            release_time_rows += int(release_time_present)
            events["item_2_02_metadata"] += int("2.02" in metadata_items)
            events["item_9_01_metadata"] += int("9.01" in metadata_items)
            events["item_2_02_body"] += int("2.02" in body_items)
            events["item_9_01_body"] += int("9.01" in body_items)
            filings.append({"symbol": row["symbol"], "accn": row["accn"], "form": row["form"], "accepted": row["accepted"], "body_sha256": row.get("body_sha256"), "metadata_items": metadata_items, "body_items": body_items if body_valid else [], "body_valid": body_valid, "body_confirmed": body_has_event, "exhibit_links": exhibits if body_valid else [], "exhibit_confirmed": bool(body_valid and exhibits), "release_time_present": release_time_present, "in_window": row.get("in_window", False)})
    return {"status": "ANALYSED_BOUNDED_BATCH", "form4_rows": form4, "filing_rows": filings, "event_metadata": events, "metadata_rows": sum(events[form] for form in ("8-K", "8-K/A", "6-K", "6-K/A")), "body_confirmed_rows": body_confirmed, "exhibit_confirmed_rows": exhibit_confirmed, "body_confirmed_earnings_events": events["item_2_02_body"], "release_time_rows": release_time_rows, "eligible_open_market_purchase_rows": sum(int(x["open_market_purchase_code_present"] and not x["routine_or_exclusion_flags"]["gift"] and not x["routine_or_exclusion_flags"]["exercise"]) for x in form4), "pit_note": "Submission items are metadata only. Body and exhibit counters require a receipt-backed, form-valid primary document; release time requires an explicit time marker. Routine history, security continuity, amendment linkage and pre-release consensus remain unresolved."}


def nport_inventory() -> dict[str, Any]:
    path = OUT / "sec_submissions_MCHI.body"
    if not path.is_file():
        return {"status": "NO_MCHI_SUBMISSIONS_RECEIPT"}
    rows = rows_from_recent(json.loads(path.read_text(encoding="utf-8")))
    wanted = {"NPORT-P", "NPORT-P/A", "N-CSR", "N-CSR/A", "N-CSRS", "N-CSRS/A"}
    selected = [row for row in rows if row.get("form") in wanted]
    return {"status": "CURRENT_SUBMISSIONS_ENUMERATED", "fund": "MCHI", "cik": "0001100663", "counts": {form: sum(1 for row in selected if row.get("form") == form) for form in sorted(wanted)}, "through_cutoff": sum(1 for row in selected if (stamp := aware(str(row.get("acceptanceDateTime", "")))) and stamp <= CUTOFF), "rows": selected, "existing_body_rows": 1130, "shares_or_weights_fields": ["shares", "percentageOfAssets"], "flow_rows": 0, "pit_note": "Current submissions and one existing NPORT body provide route/field evidence; historical accepted body/vintage and fund-flow panel remain unacquired."}


def download_nport_batch(batch_size: int = 4) -> dict[str, Any]:
    sec_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not sec_agent or "@" not in sec_agent:
        raise RuntimeError("SEC_USER_AGENT_REQUIRED_FOR_NPORT_BATCH")
    source = OUT / "sec_submissions_MCHI.body"
    payload = json.loads(source.read_text(encoding="utf-8"))
    rows = rows_from_recent(payload)
    wanted = {"NPORT-P", "NPORT-P/A", "N-CSR", "N-CSRS", "N-CSR/A", "N-CSRS/A"}
    receipt_path = OUT / "nport_batch_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else {"rows": []}
    all_receipt_rows = receipt.get("rows", []) if isinstance(receipt.get("rows", []), list) else []
    latest: dict[str, dict[str, Any]] = {str(item.get("accn")): item for item in all_receipt_rows if item.get("accn")}
    def identity_ok(accn: str, item: dict[str, Any]) -> bool:
        expected = (accn.split("-", 1)[0].lstrip("0") or "0").zfill(10)
        return str(item.get("filing_cik", "")) == expected

    done = {accn for accn, item in latest.items() if identity_ok(accn, item) and item.get("status_code") == 200 and item.get("body_path") and not item.get("content_validation_error")}
    terminal = {accn for accn, item in latest.items() if identity_ok(accn, item) and item.get("terminal")}
    selected: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for row in rows:
        stamp = aware(str(row.get("acceptanceDateTime", "")))
        if row.get("form") not in wanted or not row.get("accessionNumber") or not stamp or stamp > CUTOFF or row["accessionNumber"] in done or row["accessionNumber"] in terminal:
            continue
        if counts.get(str(row["form"]), 0) >= batch_size:
            continue
        counts[str(row["form"])] = counts.get(str(row["form"]), 0) + 1
        selected.append(row)
    root = OUT / "nport_batches"
    root.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    for row in selected:
        accn = str(row["accessionNumber"])
        # EDGAR archive paths are keyed by the filing-owner CIK, which is the
        # first accession component for these submissions; the fund CIK in the
        # submissions feed is not a safe archive-path key for every row.
        filer_cik = accn.split("-", 1)[0].lstrip("0") or "0"
        base = f"https://www.sec.gov/Archives/edgar/data/{filer_cik}/{accn.replace('-', '')}/"
        index_url = base + "index.json"
        headers = {"User-Agent": sec_agent, "Accept-Encoding": "gzip, deflate"}
        index_status, index_error, member_names = 0, "", []
        try:
            index_response = session.get(index_url, headers=headers, timeout=4, allow_redirects=False)
            index_body, index_status = bytes(index_response.content), int(index_response.status_code)
            if index_status == 200:
                member_names = [str(item.get("name", "")) for item in index_response.json().get("directory", {}).get("item", []) if isinstance(item, dict)]
        except (requests.RequestException, ValueError, AttributeError, TypeError) as exc:
            index_body, index_status, index_error = b"", 599, type(exc).__name__
        support = root / "index"
        support.mkdir(parents=True, exist_ok=True)
        index_path = support / re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{accn}_{len(receipt.get('rows', []))}.json")
        if index_body:
            index_path.write_bytes(index_body)
            index_body_path, index_body_sha = str(index_path.relative_to(REPO)), sha256(index_path)
        else:
            index_body_path, index_body_sha = None, None
        candidates = [member for member in member_names if member.lower().endswith(".txt")]
        if "primary_doc.xml" in member_names:
            candidates.append("primary_doc.xml")
        candidates.append(str(row["primaryDocument"]))
        candidates = list(dict.fromkeys(candidates))[:3]
        url = base + quote(candidates[0], safe="/._-()")
        selected_document = candidates[0]
        try:
            body, status, error = b"", 599, ""
            for candidate in candidates:
                url = base + quote(candidate, safe="/._-()")
                response = session.get(url, headers=headers, timeout=(4, 4), allow_redirects=False, stream=True)
                chunks = bytearray()
                deadline = time.monotonic() + 8
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        chunks.extend(chunk)
                    if time.monotonic() > deadline:
                        raise TimeoutError("nport_body_read_deadline")
                body, status = bytes(chunks), int(response.status_code)
                selected_document = candidate
                candidate_text = body.decode("utf-8", errors="replace")
                candidate_markers = re.search(r"<invstOrSec\b|<portData\b|<formNport\b|<edgarSubmission\b", candidate_text, re.IGNORECASE)
                candidate_ncsr = row["form"].startswith(("N-CSR", "N-CSRS")) and re.search(r"schedule\s+of\s+investments|form\s+n-csr|net\s+assets", candidate_text, re.IGNORECASE)
                if status != 200 or candidate_markers or candidate_ncsr:
                    break
        except (requests.RequestException, TimeoutError) as exc:
            body, status, error = b"", 599, type(exc).__name__
        output = root / re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{accn}_{len(receipt.get('rows', []))}_{row['primaryDocument']}")
        if body:
            output.write_bytes(body)
            body_path, body_sha = str(output.relative_to(REPO)), sha256(output)
            text = body.decode("utf-8", errors="replace")
            valid_nport = re.search(r"<invstOrSec\b|<portData\b|<formNport\b|<edgarSubmission\b", text, re.IGNORECASE)
            valid_ncsr = row["form"].startswith(("N-CSR", "N-CSRS")) and re.search(r"schedule\s+of\s+investments|form\s+n-csr|net\s+assets", text, re.IGNORECASE)
            content_error = "" if valid_nport or valid_ncsr else "nport_body_content_missing"
        else:
            body_path, body_sha, content_error = None, None, ""
        prior = [item for item in receipt.get("rows", []) if str(item.get("accn")) == accn and identity_ok(accn, item)]
        reason = content_error or error or (f"http_{status}" if status != 200 else "")
        prior_reasons = [str(item.get("terminal_reason") or item.get("content_validation_error") or item.get("transport_error") or (f"http_{item.get('status_code')}" if item.get("status_code") != 200 else "")) for item in prior]
        repeated_terminal = len(prior) >= 2 and bool(reason) and prior_reasons[-2:] == [reason, reason]
        terminal_row = repeated_terminal and ((status == 200 and bool(content_error)) or status in {403, 404, 410})
        receipt.setdefault("rows", []).append({"fund": "MCHI", "cik": "0001100663", "filing_cik": filer_cik.zfill(10), "form": row["form"], "accn": accn, "accepted": row.get("acceptanceDateTime"), "report_date": row.get("reportDate"), "primary_document": row.get("primaryDocument"), "selected_document": selected_document, "url": url, "index_url": index_url, "index_status_code": index_status, "index_transport_error": index_error, "index_body_path": index_body_path, "index_bytes": index_path.stat().st_size if index_body_path else 0, "index_body_sha256": index_body_sha, "status_code": status, "bytes": len(body), "transport_error": error, "body_path": body_path, "body_sha256": body_sha, "content_validation_error": content_error, "attempt": len(prior) + 1, "terminal": terminal_row, "terminal_reason": reason if terminal_row else "", "retrieved_at": datetime.now(UTC).isoformat(), "credential_fields_persisted": False})
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    latest = {str(item.get("accn")): item for item in receipt.get("rows", []) if item.get("accn")}
    done = {accn for accn, item in latest.items() if identity_ok(accn, item) and item.get("status_code") == 200 and item.get("body_path") and not item.get("content_validation_error")}
    terminal = {accn for accn, item in latest.items() if identity_ok(accn, item) and item.get("terminal")}
    eligible = [row for row in rows if row.get("form") in wanted and row.get("accessionNumber") and (stamp := aware(str(row.get("acceptanceDateTime", "")))) and stamp <= CUTOFF]
    remaining = sum(1 for row in eligible if row["accessionNumber"] not in done and row["accessionNumber"] not in terminal)
    receipt.update({"status": "COMPLETE_BOUNDED_NPORT_BATCH", "inventory_metadata_rows": len(eligible), "valid_accessions": len(done), "terminal_accessions": len(terminal), "retryable_accessions": remaining, "remaining_metadata_rows": remaining, "batch_size_per_form": batch_size, "pit_note": "NPORT primary bodies are retained with accepted/report dates; holdings vintage and flow admission remain pending."})
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"attempted": len(selected), "remaining_metadata_rows": remaining}


def augment_source_matrix() -> None:
    matrix_path = OUT / "source_matrix.json"
    if not matrix_path.is_file():
        return
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    inventory_data = json.loads((OUT / "candidate_filing_inventory.json").read_text(encoding="utf-8")) if (OUT / "candidate_filing_inventory.json").is_file() else {}
    batch_data = json.loads((OUT / "sec_batch_receipt.json").read_text(encoding="utf-8")) if (OUT / "sec_batch_receipt.json").is_file() else {}
    counts = inventory_data.get("window_counts", {})
    h06_total = sum(int(counts.get(form, 0)) for form in ("4", "4/A"))
    h08_total = sum(int(counts.get(form, 0)) for form in ("8-K", "8-K/A", "6-K", "6-K/A"))
    additions = {
        "H06": [("candidate_filing_inventory.json", f"complete frozen 20 CIK metadata enumeration; {h06_total} Form 4/4-A rows in window", "metadata endpoint evidence, archived body coverage incomplete"), ("sec_batch_receipt.json", f"{len(batch_data.get('rows', []))} resumable SEC body attempts with immutable hashes/accepted metadata", f"{batch_data.get('remaining_by_form', {}).get('4', 0) + batch_data.get('remaining_by_form', {}).get('4/A', 0)} in-window Form 4/4-A rows remain; invalid/error bodies stay pending")],
        "H08": [("candidate_filing_inventory.json", f"{counts.get('8-K', 0)} 8-K, {counts.get('8-K/A', 0)} 8-K/A, {counts.get('6-K', 0)} 6-K and {counts.get('6-K/A', 0)} 6-K/A metadata rows in window", "filing time is not necessarily earnings release time"), ("sec_batch_analysis.json", f"{sum(1 for row in batch_data.get('rows', []) if row.get('form') in {'8-K', '8-K/A', '6-K', '6-K/A'})} 8-K/6-K receipt rows analysed with metadata/body/exhibit separation", f"{max(0, h08_total - sum(1 for row in batch_data.get('rows', []) if row.get('form') in {'8-K', '8-K/A', '6-K', '6-K/A'}))} metadata rows remain; consensus vintages absent")],
        "H09": [("h09_relationship_scan.json", "948 acquired 10-K/Q/8-K bodies indexed for customer/supplier/segment text", "keyword hits are retrieval only; explicit relationship parsing yields zero rows")],
        "H10": [("candidate_filing_inventory.json", f"{counts.get('10-K', 0)} 10-K, {counts.get('10-K/A', 0)} 10-K/A and {counts.get('10-Q', 0)} 10-Q metadata rows in window", "submission metadata is current/archived endpoint evidence"), ("sec_batch_receipt.json", "333 original 10-K/Q bodies validated across the frozen-window filing family", "version pairing and section normalization remain unresolved")],
        "H11": [("sec_candidate_metadata_receipt.json", "MCHI submissions and archive route probed with 200 responses", "current endpoint snapshot, not historical PIT"), ("h11_nport_inventory.json", "MCHI current metadata: 1,433 NPORT-P, 44 NPORT-P/A, 17 N-CSR, 19 N-CSRS", "1,424 accepted by cutoff; no historical body/flow panel"), ("nport_batch_receipt.json", "bounded official MCHI NPORT primary-body attempts with accepted/report dates and hashes", "remaining history and fund-flow admission remain pending")],
        "H12": [("candidate_public_route_receipt.json", "NYSE /index alternate returned 200; market route 404 and API GET 405", "no historical membership/announcement archive acquired")],
        "H13": [("candidate_public_route_receipt.json", "SIP/IEX bars/trades/quotes routes returned 401", "credentials/entitlement unavailable; no intraday sample or parity")],
        "H15": [("candidate_filing_inventory.json", "8-K/8-K/A plus one DEFM14A metadata row available for official lifecycle search", "no frozen deal sample or lifecycle parser"), ("sec_batch_receipt.json", "one DEFM14A original body acquired and hashed", "terms/amendments/close/break/cancel identity not yet parsed")],
        "H16": [("candidate_filing_inventory.json", "official filing inventory available for Form 10/10-12B/8-K search", "no frozen spinoff sample or successor parser")],
    }
    for card, items in additions.items():
        sources = matrix["cards"][card]["sources"]
        for filename, coverage, pit in items:
            path = OUT / filename
            if not path.is_file():
                continue
            existing = next((source for source in sources if source.get("path") == str(path.relative_to(REPO))), None)
            if existing:
                existing.update({"body_sha256": sha256(path), "bytes": path.stat().st_size, "coverage": coverage, "pit": pit})
                continue
            sources.append({"kind": "existing_local", "role": "candidate-specific acquisition artifact", "path": str(path.relative_to(REPO)), "status": "ACQUIRED_EXISTING", "body_sha256": sha256(path), "bytes": path.stat().st_size, "coverage": coverage, "pit": pit})
    matrix_path.write_text(json.dumps(matrix, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def public_routes() -> list[dict[str, str]]:
    return [
        {"candidate": "H12", "url": "https://www.nyse.com/markets/indices"},
        {"candidate": "H12", "url": "https://www.nyse.com/index"},
        {"candidate": "H12", "url": "https://www.ice.com/index"},
        {"candidate": "H12", "url": "https://www.nyse.com/api/quotes/filter"},
        {"candidate": "H13", "url": "https://data.alpaca.markets/v2/stocks/A/bars?timeframe=1Min&start=2026-07-30T13:30:00Z&end=2026-07-30T14:00:00Z&feed=sip"},
        {"candidate": "H13", "url": "https://data.alpaca.markets/v2/stocks/A/bars?timeframe=1Min&start=2026-07-30T13:30:00Z&end=2026-07-30T14:00:00Z&feed=iex"},
        {"candidate": "H13", "url": "https://data.alpaca.markets/v2/stocks/A/trades?start=2026-07-30T13:30:00Z&end=2026-07-30T14:00:00Z&feed=sip"},
        {"candidate": "H13", "url": "https://data.alpaca.markets/v2/stocks/A/quotes?start=2026-07-30T13:30:00Z&end=2026-07-30T14:00:00Z&feed=iex"},
    ]


def probe_routes() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    session = requests.Session()
    for route in public_routes():
        try:
            response = session.get(route["url"], headers={"User-Agent": "ParayaBuck public research acquisition 2026-09-08"}, timeout=25, allow_redirects=False)
            body = bytes(response.content)
            status, error = int(response.status_code), ""
            content_type = str(response.headers.get("content-type", ""))
        except requests.RequestException as exc:
            body, status, error, content_type = b"", 599, type(exc).__name__, ""
        name = re.sub(r"[^A-Za-z0-9]+", "_", route["candidate"] + "_" + route["url"].split("//", 1)[-1])[:100]
        body_path = OUT / f"route_{name}.body"
        if body:
            body_path.write_bytes(body)
            body_name, body_sha = str(body_path.relative_to(REPO)), sha256(body_path)
        else:
            body_name, body_sha = None, None
        result.append({**route, "status_code": status, "bytes": len(body), "content_type": content_type, "transport_error": error, "body_path": body_name, "body_sha256": body_sha, "retrieved_at": datetime.now(UTC).isoformat(), "credential_fields_persisted": False})
    return result


def _download_supporting_evidence(session: requests.Session, row: dict[str, Any], accn_path: str, sec_agent: str, root: Path, attempt_id: int) -> dict[str, Any]:
    """Fetch official filing index and bounded earnings exhibits for 8-K/6-K rows."""
    base = f"https://www.sec.gov/Archives/edgar/data/{int(row['cik'])}/{accn_path}/"
    headers = {"User-Agent": sec_agent, "Accept-Encoding": "gzip, deflate"}
    result: dict[str, Any] = {"index_url": base + "index.json", "index_status_code": 0, "index_body_path": None, "index_body_sha256": None, "exhibits": []}
    try:
        response = session.get(result["index_url"], headers=headers, timeout=12, allow_redirects=False)
        body, status = bytes(response.content), int(response.status_code)
    except requests.RequestException as exc:
        body, status = b"", 599
        result["index_transport_error"] = type(exc).__name__
    result["index_status_code"] = status
    if not body:
        return result
    support = root / "support"
    support.mkdir(parents=True, exist_ok=True)
    index_path = support / re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{row['accn']}_{attempt_id}_index.json")
    index_path.write_bytes(body)
    result["index_body_path"] = str(index_path.relative_to(REPO))
    result["index_bytes"] = len(body)
    result["index_body_sha256"] = sha256(index_path)
    names: list[str] = []
    if status == 200:
        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
            names = [str(item.get("name", "")) for item in payload.get("directory", {}).get("item", []) if isinstance(item, dict)]
        except (ValueError, TypeError, AttributeError):
            names = []
    if not names:
        try:
            html_body = body.decode("utf-8", errors="replace")
            names = re.findall(r"href=[\"']([^\"']+)[\"']", html_body, re.IGNORECASE)
        except UnicodeError:
            names = []
    selected = [name for name in names if name and name != row.get("primary_document") and re.search(r"(?:ex[-_ ]?99|exhibit|earnings|99\.)", name, re.IGNORECASE)][:4]
    for name in selected:
        exhibit_url = base + quote(name, safe="/._-()")
        try:
            exhibit_response = session.get(exhibit_url, headers=headers, timeout=12, allow_redirects=False)
            exhibit_body, exhibit_status = bytes(exhibit_response.content), int(exhibit_response.status_code)
        except requests.RequestException as exc:
            exhibit_body, exhibit_status = b"", 599
            transport_error = type(exc).__name__
        else:
            transport_error = ""
        item: dict[str, Any] = {"name": name, "url": exhibit_url, "status_code": exhibit_status, "bytes": len(exhibit_body), "transport_error": transport_error, "body_path": None, "body_sha256": None}
        if exhibit_body:
            output = support / re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{row['accn']}_{attempt_id}_{name}")
            output.write_bytes(exhibit_body)
            item.update({"body_path": str(output.relative_to(REPO)), "body_sha256": sha256(output)})
        result["exhibits"].append(item)
    return result


def download_sec_batch(rows: list[dict[str, Any]], batch_size: int, forms: set[str]) -> dict[str, Any]:
    sec_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not sec_agent or "@" not in sec_agent:
        raise RuntimeError("SEC_USER_AGENT_REQUIRED_FOR_SEC_BATCH")
    root = OUT / "sec_batches"
    root.mkdir(parents=True, exist_ok=True)
    receipt_path = OUT / "sec_batch_receipt.json"
    previous = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else {"rows": []}
    prior_remaining = previous.get("remaining_by_form", {})
    if prior_remaining and all(int(prior_remaining.get(form, 0)) == 0 for form in forms):
        return {"attempted": 0, "total_receipt_rows": len(previous.get("rows", [])), "remaining_metadata_rows": 0}
    done: set[str] = set()
    terminal: set[str] = set()
    for existing in previous.get("rows", []):
        if existing.get("terminal") and existing.get("accn"):
            terminal.add(str(existing["accn"]))
        body = REPO / str(existing["body_path"]) if existing.get("body_path") else None
        if existing.get("status_code") != 200 or body is None or not body.is_file():
            continue
        try:
            parse_sec_document_content(body, {**existing, "issuer_cik": existing.get("cik"), "output": existing.get("body_path")})
        except (TypeError, ValueError):
            continue
        done.add(str(existing.get("accn")))
    # Recover bodies written before an interrupted run could checkpoint its
    # receipt. Filename matching is limited to the frozen inventory accession.
    row_by_accn = {str(row["accn"]): row for row in rows}
    for body in root.iterdir():
        if not body.is_file() or body.name.endswith(".json"):
            continue
        accession_match = re.search(r"\d{10}-\d{2}-\d{6}", body.name)
        candidate = row_by_accn.get(accession_match.group(0)) if accession_match else None
        match = candidate if candidate and candidate["accn"] not in done and candidate["symbol"] in body.name else None
        if not match:
            continue
        try:
            parse_sec_document_content(body, {**match, "issuer_cik": match.get("cik"), "output": str(body.relative_to(REPO))})
        except (TypeError, ValueError):
            continue
        previous.setdefault("rows", []).append({**match, "url": f"https://www.sec.gov/Archives/edgar/data/{int(match['cik'])}/{match['accn'].replace('-', '')}/{match['primary_document']}", "status_code": 200, "bytes": body.stat().st_size, "content_type": "application/octet-stream", "transport_error": "", "body_path": str(body.relative_to(REPO)), "body_sha256": sha256(body), "retrieved_at": datetime.now(UTC).isoformat(), "credential_fields_persisted": False, "recovered_after_interrupt": True})
        done.add(match["accn"])
    selected: list[dict[str, Any]] = []
    per_form: dict[str, int] = {}
    for row in rows:
        if not row["in_window"] or row["form"] not in forms or row["accn"] in done or row["accn"] in terminal:
            continue
        if per_form.get(row["form"], 0) >= batch_size:
            continue
        per_form[row["form"]] = per_form.get(row["form"], 0) + 1
        selected.append(row)
    session = requests.Session()
    prefetched: dict[str, tuple[bytes, int, str, str]] = {}
    if forms and forms <= {"4", "4/A"} and len(selected) > 1:
        def fetch_form4(row: dict[str, Any]) -> tuple[str, tuple[bytes, int, str, str]]:
            accn_path = row["accn"].replace("-", "")
            document = row["primary_document"] or "form4.xml"
            url = f"https://www.sec.gov/Archives/edgar/data/{int(row['cik'])}/{accn_path}/{document}"
            try:
                time.sleep(0.15)
                response = requests.get(url, headers={"User-Agent": sec_agent, "Accept-Encoding": "gzip, deflate"}, timeout=12, allow_redirects=False)
                return row["accn"], (bytes(response.content), int(response.status_code), "", str(response.headers.get("content-type", "")))
            except requests.RequestException as exc:
                return row["accn"], (b"", 599, type(exc).__name__, "")
        with ThreadPoolExecutor(max_workers=4) as executor:
            for accn, result in executor.map(fetch_form4, selected):
                prefetched[accn] = result
    for row in selected:
        accn_path = row["accn"].replace("-", "")
        document = row["primary_document"] or ("form4.xml" if row["form"].startswith("4") else "index.html")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(row['cik'])}/{accn_path}/{document}"
        if row["accn"] in prefetched:
            body, status, error, content_type = prefetched[row["accn"]]
        else:
            try:
                time.sleep(0.1)
                response = session.get(url, headers={"User-Agent": sec_agent, "Accept-Encoding": "gzip, deflate"}, timeout=12, allow_redirects=False)
                body, status, error = bytes(response.content), int(response.status_code), ""
                content_type = str(response.headers.get("content-type", ""))
            except requests.RequestException as exc:
                body, status, error, content_type = b"", 599, type(exc).__name__, ""
        attempt_id = len(previous.get("rows", []))
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{row['symbol']}_{row['form']}_{row['accn']}_{attempt_id}_{document}")
        output = root / safe
        validation_error = ""
        if body:
            output.write_bytes(body)
            body_path, body_sha = str(output.relative_to(REPO)), sha256(output)
            try:
                parse_sec_document_content(output, {**row, "issuer_cik": row.get("cik"), "output": body_path})
            except (TypeError, ValueError) as exc:
                validation_error = str(exc).split(":", 2)[0]
        else:
            body_path, body_sha = None, None
        supporting = _download_supporting_evidence(session, row, accn_path, sec_agent, root, attempt_id) if row["form"] in {"8-K", "8-K/A", "6-K", "6-K/A"} and status == 200 and validation_error == "" else {}
        reason = error or validation_error or (f"http_{status}" if status != 200 else "")
        prior_attempts = [x for x in previous.get("rows", []) if x.get("accn") == row["accn"]]
        repeated_reason = sum(1 for x in prior_attempts if (x.get("transport_error") or x.get("content_validation_error") or (f"http_{x.get('status_code')}" if x.get("status_code") != 200 else "")) == reason)
        terminal_eligible = (status == 200 and bool(validation_error)) or status in {403, 404, 410}
        is_terminal = bool(reason and terminal_eligible and repeated_reason + 1 >= 3)
        previous.setdefault("rows", []).append({**row, "url": url, "status_code": status, "bytes": len(body), "content_type": content_type, "transport_error": error, "body_path": body_path, "body_sha256": body_sha, "content_validation_error": validation_error, "attempt": len(prior_attempts) + 1, "terminal": is_terminal, "terminal_reason": reason if is_terminal else "", "retrieved_at": datetime.now(UTC).isoformat(), "credential_fields_persisted": False, **supporting})
        # Checkpoint every accession so an interruption never loses terminal
        # transport/content evidence or forces a duplicate raw-body write.
        receipt_path.write_text(json.dumps(previous, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    acquired: set[str] = set()
    latest_rows: dict[str, dict[str, Any]] = {}
    for existing in previous.get("rows", []):
        if existing.get("accn"):
            latest_rows[str(existing["accn"])] = existing
    for accn, existing in latest_rows.items():
        if existing.get("terminal"):
            terminal.add(accn)
        body = REPO / str(existing["body_path"]) if existing.get("body_path") else None
        if existing.get("status_code") != 200 or body is None or not body.is_file():
            continue
        try:
            parse_sec_document_content(body, {**existing, "issuer_cik": existing.get("cik"), "output": existing.get("body_path")})
        except (TypeError, ValueError):
            continue
        acquired.add(accn)
    invalid_rows = sum(1 for x in latest_rows.values() if x.get("content_validation_error") or (x.get("status_code") == 200 and not x.get("body_path")))
    tracked_forms = {"4", "4/A", "10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A", "6-K", "6-K/A", "DEFM14A", "10", "10-12B"}
    remaining_by_form = {form: sum(1 for row in rows if row["in_window"] and row["form"] == form and row["accn"] not in acquired and row["accn"] not in terminal) for form in sorted(tracked_forms)}
    terminal_by_form = {form: sum(1 for row in rows if row["in_window"] and row["form"] == form and row["accn"] in terminal) for form in sorted(tracked_forms)}
    previous.update({"status": "COMPLETE_BOUNDED_BATCH", "batch_size_per_form": batch_size, "forms": sorted(forms), "window": {"start": START.isoformat(), "cutoff": CUTOFF.isoformat()}, "remaining_by_form": remaining_by_form, "terminal_by_form": terminal_by_form, "terminal_accessions": sorted(terminal), "remaining_metadata_rows": sum(remaining_by_form.values()), "invalid_body_rows": invalid_rows, "pit_note": "Only body rows with immutable accepted timestamp, matching receipt hash and form-specific content validation are admissible; invalid HTTP-200 bodies remain pending until three identical terminal attempts; batch cap is explicit and resumable."})
    receipt_path.write_text(json.dumps(previous, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"attempted": len(selected), "total_receipt_rows": len(previous.get("rows", [])), "remaining_metadata_rows": previous["remaining_metadata_rows"]}


def probe_sec_metadata() -> list[dict[str, Any]]:
    sec_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not sec_agent or "@" not in sec_agent:
        raise RuntimeError("SEC_USER_AGENT_REQUIRED_FOR_SEC_METADATA")
    urls = {
        "sec_submissions_MCHI": "https://data.sec.gov/submissions/CIK0001100663.json",
        "sec_nport_archive": "https://www.sec.gov/Archives/edgar/data/1100663/",
    }
    session = requests.Session()
    rows: list[dict[str, Any]] = []
    for name, url in urls.items():
        try:
            response = session.get(url, headers={"User-Agent": sec_agent, "Accept-Encoding": "gzip, deflate"}, timeout=35, allow_redirects=False)
            body, status, error = bytes(response.content), int(response.status_code), ""
            content_type = str(response.headers.get("content-type", ""))
        except requests.RequestException as exc:
            body, status, error, content_type = b"", 599, type(exc).__name__, ""
        output = OUT / f"{name}.body"
        if body:
            output.write_bytes(body)
            body_path, body_sha = str(output.relative_to(REPO)), sha256(output)
        else:
            body_path, body_sha = None, None
        rows.append({"url": url, "status_code": status, "bytes": len(body), "content_type": content_type, "transport_error": error, "body_path": body_path, "body_sha256": body_sha, "retrieved_at": datetime.now(UTC).isoformat(), "credential_fields_persisted": False})
    return rows


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    data = inventory()
    (OUT / "candidate_filing_inventory.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (OUT / "h09_relationship_scan.json").write_text(json.dumps(h09_scan(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (OUT / "sec_batch_analysis.json").write_text(json.dumps(analyse_sec_batch(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (OUT / "h11_nport_inventory.json").write_text(json.dumps(nport_inventory(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    augment_source_matrix()
    if "--network" in sys.argv:
        sec_metadata = probe_sec_metadata()
        (OUT / "sec_candidate_metadata_receipt.json").write_text(json.dumps({"status": "COMPLETE_BOUNDED_SEC_METADATA_PROBES", "routes": sec_metadata, "pit_note": "SEC contact was process-local; metadata and archive indexes are not PIT bodies without immutable snapshots."}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        data["public_route_probes"] = probe_routes()
        (OUT / "candidate_public_route_receipt.json").write_text(json.dumps({"status": "COMPLETE_BOUNDED_ROUTE_PROBES", "routes": data["public_route_probes"], "pit_note": "Alpaca routes were credential-presence/entitlement probes; no keys are persisted."}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        batch_size = int(next((arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--batch-size=")), "2"))
        forms_arg = next((arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--forms=")), "4,4/A,10-K,10-K/A,10-Q,10-Q/A,8-K,8-K/A")
        selected_forms = set(forms_arg.split(","))
        rounds = 1
        if "--drain" in sys.argv:
            rounds = 1000000
        for _ in range(rounds):
            data["sec_batch"] = download_sec_batch(data["records"], batch_size, selected_forms)
            data["nport_batch"] = download_nport_batch(int(next((arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--nport-batch-size=")), "4")))
            if "--drain" not in sys.argv or (data["sec_batch"].get("attempted", 0) == 0 and data["nport_batch"].get("attempted", 0) == 0):
                break
        (OUT / "sec_batch_analysis.json").write_text(json.dumps(analyse_sec_batch(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (OUT / "candidate_filing_inventory.json").write_text(json.dumps(inventory(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        augment_source_matrix()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
