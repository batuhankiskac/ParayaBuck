#!/usr/bin/env python3
"""Acquire a small original-document SEC pilot for H06/H10 evidence.

One recent 10-K, 10-Q, and Form 4 (when available) is selected per entity
from the already downloaded submissions payload.  The output is immutable
research evidence and stores no requester identity or credentials.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any

import requests


REPO = Path(__file__).resolve().parents[2]
INPUT = REPO / "data/research/external/equity_fundamentals_pilot_2026_09_07_v2"
OUTPUT = REPO / "data/research/external/equity_sec_original_documents_pilot_2026_09_07"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def user_agent() -> str:
    value = os.environ.get("SEC_USER_AGENT", "").strip()
    if not value or "@" not in value:
        raise RuntimeError("BLOCKED_MISSING_APPROVED_SEC_USER_AGENT")
    return value


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "document"


def select_filings(payload: dict[str, Any]) -> list[dict[str, str]]:
    recent = payload.get("filings", {}).get("recent", {})
    keys = (
        "accessionNumber", "filingDate", "acceptanceDateTime", "form",
        "reportDate", "primaryDocument",
    )
    rows = [dict(zip(keys, values)) for values in zip(*(recent.get(k, []) for k in keys))]
    selected: list[dict[str, str]] = []
    for form in ("10-K", "10-Q", "4"):
        match = next((row for row in rows if row.get("form") == form), None)
        if match and match.get("accessionNumber") and match.get("primaryDocument"):
            selected.append(match)
    return selected


def main() -> int:
    try:
        agent = user_agent()
    except RuntimeError as exc:
        print(str(exc))
        return 2
    if OUTPUT.exists():
        raise RuntimeError(f"REFUSE_EXISTING_OUTPUT:{OUTPUT}")
    OUTPUT.mkdir(parents=True)
    selection = json.loads((INPUT / "pilot_selection.json").read_text(encoding="utf-8"))["rows"]
    requests_seen: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    session = requests.Session()
    headers = {"User-Agent": agent, "Accept-Encoding": "gzip, deflate"}
    for entity in selection:
        cik = str(entity["cik"]).zfill(10)
        payload = json.loads((INPUT / f"submissions_{cik}.json").read_text(encoding="utf-8"))
        for filing in select_filings(payload):
            accn = filing["accessionNumber"]
            path_cik = str(int(cik))
            archive_path = f"{ARCHIVES}/{path_cik}/{accn.replace('-', '')}/{filing['primaryDocument']}"
            time.sleep(0.25)
            try:
                response = session.get(archive_path, headers=headers, timeout=35, allow_redirects=False)
                status, content, response_headers = int(response.status_code), bytes(response.content), response.headers
                transport_error = ""
            except requests.RequestException as exc:
                status, content, response_headers, transport_error = 599, b"", {}, type(exc).__name__
            ext = Path(filing["primaryDocument"]).suffix or ".bin"
            out = OUTPUT / f"{safe_name(entity['symbol'])}_{cik}_{safe_name(accn)}_{safe_name(filing['form'])}{ext}"
            row = {
                "symbol": entity["symbol"], "issuer_cik": cik, "accn": accn,
                "form": filing["form"], "filing_date": filing.get("filingDate", ""),
                "accepted": filing.get("acceptanceDateTime", ""),
                "report_date": filing.get("reportDate", ""),
                "primary_document": filing["primaryDocument"], "url": archive_path,
                "status_code": status, "bytes": len(content),
                "content_type": str(response_headers.get("content-type", "")),
                "transport_error": transport_error,
                "output": str(out.relative_to(REPO)) if status == 200 else None,
            }
            requests_seen.append(row.copy())
            if status != 200:
                continue
            out.write_bytes(content)
            row["sha256"] = sha256(out)
            documents.append(row)
    receipt = {
        "status": "COMPLETE" if documents else "BLOCKED_NO_DOCUMENTS",
        "retrieved_at": datetime.now(UTC).isoformat(),
        "provider": "U.S. Securities and Exchange Commission EDGAR Archives",
        "input_selection": str((INPUT / "pilot_selection.json").relative_to(REPO)),
        "requested_forms": ["10-K", "10-Q", "4"],
        "requests": requests_seen,
        "documents": documents,
        "credential_fields_persisted": False,
        "pit_note": "Original documents preserve accession and acceptance metadata; this pilot is evidence plumbing, not an economic test universe.",
    }
    (OUTPUT / "document_acquisition_receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"SEC_DOCUMENT_PILOT_COMPLETE selected={len(requests_seen)} downloaded={len(documents)} output={OUTPUT}")
    return 0 if len(documents) == len(requests_seen) else 3


if __name__ == "__main__":
    raise SystemExit(main())
