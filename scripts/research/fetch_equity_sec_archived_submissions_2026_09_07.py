#!/usr/bin/env python3
"""Fetch SEC submissions history files referenced by the pilot payloads."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import time

import requests


REPO = Path(__file__).resolve().parents[2]
INPUT = REPO / "data/research/external/equity_fundamentals_pilot_2026_09_07_v2"
OUTPUT = REPO / "data/research/external/equity_sec_archived_submissions_2026_09_07"
BASE = "https://data.sec.gov/submissions"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not agent or "@" not in agent:
        print("BLOCKED_MISSING_APPROVED_SEC_USER_AGENT")
        return 2
    if OUTPUT.exists():
        raise RuntimeError(f"REFUSE_EXISTING_OUTPUT:{OUTPUT}")
    OUTPUT.mkdir(parents=True)
    session = requests.Session()
    headers = {"User-Agent": agent, "Accept-Encoding": "gzip, deflate"}
    requests_seen: list[dict[str, object]] = []
    for source in sorted(INPUT.glob("submissions_*.json")):
        payload = json.loads(source.read_text(encoding="utf-8"))
        for ref in payload.get("filings", {}).get("files", []):
            name = str(ref["name"])
            url = f"{BASE}/{name}"
            time.sleep(0.25)
            try:
                response = session.get(url, headers=headers, timeout=35, allow_redirects=False)
                status, content, transport = int(response.status_code), bytes(response.content), ""
                content_type = str(response.headers.get("content-type", ""))
            except requests.RequestException as exc:
                status, content, transport, content_type = 599, b"", type(exc).__name__, ""
            out = OUTPUT / name
            row: dict[str, object] = {
                "issuer_cik": source.stem.removeprefix("submissions_"),
                "name": name,
                "url": url,
                "filing_count_requested": ref.get("filingCount"),
                "filing_from": ref.get("filingFrom"),
                "filing_to": ref.get("filingTo"),
                "status_code": status,
                "bytes": len(content),
                "content_type": content_type,
                "transport_error": transport,
                "output": str(out.relative_to(REPO)) if status == 200 else None,
            }
            requests_seen.append(row)
            if status == 200:
                out.write_bytes(content)
                row["sha256"] = sha256(out)
            else:
                break
    receipt = {
        "status": "COMPLETE" if requests_seen and all(x["status_code"] == 200 for x in requests_seen) else "BLOCKED_PROVIDER_RESPONSE",
        "retrieved_at": datetime.now(UTC).isoformat(),
        "provider": "U.S. Securities and Exchange Commission EDGAR submissions",
        "requests": requests_seen,
        "credential_fields_persisted": False,
        "pit_note": "Archived files add historical acceptanceDateTime where present; current Companyfacts remains an endpoint snapshot.",
    }
    (OUTPUT / "archived_submissions_receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"SEC_ARCHIVED_SUBMISSIONS_COMPLETE files={len(requests_seen)} output={OUTPUT}")
    return 0 if receipt["status"] == "COMPLETE" else 3


if __name__ == "__main__":
    raise SystemExit(main())
