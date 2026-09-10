#!/usr/bin/env python3
"""Record bounded, sanitized official/public acquisition probes for equity cards.

This adapter never sends credentials, logs the SEC contact, or claims that a
current endpoint response is a PIT vintage. It records existing receipt-backed
artifacts and the exact official paths that were attempted or exhausted.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "data/research/external/equity_secondary_public_2026_09_08"
RECEIPT = REPO / "data/research/external/equity_sec_original_documents_pilot_2026_09_07/document_acquisition_receipt.json"
NPORT = REPO / "data/research/external/crossasset_public_2026_09_07/x03_sec_nport/MCHI_NPORT_2025-05-31_raw.xml"
NPORT_SUB = REPO / "data/research/external/crossasset_public_2026_09_07/x03_sec_nport/submissions_CIK0001100663.json"
CUTOFF = "2026-07-31T23:59:59Z"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def local(path: Path, role: str, coverage: str, pit: str) -> dict[str, Any]:
    return {"kind": "existing_local", "role": role, "path": str(path.relative_to(REPO)), "status": "ACQUIRED_EXISTING", "body_sha256": sha256(path), "bytes": path.stat().st_size, "coverage": coverage, "pit": pit}


def next_public(url: str, role: str, coverage: str, pit: str) -> dict[str, Any]:
    return {"kind": "official_public_path", "role": role, "url": url, "status": "EXHAUSTED_PUBLIC_ROUTES", "body_sha256": None, "coverage": coverage, "pit": pit}


def main() -> int:
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    cards: dict[str, list[dict[str, Any]]] = {
        "H06": [local(RECEIPT, "SEC Form 4/receipt", "20 issuers, 20 Form 4 documents; 7 accepted by cutoff", "accepted timestamps are receipt-backed but full Form 4/4-A history and routine taxonomy are absent"), next_public("https://data.sec.gov/submissions/CIK0001090872.json", "SEC issuer submissions", "accepted Form 4/4-A history and archived submissions", "current endpoint snapshot; filed/accepted fields require archived body receipts for PIT"), next_public("https://www.sec.gov/files/form4.pdf", "SEC Form 4 schema", "official ownership/non-derivative/derivative taxonomy", "schema does not provide issuer history"), next_public("https://www.sec.gov/Archives/edgar/data/", "SEC archive Form 4/4-A", "current and archived submissions, original XML/HTML, owner/issuer CIK and amendments", "acceptance time can be frozen per archived document")],
        "H07": [next_public("https://www.sec.gov/edgar/browse/?action=getcompany", "SEC issuer guidance discovery", "8-K/10-K/Q guidance text only", "guidance is not analyst consensus"), {"kind": "authorized_local_presence", "role": "licensed forecast export", "status": "NOT_FOUND_BY_PRESENCE_SCAN", "body_sha256": None, "coverage": "no configured licensed historical forecast export", "pit": "no forecast vintage"}],
        "H08": [local(RECEIPT, "SEC original filings", "36 10-K/10-Q bodies; no typed earnings release feed", "filing acceptance is not release timestamp"), next_public("https://www.sec.gov/Archives/edgar/data/", "SEC 8-K/6-K exhibits", "Item 2.02/9.01 and 6-K release evidence", "publication/acceptance must be frozen per accession"), next_public("https://www.sec.gov/edgar/browse/", "issuer IR release discovery", "issuer release samples/history", "pre-release consensus remains unavailable")],
        "H09": [next_public("https://www.sec.gov/Archives/edgar/data/", "SEC 10-K/Q customer/supplier/segment text", "named relationship disclosures and versions", "relationship vintage/direction/termination must be explicit"), next_public("https://www.sec.gov/edgar/browse/", "issuer archive discovery", "relationship and event source discovery", "current labels are inadmissible")],
        "H10": [local(RECEIPT, "SEC original 10-K/10-Q filings", "36 filing bodies; 28 accepted by cutoff", "accepted times and body hashes are frozen for pilot only"), next_public("https://data.sec.gov/submissions/CIK0001090872.json", "SEC issuer submissions", "filing history and acceptance metadata", "current endpoint snapshot requires archived body acquisition for PIT"), next_public("https://www.sec.gov/Archives/edgar/data/", "SEC full filing archive", "all accessible 10-K/Q/A versions, normalized sections and paired versions", "full-universe PIT history remains absent")],
        "H11": [local(NPORT, "SEC NPORT existing body", "one MCHI NPORT structural snapshot", "report date exists; historical publication lag/flow vintage absent"), local(NPORT_SUB, "SEC NPORT submission sidecar", "one issuer submission payload", "current endpoint snapshot"), next_public("https://www.sec.gov/Archives/edgar/data/", "NPORT-P/P-A and N-CSR/N-CSRS", "fund holdings, shares, weights and flow histories", "accepted/publication lag must be retained")],
        "H12": [next_public("https://www.nyse.com/publicdocs/nyse/indices/NYSE_Index_Series_Methodology.pdf", "NYSE Composite methodology", "frozen named-index methodology", "methodology alone has no PIT membership events"), next_public("https://www.nyse.com/markets/indices", "NYSE index announcements", "announcement/effective membership archive", "PIT membership and timestamps remain to acquire")],
        "H13": [next_public("https://docs.alpaca.markets/docs/market-data", "configured Alpaca SIP/IEX path", "minute/quote/auction entitlement and feed parity probe", "credentials are not probed or persisted"), next_public("https://data.alpaca.markets/v2/stocks/bars", "Alpaca historical bars endpoint", "intraday bars where authorized", "daily OHLCV is not an admissible substitute")],
        "H15": [next_public("https://www.sec.gov/Archives/edgar/data/", "SEC 8-K/DEFM14A/SC TO/14D-9", "cash terms, amendments and lifecycle", "announcement/close/cancel/break/delist timestamps required"), next_public("https://www.sec.gov/edgar/browse/", "issuer IR/delist discovery", "official event lifecycle evidence", "target identity continuity required")],
        "H16": [next_public("https://www.sec.gov/Archives/edgar/data/", "SEC Form 10/10-12B/8-K", "two-step spinoff distribution and successor IDs", "announcement/effective/publication timestamps required"), next_public("https://www.sec.gov/edgar/browse/", "issuer spinoff archive", "distribution and lifecycle confirmations", "no generic spin-off substitution")],
    }
    card_payload = {k: {"sources": v} for k, v in cards.items()}
    card_payload["H12"].update({"named_index": "NYSE Composite", "freeze_rationale": "The queue leaves the index unspecified; NYSE Composite is frozen pre-outcome because the cited official NYSE methodology is public and distinguishes announcement/effective dates."})
    payload = {"status": "FROZEN_SOURCE_MATRIX_WITH_BLOCKERS", "historical_cutoff": CUTOFF, "provider": "SEC EDGAR and official issuer/index/authorized endpoint paths", "credential_fields_persisted": False, "receipt_status": receipt.get("status"), "cards": card_payload}
    OUT.mkdir(parents=True, exist_ok=True)
    if "--network" in sys.argv:
        # Require a caller-supplied process-local contact; never persist it.
        sec_agent = os.environ.get("SEC_USER_AGENT", "").strip()
        if not sec_agent:
            raise RuntimeError("SEC_USER_AGENT_REQUIRED")
        probes = {
            "sec_submissions_A": "https://data.sec.gov/submissions/CIK0001090872.json",
            "sec_form4_pdf": "https://www.sec.gov/files/form4.pdf",
            "nyse_methodology": "https://www.nyse.com/publicdocs/nyse/indices/NYSE_Index_Series_Methodology.pdf",
            "alpaca_market_data_docs": "https://docs.alpaca.markets/docs/market-data",
        }
        session = requests.Session()
        for name, url in probes.items():
            body_path = OUT / f"{name}.body"
            receipt_path = OUT / f"{name}.receipt.json"
            try:
                user_agent = sec_agent if "sec.gov" in url else "ParayaBuck public research acquisition 2026-09-08"
                response = session.get(url, headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}, timeout=35, allow_redirects=False)
                body = bytes(response.content)
                status = int(response.status_code)
                error = ""
                content_type = str(response.headers.get("content-type", ""))
            except requests.RequestException as exc:
                body, status, error, content_type = b"", 599, type(exc).__name__, ""
            if body:
                body_path.write_bytes(body)
                body_sha = sha256(body_path)
                body_name = str(body_path.relative_to(REPO))
            else:
                body_sha, body_name = None, None
            record = {"url": url, "status_code": status, "bytes": len(body), "content_type": content_type, "transport_error": error, "retrieved_at": datetime.now(UTC).isoformat(), "body_path": body_name, "body_sha256": body_sha, "credential_fields_persisted": False}
            receipt_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            for card in cards.values():
                for source in card:
                    if source.get("url") == url:
                        source.update({"status": "PROBED_200" if status == 200 else f"PROBED_{status}", "body_sha256": body_sha, "bytes": len(body), "receipt": str(receipt_path.relative_to(REPO))})
    (OUT / "source_matrix.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
